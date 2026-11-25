"""Provider-only script for actual Work -> Worker/Core -> ACP qualification.

The counter measures serialized test bytes, not vendor tokens. It implements the
real request_budget protocol so Work retains Boundary + token_meter: actual.
No loader, resolver, context, tool, hook, transport or native event is replaced.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import re


class Provider:
    name = "acp-work-fixture"

    def __init__(self, config=None, coordinator=None):
        config = config or {}
        self.audit = Path(config["audit"]) if config.get("audit") else None
        self.pending, self.sid, self.round = None, None, 0
        self.coordinator = coordinator
        self.counted = set()

    def record(self, **values):
        with self.audit.open("a") as stream:
            stream.write(json.dumps({"pid": os.getpid(), **values}) + "\n")

    async def list_models(self):
        return []

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    @staticmethod
    def text(message):
        return (message.content if isinstance(message.content, str) else
                "".join(getattr(block, "text", "") for block in message.content or []))

    def get_info(self):
        from amplifier_core import ProviderInfo
        return ProviderInfo(
            id=self.name, display_name="Offline actual Work ACP",
            capabilities=["tools", "request_budget:provider_count"],
            defaults={"model": "scripted"})

    async def request_budget(self, request, *, require_measurement=False, **kwargs):
        payload = request.model_dump_json().encode()
        digest = hashlib.sha256(payload).hexdigest()
        self.counted.add(digest)
        count = (len(payload) + 3) // 4
        self.record(kind="budget", sha256=digest, requireMeasurement=require_measurement,
                    bytes=len(payload), inputTokens=count,
                    measurementSource="synthetic-serialized-request")
        return {
            "input_limit_independent_of_output": True,
            "estimated_input_tokens": count, "input_limit_tokens": 2000000,
            "context_token_budget": 2000000,
            "measurement": {"kind": "provider_count",
                            "source": "synthetic-serialized-request", "input_tokens": count},
        }

    async def complete(self, request, **kwargs):
        from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
        from amplifier_web.execution_events import CALL_PURPOSE
        purpose = (request.metadata or {}).get("purpose")
        assert purpose != "context-compaction", "This finite lane must not buy a summary"
        if CALL_PURPOSE.get():
            self.record(kind="auxiliary", purpose=CALL_PURPOSE.get())
            return ChatResponse(content=[TextBlock(text="Offline Work ACP title")])
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        assert digest in self.counted, "Foreground request was not measured before dispatch"
        coordinator = self.coordinator
        if not self.pending:
            modules = {}
            for name in (
                "amplifier_web.runtime_worker", "amplifier_web.acp_server", "amplifier_core",
                "amplifier_foundation", "amplifier_module_loop_live.orchestrator",
                "amplifier_module_context_managed", "amplifier_module_tool_skills",
                "amplifier_module_tool_computer_use", "amplifier_module_hook_context_intelligence",
            ):
                path = Path(importlib.import_module(name).__file__).resolve()
                modules[name] = {"path": str(path),
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            tools = coordinator.get("tools")
            assert "computer" not in tools and "desktop" not in tools, sorted(tools)
            assert "computer_use_unavailable" in tools, sorted(tools)
            graph = coordinator.get_capability("web.media.construction")
            inspected = graph.graph.participants_evidence()
            construction = {
                "loads": [{key: value for key, value in row.items() if key != "mount"}
                          for row in inspected["loads"]],
                "ready": [{key: value for key, value in row.items() if key != "callback"}
                          for row in inspected["ready"]],
            }
            context = coordinator.get("context")
            from amplifier_web.attachments import MediaAttachmentContext
            assert isinstance(context, MediaAttachmentContext)
            boundary = context.context
            measured = coordinator.get_capability("context.measured_request_view")
            assert measured.__self__ is boundary
            skills = coordinator.get("tools", "load_skill")
            while hasattr(skills, "original"):
                skills = skills.original
            winner = skills.skills["acp-work-precedence"]
            blocks = re.findall(
                r'<system-reminder source="hooks-skills-visibility">.*?</system-reminder>',
                "\n".join(self.text(message) for message in request.messages), re.S)
            self.record(kind="work", contextType=type(boundary).__name__,
                        contextAdapter=type(context).__name__,
                        plan=coordinator.config, modules=modules, tools=sorted(tools),
                        construction=construction, skills={
                            "winner": str(winner.path.resolve()),
                            "config": skills.config, "names": sorted(skills.skills),
                            "directories": [str(p) for p in skills.skills_dirs],
                            "visibilityBlocks": len(blocks),
                            "visibilityApproxTokens": len(blocks[0]) // 4 if blocks else None,
                        })
        self.record(kind="dispatch", sha256=digest)
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
                return ChatResponse(
                    content=[TextBlock(text=f"Native reply {self.round}: 漢🙂")], usage=usage)
        else:
            self.round += 1
            self.pending = f"native-acp-state-{self.round}"
            arguments = {"operation": "get_state", "args": {}}
        return ChatResponse(content=[], tool_calls=[ToolCall(
            id=self.pending, name="app_control", arguments=arguments)], usage=usage)


async def mount(coordinator, config=None):
    await coordinator.mount("providers", Provider(config or {}, coordinator), name=Provider.name)