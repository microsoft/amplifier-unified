"""Opt-in offline Core/Foundation routing qualification of an installed wheel.

Parent verification owns runtime provisioning. Missing explicit prerequisites
skip; incompatibility, mount failures, wrong source and routing failures fail.
No package manager, network preparation or source sys.path injection is used.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest


PROBE = Path(__file__).parent / "fixtures/default_routing_probe.py"
PREFIX = "DEFAULT_ROUTING_PROBE "


def records(result):
    return [json.loads(line.removeprefix(PREFIX)) for line in result.stdout.splitlines()
            if line.startswith(PREFIX)]


def diagnostics(result):
    return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@pytest.fixture
def routing_probe(tmp_path):
    required = ("UNIFIED_RUNTIME_PYTHON", "WARM_FOUNDATION_PATH", "WARM_ROUTING_PATH")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip("Offline routing integration prerequisites missing: " + ", ".join(missing)
                    + "; parent must supply a warmed installed-wheel runtime (no downloads)")
    # Do not resolve the executable symlink: that could discard a venv's identity.
    python = Path(os.environ["UNIFIED_RUNTIME_PYTHON"]).expanduser().absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        pytest.skip(f"UNIFIED_RUNTIME_PYTHON is not an available executable: {python}")
    foundation = Path(os.environ["WARM_FOUNDATION_PATH"]).expanduser().resolve()
    routing = Path(os.environ["WARM_ROUTING_PATH"]).expanduser().resolve()
    for variable, package in (
        ("WARM_FOUNDATION_PATH", foundation / "modules/tool-delegate/amplifier_module_tool_delegate/__init__.py"),
        ("WARM_ROUTING_PATH", routing / "modules/hooks-routing/amplifier_module_hooks_routing/__init__.py"),
    ):
        if not package.is_file():
            pytest.skip(f"{variable} lacks its required warmed package: {package}; no download attempted")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", None)
    env.update({
        "UNIFIED_RUNTIME_PYTHON": str(python),
        "WARM_FOUNDATION_PATH": str(foundation),
        "WARM_ROUTING_PATH": str(routing),
        "HOME": str(tmp_path / "home"),
        "AMPLIFIER_WEB_HOME": str(tmp_path / "unified-home"),
        "AMPLIFIER_HOME": str(tmp_path / "amplifier-home"),
        "AMPLIFIER_SESSION_STATE_HOME": str(tmp_path / "shared-state"),
        "AMPLIFIER_TERMINAL_HOME": str(tmp_path / "terminal-home"),
    })

    def invoke(*args):
        return subprocess.run([str(python), "-I", "-B", str(PROBE.resolve()), *args],
                              cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60)

    preflight = invoke("--preflight")
    if preflight.returncode == 77:
        rows = records(preflight)
        assert len(rows) == 1 and rows[0]["status"] == "missing_prerequisites", diagnostics(preflight)
        pytest.skip("Warmed runtime prerequisites missing: " + ", ".join(rows[0]["missing"]))
    assert preflight.returncode == 0, diagnostics(preflight)
    assert [row["status"] for row in records(preflight)] == ["ready"], diagnostics(preflight)
    return invoke


def test_installed_default_routing_through_real_delegate_and_lifecycle(routing_probe):
    result = routing_probe()
    assert result.returncode == 0, diagnostics(result)
    rows = records(result)
    assert len(rows) == 1 and rows[0]["status"] == "passed", diagnostics(result)
    report = rows[0]
    observed = {row["case"]: (row["provider"], row["default_model"]) for row in report["executions"]}
    assert observed == {
        "root-before": ("alpha", "conversation-v1"),
        "agent-pin-vs-role": ("alpha", "agent-pin-v1"),
        "call-pin-vs-agent-pin": ("beta", "call-pin-v1"),
        "call-role-vs-agent-pin": ("alpha", "agent-pin-v1"),
        "role-only-custom-matrix": ("beta", "matrix-coding-v1"),
        "call-role-vs-agent-role": ("alpha", "matrix-review-v1"),
        "declared-provider-vs-role": ("beta", "declared-pin-v1"),
        "call-role-vs-declared-provider": ("beta", "declared-pin-v1"),
        "call-pin-vs-declared-provider": ("alpha", "caller-alt-v1"),
        "unspecified-conversation": ("alpha", "conversation-v1"),
        "root-after-change": ("alpha", "conversation-v2"),
        "resume-agent-pin": ("alpha", "agent-pin-v1"),
        "resume-call-pin": ("beta", "call-pin-v1"),
        "resume-role-only": ("beta", "matrix-coding-v1"),
        "resume-unspecified": ("alpha", "conversation-v1"),
    }
    assert len(report["executions"]) == len(observed)
    assert all(row["matrix_events"] == 1 for row in report["executions"])
    assert all(row["lifecycle"] == ("session:resume" if row["case"].startswith("resume-")
                                    else "session:start") for row in report["executions"])
    assert {row["case"] for row in report["rejected"]} == {"reject-explicit-provider", "reject-agent-role"}
    assert all(row["error"] for row in report["rejected"])
    assert report["installed_wheel"]["checked_modules"] > 0
    assert report["model_calls"] == 0 and report["forbidden_effects"] == []


def test_execution_oracle_rejects_lifecycle_model_corruption(routing_probe):
    result = routing_probe("--negative-control")
    assert result.returncode not in (0, 77), diagnostics(result)
    assert "effective provider/model mismatch for agent-pin-vs-role" in result.stderr + result.stdout, diagnostics(result)
    assert "synthetic-regression-v1" in result.stderr + result.stdout, diagnostics(result)
    assert not any(row.get("status") == "passed" for row in records(result)), diagnostics(result)
