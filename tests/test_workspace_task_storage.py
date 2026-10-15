"""Task storage uses the execution folder without granting new file authority."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import task_resources as owner
from amplifier_web.service import AppError
from test_collaborative_workspaces import app, agent_action


def prepared(home, execution_root=None, *, command="allocate", kind="scratch-directory"):
    home.mkdir(exist_ok=True, parents=True)
    return owner.prepare(home, "workspace", "root", {"sessionId": "root", "principal": "root"},
                         {"kind": kind}, command, execution_root)


def allocated(home, execution_root=None, **kwargs):
    row, fresh = prepared(home, execution_root, **kwargs)
    if fresh:
        owner.allocate(home, row)
    return owner.read(home, "root", {"id": row["id"]})["items"][0]


@pytest.mark.parametrize("kind", owner.KINDS)
def test_workspace_storage_is_real_private_and_ignored_by_git(tmp_path, kind):
    home, workspace = tmp_path / "app", tmp_path / "checkout"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    ignore = workspace / ".gitignore"
    ignore.write_text("user-owned-existing-rule\n")
    row = allocated(home, workspace, kind=kind)
    directory = workspace / ".amplifier" / "task-storage" / hashlib.sha256(b"root").hexdigest() / row["id"]
    assert Path(row["directory"]) == directory
    assert row["storageRoot"] == str(workspace.resolve())
    assert row["status"] == "ready" and row["observation"] == "present"
    assert not row["fileAccessChanged"]
    path = Path(row["path"])
    assert path.stat().st_mode & 0o077 == 0
    if kind == "sqlite-database":
        assert path == directory / "database.sqlite3"
        with sqlite3.connect(path) as database:
            assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            database.execute("CREATE TABLE proof(value TEXT)")
            database.execute("INSERT INTO proof VALUES ('retained')")
    else:
        assert path.is_dir()
        (path / "proof.txt").write_text("retained")
    proof = path if kind == "sqlite-database" else path / "proof.txt"
    subprocess.run(["git", "-C", str(workspace), "check-ignore", "-q", str(proof)], check=True)
    status = subprocess.run(["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"],
                            text=True, capture_output=True, check=True).stdout
    assert "task-storage" not in status
    assert ignore.read_text() == "user-owned-existing-rule\n"
    assert not (home / "workspace-resources" / "owned").exists()


@pytest.mark.parametrize("use_working_directory", [False, True])
async def test_route_uses_actual_execution_folder_without_editing_permissions(app, tmp_path, monkeypatch, use_working_directory):
    source = app.state["sessions"][0]
    workspace = next(row for row in app.state["workspaces"] if row["path"] == source["workspace"])
    expected = Path(source["workspace"])
    if use_working_directory:
        expected = tmp_path / "isolated-checkout"
        expected.mkdir()
        source["workingDirectory"] = str(expected)
    async def allowed(sid, paths):
        assert sid == source["id"]
        return {"status": "observed", "paths": {path: {"status": "allowed"} for path in paths}}
    access = AsyncMock(side_effect=allowed)
    monkeypatch.setattr(app.runtime, "resource_access", access, raising=False)
    settings = copy.deepcopy(app.state.get("settings"))
    result = await agent_action(app, source, "workspace.resources.provision", {
        "workspaceId": workspace["id"], "sessionId": source["id"], "kind": "scratch-directory",
    }, "execution-storage")
    row = result["result"]["items"][0]
    assert Path(row["path"]).is_relative_to(expected / ".amplifier" / "task-storage")
    assert row["storageRoot"] == str(expected.resolve())
    assert app.state.get("settings") == settings
    assert not row["fileAccessChanged"]
    assert not app.runtime.inputs
    admitted_paths = access.await_args_list[0].args[1]
    assert set(admitted_paths) == {
        str(expected / ".amplifier"), str(expected / ".amplifier" / "task-storage"),
        str(expected / ".amplifier" / "task-storage" / ".gitignore"),
        str(Path(row["directory"]).parent), row["directory"],
    }


@pytest.mark.parametrize("report", ["denied", "unknown", "tool-dependent", "mixed", "missing-path", "unready"])
async def test_agent_allocation_requires_confirmed_access_before_any_workspace_effect(app, tmp_path, monkeypatch, report):
    source = app.state["sessions"][0]
    workspace = next(row for row in app.state["workspaces"] if row["path"] == source["workspace"])
    execution = tmp_path / "restricted-checkout"
    execution.mkdir()
    source["workingDirectory"] = str(execution)
    settings = copy.deepcopy(app.state.get("settings"))

    async def restricted(sid, paths):
        assert sid == source["id"]
        assert len(paths) == 6 and paths[-1].endswith("/database.sqlite3")
        assert not (execution / ".amplifier").exists()
        statuses = {path: {"status": "allowed"} for path in paths}
        if report in {"denied", "unknown", "tool-dependent"}:
            statuses = {path: {"status": report} for path in paths}
        elif report == "mixed":
            # Allowing the directories is insufficient when the database itself
            # is denied; admission must examine every prospective destination.
            statuses[paths[-1]] = {"status": "denied"}
        elif report == "missing-path":
            statuses.pop(paths[-1])
        return {"status": "unknown" if report == "unready" else "observed", "paths": statuses}

    access = AsyncMock(side_effect=restricted)
    monkeypatch.setattr(app.runtime, "resource_access", access, raising=False)
    with pytest.raises(AppError, match="do not confirm write access"):
        await agent_action(app, source, "workspace.resources.provision", {
            "workspaceId": workspace["id"], "sessionId": source["id"], "kind": "sqlite-database",
        }, "restricted-storage")
    access.assert_awaited_once()
    assert not (execution / ".amplifier").exists()
    assert app.state.get("settings") == settings
    assert not app.runtime.inputs
    rows = owner.read(app.data_dir, source["id"], {})["items"]
    assert len(rows) == 1 and rows[0]["status"] == "suppressed"


@pytest.mark.parametrize("engine", ["native", "function"])
@pytest.mark.parametrize("existing_parents", [False, True])
async def test_mounted_narrow_policy_needs_authority_only_for_paths_it_creates(app, tmp_path, monkeypatch, engine, existing_parents):
    pytest.importorskip("amplifier_module_tool_filesystem")
    pytest.importorskip("amplifier_module_tool_apply_patch")
    from amplifier_web.resource_access import observe
    from test_file_source_guard import mounted

    source = app.state["sessions"][0]
    workspace = next(row for row in app.state["workspaces"] if row["path"] == source["workspace"])
    execution = tmp_path / "narrow-checkout"
    execution.mkdir()
    source["workingDirectory"] = str(execution)
    storage = execution / ".amplifier" / "task-storage"
    if existing_parents:
        # Existing storage can be provisioned through the user's normal path;
        # this agent has write authority only within its task-storage subtree.
        allocated(app.data_dir, execution, command="user-seeded-storage")
        ignore_bytes = (storage / ".gitignore").read_bytes()
    coordinator, tools = await mounted(execution, tmp_path / "locks", engine,
                                       allowed_write_paths=[str(storage)])
    native = SimpleNamespace(coordinator=coordinator, session_id="mounted-root")
    inspected = []

    async def access(sid, paths):
        assert sid == source["id"]
        inspected.append(list(paths))
        return await observe(native, paths)

    monkeypatch.setattr(app.runtime, "resource_access", access, raising=False)
    settings = copy.deepcopy(app.state.get("settings"))
    args = {"workspaceId": workspace["id"], "sessionId": source["id"], "kind": "scratch-directory"}
    if existing_parents:
        result = await agent_action(app, source, "workspace.resources.provision", args, "narrow-storage")
        row = result["result"]["items"][0]
        proof = Path(row["directory"]) / "evidence.txt"
        assert row["status"] == "ready" and row["access"]["status"] == "allowed"
        written = await tools["write_file"].execute({"file_path": str(proof), "content": "within existing allowance"})
        assert written.success, written.error
        assert proof.read_text() == "within existing allowance"
        assert str(execution / ".amplifier") not in inspected[0]
        assert str(storage / ".gitignore") not in inspected[0]
        assert (storage / ".gitignore").read_bytes() == ignore_bytes
    else:
        with pytest.raises(AppError, match="do not confirm write access"):
            await agent_action(app, source, "workspace.resources.provision", args, "narrow-storage")
        assert str(execution / ".amplifier") in inspected[0]
        assert not (execution / ".amplifier").exists()
    assert app.state.get("settings") == settings


@pytest.mark.parametrize("component", [".amplifier", ".amplifier/task-storage/.gitignore"])
@pytest.mark.parametrize("replacement", ["missing", "replaced"])
async def test_parent_exempted_from_write_check_cannot_be_recreated_or_replaced(app, tmp_path, monkeypatch, component, replacement):
    source = app.state["sessions"][0]
    workspace = next(row for row in app.state["workspaces"] if row["path"] == source["workspace"])
    execution = tmp_path / "racing-checkout"
    execution.mkdir()
    source["workingDirectory"] = str(execution)
    seeded = allocated(app.data_dir, execution, command="seeded-storage")
    evidence = Path(seeded["directory"]) / "original.txt"
    evidence.write_text("preserved")
    changed = execution / component
    saved = tmp_path / "retained-parent"
    is_directory = changed.is_dir()

    async def race(sid, paths):
        assert sid == source["id"] and str(changed) not in paths
        # The parent was inspected before this asynchronous policy observation.
        # Preserve its old bytes while making its recorded path disappear/change.
        changed.rename(saved)
        if replacement == "replaced":
            if is_directory:
                changed.mkdir()
            else:
                changed.write_bytes(owner.IGNORE_CONTENT)
        return {"status": "observed", "paths": {path: {"status": "allowed"} for path in paths}}

    access = AsyncMock(side_effect=race)
    monkeypatch.setattr(app.runtime, "resource_access", access, raising=False)
    settings = copy.deepcopy(app.state.get("settings"))
    with pytest.raises(AppError):
        await agent_action(app, source, "workspace.resources.provision", {
            "workspaceId": workspace["id"], "sessionId": source["id"], "kind": "scratch-directory",
        }, "racing-storage")
    access.assert_awaited_once()
    assert changed.exists() is (replacement == "replaced")
    if replacement == "replaced":
        if is_directory:
            assert list(changed.iterdir()) == []
        else:
            assert changed.read_bytes() == owner.IGNORE_CONTENT
    if is_directory:
        assert (saved / evidence.relative_to(execution / ".amplifier")).read_text() == "preserved"
    else:
        assert saved.read_bytes() == owner.IGNORE_CONTENT
        assert evidence.read_text() == "preserved"
    rows = owner.read(app.data_dir, source["id"], {})["items"]
    assert len(rows) == 1 and rows[0]["status"] == "unknown"
    assert app.state.get("settings") == settings


def test_placement_inspection_does_not_hold_the_shared_ledger_writer(tmp_path, monkeypatch):
    from amplifier_web import task_storage_placement

    home, execution = tmp_path / "app", tmp_path / "checkout"
    execution.mkdir()
    capture = task_storage_placement.capture
    nested = []

    def inspect(home, root):
        # A separate connection must be able to commit during filesystem/Git
        # inspection. Holding BEGIN IMMEDIATE here would cause a locked database.
        row, fresh = prepared(home, command="independent-legacy-writer")
        assert fresh
        nested.append(row)
        return capture(home, root)

    monkeypatch.setattr(task_storage_placement, "capture", inspect)
    row, fresh = prepared(home, execution)
    assert fresh and len(nested) == 1 and row["id"] != nested[0]["id"]
    assert {item["id"] for item in owner.read(home, "root", {})["items"]} == {row["id"], nested[0]["id"]}


def test_legacy_retry_keeps_original_path_and_retirement_preserves_files(tmp_path):
    home, workspace = tmp_path / "app", tmp_path / "checkout"
    workspace.mkdir()
    legacy = allocated(home)
    original = Path(legacy["path"])
    assert "storageRoot" not in legacy
    assert original.is_relative_to(home / "workspace-resources" / "owned")
    evidence = original / "original.txt"
    evidence.write_bytes(b"original evidence\x00")
    # A request first admitted by the previous implementation remains the same
    # allocation after the route starts supplying its execution folder.
    retry = allocated(home, workspace)
    assert retry["id"] == legacy["id"] and Path(retry["path"]) == original
    assert not (workspace / ".amplifier").exists()
    retired = owner.retire(home, "root", legacy["owner"], {
        "id": legacy["id"], "expectedRevision": retry["revision"],
    }, "retire-legacy")
    assert retired["status"] == "retired"
    assert evidence.read_bytes() == b"original evidence\x00"
    assert allocated(home, workspace)["status"] == "retired"
    assert evidence.read_bytes() == b"original evidence\x00"


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_changed_execution_root_cannot_receive_a_prepared_allocation(tmp_path, replacement):
    home, workspace, foreign = tmp_path / "app", tmp_path / "checkout", tmp_path / "foreign"
    workspace.mkdir()
    foreign.mkdir()
    (foreign / "keep.txt").write_text("foreign")
    row, fresh = prepared(home, workspace)
    assert fresh
    workspace.rename(tmp_path / "original-checkout")
    if replacement == "symlink":
        workspace.symlink_to(foreign, target_is_directory=True)
    else:
        workspace.mkdir()
    with pytest.raises((OSError, ValueError)):
        owner.allocate(home, row)
    assert not (workspace / ".amplifier").exists()
    assert sorted(path.name for path in foreign.iterdir()) == ["keep.txt"]
    retry, fresh = prepared(home, workspace)
    assert not fresh and retry["id"] == row["id"] and retry["status"] == "unknown"


@pytest.mark.parametrize("component", [".amplifier", ".amplifier/task-storage", ".amplifier/task-storage/" + hashlib.sha256(b"root").hexdigest()])
def test_symlinked_storage_ancestor_cannot_redirect_allocation(tmp_path, component):
    home, workspace, foreign = tmp_path / "app", tmp_path / "checkout", tmp_path / "foreign"
    workspace.mkdir()
    foreign.mkdir()
    (foreign / "keep.txt").write_text("foreign")
    row, _ = prepared(home, workspace)
    link = workspace / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(foreign, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        owner.allocate(home, row)
    assert sorted(path.name for path in foreign.iterdir()) == ["keep.txt"]
    assert owner.read(home, "root", {"id": row["id"]})["items"][0]["status"] == "unknown"


@pytest.mark.parametrize("engine", ["native", "function"])
@pytest.mark.parametrize("policy", ["workspace", "narrow", "denied", "read-only"])
async def test_native_root_fresh_and_resumed_children_use_existing_workspace_authority(tmp_path, monkeypatch, engine, policy):
    for name in ("loop_live", "context_simple", "tool_filesystem", "tool_apply_patch"):
        pytest.importorskip("amplifier_module_" + name)
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.session import _apply_host_policy
    from amplifier_web.host.storage import SessionStore

    workspace = tmp_path / "checkout"
    workspace.mkdir()
    row = allocated(tmp_path / "app", workspace)
    directory = Path(row["directory"])
    restrictions = {
        "workspace": {}, "narrow": {"allowed_write_paths": ["src"]},
        "denied": {"denied_write_paths": [".amplifier/task-storage"]},
        "read-only": {"allowed_write_paths": []},
    }[policy]
    tools = [
        {"module": "tool-filesystem", "config": {"working_dir": str(workspace), **restrictions}},
        {"module": "tool-apply-patch", "config": {"working_dir": str(workspace), "engine": engine}},
    ]
    session_plan = {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}}
    bundle = Bundle(name="workspace-task-storage", session=session_plan, tools=tools,
                    agents={"worker": {"instruction": "Write task evidence"}})
    _apply_host_policy(bundle, SimpleNamespace(workspace=workspace, settings={}))
    plan = {"session": session_plan, "tools": bundle.tools, "agents": bundle.agents}
    original_plan = copy.deepcopy(plan)
    spec = importlib.util.spec_from_file_location("workspace_storage_children_fixture",
                                                 Path(__file__).parent / "fixtures/standalone_children_probe.py")
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    paths = {name: Path(importlib.util.find_spec("amplifier_module_" + name.replace("-", "_")).origin).parent
             for name in ("loop-live", "context-simple", "tool-filesystem", "tool-apply-patch")}
    prepared_bundle = fixture.ProbePrepared(plan, BundleModuleResolver(paths), bundle)
    ask = AsyncMock(side_effect=AssertionError("Workspace task storage must not request approval"))
    approvals = Approvals(None, ask)
    parent = await prepared_bundle.create_session(session_id="root", approval_system=approvals)
    parent.coordinator.register_capability("session.working_dir", str(workspace))
    seen = []

    async def write_evidence(mounted_tools, label):
        direct, patched = directory / (label + "-write.txt"), directory / (label + "-patch.txt")
        write = await mounted_tools["write_file"].execute({"file_path": str(direct), "content": label})
        patch_args = ({"type": "create_file", "path": str(patched), "diff": "+" + label}
                      if engine == "native" else {"patch": f"*** Begin Patch\n*** Add File: {patched}\n+{label}\n*** End Patch"})
        patch = await mounted_tools["apply_patch"].execute(patch_args)
        expected = policy == "workspace"
        assert write.success is expected, write.error
        assert patch.success is expected, patch.error
        assert direct.exists() is expected and patched.exists() is expected
        if expected:
            assert direct.read_text() == patched.read_text() == label
        seen.append(label)

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        await write_evidence(tools, prompt)
        return "Evidence handled"

    monkeypatch.setattr(fixture.FiniteOrchestrator, "execute", execute)
    try:
        await write_evidence(parent.coordinator.get("tools"), "root")
        registry = await install_children(parent, prepared_bundle, Runtime("root"),
                                          SessionStore(tmp_path / "children"), approvals)
        await registry.spawn("worker", "fresh", parent, sub_session_id="child")
        await registry.resume("child", "resumed", parent)
        assert seen == ["root", "fresh", "resumed"]
        ask.assert_not_awaited()
        assert plan == original_plan
    finally:
        await parent.cleanup()
