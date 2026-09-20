"""Durable evidence, ownership, event waits, and approved process controls."""

import asyncio
import json
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_operations import OperationJournal
from amplifier_web.service import AppError, AppService


def event(identity="p1", sequence=1, phase="started", **data):
    return {
        "schemaVersion": 1,
        "eventId": f"{identity}:{sequence}",
        "sequence": sequence,
        "operationId": identity,
        "ownerId": "mount1",
        "source": "tool-bash",
        "kind": "process",
        "phase": phase,
        "at": 1,
        **data,
    }


def output(identity="p1", sequence=2, cursor=0, text="hello\n"):
    return event(
        identity,
        sequence,
        "output",
        chunk={
            "cursor": cursor,
            "next_cursor": cursor + 1,
            "stream": "stdout",
            "text": text,
            "source_bytes": len(text.encode()),
            "binary_output_withheld": False,
        },
    )


def final(identity="p1", sequence=3, **status):
    return event(
        identity,
        sequence,
        "finished",
        status={
            "state": "completed",
            "returncode": 0,
            "output_complete": True,
            "total_output_bytes": 6,
            "ended_at": 2,
            **status,
        },
    )


def test_journal_reconnect_duplicate_and_restart_preserve_evidence(tmp_path):
    path = tmp_path / "operations.db"
    journal = OperationJournal(path)
    journal.ingest("chat1", "runtime1", event())
    journal.ingest("chat1", "runtime1", output())
    assert journal.ingest("chat1", "runtime1", output())[1] is False
    with pytest.raises(ValueError, match="Conflicting"):
        journal.ingest("chat1", "runtime1", output(text="changed"))
    journal.ingest("chat1", "runtime1", final())
    journal.ingest("chat1", "runtime1", event("pending"))
    completed = journal.read("chat1", "p1")
    journal.close()
    restored = OperationJournal(path)
    restored.recover()
    assert restored.read("chat1", "p1") == completed
    assert restored.status("chat1", "pending")["state"] == "outcome_unknown"
    assert restored.status("chat1", "pending")["controlAvailable"] is False
    assert restored.read("chat1", "p1", completed["nextCursor"])["chunks"] == []
    with pytest.raises(ValueError, match="conversation"):
        restored.read("chat2", "p1")
    restored.close()


def test_output_retention_and_source_delivery_gaps_are_distinct(tmp_path):
    journal = OperationJournal(tmp_path / "ops.db", max_output_bytes=6)
    journal.ingest("s", "r", event())
    journal.ingest("s", "r", output())
    journal.ingest("s", "r", output(sequence=3, cursor=1, text="world\n"))
    journal.ingest("s", "r", final(sequence=4, total_output_bytes=12))
    result = journal.read("s", "p1")
    assert result["cursorGap"] is True
    assert result["droppedOutputBytes"] == 6
    assert result["chunks"][0]["text"] == "world\n"
    assert result["captureComplete"] is True
    journal.ingest("s", "r", event("gap"))
    journal.ingest("s", "r", output("gap", sequence=3, cursor=1))
    journal.ingest("s", "r", final("gap", sequence=4, total_output_bytes=12))
    result = journal.read("s", "gap")
    assert result["captureComplete"] is False
    assert result["outputComplete"] is False
    assert result["cursorGap"] is True
    journal.close()


@pytest.mark.parametrize("flag", ["binary_output_withheld", "encoding_loss"])
def test_partial_original_output_never_becomes_complete_on_exit(tmp_path, flag):
    journal = OperationJournal(tmp_path / "ops.db")
    journal.ingest("s", "r", event())
    observation = output()
    observation["chunk"][flag] = True
    journal.ingest("s", "r", observation)
    journal.ingest("s", "r", final())
    result = journal.read("s", "p1")
    assert result["state"] == "completed"
    assert result["streamComplete"] is True
    assert result["captureComplete"] is False
    assert result["outputComplete"] is False
    journal.close()


def test_duplicate_events_cannot_change_bound_owner_or_producer(tmp_path):
    journal = OperationJournal(tmp_path / "ops.db")
    journal.ingest("s", "r", event())
    with pytest.raises(ValueError, match="owner or producer"):
        journal.ingest("s", "another-runtime", event())
    with pytest.raises(ValueError, match="owner or producer"):
        journal.ingest("s", "r", event(sequence=2, source="another-producer"))
    journal.close()


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path, workspace=tmp_path)
    await service.dispatch("session.create", {})
    yield service
    await service.close()


