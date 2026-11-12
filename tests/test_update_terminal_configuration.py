"""Synthetic real native settings writes; no live installation/provider/target turn."""
import asyncio
import copy
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, update_blockers
from amplifier_web.configuration_recovery import native_apply, prepare_attempt, receipt_path, seal
from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from test_app_updates import prepared_activation
from test_queued_configuration_boundary import ControlsTransport, plan


@pytest.fixture
async def terminal(tmp_path, monkeypatch, request):
    app, manager, candidate = await prepared_activation(tmp_path / 'app', monkeypatch)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    app.runtime = RuntimeManager()
    await app.dispatch('session.create', {})
    session = app._session()
    app.management = Management(app)
    transport = ControlsTransport(app.data_dir, session['id'])
    record = app.management.create_configuration_operation(session, plan(2), 'original-settings', None, None)
    session.update(configurationBusy=True, configurationOperationFence=record['id'])
    app.management.configuration_transition(session, record['id'], phase='applying', stage='preparing')
    attempt = prepare_attempt(app.management, session, record['id'])
    effect = getattr(request, 'param', 'saved_not_mounted')
    if effect == 'none':
        def refuse(*args): raise ValueError('Synthetic preparation refusal before settings write')
        monkeypatch.setattr(transport.controls, 'prepare_configuration', refuse)
        with pytest.raises(ValueError, match='preparation refusal'):
            await native_apply(transport.controls, plan(2), attempt,
                lambda args: _admit(app, session, args), lambda: session.get('interruptionRevision', 0))
    else:
        await native_apply(transport.controls, plan(2), attempt,
            lambda args: _admit(app, session, args), lambda: session.get('interruptionRevision', 0))
    app.management.configuration_transition(session, record['id'], phase='error', stage='readback',
        effect=effect, recoveryRequired=True, error='Synthetic saved-versus-loaded mismatch')
    session['status'] = 'error'
    app._message(session, 'user', 'Retained synthetic history before settings failure')
    app._message(session, 'assistant', 'Retained synthetic result; must never be replayed')
    app._save_changes(sessions={session['id']})
    process = AsyncMock(return_value='99.0.0')
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda _: True)
    restart = AsyncMock()
    monkeypatch.setattr(app_updates, 'request_managed_restart', restart)
    yield SimpleNamespace(app=app, manager=manager, session=session, attempt=attempt,
                          process=process, restart=restart, transport=transport)
    await transport.close()
    await app.close()


async def _admit(app, session, args):
    from amplifier_web.configuration_recovery import authorize
    return authorize(app.management, session['id'], args)


def originals(fixture):
    app, session = fixture.app, fixture.session
    root = app.data_dir / 'sessions' / session['id']
    return ({str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()},
            copy.deepcopy(session['pendingConfiguration']), session.get('configurationOperationFence'),
            copy.deepcopy(session.get('messages', [])))


async def test_terminal_saved_hold_allows_normal_app_only_activation_without_recovery(terminal):
    f = terminal
    before = originals(f)
    assert f.manager.busy()  # Component activation did not acquire this exception.
    assert not f.manager.app_busy()
    assert update_blockers.assess(f.app, application=True)['observation']['configurationReasonCounts'] == {
        'idle-configuration-hold': 1}
    f.app.state['updates']['pendingAppCheckAt'] = time.time()
    await f.manager.app()
    assert f.app.state['updates']['pendingRestart']['revision'] == 'a' * 40
    f.restart.assert_awaited_once()
    assert originals(f) == before
    assert len(f.app.management.configuration_record(f.session['id'], f.attempt['operationId'])['attempts']) == 1
    assert not f.app.runtime.workers and not f.transport.inputs
    assert not f.transport.calls


async def test_idle_unresolved_hold_automatically_activates_app_without_recovery(terminal, monkeypatch):
    """A lost historical receipt is not an executing native writer."""
    f = terminal
    receipt_path(f.attempt, home=f.app.data_dir).unlink()
    f.app.state['updates']['phase'] = 'app-staged'
    before = originals(f)
    boundary = AsyncMock(side_effect=AssertionError('Idle held work needs no cooperative wait'))
    monkeypatch.setattr(f.app.runtime, 'handoff_boundary', boundary)
    assert f.manager.busy()  # Component adoption still has its stronger gate.
    for _ in range(3):
        f.manager.wakeup.clear()
        f.manager.notify_idle()
        assert f.manager.wakeup.is_set(), 'Idle recovery hold indefinitely blocks the app update'
        assert not f.manager.app_busy()
    f.app.state['updates']['pendingAppCheckAt'] = time.time()
    await f.manager.app()
    assert f.app.state['updates']['pendingRestart']['revision'] == 'a' * 40
    f.restart.assert_awaited_once()
    assert originals(f) == before
    assert not f.app.runtime.workers and not f.transport.inputs and not f.transport.calls
    boundary.assert_not_awaited()
    assert f.app.state['updates']['drain']['workers'] == {}


