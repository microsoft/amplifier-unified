"""Real owned subprocess cells against the public transport and journal peers."""

import asyncio
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from amplifier_kernels import Kernels
from amplifier_operations import OperationJournal


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
