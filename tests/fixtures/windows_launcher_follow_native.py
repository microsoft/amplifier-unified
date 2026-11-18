"""CI-only native check: an installed PC with an earlier launcher gets the app's launcher by itself.

Real on a Windows runner: a published earlier release as the launcher in
setup's layout, with that launcher's own audit, Windows entries and starts;
the candidate as the app version; Task Scheduler; the earlier launcher's
supervisor; the app host; the helper task that the host registers; the helper;
uv; the Apps-list key in the registry.

The fixture's own part:
- The app version is the candidate plus one local commit that changes only
  the version, to 0.999.0. A host of the candidate's own version looks for a
  newer release and for newer components and installs them by itself: an app
  update of its own, in the middle of this case. An app that is newer than
  every release does not ("never downgrade a development host").
- It writes the records that an app update leaves: the generation's own
  record, the launch record, and the record that the new app's start was
  confirmed. The hand-over that writes them has its own native workflow.
  Three prepared generations of that app version stand for three app updates.
  In check 3 the app starts with the hand-over's record as an update leaves
  it (`prepared`), as after a real update; the fixture then confirms the
  start, in the place of the app's own readiness check.
- For the two failure cases it puts a program in the place of setup's private
  uv. That program fails, or waits, when it is asked to install a tool into
  the launcher's folder. Every other call goes to the real uv.
- In check 6 it ends the helper's processes. After that it only reads.

A second case, `run --case setting`, in a second job: the same installation
with "Keep Amplifier up to date" off. One build of the app can refresh the
launcher once, so the two cases cannot share a job. Its own part:
- It signs in as the owner's browser does: a ticket from the app's own entry
  for the owner (the launcher's `open` gets it there), the form token of the
  sign-in page, and the session cookie that the host gives for the two. With
  that session it sends the two requests that the bundled page sends: the
  switch ("settings.update", automatic installation off) and the install
  button ("updates.install"). tests/test_windows_launcher_follow_workflow.py
  reads both requests from the bundled page.
- The launcher installation is never made to fail there.

Not shown: the setup file, a standard account (the runner's account is
elevated), a sign-out, a restart of the PC, the Updates page in a browser
(the second case sends the page's requests; no browser draws the page or its
button), a message that is sent while Amplifier restarts, a count of
console windows, a helper that is ended after it started the app, the Arcade
layout. No AI connection exists, so the app starts no update of its own.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://github.com/microsoft/amplifier-unified"
# The earlier launcher: the publisher's release file, by its published digest.
OLD = {"version": "0.105.0", "sha256": "97df05087f93221f0831fd5b223da84d31ef2febbccdc2f3ea7d3b42b211df94",
       "revision": "c5cd4b57655f40992fe699ef8cc8c036726429b7"}
BUILT_AS = "0.999.0"
HELPER = "*windows_launcher_refresh*--automatic*"
# The app decides once a minute, and only when its update state is the same as
# at the pass before. 130 seconds with one state hold two such passes.
STABLE = 130
CHECKS = (
    "1. Earlier launcher: setup's records, its own audit, its Windows entries",
    "2. The earlier launcher starts the app version; before that version is confirmed, nothing starts by itself",
    "3. The app version is confirmed after the app started: only then the Apps list shows it",
    "4. Forced failure: the app asks by itself, the helper rolls back, the app runs on the earlier launcher",
    "5. An app that starts after the failure makes no second attempt for the same app version",
    "6. The helper is ended part-way: Task Scheduler's timed trigger puts the earlier launcher back",
    "7. The next app update: the launcher follows, and the app is ready on the same port",
    "8. No second refresh, and the new launcher's own start works",
    "9. The new launcher's uninstall removes a helper task; a helper task that was left removes itself",
    "10. Folder cleanup",
)
# The second case: "Keep Amplifier up to date" is off. Checks 1 and 2 are those of the first case.
SETTING_CHECKS = (
    *CHECKS[:2],
    "3. The owner's browser session turns \"Keep Amplifier up to date\" off; then the app version is confirmed",
    "4. With the setting off no launcher refresh starts by itself, and the page offers it",
    "5. The page's install request starts one refresh: the launcher follows, and the app is ready on the same port",
    "6. No second refresh, and the setting is still off",
    "7. The new launcher's uninstall leaves no task and no Apps-list entry",
    "8. Folder cleanup",
)
CASES = {"follow": CHECKS, "setting": SETTING_CHECKS}
# In the place of setup's private uv, for the failure cases only. It acts on
# "tool install" into the installation's own tools folder: the launcher. Every
# other call goes to the real program beside it.
STAND_IN = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;

static class UvStandIn
{
    static bool LauncherInstall(string folder, string[] args)
    {
        if (args.Length < 2 || args[0] != "tool" || args[1] != "install") return false;
        string target = Environment.GetEnvironmentVariable("UV_TOOL_DIR");
        if (String.IsNullOrEmpty(target)) return false;
        char[] separators = new char[] { '\\', '/' };
        string launcher = Path.GetFullPath(Path.Combine(folder, "..", "tools")).TrimEnd(separators);
        return String.Equals(Path.GetFullPath(target).TrimEnd(separators), launcher, StringComparison.OrdinalIgnoreCase);
    }

    static int Main(string[] args)
    {
        try
        {
            string folder = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            if (LauncherInstall(folder, args))
            {
                string mode = File.ReadAllText(Path.Combine(folder, "fixture-mode.txt")).Trim();
                File.AppendAllText(Path.Combine(folder, "fixture-calls.txt"), mode + "\n");
                if (mode == "hang") Thread.Sleep(Timeout.Infinite);
                Console.Error.WriteLine("fixture: the installation was refused");
                return 3;
            }
            string raw = Environment.CommandLine;
            int end = raw.StartsWith("\"") ? raw.IndexOf('"', 1) + 1 : raw.IndexOf(' ');
            ProcessStartInfo real = new ProcessStartInfo(Path.Combine(folder, "uv-real.exe"), end <= 0 ? "" : raw.Substring(end));
            real.UseShellExecute = false;
            using (Process child = Process.Start(real))
            {
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception)
        {
            return 4;
        }
    }
}
'''


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def shared():
    return load("launcher_follow_refresh", ROOT / "tests/fixtures/windows_launcher_refresh_native.py")


def again():
    return load("launcher_follow_start_again", ROOT / "tests/fixtures/windows_start_again_native.py")


def evidence():
    return Path(os.environ["RUNNER_TEMP"]).resolve() / "launcher-follow-evidence"


def save(name, value):
    evidence().mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2) + "\n"
    assert len(raw.encode()) <= 192 * 1024, name
    (evidence() / name).write_text(raw, encoding="utf-8")


def read(name):
    return json.loads((evidence() / name).read_text(encoding="utf-8"))


def execute(argv, *, cwd=None, timeout=120):
    started = time.monotonic()
    result = subprocess.run(list(map(str, argv)), cwd=cwd, timeout=timeout,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        value = None
    return {"exitCode": result.returncode, "json": value, "stdout": result.stdout[-3000:],
            "stderr": result.stderr[-3000:], "seconds": round(time.monotonic() - started, 1)}


def powershell(script, timeout=90):
    program = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    return subprocess.run([str(program), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=timeout)


def processes(pattern):
    """The process IDs whose command line matches. Only this case's own programs are named."""
    assert "'" not in pattern
    result = powershell("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '" + pattern
                        + "' -and $_.ProcessId -ne $PID } | ForEach-Object { $_.ProcessId }")
    assert result.returncode == 0, result.stderr[-1000:]
    return sorted(int(item) for item in result.stdout.split())


