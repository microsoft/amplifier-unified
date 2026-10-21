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
from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppService


PRIVATE = "PRIVATE_MANAGER_MESSAGE_AND_LOCAL"


async def test_real_post_provider_manager_exception_reaches_public_inspection(tmp_path, monkeypatch):
    # Required imports: an unavailable real boundary is a failure, never a skip.
    from amplifier_module_loop_live.runtime import Input, Runtime
    from amplifier_module_loop_live.failures import ManagerTurnError

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
        with pytest.raises(ManagerTurnError) as caught:
            await asyncio.wait_for(asyncio.shield(task), 10)
        assert isinstance(caught.value.__cause__, ValueError)
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
        for event in published:
            normalized = normalize_event(event, root["id"], "boundary-input")
            if normalized:
                await app.on_runtime_event(*normalized)
            elif event["type"] == "runtime.error":
                await app.on_runtime_event("runtime.error", {"sessionId": root["id"], **event})
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