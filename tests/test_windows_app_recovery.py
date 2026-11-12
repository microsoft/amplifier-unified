"""One private rollback transaction; these controls are not native acceptance."""
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web import windows_app_recovery as recovery
from amplifier_web import application_generations as generations
from amplifier_web import windows_task_runtime as runtime
from amplifier_web import windows_service as service


@pytest.fixture
def transaction(tmp_path, monkeypatch):
    bootstrap = tmp_path / "bootstrap"
    spec = SimpleNamespace(python=str(bootstrap / "Scripts/pythonw.exe"),
                           data_dir=str(tmp_path / "data"), registration_id="a" * 32)
    owner = {"prefix": str(bootstrap), "digest": "b" * 64}
    run = {"schema": 1, "registration": spec.registration_id, "run": "c" * 32,
           "pid": 123, "created": "134000000000000001", "image": r"C:\Python\pythonw.exe",
           "phase": "listening"}
    restart = {"schema": 1, "run": run,
               "target": {"revision": "d" * 40, "generation": "e" * 32, "attemptId": "f" * 32}}
    promoted = {"schema": 1, "bootstrap": owner, "revision": "d" * 40, "generation": "e" * 32,
                "version": "2.0.0", "componentDigest": "2" * 64}
    graph = [{"name": "amplifier-unified", "version": "1.0.0",
              "url": "https://github.com/microsoft/amplifier-unified", "revision": "1" * 40}]
    state = {"active": copy.deepcopy(promoted), "receipt": None, "writes": [], "restores": []}
    monkeypatch.setattr(generations, "active", lambda home: copy.deepcopy(state["active"]))
    monkeypatch.setattr(generations, "bootstrap_identity", lambda path: copy.deepcopy(owner))
    monkeypatch.setattr(recovery, "read", lambda spec: copy.deepcopy(state["receipt"]))
    def write(spec, value):
        recovery._validate(value, spec)
        state["receipt"] = copy.deepcopy(value)
        state["writes"].append(value["status"])
        return copy.deepcopy(value)
    monkeypatch.setattr(recovery, "_write", write)
    def restore(home, promoted, previous, bootstrap, **kwargs):
        assert promoted == state["active"] and previous is None
        assert kwargs["expected_bootstrap"] == owner
        state["restores"].append(copy.deepcopy(promoted))
        state["active"] = None
    monkeypatch.setattr(generations, "restore", restore)
    import subprocess
    raw_graph = [{"name": graph[0]["name"], "version": graph[0]["version"],
                  "direct": {"url": graph[0]["url"],
                             "vcs_info": {"vcs": "git", "commit_id": graph[0]["revision"]}}}]
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=json.dumps(raw_graph)))
    old = {"pointer": None, "bootstrap": owner, "installation": owner,
           "identity": {"version": "1.0.0", "revision": "1" * 40, "instanceId": "2" * 32},
           "graph": graph, "extras": [], "artifact": None}
    return SimpleNamespace(spec=spec, restart=restart, old=old, state=state)


def test_exact_failed_successor_restores_once_without_claiming_restart_ack(transaction):
    t = transaction
    recovery.prepare(t.spec, t.restart, t.old)
    recovery.restore(t.spec, t.restart)
    assert t.state["writes"] == ["prepared", "restoring", "restored"]
    assert len(t.state["restores"]) == 1 and t.state["active"] is None
    with pytest.raises(ValueError, match="unused"):
        recovery.restore(t.spec, t.restart)
    assert len(t.state["restores"]) == 1


@pytest.mark.parametrize("change", ["target", "bootstrap", "old-install", "old-graph", "acknowledged"])
def test_changed_bindings_do_not_restore_or_replay(transaction, monkeypatch, change):
    t = transaction
    recovery.prepare(t.spec, t.restart, t.old)
    if change == "target":
        t.state["active"]["generation"] = "0" * 32
    elif change == "bootstrap":
        monkeypatch.setattr(generations, "bootstrap_identity", lambda path: {"prefix": "other", "digest": "0" * 64})
    elif change == "old-install":
        t.state["receipt"]["installation"]["digest"] = "0" * 64
    elif change == "old-graph":
        t.state["receipt"]["graph"][0]["version"] = "0.0.0"
    else:
        recovery.acknowledge(t.spec, t.restart["target"])
    with pytest.raises(ValueError):
        recovery.restore(t.spec, t.restart)
    assert not t.state["restores"]


def test_wrong_attempt_does_not_retire_parent_recovery(transaction):
    t = transaction
    recovery.prepare(t.spec, t.restart, t.old)
    assert not recovery.acknowledge(t.spec, {**t.restart["target"], "attemptId": "0" * 32})
    assert t.state["writes"] == ["prepared"]
    assert recovery.acknowledge(t.spec, t.restart["target"])
    assert t.state["writes"] == ["prepared", "acknowledged"]


