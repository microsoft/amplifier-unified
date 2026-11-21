"""Offline provider for the real ACP -> Worker -> Core integration check.

Only model decisions are scripted. The title change traverses the actual
app_control bridge and command store. No native events or receipts are invented.
"""
import hashlib
import importlib
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
        usage = Usage(input_tokens=1, output_tokens=1, total_tokens=2)
        if self.pending:
            message = next(row for row in reversed(request.messages)
                           if row.role == "tool" and row.tool_call_id == self.pending)
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
    await coordinator.mount("providers", Provider(config or {}), name=Provider.name)