"""A stopped settings-recovery fence is not executing work.

Real persisted host restart and MCP discovery/switch; no provider calls, user
configuration, or conversation execution. Unknown effects remain unknown.
"""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService, AppError
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.update_blockers import blockers
from amplifier_web.updates import UpdateManager
from test_smart_tool_update_cleanup import OLD, configure, package
from test_updates import app, repo, prepare, git  # noqa: F401 — isolated Git/app fixtures


@pytest.fixture
async def held(tmp_path, monkeypatch, request):
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'absent'))
    home = tmp_path / 'app'
    first = AppService(home, RuntimeManager(), workspace=tmp_path)
    first.management = Management(first)
    first.update_manager = UpdateManager(first)
    await first.dispatch('session.create', {})
    session = first._session()
    sid = session['id']
    session.update(status='error', pendingConfiguration={'phase': 'error', 'error': 'Old interrupted change'})
    first._message(session, 'user', 'Retained synthetic conversation history')
    if getattr(request, 'param', False):
        session.update(configurationPending=True, configurationPendingRevision=7,
                       configurationRefresh={'phase': 'pending', 'revision': 7})
    path = first.management.queued_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b'{"tools": [{"module": "retained-old-tool"}]}\n'
    path.write_bytes(payload)
    await configure(first)
    await package(first.smart_tools, 'b' * 24, 'b' * 40, staged=True)
    await first.update_manager.publish(phase='staged', pendingSmartTools=[{'previous': OLD, 'target': 'b' * 24}])
    first._save_changes(sessions={sid})
    await first.close()

    app = AppService(home, RuntimeManager(), workspace=tmp_path)
    from unittest.mock import AsyncMock
    stop = AsyncMock(wraps=app.runtime.stop)
    monkeypatch.setattr(app.runtime, 'stop', stop)
    app.management = Management(app)
    app.smart_tools = SmartToolsManager(app)
    app.update_manager = UpdateManager(app)
    # Observe the actual startup refresh, not a manually repaired session.
    if app.tasks:
        await asyncio.wait_for(asyncio.gather(*list(app.tasks)), 5)
    stop.assert_not_awaited()
    assert app._session(sid)['configurationBusy']
    assert app._session(sid)['pendingConfiguration']['recoveryRequired']
    assert not app.runtime.has_pending_operations()
    yield app, sid, path, payload
    await app.close()


@pytest.mark.parametrize('held', [False, True], indirect=True, ids=['legacy', 'pending-freshness'])
async def test_cold_legacy_hold_allows_tool_update_but_retains_chat_fence_and_payload(held, monkeypatch):
    from unittest.mock import AsyncMock
    app, sid, path, payload = held
    session = app._session(sid)
    assert not session.get('configurationPending'), 'Cold legacy hold retained obsolete runtime freshness'
    assert 'configurationRefresh' not in session
    original = copy.deepcopy(session['pendingConfiguration'])
    history = copy.deepcopy(session['messages'])
    fence = session.get('configurationOperationFence')
    stop = AsyncMock(side_effect=AssertionError('No mounted runtime may be stopped or started'))
    apply = AsyncMock(side_effect=AssertionError('Unknown configuration must never be replayed'))
    monkeypatch.setattr(app.runtime, 'stop', stop)
    monkeypatch.setattr(app.management, 'apply_queued', apply)
    report = (await app.dispatch('updates.diagnostics', {}, include_state=False))['result']
    assert not report['activationGate']['blocked']
    assert report['activationGate']['configurationHolds'][0]['reasonCode'] == 'idle-configuration-hold'
    await app.smart_tools.connect('board')
    assert not app.update_manager.busy()
    await app.update_manager.activateSmartTools()
    assert app.smart_tools._server('board')['installationId'] == 'b' * 24
    assert (await app.smart_tools.call_tool('board', 'view_only', {}, origin='app'))['structuredContent']['view'] == 'compact'
    assert not app.state['updates']['pendingSmartTools']
    assert session['configurationBusy'] and session['pendingConfiguration'] == original
    assert session['messages'] == history and session.get('configurationOperationFence') == fence
    assert path.read_bytes() == payload
    assert not (app.data_dir / 'tool-effects').exists()
    assert not app.runtime.workers
    stop.assert_not_awaited()
    apply.assert_not_awaited()
    with pytest.raises(AppError, match='settings|configuration|Configuration'):
        await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Must stay held'})
    assert not app.runtime.workers
    home, workspace = app.data_dir, app.default_workspace
    await app.close()
    reopened = AppService(home, RuntimeManager(), workspace=workspace)
    reopened.management = Management(reopened)
    reopened.smart_tools = SmartToolsManager(reopened)
    reopened.update_manager = UpdateManager(reopened)
    try:
        assert reopened._session(sid)['configurationBusy']
        assert reopened._session(sid)['pendingConfiguration'] == original
        assert not reopened._session(sid).get('configurationPending')
        assert reopened._session(sid)['messages'] == history
        assert reopened._session(sid).get('configurationOperationFence') == fence
        assert reopened._session(sid).get('configurationPendingRevision') == session.get('configurationPendingRevision')
        assert path.read_bytes() == payload
        with pytest.raises(AppError, match='settings|configuration|Configuration'):
            await reopened.dispatch('conversation.send', {'sessionId': sid, 'text': 'Still held after restart'})
        assert reopened.smart_tools._server('board')['installationId'] == 'b' * 24
        assert not reopened.update_manager.busy()
        assert not reopened.runtime.workers
        report = (await reopened.dispatch('updates.diagnostics', {}, include_state=False))['result']
        assert not report['activationGate']['blocked']
        assert reopened._session(sid)['pendingConfiguration'] == original
        assert path.read_bytes() == payload
    finally:
        await reopened.close()


