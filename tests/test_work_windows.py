import asyncio
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from amplifier_web.work_windows import WorkWindows
from amplifier_web.event_window_worker import WindowModel


class FakeProjector:
    def __init__(self, home):
        self.model = WindowModel(home)
        self.inputs = []
        self.gate = None
        self.closed = False

    async def project(self, inputs, *, key):
        self.inputs.append(inputs)
        if self.gate is not None:
            await self.gate.wait()
        return self.model.project(inputs, check_budget=lambda: None)

    async def close(self):
        self.closed = True


def fixture(tmp_path, monkeypatch, count=140):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.delenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', raising=False)
    path = tmp_path / 'native/projects/p/sessions/s/context-intelligence/events.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(''.join(json.dumps({'event': 'tool:post', 'timestamp': n,
        'session_id': 's', 'data': {'tool_call_id': str(n), 'tool_name': 'read', 'result': 'value-' + str(n)}}) + '\n' for n in range(count)))
    session = {'id': 's', 'nativeProject': 'p', 'nativeIdentity': 's', 'workspace': '/fixture',
        'messages': [{'id': 'user', 'role': 'user', 'text': 'hi', 'createdAt': 0}],
        'execution': {'nodes': [], 'turns': [], 'retiredUsageNodes': [{'id': 'protected'}]}}
    service = SimpleNamespace(data_dir=tmp_path / 'app', closed=False, instance_id='host',
        projections=SimpleNamespace(detail_bodies={}), event_log_view=None)
    service._session = lambda identity, hydrate=False: session if identity == session['id'] else (_ for _ in ()).throw(ValueError('missing'))
    owner = WorkWindows(service)
    owner.projector = FakeProjector(service.data_dir)
    return owner, session, path, service


@pytest.mark.asyncio
async def test_initial_and_older_windows_never_change_execution_or_history(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch)
    original = json.dumps(session, sort_keys=True)
    first = await owner.read('s')
    assert first['mode'] == 'cursor' and first['total'] is None and first['hasEarlier']
    assert first['items'][0]['observationWindow'] and first['items'][0]['nodeCounts'] is None
    group = first['items'][0]['id']
    rows = owner.nodes('s', group)
    assert len(rows['items']) == 100
    assert all('_eventFields' not in row for row in rows['items'])
    prior = owner.current['s']
    second = await owner.read('s', first['before'])
    assert owner.current['s'] == prior
    assert len(owner.nodes('s', second['items'][0]['id'])['items']) == 40
    assert json.dumps(session, sort_keys=True) == original
    assert set(owner.projector.inputs[0]['session']) == {'id', 'nativeProject', 'nativeIdentity', 'workspace'}
    assert 'execution' not in owner.projector.inputs[0]['session']
    projection = owner.project(session)
    assert projection['executionWindow']['part'] == 'groups'
    assert projection['executionWindow']['before'] == first['before']
    receipt = projection['workProjection']['receipt']
    assert receipt['scope'] == 'recent-events' and receipt['historyComplete'] is False
    assert receipt['itemCount'] == 100 and 'nodeCount' not in receipt and 'turnCount' not in receipt
    assert projection['execution']['nodes'] == []
    await owner.close()


@pytest.mark.asyncio
async def test_field_resolves_only_owned_cached_row_and_rejects_source_replacement(tmp_path, monkeypatch):
    owner, _, path, _ = fixture(tmp_path, monkeypatch)
    first = await owner.read('s')
    rows = owner.nodes('s', first['items'][0]['id'])['items']
    args = rows[-1]['outputDetail']
    value = await owner.read_text('s', args)
    assert value['value'] == 'value-139'
    with pytest.raises(ValueError):
        await owner.read_text('s', {**args, 'id': 'arbitrary', 'path': '/etc/passwd'})
    path.write_text('{}\n')
    with pytest.raises(ValueError):
        await owner.read_text('s', args)
    await owner.close()


@pytest.mark.asyncio
async def test_quiet_reuse_is_stat_only_and_pages_are_bounded(tmp_path, monkeypatch):
    owner, _, path, _ = fixture(tmp_path, monkeypatch, count=950)
    first = await owner.read('s', _initial=True)
    await owner.read('s', _initial=True)
    assert len(owner.projector.inputs) == 1
    page = first
    for _ in range(8):
        page = await owner.read('s', page['before'])
    assert len(owner.pages) == 8
    with pytest.raises(ValueError, match='expired'):
        owner.nodes('s', first['items'][0]['id'])
    await owner.close()


