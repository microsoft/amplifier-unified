"""A note reaches the paused native tool's context before its decision."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from amplifier_web.runtime_worker import Worker
from amplifier_web.host.approvals import Approvals, ApprovalDecision


@pytest.mark.parametrize('decision', ['allow', 'deny'])
async def test_worker_delivers_note_before_releasing_exact_waiter_and_does_not_duplicate(monkeypatch, decision):
    from amplifier_web import runtime_worker
    events=[]
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker=Worker()
    future=asyncio.get_running_loop().create_future()
    async def add(message):
        assert not future.done()
    context=SimpleNamespace(add_message=AsyncMock(side_effect=add))
    worker.session=SimpleNamespace(coordinator=SimpleNamespace(get=lambda kind:context))
    worker.approvals['request']=(future,['allow','deny'])
    command={'op':'approval','id':'reply','approval_id':'request','decision':decision,'comment':'Use a temporary file.'}
    await worker._command_serial(command)
    response=future.result()
    assert response == decision and response.comment == command['comment']
    assert context.add_message.call_args.args[0]['role'] == 'user'
    await worker._command_serial(command)
    assert context.add_message.await_count == 1
    assert any('expired' in event.get('error','').lower() for event in events)


async def test_provider_receives_note_as_decision_reason():
    async def ask(*args):
        return ApprovalDecision('deny', 'Use the workspace copy.')
    request=SimpleNamespace(tool_name='write',action='change file',details=None,timeout=2)
    response=await Approvals(None,ask).request_approval(request)
    assert not response.approved and response.reason == 'Use the workspace copy.'


async def test_concurrent_child_feedback_reaches_only_its_own_context(monkeypatch):
    from amplifier_web import runtime_worker
    events = []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker = Worker()
    root = SimpleNamespace(add_message=AsyncMock())
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda kind: root))
    contexts = [SimpleNamespace(add_message=AsyncMock()) for _ in range(2)]
    approvals = Approvals(None, worker.ask)
    tasks = [asyncio.create_task(approvals.for_context(lambda c=context: c).request_approval(str(index)))
             for index, context in enumerate(contexts)]
    try:
        await asyncio.sleep(0)
        requests = [event for event in events if event.get('type') == 'approval.requested']
        assert len(requests) == 2
        for request in reversed(requests):
            await worker._command_serial({'op':'approval', 'id':'reply-' + request['id'],
                'approval_id':request['id'], 'decision':'deny', 'comment':'Guidance for ' + request['prompt']})
        assert await asyncio.gather(*tasks) == ['deny', 'deny']
        for index, context in enumerate(contexts):
            context.add_message.assert_awaited_once()
            assert context.add_message.call_args.args[0]['content'].endswith('Guidance for ' + str(index))
        root.add_message.assert_not_awaited()
        assert not worker.approval_contexts
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