@pytest.mark.parametrize('terminal', ['none'], indirect=True)
async def test_terminal_native_no_effect_is_quiescent_without_consuming_original(terminal):
    f = terminal
    before = originals(f)
    assert not f.manager.app_busy() and f.manager.busy()
    await app_updates.activate(f.manager)
    f.restart.assert_awaited_once()
    assert originals(f) == before
    assert not f.app.runtime.workers and not f.transport.calls


@pytest.mark.parametrize('unknown', [False, True])
async def test_absent_held_owner_clears_only_obsolete_freshness_before_targeted_read(terminal, monkeypatch, unknown):
    from test_targeted_configuration_recovery import manager, invoke
    f = terminal
    f.owner = f.session
    source = await manager(f)
    if unknown:
        receipt_path(f.attempt, home=f.app.data_dir).write_text(json.dumps(
            seal(f.attempt, 'unknown', terminal=True)))
    f.session.update(configurationPending=True, fileAccessPending=True,
        configurationPendingRevision=7, configurationRefresh={'phase': 'pending', 'revision': 7})
    before = originals(f)
    # This is the actual public cross-owner read: the obsolete file marker
    # rejects it before any native receipt can be observed.
    from amplifier_web.service import AppError
    with pytest.raises(AppError, match='Targeted recovery requires'):
        await invoke(f, source, 'read', includeRecovery=True)
    stop = AsyncMock(side_effect=AssertionError('No absent worker may be retired'))
    start = AsyncMock(side_effect=AssertionError('Freshness cannot mount held work'))
    drain = AsyncMock(side_effect=AssertionError('Freshness cannot drain held input'))
    monkeypatch.setattr(f.app.runtime, 'stop', stop)
    monkeypatch.setattr(f.app.runtime, 'start', start)
    monkeypatch.setattr(f.app.collaboration, 'drain', drain)
    await f.app.refresh_configuration(f.session['id'])
    assert f.session['configurationPending'] is False
    assert not f.session.get('fileAccessPending') and 'configurationRefresh' not in f.session
    assert f.session['configurationPendingRevision'] == 7
    assert f.session['configurationBusy'] and originals(f) == before
    assert not f.manager.app_busy()
    result = (await invoke(f, source, 'read', includeRecovery=True))['result']
    assert result['nativeEffect'] == ('unknown' if unknown else 'saved_not_mounted')
    assert originals(f) == before and f.manager.busy()
    await f.app.refresh_configuration(f.session['id'])
    assert originals(f) == before
    stop.assert_not_awaited()
    start.assert_not_awaited()
    drain.assert_not_awaited()
    assert not f.transport.calls and not f.transport.inputs


@pytest.mark.parametrize('fault', ['worker', 'preparing', 'preparation-record', 'task', 'lock', 'observer',
    'queued', 'applying', 'different-fence', 'missing-request', 'refresh', 'child', 'ownership'])
