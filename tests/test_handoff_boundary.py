"""Native settlement, guarded continuation and positive-boundary recovery."""
from amplifier_web import test_execution_settings as execution_settings

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.handoff_boundary import inspect
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppService
from test_worktrees import fixture, settle
from test_handoff_refusal import checkout


def boundary_worker():
    worker = Worker()
    hooks = {}
    def register(event, handler, **kwargs):
        hooks[event] = handler
        return lambda: hooks.pop(event, None)
    loop = SimpleNamespace(pending=[], _active_jobs=lambda: [])
    coordinator = SimpleNamespace(session_id='native', session_state={},
        hooks=SimpleNamespace(register=register), get=lambda name: loop,
        get_capability=lambda name: None)
    worker.session = SimpleNamespace(coordinator=coordinator)
    worker.shared_handle = object()
    worker.controls = SimpleNamespace(persist=Mock())
    worker.runtime = SimpleNamespace(generation=None, queued_inputs=0, inbox=asyncio.Queue())
    worker.checkpoint_idle = AsyncMock(return_value=True)
    return worker, loop, hooks


@pytest.mark.parametrize('pending', ['generation', 'tool-replies', 'approval', 'operations', 'inputs', 'children'])
async def test_worker_requires_actual_settlement_not_idle_event(pending):
    worker, loop, _ = boundary_worker()
    if pending == 'generation': worker.runtime.generation = {'id': 'g'}
    if pending == 'tool-replies': worker.bridges['b'] = asyncio.get_running_loop().create_future()
    if pending == 'approval': worker.approvals['a'] = object()
    if pending == 'operations': worker.operation_ids.add('op')
    if pending == 'inputs': worker.runtime.queued_inputs = 1
    if pending == 'children': loop._active_jobs = lambda: ['child']
    answer = await inspect(worker, 'move')
    assert answer['ready'] is False and pending in answer['waitingFor']
    assert not worker.shutdown.is_set()
    assert (await inspect(worker, 'move', cancel=True))['cancelled']
    assert worker._handoff_boundary is None


async def test_checkout_probe_never_waits_for_the_initiating_command_or_saves():
    worker, _, _ = boundary_worker()
    async with worker.command_lock:
        # The same command can install its marker before asking the host to move.
        assert not (await inspect(worker, 'move', arm_only=True))['ready']
        observed = await asyncio.wait_for(inspect(worker, 'move'), .1)
        assert observed == {'ready': False, 'waitingFor': ['command']}
        assert (await inspect(worker, 'move', cancel=True))['cancelled']
    assert (await inspect(worker, 'move'))['ready']
    worker.checkpoint_idle.assert_not_awaited()


@pytest.mark.parametrize('kind', ['configuration', 'update'])
async def test_shared_boundary_still_waits_for_positive_checkpoint_and_rechecks(kind):
    worker, _, _ = boundary_worker()
    entered, release = asyncio.Event(), asyncio.Event()
    worker.session.coordinator.get_capability = lambda name: True if name == 'live.continuation_guard_supported' else None
    worker.session.session_id = 'native'
    worker.session.coordinator.session_state['goal'] = {'condition': 'finish'}
    worker.runtime.generation = {'id': 'original'}
    # Install the original turn marker while work is still active.
    flags = {kind: True}
    assert not (await inspect(worker, 'boundary', **flags))['ready']
    worker.runtime.generation = None
    async def checkpoint():
        entered.set()
        await release.wait()
        return False  # Work arriving during the save invalidated the proof.
    worker.checkpoint_idle.side_effect = checkpoint
    waiting = asyncio.create_task(inspect(worker, 'boundary', **flags))
    await entered.wait()
    assert not waiting.done()
    release.set()
    answer = await waiting
    assert not answer['ready'] and answer['waitingFor'] == ['checkpoint']
    worker.checkpoint_idle.assert_awaited_once()


