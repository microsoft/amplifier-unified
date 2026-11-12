"""Closed control protocol tests; native identity cases are separate evidence."""
import asyncio
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import windows_task_runtime as runtime


def record(**changes):
    return {"schema": 1, "registration": "a" * 32, "run": "b" * 32,
            "pid": 123, "created": "134000000000000001",
            "image": r"C:\Python\python.exe", "phase": "listening", **changes}


def spec():
    return SimpleNamespace(data_dir=r"C:\Private data", registration_id="a" * 32)


@pytest.mark.parametrize("changes", [
    {"schema": True}, {"pid": True}, {"pid": -1}, {"pid": 2**32},
    {"created": 134000000000000001}, {"created": "0"}, {"created": str(2**64)},
    {"phase": []}, {"phase": "SECRET"}, {"image": r"\\server\python.exe"},
    {"image": "python.exe"}, {"image": r"C:\cmd.exe"}, {"run": "c" * 31},
    {"registration": "c" * 32}, {"environment": {"SECRET": "never"}},
])
def test_closed_record_rejects_untrusted_or_lossy_fields(changes):
    with pytest.raises(ValueError):
        runtime.validate(record(**changes), spec().registration_id)


def test_record_retains_exact_64bit_time_and_detaches_dictionary():
    value = record()
    result = runtime.validate(value, spec().registration_id)
    assert result == value and result is not value
    assert result["created"] == "134000000000000001"


@pytest.mark.parametrize("change", [{}, {"pid": 456}, {"phase": "stopped"}, {"created": "134000000000000002"}])
def test_current_process_hint_keeps_exact_native_identity(monkeypatch, change):
    from amplifier_web import windows_files
    value = record(**change)
    monkeypatch.setattr(runtime.os, "getpid", lambda: 123)
    @contextmanager
    def process(pid):
        yield {**record(), "exited": lambda: False}
    monkeypatch.setattr(runtime, "process", process)
    monkeypatch.setattr(windows_files, "address", Path)
    @contextmanager
    def directory(path, **kwargs):
        assert path == Path(spec().data_dir) / "config/services"
        assert kwargs == {"owned_root": Path(spec().data_dir), "create": False}
        yield SimpleNamespace(read_private=lambda name, limit: json.dumps(value).encode())
    monkeypatch.setattr(windows_files, "directory", directory)
    assert runtime.current_process_record(spec().data_dir) == (value if not change else None)


def test_missing_private_task_receipt_is_not_a_background_host(monkeypatch):
    from amplifier_web import windows_files
    monkeypatch.setattr(windows_files, "address", Path)
    @contextmanager
    def directory(*args, **kwargs):
        raise FileNotFoundError
        yield
    monkeypatch.setattr(windows_files, "directory", directory)
    assert runtime.current_process_record(spec().data_dir) is None


def restart(**changes):
    return {"schema": 1, "run": record(), "target": {
        "revision": "c" * 40, "generation": "d" * 32, "attemptId": "e" * 32}, **changes}


@pytest.mark.parametrize("change", [
    {"schema": True}, {"schema": 2}, {"extra": "not permitted"},
    {"run": record(phase="stopped")}, {"run": record(created=134000000000000001)},
    {"run": record(registration="f" * 32)}, {"target": None},
    {"target": {"revision": "main", "generation": "d" * 32, "attemptId": "e" * 32}},
    {"target": {"revision": "c" * 40, "generation": "d" * 32, "attemptId": "short"}},
    {"target": {"revision": "c" * 40, "generation": "d" * 32, "attemptId": "e" * 32,
                "command": "never execute caller arguments"}},
])
def test_restart_receipt_is_closed_exact_run_and_target(change):
    with pytest.raises(ValueError):
        runtime.restart_record(restart(**change), spec().registration_id)


def test_restart_receipt_detaches_and_preserves_exact_birth():
    value = restart()
    result = runtime.restart_record(value, spec().registration_id)
    assert result == value and result is not value
    assert result["run"] is not value["run"] and result["target"] is not value["target"]
    assert result["run"]["created"] == "134000000000000001"


