"""Desired folders use one ordinary retirement, not checkout marker RPCs."""
import asyncio
import copy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.runtime_worker import Worker
from test_worktrees import fixture, settle
from test_handoff_refusal import checkout
from test_collaborative_workspaces import app
from amplifier_web.runtime import RuntimeManager


async def deferred_fixture(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    async def place(session, commit):
        entered.set()
        await release.wait()
        return await commit({'quiesced': True, 'retired': True, 'outcome': 'completed',
                             'inputsReplayed': False})
    runtime.deferred_placement = AsyncMock(side_effect=place)
    runtime.handoff_boundary = AsyncMock(side_effect=AssertionError('No checkout boundary RPC'))
    return app, runtime, root, sid, entered, release


@pytest.mark.parametrize('change', ['none', 'human', 'bookkeeping', 'stop', 'objective', 'terminal'])
async def test_desired_folder_is_soft_and_checks_real_authority_at_commit(tmp_path, monkeypatch, change):
    app, runtime, root, sid, entered, release = await deferred_fixture(tmp_path, monkeypatch)
    session = app._session(sid)
    session['task'] = {'id': 'task', 'objective': 'Finish this work', 'revision': 1, 'status': 'active'}
    target = await checkout(app, sid)
    try:
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
                    'expectedExecutionRevision': 0}, command_id='once'))['result']
        await entered.wait()
        assert receipt['placementMode'] == 'deferred-retirement'
        assert not session.get('configurationBusy')
        assert not app.worktrees.execution_state(sid)['fenced']
        assert session.get('workingDirectory', str(root)) == str(root)
        if change == 'human':
            app._message(session, 'user', 'Finish the persistent child first', inputOrigin='ui')
        elif change == 'bookkeeping':
            session['task']['revision'] += 1
        elif change == 'stop':
            session['interruptionRevision'] = session.get('interruptionRevision', 0) + 1
        elif change == 'objective':
            session['task']['objective'] = 'A different assignment'
        elif change == 'terminal':
            session['task']['status'] = 'completed'
        release.set()
        await settle(app)
        saved = app.worktrees.read(sid, receipt['id'])
        accepted = change in {'none', 'human', 'bookkeeping'}
        assert saved['phase'] == ('applied' if accepted else 'rejected')
        assert session.get('workingDirectory', str(root)) == (target['path'] if accepted else str(root))
        assert session.get('executionRevision', 0) == int(accepted)
        assert not saved['inputsReplayed']
        runtime.quiesce_for_handoff.assert_not_awaited()
        runtime.handoff_boundary.assert_not_awaited()
    finally:
        release.set()
        await app.close()


async def test_new_desired_target_supersedes_soft_intent_without_another_retirement(tmp_path, monkeypatch):
    app, runtime, root, sid, entered, release = await deferred_fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    try:
        old = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
               'expectedExecutionRevision': 0}, command_id='old'))['result']
        await entered.wait()
        new = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': None,
               'expectedExecutionRevision': 0}, command_id='new'))['result']
        assert app.worktrees.read(sid, old['id'])['supersededBy'] == new['id']
        release.set()
        await settle(app)
        assert app.worktrees.read(sid, old['id'])['phase'] == 'rejected'
        assert app.worktrees.read(sid, new['id'])['phase'] == 'applied'
        assert app._session(sid)['workingDirectory'] == str(root)
        runtime.deferred_placement.assert_awaited_once()
        runtime.handoff_boundary.assert_not_awaited()
    finally:
        release.set()
        await app.close()


async def test_soft_next_target_cannot_be_removed_or_archived(tmp_path, monkeypatch):
    from amplifier_web.service import AppError
    app, runtime, root, sid, entered, release = await deferred_fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    try:
        await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
                           'expectedExecutionRevision': 0})
        await entered.wait()
        for action in ('worktree.remove', 'worktree.archive'):
            with pytest.raises(AppError, match='unresolved handoff'):
                await app.dispatch(action, {'sessionId': sid, 'id': target['id'],
                                           'expectedRevision': target['revision']})
        assert not app._session(sid).get('configurationBusy')
    finally:
        release.set()
        await settle(app)
        await app.close()


