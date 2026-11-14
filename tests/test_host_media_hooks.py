"""Registration routing and separate control-producer boundary; no inference."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core import HookResult, ModuleCoordinator, ToolResult

from amplifier_web.host.media import (
    NAMESPACE, control_tool_post, registration_coordinator, registration_hooks,
)


def test_registration_view_is_explicit_and_keeps_execution_coordinator_identity():
    raw, selected = object(), object()
    calls = []
    view = SimpleNamespace(hooks=selected)
    bridge = SimpleNamespace(participant=lambda owner: calls.append(owner) or view)
    coordinator = SimpleNamespace(hooks=raw, get_capability=lambda key:
        bridge if key == "web.media.hook_bridge" else None)
    assert registration_coordinator(coordinator) is view
    assert registration_hooks(coordinator) is selected
    assert coordinator.hooks is raw
    assert calls == ["unified-host", "unified-host"]
    assert registration_hooks(SimpleNamespace(hooks=raw)) is raw


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["replace", "null", "delete", "inplace", "deny"])
async def test_control_post_uses_actual_processed_final_result_once(change):
    coordinator = ModuleCoordinator()
    calls = []
    async def post(event, data):
        calls.append(copy.deepcopy(data))
        assert "tool_result" not in data
        if change == "null":
            return HookResult(action="modify", data={"result": None})
        if change == "delete":
            return HookResult(action="modify", data={})
        if change == "inplace":
            data["result"] = "redacted"
            return HookResult()
        if change == "deny":
            return HookResult(action="deny", reason="fixture denial")
        return HookResult(action="modify", data={"result": "replacement"})
    coordinator.hooks.register("tool:post", post, name="control-policy")
    value = await control_tool_post(coordinator, {
        "tool_name": "fixture", "tool_call_id": "call", "result": {"private": "before"}})
    assert len(calls) == 1
    expected = {"replace": "replacement", "null": None, "delete": None,
                "inplace": "redacted", "deny": {
                    "success": False, "error": {"message": "fixture denial"}}}
    assert value == (expected[change] if change == "deny" else None)


@pytest.mark.asyncio
async def test_control_post_cannot_publish_unappended_media_or_borrow_loop_checkpoint():
    coordinator = ModuleCoordinator()
    cap = SimpleNamespace(publish=AsyncMock())
    coordinator.register_capability(NAMESPACE, cap)
    boundary = AsyncMock(return_value={"complete": True, "hook_result": HookResult(),
        "result": {"success": True, "output": {NAMESPACE: {"refs": ["staged"]}, "text": "result"}}})
    coordinator.register_capability("session.media.tool_post", boundary)
    checkpoint = AsyncMock(side_effect=AssertionError("No canonical append owner"))
    coordinator.register_capability("session.durable_checkpoint", checkpoint)
    value = await control_tool_post(coordinator, {
        "tool_name": "fixture", "tool_call_id": "actual-call", "result": {"before": True}})
    assert value["output"] == {"text": "result"}
    assert value["mediaDelivery"]["available"] is False
    cap.publish.assert_awaited_once_with([], origin={"kind": "tool",
        "session_id": coordinator.session_id, "id": "actual-call"}, disposition="drop")
    boundary.assert_awaited_once()
    checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_runtime_control_invoke_returns_settled_post_value_and_real_call_id():
    from amplifier_web.runtime_controls import RuntimeControls
    coordinator = ModuleCoordinator()
    calls = []
    class Tool:
        input_schema = {"type": "object"}
        async def execute(self, arguments):
            calls.append(arguments)
            return ToolResult(success=True, output={"secret": "original"})
    await coordinator.mount("tools", Tool(), name="fixture")
    post_calls = []
    async def post(event, data):
        post_calls.append(data)
        return HookResult(action="modify", data={"result": "final result"})
    coordinator.hooks.register("tool:post", post, name="control-policy")
    boundary = AsyncMock()
    async def settled(event, **kwargs):
        actual = await post("tool:post", event)
        actual = await coordinator.process_hook_result(actual, "tool:post", event["tool_name"])
        return {"hook_result": actual, "complete": True, "result": "final result"}
    boundary.side_effect = settled
    coordinator.register_capability("session.media.tool_post", boundary)
    controls = object.__new__(RuntimeControls)
    controls.coordinator = coordinator
    controls._tool_invocation = None
    controls.checkpoint = AsyncMock()
    result = await controls.invoke({"name": "fixture", "arguments": {}}, checkpoint=False)
    assert result["result"] == "final result"
    assert result["callId"] == post_calls[0]["tool_call_id"]
    assert post_calls[0]["tool_name"] == "fixture"
    assert "tool_result" not in post_calls[0]
    assert len(calls) == len(post_calls) == 1
    controls.checkpoint.assert_not_awaited()
    boundary.assert_awaited_once()
