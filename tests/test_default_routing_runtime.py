"""Opt-in offline Core/Foundation routing qualification of an installed wheel.

Parent verification owns runtime provisioning. Missing explicit prerequisites
skip; incompatibility, mount failures, wrong source and routing failures fail.
No package manager, network preparation or source sys.path injection is used.
Nested desktop checks mount a no-effect configuration probe, not a real backend.
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
    nested = report["nested_desktop"]
    assert nested["fixture_package"] == "amplifier_module_tool_desktop_ceiling_probe"
    desktop = {row["case"]: row for row in nested["executions"]}
    assert len(nested["executions"]) == len(desktop) == 6
    assert set(desktop) == {
        "desktop-root", "desktop-middle", "desktop-grandchild",
        "desktop-root-without-tool", "desktop-middle-resumed", "desktop-grandchild-after-resume",
    }
    ceiling = {"read_only": True, "gate_writes": True}
    root = desktop["desktop-root"]
    replacement = desktop["desktop-root-without-tool"]
    assert root["mounted_config"] == ceiling and root["instance_id"] == "desktop-root-instance"
    assert replacement["session_id"] == root["session_id"]
    assert replacement["mounted_config"] is None and replacement["policy"] is None
    first_middle = desktop["desktop-middle"]
    for middle_case, grandchild_case, lifecycle in (
        ("desktop-middle", "desktop-grandchild", "session:start"),
        ("desktop-middle-resumed", "desktop-grandchild-after-resume", "session:resume"),
    ):
        middle, grandchild = desktop[middle_case], desktop[grandchild_case]
        assert middle["session_id"] == first_middle["session_id"]
        assert middle["parent_id"] == root["session_id"]
        assert middle["mounted_config"] is None and middle["instance_id"] is None
        assert middle["policy"] == ceiling and middle["lifecycle"] == lifecycle
        assert middle["grandchild_id"] == grandchild["session_id"]
        assert grandchild["parent_id"] == middle["session_id"]
        assert grandchild["session_id"] not in {middle["session_id"], root["session_id"]}
        assert grandchild["lifecycle"] == "session:start"
        assert grandchild["policy"] == grandchild["mounted_config"] == ceiling
        assert grandchild["instance_id"] == "desktop-grandchild-instance"
    assert desktop["desktop-grandchild"]["session_id"] != desktop["desktop-grandchild-after-resume"]["session_id"]
    initial, resumed = nested["checkpoints"]
    assert initial["resumed"] is False and resumed["resumed"] is True
    assert initial["session_id"] == resumed["session_id"] == first_middle["session_id"]
    assert initial["desktop_tools"] == resumed["desktop_tools"] == []
    assert initial["policy"] == resumed["policy"] == ceiling
    assert 0 < initial["message_count"] < resumed["message_count"]
    fallback = report["balanced_fallback"]
    assert fallback["origin"] == "compose_configured_bundle; routing-only finite runtime"
    assert fallback["matrix"]["name"] == "balanced" and fallback["matrix"]["source"] == "bundle"
    assert len(fallback["matrix"]["sha256"]) == 64
    selected = {row["case"]: row for row in fallback["executions"]}
    before = ("private-service", "private-conversation-v1", "high")
    after = ("private-secondary", "private-conversation-v2", "low")
    assert {case: (row["provider"], row["model"], row["effort"]) for case, row in selected.items()} == {
        "automatic-root": before, "automatic-agent-role": before, "automatic-call-role": before,
        "automatic-agent-pin": ("private-secondary", "private-pin-v1", None),
        "automatic-call-pin": ("private-service", "private-call-pin-v1", None),
        "automatic-root-changed": after, "automatic-resume": before, "automatic-resume-new-role": after,
        "strict-root": before, "resolver-error-root": before,
    }
    assert len(fallback["executions"]) == len(selected)
    assert all(row["automatic"] is (case != "strict-root") for case, row in selected.items())
    for case, row in selected.items():
        assert row["lifecycle"] == ("session:resume" if case.startswith("automatic-resume") else "session:start")
        if case in {"automatic-agent-pin", "automatic-call-pin"}:
            assert row["selection"] is None
        else:
            assert row["selection"] == {"instance": row["provider"], "model": row["model"], "effort": row["effort"]}
    retained = {row["case"]: row for row in fallback["saved"]}
    assert len(retained) == len(fallback["saved"]) == 6
    identity = retained["automatic-agent-role"]["session_id"]
    for case in ("automatic-agent-role", "automatic-resume", "automatic-resume-new-role"):
        assert retained[case]["session_id"] == selected[case]["session_id"] == identity
        assert retained[case]["selection"] == selected[case]["selection"]
        assert retained[case]["source"] == "default_fallback"
    assert retained["automatic-resume"]["role"] == "coding"
    assert retained["automatic-call-role"]["role"] == retained["automatic-resume-new-role"]["role"] == "fast"
    failures = {row["case"]: row["error"] for row in fallback["rejected"]}
    assert len(failures) == len(fallback["rejected"]) == 5
    assert set(failures) == {"automatic-invalid-pin", "strict-agent-role", "strict-call-role",
                             "resolver-error-agent", "resolver-error-call"}
    assert all("synthetic provider availability failure" in failures[case]
               for case in ("resolver-error-agent", "resolver-error-call"))
    assert {row["case"]: row["outcome"] for row in fallback["rejected"]} == {
        "automatic-invalid-pin": "tool_error", "strict-agent-role": "tool_error",
        "strict-call-role": "tool_error", "resolver-error-agent": "tool_error",
        "resolver-error-call": "raised",
    }


def test_execution_oracle_rejects_lifecycle_model_corruption(routing_probe):
    result = routing_probe("--negative-control")
    assert result.returncode not in (0, 77), diagnostics(result)
    assert "effective provider/model mismatch for agent-pin-vs-role" in result.stderr + result.stdout, diagnostics(result)
    assert "synthetic-regression-v1" in result.stderr + result.stdout, diagnostics(result)
    assert not any(row.get("status") == "passed" for row in records(result)), diagnostics(result)
