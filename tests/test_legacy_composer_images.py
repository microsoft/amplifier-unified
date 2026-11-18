"""Legacy composer wire and real on-disk Work capture; HTTP bottom only is fake."""
import asyncio
import base64
import copy
from io import BytesIO
import json
import socket

import anthropic
import httpx2 as httpx
import pytest

from amplifier_web.host import media
from amplifier_web.host.storage import SessionStore
from fixtures.ordinary_work_image_fixture import work_root
from test_narrow_tool_images import picture

MODEL = "claude-sonnet-4-5"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refused(*args, **kwargs):
        pytest.fail("Legacy composer fixture attempted a network operation")
    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    monkeypatch.setattr(socket, "getaddrinfo", refused)


class LegacySDK:
    def __init__(self):
        self.wire = []

    def setup(self, provider):
        from amplifier_module_provider_anthropic import AnthropicProvider
        assert type(provider) is AnthropicProvider and provider._client is None
        assert not hasattr(provider, "get_media_request_support_for_request")
        self.provider = provider
        provider._client = anthropic.AsyncAnthropic(api_key="offline-fixture", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.respond)))

    async def respond(self, request):
        self.wire.append((request.url.path, copy.deepcopy(json.loads(request.content))))
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 20})
        return httpx.Response(200, json={
            "id": "msg-offline", "type": "message", "role": "assistant", "model": MODEL,
            "content": [{"type": "text", "text": "offline completion"}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 20, "output_tokens": 1}})

    @staticmethod
    def pixels(body):
        return [(message["role"], base64.b64decode(block["source"]["data"]))
                for message in body["messages"]
                for block in message["content"] if isinstance(message["content"], list)
                if block.get("type") == "image"]


@pytest.mark.parametrize("format", ["PNG", "JPEG", "GIF", "WEBP"])
async def test_legacy_composer_live_sdk_wire_capture_and_reload(tmp_path, monkeypatch, format):
    from amplifier_module_loop_live.runtime import Input
    from amplifier_web.attachments import file_path, save

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, original = picture(workspace, format)
    encoded = base64.b64encode(original).decode()
    sdk = LegacySDK()
    session, store, checkpoint, checks, host = await work_root(tmp_path, monkeypatch, None,
        prepared_host=True, worker_observers=True, live=True, provider_setup=sdk.setup,
        provider_module="provider-anthropic", model=MODEL)
    runtime = session.coordinator.get_capability("live.runtime")
    upload = save(tmp_path / "app", "composer-image", encoded)
    command = Input("user", "Inspect the uploaded image", attachments=({"id": upload["id"]},))
    execution = asyncio.create_task(session.execute(""))
    try:
        await runtime.submit(command)
        finished = await runtime.wait_for(lambda row:
            row["type"] in {"generation.finished", "generation.failed"}
            and command.id in row.get("input_ids", []), timeout=15)
        assert finished["type"] == "generation.finished", finished
        assert finished["text"] == "offline completion" and host.sealed
        sends = [body for endpoint, body in sdk.wire if endpoint == "/v1/messages"]
        assert sends and LegacySDK.pixels(sends[-1]) == [("user", original)]
        assert "Image unavailable" not in json.dumps(sends[-1])
        rows = await checkpoint()
        [user] = [row for row in rows if row["role"] == "user" and
                  ((row.get("metadata") or {}).get("amplifier_input") or {}).get("id") == command.id]
        assert user["metadata"]["amplifier_input"]["id"] == command.id
        assert media.NAMESPACE not in user["metadata"]  # Existing inline upload, not a private ref.
        assert not checks
        assert not session.coordinator.get_capability("web.media.request_only").requests
        issuance = store.directory(session.session_id) / "media-upload-issuance.json"
        assert encoded not in issuance.read_text()
        assert command.id in json.loads(issuance.read_text())["inputs"]
        # The ordinary legacy transcript still owns its original inline upload;
        # diagnostics and UI events must not duplicate its pixel payload.
        assert encoded not in json.dumps(runtime.events)
        assert encoded not in json.dumps(host.emitted)
        [capture] = list((tmp_path / "shared" / "projects").rglob("events.jsonl"))
        text = capture.read_text()
        names = {json.loads(line)["event"] for line in text.splitlines()}
        assert {"llm:request", "llm:response"} <= names
        assert session.coordinator.get_capability("web.provider_request_redaction") is True
        assert encoded not in text and "data:image/" not in text
        assert "__unified_image_transport__" in text
        path.unlink()
        file_path(tmp_path / "app", upload["id"])[0].unlink()
    finally:
        if not execution.done():
            await runtime.submit(Input("stop"))
        try:
            await asyncio.wait_for(execution, 5)
        finally:
            await session.cleanup()

    reopened = SessionStore(store.base_dir)
    loaded, _ = reopened.load(session.session_id)
    cold = LegacySDK()
    resumed, _, checkpoint, checks, host = await work_root(tmp_path, monkeypatch, None,
        store=reopened, prepared_host=True, worker_observers=True, provider_setup=cold.setup,
        provider_module="provider-anthropic", model=MODEL)
    try:
        await resumed.coordinator.get("context").set_messages(loaded)
        await checkpoint()
        assert await resumed.execute("Use the saved upload without reading its source") == "offline completion"
        sends = [body for endpoint, body in cold.wire if endpoint == "/v1/messages"]
        assert LegacySDK.pixels(sends[-1]) == [("user", original)]
        assert host.sealed and not checks
        [capture] = list((tmp_path / "shared" / "projects").rglob("events.jsonl"))
        assert encoded not in capture.read_text()
    finally:
        await resumed.cleanup()


