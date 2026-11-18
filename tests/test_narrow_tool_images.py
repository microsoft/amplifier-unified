"""Narrow #2175 source/checkpoint/request fixtures; no network or inference."""
import asyncio
import base64
import copy
import hashlib
import io
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("amplifier_foundation.session.media_host")
from amplifier_core import AmplifierSession, HookResult
from amplifier_core.message_models import ChatRequest, Message
from PIL import Image

from amplifier_web.host import media
from amplifier_web.host.storage import SessionStore
from amplifier_web.surface_delivery import SurfaceProvider
from fixtures.narrow_tool_image_fixture import FixtureLoader, MediaOnlyDelivery, RecordingProvider


def picture(tmp_path, format="PNG"):
    # The nonce is only in decoded pixels: not filename, metadata, tool text or
    # provider prompt. JPEG checks the exact encoded original (no resize).
    image = Image.frombytes("RGB", (8, 8), bytes((i * 37 + 19) % 256 for i in range(192)))
    output = io.BytesIO()
    image.save(output, format=format)
    path = tmp_path / "image"
    path.write_bytes(output.getvalue())
    return path, output.getvalue()


async def root(tmp_path, *, store=None, post=None, provider=None,
               consumer_readiness={"role": "loop", "contract": 1, "ready": True},
               expected_enabled=True):
    if provider is None:
        provider = RecordingProvider()
    plan = {"session": {"orchestrator": {"module": "loop-live", "config": {"configured_bundle": True}},
                        "context": {"module": "context-simple"}},
            "providers": [{"module": "provider-openai", "config": {
                "default_model": provider.default_model}}],
            "tools": [{"module": "tool-filesystem", "config": {
                "working_dir": str(tmp_path), "allowed_read_paths": [str(tmp_path)]}}]}
    session = AmplifierSession(plan, session_id="root-media")
    session.coordinator.loader = FixtureLoader(provider, consumer_readiness=consumer_readiness)
    store = store or SessionStore(tmp_path / "sessions")
    media.bind_media(session, store)
    await session.initialize()
    coordinator = session.coordinator
    if post:
        media.registration_hooks(coordinator).register("tool:post", post, name="fixture-late", priority=900)
    async def checkpoint():
        rows = await coordinator.get("context").get_messages()
        canonical = store.save(session.session_id, rows, {})
        media.acknowledge_media(coordinator, store, session.session_id, canonical)
        return canonical
    coordinator.register_capability("session.durable_checkpoint", checkpoint)
    provider.capability = coordinator.get_capability(media.NAMESPACE)
    assert media.seal_hook_graph(coordinator) is expected_enabled
    wrapper = SurfaceProvider(provider, MediaOnlyDelivery(), coordinator=coordinator)
    return SimpleNamespace(session=session, coordinator=coordinator, store=store, provider=provider,
                           wrapper=wrapper, checkpoint=checkpoint)


@pytest.mark.parametrize("facts", [
    None,
    {"role": "loop", "contract": 1},
    {"role": "loop", "contract": 1, "ready": False},
    {"role": "loop", "contract": 1, "ready": None},
    {"role": "loop", "contract": 1, "ready": 0},
    {"role": "loop", "contract": 1, "ready": 1},
    {"role": "loop", "contract": 1, "ready": "true"},
    {"role": "loop", "contract": 1, "ready": True},
], ids=["absent-consumer", "missing-ready", "false", "null", "zero", "truthy-int", "truthy-string", "true"])
async def test_loop_readiness_requires_literal_true_on_actual_construction(tmp_path, facts):
    expected = facts is not None and facts.get("ready") is True
    current = await root(tmp_path, consumer_readiness=facts, expected_enabled=expected)
    try:
        bridge = current.coordinator.get_capability("web.media.construction").bridge
        assert bridge.validate_receipt(bridge.graph_receipt)
        assert callable(current.coordinator.get_capability("session.durable_checkpoint"))
        assert current.provider.capability.enabled is expected
    finally:
        await current.session.cleanup()


