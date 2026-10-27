"""Failure reporting only: temporary files and fake commands, never a trust store."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def harness(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "setup_ca_linux_harness",
        Path(__file__).parents[1] / "scripts/check_setup_ca_linux.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DEST", tmp_path / "synthetic-ca.crt")

    def no_process(*args, **kwargs):
        pytest.fail("Reporting tests must not execute system-trust commands")
    monkeypatch.setattr(module.subprocess, "run", no_process)
    return module


@pytest.mark.parametrize("timeout", [False, True])
def test_primary_error_survives_bundle_cleanup_failure(harness, tmp_path, timeout):
    expected = b"synthetic public certificate"
    harness.DEST.write_bytes(expected)
    report = tmp_path / "report.json"
    result = {}
    primary = RuntimeError("Injected post-install failure to check exact-byte cleanup")
    cleanup_error = (
        subprocess.TimeoutExpired(["update-ca-certificates", "--fresh"], 60)
        if timeout else OSError("bundle rebuild failed")
    )
    calls = []

    def failed_rebuild(argv):
        calls.append(argv)
        raise cleanup_error

    with pytest.raises(RuntimeError) as caught:
        with harness.reported_checks(
            result, report,
            lambda: harness.cleanup_trust_store(True, (expected,), result, failed_rebuild),
        ) as directory:
            raise primary

    assert caught.value is primary
    saved = json.loads(report.read_text())
    assert saved["status"] == "failed"
    assert saved["primaryFailure"] == {"type": "RuntimeError", "message": str(primary)}
    assert saved["cleanupFailures"] == [{
        "phase": "trust-store", "type": type(cleanup_error).__name__,
        "message": str(cleanup_error),
    }]
    assert saved["failureCleanup"] == "removed exact synthetic CA"
    assert saved["bundleRebuild"] == "failed"
    assert saved["destinationAbsent"] is True
    assert saved["temporaryDirectoryCleanup"] == "completed"
    assert not Path(directory).exists()
    assert calls == [["update-ca-certificates", "--fresh"]]


def test_temporary_directory_cleanup_only_failure_cannot_report_passed(harness, monkeypatch, tmp_path):
    result = {}
    report = tmp_path / "report.json"
    cleanup_error = OSError("temporary directory cleanup failed")
    calls = []

    class Directory:
        name = str(tmp_path / "temporary")

        def cleanup(self):
            calls.append("directory")
            assert result["status"] == "running"
            assert not report.exists()
            raise cleanup_error

    monkeypatch.setattr(harness.tempfile, "TemporaryDirectory", lambda **kwargs: Directory())
    with pytest.raises(OSError) as caught:
        with harness.reported_checks(result, report, lambda: calls.append("trust-store")):
            result["checks"] = ["primary checks completed"]

    assert caught.value is cleanup_error
    saved = json.loads(report.read_text())
    assert saved["status"] == "failed"
    assert saved["primaryFailure"] is None
    assert saved["cleanupFailures"] == [{
        "phase": "temporary-directory", "type": "OSError", "message": str(cleanup_error),
    }]
    assert saved["destinationAbsent"] is True
    assert saved["temporaryDirectoryCleanup"] == "failed"
    assert calls == ["trust-store", "directory"]


def test_success_is_reported_only_after_finalization(harness, tmp_path):
    result = {}
    report = tmp_path / "report.json"

    def cleanup():
        assert result["status"] == "running"
        assert not report.exists()

    with harness.reported_checks(result, report, cleanup) as directory:
        assert Path(directory).is_dir()
    saved = json.loads(report.read_text())
    assert saved["status"] == "passed"
    assert saved["primaryFailure"] is None
    assert saved["cleanupFailures"] == []
    assert saved["temporaryDirectoryCleanup"] == "completed"
    assert saved["destinationAbsent"] is True
    assert not Path(directory).exists()


@pytest.mark.parametrize("kind", ["changed-file", "symlink", "dangling-symlink"])
def test_cleanup_refuses_changed_bytes_and_symlinks(harness, tmp_path, kind):
    target = tmp_path / "unowned.crt"
    target.write_bytes(b"synthetic public certificate")
    if kind == "changed-file":
        harness.DEST.write_bytes(b"unexpected bytes")
    else:
        harness.DEST.symlink_to(target)
        if kind == "dangling-symlink":
            target.unlink()
    calls = []
    result = {}
    report = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="bytes changed or symlink"):
        with harness.reported_checks(
            result, report,
            lambda: harness.cleanup_trust_store(
                True, (b"synthetic public certificate",), result, calls.append,
            ),
        ):
            pass
    saved = json.loads(report.read_text())
    assert saved["status"] == "failed"
    assert saved["primaryFailure"] is None
    assert saved["cleanupFailures"][0]["phase"] == "trust-store"
    assert saved["destinationAbsent"] is False
    assert saved["bundleRebuild"] == "not required"
    assert calls == []
    if kind == "changed-file":
        assert harness.DEST.read_bytes() == b"unexpected bytes"
    else:
        assert harness.DEST.is_symlink()
        if kind == "symlink":
            assert target.read_bytes() == b"synthetic public certificate"