@pytest.mark.parametrize("first", ["legacy", "openai"])
@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
async def test_composer_uploads_survive_sdk_provider_switches(tmp_path, monkeypatch, first, format):
    """New upload on each provider, then two cold reopens after source deletion."""
    from PIL import Image
    from amplifier_module_loop_live.runtime import Input
    from amplifier_web.attachments import file_path, save
    from test_openai_work_tool_images import SDKRead, assert_pixel_free_capture

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, original = picture(workspace, format)
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format=format)
    originals = [original, buffer.getvalue()]
    expected = []
    store = None
    other = "openai" if first == "legacy" else "legacy"
    for phase, provider in enumerate((first, other, first, other)):
        loaded = store.load("ordinary-work-media")[0] if store is not None else None
        sdk = LegacySDK() if provider == "legacy" else SDKRead(private_media=False)
        session, store, checkpoint, checks, host = await work_root(tmp_path, monkeypatch, None,
            store=store, prepared_host=True, worker_observers=True, live=True,
            provider_setup=sdk.setup, provider_module=(
                "provider-anthropic" if provider == "legacy" else "provider-openai"),
            model=MODEL if provider == "legacy" else "gpt-6.1-sol")
        runtime = session.coordinator.get_capability("live.runtime")
        execution = None
        try:
            if loaded is not None:
                await session.coordinator.get("context").set_messages(loaded)
                await checkpoint()
            uploads = ()
            if phase < 2:
                upload = save(tmp_path / "app", "composer-" + str(phase),
                              base64.b64encode(originals[phase]).decode())
                uploads = ({"id": upload["id"]},)
                expected.append(originals[phase])
            command = Input("user", "Use my saved composer images; do not read files", attachments=uploads)
            execution = asyncio.create_task(session.execute(""))
            await runtime.submit(command)
            finished = await runtime.wait_for(lambda row:
                row["type"] in {"generation.finished", "generation.failed"}
                and command.id in row.get("input_ids", []), timeout=15)
            assert finished["type"] == "generation.finished", finished
            endpoint = "/v1/messages" if provider == "legacy" else "/v1/responses"
            sends = [body for route, body in sdk.wire if route == endpoint]
            assert [pixels for _, pixels in sdk.pixels(sends[-1])] == expected, (phase, provider)
            assert "Image unavailable" not in json.dumps(sends[-1])
            assert host.sealed and checks == []
            # Both real SDK native counts and sends see the same exact image bytes.
            counts = [body for route, body in sdk.wire
                      if route.endswith(("/count_tokens", "/input_tokens"))]
            assert counts and [pixels for _, pixels in sdk.pixels(counts[-1])] == expected
            rows = await checkpoint()
            assert not session.coordinator.get_capability("web.media.request_only").requests
            for pixels in expected:
                encoded = base64.b64encode(pixels).decode()
                assert encoded not in json.dumps(runtime.events)
                assert encoded not in json.dumps(host.emitted)
                assert_pixel_free_capture(tmp_path, pixels, tool=False)
            if phase < 2:
                file_path(tmp_path / "app", upload["id"])[0].unlink()
                if phase == 0:
                    path.unlink()
        finally:
            try:
                if execution is not None:
                    if not execution.done():
                        await runtime.submit(Input("stop"))
                    await asyncio.wait_for(execution, 5)
            finally:
                await session.cleanup()
        store = SessionStore(store.base_dir)  # Fresh media store and issuance owner each time.

    # Deliberate compatibility/privacy tradeoff, not reference-only history.
    assert not any(media.NAMESPACE in (row.get("metadata") or {}) for row in rows)
    images = [base64.b64decode(block["source"]["data"]) for row in rows
              if isinstance(row["content"], list) for block in row["content"]
              if block.get("type") == "image"]
    assert images == originals
    directory = store.directory(session.session_id)
    assert not list((directory / "media" / "assets").iterdir())
    assert not list((directory / "media" / "staging").iterdir())
    issuance = (directory / "media-upload-issuance.json").read_text()
    assert all(base64.b64encode(pixels).decode() not in issuance for pixels in originals)


