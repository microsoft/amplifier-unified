import asyncio
import json
import threading
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


@pytest.mark.asyncio
async def test_published_invalidation_during_cached_probe_retries_without_false_failure(tmp_path, monkeypatch):
    owner, session, _, _ = fixture(tmp_path, monkeypatch)
    await owner.read('s', _initial=True)
    receipt = dict(owner.status['s']['receipt'])
    original = json.dumps(session, sort_keys=True)
    entered, release = threading.Event(), threading.Event()
    matches = owner._matches
    def held(*args):
        entered.set()
        assert release.wait(5), 'Cached probe was not released'
        return matches(*args)
    monkeypatch.setattr(owner, '_matches', held)
    task = owner.request('s')
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        owner.invalidate({'s'})
        release.set()
        assert await task is None
        assert owner.status['s']['status'] == 'loading'
        assert owner.status['s']['current'] is False
        assert 's' not in owner.retry_at
        monkeypatch.setattr(owner, '_matches', matches)
        await owner.request('s')
        assert owner.status['s'] == {'status': 'ready', 'current': True, 'receipt': receipt}
        assert len(owner.projector.inputs) == 1
        assert json.dumps(session, sort_keys=True) == original
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [OSError('private source unavailable'), ValueError('invalid source record')])
async def test_real_cached_probe_failure_backs_off_then_restores_exact_receipt(tmp_path, monkeypatch, error):
    owner, _, _, _ = fixture(tmp_path, monkeypatch)
    await owner.read('s', _initial=True)
    receipt = dict(owner.status['s']['receipt'])
    matches = owner._matches
    def broken(*args):
        raise error
    monkeypatch.setattr(owner, '_matches', broken)
    try:
        await owner.request('s')
        assert owner.status['s'] == {'status': 'retrying', 'current': False, 'reason': 'worker_failed'}
        assert owner.request('s') is None
        monkeypatch.setattr(owner, '_matches', matches)
        # Advance only this owner's retry deadline; no sleeps or broad clock patch.
        owner.retry_at['s'] = 0
        await owner.request('s')
        assert owner.status['s'] == {'status': 'ready', 'current': True, 'receipt': receipt}
        assert len(owner.projector.inputs) == 1
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_owner_lookup_failure_before_task_admission_keeps_fixed_retry_status(tmp_path, monkeypatch):
    from amplifier_web.service import AppError
    owner, _, _, service = fixture(tmp_path, monkeypatch)
    task = owner.request('s')
    def missing(*args, **kwargs):
        raise AppError('No longer selected', 404)
    service._session = missing
    try:
        assert await task is None
        assert owner.status['s'] == {'status': 'retrying', 'current': False, 'reason': 'worker_failed'}
        assert owner.request('s') is None
        assert not owner.pages and not owner.current
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_explicit_latest_replacement_keeps_receipt_for_quiet_reuse(tmp_path, monkeypatch):
    owner, _, _, _ = fixture(tmp_path, monkeypatch)
    try:
        await owner.read('s', _initial=True)
        previous = dict(owner.status['s']['receipt'])
        await owner.read('s')  # Explicit latest read replaces the same page group.
        receipt = dict(owner.status['s']['receipt'])
        assert receipt['generation'] > previous['generation']
        assert owner.pages[owner.current['s']]['receipt'] == receipt
        owner.invalidate({'s'})
        await owner.read('s', _initial=True)
        assert owner.status['s'] == {'status': 'ready', 'current': True, 'receipt': receipt}
        assert len(owner.projector.inputs) == 2
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', ['owner', 'source', 'epoch'])
@pytest.mark.parametrize('worker_failure', [False, True])
async def test_superseded_work_request_cannot_overwrite_newer_status(tmp_path, monkeypatch, replacement, worker_failure):
    owner, session, _, service = fixture(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    project = owner.projector.project
    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        if worker_failure:
            raise OSError('Late failure from obsolete read')
        return await project(*args, **kwargs)
    monkeypatch.setattr(owner.projector, 'project', held)
    task = owner.request('s')
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if replacement == 'owner':
            current = dict(session)
            service._session = lambda identity, hydrate=False: current
        elif replacement == 'source':
            session['nativeIdentity'] = 'replacement'
        else:
            owner.invalidate({'s'})
        newer = {'status': 'loading', 'current': False, 'reason': 'newer-owner-read'}
        owner._state('s', newer)
        release.set()
        assert await task is None
        assert owner.status['s'] is newer
        assert 's' not in owner.retry_at
        assert not owner.pages and not owner.current
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
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


def test_work_notice_recipients_include_only_selected_observed_web_clients(tmp_path):
    from contextvars import ContextVar
    from amplifier_web.event_log_view import EventLogView
    current = ContextVar('notification-client', default='current')
    records = {
        name: {'kind': kind, 'selectedSessionId': selected}
        for name, kind, selected in [
            ('registered', 'web', 's'), ('connected', 'web', 's'),
            ('current', 'web', 's'), ('unrelated', 'web', 'other'),
            ('terminal', 'tui', 's'), ('dormant', 'web', 's')]
    }
    notified = []
    service = SimpleNamespace(data_dir=tmp_path,
        clients=SimpleNamespace(records=records, current=current),
        queue_clients={'a': 'connected', 'b': 'unrelated', 'c': 'terminal'},
        shell=SimpleNamespace(resources=SimpleNamespace(clients={'registered', 'unrelated'}),
                              notify=notified.append))
    EventLogView(service)._notify_work('s')
    assert sorted(notified) == ['connected', 'current', 'registered']


@pytest.mark.asyncio
async def test_connected_web_work_ready_evicts_outer_cache_without_shell_registration(
        authenticated_client, tmp_path, monkeypatch):
    """Real WS/HTTP clients: legacy notification leaves a stale outer frame."""
    from amplifier_web.server import create_app
    from test_service import Runtime
    _, source, _, _ = fixture(tmp_path, monkeypatch, count=140)
    app = await create_app(tmp_path / 'notification-http', workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Selected fixture'})
    selected = service._session()
    sid = selected['id']
    selected.update({key: value for key, value in source.items() if key != 'id'})
    await service.dispatch('session.create', {'title': 'Unrelated fixture'})
    other_sid = service._session()['id']
    for identity, selection in [('notice-web', sid), ('unrelated-web', other_sid)]:
        service.clients.attach(identity, kind='web')
        with service.clients.bind(identity):
            await service.dispatch('session.select', {'id': selection})
    windows = service.event_log_view.windows
    projector = FakeProjector(service.data_dir)
    projector.gate = asyncio.Event()
    windows.projector = projector
    original_notice = service.event_log_view._notify_work
    def legacy_notice(identity):
        # Negative control reproduces the old recipient set, never forces state.
        for client_id in tuple(service.shell.resources.clients):
            record = service.clients.records.get(client_id, {})
            if record.get('kind') == 'web' and record.get('selectedSessionId') == identity:
                service.shell.notify(client_id)
    monkeypatch.setattr(service.event_log_view, '_notify_work', legacy_notice)
    client = await authenticated_client(app)
    sockets, read = [], None
    try:
        for identity in ['notice-web', 'unrelated-web']:
            socket = await client.ws_connect('/api/events/ws?transport=delta-v1&clientId=' + identity)
            sockets.append(socket)
            initial = await socket.receive_json(timeout=5)
            assert initial['event'] == 'state'
            assert initial['data']['client']['id'] == identity
            assert not service.shell.resources.registered(identity)
        # A real requested physical page waits at the projector boundary. Only
        # its completion may set ready; no status injection or broad publish.
        read = windows.request(sid)
        assert read is not None
        async with asyncio.timeout(5):
            while not any(inputs['session']['id'] == sid for inputs in projector.inputs):
                await asyncio.sleep(0)
        response = await client.get('/api/state', headers={'X-Amplifier-Client': 'notice-web'})
        assert response.status == 200, await response.text()
        before = await response.json()
        before_session = next(row for row in before['sessions'] if row['id'] == sid)
        assert before_session['workProjection']['current'] is False
        cached_before = service._client_snapshots['notice-web']
        projector.gate.set()
        await asyncio.wait_for(asyncio.shield(read), timeout=5)
        assert windows.status[sid]['current'] is True and windows.status[sid]['status'] == 'ready'
        # No await between stale-frame evidence and the corrected notification:
        # background state publications cannot manufacture this discriminator.
        with service.clients.bind('notice-web'):
            stale = service.browser_state()
        assert stale is cached_before
        stale_session = next(row for row in stale['sessions'] if row['id'] == sid)
        assert stale_session['workProjection']['current'] is False
        with service.clients.bind('unrelated-web'):
            unrelated = service.browser_state()
        unrelated_token = service.shell.change_token('unrelated-web')
        selected_token = service.shell.change_token('notice-web')
        monkeypatch.setattr(service.event_log_view, '_notify_work', original_notice)
        original_notice(sid)
        assert 'notice-web' not in service._client_snapshots
        assert service.shell.change_token('notice-web') != selected_token
        assert service._client_snapshots['unrelated-web'] is unrelated
        assert service.shell.change_token('unrelated-web') == unrelated_token
        with service.clients.bind('notice-web'):
            current = service.browser_state()
        projected = next(row for row in current['sessions'] if row['id'] == sid)
        assert projected['workProjection']['status'] == 'ready'
        assert projected['workProjection']['current'] is True
        assert projected['workProjection']['receipt'] == windows.status[sid]['receipt']
        response = await client.get('/api/state', headers={'X-Amplifier-Client': 'notice-web'})
        assert response.status == 200, await response.text()
        external = await response.json()
        projected = next(row for row in external['sessions'] if row['id'] == sid)
        assert projected['workProjection']['status'] == 'ready' and projected['workProjection']['current']
        assert not service.shell.resources.registered('notice-web')
        print('WORK-NOTIFICATION-CONTROL', {'legacyExternalCurrent': False, 'internalCurrent': True,
              'correctedExternalCurrent': True, 'unrelatedCacheAndTokenUnchanged': True})
    finally:
        projector.gate.set()
        if read is not None:
            if not read.done():
                read.cancel()
            await asyncio.gather(read, return_exceptions=True)
        try:
            for socket in sockets:
                await socket.close()
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_requested_root_proof_uses_existing_eight_page_budget_and_never_poll_rescans(tmp_path, monkeypatch):
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=576)
    from amplifier_web.diagnostics import Diagnostics
    service.state = {'sessions': [session]}
    service.default_workspace = '/fixture'
    diagnostics = Diagnostics(service)
    try:
        result = await owner.prove('s')
        assert result['status'] == 'complete' and result['steps'] == 6
        assert len(owner.pages) == 6 <= 8
        assert len(owner.projector.inputs) == 6
        assert all(value['operation'] == 'proof-step' for value in owner.projector.inputs)
        assert len({value['deadline'] for value in owner.projector.inputs}) == 1
        for _ in range(5):
            assert owner.project(session)['workProjection']['rootProof']['status'] == 'complete'
            assert owner.request('s') is None
            assert (await owner.prove('s'))['status'] == 'complete'
        assert len(owner.projector.inputs) == 6
        assert owner.proof_nodes('s', result['id']) == []  # No manufactured receipt authority.
        raw = next(iter(owner.pages.values()))
        assert owner.observations('s', raw['group'], raw['generation'])['items']
        assert all(row['association'] == 'unverified' for page in owner.pages.values() for row in page['items'])
    finally:
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['epoch', 'owner', 'source-identity', 'first-page',
                                  'older-page', 'replaced-page', 'receipt-digest',
                                  'proof-retired', 'closed'])