async def test_placement_does_not_clear_an_independent_configuration_fence(tmp_path, monkeypatch):
    app, runtime, root, sid, entered, release = await deferred_fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    try:
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
                    'expectedExecutionRevision': 0}))['result']
        await entered.wait()
        app._session(sid)['configurationBusy'] = True
        release.set()
        await settle(app)
        assert app.worktrees.read(sid, receipt['id'])['phase'] == 'rejected'
        assert app._session(sid)['configurationBusy']
    finally:
        release.set()
        await app.close()


async def test_shutdown_invalidates_commit_of_a_retained_retirement(tmp_path, monkeypatch):
    app, runtime, root, sid, entered, release = await deferred_fixture(tmp_path, monkeypatch)
    callbacks = []
    async def place(session, commit):
        callbacks.append(commit)
        entered.set()
        await asyncio.Future()
    runtime.deferred_placement.side_effect = place
    target = await checkout(app, sid)
    receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
               'expectedExecutionRevision': 0}))['result']
    await entered.wait()
    await app.worktrees.close()
    saved = app.worktrees.read(sid, receipt['id'])
    assert saved['phase'] == 'pending' and saved['step'] == 'desired'
    assert saved['placementUnperformed'] and saved['continuation']['status'] == 'suppressed'
    from amplifier_web.worktrees import HandoffSuperseded
    with pytest.raises(HandoffSuperseded):
        await callbacks[0]({'quiesced': True, 'retired': True})
    assert app.worktrees.read(sid, receipt['id']) == saved
    assert app._session(sid).get('workingDirectory', str(root)) == str(root)
    await app.close()
    from amplifier_web.service import AppService
    # Old process settlement belongs to RuntimeManager. This adapter has no
    # mounted worker; the real store path proves its next acquisition separately.
    async def next_owner(session, commit):
        return await commit({'quiesced': True, 'retired': False, 'inputsReplayed': False})
    runtime.deferred_placement = AsyncMock(side_effect=next_owner)
    restored = AppService(app.data_dir, runtime, workspace=root)
    try:
        restored.worktrees.start()
        await settle(restored)
        assert restored.worktrees.read(sid, receipt['id'])['phase'] == 'applied'
        assert restored._session(sid)['workingDirectory'] == target['path']
        runtime.deferred_placement.assert_awaited_once()
        runtime.collaboration_input.assert_not_awaited()
    finally:
        await restored.close()


async def test_delayed_closing_projection_never_downgrades_a_newer_placement(tmp_path, monkeypatch):
    app, runtime, root, sid, entered, release = await deferred_fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    worker = {}
    runtime.workers = {sid: worker}
    try:
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
                   'expectedExecutionRevision': 0}))['result']
        await entered.wait()
        async with app.lock:
            app.worktrees.placement_closing(sid, worker)
            record = app.worktrees.read(sid, receipt['id'])
            record.update(phase='applied', step='applied')
            app.worktrees.save(record)
        await asyncio.sleep(0)
        assert app.worktrees.read(sid, receipt['id'])['step'] == 'applied'
    finally:
        release.set()
        await app.close()


async def test_unconfirmed_placement_publication_is_not_downgraded_to_no_effect(tmp_path, monkeypatch):
    app, runtime, root, sid, entered, release = await deferred_fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    original = app.worktrees.changed
    failed = False
    def fail_commit(*args):
        nonlocal failed
        if not failed and any(row.get('step') == 'committing' for row in app.worktrees.receipts(sid)):
            failed = True
            raise OSError('actual publication uncertain')
        return original(*args)
    try:
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
                   'expectedExecutionRevision': 0}))['result']
        await entered.wait()
        monkeypatch.setattr(app.worktrees, 'changed', fail_commit)
        release.set()
        await settle(app)
        saved = app.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == 'unknown' and saved['step'] == 'committing'
        assert not saved.get('placementUnperformed') and not saved['inputsReplayed']
    finally:
        monkeypatch.setattr(app.worktrees, 'changed', original)
        release.set()
        await app.close()


