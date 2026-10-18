"""Deterministic acceptance driver; real loop/worker/tools, no network or models."""

import json
from amplifier_core import ProviderInfo
from amplifier_core.message_models import (
    ChatResponse,
    TextBlock,
    ToolCallBlock,
    ToolCall,
)


class Provider:
    name = "stories-acceptance"

    def get_info(self):
        return ProviderInfo(
            id=self.name,
            display_name="Stories deterministic acceptance",
            defaults={"model": "synthetic"},
        )

    async def list_models(self):
        return []

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    async def complete(self, request, **kwargs):
        messages = [
            m.model_dump() if hasattr(m, "model_dump") else m for m in request.messages
        ]
        task_index = -1
        for i, m in enumerate(messages):
            raw = m.get("content", "")
            if isinstance(raw, list):
                raw = "".join(x.get("text", "") for x in raw if isinstance(x, dict))
            if (
                m.get("role") == "user"
                and isinstance(raw, str)
                and raw.startswith("STORIES_ACCEPTANCE ")
            ):
                task_index = i
                task = json.loads(raw.split(" ", 1)[1])
        if task_index < 0:
            return ChatResponse(
                content=[TextBlock(text="No acceptance task supplied.")]
            )
        outputs = []
        for m in messages[task_index + 1 :]:
            if m.get("role") == "tool":
                outputs.append(m)
        step = len(outputs)
        if step >= len(task["steps"]):
            return ChatResponse(
                content=[
                    TextBlock(text="STORIES_ACCEPTANCE_DONE " + json.dumps(outputs))
                ]
            )
        spec = task["steps"][step]
        args = spec["arguments"]
        call = ToolCall(
            id="stories-proof-" + str(step), name=spec["name"], arguments=args
        )
        return ChatResponse(
            content=[ToolCallBlock(id=call.id, name=call.name, input=args)],
            tool_calls=[call],
            finish_reason="tool_calls",
        )


async def mount(coordinator, config=None):
    await coordinator.mount("providers", Provider(), name="stories-acceptance")
