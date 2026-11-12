"""Installed native logon-host check in an explicit new private test root.

This fixture reuses an accepted host dependency fixture: it does NOT qualify the
clean-machine bootstrap. No original app roots, credentials, browser or provider
are used. The real CLI and real Task Scheduler own the test host on port 8953.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time


PUBLIC = set("SYSTEMROOT WINDIR SYSTEMDRIVE COMSPEC PATHEXT OS COMPUTERNAME USERNAME USERDOMAIN USERPROFILE HOMEDRIVE HOMEPATH APPDATA LOCALAPPDATA PROGRAMDATA PROCESSOR_ARCHITECTURE PROCESSOR_IDENTIFIER NUMBER_OF_PROCESSORS".split())


def environment(root, old):
    env = {k: v for k, v in os.environ.items() if k.upper() in PUBLIC}
    env.update({
        "PATH": str(root / ".venv/Scripts") + ";" + str(Path(sys._base_executable).parent)
                + r";C:\Program Files\Git\cmd;C:\Windows\System32;C:\Windows",
        "AMPLIFIER_WEB_HOME": str(root / "app"), "AMPLIFIER_HOME": str(root / "shared"),
        "AMPLIFIER_SESSION_STATE_HOME": str(root / "ownership"),
        "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(root / "capture"),
        "AMPLIFIER_UNIFIED_IMPORT_HOME": str(root / "import"),
        "TEMP": str(root / "temp"), "TMP": str(root / "temp"),
        "UV_CACHE_DIR": str(old / "cache/uv"), "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_CONFIG": "1", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "NUL", "GIT_TERMINAL_PROMPT": "0",
    })
    return env


async def install(root, old, wheel, expected):
    from amplifier_web.windows_jobs import WindowsOwnedProcess
    from amplifier_web.windows_files import directory
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == expected
    assert not (root / ".venv").exists(), "Do not replay the installer"
    for name in ("app", "shared", "ownership", "capture", "import", "workspace", "temp", "proof"):
        with directory(root / name, owned_root=root, create=True):
            pass
    env = environment(root, old)
    uv = old / "candidates/0590-d2163beda/.venv/Scripts/uv.exe"
    receipts = []
    async def run(label, argv):
        job = await WindowsOwnedProcess.start(list(map(str, argv)), cwd=root, env=env,
                                              trusted_python=sys._base_executable)
        job.stdin.close()
        stdout, stderr = await asyncio.gather(job.stdout.read(), job.stderr.read())
        result = await job.wait()
        (root / "proof" / (label + ".stdout")).write_bytes(stdout)
        (root / "proof" / (label + ".stderr")).write_bytes(stderr)
        row = {"label": label, "identity": asdict(job.identity), "result": asdict(result)}
        receipts.append(row)
        (root / "proof/install.json").write_text(json.dumps(receipts, indent=2))
        print(json.dumps(row), flush=True)
        assert result.payload_returncode == 0 and result.active_processes == 0
        assert job.tree_empty and job.io_finalized
    python = root / ".venv/Scripts/python.exe"
    await run("venv", [uv, "venv", "--python", sys._base_executable, python.parent.parent])
    deps = old / "candidates/b62b43d05/artifacts"
    await run("dependencies", [uv, "pip", "install", "--python", python, "--offline", "--no-index",
                              "--no-deps", "--no-build", "--link-mode", "copy",
                              "--find-links", deps / "wheelhouse", "-r", deps / "frozen64-requirements.txt"])
    await run("root", [uv, "pip", "install", "--python", python, "--offline", "--no-index",
                      "--no-deps", "--no-build", "--link-mode", "copy", wheel])
    await run("pipcheck", [uv, "pip", "check", "--python", python])
    await run("version", [root / ".venv/Scripts/amplifier-unified.exe", "--version"])
    print(json.dumps({"nativeInstalled": True, "wheelSha256": expected,
                      "dependencyScope": "reused frozen64 host fixture; not clean bootstrap"}))


def exercise(root, old, *, resume=False):
    from amplifier_web import windows_service
    from amplifier_web.windows_task_runtime import observe
    env = environment(root, old)
    os.environ.clear()
    os.environ.update(env)
    cli = root / ".venv/Scripts/amplifier-unified.exe"
    scope = [str(cli), "--data-dir", str(root / "app"), "--workspace", str(root / "workspace")]
    rows = []
    proof = root / "proof" / ("exercise-" + str(time.time_ns()))
    proof.mkdir()
    def run(label, args, *, expect=0):
        result = subprocess.run([*scope, *args], cwd=root / "workspace", env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
        (proof / (label + ".stdout")).write_bytes(result.stdout)
        (proof / (label + ".stderr")).write_bytes(result.stderr)
        rows.append({"label": label, "returncode": result.returncode,
                     "stdoutBytes": len(result.stdout), "stderrBytes": len(result.stderr)})
        (proof / "exercise.json").write_text(json.dumps(rows, indent=2))
        assert result.returncode == expect, label
        return result.stdout
    current, existing = windows_service._read()
    if resume:
        assert existing and existing.data_dir == str(root / "app")
        assert current["state"] == 3 and current["instances"] == 0
        # Registration already happened. Do not repeat configuration, TLS,
        # registration, or overwrite the original failed receipt.
        run("start-existing", ["service", "start"])
    else:
        assert existing is None and current["exists"] is False, "Never adopt or replace another task"
        for key, value in (("port", "8953"), ("bind", "[127.0.0.1]"),
                           ("public_origins", "[https://127.0.0.1:8953]")):
            run("config-" + key, ["config", "set", key, value])
        run("tls", ["setup-tls"])
        # No automatic/browser/certificate trust action.
        run("install", ["service", "install"])
    task, spec = windows_service._read(root / "app")
    assert spec and spec.launch_roots["AMPLIFIER_HOME"] == str(root / "shared")
    try:
        first = json.loads(run("ready", ["service", "status", "--wait", "60", "--json"]))
        assert first["ok"] and first["processVerified"] and first["responding"]
        record = observe(spec)
        assert record and record["pid"] == first["pid"]
        assert windows_service.owned_spec(task["xml"], spec.sid) == spec
        # Public status/read leaves the exact server alive. CLI exits without
        # sharing its lifetime with the task.
        second = json.loads(run("ready-again", ["service", "status", "--json"]))
        assert second["pid"] == first["pid"] and second["created"] == first["created"]
        run("stop", ["service", "stop"])
        assert observe(spec) is None
        stopped = windows_service.task_state(root / "app")
        assert stopped["instances"] == 0
        run("start", ["service", "start"])
        restarted = json.loads(run("restarted", ["service", "status", "--json"]))
        assert restarted["ok"] and restarted["created"] != first["created"]
        run("logs", ["service", "logs"])
        run("stop-final", ["service", "stop"])
        assert observe(spec) is None
        run("uninstall", ["service", "uninstall"])
        assert windows_service._read()[1] is None
        print(json.dumps({"nativeLogonHost": "passed", "registered": True, "ready": True,
                          "normalStop": True, "restartNewIdentity": True, "privateLogs": True,
                          "removed": True, "noBrowserProviderOrOriginalState": True}))
    finally:
        # Query exact definition before cleanup, never overwrite a changed task.
        _, actual = windows_service._read()
        if actual is not None:
            assert actual == spec, "Task changed; preserve custody for explicit reconciliation"
            windows_service.uninstall(root / "app")


def setup_flow(root, old):
    """Exercise the installed setup composer without a browser or real account."""
    from amplifier_web import windows_service, windows_installation, installation_runtime
    from amplifier_web.windows_task_runtime import observe
    env = environment(root, old)
    # Keep the previously installed dependency resolver available; accountless
    # prepare must only inspect its recorded graph on this invocation.
    env["PATH"] = str(old / "candidates/0590-d2163beda/.venv/Scripts") + ";" + env["PATH"]
    os.environ.clear()
    os.environ.update(env)
    assert windows_service._read()[1] is None, "Do not adopt an existing task"
    baseline = root / "app/updates/baseline-runtime"
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in baseline.iterdir() if p.is_file()}
    result = None
    proof = root / "proof" / ("setup-flow-" + str(time.time_ns()))
    proof.mkdir()
    try:
        result = asyncio.run(windows_installation.prepare_background(
            root, root / ".venv/Scripts/amplifier-unified.exe", env, port=8953))
        assert result["created"] and result["background"]
        assert result["runtime"]["prepared"] and result["runtime"]["connectionRequired"]
        assert result["runtime"]["workerReady"] is False
        task, spec = windows_service._read(root / "app")
        assert spec and task["instances"] == 1
        first = observe(spec)
        assert first and first["pid"] == result["health"]["pid"]
        second = asyncio.run(windows_installation.prepare_background(
            root, root / ".venv/Scripts/amplifier-unified.exe", env, port=8953))
        assert second["created"] is False and second["runtime"] is None
        assert observe(spec) == first
        assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in baseline.iterdir() if p.is_file()}
        assert installation_runtime.status(root / "app")["workerReady"] is False
        (proof / "result.json").write_text(json.dumps({
            "first": result, "reopened": second, "sameRun": True,
            "dependencyReceiptsUnchanged": True, "noBrowserOrModelRequest": True}, indent=2))
    finally:
        task, spec = windows_service._read(root / "app")
        if spec is not None:
            assert Path(spec.python).parent == root / ".venv/Scripts"
            windows_service.uninstall(root / "app")
        assert windows_service._read()[1] is None
        (proof / "cleanup.json").write_text(json.dumps({"taskAbsent": True}))
    assert result is not None
    print(json.dumps({"nativeSetupFlow": "passed", "sameRunOnReopen": True,
                      "normalCleanup": True, "noModelBrowserOrOriginalAppEffects": True}))


def replacement_flow(root, old):
    """Manual same-version host-graph replacement/rollback, not auto-upgrade."""
    from amplifier_web import windows_service, windows_task_runtime
    env = environment(root, old)
    env["PATH"] = str(root / "bootstrap-inputs-v1/portable-uv") + ";" + env["PATH"]
    os.environ.clear()
    os.environ.update(env)
    assert windows_service._read()[1] is None
    first = root / ".venv/Scripts"
    second = root / "bootstrap-host-v2/Scripts"
    assert (first / "python.exe").is_file() and (second / "python.exe").is_file()
    proof = root / "proof" / ("replacement-flow-" + str(time.time_ns()))
    proof.mkdir()
    baseline = root / "app/updates/baseline-runtime"
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in baseline.iterdir() if p.is_file()}
    receipts = []
    runs = []

    def command(label, scripts, *args):
        result = subprocess.run([str(scripts / "amplifier-unified.exe"),
                                 "--data-dir", str(root / "app"),
                                 "--workspace", str(root / "workspace"), *args],
                                cwd=root, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=90)
        (proof / (label + ".stdout")).write_bytes(result.stdout)
        (proof / (label + ".stderr")).write_bytes(result.stderr)
        receipts.append({"label": label, "returncode": result.returncode,
                         "stdoutBytes": len(result.stdout), "stderrBytes": len(result.stderr)})
        (proof / "commands.json").write_text(json.dumps(receipts, indent=2))
        assert result.returncode == 0, label
        return result.stdout

    def observe(label, scripts):
        health = json.loads(command(label + "-status", scripts, "service", "status", "--wait", "60", "--json"))
        assert health["ok"] and health["processVerified"] and health["responding"]
        _, spec = windows_service._read(root / "app")
        assert Path(spec.python) == scripts / "python.exe"
        record = windows_task_runtime.observe(spec)
        assert record["pid"] == health["pid"]
        runs.append(record)
        (proof / "runs.json").write_text(json.dumps(runs, indent=2))
        return spec

    def exited(record):
        with windows_task_runtime.process(record["pid"]) as actual:
            assert actual is None or actual["created"] != record["created"] or actual["exited"]()

    try:
        command("initial-install", first, "service", "install")
        initial_spec = observe("initial", first)
        command("replace", second, "service", "install", "--replace")
        replacement_spec = observe("replacement", second)
        exited(runs[0])
        assert initial_spec.registration_id == replacement_spec.registration_id
        assert runs[0]["run"] != runs[1]["run"]
        command("rollback", first, "service", "install", "--replace")
        restored_spec = observe("restored", first)
        exited(runs[1])
        assert restored_spec == initial_spec and runs[2]["run"] not in {runs[0]["run"], runs[1]["run"]}
        assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in baseline.iterdir() if p.is_file()}
        # Actual replacement responses must name retained readable backups.
        backup_count = 0
        for label, expected in (("replace", initial_spec), ("rollback", replacement_spec)):
            text = (proof / (label + ".stdout")).read_text()
            candidates = [p for p in (root / "app/config/services").glob("task-backup-*.xml")
                          if str(p) in text]
            assert len(candidates) == 1
            assert windows_service.owned_spec(candidates[0].read_text(), expected.sid) == expected
            backup_count += 1
        (proof / "result.json").write_text(json.dumps({
            "sameVersionHostGraphReplacement": True, "normalRollback": True,
            "privateBackupsVerified": backup_count, "runtimeReceiptBytesUnchanged": True,
            "appVersionMigrationOrAutomaticUpdateQualified": False}, indent=2))
    finally:
        _, current = windows_service._read(root / "app")
        if current is not None:
            assert Path(current.python).parent in {first, second}
            windows_service.uninstall(root / "app")
        assert windows_service._read()[1] is None
        for record in runs:
            exited(record)
        assert windows_task_runtime.listener_owners("127.0.0.1", 8953) == set()
        (proof / "cleanup.json").write_text(json.dumps({"taskAbsent": True, "allExactRunsExited": True,
                                                       "listener8953Absent": True}))
    print(json.dumps({"nativeManualReplacement": "passed", "rollback": True,
                      "sameVersionOnly": True, "normalCleanup": True}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["install", "exercise", "continue-exercise", "setup-flow", "replacement-flow"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sha256")
    args = parser.parse_args()
    assert sys.platform == "win32" and sys.version_info[:2] == (3, 13)
    if args.operation == "install":
        asyncio.run(install(args.root, args.old, args.wheel, args.sha256))
    elif args.operation == "setup-flow":
        setup_flow(args.root, args.old)
    elif args.operation == "replacement-flow":
        replacement_flow(args.root, args.old)
    else:
        exercise(args.root, args.old, resume=args.operation == "continue-exercise")


if __name__ == "__main__":
    main()