async def test_shared_actions_wait_for_change_preserve_navigation_and_draft(app):
    owner = app._session()["id"]
    await app.dispatch("session.create", {})
    selected = app._session()["id"]
    await app.dispatch("view.update", {"patch": {"draft": "unsent draft"}})
    await app.operations.observe(owner, owner, event())
    first = await app.app_bridge(
        "dispatch", {"action": "operations.read", "args": {"id": "p1"}}, owner
    )
    revision = first["result"]["revision"]
    waiter = asyncio.create_task(
        app.app_bridge(
            "dispatch",
            {
                "action": "operations.wait",
                "args": {"id": "p1", "afterRevision": revision, "waitMs": 5000},
            },
            owner,
        )
    )
    await asyncio.sleep(0.02)
    assert not waiter.done()
    await app.operations.observe(owner, owner, output())
    result = await asyncio.wait_for(waiter, 1)
    assert result["result"]["chunks"][0]["text"] == "hello\n"
    assert app.state["selectedSessionId"] == selected
    assert app.state["view"]["draft"] == "unsent draft"
    with pytest.raises(AppError, match="calling conversation"):
        await app.app_bridge(
            "dispatch",
            {"action": "operations.read", "args": {"sessionId": owner, "id": "p1"}},
            selected,
        )
    with pytest.raises(AppError, match="not found"):
        await app.app_bridge(
            "dispatch", {"action": "operations.read", "args": {"id": "p1"}}, selected
        )


async def test_restart_does_not_mount_replay_or_cancel_unknown_work(tmp_path):
    runtime = SimpleNamespace(control=AsyncMock(), start=AsyncMock(), close=AsyncMock())
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch("session.create", {})
    sid = app._session()["id"]
    await app.operations.observe(sid, sid, event())
    await app.operations.observe(sid, sid, output())
    await app.close()
    restored = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        result = await restored.dispatch(
            "operations.read", {"sessionId": sid, "id": "p1"}
        )
        assert result["result"]["state"] == "outcome_unknown"
        assert result["result"]["chunks"][0]["text"] == "hello\n"
        await restored.dispatch("operations.cancel", {"sessionId": sid, "id": "p1"})
        runtime.start.assert_not_awaited()
        runtime.control.assert_not_awaited()
    finally:
        await restored.close()


async def test_existing_worker_receipts_project_without_duplicate_registry(app):
    sid = app._session()["id"]
    await app.on_runtime_event(
        "worker.updated", {"sessionId": sid, "id": "child", "status": "running"}
    )
    first = await app.dispatch(
        "operations.status", {"sessionId": sid, "id": "worker:child"}
    )
    waiter = asyncio.create_task(
        app.dispatch(
            "operations.wait",
            {
                "sessionId": sid,
                "id": "worker:child",
                "afterRevision": first["result"]["revision"],
                "waitMs": 5000,
            },
        )
    )
    await asyncio.sleep(0)
    await app.on_runtime_event(
        "worker.updated",
        {"sessionId": sid, "id": "child", "status": "completed", "report": "done"},
    )
    result = await asyncio.wait_for(waiter, 1)
    assert result["result"]["evidence"]["report"] == "done"
    assert app.operations.journal.list(sid) == []


async def test_runtime_end_marks_only_unfinished_process_outcome_unknown(app):
    sid = app._session()["id"]
    await app.operations.observe(sid, sid, event())
    await app.operations.observe(sid, sid, output())
    await app.operations.observe(sid, sid, final())
    await app.operations.observe(sid, sid, event("unfinished"))
    await app.on_runtime_event("runtime.ended", {"sessionId": sid, "status": "stopped"})
    assert app.operations.status(sid, "p1")["state"] == "completed"
    assert app.operations.status(sid, "unfinished")["state"] == "outcome_unknown"