@pytest.mark.asyncio
async def test_departed_owner_or_closed_host_cannot_publish_late_result(tmp_path, monkeypatch):
    owner, session, _, service = fixture(tmp_path, monkeypatch)
    owner.projector.gate = asyncio.Event()
    task = asyncio.create_task(owner.read('s'))
    await asyncio.sleep(0)
    replacement = dict(session)
    service._session = lambda identity, hydrate=False: replacement
    owner.projector.gate.set()
    with pytest.raises(ValueError, match='changed'):
        await task
    assert not owner.pages
    await owner.close()


@pytest.mark.asyncio
async def test_coalesced_interest_and_cancel_do_not_mutate_commands(tmp_path, monkeypatch):
    owner, session, _, _ = fixture(tmp_path, monkeypatch)
    owner.projector.gate = asyncio.Event()
    task = owner.request('s')
    assert owner.request('s') is task
    await asyncio.sleep(0)
    owner.retain_interest(set())
    await asyncio.gather(task, return_exceptions=True)
    assert not owner.pages and not owner.current
    assert session['execution']['retiredUsageNodes'] == [{'id': 'protected'}]
    await owner.close()


def test_bounded_live_overlay_does_not_scan_or_replace_native_graph(tmp_path, monkeypatch):
    owner, session, _, _ = fixture(tmp_path, monkeypatch)
    class TailOnly(list):
        def __iter__(self):
            raise AssertionError('Full graph iteration')
    session['execution']['nodes'] = TailOnly([{'id': str(i), 'kind': 'tool', 'canonicalHistory': True} for i in range(10000)] +
        [{'id': 'live', 'kind': 'tool', 'label': 'Run', 'turnId': 't', 'phase': 'running', 'startedAt': 1}])
    session['execution']['turns'] = [{'id': 't', 'phase': 'running', 'anchorMessageId': 'user'}]
    result = owner.project(session)
    assert result['execution']['segments'][0]['phase'] == 'running'
    assert result['execution']['segments'][0]['partialWindow'] is True
    assert result['execution']['segments'][0]['nodeCounts'] is None
    assert result['execution']['segments'][0]['aggregateUsage'] is None
    assert owner.live_view(session)['execution']['nodes'][0]['id'] == 'live'
    assert len(session['execution']['nodes']) == 10001


@pytest.mark.asyncio
async def test_actual_worker_uses_bounded_window_protocol(tmp_path, monkeypatch):
    owner, _, _, _ = fixture(tmp_path, monkeypatch, count=5)
    owner.projector = None
    try:
        result = await owner.read('s')
        assert len(owner.nodes('s', result['items'][0]['id'])['items']) == 5
        assert owner.projector._process is not None
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_same_owner_source_reassignment_rejects_cached_rows_and_inflight_field(tmp_path, monkeypatch):
    owner, session, _, _ = fixture(tmp_path, monkeypatch)
    first = await owner.read('s')
    group = first['items'][0]['id']
    args = owner.nodes('s', group)['items'][-1]['outputDetail']
    owner.projector.gate = asyncio.Event()
    task = asyncio.create_task(owner.read_text('s', args))
    await asyncio.sleep(0)
    session['nativeIdentity'] = 'other'
    assert owner.project(session)['execution']['segments'] == []
    assert owner.project(session)['workProjection'] == {'status': 'loading', 'current': False}
    with pytest.raises(ValueError, match='expired'):
        owner.nodes('s', group)
    owner.projector.gate.set()
    with pytest.raises(ValueError, match='expired'):
        await task
    await owner.close()


def test_browser_window_projection_never_hydrates_cold_payloads(tmp_path, monkeypatch):
    from amplifier_web.cold_display import ColdRecord, MARKER
    from amplifier_web.browser_detail import project
    import amplifier_web.cold_display as cold
    def forbidden(*args):
        raise AssertionError('Cold execution/messages/configuration were hydrated')
    monkeypatch.setattr(cold, 'load', forbidden)
    owner, session, _, _ = fixture(tmp_path, monkeypatch)
    session = ColdRecord({'id': 's', 'workspace': '/fixture', 'nativeProject': 'p',
        'status': 'idle', MARKER: {key: {'$resource': key} for key in
                                ('messages', 'execution', 'configuration', 'historyActivity')}}, None)
    overlay = {'messages': [{'id': 'recent', 'role': 'user', 'text': 'recent', 'displayOnly': True}],
               'messageWindow': {'mode': 'cursor', 'total': None, 'offset': None}}
    result = project(session, message_overlay=overlay, work_overlay=owner.project(session))
    assert result['messages'][0]['id'] == 'recent'
    assert result['execution']['nodes'] == []
    assert MARKER not in result and 'configuration' not in result
    assert all(not dict.__contains__(session, key) for key in ('messages', 'execution', 'configuration'))