async def test_loop_readiness_withdrawal_is_not_resurrected_by_role_and_contract(tmp_path):
    current = await root(tmp_path)
    try:
        cap = current.provider.capability
        cap.report("loop-live", event="consumer-ready", outcome="unavailable",
                   details={"role": "loop", "contract": 1, "ready": False})
        assert media.seal_hook_graph(current.coordinator) is False
        assert cap.enabled is False
        cap.report("loop-live", event="consumer-ready", outcome="ready",
                   details={"role": "loop", "contract": 1, "ready": True})
        assert media.seal_hook_graph(current.coordinator) is True
    finally:
        await current.session.cleanup()


async def test_true_loop_readiness_does_not_replace_checkpoint_or_bridge_receipt(tmp_path):
    current = await root(tmp_path)
    try:
        coordinator = current.coordinator
        coordinator.register_capability("session.durable_checkpoint", None)
        assert media.seal_hook_graph(coordinator) is False
        coordinator.register_capability("session.durable_checkpoint", current.checkpoint)
        assert media.seal_hook_graph(coordinator) is True
        async def unowned(event, data):
            return HookResult()
        coordinator.hooks.register("tool:post", unowned, name="unowned-late-readiness")
        assert media.seal_hook_graph(coordinator) is False
        assert current.provider.capability.enabled is False
    finally:
        await current.session.cleanup()


async def read(root, path, call_id="call-image", **arguments):
    coordinator = root.coordinator
    context = coordinator.get("context")
    args = {"file_path": str(path), **arguments}
    # Actual accepted turn-engine canonical shape, including its two equal
    # representations. This is not an invented provider-facing user message.
    await context.add_message({"role": "assistant",
        "content": [{"type": "tool_call", "id": call_id, "name": "read_file", "input": args}],
        "tool_calls": [{"id": call_id, "tool": "read_file", "arguments": args}]})
    contexts = getattr(coordinator, "_tool_dispatch_contexts", None)
    if contexts is None:
        contexts = {}
        coordinator._tool_dispatch_contexts = contexts
    contexts[asyncio.current_task()] = {"tool_call_id": call_id}
    try:
        await coordinator.hooks.emit("tool:pre", {"tool_name": "read_file", "tool_call_id": call_id,
                                                 "tool_input": args})
        result = await coordinator.get("tools")["read_file"].execute(args)
        settled = await coordinator.get_capability("session.media.tool_post")({
            "tool_name": "read_file", "tool_call_id": call_id, "result": result.model_dump()},
            hooks=coordinator.hooks, coordinator=coordinator)
        final = settled["result"]
        refs = (((final or {}).get("output") or {}).get(media.NAMESPACE) or {}).get("refs", []) if isinstance(final, dict) else []
        await root.provider.capability.publish(refs,
            origin={"kind": "tool", "session_id": coordinator.session_id, "id": call_id},
            disposition="retain" if refs else "drop")
        await context.add_message({"role": "tool", "name": "read_file", "tool_call_id": call_id,
            "content": "Image read" if refs else json.dumps(final),
            **({"metadata": {media.NAMESPACE: {"refs": refs}}} if refs else {})})
        canonical = await root.checkpoint()
        return result, final, refs, canonical
    finally:
        contexts.pop(asyncio.current_task(), None)


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
async def test_actual_core_read_original_count_send_reload(tmp_path, format):
    path, original = picture(tmp_path, format)
    first = await root(tmp_path)
    try:
        result, _, refs, canonical = await read(first, path)
        assert result.success and refs[0]["sha256"] == hashlib.sha256(original).hexdigest()
        assert refs[0]["provenance"]["expires_at"] is None
        before = copy.deepcopy(canonical)
        encoded = json.dumps(canonical)
        assert base64.b64encode(original).decode() not in encoded
        request = ChatRequest(messages=[Message(**row) for row in canonical], model="gpt-6.1-sol")
        await first.wrapper.request_budget(request)
        await first.wrapper.request_budget(request)
        private = first.provider.counted
        await first.wrapper.complete(request)
        assert first.provider.observations[-1][2] == [(1, "tool", "call-image", original)]
        assert len({row[1] for row in first.provider.observations}) == 1
        assert private.messages == []
        assert canonical == before
        assert not first.wrapper._media and not first.coordinator.get_capability("web.media.request_only").requests
    finally:
        await first.session.cleanup()
    path.unlink()  # Reload must use owned originals, not reopen this path.
    second = await root(tmp_path, store=first.store)
    try:
        loaded, _ = first.store.load("root-media")
        await second.coordinator.get("context").set_messages(loaded)
        await second.checkpoint()
        request = ChatRequest(messages=[Message(**row) for row in loaded], model="gpt-6.1-sol")
        await second.wrapper.request_budget(request)
        await second.wrapper.complete(request)
        assert second.provider.observations[-1][2] == [(1, "tool", "call-image", original)]
    finally:
        await second.session.cleanup()


