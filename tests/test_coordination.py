import asyncio
import copy

import pytest

from amplifier_operations.coordination import ChangeSignal, delivery
from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppError, AppService


class Runtime:
    def __init__(self):
        self.sent, self.messages, self.stops = [], [], []
        self.failure = False

    async def send(self, session, text, input_id, emit):
        self.sent.append((session["id"], text, input_id))
        return {"accepted": True}

    async def message_worker(self, sid, wid, text, input_id=None):
        self.messages.append((sid, wid, text, input_id))
        if self.failure:
            raise RuntimeError("Connection lost after submission")
        return {"accepted": True, "inputId": input_id, "completed": False}

    async def stop_worker(self, sid, wid):
        self.stops.append((sid, wid))

    async def stop(self, sid):
        self.stops.append((sid,))

    async def close(self):
        pass


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    # No runtime is started by constructing saved authoritative records.
    service.state["sessions"] = [service._new_session({"title": title}) for title in ("Selected", "Other")]
    service.state["selectedSessionId"] = service.state["sessions"][0]["id"]
    service.state["view"]["draft"] = "Unsent original"
    service._publish()
    yield service
    if not service.closed:
        await service.close()


async def worker(app, sid, wid="child-a", **patch):
    await app.on_runtime_event("worker.updated", {"sessionId": sid, "id": wid, "kind": "session", "parentSessionId": "native-parent", "runId": "run-1", "persistent": True, "status": "running", **patch})


async def wait(app, targets, **args):
    return (await app.dispatch("coordination.wait", {"targets": targets, **args}))["result"]


def after(result):
    return [{**row["target"], "afterCursor": row["nextCursor"]} for row in result["targets"]]


async def test_wait_two_targets_wakes_first_report_ignores_activity_and_dedups(app):
    sid = app.state["sessions"][1]["id"]
    await worker(app, sid)
    baseline = await wait(app, [{"sessionId": sid}, {"sessionId": sid, "workerId": "child-a"}])
    waiter = asyncio.create_task(wait(app, after(baseline), waitMs=1000))
    await asyncio.sleep(.01)
    await app.on_runtime_event("worker.updated", {"sessionId": sid, "id": "child-a", "phase": "retrying", "status": "running"})
    await asyncio.sleep(.01)
    assert not waiter.done()
    await worker(app, sid, status="idle", report="First report", reportId="report-1", reports=1)
    result = await waiter
    child = result["targets"][1]
    assert child["results"][0]["id"] == "report-1"
    assert child["parentSessionId"] == "native-parent"
    assert child["status"] == "idle"  # A persistent report does not complete its responsibility.
    assert not result["timedOut"]
    assert app.state["view"]["draft"] == "Unsent original"
    assert app.state["selectedSessionId"] != sid
    await worker(app, sid, status="idle", report="First report", reportId="report-1", reports=1)
    replay = await wait(app, after(result), waitMs=1)
    assert replay["timedOut"] and not replay["changed"]
    assert replay["targets"][1]["results"] == []
    assert app.runtime.sent == []


async def test_reconnect_and_restart_keep_receipt_ids_without_replay(app):
    sid = app.state["sessions"][0]["id"]
    await worker(app, sid, status="idle", report="A saved result", reportId="stable-report")
    first = await wait(app, [{"sessionId": sid, "workerId": "child-a"}])
    # Repeating an unacknowledged cursor is intentionally repeatable; clients
    # deduplicate receipt IDs, then persist the advanced cursor.
    retry = await wait(app, [{"sessionId": sid, "workerId": "child-a"}])
    assert first["targets"][0]["results"] == retry["targets"][0]["results"]
    await app.close()
    app.closed = True
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        resumed = await wait(reopened, after(first), waitMs=1)
        assert resumed["targets"][0]["results"] == []
        assert not reopened.runtime.sent and not reopened.runtime.messages
    finally:
        await reopened.close()


async def test_followups_use_shared_admission_preserve_selection_and_draft(app):
    selected, target = app.state["sessions"]
    await app.dispatch("coordination.followup", {"sessionId": target["id"], "text": "Explicit follow-up"}, command_id="top-followup")
    assert app.state["selectedSessionId"] == selected["id"]
    assert app.state["view"]["draft"] == "Unsent original"
    assert app.runtime.sent == [(target["id"], "Explicit follow-up", "top-followup")]
    await worker(app, target["id"], status="idle")
    args = {"sessionId": target["id"], "workerId": "child-a", "text": "Another step"}
    first = await app.dispatch("coordination.followup", args, command_id="worker-followup")
    second = await app.dispatch("coordination.followup", args, command_id="worker-followup")
    assert first["delivery"] == second["delivery"] == "accepted"
    assert second["duplicate"]
    assert len(app.runtime.messages) == 1
    assert app.runtime.messages[0][-1] == "worker-followup"