async def test_held_freshness_retains_markers_when_absence_is_unproved(terminal, monkeypatch, fault):
    f = terminal
    f.session.update(configurationPending=True, fileAccessPending=True, configurationPendingRevision=7)
    release = asyncio.Event()
    task = None
    if fault == 'worker':
        f.app.runtime.workers[f.session['id']] = {'process': SimpleNamespace(returncode=0)}
    elif fault == 'preparing':
        lock = asyncio.Lock(); await lock.acquire(); f.app.runtime._locks[f.session['id']] = lock
    elif fault == 'preparation-record':
        f.app.runtime._preparations[f.session['id']] = {'owner': 'retained'}
    elif fault == 'task':
        task = asyncio.create_task(release.wait())
        f.app.management.pending_config_tasks[f.session['id']] = task
    elif fault == 'lock':
        await f.app.management.lock.acquire()
    elif fault == 'observer':
        monkeypatch.setattr(f.app.runtime, 'has_pending_operations', lambda: None)
    elif fault in {'queued', 'applying'}:
        f.session['pendingConfiguration']['phase'] = fault
    elif fault == 'different-fence':
        f.session['configurationOperationFence'] = 'different-operation'
    elif fault == 'missing-request':
        f.session['pendingConfiguration'].pop('requestHash')
    elif fault == 'refresh':
        f.session['configurationRefresh'] = {'phase': 'applying', 'revision': 7}
    elif fault == 'child':
        f.session['workers'] = [{'status': 'working'}]
    else:
        f.session['ownership'] = {'status': 'blocked'}
    before = copy.deepcopy(f.session)
    stop = AsyncMock(side_effect=AssertionError('Held freshness cannot retire runtime'))
    monkeypatch.setattr(f.app.runtime, 'stop', stop)
    try:
        await f.app.refresh_configuration(f.session['id'])
        assert f.session == before
        stop.assert_not_awaited()
    finally:
        release.set()
        if task:
            await task
        f.app.runtime._preparations.clear()


async def test_restart_retires_stale_mount_freshness_not_held_configuration(terminal):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    f = terminal
    f.session.update(configurationPending=True, fileAccessPending=True, configurationPendingRevision=7,
        configurationRefresh={'phase': 'pending', 'revision': 7})
    before = originals(f)
    home, workspace, sid = f.app.data_dir, f.app.default_workspace, f.session['id']
    await f.app.dispatch('session.create', {'title': 'Unselected held owner sentinel'})
    assert f.app.state['selectedSessionId'] != sid
    f.app._save_changes(sessions={sid}, globals={'selectedSessionId'})
    await f.app.close()
    runtime = RuntimeManager()
    runtime.start = AsyncMock(side_effect=AssertionError('Restart must not mount held work'))
    runtime.stop = AsyncMock(side_effect=AssertionError('Absent held owner needs no retirement'))
    runtime.send = AsyncMock(side_effect=AssertionError('Restart must not deliver held input'))
    app = AppService(home, runtime, workspace=workspace)
    app.management = Management(app)
    app.update_manager = UpdateManager(app)
    try:
        await asyncio.gather(*list(app.tasks))
        session = app._session(sid)
        # Startup itself must retire freshness, even for an unselected owner.
        assert session['configurationBusy'] and session['configurationPending'] is False
        assert not session.get('fileAccessPending') and 'configurationRefresh' not in session
        assert originals(SimpleNamespace(app=app, session=session)) == before
        assert app.update_manager.busy() and not app.update_manager.app_busy()
        runtime.start.assert_not_awaited()
        runtime.stop.assert_not_awaited()
        runtime.send.assert_not_awaited()
        await app.refresh_configuration(sid)
        assert originals(SimpleNamespace(app=app, session=session)) == before
    finally:
        await app.close()


async def test_app_gate_presentation_and_idle_notification_use_same_assessment(terminal, monkeypatch):
    from amplifier_web import update_report
    f = terminal
    monkeypatch.setattr(update_report, 'runtime_report', lambda _: {})
    await f.manager.publish()
    assert not f.app.state['updates']['blockers']
    report = await update_report.collect(f.manager)
    assert not report['activationGate']['blocked']
    assert report['activationGate']['configurationReasonCounts'] == {'idle-configuration-hold': 1}
    f.app.state['updates']['phase'] = 'app-staged'
    f.manager.wakeup.clear()
    f.manager.notify_idle()
    assert f.manager.wakeup.is_set() and f.manager.busy() and not f.manager.app_busy()


@pytest.mark.parametrize('outcome', ['pass', 'failure', 'cancel'])
async def test_writer_exclusion_covers_replacement_await_and_releases_on_every_exit(terminal, monkeypatch, outcome):
    f = terminal
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    store = SharedSessionStore(f.session['workspace'], f.session['id'])
    async def replacement(*args, **kwargs):
        with pytest.raises(SessionBusyError):
            store.acquire(app='competing-during-replacement')
        await asyncio.sleep(0)
        if outcome == 'failure': raise RuntimeError('Synthetic replacement failure')
        if outcome == 'cancel': raise asyncio.CancelledError()
        return '99.0.0'
    monkeypatch.setattr(app_updates, 'process', replacement)
    if outcome == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await app_updates.activate(f.manager)
    else:
        await app_updates.activate(f.manager)
    acquired = store.acquire(app='after-boundary')
    acquired.release()
    assert f.session['configurationBusy']
    if outcome == 'cancel':
        assert f.app.state['updates']['pendingReplacement']
        assert f.app.state['updates']['phase'] == 'interrupted'


