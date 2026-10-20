"""Actual Core/Foundation/loop/delegate ownership with deterministic providers."""
import asyncio
from dataclasses import dataclass, field
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_core import ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_module_tool_delegate import DelegateTool

from amplifier_web.host.children import install_children
from amplifier_web.host.execution_pause import ExecutionPause
from amplifier_web.host.storage import SessionStore


class Provider:
    name = "fixture"
    config = {}

    def __init__(self):
        self.requests, self.responses = asyncio.Queue(), asyncio.Queue()

    def get_info(self):
        return SimpleNamespace(default_model="fixture")

    async def complete(self, request, **kwargs):
        await self.requests.put(request)
        return await self.responses.get()

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    async def request(self):
        return await asyncio.wait_for(self.requests.get(), 5)

    async def reply(self, text="", calls=None):
        await self.responses.put(ChatResponse(content=[TextBlock(text=text)], tool_calls=calls))


class Effect:
    name = "effect"
    description = "A counted effect"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self):
        self.count = 0

    async def execute(self, arguments):
        self.count += 1
        return ToolResult(success=True, output="effect complete")


@dataclass
class Prepared(PreparedBundle):
    fixture: dict = field(default_factory=dict)

    async def create_session(self, **kwargs):
        session = await super().create_session(**kwargs)
        self.fixture["created"].append((session.session_id, kwargs.get("parent_id")))
        role = "child" if kwargs.get("parent_id") else "root"
        coordinator = session.coordinator
        await coordinator.mount("providers", self.fixture[role], name="fixture")
        await coordinator.mount("tools", self.fixture["effect"], name="effect")
        await coordinator.mount("tools", DelegateTool(coordinator, {
            "features": {"self_delegation": {"enabled": True}}}), name="delegate")
        return session


