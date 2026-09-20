"""Real owned subprocess cells plus host action/approval and restart boundaries."""

import asyncio
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_kernels import Kernels
from amplifier_operations import OperationJournal
from amplifier_web.service import AppError, AppService


@pytest.fixture
async def computation(tmp_path):
    module = pytest.importorskip("amplifier_module_tool_bash")
    tool = module.BashTool(
        {
            "managed_processes": True,
            "managed_stdin": True,
            "safety_profile": "unrestricted",
            "working_dir": str(tmp_path),
            "managed_max_output_bytes": 100000,
        }
    )
    journal = OperationJournal(tmp_path / "ops.sqlite3")

    async def transport(action, args):
        result = await tool.execute({"action": action, **args})
        if not result.success:
            raise ValueError(result.error)
        return result.output

    async def observe(event):
        journal.ingest("s", "r", event)

    paths = {"python": sys.executable}
    if shutil.which("node"):
        paths["node"] = shutil.which("node")
    (tmp_path / "local_value.py").write_text("value = 40\n")
    (tmp_path / "local_value.cjs").write_text("exports.value = 40;\n")
    kernels = Kernels(transport, observe, paths)
    yield kernels, journal, tool
    await kernels.shutdown()
    await tool.close()
    journal.close()


async def done(journal, identity):
    async with asyncio.timeout(15):
        while True:
            record = journal.read("s", identity)
            if record["state"] not in {"running", "cancel_requested", "queued"}:
                return record
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("language", ["python", "node"])
async def test_real_state_cells_error_reset_and_runtime_identity(computation, language):
    kernels, journal, _ = computation
    if language not in kernels.runtimes:
        pytest.skip("Node is unavailable")
    kernel = await kernels.create(language)
    identity, generation = kernel["id"], kernel["generation"]
    assert Path(kernel["runtime"]["executable"]).is_absolute()
    assert kernel["runtime"]["version"]
    first = await kernels.execute(
        identity,
        generation,
        "from local_value import value; counter = value"
        if language == "python"
        else "var counter = require('./local_value.cjs').value",
    )
    assert (await done(journal, first["id"]))["state"] == "completed"
    second = await kernels.execute(
        identity,
        generation,
        "print('saved'); counter + 2"
        if language == "python"
        else "console.log('saved'); counter + 2",
    )
    result = await done(journal, second["id"])
    assert result["evidence"]["result"] == "42"
    assert "saved" in "".join(chunk["text"] for chunk in result["chunks"])
    assert result["outputComplete"] is True
    failed = await kernels.execute(
        identity,
        generation,
        "1 / 0" if language == "python" else "throw Error('cell failed')",
    )
    assert (await done(journal, failed["id"]))["state"] == "failed"
    reset = await kernels.reset(identity, generation)
    assert reset["generation"] == generation + 1
    assert reset["processId"] != kernel["processId"]
    with pytest.raises(ValueError, match="generation"):
        await kernels.execute(identity, generation, "counter")
    missing = await kernels.execute(identity, reset["generation"], "counter")
    assert (await done(journal, missing["id"]))["state"] == "failed"
    await kernels.close(identity, reset["generation"])
    assert journal.status("s", identity)["state"] == "completed"


@pytest.mark.parametrize("language", ["python", "node"])
async def test_interrupt_serialization_and_explicit_fresh_reset(computation, language):
    kernels, journal, _ = computation
    if language not in kernels.runtimes:
        pytest.skip("Node is unavailable")
    kernel = await kernels.create(language)
    code = (
        "import time; print('working', flush=True); time.sleep(30)"
        if language == "python"
        else "console.log('working'); while (true) {}"
    )
    cell = await kernels.execute(kernel["id"], kernel["generation"], code)
    with pytest.raises(ValueError, match="busy"):
        await kernels.execute(kernel["id"], kernel["generation"], "1+1")
    async with asyncio.timeout(5):
        while "working" not in "".join(
            row["text"] for row in journal.read("s", cell["id"])["chunks"]
        ):
            await asyncio.sleep(0.01)
    stopped = await kernels.interrupt(kernel["id"], kernel["generation"])
    assert isinstance(stopped["returncode"], int)
    assert (await done(journal, cell["id"]))["state"] == "cancelled"
    assert stopped["state"] == "unavailable"
    refreshed = await kernels.reset(kernel["id"], kernel["generation"])
    assert refreshed["state"] == "idle"


