"""A staged optional tool and saved settings must not strand an included worker update."""
import copy
import shutil
import sqlite3
import time
from unittest.mock import AsyncMock

import pytest

from amplifier_web import updates
from amplifier_web.configuration_recovery import authorize, native_apply, prepare_attempt
from amplifier_web.management import Management
from amplifier_web.service import AppService
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.updates import UpdateManager, active_release, foundation_home
from amplifier_web.windows_launcher_follow import _idle as launcher_idle
from test_queued_configuration_boundary import ControlsTransport, plan
from test_runtime_retention import manager as native_workers
from test_updates import Runtime, app, git, prepare, repo


async def retain_saved_settings(app, number):
    await app.dispatch("session.create", {"title": f"Saved settings {number}"})
    session = app._session()
    sid = session["id"]
    config = plan(number + 2)
    change = app.management.create_configuration_operation(session, config, f"saved-{number}", None, None)
    session.update(configurationBusy=True, configurationOperationFence=change["id"])
    app.management.configuration_transition(session, change["id"], phase="applying", stage="preparing")
    attempt = prepare_attempt(app.management, session, change["id"])
    transport = ControlsTransport(app.data_dir, sid)
    try:
        async def admit(args):
            return authorize(app.management, sid, args)
        await native_apply(transport.controls, config, attempt, admit, lambda: session.get("interruptionRevision", 0))
    finally:
        await transport.close()
    app.management.configuration_transition(session, change["id"], phase="error", stage="readback",
        effect="saved_not_mounted", recoveryRequired=True, error="Synthetic saved settings awaiting recovery")
    session["status"] = "error"
    app._message(session, "user", f"Original request {number}")
    app._message(session, "assistant", f"Previously completed response {number}")
    app._save_changes(sessions={sid})
    return session


def snapshot(app, session):
    root = app.data_dir / "sessions" / session["id"]
    files = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    return files, copy.deepcopy({key: session.get(key) for key in (
        "configurationBusy", "configurationOperationFence", "pendingConfiguration", "messages", "generations")})


