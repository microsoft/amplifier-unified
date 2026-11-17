"""Offline actual-Core construction; only the provider endpoint is synthetic."""
from __future__ import annotations

import base64
from pathlib import Path

from amplifier_core.message_models import ChatResponse, TextBlock

from amplifier_web.host.media import NAMESPACE


class RecordingProvider:
    name = "offline-media"
    config = {}
    default_model = "offline"
    support = {"v": 1, "positions": ["user", "tool"]}

    def __init__(self):
        self.capability = None
        self.counted = None
        self.observations = []
        self.fail_count = False
        self.fail_send = False

    def get_media_request_support(self, **kwargs):
        return self.support

    def get_info(self):
        from amplifier_core import ProviderInfo
        return ProviderInfo(id=self.name, display_name=self.name, capabilities=[],
                            defaults={"model": self.default_model})

    def parse_tool_calls(self, response):
        return []

    def observe(self, phase, request):
        images = [(index, message.role, message.tool_call_id, base64.b64decode(block.source["data"]))
                  for index, message in enumerate(request.messages)
                  for block in message.content if isinstance(message.content, list)
                  if getattr(block, "type", None) == "image"]
        self.observations.append((phase, id(request), images))

    async def request_budget(self, request, *, request_options=None, **kwargs):
        self.capability.validate(request, purpose="count", request_options={**(request_options or {}), **kwargs})
        self.counted = request
        self.observe("count", request)
        if self.fail_count:
            raise ValueError("offline count failed")
        return {"input_tokens": 7}

    async def complete(self, request, **kwargs):
        self.capability.validate(request, purpose="transport", request_options=kwargs)
        if request is not self.counted:
            raise ValueError("Count/send request object differs")
        self.observe("send", request)
        if self.fail_send:
            raise ValueError("offline send failed")
        return ChatResponse(content=[TextBlock(text="offline completion")])

    async def stream(self, request, **kwargs):
        self.capability.validate(request, purpose="transport", request_options=kwargs)
        if request is not self.counted:
            raise ValueError("Streaming count/send request object differs")
        self.observe("stream", request)
        yield "offline stream"


class FixtureLoader:
    """Public Core mount API, real live/context/filesystem implementations."""
    def __init__(self, provider, post=None):
        self.provider, self.post = provider, post
        self.queue = []

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        import amplifier_module_loop_live.orchestrator as live
        import amplifier_module_context_simple as context
        import amplifier_module_tool_filesystem as filesystem

        async def mount(current):
            if module_id == "loop-live":
                await live.mount(current, config)
                # The narrow loop owner supplies this same readiness report.
                # It is not source selection or a construction receipt.
                current.get_capability(NAMESPACE).report("loop-live", event="consumer-ready",
                    outcome="ready", details={"role": "loop", "contract": 1})
            elif module_id == "context-simple":
                await context.mount(current, config)
            elif module_id == "tool-filesystem":
                await filesystem.mount(current, config)
            elif module_id == "provider-openai-chatgpt":
                await current.mount("providers", self.provider, name="offline")
        return mount

    def enqueue_on_session_ready(self, module_id, callback):
        self.queue.append((module_id, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


def source_files():
    import amplifier_module_loop_live.orchestrator as live
    import amplifier_module_context_simple as context
    import amplifier_module_tool_filesystem as filesystem
    from amplifier_web.host import media
    from amplifier_web import app_guidance
    return [Path(__file__).resolve(), Path(live.__file__), Path(context.__file__),
            Path(filesystem.__file__), Path(media.__file__), Path(app_guidance.__file__)]


class MediaOnlyDelivery:
    async def prepare(self, request, provider):
        return request

    def commit(self, request):
        pass