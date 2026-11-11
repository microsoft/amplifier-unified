"""Post-promotion failures must restore a verified old host, not replay installs."""
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, application_generations as generations
from amplifier_web.update_diagnostics import CommandTimeout
from amplifier_web.updates import work_paused
from test_app_features import BASELINE, HOST_APP, managed
from test_update_replacement import stage_replacement, saved_updates


@pytest.fixture
async def promoted(managed, monkeypatch, request):
    prefix = Path(managed.previous["installation"])
    info = prefix / "lib/python3.13/site-packages/amplifier_unified-old.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Name: amplifier-unified\nVersion: {HOST_APP['version']}\n")
    (info.parent / "amplifier_web").mkdir()
    (info.parent / "amplifier_web/application_generations.py").touch()
    old_python = prefix / "bin/python"
    old_python.parent.mkdir()
    old_python.touch()
    managed.bootstrap = prefix
    if getattr(request, "param", None) == "generation":
        import shutil
        receipt = {"version": HOST_APP["version"], "revision": HOST_APP["revision"], "generation": "b" * 32,
                   "componentDigest": app_updates.components.digest(BASELINE)}
        folder = generations.directory(managed.manager.home, receipt["revision"], receipt["generation"])
        folder.mkdir(parents=True)
        (folder / "validated.json").write_text(json.dumps(receipt))
        target_prefix = folder / "tools/amplifier-unified"
        shutil.copytree(prefix, target_prefix)
        managed.previous_pointer = generations.promote(managed.manager.home, receipt, prefix)
        old_python = target_prefix / "bin/python"
    monkeypatch.setattr(app_updates, "installed_target", AsyncMock(return_value=(
        "/fixture/uv", "/fixture/launcher", old_python, managed.previous)))
    monkeypatch.setattr(generations, "can_read_candidate", AsyncMock(return_value=True))
    await stage_replacement(managed, "app")
    managed.old_python = old_python
    managed.old_graph = sorted([*copy.deepcopy(BASELINE), copy.deepcopy(HOST_APP)], key=lambda row: row["name"])
    candidate = copy.deepcopy(app_updates.components.read_graph.return_value)

    async def graph(_process, python, **kwargs):
        return copy.deepcopy(managed.old_graph if Path(python) == old_python else candidate)

    monkeypatch.setattr(app_updates.components, "read_graph", graph)
    managed.probe_calls = []
    managed.candidate_failure = CommandTimeout(30)
    managed.previous_failure = None
    managed.on_candidate = lambda: None
    managed.on_previous = lambda: None
    managed.candidate_graph = candidate
    original = app_updates.process

    async def process(*args, **kwargs):
        if app_updates.PROBE in args:
            managed.probe_calls.append(Path(args[0]))
            assert work_paused(managed.service.state)
            if Path(args[0]) != old_python:
                assert generations.active(managed.manager.home)
                managed.on_candidate()
                if managed.candidate_failure:
                    raise managed.candidate_failure
            else:
                managed.on_previous()
                if managed.previous_failure:
                    raise managed.previous_failure
            version = (HOST_APP["version"] if Path(args[0]) == old_python else
                       next(row["version"] for row in candidate if row["name"] == "amplifier-unified"))
            return "AMPLIFIER_UPDATE_PROBE=" + json.dumps({
                "ok": True, "stage": "complete", "version": version,
                "isolated": True, "packageInEnvironment": True, "frontendPresent": True, "loginAvailable": True,
            })
        return await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, "process", process)
    return managed


async def test_probe_timeout_restores_verified_bootstrap_and_unfences(promoted):
    manager, service = promoted.manager, promoted.service
    await app_updates.activate(manager)
    assert generations.active(manager.home) is None
    state = service.state["updates"]
    assert not state.get("pendingReplacement") and not state.get("pendingRestart")
    assert not work_paused(service.state)
    assert "restored" in state["detail"].lower()
    assert state["phase"] == "error"  # Failed update is not an installed result.
    assert not saved_updates(service).get("pendingReplacement")
    assert any(row["phase"] == "replacement-probe" and row["status"] == "failed"
               for row in manager.diagnostics.state["events"])
    promoted.restart.assert_not_awaited()
    assert promoted.probe_calls[-1] == promoted.old_python
    assert not any("install" in args and not options.get("env", {}).get("UV_TOOL_DIR")
                   for args, options in promoted.calls)