async def test_unknown_termination_never_claims_clean_reset(computation):
    kernels, _, _ = computation
    kernel = await kernels.create("python")
    original = kernels.transport

    async def uncertain(action, args):
        if action == "terminate":
            return {"state": "outcome_unknown", "returncode": None}
        return await original(action, args)

    kernels.transport = uncertain
    with pytest.raises(ValueError, match="termination is unknown"):
        await kernels.reset(kernel["id"], kernel["generation"])
    assert kernels.get(kernel["id"])["generation"] == kernel["generation"]
    assert kernels.get(kernel["id"])["processId"] == kernel["processId"]
    kernels.transport = original


async def test_output_budget_and_protocol_damage_remain_honest(computation):
    kernels, journal, _ = computation
    kernels.max_output_bytes = 100
    kernel = await kernels.create("python")
    cell = await kernels.execute(kernel["id"], kernel["generation"], "print('x'*200)")
    result = await done(journal, cell["id"])
    assert result["state"] == "completed"
    assert result["outputComplete"] is False
    cell = await kernels.execute(
        kernel["id"], kernel["generation"], "import os; os.write(1,b'not protocol\\n')"
    )
    assert (await done(journal, cell["id"]))["state"] == "outcome_unknown"
    assert kernels.get(kernel["id"])["state"] == "unavailable"


async def test_restricted_transport_cannot_create_code_interpreter(tmp_path):
    calls = []

    async def transport(action, args):
        calls.append(action)
        return {"stdin_allowed": False}

    kernels = Kernels(transport, AsyncMock(), {"python": sys.executable})
    with pytest.raises(ValueError, match="unrestricted"):
        await kernels.create("python")
    assert calls == ["list"]


async def test_restart_keeps_cells_but_cannot_replay_or_resume_variables(
    tmp_path, computation
):
    kernels, journal, _ = computation
    kernel = await kernels.create("python")
    cell = await kernels.execute(
        kernel["id"], kernel["generation"], "saved_value = 42; saved_value"
    )
    assert (await done(journal, cell["id"]))["evidence"]["result"] == "42"
    journal.recover()
    assert journal.status("s", kernel["id"])["state"] == "outcome_unknown"
    assert journal.status("s", cell["id"])["state"] == "completed"
    foreign = Kernels(AsyncMock(), AsyncMock(), {"python": sys.executable})
    with pytest.raises(ValueError, match="not owned"):
        await foreign.execute(kernel["id"], kernel["generation"], "saved_value")


@pytest.fixture
async def host(tmp_path):
    pytest.importorskip("amplifier_module_tool_bash")
    from fixtures.computation_runtime import Runtime

    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch("session.create", {})
    app.runtime = Runtime(app, tmp_path)
    yield app
    await app.close()


async def host_done(app, sid, identity):
    value = (await app.dispatch("operations.read", {"sessionId": sid, "id": identity}))[
        "result"
    ]
    async with asyncio.timeout(15):
        while value["state"] in {"running", "queued", "cancel_requested"}:
            value = (
                await app.dispatch(
                    "operations.wait",
                    {
                        "sessionId": sid,
                        "id": identity,
                        "afterRevision": value["revision"],
                        "waitMs": 1000,
                    },
                )
            )["result"]
    return value


