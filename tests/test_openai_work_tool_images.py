"""Real OpenAI SDK/public-source join; no remote provider inference.

The public provider mount, Core, Work, posthooks, legacy checkpoint/ACK and late
Worker installers are real. Only the HTTP bottom returns scripted Responses.
Finite turns and the real Live inbox/Streaming engine are covered. This is not
Worker.start/process-pipe/installed-host qualification.
"""
import asyncio
import base64
import copy
import hashlib
import json
import socket

import httpx
import openai
import pytest

pytest.importorskip("amplifier_foundation.session.media_hook_bridge")
from amplifier_core import HookResult
from amplifier_web.host import media
from amplifier_web.host.storage import SessionStore
from fixtures.ordinary_work_image_fixture import work_root
from test_narrow_tool_images import picture

MODEL = "gpt-6.1-sol"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refused(*args, **kwargs):
        pytest.fail("Offline SDK join attempted a network operation")
    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    monkeypatch.setattr(socket, "getaddrinfo", refused)


class SDKRead:
    def __init__(self, paths=(), *, revoke_send=False):
        self.paths = list(paths)
        self.wire, self.private = [], []
        self.counted = set()
        self.provider = None
        self.revoke_send = revoke_send

    def setup(self, provider):
        # Inject only the documented SDK client seam after the actual public
        # provider mount, before first use. No provider method or SDK monkeypatch.
        from amplifier_module_provider_openai import OpenAIProvider
        assert type(provider) is OpenAIProvider and provider._client is None
        self.provider = provider
        provider.path = self.paths[0] if self.paths else None
        provider._client = openai.AsyncOpenAI(api_key="offline-fixture", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.respond),
                                         event_hooks={"request": [self.before_http]}))

    @staticmethod
    def outputs(body):
        return [item for item in body.get("input", [])
                if item.get("type") == "function_call_output"]

    @classmethod
    def pixels(cls, body):
        return [(item.get("call_id"), base64.b64decode(block["image_url"].split(",", 1)[1]))
                for item in body.get("input", [])
                for block in (item.get("output", []) if item.get("type") == "function_call_output"
                              else item.get("content", []) if item.get("role") == "user" else [])
                if isinstance(block, dict) and block.get("type") == "input_image"]

    async def before_http(self, request):
        if self.revoke_send and request.url.path == "/v1/responses":
            if self.pixels(json.loads(request.content)):
                self.provider.coordinator.get_capability(media.NAMESPACE).enabled = False

    async def respond(self, request):
        body = json.loads(request.content)
        pixels = self.pixels(body)
        if pixels:
            # The provider binding points at the exact real issuer registration,
            # not a copied/forged private carrier. Do not synthesize registration.
            candidates = [entry.request_ref() for entry in self.provider._media_request_bindings.values()]
            candidates = [value for value in candidates if value is not None and value.messages]
            assert len(candidates) == 1
            private = candidates[0]
            cap = self.provider.coordinator.get_capability(media.NAMESPACE)
            assert cap._store._prepared[id(private)].request is private
            if request.url.path.endswith("/input_tokens"):
                self.counted.add(id(private))
            else:
                assert id(private) in self.counted
                assert any(old["input"] == body["input"] for path, old in self.wire
                           if path.endswith("/input_tokens"))
            self.private.append(private)
        self.wire.append((request.url.path, copy.deepcopy(body)))
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": 20})
        path = self.paths.pop(0) if self.paths else None
        output = ([{"type": "function_call", "id": "fc-offline", "call_id": "call-sdk-read",
                    "name": "read_file", "arguments": json.dumps({"file_path": str(path)}),
                    "status": "completed"}] if path is not None else
                  [{"type": "message", "id": "msg-offline", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "offline completion", "annotations": []}]}])
        # Different canonical calls in an image-then-plaintext recovery turn.
        if path is not None and any(p == "/v1/responses" for p, _ in self.wire[:-1]):
            output[0].update(id="fc-followup", call_id="call-sdk-text")
        return httpx.Response(200, json={"id": "resp-offline", "object": "response", "created_at": 1,
            "model": MODEL, "status": "completed", "output": output,
            "usage": {"input_tokens": 20, "output_tokens": 1, "total_tokens": 21}})

    def image_sends(self):
        return [body for path, body in self.wire if path == "/v1/responses" and self.pixels(body)]