async def test_selected_projection_never_redeems_lost_support_or_rescans(tmp_path, monkeypatch, change):
    from amplifier_web.diagnostics import Diagnostics
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=140)
    service.state, service.default_workspace = {'sessions': [session]}, '/fixture'
    diagnostics = Diagnostics(service)
    try:
        selected = await owner.read_selected('s')
        assert selected['rootProof']['status'] == 'complete'
        assert owner.project(session)['workProjection']['current'] is True
        proof = owner._root_proofs['s']
        page = owner.pages[proof['pages'][0][0]]
        if change == 'epoch':
            owner.invalidate({'s'})
        elif change == 'owner':
            session = dict(session)
            service._session = lambda identity, hydrate=False: session
        elif change == 'source-identity':
            session['nativeIdentity'] = 'another-root'
        elif change in {'first-page', 'older-page'}:
            owner.pages.pop(proof['pages'][0 if change == 'first-page' else -1][0])
        elif change == 'replaced-page':
            owner.pages[page['group']] = dict(page)
        elif change == 'receipt-digest':
            page['_proof']['receiptDigest'] = 'invalid'
        elif change == 'proof-retired':
            owner._root_proofs.pop('s')
        else:
            service.closed = True
        calls = len(owner.projector.inputs)
        def forbidden(*args, **kwargs):
            raise AssertionError('Passive Work projection started source/index IO')
        with monkeypatch.context() as guard:
            guard.setattr(owner, '_matches', forbidden)
            guard.setattr(owner, '_binding_current', forbidden)
            guard.setattr(owner, '_worker', forbidden)
            for _ in range(3):
                projected = owner.project(session)
                assert projected['workProjection']['current'] is False
                assert not any(group.get('bindingProvenance')
                               for group in projected['execution']['segments'])
        assert len(owner.projector.inputs) == calls
        # A projected false result must not overwrite a newer status or mint a
        # replacement receipt. No user operation was performed by the snapshot.
        assert owner.status['s']['receipt'] == page['receipt']
    finally:
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', ['complete', 'incomplete'])
async def test_current_raw_display_is_distinct_from_association_completeness(tmp_path, monkeypatch, limit):
    from amplifier_web.diagnostics import Diagnostics
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=901 if limit == 'incomplete' else 140)
    service.state, service.default_workspace = {'sessions': [session]}, '/fixture'
    diagnostics = Diagnostics(service)
    try:
        await owner.read('s')
        raw = owner.project(session)['workProjection']
        assert raw['current'] is True and raw['receipt']['scope'] == 'recent-events'
        selected = await owner.read_selected('s')
        assert selected['rootProof']['status'] == limit
        assert selected['rootProof']['boundCount'] == 0  # Body-only records have no attested ownership.
        projection = owner.project(session)
        assert projection['workProjection']['current'] is True
        receipt = projection['workProjection']['receipt']
        assert receipt == owner.pages[owner.current['s']]['receipt']
        assert receipt['scope'] == 'root-proof' and receipt['historyComplete'] is False
        assert receipt['proofId'] == selected['rootProof']['id']
        assert set(receipt) == {'hostInstanceId', 'sessionId', 'generation', 'scope',
                                'windowKey', 'historyComplete', 'proofId', 'itemCount'}
        assert projection['execution']['segments']
        assert all(row.get('observationWindow') for row in projection['execution']['segments'])
        before = len(owner.projector.inputs)
        owner.pages.pop(owner._root_proofs['s']['pages'][-1][0])
        # An incomplete proof can describe current raw bytes, but NOT after a
        # supporting page disappears. Completion status alone is not a fence.
        assert owner.project(session)['workProjection']['current'] is False
        assert len(owner.projector.inputs) == before
    finally:
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
async def test_unpublished_proof_page_cannot_reuse_a_raw_display_receipt(tmp_path, monkeypatch):
    from amplifier_web.diagnostics import Diagnostics
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=140)
    service.state, service.default_workspace = {'sessions': [session]}, '/fixture'
    diagnostics = Diagnostics(service)
    try:
        await owner.read('s')
        old_receipt = dict(owner.project(session)['workProjection']['receipt'])
        proof = await owner.prove('s')
        assert proof['status'] == 'complete'
        page = owner.pages[owner.current['s']]
        assert page['_rootProofId'] == proof['id']
        assert '_consumerProof' not in page and 'receipt' not in page
        projected = owner.project(session)
        assert projected['workProjection']['current'] is False
        assert projected['workProjection']['receipt'] == old_receipt
        assert all(g.get('observationWindow') for g in projected['execution']['segments'])
        before = len(owner.projector.inputs)
        selected = await owner.read_selected('s')  # Publish the existing proof, no new scan.
        assert selected['rootProof']['id'] == proof['id']
        assert owner.project(session)['workProjection']['current'] is True
        assert len(owner.projector.inputs) == before
    finally:
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['source', 'index'])
async def test_known_physical_proof_refusal_is_visible_without_snapshot_io(tmp_path, monkeypatch, change):
    from amplifier_web.diagnostics import Diagnostics
    owner, session, path, service = fixture(tmp_path, monkeypatch, count=140)
    service.state, service.default_workspace = {'sessions': [session]}, '/fixture'
    diagnostics = Diagnostics(service)
    try:
        selected = await owner.read_selected('s')
        proof_id = selected['rootProof']['id']
        if change == 'source':
            with path.open('a') as stream:
                stream.write('{}\n')
        else:
            with diagnostics._db() as db:
                db.execute("INSERT INTO records(id,at,stream,session,workspace,event,data) VALUES('new',0,'tools','s','/fixture','tool:post','{}')")
        with pytest.raises(ValueError, match='root proof'):
            owner.proof_nodes('s', proof_id)  # An explicit read detects the physical change.
        before = len(owner.projector.inputs)
        def forbidden(*args, **kwargs):
            raise AssertionError('Snapshot repeated the physical proof check')
        with monkeypatch.context() as guard:
            guard.setattr(owner, '_matches', forbidden)
            guard.setattr(owner, '_binding_current', forbidden)
            projected = owner.project(session)['workProjection']
            assert projected['current'] is False and projected['rootProof']['status'] == 'stale'
        assert len(owner.projector.inputs) == before
    finally:
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
async def test_root_proof_ninth_page_is_incomplete_and_explicit_reopen_is_bounded(tmp_path, monkeypatch):
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=901)
    from amplifier_web.diagnostics import Diagnostics
    service.state = {'sessions': [session]}
    service.default_workspace = '/fixture'
    diagnostics = Diagnostics(service)
    try:
        result = await owner.prove('s')
        assert result['status'] == 'incomplete' and result['reason'] == 'page-limit'
        assert result['steps'] == len(owner.projector.inputs) == len(owner.pages) == 8
        assert owner.request('s') is None
        assert (await owner.prove('s')) == result
        assert len(owner.projector.inputs) == 8
        reopened = await owner.prove('s', reopen=True)
        assert reopened['id'] != result['id'] and reopened['steps'] == 8
        assert reopened['status'] == 'incomplete' and len(owner.pages) <= 8
        assert len(owner.projector.inputs) == 16  # Two distinct explicit finite operations, not a ninth step.
    finally:
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['source', 'index', 'owner', 'epoch', 'eviction'])
async def test_root_proof_expires_all_support_after_completed_or_inflight_change(tmp_path, monkeypatch, mutation):
    owner, session, path, service = fixture(tmp_path, monkeypatch, count=140)
    from amplifier_web.diagnostics import Diagnostics
    service.state = {'sessions': [session]}
    service.default_workspace = '/fixture'
    diagnostics = Diagnostics(service)
    try:
        result = await owner.prove('s')
        assert result['status'] == 'complete'
        if mutation == 'source':
            with path.open('a') as stream:
                stream.write('{}\n')
        elif mutation == 'index':
            with diagnostics._db() as db:
                db.execute("INSERT INTO records(id,at,stream,session,workspace,event,data) VALUES('new',0,'tools','s','/fixture','tool:post','{}')")
        elif mutation == 'owner':
            other = dict(session)
            service._session = lambda identity, hydrate=False: other
        elif mutation == 'epoch':
            owner.invalidate({'s'})
        else:
            owner.pages.pop(next(iter(owner.pages)))
        with pytest.raises(ValueError, match='proof'):
            owner.proof_nodes('s', result['id'])
        assert owner.project(session)['workProjection']['rootProof']['status'] == 'stale'
        before = len(owner.projector.inputs)
        assert (await owner.prove('s'))['status'] == 'stale'
        assert owner.request('s') is None
        assert len(owner.projector.inputs) == before
    finally:
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
async def test_root_proof_aggregate_deadline_keeps_late_worker_owned_without_publication(tmp_path, monkeypatch):
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=140)
    from amplifier_web.diagnostics import Diagnostics
    import amplifier_web.work_windows as windows_module
    service.state = {'sessions': [session]}
    service.default_workspace = '/fixture'
    diagnostics = Diagnostics(service)
    entered, release = asyncio.Event(), asyncio.Event()
    project = owner.projector.project
    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await project(*args, **kwargs)
    monkeypatch.setattr(owner.projector, 'project', held)
    monkeypatch.setattr(windows_module, 'PROOF_SECONDS', .05)
    task = asyncio.create_task(owner.prove('s'))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(asyncio.shield(task), 1)
        assert result['status'] == 'incomplete' and result['reason'] == 'deadline'
        assert owner._proof_draining and not owner.pages
        release.set()
        await asyncio.gather(*tuple(owner._proof_draining), return_exceptions=True)
        assert not owner.pages
        assert owner.project(session)['workProjection']['rootProof']['reason'] == 'deadline'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
