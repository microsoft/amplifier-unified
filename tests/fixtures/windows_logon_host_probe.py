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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["install", "exercise", "continue-exercise"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sha256")
    args = parser.parse_args()
    assert sys.platform == "win32" and sys.version_info[:2] == (3, 13)
    if args.operation == "install":
        asyncio.run(install(args.root, args.old, args.wheel, args.sha256))
    else:
        exercise(args.root, args.old, resume=args.operation == "continue-exercise")


if __name__ == "__main__":
    main()