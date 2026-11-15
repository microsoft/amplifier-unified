"""File-entry/real-pipe media regression; prepared-source integration is opt-in."""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests/fixtures/normal_media_pipe_probe.py"


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(PROBE.parent))
    spec = importlib.util.spec_from_file_location("pipe_probe_unit", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pipe_fixture_never_constructs_or_injects_worker_graph_provider_or_binding():
    tree = ast.parse(PROBE.read_text())
    calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
             for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, (ast.Name, ast.Attribute))}
    assert not calls & {"Worker", "NamedMediaGraph", "select_from_declaration", "mint_owner",
                        "bind_media", "acknowledge_media", "create_new", "force_enabled",
                        "AsyncOpenAI", "OpenAIProvider"}
    assert "create_subprocess_exec" in calls and "host_result" in calls
    assert not any(isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Subscript)
                           and isinstance(target.value, ast.Attribute)
                           and target.value.attr == "modules" for target in node.targets)
                   for node in ast.walk(tree)), "No sys.modules canonicalization workaround"
    patches = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == "object"
               and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)]
    assert {node.args[1].value for node in patches} == {
        "connect", "connect_ex", "handle_async_request"}


def test_pipe_private_parent_is_checked_not_repaired(probe, tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(AssertionError, match="0700"):
        probe.private_parent(tmp_path)
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    tmp_path.chmod(0o700)
    probe.private_parent(tmp_path)
    (tmp_path / "occupied").touch()
    with pytest.raises(AssertionError, match="fresh empty"):
        probe.private_parent(tmp_path)


def test_pipe_socket_denial_records_both_connection_paths_without_network(probe, tmp_path):
    from contextlib import ExitStack
    import socket
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    report = tmp_path / "denials.json"
    with ExitStack() as stack:
        denied = probe.deny_socket_connections(stack, report)
        with socket.socket() as client:
            for operation in (client.connect, client.connect_ex):
                with pytest.raises(AssertionError, match="denied"):
                    operation(("192.0.2.1", 443))
        assert len(denied) == 2
        assert json.loads(report.read_text()) == denied
    assert socket.socket.connect is original_connect and socket.socket.connect_ex is original_connect_ex
    tree = ast.parse(PROBE.read_text())
    bootstrap = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bootstrap")
    calls = [node for node in ast.walk(bootstrap) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id in {"deny_socket_connections", "helpers"}]
    assert next(node.lineno for node in calls if node.func.id == "deny_socket_connections") < next(
        node.lineno for node in calls if node.func.id == "helpers")


def test_pipe_finite_context_prepare_readback_and_unknown_service(probe):
    from amplifier_web.preparation_context import OPERATIONS
    result = probe.host_result({"operation": "context.prepare", "args": {"_contextInputs": ["one"]}})
    assert set(result) == set(OPERATIONS)
    assert result["context.manifest"]["value"] == {"surfaces": [], "inputIds": ["one"]}
    assert result["coordination.current"]["value"]["sessionId"] == probe.SESSION
    assert result["message.reactions.context"] == {"available": False}
    assert probe.host_result({"operation": "capacity.admit",
                              "args": {"call": {"sessionId": probe.SESSION}}}) == {
                                  "allowed": True, "budgetRevision": 0}
    with pytest.raises(AssertionError):
        probe.host_result({"operation": "capacity.admit", "args": {"call": {"sessionId": "foreign"}}})
    with pytest.raises(AssertionError, match="Unexpected"):
        probe.host_result({"operation": "dispatch", "args": {"action": "anything"}})


async def test_pipe_until_rejects_eof_instead_of_inventing_retirement_ack(probe):
    class Writer:
        def write(self, value):
            pass

        async def drain(self):
            pass

    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stdout.feed_eof()
    stderr.feed_eof()
    from types import SimpleNamespace
    pipe = probe.Pipe(SimpleNamespace(stdout=stdout, stderr=stderr, stdin=Writer()), b"private")
    with pytest.raises(AssertionError, match="EOF"):
        await pipe.until(lambda event: event.get("result") == {"retired": True})
    await pipe.reader
    await pipe.stderr


async def test_pipe_reader_preserves_real_json_and_answers_bridge_through_stdin(probe):
    from types import SimpleNamespace
    from amplifier_web.runtime_protocol import encode_message

    class Writer:
        def __init__(self):
            self.frames = []

        def write(self, value):
            self.frames.append(json.loads(value))

        async def drain(self):
            pass

    stdout, stderr, writer = asyncio.StreamReader(), asyncio.StreamReader(), Writer()
    event = {"op": "bridge", "id": "prepare", "operation": "context.prepare",
             "args": {"_contextInputs": ["one"]}}
    stdout.feed_data(encode_message(event))
    stdout.feed_eof()
    stderr.feed_eof()
    pipe = probe.Pipe(SimpleNamespace(stdout=stdout, stderr=stderr, stdin=writer), b"private")
    await pipe.reader
    assert pipe.events == [event]
    assert writer.frames == [{"op": "bridge.result", "id": "prepare", "result": probe.host_result(event)}]
    await pipe.stderr


def test_actual_media_file_entry_json_pipes_count_send_idle_retire_ack_and_resume(tmp_path):
    if os.environ.get("NORMAL_MEDIA_FILE_ENTRY") != "1":
        pytest.skip("set NORMAL_MEDIA_FILE_ENTRY=1 with an already prepared source runtime/PYTHONPATH")
    if sys.platform != "linux":
        pytest.skip("Prospective canonical source custody is qualified on Linux only")
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    assert python, "Select an existing prepared interpreter; this test never installs"
    tmp_path.chmod(0o700)
    profile, worker = (os.environ.get("NORMAL_MEDIA_INSTALLED_PROFILE"),
                       os.environ.get("NORMAL_MEDIA_INSTALLED_WORKER"))
    command = [python, str(PROBE), str(tmp_path)]
    env = {**os.environ, "AMPLIFIER_RUNTIME_IMMUTABLE": "1", "UV_OFFLINE": "1"}
    if profile or worker:
        assert profile and worker, "Installed inputs must be supplied together"
        command.extend(["--installed-profile", profile, "--installed-worker", worker])
        env.pop("WARM_LOOP_SOURCE", None)
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONUSERBASE", None)
        env.update(PYTHONPATH=str(PROBE.parent), PYTHONNOUSERSITE="1")
    result = subprocess.run(command, cwd=tmp_path if profile else ROOT, text=True,
                            capture_output=True, timeout=120, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    # Keep the consumed, pixel-free parent pipe receipts alongside both exact
    # child observers and stderr readbacks, after the fresh-root launch finished.
    (tmp_path / "pipe-result.json").write_text(result.stdout)
    assert evidence["mode"] == ("installed-offline-transport" if profile else "source-only")
    assert evidence["installed_package_parity"] is bool(profile)
    assert evidence["real_file_entry"][1] == (worker if profile else str(ROOT / "amplifier_web/runtime_worker.py"))
    assert evidence["manager_preparation_executed"] is False
    for name in ("actual_vendor_provider_and_sdk", "offline_transport_not_inference",
                 "two_turn_idle_custody", "fresh_process_resume_without_replay",
                 "canonical_t2_ack", "canonical_and_protocol_no_pixels"):
        assert evidence[name] is True
    assert len(evidence["children"]) == len(evidence["receipts"]) == 2
    assert evidence["children"][0]["pid"] != evidence["children"][1]["pid"]
    for child, receipt, turns in zip(evidence["children"], evidence["receipts"], (2, 1)):
        assert child["final"] and not child["failures"] and not child["socket_denials"]
        assert child["canonical_cleanup_joined"] and child["private_requests_cleared"]
        assert child["file_bootstrapped_app_without_outer_root"]
        assert {"httpx", "httpx2"} <= set(child["transport_packages"])
        assert all(row["wire_http_package"] == "httpx2" for row in child["snapshots"])
        assert sum(row["phase"] == "send" for row in child["records"]) == turns
        assert sum(row["phase"] == "count" for row in child["records"]) >= turns
        assert child["source"]["catalog"]["recipe"] == "unified-openai-static-sol-local-capture-v1"
        assert not child["source"]["catalog"]["mismatches"]
        assert len(child["typed_receipts"]) == len(child["records"])
        for typed in child["typed_receipts"]:
            assert typed["receipt_type"] == "VersionedCheckpointReceipt"
            assert typed["result_type"] == "CommitAck" and typed["refs"]
            assert typed["prefix"]["session_id"] == "normal-media-file-entry"
        assert evidence["cas_scope"].startswith("successful exact-prefix path")
        assert receipt["reply_before_eof"] and receipt["exit_code"] == 0
        assert receipt["retirement_reply_index"] < receipt["stdout_eof_index"]
        assert "context.prepare" in receipt["bridge_operations"]
        if profile:
            assert child["source"]["profile"]["sha256"] == hashlib.sha256(Path(profile).read_bytes()).hexdigest()
            assert {"amplifier_web", "openai", "httpx", "httpx2"} <= set(child["source"]["imports"])


@pytest.fixture
def installed_inputs(probe, tmp_path):
    """Synthetic file identities for selector tests only; NOT an installed runtime."""
    fixture = probe.helpers()
    environment = tmp_path / "qualification"
    environment.mkdir()
    (environment / "pyvenv.cfg").write_text("fixture only; never executed")
    python = environment / "bin/python"
    python.parent.mkdir()
    python.write_text("# fixture interpreter; never execute")
    python.chmod(0o700)
    site = environment / "lib/python3.13/site-packages"
    packages, files = {}, {}
    for name in fixture.INSTALLED_PACKAGES:
        directory = site / name
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("# " + name)
        packages[name] = {"root": str(directory), "distribution": name.replace("_", "-"), "version": "1.0"}
    app = site / "amplifier_web"
    for relative, value in {"runtime_worker.py": "# selected entry", "runtime_bootstrap.py": "# bootstrap",
                            "host/media_sources.json": '{"recipe":"fixture","sources":{},"revisions":{}}'}.items():
        path = app / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text(value)
    for path in site.rglob("*"):
        if path.is_file():
            files[str(path.relative_to(environment))] = hashlib.sha256(path.read_bytes()).hexdigest()
    profile = tmp_path / "installed-profile.json"
    value = {"v": 1, "environment_root": str(environment), "python": str(python),
             "app_root": str(app), "packages": packages, "files": files}
    profile.write_text(json.dumps(value))
    return fixture, profile, value


def test_installed_selector_pins_artifact_files_not_current_catalog_observations(installed_inputs):
    fixture, profile, value = installed_inputs
    target = fixture.InstalledTarget(profile)
    assert target.worker == Path(value["app_root"]) / "runtime_worker.py"
    assert target.verify_file(target.catalog)["sha256"] == value["files"][
        str(target.catalog.relative_to(target.environment))]
    target.worker.write_text("# unexpected new bytes")
    with pytest.raises(ValueError, match="hash differs"):
        target.verify_file(target.worker)


@pytest.mark.parametrize("change", ["version-bool", "source-root", "missing-file", "traversal",
                                   "missing-package", "foreign-app", "bad-hash"])
def test_installed_selector_refuses_incomplete_or_source_fallback_profiles(installed_inputs, change):
    fixture, profile, value = installed_inputs
    if change == "version-bool":
        value["v"] = True
    elif change == "source-root":
        value["environment_root"] = str(ROOT)
    elif change == "missing-file":
        value["files"] = {}
    elif change == "traversal":
        value["files"]["../outside"] = "0" * 64
    elif change == "missing-package":
        del value["packages"]["amplifier_web"]
    elif change == "foreign-app":
        value["app_root"] = str(ROOT / "amplifier_web")
    else:
        key = next(k for k in value["files"] if k.endswith("/runtime_worker.py"))
        value["files"][key] = "0" * 64
    profile.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        fixture.InstalledTarget(profile)


def test_installed_selector_refuses_symlink_and_profile_mutation(installed_inputs, tmp_path):
    fixture, profile, value = installed_inputs
    target = fixture.InstalledTarget(profile)
    bootstrap = target.app / "runtime_bootstrap.py"
    original = bootstrap.read_bytes()
    other = tmp_path / "outside.py"
    other.write_bytes(original)
    bootstrap.unlink()
    bootstrap.symlink_to(other)
    with pytest.raises(ValueError, match="escaped"):
        target.verify_file(bootstrap)
    profile.write_text(json.dumps(value) + "\n")
    with pytest.raises(ValueError, match="profile changed"):
        target.verify_file(target.catalog)


def test_installed_mode_requires_explicit_pair_and_correct_interpreter(probe, installed_inputs, monkeypatch):
    fixture, profile, value = installed_inputs
    monkeypatch.setattr(fixture, "INSTALLED_TARGET", None)
    assert probe.installed_target(None, None) is None
    with pytest.raises(ValueError, match="together"):
        probe.installed_target(str(profile), None)
    with pytest.raises(ValueError, match="does not match"):
        probe.installed_target(str(profile), str(ROOT / "amplifier_web/runtime_worker.py"))
    with pytest.raises(ValueError, match="interpreter"):
        probe.installed_target(str(profile), str(Path(value["app_root"]) / "runtime_worker.py"))
    assert fixture.INSTALLED_TARGET is None


def test_installed_child_environment_drops_all_inherited_dependency_overlays(
        probe, installed_inputs, tmp_path, monkeypatch):
    fixture, profile, _ = installed_inputs
    target = fixture.InstalledTarget(profile)
    monkeypatch.setenv("PYTHONPATH", "/unreviewed/source:/another/dependency")
    monkeypatch.setenv("WARM_LOOP_SOURCE", "/unreviewed/loop")
    monkeypatch.setenv("PYTHONHOME", "/outer/runtime")
    monkeypatch.setenv("PYTHONUSERBASE", "/outer/user")
    bootstrap = tmp_path / "bootstrap"
    selected = probe.child_environment(bootstrap, {"loop-live": "/source-loop"}, tmp_path, target)
    assert selected["PYTHONPATH"].split(os.pathsep) == [str(bootstrap), str(PROBE.parent)]
    assert all(key not in selected for key in ("WARM_LOOP_SOURCE", "PYTHONHOME", "PYTHONUSERBASE"))
    assert selected["PYTHONNOUSERSITE"] == "1"
    assert selected["NORMAL_MEDIA_INSTALLED_PROFILE"] == str(profile)
    # Original source mode keeps its deliberate source overlays.
    source = probe.child_environment(bootstrap, {"loop-live": "/source-loop"}, tmp_path)
    assert "/unreviewed/source" in source["PYTHONPATH"] and source["WARM_LOOP_SOURCE"] == "/source-loop"


def test_installed_source_resolver_cannot_replace_the_production_loop_locator():
    tree = ast.parse(PROBE.read_text())
    resolver = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "finite_sources")
    writes = [node for node in ast.walk(resolver) if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Attribute) and t.attr == "installed_loop_source" for t in node.targets)]
    assert len(writes) == 1
    guard = next(node for node in ast.walk(resolver) if isinstance(node, ast.If)
                 and writes[0] in node.body)
    assert "not installed" in ast.unparse(guard.test)