async def sdk_root(tmp_path, monkeypatch, sdk, **kwargs):
    return await work_root(tmp_path, monkeypatch, None, prepared_host=True,
                           worker_observers=True, provider_setup=sdk.setup, **kwargs)


def assert_pixel_free_capture(root, original, *, tool):
    """Read the real Context Intelligence hook's on-disk capture, not telemetry."""
    [path] = list((root / "shared" / "projects").rglob("events.jsonl"))
    text = path.read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    names = {row["event"] for row in rows}
    assert {"llm:request", "llm:response"} <= names
    if tool:
        assert "tool:post" in names
    assert base64.b64encode(original).decode() not in text
    assert "data:image/" not in text
    def inspect(value):
        if isinstance(value, dict):
            if value.get("type") in ("image", "input_image"):
                assert value == {"type": value["type"], "media": "[image omitted from diagnostics]"}
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(rows)
    print("SDK_IMAGE_CAPTURE " + json.dumps({
        "path": str(path), "events": len(rows), "pixel_free": True,
        "original_sha256": hashlib.sha256(original).hexdigest(),
    }, sort_keys=True))


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
async def test_actual_openai_work_first_read_ack_deleted_source_warm_cold(tmp_path, monkeypatch, format):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, original = picture(workspace, format)
    sdk = SDKRead([path])
    session, store, checkpoint, checks, host = await sdk_root(tmp_path, monkeypatch, sdk)
    try:
        assert await session.execute("Read the authorized local image") == "offline completion"
        assert host.sealed and checks == [("read_file", {"file_path": str(path)})]
        assert SDKRead.pixels(sdk.image_sends()[-1]) == [("call-sdk-read", original)]
        assert base64.b64encode(original).decode() not in json.dumps(host.emitted)
        binding = session.coordinator.get_capability("web.media.construction")
        assert binding.bridge.validate_receipt(binding.bridge.graph_receipt)
        rows = await checkpoint()
        assert any(media.NAMESPACE in (row.get("metadata") or {}) for row in rows)
        assert all(row["role"] == "tool" and row["tool_call_id"] == "call-sdk-read"
                   for row in rows if media.NAMESPACE in (row.get("metadata") or {}))
        assert base64.b64encode(original).decode() not in json.dumps(rows)
        before = copy.deepcopy(rows)
        await session.coordinator.get("context").set_messages(rows)
        assert await session.coordinator.get("context").get_messages() == before
        path.unlink()
        await session.execute("Use the retained image without reading the deleted source")
        assert checks == [("read_file", {"file_path": str(path)})]
        assert SDKRead.pixels(sdk.image_sends()[-1]) == [("call-sdk-read", original)]
        assert all(request.messages == [] for request in sdk.private)
        assert not session.coordinator.get_capability("web.media.request_only").requests
        assert_pixel_free_capture(tmp_path, original, tool=True)
    finally:
        await session.cleanup()
    reopened = SessionStore(store.base_dir)
    loaded, _ = reopened.load("ordinary-work-media")
    cold = SDKRead()
    resumed, _, checkpoint, checks, host = await sdk_root(tmp_path, monkeypatch, cold, store=reopened)
    try:
        await resumed.coordinator.get("context").set_messages(loaded)
        await checkpoint()
        await resumed.execute("Use the retained image after reopening")
        assert host.sealed and checks == []
        assert SDKRead.pixels(cold.image_sends()[-1]) == [("call-sdk-read", original)]
        assert all(request.messages == [] for request in cold.private)
        # A newly unsupported actual route must fit an unmarked warning before
        # any pixel read, not a marked F warning the provider must reject.
        cold.provider._client.base_url = "https://proxy.invalid/v1"
        monkeypatch.setattr(resumed.coordinator.get_capability("web.media.store"), "resolve_ref",
                            lambda *a, **k: pytest.fail("Unsupported route hydrated stored pixels"))
        start = len(cold.wire)
        await resumed.execute("Continue with text on the unqualified route")
        assert all(not SDKRead.pixels(body) for _, body in cold.wire[start:])
        assert "Image unavailable" in json.dumps(cold.wire[start:])
        bridge = resumed.coordinator.get_capability("web.media.construction").bridge
        resumed.config["agents"]["computer-use:computer-operator"]["provider_preferences"] = [
            {"provider": "openai", "model": "latest"}]
        assert not bridge.validate_receipt(bridge.graph_receipt)  # Whole-config F witness still fences it.
    finally:
        await resumed.cleanup()


