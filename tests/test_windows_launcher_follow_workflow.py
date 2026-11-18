"""The native "launcher follows the app" case: what can be checked without Windows.

The case itself runs only on a Windows runner (`windows-launcher-follow.yml`).
These cases fail on every platform when the workflow could pass with a failed
check, when the fixture reads a name that the product does not have, or when
the fixture's reader of the app's saved state does not match the app's writer.
The last two cases are about the launcher's portable cases themselves: a pull
request must run them, and the job that runs them must stop for a wrong result.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tomllib

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/windows-launcher-follow.yml"
FIXTURE = ROOT / "tests/fixtures/windows_launcher_follow_native.py"
LISTED = ROOT / ".github/workflows/other-test-files.yml"


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
    # The stand-in refuses or waits only for a tool installation into the launcher's folder.
    assert 'args[0] != "tool" || args[1] != "install"' in fixture.STAND_IN
    assert 'GetEnvironmentVariable("UV_TOOL_DIR")' in fixture.STAND_IN and 'Path.Combine(folder, "..", "tools")' in fixture.STAND_IN
    assert "uv-real.exe" in fixture.STAND_IN and "TrimEnd(separators)" in fixture.STAND_IN


# Test files that the listed files import and that `other-test-files.yml` does not run, each with
# the workflow whose pytest command runs it.
ELSEWHERE = {"tests/test_service.py": "coordination.yml",
             "tests/test_update_health_setup_record.py": "platform-mechanisms.yml",
             "tests/test_update_sequence.py": "update-ai-connection.yml",
             "tests/test_windows_service.py": "platform-mechanisms.yml"}


def starts_pytest(line, name):
    """A line of a step that starts pytest with this file: `uv run ... pytest` or `<python> ... -m pytest`.

    A comment, or another command that only has the words, is no run.
    """
    words = list(itertools.takewhile(lambda word: not word.startswith("#"), line.split()))
    if "pytest" not in words:
        return False
    before, after = words[:words.index("pytest")], words[words.index("pytest"):]
    starts = before[:2] == ["uv", "run"] or len(before) > 1 and before[-1] == "-m" and "python" in before[0].lower()
    return starts and any(word == name and after[index - 1] not in ("--ignore", "--deselect")
                          for index, word in enumerate(after))


def given_to_pytest(program):
    """The files and the single cases that the step's program gives pytest: its `FILES` and `CASES`.

    Also the names that its `NOT_ON_WINDOWS` leaves out. A file name anywhere else in the program is no run.
    """
    values = {node.targets[0].id: node.value for node in ast.parse(program).body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    files = values["FILES"].func.value.value.split()  # A text with one file in each line, and `.split()`.
    cases = ast.literal_eval(values["CASES"])
    left_out = [key.value for key in values["NOT_ON_WINDOWS"].keys]
    assert files and len(set(files)) == len(files) and all("::" in case for case in cases)
    # The program gives pytest exactly these, less what this runner leaves out.
    assert "files = [file for file in [*FILES, *CASES] if file not in left_out]" in program
    assert '*files, *(f"--deselect={name}" for name in left_out if "::" in name)]).returncode' in program
    return files, cases, left_out


def test_a_pull_request_runs_every_launcher_case_file():
    """A name in a path filter starts a workflow. Only a pytest command runs a file.

    Until `other-test-files.yml` no pull-request check ran the launcher's
    portable cases, and nobody saw that. A new `tests/test_windows_launcher_*.py`
    fails here until that workflow starts for it and runs it. The same holds for
    every file that the workflow lists: what it runs, and what those files read,
    starts it.
    """
    document = yaml.safe_load(LISTED.read_text(encoding="utf-8"))
    assert "continue-on-error" not in json.dumps(document)
    triggers = document[True]  # YAML reads the key `on` as True.
    paths = triggers["pull_request"]["paths"]
    assert triggers["push"] == {"branches": ["main"], "paths": paths} and "workflow_dispatch" in triggers
    # A new push to a pull request ends only that pull request's earlier run. A run by hand and a run
    # for a push to main are for the same commit; the event's name keeps them in two groups.
    assert document["concurrency"] == {
        "group": "other-test-files-${{ github.event_name }}-${{ github.event.pull_request.number || github.sha }}",
        "cancel-in-progress": True}
    (job,) = document["jobs"].values()
    assert job["strategy"]["matrix"] == {"os": ["ubuntu-latest", "windows-latest"]} and "if" not in job
    (step,) = [step for step in job["steps"] if "pytest" in step.get("run", "")]
    assert step == job["steps"][-1] and "if" not in step
    # The step ends with a failure for a failed case, an unexpected skip and a file with no passed case.
    for refusal in ('problems.append(f"failed: {name}")', "does not expect that on this runner",
                    "no case of this file passed", "this step expects a failure, and no case had it", "sys.exit(1)"):
        assert refusal in step["run"]
    files, cases, left_out = given_to_pytest(step["run"])
    run = sorted({*files, *(case.split("::")[0] for case in cases)})
    # The Windows job can leave out a listed file, or a case of one, by name. The two
    # files with cases that need Windows are never left out there as a whole.
    assert [name for name in left_out if name.split("::")[0] not in run] == []
    for kept in ("tests/test_browser_entry.py", "tests/test_windows_bootstrap_prototype.py"):
        assert kept in files and kept not in left_out

    def starts(path):
        return any(re.fullmatch(re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*"), path)
                   for pattern in paths)

    launcher = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("test_windows_launcher_*.py"))
    assert Path(__file__).resolve().relative_to(ROOT).as_posix() in launcher and len(launcher) >= 5
    assert [name for name in launcher if name not in files] == []
    assert [name for name in run if not (ROOT / name).is_file() or not starts(name)] == []
    # A launcher test file that does not exist yet starts the workflow too, and this case then fails for it.
    assert starts("tests/test_windows_launcher_a_name_that_no_file_has.py")
    # The list case fails when a change makes the launcher load another file,
    # so every file of the app and the lock start the workflow. Then what this
    # file reads beside the app.
    for needed in ("amplifier_web/windows_consumer.py", "amplifier_web/host/config.py", "uv.lock", "pyproject.toml",
                   "scripts/windows_launcher_files.py", "scripts/release.py",
                   WORKFLOW.relative_to(ROOT).as_posix(), FIXTURE.relative_to(ROOT).as_posix(),
                   *(f".github/workflows/{name}" for name in set(ELSEWHERE.values())),
                   "tests/fixtures/windows_launcher_refresh_native.py", "tests/fixtures/windows_start_again_native.py",
                   "tests/fixtures/manual_app_handoff_native.py", "tests/conftest.py"):
        assert (ROOT / needed).is_file() and starts(needed), needed
    # Every test file that a listed file imports, directly or through another one.
    reached, queue = set(), list(run)
    while queue:
        name = queue.pop()
        if name not in reached:
            reached.add(name)
            for node in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
                modules = ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                           else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                queue += [f"tests/{module}.py" for module in modules if (ROOT / f"tests/{module}.py").is_file()]
    assert [name for name in sorted(reached) if not starts(name)] == []
    # And the other way: the step runs every test file that the path filter names. A name below
    # `paths:` that nothing runs is the gap that this workflow closes. The only others are test
    # files that a run file imports. An import runs none of their cases, so each has a line that
    # starts pytest with it, in a step with no condition, in another workflow that starts for a
    # pull request. Not read here: the condition of that step's job, and whether that workflow
    # starts for a change of the file alone.
    named = [path for path in paths if path.startswith("tests/test_") and "*" not in path]
    assert sorted(set(named) - set(run)) == sorted(ELSEWHERE)
    for name, workflow in ELSEWHERE.items():
        assert name in reached, name
        other = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
        assert "pull_request" in other[True], workflow
        assert [job for job, body in other["jobs"].items() for step in body.get("steps", [])
                if "if" not in step and any(starts_pytest(line, name) for line in step.get("run", "").splitlines())
                ], (name, workflow)
    assert [path for path in paths if "*" not in path and not (ROOT / path).is_file()] == []


NEEDS_WINDOWS = "import pytest\n@pytest.mark.skip(reason='native prototype PowerShell model pending')\ndef test_one(): pass\n"


ONE_FAILS = "def test_one(): assert False\ndef test_two(): pass\n"
BY_NAME = "def test_one(): pass\n# The step gives pytest this case by its name, not the whole file.\n"
EXPECTED_ON_WINDOWS = "import pytest\n@pytest.mark.xfail(strict=True)\ndef test_one(): assert False\ndef test_two(): pass\n"


@pytest.mark.parametrize("body,windows,stops", [
    ("def test_one(): pass\n", False, None),
    # What the Windows job leaves out by name, Linux runs.
    (ONE_FAILS, True, None),
    (ONE_FAILS, False, "failed: tests/test_one.py::test_one"),
    ("def test_one(): assert False\n", False, "failed: tests/test_one.py::test_one"),
    ("import nothing_with_this_name\n", False, "no case of this file passed: tests/test_one.py"),
    ("import pytest\n@pytest.mark.skip(reason='another reason')\ndef test_one(): pass\ndef test_two(): pass\n",
     False, "skipped, and this step does not expect that on this runner: tests/test_one.py::test_one: another reason"),
    # A case that needs Windows may be skipped on Linux and never on Windows.
    (NEEDS_WINDOWS + "def test_two(): pass\n", False, None),
    (NEEDS_WINDOWS + "def test_two(): pass\n", True, "skipped, and this step does not expect that on this runner"),
    (NEEDS_WINDOWS, False, "no case of this file passed: tests/test_one.py"),
    # An expected failure that the step's list names only for Windows: Windows may have it, Linux not.
    (EXPECTED_ON_WINDOWS, True, None),
    (EXPECTED_ON_WINDOWS, False, "expected to fail, and this step does not expect that: tests/test_one.py::test_one"),
    # An expected failure that the list does not name.
    (EXPECTED_ON_WINDOWS.replace("test_one", "test_other"), True,
     "expected to fail, and this step does not expect that: tests/test_one.py::test_other"),
    # The list names an expected failure for this runner, and the case has none: the list is behind.
    ("def test_one(): pass\n", True, "this step expects a failure, and no case had it: tests/test_one.py::test_one"),
    (BY_NAME, True, "this step expects a failure, and no case had it: tests/test_one.py::test_one"),
])
def test_the_step_that_runs_the_listed_files_stops_for_a_wrong_result(tmp_path, body, windows, stops):
    """The step's own program, with one made-up test file in place of its list."""
    document = yaml.safe_load(LISTED.read_text(encoding="utf-8"))
    (job,) = document["jobs"].values()
    program = job["steps"][-1]["run"]
    leave = 'NOT_ON_WINDOWS = {"tests/test_one.py::test_one": "a reason"}' if body == ONE_FAILS else "NOT_ON_WINDOWS = {}"
    given = ('FILES = []', 'CASES = ["tests/test_one.py::test_one"]') if body == BY_NAME else (
        'FILES = ["tests/test_one.py"]', "CASES = []")
    for pattern, replacement in ((r'FILES = """.*?"""\.split\(\)', given[0]),
                                 (r"CASES = \[[^\]]*\]", given[1]),
                                 (r"NOT_ON_WINDOWS = \{.*?\n\}", leave),
                                 (r"EXPECTED_FAILURES = \{.*?\n\}", 'EXPECTED_FAILURES = {"tests/test_one.py::test_one": windows}'),
                                 (r'windows = sys\.platform == "win32"', f"windows = {windows}"),
                                 # The job requires CI's Python. This case is about the step's decisions.
                                 (r"assert sys\.version_info\[:2\] == \(3, 13\), sys\.version", "pass")):
        program, count = re.subn(pattern, lambda match: replacement, program, flags=re.S)
        assert count == 1, pattern
    (tmp_path / "tests/fixtures").mkdir(parents=True)
    (tmp_path / "tests/test_one.py").write_text(body, encoding="utf-8")
    # On Windows the step takes the account as the owner of new folders from this file.
    helpers = "tests/fixtures/manual_app_handoff_native.py"
    assert helpers in program
    (tmp_path / helpers).write_bytes((ROOT / helpers).read_bytes())
    (tmp_path / "step.py").write_text(program, encoding="utf-8")
    done = subprocess.run([sys.executable, "-B", str(tmp_path / "step.py")], cwd=tmp_path, capture_output=True, text=True,
                          timeout=120, env={**os.environ, "RUNNER_TEMP": str(tmp_path), "PYTEST_ADDOPTS": ""})
    assert "tests/test_one.py: " in done.stdout, done.stdout + done.stderr
    assert ("left out on this runner: tests/test_one.py::test_one: a reason" in done.stdout) == (body == ONE_FAILS and windows)
    if stops is None:
        assert done.returncode == 0 and "stop the job" not in done.stdout, done.stdout + done.stderr
    else:
        assert done.returncode == 1 and stops in done.stdout, done.stdout + done.stderr

