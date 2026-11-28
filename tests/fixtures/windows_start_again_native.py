"""Start the installed Windows launcher again after a keep-folder uninstall.

Real on a Windows runner: the candidate installed with uv in setup's private
layout, the product's own entry and launcher commands in setup's order
(entries, then start), runtime preparation from the network, Task Scheduler,
the host that the task starts, the product's uninstall function (called in
this process, with its one question answered No: uninstall, and keep the
folder), and the second start.

Not shown: the setup file and its window, a clean ordinary account (the
runner's account is elevated), the certificate question and the browser (the
launcher runs with --no-open and a console Python, so that no message box can
appear), a chat, a sign-out and sign-in. The AI account is a configured
provider row without a key; no model request is sent.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://github.com/microsoft/amplifier-unified"
CHECKS = (
    "1. Windows entries, then the first start",
    "2. Task, host, port and marker",
    "3. AI account configured without a key",
    "4. Runtime status with the account configured",
    "5. Keep-folder uninstall",
    "6. Windows entries again",
    "7. Second start",
    "8. Same port, marker and account kept, no preparation folder",
    "9. Final uninstall and folder cleanup",
)


def remove_tree(path):
    """Remove this case's own folder. Git marks its object files read-only."""
    def writable(function, target, error):
        os.chmod(target, stat.S_IWRITE)
        function(target)
    shutil.rmtree("\\\\?\\" + str(path), onexc=writable)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def shared():
    return load("start_again_refresh", ROOT / "tests/fixtures/windows_launcher_refresh_native.py")


def evidence():
    return Path(os.environ["RUNNER_TEMP"]).resolve() / "start-again-evidence"


def save(name, value):
    evidence().mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2) + "\n"
    assert len(raw.encode()) <= 96 * 1024, name
    (evidence() / name).write_text(raw, encoding="utf-8")


def read(name):
    return json.loads((evidence() / name).read_text(encoding="utf-8"))