@pytest.mark.parametrize('size', [1, 128])
async def test_app_status_cost_excludes_recovery_files_and_native_history(terminal, monkeypatch, size):
    from unittest.mock import Mock
    f = terminal
    root = f.app.data_dir / 'sessions' / f.session['id']
    (root / 'retained-synthetic-history.json').write_text(json.dumps(['old history' * 1024] * size))
    before = originals(f)
    observe = Mock(wraps=f.app.runtime.has_pending_operations)
    monkeypatch.setattr(f.app.runtime, 'has_pending_operations', observe)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_text', lambda *a, **k: pytest.fail('No disk scan for app status'))
        patch.setattr(Path, 'read_bytes', lambda *a, **k: pytest.fail('No native/history scan for app status'))
        for _ in range(3):
            assert not f.manager.app_busy()
        assert observe.call_count == 3  # One observation, not one per hold/predicate.
    assert originals(f) == before


async def test_legacy_and_modern_holds_both_retain_native_exclusion_and_unwind_partial_acquisition(terminal, monkeypatch):
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    f = terminal
    await f.app.dispatch('session.create', {})
    legacy = f.app._session()
    legacy.update(status='error', configurationBusy=True,
                  pendingConfiguration={'phase': 'error', 'recoveryRequired': True})
    first = SharedSessionStore(f.session['workspace'], f.session['id'])
    second = SharedSessionStore(legacy['workspace'], legacy['id'])
    held = second.acquire(app='competing-legacy-writer')
    try:
        await app_updates.activate(f.manager)
        f.process.assert_not_awaited()
        f.restart.assert_not_awaited()
        assert not f.app.state['updates'].get('pendingReplacement')
        assert f.app.state['updates']['currentStatus']['actionRequired']
        assert 'cannot confirm earlier work' in f.app.state['updates']['currentStatus']['detail']
        acquired = first.acquire(app='partial-boundary-released')
        acquired.release()
    finally:
        held.release()
    async def replacement(*a, **kw):
        for store in (first, second):
            with pytest.raises(SessionBusyError):
                store.acquire(app='competing-during-replacement')
        return '99.0.0'
    monkeypatch.setattr(app_updates, 'process', replacement)
    await app_updates.activate(f.manager)
    f.restart.assert_awaited_once()
    assert f.session['configurationBusy'] and legacy['configurationBusy']
    for store in (first, second):
        acquired = store.acquire(app='all-boundaries-released')
        acquired.release()


async def test_cancel_before_admission_does_not_close_input_or_replace(terminal, monkeypatch):
    f = terminal
    async def cancelled():
        raise asyncio.CancelledError()
    monkeypatch.setattr(app_updates, 'installed_target', cancelled)
    before = originals(f)
    with pytest.raises(asyncio.CancelledError):
        await app_updates.activate(f.manager)
    assert not f.app.state['updates'].get('pendingReplacement')
    assert not f.app.state['updates'].get('drain')
    assert originals(f) == before
    f.process.assert_not_awaited()
    f.restart.assert_not_awaited()