def end_processes(pattern):
    found = processes(pattern)
    for pid in found:
        # With its child processes. A process that is already gone is not an error.
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=60)
    return found


def use_uv(root, mode):
    """`None`: setup's real uv. `fail` or `hang`: the stand-in, for a tool installation only."""
    folder = root / "uv"
    program, real = folder / "uv.exe", folder / "uv-real.exe"
    if mode is None:
        if real.exists():
            for attempt in range(20):
                try:
                    program.unlink()
                    break
                except PermissionError:  # A stand-in that was ended a moment ago.
                    if attempt == 19:
                        raise
                    time.sleep(1)
            real.rename(program)
        return
    if not real.exists():
        program.rename(real)
        shutil.copyfile(evidence() / "uv-stand-in.exe", program)
    (folder / "fixture-mode.txt").write_text(mode, encoding="ascii")


def calls(root):
    path = root / "uv/fixture-calls.txt"
    return path.read_text(encoding="ascii").split() if path.exists() else []


def prepare():
    report = {"status": "FAIL", "stage": "layout"}
    try:
        refresh = shared()
        helpers = refresh.helpers()
        git = lambda *args: subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
        assert not git("status", "--porcelain"), "The candidate checkout must be clean"
        candidate = {"revision": git("rev-parse", "HEAD"),
                     "version": tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]}
        number = lambda text: tuple(map(int, text.split(".")))
        assert number(BUILT_AS) > number(candidate["version"]) > number(OLD["version"]), candidate
        version = BUILT_AS
        root = helpers.canonical_temp() / ("follow-" + uuid.uuid4().hex[:7])
        assert len(str(root)) <= 59, "Use a folder within setup's supported length"
        assert not root.exists()
        save("ownership.json", {"root": str(root)})
        files = load("launcher_follow_files", ROOT / "amplifier_web/windows_files.py")
        for name in ("", "app", "app/updates", "shared", "ownership", "capture", "import", "s", "workspace",
                     "cache", "python", "python-bin", "tools", "tool-bin", "temp", "uv", "git", "git/cmd"):
            with files.directory(root / name, owned_root=root, create=True):
                pass
        uv = shutil.which("uv")
        assert uv and shutil.which("git")
        env = {**os.environ, "UV_LINK_MODE": "copy"}
        installed = []
        with helpers.token_user_default_owner():
            report["stage"] = "earlier release file"
            old_wheel = root / f"amplifier_unified-{OLD['version']}-py3-none-any.whl"
            with urllib.request.urlopen(f"{SOURCE}/releases/download/v{OLD['version']}/{old_wheel.name}",
                                        timeout=120) as response:
                raw = response.read(64 * 1024 * 1024)
            assert hashlib.sha256(raw).hexdigest() == OLD["sha256"]
            old_wheel.write_bytes(raw)
            with zipfile.ZipFile(old_wheel) as archive:
                assert json.loads(archive.read("amplifier_web/release-source.json"))["revision"] == OLD["revision"]
            report["stage"] = "the app's version commit"
            release = load("launcher_follow_release", ROOT / "scripts/release.py")
            assert release.version_at(ROOT) == candidate["version"]
            for name, field in (("pyproject.toml", "version"), ("amplifier_web/__init__.py", "__version__")):
                path = ROOT / name
                ending = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
                text = path.read_text(encoding="utf-8")
                # As the release tool does: only the project table of pyproject.toml.
                block = re.search(r"(?ms)^\[project\][ \t]*\n.*?(?=^\[|\Z)", text) if field == "version" else None
                start, end = (block.start(), block.end()) if block else (0, len(text))
                text = text[:start] + release.replace_version(text[start:end], field, candidate["version"], version) + text[end:]
                path.write_text(text, encoding="utf-8", newline=ending)
            assert release.version_at(ROOT) == version
            subprocess.check_call(["git", "-c", "user.name=launcher-follow fixture",
                                   "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "--no-verify",
                                   "-m", "fixture: the app's version is " + version,
                                   "--", "pyproject.toml", "amplifier_web/__init__.py"], cwd=ROOT)
            revision = git("rev-parse", "HEAD")
            changed = git("diff", "--name-only", candidate["revision"], revision).split()
            assert sorted(changed) == ["amplifier_web/__init__.py", "pyproject.toml"], changed
            assert not git("status", "--porcelain")
            report["stage"] = "app wheel"
            release.stage_source(ROOT, "microsoft/amplifier-unified", revision, version)
            refresh.command([uv, "build", "--wheel", "--out-dir", root / "build"], env=env, cwd=ROOT, timeout=300)
            built = list((root / "build").glob("*.whl"))
            assert len(built) == 1 and built[0].name == f"amplifier_unified-{version}-py3-none-any.whl"
            report["stage"] = "earlier launcher installation"
            refresh.command([uv, "tool", "install", "--python", sys.executable, "--no-build-package",
                             "amplifier-core", old_wheel],
                            env={**env, "UV_TOOL_DIR": str(root / "tools"), "UV_TOOL_BIN_DIR": str(root / "tool-bin")},
                            cwd=root, timeout=900)
            for index in range(3):
                report["stage"] = f"app generation {index + 1}"
                generation = uuid.uuid4().hex
                folder = root / "app/updates/a" / generation
                folder.mkdir(parents=True)
                wheel = folder / built[0].name
                shutil.copyfile(built[0], wheel)
                refresh.command([uv, "tool", "install", "--python", sys.executable, "--no-build-package",
                                 "amplifier-core", wheel],
                                env={**env, "UV_TOOL_DIR": str(folder / "tools"), "UV_TOOL_BIN_DIR": str(folder / "bin")},
                                cwd=root, timeout=900)
                assert (folder / "tools/amplifier-unified/Scripts/pythonw.exe").is_file()
                installed.append({"generation": generation, "wheel": str(wheel),
                                    "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()})
            report["stage"] = "private helpers"
            shutil.copyfile(uv, root / "uv/uv.exe")
            # A lone Git wrapper cannot fetch. A real installation needs its tree.
            git_tree = Path(git("--exec-path")).parents[2]
            assert (git_tree / "cmd/git.exe").is_file()
            shutil.copytree(git_tree, root / "git", dirs_exist_ok=True)
            report["stage"] = "stand-in for uv"
            evidence().mkdir(parents=True, exist_ok=True)
            source = evidence() / "uv-stand-in.cs"
            source.write_text(STAND_IN, encoding="utf-8")
            compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
            compiled = subprocess.run([str(compiler), "/nologo", "/target:exe",
                                       "/out:" + str(evidence() / "uv-stand-in.exe"), str(source)],
                                      cwd=evidence(), capture_output=True, text=True, timeout=120)
            assert compiled.returncode == 0, compiled.stdout[-2000:]
            # Prove its behaviours here, before a product process depends on them.
            trial = evidence() / "stand-in-trial"
            for name in ("uv", "tools", "other"):
                (trial / name).mkdir(parents=True)
            shutil.copyfile(evidence() / "uv-stand-in.exe", trial / "uv/uv.exe")
            shutil.copyfile(uv, trial / "uv/uv-real.exe")
            (trial / "uv/fixture-mode.txt").write_text("fail", encoding="ascii")

            def stand_in(*args, tools=None):
                environment = {key: value for key, value in os.environ.items() if key != "UV_TOOL_DIR"}
                if tools is not None:
                    environment["UV_TOOL_DIR"] = str(trial / tools)
                return subprocess.run([str(trial / "uv/uv.exe"), *args], env=environment,
                                      capture_output=True, text=True, timeout=120)
            refused = stand_in("tool", "install", "a b", tools="tools")
            assert refused.returncode == 3 and (trial / "uv/fixture-calls.txt").read_text() == "fail\n", refused
            # An installation into another folder, and every other call, reach the real uv.
            for passed_on in (stand_in("tool", "install", "--help", tools="other"),
                              stand_in("tool", "install", "--help"), stand_in("--version")):
                assert passed_on.returncode == 0 and passed_on.stdout.strip(), passed_on
            assert stand_in("--version").stdout.startswith("uv ")
            assert (trial / "uv/fixture-calls.txt").read_text() == "fail\n"
            shutil.rmtree(trial)
        python = root / "app/updates/a" / installed[0]["generation"] / "tools/amplifier-unified/Scripts/python.exe"
        assert python.is_file() and (root / "tools/amplifier-unified/Scripts/pythonw.exe").is_file()
        save("prepared.json", {"root": str(root), "python": str(python), "generations": installed,
                               "old": {**OLD, "wheel": str(old_wheel)}, "candidate": candidate,
                               "new": {"version": version, "revision": revision}})
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write("FOLLOW_PYTHON=" + str(python) + "\n")
        report.update(status="PASS", stage="prepared", earlier=OLD["version"], candidate=candidate,
                      builtAs={"version": version, "revision": revision})
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error)[-1500:])
    save("preparation.json", report)
    return report["status"] == "PASS"