async def test_actual_pipe_exit_before_retirement_reply_reports_runtime_failure_not_folder_uncertainty(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from test_worktrees import repository
    root = repository(tmp_path)
    script = ("import json,sys\n"
              "for line in sys.stdin:\n"
              " d=json.loads(line)\n"
              " if d['op']=='start': print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)\n"
              " elif d['op']=='retire': sys.exit(3)\n"
              " elif d['op']=='stop': break\n")
    runtime = RuntimeManager(command=[sys.executable, '-c', script])
    app = AppService(tmp_path / 'app', runtime, workspace=root)
    await app.dispatch('session.create', {})
    session = app._session()
    await runtime.start(session, AsyncMock())
    try:
        target = await checkout(app, session['id'])
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': session['id'], 'id': target['id'],
                   'expectedExecutionRevision': 0}))['result']
        await asyncio.wait_for(settle(app), 5)
        saved = app.worktrees.read(session['id'], receipt['id'])
        assert saved['phase'] == 'rejected' and saved['placementUnperformed']
        assert saved['runtimeOutcome'] == 'unconfirmed' and saved['continuation']['status'] == 'suppressed'
        assert not app.worktrees.execution_state(session['id'])['fenced']
        assert session.get('workingDirectory', str(root)) == str(root)
        assert session['id'] not in runtime._retired
    finally:
        await app.close()


async def test_foreign_native_writer_is_not_released_for_an_unperformed_folder_choice(tmp_path, monkeypatch):
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    root = tmp_path / 'history'
    root.mkdir()
    store = SharedSessionStore(root, 'foreign-native')
    held = store.acquire(app='foreign-original-owner')
    runtime = RuntimeManager()
    commit = AsyncMock()
    try:
        with pytest.raises(SessionBusyError):
            await runtime.deferred_placement({'id': 'display-alias', 'runtimeSessionId': 'foreign-native',
                'workspace': str(root), 'placementRequestId': 'unperformed'}, commit)
        commit.assert_not_awaited()
        with pytest.raises(SessionBusyError) as still_owned:
            store.acquire(app='not-a-takeover')
        assert still_owned.value.owner['acquisition_id'] == held.owner['acquisition_id']
    finally:
        held.release()
        await runtime.close()


@pytest.mark.parametrize('foreign_owner', [False, True])
async def test_restart_retiring_intent_uses_new_exclusive_lease_without_replaying_control(tmp_path, monkeypatch, foreign_owner):
    from amplifier_foundation.session import SharedSessionStore
    from amplifier_web.service import AppService
    app, runtime, root, sid, entered, release = await deferred_fixture(tmp_path, monkeypatch)
    target = await checkout(app, sid)
    receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
               'expectedExecutionRevision': 0}))['result']
    await entered.wait()
    await app.close()
    record = app.worktrees.read(sid, receipt['id'])
    record.update(phase='pending', step='retiring')
    app.worktrees.save(record)
    store = SharedSessionStore(root, sid)
    held = store.acquire(app='original-foreign-worker') if foreign_owner else None
    fresh = RuntimeManager()
    request = fresh._request_unlocked = AsyncMock(side_effect=AssertionError('No old native command replay'))
    restored = AppService(app.data_dir, fresh, workspace=root)
    try:
        restored.worktrees.start()
        await asyncio.wait_for(settle(restored), 5)
        saved = restored.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == ('rejected' if foreign_owner else 'applied'), saved
        assert saved['continuation']['status'] == 'suppressed'
        assert not saved['inputsReplayed'] and not fresh.workers
        request.assert_not_awaited()
        if not foreign_owner:
            assert saved['release']['ownershipAcquisitionId'] and not saved['release']['retired']
            after = store.acquire(app='proof-placement-released')
            after.release()
    finally:
        if held:
            held.release()
        await restored.close()