@pytest.mark.parametrize("forbidden", ["source", "installation"])
def test_installed_output_refuses_symlinked_ancestor_before_any_writes(
        probe, installed_inputs, tmp_path, monkeypatch, forbidden):
    fixture, profile, _ = installed_inputs
    target = fixture.InstalledTarget(profile)
    source = tmp_path / "source-checkout"
    source.mkdir()
    monkeypatch.setattr(probe, "ROOT", source)
    parent = source if forbidden == "source" else target.environment
    root = parent / "private-empty"
    root.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    with pytest.raises(ValueError, match="without aliases"):
        probe.qualification_root(alias / root.name, target)
    assert list(root.iterdir()) == []
    with pytest.raises(ValueError, match="outside source"):
        probe.qualification_root(root, target)
    allowed = tmp_path / "outside"
    allowed.mkdir(mode=0o700)
    assert probe.qualification_root(allowed, target) == allowed


@pytest.mark.parametrize("change", ["none", "editable", "version", "unrecorded", "foreign-loaded-module",
                                   "altered-loaded-module"])
def test_installed_loaded_origins_require_manifest_and_distribution_membership(
        installed_inputs, monkeypatch, tmp_path, change):
    """Synthetic metadata exercises refusal logic, not actual installed qualification."""
    from types import SimpleNamespace
    import importlib.metadata
    fixture, profile, value = installed_inputs
    target = fixture.InstalledTarget(profile)
    app = target.app / "__init__.py"
    modules = {"amplifier_web": SimpleNamespace(__file__=str(app))}
    if change == "foreign-loaded-module":
        foreign = tmp_path / "checkout.py"
        foreign.write_text("# source fallback")
        modules["amplifier_web.runtime_worker"] = SimpleNamespace(__file__=str(foreign))
    elif change == "altered-loaded-module":
        app.write_text("# different bytes")
    monkeypatch.setattr(fixture, "sys", SimpleNamespace(
        executable=str(target.python), prefix=str(target.environment), path=[], modules=modules))
    monkeypatch.setattr(fixture, "origin",
                        lambda name: Path(target.packages[name]["root"]) / "__init__.py")
    by_distribution = {row["distribution"]: (name, row) for name, row in target.packages.items()}

    def distribution(name):
        package, item = by_distribution[name]
        member = Path(package) / "__init__.py"
        return SimpleNamespace(
            version="changed" if change == "version" else item["version"],
            read_text=lambda _: '{"dir_info":{"editable":true}}' if change == "editable" else "{}",
            files=[] if change == "unrecorded" else [member],
            locate_file=lambda row: Path(item["root"]).parent / row)

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    if change == "none":
        evidence = target.check_origins()
        assert evidence["mode"] == "installed-offline-transport"
        assert len(evidence["imports"]) == len(fixture.INSTALLED_PACKAGES)
        assert evidence["loaded_modules"]["amplifier_web"]["path"] == str(app)
    else:
        with pytest.raises(ValueError):
            target.check_origins()