async def test_inline_composer_and_authorized_tool_ref_remain_distinct_across_switch(tmp_path, monkeypatch):
    from PIL import Image
    from amplifier_module_loop_live.runtime import Input
    from amplifier_web.attachments import file_path, save
    from test_openai_work_tool_images import SDKRead, assert_pixel_free_capture, sdk_root

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, tool_pixels = picture(workspace)
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
    upload_pixels = buffer.getvalue()
    sdk = SDKRead([path], private_media=False)
    session, store, checkpoint, checks, host = await sdk_root(tmp_path, monkeypatch, sdk, live=True)
    upload = save(tmp_path / "app", "composer.png", base64.b64encode(upload_pixels).decode())
    command = Input("user", "Inspect my upload and read the authorized local image",
                    attachments=({"id": upload["id"]},))
    runtime = session.coordinator.get_capability("live.runtime")
    execution = asyncio.create_task(session.execute(""))
    try:
        await runtime.submit(command)
        finished = await runtime.wait_for(lambda row:
            row["type"] in {"generation.finished", "generation.failed"}
            and command.id in row.get("input_ids", []), timeout=15)
        assert finished["type"] == "generation.finished", finished
        assert SDKRead.pixels(sdk.image_sends()[-1]) == [
            (None, upload_pixels), ("call-sdk-read", tool_pixels)]
        assert host.sealed and checks == [("read_file", {"file_path": str(path)})]
        rows = await checkpoint()
        [tool] = [row for row in rows if media.NAMESPACE in (row.get("metadata") or {})]
        assert tool["role"] == "tool" and tool["tool_call_id"] == "call-sdk-read"
        assert base64.b64encode(tool_pixels).decode() not in json.dumps(rows)
        path.unlink()
        file_path(tmp_path / "app", upload["id"])[0].unlink()
    finally:
        try:
            if not execution.done():
                await runtime.submit(Input("stop"))
            await asyncio.wait_for(execution, 5)
        finally:
            await session.cleanup()

    for legacy in (True, False):
        reopened = SessionStore(store.base_dir)
        loaded, _ = reopened.load("ordinary-work-media")
        cold = LegacySDK() if legacy else SDKRead()
        resumed, _, checkpoint, checks, host = await work_root(tmp_path, monkeypatch, None,
            store=reopened, prepared_host=True, worker_observers=True, provider_setup=cold.setup,
            provider_module="provider-anthropic" if legacy else "provider-openai",
            model=MODEL if legacy else "gpt-6.1-sol")
        try:
            await resumed.coordinator.get("context").set_messages(loaded)
            await checkpoint()
            assert await resumed.execute("Use the saved images; do not read files") == "offline completion"
            endpoint = "/v1/messages" if legacy else "/v1/responses"
            [sent] = [body for route, body in cold.wire if route == endpoint]
            assert [pixels for _, pixels in cold.pixels(sent)] == (
                [upload_pixels] if legacy else [upload_pixels, tool_pixels])
            assert ("Image unavailable" in json.dumps(sent)) is legacy
            assert host.sealed and checks == []
            saved = await checkpoint()
            assert [row for row in saved if media.NAMESPACE in (row.get("metadata") or {})] == [tool]
            assert base64.b64encode(tool_pixels).decode() not in json.dumps(saved)
            for pixels in (upload_pixels, tool_pixels):
                assert base64.b64encode(pixels).decode() not in json.dumps(host.emitted)
                assert_pixel_free_capture(tmp_path, pixels, tool=True)
        finally:
            await resumed.cleanup()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("rule", ["drop", "request-only", "max-bytes", "max-pixels", "max-images", "exact-bounds"])
