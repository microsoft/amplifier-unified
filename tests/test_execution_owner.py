"""Attachment isolation and backpressure; native policy is tested by its owners."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from amplifier_web.errors import AppError
from amplifier_web.execution_owner import ExecutionOwner, Projection
from amplifier_web.runtime_event_batches import EventSink


class Service:
    def __init__(self):
        self.calls, self.events = [], []
        self.session = {}
        self.completed = asyncio.get_running_loop().create_future()
        self.on_runtime_event = EventSink(self.event, self.batch)

    async def event(self, kind, payload):
        self.events.append((kind, payload))

    async def batch(self, events):
        self.events.extend(events)

    def _task(self, coroutine):
        return asyncio.create_task(coroutine)

    async def dispatch(self, action, args, **kwargs):
        self.calls.append((action, args, kwargs))
        if action == "conversation.stop":
            self.session["lastInterruption"] = {"commandId": kwargs["command_id"]}
        return {"accepted": True, "result": {"sessionId": "actual-owner"}}

    async def wait_for_response(self, sid, input_id, *, timeout):
        assert (sid, input_id, timeout) == ("actual-owner", "input", None)
        return await asyncio.shield(self.completed)

    def _session(self, identity, **kwargs):
        assert identity == "actual-owner"
        return self.session

    async def close(self):
        pass


async def attached(tmp_path):
    service = Service()
    owner = ExecutionOwner(service, workspace=tmp_path, bundle="configured", origin="ui")
    assert await owner.create(str(tmp_path)) == "actual-owner"
    return owner, service


async def started(owner, observe):
    task = asyncio.create_task(owner.prompt("actual-owner", "input", "text", observe))
    while not owner.pending:
        await asyncio.sleep(0)
    return task


async def test_projection_follows_owner_and_filters_other_inputs(tmp_path):
    owner, service = await attached(tmp_path)
    seen = []
    async def observe(event):
        assert service.events[-1][1]["inputId"] == "input"
        seen.append(event)
    task = await started(owner, observe)
    for sid, input_id in [("unrelated", "input"), ("actual-owner", "old"), ("actual-owner", "input")]:
        await service.on_runtime_event("assistant.delta", {"sessionId": sid, "inputId": input_id, "text": "public", "private": "never projected"})
    service.completed.set_result({"text": "finished", "active_job_ids": ["background"]})
    assert await task == {"outcome": "completed", "text": "finished"}
    assert seen == [{"type": "assistant.delta", "text": "public"}]
    await owner.close()


async def test_detached_generation_is_not_fabricated_cancellation(tmp_path):
    owner, service = await attached(tmp_path)
    task = await started(owner, lambda event: None)
    service.completed.set_exception(AppError("Turn detached without completed response"))
    with pytest.raises(AppError, match="detached"):
        await task
    await owner.close()


async def test_cancel_requires_original_stop_completion_and_completed_race_wins(tmp_path):
    owner, service = await attached(tmp_path)
    task = await started(owner, lambda event: None)
    await owner.cancel("actual-owner")
    await owner.cancel("actual-owner")
    assert [row[0] for row in service.calls].count("conversation.stop") == 1
    service.completed.set_result({"text": "already finished"})
    assert (await task)["outcome"] == "completed"
    await owner.cancel("actual-owner")
    assert [row[0] for row in service.calls].count("conversation.stop") == 1
    await owner.close()


async def test_release_does_not_stop_work_and_settles_observer(tmp_path):
    owner, service = await attached(tmp_path)
    task = await started(owner, lambda event: None)
    await owner.release("actual-owner")
    with pytest.raises(ValueError, match="Observation detached"):
        await task
    assert not service.completed.done()
    assert not any(row[0] == "conversation.stop" for row in service.calls)
    await owner.close()


async def test_native_failure_during_failed_stop_does_not_strand_prompt(tmp_path):
    owner, service = await attached(tmp_path)
    dispatch = service.dispatch
    stopping, fail_stop = asyncio.Event(), asyncio.Event()
    async def pending_stop(action, args, **kwargs):
        if action == "conversation.stop":
            stopping.set()
            await fail_stop.wait()
            raise AppError("Stop persistence failed")
        return await dispatch(action, args, **kwargs)
    service.dispatch = pending_stop
    prompt = await started(owner, lambda event: None)
    stop = asyncio.create_task(owner.cancel("actual-owner"))
    await stopping.wait()
    service.completed.set_exception(AppError("Native turn failed"))
    await asyncio.sleep(0)
    fail_stop.set()
    with pytest.raises(AppError, match="Stop persistence failed"):
        await stop
    with pytest.raises(ValueError, match="Stop did not settle"):
        await asyncio.wait_for(prompt, 1)
    await owner.close()


async def test_slow_observer_is_bounded_and_failure_does_not_enter_owner(monkeypatch):
    from amplifier_web import execution_owner
    monkeypatch.setattr(execution_owner, "MAX_MESSAGE_BYTES", 160)
    gate = asyncio.Event()
    async def slow(event):
        await gate.wait()
    projection = Projection(slow)
    try:
        for _ in range(100):
            projection.offer({"type": "assistant.delta", "text": "x" * 20})
        assert projection.bytes <= 160
        assert projection.queue.qsize() <= execution_owner.MAX_EVENTS
        assert projection.failed.done()
        with pytest.raises(ValueError, match="backlog"):
            await projection.finish()
    finally:
        await projection.close()


async def test_observer_failure_cannot_poison_canonical_ingestion(tmp_path):
    owner, service = await attached(tmp_path)
    async def broken(event):
        raise OSError("Client pipe closed")
    task = await started(owner, broken)
    await service.on_runtime_event("assistant.delta", {
        "sessionId": "actual-owner", "inputId": "input", "text": "persisted"})
    with pytest.raises(ValueError, match="Observation delivery failed"):
        await asyncio.wait_for(task, 1)
    assert service.events[-1][1]["text"] == "persisted"
    assert not service.completed.done()
    await owner.close()


@pytest.mark.parametrize("standing_full", [True, False])
async def test_pending_approval_waits_for_owner_then_reports_unavailable(tmp_path, standing_full):
    owner, service = await attached(tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()
    approval = {"id": "exact-approval", "status": "pending"}
    service.session["approvals"] = [approval]
    async def settle(sid, *, wait):
        assert wait is True and sid == "actual-owner"
        entered.set()
        await finish.wait()
        if standing_full:
            approval["status"] = "allow"
    service.access_mode = SimpleNamespace(settle=settle)
    service._task = asyncio.create_task
    prompt = await started(owner, lambda event: None)
    await service.on_runtime_event("approval.requested", {
        "sessionId": "actual-owner", "id": "exact-approval"})
    await entered.wait()
    assert not prompt.done()
    finish.set()
    if standing_full:
        service.completed.set_result({"text": "authorized"})
        assert (await prompt)["outcome"] == "completed"
        assert approval["status"] == "allow"
    else:
        with pytest.raises(ValueError, match="Interactive approval is unavailable"):
            await asyncio.wait_for(prompt, 1)
        assert approval["status"] == "pending" and not service.completed.done()
    assert not any(row[0] in {"conversation.stop", "approval.respond"} for row in service.calls)
    await owner.close()


@pytest.mark.parametrize("first", [0, 1])
async def test_shared_observers_keep_one_canonical_sink_in_either_close_order(tmp_path, first):
    service = Service()
    service.close = AsyncMock()
    original = service.on_runtime_event
    owners = [ExecutionOwner(service, workspace=tmp_path, bundle="configured", origin="ui")
              for _ in range(2)]
    sink = service.on_runtime_event
    assert owners[0]._observers is owners[1]._observers
    assert owners[0]._observers.original is original
    seen = [[], []]

    def observer(index):
        async def observe(kind, payload):
            assert (kind, payload) in service.events  # Canonical ingestion is first.
            seen[index].append((kind, payload))
        return observe
    for index, owner in enumerate(owners):
        owner.observe = observer(index)

    event = ("assistant.delta", {"sessionId": "actual-owner", "inputId": "input", "text": "one"})
    await sink(*event)
    batch = [("assistant.delta", {**event[1], "text": "two"}),
             ("assistant.message", {**event[1], "text": "three"})]
    await sink.batch(batch)
    assert service.events == [event, *batch]
    assert seen == [[event, *batch], [event, *batch]]

    await owners[first].disconnect()
    assert service.on_runtime_event is sink
    later = ("assistant.delta", {**event[1], "text": "after disconnect"})
    await service.on_runtime_event(*later)
    assert seen[first] == [event, *batch]
    assert seen[1-first] == [event, *batch, later]
    with pytest.raises(ValueError, match="disconnected"):
        await owners[first].create(str(tmp_path))

    await owners[1-first].disconnect()
    await owners[first].disconnect()  # Repeated cleanup cannot disturb the host.
    assert service.on_runtime_event is sink
    assert not service._execution_observers.attachments
    service.close.assert_not_called()
    reopened = ExecutionOwner(service, workspace=tmp_path, bundle="configured", origin="ui")
    reopened.observe = AsyncMock()
    # A resident worker can retain its callback across every client's departure.
    # New attachments must join that exact tap, not a new wrapper around it.
    assert service.on_runtime_event is sink
    await sink(*later)
    reopened.observe.assert_awaited_once_with(*later)
    assert service.events == [event, *batch, later, later]
    await reopened.close()  # The standalone launcher still owns service shutdown.
    service.close.assert_awaited_once()
    assert not service._execution_observers.attachments


async def test_disconnect_settles_observation_and_drain_without_stopping_work(tmp_path):
    owner, service = await attached(tmp_path)
    service.close = AsyncMock()
    other = ExecutionOwner(service, workspace=tmp_path, bundle="configured", origin="ui")
    sink = service.on_runtime_event
    entered = asyncio.Event()
    async def slow(event):
        entered.set()
        await asyncio.Event().wait()
    task = await started(owner, slow)
    row = owner.pending["actual-owner"]
    await sink("assistant.delta", {"sessionId": "actual-owner", "inputId": "input", "text": "saved"})
    await entered.wait()
    await owner.disconnect()
    with pytest.raises(ValueError, match="Observation detached"):
        await task
    assert not owner.pending and not owner.attached
    assert row["delivery"].task.done()
    assert not service.completed.done()
    assert not any(call[0] == "conversation.stop" for call in service.calls)
    assert service.on_runtime_event is sink
    service.close.assert_not_called()
    await other.disconnect()


async def test_disconnection_during_creation_keeps_created_work_without_late_attachment(tmp_path):
    service = Service()
    service.close = AsyncMock()
    owner = ExecutionOwner(service, workspace=tmp_path, bundle="configured", origin="ui")
    entered, proceed = asyncio.Event(), asyncio.Event()
    dispatch = service.dispatch
    async def held_create(*args, **kwargs):
        receipt = await dispatch(*args, **kwargs)
        entered.set()
        await proceed.wait()
        return receipt
    service.dispatch = held_create
    create = asyncio.create_task(owner.create(str(tmp_path)))
    await entered.wait()
    await owner.disconnect()
    proceed.set()
    with pytest.raises(ValueError, match="disconnected"):
        await create
    assert not owner.attached
    assert [call[0] for call in service.calls] == ["session.create"]
    service.close.assert_not_called()


async def test_disconnection_during_mcp_binding_releases_only_its_binding(tmp_path):
    service = Service()
    entered, proceed = asyncio.Event(), asyncio.Event()
    bound = {}
    async def bind(identity, attachment, servers):
        entered.set()
        await proceed.wait()
        bound[identity] = attachment
    def release(identity, attachment):
        assert bound[identity] == attachment
        del bound[identity]
    service.runtime = SimpleNamespace(bind_ephemeral_mcp=bind, release_ephemeral_mcp=release)
    owner = ExecutionOwner(service, workspace=tmp_path, bundle="configured", origin="ui", enable_mcp=True)
    create = asyncio.create_task(owner.create(str(tmp_path), mcp_servers={}))
    await entered.wait()
    await owner.disconnect()
    proceed.set()
    with pytest.raises(ValueError, match="disconnected"):
        await create
    assert not bound and not owner.attached
