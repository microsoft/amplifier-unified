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
    for fault in ("extraction", "persistence"):
        for callback_raises in (False, True):
            report = {"sessionId": "owned-synthetic", "runtimeEvents": [],
                      "startupDiagnostics": [], "status": "running"}
            payload = {"sessionId": "owned-synthetic", "phase": "worker_startup",
                       "diagnosticReceipt": name, "error": secret}
            payload_before = json.dumps(payload)
            callback_calls, save_calls, extract_calls = [], [], []
            callback_error = ValueError("product callback failure")
            returned = object()

            def extract(*args):
                extract_calls.append(args)
                if fault == "extraction":
                    raise OSError(secret)
                return derivative

            def persist():
                save_calls.append(True)
                raise OSError(secret)

            async def callback(kind, original_payload):
                callback_calls.append((kind, original_payload))
                if callback_raises:
                    raise callback_error
                return returned

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(fixture, "_producer_startup_diagnostic", extract)
                observer = fixture._producer_event_observer(root, report, persist, callback)
                if callback_raises:
                    with pytest.raises(ValueError) as caught:
                        asyncio.run(observer("runtime.error", payload))
                    assert caught.value is callback_error
                else:
                    assert asyncio.run(observer("runtime.error", payload)) is returned
            assert len(callback_calls) == 1 and callback_calls[0][1] is payload
            assert json.dumps(payload) == payload_before and len(extract_calls) == 1
            assert len(save_calls) == (fault == "persistence")
            assert report["status"] == "failed" and report["diagnosticIncomplete"] is True
            assert report["diagnosticFailures"][0]["phase"] == (
                "event-receipt-extraction" if fault == "extraction" else "event-receipt-save")
            assert report["secondaryFailures"] and "primaryFailure" not in report
            assert secret not in json.dumps(report)
            passed.append(f"event-{fault}-callback-{'raises' if callback_raises else 'returns'}")

    for fault in ("collection", "persistence"):
        for has_primary in (False, True):
            primary = {"phase": "producer-admission", "exceptionClass": "ValueError"}
            report = {"status": "running", "startupDiagnostics": []}
            if has_primary:
                report["primaryFailure"] = primary
            cleanup_calls, collect_calls, save_calls = [], [], []
            primary_error = ValueError("product primary failure")
            returned = object()

            def collect(*args):
                collect_calls.append(args)
                if fault == "collection":
                    raise OSError(secret)
                return {"rows": [], "gaps": ["schema-outcome-not-observed"]}

            def persist():
                save_calls.append(True)
                if fault == "persistence":
                    raise OSError(secret)

            async def cleanup():
                cleanup_calls.append(True)
                return returned

            async def attempt():
                try:
                    if has_primary:
                        raise primary_error
                finally:
                    result = await fixture._producer_cleanup_with_diagnostics(root, report, persist, cleanup)
                    assert result is returned

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(fixture, "_producer_schema_diagnostics", collect)
                if has_primary:
                    with pytest.raises(ValueError) as caught:
                        asyncio.run(attempt())
                    assert caught.value is primary_error and report["primaryFailure"] is primary
                else:
                    asyncio.run(attempt())
                    assert "primaryFailure" not in report
            assert len(cleanup_calls) == len(collect_calls) == len(save_calls) == 1
            assert report["status"] == "failed" and report["diagnosticIncomplete"] is True
            assert report["diagnosticFailures"][0]["phase"] == (
                "precleanup-collection" if fault == "collection" else "precleanup-save")
            assert report["secondaryFailures"] and secret not in json.dumps(report)
            passed.append(f"precleanup-{fault}-{'with' if has_primary else 'without'}-primary")

    report = {"status": "observed", "secondaryFailures": [], "primaryFailure": primary}
    secondary = report["secondaryFailures"]
    assert fixture._producer_diagnostic_step(root, report, "final-cleanup-save", broken_writer) is False
    assert report["primaryFailure"] is primary and report["secondaryFailures"] is secondary
    assert secondary and report["status"] == "failed" and secret not in json.dumps(report)
    passed.append("final-save-failure-marks-incomplete-without-replacing-primary")

    async def cleanup_failure():
        cleanup_calls.append(True)
        raise primary_error

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fixture, "_producer_schema_diagnostics", lambda root: {"rows": [], "gaps": []})
        with pytest.raises(ValueError) as caught:
            asyncio.run(fixture._producer_cleanup_with_diagnostics(root, report, lambda: None, cleanup_failure))
        assert caught.value is primary_error
    assert len(cleanup_calls) == 2
    passed.append("cleanup-exception-not-swallowed")

    # Core validates providers by calling mount with a validation coordinator,
    # then calls mount again for the session. Reproduce the old receipt collision
    # using the actual validators, without model/tool execution or another worker.
    from amplifier_core.testing import MockCoordinator
    from amplifier_core.validation.base import ValidationResult
    from amplifier_core.validation.provider import ProviderValidator
    from amplifier_core.validation.tool import ToolValidator

    mount_root = root / "mount-lifecycle"
    counts = mount_root / "counts"
    counts.mkdir(parents=True)
    validator = ProviderValidator()
    legacy_receipt = counts / "legacy-mounted.json"

    async def legacy_mount(coordinator, config=None):
        fixture._producer_record(counts, "legacy-mounted", {"pid": os.getpid()})
        await fixture.mount_producer_provider(coordinator, config)

    async def mount_controls():
        legacy_result = ValidationResult(module_type="provider", module_path="synthetic-legacy")
        await validator._check_protocol_compliance(legacy_result, legacy_mount, config={})
        assert legacy_result.passed and legacy_receipt.exists()
        legacy_session = MockCoordinator()
        with pytest.raises(FileExistsError):
            await legacy_mount(legacy_session, {})
        assert not legacy_session.get("providers")
        legacy_receipt.unlink()
        passed.append("legacy-validation-consumes-exclusive-mount-receipt")

        provider_result = ValidationResult(module_type="provider", module_path="synthetic-provider")
        tool_result = ValidationResult(module_type="tool", module_path="synthetic-tool")
        await validator._check_protocol_compliance(
            provider_result, fixture.mount_producer_provider, config={})
        await ToolValidator()._check_protocol_compliance(
            tool_result, fixture.mount_producer_tools, config={})
        assert provider_result.passed and tool_result.passed and not list(counts.iterdir())
        session = MockCoordinator()
        await fixture.mount_producer_provider(session, {})
        await fixture.mount_producer_tools(session, {})
        provider = session.get("providers", "regularproducer")
        assert provider.coordinator is session and provider.calls == 0
        assert not list(counts.iterdir())  # Validation and mount are not readiness.
        passed.append("provider-and-tool-validation-then-mount-have-no-receipt-side-effect")

        await fixture.producer_session_ready(session)
        receipt = json.loads((counts / "worker-mounted.json").read_text())
        assert receipt == {"pid": os.getpid(), "phase": "session-ready"}
        with pytest.raises(FileExistsError):
            await fixture.producer_session_ready(session)
        assert len(list(counts.iterdir())) == 1 and provider.calls == 0
        assert not list(counts.glob("*attempt*.json"))
        passed.append("readiness-records-one-bound-provider-without-provider-tool-execution")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("UNIFIED_PRODUCER_ROOT", str(mount_root))
        asyncio.run(mount_controls())
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
    assert set(evidence["moduleSourcePreflight"]) == {"provider-regularproducer", "tool-regularproducer"}
    assert all(row == {"declaredPackageParent": True, "oldPackageDirectoryRejected": True,
                       "foundationActivated": True, "originMatches": True, "installDependencies": False}
               for row in evidence["moduleSourcePreflight"].values())
    assert evidence["workerSchemaDiagnostics"]["gaps"] == []
    assert not evidence.get("diagnosticIncomplete", False)
    assert any(row["status"] == "returned" for row in evidence["workerSchemaDiagnostics"]["rows"])
    worker_schema = [row for row in evidence["workerSchemaDiagnostics"]["rows"] if row["status"] == "returned"]
    assert all(row["ownedPackageOriginMatches"] and row["expectedParentInSysPath"]
               and row["fixtureHelperOriginMatches"] and row["schemaHelperOriginMatches"]
               for row in worker_schema)
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