async def test_unknown_followup_is_not_replayed(app):
    sid = app.state["sessions"][0]["id"]
    await worker(app, sid, status="idle")
    app.runtime.failure = True
    args = {"sessionId": sid, "workerId": "child-a", "text": "Maybe delivered"}
    with pytest.raises(RuntimeError, match="Connection lost"):
        await app.dispatch("coordination.followup", args, command_id="uncertain")
    result = await app.dispatch("coordination.followup", args, command_id="uncertain")
    assert result["delivery"] == "unknown" and result["duplicate"]
    assert len(app.runtime.messages) == 1


async def test_followup_retry_after_browser_identity_rotation_deduplicates(app):
    sid = app.state["sessions"][0]["id"]
    await worker(app, sid, status="idle")
    args = {"sessionId": sid, "workerId": "child-a", "text": "Once across reconnect"}
    app.clients.attach("before")
    app.clients.attach("after", resume="before")
    with app.clients.bind("before"):
        await app.dispatch("coordination.followup", args, command_id="same-request")
    with app.clients.bind("after"):
        result = await app.dispatch("coordination.followup", args, command_id="same-request")
    assert result["duplicate"] and result["delivery"] == "accepted"
    assert len(app.runtime.messages) == 1


async def test_shutdown_during_submission_preserves_unknown_receipt(app):
    sid = app.state["sessions"][0]["id"]
    await worker(app, sid, status="idle")
    entered = asyncio.Event()
    async def pending(*args):
        entered.set()
        await asyncio.Event().wait()
    app.runtime.message_worker = pending
    args = {"sessionId": sid, "workerId": "child-a", "text": "No replay after crash"}
    submission = asyncio.create_task(app.dispatch("coordination.followup", args, command_id="crash-window"))
    await entered.wait()
    submission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submission
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    try:
        result = await reopened.dispatch("coordination.followup", args, command_id="crash-window")
        assert result["duplicate"] and result["delivery"] == "unknown"
        assert reopened._session(sid)["workers"][0]["status"] == "interrupted"
        assert not reopened.runtime.messages
    finally:
        await reopened.close()


async def test_agent_can_control_own_workers_but_not_other_conversations(app):
    caller, other = app.state["sessions"]
    await worker(app, caller["id"], status="idle")
    args = {"sessionId": caller["id"], "workerId": "child-a", "text": "Scoped step"}
    result = await app.app_bridge("dispatch", {"action": "coordination.followup", "args": args, "id": "agent-step"}, caller["id"])
    assert result["delivery"] == "accepted"
    with pytest.raises(AppError, match="user must explicitly"):
        await app.app_bridge("dispatch", {"action": "coordination.followup", "args": {"sessionId": other["id"], "text": "Unapproved cross-task work"}}, caller["id"])
    with pytest.raises(AppError, match="user must explicitly"):
        await app.dispatch("coordination.followup", args, origin="agent")
    assert len(app.runtime.messages) == 1


async def test_interrupt_shared_stop_epoch_is_idempotent_and_not_completion(app):
    sid = app.state["sessions"][0]["id"]
    app._session(sid)["status"] = "working"
    first = await app.dispatch("coordination.interrupt", {"sessionId": sid}, command_id="stop-1")
    second = await app.dispatch("coordination.interrupt", {"sessionId": sid}, command_id="stop-1")
    assert first["accepted"] and second["duplicate"]
    assert app._session(sid)["interruptionRevision"] == 1
    assert app._session(sid)["lastInterruption"]["commandId"] == "stop-1"
    await asyncio.sleep(.01)
    assert app.runtime.stops == [(sid,)]
    assert app._session(sid)["status"] != "completed"


async def test_attention_wakes_without_selecting_and_keeps_task_identity_separate(app):
    sid = app.state["sessions"][1]["id"]
    app._session(sid)["status"] = "working"
    baseline = await wait(app, [{"sessionId": sid}])
    pending = asyncio.create_task(wait(app, after(baseline), waitMs=1000))
    await asyncio.sleep(.01)
    app.state.setdefault("runtimeControl", {})[sid] = {"task.get": {"task": {"id": "saved-task", "status": "active", "revision": 3}}}
    response = await app.dispatch("question.create", {"sessionId": sid, "prompt": "Choose the report color", "required": True, "dependency": "report", "allowFreeText": True})
    question_id = response["result"]["id"]
    result = (await pending)["targets"][0]
    assert result["attention"] and result["questionIds"] == [question_id]
    assert result["taskId"] == "saved-task" and result["target"]["sessionId"] == sid
    assert app.state["selectedSessionId"] != sid


