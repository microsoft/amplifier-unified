"""Exercise the registered-browser boundary, not a hand-populated exchange tree."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from amplifier_web.execution import ingest
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.session_projection import stored_execution
from test_work_windows import FakeProjector, fixture


def recorded_delegate(session, path, monkeypatch):
    """The same hook feeds canonical recording and the metadata-only observer."""
    tick = [10.0]
    # Keep deterministic observer stamps without freezing the service's shared
    # time module, HTTP timers, or background task clocks.
    monkeypatch.setattr('amplifier_web.execution_events.time',
                        SimpleNamespace(time=lambda: tick[0]))
    root = session['nativeIdentity']
    request = 'Review the launch checklist: café.\n' * 80
    reply = 'EXCHANGE-CHECK: the returned answer.\n' * 100
    session['execution']['turns'] = [
        {'id': 'turn', 'anchorMessageId': 'user', 'phase': 'running', 'startedAt': 10}
    ]

    def emit(event):
        normalized = normalize_event(event, root)
        if normalized and normalized[0] == 'execution.event':
            ingest(session, normalized[1])

    observer = ExecutionEvents(root, emit)
    observer.lifecycle({'type': 'input.delivered', 'input_id': 'turn', 'source': 'user'})

    def hook(name, data):
        tick[0] += 1
        record = {'event': name, 'timestamp': tick[0], 'session_id': root,
                  'data': {'session_id': root, **data}}
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        if name.startswith('tool:'):
            observer.hook(root, name, record['data'])

    arguments = {'agent': 'review', 'instruction': request}
    hook('tool:pre', {'tool_call_id': 'delegate-one', 'tool_name': 'delegate',
                      'tool_input': arguments})
    hook('delegate:agent_spawned', {'tool_call_id': 'delegate-one',
                                    'parent_session_id': root, 'sub_session_id': 'child'})
    observer.lifecycle({'type': 'child.updated', 'sessionId': 'child',
                        'parentSessionId': root, 'callId': 'delegate-one',
                        'runId': 'run-one', 'agent': 'review', 'status': 'running'})
    hook('tool:post', {'tool_call_id': 'delegate-one', 'tool_name': 'delegate',
                       'tool_input': arguments,
                       'result': {'success': True, 'output': {'response': reply,
                                                             'session_id': 'child'}}})
    observer.lifecycle({'type': 'child.updated', 'sessionId': 'child',
                        'parentSessionId': root, 'callId': 'delegate-one',
                        'runId': 'run-one', 'agent': 'review', 'status': 'completed',
                        'report': 'A retained summary is not the original reply.'})
    session['execution']['turns'][0].update(phase='completed', endedAt=tick[0])
    # Crucial distinction from the older browser fixture: do not inject bodies.
    assert all('input' not in row and 'output' not in row
               for row in session['execution']['nodes'])
    return request, reply


def exchanges(rows):
    found = {}
    for row in rows:
        for descriptor in (row.get('exchange'), (row.get('delegate') or {}).get('exchange')):
            if descriptor:
                found.setdefault(descriptor['id'], {}).update(descriptor)
    return list(found.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('cold', [False, True], ids=['resident-observer', 'persisted-projection'])
async def test_registered_browser_reads_recorded_delegate_exchange_without_body_injection(
        authenticated_client, tmp_path, monkeypatch, cold):
    from amplifier_web.server import create_app
    from amplifier_web.work_windows import WorkWindows
    from test_service import Runtime

    _, source, path, _ = fixture(tmp_path, monkeypatch, count=0)
    app = await create_app(tmp_path / 'web', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    await service.dispatch('session.create', {})
    session = service._session()
    session.update({key: value for key, value in source.items() if key != 'id'})
    expected = recorded_delegate(session, path, monkeypatch)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    if cold:
        # Exercise the real persistence projection and a fresh window cache.
        # This is not yet the separate process-restart/large-history acceptance.
        session['execution'] = json.loads(json.dumps(stored_execution(session['execution'])))
        assert session['execution']['nodes'] == []
        await service.event_log_view.windows.close()
        service.event_log_view.windows = WorkWindows(service)

    windows = service.event_log_view.windows
    windows.projector = FakeProjector(service.data_dir)
    service.clients.attach('exchange-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'exchange-browser'
    original = service._session
    before = copy.deepcopy(session['execution'])

    def bounded(identity=None, *, hydrate=True):
        assert hydrate is False, 'A Work read must not hydrate the full execution graph'
        return original(identity, hydrate=False)

    monkeypatch.setattr(service, '_session', bounded)
    try:
        group = 'turn@user'
        if cold:
            response = await client.get('/api/conversation/detail', params={
                'sessionId': session['id'], 'part': 'groups'})
            assert response.status == 200, await response.text()
            group = (await response.json())['items'][0]['id']
        response = await client.get('/api/conversation/detail', params={
            'sessionId': session['id'], 'part': 'nodes', 'group': group})
        assert response.status == 200, await response.text()
        rows = (await response.json())['items']
        descriptors = exchanges(rows)
        for field, exact in zip(('delegateRequest', 'delegateResponse'), expected):
            candidates = [entry for entry in descriptors
                          if field in entry or entry.get(field + 'Detail')]
            assert candidates, f'{field} missing from registered-browser {"saved" if cold else "resident"} page'
            value = candidates[-1].get(field, '')
            reference = candidates[-1].get(field + 'Detail')
            if reference:
                value, offset = '', 0
                while offset is not None:
                    response = await client.get('/api/conversation/detail', params={
                        **reference, 'sessionId': session['id'], 'offset': offset})
                    assert response.status == 200, await response.text()
                    chunk = await response.json()
                    value += chunk['value']
                    offset = chunk['nextOffset']
            assert value == exact
        assert session['execution'] == before
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source_hash
    finally:
        monkeypatch.setattr(service, '_session', original)