def test_canonically_enriched_accepted_live_observation_stays_visible(tmp_path, monkeypatch):
    owner, session, _, _ = fixture(tmp_path, monkeypatch)
    session['execution'].update(nodes=[{'id': 'live', 'kind': 'tool', 'turnId': 't',
        'canonicalHistory': True, 'liveObservation': True, 'startedAt': 1, 'phase': 'running',
        'sessionId': 's', 'imageGeneration': {'requestId': 'image', 'operation': 'generate'}}],
        turns=[{'id': 't', 'phase': 'running', 'anchorMessageId': 'user'}])
    session['status'] = 'working'
    session['execution'].update(currentTurnId='t', aggregateUsage={'totalTokens': 123},
                                plan={'turnId': 't', 'items': [{'content': 'Current task'}]})
    assert owner.live_view(session)['execution']['nodes'][0]['id'] == 'live'
    assert owner.project(session)['execution']['segments'][0]['phase'] == 'running'
    assert owner.project(session)['execution']['aggregateUsage'] == {'totalTokens': 123}
    assert owner.project(session)['execution']['plan'] == session['execution']['plan']
    assert owner.project(session)['execution']['currentTurnId'] == 't'
    assert owner.project(session)['execution']['imageGeneration'][0]['phase'] == 'running'


@pytest.mark.asyncio
async def test_invalidation_exposes_noncurrent_then_quiet_receipt_recovers_without_redecode(tmp_path, monkeypatch):
    owner, _, _, _ = fixture(tmp_path, monkeypatch)
    await owner.read('s', _initial=True)
    receipt = owner.status['s']['receipt']
    owner.invalidate({'s'})
    assert owner.status['s']['current'] is False
    await owner.read('s', _initial=True)
    assert owner.status['s']['current'] is True and owner.status['s']['receipt'] == receipt
    assert len(owner.projector.inputs) == 1
    await owner.close()


@pytest.mark.asyncio
async def test_failed_source_probe_has_visible_retry_without_forever_loading(tmp_path, monkeypatch):
    owner, _, _, _ = fixture(tmp_path, monkeypatch)
    async def broken(*args, **kwargs):
        raise OSError('unavailable private source')
    owner.projector.project = broken
    await owner.request('s')
    assert owner.status['s'] == {'status': 'retrying', 'current': False, 'reason': 'worker_failed'}
    assert owner.request('s') is None
    assert owner.project(owner._owner('s'))['workProjection']['status'] == 'retrying'
    await owner.close()


def test_failed_or_unretained_request_fences_are_bounded_without_reusing_old_generation(tmp_path, monkeypatch):
    owner, _, _, _ = fixture(tmp_path, monkeypatch)
    original = owner._fence('old')
    for index in range(100):
        owner._fence(str(index))
    assert len(owner.epochs) == 32 and 'old' not in owner.epochs
    assert owner._fence('old') > original


@pytest.mark.asyncio
async def test_passive_browser_interest_never_enters_full_history_projection(tmp_path, monkeypatch):
    from amplifier_web.event_log_view import EventLogView
    service = SimpleNamespace(data_dir=tmp_path, closed=False,
        queue_clients={'web-q': 'browser', 'api-q': 'terminal'},
        queue_sessions={'web-q': None, 'api-q': 'explicit'},
        clients=SimpleNamespace(records={'browser': {'kind': 'web', 'selectedSessionId': 'recent'},
                                         'terminal': {'kind': 'tui'}}), _state={})
    view = EventLogView(service)
    requested, complete = [], []
    view.windows.request = requested.append
    view.projector = object()
    view._process_refresh_task = complete.append
    async def end_iteration(_):
        service.closed = True
    monkeypatch.setattr('amplifier_web.event_log_view.asyncio.sleep', end_iteration)
    await view.loop()
    assert requested == ['recent'] and complete == ['explicit']