async def test_operation_cancel_uses_existing_hooks_and_rejects_redirect(
    app, monkeypatch
):
    from contextvars import ContextVar

    from amplifier_web.runtime_controls import RuntimeControls

    # A unit seam for the existing loop attribution ContextVar; no model needed.
    monkeypatch.setitem(
        sys.modules,
        "amplifier_module_loop_live.scope",
        SimpleNamespace(JOB_CALL=ContextVar("job_call", default=None)),
    )
    sid = app._session()["id"]
    await app.operations.observe(sid, sid, event())
    tool = SimpleNamespace(
        input_schema={"type": "object"},
        execute=AsyncMock(return_value={"success": True}),
    )
    hooks = SimpleNamespace(
        emit=AsyncMock(return_value=SimpleNamespace(action="continue"))
    )
    coordinator = SimpleNamespace(
        get=lambda name: {"bash": tool} if name == "tools" else None,
        get_capability=lambda name: None,
        hooks=hooks,
        process_hook_result=AsyncMock(side_effect=lambda result, *args: result),
    )
    controls = object.__new__(RuntimeControls)
    controls.coordinator = coordinator
    controls.session = SimpleNamespace(session_id=sid)
    controls.runtime = SimpleNamespace(generation="active")
    # Cancellation may run while the model is active and does not need the
    # general configuration lock, checkpoint or require_idle path.
    controls.lock = asyncio.Lock()

    async def control(_, operation, args):
        return await controls.perform(operation, args)

    app.runtime = SimpleNamespace(
        control=AsyncMock(side_effect=control), close=AsyncMock()
    )
    hooks.emit.return_value = SimpleNamespace(action="deny", reason="policy denied")
    with pytest.raises(AppError, match="policy denied"):
        await app.dispatch("operations.cancel", {"sessionId": sid, "id": "p1"})
    tool.execute.assert_not_awaited()
    hooks.emit.return_value = SimpleNamespace(
        action="modify",
        data={"tool_input": {"action": "terminate", "process_id": "foreign"}},
    )
    with pytest.raises(AppError, match="cannot redirect"):
        await app.dispatch("operations.cancel", {"sessionId": sid, "id": "p1"})
    tool.execute.assert_not_awaited()

    async def mutate(event, data):
        if event == "tool:pre":
            data["tool_input"]["process_id"] = "in-place-foreign"
        return SimpleNamespace(action="continue")

    hooks.emit.side_effect = mutate
    with pytest.raises(AppError, match="cannot redirect"):
        await app.dispatch("operations.cancel", {"sessionId": sid, "id": "p1"})
    tool.execute.assert_not_awaited()
    hooks.emit.side_effect = None
    hooks.emit.return_value = SimpleNamespace(action="continue")
    await app.dispatch(
        "operations.cancel", {"sessionId": sid, "id": "p1"}, origin="agent"
    )
    tool.execute.assert_awaited_once_with({"action": "terminate", "process_id": "p1"})
    pre = next(
        call.args[1]
        for call in reversed(hooks.emit.await_args_list)
        if call.args[0] == "tool:pre"
    )
    assert pre["actor"] == "agent"
    assert pre["operation_id"] == "p1"
    # Request success is not substituted for observed process completion.
    assert app.operations.status(sid, "p1")["state"] == "running"


async def test_real_process_output_is_durable_beyond_module_ring_and_cancel_observed(
    app,
):
    bash = pytest.importorskip("amplifier_module_tool_bash")
    tool = bash.BashTool(
        {
            "managed_processes": True,
            "managed_stdin": True,
            "safety_profile": "unrestricted",
            "managed_max_output_bytes": 4096,
        }
    )
    sid = app._session()["id"]
    tool._processes.observer = lambda: (
        lambda event: app.operations.observe(sid, sid, event)
    )
    try:
        command = f"{shlex.quote(sys.executable)} -u -c " + shlex.quote(
            "import sys; print('x'*8000); sys.stdin.readline(); print('last'); sys.stdin.readline()"
        )
        result = await tool.execute({"action": "start", "command": command})
        process_id = result.output["process_id"]
        await tool.execute(
            {"action": "wait", "process_id": process_id, "wait_ms": 3000}
        )
        await tool.execute(
            {"action": "write", "process_id": process_id, "stdin": "go\n"}
        )
        result = await tool.execute({"action": "terminate", "process_id": process_id})
        assert result.success
        durable = app.operations.journal.read(sid, process_id)
        assert durable["state"] == "cancelled"
        assert durable["returncode"] < 0
        assert durable["totalOutputBytes"] >= 8001
        assert len("".join(chunk["text"] for chunk in durable["chunks"])) >= 8001
        assert durable["droppedOutputBytes"] == 0
        assert result.output["dropped_output_bytes"] > 0
        assert durable["outputComplete"] is True
    finally:
        await tool.close()