@pytest.mark.parametrize("denial", ["post-null", "read-policy", "animated-gif"])
async def test_actual_openai_work_refusal_has_no_pixels_and_plaintext_recovers(tmp_path, monkeypatch, denial):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, _ = picture(workspace)
    if denial == "animated-gif":
        from PIL import Image
        path = workspace / "animated.gif"
        Image.new("RGB", (2, 2), "red").save(path, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (2, 2), "blue")], duration=100, loop=0)
    text = workspace / "plaintext"
    text.write_text("Plaintext after refused image")
    sdk = SDKRead([path, text])
    async def post(event, data):
        return (HookResult(action="modify", data={"result": None})
                if denial == "post-null" and data["tool_call_id"] == "call-sdk-read" else HookResult())
    session, _, checkpoint, _, host = await sdk_root(tmp_path, monkeypatch, sdk, post=post)
    try:
        if denial == "read-policy":
            from amplifier_web.read_policy import mounted_read_file
            mounted_read_file(session.coordinator).denied_read_paths = [str(path)]
        assert await session.execute("Read image then plaintext") == "offline completion"
        rows = await checkpoint()
        assert host.sealed and not sdk.image_sends()
        assert "Plaintext after refused image" in json.dumps(rows)
        assert not any(media.NAMESPACE in (row.get("metadata") or {}) for row in rows)
    finally:
        await session.cleanup()