@pytest.mark.parametrize('ending', ['late-reply', 'cancel', 'exit'])
async def test_boundary_timeout_retains_one_original_reply_and_ordered_removal(ending):
    from amplifier_web.runtime import RuntimeManager
    manager = RuntimeManager()
    manager.boundary_reply_timeout = .01
    row = {'process': SimpleNamespace(returncode=None)}
    manager.workers['s'] = row
    reply = asyncio.get_running_loop().create_future()
    calls = []
    async def request(sid, op, **args):
        calls.append(args)
        if args.get('cancel'):
            return {'cancelled': True}
        return await reply
    manager._request = request
    try:
        first = await manager.handoff_boundary('s', 'original')
        assert first['requestPending'] and first['waitingFor'] == ['boundary-reply']
        assert not reply.cancelled()
        second = await manager.handoff_boundary('s', 'original', cancel=ending == 'cancel')
        assert second['requestPending'] and len(calls) == 1
        if ending == 'exit':
            reply.set_exception(RuntimeError('original worker exited'))
            with pytest.raises(RuntimeError, match='original worker exited'):
                await manager.handoff_boundary('s', 'original')
        else:
            reply.set_result({'ready': True, 'waitingFor': []})
            observed = await manager.handoff_boundary('s', 'original', cancel=ending == 'cancel')
            assert observed == ({'cancelled': True} if ending == 'cancel' else {'ready': True, 'waitingFor': []})
            assert len(calls) == (2 if ending == 'cancel' else 1)
            assert not row['boundary_waits']
    finally:
        manager.workers.clear()
        await manager.retention.close()


@pytest.mark.parametrize('ending', ['late-reply', 'disconnect', 'partial-write'])
async def test_boundary_real_pipe_keeps_original_admission_through_deadline(ending, tmp_path):
    import json
    import sys
    from amplifier_web.runtime import RuntimeManager
    calls = tmp_path / 'received.json'
    script = r"""
import json,os,sys,time
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start':
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
    elif data['op']=='handoff.boundary':
        with open(sys.argv[1],'a') as out: out.write(json.dumps(data)+'\n')
        time.sleep(.15)
        if sys.argv[2]=='disconnect': os._exit(0)
        print(json.dumps({'op':'reply','id':data['id'],'result':{'ready':True,'waitingFor':[]}}),flush=True)
    elif data['op']=='stop': break
"""
    manager = RuntimeManager(command=[sys.executable, '-c', script, str(calls), ending])
    manager.boundary_reply_timeout = .01
    manager.retention.wake = lambda: None
    try:
        await manager.start({'id': 'pipe-owner', 'workspace': str(tmp_path)}, AsyncMock())
        row = manager.workers['pipe-owner']
        write = manager._write
        if ending == 'partial-write':
            async def uncertain_write(owner, data):
                await write(owner, data)
                if data['op'] == 'handoff.boundary':
                    raise BrokenPipeError('test lost transport confirmation after writing')
            manager._write = uncertain_write
        first = await manager.handoff_boundary('pipe-owner', 'same-move')
        assert first['requestPending'] and len(row['inflight']) == len(row['pending']) == 1
        original = next(iter(row['pending'].values()))
        assert not original.cancelled()
        manager.boundary_reply_timeout = 2
        if ending == 'disconnect':
            with pytest.raises(RuntimeError):
                await manager.handoff_boundary('pipe-owner', 'same-move')
        else:
            assert (await manager.handoff_boundary('pipe-owner', 'same-move'))['ready']
            assert not row['pending'] and not row['inflight'] and not original.cancelled()
        received = [json.loads(line) for line in calls.read_text().splitlines()]
        assert len(received) == 1 and received[0]['request_id'] == 'same-move'
    finally:
        await manager.close()


