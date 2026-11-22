"""Work's real checkpoint, source policy, tool loop and provider, with a scripted HTTP bottom."""

import base64
import hashlib
import io
import json
import os

import httpx
import pytest
from PIL import Image

from amplifier_web.host import media
from test_tool_image_consumer_join import actual_root


def png(color):
    out = io.BytesIO()
    Image.new("RGB", (512, 256), color).save(out, "PNG")
    return out.getvalue()


@pytest.mark.skipif(os.name != "posix", reason="Work binds durable media on POSIX")
@pytest.mark.asyncio
async def test_generated_preview_and_reference_edit_in_one_work_turn(
    tmp_path, monkeypatch
):
    artwork = pytest.importorskip("amplifier_module_provider_openai_chatgpt.artwork")
    from amplifier_module_provider_openai_chatgpt.provider import ChatGPTProvider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    tmp_path = tmp_path.resolve()
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "home"))
    first, second = png("red"), png("blue")
    sends = []

    def transport(request):
        assert request.url.host == "chatgpt.com"
        payload = json.loads(request.content)
        n = len(sends)
        generated = any(tool["type"] == "image_generation" for tool in payload["tools"])
        sends.append("art" if generated else "chat")
        if generated:
            if n == 3:
                assert payload["input"][0]["content"][1]["image_url"].endswith(
                    base64.b64encode(first).decode()
                )
            item = {
                "type": "image_generation_call",
                "result": base64.b64encode(first if n == 1 else second).decode(),
            }
        else:
            if n:
                output = [
                    i
                    for i in payload["input"]
                    if i.get("type") == "function_call_output"
                ][-1]["output"]
                raw = base64.b64decode(output[1]["image_url"].split(",")[1])
                with Image.open(io.BytesIO(raw)) as preview:
                    assert preview.convert("RGB").getpixel((0, 0)) == (
                        (255, 0, 0) if n == 2 else (0, 0, 255)
                    )
            if n in (0, 2):
                args = (
                    {"prompt": "create"}
                    if not n
                    else {
                        "prompt": "revise",
                        "reference": hashlib.sha256(first).hexdigest(),
                    }
                )
                item = {
                    "type": "function_call",
                    "id": f"fc_{n}",
                    "call_id": f"call_{n}",
                    "name": "codex_artwork",
                    "arguments": json.dumps(args),
                }
            else:
                item = {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Both previews inspected."}
                    ],
                }
        events = [
            {"type": "response.output_item.done", "item": item},
            {
                "type": "response.completed",
                "response": {"usage": {"input_tokens": 20, "output_tokens": 15}},
            },
        ]
        return httpx.Response(
            200, text="".join("data: " + json.dumps(event) + "\n\n" for event in events)
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(transport), **kw),
    )
    p = ChatGPTProvider(
        {"auth_mode": "chatgpt_codex", "default_model": "gpt-6.2-sol"},
        tokens={
            "account_id": "fixture",
            "access_token": "fixture",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )
    p.path = tmp_path
    tool = artwork.CodexArtwork(p, tmp_path / "art")
    session, _, checkpoint, _, _, _ = await actual_root(tmp_path, p)
    p._coordinator = session.coordinator
    try:
        await media.registration_coordinator(session.coordinator).mount(
            "tools", tool, name=tool.name
        )
        assert await session.execute("Draw and revise") == "Both previews inspected."
        assert sends == ["chat", "art", "chat", "art", "chat"]
        rows = await checkpoint()
        refs = [
            ref
            for row in rows
            for ref in ((row.get("metadata") or {}).get(media.NAMESPACE) or {}).get(
                "refs", []
            )
        ]
        assert len(refs) == 2
        policy = session.coordinator.get_capability("web.media.source_policy")
        assert all(policy.validate(ref, purpose="model") for ref in refs)
        tool.directory = tmp_path / "another-workspace"
        assert not any(policy.validate(ref, purpose="model") for ref in refs)
    finally:
        await session.cleanup()


@pytest.mark.parametrize(
    "damage", ["none", "path", "preview", "wrong-name", "duplicate", "foreign"]
)
def test_checkpoint_pairs_generated_receipt_with_issued_preview(tmp_path, damage):
    from types import SimpleNamespace
    from amplifier_web.read_policy import read_file_call_paths

    artifact_id, preview_id = "a" * 64, "b" * 64
    expected = str(tmp_path / (artifact_id + ".png"))
    ref = {
        "sha256": preview_id,
        "provenance": {
            "kind": "tool",
            "session_id": "another" if damage == "foreign" else "session",
            "id": "call",
        },
    }
    call = {"id": "call", "name": "codex_artwork", "arguments": {"prompt": "draw"}}
    receipt = {
        "artifact_id": artifact_id,
        "path": "/elsewhere" if damage == "path" else expected,
        "preview_sha256": "c" * 64 if damage == "preview" else preview_id,
    }
    rows = [
        {
            "role": "assistant",
            "tool_calls": [call, call] if damage == "duplicate" else [call],
        },
        {
            "role": "tool",
            "name": "other" if damage == "wrong-name" else "codex_artwork",
            "tool_call_id": "call",
            "content": json.dumps(receipt),
            "metadata": {media.NAMESPACE: {"refs": [ref]}},
        },
    ]
    assert read_file_call_paths(rows, "session") == {}
    assert read_file_call_paths(
        rows, "session", artwork=SimpleNamespace(directory=tmp_path)
    ) == ({"call": expected} if damage == "none" else {})
