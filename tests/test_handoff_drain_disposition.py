"""Checkout bridges no longer install an arm/cancel or late-ACK protocol.

The original protocol tests are superseded by these absence controls, native
retirement/lease tests, and the retained legacy receipt admission tests.
"""
import asyncio

import pytest

from test_handoff_child_admission import worker


@pytest.mark.parametrize('answer', ['pending', 'rejected', 'error', 'caller-cancelled'])
async def test_checkout_bridge_never_installs_a_marker_or_freezes_children(worker, monkeypatch, answer):
    value, children = worker
    messages = []
    sent = asyncio.Event()
    def publish(message):
        if message.get('op') == 'bridge':
            messages.append(message)
            sent.set()
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', publish)
    task = asyncio.create_task(value.bridge('dispatch', {'action': 'worktree.handoff', 'id': 'choice',
        '_runtimeSessionId': 'root', 'args': {}, '_handoffSnapshot': {'forged': True}}))
    await sent.wait()
    assert not getattr(value, '_handoff_boundary', None) and children.admission_gate is None
    assert '_handoffSnapshot' not in messages[0]['args']
    message = {'op': 'bridge.result', 'id': messages[0]['id']}
    if answer == 'caller-cancelled':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The original late reply is harmless. It cannot re-arm admission.
        message['result'] = {'accepted': True, 'result': {'id': 'saved-choice', 'phase': 'pending'}}
    elif answer == 'error':
        message.update(error='owned fixture failure', _handoffDisposition={'id': 'choice', 'phase': 'absent'})
    else:
        message['result'] = {'accepted': answer == 'pending', 'result': {'phase': answer}}
    await value._command_serial(message)
    if answer == 'error':
        with pytest.raises(RuntimeError, match='owned fixture failure'):
            await task
    elif answer != 'caller-cancelled':
        assert (await task)['accepted'] is (answer == 'pending')
    assert len(messages) == 1 and not value.bridges
    assert not getattr(value, '_handoff_boundary', None) and children.admission_gate is None
    assert not value.tasks
