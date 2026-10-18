"""Opt-in terminal adapter acceptance against the real isolated runtime."""
import json
import os
from pathlib import Path
import subprocess

import pytest


def test_terminal_clients_follow_detach_reconnect_and_stop_real_worker(tmp_path):
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    if not python:
        pytest.skip("set UNIFIED_RUNTIME_PYTHON to Python with the pinned runtime and host dependencies")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([python, str(root / "tests/fixtures/live_terminal_probe.py"), str(tmp_path)],
                            cwd=root, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence == dict.fromkeys(("two_live_clients", "idle_resume", "duplicate_runs_once",
                                      "detach_keeps_work", "reconnect_restores_history",
                                      "explicit_stop_cancels_provider"), True)


def _check_regular_work_details_real_producer_and_service_reopen(tmp_path):
    """An observation check, not a claim that missing-work product repair passed."""
    import signal

    root = Path(__file__).resolve().parents[1]
    scratch = tmp_path / "regular-producer"
    evidence_path = Path(os.environ.get("UNIFIED_PRODUCER_EVIDENCE",
                                       str(tmp_path / "regular-producer-evidence.json"))).resolve()
    assert not evidence_path.is_relative_to(scratch.resolve()), "Evidence must survive outside scratch"
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")

    def failure(phase, message):
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
        evidence.update(status="failed", failure={"phase": phase, "message": message})
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    if not python:
        failure("wrapper-setup", "UNIFIED_RUNTIME_PYTHON is required; no dependency installation or skip")
        pytest.fail("Set UNIFIED_RUNTIME_PYTHON to the prepared pinned native runtime")
    env = dict(os.environ, UNIFIED_PRODUCER_EVIDENCE=str(evidence_path))
    try:
        process = subprocess.Popen(
            [python, str(root / "tests/fixtures/live_terminal_probe.py"), str(scratch), "--producer"],
            cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            start_new_session=True)
    except OSError as error:
        failure("wrapper-launch", str(error))
        raise
    try:
        stdout, stderr = process.communicate(timeout=175)
    except subprocess.TimeoutExpired:
        # Terminate this fixture's own process group, including its worker/readers.
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
        failure("wrapper-timeout", "180-second outer budget; owned process group killed")
        pytest.fail(f"Producer timeout; evidence: {evidence_path}\n{stdout}\n{stderr}")
    if process.returncode and not evidence_path.exists():
        failure("wrapper-exit", f"Fixture exited {process.returncode} before saving a report")
    assert process.returncode == 0, f"Evidence: {evidence_path}\n{stdout}\n{stderr}"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "observed", evidence.get("failure")
    assert evidence["phase"] == "complete"
    assert evidence["noExtraColdExecution"] is True
    assert evidence["actualCaptureState"]["enabled"] is True
    assert evidence["captureConfiguration"]["destinations"] == {}
    assert evidence["countsBeforeCold"] == evidence["countsAfterCold"]
    assert evidence["finalCounts"]["provider"] == 3
    assert evidence["finalCounts"]["tool"] == 2
    assert evidence["finalCounts"]["providerAttempts"] == 3
    assert evidence["finalCounts"]["toolAttempts"] == 2
    assert evidence["finalCounts"]["workerMounts"] == evidence["finalCounts"]["workerLaunches"] == 1
    assert not evidence["finalCounts"]["denials"] and not evidence["fieldIntegrityErrors"]
    assert evidence["finalCleanup"] == {"allServicesClosed": True, "remainingWorkers": 0, "errors": []}
    # Do NOT assert missing/ambiguous counts are zero or positive. This admitted
    # experiment observes the installed producer, not an invented repair verdict.


# This fixed manual experiment needs additional pinned capture dependencies.
# It is not part of ordinary collection and cannot turn an ordinary host suite
# into a selected-but-skipped success.
if os.environ.get("UNIFIED_RUN_PRODUCER_CHECK") == "1":
    test_regular_work_details_real_producer_and_service_reopen = (
        _check_regular_work_details_real_producer_and_service_reopen)
