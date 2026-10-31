"""A provisional handoff gate follows receipts, never transport guesses."""
import asyncio
import uuid

import pytest

from amplifier_web.handoff_drain import begin, finish
from amplifier_web.handoff_boundary import inspect
from test_handoff_child_admission import worker
from test_handoff_model_admission import app
from test_handoff_refusal import checkout


def identity(command='move'):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'worktree-handoff:' + command))


@pytest.mark.parametrize('after_commit', [False, True])
@pytest.mark.parametrize('omitted', [False, True])
async def test_failed_handler_reports_exact_absence_or_committed_receipt(app, monkeypatch, after_commit, omitted):
    session = app._session()
    sid = session['id']
    target = await checkout(app, sid)
    args = {'action': 'worktree.handoff', 'id': 'move',
            'args': {'sessionId': sid, 'id': target['id'] if after_commit else 'missing-checkout',
                     'expectedExecutionRevision': 0}, '_runtimeSessionId': sid,
            '_generationId': session['collaborationGeneration']['id'],
            '_inputBindings': [{'inputId': 'originating-input'}],
            '_handoffSnapshot': {'children': [], 'jobs': []}}
    if omitted:
        args['args'].pop('sessionId')
    if after_commit:
        original = app._bound_app_bridge
        async def fail_after_handler(*values):
            await original(*values)
            raise RuntimeError('fixture failure after durable receipt')
        monkeypatch.setattr(app, '_bound_app_bridge', fail_after_handler)
    with pytest.raises(Exception) as failed:
        await app.app_bridge('dispatch', args, sid)
    disposition = failed.value._handoff_disposition
    assert disposition['id'] == identity()
    assert disposition['phase'] == ('pending' if after_commit else 'absent')
    if after_commit:
        assert app.worktrees.read(sid, identity())['phase'] == 'pending'
        assert disposition['finishingTurn']['nativeDrain'] == args['_handoffSnapshot']


@pytest.mark.parametrize('response', ['absent', 'pending-error', 'mismatch', 'unknown', 'cancel'])
async def test_gate_tracks_reply_disposition_and_existing_reconciliation(worker, monkeypatch, response):
    value, children = worker
    sent = asyncio.Event()
    snapshots = []
    receipt = None
    def publish(message):
        nonlocal receipt
        if message.get('op') != 'bridge':
            return
        snapshots.append(message['args']['_handoffSnapshot'])
        receipt = {'id': identity(), 'phase': 'pending', 'finishingTurn': {
            'workerInstance': 'fixture', 'generationId': 'original', 'inputIds': ['original-input'],
            'configurationRevision': 0, 'nativeDrain': snapshots[-1]}}
        future = value.bridges[message['id']]
        if response != 'cancel':
            error = RuntimeError('original bridge error')
            if response in {'absent', 'pending-error', 'mismatch'}:
                error._handoff_disposition = receipt if response == 'pending-error' else {
                    'id': identity('different') if response == 'mismatch' else identity(), 'phase': 'absent'}
            future.set_exception(error)
        sent.set()
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', publish)
    task = asyncio.create_task(value.bridge('dispatch', {'action': 'worktree.handoff', 'id': 'move',
        '_runtimeSessionId': 'root', 'args': {'sessionId': 'root', 'id': None}}))
    await sent.wait()
    if response == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError, match='original bridge error'):
            await task
    if response == 'absent':
        assert value._handoff_boundary is None and children.admission_gate is None
    else:
        assert value._handoff_boundary['id'] == identity() and children.admission_gate
        # Existing boundary polling carries the exact saved receipt after a
        # lost/error reply. It neither repeats the action nor submits input.
        await value._command_serial({'op': 'handoff.boundary', 'id': 'poll',
                                     'request_id': identity(), 'receipt': receipt})
        assert value._handoff_boundary['drain']['binding'] == receipt['finishingTurn']
        assert len(snapshots) == 1
        await inspect(value, identity(), cancel=True)
        assert children.admission_gate is None


async def test_definite_local_encoding_failure_releases_before_publication(worker, monkeypatch):
    value, children = worker
    sent = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', sent.append)
    def reject_encoding(message):
        raise ValueError('fixture encoding failure before publication')
    monkeypatch.setattr('amplifier_web.runtime_worker.encode_message', reject_encoding)
    with pytest.raises(ValueError, match='before publication'):
        await value.bridge('dispatch', {'action': 'worktree.handoff', 'id': 'move',
            '_runtimeSessionId': 'root', 'args': {'sessionId': 'root', 'id': object()}})
    assert not any(message.get('op') == 'bridge' for message in sent)
    assert value._handoff_boundary is None and children.admission_gate is None