@pytest.mark.parametrize("mode", ["accepted", "failed", "owner-stop", "timeout", "changed-run", "changed-binding", "unknown-stop"])
def test_updated_host_readiness_is_bounded_and_normal_stop_wins(transaction, monkeypatch, mode):
    import asyncio
    t = transaction
    record = t.restart["run"]
    observed = {**record, "exited": lambda: mode in {"failed", "timeout"}}
    monkeypatch.setattr(runtime, "read", lambda spec, name=runtime.RECEIPT:
        record if name == runtime.STOP and mode == "owner-stop" else None if name == runtime.STOP else
        {**record, "created": "1"} if mode == "changed-run" else record)
    monkeypatch.setattr(recovery, "read", lambda spec: {
        "restart": {} if mode == "changed-binding" else t.restart,
        "status": "acknowledged" if mode == "accepted" else "prepared"})
    stopped = []
    def stop(spec, **kwargs):
        stopped.append((spec, kwargs))
        if mode == "unknown-stop":
            raise RuntimeError("Normal stop not confirmed")
    monkeypatch.setattr(runtime, "request_stop", stop)
    ticks = iter([0, 91])
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    finished = SimpleNamespace(done=lambda: mode == "failed")
    if mode in {"changed-run", "changed-binding", "unknown-stop"}:
        with pytest.raises(RuntimeError):
            asyncio.run(service._wait_updated_host(t.spec, finished, observed, t.restart))
    else:
        assert asyncio.run(service._wait_updated_host(t.spec, finished, observed, t.restart)) is (mode in {"failed", "timeout"})
    assert len(stopped) == (1 if mode in {"timeout", "unknown-stop"} else 0)
    if stopped:
        assert stopped[0][1]["recovery"] is True


def test_old_run_stop_in_launch_gap_normally_stops_exact_successor(transaction, monkeypatch):
    import asyncio
    t = transaction
    old = t.restart["run"]
    current = {**old, "run": "0" * 32, "pid": 456, "created": "134000000000000002"}
    actual = {**current, "exited": lambda: False}
    monkeypatch.setattr(runtime, "read", lambda spec, name=runtime.RECEIPT:
                        old if name == runtime.STOP else current)
    stopped = []
    monkeypatch.setattr(runtime, "request_stop", lambda spec, **kwargs:
                        stopped.append((spec, kwargs)))
    monkeypatch.setattr(recovery, "read", lambda *a: pytest.fail("Stop must precede readiness/ACK"))
    assert asyncio.run(service._wait_updated_host(
        t.spec, SimpleNamespace(done=lambda: False), actual, t.restart)) is False
    assert stopped == [(t.spec, {"timeout": 30, "expected": current})]


@pytest.mark.parametrize("owner_stop", [False, True])
def test_readiness_timeout_real_signal_composes_with_supervisor_recovery(transaction, monkeypatch, owner_stop):
    import asyncio
    from contextlib import contextmanager
    t = transaction
    old = t.restart["run"]
    current = {**old, "run": "0" * 32, "pid": 456, "created": "134000000000000002"}
    state = {"launches": 0, "restores": 0, "signals": {}, "exited": False}
    monkeypatch.setattr(service, "_read", lambda home: ({"state": 4, "instances": 1}, t.spec))
    class Folder:
        def __truediv__(self, name):
            return SimpleNamespace(read_text=lambda: json.dumps(t.restart["target"]))
    def select(spec):
        return spec.python, ({**t.restart["target"], "folder": Folder()}
                             if state["launches"] == 1 else None)
    monkeypatch.setattr(service, "_launch_python", select)
    monkeypatch.setattr(runtime, "read_restart", lambda spec: t.restart)
    monkeypatch.setattr(runtime, "read", lambda spec, name=runtime.RECEIPT:
                        current if name == runtime.RECEIPT else state["signals"].get(name))
    actual = {**current, "exited": lambda: state["exited"]}
    @contextmanager
    def process(pid):
        assert pid == current["pid"]
        yield actual
    monkeypatch.setattr(runtime, "process", process)
    def publish(spec, record, name):
        state["signals"][name] = dict(record)
        state["exited"] = True  # Simulated app consumed its normal stop signal.
    monkeypatch.setattr(runtime, "publish", publish)
    monkeypatch.setattr(recovery, "read", lambda spec: {"restart": t.restart, "status": "prepared"})
    ticks = iter([0, 91])
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    async def launch(*args, **kwargs):
        state["launches"] += 1
        if state["launches"] == 1:
            return old, runtime.RESTART_EXIT, False
        if state["launches"] == 2:
            failed = await service._wait_updated_host(
                t.spec, SimpleNamespace(done=lambda: state["exited"]), actual, t.restart)
            if owner_stop:
                runtime.request_stop(t.spec, expected=current)
            return current, 0, failed
        return {**current, "run": "9" * 32}, 0, False
    monkeypatch.setattr(service, "_launch_owned_host", launch)
    monkeypatch.setattr(recovery, "restore", lambda *args:
                        state.update(restores=state["restores"] + 1))
    assert service._supervise(t.spec) == 0
    assert state["signals"][runtime.RECOVERY_STOP] == current
    assert state["restores"] == (0 if owner_stop else 1)
    assert state["launches"] == (2 if owner_stop else 3)
    assert (runtime.STOP in state["signals"]) is owner_stop


