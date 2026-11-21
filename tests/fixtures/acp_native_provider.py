"""Offline provider for the real ACP -> Worker -> Core integration check.

Only model decisions are scripted. The title change traverses the actual
app_control bridge and command store. No native events or receipts are invented.
"""
import hashlib
import importlib
import asyncio
import json
import os
from pathlib import Path


class Provider:
    name = "acp-native-fixture"

    def __init__(self, config):
        self.audit = Path(config["audit"]) if config.get("audit") else None
        self.pending = None
        self.sid = None
        self.round = 0
        self.create_task = config.get("create_task", False)

    def get_info(self):
        from amplifier_core import ProviderInfo
        return ProviderInfo(id=self.name, display_name="Offline native ACP",
                            capabilities=["tools"], defaults={"model": "scripted"})

    async def list_models(self):
        return []

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    def record(self, **values):
        with self.audit.open("a") as stream:
            stream.write(json.dumps({"pid": os.getpid(), **values}) + "\n")

    @staticmethod
    def text(message):
        return (message.content if isinstance(message.content, str) else
                "".join(getattr(block, "text", "") for block in message.content or []))

    async def complete(self, request, **kwargs):
        from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
        from amplifier_web.execution_events import CALL_PURPOSE
        usage = Usage(input_tokens=1, output_tokens=1, total_tokens=2)
        if CALL_PURPOSE.get():
            return ChatResponse(content=[TextBlock(text="Offline native ACP fixture")], usage=usage)
        if not request.tools:
            # Native goal assessment is a distinct tool-less request. Script its
            # verdict without consuming the foreground tool/reply state machine.
            self.record(kind="utility")
            return ChatResponse(content=[TextBlock(text="YES\nThe finite fixture effects completed.")], usage=usage)
        if self.pending:
            message = next((row for row in reversed(request.messages)
                            if row.role == "tool" and row.tool_call_id == self.pending), None)
            if message is None:
                self.record(kind="missing-tool", pending=self.pending,
                            messages=[row.model_dump(mode="json") for row in request.messages[-4:]])
            assert message is not None, [(row.role, row.tool_call_id, self.text(row)[:1000])
                                         for row in request.messages[-4:]]
            receipt = json.loads(self.text(message))
            if "success" in receipt and "output" in receipt:
                assert receipt["success"], receipt
                receipt = receipt["output"]
            self.record(kind="receipt", commandId=self.pending, receipt=receipt)
            if self.pending.startswith("native-acp-state-"):
                self.sid = receipt["session"]["id"]
                self.pending = f"native-acp-title-{self.round}"
                arguments = {"operation": "dispatch", "args": {
                    "action": "session.rename", "args": {
                        "id": self.sid, "title": f"Native ACP effect {self.round}"},
                    "id": self.pending}}
            elif self.create_task and self.round == 1 and self.pending.startswith("native-acp-title-"):
                assert receipt["accepted"], receipt
                self.pending = "native-acp-work"
                arguments = {"operation": "dispatch", "args": {
                    "action": "task.create", "args": {
                        "sessionId": self.sid, "expectedRevision": 0,
                        "objective": "Verify the explicitly accepted native ACP fixture",
                        "maxTurns": 10}, "id": self.pending}}
            else:
                assert receipt["accepted"], receipt
                self.pending = None
                return ChatResponse(content=[TextBlock(text=f"Native reply {self.round}: 漢🙂")],
                                    usage=usage)
        else:
            self.round += 1
            modules = {}
            for name in ("amplifier_web.runtime_worker", "amplifier_web.acp_server",
                         "amplifier_core", "amplifier_module_loop_live.orchestrator",
                         "amplifier_module_context_simple"):
                path = Path(importlib.import_module(name).__file__).resolve()
                modules[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            self.record(kind="provider", round=self.round, modules=modules)
            self.pending = f"native-acp-state-{self.round}"
            arguments = {"operation": "get_state", "args": {}}
        return ChatResponse(content=[], tool_calls=[ToolCall(
            id=self.pending, name="app_control", arguments=arguments)], usage=usage)


async def mount(coordinator, config=None):
    selected = config or {}
    provider = SharedProvider(selected) if selected.get("shared") else Provider(selected)
    await coordinator.mount("providers", provider, name=Provider.name)


class SharedProvider(Provider):
    """Deterministic room response while the real native owner keeps context."""
    async def complete(self, request, **kwargs):
        from amplifier_core.message_models import ChatResponse, TextBlock, Usage
        marker = "Participate in this shared conversation as "
        text = next((self.text(row) for row in reversed(request.messages)
                     if marker in self.text(row)), "")
        start = text.rfind(marker)
        if start < 0:
            header = "Source-attributed public conversation input (not a human authority grant):\n"
            text = next((self.text(row) for row in reversed(request.messages)
                         if header in self.text(row)), "")
            assert text, "Source carrier absent on advertised singleton profile"
            public, body = text.split(header, 1)[1].split("\n\n", 1)
            public = json.loads(public)
            context = {"conversation_id": public["source"]["conversationId"],
                "trigger_id": public["input"]["id"],
                "messages": [{"id": public["input"]["id"], "sender": public["input"]["actor"], "text": body}],
                "participants": []}
        else:
            context = json.loads(text[start:].split("\n", 1)[1])
        trigger = next(row for row in context["messages"] if row["id"] == context["trigger_id"])
        agent = next((row.get("is_agent") for row in context["participants"]
                      if row["id"] == trigger["sender"]), None)
        self.record(kind="room-input", context=context)
        if agent is not True:
            marker_path = self.audit.parent / ("entered-" + context["conversation_id"])
            marker_path.touch()
            async with asyncio.timeout(45):
                while not (self.audit.parent / "release").exists():
                    await asyncio.sleep(.02)
        output = "[[PASS]]" if agent is True else "U public reply: " + trigger["text"]
        return ChatResponse(content=[TextBlock(text=output)],
                            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))