def bounded(value, limit=6000):
    raw = json.dumps(value, default=str)
    return value if len(raw) <= limit else {"truncated": raw[:limit]}


def saved(home, key):
    """One part of the state that the app last saved. Read only; the app keeps its database open."""
    database = sqlite3.connect((home / "app.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        row = database.execute("SELECT value FROM state_records WHERE kind='global' AND id=?", (key,)).fetchone()
        if row is not None:
            value = json.loads(row[0])
            return value["value"] if value["present"] else {}
        row = database.execute("SELECT value FROM state WHERE id=1").fetchone()
        return (json.loads(row[0]).get(key) or {}) if row else {}
    finally:
        database.close()


def saved_updates(home):
    """What the app last saved about updates."""
    return saved(home, "updates")


def app_state(home):
    """For the report: the update facts that explain a result."""
    try:
        value = saved_updates(home)
        status = value.get("currentStatus") or {}
        return bounded({**{key: value.get(key) for key in (
            "phase", "lastCheck", "lastAttempt", "available", "appAvailable", "pendingApp", "pendingRestart",
            "sequence", "launcherRefresh")},
            "currentStatus": {key: status.get(key) for key in ("status", "title", "detail")},
            "drain": (value.get("drain") or {}).get("phase")})
    except Exception as error:
        return {"errorType": type(error).__name__}


def run(case="follow"):
    names = CASES[case]
    report = {"status": "FAIL", "case": case, "scheduledTask": "real", "stage": "records",
              "checks": [{"name": name, "status": "FAIL", "reason": "Not reached"} for name in names]}

    def begin(index):
        report["stage"] = names[index - 1]
        report["checks"][index - 1]["reason"] = "In progress"
        save("result.json", report)

    def passed(index, **facts):
        report["checks"][index - 1] = {"name": names[index - 1], "status": "PASS", **facts}
        save("result.json", report)

    observe = None
    try:
        prepared = read("prepared.json")
        root, old, new = Path(prepared["root"]), prepared["old"], prepared["new"]
        report.update(earlier=old["version"], candidate=prepared["candidate"], builtAs=new)
        home, launcher = root / "app", root / "tools/amplifier-unified"
        folders = [home / "updates/a" / row["generation"] for row in prepared["generations"]]
        prefixes = [folder / "tools/amplifier-unified" for folder in folders]
        assert os.path.samefile(sys.prefix, prefixes[0]), "Run with the Python of the first prepared app generation"
        # Bind homes before importing code that can read the runner's profile.
        os.environ.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(root / "shared"),
                          AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"),
                          AMPLIFIER_GITHUB_ACCESS="anonymous")
        from amplifier_web import app_component_graph as components, application_generations as generations
        from amplifier_web import auth, browser_entry
        from amplifier_web import windows_app_recovery as recovery, windows_consumer as consumer
        from amplifier_web import windows_consumer_shell as shell, windows_installation
        from amplifier_web import windows_launcher_files as listed_files, windows_launcher_follow as offer
        from amplifier_web import windows_launcher_refresh as refresher
        from amplifier_web import windows_launcher_task as task, windows_service
        from amplifier_web import windows_task_runtime as runtime, windows_uninstall
        from amplifier_web.deployment import load_server_config, write_private
        from amplifier_web.installation_health import status
        from amplifier_web.updates import process
        launcher_python = launcher / "Scripts/python.exe"

        def launcher_command(*args, timeout=600):
            # The launcher's own program, as its Start-menu entries start it, with the console Python.
            return execute([launcher_python, "-I", "-B", "-m", "amplifier_web.windows_consumer",
                            "--root", root, *args], cwd=root / "workspace", timeout=timeout)

        def record(name):
            try:
                return refresher.read_json(home, name)
            except Exception as error:
                return {"errorType": type(error).__name__}

        def main_task():
            try:
                value, spec = windows_service._read(home)
                return {"managed": spec is not None, "state": value.get("state"),
                        "instances": value.get("instances"), "lastResult": value.get("lastResult")}
            except Exception as error:
                return {"errorType": type(error).__name__}

        def helper_task():
            try:
                value, spec = task.query(home)
                return {"exists": value["exists"], "state": value.get("state"), "instances": value.get("instances"),
                        "lastResult": value.get("lastResult"), "request": spec.request if spec else None,
                        "registered": spec.registered if spec else None}
            except Exception as error:
                return {"errorType": type(error).__name__}

        def helper_xml():
            """For the report only: the helper task as Task Scheduler holds it."""
            try:
                sid = windows_service._owner()
                value = windows_service._scheduler("query", sid, name=task.task_name(sid))
                return value.get("xml", "")[:6000] if value["exists"] else None
            except Exception as error:
                return {"errorType": type(error).__name__}

        def launcher_now():
            try:
                facts = refresher.launcher_facts(launcher)
                return {"version": facts["version"], "revision": facts["revision"], "digest": facts["identity"]["digest"]}
            except Exception as error:
                return {"errorType": type(error).__name__}

        def apps_list():
            # The registry reader gives each value with its type: (text, 1).
            values = shell.registry(root)
            return values["DisplayVersion"][0] if values else None

        def set_aside():
            return sorted(path.name for path in root.glob("launcher-*"))

        def observe():
            journal = record(refresher.JOURNAL) or {}
            log = home / "config/services/task-output.log"
            return bounded({
                "request": record(refresher.REQUEST), "helperRecord": record(refresher.RECORD),
                "journal": {key: journal.get(key) for key in (
                    "registration", "request", "previous", "from", "to", "wasRunning", "done", "errorType")},
                "kept": record(refresher.KEPT), "mainTask": main_task(), "helperTask": helper_task(),
                "helperTaskXml": helper_xml(),
                "helperProcesses": processes(HELPER), "launcher": launcher_now(), "setAside": set_aside(),
                "appsList": apps_list(), "uvCalls": calls(root), "appState": app_state(home),
                "taskOutput": log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.exists() else None,
            }, 40000)

        def wait(what, check, timeout):
            deadline = time.monotonic() + timeout
            while True:
                try:
                    value = check()
                except (OSError, ValueError, RuntimeError, sqlite3.Error):
                    value = None  # A record that is being replaced. Read it again.
                if value:
                    return value
                assert time.monotonic() < deadline, "Not seen in time: " + what
                time.sleep(2)

        def health():
            return asyncio.run(status(home, wait=60))

        def healthy(value):
            return all(value.get(name) is True for name in ("ok", "processVerified", "responding"))

        def follow_state():
            # The app writes its database at any moment. Read again when it is busy.
            for attempt in range(10):
                try:
                    return saved_updates(home).get("launcherRefresh") or {}
                except sqlite3.Error:
                    if attempt == 9:
                        raise
                    time.sleep(1)

        def unhurried(read):
            # As follow_state: the app writes its database at any moment.
            for attempt in range(10):
                try:
                    return read()
                except sqlite3.Error:
                    if attempt == 9:
                        raise
                    time.sleep(1)

        def switches():
            """The two update switches as the app saved them."""
            return unhurried(lambda: (saved(home, "settings") or {}).get("updates") or {})

        def automatic():
            # The app's own rule for "Keep Amplifier up to date": both switches, and each is on when it is absent.
            value = switches()
            return value.get("autoCheck", True) and value.get("autoInstall", True)

        def page_row():
            """The headline that the app gives the Updates page: the saved current status."""
            return unhurried(lambda: saved_updates(home).get("currentStatus") or {})

        def page_request(action, args):
            """One request of the Updates page, from a browser session of the owner.

            The ticket comes from the app's own entry for the owner, as the
            launcher's `open` gets it. The sign-in page gives the form token,
            and the host gives the session cookie for the two. The action then
            goes with that cookie only, as the page sends it. The report gets
            the host's answer and no ticket, token or cookie.
            """
            from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TCPConnector

            async def send():
                config = load_server_config(home)
                _, pin = browser_entry._owner_probe_targets(home, config)
                url = await browser_entry.owner_browser_url(home, config)
                origin, separator, ticket = url.partition("/login#ticket=")
                assert separator and ticket, "The owner's entry gave no ticket"
                async with ClientSession(connector=TCPConnector(ssl=pin), trust_env=False, cookie_jar=DummyCookieJar(),
                                         timeout=ClientTimeout(total=120)) as client:
                    async with client.get(origin + "/login") as page:
                        assert page.status == 200, page.status
                        token = page.cookies[auth.CSRF_COOKIE].value
                    headers = {"Origin": origin, "Cookie": f"{auth.CSRF_COOKIE}={token}"}
                    async with client.post(origin + browser_entry.REDEEM_PATH, headers=headers,
                                           data={"csrf": token, "ticket": ticket}, allow_redirects=False) as entry:
                        assert entry.status == 303, entry.status
                        session = entry.cookies[auth.SESSION_COOKIE].value
                    headers = {"Origin": origin, "Cookie": f"{auth.SESSION_COOKIE}={session}"}
                    async with client.post(origin + "/api/actions", headers=headers,
                                           json={"action": action, "args": args, "id": uuid.uuid4().hex}) as answer:
                        value = await answer.json()
                        return {"action": action, "args": args, "status": answer.status,
                                "error": value.get("error"), "accepted": value.get("accepted")}
            return asyncio.run(send())

        def settle(what):
            """Wait until the app had two passes with one update state. Then it has decided."""
            def snapshot():
                value = saved_updates(home)
                sequence = value.get("sequence") or {}
                return (value.get("phase"), sequence.get("stage"), sequence.get("nextStage"),
                        value.get("lastCheck"), value.get("lastAttempt"), value.get("installedAt"))
            deadline, since, last = time.monotonic() + 900, time.monotonic(), None
            while True:
                try:
                    now = snapshot()
                except sqlite3.Error:
                    now = last
                if now != last:
                    last, since = now, time.monotonic()
                busy = last is None or last[0] in {"checking", "staging", "validating", "staged", "app-staged", "activating"}
                if not busy and time.monotonic() - since >= STABLE:
                    return {"phase": last[0], "stage": last[1], "nextStage": last[2], "seconds": STABLE}
                assert time.monotonic() < deadline, "The app's update state did not settle: " + what
                time.sleep(5)

        def request_after(*earlier):
            def check():
                value = record(refresher.REQUEST)
                return value if value and value.get("id") and value["id"] not in earlier else None
            return check

        def closed(identity):
            def check():
                value = record(refresher.REQUEST)
                return value if value and value.get("id") == identity and value.get("status") == "closed" else None
            return check

        def stop():
            try:
                windows_service.command("stop", home)
            except RuntimeError as slow:
                # The command waits 10 seconds for the task after the host has ended.
                # This case is about the refresh: record a slower stop, and wait for it.
                report.setdefault("slowStops", []).append(str(slow)[:200])
            wait("the app has stopped", lambda: main_task().get("instances") == 0, 120)

        with shared().helpers().token_user_default_owner():
            begin(1)
            earlier = refresher.launcher_facts(launcher)
            assert (earlier["version"], earlier["revision"]) == (old["version"], old["revision"])
            old_artifact = {"url": f"{SOURCE}/releases/download/v{old['version']}/{Path(old['wheel']).name}",
                            "sha256": old["sha256"], "path": old["wheel"]}
            old_graph = asyncio.run(components.read_graph(process, str(launcher_python), artifact=old_artifact))
            assert len([row for row in old_graph if row["name"] == "amplifier-unified"
                        and (row["version"], row["revision"]) == (old["version"], old["revision"])]) == 1
            write_private(root / consumer.RECEIPT, json.dumps({
                "schema": 1, "kind": "windows-consumer-preview", "version": old["version"],
                "revision": old["revision"], "artifact": old_artifact, "graph": old_graph,
                "uv": str(root / "uv/uv.exe"), "git": str(root / "git/cmd/git.exe")}), owned_root=root)
            # The earlier launcher earns setup's proof itself; the fixture does not forge one.
            audit = launcher_command("--audit")
            assert audit["exitCode"] == 0 and audit["json"]["ok"] is True, audit
            write_private(root / windows_uninstall.COMPLETE,
                          json.dumps({"schema": 1, "version": old["version"]}), owned_root=root)
            assert not windows_service._read()[0]["exists"], "A pre-existing task must not be changed"
            assert helper_task() == {"exists": False, "state": None, "instances": None, "lastResult": None,
                                     "request": None, "registered": None}, "A pre-existing helper task must not be changed"
            entries = launcher_command("--integrate")
            assert entries["exitCode"] == 0 and shell.listed(root), entries
            assert apps_list() == old["version"]
            # Task Scheduler takes the helper task's definition, and the product reads
            # it back as its own. Checked here first: every later check needs it.
            probe = uuid.uuid4().hex
            try:
                task.ensure(home, probe, uuid.uuid4().hex, str(prefixes[0] / "Scripts/pythonw.exe"))
                value, definition = task.query(home)
            except BaseException:
                report["helperTaskXml"] = helper_xml()
                raise
            assert definition.request == probe and value["instances"] == 0, (definition, value)
            assert task.remove_task(home) and helper_task().get("exists") is False
            passed(1, launcher=launcher_now(), audit=audit["json"], appsList=apps_list(),
                   helperTaskXml=value["xml"][:6000])

            begin(2)
            validated = []
            for index, row in enumerate(prepared["generations"]):
                folder = generations.directory(home, new["revision"], row["generation"])
                assert refresher._same(folder, folders[index])
                wheel = Path(row["wheel"])
                artifact = {"url": f"{SOURCE}/releases/download/v{new['version']}/{wheel.name}",
                            "sha256": row["sha256"], "path": str(wheel)}
                graph = asyncio.run(components.read_graph(
                    process, str(prefixes[index] / "Scripts/python.exe"), artifact=artifact))
                app = next(item for item in graph if item["name"] == "amplifier-unified")
                assert (app["version"], app["revision"]) == (new["version"], new["revision"])
                value = {"revision": new["revision"], "version": new["version"], "generation": row["generation"],
                         "attemptId": uuid.uuid4().hex, "extras": [], "componentGraph": graph,
                         "componentDigest": components.digest(graph), "artifact": artifact,
                         "installation": generations.bootstrap_identity(prefixes[index])}
                (folder / "validated.json").write_text(json.dumps(value))
                (folder / "components.txt").write_text(components.requirements(graph), encoding="utf-8")
                validated.append(value)
            generations.promote(home, validated[0], launcher)
            first = launcher_command("--no-open", timeout=1500)
            report["firstStart"] = first
            started = health()
            if first["exitCode"] or not healthy(started):
                report["observed"] = observe()
                raise AssertionError("The earlier launcher did not start the candidate app version")
            assert first["json"]["background"] is True
            port = windows_installation.saved_port(home)
            _, spec = windows_service._read(home)
            assert isinstance(port, int) and spec is not None
            assert os.path.samefile(Path(spec.python).parent.parent, launcher) and spec.supervised
            # The manual command's own decision, on this real layout and this real task.
            decision = asyncio.run(refresher.refresh(refresher.TaskService(home), home, check=True))
            assert decision["ok"] and decision["action"] == "refresh" and not decision["changed"], decision
            # A launcher from before this change lacks files that the list names
            # now. The comparison reports them, and the launcher differs.
            difference = listed_files.differs(launcher, prefixes[0])
            assert difference["differs"] is True and difference["files"], difference
            # The app is newer than the launcher and its files differ. No record
            # says that this app version's start was confirmed: nothing starts.
            assert recovery.read(spec) is None
            # The host saves what it compared. Only then is "no request" a decision.
            compared = wait("the app has compared the launcher with itself", lambda: (lambda value: (
                value.get("app") or {}).get("generation") == validated[0]["generation"] and value)(follow_state()), 600)
            assert compared["launcher"] == {"version": old["version"]} and compared.get("phase") is None, compared
            assert not compared.get("attempt") and not compared.get("requested"), compared
            decided = settle("before the app version is confirmed")
            assert follow_state() == compared, follow_state()
            assert record(refresher.REQUEST) is None and helper_task().get("exists") is False
            assert apps_list() == old["version"] and refresher.launcher_facts(launcher) == earlier
            assert healthy(health()) and not calls(root)
            passed(2, seconds=first["seconds"], port=port, health=started, decision=decision["message"],
                   difference=difference, compared=compared, decided=decided, appState=app_state(home))

            def update(index, uv_mode, status="acknowledged"):
                """What an app update leaves, written with the app stopped. Then the launcher's own start."""
                host_run = runtime.read(spec)
                assert host_run and host_run["phase"] == "listening", host_run
                stop()
                use_uv(root, uv_mode)
                if generations.active(home)["generation"] != validated[index]["generation"]:
                    generations.promote(home, validated[index], launcher)
                bootstrap = generations.bootstrap_identity(launcher)
                recovery._write(spec, {
                    "schema": 1, "registration": spec.registration_id,
                    "restart": {"schema": 1, "run": host_run, "target": {
                        key: validated[index][key] for key in ("revision", "generation", "attemptId")}},
                    "promoted": recovery.pointer(generations.active(home)), "previous": None,
                    "bootstrap": bootstrap, "installation": bootstrap,
                    "identity": {"version": old["version"], "revision": old["revision"],
                                 "instanceId": uuid.uuid4().hex},
                    "graph": old_graph, "extras": [], "artifact": old_artifact, "status": status})
                assert recovery.read(spec)["status"] == status
                again_started = launcher_command("--no-open")
                if again_started["exitCode"]:
                    # The launcher waits 60 seconds for the app. A first start of a
                    # new generation's files can take longer on a runner.
                    report.setdefault("slowStarts", []).append({"generation": index, **again_started})
                try:
                    now = wait("the app is ready after the update records",
                               lambda: (lambda value: healthy(value) and value)(health()), 300)
                except AssertionError:
                    report["start"] = again_started
                    raise
                assert generations.active(home)["generation"] == validated[index]["generation"]
                assert windows_installation.saved_port(home) == port
                return now

            def rolled_back(identity, result):
                """After a failed refresh: everything is as before it, and the app runs."""
                assert result["result"] == "rolled-back", result
                helper = record(refresher.RECORD)
                assert (helper["request"], helper["state"], helper["result"]) == (identity, "settled", "rolled-back"), helper
                assert refresher.unfinished(home) is None
                assert refresher.launcher_facts(launcher) == earlier, "The earlier launcher is not in place"
                assert not set_aside(), set_aside()
                assert not (root / f"amplifier_unified-{new['version']}-py3-none-any.whl").exists()
                assert json.loads((root / consumer.RECEIPT).read_text())["version"] == old["version"]
                version = execute([launcher_python, "-I", "-c", "import amplifier_web; print(amplifier_web.__version__)"])
                assert version["exitCode"] == 0 and version["stdout"].strip() == old["version"], version
                now = wait("the app is ready on the earlier launcher", lambda: (lambda value: healthy(value) and value)(health()), 240)
                assert windows_installation.saved_port(home) == port
                assert windows_service._read(home)[1] == spec, "The start-at-sign-in task changed"
                wait("the helper task is removed", lambda: helper_task().get("exists") is False, 240)
                return helper, now

            def followed(request, result, prefix):
                """After a refresh that worked: the launcher is the app's, and everything else is as before."""
                assert result["result"] == "refreshed", result
                helper = record(refresher.RECORD)
                assert (helper["request"], helper["state"], helper["result"]) == (request["id"], "settled", "refreshed"), helper
                fresh = refresher.launcher_facts(launcher)
                assert (fresh["version"], fresh["revision"]) == (new["version"], new["revision"])
                assert fresh["identity"]["prefix"] == earlier["identity"]["prefix"], "The launcher is at another path"
                assert generations.active(home)["bootstrap"] == fresh["identity"], "The launch record names another launcher"
                assert refresher.unfinished(home) is None
                previous = Path(helper["previous"])
                assert generations.bootstrap_identity(previous)["digest"] == earlier["identity"]["digest"]
                assert set_aside() == [previous.name]
                # The helper writes this record after it released the refresh lock.
                wait("the record of the kept launcher folder",
                     lambda: record(refresher.KEPT) == {"schema": 1, "paths": [str(previous)]}, 60)
                assert json.loads((root / consumer.RECEIPT).read_text())["version"] == new["version"]
                assert (root / consumer.QUALIFICATION).is_file()
                wait("the app is ready on the new launcher", lambda: (lambda value: healthy(value) and value)(health()), 240)
                assert windows_installation.saved_port(home) == port and windows_service._read(home)[1] == spec
                assert apps_list() == new["version"] and shell.listed(root)
                wait("the helper task is removed", lambda: helper_task().get("exists") is False, 240)
                difference = listed_files.differs(launcher, prefix)
                assert difference == {"differs": False, "files": [], "packages": [], "unreadable": []}, difference
                return helper, fresh, previous, difference

            begin(3)
            # As after a real update: the app starts, and the record does not say yet
            # that this start is confirmed. The app must not take that for an answer.
            # Only the first case makes the launcher installation fail.
            confirmed = update(0, "fail" if case == "follow" else None, status="prepared")
            undecided = settle("before the app's start is confirmed")
            assert recovery.read(spec)["status"] == "prepared"
            assert apps_list() == old["version"] and not follow_state().get("appsList"), follow_state()
            assert record(refresher.REQUEST) is None and helper_task().get("exists") is False
            assert not follow_state().get("attempt") and not calls(root)
            switched = {}
            if case == "setting":
                # The switch of the Updates page, before the app can decide anything about the launcher.
                assert automatic(), switches()
                switch = page_request("settings.update", {"patch": {"updates": {"autoInstall": False}}})
                assert switch["status"] == 200 and not switch["error"], switch
                wait("the app has saved the switch", lambda: not automatic(), 60)
                assert switches().get("autoCheck", True) is True and switches().get("autoInstall") is False, switches()
                switched = {"switch": switch, "switches": switches()}
            confirmed_at = time.time()
            assert recovery.acknowledge(spec, {
                key: validated[0][key] for key in ("revision", "generation", "attemptId")})
            wait("the Apps list shows the confirmed app version", lambda: apps_list() == new["version"], 300)
            assert json.loads((root / shell.FILE).read_text())["registry"]["DisplayVersion"] == new["version"]
            assert shell.listed(root)
            # Only the version changed: every other value of the key is as setup wrote it.
            assert shell.registry(root) == shell._typed_registry(shell.registration_values(root, new["version"]))
            written = wait("the app has saved what it did with the Apps list",
                           lambda: follow_state().get("appsList"), 120)
            assert written == {"settled": None, "result": {
                "changed": True, "from": old["version"], "to": new["version"]}}, written
            passed(3, appsList=apps_list(), written=written, launcher=launcher_now(), undecided=undecided, **switched)

            if case == "setting":
                # The second case, to its end. The setting is off, and nothing makes the installation fail.
                begin(4)
                wait("the app has saved that the refresh waits for a person",
                     lambda: follow_state().get("phase") == "ready", 600)
                offer_seconds = round(time.time() - confirmed_at)
                # The app decides at each pass, one a minute. With the setting on, its request comes
                # at the first pass after the confirmation: the first case measures 49 to 50 seconds.
                # The offer is saved at that same pass. From here `settle` holds two more passes.
                decided = settle("with the setting off")
                state = follow_state()
                assert not automatic(), switches()
                assert state.get("phase") == "ready" and not state.get("attempt") and not state.get("requested"), state
                assert record(refresher.REQUEST) is None and helper_task().get("exists") is False
                assert not processes(HELPER) and not calls(root)
                assert refresher.launcher_facts(launcher) == earlier and not set_aside()
                assert healthy(health())
                # The launcher's own offer is the page's headline, with the page's install action.
                ready, row = offer._TEXT["ready"], page_row()
                assert (row.get("status"), row.get("title"), row.get("action")) == (ready[0], ready[1], ready[3]), row
                assert old["version"] in row["detail"] and offer._BUTTON in row["detail"], row
                passed(4, offerSeenSecondsAfterConfirmation=offer_seconds,
                       secondsWithNoRequest=round(time.time() - confirmed_at), decided=decided,
                       switches=switches(), pageRow=row, appState=app_state(home))

                begin(5)
                pressed_at = time.time()
                pressed = page_request("updates.install", {})
                assert pressed["status"] == 200 and not pressed["error"], pressed
                one = wait("the request that the page's install action started", request_after(), 300)
                request_seconds = round(one["at"] - pressed_at)
                assert request_seconds >= 0, (one["at"], pressed_at)
                assert one["target"]["generation"] == validated[0]["generation"] and one["registration"] == spec.registration_id
                settled = wait("the request is closed", closed(one["id"]), 1500)
                helper, fresh, previous, difference = followed(one, settled, prefixes[0])
                # The app saved this attempt before the refresh, with no result. Wait for the result.
                done = wait("the app has saved the result", lambda: (lambda value: (
                    value.get("attempt") or {}).get("id") == one["id"] and value["attempt"].get("result")
                    and value)(follow_state()), 120)
                assert done["attempt"]["result"] == "refreshed" and done["phase"] == "done", done
                # A person's request, not the app's own decision. It is used up, and the setting is as it was.
                assert done["attempt"]["automatic"] is False and not done.get("requested"), done
                assert not automatic() and not calls(root)
                passed(5, action=pressed, secondsFromActionToRequest=request_seconds, helperRecord=helper,
                       launcher=launcher_now(), keptLauncher=previous.name, port=port, appsList=apps_list(),
                       difference=difference, appState=app_state(home))

                begin(6)
                decided = settle("after the refresh that the page asked for")
                state = follow_state()
                assert record(refresher.REQUEST) == settled
                assert helper_task().get("exists") is False and not processes(HELPER)
                assert refresher.launcher_facts(launcher) == fresh and set_aside() == [previous.name]
                assert state.get("attempt") == done["attempt"] and not state.get("requested"), state
                assert not automatic() and not calls(root) and healthy(health())
                passed(6, decided=decided, switches=switches(), appState=app_state(home))

                begin(7)
                removed = execute([launcher_python, "-I", "-B", Path(__file__), "uninstall"], timeout=600)
                report["uninstall"] = removed
                assert removed["exitCode"] == 0 and removed["json"]["question"] is True, removed
                assert not windows_service.managed(home) and not shell.listed(root)
                assert helper_task().get("exists") is False and not processes(HELPER)
                passed(7)
                report.update(status="PENDING", stage="Folder cleanup pending")
                return True

            begin(4)
            one = wait("the app's own request", request_after(), 600)
            # The app that already ran asks by itself: from the confirmation to its request.
            # A measurement for the report. The one condition: the request is not from before the confirmation.
            request_seconds = round(one["at"] - confirmed_at)
            assert request_seconds >= 0, (one["at"], confirmed_at)
            assert one["target"]["generation"] == validated[0]["generation"] and one["registration"] == spec.registration_id
            asked = wait("the helper task", lambda: (lambda value: value.get("exists") and value)(helper_task()), 60)
            assert asked["request"] == one["id"], asked
            settled = wait("the request is closed", closed(one["id"]), 900)
            helper, now = rolled_back(one["id"], settled)
            assert helper["step"] == "installing the launcher" and helper["stoppedApp"] is True, helper
            assert calls(root) == ["fail"], calls(root)
            assert (now["pid"], now["created"]) != (confirmed["pid"], confirmed["created"])
            assert apps_list() == new["version"]
            failed = wait("the app has saved the result", lambda: (lambda value: (
                value.get("attempt") or {}).get("result") == "rolled-back" and value)(follow_state()), 120)
            assert failed["phase"] == "failed" and failed["attempt"]["id"] == one["id"], failed
            assert failed["attempt"]["generation"] == validated[0]["generation"], failed
            assert failed["attempt"]["automatic"] is True and failed["attempt"]["step"] == "installing the launcher", failed
            passed(4, request={key: one.get(key) for key in ("id", "registration", "target")},
                   secondsFromConfirmationToRequest=request_seconds, helperTask=asked, helperRecord=helper,
                   launcher=launcher_now(), port=port, appState=app_state(home))

            begin(5)
            # An app that a person starts after the failure has only the saved attempt.
            stop()
            restarted = launcher_command("--no-open")
            assert restarted["exitCode"] == 0 and healthy(health()), restarted
            decided = settle("after the failed refresh")
            assert record(refresher.REQUEST) == settled
            assert helper_task().get("exists") is False and not processes(HELPER)
            assert calls(root) == ["fail"] and refresher.launcher_facts(launcher) == earlier
            assert follow_state().get("attempt") == failed["attempt"] and follow_state().get("phase") == "failed"
            assert healthy(health())
            passed(5, decided=decided, appState=app_state(home))

            begin(6)
            update(1, "hang")
            two = wait("the app's request for the next app version", request_after(one["id"]), 600)
            assert two["target"]["generation"] == validated[1]["generation"]
            registered = wait("the helper task", lambda: (lambda value: value.get("exists") and value)(helper_task()), 60)
            assert registered["request"] == two["id"], registered
            wait("the earlier launcher is set aside and the installation runs",
                 lambda: calls(root) == ["fail", "hang"] and not launcher_python.exists(), 600)
            aside = set_aside()
            helper = record(refresher.RECORD)
            assert len(aside) == 1 and (helper["request"], helper["state"]) == (two["id"], "stopped"), (aside, helper)
            assert main_task().get("instances") == 0, "The app must be down in the refresh"
            ended = end_processes(HELPER)
            assert ended, "No helper process was found"
            end_processes("*" + str(root / "uv/uv.exe") + "*")
            wait("the helper has ended", lambda: not processes(HELPER) and helper_task().get("instances") == 0, 90)
            ended_at = time.monotonic()
            # The state that the earlier command left with no way back: the app is
            # down, the launcher is aside. From here this fixture only reads.
            assert refresher.journal_kind(home, spec.registration_id) == "live"
            assert main_task().get("instances") == 0 and not launcher_python.exists()
            assert helper_task().get("exists") is True and record(refresher.REQUEST)["status"] == "open"
            settled_two = wait("Task Scheduler starts the helper again, and the request is closed", closed(two["id"]), 900)
            seconds = round(time.monotonic() - ended_at)
            helper, _ = rolled_back(two["id"], settled_two)
            assert helper["attempts"] >= 1, helper
            assert calls(root) == ["fail", "hang"]
            saved = wait("the app has saved the result", lambda: (lambda value: (
                value.get("attempt") or {}).get("id") == two["id"] and value)(follow_state()), 120)
            assert saved["attempt"]["result"] == "rolled-back" and saved["phase"] == "failed", saved
            passed(6, helperTask=registered, endedProcesses=ended, secondsFromEndToClosed=seconds,
                   helperRecord=helper, launcher=launcher_now(), appState=app_state(home))

            begin(7)
            update(2, None)
            three = wait("the app's request for the next app version", request_after(one["id"], two["id"]), 600)
            assert three["target"]["generation"] == validated[2]["generation"]
            settled_three = wait("the request is closed", closed(three["id"]), 1500)
            helper, fresh, previous, difference = followed(three, settled_three, prefixes[2])
            assert calls(root) == ["fail", "hang"]
            done = wait("the app has saved the result", lambda: (lambda value: (
                value.get("attempt") or {}).get("id") == three["id"] and value)(follow_state()), 120)
            assert done["attempt"]["result"] == "refreshed" and done["phase"] == "done", done
            passed(7, helperRecord=helper, launcher=launcher_now(), keptLauncher=previous.name, port=port,
                   appsList=apps_list(), difference=difference, appState=app_state(home))

            begin(8)
            before = health()
            stop()
            start = launcher_command("--no-open")
            after = health()
            assert start["exitCode"] == 0 and start["json"]["background"] is True and healthy(after), (start, after)
            assert (after["pid"], after["created"]) != (before["pid"], before["created"])
            assert windows_installation.saved_port(home) == port
            decided = settle("after the refresh")
            assert record(refresher.REQUEST) == settled_three
            assert helper_task().get("exists") is False and not processes(HELPER)
            assert refresher.launcher_facts(launcher) == fresh and follow_state().get("attempt") == done["attempt"]
            assert calls(root) == ["fail", "hang"] and healthy(health())
            passed(8, decided=decided, startSeconds=start["seconds"], appState=app_state(home))

            begin(9)
            # A helper task that is registered at the uninstall, as a host registers
            # it. It is not started; its first timed start is 5 minutes away.
            helper_python = str(prefixes[2] / "Scripts/pythonw.exe")
            task.ensure(home, uuid.uuid4().hex, spec.registration_id, helper_python)
            assert helper_task().get("exists") is True
            removed = execute([launcher_python, "-I", "-B", Path(__file__), "uninstall"], timeout=600)
            report["uninstall"] = removed
            assert removed["exitCode"] == 0 and removed["json"]["question"] is True, removed
            assert not windows_service.managed(home) and not shell.listed(root)
            assert helper_task().get("exists") is False, "The uninstall left the helper task"
            # A launcher from before this change cannot remove the helper task.
            # One is left here, with no main task. Its own next start removes it.
            task.ensure(home, uuid.uuid4().hex, spec.registration_id, helper_python)
            left = helper_task()
            assert left.get("exists") is True, left
            task.start(home)
            wait("the helper removes its own task", lambda: helper_task().get("exists") is False, 180)
            wait("the helper has ended", lambda: not processes(HELPER), 60)
            report["checks"][8] = {"name": CHECKS[8], "status": "PASS", "leftTask": left}
            report.update(status="PENDING", stage="Folder cleanup pending")
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error)[-2000:])
        trace, here = error.__traceback__, None
        while trace is not None:
            if trace.tb_frame.f_code.co_filename == __file__:
                here = trace.tb_lineno
            trace = trace.tb_next
        report["line"] = here
        if observe is not None and "observed" not in report:
            try:
                report["observed"] = observe()
            except BaseException as failed:
                report["observed"] = {"errorType": type(failed).__name__}
        for check in report["checks"]:
            if check.get("reason") == "In progress":
                check["reason"] = report["error"]
    finally:
        # Also for the second case, which ends inside the block above.
        save("result.json", report)
        print(json.dumps(report), flush=True)
    return report["status"] == "PENDING"


