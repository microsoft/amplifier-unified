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
@pytest.mark.parametrize('report', ['absent', 'saved', 'cleared'])
async def test_loading_work_window_defers_terminal_workers_without_losing_evidence(tmp_path, monkeypatch, report):
    from amplifier_web import browser_detail
    from amplifier_web.browser_state import SnapshotCopies

    class Report(str):
        def __deepcopy__(self, memo):
            raise AssertionError('A deferred terminal report was copied')

    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=1)
    original_source = path.read_bytes()
    expected_plan = None if report == 'absent' else {'items': (
        [{'content': 'Last reported root checklist', 'status': 'in_progress'}]
        if report == 'saved' else [])}
    if expected_plan is not None:
        session['_reportedPlan'] = json.loads(json.dumps(expected_plan))

    def assert_report(view):
        assert view['execution']['plan'] == expected_plan
        assert '_reportedPlan' not in view
        if expected_plan is not None:
            assert view['execution']['plan'] is not session['_reportedPlan']
            assert view['execution']['plan']['items'] is not session['_reportedPlan']['items']
            assert all(projected is not saved for projected, saved in
                       zip(view['execution']['plan']['items'], session['_reportedPlan']['items']))
    statuses = ['completed'] * 201 + ['cancelled'] * 5 + ['error'] + ['interrupted'] * 3
    session['workers'] = [{'id': f'w-{i}', 'callId': f'c-{i % 133}',
        'kind': 'job' if i < 109 else 'session', 'name': f'Worker {i}', 'status': status,
        'report': Report('Retained report ' + 'x' * 700),
        'reportReceipts': [{'id': f'report-{i}', 'text': 'Durable receipt'}]}
        for i, status in enumerate(statuses)]
    session.update(status='working', activity={'phase': 'tools', 'label': 'Current work',
        'activeTools': [{'callId': 'current-tool', 'tool': 'read', 'startedAt': 1}]},
        runtimeEvents=[{'type': 'runtime.tool', 'phase': 'pre', 'callId': 'current-tool', 'tool': 'read'}])
    original = json.dumps(session, sort_keys=True)
    legacy = browser_detail.project(session)
    assert legacy['execution']['plan'] == expected_plan and '_reportedPlan' not in legacy
    owner.projector.gate = asyncio.Event()
    pending = owner.request('s')
    try:
        await asyncio.sleep(0)
        assert not pending.done()
        overlay = owner.project(session)
        assert overlay['workProjection'] == {'status': 'loading', 'current': False}
        assert all(not overlay['execution'][key] for key in ('nodes', 'turns', 'segments'))

        def unexpected_hash(text):
            raise AssertionError('A deferred terminal report was hashed')

        with monkeypatch.context() as patch:
            patch.setattr(browser_detail, 'digest', unexpected_hash)
            projected = browser_detail.project(session, work_overlay=overlay)
            assert_report(projected)  # SnapshotCopies must not hide a projection alias.
            loading = SnapshotCopies().detach(projected, 'web')
        assert loading['workers'] == []
        assert loading['workerSummary']['bounded'] is True
        assert loading['workerSummary']['total'] == loading['workerSummary']['finished'] == 210
        assert len(loading['workerSummary']['recentFinished']) == 5
        assert loading['workProjection'] == overlay['workProjection']
        assert loading['executionWindow'] == overlay['executionWindow']
        # WS3 Plan is a saved display value independent of Work readiness:
        # absent legacy reports are explicit null; a valid clear keeps items[].
        assert 'plan' not in overlay['execution']
        assert loading['execution'] == {**overlay['execution'], 'plan': expected_plan}
        assert_report(loading)
        assert loading['status'] == session['status']
        assert loading['activity'] == session['activity'] and loading['runtimeEvents'] == session['runtimeEvents']
        assert len(json.dumps(loading).encode()) < len(json.dumps(legacy).encode()) / 10
        assert json.dumps(session, sort_keys=True) == original

        explicit = browser_detail.page(session, 'workers')
        assert explicit['total'] == 210 and len(explicit['items']) == 100
        reference = explicit['items'][-1]['reportDetail']
        assert browser_detail.read_text(session, reference)['value'] == session['workers'][-1]['report']

        # Active/unknown observations and the terminal override for the same
        # call remain available to the existing controls during loading.
        retained = [{'id': state, 'status': state, 'kind': 'session'}
                    for state in ('running', 'idle', 'pending', 'new-state')]
        retained += [{'id': 'late', 'callId': 'mixed', 'kind': 'session', 'status': 'working'},
                     {'id': 'done', 'callId': 'mixed', 'kind': 'job',
                      'event': 'job.returned', 'status': 'completed'},
                     {'status': 'completed', 'name': 'Unidentified observation'}]
        session['workers'].extend(retained)
        with monkeypatch.context() as patch:
            patch.setattr(browser_detail, 'digest', unexpected_hash)
            current = browser_detail.project(session, work_overlay=overlay)
        assert current['workers'] == retained
        assert current['workerSummary']['total'] == 217
        assert loading['workerSummary']['total'] == 210 and loading['workers'] == []
        assert_report(current)

        owner.projector.gate.set()
        await pending
        ready_overlay = owner.project(session)
        ready = browser_detail.project(session, work_overlay=ready_overlay)
        assert ready['workProjection']['status'] == 'ready'
        assert ready['execution']['turns'] and ready['workerSummary']['bounded']
        assert ready['workers'] == retained
        assert ready['execution'] == {**ready_overlay['execution'], 'plan': expected_plan}
        assert_report(ready)
        if expected_plan is not None:
            # Mutating a detached browser result cannot change the saved value
            # or another observation, including the explicit empty-list case.
            loading['execution']['plan']['items'].append({'content': 'Not saved', 'status': 'pending'})
            assert ready['execution']['plan'] == current['execution']['plan'] == session['_reportedPlan'] == expected_plan
            projected['execution']['plan']['items'].append({'content': 'Also not saved', 'status': 'pending'})
            assert ready['execution']['plan'] == session['_reportedPlan'] == expected_plan
            ready['execution']['plan']['items'].append({'content': 'Still not saved', 'status': 'pending'})
            assert current['execution']['plan'] == session['_reportedPlan'] == expected_plan
        assert json.dumps({**session, 'workers': session['workers'][:210]}, sort_keys=True) == original
        assert path.read_bytes() == original_source
    finally:
        await owner.close()


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
    # Settle initial catalog work; the lock below also fences later refreshes.
    # Joining only transcript readers cannot exclude catalog publication.
    await service.history.refresh(force=False)
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
        # Catalog refresh owns this lock through publication. Work completion,
        # transcript loading and HTTP reads do not acquire it; they remain real.
        async with service.history.lock:
            # Initial transcript reads notify this same outer cache. Join their
            # per-session owners before isolating the Work notification below.
            async with asyncio.timeout(5):
                for identity in (sid, other_sid):
                    await service.history.load(identity, only_if_changed=True, display=True)
            # Startup diagnostics independently publishes a global frame. Settle
            # that first snapshot before isolating Work's status-only notification.
            await service.diagnostics.publish()
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
            with service.clients.bind('unrelated-web'):
                unrelated = service.browser_state()
            unrelated_token = service.shell.change_token('unrelated-web')
            selected_token = service.shell.change_token('notice-web')
            shared_revision = service._state['revision']
            catalog = service.projections.sessions(service._state)
            projector.gate.set()
            await asyncio.wait_for(asyncio.shield(read), timeout=5)
            assert windows.status[sid]['current'] is True and windows.status[sid]['status'] == 'ready'
            # The negative interval must not be rescued by a catalog/global frame
            # or another notifier while the physical Work read completes.
            assert service._state['revision'] == shared_revision
            assert service.projections.sessions(service._state) is catalog
            assert service.shell.change_token('notice-web') == selected_token
            assert service._client_snapshots['unrelated-web'] is unrelated
            assert service.shell.change_token('unrelated-web') == unrelated_token
            with service.clients.bind('notice-web'):
                stale = service.browser_state()
            assert stale is cached_before
            stale_session = next(row for row in stale['sessions'] if row['id'] == sid)
            assert stale_session['workProjection']['current'] is False
            monkeypatch.setattr(service.event_log_view, '_notify_work', original_notice)
            original_notice(sid)
            assert 'notice-web' not in service._client_snapshots
            assert service.shell.change_token('notice-web') != selected_token
            assert service._client_snapshots['unrelated-web'] is unrelated
            assert service.shell.change_token('unrelated-web') == unrelated_token
            assert service._state['revision'] == shared_revision
            assert service.projections.sessions(service._state) is catalog
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
            assert projected['workProjection']['receipt'] == windows.status[sid]['receipt']
            assert service._state['revision'] == shared_revision
            assert service.projections.sessions(service._state) is catalog
            assert service._client_snapshots['unrelated-web'] is unrelated
            assert service.shell.change_token('unrelated-web') == unrelated_token
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
async def test_oversized_legacy_placement_restores_qualified_model_steps_from_cold_owner(tmp_path, monkeypatch):
    import sqlite3
    from amplifier_web import saved_execution_window as legacy
    from amplifier_web.cold_display import ColdRecord, MARKER, execution_reference
    from amplifier_web.resource_files import detach

    owner, initial, _, service = fixture(tmp_path, monkeypatch, count=1)
    db = service.db = sqlite3.connect(tmp_path / 'state.sqlite')
    db.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    turn = {'id': 't', 'anchorMessageId': 'user', 'inputId': 'user', 'phase': 'completed',
            'startedAt': 1, 'endedAt': 500}
    model = {'id': 'last', 'kind': 'llm', 'turnId': 't', 'provider': 'anthropic', 'model': 'example',
             'sessionId': 's', 'rootSessionId': 's', 'producerId': 'worker', 'liveObservation': True,
             'phase': 'completed', 'startedAt': 200, 'endedAt': 201, 'usage': {'inputTokens': 17}}
    # Preserve an app-only tool containing a very large legacy payload next to a
    # genuine model receipt, and reject a canonical row with no host placement.
    nodes = [dict(model, id=f'model-{i}', startedAt=i+1, endedAt=i+2) for i in range(130)]
    large_id = 'tool-' + 'k' * 214
    nodes += [{'id': large_id, 'kind': 'tool', 'turnId': 't', 'label': 'Build', 'startedAt': 199,
               'input': 'private tool body' * 270000}, model, dict(model, id='canonical', canonicalHistory=True)]
    old_turn = {**turn, 'id': 'older', 'startedAt': 0}
    nodes[0]['turnId'] = 'older'
    turns = [old_turn] + [{**turn, 'id': f'unrelated-{i}'} for i in range(23)] + [turn]
    reference = execution_reference(db, {'nodes': nodes, 'turns': turns, 'plan': {'obsolete': True},
                                        'retiredUsageNodes': [model], 'currentTurnId': 't'})
    assert reference['bytes'] > legacy.PAGE_BYTES
    reference.pop('bytes')  # Historical references need only the content identity.
    initial.pop('execution')
    session = ColdRecord({**initial, MARKER: {'execution': reference}}, db)
    service._session = lambda identity, hydrate=False: session
    try:
        await owner.read('s', _initial=True)
        frame = owner.project(session)
        assert frame['workProjection']['status'] == 'ready'
        assert 'plan' not in frame['execution']
        group = next(row for row in frame['execution']['segments'] if row['turnId'] == 't')
        assert group['partialWindow'] and group['nodeCounts'] is None
        rows = owner.live_pages.read(session, group['id'])['items']
        assert len(rows) == 100
        assert {row['id'] for row in rows} >= {'last', large_id}
        assert next(row for row in rows if row['id'] == 'last')['usage'] == {'inputTokens': 17}
        assert all(row['id'] != 'canonical' for row in rows)
        assert 'private tool body' not in json.dumps([frame, rows])
        assert frame['executionWindow']['unavailableRanges'][-1]['source'] == 'saved-execution'
        saved = owner.resident_tree(session)
        assert saved['nodes'][0]['id'] == 'model-0' and saved['nodes'][-1]['id'] == 'last'
        with pytest.raises(ValueError):
            saved['nodes'][:130]
        oldest = owner.live_pages.read_groups(session, frame['executionWindow']['before'])
        assert 'older' in {turn['id'] for turn in oldest['turns']}
        earlier = next(row for row in oldest['items'] if row['turnId'] == 'older')
        assert [item['id'] for item in owner.live_pages.read(session, earlier['id'])['items']] == ['model-0']
        assert 'retiredUsageNodes' not in saved
        assert not dict.__contains__(session, 'execution') and session[MARKER]['execution'] == reference
        # Explicit new event page with the same pinned immutable input reuses the
        # accepted display pages without a second full decode or a database write.
        with monkeypatch.context() as patch:
            patch.setattr(legacy, 'open_tree', lambda *args: pytest.fail('legacy snapshot decoded twice'))
            await owner.read('s')
        assert owner.resident_tree(session) is saved
        assert db.execute('SELECT count(*) FROM state_resources').fetchone()[0] == 1
        source = detach(db, reference['$resource'])
        with (source.directory / (source.identity + '.json')).open('r+b') as stream:
            stream.write(b'x')
        # A third read rejects altered placement yet still admits the
        # independently verified native tool event.
        native = await owner.read('s')
        assert owner.resident_tree(session) == {}
        assert native['unavailableRanges'][-1]['reason'] == 'source_unavailable'
        assert len(owner.nodes('s', native['items'][0]['id'])['items']) == 1
    finally:
        await owner.close()
        db.close()