async def test_same_root_proof_waiters_join_and_owner_epoch_change_rejects_late_step(tmp_path, monkeypatch):
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=140)
    from amplifier_web.diagnostics import Diagnostics
    service.state = {'sessions': [session]}
    service.default_workspace = '/fixture'
    diagnostics = Diagnostics(service)
    entered, release = asyncio.Event(), asyncio.Event()
    original = owner.projector.project
    calls = []
    async def held(*args, **kwargs):
        calls.append(args)
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(owner.projector, 'project', held)
    first = asyncio.create_task(owner.prove('s'))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(owner.prove('s'))
        owner.invalidate({'s'})
        release.set()
        a, b = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert a['id'] == b['id'] and a['status'] == b['status'] == 'stale'
        assert len(calls) == 1 and not owner.pages
    finally:
        release.set()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['source', 'index', 'owner', 'epoch', 'eviction'])
async def test_cumulative_proof_rechecks_dependencies_after_each_real_page_await(tmp_path, monkeypatch, mutation):
    owner, session, path, service = fixture(tmp_path, monkeypatch, count=140)
    from amplifier_web.diagnostics import Diagnostics
    service.state = {'sessions': [session]}
    service.default_workspace = '/fixture'
    diagnostics = Diagnostics(service)
    entered, release = asyncio.Event(), asyncio.Event()
    original = owner.projector.project
    async def held(*args, **kwargs):
        value = await original(*args, **kwargs)
        if len(owner.pages) == 1:
            entered.set()
            await release.wait()
        return value
    monkeypatch.setattr(owner.projector, 'project', held)
    task = asyncio.create_task(owner.prove('s'))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert len(owner.pages) == 1 and len(owner.projector.inputs) == 2
        if mutation == 'source':
            with path.open('a') as stream:
                stream.write('{}\n')
        elif mutation == 'index':
            with diagnostics._db() as db:
                db.execute("INSERT INTO records(id,at,stream,session,workspace,event,data) VALUES('late',0,'tools','s','/fixture','tool:post','{}')")
        elif mutation == 'owner':
            replacement = dict(session)
            service._session = lambda identity, hydrate=False: replacement
        elif mutation == 'epoch':
            owner.invalidate({'s'})
        else:
            owner.pages.pop(next(iter(owner.pages)))
        release.set()
        result = await asyncio.wait_for(asyncio.shield(task), 2)
        assert result['status'] == 'stale'
        assert len(owner.projector.inputs) == 2  # No third scan to recover a newer generation.
        with pytest.raises(ValueError, match='proof'):
            owner.proof_nodes('s', result['id'])
        assert not any(page.get('_rootProofBound') for page in owner.pages.values())
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
async def test_explicit_proof_entry_point_and_existing_jobs_share_one_four_job_ceiling(tmp_path, monkeypatch):
    owner, session, _, _ = fixture(tmp_path, monkeypatch, count=1)
    # Pending slots stand for already admitted work, not a separate proof pool.
    placeholders = [asyncio.get_running_loop().create_future() for _ in range(4)]
    owner.pending.update((str(i), value) for i, value in enumerate(placeholders))
    try:
        with pytest.raises(ValueError, match='busy'):
            await owner.read('s', proof=True)
        assert not owner._root_proofs and not owner.projector.inputs
        with pytest.raises(ValueError, match='explicit latest'):
            await owner.read('s', before='older', proof=True)
        with pytest.raises(ValueError, match='explicit latest'):
            await owner.read('s', _initial=True, proof=True)
        assert owner.project(session)['workProjection'].get('rootProof') is None
    finally:
        owner.pending.clear()
        for future in placeholders:
            future.cancel()
        await owner.close()