async def test_failed_disposition_cleanup_does_not_replace_original_error(worker, monkeypatch):
    value, children = worker
    async def broken(*args):
        raise ValueError('cleanup failed')
    monkeypatch.setattr('amplifier_web.handoff_drain.finish', broken)
    def publish(message):
        if message.get('op') == 'bridge':
            error = RuntimeError('original failure')
            error._handoff_disposition = {'id': identity(), 'phase': 'absent'}
            value.bridges[message['id']].set_exception(error)
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', publish)
    with pytest.raises(RuntimeError, match='original failure'):
        await value.bridge('dispatch', {'action': 'worktree.handoff', 'id': 'move',
            '_runtimeSessionId': 'root', 'args': {'sessionId': 'root', 'id': None}})
    assert children.admission_gate and value._handoff_boundary['id'] == identity()


@pytest.mark.parametrize('target', ['native', 'omitted', 'canonical', 'peer'])
async def test_only_own_handoff_forms_freeze_native_admission(worker, monkeypatch, target):
    value, children = worker
    value.start_config = {'ownerSessionId': 'canonical-owner'}  # Host start configuration, not tool input.
    sent = []
    def publish(message):
        if message.get('op') == 'bridge':
            sent.append(message)
            value.bridges[message['id']].set_result({'accepted': False})
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', publish)
    values = {'id': 'checkout'}
    if target != 'omitted':
        values['sessionId'] = {'native': 'root', 'canonical': 'canonical-owner', 'peer': 'different-chat'}[target]
    await value.bridge('action.dispatch', {'action': 'worktree.handoff', 'id': 'move',
        '_runtimeSessionId': 'root', 'args': values})
    assert ('_handoffSnapshot' in sent[0]['args']) is (target != 'peer')
    assert getattr(value, '_handoff_boundary', None) is None and children.admission_gate is None


@pytest.mark.parametrize('ending', ['cancel', 'timeout'])
@pytest.mark.parametrize('reply', ['absent', 'pending', 'mismatch', 'unknown'])
async def test_exact_late_reply_reconciles_after_bridge_waiter_is_gone(worker, monkeypatch, ending, reply):
    value, children = worker
    sent, messages = asyncio.Event(), []
    def publish(message):
        if message.get('op') == 'bridge':
            messages.append(message)
            sent.set()
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', publish)
    if ending == 'timeout':
        original_wait = asyncio.wait_for
        async def short_wait(awaitable, timeout):
            return await original_wait(awaitable, 0.001)
        monkeypatch.setattr('amplifier_web.runtime_worker.asyncio.wait_for', short_wait)
    task = asyncio.create_task(value.bridge('dispatch', {'action': 'worktree.handoff', 'id': 'move',
        '_runtimeSessionId': 'root', 'args': {}}))
    await sent.wait()
    if ending == 'cancel':
        task.cancel()
    with pytest.raises(asyncio.CancelledError if ending == 'cancel' else TimeoutError):
        await task
    assert not value.bridges and children.admission_gate
    response = {'op': 'bridge.result', 'id': messages[0]['id'], 'error': 'late validation failure'}
    if reply == 'absent':
        response['_handoffDisposition'] = {'id': identity(), 'phase': 'absent'}
    elif reply == 'mismatch':
        response['_handoffDisposition'] = {'id': identity(), 'phase': 'absent'}
        response['id'] = 'unrelated-bridge'
    elif reply == 'pending':
        response.pop('error')
        response['result'] = {'accepted': True, 'result': {'id': identity(), 'phase': 'pending',
            'finishingTurn': {'nativeDrain': messages[0]['args']['_handoffSnapshot']}}}
    await value._command_serial(response)
    assert len(messages) == 1  # No request replay.
    if reply == 'absent':
        assert value._handoff_boundary is None and children.admission_gate is None
    else:
        assert value._handoff_boundary and children.admission_gate
        if reply == 'pending':
            assert value._handoff_boundary['drain']['binding']
            # A stale no-effect reply cannot erase an already-known durable move.
            await value._command_serial({'op': 'bridge.result', 'id': messages[0]['id'],
                'error': 'old validation failure', '_handoffDisposition': {'id': identity(), 'phase': 'absent'}})
            assert value._handoff_boundary['drain']['binding']