@pytest.mark.parametrize("hot", [True, False], ids=["leased-promotion", "cold-handoff"])
async def test_included_generation_with_staged_tool_and_saved_configuration(
        app, repo, native_workers, monkeypatch, hot):
    manager, previous_cache = await prepare(app, repo)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(app.data_dir))
    app.runtime = native_workers
    native_workers.home = app.data_dir
    if not hot:
        monkeypatch.setattr(native_workers, "promote_generation", None)
    app.management = Management(app)
    app.smart_tools = SmartToolsManager(app)
    included = {**manager.inventory[0], "kind": "bundle / module", "updateTier": "included"}
    tool = {"id": "smart-tool:previous", "installationId": "previous", "kind": "smart tool",
            "label": "digital-twin", "status": "update", "eligible": True, "latest": "b" * 40,
            "updateTier": "other"}
    running = app._session()
    await native_workers.send(running, "Original synthetic turn", "original-input", native_workers.emit)
    old_process = native_workers.workers[running["id"]]["process"]
    held = [await retain_saved_settings(app, i) for i in range(4)]
    originals = [snapshot(app, session) for session in held]
    assert manager.busy()

    stage_tool = AsyncMock(return_value={"id": "target"})
    switch_tool = AsyncMock(side_effect=AssertionError("Held tool must not switch"))
    monkeypatch.setattr(app.smart_tools, "stage_update", stage_tool)
    monkeypatch.setattr(app.smart_tools, "activate_update", switch_tool)
    manager.inventory = [tool.copy()]
    await manager.installSmartTools()
    state = app.state["updates"]
    staged_tool = copy.deepcopy(state["pendingSmartTools"])
    assert staged_tool == [{"previous": "previous", "target": "target", "component": "digital-twin", "revision": "b" * 40}]
    state.update(phase="installed", available=1, appAvailable=False, lastCheck=time.time(),
                 application={"id": "application", "status": "current"},
                 sequence={"stage": "included", "nextStage": "included", "install": True})

    inventory_sources = AsyncMock(side_effect=lambda **kwargs: [included.copy(), tool.copy()])
    monkeypatch.setattr(manager, "inventory_sources", inventory_sources)
    original_process = updates.process
    async def local_ref(*args, **kwargs):
        if "ls-remote" in args:
            return repo[2] + " refs/heads/main"
        return await original_process(*args, **kwargs)
    monkeypatch.setattr(updates, "process", local_ref)
    qualified = []
    async def qualify(stage, release):
        assert git(stage / "foundation/cache/repository", "rev-parse", "HEAD") == repo[2]
        qualified.append(release)
    monkeypatch.setattr(manager, "validate", qualify)

    for _ in range(3):
        await manager.tick()
    pointer = active_release(app.data_dir)
    assert git(previous_cache, "rev-parse", "HEAD") == repo[1]
    row = native_workers.workers[running["id"]]
    assert row["process"] is old_process and old_process.returncode is None
    if hot:
        expected = pointer.get("current")
        assert expected, "The ready optional tool stranded the included worker generation"
        assert git(foundation_home(app.data_dir) / "cache/repository", "rev-parse", "HEAD") == repo[2]
        assert row["update_pending"]
        await app.dispatch("session.create", {"title": "New work"})
        fresh = app._session()
        await native_workers.prewarm(fresh, native_workers.emit)
        assert native_workers.workers[fresh["id"]]["generation"] == expected
    else:
        expected = state.get("pendingRelease")
        assert expected and not pointer.get("current")
        assert not row.get("update_pending")
    completed_checks = inventory_sources.await_count
    for _ in range(2):
        await manager.tick()
    assert inventory_sources.await_count == completed_checks
    assert [snapshot(app, session) for session in held] == originals
    assert state["pendingSmartTools"] == staged_tool
    assert qualified == [expected]
    assert active_release(app.data_dir) == pointer
    stage_tool.assert_awaited_once()
    switch_tool.assert_not_awaited()
    delivered = [data["inputId"] for kind, data in native_workers.events
                 if kind == "runtime.status" and data.get("event") == "input.delivered"]
    assert delivered == ["original-input"]


@pytest.fixture
def staged_update(app):
    app.smart_tools = SmartToolsManager(app)
    tool = {"previous": "old", "target": "new", "component": "digital-twin", "revision": "b" * 40}
    app.state["updates"].update(phase="installed", pendingSmartTools=[tool],
        lastCheck=time.time(), sequence={"stage": "complete", "install": False})
    return app.update_manager


async def test_included_check_preserves_ordinary_tool_switch_crash_boundary(staged_update, tmp_path, monkeypatch):
    manager = staged_update
    app = manager.service
    monkeypatch.setattr(manager, "inventory_sources", AsyncMock(return_value=[]))
    await manager.check(tier="included", fresh=True, automatic=True)

    # Restart from the durable state at the moment the external switch begins.
    # The live manager can finish independently after we capture this snapshot.
    crash_home = tmp_path / "interrupted-switch"
    async def capture(previous, target):
        assert (previous, target) == ("old", "new")
        shutil.copytree(manager.directory, crash_home / "updates")
        shutil.copytree(app.data_dir / "artifacts", crash_home / "artifacts")
        db_path = app.data_dir / "app.sqlite3"
        with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(crash_home / "app.sqlite3") as destination:
                source.backup(destination)

    monkeypatch.setattr(app.smart_tools, "activate_update", capture)
    await manager.activateSmartTools()
    restored = AppService(crash_home, Runtime(), workspace=tmp_path)
    try:
        restored.update_manager = UpdateManager(restored)
        restored.smart_tools = SmartToolsManager(restored)
        retry = AsyncMock(return_value={})
        monkeypatch.setattr(restored.smart_tools, "activate_update", retry)
        await restored.update_manager.tick()
        retry.assert_not_awaited()
        assert restored.state["updates"]["pendingSmartTools"][0]["target"] == "new"
    finally:
        await restored.close()


async def test_cold_windows_launcher_still_waits_for_targeted_tool(staged_update):
    assert not staged_update.app_busy()
    assert not launcher_idle(staged_update)