async def test_retained_worker_reports_are_bounded_and_not_in_hot_browser_projection(app):
    sid = app.state["sessions"][0]["id"]
    for i in range(35):
        await worker(app, sid, status="idle", report="Text" + str(i), reportId=f"report-{i}")
    snapshot = (await wait(app, [{"sessionId": sid, "workerId": "child-a"}]))["targets"][0]
    assert snapshot["cursorGap"] and snapshot["earliestSequence"] == 4
    assert len(app._session(sid)["workers"][0]["reportReceipts"]) == 32
    browser = app.browser_state()
    assert "reportReceipts" not in browser["sessions"][0]["workers"][0]
    assert len((await app.dispatch("coordination.list", {}))["result"]["items"]) == 3


async def test_repeated_generation_final_has_one_authoritative_message(app):
    sid = app.state["sessions"][0]["id"]
    payload = {"sessionId": sid, "inputId": "input", "generationId": "generation", "text": "A final response"}
    await app.on_runtime_event("assistant.message", payload)
    await app.on_runtime_event("assistant.message", payload)
    assert len(app._session(sid)["messages"]) == 1
    result = await wait(app, [{"sessionId": sid}])
    assert len(result["targets"][0]["results"]) == 1


def test_cursor_rejects_other_identity_marks_rewrites_and_can_acknowledge_expired_history():
    source = {"identity": "one", "results": [{"id": "a", "sequence": 1, "text": "old"}]}
    cursor = delivery(source)["nextCursor"]
    with pytest.raises(ValueError, match="another target"):
        delivery({**source, "identity": "two"}, cursor)
    rewritten = delivery({**source, "results": [{"id": "b", "sequence": 1, "text": "new"}]}, cursor)
    assert rewritten["cursorGap"]
    expired = {"identity": "one", "results": [], "latestSequence": 50}
    gap = delivery(expired, cursor)
    assert gap["cursorGap"] and not gap["hasMore"]
    assert not delivery(expired, gap["nextCursor"])["changed"]


async def test_target_validation_partial_errors_and_cancel_wait(app):
    sid = app.state["sessions"][0]["id"]
    baseline = await wait(app, [{"sessionId": sid}])
    wrong = copy.deepcopy(after(baseline))
    wrong[0]["sessionId"] = app.state["sessions"][1]["id"]
    result = await wait(app, wrong + [{"sessionId": sid, "workerId": "missing"}])
    assert len(result["errors"]) == 2
    with pytest.raises(AppError, match="distinct"):
        await wait(app, [{"sessionId": sid}] * 2)
    pending = asyncio.create_task(wait(app, after(baseline), waitMs=60000))
    await asyncio.sleep(.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not app.runtime.stops


def test_pages_bound_utf8_and_acknowledge_only_delivered_reports():
    rows = [{"id": f"r{i}", "sequence": i, "text": "🌱" * 300} for i in range(5, 9)]
    source = {"identity": "child", "results": rows, "signal": {"status": "completed"}}
    first = delivery(source, max_bytes=512)
    assert first["cursorGap"] and first["hasMore"]
    assert len(first["results"][0]["text"].encode()) == 512
    assert first["results"][0]["textTruncated"]
    second = delivery(source, first["nextCursor"], max_bytes=4096)
    assert [r["id"] for r in second["results"]] == ["r6", "r7", "r8"]
    assert not second["hasMore"]
    assert not delivery(source, second["nextCursor"])["changed"]


async def test_notifier_has_no_read_wait_lost_wakeup():
    signal = ChangeSignal()
    reads = 0
    def read():
        nonlocal reads
        reads += 1
        if reads == 1:
            signal.notify()
        return {"changed": reads == 2}
    result = await signal.wait(read, wait_ms=100)
    assert result["changed"] and not result["timedOut"] and reads == 2


def test_runtime_event_keeps_real_parent_run_and_report_identity():
    kind, value = normalize_event({"type": "child.updated", "sessionId": "child", "parentSessionId": "parent", "runId": "run", "reportId": "report", "reports": 2, "report": "Result"}, "conversation")
    assert kind == "worker.updated"
    assert (value["sessionId"], value["id"], value["parentSessionId"], value["runId"], value["reportId"]) == ("conversation", "child", "parent", "run", "report")