def execute(argv, *, env=None, cwd=None, timeout=120):
    started = time.monotonic()
    result = subprocess.run(list(map(str, argv)), env=env, cwd=cwd, timeout=timeout,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        value = json.loads(result.stdout.strip())
    except ValueError:
        value = None
    return {"exitCode": result.returncode, "json": value,
            "stdout": result.stdout[-12000:], "stderr": result.stderr[-4000:],
            "seconds": round(time.monotonic() - started, 3)}


def prepare():
    report = {"status": "FAIL", "stage": "layout"}
    try:
        refresh = shared()
        helpers = refresh.helpers()
        git = lambda *args: subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
        assert not git("status", "--porcelain"), "The candidate checkout must be clean"
        revision = git("rev-parse", "HEAD")
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        root = helpers.canonical_temp() / ("start-again-" + uuid.uuid4().hex[:7])
        assert len(str(root)) <= 59, "Use a folder within setup's supported length"
        assert not root.exists()
        save("ownership.json", {"root": str(root)})
        files = load("start_again_files", ROOT / "amplifier_web/windows_files.py")
        for name in ("", "app", "app/updates", "shared", "ownership", "capture", "import",
                     "s", "workspace", "cache", "python", "python-bin", "tools",
                     "tool-bin", "temp", "uv", "git", "git/cmd"):
            with files.directory(root / name, owned_root=root, create=True):
                pass
        uv = shutil.which("uv")
        assert uv and shutil.which("git")
        env = {**os.environ, "UV_LINK_MODE": "copy"}
        with helpers.token_user_default_owner():
            report["stage"] = "candidate wheel"
            release = load("start_again_release", ROOT / "scripts/release.py")
            release.stage_source(ROOT, "microsoft/amplifier-unified", revision, version)
            refresh.command([uv, "build", "--wheel", "--out-dir", root / "build"],
                            env=env, cwd=ROOT, timeout=300)
            wheels = list((root / "build").glob("*.whl"))
            assert len(wheels) == 1
            wheel = root / wheels[0].name
            shutil.copyfile(wheels[0], wheel)
            report["stage"] = "candidate launcher installation"
            refresh.command([uv, "tool", "install", "--python", sys.executable,
                             "--no-build-package", "amplifier-core", wheel],
                            env={**env, "UV_TOOL_DIR": str(root / "tools"),
                                 "UV_TOOL_BIN_DIR": str(root / "tool-bin")},
                            cwd=root, timeout=900)
            shutil.copyfile(uv, root / "uv/uv.exe")
            # A lone Git wrapper cannot fetch. Real preparation needs its tree.
            git_tree = Path(git("--exec-path")).parents[2]
            assert (git_tree / "cmd/git.exe").is_file()
            shutil.copytree(git_tree, root / "git", dirs_exist_ok=True)
            git_check = execute([root / "git/cmd/git.exe", "--version"])
            assert git_check["exitCode"] == 0, git_check
            report["privateGit"] = git_check
        python = root / "tools/amplifier-unified/Scripts/python.exe"
        assert python.is_file()
        prepared = {"root": str(root), "version": version, "revision": revision,
                    "artifact": {"url": f"{SOURCE}/releases/download/v{version}/{wheel.name}",
                                 "path": str(wheel),
                                 "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}}
        save("prepared.json", prepared)
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write("START_AGAIN_PYTHON=" + str(python) + "\n")
        report.update(status="PASS", stage="prepared", revision=revision, version=version)
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error)[-1000:])
    save("preparation.json", report)
    return report["status"] == "PASS"


def cli(root, *args, timeout=120):
    return execute([root / "tools/amplifier-unified/Scripts/amplifier-unified.exe",
                    "--data-dir", root / "app", "--workspace", root / "workspace", *args],
                   cwd=root, timeout=timeout)


def launcher(root, *args, timeout=600):
    # Setup's own commands and working folder, with the console Python.
    return execute([sys.executable, "-I", "-B", "-m", "amplifier_web.windows_consumer",
                    "--root", root, *args], cwd=root / "workspace", timeout=timeout)


def healthy(result):
    value = result["json"] or {}
    return (result["exitCode"] == 0 and all(value.get(name) is True
            for name in ("ok", "processVerified", "responding")))


def task_facts(home):
    from amplifier_web import windows_service
    try:
        return {"managed": windows_service.managed(home), **windows_service.task_state(home)}
    except (OSError, ValueError, RuntimeError) as error:
        return {"managed": False, "errorType": type(error).__name__, "error": str(error)[-1000:]}


@contextmanager
def no_message_box():
    """Product code that opens a message box here fails the case at once.

    A message box waits for a person. On a runner it would hold the job until
    its time limit. The test answers the questions that it knows through the
    product's message function; any other message box is a changed product
    behaviour that this case must report, with its text.
    """
    import ctypes
    real = ctypes.WinDLL

    def refuse(owner, text, caption, flags):
        raise AssertionError("Product code opened a message box in this case: " + str(text)[:600])

    class User32:
        def __init__(self, library):
            self._library = library

        def __getattr__(self, name):
            return refuse if name == "MessageBoxW" else getattr(self._library, name)

    def load(name, *args, **kwargs):
        library = real(name, *args, **kwargs)
        return User32(library) if str(name).lower() in ("user32", "user32.dll") else library

    ctypes.WinDLL = load
    try:
        yield
    finally:
        ctypes.WinDLL = real


def guard_refuses(message_function):
    """Prove the guard on the product's real message function, without a wait."""
    seen = []

    def probe():
        try:
            message_function("message-box guard probe")
        except AssertionError as refused:
            seen.append(str(refused))
        except BaseException as other:
            seen.append("other failure: " + type(other).__name__)

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(15)
    return not thread.is_alive() and len(seen) == 1 and "message-box guard probe" in seen[0]


def uninstall(root, notices):
    """The product's uninstall, with the choice that keeps the folder.

    A finished installation has setup's completion record. The product then
    asks one question with three buttons (`windows_uninstall.question`): Yes
    leads to the deletion of the folder, No uninstalls and keeps the folder,
    Cancel changes nothing. This case answers No. It never answers Yes to
    that question, so it cannot delete the folder. Any other message is
    recorded and answered Yes, as before.
    """
    from amplifier_web import windows_consumer, windows_installation, windows_uninstall
    message, question = windows_installation.owner_notice, windows_uninstall.question

    def yes(text, *, confirm=False):
        notices.append({"kind": "message", "confirm": confirm, "message": text})
        return True

    def keep(text, *, cancel=False):
        notices.append({"kind": "question", "cancel": cancel, "answer": "no", "message": text})
        return "no"

    with no_message_box():
        assert guard_refuses(message), "The guard did not refuse the product's message box"
        assert guard_refuses(question), "The guard did not refuse the product's question"
        windows_installation.owner_notice, windows_uninstall.question = yes, keep
        try:
            return asyncio.run(windows_consumer.maintenance(root, "uninstall"))
        finally:
            windows_installation.owner_notice, windows_uninstall.question = message, question


def kept_folder_question(notices):
    """Exactly the one question of an uninstall, answered with the choice that keeps the folder.

    Required: one question and no message; three buttons; the first line of
    the uninstall question; the sentence that says what No does; the sentence
    that says what Cancel does. A message, a second question, a question
    with two buttons, or a question without those sentences is another case:
    it fails here. The whole text is not compared: Amplifier adds a
    sentence about the certificate when Windows trusts it.
    """
    return (len(notices) == 1 and notices[0]["kind"] == "question" and notices[0]["cancel"] is True
            and notices[0]["answer"] == "no"
            and notices[0]["message"].startswith("Uninstall Amplifier?")
            and "Select No to keep them. They stay in this folder, together with the program files:"
            in notices[0]["message"]
            and "Select Cancel to keep Amplifier installed." in notices[0]["message"])


def releases(root):
    """The names of the preparation folders. A replaced folder has another name."""
    path = root / "app/updates/releases"
    return sorted(child.name for child in path.iterdir() if child.is_dir()) if path.exists() else []


def running(state):
    # Task Scheduler's own answer, not only the host's health.
    return state.get("managed") is True and state.get("state") == "running" and state.get("instances") == 1


def process_identity(health):
    # Windows reuses process numbers. The creation time makes the identity.
    return health["pid"], health["created"]


def preparation_facts(root):
    """What a complete first installation left: its saved phase records, bounded."""
    path = root / "app/updates/releases"
    folders = sorted((child for child in path.iterdir() if child.is_dir()),
                     key=lambda child: child.stat().st_mtime) if path.exists() else []
    facts = {"releaseCount": len(folders)}
    if folders:
        for name in ("install-failure.json", "install-progress.json"):
            record = folders[-1] / name
            if record.exists():
                value = json.loads(record.read_text(encoding="utf-8"))
                if name == "install-progress.json":
                    value = [f'{event["phase"]}: {event["status"]}' for event in value["events"][-12:]]
                facts[name] = value
    return facts


async def listener_answers(home):
    """Check the pinned HTTPS listener even when no task is registered."""
    import aiohttp
    from amplifier_web import browser_entry
    from amplifier_web.deployment import load_server_config
    urls, pin = browser_entry._owner_probe_targets(home, load_server_config(home))
    assert isinstance(pin, aiohttp.Fingerprint) and urls
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3), trust_env=False) as client:
        for url in urls:
            assert url.startswith("https://")
            try:
                async with client.get(url.replace("/api/health", "/login"), ssl=pin,
                                      allow_redirects=False) as response:
                    await response.read()
                    return True
            except aiohttp.ClientConnectorError:
                pass
    return False