async def test_composer_policy_real_loop_checkpoint_sdk_and_cold_switch(tmp_path, monkeypatch, legacy, rule):
    from PIL import Image
    from amplifier_foundation.session.media import MediaPolicy
    from amplifier_module_loop_live.runtime import Input
    from amplifier_web.attachments import file_path, save
    from test_openai_work_tool_images import SDKRead, assert_pixel_free_capture

    def png(size, color):
        buffer = BytesIO()
        Image.new("RGB", size, color).save(buffer, format="PNG")
        return buffer.getvalue()

    good = png((8, 8), "blue")
    bad = png((16, 16), "green")
    policy = {
        "drop": {"retention": "drop"}, "request-only": {"retention": "request-only"},
        "max-bytes": {"max_bytes": len(good)}, "max-pixels": {"max_pixels": 64},
        # Existing upload ingestion is one image per origin, not a message quota.
        "max-images": {"max_images": 1},
        "exact-bounds": {"max_bytes": len(good), "max_pixels": 64, "max_images": 1},
    }[rule]
    if rule in {"max-bytes", "exact-bounds"}:
        bad = good + b"\x00" * 80
    originals = [bad, good, good]
    expected = ([] if rule in {"drop", "request-only"} else
                [good, good] if rule in {"max-bytes", "max-pixels", "exact-bounds"} else originals)
    class CheckpointSDK(LegacySDK if legacy else SDKRead):
        async def respond(self, request):
            # Observe the real on-disk checkpoint at every physical SDK count/send,
            # not merely after the loop finishes or via an in-memory callback.
            saved, _ = store.load(session.session_id)
            [saved_user] = [row for row in saved if row["role"] == "user"
                           and ((row.get("metadata") or {}).get("amplifier_input") or {}).get("id") == command.id]
            assert [base64.b64decode(block["source"]["data"]) for block in saved_user["content"]
                    if block["type"] == "image"] == expected
            return await super().respond(request)

    sdk = CheckpointSDK() if legacy else CheckpointSDK(private_media=False)
    session, store, checkpoint, checks, host = await work_root(tmp_path, monkeypatch, None,
        prepared_host=True, worker_observers=True, live=True, provider_setup=sdk.setup,
        provider_module="provider-anthropic" if legacy else "provider-openai",
        model=MODEL if legacy else "gpt-6.1-sol")
    # The composer must read the current host policy, not cache installation defaults.
    session.coordinator.get_capability("web.media.store").policy = MediaPolicy(**policy)
    uploads = [save(tmp_path / "app", f"image-{i}.png", base64.b64encode(pixels).decode())
               for i, pixels in enumerate(originals)]
    uploads.append(save(tmp_path / "app", "notes.txt", base64.b64encode(b"Keep attached text").decode()))
    command = Input("user", "Keep user text", attachments=tuple({"id": row["id"]} for row in uploads))
    runtime = session.coordinator.get_capability("live.runtime")
    execution = asyncio.create_task(session.execute(""))
    try:
        await runtime.submit(command)
        finished = await runtime.wait_for(lambda row:
            row["type"] in {"generation.finished", "generation.failed"}
            and command.id in row.get("input_ids", []), timeout=15)
        assert finished["type"] == "generation.finished", finished
        assert host.sealed and not checks
        endpoint = "/v1/messages" if legacy else "/v1/responses"
        sends = [body for route, body in sdk.wire if route == endpoint]
        counts = [body for route, body in sdk.wire if route.endswith(("/count_tokens", "/input_tokens"))]
        assert sends and counts
        assert all([pixels for _, pixels in sdk.pixels(body)] == expected for body in sends + counts)
        rows = await checkpoint()
        [user] = [row for row in rows if row["role"] == "user"
                  and ((row.get("metadata") or {}).get("amplifier_input") or {}).get("id") == command.id]
        assert media.NAMESPACE not in user["metadata"]
        assert "Keep user text" in json.dumps(user) and "Keep attached text" in json.dumps(user)
        images = [base64.b64decode(block["source"]["data"]) for block in user["content"]
                  if block["type"] == "image"]
        assert images == expected
        notices = [block for block in user["content"] if "Image unavailable:" in block.get("text", "")]
        assert len(notices) == len(originals) - len(expected)
        directory = store.directory(session.session_id)
        canonical = (directory / "transcript.jsonl").read_text()
        assert (base64.b64encode(bad).decode() in canonical) is (bad in expected)
        issuance = directory / "media-upload-issuance.json"
        assert issuance.exists() is bool(expected)
        if expected:
            assert command.id in json.loads(issuance.read_text())["inputs"]
        for name in ("assets", "staging", "records", "origins"):
            assert not list((directory / "media" / name).iterdir())
        for pixels in originals:
            encoded = base64.b64encode(pixels).decode()
            assert encoded not in json.dumps(runtime.events)
            assert encoded not in json.dumps(host.emitted)
            if issuance.exists():
                assert encoded not in issuance.read_text()
            assert_pixel_free_capture(tmp_path, pixels, tool=False)
        for upload in uploads:
            file_path(tmp_path / "app", upload["id"])[0].unlink()
    finally:
        try:
            if not execution.done():
                await runtime.submit(Input("stop"))
            await asyncio.wait_for(execution, 5)
        finally:
            await session.cleanup()

    # Fresh store and opposite provider: permitted originals survive; refusals cannot resurrect.
    cold = SDKRead(private_media=False) if legacy else LegacySDK()
    reopened = SessionStore(store.base_dir)
    resumed, _, checkpoint, checks, host = await work_root(tmp_path, monkeypatch, None,
        store=reopened, prepared_host=True, worker_observers=True, provider_setup=cold.setup,
        provider_module="provider-openai" if legacy else "provider-anthropic",
        model="gpt-6.1-sol" if legacy else MODEL)
    try:
        await resumed.coordinator.get("context").set_messages(reopened.load(session.session_id)[0])
        await checkpoint()
        assert await resumed.execute("Use the saved input without rereading files") == "offline completion"
        endpoint = "/v1/responses" if legacy else "/v1/messages"
        sends = [body for route, body in cold.wire if route == endpoint]
        assert sends and all([pixels for _, pixels in cold.pixels(body)] == expected for body in sends)
        assert host.sealed and not checks
        for pixels in originals:
            assert_pixel_free_capture(tmp_path, pixels, tool=False)
    finally:
        await resumed.cleanup()