@pytest.mark.asyncio
async def test_six_browser_interests_rotate_past_four_active_read_slots(tmp_path, monkeypatch):
    from amplifier_web.event_log_view import EventLogView
    service = SimpleNamespace(data_dir=tmp_path, closed=False,
        queue_clients={str(i): str(i) for i in range(6)},
        queue_sessions={str(i): None for i in range(6)},
        clients=SimpleNamespace(records={str(i): {'kind': 'web', 'selectedSessionId': str(i)} for i in range(6)}),
        _state={})
    view = EventLogView(service)
    admitted, current = set(), []
    ticks = 0
    def request(identity):
        if len(current) < 4:
            current.append(identity)
            admitted.add(identity)
    view.windows.request = request
    async def next_iteration(_):
        nonlocal ticks
        assert len(current) == 4
        current.clear()
        ticks += 1
        service.closed = ticks == 3
    monkeypatch.setattr('amplifier_web.event_log_view.asyncio.sleep', next_iteration)
    await view.loop()
    assert admitted == {str(i) for i in range(6)}


@pytest.mark.asyncio
async def test_http_work_pages_use_exact_window_cursor_without_hydrating(authenticated_client, tmp_path, monkeypatch):
    from amplifier_web.server import create_app
    from test_service import Runtime
    _, source, path, _ = fixture(tmp_path, monkeypatch)
    app = await create_app(tmp_path / 'http', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    await service.dispatch('session.create', {})
    session = service._session()
    identity = session['id']
    session.update({key: value for key, value in source.items() if key != 'id'})
    service.event_log_view.windows.projector = FakeProjector(service.data_dir)
    service.clients.attach('window-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'window-browser'
    original = service._session
    calls = []
    def bounded(identity=None, *, hydrate=True):
        calls.append(hydrate)
        assert hydrate is False, 'Ordinary Work page hydrated the authoritative cold graph'
        return original(identity, hydrate=False)
    monkeypatch.setattr(service, '_session', bounded)
    query = {'sessionId': identity, 'part': 'groups'}
    response = await client.get('/api/conversation/detail', params=query)
    assert response.status == 200, await response.text()
    first = await response.json()
    assert first['mode'] == 'cursor' and first['hasEarlier']
    older = await (await client.get('/api/conversation/detail', params={**query, 'before': first['before']})).json()
    assert older['before'] is None and older['total'] is None
    group = older['items'][0]['id']
    nodes = await (await client.get('/api/conversation/detail', params={**query, 'part': 'nodes', 'group': group})).json()
    assert len(nodes['items']) == 40
    field = nodes['items'][-1]['outputDetail']
    text = await (await client.get('/api/conversation/detail', params={**field, 'sessionId': identity})).json()
    assert text['value'] == 'value-39'
    sync = await (await client.post('/api/conversation/work-sync', json={
        'sessionId': identity, 'group': group,
        'known': {row['id']: row['detailVersion'] for row in nodes['items']}})).json()
    assert sync['items'] == [] and len(sync['order']) == 40
    assert calls and all(value is False for value in calls)
    monkeypatch.setattr(service, '_session', original)


@pytest.mark.asyncio
async def test_http_native_continuation_is_explicit_and_never_replaces_saved_message_authority(authenticated_client, tmp_path, monkeypatch):
    from amplifier_web.server import create_app
    from test_service import Runtime
    _, source, path, _ = fixture(tmp_path, monkeypatch, count=3)
    transcript = path.parent.parent / 'transcript.jsonl'
    transcript.write_text(''.join(json.dumps({'role': 'user' if i % 2 == 0 else 'assistant',
        'content': 'native-' + str(i) + ('x' * 5000 if i == 139 else '')}) + '\n' for i in range(140)))
    app = await create_app(tmp_path / 'messages-http', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    await service.dispatch('session.create', {})
    session = service._session()
    session.update({key: value for key, value in source.items() if key != 'id'})
    service.clients.attach('message-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'message-browser'
    canonical = json.dumps(session['messages'])
    query = {'sessionId': session['id'], 'part': 'messages'}
    saved = await (await client.get('/api/conversation/detail', params=query)).json()
    assert [row['id'] for row in saved['items']] == ['user']
    first_response = await client.get('/api/conversation/detail', params={**query, 'native': 'true'})
    assert first_response.status == 200, await first_response.text()
    first = await first_response.json()
    assert len(first['items']) == 100 and first['total'] is None and first['hasEarlier']
    assert all(row['displayOnly'] and 'nativeIndex' not in row and 'nativeMessageId' not in row for row in first['items'])
    older = await (await client.get('/api/conversation/detail', params={**query, 'native': 'true', 'before': first['before']})).json()
    assert len(older['items']) == 40 and not older['hasEarlier']
    text = await (await client.get('/api/conversation/detail', params=first['items'][-1]['textDetail'])).json()
    assert text['value'] == 'native-139' + 'x' * 5000
    assert json.dumps(session['messages']) == canonical