@pytest.mark.parametrize('during_cleanup', [False, True])
async def test_shutdown_during_pending_boundary_keeps_uncertainty_until_absence_check(tmp_path, monkeypatch, during_cleanup):
    from amplifier_web.runtime import RuntimeManager
    app, adapter, root, sid = await fixture(tmp_path, monkeypatch)
    entered = asyncio.Event()
    async def pending(*args, **kwargs):
        if during_cleanup and not kwargs.get('cancel'):
            raise ValueError('Test pre-release refusal before cleanup')
        entered.set()
        await asyncio.Future()
    adapter.handoff_boundary = pending
    target = await checkout(app, sid)
    receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
        'expectedExecutionRevision': 0}, command_id='shutdown-wait'))['result']
    await asyncio.wait_for(entered.wait(), 5)
    await app.worktrees.close()
    saved = app.worktrees.read(sid, receipt['id'])
    assert saved['phase'] == 'unknown' and saved['step'] == 'boundary-unconfirmed'
    assert app._session(sid)['configurationBusy']
    adapter.quiesce_for_handoff.assert_not_awaited()
    await app.close()
    runtime = RuntimeManager()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'unknown'
        checked = (await app.dispatch('worktree.check', {'sessionId': sid, 'id': receipt['id']}))['result']
        assert checked['displayState'] == 'not_moved' and checked['phase'] == 'rejected'
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
        assert not app._session(sid)['messages'] and not app.worktrees.jobs
    finally:
        await app.close()


async def test_stop_with_wedged_reply_finishes_cleanup_wait_then_late_ack_settles_same_move(tmp_path, monkeypatch):
    from amplifier_web.runtime import RuntimeManager
    app, adapter, root, sid = await fixture(tmp_path, monkeypatch)
    manager = RuntimeManager()
    manager.boundary_reply_timeout = .01
    reply = asyncio.get_running_loop().create_future()
    entered = asyncio.Event()
    calls = []
    async def request(owner, op, **args):
        calls.append(args)
        if args.get('cancel'):
            return {'cancelled': True}
        entered.set()
        return await reply
    manager._request = request
    manager.workers[sid] = {'process': SimpleNamespace(returncode=None),
                            'runtimeOwnerId': 'original-worker'}
    app.runtime = manager
    app.worktrees.bind_runtime()
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
            'expectedExecutionRevision': 0}, command_id='wedged'))['result']
        await entered.wait()
        app._session(sid).update(interruptionRevision=1, status='stopped')
        await asyncio.wait_for(settle(app), 3)
        current = app.worktrees.read(sid, receipt['id'])
        assert current['phase'] == 'unknown' and current['step'] == 'boundary-unconfirmed'
        assert current['displayState'] == 'needs_recovery'
        assert 'Check move' in current['detail'] and len(calls) == 1 and not reply.cancelled()
        before = (await app.dispatch('worktree.check', {'sessionId': sid, 'id': receipt['id']}))['result']
        assert before['phase'] == 'unknown' and len(calls) == 1
        reply.set_result({'ready': False, 'waitingFor': ['command']})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.gather(*[row['task'] for row in manager.workers[sid]['boundary_waits'].values()])
        checked = (await app.dispatch('worktree.check', {'sessionId': sid, 'id': receipt['id']}))['result']
        assert checked['phase'] == 'rejected' and checked['displayState'] == 'not_moved'
        assert checked['id'] == receipt['id'] and checked['boundaryCleanup']['markerRemoved']
        assert len(calls) == 2 and calls[-1]['cancel'] and len(app.worktrees.receipts(sid)) == 1
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
        assert app._session(sid)['status'] == 'stopped' and not app._session(sid)['messages']
    finally:
        manager.workers.clear()
        await app.close()