async def test_host_agent_and_ui_share_code_approval_state_and_journal(host):
    sid = host._session()["id"]
    await host.dispatch("view.update", {"patch": {"draft": "Keep unsent text"}})
    created = await host.app_bridge(
        "dispatch", {"action": "kernels.create", "args": {"language": "python"}}, sid
    )
    kernel = created["result"]
    code = "value = 41; print('preserved'); value + 1"
    result = await host.app_bridge(
        "dispatch",
        {
            "action": "kernels.execute",
            "args": {
                "kernelId": kernel["id"],
                "generation": kernel["generation"],
                "code": code,
            },
        },
        sid,
    )
    record = await host_done(host, sid, result["result"]["id"])
    assert record["evidence"]["result"] == "42"
    assert record["metadata"]["code"] == code
    pre = [
        row
        for row in host.runtime.calls
        if row["tool_name"] == "compute" and row["tool_input"]["action"] == "execute"
    ][-1]
    assert pre["tool_input"]["code"] == code
    assert pre["tool_input"]["generation"] == kernel["generation"]
    assert pre["actor"] == "agent"
    assert not [
        row for row in host.operations.journal.list(sid) if row["kind"] == "process"
    ]
    assert host.state["selectedSessionId"] == sid
    assert host.state["view"]["draft"] == "Keep unsent text"
    with pytest.raises(AppError, match="calling conversation"):
        await host.app_bridge(
            "dispatch",
            {
                "action": "kernels.status",
                "args": {"sessionId": "foreign", "kernelId": kernel["id"]},
            },
            sid,
        )


async def test_exact_cell_and_generation_cannot_be_rewritten_after_approval(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]

    async def rewrite(data):
        if data["tool_name"] == "compute":
            data["tool_input"]["code"] = "unexpected_side_effect = True"
        return SimpleNamespace(action="continue")

    host.runtime.hook = rewrite
    with pytest.raises(AppError, match="cannot redirect"):
        await host.dispatch(
            "kernels.execute",
            {
                "sessionId": sid,
                "kernelId": kernel["id"],
                "generation": kernel["generation"],
                "code": "1 + 1",
            },
        )
    assert not [
        row for row in host.operations.journal.list(sid) if row["kind"] == "kernel-cell"
    ]
    host.runtime.hook = None


async def test_required_question_blocks_only_exact_dependent_cell(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]
    answered = False

    def guard(session_id, question_id):
        assert session_id == sid and question_id == "q1"
        if not answered:
            raise AppError("Question unanswered", 409, code="question_unanswered")
        return {"value": "yes"}

    host.questions.answer_for_dependency = guard
    args = {
        "sessionId": sid,
        "kernelId": kernel["id"],
        "generation": kernel["generation"],
        "code": "1 + 1",
    }
    with pytest.raises(AppError, match="unanswered"):
        await host.dispatch("kernels.execute", {**args, "questionIds": ["q1"]})
    independent = (await host.dispatch("kernels.execute", args))["result"]
    assert (await host_done(host, sid, independent["id"]))["state"] == "completed"
    answered = True
    dependent = (
        await host.dispatch("kernels.execute", {**args, "questionIds": ["q1"]})
    )["result"]
    assert (await host_done(host, sid, dependent["id"]))["state"] == "completed"


async def test_saved_kernel_restart_reads_are_passive_and_never_control_unknown(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]
    host.operations.journal.recover()
    count = len(host.runtime.calls)
    value = (
        await host.dispatch(
            "kernels.status", {"sessionId": sid, "kernelId": kernel["id"]}
        )
    )["result"]
    assert value["state"] == "unavailable"
    with pytest.raises(AppError, match="original kernel is unavailable"):
        await host.dispatch(
            "kernels.reset",
            {
                "sessionId": sid,
                "kernelId": kernel["id"],
                "generation": kernel["generation"],
            },
        )
    assert len(host.runtime.calls) == count