@pytest.mark.parametrize("stop", [True, False])
def test_supervised_host_closes_normally_with_distinct_restart_exit(monkeypatch, stop):
    from aiohttp.web_runner import GracefulExit
    runner = object.__new__(runtime.Runner)
    runner.spec = SimpleNamespace(supervised=True)
    runner.record, runner.exit_code = record(), 0
    phases = []
    runner.phase = phases.append
    runner.stop_requested = lambda: stop
    monkeypatch.setattr(runtime, "read_restart", lambda spec: restart())
    with pytest.raises(GracefulExit):
        asyncio.run(runner.watch())
    assert runner.exit_code == (0 if stop else runtime.RESTART_EXIT)
    assert phases == ["stopping"]


def test_prior_run_restart_never_restarts_the_current_host(monkeypatch):
    runner = object.__new__(runtime.Runner)
    runner.spec = SimpleNamespace(supervised=True)
    runner.record, runner.exit_code = record(), 0
    runner.phase = lambda *a: pytest.fail("a prior run cannot stop this host")
    runner.stop_requested = lambda: False
    monkeypatch.setattr(runtime, "read_restart", lambda spec: restart(run=record(run="f" * 32)))
    async def stop_observing(delay):
        raise asyncio.CancelledError
    monkeypatch.setattr(runtime.asyncio, "sleep", stop_observing)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.watch())
    assert runner.exit_code == 0


def test_control_files_use_only_existing_private_boundary(monkeypatch):
    from amplifier_web import windows_files
    events, files = [], {}
    monkeypatch.setattr(windows_files, "address", PureWindowsPath)

    class Pinned:
        def read_private(self, name, *, limit):
            assert limit == runtime.LIMIT
            if name not in files:
                raise FileNotFoundError(name)
            return files[name]

        def publish_private(self, name, value, *, replace):
            assert replace is True
            files[name] = value

    @contextmanager
    def directory(path, **kwargs):
        events.append((path, kwargs))
        yield Pinned()

    monkeypatch.setattr(windows_files, "directory", directory)
    assert runtime.read(spec()) is None
    runtime.publish(spec(), record())
    assert runtime.read(spec()) == record()
    assert set(files) == {runtime.RECEIPT}
    assert all(event[0] == PureWindowsPath(spec().data_dir) / "config/services" for event in events)
    assert all(event[1]["owned_root"] == PureWindowsPath(spec().data_dir) for event in events)
    assert events[0][1]["create"] is False and events[1][1]["create"] is True


@pytest.mark.parametrize("name", [runtime.RECEIPT, runtime.STOP, runtime.RESTART])
def test_reinstall_ignores_valid_prior_registration_but_not_malformed_records(monkeypatch, name):
    from amplifier_web import windows_files
    old = record(registration="f" * 32)
    stored = restart(run=old) if name == runtime.RESTART else old
    class Pinned:
        def read_private(self, requested, *, limit):
            assert requested == name and limit == runtime.LIMIT
            return json.dumps(stored).encode()
    @contextmanager
    def directory(*args, **kwargs):
        yield Pinned()
    monkeypatch.setattr(windows_files, "address", PureWindowsPath)
    monkeypatch.setattr(windows_files, "directory", directory)
    assert (runtime.read_restart(spec()) if name == runtime.RESTART else runtime.read(spec(), name)) is None
    old["created"] = 134000000000000001  # Lossy/unknown old records are not ignored.
    with pytest.raises(ValueError):
        runtime.read_restart(spec()) if name == runtime.RESTART else runtime.read(spec(), name)


@pytest.fixture
def controls(monkeypatch):
    state = {"record": record(), "exited": False, "writes": [], "actual": record()}
    monkeypatch.setattr(runtime, "read", lambda *a: copy.deepcopy(state["record"]))
    monkeypatch.setattr(runtime, "publish",
                        lambda spec, value, name: state["writes"].append((name, copy.deepcopy(value))))
    @contextmanager
    def process(pid):
        assert pid == 123
        yield {**state["actual"], "exited": lambda: state["exited"]}
    monkeypatch.setattr(runtime, "process", process)
    return state


