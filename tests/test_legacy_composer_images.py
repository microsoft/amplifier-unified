"""Legacy composer wire and real on-disk Work capture; HTTP bottom only is fake."""
import asyncio
import base64
import copy
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