async def test_partial_hook_installation_releases_its_own_gate(worker, monkeypatch):
    value, children = worker
    hooks = value.session.coordinator.hooks
    original, removed = hooks.register, []
    def failing_registration(event, handler, **kwargs):
        if kwargs.get('name') == 'unified-checkout-drain':
            raise RuntimeError('fixture registration failure')
        undo = original(event, handler, **kwargs)
        def unregister():
            removed.append(event)
            undo()
        return unregister
    from types import SimpleNamespace
    coordinator = value.session.coordinator
    value.session = SimpleNamespace(session_id=value.session.session_id, coordinator=SimpleNamespace(
        hooks=SimpleNamespace(register=failing_registration), session_state=coordinator.session_state,
        get=coordinator.get, get_capability=coordinator.get_capability))
    with pytest.raises(RuntimeError, match='registration failure'):
        await begin(value, 'move')
    assert value._handoff_boundary is None and children.admission_gate is None
    assert removed == ['orchestrator:goal_progress']


@pytest.mark.parametrize('response', ['rejected', 'absent'])
@pytest.mark.parametrize('cancel_at', ['reconciling', 'consumed'])
async def test_consumed_no_effect_ack_finishes_after_bridge_cancellation(worker, monkeypatch, response, cancel_at):
    value, children = worker
    sent, reconciling = asyncio.Event(), asyncio.Event()
    messages = []
    def publish(message):
        if message.get('op') == 'bridge':
            messages.append(message)
            sent.set()
    original = inspect
    async def blocked_inspect(worker, request_id, cancel=False):
        if cancel:
            reconciling.set()
        return await original(worker, request_id, cancel=cancel)
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', publish)
    monkeypatch.setattr('amplifier_web.handoff_boundary.inspect', blocked_inspect)
    bridge = asyncio.create_task(value.bridge('dispatch', {'action': 'worktree.handoff', 'id': 'move',
        '_runtimeSessionId': 'root', 'args': {}}))
    await sent.wait()
    await value.command_lock.acquire()
    try:
        message = {'op': 'bridge.result', 'id': messages[0]['id']}
        if response == 'rejected':
            message['result'] = {'accepted': False}
        else:
            message.update(error='definite validation failure',
                _handoffDisposition={'id': identity(), 'phase': 'absent'})
        await value._command_serial(message)
        if cancel_at == 'reconciling':
            await asyncio.wait_for(reconciling.wait(), 1)
        # The consumed variant cancels before the bridge waiter resumes.
        bridge.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bridge
        assert children.admission_gate and value._handoff_boundary
    finally:
        value.command_lock.release()
    # An exact reply was already consumed; no new host notification is coming.
    # Any worker-owned reconciliation must finish without replaying the move.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert value._handoff_boundary is None and children.admission_gate is None
    assert len(messages) == 1 and not value.bridges


async def test_cancelled_ack_reports_reconciliation_failure_and_retains_marker(worker, monkeypatch):
    value, children = worker
    entered, release = asyncio.Event(), asyncio.Event()
    published = []
    async def fail_after_wait(*args):
        entered.set()
        await release.wait()
        raise ValueError('private cleanup details')
    def publish(message):
        published.append(message)
        if message.get('op') == 'bridge':
            value.bridges[message['id']].set_result({'accepted': False})
    monkeypatch.setattr('amplifier_web.handoff_drain.finish', fail_after_wait)
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', publish)
    bridge = asyncio.create_task(value.bridge('dispatch', {'action': 'worktree.handoff', 'id': 'move',
        '_runtimeSessionId': 'root', 'args': {}}))
    await entered.wait()
    bridge.cancel()
    with pytest.raises(asyncio.CancelledError):
        await bridge
    assert len(value.tasks) == 1
    release.set()
    await asyncio.gather(*value.tasks, return_exceptions=True)
    assert not value.tasks and children.admission_gate and value._handoff_boundary
    diagnostic = next(message for message in published if message.get('phase') == 'handoff-drain-unavailable')
    assert diagnostic['errorType'] == 'ValueError' and 'private' not in str(diagnostic)
    assert sum(message.get('op') == 'bridge' for message in published) == 1