@pytest.mark.parametrize('failure', [RuntimeError('lost save'), OSError('storage failed')])
@pytest.mark.parametrize('owner_unreadable', [False, True])
async def test_runtime_failure_is_separate_from_placement_uncertainty(tmp_path, monkeypatch, failure, owner_unreadable):
    app, runtime, root, sid, _, release = await deferred_fixture(tmp_path, monkeypatch)
    owner_reader = app.worktrees.saved_owner
    runtime.deferred_placement.side_effect = failure
    target = await checkout(app, sid)
    try:
        receipt = (await app.dispatch('worktree.handoff', {'sessionId': sid, 'id': target['id'],
                    'expectedExecutionRevision': 0}))['result']
        if owner_unreadable:
            def missing(*args, **kwargs):
                raise OSError('saved owner unreadable')
            monkeypatch.setattr(app.worktrees, 'saved_owner', missing)
        await settle(app)
        saved = app.worktrees.read(sid, receipt['id'])
        assert saved['phase'] == ('unknown' if owner_unreadable else 'rejected')
        assert app.worktrees.execution_state(sid)['fenced'] is owner_unreadable
        if not owner_unreadable:
            assert saved['placementUnperformed'] and saved['runtimeOutcome'] == 'unconfirmed'
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
        assert saved['continuation']['status'] == 'suppressed'
    finally:
        monkeypatch.setattr(app.worktrees, 'saved_owner', owner_reader)
        release.set()
        await app.close()


async def test_native_soft_retirement_does_not_own_initiating_command_lock(monkeypatch):
    worker = Worker()
    hooks, replies = {}, []
    def register(event, handler, **kwargs):
        hooks[event] = handler
        return lambda: hooks.pop(event, None)
    goal = {'condition': 'Finish', 'turns_used': 4, 'cap': 9}
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(
        session_state={'goal': goal}, hooks=SimpleNamespace(register=register)))
    worker.controls = SimpleNamespace(persist=Mock())
    idle = False
    worker.idle = lambda: idle
    worker.checkpoint_idle = AsyncMock(side_effect=lambda: idle)
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    async with worker.command_lock:
        retirement = asyncio.create_task(worker.command({'op': 'retire', 'id': 'one', 'whenSettled': True}))
        await asyncio.sleep(0)
        assert worker.retirement_intent and not retirement.done()
        assert not worker.shutdown.is_set()
        await hooks['orchestrator:goal_progress']('orchestrator:goal_progress',
            {'state': 'continuing', 'condition': 'Finish', 'turn': 4})
        assert worker.session.coordinator.session_state['goal'] == goal
        assert worker.session.coordinator.session_state['goal'] is not goal
        worker.checkpoint_idle.assert_not_awaited()
    idle = True
    worker.retirement_changed.set()
    await asyncio.wait_for(retirement, 1)
    assert worker.shutdown.is_set() and worker.retirement_intent is None
    assert replies[-1] == {'op': 'reply', 'id': 'one', 'result': {
        'retired': True, 'outcome': None, 'continuationAllowed': True}}
    worker.checkpoint_idle.assert_awaited_once()


