"""Lost Stop replies recover through real runtime/native ownership, never replay."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from amplifier_foundation.session import SharedSessionStore, SessionBusyError
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService, AppError
from test_assignment_recovery import app, start, stop, resume


async def uncertain(app):
    source = app.state['sessions'][0]
    await start(app); await stop(app, source)
    old = app.collaboration.receipt('stop-recovery')
    old['interruptions'][source['id']] = 'unknown'
    app.collaboration.save('stop-recovery', old); app.db.commit()
    runtime = RuntimeManager()
    app.runtime.stopped_boundary = runtime.stopped_boundary
    return source, old, runtime


async def test_continue_recovers_without_second_stop_and_retains_receipts_on_restart(app):
    source, old, runtime = await uncertain(app)
    before = copy.deepcopy(app.assignments.current(source))
    args = {'sessionId': source['id'], 'expectedRevision': before['revision']}
    result = await app.dispatch('assignment.resume', args, command_id='recover-once')
    proof = result['result']['stopRecovery']
    assert proof['assignmentRevision'] == before['revision']
    assert proof['members'][0]['interruptionRevision'] == source['interruptionRevision']
    assert proof['members'][0]['workerAbsent']
    assert not proof['members'][0]['inputsReplayed']
    assert not app.runtime.inputs and not runtime.workers
    assert app.collaboration.receipt('stop-recovery') == old
    await app.close()
    other = AppService(app.data_dir, RuntimeManager(), workspace=source['workspace'])
    try:
        again = await other.dispatch('assignment.resume', args, command_id='recover-once')
        assert again['duplicate'] and again['result'] == result['result']
        assert other.collaboration.receipt('stop-recovery') == old
    finally:
        await other.close()


async def test_restart_unknown_stop_is_recoverable_after_owner_absence(app):
    source, old, _ = await uncertain(app)
    old['interruptions'][source['id']] = 'requested'
    old['interruptionsPending'] = 1
    app.collaboration.save('stop-recovery', old); app.db.commit()
    await app.close()
    other = AppService(app.data_dir, RuntimeManager(), workspace=source['workspace'])
    try:
        saved = other._session(source['id'])
        unknown = copy.deepcopy(other.collaboration.receipt('stop-recovery'))
        assert unknown['interruptions'][source['id']] == 'unknown'
        result = await resume(other, saved)
        assert result['result']['assignment']['status'] == 'active'
        assert other.collaboration.receipt('stop-recovery') == unknown
        assert not other.runtime.workers
    finally:
        await other.close()


@pytest.mark.parametrize('block', ['native_owner', 'preparation', 'admission', 'live_process', 'unobserved_exit', 'bridge', 'shutdown'])
async def test_unknown_stop_never_uses_empty_status_as_proof(app, block):
    source, old, runtime = await uncertain(app)
    sid = source['id']; held = None
    if block == 'native_owner':
        held = SharedSessionStore(source['workspace'], sid).acquire(app='independent-owner')
    elif block == 'preparation': runtime._preparations[sid] = object()
    elif block == 'admission':
        runtime._locks[sid] = asyncio.Lock(); await runtime._locks[sid].acquire()
    elif block == 'shutdown': runtime._closed = True
    else:
        runtime.workers[sid] = {'process': SimpleNamespace(returncode=None if block == 'live_process' else 0),
                               'executionEnded': block != 'unobserved_exit', 'bridge_tasks': []}
        if block == 'bridge': runtime.workers[sid]['bridge_tasks'] = [asyncio.get_running_loop().create_future()]
    before = copy.deepcopy(app.assignments.current(source))
    try:
        with pytest.raises(AppError, match='confirm'):
            await resume(app, source)
        assert app.assignments.current(source) == before
        assert app.collaboration.receipt('stop-recovery') == old
        assert not app.runtime.inputs
    finally:
        if held: held.release()


async def test_recovery_holds_native_ownership_until_durable_commit_and_rolls_back(app, monkeypatch):
    source, old, runtime = await uncertain(app)
    before = copy.deepcopy(app.assignments.current(source))
    store = SharedSessionStore(source['workspace'], source['id'])
    def fail(**kwargs):
        with pytest.raises(SessionBusyError):
            store.acquire(app='competing-writer')
        raise OSError('injected publication failure')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_publish_changes', fail)
        with pytest.raises(OSError, match='injected'):
            await resume(app, source)
    assert app.assignments.current(source) == before
    assert app.collaboration.receipt('stop-recovery') == old
    held = store.acquire(app='proof-released'); held.release()
    assert (await resume(app, source))['result']['assignment']['status'] == 'active'


async def test_real_process_must_exit_before_unknown_stop_can_be_recovered(app):
    import sys
    from test_handoff_admission import SCRIPT
    source, _, _ = await uncertain(app)
    runtime = RuntimeManager(command=[sys.executable, '-c', SCRIPT])
    runtime.retention.wake = lambda: None
    app.runtime.stopped_boundary = runtime.stopped_boundary
    try:
        await runtime.start(source, app.on_runtime_event)
        process = runtime.workers[source['id']]['process']
        assert process.returncode is None
        with pytest.raises(RuntimeError, match='settle'):
            with runtime.stopped_boundary(source):
                pytest.fail('Live process was accepted as settled')
        await runtime.stop(source['id'])
        assert process.returncode is not None
        result = await resume(app, source)
        assert result['result']['stopRecovery']['members'][0]['workerAbsent']
        assert not runtime.workers and not app.runtime.inputs
    finally:
        await runtime.close()
