"""Opt-in integration proof for the actual isolated warm-worker lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unsupported-authored-loop", "unavailable-tentative-root", "saved-override"])
async def test_media_preflight_cannot_preempt_authoritative_saved_configuration(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.host import session as host
    from amplifier_web import runtime_controls
    override = tmp_path / "configuration.json"
    if failure == "saved-override":
        override.write_text("{}")
    monkeypatch.setattr(runtime_controls, "override_path", lambda _: override)
    monkeypatch.setattr(host, "load_config", lambda *args, **kwargs: SimpleNamespace(active_bundle="tentative"))
    monkeypatch.setattr("amplifier_web.host.config.load_config", host.load_config)
    monkeypatch.setattr("amplifier_web.host.config.prepare_registry", lambda _: None)
    monkeypatch.setattr("amplifier_web.host.bundle_paths.canonical_bundle_reference", lambda _, value: value)
    called = []
    async def load(*args, **kwargs):
        called.append(True)
        if failure == "unavailable-tentative-root":
            raise FileNotFoundError("tentative declaration no longer exists")
        if failure == "saved-override":
            pytest.fail("A saved override must reach manager resolution without tentative media loading")
        return None, SimpleNamespace(to_mount_plan=lambda: {
            "session": {"orchestrator": {"module": "loop-basic"}}}), "tentative"
    monkeypatch.setattr(host, "load_root_bundle", load)
    selected, root = await Worker().media_preflight({"id": "saved", "workspace": str(tmp_path)}, tmp_path)
    assert selected is root is None
    assert bool(called) is (failure != "saved-override")


@pytest.mark.asyncio
async def test_idle_retirement_joins_actual_runtime_close_before_shutdown(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_web import runtime_worker
    worker = runtime_worker.Worker(canonical_bootstrap=False)
    closed = asyncio.Event()
    async def execute():
        await closed.wait()
    worker.execution = asyncio.create_task(execute())
    async def submit(command):
        assert command.kind == "stop" and command.target == "finish"
        assert worker.retiring and not worker.shutdown.is_set()
        await asyncio.sleep(0)
        closed.set()
    worker.runtime = SimpleNamespace(submit=submit)
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(get_capability=lambda _: worker.runtime))
    worker.checkpoint_idle = AsyncMock(return_value=True)
    monkeypatch.setattr(runtime_worker, "publish", lambda _: None)
    await worker.command({"op": "retire", "id": "retire"})
    assert worker.execution.done() and closed.is_set() and worker.shutdown.is_set()


@pytest.mark.asyncio
async def test_retirement_reply_precedes_concurrent_cleanup_command_cancellation(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_web import runtime_worker
    worker = runtime_worker.Worker(canonical_bootstrap=False)
    closed = asyncio.Event()
    async def execution():
        await closed.wait()
    worker.execution = asyncio.create_task(execution())
    worker.execution.add_done_callback(worker.executed)
    async def submit(command):
        closed.set()
    worker.runtime = SimpleNamespace(submit=submit)
    worker.session = SimpleNamespace(
        coordinator=SimpleNamespace(get_capability=lambda _: worker.runtime), cleanup=AsyncMock())
    worker.checkpoint_idle = AsyncMock(return_value=True)
    worker.checkpoint_terminal_outcome = AsyncMock()
    async def read():
        await asyncio.sleep(100)
    worker.read = read
    worker.computer_episodes = SimpleNamespace(fence=lambda: None, close=AsyncMock(return_value={"settled": True}))
    events = []
    monkeypatch.setattr(runtime_worker, "publish", events.append)
    cleanup = asyncio.create_task(worker._run())
    retirement = asyncio.create_task(worker.command({"op": "retire", "id": "ordered-retire"}))
    worker.tasks.add(retirement)
    await retirement
    await cleanup
    assert not retirement.cancelled()
    assert any(event.get("id") == "ordered-retire" and event.get("result") == {"retired": True}
               for event in events)
    assert worker._cleanup_settled


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Prospective lifetime is qualified on Linux")
async def test_cancelled_worker_run_retains_physical_binding_until_cleanup_owner_settles(tmp_path):
    import asyncio
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_web.runtime_worker import Worker
    binding = VersionedSessionBinding.create_new(
        tmp_path / "owned-worker", session_id="owned-worker", operation_id="init", admit=lambda: None)
    worker = Worker(canonical_binding=binding, canonical_resume=False)
    entered, release = asyncio.Event(), asyncio.Event()
    async def retained_cleanup():
        entered.set()
        await release.wait()
        binding.check()
        worker._cleanup_settled = True
    worker._run = retained_cleanup
    task = asyncio.create_task(worker.run())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    binding.check()
    with pytest.raises(ValueError):
        VersionedSessionBinding.resume(binding.directory, session_id=binding.session_id,
                                        expected=binding.expected)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ValueError, match="closed"):
        binding.check()
    reopened = VersionedSessionBinding.resume(binding.directory, session_id=binding.session_id,
                                              expected=binding.expected)
    await reopened.aclose()

def test_warm_media_plan_is_derived_from_fixed_declarations(tmp_path):
    probe = runpy.run_path(str(ROOT / "tests/fixtures/warm_worker_probe.py"))
    plan = probe["expected_media_plan"](
        tmp_path, tmp_path / "context_package/__init__.py",
        tmp_path / "capture_package/__init__.py")
    assert plan["providers"] == [
        {"module": "provider-fixture", "source": str(tmp_path / "provider"), "config": {}}]
    assert plan["root_session_id"] == "warm-fixture"
    assert plan["project_dir"] == str(tmp_path / "workspace")
    assert plan["session"]["orchestrator"]["config"] == {
        "configured_bundle": True, "background_delegate": True}
    assert plan["hooks"][0]["config"]["context_intelligence_api_key"] == ""
    assert plan["hooks"][0]["config"]["additional_events"] == ["session:config", "mentions:resolved"]


def test_warm_media_provider_retains_only_private_digest_records(tmp_path, monkeypatch):
    import base64
    import hashlib
    from amplifier_core.message_models import ChatRequest, ImageBlock, Message

    probe = runpy.run_path(str(ROOT / "tests/fixtures/warm_worker_probe.py"))
    namespace = {"__name__": "warm_media_provider_test"}
    exec(compile(probe["MEDIA_PROVIDER"], "<private-fixture-provider>", "exec"), namespace)
    pixels = b"test-only-private-png-sentinel"
    digest = hashlib.sha256(pixels).hexdigest()
    path = tmp_path / "private-provider.jsonl"
    monkeypatch.setenv("WARM_MEDIA_SHA256", digest)
    monkeypatch.setenv("WARM_MEDIA_RECORD", str(path))
    provider = namespace["FixtureProvider"]()
    request = ChatRequest(messages=[Message(role="user", content=[ImageBlock(source={
        "type": "base64", "media_type": "image/png", "data": base64.b64encode(pixels).decode()})])])
    provider.record("count", request)
    provider.record("send", request)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["phase"] for row in rows] == ["count", "send"]
    assert all(row["image_sha256"] == digest and row["image_count"] == 1 for row in rows)
    assert rows[0]["request_id"] == rows[1]["request_id"] == id(request)
    assert base64.b64encode(pixels).decode() not in path.read_text()
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_warm_media_evidence_rejects_canonical_or_event_pixels(tmp_path):
    import base64
    import hashlib

    probe = runpy.run_path(str(ROOT / "tests/fixtures/warm_worker_probe.py"))
    pixels = b"private-pixel-sentinel"
    records = [
        {"phase": phase, "image_sha256": hashlib.sha256(pixels).hexdigest(),
         "image_count": 1, "request_id": turn, "provider_id": 1 if turn < 3 else 2}
        for turn in (1, 2, 3) for phase in ("count", "send")
    ]
    (tmp_path / "private-provider.jsonl").write_text(
        "\n".join(json.dumps(row) for row in records) + "\n")
    evidence = probe["media_evidence"](tmp_path, pixels, [], [])
    assert all(evidence.values())
    leaked = {"data": base64.b64encode(pixels).decode()}
    with pytest.raises(AssertionError):
        probe["media_evidence"](tmp_path, pixels, [leaked], [])
    with pytest.raises(AssertionError):
        probe["media_evidence"](tmp_path, pixels, [], [leaked])
    records[3]["request_id"] = 99
    (tmp_path / "private-provider.jsonl").write_text(
        "\n".join(json.dumps(row) for row in records) + "\n")
    with pytest.raises(AssertionError):
        probe["media_evidence"](tmp_path, pixels, [], [])


def test_warm_media_unqualified_graph_is_diagnostic_not_a_grant():
    from types import SimpleNamespace

    probe = runpy.run_path(str(ROOT / "tests/fixtures/warm_worker_probe.py"))
    def unselected_callback():
        pass
    graph = SimpleNamespace(participants_evidence=lambda: {
        "loads": [], "ready": [], "registrations": [{"original": unselected_callback}]})
    capabilities = {probe["MEDIA"]: SimpleNamespace(enabled=False), "web.media.hook_graph": graph}
    selection = SimpleNamespace(accepts_plan=lambda config: True, accepts_callable=lambda callback: False)
    worker = SimpleNamespace(session=SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=capabilities.get, config={"session": {}})))
    before = dict(vars(selection))
    with pytest.raises(AssertionError) as error:
        probe["require_media"](worker, selection)
    diagnostic = json.loads(str(error.value))
    assert diagnostic["expected_plan_matches"] is True
    assert diagnostic["media_enabled"] is False
    assert diagnostic["rejected_callbacks"][0]["callback"].endswith("unselected_callback")
    assert vars(selection) == before
    assert capabilities[probe["MEDIA"]].enabled is False


@pytest.mark.parametrize('fixture', ['warm_worker_probe.py', 'history_edit_probe.py'])
async def test_worker_probe_answers_current_preparation_batch(fixture):
    """Check the fixture bridge without starting its opt-in real runtime."""
    import ast
    import asyncio
    from types import SimpleNamespace
    from amplifier_web.preparation_context import OPERATIONS

    tree = ast.parse((ROOT / 'tests' / 'fixtures' / fixture).read_text())
    main = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'main')
    publish = next(node for node in main.body if isinstance(node, ast.FunctionDef)
                   and node.name == 'publish')
    future = asyncio.get_running_loop().create_future()
    worker = SimpleNamespace(session=SimpleNamespace(session_id='fixture-session'),
                             bridges={'request': future})
    scope = {'events': [], 'workers': [worker]}
    exec(compile(ast.Module(body=[publish], type_ignores=[]), fixture, 'exec'), scope)
    scope['publish']({'op': 'bridge', 'id': 'request', 'operation': 'context.prepare',
                      'args': {'_contextInputs': ['original-input']}})
    result = await future
    assert set(result) == set(OPERATIONS)
    assert result['context.manifest'] == {
        'available': True, 'value': {'surfaces': [], 'inputIds': ['original-input']}}
    assert result['coordination.current']['value']['sessionId'] == 'fixture-session'
    assert result['memory.context'] == {'available': True, 'value': {'items': []}}
    assert result['message.reactions.context'] == {'available': False}


def test_actual_worker_parks_and_reuses_with_real_shared_state(tmp_path):
    # Retain the registered CI node ID; parking itself is deliberately gone.
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    foundation = os.environ.get("WARM_FOUNDATION_PATH")
    if not python and not foundation:
        pytest.skip("set UNIFIED_RUNTIME_PYTHON or WARM_FOUNDATION_PATH for the isolated runtime proof")
    command = [python] if python else [shutil.which("uv"), "run", "--project",
        str(ROOT / "amplifier_web/runtime_deps"), "--with", foundation, "python"]
    assert command[0], "uv is required for the isolated runtime proof"
    result = subprocess.run(
        [*command, str(ROOT / "tests/fixtures/warm_worker_probe.py"), str(tmp_path)],
        cwd=ROOT, text=True, capture_output=True, timeout=180,
        env={**os.environ, "WARM_WORKER_MEDIA": "0",
             "UV_PROJECT_ENVIRONMENT": str(tmp_path / "runtime-env")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["same_mounted_session"] is True
    assert evidence["two_authoritative_turns"] is True
    assert evidence["cold_prepare_seconds"] >= 0
    assert evidence["same_owner_dispatch_seconds"] >= 0


def test_actual_warm_worker_media_upload_count_send_ack_and_resume(tmp_path):
    """Same maintained fixture and real private Python selection; no mock graph."""
    if os.environ.get("WARM_WORKER_MEDIA") != "1":
        pytest.skip("set WARM_WORKER_MEDIA=1 and UNIFIED_RUNTIME_PYTHON for actual media lifecycle")
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    if not python:
        pytest.skip("UNIFIED_RUNTIME_PYTHON must select the already prepared media runtime")
    result = subprocess.run(
        [python, str(ROOT / "tests/fixtures/warm_worker_probe.py"), str(tmp_path)],
        cwd=ROOT, text=True, capture_output=True, timeout=180,
        env={**os.environ, "AMPLIFIER_RUNTIME_IMMUTABLE": "1", "UV_OFFLINE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["same_mounted_session"] is True
    assert evidence["same_writer"] is True
    assert evidence["resumed_without_replay"] is True
    assert evidence["retired_safely"] is True
    assert evidence["media"] == {
        "counted_private_png": True, "count_send_same_request": True,
        "warm_same_provider": True, "resumed_fresh_provider": True,
        "canonical_ref_ack": True, "canonical_and_host_events_no_pixels": True}
