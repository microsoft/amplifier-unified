"""Worker activity must not manufacture or reopen a lifecycle."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppService


@pytest.fixture
async def service(tmp_path):
    app = AppService(tmp_path, SimpleNamespace(close=AsyncMock()), workspace=tmp_path)
    app.state["sessions"] = [app._new_session({"title": "Worker history"})]
    yield app
    await app.close()


async def emit(service, event):
    sid = service.state["sessions"][0]["id"]
    kind, payload = normalize_event(event, sid)
    await service.on_runtime_event(kind, payload)
    return service.state["sessions"][0]


@pytest.mark.parametrize("status", ["completed", "error", "cancelled", "interrupted", "outcome_unknown", "idle"])
async def test_late_activity_preserves_worker_lifecycle_and_report(service, status):
    session = await emit(service, {"type": "child.updated", "sessionId": "child", "runId": "run-1",
        "status": status, "persistent": status == "idle", "report": "Saved result", "reportId": "receipt-1"})
    before = copy.deepcopy(session["workers"])
    await emit(service, {"type": "worker.activity", "workerId": "child", "runId": "run-1",
        "phase": "retrying", "time": 20, "detail": "Late hook"})
    assert session["workers"] == before
    assert service.session_state(session["id"])["sessions"][0]["workers"][0]["status"] == status
    agent = await service.dispatch("coordination.list", {"sessionId": session["id"]}, origin="agent")
    assert agent["result"]["items"][1]["status"] == status
    assert agent["result"]["items"][1]["canFollowup"] is (status == "idle")


async def test_activity_preserves_stopping_and_distinct_children(service):
    for identity, status in (("first", "stopping"), ("second", "running")):
        await emit(service, {"type": "child.updated", "sessionId": identity, "runId": identity,
            "status": status, "callId": "shared-call"})
    session = await emit(service, {"type": "worker.activity", "workerId": "first", "runId": "first",
        "phase": "tools", "callId": "shared-call"})
    assert [(w["id"], w["status"]) for w in session["workers"]] == [("first", "stopping"), ("second", "running")]


async def test_real_new_run_starts_but_old_run_activity_cannot_overwrite_it(service):
    await emit(service, {"type": "child.updated", "sessionId": "child", "runId": "old", "status": "completed"})
    session = await emit(service, {"type": "child.updated", "sessionId": "child", "runId": "new", "status": "running"})
    before = copy.deepcopy(session["workers"])
    await emit(service, {"type": "worker.activity", "workerId": "child", "runId": "old", "phase": "tools"})
    assert session["workers"] == before
    await emit(service, {"type": "worker.activity", "workerId": "child", "runId": "new", "phase": "retrying"})
    assert session["workers"][0]["phase"] == "retrying"
    assert session["workers"][0]["status"] == "running"


@pytest.mark.parametrize("activity_run", [None, "old"])
async def test_unbound_or_old_run_activity_is_quiet(service, activity_run):
    session = await emit(service, {"type": "child.updated", "sessionId": "child", "runId": "new", "status": "running"})
    before = copy.deepcopy(session)
    revision = service.state["revision"]
    await emit(service, {"type": "worker.activity", "workerId": "child", "runId": activity_run, "phase": "tools"})
    assert session == before
    assert service.state["revision"] == revision


async def test_orphan_activity_does_not_create_a_worker(service):
    session = service.state["sessions"][0]
    before = copy.deepcopy(session)
    await emit(service, {"type": "worker.activity", "workerId": "unknown", "phase": "tools"})
    assert session == before


async def test_progress_changes_only_current_activity_fields(service):
    session = await emit(service, {"type": "child.updated", "sessionId": "child", "runId": "current",
        "status": "running", "agent": "Original", "callId": "original-call", "persistent": True,
        "parentSessionId": "parent", "report": "Saved result", "reportId": "saved-report"})
    original = copy.deepcopy(session["workers"][0])
    await emit(service, {"type": "worker.activity", "workerId": "child", "runId": "current",
        "phase": "retrying", "detail": "Retrying", "time": 22, "retryAttempt": 2, "retryMax": 3,
        "name": "Replacement", "callId": "wrong-call", "persistent": False, "report": "Wrong"})
    assert session["workers"][0] == {**original, "phase": "retrying", "detail": "Retrying",
        "updatedAt": 22, "retryAttempt": 2, "retryMax": 3}


async def test_terminal_job_does_not_settle_children_with_shared_call_id(service):
    for identity in ("first", "second"):
        await emit(service, {"type": "child.updated", "sessionId": identity, "runId": identity,
            "status": "running", "callId": "shared-call"})
    session = await emit(service, {"type": "job.returned", "job_id": "job", "call_id": "shared-call"})
    assert [(w["id"], w["status"]) for w in session["workers"]] == [
        ("first", "running"), ("second", "running"), ("job", "completed")]


async def test_terminal_projection_stays_settled_for_update_readiness_and_waiters(service):
    from amplifier_web.updates import UpdateManager
    session = await emit(service, {"type": "child.updated", "sessionId": "child", "runId": "run",
        "status": "completed", "report": "Saved result", "reportId": "receipt"})
    session["status"] = "idle"
    manager = UpdateManager(service)
    target = {"sessionId": session["id"], "workerId": "child"}
    first = (await service.dispatch("coordination.wait", {"targets": [target], "waitMs": 0}, origin="agent"))["result"]
    assert not manager.busy()
    await emit(service, {"type": "worker.activity", "workerId": "child", "runId": "run", "phase": "tools"})
    repeated = (await service.dispatch("coordination.wait", {"targets": [
        {**target, "afterCursor": first["targets"][0]["nextCursor"]}], "waitMs": 0}, origin="agent"))["result"]
    assert not repeated["changed"]
    assert repeated["targets"][0]["results"] == []
    assert not manager.busy()
    await emit(service, {"type": "child.updated", "sessionId": "child", "runId": "resumed", "status": "running"})
    assert manager.busy()