@pytest.mark.parametrize("change", [{"created": "134000000000000002"}, {"image": r"C:\Other\python.exe"}])
def test_reused_pid_or_wrong_image_never_receives_stop(controls, change):
    controls["actual"].update(change)
    assert runtime.observe(spec()) is None
    with pytest.raises(RuntimeError, match="identity changed"):
        runtime.request_stop(spec())
    assert controls["writes"] == []


def test_no_receipt_or_access_denied_is_not_force_stop(controls, monkeypatch):
    controls["record"] = None
    with pytest.raises(RuntimeError, match="No verified"):
        runtime.request_stop(spec())
    @contextmanager
    def denied(pid):
        raise PermissionError("synthetic")
        yield
    controls["record"] = record()
    monkeypatch.setattr(runtime, "process", denied)
    with pytest.raises(PermissionError):
        runtime.request_stop(spec())
    assert controls["writes"] == []


def test_stop_writes_once_and_waits_for_exact_handle_exit(controls, monkeypatch):
    def settle(_delay):
        assert controls["writes"] == [(runtime.STOP, record())]
        controls["exited"] = True
    monkeypatch.setattr(runtime.time, "sleep", settle)
    runtime.request_stop(spec())
    assert controls["writes"] == [(runtime.STOP, record())]


def test_late_stop_is_retained_after_exact_old_host_exit(controls):
    controls["exited"] = True
    runtime.request_stop(spec())
    assert controls["writes"] == [(runtime.STOP, record())]


def test_internal_recovery_stop_does_not_impersonate_owner_stop(controls, monkeypatch):
    controls["exited"] = True
    runtime.request_stop(spec(), expected=record(), recovery=True)
    assert controls["writes"] == [(runtime.RECOVERY_STOP, record())]
    monkeypatch.setattr(runtime, "read", lambda spec, name:
                        record() if name == runtime.RECOVERY_STOP else None)
    assert not runtime.stop_requested(spec(), record())
    runner = runtime.Runner.__new__(runtime.Runner)
    runner.spec, runner.record = spec(), record()
    assert runner.stop_requested()  # Still normal app cleanup, not a force kill.


def test_late_stop_is_retained_when_old_process_already_absent(controls, monkeypatch):
    @contextmanager
    def absent(pid):
        yield None
    monkeypatch.setattr(runtime, "process", absent)
    runtime.request_stop(spec())
    assert controls["writes"] == [(runtime.STOP, record())]


def test_stop_timeout_retains_request_without_retry(controls):
    with pytest.raises(RuntimeError, match="no force-stop"):
        runtime.request_stop(spec(), timeout=0)
    assert controls["writes"] == [(runtime.STOP, record())]


def test_expected_stop_cannot_follow_a_new_run_between_readiness_and_request(controls):
    expected = record(run="c" * 32)
    with pytest.raises(RuntimeError, match="expected task run changed"):
        runtime.request_stop(spec(), expected=expected)
    assert not controls["writes"]


def test_new_run_after_request_is_not_stopped_again(controls, monkeypatch):
    def replace(_delay):
        controls["record"]["run"] = "c" * 32
    monkeypatch.setattr(runtime.time, "sleep", replace)
    with pytest.raises(RuntimeError, match="changed while stopping"):
        runtime.request_stop(spec())
    assert len(controls["writes"]) == 1


def test_runner_ignores_stale_request_after_phase_change(monkeypatch):
    runner = runtime.Runner.__new__(runtime.Runner)
    runner.spec, runner.record = spec(), record()
    monkeypatch.setattr(runtime, "read", lambda *a: record(phase="starting"))
    assert runner.stop_requested()
    monkeypatch.setattr(runtime, "read", lambda *a: record(run="c" * 32))
    assert not runner.stop_requested()
    monkeypatch.setattr(runtime, "read", lambda *a: record(created="134000000000000002"))
    assert not runner.stop_requested()