@pytest.mark.parametrize('unknown', [False, True])
async def test_repeated_app_readiness_keeps_persisted_configuration_hold_and_no_input(terminal, monkeypatch, unknown):
    from amplifier_web import update_readiness
    from amplifier_web.auth import data_identity
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    from amplifier_web.update_drain import finish
    f = terminal
    if unknown:
        receipt_path(f.attempt, home=f.app.data_dir).write_text(json.dumps(
            seal(f.attempt, 'unknown', terminal=True)))
    intent = {'id': 'exact-app-only', 'scope': 'app'}
    await f.manager.command('app', command_id=intent['id'])
    assert f.app.state['updates']['pendingRestart']['updateIntent'] == intent
    before = originals(f)
    home, workspace, sid = f.app.data_dir, f.app.default_workspace, f.session['id']
    f.app._save_changes(sessions={sid}, globals={'updates'})
    await f.app.close()
    monkeypatch.setattr(update_readiness, 'running_identity', lambda: {
        'version': '99.0.0', 'revision': 'a'*40, 'instanceId': 'new-host'})
    runtime = RuntimeManager()
    runtime.start = AsyncMock(side_effect=AssertionError('Held target must never mount'))
    runtime.send = AsyncMock(side_effect=AssertionError('Held target must never receive input'))
    app = AppService(home, runtime, workspace=workspace)
    app.management = Management(app)
    app.update_manager = manager = UpdateManager(app)
    try:
        await asyncio.gather(*list(app.tasks))
        assert await manager.confirm_readiness({'ok': True, 'app': 'amplifier-unified',
            'dataIdentity': data_identity(home), **manager.running_identity})
        await finish(manager, 'installed')
        await asyncio.sleep(0)
        current = SimpleNamespace(app=app, session=app._session(sid))
        assert originals(current) == before
        assert app._session(sid)['configurationBusy']
        assert manager.service.state['updates']['sequence']['stage'] == 'complete'
        assert manager.service.state['updates']['sequence']['install'] is False
        runtime.start.assert_not_awaited()
        runtime.send.assert_not_awaited()
        from amplifier_web.service import AppError
        with pytest.raises(AppError, match='settings'):
            await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Held input must not start'})
        assert originals(current) == before
        # Another ordinary staged app-only target must work around the SAME
        # persisted unresolved operation, not merely provide a one-time escape.
        revision = 'b' * 40
        next_intent = {'id': 'next-app-only', 'scope': 'app'}
        folder = manager.directory / 'applications' / revision
        folder.mkdir(parents=True)
        (folder / 'validated.json').write_text(json.dumps({'revision': revision, 'version': '100.0.0'}))
        candidate = folder / 'tools/amplifier-unified/bin/python'
        candidate.parent.mkdir(parents=True); candidate.touch()
        app.state['updates'].update(phase='app-staged', applicationIntent=next_intent,
            pendingApp={'revision': revision, 'latest': 'v100.0.0', 'attemptId': 'next-attempt'})
        f.process.return_value = '100.0.0'
        assert not manager.app_busy() and manager.busy()
        app.state['updates']['pendingAppCheckAt'] = time.time()
        await manager.app()
        assert app.state['updates']['pendingRestart']['revision'] == revision
        assert app.state['updates']['pendingRestart']['updateIntent'] == next_intent
        assert f.restart.await_count == 2
        assert originals(current) == before and app._session(sid)['configurationBusy']
        runtime.start.assert_not_awaited()
        runtime.send.assert_not_awaited()
        assert not f.transport.calls and not f.transport.inputs
    finally:
        await app.close()


@pytest.mark.parametrize('fault', ['working', 'refresh', 'child', 'worker',
    'observer-unknown', 'observer-error', 'management', 'pending-task', 'preparation'])
async def test_active_or_unobservable_operation_stays_blocked(terminal, monkeypatch, fault):
    f = terminal
    app, session = f.app, f.session
    receipt = receipt_path(f.attempt, home=app.data_dir)
    release = asyncio.Event()
    task = None
    if fault == 'working': session['status'] = 'working'
    elif fault == 'refresh':
        session['configurationRefresh'] = {'phase': 'applying'}
        app.runtime.workers[session['id']] = {'process': SimpleNamespace(returncode=0)}
    elif fault == 'child': session['workers'] = [{'status': 'working'}]
    elif fault == 'worker': app.runtime.workers[session['id']] = {'process': SimpleNamespace(returncode=0)}
    elif fault == 'observer-unknown': monkeypatch.setattr(app.runtime, 'has_pending_operations', lambda: None)
    elif fault == 'observer-error':
        def unavailable(): raise RuntimeError('private error')
        monkeypatch.setattr(app.runtime, 'has_pending_operations', unavailable)
    elif fault == 'management': await app.management.lock.acquire()
    elif fault == 'pending-task':
        task = asyncio.create_task(release.wait()); app.management.pending_config_tasks[session['id']] = task
    elif fault == 'preparation':
        lock = asyncio.Lock(); await lock.acquire(); app.runtime._locks[session['id']] = lock
    before = originals(f)
    try:
        # Throwing the global observer must not make the activation permissive.
        try: await app_updates.activate(f.manager)
        except RuntimeError: assert fault == 'observer-error'
        f.process.assert_not_awaited()
        f.restart.assert_not_awaited()
        assert originals(f) == before
        assert f.app.state['updates'].get('pendingRestart') is None
    finally:
        release.set()
        if task: await task
        if fault == 'management': app.management.lock.release()
        if fault == 'preparation': app.runtime._locks[session['id']].release()
        if fault in {'worker', 'refresh'}: app.runtime.workers.clear()


