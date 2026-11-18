"""Exercise native image transport and recording together, without network or keys.

Run with AMPLIFIER_TEST_GEMINI_WIRE=1 and a Gemini provider revision supporting
native function-response images and sanitized error diagnostics.
"""

import base64
import json
import logging
import os
import socket
import traceback
from types import SimpleNamespace

import httpx
import pytest
from amplifier_core import HookResult, ModuleCoordinator
from amplifier_core.llm_errors import LLMError
from amplifier_core.message_models import (
    ChatRequest,
    ImageBlock,
    Message,
    TextBlock,
    ToolCallBlock,
)

from amplifier_web.execution_events import CURRENT_CALL
from amplifier_web.provider_payloads import DIRECTORY
from amplifier_web.provider_recording import install_request_redaction


PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAA"
    "MCAO+jK1kAAAAASUVORK5CYII="
)


@pytest.mark.skipif(
    os.environ.get("AMPLIFIER_TEST_GEMINI_WIRE") != "1",
    reason="requires a compatible Gemini provider; set AMPLIFIER_TEST_GEMINI_WIRE=1",
)
@pytest.mark.parametrize("status", [200, 400], ids=["success", "rejection"])
async def test_gemini_native_image_wire_and_recording(tmp_path, monkeypatch, caplog, status):
    import amplifier_module_provider_gemini as gemini
    from amplifier_module_hook_context_intelligence.handlers import logging_handler
    from google import genai

    def deny_network(*args, **kwargs):
        pytest.fail("The provider must use the offline HTTP transport")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    for env in ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE"):
        monkeypatch.delenv(env, raising=False)
    caplog.set_level(logging.DEBUG, logger=gemini.__name__)

    coordinator = ModuleCoordinator()
    install_request_redaction(coordinator, directory=tmp_path / "child")
    resolver = SimpleNamespace(
        working_dir=str(tmp_path), session_dir=lambda session_id: tmp_path / session_id
    )
    recorder = logging_handler.LoggingHandler(resolver)
    provider_events = []

    async def bind_session_and_observe(event, data):
        # Bare provider emissions don't have the host's session envelope.
        provider_events.append(json.dumps(data, default=str))
        return HookResult(action="modify", data={**data, "session_id": "child"})

    for event in ("llm:request", "llm:response", "llm:error"):
        coordinator.hooks.register(event, bind_session_and_observe, priority=-200,
                                   name="bind-test-session")
        coordinator.hooks.register(event, recorder, priority=100, name="LoggingHandler")

    request = ChatRequest(messages=[
        Message(role="assistant", content=[ToolCallBlock(
            id="call-1", name="read_file", input={"path": "fixture.png"}
        )]),
        Message(role="tool", name="read_file", tool_call_id="call-1", content=[
            TextBlock(text="tool text before"),
            ImageBlock(source={"type": "base64", "media_type": "image/png", "data": PNG_BASE64}),
            TextBlock(text="tool text after"),
        ]),
    ])
    original = request.model_dump()
    png = base64.b64decode(PNG_BASE64, validate=True)
    wire_base64 = base64.urlsafe_b64encode(png).decode()
    received = []

    def receive(http_request):
        assert http_request.method == "POST"
        assert http_request.url.host == "offline.invalid"
        assert http_request.url.path.endswith(":generateContent")
        received.append(json.loads(http_request.content))
        if status == 400:
            return httpx.Response(400, json={"error": {
                "code": 400, "status": "INVALID_ARGUMENT",
                "message": f"rejected image data:image/png;base64,{wire_base64}",
            }})
        return httpx.Response(200, json={"candidates": [{
            "content": {"role": "model", "parts": [{"text": "fixture reply"}]},
            "finishReason": "STOP",
        }]})

    provider = gemini.GeminiProvider(
        api_key="offline-placeholder", coordinator=coordinator,
        config={"default_model": "gemini-3.7-flash", "use_streaming": False,
                "max_retries": 0, "raw": True},
    )
    client = genai.Client(api_key="offline-placeholder", http_options=genai.types.HttpOptions(
        base_url="https://offline.invalid/",
        async_client_args={"transport": httpx.MockTransport(receive)},
    ))
    provider._client = provider._count_route_client = client
    provider._client_uses_canonical_developer_route = True
    token = CURRENT_CALL.set("llm:offline-wire")
    error_diagnostic = ""
    try:
        if status == 400:
            with pytest.raises(LLMError) as raised:
                await provider.complete(request)
            assert raised.value.status_code == 400
            error_diagnostic = "".join(traceback.format_exception(raised.value))
        else:
            response = await provider.complete(request)
            assert any(block.text == "fixture reply" for block in response.content)
    finally:
        CURRENT_CALL.reset(token)
        await client.aio.aclose()
        client.close()

    assert request.model_dump() == original
    (body,) = received
    call, result = body["contents"]
    function_call = call["parts"][0]["functionCall"]
    function_response = result["parts"][0]["functionResponse"]
    assert function_call["id"] == function_response["id"] == "call-1"
    assert function_call["name"] == function_response["name"] == "read_file"
    (attachment,) = function_response["parts"]
    inline_data = attachment["inline_data"]
    assert inline_data["mime_type"] == "image/png"
    assert base64.urlsafe_b64decode(inline_data["data"]) == png

    journal = (tmp_path / "child" / "events.jsonl").read_text(encoding="utf-8")
    request_record, response_record = map(json.loads, journal.splitlines())
    assert [request_record["event"], response_record["event"]] == ["llm:request", "llm:response"]
    assert all(record["data"]["unified_call_id"] == "llm:offline-wire"
               for record in (request_record, response_record))
    request_data = request_record["data"]
    assert request_data["request_capture"]["redacted"] is True
    assert "tool text before" in journal and "tool text after" in journal
    response_data = response_record["data"]
    if status == 400:
        assert response_data["status"] == "error" and response_data["error"]
    else:
        assert response_data["status"] == "ok"
        assert response_data["request_capture"]["redacted"] is True
        assert "fixture reply" in journal
    assert not (tmp_path / "child" / DIRECTORY).exists()
    assert len(provider_events) == 2
    for diagnostics in (journal, *provider_events, caplog.text, error_diagnostic):
        assert PNG_BASE64.rstrip("=") not in diagnostics
        assert wire_base64.rstrip("=") not in diagnostics
        assert "data:image/png;base64," not in diagnostics
        assert repr(png)[2:-1] not in diagnostics