async def test_check_reads_receipt_only_after_acquiring_host_lock(tmp_path, monkeypatch):
    app, _, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
            'expectedExecutionRevision': 0}, command_id='move'))['result']
        await settle(app)
        async with app.lock:
            check = asyncio.create_task(app.worktrees._dispatch('worktree.check',
                {'sessionId': sid, 'id': receipt['id']}, 'ui', 'check'))
            await asyncio.sleep(0)
            row = app.worktrees.read(sid, receipt['id'])
            row.update(revision=row['revision'] + 1, detail='Newer confirmed status')
            app.worktrees.save(row)
        result = await check
        assert result['revision'] == row['revision'] and result['detail'] == 'Newer confirmed status'
    finally:
        await app.close()


async def test_check_move_is_machine_observation_not_a_second_move(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
        await settle(app)
        before = app.worktrees.read(sid, receipt['id'])
        calls = runtime.quiesce_for_handoff.await_count
        observed = (await app.dispatch('worktree.check', {'sessionId': sid, 'id': receipt['id']}))['result']
        assert observed['id'] == before['id'] and observed['revision'] == before['revision']
        assert observed['displayState'] == 'moved'
        assert observed['source'] == str(root) and observed['target'] == target['path']
        assert runtime.quiesce_for_handoff.await_count == calls
        assert len(app.worktrees.receipts(sid)) == 1
        runtime.quiesce_for_handoff.side_effect = RuntimeError('unknown release')
        failed = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': None,
            'expectedExecutionRevision': 1}, command_id='unknown'))['result']
        await settle(app)
        calls = runtime.quiesce_for_handoff.await_count
        observed = (await app.dispatch('worktree.check', {'sessionId': sid, 'id': failed['id']}))['result']
        assert observed['displayState'] == 'needs_recovery'
        assert observed['target'] == str(root)
        assert runtime.quiesce_for_handoff.await_count == calls
        assert app._session(sid)['workingDirectory'] == target['path']
        assert app._session(sid)['configurationBusy']
    finally:
        await app.close()


async def test_check_historical_success_does_not_confuse_a_later_move_with_failure(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        target = await checkout(app, sid)
        first = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
            'expectedExecutionRevision': 0}, command_id='outward'))['result']
        await settle(app)
        await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': None,
            'expectedExecutionRevision': 1}, command_id='homeward')
        await settle(app)
        calls = runtime.quiesce_for_handoff.await_count
        observed = (await app.dispatch('worktree.check', {'sessionId': sid, 'id': first['id']}))['result']
        assert observed['displayState'] == 'moved' and observed['phase'] == 'applied'
        assert observed['target'] == target['path']
        assert app._session(sid)['executionRevision'] == 2
        assert app._session(sid)['workingDirectory'] == str(root)
        assert runtime.quiesce_for_handoff.await_count == calls == 2
    finally:
        await app.close()


async def test_restart_before_release_check_confirms_absence_without_releasing_or_replaying(tmp_path, monkeypatch):
    from amplifier_web.runtime import RuntimeManager
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
        'expectedExecutionRevision': 0}, command_id='move'))['result']
    await settle(app)
    saved = app.worktrees.read(sid, receipt['id'])
    # Crash cut: only the pending observation existed, no release was attempted.
    app._session(sid).update(workingDirectory=str(root), executionRevision=0)
    app.worktrees.changed()
    await app.close()
    saved.update(phase='pending', step='waiting', revision=1)
    saved.pop('release', None)
    app.worktrees.save(saved)
    actual = RuntimeManager()
    actual.start = AsyncMock(side_effect=AssertionError('check must not start a worker'))
    actual.quiesce_for_handoff = AsyncMock(side_effect=AssertionError('check must not request release'))
    app = AppService(tmp_path / 'app', actual, workspace=root)
    try:
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'unknown'
        app.worktrees.start()
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'rejected'
        observed = (await app.dispatch('worktree.check', {'sessionId': sid, 'id': receipt['id']}))['result']
        assert observed['phase'] == 'rejected' and observed['displayState'] == 'not_moved'
        assert observed['boundaryCleanup']['workerAbsent'] and not observed['inputsReplayed']
        assert app._session(sid)['workingDirectory'] == str(root)
        assert app._session(sid)['executionRevision'] == 0
        assert not app._session(sid)['configurationBusy']
        assert not app.worktrees.jobs and not app._session(sid)['messages']
        actual.start.assert_not_awaited()
        actual.quiesce_for_handoff.assert_not_awaited()
    finally:
        await app.close()