@pytest.mark.parametrize("change", ["null", "remove", "redact", "inplace", "deny", "throw"])
async def test_final_post_policy_has_no_resurrection(tmp_path, change):
    async def post(event, data):
        if change == "null":
            return HookResult(action="modify", data={"result": None})
        if change == "remove":
            return HookResult(action="modify", data={})
        if change == "redact":
            return HookResult(action="modify", data={"result": "redacted"})
        if change == "inplace":
            data["result"]["output"].pop(media.NAMESPACE)
        if change == "throw":
            raise ValueError("fixture refused")
        return HookResult(action="deny" if change == "deny" else "continue")
    path, original = picture(tmp_path)
    current = await root(tmp_path, post=post)
    try:
        _, final, refs, canonical = await read(current, path)
        assert refs == []
        assert not any(media.NAMESPACE in (row.get("metadata") or {}) for row in canonical)
        assert base64.b64encode(original).decode() not in json.dumps(canonical)
        # A refused image does not poison a later ordinary text read.
        text = tmp_path / "plain"
        text.write_text("plaintext recovery")
        result = await current.coordinator.get("tools")["read_file"].execute({"file_path": str(text)})
        assert result.success and "plaintext recovery" in result.output["content"]
    finally:
        await current.session.cleanup()


async def test_current_policy_revocation_refuses_fitted_send_then_warns(tmp_path):
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, refs, rows = await read(current, path)
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        await current.wrapper.request_budget(request)
        tool = current.coordinator.get("tools")["read_file"]
        tool.denied_read_paths = [str(tmp_path)]
        with pytest.raises(ValueError):
            await current.wrapper.complete(request)
        assert not any(row[0] == "send" for row in current.provider.observations)
        fresh = request.model_copy(deep=True)
        await current.wrapper.request_budget(fresh)
        await current.wrapper.complete(fresh)
        assert current.provider.observations[-1][2] == []
        assert refs  # Stored reference/originals were not removed by policy.
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize("failure", ["count", "send", "abandon", "mutate"])
async def test_private_cleanup_on_failure_and_abandonment(tmp_path, failure):
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, _, rows = await read(current, path)
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        current.provider.fail_count = failure == "count"
        if failure == "count":
            with pytest.raises(ValueError):
                await current.wrapper.request_budget(request)
        else:
            await current.wrapper.request_budget(request)
            if failure == "abandon":
                assert current.wrapper.release_media_request(request)
            else:
                current.provider.fail_send = failure == "send"
                if failure == "mutate":
                    request.messages[0].content = "changed after count"
                with pytest.raises(ValueError):
                    await current.wrapper.complete(request)
        assert not current.wrapper._media
        assert not current.coordinator.get_capability("web.media.request_only").requests
        assert current.provider.counted is None or current.provider.counted.messages == []
    finally:
        await current.session.cleanup()