@pytest.mark.parametrize('guard', [
    'mounted-idle', 'mounted-exited', 'preparing', 'other-inflight',
    'settings-task', 'cancelling-task', 'management-lock',
    'refresh-applying', 'refresh-error', 'refresh-queued', 'queued', 'applying',
    'modern-operation', 'fence', 'handoff', 'pending-handoff', 'running-turn',
    'queued-child', 'working-child', 'persistent-child', 'working',
    'runtime-absent', 'workers-unavailable', 'runtime-observer-unavailable',
    'runtime-observer-error', 'runtime-observer-unknown', 'tasks-unavailable', 'lock-unavailable',
    'ownership-unavailable', 'ownership-unknown', 'ownership-blocked',
])
async def test_held_refresh_never_retires_active_or_unobserved_state(held, monkeypatch, guard):
    from unittest.mock import AsyncMock
    app, sid, path, payload = held
    session = app._session(sid)
    session.update(configurationPending=True, configurationPendingRevision=7,
                   configurationRefresh={'phase': 'pending', 'revision': 7})
    with monkeypatch.context() as patch:
        stop = AsyncMock(side_effect=AssertionError('A held runtime must not be retired'))
        apply = AsyncMock(side_effect=AssertionError('Unknown configuration must not be applied'))
        drain = AsyncMock(side_effect=AssertionError('A held conversation must not drain input'))
        patch.setattr(app.runtime, 'stop', stop)
        patch.setattr(app.management, 'apply_queued', apply)
        patch.setattr(app.collaboration, 'drain', drain)
        task = None
        release = asyncio.Event()
        if guard in {'mounted-idle', 'mounted-exited', 'other-inflight'}:
            identity = 'other-fixture' if guard == 'other-inflight' else sid
            patch.setattr(app.runtime, 'workers', {identity: {
                'inflight': guard == 'other-inflight',
                'process': SimpleNamespace(returncode=0 if guard == 'mounted-exited' else None)}})
        elif guard == 'preparing':
            lock = asyncio.Lock()
            await lock.acquire()
            patch.setattr(app.runtime, '_locks', {sid: lock})
        elif guard in {'settings-task', 'cancelling-task'}:
            async def pending():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
            task = asyncio.create_task(pending())
            patch.setattr(app.management, 'pending_config_tasks', {sid: task})
            await asyncio.sleep(0)
            if guard == 'cancelling-task':
                task.cancel()
                await asyncio.sleep(0)
        elif guard == 'management-lock':
            lock = asyncio.Lock()
            await lock.acquire()
            patch.setattr(app.management, 'lock', lock)
        elif guard.startswith('refresh-'):
            session['configurationRefresh']['phase'] = guard.removeprefix('refresh-')
        elif guard in {'queued', 'applying'}:
            session['pendingConfiguration']['phase'] = guard
        elif guard == 'modern-operation':
            session['pendingConfiguration']['operationId'] = 'retained-operation'
        elif guard == 'fence':
            session['configurationOperationFence'] = 'held-operation'
        elif guard in {'handoff', 'pending-handoff'}:
            session['worktreeHandoffs'] = [{'phase': 'unknown' if guard == 'handoff' else 'pending'}]
        elif guard == 'running-turn':
            session['execution'] = {'turns': [{'id': 'fixture-turn', 'phase': 'running'}]}
        elif guard.endswith('-child'):
            session['workers'] = [{'status': {'queued-child': 'queued', 'working-child': 'working',
                'persistent-child': 'idle'}[guard], 'persistent': guard == 'persistent-child'}]
        elif guard == 'working':
            session['status'] = 'working'
        elif guard == 'runtime-absent':
            patch.setattr(app, 'runtime', None)
        elif guard == 'workers-unavailable':
            patch.setattr(app.runtime, 'workers', None)
        elif guard == 'runtime-observer-unavailable':
            patch.setattr(app.runtime, 'has_pending_operations', None)
        elif guard == 'runtime-observer-error':
            def unavailable():
                raise RuntimeError('Unobservable runtime')
            patch.setattr(app.runtime, 'has_pending_operations', unavailable)
        elif guard == 'runtime-observer-unknown':
            patch.setattr(app.runtime, 'has_pending_operations', lambda: None)
        elif guard == 'tasks-unavailable':
            patch.setattr(app.management, 'pending_config_tasks', None)
        elif guard == 'lock-unavailable':
            patch.setattr(app.management, 'lock', None)
        elif guard.startswith('ownership-'):
            session['ownership'] = None if guard == 'ownership-unavailable' else {'status': guard.removeprefix('ownership-')}
        before = copy.deepcopy(session)
        try:
            await app.refresh_configuration(sid)
            assert session == before
            assert path.read_bytes() == payload
            stop.assert_not_awaited()
            apply.assert_not_awaited()
            drain.assert_not_awaited()
        finally:
            release.set()
            if task:
                await task
    # The local refresh retains its marker, but it is not a global update lock.
    # Only the session's actual activity/handoff still blocks after observation
    # is restored. Neither result authorizes recovery or clears the marker.
    assert app.update_manager.busy() == (guard in {
        'handoff', 'pending-handoff', 'running-turn', 'queued-child', 'working-child',
        'persistent-child', 'working'})