@pytest.mark.parametrize('fault', ['missing', 'forged', 'unknown', 'partial', 'wrong-session', 'stale-attempt',
    'wrong-revision', 'payload', 'saved-plan', 'stop'])
async def test_historical_evidence_is_not_app_activity_or_recovery(terminal, monkeypatch, fault):
    f = terminal
    receipt = receipt_path(f.attempt, home=f.app.data_dir)
    if fault == 'missing':
        receipt.unlink()
    elif fault == 'forged':
        data = json.loads(receipt.read_text()); data['signature'] = '0' * 64; receipt.write_text(json.dumps(data))
    elif fault in {'unknown', 'partial', 'wrong-session'}:
        attempt = {**f.attempt, **({'sessionId': 'foreign'} if fault == 'wrong-session' else {})}
        receipt.write_text(json.dumps(seal(attempt, 'unknown' if fault == 'unknown' else 'saved_not_mounted',
                                          terminal=fault != 'partial')))
    elif fault == 'stale-attempt': f.session['pendingConfiguration']['attemptId'] = 'stale'
    elif fault == 'wrong-revision': f.session['pendingConfiguration']['revision'] += 1
    elif fault == 'payload': f.app.management.queued_path(f.session['id']).write_text('{}')
    elif fault == 'saved-plan': (receipt.parent.parent / 'configuration.json').write_text('{}')
    elif fault == 'stop': f.session['interruptionRevision'] = f.session.get('interruptionRevision', 0) + 1
    before = originals(f)
    # Neither status reads nor activation may validate historical native evidence.
    with monkeypatch.context() as patch:
        patch.setattr(f.app.management, 'configuration_record', lambda *a: pytest.fail('No operation scan'))
        patch.setattr('amplifier_web.configuration_recovery.evidence', lambda *a, **k: pytest.fail('No native scan'))
        for _ in range(3):
            assert not f.manager.app_busy()
        await app_updates.activate(f.manager)
    f.restart.assert_awaited_once()
    assert originals(f) == before and not f.transport.inputs and not f.transport.calls


@pytest.mark.parametrize('change', ['revision', 'stop', 'native', 'workspace', 'execution', 'target',
    'intent', 'writer', 'preparation', 'task', 'cancelling-task', 'active', 'lock'])
async def test_activation_rechecks_same_hold_after_target_discovery(terminal, monkeypatch, change):
    f = terminal
    original = app_updates.installed_target
    held = None
    task = None
    release = asyncio.Event()
    async def discover():
        nonlocal held, task
        value = await original()
        if change == 'revision':
            f.app.management.configuration_transition(f.session, f.attempt['operationId'], error='Changed error')
        elif change == 'stop': f.session['interruptionRevision'] = f.session.get('interruptionRevision', 0) + 1
        elif change == 'native': f.session['runtimeSessionId'] = 'foreign'
        elif change == 'workspace': f.session['workspace'] = str(Path(f.session['workspace']) / 'foreign')
        elif change == 'execution': f.session['executionRevision'] = f.session.get('executionRevision', 0) + 1
        elif change == 'target': f.app.state['updates']['pendingApp']['attemptId'] = 'e' * 32
        elif change == 'intent': f.app.state['updates']['applicationIntent'] = {'id': 'new', 'scope': 'whole'}
        elif change == 'preparation': f.app.runtime._preparations[f.session['id']] = {'owner': 'retained'}
        elif change == 'active': f.session['status'] = 'working'
        elif change == 'lock': await f.app.management.lock.acquire()
        elif change in {'task', 'cancelling-task'}:
            async def pending():
                try: await release.wait()
                except asyncio.CancelledError: await release.wait()
            task = asyncio.create_task(pending())
            f.app.management.pending_config_tasks[f.session['id']] = task
            await asyncio.sleep(0)
            if change == 'cancelling-task':
                task.cancel()
                await asyncio.sleep(0)
        elif change == 'writer':
            from amplifier_foundation.session import SharedSessionStore
            held = SharedSessionStore(f.session['workspace'], f.session['id']).acquire(app='foreign-writer')
        return value
    monkeypatch.setattr(app_updates, 'installed_target', discover)
    try:
        await app_updates.activate(f.manager)
        f.process.assert_not_awaited()
        f.restart.assert_not_awaited()
        assert not f.app.state['updates'].get('pendingRestart')
    finally:
        if held: held.release()
        release.set()
        if task: await task
        f.app.runtime._preparations.clear()
        if change == 'lock': f.app.management.lock.release()
    if change in {'writer', 'preparation', 'task', 'cancelling-task', 'lock'}:
        # Settled work wakes the SAME ordinary target; no force/recovery action.
        monkeypatch.setattr(app_updates, 'installed_target', original)
        await app_updates.activate(f.manager)
        f.restart.assert_awaited_once()


