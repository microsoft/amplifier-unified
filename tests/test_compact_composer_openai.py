"""Real ordinary Responses input and indexed refusal on the same static GIF/PNG."""
import base64
import os

import pytest

from amplifier_web import attachments
from amplifier_web.host.media import NAMESPACE
from amplifier_web.surface_delivery import SurfaceProvider


@pytest.mark.skipif(os.name != "posix", reason="Compact composer writes require a private POSIX home")
@pytest.mark.parametrize("case", ["one-input", "earlier-gif", "custom-endpoint", "extra-option"])
async def test_ordinary_and_compact_uploads_reach_the_same_openai_wire(tmp_path, monkeypatch, case):
    from amplifier_core.message_models import ChatRequest, Message
    from amplifier_module_loop_live.runtime import Input
    from amplifier_module_provider_openai import OpenAIProvider
    from fixtures.narrow_tool_image_fixture import MediaOnlyDelivery
    from test_narrow_tool_images import picture
    from test_openai_work_tool_images import SDKRead, MODEL
    from test_tool_image_consumer_join import actual_root, ScriptedReadProvider
    import socket

    def offline(*args, **kwargs):
        pytest.fail("The local SDK comparison attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", offline)
    monkeypatch.setattr(socket.socket, "connect_ex", offline)
    monkeypatch.setattr(socket, "getaddrinfo", offline)
    home = tmp_path / "app"
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    session, _, _, _, _, host = await actual_root(tmp_path, ScriptedReadProvider(tmp_path, read=False))
    coordinator = session.coordinator
    try:
        with host.execution_registration(coordinator, coordinator.hooks):
            assert await coordinator.get("orchestrator")._media_consumer.refresh()
        assert host.sealed
        groups, originals = [], []
        custom = case == "custom-endpoint"
        for fmt in (("PNG",) if case in {"custom-endpoint", "extra-option"} else ("GIF", "PNG")):
            _, pixels = picture(tmp_path, fmt)
            originals.append(pixels)
            upload = attachments.save(home, fmt, base64.b64encode(pixels).decode())
            if case == "earlier-gif" or not groups:
                groups.append([])
            groups[-1].append(upload)
        before, after = [], []
        for i, group in enumerate(groups):
            command = Input("user", f"Look at these {i}", attachments=tuple(group))
            meta = {"amplifier_input": {"id": command.id, "kind": "user", "source": "user"}}
            before.append(Message(role="user", metadata=meta, content=attachments.encode(command)))
            content = attachments.encode(command, coordinator, image_references=True)
            await coordinator.get("context").add_message({"role": "user", "metadata": meta, "content": content})
        after = [Message(**row) for row in await coordinator.get("context").get_messages()]
        assert any(NAMESPACE in (message.metadata or {}) for message in after)
        def private_read(*args, **kwargs):
            pytest.fail("A refused indexed request attempted to open a private asset")
        monkeypatch.setattr(coordinator.get_capability("web.media.store"), "resolve_ref", private_read)
        wires = []
        for messages in (before, after):
            config = {"default_model": MODEL, "use_streaming": False}
            if custom:
                config["base_url"] = "https://offline.invalid/v1"
            if case == "extra-option":
                config["extra_request_params"] = {"metadata": {"case": "local"}}
            provider = OpenAIProvider(api_key="offline", config=config, coordinator=coordinator)
            sdk = SDKRead(private_media=False)
            sdk.setup(provider)
            if custom:
                provider._client.base_url = config["base_url"]
            wrapped = SurfaceProvider(provider, MediaOnlyDelivery(), coordinator=coordinator)
            try:
                request = ChatRequest(messages=messages, model=MODEL)
                assert provider.get_media_request_support_for_request(request=request, model=MODEL)["positions"] == []
                await wrapped.request_budget(request, context_estimate=250, require_measurement=True)
                retained = []
                if messages is after:
                    view = wrapped._media[id(request)][1].request
                    retained = [block.source for row in view.messages for block in row.content if block.type == "image"]
                    assert len(retained) == len(originals)
                await wrapped.complete(request)
                if messages is after:
                    assert view.messages == [] and all(not source for source in retained)
                pixels = [[body for _, body in sdk.pixels(doc)] for _, doc in sdk.wire]
                expected = [] if custom else originals
                assert all(value == expected for value in pixels)
                routes = [path for path, _ in sdk.wire]
                assert routes.count("/v1/responses") == 1
                if case in {"one-input", "earlier-gif"}:
                    assert "/v1/responses/input_tokens" in routes
                wires.append([(path, doc["input"]) for path, doc in sdk.wire])
                if messages is after and case == "extra-option":
                    # A declared MIME/ordinary-role exclusion remains authoritative.
                    provider.get_media_request_support = lambda **kw: {"v": 1, "positions": []}
                    await wrapped.request_budget(request, context_estimate=250)
                    assert not any(b.type == "image" for m in wrapped._media[id(request)][1].request.messages
                                   for b in m.content)
                    await wrapped.close_media()
                    del provider.get_media_request_support
                    provider.get_media_request_support_for_request = lambda **kw: {
                        "v": 2, "positions": [], "mime_types": []}
                    await wrapped.request_budget(request, context_estimate=250)
                    assert not any(b.type == "image" for m in wrapped._media[id(request)][1].request.messages
                                   for b in m.content)
                if messages is after and case == "one-input":
                    await wrapped.request_budget(request, context_estimate=250, require_measurement=True)
                    fitted = wrapped._media[id(request)][1].request
                    handles = [b.source for m in fitted.messages for b in m.content if b.type == "image"]
                    attachments.file_path(home, groups[-1][-1]["id"])[0].unlink()
                    prior = len(sdk.wire)
                    with pytest.raises((ValueError, OSError)):
                        await wrapped.complete(request)
                    assert len(sdk.wire) == prior and not fitted.messages and all(not src for src in handles)
            finally:
                await wrapped.close_media()
                await provider.close()
        assert wires[0] == wires[1]
    finally:
        await session.cleanup()


def test_failed_upload_receipt_does_not_grant_an_in_memory_binding(tmp_path, monkeypatch):
    from amplifier_web.host.media import RequestOnlyRegistry
    from amplifier_web.host import config
    path = tmp_path / "issued.json"
    registry = RequestOnlyRegistry(path)
    registry.issue_upload("first", [])
    previous = path.read_bytes()
    def refuse(*args):
        raise OSError("storage unavailable")
    monkeypatch.setattr(config, "write_private", refuse)
    with pytest.raises(OSError, match="storage unavailable"):
        registry.issue_upload("second", [], refs=[{"sha256": "x"}])
    assert set(registry.uploads) == {"first"} and path.read_bytes() == previous
