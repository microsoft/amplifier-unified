"""Original resident-call completeness, not a capture-only tail substitute.

Synthetic canonical records pass through the real observer and host ingestion.
The HTTP reads use the normal saved-window worker; no private proof/cache is
seeded and no historical tool ownership is inferred.
"""
import asyncio
import hashlib
import json
import time

import pytest

from amplifier_web.automatic_history import AutomaticHistory
from amplifier_web.execution import ensure_turn, finish
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.server import create_app
from amplifier_web.session_files import append_event
from test_service import Runtime


async def _start(home, workspace, monkeypatch):
    """Observe the first scheduled refresh without adding/cancelling one."""
    completed = asyncio.Event()
    errors = []
    original = AutomaticHistory.refresh

    async def refresh(history, *args, **kwargs):
        try:
            return await original(history, *args, **kwargs)
        except BaseException as error:
            if history.service.data_dir == home:
                errors.append(type(error).__name__)
            raise
        finally:
            if history.service.data_dir == home:
                completed.set()

    # Restore the class before returning; each new service gets its own observer.
    with monkeypatch.context() as patch:
        patch.setattr(AutomaticHistory, 'refresh', refresh)
        runtime = Runtime()
        application = await create_app(home, workspace=workspace, runtime=runtime,
                                       voice=False, preload_providers=False,
                                       background_updates=False)
        try:
            await asyncio.wait_for(completed.wait(), 10)
            assert errors == []
            assert not application['service'].state['sharedHistory'].get('error')
        except BaseException:
            await application['service'].close()
            raise
    return application, runtime


async def _emit_resident_calls(service, split):
    await service.dispatch('session.create', {})
    session = service._session()
    sid = session['id']
    native = session.get('runtimeSessionId') or sid
    input_id = 'original-144-input'
    service._message(session, 'user', 'Inspect four files and then 140 more.',
                     'chat', inputId=input_id)
    ensure_turn(session, input_id)
    emitted = []
    observer = ExecutionEvents(native, emitted.append)
    observer.lifecycle({'type': 'input.delivered', 'input_id': input_id, 'source': 'user'})
    anchor = service._message(session, 'assistant', 'First four files.', 'chat',
                              inputId=input_id, timestampKnown=True)
    expected = {}
    for index in range(144):
        if split and index == 4:
            anchor = service._message(session, 'assistant', 'The remaining files.',
                                      'chat', inputId=input_id, timestampKnown=True)
        arguments = {'file_path': f'file-{index}.txt',
                     'note': ('界𝄞' * 10000 if index < 4 else 'small') + f':INPUT-{index}'}
        output = ('雪𝄞' * 10000 if index < 4 else 'small') + f':OUTPUT-{index}'
        call = f'original-call-{index}'
        tool = ('app_control', 'app_control', 'read_file', 'bash')[index] if index < 4 else 'read_file'
        for event in ('tool:pre', 'tool:post'):
            data = {'session_id': native, 'tool_call_id': call, 'tool_name': tool,
                    'tool_input': arguments, 'timestamp': time.time()}
            if event == 'tool:post':
                data['result'] = output
            canonical = observer.hook(native, event, data)
            assert canonical is not None
            update = emitted[-1]['event']
            assert update['turnId'] == input_id
            await service.on_runtime_event('execution.event', update)
            append_event(session['workspace'], native, event, canonical)
        expected[update['id']] = {
            'call': call, 'tool': tool, 'anchor': anchor['id'], 'turn': input_id,
            'input': arguments, 'output': output,
        }
    final = service._message(session, 'assistant', 'All 144 files inspected.', 'chat',
                              inputId=input_id)
    finish(session, 'completed')
    session['status'] = 'idle'
    nodes = session['execution']['nodes']
    assert len(nodes) == len(expected) == 144
    assert {row['id'] for row in nodes} == set(expected)
    assert all(row.get('liveObservation') is True for row in nodes)
    assert all(row.get('toolObservationId') and row.get('rootSessionId') == sid for row in nodes)
    # These are all real resident host observations, not authored inline bodies.
    assert all('input' not in row and 'output' not in row for row in nodes)
    await service._flush_pending_progress()
    return session, expected, final['id']


async def _get(client, params):
    response = await client.get('/api/conversation/detail', params=params)
    value = await response.json()
    assert response.status == 200, {'request': params, 'status': response.status, 'response': value}
    return value


async def _field(client, reference):
    chunks, offset = [], 0
    for _ in range(64):
        page = await _get(client, {**reference, 'offset': offset, 'limit': 4096})
        assert len(page['value']) <= 4096
        chunks.append(page['value'])
        if page.get('nextOffset') is None:
            return ''.join(chunks), len(chunks)
        assert page['nextOffset'] > offset
        offset = page['nextOffset']
    pytest.fail('The exact field did not terminate within the synthetic bound')


