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


def test_stop_timeout_retains_request_without_retry(controls):
    with pytest.raises(RuntimeError, match="no force-stop"):
        runtime.request_stop(spec(), timeout=0)
    assert controls["writes"] == [(runtime.STOP, record())]


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