@pytest.mark.parametrize('refresh', [None, {'phase': 'pending', 'revision': 7}])
async def test_settled_held_refresh_only_clears_freshness_and_is_idempotent(held, refresh):
    app, sid, path, payload = held
    session = app._session(sid)
    session.update(configurationPending=True, configurationPendingRevision=7, fileAccessPending=True)
    if refresh is not None:
        session['configurationRefresh'] = refresh
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    app.management.pending_config_tasks[sid] = done
    before = copy.deepcopy(session)
    expected = {**before, 'configurationPending': False}
    expected.pop('configurationRefresh', None)
    expected.pop('fileAccessPending')
    await app.refresh_configuration(sid)
    assert session == expected
    assert path.read_bytes() == payload
    assert not app.update_manager.busy()
    await app.refresh_configuration(sid)
    assert session == expected and path.read_bytes() == payload


@pytest.mark.parametrize('guard', ['settings-task', 'cancelling-task', 'management-lock', 'runtime', 'other-runtime',
    'preparation-record', 'mounted-worker', 'handoff', 'pending-handoff', 'working',
    'worker', 'working-worker', 'persistent-worker', 'running-turn', 'unobservable-runtime',
    'unobservable-tasks', 'unobservable-lock'])
async def test_actual_or_unobservable_work_still_holds_update(held, guard):
    app, sid, path, payload = held
    session = app._session(sid)
    task = None
    release = asyncio.Event()
    if guard in {'settings-task', 'cancelling-task'}:
        async def pending():
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        task = asyncio.create_task(pending())
        app.management.pending_config_tasks[sid] = task
        await asyncio.sleep(0)
        if guard == 'cancelling-task':
            task.cancel()
            await asyncio.sleep(0)
    elif guard == 'management-lock':
        await app.management.lock.acquire()
    elif guard == 'runtime':
        app.runtime.has_pending_operations = lambda: True
    elif guard == 'other-runtime':
        app.runtime._locks['other-fixture-chat'] = asyncio.Lock()
        await app.runtime._locks['other-fixture-chat'].acquire()
    elif guard == 'preparation-record':
        app.runtime._preparations[sid] = {'owner': 'fixture'}
    elif guard == 'mounted-worker':
        app.runtime.workers[sid] = {'process': SimpleNamespace(returncode=0)}
    elif guard in {'handoff', 'pending-handoff'}:
        session['worktreeHandoffs'] = [{'phase': 'unknown' if guard == 'handoff' else 'pending'}]
    elif guard == 'working':
        session['status'] = 'working'
    elif guard == 'worker':
        session['workers'] = [{'status': 'running'}]
    elif guard == 'working-worker':
        session['workers'] = [{'status': 'working'}]
    elif guard == 'persistent-worker':
        session['workers'] = [{'status': 'idle', 'persistent': True}]
    elif guard == 'running-turn':
        session['execution'] = {'turns': [{'id': 'running-turn', 'phase': 'running'}]}
    elif guard == 'unobservable-runtime':
        app.runtime.has_pending_operations = None
    elif guard == 'unobservable-tasks':
        app.management.pending_config_tasks = None
    elif guard == 'unobservable-lock':
        app.management.lock = None
    try:
        from amplifier_web.update_blockers import assess
        expected = {
            'settings-task': 'configuration-task-pending', 'cancelling-task': 'configuration-task-pending',
            'management-lock': 'management-lock-held', 'runtime': 'runtime-operations-pending',
            'other-runtime': 'runtime-operations-pending', 'preparation-record': 'runtime-operations-pending',
            'mounted-worker': 'mounted-worker', 'handoff': 'checkout-handoff-unknown',
            'pending-handoff': 'checkout-handoff-pending',
            'working': 'session-not-quiescent', 'worker': 'active-worker', 'working-worker': 'active-worker',
            'persistent-worker': 'persistent-worker', 'running-turn': 'running-turn',
            'unobservable-runtime': 'runtime-observer-unavailable', 'unobservable-tasks': 'management-tasks-unavailable',
            'unobservable-lock': 'management-lock-unavailable',
        }
        observation = assess(app)['observation']
        detail = next(row for row in observation['configurationHolds']
                      if row['sessionRef'] == hashlib.sha256(sid.encode()).hexdigest()[:16])
        assert detail['reasonCode'] == expected[guard]
        assert not detail['quiescent']
        assert app.update_manager.busy()
        await app.update_manager.activateSmartTools()
        assert app.smart_tools._server('board')['installationId'] == OLD
        assert app.state['updates']['pendingSmartTools']
        assert path.read_bytes() == payload
    finally:
        release.set()
        if task:
            await task
        if guard == 'management-lock':
            app.management.lock.release()
        if guard == 'other-runtime':
            app.runtime._locks['other-fixture-chat'].release()
        if guard == 'unobservable-tasks':
            app.management.pending_config_tasks = {}
        if guard == 'unobservable-lock':
            app.management.lock = asyncio.Lock()
        app.runtime._preparations.clear()
        app.runtime.workers.clear()