async def test_mixed_direct_upload_and_tool_ref_keep_exact_positions(tmp_path, monkeypatch):
    from amplifier_web.attachments import encode, save
    path, original = picture(tmp_path)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    current = await root(tmp_path)
    try:
        uploaded = save(tmp_path / "app", "upload.png", base64.b64encode(original).decode())
        content = encode(SimpleNamespace(id="actual-input", text="attached", attachments=[uploaded]),
                         current.coordinator)
        await current.coordinator.get("context").add_message({"role": "user", "content": content,
            "metadata": {"amplifier_input": {"version": 1, "id": "actual-input"}}})
        _, _, _, rows = await read(current, path)
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        await current.wrapper.request_budget(request)
        await current.wrapper.complete(request)
        images = current.provider.observations[-1][2]
        assert images == [(0, "user", None, original), (2, "tool", "call-image", original)]
        # A copy in another input is not authorized by equal pixel bytes.
        forged = request.model_copy(deep=True)
        forged.messages[0].metadata["amplifier_input"]["id"] = "not-issued"
        await current.wrapper.request_budget(forged)
        await current.wrapper.complete(forged)
        assert current.provider.observations[-1][2] == [(2, "tool", "call-image", original)]
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize("restriction", ["size", "pagination", "gif", "corrupt"])
async def test_image_refusal_is_meaningful_and_text_recovers(tmp_path, restriction):
    from amplifier_foundation.session.media import MediaPolicy
    path, original = picture(tmp_path)
    current = await root(tmp_path)
    try:
        if restriction == "size":
            current.coordinator.get_capability("web.media.store").policy = MediaPolicy(max_bytes=len(original)-1)
        elif restriction == "gif":
            output = io.BytesIO()
            Image.new("RGB", (2, 2)).save(output, format="GIF")
            path.write_bytes(output.getvalue())
        elif restriction == "corrupt":
            path.write_bytes(b"\x89PNG\r\n\x1a\nnot an image")
        result, _, refs, _ = await read(current, path, **({"limit": 1} if restriction == "pagination" else {}))
        assert not result.success and not refs
        assert "no pixels" in result.error["message"]
        path.write_text("ordinary text after refusal")
        result = await current.coordinator.get("tools")["read_file"].execute({"file_path": str(path)})
        assert result.success and "ordinary text after refusal" in result.output["content"]
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize("started", [False, True])
async def test_stream_close_releases_counted_private_candidate(tmp_path, started):
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, _, rows = await read(current, path)
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        await current.wrapper.request_budget(request)
        private = current.provider.counted
        stream = current.wrapper.stream(request)
        if started:
            assert await anext(stream) == "offline stream"
        await stream.aclose()
        assert private.messages == []
        assert not current.wrapper._media and not current.wrapper.prepared
    finally:
        await current.session.cleanup()


async def test_mixed_upload_issuance_survives_reload_without_becoming_global_trust(tmp_path, monkeypatch):
    from amplifier_web.attachments import encode, save
    path, original = picture(tmp_path)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "app"))
    first = await root(tmp_path)
    try:
        uploaded = save(tmp_path / "app", "upload.png", base64.b64encode(original).decode())
        content = encode(SimpleNamespace(id="persisted-input", text="attached", attachments=[uploaded]),
                         first.coordinator)
        await first.coordinator.get("context").add_message({"role": "user", "content": content,
            "metadata": {"amplifier_input": {"version": 1, "id": "persisted-input"}}})
        await read(first, path)
    finally:
        await first.session.cleanup()
    receipt = first.store.directory("root-media") / "media-upload-issuance.json"
    assert base64.b64encode(original).decode() not in receipt.read_text()
    second = await root(tmp_path, store=first.store)
    try:
        rows, _ = first.store.load("root-media")
        await second.coordinator.get("context").set_messages(rows)
        await second.checkpoint()
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        await second.wrapper.request_budget(request)
        await second.wrapper.complete(request)
        assert second.provider.observations[-1][2] == [
            (0, "user", None, original), (2, "tool", "call-image", original)]
    finally:
        await second.session.cleanup()