async def test_completed_cell_cancel_cannot_interrupt_a_new_cell(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]
    args = {
        "sessionId": sid,
        "kernelId": kernel["id"],
        "generation": kernel["generation"],
    }
    first = (await host.dispatch("kernels.execute", {**args, "code": "1+1"}))["result"]
    await host_done(host, sid, first["id"])
    second = (
        await host.dispatch(
            "kernels.execute", {**args, "code": "import time; time.sleep(30)"}
        )
    )["result"]
    await host.dispatch("operations.cancel", {"sessionId": sid, "id": first["id"]})
    assert host.operations.status(sid, second["id"])["state"] == "running"
    with pytest.raises(AppError, match="no longer active"):
        await host.dispatch("kernels.interrupt", {**args, "cellId": first["id"]})
    await host.dispatch("operations.cancel", {"sessionId": sid, "id": second["id"]})
    assert (await host_done(host, sid, second["id"]))["state"] == "cancelled"


async def test_question_dependency_rechecked_after_code_approval(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]
    answered = True

    def guard(*_):
        if not answered:
            raise AppError("Question was superseded", 409)
        return {"value": "yes"}

    host.questions.answer_for_dependency = guard

    async def approval(data):
        nonlocal answered
        if data["tool_name"] == "compute":
            answered = False
        return SimpleNamespace(action="continue")

    host.runtime.hook = approval
    with pytest.raises(AppError, match="superseded"):
        await host.dispatch(
            "kernels.execute",
            {
                "sessionId": sid,
                "kernelId": kernel["id"],
                "generation": kernel["generation"],
                "code": "1+1",
                "questionIds": ["q1"],
            },
        )
    assert not [
        row for row in host.operations.journal.list(sid) if row["kind"] == "kernel-cell"
    ]
    assert host.runtime.controls.kernels.get(kernel["id"])["state"] == "idle"
    host.runtime.hook = None


async def test_reader_cancellation_cannot_lose_a_started_final_receipt(computation):
    kernels, journal, _ = computation
    kernel = await kernels.create("python")
    original = kernels.observe
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused(event):
        if event["kind"] == "kernel-cell" and event["phase"] == "finished":
            entered.set()
            await release.wait()
        await original(event)

    kernels.observe = paused
    cell = await kernels.execute(kernel["id"], kernel["generation"], "42")
    await asyncio.wait_for(entered.wait(), 5)
    reader = kernels.get(kernel["id"])["reader"]
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    release.set()
    result = await done(journal, cell["id"])
    assert result["state"] == "completed"
    assert result["evidence"]["result"] == "42"


async def test_start_policy_denial_releases_capacity_without_orphan_receipts(tmp_path):
    journal = OperationJournal(tmp_path / "ops.sqlite3")

    async def transport(action, args):
        if action == "list":
            return {"stdin_allowed": True}
        raise ValueError("Start denied")

    async def observed(event):
        journal.ingest("s", "r", event)

    kernels = Kernels(transport, observed, {"python": sys.executable}, max_kernels=1)
    for _ in range(2):
        with pytest.raises(ValueError, match="Start denied"):
            await kernels.create("python")
    assert kernels.records == {}
    assert all(row["state"] == "failed" for row in journal.list("s"))
    journal.close()


async def test_only_explicit_create_prepares_owner_never_passive_reads_or_old_cells(host):
    ensure = AsyncMock()
    host.management = SimpleNamespace(ensure_runtime=ensure, setup_manager=None, provider_catalog=SimpleNamespace(close=AsyncMock()))
    sid = host._session()['id']
    kernel = (await host.dispatch('kernels.create', {'sessionId': sid, 'language': 'python'}))['result']
    ensure.assert_awaited_once()
    assert ensure.await_args.args[0]['id'] == sid
    await host.dispatch('kernels.list', {'sessionId': sid})
    await host.dispatch('kernels.status', {'sessionId': sid, 'kernelId': kernel['id']})
    cell = (await host.dispatch('kernels.execute', {'sessionId': sid, 'kernelId': kernel['id'],
        'generation': kernel['generation'], 'code': '42'}))['result']
    await host_done(host, sid, cell['id'])
    ensure.assert_awaited_once()
    host.operations.journal.recover()
    with pytest.raises(AppError, match='original kernel is unavailable'):
        await host.dispatch('kernels.execute', {'sessionId': sid, 'kernelId': kernel['id'],
            'generation': kernel['generation'], 'code': '42'})
    ensure.assert_awaited_once()
