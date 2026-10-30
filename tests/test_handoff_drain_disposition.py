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
async def test_failed_handler_reports_exact_absence_or_committed_receipt(app, monkeypatch, after_commit):
    session = app._session()
    sid = session['id']
    target = await checkout(app, sid)
    args = {'action': 'worktree.handoff', 'id': 'move',
            'args': {'sessionId': sid, 'id': target['id'] if after_commit else 'missing-checkout',
                     'expectedExecutionRevision': 0}, '_runtimeSessionId': sid,
            '_generationId': session['collaborationGeneration']['id'],
            '_inputBindings': [{'inputId': 'originating-input'}],
            '_handoffSnapshot': {'children': [], 'jobs': []}}
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