async def mount(path, fixture):
    plan = {"session": {"orchestrator": {"module": "loop-live", "config": {
        "min_delay_between_calls_ms": 0, "background_delegate": False}},
        "context": {"module": "context-simple"}},
        "agents": {"worker": {"instruction": "Do the assigned work."}}}
    paths = {name: Path(importlib.util.find_spec(
        "amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")}
    prepared = Prepared(plan, BundleModuleResolver(paths),
                        Bundle(name="fixture", session=plan["session"], agents=plan["agents"]),
                        fixture=fixture)
    session = await prepared.create_session(session_id="root")
    coordinator = session.coordinator
    runtime = Runtime("root")
    coordinator.register_capability("live.runtime", runtime)
    store = SessionStore(path / "history")
    registry = await install_children(session, prepared, runtime, store, None)
    from amplifier_module_loop_live.job_store import JobStore
    ledger = JobStore(store.directory("root") / "live-jobs")
    coordinator.register_capability("live.jobs", ledger)
    async def close_ledger():
        ledger.close()
    coordinator.register_cleanup(close_ledger)
    saved = store.load("root")
    if saved:
        await coordinator.get("context").set_messages(saved[0])
    async def checkpoint(status="running"):
        return store.save("root", await coordinator.get("context").get_messages(),
                          {"status": status, "parent_id": None})
    coordinator.register_capability("live.checkpoint", checkpoint)
    if saved is None:
        await checkpoint()
    async def authorize(receipt):
        fixture["claims"].append(receipt)
        return len(fixture["claims"]) == 1
    async def validate(receipt):
        return fixture.get("allowed", True)
    async def observe(receipt):
        fixture["receipts"].append(receipt)
        return True
    owner = ExecutionPause(path / "maintenance", "root", store,
                           authorize=authorize, validate=validate, observe=observe)
    await owner.install(coordinator)
    task = asyncio.create_task(session.execute(""))
    await runtime.wait_for(lambda event: event["type"] == "session.ready", 5)
    return session, runtime, task, owner, registry


async def close(session, runtime, task):
    if not task.done():
        await runtime.submit(Input("stop"))
        await asyncio.wait_for(task, 5)
    await session.cleanup()


@pytest.mark.asyncio
async def test_real_delegate_pause_release_remount_original_completion_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "shared"))
    fixture = {"root": Provider(), "child": Provider(), "effect": Effect(),
               "created": [], "claims": [], "receipts": []}
    session, runtime, task, owner, registry = await mount(tmp_path, fixture)
    child_id = None
    try:
        await runtime.submit(Input("user", "Original request", id="original-input"))
        await fixture["root"].request()
        generation = runtime.generation["id"]
        await fixture["root"].reply(calls=[ToolCall(id="delegate-call", name="delegate", arguments={
            "agent": "worker", "instruction": "Original child instruction"})])
        await fixture["child"].request()
        child_id = next(identity for identity, parent in fixture["created"] if parent == "root")
        await owner.request("epoch-one")
        await fixture["child"].reply(calls=[ToolCall(id="child-effect", name="effect", arguments={})])
        await runtime.wait_for(lambda event: event["type"] == "session.suspended", 5)
        assert fixture["effect"].count == 0
        assert registry.rows[child_id]["status"] == "paused"
        root_frame = owner.read("root")["frame"]
        assert root_frame["phase"] == "settling_tools"
        assert root_frame["settled_tools"][0]["continuation"]["sessionId"] == child_id
        await runtime.release_paused("epoch-one")
        await asyncio.wait_for(task, 5)
        assert runtime.events[-1]["status"] == "paused"
    finally:
        await close(session, runtime, task)

    session, runtime, task, owner, registry = await mount(tmp_path, fixture)
    try:
        await runtime.wait_for(lambda event: event["type"] == "session.suspended", 5)
        assert fixture["effect"].count == 0 and fixture["child"].requests.empty()
        await owner.resume("epoch-one")
        request = await fixture["child"].request()
        assert fixture["effect"].count == 1
        assert str(request.messages).count("Original child instruction") == 1
        await fixture["child"].reply("Child finished")
        request = await fixture["root"].request()
        assert "delegate-call" in str(request.messages) and "Child finished" in str(request.messages)
        await fixture["root"].reply("Root finished")
        result = await runtime.wait_for(lambda event: event["type"] == "generation.finished", 5)
        assert result["generation_id"] == generation
        assert result["input_ids"] == ["original-input"]
        assert fixture["created"].count((child_id, "root")) == 2
        assert len({identity for identity, parent in fixture["created"] if parent == "root"}) == 1
        assert fixture["effect"].count == 1 and len(fixture["claims"]) == 1
        assert owner.read(child_id)["state"] == "consumed"
        assert registry.rows[child_id]["status"] == "completed"
        with pytest.raises(ValueError, match="matching"):
            await owner.resume("epoch-one")
    finally:
        await close(session, runtime, task)


@pytest.mark.asyncio
async def test_delegate_finishing_during_pause_retains_pending_result_without_redispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "shared"))
    fixture = {"root": Provider(), "child": Provider(), "effect": Effect(),
               "created": [], "claims": [], "receipts": []}
    session, runtime, task, owner, registry = await mount(tmp_path, fixture)
    try:
        await runtime.submit(Input("user", "Delegate work", id="background-input"))
        await fixture["root"].request()
        generation = runtime.generation["id"]
        await fixture["root"].reply(calls=[ToolCall(id="background-call", name="delegate", arguments={
            "agent": "worker", "instruction": "One child effect", "async": True})])
        await fixture["child"].request()
        await fixture["root"].request()
        await fixture["child"].reply(calls=[ToolCall(id="effect-call", name="effect", arguments={})])
        await fixture["child"].request()
        assert fixture["effect"].count == 1
        await owner.request("result-epoch")
        await fixture["root"].reply("Delegate is working")
        await fixture["child"].reply("Child completed effect")
        await runtime.wait_for(lambda event: event["type"] == "session.suspended", 5)
        proof = owner.snapshot()
        assert proof["status"] == "suspended"
        assert proof["generationId"] is None
        assert proof["inputIds"] == ["background-input"] and proof["pendingInputIds"] == []
        assert proof["rootGenerationTerminal"] == {"generationId": generation, "inputIds": ["background-input"]}
        assert proof["jobOrigins"][0]["status"] == "returned"
        assert proof["pendingResults"] == [{"inputId": owner.read("root")["live"]["pending"][0]["id"],
            "jobId": proof["jobOrigins"][0]["jobId"], "callId": "background-call"}]
        await runtime.release_paused("result-epoch")
        await asyncio.wait_for(task, 5)
    finally:
        await close(session, runtime, task)
    session, runtime, task, owner, registry = await mount(tmp_path, fixture)
    try:
        await runtime.wait_for(lambda event: event["type"] == "session.suspended", 5)
        assert owner.snapshot()["pendingResults"] == proof["pendingResults"]
        await owner.resume("result-epoch")
        request = await fixture["root"].request()
        assert "Child completed effect" in str(request.messages)
        assert "background-call" in str(request.messages)
        assert fixture["effect"].count == 1 and fixture["child"].requests.empty()
        await fixture["root"].reply("Result received")
        await runtime.wait_for(lambda event: event["type"] == "generation.finished", 5)
        assert len([parent for _, parent in fixture["created"] if parent == "root"]) == 1
    finally:
        await close(session, runtime, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [False, True])
