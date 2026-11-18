"""Finite real OpenAI SDK/F42 public-source join; no remote provider inference.

The public provider mount, Core, Work, posthooks, legacy checkpoint/ACK and late
Worker installers are real. Only the HTTP bottom returns scripted Responses.
This is not Worker.start/LIVE-manager/installed-host qualification.
"""
import base64
import copy
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
        return [(item["call_id"], base64.b64decode(block["image_url"].split(",", 1)[1]))
                for item in cls.outputs(body) for block in item.get("output", [])
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


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
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
        _, _, selection = session.coordinator.get_capability("web.media.construction")
        assert selection.accepts_plan(session.config)
        assert all("provider_preferences" in row for row in session.config["agents"].values())
        for field, value in (("instruction", "changed"), ("tools", []), ("model_role", "other")):
            changed = copy.deepcopy(session.config)
            changed["agents"]["computer-use:computer-operator"][field] = value
            assert not selection.accepts_plan(changed)
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
        bridge, _, selection = resumed.coordinator.get_capability("web.media.construction")
        resumed.config["agents"]["computer-use:computer-operator"]["provider_preferences"][0]["model"] = "latest"
        assert selection.accepts_plan(resumed.config)  # This field is not root execution authority.
        assert not bridge.validate_receipt(bridge.graph_receipt)  # Whole-config F witness still fences it.
    finally:
        await resumed.cleanup()


@pytest.mark.parametrize("denial", ["post-null", "read-policy", "gif"])
async def test_actual_openai_work_refusal_has_no_pixels_and_plaintext_recovers(tmp_path, monkeypatch, denial):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path, _ = picture(workspace, "GIF" if denial == "gif" else "PNG")
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
