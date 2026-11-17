"""The native "launcher follows the app" case: what can be checked without Windows.

The case itself runs only on a Windows runner (`windows-launcher-follow.yml`).
These cases fail on every platform when the workflow could pass with a failed
check, when the fixture reads a name that the product does not have, or when
the fixture's reader of the app's saved state does not match the app's writer.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tomllib

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/windows-launcher-follow.yml"
FIXTURE = ROOT / "tests/fixtures/windows_launcher_follow_native.py"


@pytest.fixture(scope="module")
def fixture():
    spec = importlib.util.spec_from_file_location("launcher_follow_native_fixture", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def job():
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    (name, value), = document["jobs"].items()
    return document, name, value


def test_the_job_fails_when_a_check_fails_or_is_not_reached(fixture):
    document, name, value = job()
    assert name == "windows-launcher-follow" and "continue-on-error" not in json.dumps(document)
    steps = {step["name"]: step for step in value["steps"] if "name" in step}
    run, = [step for step in steps.values() if "windows_launcher_follow_native.py run" in step.get("run", "")]
    required = steps["Require every check"]
    cleanup, = [step for step in steps.values() if "windows_launcher_follow_native.py cleanup" in step.get("run", "")]
    # The case and the requirement run only after success; the cleanup always.
    assert "if" not in run and "if" not in required and cleanup["if"] == "always()"
    for step in (run, cleanup):
        assert "$LASTEXITCODE -ne 0" in step["run"] and "throw" in step["run"]
    # Every check by number, the whole status, and no check that is not PASS.
    count = len(fixture.CHECKS)
    assert [name.split(".")[0] for name in fixture.CHECKS] == [str(number) for number in range(1, count + 1)]
    assert f"$report.checks.Count -ne {count}" in required["run"]
    assert "$report.status -ne 'PASS'" in required["run"] and "$_.status -ne 'PASS'" in required["run"]
    order = list(steps)
    assert order.index(run["name"]) < order.index(cleanup["name"]) < order.index("Require every check")
    assert sum(step.get("timeout-minutes", 0) for step in value["steps"]) <= value["timeout-minutes"] + 10


def test_the_report_is_complete_only_after_the_folder_is_removed(fixture):
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("def uninstall():")]
    cleanup = source[source.index("def cleanup():"):]
    # `run` never says PASS: the last check belongs to the cleanup.
    assert 'status="PASS"' not in run and 'status="PENDING"' in run and 'return report["status"] == "PENDING"' in run
    assert 'report.update(status="PASS", stage="Complete")' in cleanup
    assert 'all(check["status"] == "PASS" for check in report["checks"])' in cleanup
    assert "skip" not in source.lower().replace("skipped", "").replace("no check is\nskipped", "")


def test_the_workflow_starts_for_the_code_that_the_case_runs():
    document, _, _ = job()
    paths = set(document[True]["pull_request"]["paths"])  # YAML reads the key `on` as True.
    assert {".github/workflows/windows-launcher-follow.yml", "tests/fixtures/windows_launcher_follow_native.py",
            "tests/fixtures/windows_launcher_refresh_native.py", "tests/fixtures/windows_start_again_native.py",
            "tests/fixtures/manual_app_handoff_native.py", "amplifier_web/windows_launcher_follow.py",
            "amplifier_web/windows_launcher_refresh.py", "amplifier_web/windows_launcher_task.py",
            "amplifier_web/windows_launcher_files.py", "amplifier_web/windows_consumer_shell.py",
            "amplifier_web/windows_service.py", "amplifier_web/update_drain.py"} <= paths
    assert all((ROOT / path).is_file() for path in paths)


def test_versions_keep_their_order(fixture):
    number = lambda text: tuple(map(int, text.split(".")))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    # The earlier launcher is older than the candidate. The app is built as a
    # version above every release: a host that is behind a release updates itself.
    assert number(fixture.OLD["version"]) < number(project) < number(fixture.BUILT_AS)
    assert len(fixture.OLD["sha256"]) == 64 and len(fixture.OLD["revision"]) == 40
    assert "*" not in fixture.HELPER.strip("*").replace("*--automatic", "")
    from amplifier_web import windows_launcher_refresh as refresher
    # The pattern that finds the helper's processes names the helper's real command.
    assert "windows_launcher_refresh" in refresher.__name__ and '"--automatic"' in Path(refresher.__file__).read_text()


MODULES = {
    "components": "app_component_graph", "generations": "application_generations",
    "recovery": "windows_app_recovery", "consumer": "windows_consumer", "shell": "windows_consumer_shell",
    "windows_installation": "windows_installation", "listed_files": "windows_launcher_files",
    "refresher": "windows_launcher_refresh", "task": "windows_launcher_task",
    "windows_service": "windows_service", "runtime": "windows_task_runtime",
    "windows_uninstall": "windows_uninstall",
}


def test_every_product_name_that_the_fixture_reads_exists():
    # A wrong name fails only on the Windows runner, after 20 minutes. Find it here.
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"))
    imported = {(alias.asname or alias.name): alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "amplifier_web" for alias in node.names}
    assert {name: imported.get(name) for name in MODULES} == MODULES
    read = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in MODULES:
            read.setdefault(node.value.id, set()).add(node.attr)
    assert set(read) == set(MODULES)
    missing = sorted(f"{MODULES[alias]}.{name}" for alias, names in read.items() for name in names
                     if not hasattr(importlib.import_module("amplifier_web." + MODULES[alias]), name))
    assert missing == []
    for module, name in (("deployment", "write_private"), ("installation_health", "status"), ("updates", "process")):
        assert hasattr(importlib.import_module("amplifier_web." + module), name)
    # The two other fixtures, by the names that this one calls.
    for path, names in (("windows_launcher_refresh_native.py", {"helpers", "command", "require_ci"}),
                        ("windows_start_again_native.py", {"uninstall", "kept_folder_question", "remove_tree"}),
                        ("manual_app_handoff_native.py", {"canonical_temp", "token_user_default_owner"})):
        defined = {node.name for node in ast.parse((ROOT / "tests/fixtures" / path).read_text(encoding="utf-8")).body
                   if isinstance(node, ast.FunctionDef)}
        assert names <= defined, path
    # The release tool's functions that make the app's version commit and its source record.
    import inspect
    spec = importlib.util.spec_from_file_location("launcher_follow_release_names", ROOT / "scripts/release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    assert list(inspect.signature(release.replace_version).parameters) == ["text", "field", "old", "new"]
    assert list(inspect.signature(release.stage_source).parameters) == ["root", "repository", "revision", "version"]
    assert list(inspect.signature(release.version_at).parameters)[0] == "root"
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = tomllib.loads(project)["project"]["version"]
    assert release.version_at(ROOT) == version
    assert tomllib.loads(release.replace_version(project, "version", version, "0.999.0"))["project"]["version"] == "0.999.0"


def test_the_fixture_reads_the_update_state_that_the_app_saves(fixture, tmp_path):
    from amplifier_web import state_records
    database = sqlite3.connect(tmp_path / "app.sqlite3")
    database.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    state_records.initialize(database)
    base = {"revision": 1, "updates": {"phase": "idle"}}
    database.execute("INSERT INTO state VALUES (1, ?)", (json.dumps(base),))
    database.commit()
    assert fixture.saved_updates(tmp_path) == {"phase": "idle"}
    follow = {"phase": "failed", "launcher": {"version": "0.105.0"},
              "attempt": {"id": "a" * 32, "result": "rolled-back"}}
    state = {"revision": 2, "updates": {"phase": "idle", "launcherRefresh": follow}}
    state_records.save(database, state, {}, [], global_keys=["updates"])
    database.commit()
    assert fixture.saved_updates(tmp_path) == state["updates"]
    assert state_records.load(database)["updates"] == fixture.saved_updates(tmp_path)
    assert fixture.app_state(tmp_path)["launcherRefresh"] == follow
    state.pop("updates")
    state_records.save(database, state, {}, [], global_keys=["updates"])
    database.commit()
    assert fixture.saved_updates(tmp_path) == {}
    database.close()
    assert "errorType" in fixture.app_state(tmp_path / "absent")


def test_the_stand_in_for_uv_is_put_in_place_and_taken_away(fixture, tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "uv-stand-in.exe").write_bytes(b"stand-in")
    monkeypatch.setattr(fixture, "evidence", lambda: evidence)
    (tmp_path / "uv").mkdir()
    program = tmp_path / "uv/uv.exe"
    program.write_bytes(b"real")
    fixture.use_uv(tmp_path, None)
    assert program.read_bytes() == b"real" and fixture.calls(tmp_path) == []
    for mode in ("fail", "hang"):
        fixture.use_uv(tmp_path, mode)
        assert program.read_bytes() == b"stand-in" and (tmp_path / "uv/uv-real.exe").read_bytes() == b"real"
        assert (tmp_path / "uv/fixture-mode.txt").read_text() == mode
    (tmp_path / "uv/fixture-calls.txt").write_text("fail\nhang\n")
    fixture.use_uv(tmp_path, None)
    assert program.read_bytes() == b"real" and not (tmp_path / "uv/uv-real.exe").exists()
    assert fixture.calls(tmp_path) == ["fail", "hang"]
    # The stand-in refuses or waits only for a tool installation.
    assert 'args[0] == "tool" && args[1] == "install"' in fixture.STAND_IN and "uv-real.exe" in fixture.STAND_IN
