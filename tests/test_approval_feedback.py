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


@pytest.mark.parametrize('outcome', ['success', 'lost_reply', 'append_failed', 'stopped', 'cancelled_waiter'])
async def test_overlapping_approval_retries_share_delivery_and_settle_waiter(monkeypatch, outcome):
    from amplifier_web import runtime_worker
    events, messages = [], []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker = Worker()
    entered, release = asyncio.Event(), asyncio.Event()
    async def add(message):
        messages.append(message)
        entered.set()
        await release.wait()
        if outcome == 'append_failed':
            raise OSError('Synthetic failure after append')
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: SimpleNamespace(add_message=add)))
    waiter = asyncio.create_task(worker.ask('Original request', ['allow', 'deny'], identity='request'))
    await asyncio.sleep(0)
    command = {'op': 'approval', 'id': 'first', 'approval_id': 'request',
               'decision': 'allow', 'comment': 'Use the temporary copy.'}
    first = asyncio.create_task(worker.command(command))
    retry = None
    try:
        await entered.wait()
        if outcome == 'lost_reply':
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        retry = asyncio.create_task(worker.command({**command, 'id': 'retry'}))
        await asyncio.sleep(0)
        assert len(messages) == 1
        assert not waiter.done()
        if outcome == 'stopped':
            worker.stop_revision += 1
        elif outcome == 'cancelled_waiter':
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        release.set()
        await asyncio.gather(first, retry, return_exceptions=True)
        if outcome in {'success', 'lost_reply'}:
            result = await waiter
            assert result == 'allow' and result.comment == command['comment']
            assert any(e.get('id') == 'retry' and e.get('result', {}).get('accepted') for e in events)
            assert not any(e.get('error') for e in events)
        elif outcome == 'cancelled_waiter':
            assert waiter.cancelled()
        else:
            with pytest.raises(RuntimeError, match='no approval was applied'):
                await waiter
            await worker.command({**command, 'id': 'late-retry'})
            assert not any(e.get('result', {}).get('accepted') for e in events)
        assert len(messages) == 1
        assert not worker.approvals
        assert not worker.approval_deliveries
    finally:
        release.set()
        for task in [first, retry, waiter, *worker.tasks]:
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in [first, retry, waiter, *worker.tasks] if t), return_exceptions=True)


async def test_conflicting_reply_cannot_replace_inflight_decision(monkeypatch):
    from amplifier_web import runtime_worker
    events, messages = [], []
    monkeypatch.setattr(runtime_worker, 'publish', events.append)
    worker = Worker()
    entered, release = asyncio.Event(), asyncio.Event()
    async def add(message):
        messages.append(message)
        entered.set()
        await release.wait()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: SimpleNamespace(add_message=add)))
    waiter = asyncio.create_task(worker.ask('Original request', ['allow', 'deny'], identity='request'))
    await asyncio.sleep(0)
    command = {'op': 'approval', 'id': 'first', 'approval_id': 'request', 'decision': 'deny', 'comment': 'Do not write.'}
    first = asyncio.create_task(worker.command(command))
    try:
        await entered.wait()
        await worker.command({**command, 'id': 'conflict', 'decision': 'allow', 'comment': 'Write now.'})
        assert any(e.get('id') == 'conflict' and 'different decision' in e.get('error', '') for e in events)
        release.set()
        await first
        assert await waiter == 'deny'
        assert len(messages) == 1 and 'Do not write.' in messages[0]['content']
    finally:
        release.set()
        first.cancel()
        waiter.cancel()
        await asyncio.gather(first, waiter, return_exceptions=True)
