"""Real ownership mechanism, with controlled native worker operations."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from amplifier_foundation.session import (
    CannotRelease,
    SessionBusyError,
    SharedSessionStore,
    register_release_handler,
    request_release,
)
from amplifier_foundation.session.activation import ActivationGate
from amplifier_module_loop_live.runtime import Runtime
from amplifier_native_runtime import WorkerReleaseParticipant


@asynccontextmanager
async def native_worker(tmp_path, monkeypatch, *, failure=None, blocked=False):
    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "locks"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SharedSessionStore(workspace, "participant")
    held = store.acquire(app="fixture-native")
    gate = ActivationGate()
    activation = gate.activate()
    events, steps = [], []
    drained = asyncio.Event()
    if not blocked:
        drained.set()
    stop_received = asyncio.Event()
    yielded = asyncio.Event()
    messages = [{"role": "user", "content": "retained before release"}]
    saved = tmp_path / "checkpoint.json"
    runtime = Runtime(session_id="participant")
    runtime.capture_activation = gate.current

    async def execute():
        kind, command = await runtime.inbox.get()
        assert kind == "input" and command.kind == "stop"
        assert command.target == "cancel"
        steps.append("stop")
        stop_received.set()
        await drained.wait()
        runtime.closed = True
        steps.append("execution-settled")

    async def checkpoint(status):
        assert status == "cancelled"
        held.check()
        gate.check_current()
        steps.append("checkpoint")
        if failure == "checkpoint":
            raise OSError("fixture full disk")
        saved.write_text(json.dumps(messages))

    async def close_controls():
        held.check()
        steps.append("controls")
        if failure == "controls":
            raise OSError("fixture controls failure")

    async def cleanup():
        held.check()
        steps.append("cleanup")
        if failure == "cleanup":
            raise OSError("fixture cleanup failure")
        messages.clear()  # A final save after disposing context would lose history.

    worker = SimpleNamespace(
        command_lock=asyncio.Lock(), shared_handle=held,
        activation_gate=gate, activation=activation, runtime=runtime,
        execution=asyncio.create_task(execute()), naming=None,
        session=SimpleNamespace(cleanup=cleanup, coordinator=SimpleNamespace(
            get_capability=lambda key: checkpoint if key == "live.checkpoint" else None)),
        controls=SimpleNamespace(close=close_controls), parked=False,
    )

    def publish(event):
        events.append(event)
        if event.get("status") == "yielded":
            assert not held.active
            yielded.set()

    owner = WorkerReleaseParticipant(worker, publish)

    async def prepare(request):
        outcome = await owner.prepare(request)
        assert held.active  # The callback must never release Foundation's handle.
        steps.append("callback-return")
        return outcome

    owner.registration = await register_release_handler(held, prepare_release=prepare)
    fixture = SimpleNamespace(
        worker=worker, owner=owner, store=store, held=held, events=events,
        steps=steps, drained=drained, stop_received=stop_received, yielded=yielded,
        saved=saved, messages=messages,
    )
    try:
        yield fixture
    finally:
        drained.set()
        if not worker.execution.done():
            worker.execution.cancel()
        await asyncio.gather(worker.execution, return_exceptions=True)
        if worker.naming and worker.naming.pending:
            worker.naming.pending.cancel()
            await asyncio.gather(worker.naming.pending, return_exceptions=True)
        await owner.registration.close()
        if held.active:
            held.release()


async def release(fixture):
    return await request_release(
        fixture.store, expected_owner=fixture.held.owner,
        request_id="takeover", requester_app="fixture-successor",
    )


@pytest.mark.skipif(os.name == "nt", reason="Foundation's current handoff transport is POSIX")
async def test_release_saves_before_cleanup_and_foundation_unlocks_last(tmp_path, monkeypatch):
    async with native_worker(tmp_path, monkeypatch) as f:
        previous = f.worker.activation
        result = await release(f)
        assert result.status == "released"
        await asyncio.wait_for(f.yielded.wait(), 2)
        assert f.steps == ["stop", "execution-settled", "checkpoint", "controls", "cleanup", "callback-return"]
        assert json.loads(f.saved.read_text()) == [{"role": "user", "content": "retained before release"}]
        assert f.messages == []
        assert f.worker.parked and f.worker.shared_handle is None
        assert f.worker.session is f.worker.runtime is f.worker.controls is None
        with pytest.raises(RuntimeError, match="released or superseded"):
            f.worker.activation_gate.check(previous)
        successor = f.store.acquire(app="fixture-successor")
        successor.release()
        assert [event["status"] for event in f.events] == ["yielding", "yielded"]


@pytest.mark.skipif(os.name == "nt", reason="Foundation's current handoff transport is POSIX")
@pytest.mark.parametrize("failure", ["checkpoint", "controls", "cleanup"])
async def test_failed_persistence_or_cleanup_keeps_original_writer(tmp_path, monkeypatch, failure):
    async with native_worker(tmp_path, monkeypatch, failure=failure) as f:
        result = await release(f)
        assert result.status == "cannot_release"
        assert f.held.active and f.owner.yielding and not f.worker.parked
        f.worker.activation_gate.check(f.worker.activation)
        with pytest.raises(SessionBusyError):
            f.store.acquire(app="must-not-steal")
        assert f.messages == [{"role": "user", "content": "retained before release"}]
        assert f.events[-1] == {"type": "runtime.ownership", "status": "yield-failed",
            "source": "fixture-successor", "detail": "Unable to finish safe shutdown (OSError)."}
        assert not f.yielded.is_set()


@pytest.mark.skipif(os.name == "nt", reason="Foundation's current handoff transport is POSIX")
async def test_old_acquisition_cannot_stop_or_save_current_worker(tmp_path, monkeypatch):
    async with native_worker(tmp_path, monkeypatch) as f:
        request = SimpleNamespace(acquisition_id="stale", requester_app="old-client",
            report_progress=lambda value: pytest.fail("stale request made progress"))
        result = await f.owner.prepare(request)
        assert isinstance(result, CannotRelease) and result.code == "owner_changed"
        assert f.held.active and not f.owner.yielding
        assert f.steps == [] and f.events == [] and not f.saved.exists()


@pytest.mark.skipif(os.name == "nt", reason="Foundation's current handoff transport is POSIX")
async def test_naming_is_cancelled_and_joined_before_checkpoint(tmp_path, monkeypatch):
    async with native_worker(tmp_path, monkeypatch) as f:
        started = asyncio.Event()

        async def naming():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                f.steps.append("naming-settled")

        f.worker.naming = SimpleNamespace(pending=asyncio.create_task(naming()))
        await started.wait()
        assert (await release(f)).status == "released"
        await asyncio.wait_for(f.yielded.wait(), 2)
        assert f.worker.naming.pending.cancelled()
        assert f.steps.index("execution-settled") < f.steps.index("naming-settled") < f.steps.index("checkpoint")


@pytest.mark.skipif(os.name == "nt", reason="Foundation's current handoff transport is POSIX")
async def test_cancelled_requester_does_not_cancel_admitted_drain(tmp_path, monkeypatch):
    async with native_worker(tmp_path, monkeypatch, blocked=True) as f:
        requesting = asyncio.create_task(release(f))
        await asyncio.wait_for(f.stop_received.wait(), 2)
        requesting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await requesting
        assert f.held.active and not f.worker.execution.done()
        assert not f.saved.exists()
        f.drained.set()
        await asyncio.wait_for(f.yielded.wait(), 2)
        assert not f.worker.execution.cancelled()
        assert f.saved.exists() and not f.held.active


def test_import_does_not_load_runtime_or_an_application():
    import amplifier_native_runtime
    target = str(Path(amplifier_native_runtime.__file__).resolve().parent.parent)
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from amplifier_native_runtime import WorkerReleaseParticipant
assert WorkerReleaseParticipant.__module__ == 'amplifier_native_runtime.release'
for name in ('amplifier_foundation', 'amplifier_core', 'amplifier_module_loop_live', 'amplifier_web', 'amplifier_acp'):
    assert name not in sys.modules, name
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", code, target], check=True)