async def test_actual_openai_work_revocation_after_count_prevents_physical_image_send(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, _ = picture(workspace)
    sdk = SDKRead([path], revoke_send=True)
    session, _, _, _, _ = await sdk_root(tmp_path, monkeypatch, sdk)
    try:
        # Native Core forwards the actual loop error as an execution RuntimeError.
        with pytest.raises(RuntimeError, match="InvalidRequestError: Private media request"):
            await session.execute("Read the authorized local image")
        assert any(SDKRead.pixels(body) for url, body in sdk.wire if url.endswith("/input_tokens"))
        assert not sdk.image_sends()
        assert all(request.messages == [] for request in sdk.private)
        assert not session.coordinator.get_capability("web.media.request_only").requests
    finally:
        await session.cleanup()


@pytest.mark.parametrize("origin", ["tool", "composer"])
@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
async def test_actual_live_work_sdk_image_references_and_capture(tmp_path, monkeypatch, origin, format):
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator
    from amplifier_module_loop_live.runtime import Input
    from amplifier_module_loop_streaming import StreamingOrchestrator
    from amplifier_web.attachments import file_path, save

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, original = picture(workspace, format)
    sdk = SDKRead([path] if origin == "tool" else [])
    session, store, checkpoint, checks, host = await sdk_root(tmp_path, monkeypatch, sdk, live=True)
    coordinator = session.coordinator
    loop = coordinator.get("orchestrator")
    assert type(loop) is BundleLiveOrchestrator and isinstance(loop, StreamingOrchestrator)
    runtime = coordinator.get_capability("live.runtime")
    uploads = ()
    if origin == "composer":
        upload = save(tmp_path / "app", path.name, base64.b64encode(original).decode())
        uploads = ({"id": upload["id"]},)
    command = Input("user", "Inspect the authorized image", attachments=uploads)
    execution = asyncio.create_task(session.execute(""))
    try:
        await runtime.submit(command)
        finished = await runtime.wait_for(lambda row:
            row["type"] in {"generation.finished", "generation.failed"}
            and command.id in row.get("input_ids", []), timeout=15)
        assert finished["type"] == "generation.finished", finished
        assert finished["text"] == "offline completion" and host.sealed
        expected = [("call-sdk-read" if origin == "tool" else None, original)]
        assert SDKRead.pixels(sdk.image_sends()[-1]) == expected
        rows = await checkpoint()
        [message] = [row for row in rows if media.NAMESPACE in (row.get("metadata") or {})]
        [ref] = message["metadata"][media.NAMESPACE]["refs"]
        assert message["role"] == ("tool" if origin == "tool" else "user")
        assert ref["provenance"]["kind"] == ("tool" if origin == "tool" else "upload")
        assert ref["sha256"] == hashlib.sha256(original).hexdigest()
        assert checks == ([("read_file", {"file_path": str(path)})] if origin == "tool" else [])
        assert base64.b64encode(original).decode() not in json.dumps(rows)
        assert base64.b64encode(original).decode() not in json.dumps(runtime.events)
        assert base64.b64encode(original).decode() not in json.dumps(host.emitted)
        # No replay of either producer is needed for the next real Live turn.
        path.unlink()
        if uploads:
            file_path(tmp_path / "app", upload["id"])[0].unlink()
        followup = Input("user", "Keep using the retained image without reading its source")
        await runtime.submit(followup)
        finished = await runtime.wait_for(lambda row:
            row["type"] in {"generation.finished", "generation.failed"}
            and followup.id in row.get("input_ids", []), timeout=15)
        assert finished["type"] == "generation.finished", finished
        assert SDKRead.pixels(sdk.image_sends()[-1]) == expected
        assert all(request.messages == [] for request in sdk.private)
        assert_pixel_free_capture(tmp_path, original, tool=origin == "tool")
    finally:
        if not execution.done():
            await runtime.submit(Input("stop"))
        try:
            await asyncio.wait_for(execution, 5)
        finally:
            await session.cleanup()
    if origin == "composer":
        # Fresh coordinator/checkpoint acknowledgement, not a live receipt reused
        # after the original upload has been removed.
        reopened = SessionStore(store.base_dir)
        loaded, _ = reopened.load(session.session_id)
        cold = SDKRead()
        resumed, _, checkpoint, checks, _ = await sdk_root(tmp_path, monkeypatch, cold, store=reopened)
        try:
            await resumed.coordinator.get("context").set_messages(loaded)
            await checkpoint()
            await resumed.execute("Use the saved upload without rereading")
            assert not checks
            assert SDKRead.pixels(cold.image_sends()[-1]) == expected
            assert_pixel_free_capture(tmp_path, original, tool=False)
        finally:
            await resumed.cleanup()


async def test_static_gif_is_retained_but_unsupported_openai_route_gets_no_pixels(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, original = picture(workspace, "GIF")
    sdk = SDKRead([path])
    session, _, checkpoint, _, host = await sdk_root(tmp_path, monkeypatch, sdk)
    try:
        assert await session.execute("Inspect the static GIF") == "offline completion"
        rows = await checkpoint()
        [message] = [row for row in rows if media.NAMESPACE in (row.get("metadata") or {})]
        [ref] = message["metadata"][media.NAMESPACE]["refs"]
        assert ref["mime"] == "image/gif" and host.sealed
        with session.coordinator.get_capability("web.media.store").resolve_ref(ref) as saved:
            assert saved.read() == original
        assert not sdk.image_sends() and "Image unavailable" in json.dumps(sdk.wire)
        assert_pixel_free_capture(tmp_path, original, tool=True)
    finally:
        await session.cleanup()


@pytest.mark.parametrize("field,value", [
    ("instruction", "changed"), ("tools", []), ("model_role", "other"),
    ("provider_preferences", [{"provider": "openai", "model": "latest"}]),
])
async def test_work_media_graph_refuses_changed_agent_configuration(tmp_path, monkeypatch, field, value):
    sdk = SDKRead()
    session, _, _, _, _ = await sdk_root(tmp_path, monkeypatch, sdk)
    try:
        await session.execute("Seal the ordinary Work construction")
        bridge = session.coordinator.get_capability("web.media.construction").bridge
        assert bridge.validate_receipt(bridge.graph_receipt)
        session.config["agents"]["computer-use:computer-operator"][field] = value
        assert not bridge.validate_receipt(bridge.graph_receipt)
        # Invalid construction is terminal; restoring dictionaries must not be
        # used to resurrect the original receipt for later image sends.
    finally:
        await session.cleanup()