async def test_goal_yields_at_completed_turn_without_clearing_progress():
    worker, _, hooks = boundary_worker()
    goal = {'condition': 'finish work', 'turns_used': 3, 'continuations': 2, 'cap': 8}
    worker.session.coordinator.session_state['goal'] = goal
    assert (await inspect(worker, 'move'))['ready']
    await hooks['orchestrator:goal_progress']('orchestrator:goal_progress',
        {'state': 'continuing', 'session_id': 'native', 'condition': 'finish work', 'turn': 3})
    saved = worker.session.coordinator.session_state['goal']
    assert saved == goal and saved is not goal
    worker.controls.persist.assert_called_once_with(task_only=True)


async def test_goal_cap_or_completion_cannot_rearm_a_saved_task():
    worker, _, _ = boundary_worker()
    state = worker.session.coordinator.session_state
    state.update(task={'status': 'active'}, goal={'condition': 'finish', 'cap': 1})
    assert (await inspect(worker, 'move'))['continuationAllowed']
    state['goal'] = None  # The native loop clears completed or exhausted goals.
    assert (await inspect(worker, 'move')) == {'ready': True, 'waitingFor': [], 'continuationAllowed': False}


async def wait_for_step(app, sid, identity, step):
    async with asyncio.timeout(10):
        while app.worktrees.read(sid, identity).get('step') != step:
            await asyncio.sleep(.01)