@pytest.mark.parametrize('refresh', ['pending', 'queued', 'applying'])
@pytest.mark.parametrize('saved', [False, True], ids=['mixed-history', 'four-saved-unloaded'])
async def test_four_stopped_configuration_records_do_not_block_unrelated_app_replacement(terminal, monkeypatch, refresh, saved):
    """Saved flags are not executing work, including stale queued/applying views."""
    f = terminal
    sessions = [f.session]
    for _ in range(3):
        await f.app.dispatch('session.create', {})
        sessions.append(f.app._session())
    for session, pending in zip(sessions, (
            {'phase': 'error', 'recoveryRequired': True},
            {'phase': 'error', 'effect': 'none', 'operationId': 'retained-none'},
            {'phase': 'queued', 'effect': 'saved_not_mounted', 'operationId': 'retained-saved'},
            {'phase': 'applying', 'effect': 'unknown', 'operationId': 'retained-unknown'})):
        session.update(status='stopped', interruptionRevision=1, configurationBusy=True, configurationPending=True,
            configurationPendingRevision=23, configurationRefresh={'phase': refresh},
            pendingConfiguration=({**pending, 'phase': 'error', 'effect': 'saved_not_mounted', 'stage': 'reading',
                'error': 'Saved configuration does not match loaded-plan readback. Its operation remains unresolved.'}
                if saved else pending))
    before = copy.deepcopy(sessions)
    files = {str(p): p.read_bytes() for p in f.app.data_dir.rglob('*')
             if p.is_file() and '/sessions/' in str(p)}
    start = AsyncMock(side_effect=AssertionError('No historical owner may mount'))
    drain = AsyncMock(side_effect=AssertionError('Update cannot drain stopped inputs'))
    monkeypatch.setattr(f.app.runtime, 'start', start)
    monkeypatch.setattr(f.app.collaboration, 'drain', drain)
    observed = update_blockers.assess(f.app, application=True)
    assert not observed['blockers']
    assert observed['observation']['configurationReasonCounts'] == {'idle-configuration-hold': 4}
    assert f.manager.busy()  # Stronger component adoption is unchanged.
    await app_updates.activate(f.manager)
    f.restart.assert_awaited_once()
    assert sessions == before
    assert files == {str(p): p.read_bytes() for p in f.app.data_dir.rglob('*')
                     if p.is_file() and '/sessions/' in str(p)}
    start.assert_not_awaited()
    drain.assert_not_awaited()
    assert not f.transport.calls and not f.transport.inputs


@pytest.mark.parametrize('phase', ['queued', 'applying'])
async def test_saved_configuration_without_busy_still_retains_native_writer_exclusion(terminal, monkeypatch, phase):
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    f = terminal
    f.session.update(configurationBusy=False, configurationPending=True)
    f.session['pendingConfiguration']['phase'] = phase
    f.session.pop('configurationOperationFence', None)
    store = SharedSessionStore(f.session['workspace'], f.session['id'])
    async def replace(*args, **kwargs):
        with pytest.raises(SessionBusyError):
            store.acquire(app='independent-writer')
        return '99.0.0'
    monkeypatch.setattr(app_updates, 'process', replace)
    await app_updates.activate(f.manager)
    f.restart.assert_awaited_once()
    lease = store.acquire(app='after-replacement')
    lease.release()


@pytest.mark.parametrize('terminal', ['none'], indirect=True)
@pytest.mark.parametrize('proof', ['terminal', 'missing', 'partial', 'forged', 'unknown', 'saved', 'conflict', 'native',
    'malformed-history', 'malformed-attempt', 'different-fence'])