@pytest.mark.parametrize("failure", [
    CommandTimeout(30), RuntimeError("failed verification"), asyncio.CancelledError(),
])
async def test_failed_or_cancelled_previous_probe_keeps_fence(promoted, failure):
    promoted.previous_failure = failure
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await app_updates.activate(promoted.manager)
    else:
        await app_updates.activate(promoted.manager)
    assert generations.active(promoted.manager.home)
    assert work_paused(promoted.service.state)
    assert saved_updates(promoted.service)["pendingReplacement"]
    promoted.restart.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["identity", "marker", "bootstrap", "inventory", "pointer", "receipt"])
async def test_changed_recovery_evidence_never_opens_gate(promoted, mutation, monkeypatch):
    def change():
        if mutation == "identity":
            promoted.manager.running_identity["instanceId"] = "other-owner"
        elif mutation == "marker":
            promoted.service.state["updates"]["pendingReplacement"]["qualification"]["dependencyDigest"] = "f" * 64
        elif mutation == "bootstrap":
            info = next(Path(promoted.previous["installation"]).rglob("METADATA"))
            info.write_text(info.read_text() + "Summary: changed\n")
        elif mutation == "inventory":
            monkeypatch.setattr(app_updates.components, "installed_graph", lambda: [])
        elif mutation == "pointer":
            path = promoted.manager.home / "updates/application-active.json"
            data = json.loads(path.read_text())
            data["bootstrap"]["digest"] = "changed"
            path.write_text(json.dumps(data))
        else:
            current = generations.active(promoted.manager.home)
            path = current["folder"] / "validated.json"
            data = json.loads(path.read_text())
            data["componentDigest"] = "changed"
            path.write_text(json.dumps(data))
    promoted.on_previous = change
    await app_updates.activate(promoted.manager)
    assert work_paused(promoted.service.state)
    assert saved_updates(promoted.service)["pendingReplacement"]
    assert (promoted.manager.home / "updates/application-active.json").exists()
    promoted.restart.assert_not_awaited()


async def test_probe_cancellation_does_not_start_recovery(promoted):
    promoted.candidate_failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await app_updates.activate(promoted.manager)
    assert len(promoted.probe_calls) == 1
    assert work_paused(promoted.service.state)
    assert generations.active(promoted.manager.home)
    promoted.restart.assert_not_awaited()


