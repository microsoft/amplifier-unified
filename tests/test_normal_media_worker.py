"""Maintained ordinary-Worker source probe; real integration is explicit opt-in."""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests/fixtures/normal_media_worker_probe.py"


@pytest.fixture
def probe():
    # Importing the probe does not run startup, alter environment or load vendor
    # modules. Integration dependencies are required only by the opt-in node.
    return runpy.run_path(str(PROBE))


def test_normal_worker_fixture_never_injects_graph_or_canonical_owners():
    tree = ast.parse(PROBE.read_text())
    workers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name) and node.func.id == "Worker"]
    assert len(workers) == 2
    assert all(not call.args and not call.keywords for call in workers)
    forbidden = {"NamedMediaGraph", "media_selection", "select_from_declaration", "mint_owner",
                 "bind_media", "acknowledge_media", "create_new", "force_enabled"}
    calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
             for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, (ast.Name, ast.Attribute))}
    assert not calls & forbidden
    # Actual JSON publication stays in the production source catalog, not a
    # fixture callback presented as a trusted dependency.
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "object" and len(node.args) > 1
                   and isinstance(node.args[1], ast.Constant) and node.args[1].value == "publish"
                   for node in ast.walk(tree))


def test_normal_worker_authored_bundle_is_narrow_and_keeps_explicit_provider_choice(probe, tmp_path, monkeypatch):
    # Declaration-shape unit check needs no optional module installation. The
    # integration node uses real origins and the untouched packaged catalog.
    monkeypatch.setitem(probe["authored_bundle"].__globals__, "origin",
                        lambda package: tmp_path / package / "__init__.py")
    bundle = probe["authored_bundle"](tmp_path)
    assert set(bundle) == {"bundle", "session", "providers", "hooks"}
    assert set(bundle["session"]) == {"orchestrator", "context"}
    assert bundle["session"]["orchestrator"]["module"] == "loop-live"
    assert bundle["session"]["context"]["module"] == "context-simple"
    assert len(bundle["providers"]) == len(bundle["hooks"]) == 1
    provider = bundle["providers"][0]
    assert provider["module"] == "provider-openai"
    assert provider["config"] == {"api_key": probe["DUMMY_KEY"], "default_model": "gpt-6.1-sol",
                                  "base_url": "https://api.openai.com/v1", "use_streaming": False,
                                  "max_output_tokens": 64}
    capture = bundle["hooks"][0]
    assert capture["module"] == "hook-context-intelligence"
    assert capture["config"] == {"destinations": {}, "context_intelligence_server_url": "",
                                 "context_intelligence_api_key": "", "base_path": str(tmp_path / "capture")}


def test_normal_worker_wire_png_boundary_rejects_missing_multiple_or_changed_type(probe):
    pixels = b"unit-only-png-sentinel"
    image = {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(pixels).decode()}
    assert probe["private_png"]({"input": [{"content": [image]}]}) == pixels
    for blocks in ([], [image, image], [{**image, "image_url": image["image_url"].replace("image/png", "image/jpeg")}]):
        with pytest.raises(AssertionError):
            probe["private_png"]({"input": [{"content": blocks}]})


def test_normal_worker_public_evidence_rejects_pixels(probe):
    pixels = b"unit-only-private-sentinel"
    probe["no_pixels"]({"ref": "safe-owned-reference"}, pixels)
    for value in ({"data": base64.b64encode(pixels).decode()}, {"type": "image"},
                  {"text": "data:image/png;base64,not-retained"}):
        with pytest.raises(AssertionError):
            probe["no_pixels"](value, pixels)


async def test_normal_worker_bridge_uses_current_finite_preparation_protocol(probe):
    from amplifier_web.preparation_context import OPERATIONS
    bridge = probe["HostBridge"](b"unit-private-sentinel")
    future = asyncio.get_running_loop().create_future()
    bridge.workers.append(SimpleNamespace(session=SimpleNamespace(session_id="normal-media-worker"),
                                          bridges={"request": future}))
    event = {"op": "bridge", "id": "request", "operation": "context.prepare",
             "args": {"_contextInputs": ["one"]}}
    # Exercise the actual binary JSON protocol sink, not an event hook grant.
    payload = (json.dumps(event) + "\n").encode()
    assert bridge.write(payload) == len(payload)
    bridge.flush()
    result = await future
    assert set(result) == set(OPERATIONS)
    assert result["context.manifest"]["value"] == {"surfaces": [], "inputIds": ["one"]}
    assert result["coordination.current"]["value"]["sessionId"] == "normal-media-worker"
    assert result["message.reactions.context"] == {"available": False}
    assert result["memory.context"] == {"available": True, "value": {"items": []}}


async def test_normal_worker_offline_transport_denies_undeclared_route(probe):
    import httpx
    transport = probe["OfflineResponses"](b"unit-only-private-sentinel")
    # No SDK method, provider or scope is replaced for this negative boundary.
    with transport.installed():
        async with httpx.AsyncClient() as client:
            with pytest.raises(AssertionError):
                await client.post("https://unselected.invalid/v1/responses", json={})
    assert len(transport.failures) == 1 and not transport.records


def test_actual_normal_worker_openai_media_count_send_t2_ack_retire_and_resume(tmp_path):
    """No installation fallback: use the caller's already prepared source runtime."""
    if os.environ.get("NORMAL_MEDIA_WORKER") != "1":
        pytest.skip("set NORMAL_MEDIA_WORKER=1 and UNIFIED_RUNTIME_PYTHON for the offline source proof")
    if sys.platform != "linux":
        pytest.skip("Prospective T2 source lifetime is qualified on Linux only")
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    assert python, "UNIFIED_RUNTIME_PYTHON must select an already prepared runtime; this test never installs"
    result = subprocess.run([python, str(PROBE), str(tmp_path)], cwd=ROOT, text=True,
                            capture_output=True, timeout=120,
                            env={**os.environ, "AMPLIFIER_RUNTIME_IMMUTABLE": "1", "UV_OFFLINE": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    for key in ("ordinary_worker_constructor", "actual_vendor_provider_and_sdk", "offline_httpx_transport",
                "count_send_same_private_request", "canonical_t2_ack", "same_owner_two_turns",
                "idle_physical_custody", "retired_safely", "fresh_resume_without_replay",
                "private_requests_cleared", "canonical_and_host_events_no_pixels"):
        assert evidence[key] is True, key
    assert evidence["production_catalog_recipe"] == "unified-openai-static-sol-local-capture-v1"
    assert sum(row["phase"] == "send" for row in evidence["records"]) == 3
    assert sum(row["phase"] == "count" for row in evidence["records"]) >= 3
    assert evidence["source"]["mode"] == "source-only"
    assert evidence["source"]["installed_package_parity"] is False
    assert not evidence["source"]["catalog"]["mismatches"]
    assert "amplifier_module_provider_openai" in evidence["source"]["imports"]
    assert "context_intelligence" in evidence["source"]["imports"]
