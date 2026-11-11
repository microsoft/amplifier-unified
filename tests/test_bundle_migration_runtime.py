"""Real native-input acceptance, isolated from the test process's shared state."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scenario", [
    "cold-none", "cold-empty", "cold-missing", "cold-restart", "native-restart", "production-probe",
    "warm-unpinned", "warm-pin", "warm-reset", "model-only", "peer-archived-cross",
])
def test_saved_migration_reaches_completed_native_turn(tmp_path, scenario):
    root = tmp_path / scenario
    # A DTU's durable outer timeout owns process containment. Avoid killing only
    # the parent probe and losing cleanup evidence for its worker descendants.
    result = subprocess.run([
        sys.executable, str(Path(__file__).parent / "fixtures/bundle_migration_matrix_probe.py"),
        str(root), scenario,
    ], capture_output=True, text=True, timeout=None)
    report = root / "result.json"
    if os.environ.get("AMPLIFIER_TEST_ARTIFACTS") and report.exists():
        (Path(os.environ["AMPLIFIER_TEST_ARTIFACTS"]) / f"matrix-{scenario}.json").write_bytes(
            report.read_bytes())
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(report.read_text())
    assert value["passed"] and value["cleanupConfirmed"] and value["inputExactlyOnce"]