def test_absent_management_cannot_prove_hold_is_quiescent():
    service = SimpleNamespace(state={'sessions': [{'id': 'chat', 'status': 'error', 'configurationBusy': True,
        'pendingConfiguration': {'phase': 'error', 'recoveryRequired': True}}]},
        runtime=SimpleNamespace(has_pending_operations=lambda: False))
    assert blockers(service)


async def test_public_diagnostics_identifies_global_wait_without_mutation(held, monkeypatch):
    from amplifier_web import update_report
    from amplifier_web import cold_display
    from unittest.mock import AsyncMock, Mock
    app, sid, path, payload = held
    session = app._session(sid)
    session['title'] = 'SECRET_CHAT_TITLE'
    session['pendingConfiguration']['error'] = 'SECRET_ERROR /private/secret/path'
    session['pendingConfiguration']['payload'] = {'api_key': 'SECRET_CREDENTIAL'}
    monkeypatch.setattr(update_report, 'runtime_report', lambda home: {})
    peer = cold_display.ColdRecord({
        'id': 'untouched-cold-peer', 'status': 'idle', 'historyLoaded': False,
        '_coldFields': {key: {'$resource': 'PRIVATE_COLD_SENTINEL'}
                        for key in ('messages', 'configuration', 'execution')},
    }, app.db)
    app.state['sessions'].append(peer)
    load = Mock(side_effect=AssertionError('Diagnostics must not load cold peer fields'))
    hydrate = Mock(side_effect=AssertionError('Diagnostics must not hydrate a peer'))
    history = AsyncMock(side_effect=AssertionError('Diagnostics must not load peer history'))
    original_load, original_hydrate, original_history = cold_display.load, app.cold_display.hydrate, app.history.ensure_loaded
    monkeypatch.setattr(cold_display, 'load', load)
    monkeypatch.setattr(app.cold_display, 'hydrate', hydrate)
    monkeypatch.setattr(app.history, 'ensure_loaded', history)
    await app.management.lock.acquire()
    before = copy.deepcopy({**app.state, 'sessions': [
        cold_display.raw(row) for row in app.state['sessions']]})
    original_files = {str(file.relative_to(app.data_dir)): file.read_bytes()
                      for file in app.data_dir.rglob('*') if file.is_file()}
    try:
        result = await app.dispatch('updates.diagnostics', {}, include_state=False)
        assert result['accepted'] and result['effects'] == []
        report = result['result']
        assert report['busy'] is False  # Updater lock is not management quiescence.
        gate = report['activationGate']
        assert gate['blocked'] and gate['stateRevision'] == before['revision']
        assert gate['basis'] == 'first-failing-predicate'
        assert gate['configurationHolds'] == [{
            'sessionRef': hashlib.sha256(sid.encode()).hexdigest()[:16], 'quiescent': False,
            'reasonCode': 'management-lock-held', 'scope': 'global'}]
        text = json.dumps(report)
        assert all(secret not in text for secret in
                   ('SECRET_CHAT_TITLE', 'SECRET_ERROR', '/private/secret/path', 'SECRET_CREDENTIAL', sid, str(path)))
        assert app.state == before
        assert not app.runtime.workers and not app.management.pending_config_tasks
        assert path.read_bytes() == payload
        load.assert_not_called()
        hydrate.assert_not_called()
        history.assert_not_awaited()
        assert dict.get(peer, '_coldFields') == before['sessions'][-1]['_coldFields']
        assert original_files == {str(file.relative_to(app.data_dir)): file.read_bytes()
                                  for file in app.data_dir.rglob('*') if file.is_file()}
    finally:
        app.management.lock.release()
        app.state['sessions'].remove(peer)
        monkeypatch.setattr(cold_display, 'load', original_load)
        monkeypatch.setattr(app.cold_display, 'hydrate', original_hydrate)
        monkeypatch.setattr(app.history, 'ensure_loaded', original_history)
    report = (await app.dispatch('updates.diagnostics', {}, include_state=False))['result']
    assert not report['activationGate']['blocked']
    assert report['activationGate']['configurationHolds'][0]['reasonCode'] == 'idle-configuration-hold'
    assert session['configurationBusy'] and path.read_bytes() == payload