async def test_paused_background_delegate_resumes_original_job_and_child(tmp_path, monkeypatch, persistent):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "shared"))
    fixture = {"root": Provider(), "child": Provider(), "effect": Effect(),
               "created": [], "claims": [], "receipts": []}
    session, runtime, task, owner, registry = await mount(tmp_path, fixture)
    try:
        await runtime.submit(Input("user", "Delegate work", id="background-input"))
        await fixture["root"].request()
        generation = runtime.generation["id"]
        await fixture["root"].reply(calls=[ToolCall(id="background-call", name="delegate", arguments={
            "agent": "worker", "instruction": "One child effect", "async": True, "persistent": persistent})])
        await fixture["child"].request()
        await fixture["root"].request()
        child_id = next(identity for identity, parent in fixture["created"] if parent == "root")
        await owner.request("background-epoch")
        await fixture["root"].reply("Delegate is working")
        await fixture["child"].reply(calls=[ToolCall(id="effect-call", name="effect", arguments={})])
        await runtime.wait_for(lambda event: event["type"] == "session.suspended", 5)
        paused = owner.snapshot()
        assert paused["status"] == "suspended"
        assert paused["ownedGenerations"] == [{"generationId": generation, "inputIds": ["background-input"]}]
        assert paused["rootGenerationTerminal"] == paused["ownedGenerations"][0]
        assert paused["jobOrigins"][0]["generationId"] == generation
        assert paused["inputIds"] == ["background-input"]
        assert owner.read("root")["live"]["pending"] == []
        job = next(iter(session.coordinator.get("orchestrator").jobs.values()))
        job_id = job["job_id"] if "job_id" in job else next(iter(session.coordinator.get("orchestrator").jobs))
        assert job["status"] == "paused" and fixture["effect"].count == 0
        await runtime.release_paused("background-epoch")
        await asyncio.wait_for(task, 5)
    finally:
        await close(session, runtime, task)
    session, runtime, task, owner, registry = await mount(tmp_path, fixture)
    try:
        await runtime.wait_for(lambda event: event["type"] == "session.suspended", 5)
        await owner.resume("background-epoch")
        await fixture["child"].request()
        assert fixture["effect"].count == 1
        await fixture["child"].reply("Child report")
        if persistent:
            for _ in range(200):
                if registry.rows[child_id]["status"] == "idle":
                    break
                await asyncio.sleep(.005)
            assert registry.rows[child_id]["status"] == "idle"
            await registry.control(child_id, "finish")
        result = await runtime.wait_for(lambda event: event["type"] == "job.returned", 5)
        assert result["job_id"] == job_id and result["call_id"] == "background-call"
        assert fixture["created"].count((child_id, "root")) == 2
        assert fixture["effect"].count == 1
        assert len([event for event in runtime.events if event["type"] == "job.returned"]) == 1
    finally:
        await close(session, runtime, task)