def test_runner_watch_requests_aiohttp_graceful_exit(monkeypatch):
    from aiohttp.web_runner import GracefulExit
    runner = runtime.Runner.__new__(runtime.Runner)
    phases = []
    monkeypatch.setattr(runner, "stop_requested", lambda: True)
    monkeypatch.setattr(runner, "phase", phases.append)
    with pytest.raises(GracefulExit):
        asyncio.run(runner.watch())
    assert phases == ["stopping"]


def test_real_aiohttp_watcher_runs_cleanup_before_exit(tmp_path):
    # A subprocess is required: GracefulExit intentionally stops run_app's loop.
    source = """
import sys
from pathlib import Path
from aiohttp import web
from amplifier_web.windows_task_runtime import Runner
marker = Path(sys.argv[1])
runner = Runner.__new__(Runner)
runner.stop_requested = lambda: True
runner.phase = lambda phase: None
app = web.Application()
async def cleanup(app):
    marker.write_text('cleaned', encoding='utf-8')
app.on_cleanup.append(cleanup)
runner.attach(app)
web.run_app(app, host='127.0.0.1', port=0, print=None)
assert marker.read_text() == 'cleaned'
"""
    marker = tmp_path / "cleanup"
    result = subprocess.run([sys.executable, "-c", source, str(marker)],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "cleaned"


def test_private_log_bounds_tail_and_coalesces_writes(monkeypatch):
    from amplifier_web import windows_files
    snapshots = []
    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(windows_files, "address", PureWindowsPath)
    class Pinned:
        def publish_private(self, name, value, *, replace):
            assert name == runtime.LOG and replace is True
            snapshots.append(value)
    @contextmanager
    def directory(path, **kwargs):
        assert path == PureWindowsPath(spec().data_dir) / "config/services"
        assert kwargs == {"owned_root": PureWindowsPath(spec().data_dir), "create": True}
        yield Pinned()
    monkeypatch.setattr(windows_files, "directory", directory)
    with runtime.PrivateLog(spec()) as log:
        for _ in range(100):
            assert log.write("x" * 4096) == 4096
            log.flush()
        assert len(snapshots) == 1  # Initial empty snapshot; no per-line copies.
        assert len(log._tail) == runtime.LOG_LIMIT
        clock[0] += 1
        log.write("café / 日本語\n")
        assert len(snapshots) == 2
        assert len(snapshots[-1]) == runtime.LOG_LIMIT
        assert snapshots[-1].endswith("café / 日本語\n".encode())
        log.write("final")
    assert snapshots[-1].endswith(b"final") and len(snapshots) == 3


@pytest.mark.skipif(os.name != "nt", reason="Native passive Windows process handle required")
def test_native_identity_does_not_signal_or_terminate_sentinel():
    child = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time;time.sleep(2)"])
    with runtime.process(child.pid) as first:
        assert first and not first["exited"]()
        with runtime.process(child.pid) as second:
            assert runtime.matches(first, second)
        assert child.poll() is None
        assert child.wait(timeout=10) == 0
        assert first["exited"]()


@pytest.mark.skipif(os.name != "nt", reason="Native IPv4 owner table required")
def test_native_listener_table_joins_only_actual_listening_process():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        assert runtime.listener_owners("127.0.0.1", port) == {os.getpid()}
        assert runtime.listener_owned({"pid": os.getpid()}, "127.0.0.1", port)
        assert not runtime.listener_owned({"pid": os.getpid() + 1}, "127.0.0.1", port)
    assert os.getpid() not in runtime.listener_owners("127.0.0.1", port)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Toolhelp process ancestry required")
def test_native_ancestry_joins_real_child_without_environment_claims():
    child = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time;time.sleep(1)"])
    try:
        assert runtime.parent_pid(child.pid) == os.getpid()
        assert child.wait(timeout=10) == 0
    finally:
        if child.poll() is None:
            child.wait(timeout=10)