def fresh_user_environment():
    """Ask Windows for a new user's environment, not the test host's old PATH."""
    import ctypes as C
    from ctypes import wintypes as W
    kernel, adv, user = C.WinDLL("kernel32"), C.WinDLL("advapi32"), C.WinDLL("userenv")
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    adv.OpenProcessToken.restype = W.BOOL
    user.CreateEnvironmentBlock.argtypes = [C.POINTER(C.c_void_p), W.HANDLE, W.BOOL]
    user.CreateEnvironmentBlock.restype = W.BOOL
    user.DestroyEnvironmentBlock.argtypes = [C.c_void_p]
    token, block = W.HANDLE(), C.c_void_p()
    assert adv.OpenProcessToken(kernel.GetCurrentProcess(), 0xa, C.byref(token))
    try:
        assert user.CreateEnvironmentBlock(C.byref(block), token, False)
        try:
            text = C.cast(block, C.POINTER(C.c_wchar))
            rows, index = [], 0
            while text[index]:
                start = index
                while text[index]:
                    index += 1
                rows.append("".join(text[start:index]))
                index += 1
            return dict(row.split("=", 1) for row in rows if not row.startswith("="))
        finally:
            user.DestroyEnvironmentBlock(block)
    finally:
        kernel.CloseHandle(token)