async def _verify(client, service, sid, expected, final_id, retired):
    # Same public boundary the registered browser uses. Do not call private
    # resident_tree/proof builders to discover or pre-admit the missing group.
    await _get(client, {'sessionId': sid, 'part': 'groups'})
    response = await client.get('/api/state')
    assert response.status == 200
    state = await response.json()
    visible = next(row for row in state['sessions'] if row['id'] == sid)
    segments = [row for row in visible['execution']['segments']
                if row.get('turnId') == 'original-144-input' and not row.get('observationWindow')]
    anchors = list(dict.fromkeys(row['anchor'] for row in expected.values()))
    assert [row['anchorMessageId'] for row in segments] == anchors, {
        'requiredAnchors': anchors, 'actualGroups': segments,
        'workProjection': visible.get('workProjection'),
    }
    assert all(row['anchorMessageId'] != final_id for row in segments)
    all_ids, pages_read = [], []
    for segment in segments:
        query = {'sessionId': sid, 'part': 'nodes', 'group': segment['id']}
        page = await _get(client, query)
        pages, cursors = [page], set()
        for _ in range(8):
            cursor = page.get('before')
            if cursor is None:
                break
            assert cursor not in cursors
            cursors.add(cursor)
            page = await _get(client, {**query, 'before': cursor, 'revision': pages[0]['revision']})
            pages.append(page)
        else:
            pytest.fail('Resident cursor did not terminate in its existing range budget')
        assert all(len(part['items']) <= 100 for part in pages)
        nodes = [node for part in reversed(pages) for node in part['items']]
        required = [identity for identity, row in expected.items()
                    if row['anchor'] == segment['anchorMessageId']]
        assert [node['id'] for node in nodes] == required
        assert len(set(required)) == len(nodes)
        for node in nodes:
            row = expected[node['id']]
            assert node['turnId'] == row['turn']
            assert node['anchorMessageId'] == row['anchor']
            assert node['toolCallId'] == row['call']
            assert node['label'] == row['tool']
            assert node['toolDetail']['sessionId'] == sid
            assert node['toolDetail']['id'] == node['id']
        all_ids.extend(required)
        pages_read.append([len(part['items']) for part in pages])

        references = []
        # Full long fields for the original four and the latest small call.
        for node in nodes:
            if node['id'] not in list(expected)[:4] + list(expected)[-1:]:
                continue
            lookup = node['toolDetail']
            for _ in range(8):
                detail = await _get(client, lookup)
                if detail['item'].get('inputDetail') and detail['item'].get('outputDetail'):
                    break
                assert detail.get('before'), detail
                lookup = {**node['toolDetail'], 'lookupKey': detail['lookupKey'],
                          'before': detail['before']}
            else:
                pytest.fail('Exact canonical tool lookup did not terminate')
            for field in ('input', 'output'):
                reference = detail['item'][field + 'Detail']
                assert reference['sessionId'] == sid
                assert reference['digest'] and reference['windowKey']
                value, chunks = await _field(client, reference)
                assert (json.loads(value) if field == 'input' else value) == expected[node['id']][field]
                if node['id'] in list(expected)[:4]:
                    assert chunks > 4
                references.append((reference, value))
        # Resident admitted groups support up to800 known IDs; saved observation
        # windows are a distinct100-ID contract and are not used as a substitute.
        sync = await client.post('/api/conversation/work-sync', json={
            'sessionId': sid, 'group': segment['id'],
            'known': {node['id']: node['detailVersion'] for node in nodes}})
        assert sync.status == 200, await sync.text()
        synced = await sync.json()
        assert synced['order'] == required
        for reference, value in references:
            assert (await _field(client, reference))[0] == value
    assert all_ids == list(expected) and len(set(all_ids)) == 144
    if retired:
        assert not dict.__contains__(service._session(sid, hydrate=False), 'execution')
    print('INTERIM-A-144', json.dumps({
        'count': len(all_ids), 'unique': len(set(all_ids)), 'pageSizes': pages_read,
        'identitiesSha256': hashlib.sha256(json.dumps(all_ids).encode()).hexdigest(),
        'retired': retired,
    }))


@pytest.mark.parametrize('phase', ['live', 'reopen', 'retired-reopen'])
@pytest.mark.parametrize('split', [False, True], ids=['one-group', 'early-and-tail-groups'])
async def test_144_resident_calls_complete_through_registered_web(
        tmp_path, monkeypatch, authenticated_client, phase, split):
    """Same workload independently qualifies live and genuine cold reopen."""
    home = tmp_path / 'app'
    application, runtime = await _start(home, tmp_path, monkeypatch)
    service, client, socket = application['service'], None, None
    try:
        session, expected, final_id = await _emit_resident_calls(service, split)
        sid = session['id']
        retired = phase == 'retired-reopen'
        if phase != 'live':
            if retired:
                service._state['selectedSessionId'] = None
                monkeypatch.setattr(service.cold_display, 'RECENT_LIMIT', 0)
                monkeypatch.setattr(service.cold_display, 'MIN_BYTES', 1)
                service.cold_display.retire(force=True)
                assert not dict.__contains__(session, 'execution')
            service._save()
            await service.close()
            assert runtime.started == runtime.sent == []
            application, runtime = await _start(home, tmp_path, monkeypatch)
            service = application['service']
        service.clients.attach('original-acceptance-browser', kind='web')
        with service.clients.bind('original-acceptance-browser'):
            # Selection is client-local presentation, not source/Work readiness.
            service.state['selectedSessionId'] = sid
        client = await authenticated_client(application)
        client.session.headers['X-Amplifier-Client'] = 'original-acceptance-browser'
        socket = await client.ws_connect('/api/events/ws?clientId=original-acceptance-browser')
        initial = await socket.receive_json(timeout=5)
        assert initial['event'] == 'state'
        assert initial['data']['client']['id'] == 'original-acceptance-browser'
        # A tests completeness after ordinary transcript readiness; C separately
        # retains the first read during startup. Observe real stream updates,
        # never hydrate or fabricate a saved message/group to pass A.
        async with asyncio.timeout(10):
            snapshot = initial['data']
            while True:
                visible = next(row for row in snapshot['sessions'] if row['id'] == sid)
                assert not visible.get('historyError')
                if not visible.get('historyLoading'):
                    assert final_id in {row['id'] for row in visible['messages']}
                    break
                event = await socket.receive_json()
                if event['event'] == 'state':
                    snapshot = event['data']
        await _verify(client, service, sid, expected, final_id, retired)
        assert runtime.started == runtime.sent == []
    finally:
        if socket is not None:
            await socket.close()
        if client is not None:
            await client.close()
        if not service.closed:
            await service.close()