@pytest.mark.parametrize("change", ["missing", "wrong-name", "duplicate", "orphan", "path"])
async def test_canonical_relation_cannot_be_forged_or_rebound(tmp_path, change):
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, _, rows = await read(current, path)
        changed = copy.deepcopy(rows)
        if change == "missing":
            changed = changed[1:]
        elif change == "wrong-name":
            changed[0]["tool_calls"][0]["name"] = "not_read_file"
        elif change == "duplicate":
            changed = [*changed, *copy.deepcopy(changed)]
        elif change == "orphan":
            changed = [changed[1], changed[0]]
        else:
            changed[0]["tool_calls"][0]["arguments"]["file_path"] = str(tmp_path / "different")
        await current.coordinator.get("context").set_messages(changed)
        await current.checkpoint()
        request = ChatRequest(messages=[Message(**row) for row in changed], model="gpt-6.1-sol")
        await current.wrapper.request_budget(request)
        await current.wrapper.complete(request)
        assert current.provider.observations[-1][2] == []
    finally:
        await current.session.cleanup()


async def test_selected_provider_abandons_its_exact_private_count(tmp_path):
    from amplifier_web.host.session import SelectedProvider
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, _, rows = await read(current, path)
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        selected = SelectedProvider(current.provider, {"model": "gpt-6.1-sol"}, lambda _: current.wrapper)
        await selected.request_budget(request)
        assert selected.release_media_request(request)
        assert not current.wrapper._media and not current.wrapper.prepared
        assert current.provider.counted.messages == []
    finally:
        await current.session.cleanup()


async def test_actual_host_app_hooks_and_work_wrappers_participate_without_bypass(tmp_path):
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.read_policy import mounted_read_file, media_read_allowed
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        return {}
    async def deny(name, args):
        return {"allowed": False}
    try:
        coordinator = current.coordinator
        original = mounted_read_file(coordinator)
        coordinator.register_capability("web.work_effect_check", deny)
        with media.hook_configuration(coordinator):
            await install_app_access(coordinator, bridge, execution_role="root")
            await media.flush_adapters(coordinator)
        assert coordinator.get_capability(media.NAMESPACE).enabled
        assert coordinator.get("tools")["read_file"] is not original
        assert mounted_read_file(coordinator) is original
        assert media_read_allowed(coordinator, str(path))
        # Work still refuses execution; source-policy inspection is not bypass.
        denied = await coordinator.get("tools")["read_file"].execute({"file_path": str(path)})
        assert denied.success is False
        assert not list((current.store.directory("root-media") / "media" / "assets").iterdir())
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize("change", ["support", "options", "indexed-null"])
async def test_counted_support_and_options_cannot_change_before_send(tmp_path, change):
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, _, rows = await read(current, path)
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        await current.wrapper.request_budget(request)
        if change == "support":
            current.provider.support = {"v": 1, "positions": []}
        elif change == "indexed-null":
            current.provider.get_media_request_support_for_request = None
        with pytest.raises(ValueError):
            await current.wrapper.complete(request, **({"temperature": 0.7} if change == "options" else {}))
        assert not current.wrapper._media and not current.wrapper.prepared
        assert not any(row[0] == "send" for row in current.provider.observations)
    finally:
        await current.session.cleanup()


async def test_unknown_post_graph_is_warning_only_and_does_not_read_assets(tmp_path, monkeypatch):
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, _, rows = await read(current, path)
        async def unowned(event, data):
            return HookResult()
        # A raw late registration cannot borrow the earlier source receipt.
        current.coordinator.hooks.register("tool:post", unowned, name="unknown-post")
        monkeypatch.setattr(current.coordinator.get_capability("web.media.store"), "resolve_ref",
                            lambda *args, **kwargs: pytest.fail("Unknown graph attempted hydration"))
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        projection = await media.prepare_media(current.coordinator, current.provider, request)
        projection.bind_canonical(request)
        projection.validate()
        assert "no pixels supplied" in projection.request.messages[-1].content[-1].text
        assert projection.request.messages[-1].tool_call_id == "call-image"
        assert media.NAMESPACE not in projection.request.messages[-1].metadata
        projection.release()
    finally:
        await current.session.cleanup()


async def test_selected_execution_change_cannot_replace_counted_private_request(tmp_path):
    from amplifier_web.host.session import SelectedProvider
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, _, rows = await read(current, path)
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        adapters = [current.wrapper]
        selected = SelectedProvider(current.provider, {"model": "gpt-6.1-sol"}, lambda _: adapters[0])
        await selected.request_budget(request)
        private = current.provider.counted
        adapters[0] = SurfaceProvider(current.provider, MediaOnlyDelivery(), coordinator=current.coordinator)
        with pytest.raises(ValueError, match="selected provider changed"):
            await selected.complete(request)
        assert private.messages == [] and not current.wrapper._media
        assert not any(row[0] == "send" for row in current.provider.observations)
    finally:
        await current.session.cleanup()