def command_path_proof(root):
    """Real new cmd, Windows PowerShell, PowerShell 7 and Git Bash processes."""
    import ntpath
    from amplifier_web import windows_cli_path
    home, proof = root / "app", {}
    env = fresh_user_environment()
    env.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(root / "shared"),
               PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    system = Path(os.environ["SystemRoot"]) / "System32"
    cmd = system / "cmd.exe"
    windows_ps = system / "WindowsPowerShell/v1.0/powershell.exe"
    pwsh = Path("C:/Program Files/PowerShell/7/pwsh.exe")
    expected = str(home / "bin/amplifier-unified.cmd")
    found = execute([cmd, "/d", "/v:off", "/c", "where amplifier-unified"], env=env, cwd=root)
    assert found["exitCode"] == 0, found
    assert ntpath.normcase(found["stdout"].splitlines()[0]) == ntpath.normcase(expected), found
    help_cmd = execute([cmd, "/d", "/v:off", "/c", "amplifier-unified --help"], env=env, cwd=root)
    assert help_cmd["exitCode"] == 0 and "usage:" in help_cmd["stdout"], help_cmd
    proof["cmdHelp"] = "PASS: new cmd.exe found the stable command; --help returned 0"
    for name, ps in (("PowerShell51", windows_ps), ("PowerShell7", pwsh)):
        result = execute([ps, "-NoProfile", "-NonInteractive", "-Command",
            "$c=Get-Command amplifier-unified; Write-Output $c.Source; "
            "amplifier-unified --help; exit $LASTEXITCODE"], env=env, cwd=root)
        assert result["exitCode"] == 0 and expected.lower() in result["stdout"].lower(), result
        assert "usage:" in result["stdout"], result
        proof[name] = f"PASS: new {name} found the stable command; --help returned 0"
    # A missing setting returns a known nonzero status. Compare stderr to the
    # real executable to prove exact argument text and status, not just a launch.
    for argument in ('CLI space "quoted"', "CLI 100% ! ^ & text"):
        direct = execute([root / "tools/amplifier-unified/Scripts/amplifier-unified.exe",
            "--data-dir", home, "config", "get", argument], env=env, cwd=root)
        line = subprocess.list2cmdline(["amplifier-unified", "config", "get", argument])
        via = execute([cmd, "/d", "/v:off", "/s", "/c", line], env=env, cwd=root)
        assert direct["exitCode"] != 0 and via["exitCode"] == direct["exitCode"], (direct, via)
        assert via["stdout"] == direct["stdout"] and via["stderr"] == direct["stderr"], (direct, via)
    proof["argumentsAndExit"] = "PASS: spaces, quotes, %, !, ^ and &; failure output and exit code unchanged"
    # The saved, non-default data folder must be the one the bare command reads.
    expected_port = cli(root, "config", "get", "port")
    actual_port = execute([cmd, "/d", "/c", "amplifier-unified config get port"], env=env, cwd=root)
    assert actual_port["exitCode"] == 0 and actual_port["stdout"] == expected_port["stdout"]
    proof["dataFolder"] = "PASS: bare command read this installation's non-default data folder"
    bash = execute([root / "git/bin/bash.exe", "--noprofile", "--norc", "-c",
        'export PATH="$(cygpath -u "$CLI_BIN"):$PATH"; amplifier-unified --help'],
        env={**env, "CLI_BIN": str(home / "bin")}, cwd=root)
    assert bash["exitCode"] == 0 and "usage:" in bash["stdout"], bash
    proof["GitBash"] = "PASS: Git Bash ran the extensionless command; --help returned 0"
    assert windows_cli_path.doctor(home)["ok"] is True
    for line in proof.values():
        print(line, flush=True)
    return proof


