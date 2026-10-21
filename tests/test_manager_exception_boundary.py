"""Offline real loop catch and worker publication; no vendor or child process.

The synthetic provider fails in parse_tool_calls, AFTER complete returned.
The pending child is an asyncio task owned by the real loop's shutdown path,
not a native delegated session. Process transport alone is replaced by a queue.
"""
import asyncio
import copy
import importlib
import json
from pathlib import Path

import pytest

from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_core.models import ProviderInfo
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_web import runtime_worker
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.service import AppService


PRIVATE = "PRIVATE_MANAGER_MESSAGE_AND_LOCAL"


async def test_real_post_provider_manager_exception_reaches_public_inspection(tmp_path, monkeypatch):
    # Required imports: an unavailable real boundary is a failure, never a skip.
    from amplifier_module_loop_live.runtime import Input, Runtime

    paths = {name: Path(importlib.import_module("amplifier_module_" + name.replace("-", "_")).__file__).parent
             for name in ("loop-live", "context-simple")}
    plan = {"session": {"orchestrator": {"module": "loop-live", "config": {"use_streaming": False}},
                        "context": {"module": "context-simple"}}}
    app = AppService(tmp_path / "app", workspace=tmp_path)
    session = task = None
    worker = runtime_worker.Worker()
    published, raw = [], []
    monkeypatch.setattr(runtime_worker, "publish", lambda row: published.append(copy.deepcopy(row)))
    try:
        await app.dispatch("session.create", {})
        root = app._session()
        prepared = PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name="exception-boundary", session=plan["session"]))
        session = await prepared.create_session(session_id=root["id"])
        worker.session = session
        if hasattr(worker, 'capture_manager_failures'):
            worker.capture_manager_failures()
        def observe(event):
            raw.append(copy.deepcopy(event))
            worker.observe(event)  # Production terminal publication, not a fake.
        worker.runtime = Runtime(root["id"], observer=observe)
        coordinator = session.coordinator
        coordinator.register_capability("live.runtime", worker.runtime)
        loop = coordinator.get("orchestrator")
        child_started, child_cancelled = asyncio.Event(), asyncio.Event()

        async def controlled_child():
            child_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                child_cancelled.set()

        def manager_admission_sentinel():
            private_local = PRIVATE
            raise ValueError(private_local)

        class Provider:
            name = "offline-boundary"
            calls = 0
            def get_info(self):
                return ProviderInfo(id=self.name, display_name="Offline fixture",
                                    capabilities=["tools"], defaults={"model": "fixture"})
            async def list_models(self):
                return []
            async def complete(self, request, **kwargs):
                self.calls += 1
                assert self.calls == 1, "Synthetic effect must not repeat"
                return ChatResponse(content=[], tool_calls=[ToolCall(id="fixture-call", name="unused", arguments={})],
                                    usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
            def parse_tool_calls(self, response):
                # Inject at the production post-complete parser boundary.
                # Do not replace execute(), turn_failure(), or the manager catch.
                child = asyncio.create_task(controlled_child())
                loop.jobs["controlled-child"] = {"task": child, "call_id": "controlled-call", "status": "pending"}
                manager_admission_sentinel()

        provider = Provider()
        worker.telemetry = ExecutionEvents(root["id"], runtime_worker.publish)
        await coordinator.mount("providers", worker.telemetry.instrument_provider(root["id"], provider), name=provider.name)
        task = worker.execution = asyncio.create_task(session.execute(""))
        await worker.runtime.wait_for(lambda row: row["type"] == "session.idle", timeout=10)
        await worker.runtime.submit(Input("user", "Synthetic boundary input", id="boundary-input"))
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(asyncio.shield(task), 10)
        await asyncio.wait_for(child_cancelled.wait(), 2)
        assert child_started.is_set()
        worker.executed(task)  # Production callback still has the real cause.
        terminal = next(row for row in raw if row["type"] == "generation.failed")
        exit_event = next(row for row in published if row["type"] == "runtime.error")
        assert terminal["error_type"] == "ValueError"
        assert terminal["error_category"] == "unknown" and terminal["error_stage"] == "manager_turn"
        assert terminal["input_ids"] == ["boundary-input"]
        # Record first loss without reconstructing anything from exception text.
        print("BOUNDARY", json.dumps({"rawLoopHasChain": bool(terminal.get("exception_chain")),
            "workerHasChain": bool(exit_event.get("terminalFailure", {}).get("exceptionChain")),
            "generation": terminal["generation_id"], "inputs": terminal["input_ids"]}))
        # Keep the production JSON-lines reader and its runtime.error branch.
        # Only OS pipes/process effects are replaced; all bytes originate above.
        stdout = asyncio.StreamReader()
        stdout.feed_data(b"".join((json.dumps(row) + "\n").encode() for row in published))
        stdout.feed_eof()
        class Process:
            returncode = 0
            async def wait(self):
                return 0
        process = Process()
        process.stdout = stdout
        ready = asyncio.get_running_loop().create_future()
        ready.set_result({})
        row = {"process": process, "ready": ready, "pending": {}, "closing": False,
               "runtime_id": root["id"], "inputId": "boundary-input", "emit": app.on_runtime_event}
        await RuntimeManager()._read(root["id"], row)
        receipt = await app.dispatch("session.inspect", {"id": root["id"]})
        failure = receipt["result"]["failure"]
        assert failure["errorType"] == "ValueError" and failure["category"] == "unknown"
        assert failure["stage"] == "manager_turn" and failure["retryable"] is False
        assert failure["generationId"] == terminal["generation_id"] and failure["inputIds"] == ["boundary-input"]
        chain = failure.get("exceptionChain", [])
        assert any(item["errorType"] == "ValueError" and
                   any(frame["function"] == "manager_admission_sentinel" for frame in item["frames"]) for item in chain)
        assert failure["effects"] == "not_rolled_back" and failure["replayed"] is False
        assert PRIVATE not in json.dumps([published, receipt["result"]])
        assert provider.calls == 1 and list(worker.runtime.accepted) == ["boundary-input"]
        assert len(app.state["sessions"]) == 1 and loop.jobs["controlled-child"]["task"].done()
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if session:
            await session.cleanup()
        await app.close()


def test_capture_rejects_payloads_and_bounds_frames():
    from amplifier_web.support_diagnostics import public_exception_chain, terminal_exception_capture
    frame = {"file": "fixture.py", "function": "sentinel", "line": 42,
             "message": PRIVATE, "locals": {"secret": PRIVATE}}
    value = [{"errorType": "ValueError", "frames": [frame] * 20, "body": PRIVATE}] * 8
    safe = public_exception_chain(value)
    assert len(safe) == 5 and all(len(item["frames"]) == 12 for item in safe)
    assert PRIVATE not in json.dumps(safe)
    for invalid in (True, -1, 0, 10_000_001, "42", None):
        assert public_exception_chain([{"errorType": "ValueError", "frames": [{**frame, "line": invalid}]}])[0]["frames"] == []
    assert public_exception_chain([{"errorType": PRIVATE + "\n", "frames": [frame]}]) == []
    assert public_exception_chain([{"errorType": "ValueError", "frames": [{**frame, "file": "/private/fixture.py"}]}])[0]["frames"] == []
    for bad in (None, [], {}, {"exceptionChain": value, "generationId": "g", "inputIds": [True], "errorType": "ValueError"}):
        assert terminal_exception_capture(bad) is None


@pytest.mark.parametrize("change", ["stop", "new-generation", "wrong-generation", "wrong-input",
                                   "wrong-type", "child", "old-failure", "already-captured"])
def test_supplement_cannot_replace_or_cross_failure_identity(change):
    from amplifier_web.session_health import generation_failure, terminal_exception_failure
    event = {"sessionId": "root", "rootSessionId": "root", "event": "generation.failed", "generation_id": "g",
             "input_ids": ["i"], "error_type": "ValueError", "error_stage": "manager_turn", "error_category": "unknown"}
    failure = {**generation_failure(event), "generationId": "g", "inputIds": ["i"], "recordedAt": 100}
    session = {"id": "root", "status": "error", "failure": failure, "generations": [event]}
    capture = {"generationId": "g", "inputIds": ["i"], "errorType": "ValueError",
               "exceptionChain": [{"errorType": "ValueError", "frames": []}]}
    enriched = terminal_exception_failure(session, capture)
    assert enriched == {**failure, "exceptionChain": capture["exceptionChain"]}
    assert session["failure"] is failure and "exceptionChain" not in failure
    if change == "stop":
        session["status"] = "stopped"
    elif change == "new-generation":
        session["generations"].append({**event, "event": "generation.started", "generation_id": "new"})
    elif change == "wrong-generation":
        capture["generationId"] = "other"
    elif change == "wrong-input":
        capture["inputIds"] = ["other"]
    elif change == "wrong-type":
        event["error_type"] = "TypeError"
    elif change == "child":
        event["sessionId"] = "child"
    elif change == "old-failure":
        failure["generationId"] = "old"
    else:
        failure["exceptionChain"] = [{"errorType": "ValueError", "frames": [{"file": "original.py"}]}]
    before = copy.deepcopy(session)
    assert terminal_exception_failure(session, capture) is None
    assert session == before


async def test_unsupported_or_stopped_capture_does_not_change_execution(monkeypatch):
    from types import SimpleNamespace
    from amplifier_web.runtime_worker import Worker
    worker = Worker()
    published = []
    monkeypatch.setattr(runtime_worker, "publish", published.append)
    class Immutable:
        __slots__ = ()
        async def execute(self):
            return "original"
    immutable = Immutable()
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: immutable))
    worker.capture_manager_failures()
    assert await immutable.execute() == "original" and not published
    from amplifier_module_loop_live.failures import ManagerTurnError
    class Loop:
        async def execute(self):
            worker.stop_revision += 1
            try:
                raise ValueError(PRIVATE)
            except ValueError as error:
                raise ManagerTurnError({"error_type": "ValueError", "error_category": "unknown",
                                        "error_stage": "manager_turn", "error_message": "Safe"}) from error
    loop = Loop()
    worker.runtime = SimpleNamespace(stop_requested=False)
    worker.failed_generation = {"generation_id": "g", "input_ids": ["i"], "error_type": "ValueError",
                                "error_category": "unknown", "error_stage": "manager_turn"}
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: loop))
    worker.capture_manager_failures()
    original = loop.execute
    worker.capture_manager_failures()
    assert loop.execute is original
    with pytest.raises(ManagerTurnError):
        await loop.execute()
    assert not published