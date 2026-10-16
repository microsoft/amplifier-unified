"""Fresh-process qualification of real default-behavior preparation/mount/export.

Opt in with UNIFIED_BEHAVIOR_REPOS=/opt/defaults/repos. UNIFIED_RUNTIME_PYTHON
optionally selects the prepared interpreter (otherwise the current interpreter).
Copy fixtures/default_mount_probe.py and test_default_behaviors_integration.py
alongside this test. Each invocation is isolated from the driver's imported
upstream modules. Missing prerequisites skip explicitly; incompatible APIs,
source mismatches, forbidden effects and acceptance failures fail.

The child keeps Unified installed from its wheel but uses actual Foundation
preparation of declared upstream repository sources. No model/provider turn,
desktop action, network request, package install or source-file edit is allowed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROBE = Path(__file__).parent / "fixtures/default_mount_probe.py"
PREFIX = "DEFAULT_MOUNT_PROBE "


def _records(result):
    return [json.loads(line[len(PREFIX):]) for line in result.stdout.splitlines()
            if line.startswith(PREFIX)]


def _diagnostics(result):
    return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@pytest.fixture(scope="module")
def mount_probe(tmp_path_factory):
    if not os.environ.get("UNIFIED_BEHAVIOR_REPOS"):
        pytest.skip("Requires prepared owned DTU and UNIFIED_BEHAVIOR_REPOS")
    assert Path("/run/systemd/container").exists() or Path("/.dockerenv").exists(), (
        "Mount qualification is container-only, never a host test"
    )
    # Do not resolve a venv executable symlink into its base interpreter.
    python = Path(os.environ.get("UNIFIED_RUNTIME_PYTHON", sys.executable)).absolute()
    assert python.is_file() and os.access(python, os.X_OK), str(python)
    assert PROBE.is_file(), "Copy fixtures/default_mount_probe.py beside this test"
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
                "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "SSH_AUTH_SOCK",
                "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
                "GEMINI_API_KEY", "AZURE_OPENAI_API_KEY"):
        env.pop(key, None)
    env["UNIFIED_BEHAVIOR_REPOS"] = str(Path(env["UNIFIED_BEHAVIOR_REPOS"]).resolve())
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    def invoke(case):
        cwd = tmp_path_factory.mktemp("default-mount-" + case)
        child_env = {**env, **{key: str(cwd / key.lower()) for key in (
            "HOME", "AMPLIFIER_HOME", "AMPLIFIER_WEB_HOME", "AMPLIFIER_SESSION_STATE_HOME",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
        )}}
        # Explicit plugin only: unrelated installed pytest plugins are not part
        # of host preparation. Child audit guards remain active during imports.
        return subprocess.run([
            str(python), "-I", "-B", "-m", "pytest", "-q", "-s", "--tb=short",
            "--noconftest", "--import-mode=importlib", "-c", os.devnull,
            "-p", "pytest_asyncio.plugin", "-p", "no:cacheprovider",
            "--basetemp", str(cwd / "pytest"), str(PROBE.resolve()) + "::test_" + case,
        ], cwd=cwd, env=child_env, text=True, capture_output=True, timeout=90)

    preflight = invoke("preflight")
    rows = _records(preflight)
    if preflight.returncode == 0 and rows and rows[-1]["status"] == "missing_prerequisites":
        pytest.skip("Prepared mount prerequisites: " + json.dumps(rows[-1]["missing"]))
    assert preflight.returncode == 0, _diagnostics(preflight)
    assert len(rows) == 1 and rows[0]["status"] == "ready", _diagnostics(preflight)
    print(PREFIX + json.dumps(rows[0], sort_keys=True))
    return invoke


def test_real_five_behavior_mount_has_shipped_balanced_resolver(mount_probe):
    result = mount_probe("mount")
    assert result.returncode == 0, _diagnostics(result)
    rows = _records(result)
    assert len(rows) == 1 and rows[0]["status"] == "passed", _diagnostics(result)
    assert rows[0]["case"] == "mount" and rows[0]["forbidden_effects"] == []
    print(PREFIX + json.dumps(rows[0], sort_keys=True))


def test_mounted_defaults_export_actual_resources_and_flatten_routing(mount_probe):
    result = mount_probe("export")
    assert result.returncode == 0, _diagnostics(result)
    rows = _records(result)
    assert len(rows) == 1 and rows[0]["status"] == "passed", _diagnostics(result)
    assert rows[0]["case"] == "export" and rows[0]["forbidden_effects"] == []
    print(PREFIX + json.dumps(rows[0], sort_keys=True))