def restore_runner_path(root):
    """Always restore the runner value byte for byte, including absence and type."""
    from amplifier_web import windows_cli_path
    backup = root / "runner-path-backup.json"
    if not backup.exists():
        return
    value, kind = json.loads(backup.read_text(encoding="utf-8"))
    with windows_cli_path.environment(write=True) as (handle, commit):
        current, current_kind = windows_cli_path._read(handle)
        if (current, current_kind) != (value, kind):
            windows_cli_path._set(handle, value, kind)
            commit()
    windows_cli_path._broadcast()
    with windows_cli_path.environment() as (handle, _):
        assert windows_cli_path._read(handle) == (value, kind)
    print("PASS: runner user Path restored exactly, including its type or absence", flush=True)


def run():
    report = {"status": "FAIL", "scheduledTask": "real", "stage": "records",
              "checks": [{"name": name, "status": "FAIL", "reason": "Not reached"} for name in CHECKS]}

    def begin(index):
        report["stage"] = CHECKS[index - 1]
        report["checks"][index - 1]["reason"] = "In progress"
        save("result.json", report)

    def passed(index, **facts):
        report["checks"][index - 1] = {"name": CHECKS[index - 1], "status": "PASS", **facts}
        save("result.json", report)

    try:
        prepared = read("prepared.json")
        root = Path(prepared["root"])
        home = root / "app"
        assert Path(sys.prefix) == root / "tools/amplifier-unified"
        # Bind homes before importing code that can read the runner's profile.
        os.environ.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(root / "shared"),
                          AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"),
                          AMPLIFIER_GITHUB_ACCESS="anonymous")
        from amplifier_web import app_component_graph as components
        from amplifier_web import installation_runtime, windows_consumer as consumer
        from amplifier_web import windows_consumer_shell as shell, windows_installation, windows_service
        from amplifier_web import windows_uninstall
        from amplifier_web.deployment import write_private
        from amplifier_web.updates import process
        with shared().helpers().token_user_default_owner():
            graph = asyncio.run(components.read_graph(process, sys.executable, artifact=prepared["artifact"]))
            write_private(root / consumer.RECEIPT, json.dumps({
                "schema": 1, "kind": "windows-consumer-preview",
                "version": prepared["version"], "revision": prepared["revision"],
                "artifact": prepared["artifact"], "graph": graph,
                "uv": str(root / "uv/uv.exe"), "git": str(root / "git/cmd/git.exe")}), owned_root=root)
            os.environ.update(consumer.environment(root, consumer.installation(root)[1]))
            # The fixture earns setup's proof; it does not forge one.
            report["audit"] = consumer.audit(root)
            assert report["audit"]["ok"]
            # Setup's completion record, where setup writes it: after the audit
            # and before the entries. Only with it does the uninstall offer its
            # choice, as on an installed PC. Without it the product asks the
            # question of a folder that setup did not complete.
            write_private(root / windows_uninstall.COMPLETE,
                          json.dumps({"schema": 1, "version": prepared["version"]}), owned_root=root)
            assert windows_uninstall.completed(root)
            assert not windows_service._read()[0]["exists"], "A pre-existing task must not be changed"
            begin(1)
            from amplifier_web import windows_cli_path
            with windows_cli_path.environment() as (handle, _):
                original_path = windows_cli_path._read(handle)
            write_private(root / "runner-path-backup.json", json.dumps(original_path), owned_root=root)
            # Setup's order: the entries first, then the start.
            entries = launcher(root, "--integrate")
            assert entries["exitCode"] == 0 and shell.listed(root), entries
            report["commandPath"] = command_path_proof(root)
            first = launcher(root, "--no-open")
            report["firstStart"] = first
            state, status = task_facts(home), cli(root, "service", "status", "--json")
            if first["exitCode"] or not running(state) or not healthy(status):
                log = home / "config/services/task-output.log"
                from amplifier_web.runtime_environment import receipt_directory
                receipt = receipt_directory(home)
                runtime_files = {}
                for name in ("install-failure.json", "install-progress.json"):
                    path = receipt / name
                    if path.exists():
                        runtime_files[name] = json.loads(path.read_text(encoding="utf-8"))
                attempt = {"launcher": first, "task": state, "serviceStatus": status,
                           "hresult": None,
                           "hresultNote": "The public launcher does not expose the scheduler receipt.",
                           "runtimeFiles": runtime_files,
                           "privateGitHelper": execute([root / "git/cmd/git.exe", "remote-https"]),
                           "runnerGitHelper": execute(["C:/Program Files/Git/bin/git.exe", "remote-https"]),
                           "privateGitExecPath": execute([root / "git/cmd/git.exe", "--exec-path"]),
                           "taskOutput": "\n".join(log.read_text(encoding="utf-8", errors="replace")
                                                  .splitlines()[-40:])[-12000:] if log.exists() else None}
                save("task-attempt.json", attempt)
                windows_service.uninstall(home)
                raise AssertionError("The real task attempt failed; inspect task-attempt.json")
            assert first["json"]["background"] is True
            passed(1, entries=entries, launcher=first)

            begin(2)
            port = windows_installation.saved_port(home)
            assert isinstance(port, int)
            marker = home / "start-again-marker.txt"
            write_private(marker, b"start again: retained data\n", owned_root=root)
            digest = hashlib.sha256(marker.read_bytes()).hexdigest()
            passed(2, task=state, serviceStatus=status, port=port, markerSha256=digest)

            begin(3)
            settings = {"config": {"providers": [{"module": "provider-openai", "config": {
                "default_model": "gpt-4.1"}}]}}
            path = root / "shared/settings.yaml"
            write_private(path, json.dumps(settings), owned_root=root / "shared")
            assert installation_runtime._needs_connection(home) is False
            passed(3, path=str(path), content=settings, needsConnection=False,
                   note="A configured provider row, not a verified or usable AI credential")

            begin(4)
            runtime = cli(root, "runtime", "status", "--json")
            report["configuredRuntimeStatus"] = runtime
            save("result.json", report)
            assert runtime["exitCode"] == 0, "Product finding: configured runtime status failed"
            assert runtime["json"]["ok"] is True and runtime["json"]["prepared"] is True, runtime
            passed(4, runtimeStatus=runtime)

            begin(5)
            notices = []
            removed = uninstall(root, notices)
            with windows_cli_path.environment() as (handle, _):
                assert windows_cli_path._read(handle) == original_path
            print("PASS: real keep-folder uninstall removed only its PATH addition; other entries identical",
                  flush=True)
            assert kept_folder_question(notices), notices
            state, status = task_facts(home), cli(root, "service", "status", "--json")
            assert removed["ok"] and not state["managed"]
            assert not status["json"].get("responding", False) and not shell.listed(root)
            assert not asyncio.run(listener_answers(home)), "The HTTPS listener still answers"
            assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
            # The keep choice began no removal of the folder, and setup's record is in place.
            assert not windows_uninstall.unfinished(root) and windows_uninstall.completed(root)
            assert root.is_dir() and hashlib.sha256(marker.read_bytes()).hexdigest() == digest
            folders = releases(root)
            passed(5, uninstall=removed, notices=notices, task=state, serviceStatus=status,
                   releaseCount=len(folders), folderPresent=True, markerUnchanged=True, listenerAnswers=False)

            begin(6)
            # As setup's second run: the entries come back before the start.
            entries = launcher(root, "--integrate")
            assert entries["exitCode"] == 0 and shell.listed(root), entries
            folder = Path(json.loads((root / shell.FILE).read_text())["folder"])
            assert {child.name for child in folder.iterdir()} == set(shell.LINKS)
            passed(6, entries=entries, shortcuts=4, listed=True)

            begin(7)
            # The state in which the earlier launcher prepared again: an
            # account is configured and no generation is active.
            assert installation_runtime._needs_connection(home) is False
            assert not (home / "updates/active.json").exists()
            second = launcher(root, "--no-open")
            report["secondStart"] = second
            if second["exitCode"]:
                # A start that prepared again leaves its records. Keep them.
                report["secondStartPreparation"] = preparation_facts(root)
            assert second["exitCode"] == 0 and second["json"]["background"] is True, second
            passed(7, launcher=second)

            begin(8)
            state, status = task_facts(home), cli(root, "service", "status", "--json")
            after_port, after_folders = windows_installation.saved_port(home), releases(root)
            # The failure handler saves these facts even when passed(8) is not reached.
            verification = dict(task=state, serviceStatus=status,
                                expectedPort=port, port=after_port,
                                expectedReleaseFolders=folders, releaseFolders=after_folders,
                                expectedMarkerSha256=digest)
            report["secondStartVerification"] = verification
            assert running(state) and healthy(status), (state, status)
            # A new host process, not the one that the uninstall stopped.
            assert process_identity(status["json"]) != process_identity(first["json"]["health"]), \
                "Second start retained the first host process identity"
            assert after_port == port and after_folders == folders, \
                "Saved port or preparation folders changed"
            verification["markerSha256"] = hashlib.sha256(marker.read_bytes()).hexdigest()
            assert verification["markerSha256"] == digest, "Saved marker changed"
            verification["needsConnection"] = installation_runtime._needs_connection(home)
            assert verification["needsConnection"] is False, "Configured account needs connection"
            assert second["json"]["runtime"]["prepared"] is True, "Second start runtime is not prepared"
            passed(8, task=state, serviceStatus=status, port=after_port, releaseCount=len(after_folders),
                   markerUnchanged=True, needsConnection=False, runtime=second["json"]["runtime"],
                   firstStartSeconds=first["seconds"], secondStartSeconds=second["seconds"],
                   runtimePrepare="Observed: no folder in app\\updates\\releases was added or replaced. "
                                  "With an account configured and no active generation, runtime "
                                  "prepare creates one as its first action.")

            begin(9)
            notices = []
            removed = uninstall(root, notices)
            assert kept_folder_question(notices), notices
            assert removed["ok"] and not windows_service.managed(home) and not shell.listed(root)
            assert not cli(root, "service", "status", "--json")["json"].get("responding", False)
            assert not asyncio.run(listener_answers(home)), "The final HTTPS listener still answers"
            # The cleanup step removes this Python environment after it exits.
            report["checks"][8].update(reason="Final uninstall passed; folder removal pending",
                                       uninstall=removed, notices=notices)
            report["stage"] = "Folder cleanup pending"
            report["status"] = "PENDING"
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error)[-2000:])
        for check in report["checks"]:
            if check.get("reason") == "In progress":
                check["reason"] = report["error"]
    save("result.json", report)
    print(json.dumps(report), flush=True)
    return report["status"] == "PENDING"


