"""Start the installed Windows launcher again after a keep-folder uninstall.

Real on a Windows runner: the candidate installed with uv in setup's private
layout, the product's own entry and launcher commands in setup's order
(entries, then start), runtime preparation from the network, Task Scheduler,
the host that the task starts, the public uninstall, and the second start.

Not shown: the setup file and its window, a clean ordinary account (the
runner's account is elevated), the certificate question and the browser (the
launcher runs with --no-open and a console Python, so that no message box can
appear), a chat, a sign-out and sign-in. The AI account is a configured
provider row without a key; no model request is sent.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
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
    "8. Same port, same data, no new preparation",
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
    assert len(result.stdout) <= 64 * 1024, "Command output exceeded the evidence limit"
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


def launcher(root, *args, timeout=1800):
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


def uninstall(root, notices):
    from amplifier_web import windows_consumer, windows_installation
    original = windows_installation.owner_notice

    def yes(message, *, confirm=False):
        notices.append({"confirm": confirm, "message": message})
        return True

    windows_installation.owner_notice = yes
    try:
        return asyncio.run(windows_consumer.maintenance(root, "uninstall"))
    finally:
        windows_installation.owner_notice = original


def releases(root):
    path = root / "app/updates/releases"
    return sum(child.is_dir() for child in path.iterdir()) if path.exists() else 0


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
            assert not windows_service._read()[0]["exists"], "A pre-existing task must not be changed"
            begin(1)
            # Setup's order: the entries first, then the start.
            entries = launcher(root, "--integrate")
            assert entries["exitCode"] == 0 and shell.listed(root), entries
            first = launcher(root, "--no-open")
            report["firstStart"] = first
            state, status = task_facts(home), cli(root, "service", "status", "--json")
            if first["exitCode"] or not state["managed"] or not healthy(status):
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
            state, status = task_facts(home), cli(root, "service", "status", "--json")
            assert removed["ok"] and not state["managed"]
            assert not status["json"].get("responding", False) and not shell.listed(root)
            assert not asyncio.run(listener_answers(home)), "The HTTPS listener still answers"
            assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
            assert root.is_dir() and hashlib.sha256(marker.read_bytes()).hexdigest() == digest
            count = releases(root)
            passed(5, uninstall=removed, notices=notices, task=state, serviceStatus=status,
                   releaseCount=count, folderPresent=True, markerUnchanged=True, listenerAnswers=False)

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
            assert second["exitCode"] == 0 and second["json"]["background"] is True, second
            passed(7, launcher=second)

            begin(8)
            state, status = task_facts(home), cli(root, "service", "status", "--json")
            after_port, after_count = windows_installation.saved_port(home), releases(root)
            assert state["managed"] and healthy(status), (state, status)
            assert status["json"]["pid"] != first["json"]["health"]["pid"]  # A new host process.
            assert after_port == port and after_count == count
            assert hashlib.sha256(marker.read_bytes()).hexdigest() == digest
            assert installation_runtime._needs_connection(home) is False
            assert second["json"]["runtime"]["prepared"] is True
            passed(8, task=state, serviceStatus=status, port=after_port, releaseCount=after_count,
                   markerUnchanged=True, needsConnection=False, runtime=second["json"]["runtime"],
                   firstStartSeconds=first["seconds"], secondStartSeconds=second["seconds"],
                   runtimePrepare="Did not run. With an account configured and no active generation, "
                                  "runtime prepare first creates a folder in app\\updates\\releases. "
                                  "The count did not change.")

            begin(9)
            notices = []
            removed = uninstall(root, notices)
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
            if (root / shell.FILE).exists():
                shell.remove(root)
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