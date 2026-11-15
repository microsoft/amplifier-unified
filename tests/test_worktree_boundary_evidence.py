"""Original handoff RPC evidence never authorizes replay or guessed settlement."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from amplifier_web.runtime import RuntimeManager, RuntimeOperationPending, RuntimeHostClosingError
from amplifier_web.service import AppError, AppService
from test_handoff_refusal import checkout
from test_worktrees import fixture, settle


async def move(app, sid, target, command='move'):
    result = (await app.dispatch('worktree.handoff', {'sessionId': sid,
        'id': target['id'], 'expectedExecutionRevision': 0}, command_id=command))['result']
    await settle(app)
    return app.worktrees.read(sid, result['id'])


def operation(record, *, kind='boundary', state='issued', result=None, error=False):
    return {'id': str(uuid.uuid4()), 'requestId': record['id'],
        'sessionId': record['sessionId'], 'runtimeOwnerId': 'original-owner',
        'executionHost': copy.deepcopy(record['executionHost']),
        'kind': kind, 'state': state, 'result': result, 'error': error}


@pytest.mark.parametrize('error', [
    RuntimeOperationPending('handoff.boundary', 'exact-rpc'),
    RuntimeHostClosingError(), OSError('broken pipe'), ValueError('worker failed after hook')])
async def test_waiting_failure_keeps_unknown_without_cleanup_or_release(tmp_path, monkeypatch, error):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    runtime.handoff_boundary = AsyncMock(side_effect=error)
    try:
        target = await checkout(app, sid)
        saved = await move(app, sid, target)
        assert saved['phase'] == 'unknown' and saved['step'] == 'waiting'
        assert app.worktrees.execution_state(sid)['fenced']
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
        runtime.handoff_boundary.assert_awaited_once()
        runtime.quiesce_for_handoff.assert_not_awaited()
        assert saved['inputsReplayed'] is False
    finally:
        await app.close()


@pytest.mark.parametrize('error', [RuntimeOperationPending('handoff.boundary'), OSError('cancel lost')])
async def test_cancel_failure_is_durable_unknown_not_rejected(tmp_path, monkeypatch, error):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    async def boundary(*args, **kwargs):
        if kwargs.get('cancel'):
            raise error
        app._session(sid)['interruptionRevision'] = 1
        return {'ready': False}
    runtime.handoff_boundary = AsyncMock(side_effect=boundary)
    try:
        saved = await move(app, sid, await checkout(app, sid))
        assert saved['phase'] == 'unknown'
        assert saved['boundaryCleanup']['status'] == 'issued'
        assert runtime.handoff_boundary.await_count == 2
        runtime.quiesce_for_handoff.assert_not_awaited()
        assert app.worktrees.execution_state(sid)['fenced']
        assert app._session(sid).get('workingDirectory', str(root)) == str(root)
    finally:
        await app.close()


async def uncertain(app, runtime, sid):
    target = await checkout(app, sid)
    async def boundary(owner, identity, **kwargs):
        record = app.worktrees.read(sid, identity)
        app.worktrees.observe_boundary(sid, identity, operation(record))
        raise RuntimeOperationPending('handoff.boundary')
    runtime.handoff_boundary = AsyncMock(side_effect=boundary)
    saved = await move(app, sid, target)
    return saved, target


def reply(app, record, result=None, error=False):
    issued = next(iter(record['boundaryOperations'].values()))
    app.worktrees.observe_boundary(record['sessionId'], record['id'],
        {**issued, 'state': 'returned', 'result': result, 'error': error})
    return app.worktrees.read(record['sessionId'], record['id'])


async def reconcile(app, record, **extra):
    return await app.dispatch('worktree.reconcile', {
        'sessionId': record['sessionId'], 'id': record['id'],
        'expectedRevision': record['revision'], 'destination': 'target',
        'evidence': 'Inspected the exact source and target.', **extra}, command_id='reconcile')


@pytest.mark.parametrize('phase', ['unknown', 'rejected'])
async def test_passive_original_read_late_reply_and_finite_reconcile_no_second_boundary(tmp_path, monkeypatch, phase):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved, target = await uncertain(app, runtime, sid)
        saved['phase'] = phase
        app.worktrees.save(saved)
        before = copy.deepcopy(saved)
        read = (await app.dispatch('worktree.list', {'sessionId': sid, 'handoffId': saved['id']}))['result']
        assert read['handoff'] == before and read['settlement']['status'] == 'blocked'
        with pytest.raises(AppError, match='no terminal reply'):
            await reconcile(app, saved)
        assert app.worktrees.read(sid, saved['id']) == before
        returned = reply(app, saved, {'ready': True})
        with pytest.raises(AppError, match='current unknown'):
            await reconcile(app, saved)  # The reply advanced the original revision.
        result = (await reconcile(app, returned))['result']
        await settle(app)
        final = app.worktrees.read(sid, result['id'])
        assert final['phase'] == 'applied' and final['release']['quiesced']
        assert app.worktrees.read(sid, saved['id'])['phase'] == 'reconciled'
        assert app._session(sid)['workingDirectory'] == target['path']
        runtime.handoff_boundary.assert_awaited_once()
        runtime.quiesce_for_handoff.assert_awaited_once()
        assert runtime.quiesce_for_handoff.call_args.args[1] == saved['id']
        assert runtime.quiesce_for_handoff.call_args.kwargs == {'expected_runtime_owner': 'original-owner'}
        assert app._session(sid)['messages'] == []
    finally:
        await app.close()


@pytest.mark.parametrize('legacy_phase', ['rejected', 'unknown'])
async def test_legacy_waiting_receipt_stays_inspectable_blocked_without_phase_edit(tmp_path, monkeypatch, legacy_phase):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved, _ = await uncertain(app, runtime, sid)
        saved.pop('boundaryOperations')
        saved.pop('boundaryEvidenceVersion')
        saved.update(phase=legacy_phase, detail='Any old diagnostic prose, not authority')
        app.worktrees.save(saved)
        app.worktrees.changed()
        read = (await app.dispatch('worktree.list', {'sessionId': sid, 'handoffId': saved['id']}))['result']
        assert read['handoff'] == saved and read['settlement']['status'] == 'blocked'
        assert app.worktrees.operation_shape(saved)['state'] == 'outcome_unknown'
        assert app.worktrees.execution_state(sid)['fenced']
        with pytest.raises(AppError, match='identity and terminal evidence are unavailable'):
            await reconcile(app, saved)
        assert app.worktrees.read(sid, saved['id']) == saved
        runtime.handoff_boundary.assert_awaited_once()
        runtime.quiesce_for_handoff.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize('change', ['host', 'revision', 'stop', 'task', 'question', 'peer'])
async def test_original_settlement_rejects_stale_or_untrusted_authority(tmp_path, monkeypatch, change):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved, _ = await uncertain(app, runtime, sid)
        saved = reply(app, saved, {'ready': True})
        session = app._session(sid)
        if change == 'host':
            saved['executionHost']['id'] = 'foreign'
            app.worktrees.save(saved)
        if change == 'revision': session['executionRevision'] = 10
        if change == 'stop': session['interruptionRevision'] = 1
        if change == 'task': session['task'] = {'id': 'changed', 'revision': 9}
        if change in {'question', 'peer'}:
            session['messages'].append({'id': 'quote', 'role': 'user', 'createdAt': saved['createdAt'] + 1,
                'inputOrigin': 'ui' if change == 'question' else 'peer',
                **({'questionId': 'foreign-question'} if change == 'question' else {})})
            with pytest.raises(AppError, match='actual finding'):
                await app.app_bridge('dispatch', {'action': 'worktree.reconcile', 'args': {
                    'id': saved['id'], 'expectedRevision': saved['revision'],
                    'destination': 'source', 'evidence': 'quoted finding', 'sourceMessageId': 'quote'}}, sid)
        else:
            with pytest.raises(AppError):
                await reconcile(app, saved)
        runtime.handoff_boundary.assert_awaited_once()
        runtime.quiesce_for_handoff.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize('change', ['id', 'requestId', 'sessionId', 'runtimeOwnerId', 'executionHost'])
async def test_terminal_evidence_cannot_change_issued_identity(tmp_path, monkeypatch, change):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved, _ = await uncertain(app, runtime, sid)
        issued = copy.deepcopy(next(iter(saved['boundaryOperations'].values())))
        issued.update(state='returned', result={'ready': True})
        issued[change] = {'id': 'other'} if change == 'executionHost' else 'other'
        with pytest.raises(ValueError):
            app.worktrees.observe_boundary(sid, saved['id'], issued)
        assert app.worktrees.read(sid, saved['id']) == saved
    finally:
        await app.close()


async def test_runtime_retains_issuance_and_late_reply_after_caller_cancel(tmp_path, monkeypatch):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    manager = RuntimeManager()
    try:
        saved, _ = await uncertain(app, runtime, sid)
        # A distinct original receipt for the transport test, with no earlier RPC.
        saved.pop('boundaryOperations')
        app.worktrees.save(saved)
        manager.bind_handoff_observer(app.worktrees.observe_boundary)
        row = {'runtimeOwnerId': 'original-owner', 'process': SimpleNamespace(returncode=None),
            'pending': {}, 'inflight': set()}
        manager.workers[sid] = row
        manager._write = AsyncMock()
        pending = await manager._admit(sid, 'handoff.boundary', {'request_id': saved['id'], 'cancel': False})
        rpc = pending[1]
        assert app.worktrees.read(sid, saved['id'])['boundaryOperations'][rpc]['state'] == 'issued'
        caller = asyncio.create_task(manager._reply(*pending, op='handoff.boundary', args={}))
        await asyncio.sleep(0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError): await caller
        manager._observe_handoff_reply(sid, row, {'id': rpc, 'result': {'ready': True}})
        returned = app.worktrees.read(sid, saved['id'])
        assert returned['boundaryOperations'][rpc]['state'] == 'returned'
        assert app.worktrees.settlement(returned)['status'] == 'ready'
        manager._write.assert_awaited_once()
    finally:
        manager.workers.clear()
        await manager.retention.close()
        await app.close()


async def test_restart_waiting_is_unknown_and_never_calls_runtime(tmp_path, monkeypatch):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    saved, _ = await uncertain(app, runtime, sid)
    saved['phase'] = 'pending'
    app.worktrees.save(saved)
    await app.close()
    restored = AppService(tmp_path / 'app', runtime, workspace=root)
    try:
        restored.worktrees.start()
        await settle(restored)
        assert restored.worktrees.read(sid, saved['id'])['phase'] == 'unknown'
        assert restored.worktrees.execution_state(sid)['fenced']
        runtime.handoff_boundary.assert_awaited_once()
        runtime.quiesce_for_handoff.assert_not_awaited()
    finally:
        await restored.close()


async def test_notification_only_recovery_never_cancels_or_clears_issued_operation(tmp_path, monkeypatch):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved, _ = await uncertain(app, runtime, sid)
        saved.update(phase='rejected', managerPlacement={'retained': True},
                     boundaryCleanup={'cancelled': True})
        app.worktrees.save(saved)
        monkeypatch.setattr(app.worktrees, 'enqueue_continuation', lambda record: None)
        await app.worktrees.finish(saved, copy.deepcopy(app._session(sid)))
        assert app.worktrees.unresolved(saved)
        assert app.worktrees.operation_shape(saved)['state'] == 'outcome_unknown'
        runtime.handoff_boundary.assert_awaited_once()
        runtime.quiesce_for_handoff.assert_not_awaited()
    finally:
        await app.close()


async def test_reply_save_failure_keeps_reader_and_retries_only_observed_bytes(tmp_path, monkeypatch):
    from test_runtime_event_batches import reader
    import json
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved, _ = await uncertain(app, runtime, sid)
        issued = next(iter(saved['boundaryOperations'].values()))
        manager, row, task, _ = await reader([], eof=False, root=sid)
        manager.bind_handoff_observer(app.worktrees.observe_boundary)
        row['handoffOperations'] = {issued['id']: issued}
        row['inflight'] = {issued['id'], 'unrelated'}
        first = asyncio.get_running_loop().create_future()
        second = asyncio.get_running_loop().create_future()
        row['pending'] = {issued['id']: first, 'unrelated': second}
        save = app.worktrees.save
        def fail_terminal(record):
            if any(op['state'] == 'returned' for op in record.get('boundaryOperations', {}).values()):
                raise OSError('controlled terminal persistence failure')
            return save(record)
        monkeypatch.setattr(app.worktrees, 'save', fail_terminal)
        stream = row['process'].stdout
        stream.feed_data((json.dumps({'op': 'reply', 'id': issued['id'],
                                     'result': {'ready': True}}) + '\n').encode())
        with pytest.raises(RuntimeOperationPending):
            await asyncio.wait_for(first, 2)
        assert not task.done()
        assert app.worktrees.read(sid, saved['id']) == saved
        assert app.worktrees.settlement(saved)['status'] == 'blocked'
        monkeypatch.setattr(app.worktrees, 'save', save)
        stream.feed_data((json.dumps({'op': 'reply', 'id': 'unrelated',
                                     'result': {'ok': True}}) + '\n').encode())
        assert await asyncio.wait_for(second, 2) == {'ok': True}
        assert app.worktrees.settlement(app.worktrees.read(sid, saved['id']))['status'] == 'ready'
        stream.feed_eof()
        await task
        await manager.retention.close()
        runtime.handoff_boundary.assert_awaited_once()
    finally:
        await app.close()


@pytest.mark.parametrize('polls', [2, 100])
async def test_poll_history_is_retained_by_id_without_growing_current_receipt(tmp_path, monkeypatch, polls):
    import json
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved, _ = await uncertain(app, runtime, sid)
        saved = reply(app, saved, {'ready': False})
        first = next(iter(saved['boundaryOperations']))
        sizes = []
        for _ in range(polls):
            issued = operation(saved)
            app.worktrees.observe_boundary(sid, saved['id'], issued)
            app.worktrees.observe_boundary(sid, saved['id'],
                {**issued, 'state': 'returned', 'result': {'ready': False}})
            saved = app.worktrees.read(sid, saved['id'])
            sizes.append(len(json.dumps(saved)))
        assert len(saved['boundaryOperations']) == 1
        assert max(sizes) - min(sizes) < 100
        read = (await app.dispatch('worktree.list', {'sessionId': sid, 'handoffId': saved['id'],
                                                    'runtimeOperationId': first}))['result']
        assert read['runtimeOperation']['id'] == first
        assert read['runtimeOperation']['state'] == 'returned'
        assert not read['runtimeOperation']['result']['ready']
        assert read['settlement']['status'] == 'blocked'
        assert len(list((app.worktrees.receipt_dir / saved['id']).glob('*.json'))) == polls
        runtime.handoff_boundary.assert_awaited_once()
    finally:
        await app.close()


async def test_original_ready_owner_cannot_release_successor(tmp_path, monkeypatch):
    manager = RuntimeManager()
    manager.workers['session'] = {'runtimeOwnerId': 'successor'}
    try:
        with pytest.raises(ValueError, match='owner changed'):
            await manager.quiesce_for_handoff({'id': 'session', 'workspace': str(tmp_path)},
                                             'original-handoff', expected_runtime_owner='original')
    finally:
        manager.workers.clear()
        await manager.retention.close()


async def test_successor_cannot_issue_cleanup_for_original_boundary(tmp_path, monkeypatch):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved, _ = await uncertain(app, runtime, sid)
        saved = reply(app, saved, {'ready': False})
        successor = operation(saved, kind='cancel')
        successor['runtimeOwnerId'] = 'successor'
        with pytest.raises(ValueError, match='owner changed'):
            app.worktrees.observe_boundary(sid, saved['id'], successor)
        original = copy.deepcopy(next(iter(saved['boundaryOperations'].values())))
        with pytest.raises(ValueError, match='cannot be replaced'):
            app.worktrees.observe_boundary(sid, saved['id'], {**original, 'result': {'ready': True}})
        assert app.worktrees.read(sid, saved['id']) == saved
    finally:
        await app.close()


async def test_timeout_exposes_exact_rpc_without_repeating_or_discarding_issuance(monkeypatch):
    manager = RuntimeManager()
    future = asyncio.get_running_loop().create_future()
    row = {'pending': {'exact-rpc': future}, 'inflight': {'exact-rpc'}}
    wait_for = asyncio.wait_for
    async def immediate_timeout(awaitable, timeout):
        return await wait_for(awaitable, 0)
    monkeypatch.setattr('amplifier_web.runtime.asyncio.wait_for', immediate_timeout)
    try:
        with pytest.raises(RuntimeOperationPending) as caught:
            await manager._reply(row, 'exact-rpc', future, op='handoff.boundary', args={})
        assert caught.value.operation_id == 'exact-rpc'
        assert row['inflight'] == {'exact-rpc'}
    finally:
        await manager.retention.close()


async def test_preflight_refusal_retires_provisional_native_hook(tmp_path, monkeypatch):
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    runtime.handoff_boundary = AsyncMock(return_value={'cancelled': True})
    try:
        target = await checkout(app, sid)
        def refuse(*args):
            raise ValueError('controlled preflight refusal after provisional drain admission')
        monkeypatch.setattr(app.worktrees, 'preflight', refuse)
        saved = await move(app, sid, target)
        assert saved['phase'] == 'rejected' and saved['boundaryCleanup']['cancelled']
        runtime.handoff_boundary.assert_awaited_once_with(sid, saved['id'], cancel=True)
        runtime.quiesce_for_handoff.assert_not_awaited()
        assert not app.worktrees.execution_state(sid)['fenced']
    finally:
        await app.close()