async def test_handoff_waits_for_tool_result_and_children_then_continues_once(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    runtime.collaboration_input = AsyncMock(return_value={'accepted': True})
    ready = asyncio.Event()
    runtime.handoff_boundary = AsyncMock(side_effect=lambda *args, **kwargs: {'ready': ready.is_set()})
    try:
        target = await checkout(app, sid)
        session = app._session(sid)
        session.update(status='working', collaborationGeneration={'id': 'g', 'terminal': False},
            messages=[{'id': 'human', 'role': 'user', 'inputOrigin': 'ui', 'text': 'Do the work', 'inputId': 'original'}],
            artifactRefs=['original-artifact'])
        command = {'sessionId': sid, 'id': target['id'], 'expectedExecutionRevision': 0}
        receipt = (await app.dispatch('worktree.handoff', command, command_id='move'))['result']
        await wait_for_step(app, sid, receipt['id'], 'waiting')
        assert not runtime.quiesce_for_handoff.called
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'pending'
        assert session.get('workingDirectory', str(root)) == str(root)
        ready.set()
        await settle(app)
        await asyncio.gather(*list(app.tasks))
        saved = app.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == 'applied'
        assert saved['release']['quiesced']
        assert session['workingDirectory'] == target['path']
        assert session['workspace'] == str(root) and session['artifactRefs'] == ['original-artifact']
        runtime.collaboration_input.assert_awaited_once()
        assert runtime.collaboration_input.call_args.args[1]['inputId'] != 'original'
        assert len([m for m in session['messages'] if m.get('inputId') == 'original']) == 1
        assert (await app.dispatch('worktree.handoff', command, command_id='move'))['result']['duplicate']
        assert runtime.collaboration_input.await_count == 1
    finally:
        await app.close()


async def test_stop_during_boundary_wait_cancels_unperformed_move(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    runtime.handoff_boundary = AsyncMock(side_effect=lambda *args, **kwargs:
        {'cancelled': True} if kwargs.get('cancel') else {'ready': False})
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
        await wait_for_step(app, sid, receipt['id'], 'waiting')
        app._session(sid)['interruptionRevision'] = 1
        await settle(app)
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'rejected'
        runtime.quiesce_for_handoff.assert_not_awaited()
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
        assert not app._session(sid)['configurationBusy']
        assert runtime.handoff_boundary.call_args.kwargs == {'cancel': True}
    finally:
        await app.close()


async def test_goal_finished_while_waiting_still_moves_without_starting_another_turn(tmp_path, monkeypatch):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    runtime.handoff_boundary = AsyncMock(return_value={'ready': True, 'continuationAllowed': False})
    runtime.collaboration_input = AsyncMock()
    app._session(sid).update(status='working', collaborationGeneration={'id': 'g', 'terminal': False})
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
        await settle(app)
        saved = app.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == 'applied' and saved['continuation']['status'] == 'suppressed'
        runtime.collaboration_input.assert_not_awaited()
    finally:
        await app.close()


async def test_stop_after_confirmed_release_keeps_source_resumable_without_replaying(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    session = app._session(sid)
    async def released(*args):
        session.update(interruptionRevision=1, status='stopped', ownership={'status': 'yielded'},
            lockOwner={'app': 'old-runtime'})
        return {'quiesced': True, 'inputsReplayed': False}
    runtime.quiesce_for_handoff.side_effect = released
    runtime.collaboration_input = AsyncMock()
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
        await settle(app)
        saved = app.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == 'rejected' and saved['release']['quiesced']
        assert 'saved and released' in saved['detail']
        assert session.get('workingDirectory', str(root)) == str(root)
        assert session['status'] == 'stopped' and not session['configurationBusy']
        assert session['ownership'] == {'status': 'available'} and 'lockOwner' not in session
        runtime.collaboration_input.assert_not_awaited()
    finally:
        await app.close()


async def test_late_stop_after_real_release_keeps_exact_proof_without_placement(tmp_path, monkeypatch):
    import os
    from amplifier_foundation.session import SharedSessionStore, register_release_handler, ReadyToRelease
    from amplifier_web.runtime import RuntimeManager
    app, adapter, root, sid = await fixture(tmp_path, monkeypatch)
    session = app._session(sid)
    manager = RuntimeManager()
    store = SharedSessionStore(root, sid)
    held = store.acquire(app='amplifier-unified', pid=os.getpid())
    manager.workers[sid] = {'process': SimpleNamespace(pid=os.getpid()+1000000, returncode=None),
        'native_worker_pid': os.getpid(), 'native_writer': dict(held.owner)}
    manager.stop = AsyncMock()
    async def release(request):
        session.update(interruptionRevision=1, status='stopped')
        return ReadyToRelease()
    registration = await register_release_handler(held, prepare_release=release)
    async def quiesce(snapshot, identity):
        def guard():
            from amplifier_web.worktrees import HandoffSuperseded
            if session.get('interruptionRevision'):
                raise HandoffSuperseded('Stopped')
        return await manager.quiesce_for_handoff(snapshot, identity, guard=guard)
    adapter.quiesce_for_handoff = quiesce
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
            'expectedExecutionRevision': 0}, command_id='late-stop'))['result']
        await settle(app)
        observed = app.worktrees.read(sid, receipt['id'])
        assert observed['phase'] == 'rejected' and observed['release']['quiesced']
        assert observed['release']['releasedOwner']['acquisition_id'] == held.owner['acquisition_id']
        assert not held.active and observed['inputsReplayed'] is False
        assert 'saved and released' in observed['detail']
        assert session.get('workingDirectory', str(root)) == str(root)
        assert session['status'] == 'stopped' and session.get('executionRevision', 0) == 0
        assert not session['configurationBusy'] and not session['messages']
    finally:
        await registration.close()
        if held.active: held.release()
        await manager.retention.close()
        await app.close()