@pytest.mark.parametrize('unknown', [False, True], ids=['four-saved', 'one-unknown'])
async def test_four_cold_operations_allow_check_install_and_tick_without_recovery(app, repo, monkeypatch, unknown):
    """Real native file writes, cold AppService, Git staging and MCP processes.

    The component qualifier is the existing repository-staging fixture, not a
    full provider graph. No model, live installation or original chat is used.
    """
    from unittest.mock import AsyncMock
    from amplifier_web import updates
    from amplifier_web.configuration_recovery import native_apply, prepare_attempt, authorize, receipt_path, seal
    from amplifier_web.updates import active_release, foundation_home
    from test_queued_configuration_boundary import ControlsTransport, plan

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    app.runtime = RuntimeManager()
    app.management = Management(app)
    manager, root = await prepare(app, repo)
    sources = copy.deepcopy(manager.inventory)
    await configure(app)
    await package(app.smart_tools, 'b' * 24, 'b' * 40, staged=True)
    await manager.publish(phase='staged', pendingSmartTools=[{'previous': OLD, 'target': 'b' * 24}])
    identities = []
    expected = {}
    for number in range(4):
        if number:
            await app.dispatch('session.create', {})
        session = app._session()
        sid = session['id']
        identities.append(sid)
        transport = ControlsTransport(app.data_dir, sid)
        try:
            record = app.management.create_configuration_operation(session, plan(number + 2), f'original-{number}', None, None)
            session.update(configurationBusy=True, configurationOperationFence=record['id'])
            app.management.configuration_transition(session, record['id'], phase='applying', stage='preparing')
            attempt = prepare_attempt(app.management, session, record['id'])

            async def admit(args):
                return authorize(app.management, sid, args)

            await native_apply(transport.controls, plan(number + 2), attempt, admit,
                               lambda: session.get('interruptionRevision', 0))
            effect = 'unknown' if unknown and number == 3 else 'saved_not_mounted'
            if effect == 'unknown':
                receipt_path(attempt, home=app.data_dir).write_text(json.dumps(seal(attempt, 'unknown', terminal=True)))
            app.management.configuration_transition(session, record['id'], phase='error', stage='readback',
                effect=effect, recoveryRequired=True, error='Synthetic saved-versus-loaded mismatch')
            session.update(status='stopped', interruptionRevision=1)
            app._message(session, 'user', f'Original history {number}')
            expected[sid] = {key: copy.deepcopy(session.get(key)) for key in (
                'pendingConfiguration', 'configurationOperationFence', 'messages', 'interruptionRevision', 'status')}
            assert not transport.calls and not transport.inputs
        finally:
            await transport.close()
    app._save_changes(sessions=set(identities))
    files = {str(path.relative_to(app.data_dir)): path.read_bytes()
             for path in (app.data_dir / 'sessions').rglob('*') if path.is_file()}
    home, workspace = app.data_dir, app.default_workspace
    await app.close()

    current = AppService(home, RuntimeManager(), workspace=workspace)
    current.management = Management(current)
    current.smart_tools = SmartToolsManager(current)
    current.update_manager = manager = UpdateManager(current)
    try:
        if current.tasks:
            await asyncio.gather(*list(current.tasks))
        for method in ('start', 'send', 'stop'):
            monkeypatch.setattr(current.runtime, method, AsyncMock(side_effect=AssertionError('No original runtime work')))
        monkeypatch.setattr(current.management, 'apply_queued',
                            AsyncMock(side_effect=AssertionError('No old operation replay')))
        assert not manager.busy(), 'Saved recovery is not executing configuration work'
        assert all(current._session(sid)['configurationBusy'] for sid in identities)
        await current.smart_tools.connect('board')
        staged = copy.deepcopy(current.state['updates']['pendingSmartTools'])
        stage = AsyncMock(side_effect=AssertionError('Do not duplicate the known staged tool'))
        monkeypatch.setattr(current.smart_tools, 'stage_update', stage)
        monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(return_value=[]))
        await manager.check()  # Refresh app availability while keeping the exact tool target.
        assert current.state['updates']['pendingSmartTools'] == staged
        await manager.install()
        assert current.smart_tools._server('board')['installationId'] == 'b' * 24
        assert not current.state['updates']['pendingSmartTools']
        assert not current.state['updates']['blockers']
        assert len(current.smart_tools.state['installations']) == 2
        stage.assert_not_awaited()
        assert not (home / 'tool-effects').exists()

        # Actual check and Git stage, with only discovery inputs/qualification
        # supplied by the maintained synthetic repository fixture.
        original_process = updates.process
        async def process(*args, **kwargs):
            args = tuple(str(repo[0]) if arg == 'https://example.invalid/repo' else arg for arg in args)
            return await original_process(*args, **kwargs)
        monkeypatch.setattr(updates, 'process', process)
        monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(return_value=copy.deepcopy(sources)))
        release = asyncio.Event()
        late_task = None
        async def validate(stage, release_id):
            nonlocal late_task
            assert git(stage / 'foundation/cache/repository', 'rev-parse', 'HEAD') == repo[2]
            late_task = asyncio.create_task(release.wait())
            current.management.pending_config_tasks[identities[0]] = late_task
        monkeypatch.setattr(manager, 'validate', validate)
        # Exercise the idle-requiring adapter path rather than hot generation
        # promotion, which intentionally permits already running work.
        monkeypatch.setattr(current.runtime, 'promote_generation', None)
        await manager.check(tier='other')
        assert current.state['updates']['available'] == 1
        await manager.install()
        assert current.state['updates']['phase'] == 'staged'
        assert not active_release(home)
        assert current.state['updates']['blockers']
        await manager.tick()
        assert not active_release(home)
        release.set()
        await late_task
        await manager.tick()
        assert active_release(home)['current']
        assert git(foundation_home(home) / 'cache/repository', 'rev-parse', 'HEAD') == repo[2]
        assert git(root, 'rev-parse', 'HEAD') == repo[1]
        current.state['settings']['updates']['autoCheck'] = False
        await manager.tick()
        assert not current.state['updates']['blockers']
        assert not manager.busy()
        for sid in identities:
            session = current._session(sid)
            assert {key: session.get(key) for key in expected[sid]} == expected[sid]
            assert session['configurationBusy']
        assert files == {str(path.relative_to(home)): path.read_bytes()
                         for path in (home / 'sessions').rglob('*') if path.is_file()}
        assert not current.runtime.workers
    finally:
        if 'release' in locals():
            release.set()
            if late_task:
                await late_task
        await current.close()
    reopened = AppService(home, RuntimeManager(), workspace=workspace)
    reopened.management = Management(reopened)
    reopened.smart_tools = SmartToolsManager(reopened)
    reopened.update_manager = UpdateManager(reopened)
    try:
        if reopened.tasks:
            await asyncio.gather(*list(reopened.tasks))
        assert not reopened.update_manager.busy()
        assert reopened.smart_tools._server('board')['installationId'] == 'b' * 24
        for sid in identities:
            session = reopened._session(sid)
            assert {key: session.get(key) for key in expected[sid]} == expected[sid]
            assert session['configurationBusy']
            with pytest.raises(AppError, match='settings|configuration|Configuration'):
                await reopened.dispatch('conversation.send', {'sessionId': sid, 'text': 'Still held'})
        assert files == {str(path.relative_to(home)): path.read_bytes()
                         for path in (home / 'sessions').rglob('*') if path.is_file()}
        assert not reopened.runtime.workers
    finally:
        await reopened.close()