@pytest.mark.parametrize("slow", [False, True])
async def test_controlled_cold_import_through_real_timeout_process(promoted, monkeypatch, tmp_path, slow):
    import sys
    from amplifier_web.updates import process as real_process

    module = tmp_path / "controlled_cold_import.py"
    entered = tmp_path / "entered"
    module.write_text(
        "import time\nfrom pathlib import Path\n"
        f"Path({str(entered)!r}).touch()\n"
        f"time.sleep({10 if slow else 0.02})\n")
    probe = (
        "import importlib.util,sys\n"
        "spec=importlib.util.spec_from_file_location('controlled_import',sys.argv[1])\n"
        "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n")
    original = app_updates.process
    promoted.candidate_failure = None

    async def process(*args, **kwargs):
        if app_updates.PROBE in args and Path(args[0]) != promoted.old_python:
            assert work_paused(promoted.service.state)
            # Use the production termination/wait path and an actual importing
            # child, reducing only this synthetic fixture's deadline.
            await real_process(sys.executable, "-I", "-c", probe, module, timeout=1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(app_updates, "process", process)
    await app_updates.activate(promoted.manager)
    assert entered.is_file()
    if slow:
        assert generations.active(promoted.manager.home) is None
        assert not work_paused(promoted.service.state)
        assert any(row["phase"] == "replacement-probe" and row["status"] == "failed"
                   and row["errorType"] == "CommandTimeout"
                   for row in promoted.manager.diagnostics.state["events"])
        promoted.restart.assert_not_awaited()
    else:
        assert generations.active(promoted.manager.home)
        assert work_paused(promoted.service.state)
        assert promoted.service.state["updates"]["pendingRestart"]
        promoted.restart.assert_awaited_once()


async def test_successful_cold_probe_keeps_normal_managed_restart(promoted):
    promoted.candidate_failure = None
    await app_updates.activate(promoted.manager)
    assert len(promoted.probe_calls) == 1
    assert generations.active(promoted.manager.home)
    assert promoted.service.state["updates"]["pendingRestart"]
    assert work_paused(promoted.service.state)
    promoted.restart.assert_awaited_once_with(promoted.manager)


async def test_failed_restore_retains_unknown_marker(promoted, monkeypatch):
    monkeypatch.setattr(generations, "restore", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write refused")))
    await app_updates.activate(promoted.manager)
    assert work_paused(promoted.service.state)
    assert saved_updates(promoted.service)["pendingReplacement"]
    assert generations.active(promoted.manager.home)


async def test_recovery_publication_failure_keeps_gate_closed(promoted, monkeypatch):
    original = promoted.service._publish_changes
    def publish(*args, **kwargs):
        if not promoted.service.state["updates"].get("pendingReplacement"):
            raise OSError("state persistence failed")
        return original(*args, **kwargs)
    monkeypatch.setattr(promoted.service, "_publish_changes", publish)
    await app_updates.activate(promoted.manager)
    assert work_paused(promoted.service.state)
    assert saved_updates(promoted.service)["pendingReplacement"]
    assert generations.active(promoted.manager.home) is None
    failures = [row for row in promoted.manager.diagnostics.state["events"] if row["status"] == "failed"]
    assert any(row["phase"] == "replacement-probe" and row["errorType"] == "CommandTimeout" for row in failures)
    assert any(row["phase"] == "replacement-recovery" and row["errorType"] == "OSError" for row in failures)


async def test_component_mismatch_keeps_original_failure_after_recovery(promoted, monkeypatch):
    promoted.candidate_failure = None
    async def graph(_process, python, **kwargs):
        if Path(python) == promoted.old_python:
            return copy.deepcopy(promoted.old_graph)
        return copy.deepcopy(promoted.candidate_graph if not promoted.probe_calls else [])
    monkeypatch.setattr(app_updates.components, "read_graph", graph)
    await app_updates.activate(promoted.manager)
    assert generations.active(promoted.manager.home) is None
    assert not work_paused(promoted.service.state)
    assert any(row["phase"] == "replacement-components" and row["status"] == "failed"
               and row["errorType"] == "ValueError" for row in promoted.manager.diagnostics.state["events"])


@pytest.mark.parametrize("promoted", ["generation"], indirect=True)
async def test_previous_private_generation_restored_not_public_bootstrap(promoted):
    await app_updates.activate(promoted.manager)
    assert generations.active(promoted.manager.home) == promoted.previous_pointer
    assert not work_paused(promoted.service.state)
    assert promoted.probe_calls[-1] == promoted.old_python


@pytest.mark.parametrize("promoted", ["generation"], indirect=True)
@pytest.mark.parametrize("when", ["before-promotion", "during-verification"])
async def test_manual_bootstrap_replacement_cannot_restore_stale_private_target(promoted, monkeypatch, when):
    def replace_bootstrap():
        info = next(promoted.bootstrap.rglob("METADATA"))
        info.write_text(info.read_text() + "Summary: manual reinstall\n")
    if when == "before-promotion":
        original = generations.promote
        def promote(*args):
            replace_bootstrap()
            return original(*args)
        monkeypatch.setattr(generations, "promote", promote)
    else:
        promoted.on_previous = replace_bootstrap
    await app_updates.activate(promoted.manager)
    assert work_paused(promoted.service.state)
    assert saved_updates(promoted.service)["pendingReplacement"]
    assert generations.active(promoted.manager.home) != promoted.previous_pointer
    promoted.restart.assert_not_awaited()


async def test_restored_host_releases_only_saved_unsubmitted_input_once(promoted):
    from test_update_replacement import save_held_input
    from amplifier_web.update_drain import finish
    entered = asyncio.Event()
    release = asyncio.Event()
    process = app_updates.process
    async def delayed(*args, **kwargs):
        if app_updates.PROBE in args and Path(args[0]) == promoted.old_python:
            entered.set()
            await release.wait()
        return await process(*args, **kwargs)
    from unittest.mock import patch
    with patch.object(app_updates, "process", delayed):
        activation = asyncio.create_task(app_updates.activate(promoted.manager))
        await asyncio.wait_for(entered.wait(), 3)
        await save_held_input(promoted.service, "held-before-restoration")
        release.set()
        await activation
    await finish(promoted.manager, "withdrawn")
    await finish(promoted.manager, "withdrawn")
    assert len(promoted.service.runtime.sent) == 1
    assert promoted.service.state["updates"]["drain"]["phase"] == "released"


@pytest.mark.parametrize("promoted", [None, "generation"], indirect=True)
@pytest.mark.parametrize("effect", ["none", "saved_not_mounted"])
async def test_restore_retains_terminal_configuration_hold_and_releases_only_unsent(
        promoted, monkeypatch, effect):
    from types import SimpleNamespace
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    from amplifier_web.configuration_recovery import native_apply, prepare_attempt
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.update_drain import finish
    from test_queued_configuration_boundary import ControlsTransport, plan
    from test_update_terminal_configuration import _admit, originals
    from test_update_replacement import save_held_input

    service = promoted.service
    original_runtime = service.runtime
    service.runtime = RuntimeManager()
    # Real absent-writer boundary; only delivery to the unrelated chat is synthetic.
    monkeypatch.setattr(service.runtime, "send", original_runtime.send)
    service.runtime.sent = original_runtime.sent
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(service.data_dir))
    monkeypatch.setenv("AMPLIFIER_HOME", str(service.data_dir / "native"))
    await service.dispatch("session.create", {"title": "Terminal held configuration"})
    held = service._session()
    service.management = Management(service)
    transport = ControlsTransport(service.data_dir, held["id"])
    try:
        record = service.management.create_configuration_operation(held, plan(2), "original-settings", None, None)
        held.update(configurationBusy=True, configurationOperationFence=record["id"])
        service.management.configuration_transition(held, record["id"], phase="applying", stage="preparing")
        attempt = prepare_attempt(service.management, held, record["id"])
        if effect == "none":
            def refuse(*args):
                raise ValueError("Synthetic refusal before settings write")
            monkeypatch.setattr(transport.controls, "prepare_configuration", refuse)
            with pytest.raises(ValueError, match="Synthetic refusal"):
                await native_apply(transport.controls, plan(2), attempt,
                    lambda args: _admit(service, held, args), lambda: held.get("interruptionRevision", 0))
        else:
            await native_apply(transport.controls, plan(2), attempt,
                lambda args: _admit(service, held, args), lambda: held.get("interruptionRevision", 0))
        service.management.configuration_transition(held, record["id"], phase="error", stage="readback",
            effect=effect, recoveryRequired=True, error="Synthetic held configuration")
        held["status"] = "error"
        fixture = SimpleNamespace(app=service, session=held)
        before = originals(fixture)
        store = SharedSessionStore(held["workspace"], held["id"])
        await service.dispatch("session.create", {"title": "Unsubmitted input"})
        unheld_id = service._session()["id"]
        original_process = app_updates.process
        probes = []

        async def process(*args, **kwargs):
            if app_updates.PROBE in args:
                with pytest.raises(SessionBusyError):
                    store.acquire(app="competing-during-promotion-recovery")
                assert originals(fixture) == before
                probes.append(Path(args[0]))
                if Path(args[0]) == promoted.old_python:
                    await save_held_input(service, "positively-unsent-during-recovery")
            return await original_process(*args, **kwargs)

        monkeypatch.setattr(app_updates, "process", process)
        assert not promoted.manager.app_busy() and promoted.manager.busy()
        await app_updates.activate(promoted.manager)
        assert len(probes) == 2 and probes[-1] == promoted.old_python
        assert generations.active(promoted.manager.home) == getattr(promoted, "previous_pointer", None)
        assert not saved_updates(service).get("pendingReplacement") and not work_paused(service.state)
        assert originals(fixture) == before and not transport.calls and not transport.inputs
        acquired = store.acquire(app="after-restoration")
        acquired.release()
        await finish(promoted.manager, "withdrawn")
        await finish(promoted.manager, "withdrawn")
        assert len(service.runtime.sent) == 1
        assert service.runtime.sent[0][0] == unheld_id
        assert originals(fixture) == before
        failures = [row for row in promoted.manager.diagnostics.state["events"] if row["status"] == "failed"]
        assert any(row["phase"] == "replacement-probe" and row["errorType"] == "CommandTimeout" for row in failures)
        promoted.restart.assert_not_awaited()
    finally:
        await transport.close()