@pytest.mark.parametrize('held_projection_lock', [False, True])
async def test_host_soft_intent_keeps_source_controls_and_input_live_then_commits_once(tmp_path, held_projection_lock):
    script = r'''
import json,sys
retirement=None
def emit(value): print(json.dumps(value),flush=True)
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start':
        emit({'type':'runtime.ready','report':{}})
    elif data['op']=='retire':
        retirement=data['id']
    elif data['op']=='control' and data['operation']=='finish.retirement':
        emit({'op':'reply','id':data['id'],'result':{'ok':True}})
        emit({'type':'runtime.retiring'})
        emit({'op':'reply','id':retirement,'result':{'retired':True,'outcome':'completed','continuationAllowed':True}})
        break
    elif data['op']=='stop': break
    else:
        emit({'op':'reply','id':data['id'],'result':{'accepted':True}})
'''
    manager = RuntimeManager(command=[sys.executable, '-c', script])
    manager.retention.wake = lambda: None
    session = {'id': 'soft-fixture', 'workspace': str(tmp_path), 'placementRequestId': 'same-intent'}
    applied = {'directory': str(tmp_path), 'revision': 0, 'fenced': False}
    manager.bind_execution_state(lambda sid: applied)
    emit = AsyncMock()
    calls = []
    projection_lock = asyncio.Lock()
    projection_tasks = []
    def closing(sid, row):
        async def project():
            async with projection_lock:
                pass
        projection_tasks.append(asyncio.create_task(project()))
    manager._placement_closing = closing
    async def commit(proof):
        async with projection_lock:
            pass
        calls.append(proof)
        applied.update(directory=str(tmp_path / 'target'), revision=1)
        return 'applied'
    await manager.start(session, emit)
    original = manager.workers[session['id']]['process']
    pending = asyncio.create_task(manager.deferred_placement(session, commit))
    try:
        async with asyncio.timeout(2):
            while not manager.workers[session['id']]['pending']:
                await asyncio.sleep(0)
        row = manager.workers[session['id']]
        assert not row.get('closing') and not row.get('retirement_task') and not row['inflight']
        assert (await manager._request(session['id'], 'send', text='finish child', input_id='once'))['accepted']
        assert (await manager.control(session['id'], 'read.only'))['accepted']
        await manager.retention.sweep()
        assert manager.workers[session['id']] is row and not calls
        if held_projection_lock:
            await projection_lock.acquire()
        assert (await manager.control(session['id'], 'finish.retirement'))['ok']
        if held_projection_lock:
            async with asyncio.timeout(2):
                while row['pending']:
                    await asyncio.sleep(0)
            assert row['closing'] and not calls  # Reply passed the locked projection.
            projection_lock.release()
        assert await asyncio.wait_for(pending, 3) == 'applied'
        assert original.returncode == 0 and len(calls) == 1
        assert manager._retired[session['id']][0]['workingDirectory'] == applied['directory']
        assert manager._retired[session['id']][0]['executionRevision'] == 1
    finally:
        if projection_lock.locked():
            projection_lock.release()
        await asyncio.gather(*projection_tasks, return_exceptions=True)
        await manager.close()


@pytest.mark.parametrize('change', ['assignment', 'manager_stop', 'target_stop', 'manager_human',
                                  'target_human', 'manager_task', 'target_task'])
@pytest.mark.parametrize('lost_save', [False, True])
async def test_deferred_manager_authority_and_real_unknown_are_distinct(app, tmp_path, change, lost_save):
    from test_collaborative_workspaces import agent_action
    from test_manager_placement import prepared, checkout as manager_checkout
    source, target, _, root = await prepared(app, tmp_path)
    record = await manager_checkout(app, source, target, root)
    owner_reader = app.worktrees.saved_owner
    async def place(session, commit):
        if change == 'assignment':
            value = app.assignments.current(source)
            value['members'][target['id']]['status'] = 'detached'
            app.assignments.save(value)
        elif change.endswith('_stop'):
            row = source if change.startswith('manager') else target
            row['interruptionRevision'] = row.get('interruptionRevision', 0) + 1
        elif change.endswith('_human'):
            app._message(source if change.startswith('manager') else target, 'user', 'Continue this work', inputOrigin='ui')
        else:
            (source if change.startswith('manager') else target)['task'] = {'id': 'replaced', 'objective': 'other'}
        if lost_save:
            def once(*args, **kwargs):
                app.worktrees.saved_owner = owner_reader
                raise OSError('Actual placement transaction unavailable')
            app.worktrees.saved_owner = once
            raise RuntimeError('Native checkpoint result was lost')
        return await commit({'quiesced': True, 'retired': True, 'outcome': 'completed'})
    app.runtime.deferred_placement = AsyncMock(side_effect=place)
    pending = (await agent_action(app, source, 'worktree.handoff', {
        'sessionId': target['id'], 'id': record['id'], 'expectedExecutionRevision': 0}, 'new-move'))['result']
    await settle(app)
    saved = app.worktrees.read(target['id'], pending['id'])
    accepted = change.endswith('_human') and not lost_save
    revoked = not change.endswith('_human')
    assert saved['phase'] == ('unknown' if lost_save else 'applied' if accepted else 'rejected')
    assert target.get('workingDirectory', target['workspace']) == (record['path'] if accepted else str(tmp_path))
    assert app.worktrees.execution_state(target['id'])['fenced'] is lost_save
    assert not saved['inputsReplayed']
    app.runtime.handoff_boundary.assert_not_awaited()
    app.runtime.quiesce_for_handoff.assert_not_awaited()
    assert all(row[0] != target['id'] for row in app.runtime.inputs)