@pytest.mark.parametrize('phase', ['pending', 'applied'])
async def test_restart_after_positive_release_finishes_placement_without_replaying_input(tmp_path, monkeypatch, phase):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
        'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
    await settle(app)
    record = app.worktrees.read(sid, receipt['id'])
    record.update(phase=phase, step='released', revision=1)
    app.worktrees.save(record)
    app._session(sid).update(workingDirectory=str(root), executionRevision=0)
    app.worktrees.changed()
    await app.close()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        app.worktrees.start()
        await settle(app)
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'applied'
        assert app._session(sid)['workingDirectory'] == target['path']
        assert not app._session(sid)['configurationBusy']
        assert runtime.quiesce_for_handoff.await_count == 2
        assert app._session(sid)['messages'] == []
    finally:
        await app.close()


async def test_restart_after_later_move_suppresses_old_notification_without_releasing_again(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
        'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
    await settle(app)
    record = app.worktrees.read(sid, receipt['id'])
    record.update(resumeRequested=True, continuation={'status': 'waiting'})
    app.worktrees.save(record)
    app._session(sid).update(workingDirectory=str(root), executionRevision=2)
    app.worktrees.changed()
    await app.close()
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        app.worktrees.start()
        await settle(app)
        saved = app.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == 'applied' and saved['continuation']['status'] == 'suppressed'
        assert app._session(sid)['workingDirectory'] == str(root)
        assert runtime.quiesce_for_handoff.await_count == 1
        assert not app._session(sid)['configurationBusy']
    finally:
        await app.close()


async def test_completed_move_without_notification_does_not_admit_history_on_start(tmp_path, monkeypatch):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    await app.dispatch('worktree.handoff', {'sessionId': sid,
        'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move')
    await settle(app)
    await app.close()
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path / 'repo')
    try:
        assert app.worktrees.recoverable == []
        app.worktrees.start()
        assert not app.worktrees.jobs
    finally:
        await app.close()


async def test_notification_failure_cannot_make_confirmed_placement_unknown(tmp_path, monkeypatch):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        target = await checkout(app, sid)
        monkeypatch.setattr(app.worktrees, 'enqueue_continuation', Mock(side_effect=OSError('notification save failed')))
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
        await settle(app)
        record = app.worktrees.read(sid, receipt['id'])
        assert record['phase'] == 'applied'
        assert record['continuation']['status'] == 'waiting'
        assert app._session(sid)['workingDirectory'] == target['path']
        assert not app._session(sid)['configurationBusy']
    finally:
        await app.close()


@pytest.mark.parametrize('change', ['stop', 'human', 'placement', 'task'])
async def test_saved_continuation_is_suppressed_when_authority_changes(tmp_path, monkeypatch, change):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        target = await checkout(app, sid)
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid,
            'id': target['id'], 'expectedExecutionRevision': 0}, command_id='move'))['result']
        await settle(app)
        session = app._session(sid)
        request = {'senderSessionId': sid, 'handoffId': receipt['id']}
        assert app.worktrees.continuation_guard(request) is None
        if change == 'stop': session['interruptionRevision'] = 1
        if change == 'human': session['messages'].append({'id': 'new', 'role': 'user', 'inputOrigin': 'ui'})
        if change == 'placement': session['executionRevision'] += 1
        if change == 'task': session['task'] = {'id': 'new', 'revision': 1}
        assert app.worktrees.continuation_guard(request)
    finally:
        await app.close()


async def test_backpressured_handoff_notification_retains_an_inbox_slot(tmp_path, monkeypatch):
    from amplifier_web.service import AppError
    app, _, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        monkeypatch.setitem(execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputs', 1)
        request = {'commandAction':'worktree.handoff', 'requestId':'move-wait', 'senderSessionId':sid,
            'subscription':app.collaboration.saved_wait(app._session(sid), 'move-wait', status='backpressured')}
        app.collaboration.insert('move-wait', 'fixture', request)
        app.db.commit()
        with pytest.raises(AppError, match='including reserved replies'):
            app.collaboration.require_inbox_slot(sid)
    finally:
        await app.close()