async def test_startup_observes_only_terminal_none_without_waking_stopped_owner(terminal, monkeypatch, proof):
    from amplifier_web.service import AppService
    from amplifier_web.configuration_recovery import suppress_input
    f = terminal
    app, session = f.app, f.session
    operation = f.attempt['operationId']
    receipt = receipt_path(f.attempt, home=app.data_dir)
    if proof == 'missing':
        receipt.unlink()
    elif proof in {'partial', 'forged', 'unknown', 'saved'}:
        data = seal(f.attempt, {'unknown': 'unknown', 'saved': 'saved_not_mounted'}.get(proof, 'none'),
            terminal=proof != 'partial')
        if proof == 'forged': data['signature'] = '0' * 64
        receipt.write_text(json.dumps(data))
    if proof == 'conflict':
        app.management.configuration_transition(session, operation, effect='saved_not_mounted')
    app.management.configuration_transition(session, operation, phase='applying', stage='dispatching',
        effect='unknown', recoveryRequired=True, error='Retained interrupted attempt')
    if proof.startswith('malformed-'):
        app.management.configuration_transition(session, operation, phase='error')
        record_path = app.management.configuration_record_path(session['id'], operation)
        malformed = json.loads(record_path.read_text())
        malformed['history' if proof == 'malformed-history' else 'attempts'] = ['malformed']
        record_path.write_text(json.dumps(malformed))
    session.update(status='stopped', configurationPending=True, configurationPendingRevision=7)
    if proof == 'different-fence': session['configurationOperationFence'] = 'independent-operation'
    if proof == 'native': session['runtimeSessionId'] = 'different-native'
    original = copy.deepcopy(app.management.configuration_record(session['id'], operation))
    home, workspace, sid = app.data_dir, app.default_workspace, session['id']
    app._save_changes(sessions={sid})
    await app.close()
    for _ in range(2):
        runtime = RuntimeManager()
        runtime.start = AsyncMock(side_effect=AssertionError('Bookkeeping must not warm runtime'))
        runtime.send = AsyncMock(side_effect=AssertionError('Bookkeeping must not deliver old input'))
        current = AppService(home, runtime, workspace=workspace)
        drain = AsyncMock(side_effect=AssertionError('Terminal bookkeeping cannot drain old inputs'))
        monkeypatch.setattr(current.collaboration, 'drain', drain)
        current.management = Management(current)
        try:
            await asyncio.gather(*list(current.tasks))
            restored = current._session(sid)
            retained = current.management.configuration_record(sid, operation)
            assert restored['status'] == 'stopped'
            assert retained['request'] == original['request']
            assert retained['attempts'] == original['attempts']
            assert retained['history'][:len(original['history'])] == original['history']
            if proof == 'terminal':
                assert retained['state']['effect'] == 'none'
                assert retained['state']['stage'] == 'reconciled'
                assert not restored['configurationBusy']
                assert not restored.get('configurationOperationFence')
                assert suppress_input(restored, {'acceptedAt': 0})
                assert retained['recoveryNoInput']
            else:
                assert restored['configurationBusy']
                assert restored['configurationOperationFence'] == (
                    'independent-operation' if proof == 'different-fence' else operation)
                assert retained['state']['effect'] == 'unknown'
            runtime.start.assert_not_awaited()
            runtime.send.assert_not_awaited()
            drain.assert_not_awaited()
        finally:
            await current.close()


@pytest.mark.parametrize('terminal', ['none'], indirect=True)
async def test_startup_does_not_reclassify_already_settled_none_failure(terminal, monkeypatch):
    f = terminal
    app, session = f.app, f.session
    operation = f.attempt['operationId']
    app.management.configuration_transition(session, operation, phase='error', stage='reconciled',
        effect='none', recoveryRequired=False)
    app.management.settle_configuration_fence(session, operation)
    session['status'] = 'idle'
    before = copy.deepcopy(session['pendingConfiguration'])
    record = app.management.configuration_record(session['id'], operation)
    drain = AsyncMock()
    monkeypatch.setattr(app.collaboration, 'drain', drain)
    app.management = Management(app)
    await asyncio.gather(*list(app.tasks))
    assert session['pendingConfiguration'] == before
    assert app.management.configuration_record(session['id'], operation) == record
    assert not session.get('cancelledConfigurationInput')
    assert not session['configurationBusy']
    drain.assert_awaited_once_with(session['id'])  # Ordinary continuation remains eligible.
