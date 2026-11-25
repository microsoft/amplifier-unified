"""Deterministic decisions for actual native profile lifecycle checks.

Only the provider is scripted. Effects use the real app_control bridge; the
barrier delays a response after its actual command receipt, never fabricates
native events, execution ownership, delivery, or failure observations.
"""
import asyncio
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "acp_native_base", Path(__file__).with_name("acp_native_provider.py"))
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)


def module_manifest():
    modules = {}
    for name in ("amplifier_core", "amplifier_core._engine", "amplifier_foundation",
                 "amplifier_module_loop_live.orchestrator", "amplifier_module_loop_streaming",
                 "amplifier_module_context_simple", "amplifier_web.runtime_worker",
                 "amplifier_web.acp_server", "amplifier_web.acp_participant",
                 "amplifier_web.execution_owner", "amplifier_web.agent_instance"):
        path = Path(importlib.import_module(name).__file__).resolve()
        modules[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    modules["fixture-provider"] = {
        "path": str(Path(__file__).resolve()),
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return modules


class Provider(_base.Provider):
    name = "acp-lifecycle-fixture"

    def __init__(self, config):
        super().__init__(config)
        self.scenario = config.get("scenario")
        self.turn = None

    async def complete(self, request, **kwargs):
        from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
        from amplifier_web.execution_events import CALL_PURPOSE
        usage = Usage(input_tokens=1, output_tokens=1, total_tokens=2)
        if CALL_PURPOSE.get() or not request.tools:
            return ChatResponse(content=[TextBlock(text="YES\nFinite fixture complete.")], usage=usage)
        if self.pending:
            message = next(row for row in reversed(request.messages)
                           if row.role == "tool" and row.tool_call_id == self.pending)
            receipt = json.loads(self.text(message))
            if "success" in receipt and "output" in receipt:
                assert receipt["success"], receipt
                receipt = receipt["output"]
            self.record(kind="receipt", commandId=self.pending, receipt=receipt)
            if self.pending.startswith("lifecycle-state-"):
                self.sid = receipt["session"]["id"]
                self.pending = f"lifecycle-title-{self.turn}"
                arguments = {"operation": "dispatch", "args": {
                    "action": "session.rename", "args": {
                        "id": self.sid, "title": "Lifecycle effect " + self.turn},
                    "id": self.pending}}
            elif self.pending == "lifecycle-title-first":
                assert receipt["accepted"], receipt
                self.pending = "lifecycle-work"
                arguments = {"operation": "dispatch", "args": {
                    "action": "task.create", "args": {
                        "sessionId": self.sid, "expectedRevision": 0,
                        "objective": "Verify the finite native lifecycle fixture",
                        "maxTurns": 10}, "id": self.pending}}
            else:
                assert receipt["accepted"], receipt
                self.pending = None
                self.record(kind="after-effect", turn=self.turn)
                if self.turn == "first" and self.scenario == "lost-response":
                    (self.audit.parent / "after-effect").touch()
                    async with asyncio.timeout(45):
                        while not (self.audit.parent / "release").exists():
                            await asyncio.sleep(.02)
                if self.turn == "first" and self.scenario == "failure":
                    self.record(kind="provider-failure", turn=self.turn)
                    raise RuntimeError("LIFECYCLE_FIXTURE_FAILURE_AFTER_REAL_EFFECT")
                return ChatResponse(content=[TextBlock(text="Lifecycle reply " + self.turn + ": 漢🙂")],
                                    usage=usage)
        else:
            # Read only the last actual source input. Resumed provider instances
            # do not infer a turn number from process-local counters.
            last = next(self.text(row) for row in reversed(request.messages)
                        if row.role == "user" and "Lifecycle input " in self.text(row))
            self.turn = last.rsplit("Lifecycle input ", 1)[1].strip()
            assert self.turn in {"first", "second"}, last
            self.record(kind="provider", turn=self.turn, modules=module_manifest(),
                        messages=[row.model_dump(mode="json") for row in request.messages
                                  if row.role == "user" and self.text(row).startswith(
                                      "Source-attributed public conversation input ")])
            self.pending = f"lifecycle-state-{self.turn}"
            arguments = {"operation": "get_state", "args": {}}
        return ChatResponse(content=[], tool_calls=[ToolCall(
            id=self.pending, name="app_control", arguments=arguments)], usage=usage)


class StreamingProvider(Provider):
    """Exercise the selected loop's actual provider.stream mapping.

    As with the mounted Anthropic provider, providers own
    llm:stream_block_delta; loop-live translates these hooks to native
    assistant.delta. No native runtime/lifecycle event is injected here.
    """

    async def stream(self, request, **kwargs):
        chunks = ["[[PA", "SS]]"] if self.scenario == "stream-pass" else ["Public 漢", "🙂 reply"]
        self.record(kind="provider-stream", chunks=chunks, modules=module_manifest())
        await self.coordinator.hooks.emit("llm:stream_block_delta", {
            "request_id": "fixture-stream", "block_index": 0,
            "block_type": "text", "sequence": 0, "text": chunks[0]})
        yield {"content": chunks[0], "block_type": "text"}
        (self.audit.parent / "stream-prefix").touch()
        async with asyncio.timeout(45):
            while not (self.audit.parent / "release").exists():
                await asyncio.sleep(.02)
        await self.coordinator.hooks.emit("llm:stream_block_delta", {
            "request_id": "fixture-stream", "block_index": 0,
            "block_type": "text", "sequence": 1, "text": chunks[1]})
        yield {"content": chunks[1], "block_type": "text"}


async def mount(coordinator, config=None):
    selected = config or {}
    provider = StreamingProvider if selected.get("scenario", "").startswith("stream-") else Provider
    instance = provider(selected)
    instance.coordinator = coordinator
    await coordinator.mount("providers", instance, name=Provider.name)