async def test_owned_processes_fence_worker_parking_and_cancellation_releases_command_lock(
    monkeypatch,
):
    from amplifier_web.runtime_worker import Worker

    worker = Worker()
    worker.ownership = SimpleNamespace(yielding=False)
    worker.acquire_for_mutation = AsyncMock()
    worker.bind_activation = lambda: "token"
    worker.activation_gate = SimpleNamespace(reset=lambda token: None)
    worker.park = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def command(data):
        assert not worker.command_lock.locked()
        assert worker.operation_controls == 1
        entered.set()
        await release.wait()

    worker._command_serial = command
    task = asyncio.create_task(
        worker.command({"op": "control", "operation": "operations.cancel"})
    )
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done()
    # Approval response admission remains possible while cancellation waits.
    async with worker.command_lock:
        release.set()
    await asyncio.wait_for(task, 1)
    assert worker.operation_controls == 0
    worker.park.assert_awaited_once()


async def test_managed_process_observation_keeps_worker_owned(monkeypatch):
    from amplifier_web.runtime_worker import Worker

    worker = Worker()
    worker.ownership = SimpleNamespace(yielding=False)
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: None))
    worker.runtime = SimpleNamespace(
        queued_inputs=0, inbox=asyncio.Queue(), generation=None
    )
    worker.shared_handle = SimpleNamespace(release=AsyncMock())
    worker.operation_ids.add(("runtime", "live-command"))
    await worker.park()
    assert worker.parked is False
    worker.shared_handle.release.assert_not_awaited()


async def test_private_backup_contains_durable_operation_evidence(app, tmp_path):
    import sqlite3
    import tarfile

    from amplifier_web.recovery import _backup_files

    sid = app._session()["id"]
    await app.operations.observe(sid, sid, event())
    await app.operations.observe(sid, sid, output())
    result = await asyncio.to_thread(_backup_files, app.data_dir, [])
    with tarfile.open(result["backup"]) as archive:
        saved = archive.extractfile("operations.sqlite3").read()
    path = tmp_path / "restored-operations.sqlite3"
    path.write_bytes(saved)
    with sqlite3.connect(path) as db:
        assert (
            json.loads(db.execute("SELECT value FROM operation_output").fetchone()[0])[
                "text"
            ]
            == "hello\n"
        )


async def test_cancellation_never_reactivates_retired_runtime():
    from amplifier_web.runtime import RuntimeManager

    manager = RuntimeManager()
    manager._retired["retired"] = ({"id": "retired"}, AsyncMock())
    manager._start_locked = AsyncMock()
    try:
        with pytest.raises(RuntimeError, match="not running"):
            await manager.control("retired", "operations.cancel", {"processId": "gone"})
        manager._start_locked.assert_not_awaited()
    finally:
        await manager.close()


async def test_registered_receipt_adapter_keeps_original_store_authoritative(app):
    sid = app._session()["id"]
    original = {
        "id": "schedule:run1",
        "sessionId": sid,
        "state": "running",
        "revision": "1",
        "source": "schedule",
        "controlAvailable": False,
    }
    app.operations.register_source(
        "schedule", lambda owner: [original], lambda owner, identity: original
    )
    result = await app.dispatch(
        "operations.read", {"sessionId": sid, "id": original["id"]}
    )
    assert result["result"]["state"] == "running"
    assert app.operations.journal.list(sid) == []
    original.update(state="completed", revision="2")
    app.operations.notify()
    result = await app.dispatch(
        "operations.wait",
        {"sessionId": sid, "id": original["id"], "afterRevision": "1"},
    )
    assert result["result"]["state"] == "completed"
    original["sessionId"] = "another-owner"
    with pytest.raises(AppError, match="different owner"):
        await app.dispatch("operations.read", {"sessionId": sid, "id": original["id"]})
