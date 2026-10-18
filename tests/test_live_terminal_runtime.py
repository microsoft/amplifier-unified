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


def _check_worker_schema_diagnostic_contract(tmp_path):
    """Nested local-helper controls, not real worker/producer qualification."""
    import asyncio
    import importlib.util

    source = Path(__file__).parent / "fixtures/live_terminal_probe.py"
    spec = importlib.util.spec_from_file_location("producer_diagnostic_controls", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    root = tmp_path / "diagnostic-controls"
    root.mkdir()
    rows, calls, passed = [], [], []
    secret = "DO-NOT-RETAIN-PRIVATE-DIAGNOSTIC"
    config, environment = {"private": secret}, {"private": secret}
    schema = {"fields": [], "private": secret}

    async def success(module_id, **kwargs):
        calls.append((module_id, kwargs))
        return schema

    observed = fixture._producer_schema_observer(root, success, lambda name, row: rows.append((name, row)))
    assert asyncio.run(observed("provider-regularproducer", config=config, environment=environment)) is schema
    assert len(calls) == 1 and calls[0][1]["config"] is config and calls[0][1]["environment"] is environment
    assert len(rows) == 1 and fixture._producer_valid_schema_record(rows[0][1], rows[0][0])
    passed.append("unchanged-success-and-arguments-once")
    assert asyncio.run(observed("provider-unrelated")) is schema
    assert len(calls) == 2 and len(rows) == 1
    passed.append("unrelated-provider-unobserved")

    error = ModuleNotFoundError(secret, name=fixture._SCHEMA_MODULE)
    failures = []

    async def fails(module_id, **kwargs):
        failures.append(module_id)
        raise error

    observed_failure = fixture._producer_schema_observer(root, fails, lambda name, row: rows.append((name, row)))
    try:
        asyncio.run(observed_failure("provider-regularproducer"))
    except ModuleNotFoundError as caught:
        assert caught is error
        tb = caught.__traceback__
        while tb and tb.tb_frame.f_code is not fails.__code__:
            tb = tb.tb_next
        assert tb is not None
    else:
        raise AssertionError("Observer swallowed the original failure")
    assert len(failures) == 1 and rows[-1][1]["exception"]["missingModule"] == fixture._SCHEMA_MODULE
    assert secret not in json.dumps(rows)
    passed.append("same-exception-and-original-traceback-once")

    def broken_writer(*args):
        raise OSError(secret)

    assert asyncio.run(fixture._producer_schema_observer(root, success, broken_writer)(
        "provider-regularproducer")) is schema
    with pytest.raises(ModuleNotFoundError) as captured:
        asyncio.run(fixture._producer_schema_observer(root, fails, broken_writer)("provider-regularproducer"))
    assert captured.value is error and len(failures) == 2
    passed.append("observer-write-failure-preserves-outcome")
    unknown = fixture._producer_exception_shape(ModuleNotFoundError(secret, name=secret), root)
    assert unknown["missingModule"] == "other" and secret not in json.dumps(unknown)
    passed.append("unknown-module-and-message-not-exported")
    capped_rows, before = [], len(calls)
    bounded = fixture._producer_schema_observer(
        root, success, lambda name, row: capped_rows.append((name, row)))
    for _ in range(6):
        assert asyncio.run(bounded("provider-regularproducer")) is schema
    assert len(calls) == before + 6
    assert [name for name, _ in capped_rows] == [
        "schema-1", "schema-2", "schema-3", "schema-4", "overflow", "overflow"]
    passed.append("observation-cap-does-not-cap-original-calls")

    receipts = root / "app/logs/workers"
    receipts.mkdir(parents=True)
    name = "startup-" + "1" * 32 + ".log"
    path = receipts / name
    header = {"phase": "session-mount", "exitCode": 1, "private": secret,
              "failure": json.dumps({"errorType": "ModuleNotFoundError", "message": secret,
                                    "frames": [{"file": "/private/" + secret, "line": 7,
                                                "function": secret, "locals": {"secret": secret}}]})}
    path.write_text(json.dumps(header) + "\n\n" + secret)
    derivative = fixture._producer_startup_diagnostic(root, name)
    assert derivative == {"status": "read", "present": True, "name": name, "phase": "session-mount",
                          "exitCode": 1, "exceptionClass": "ModuleNotFoundError",
                          "frames": [{"location": "other", "line": 7}]}
    assert secret not in json.dumps(derivative)
    passed.append("strict-receipt-derivative-no-private-text")
    assert fixture._producer_startup_diagnostic(root, "../" + name)["status"] == "invalid-name"
    assert fixture._producer_startup_diagnostic(root, "startup-" + "2" * 32 + ".log")["status"] == "unavailable"
    passed.append("invalid-and-missing-receipt-no-fallback")
    for body in ("not-json", "[]", json.dumps({"failure": "not-json"}), "x" * 60_001):
        path.write_text(body)
        assert fixture._producer_startup_diagnostic(root, name)["status"] == "malformed-or-unavailable"
    passed.append("malformed-and-oversized-receipt-refused")
    path.write_text(json.dumps({"phase": "session-mount", "exitCode": None}))
    assert fixture._producer_startup_diagnostic(root, name)["status"] == "metadata-only"
    passed.append("metadata-only-receipt-not-original-exception-proof")
    path.unlink()
    private = root / "private-receipt"
    private.write_text(json.dumps(header) + "\n\n" + secret)
    path.symlink_to(private)
    assert fixture._producer_startup_diagnostic(root, name)["status"] == "unavailable"
    path.unlink()
    path.write_text(json.dumps(header))
    moved = root / "held-workers"
    receipts.rename(moved)
    receipts.symlink_to(moved, target_is_directory=True)
    assert fixture._producer_startup_diagnostic(root, name)["status"] == "malformed-or-unavailable"
    receipts.unlink()
    moved.rename(receipts)
    passed.append("file-and-directory-symlinks-refused")

    directory = root / "schema-diagnostics"
    directory.mkdir()
    assert fixture._producer_schema_diagnostics(root)["gaps"] == [
        "observer-not-installed", "schema-outcome-not-observed"]
    (directory / "installed.json").write_text(json.dumps({"status": "installed"}))
    (directory / "schema-1.json").write_text(json.dumps(rows[0][1]))
    assert fixture._producer_schema_diagnostics(root)["gaps"] == []
    corrupted = {**rows[0][1], "ownedPackageOriginMatches": secret}
    (directory / "schema-1.json").write_text(json.dumps(corrupted))
    readback = fixture._producer_schema_diagnostics(root)
    assert "unreadable-schema-1" in readback["gaps"] and secret not in json.dumps(readback)
    for corrupted in (
        {**rows[0][1], "schemaShape": {**rows[0][1]["schemaShape"], "fieldCount": secret}},
        {**rows[1][1], "exception": {**rows[1][1]["exception"], "missingModule": secret}},
        {**rows[1][1], "exception": {**rows[1][1]["exception"],
                                    "frames": [{"location": secret, "line": 7}]}},
    ):
        (directory / "schema-1.json").write_text(json.dumps(corrupted))
        readback = fixture._producer_schema_diagnostics(root)
        assert "unreadable-schema-1" in readback["gaps"] and secret not in json.dumps(readback)
    passed.append("missing-and-corrupt-observation-stays-gap")
    return {"status": "passed", "cases": passed,
            "scope": "Authored helper controls only; actual worker evidence is separate"}


def _check_regular_work_details_real_producer_and_service_reopen(tmp_path):
    """An observation check, not a claim that missing-work product repair passed."""
    import signal

    root = Path(__file__).resolve().parents[1]
    scratch = tmp_path / "regular-producer"
    evidence_path = Path(os.environ.get("UNIFIED_PRODUCER_EVIDENCE",
                                       str(tmp_path / "regular-producer-evidence.json"))).resolve()
    assert not evidence_path.is_relative_to(scratch.resolve()), "Evidence must survive outside scratch"
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    controls = _check_worker_schema_diagnostic_contract(tmp_path)

    def failure(phase, message):
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
        wrapper_failure = {"phase": phase, "message": message}
        evidence["wrapperFailure"] = wrapper_failure
        evidence.setdefault("primaryFailure", wrapper_failure)
        evidence.update(status="failed", failure=evidence["primaryFailure"])
        evidence["diagnosticContractControls"] = controls
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
    except OSError:
        failure("wrapper-launch", "Producer process could not launch")
        pytest.fail("Producer launch failed; see sanitized evidence", pytrace=False)
    try:
        stdout, stderr = process.communicate(timeout=175)
    except subprocess.TimeoutExpired:
        # Terminate this fixture's own process group, including its worker/readers.
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
        failure("wrapper-timeout", "180-second outer budget; owned process group killed")
        pytest.fail(f"Producer timeout; sanitized evidence: {evidence_path}", pytrace=False)
    if process.returncode and not evidence_path.exists():
        failure("wrapper-exit", f"Fixture exited {process.returncode} before saving a report")
    # Neither raw process stream is an artifact. StartupCapture is best-effort
    # private redaction, not permission to put its messages into CI/JUnit.
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["diagnosticContractControls"] = controls
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    assert process.returncode == 0, f"Producer exit {process.returncode}; sanitized evidence: {evidence_path}"
    assert evidence["status"] == "observed", evidence.get("failure")
    assert evidence["phase"] == "complete"
    assert evidence["providerSchemaPreflight"]["hostValidationUsed"] is True
    assert evidence["providerSchemaPreflight"]["providerAttempts"] == 0
    assert evidence["providerSchemaPreflight"]["toolAttempts"] == 0
    assert evidence["workerSchemaDiagnostics"]["gaps"] == []
    assert any(row["status"] == "returned" for row in evidence["workerSchemaDiagnostics"]["rows"])
    assert evidence["processGuardPreflight"]["status"] == "passed"
    assert evidence["processGuardPreflight"]["cases"][0]["actualPopenAndPosixSpawn"] is True
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
    assert evidence["secondaryFailures"] == []
    # Do NOT assert missing/ambiguous counts are zero or positive. This admitted
    # experiment observes the installed producer, not an invented repair verdict.


# This fixed manual experiment needs additional pinned capture dependencies.
# It is not part of ordinary collection and cannot turn an ordinary host suite
# into a selected-but-skipped success.
if os.environ.get("UNIFIED_RUN_PRODUCER_CHECK") == "1":
    test_regular_work_details_real_producer_and_service_reopen = (
        _check_regular_work_details_real_producer_and_service_reopen)