@pytest.mark.asyncio
async def test_raw_cached_read_refuses_page_replaced_by_explicit_root_proof(tmp_path, monkeypatch):
    from amplifier_web.diagnostics import Diagnostics
    from amplifier_web.work_windows import _SupersededRead
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=140)
    service.state, service.default_workspace = {'sessions': [session]}, '/fixture'
    diagnostics = Diagnostics(service)
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        await owner.read('s')
        group = owner.current['s']
        cached = owner.pages[group]
        original = asyncio.to_thread
        async def held(function, *args, **kwargs):
            entered.set()
            await release.wait()
            return await original(function, *args, **kwargs)
        monkeypatch.setattr(asyncio, 'to_thread', held)
        task = asyncio.create_task(owner.read('s', _initial=True))
        await asyncio.wait_for(entered.wait(), 2)
        result = await owner.prove('s')
        assert result['status'] == 'complete' and owner.pages[group] is not cached
        release.set()
        with pytest.raises(_SupersededRead):
            await asyncio.wait_for(asyncio.shield(task), 2)
        assert owner.status['s']['status'] != 'retrying'
    finally:
        release.set()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['epoch', 'eviction'])
async def test_proof_waiter_revalidates_after_task_completion(tmp_path, monkeypatch, mutation):
    from amplifier_web.diagnostics import Diagnostics
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=140)
    service.state, service.default_workspace = {'sessions': [session]}, '/fixture'
    diagnostics = Diagnostics(service)
    original = owner._prove
    completed = []
    async def complete_then_supersede(*args):
        result = await original(*args)
        assert result['status'] == 'complete'
        completed.append(result)
        def supersede():
            if mutation == 'epoch':
                owner.invalidate({'s'})
            else:
                owner.pages.pop(next(iter(owner.pages)))
        asyncio.get_running_loop().call_soon(supersede)
        return result
    monkeypatch.setattr(owner, '_prove', complete_then_supersede)
    try:
        result = await owner.prove('s')
        assert completed and result['id'] == completed[0]['id']
        assert result['status'] == 'stale' and result['boundCount'] == 0
    finally:
        await owner.close()
        await diagnostics.close()


@pytest.mark.asyncio
async def test_proof_deadline_checked_after_final_dependency_validation(tmp_path, monkeypatch):
    from amplifier_web.diagnostics import Diagnostics
    from amplifier_web import event_window
    owner, session, _, service = fixture(tmp_path, monkeypatch, count=140)
    service.state, service.default_workspace = {'sessions': [session]}, '/fixture'
    diagnostics = Diagnostics(service)
    validated = []
    original_validate, original_current = event_window.validate_root_proof, owner._binding_current
    def validate(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        assert result['status'] == 'complete'
        validated.append(True)
        return result
    def current(page):
        result = original_current(page)
        if validated:
            owner._root_proofs['s']['deadline'] = 0  # Expire only at final publication boundary.
        return result
    monkeypatch.setattr(event_window, 'validate_root_proof', validate)
    monkeypatch.setattr(owner, '_binding_current', current)
    try:
        result = await owner.prove('s')
        assert validated == [True]
        assert result['status'] == 'incomplete' and result['reason'] == 'deadline'
        assert not any(page.get('_rootProofBound') for page in owner.pages.values())
    finally:
        await owner.close()
        await diagnostics.close()