def cleanup():
    if not (evidence() / "ownership.json").exists():
        return True
    root = Path(read("ownership.json")["root"])
    python = root / "tools/amplifier-unified/Scripts/python.exe"
    if Path(sys.prefix) == root / "tools/amplifier-unified":
        from amplifier_web import windows_service, windows_consumer_shell as shell
        os.environ.update(AMPLIFIER_WEB_HOME=str(root / "app"), AMPLIFIER_HOME=str(root / "shared"))
        with shared().helpers().token_user_default_owner():
            # _read(home) refuses a task bound to any other installation.
            windows_service.uninstall(root / "app")
            restore_runner_path(root)
            folder = None
            if (root / shell.FILE).exists():
                folder = Path(json.loads((root / shell.FILE).read_text())["folder"])
                shell.remove(root)
            # Success only when nothing of this case is left registered.
            assert not windows_service.managed(root / "app"), "The sign-in task is still registered"
            assert shell.registry(root) is None, "The Apps-list entry is still registered"
            assert folder is None or not folder.exists(), "The Start-menu folder is still there"
        return True
    assert root.parent == shared().helpers().canonical_temp() and root.name.startswith("start-again-")
    if python.exists():
        result = execute([python, "-I", "-B", Path(__file__), "cleanup"], timeout=180)
        save("cleanup.json", result)
        if result["exitCode"]:
            return False
    remove_tree(root)
    assert not root.exists()
    save("cleanup.json", {"removed": True, "root": str(root)})
    if (evidence() / "result.json").exists():
        report = read("result.json")
        if report["status"] == "PENDING":
            report["checks"][8].update(status="PASS", reason="Final uninstall and folder removal passed",
                                       folderRemoved=True)
            assert all(check["status"] == "PASS" for check in report["checks"])
            report.update(status="PASS", stage="Complete")
            save("result.json", report)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "run", "cleanup"))
    shared().require_ci()
    raise SystemExit(0 if globals()[parser.parse_args().operation]() else 1)