@pytest.mark.parametrize('guard', ['task', 'preparation', 'worker'])
async def test_tool_tick_waits_for_actual_work_then_closes_new_input_during_switch(held, monkeypatch, guard):
    from unittest.mock import AsyncMock
    app, sid, path, payload = held
    app._session(sid)['pendingConfiguration']['operationId'] = 'saved-not-mounted'
    await app.dispatch('session.create', {'title': 'Unrelated new input'})
    other = app._session()['id']
    await app.smart_tools.connect('board')
    app.state['settings']['updates']['autoCheck'] = False
    app.state['updates']['application'] = {'status': 'current'}
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    with monkeypatch.context() as patch:
        if guard == 'task':
            patch.setattr(app.management, 'pending_config_tasks', {sid: task})
        elif guard == 'preparation':
            patch.setattr(app.runtime, '_preparations', {sid: {'owner': 'fixture'}})
        else:
            patch.setattr(app.runtime, 'workers', {sid: {'process': SimpleNamespace(returncode=0)}})
        await app.update_manager.tick()
        assert app.smart_tools._server('board')['installationId'] == OLD
        assert app.state['updates']['pendingSmartTools'] and app.state['updates']['blockers']
    release.set()
    await task
    prepare_connection = app.smart_tools._prepare_update_connection
    calls = []
    async def prepare_with_new_input(row):
        assert app.state['updates']['phase'] == 'activating'
        with pytest.raises(AppError, match='updat|switch'):
            await app.dispatch('conversation.send', {'sessionId': other, 'text': 'Racing update'})
        calls.append(row['installationId'])
        return await prepare_connection(row)
    monkeypatch.setattr(app.smart_tools, '_prepare_update_connection', prepare_with_new_input)
    start = AsyncMock(side_effect=AssertionError('New input must not reach runtime'))
    monkeypatch.setattr(app.runtime, 'start', start)
    await app.update_manager.tick()
    assert calls == ['b' * 24]
    assert app.smart_tools._server('board')['installationId'] == 'b' * 24
    assert not app.state['updates']['pendingSmartTools'] and not app.state['updates']['blockers']
    assert path.read_bytes() == payload
    start.assert_not_awaited()