async def test_private_projection_is_released_on_cancellation(tmp_path):
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    entered = asyncio.Event()
    async def hold(request, **kwargs):
        current.provider.capability.validate(request, purpose="transport", request_options=kwargs)
        entered.set()
        await asyncio.Event().wait()
    try:
        _, _, _, rows = await read(current, path)
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol")
        await current.wrapper.request_budget(request)
        private = current.provider.counted
        current.provider.complete = hold
        task = asyncio.create_task(current.wrapper.complete(request))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not current.wrapper._media and private.messages == []
    finally:
        await current.session.cleanup()


@pytest.mark.parametrize("form", ["loop-tool-arguments", "core-name-input", "equivalent-dual"])
async def test_canonical_supported_forms_bind_after_actual_core_context_roundtrip(tmp_path, form):
    from amplifier_core.message_models import ToolCallBlock
    from amplifier_web.read_policy import read_file_call_paths
    path, _ = picture(tmp_path)
    current = await root(tmp_path)
    try:
        _, _, _, rows = await read(current, path)
        call = {"id": "call-image", "tool": "read_file", "arguments": {"file_path": str(path)}}
        block = ToolCallBlock(id=call["id"], name=call["tool"], input=call["arguments"])
        rows[0]["tool_calls"] = [call] if form != "core-name-input" else []
        rows[0]["content"] = [block.model_dump()] if form != "loop-tool-arguments" else ""
        context = current.coordinator.get("context")
        await context.set_messages(rows)
        canonical = await current.checkpoint()
        assert read_file_call_paths(canonical, "root-media") == {"call-image": str(path)}
    finally:
        await current.session.cleanup()


async def test_request_only_surface_and_tool_refs_count_send_then_release(tmp_path):
    from amplifier_web.surface_delivery import SurfaceDelivery
    path, original = picture(tmp_path)
    current = await root(tmp_path)
    surface = SurfaceDelivery(None)
    async def manifest(operation, args):
        assert operation == "context.manifest"
        return {"inputIds": ["input"], "surfaces": [{
            "surfaceId": "canvas", "revision": 1, "image": {"available": True, "digest": "original"}}]}
    surface.bridge = manifest
    surface.epoch = ["input"]
    surface.receipts["receipt"] = {"result": {
        "surfaceId": "canvas", "revision": 1, "representation": "image", "digest": "original",
        "observationReceipt": "receipt"}}
    surface.images["receipt"] = {"image": base64.b64encode(original).decode(),
                                "result": surface.receipts["receipt"]["result"], "epoch": ["input"]}
    async def supports(request, provider):
        return True
    surface.image_capabilities.supports = supports
    # SurfaceDelivery retains pixels only for its real tool receipt. Keep it
    # after the existing assistant/tool pair, without fabricating a user image.
    try:
        _, _, _, rows = await read(current, path)
        rows += [{"role": "assistant", "content": "", "tool_calls": [{
            "id": "call-surface", "name": "app_control", "arguments": {}}]},
            {"role": "tool", "name": "app_control", "tool_call_id": "call-surface",
             "content": json.dumps(surface.receipts["receipt"]["result"])}]
        request = ChatRequest(messages=[Message(**row) for row in rows], model="gpt-6.1-sol",
                              tools=[{"name": "app_control", "description": "", "parameters": {}}])
        wrapper = SurfaceProvider(current.provider, surface, coordinator=current.coordinator)
        await wrapper.request_budget(request)
        private = current.provider.counted
        await wrapper.complete(request)
        images = current.provider.observations[-1][2]
        assert images == [(1, "tool", "call-image", original), (4, "user", None, original)]
        assert private.messages == []
        assert not current.coordinator.get_capability("web.media.request_only").requests
    finally:
        await current.session.cleanup()