@pytest.mark.parametrize("change", [{"status": "success"}, {"status": []}, {"schema": True},
                                  {"registration": "0" * 32}, {"extra": "no command allowed"}])
def test_recovery_record_has_no_arbitrary_command_or_success_protocol(transaction, change):
    t = transaction
    value = recovery.prepare(t.spec, t.restart, t.old)
    with pytest.raises(ValueError):
        recovery._validate({**value, **change}, t.spec)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "wrong-health", "same-host", "wrong-attempt", "wrong-pointer",
                                   "wrong-run", "wrong-origin", "replacement-unknown", "wrong-process",
                                   "graph-drift", "settings-extras", "commit-failure"])
async def test_only_verified_old_host_can_reopen_after_rollback(transaction, monkeypatch, change):
    import asyncio
    from amplifier_web import app_component_graph as components, app_updates, auth
    t = transaction
    recovery.prepare(t.spec, t.restart, t.old)
    recovery.restore(t.spec, t.restart)
    state = {"pendingRestart": {**t.restart["target"], "version": "2.0.0",
                               "sourceInstanceId": t.old["identity"]["instanceId"]},
             "pendingReplacement": None, "phase": "activating", "settingsPreserved": True}
    identity = {**t.old["identity"], "instanceId": "9" * 32}
    current_run = {**t.restart["run"], "run": "8" * 32}
    monkeypatch.setattr(recovery.os, "getpid", lambda: 123)
    saved, events = [], []
    def publish(**kwargs):
        if change == "commit-failure":
            raise OSError("synthetic state write failure")
        saved.append(copy.deepcopy(state))
    manager = SimpleNamespace(home=t.spec.data_dir, running_identity=identity, lock=asyncio.Lock(),
        service=SimpleNamespace(state={"updates": state}, lock=asyncio.Lock(), _publish_changes=publish),
        diagnostics=SimpleNamespace(record=lambda *a, **kw: events.append((a, kw))),
        wakeup=asyncio.Event())
    monkeypatch.setattr(service, "update_owner", lambda home: t.spec)
    monkeypatch.setattr(auth, "data_identity", lambda home: "same-data")
    monkeypatch.setattr(runtime, "observe", lambda spec: current_run)
    monkeypatch.setattr(app_updates, "installed_extras", lambda: ["tui"] if change == "settings-extras" else [])
    async def graph(*a, **kw):
        return [] if change == "graph-drift" else t.old["graph"]
    monkeypatch.setattr(components, "read_graph", graph)
    health = {"ok": True, "app": "amplifier-unified", "dataIdentity": "same-data", **identity}
    if change == "wrong-health":
        health["dataIdentity"] = "other"
    elif change == "same-host":
        identity["instanceId"] = t.old["identity"]["instanceId"]
        health.update(identity)
    elif change == "wrong-attempt":
        state["pendingRestart"]["attemptId"] = "0" * 32
    elif change == "wrong-pointer":
        t.state["active"] = t.state["receipt"]["promoted"]
    elif change == "wrong-run":
        current_run["run"] = t.restart["run"]["run"]
    elif change == "wrong-origin":
        state["pendingRestart"]["sourceInstanceId"] = "unrelated-host"
    elif change == "replacement-unknown":
        state["pendingReplacement"] = {"unqualified": True}
    elif change == "wrong-process":
        current_run["pid"] = 124
    before = copy.deepcopy(state)
    if change == "commit-failure":
        with pytest.raises(OSError):
            await recovery.confirm(manager, health)
        assert state == before
    else:
        assert await recovery.confirm(manager, health) is (change is None)
    if change is not None:
        assert not saved and not events and not manager.wakeup.is_set()
        assert state == before
    else:
        assert state["pendingRestart"] is None and state["phase"] == "error"
        assert "applicationResult" not in state  # Never turn failure into restart-ack.
        assert state["applicationRecovery"]["failedTarget"] == t.restart["target"]
        assert state["settingsPreserved"] and manager.wakeup.is_set()
        state["application"] = {"revision": t.restart["target"]["revision"]}
        assert recovery.automatic_retry_held(state)
        state["application"]["revision"] = "0" * 40
        assert not recovery.automatic_retry_held(state)