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
        hooks=SimpleNamespace(register=register), get=lambda name: loop)
    worker.session = SimpleNamespace(coordinator=coordinator)
    worker.controls = SimpleNamespace(persist=Mock())
    worker.runtime = SimpleNamespace(generation=None, queued_inputs=0, inbox=asyncio.Queue())
    worker.parked = True
    return worker, loop, hooks


@pytest.mark.parametrize('pending', ['generation', 'tool-replies', 'approval', 'operations', 'inputs', 'children', 'checkpoint'])
async def test_worker_requires_actual_settlement_not_idle_event(pending):
    worker, loop, _ = boundary_worker()
    if pending == 'generation': worker.runtime.generation = {'id': 'g'}
    if pending == 'tool-replies': worker.bridges['b'] = asyncio.get_running_loop().create_future()
    if pending == 'approval': worker.approvals['a'] = object()
    if pending == 'operations': worker.operation_ids.add('op')
    if pending == 'inputs': worker.runtime.queued_inputs = 1
    if pending == 'children': loop._active_jobs = lambda: ['child']
    if pending == 'checkpoint': worker.parked = False
    answer = await inspect(worker, 'move')
    assert answer['ready'] is False and pending in answer['waitingFor']
    assert not worker.shutdown.is_set()
    assert (await inspect(worker, 'move', cancel=True))['cancelled']
    assert worker._handoff_boundary is None


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
    runtime.handoff_boundary = AsyncMock(return_value={'ready': False})
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
        assert session.get('workingDirectory', str(root)) == str(root)
        assert session['status'] == 'stopped' and not session['configurationBusy']
        assert session['ownership'] == {'status': 'available'} and 'lockOwner' not in session
        runtime.collaboration_input.assert_not_awaited()
    finally:
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