def uninstall():
    """Run with the new launcher's own Python: the product's uninstall, with the choice that keeps the folder."""
    root = Path(read("prepared.json")["root"])
    assert os.path.samefile(sys.prefix, root / "tools/amplifier-unified")
    os.environ.update(AMPLIFIER_WEB_HOME=str(root / "app"), AMPLIFIER_HOME=str(root / "shared"),
                      AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"), AMPLIFIER_GITHUB_ACCESS="anonymous")
    start_again, notices = again(), []
    with shared().helpers().token_user_default_owner():
        result = start_again.uninstall(root, notices)
    print(json.dumps({"result": result, "notices": notices,
                      "question": start_again.kept_folder_question(notices)}), flush=True)
    return result.get("ok") is True


def cleanup():
    if not (evidence() / "ownership.json").exists():
        return True
    root = Path(read("ownership.json")["root"])
    python = Path(read("prepared.json")["python"]) if (evidence() / "prepared.json").exists() else None
    if python is not None and python.exists() and os.path.samefile(sys.prefix, python.parent.parent):
        # With an app generation's Python: the launcher folder can be aside or absent after a failed case.
        home = root / "app"
        os.environ.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(root / "shared"),
                          AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"))
        from amplifier_web import windows_consumer_shell as shell, windows_launcher_task as task, windows_service
        with shared().helpers().token_user_default_owner():
            # Both readers refuse a task that is bound to any other installation.
            windows_service.uninstall(home)
            end_processes(HELPER)
            assert task.retire_task(home), "The helper task is still registered"
            if (root / shell.FILE).exists():
                shell.remove(root)
            assert not windows_service.managed(home), "The sign-in task is still registered"
            assert task.query(home)[0]["exists"] is False, "The helper task is still registered"
            assert shell.registry(root) is None, "The Apps-list entry is still registered"
        return True
    assert root.parent == shared().helpers().canonical_temp() and root.name.startswith("follow-")
    if python is not None and python.exists():
        result = execute([python, "-I", "-B", Path(__file__), "cleanup"], timeout=300)
        save("cleanup.json", result)
        if result["exitCode"]:
            return False
    if root.exists():
        # Only processes that name this case's own folder: a stand-in that still waits, or a host that was left.
        end_processes("*" + str(root) + "*")
        time.sleep(3)
        again().remove_tree(root)
    assert not root.exists()
    save("cleanup.json", {"removed": True, "root": str(root)})
    if (evidence() / "result.json").exists():
        report = read("result.json")
        if report["status"] == "PENDING":
            names = CASES[report["case"]]  # The last check of each case is the folder.
            assert len(report["checks"]) == len(names)
            report["checks"][-1] = {"name": names[-1], "status": "PASS", "folderRemoved": True}
            assert all(check["status"] == "PASS" for check in report["checks"])
            report.update(status="PASS", stage="Complete")
            save("result.json", report)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operation", choices=("prepare", "run", "uninstall", "cleanup"))
    parser.add_argument("--case", choices=tuple(CASES), default="follow", help="For `run`: which case")
    shared().require_ci()
    options = parser.parse_args()
    done = run(options.case) if options.operation == "run" else globals()[options.operation]()
    raise SystemExit(0 if done else 1)
