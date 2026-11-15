"""Registration routing and separate control-producer boundary; no inference."""
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core import HookResult, ModuleCoordinator, ToolResult

from amplifier_web.host.media import (
    NAMESPACE, control_tool_post, registration_coordinator, registration_hooks,
)

DISPATCH_POLICY = {}


async def global_policy_dispatch(event, data):
    return await DISPATCH_POLICY["post"](event, data)


def policy_with_action(action):
    async def post(event, data):
        return HookResult(action=action)
    return post

def policy_with_flags(deny, audit):
    async def post(event, data):
        return HookResult(action="deny" if deny else "continue", reason="audit" if audit else None)
    return post

def policy_with_config(config):
    async def post(event, data):
        return HookResult(action="deny" if config["deny"] is True else "continue")
    return post

async def policy_positional_and_keyword(event, data, deny=True, *, audit=False):
    return HookResult(action="deny" if deny else "continue", reason="audit" if audit else None)

def test_external_constructor_bindings_are_exact_and_released_with_their_construction():
    from amplifier_web.host.media_graph import NamedMediaGraph
    class Owner:
        delegate = None
        async def __call__(self, event, data):
            return HookResult()
    first = Owner()
    values = {"selected.owner": first}
    coordinator = SimpleNamespace(get_capability=values.get)
    selected = NamedMediaGraph("receiver", plans=[{}],
        sources={__file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        dependencies=(HookResult,), owner_rules=(("external", Owner, ("delegate",)),),
        capability_owners=(("external", "selected.owner", ()),))
    assert selected.bind_capability_owners(coordinator)
    assert selected.accepts_callable(first)
    values["selected.owner"] = Owner()
    assert not selected.accepts_callable(first)
    assert not selected.bind_capability_owners(coordinator)
    selected.release_construction(coordinator)
    assert not selected.accepts_callable(first)
    assert not selected._owners and not selected._capability_bindings

def test_selected_bound_receiver_snapshots_preserve_recursive_types():
    from amplifier_web.host.media_graph import NamedMediaGraph
    class Policy:
        def __init__(self):
            self.config = {"deny": True, "order": ["first"]}
        async def post(self, event, data):
            return HookResult(action="deny" if self.config["deny"] is True
                              and type(self.config["order"]) is list else "continue")
    owner = Policy()
    selected = NamedMediaGraph("bound-types", plans=[{}],
        sources={__file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        bindings=(owner,), dependencies=(HookResult, list))
    assert selected.accepts_callable(owner.post)
    owner.config["deny"] = 1
    assert not selected.accepts_callable(owner.post)
    owner.config["deny"] = True
    assert selected.accepts_callable(owner.post)
    owner.config["order"] = ("first",)
    assert not selected.accepts_callable(owner.post)

@pytest.mark.parametrize("alternatives", [False, True])
def test_environments_are_recursive_type_exact_and_defaults_keep_parameter_kinds(alternatives):
    from amplifier_web.host.media_graph import NamedMediaGraph
    config = {"config": {"deny": True}}
    selected = NamedMediaGraph("typed-config", plans=[{}],
        sources={__file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        dependencies=(HookResult,), environments={
            "policy_with_config.<locals>.post": [config] if alternatives else config,
            "policy_positional_and_keyword": {"deny": True, "audit": False}})
    assert selected.accepts_callable(policy_with_config({"deny": True}))
    assert not selected.accepts_callable(policy_with_config({"deny": 1}))
    assert selected.accepts_callable(policy_positional_and_keyword)

def test_named_graph_requires_selected_global_targets_and_capture_values():
    from amplifier_web.host.media_graph import NamedMediaGraph
    sources = {__file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    selected = NamedMediaGraph("specific-policy", plans=[{}], sources=sources,
                              dependencies=(HookResult,), environments={
                                  "policy_with_action.<locals>.post": {"action": "deny"}})
    assert selected.accepts_callable(policy_with_action("deny"))
    assert not selected.accepts_callable(policy_with_action("continue"))
    assert not selected.accepts_callable(global_policy_dispatch)
    declared = selected.plans[0]
    declared["hooks"] = [{"module": "unselected"}]
    assert selected.accepts_plan({})
    assert not selected.accepts_plan(declared)
    flags = NamedMediaGraph("specific-flags", plans=[{}], sources=sources,
        dependencies=(HookResult,), environments={
            "policy_with_flags.<locals>.post": {"deny": True, "audit": False}})
    assert flags.accepts_callable(policy_with_flags(True, False))
    assert not flags.accepts_callable(policy_with_flags(False, True))

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
async def test_failed_store_binding_releases_original_construction_on_session_cleanup():
    from amplifier_core import AmplifierSession
    from amplifier_web.host import media
    from amplifier_web.host.media_graph import NamedMediaGraph
    class Store:
        def media(self, *args, **kwargs):
            raise OSError("fixture opening failure")
    plan = {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}}
    selection = NamedMediaGraph("failed-opening", plans=[plan],
        sources={__file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        owner_rules=(("coordinator", ModuleCoordinator, ("session_id",)),))
    session = AmplifierSession(plan, session_id="failed-open")
    with pytest.raises(OSError, match="opening failure"):
        media.bind_media(session, Store(), selection=selection)
    assert id(session.coordinator) in media._CONSTRUCTIONS
    await session.cleanup()
    assert id(session.coordinator) not in media._CONSTRUCTIONS
    assert not selection._owners


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
    # The execute/post owner settles its captured origin in finally, not here.
    cap.publish.assert_not_awaited()
    boundary.assert_awaited_once()
    checkpoint.assert_not_awaited()


class NamedFixtureLoader:
    """Selected source fixture exercising real Core mount/ready phases."""
    def __init__(self, provider, context, tool=None, sentinel=""):
        self.provider, self.context = provider, context
        self.tool, self.sentinel = tool or object(), sentinel
        self.queue = []

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            if module_id == "fixture-loop":
                await current.mount("orchestrator", object())
                current.get_capability(NAMESPACE).report(
                    "fixture-loop", event="consumer-ready", outcome="ready",
                    details={"role": "loop", "contract": 1})
            elif module_id == "fixture-context":
                await current.mount("context", self.context)
                await current.mount("tools", self.tool, name="fixture")
            elif module_id == "fixture-provider":
                await current.mount("providers", self.provider, name="fixture")
            elif module_id == "fixture-hooks":
                current.hooks.register("tool:post", self.post, name="module-post", priority=20)
        if module_id == "fixture-hooks":
            mount.__on_session_ready__ = (module_id, self.ready)
        return mount

    async def post(self, event, data):
        if self.sentinel and self.sentinel in str(data):
            raise RuntimeError("Private pixels reached the first post observer")
        return HookResult()

    async def ready(self, current):
        current.hooks.register("tool:post", self.post, name="ready-post", priority=40)

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("redact", [None, "null", "string", "inplace", "deny", "unknown", "control",
                                   "transition", "replacement", "eviction"])
async def test_named_actual_core_construction_final_result_ack_private_pixels(tmp_path, redact):
    """No graph Boolean: actual source/callback join, mount/ready/late and ACK."""
    import base64
    import io
    import inspect
    from PIL import Image
    from amplifier_core import AmplifierSession
    from amplifier_core.message_models import ChatRequest, Message
    from amplifier_web.host import media as host_media
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.media_graph import NamedMediaGraph
    from amplifier_web.surface_delivery import SurfaceProvider
    bridge_module = pytest.importorskip("amplifier_foundation.session.media_hook_bridge")
    if not hasattr(bridge_module.MediaHookBridge, "begin_participating_graph"):
        pytest.skip("Requires the selected positive Foundation bridge")
    raw = io.BytesIO()
    Image.new("RGB", (2, 2), (5, 37, 83)).save(raw, format="PNG")
    pixels = raw.getvalue()
    seen = []
    class Provider:
        default_model = "fixture"
        def get_media_request_support(self, **kwargs):
            return {"v": 1, "positions": ["tool"]}
        async def request_budget(self, request, *, request_options=None, **kwargs):
            cap.validate(request, purpose="count", request_options=request_options or {})
            seen.append(request)
            return {"input_tokens": 7}
        async def complete(self, request, **kwargs):
            cap.validate(request, purpose="transport", request_options=kwargs)
            assert request is seen[-1]
            assert base64.b64decode(request.messages[0].content[-1].source["data"]) == pixels
            return request
    class Context:
        async def add_message(self, message):
            pass
    plan = {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
            "providers": [{"module": "fixture-provider"}],
            "hooks": [{"module": "fixture-hooks"}]}
    actual_calls = []
    class RawTool:
        input_schema = {"type": "object"}
        async def execute(self, arguments):
            from amplifier_core.message_models import ImageBlock
            return ToolResult(success=True, content=[ImageBlock(source={
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(pixels).decode()})])
    fixture_loader = NamedFixtureLoader(Provider(), Context(), RawTool(),
                                       base64.b64encode(pixels).decode())
    selected = NamedMediaGraph("actual-core-media-fixture", plans=[plan], sources={
        __file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        bindings=(fixture_loader,), observations=(actual_calls,), dependencies=(HookResult,),
        environments={
            "NamedFixtureLoader.load.<locals>.mount": [
                {"module_id": name} for name in (
                    "fixture-loop", "fixture-context", "fixture-provider", "fixture-hooks")],
            # Each actual load occurrence needs its complete environment.
            "test_named_actual_core_construction_final_result_ack_private_pixels.<locals>.late": {
                "redact": redact}})
    session = AmplifierSession(plan, session_id="named-root")
    session.coordinator.loader = fixture_loader
    store = SessionStore(tmp_path / "sessions")
    host_media.bind_media(session, store, selection=selected)
    coordinator = session.coordinator
    cap = coordinator.get_capability(NAMESPACE)
    await session.initialize()
    async def late(event, data):
        actual_calls.append(data["tool_call_id"])
        if redact == "null":
            return HookResult(action="modify", data={"result": None})
        if redact == "string":
            return HookResult(action="modify", data={"result": "redacted"})
        if redact == "inplace":
            data["result"]["output"].pop(NAMESPACE)
        return HookResult(action="deny" if redact == "deny" else "continue")
    host_media.registration_hooks(coordinator).register("tool:post", late, priority=900, name="late-host")
    coordinator.register_capability("session.durable_checkpoint", lambda: None)
    assert cap.enabled is False
    assert host_media.seal_hook_graph(coordinator) is True
    assert cap.enabled is True
    if redact in {"transition", "replacement", "eviction"}:
        from amplifier_web.host.media import hook_configuration, _HOOK_BRIDGE
        bridge = coordinator.get_capability(_HOOK_BRIDGE)
        original_receipt = bridge.graph_receipt
        from amplifier_web.surface_delivery import SurfaceProvider
        wrapped = SurfaceProvider(coordinator.get("providers")["fixture"],
                                  host_media.MediaOnlyDelivery(), coordinator=coordinator)
        from amplifier_core.message_models import ChatRequest, Message
        counted = ChatRequest(messages=[Message(role="user", content="text")])
        await wrapped.request_budget(counted)
        if redact == "replacement":
            await wrapped.request_budget(counted, request_options={"temperature": 0.1})
            assert wrapped.release_media_request(counted, request_options={"temperature": 0.1})
        elif redact == "eviction":
            # Provider lacking all support methods retains text-only requests.
            wrapped.original.get_media_request_support = None
            async def text_budget(request, **kwargs):
                return {"input_tokens": 5}
            wrapped.original.request_budget = text_budget
            requests = [counted] + [ChatRequest(messages=[Message(role="user", content=str(i))])
                                    for i in range(4)]
            for value in requests[1:]:
                await wrapped.request_budget(value)
            assert not wrapped.release_media_request(counted)
            for value in requests[1:]:
                assert wrapped.release_media_request(value)
        else:
            with pytest.raises(ValueError, match="idle"):
                with hook_configuration(coordinator):
                    pass
            assert wrapped.release_media_request(counted, request_options={})
        with hook_configuration(coordinator):
            host_media.registration_hooks(coordinator).register(
                "tool:post", late, priority=1000, name="mode-added")
        assert bridge.graph_receipt is not original_receipt
        assert bridge.validate_receipt(original_receipt) is False
        assert cap.enabled is True
        await session.cleanup()
        return
    if redact == "control":
        from amplifier_web.runtime_controls import RuntimeControls
        controls = object.__new__(RuntimeControls)
        controls.coordinator, controls._tool_invocation = coordinator, None
        controls.checkpoint = AsyncMock()
        final = await controls.invoke({"name": "fixture", "arguments": {}}, checkpoint=False)
        assert len(actual_calls) == 1
        assert final["result"]["mediaDelivery"]["available"] is False
        assert base64.b64encode(pixels).decode() not in str(final)
        await session.cleanup()
        return
    if redact == "unknown":
        async def unowned(event, data):
            return HookResult()
        coordinator.hooks.register("tool:post", unowned, name="raw-unowned")
        host_media.enable_media(coordinator)
        assert cap.enabled is False
        assert host_media.seal_hook_graph(coordinator) is False
        await session.cleanup()
        return
    origin = {"kind": "tool", "session_id": session.session_id, "id": "call-1"}
    await coordinator.hooks.emit("tool:pre", {"tool_name": "fixture", "tool_call_id": "call-1"})
    ingested = await cap.ingest(pixels, source_format="image-bytes", origin=origin, policy=None)
    ref = ingested["value"]
    staged = {"success": True, "output": {NAMESPACE: {"refs": [ref]}}}
    settled = await coordinator.get_capability("session.media.tool_post")({
        "tool_name": "fixture", "tool_call_id": "call-1", "result": staged})
    assert actual_calls == ["call-1"]
    assert settled["complete"] is True
    refs = (settled["result"].get("output", {}).get(NAMESPACE, {}).get("refs", [])
            if isinstance(settled["result"], dict) else [])
    await cap.publish(refs, origin=origin, disposition="retain" if refs else "drop")
    if redact is None:
        assert refs == [ref]
        coordinator.get_capability("web.media.store").classify_origin(ref, classification="tool")
        rows = [{"role": "tool", "name": "fixture", "tool_call_id": "call-1",
                 "content": "Image", "metadata": {NAMESPACE: {"refs": refs}}}]
        canonical = store.save(session.session_id, rows, {})
        host_media.acknowledge_media(coordinator, store, session.session_id, canonical)
        request = ChatRequest(messages=[Message.model_validate(row) for row in rows])
        adapter = SurfaceProvider(coordinator.get("providers")["fixture"],
                                  host_media.MediaOnlyDelivery(), coordinator=coordinator)
        await adapter.request_budget(request)
        result = await adapter.complete(request)
        assert result.messages == []  # Terminal release clears the private copy.
        assert base64.b64encode(pixels).decode() not in str(canonical)
        # Same actual owner on the next input, not a fabricated resumed graph.
        assert host_media.seal_hook_graph(coordinator) is True
    else:
        assert refs == []
        assert settled["result"] != staged
    await session.cleanup()


@pytest.mark.asyncio
async def test_raw_control_typed_image_is_neutral_before_first_post_observer():
    import base64
    from amplifier_core.message_models import ImageBlock
    from amplifier_web.runtime_controls import RuntimeControls
    coordinator = ModuleCoordinator()
    sentinel = base64.b64encode(b"typed-private-control-pixels").decode()
    observed = []
    async def first(event, data):
        assert sentinel not in str(data)
        observed.append(copy.deepcopy(data))
        return HookResult()
    coordinator.hooks.register("tool:post", first, priority=-10000, name="first-observer")
    class RawTool:
        input_schema = {"type": "object"}
        async def execute(self, arguments):
            return ToolResult(success=True, content=[ImageBlock(source={
                "type": "base64", "media_type": "image/png", "data": sentinel})])
    await coordinator.mount("tools", RawTool(), name="fixture")
    controls = object.__new__(RuntimeControls)
    controls.coordinator = coordinator
    controls._tool_invocation = None
    controls.checkpoint = AsyncMock()
    value = await controls.invoke({"name": "fixture", "arguments": {}}, checkpoint=False)
    assert observed and sentinel not in str(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tool", "post", "cancel", "mutate-id", "drop"])
async def test_control_settles_actual_origin_once_on_all_terminal_paths(failure):
    import asyncio
    from amplifier_web.runtime_controls import RuntimeControls
    coordinator = ModuleCoordinator()
    cap = SimpleNamespace(publish=AsyncMock())
    coordinator.register_capability(NAMESPACE, cap)
    calls = []
    class Tool:
        input_schema = {"type": "object"}
        async def execute(self, arguments):
            from amplifier_module_loop_live.scope import JOB_CALL
            calls.append(JOB_CALL.get())
            if failure == "tool":
                raise RuntimeError("primary tool failure")
            return ToolResult(success=True, output="done")
    await coordinator.mount("tools", Tool(), name="fixture")
    async def boundary(event, **kwargs):
        if failure == "post":
            raise RuntimeError("primary post failure")
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "mutate-id":
            event.pop("tool_call_id")
        return {"complete": True, "hook_result": HookResult(), "result": "final"}
    coordinator.register_capability("session.media.tool_post", boundary)
    controls = object.__new__(RuntimeControls)
    controls.coordinator, controls._tool_invocation = coordinator, None
    controls.checkpoint = AsyncMock()
    if failure in {"tool", "post", "cancel"}:
        cap.publish.side_effect = ValueError("secondary drop failure")
        expected = asyncio.CancelledError if failure == "cancel" else RuntimeError
        with pytest.raises(expected) as error:
            await controls.invoke({"name": "fixture", "arguments": {}}, checkpoint=False)
        assert any("origin settlement failed" in note for note in error.value.__notes__)
    elif failure == "drop":
        cap.publish.side_effect = ValueError("drop failure")
        with pytest.raises(ValueError, match="drop failure"):
            await controls.invoke({"name": "fixture", "arguments": {}}, checkpoint=False)
    else:
        await controls.invoke({"name": "fixture", "arguments": {}}, checkpoint=False)
    cap.publish.assert_awaited_once_with([], origin={"kind": "tool",
        "session_id": coordinator.session_id, "id": calls[0]}, disposition="drop")


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
