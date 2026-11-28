"""Lifetime policy, legacy compatibility and the reserve-before-spawn boundary."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from amplifier_web import windows_service as service
from amplifier_web import windows_service_lifetime as lifetime

SID = "S-1-5-21-100-200-300-1001"
KEY = {"authenticationId": "00000000:00000001", "logonTime": "134000000000000001"}


def spec(**values):
    return service.TaskSpec(**{
        "sid": SID, "python": r"C:\test\Scripts\pythonw.exe", "data_dir": r"C:\data",
        "workspace": r"C:\work", "state_home": r"C:\state", "tool_path": r"C:\tools",
        "registration_id": "a" * 32, "supervised": True,
        "repeat_start": "2026-11-28T01:02:03+00:00", **values,
    })


@pytest.fixture
def disk(monkeypatch):
    state = {"bytes": None, "writes": [], "time": 100000, "key": dict(KEY)}

    class Pinned:
        def read_private(self, name, *, limit):
            assert name == lifetime.RECORD and limit == 4096
            if state["bytes"] is None:
                raise FileNotFoundError(name)
            if state.get("read_error"):
                raise PermissionError("test read refused")
            return state["bytes"]

        def publish_private(self, name, raw, *, replace):
            assert name == lifetime.RECORD and replace and len(raw) < 4096
            if state.get("write_error"):
                raise PermissionError("test write refused")
            state["bytes"] = raw
            state["writes"].append(json.loads(raw))

    @contextmanager
    def store(_):
        yield Pinned()

    monkeypatch.setattr(lifetime, "supported", lambda _: True)
    monkeypatch.setattr(lifetime, "store", store)
    monkeypatch.setattr(lifetime.time, "time", lambda: state["time"])

    def identity(_):
        if state.get("ambiguous"):
            raise ValueError("two sessions")
        return state["key"]

    monkeypatch.setattr(lifetime, "native_sign_in", identity)
    return state


def saved(disk):
    return json.loads(disk["bytes"])


def seed(disk, *, manual=False):
    return lifetime.owner_choice(spec(), stop=False, manual=manual)


def test_stop_while_down_is_saved_and_neither_time_nor_new_sign_in_clears_it(disk):
    lifetime.owner_choice(spec(), stop=True)
    before = disk["bytes"]
    for offset in (1, 6 * 3600, 30 * 86400):
        disk["time"] += offset
        disk["key"] = {**KEY, "authenticationId": "00000000:00000002"}
        with pytest.raises(lifetime.Paused, match="stays stopped"):
            lifetime.admit(spec())
        assert disk["bytes"] == before
    lifetime.owner_choice(spec(), stop=False)
    assert lifetime.admit(spec()) == saved(disk)["intent"]
    assert saved(disk)["starts"] == []  # An explicit start is not an automatic start.


def test_missing_record_grants_no_automatic_repeating_start_but_explicit_start_works(disk):
    with pytest.raises(lifetime.Paused):
        lifetime.admit(spec())
    intent = lifetime.owner_choice(spec(), stop=False)
    assert lifetime.admit(spec()) == intent


@pytest.mark.parametrize("failure", ["missing", "malformed", "unreadable", "identity"])
def test_explicit_start_does_not_require_a_successful_new_read(disk, failure):
    if failure == "malformed":
        disk["bytes"] = b"not json"
    elif failure == "unreadable":
        seed(disk)
        disk["read_error"] = True
    elif failure == "identity":
        disk["ambiguous"] = True
    intent = lifetime.owner_choice(spec(), stop=False)
    disk.pop("read_error", None)
    assert lifetime.admit(spec()) == intent
    assert saved(disk)["manual"] is False


def test_limit_is_shared_across_entries_and_refills_only_after_six_hours(disk):
    seed(disk)
    for _ in range(3):
        lifetime.admit(spec())
        disk["time"] += 1
    assert len(saved(disk)["starts"]) == 3
    with pytest.raises(lifetime.Paused, match="three times"):
        lifetime.admit(spec())
    first = saved(disk)["starts"][0]
    disk["time"] = first + lifetime.WINDOW - 1
    with pytest.raises(lifetime.Paused):
        lifetime.admit(spec())
    disk["time"] += 1
    lifetime.admit(spec())
    assert saved(disk)["starts"] == [first + 1, first + 2, disk["time"]]


def test_new_sign_in_and_explicit_start_keep_automatic_reservations(disk):
    seed(disk)
    for _ in range(3):
        lifetime.admit(spec())
    starts = saved(disk)["starts"]
    disk["key"] = {**KEY, "authenticationId": "00000000:00000002"}
    with pytest.raises(lifetime.Paused):
        lifetime.admit(spec())
    lifetime.owner_choice(spec(), stop=False)
    lifetime.admit(spec())  # Explicit start is allowed through the full budget.
    assert saved(disk)["starts"] == starts
    with pytest.raises(lifetime.Paused):
        lifetime.admit(spec())


@pytest.mark.parametrize("failure", ["backward-clock", "ambiguous", "malformed", "write-error"])
def test_automatic_uncertainty_pauses_and_does_not_spend_a_lost_reservation(disk, failure):
    seed(disk)
    before = disk["bytes"]
    if failure == "backward-clock":
        disk["time"] -= 1
    elif failure == "ambiguous":
        disk["ambiguous"] = True
    elif failure == "malformed":
        disk["bytes"] = b"{}"
        before = disk["bytes"]
    else:
        disk["write_error"] = True
    with pytest.raises(lifetime.Paused):
        lifetime.admit(spec())
    assert disk["bytes"] == before


def test_stop_and_later_start_supersede_an_earlier_start_ticket(disk):
    old = seed(disk)
    assert lifetime.allowed(spec(), old, exact=True)
    lifetime.owner_choice(spec(), stop=True)
    assert not lifetime.allowed(spec(), old)
    new = lifetime.owner_choice(spec(), stop=False)
    assert new != old
    assert lifetime.allowed(spec(), old)  # A running host need not end for another Open.
    assert not lifetime.allowed(spec(), old, exact=True)  # A delayed request must not replay.
    assert lifetime.allowed(spec(), new, exact=True)


def test_legacy_missing_record_and_unknown_launcher_keep_the_old_start_path(disk, monkeypatch):
    assert lifetime.admit(spec(repeat_start=None)) is None
    monkeypatch.setattr(lifetime, "supported", lambda _: False)
    monkeypatch.setattr(lifetime, "store", lambda _: pytest.fail("unknown launcher must be left alone"))
    assert lifetime.admit(spec()) is None
    assert lifetime.owner_choice(spec(), stop=True) is None


@pytest.mark.parametrize("operation", ["start", "stop", "restart"])
def test_legacy_controls_are_not_blocked_by_an_unreadable_new_record(disk, monkeypatch, operation):
    value = spec(repeat_start=None)
    disk["read_error"], disk["write_error"] = True, True
    monkeypatch.setattr(service, "_read", lambda _: ({"instances": 0, "xml": "old"}, value))
    effects = []
    monkeypatch.setattr(service, "_scheduler", lambda op, *a, **k: effects.append(op))
    service.command(operation, value.data_dir)
    assert effects == ([] if operation == "stop" else ["start"])
    assert lifetime.admit(value) is None


def test_host_watcher_sees_lifetime_stop_without_using_scheduler_result(disk, monkeypatch):
    from amplifier_web import windows_task_runtime as runtime
    intent = seed(disk)
    runner = runtime.Runner.__new__(runtime.Runner)
    runner.spec, runner.intent, runner.record = spec(), intent, {}
    monkeypatch.setattr(runtime, "stop_requested", lambda *a, **k: False)
    assert not runner.stop_requested()
    lifetime.owner_choice(spec(), stop=True)
    assert runner.stop_requested()


@pytest.mark.parametrize("field, bad", [
    ("schema", True), ("ownerSid", "foreign"), ("registration", "b" * 32),
    ("intent", "not-random"), ("manual", 1), ("starts", [100001]),
    ("starts", [1, 2, 3, 4]), ("starts", [True]), ("lastTime", -1),
    ("logon", {"authenticationId": "bad", "logonTime": "123"}),
])
def test_closed_record_refuses_changed_fields(disk, field, bad):
    seed(disk)
    value = saved(disk)
    value[field] = bad
    with pytest.raises(ValueError):
        lifetime.validate(value, spec())


def test_duplicate_or_foreign_state_does_not_grant_a_start(disk):
    seed(disk)
    disk["bytes"] = disk["bytes"][:-1] + b', "schema": 1}'
    with pytest.raises(lifetime.Paused):
        lifetime.admit(spec())


def test_readers_accept_both_layouts_but_no_installer_selects_repetition(monkeypatch):
    old = spec(repeat_start=None)
    assert "repeat_start" not in json.loads(__import__("base64").b64decode(old.encoded()))
    assert service.owned_spec(service.task_xml(old), SID) == old
    new = spec()
    xml = ET.fromstring(service.task_xml(new))
    assert len(xml.find(service._q("Triggers"))) == 2
    for path in ("Triggers/TimeTrigger/Enabled", "Triggers/TimeTrigger/Repetition/StopAtDurationEnd"):
        parent_path, _, name = path.rpartition("/")
        parent = xml.find("/".join(service._q(part) for part in parent_path.split("/")))
        parent.remove(parent.find(service._q(name)))
    assert service.owned_spec(ET.tostring(xml, encoding="unicode"), SID) == new
    from amplifier_web import host_dependencies, shared_state, windows_files
    monkeypatch.setattr(service, "_require_python", lambda: None)
    monkeypatch.setattr(service, "_background_python", lambda _: old.python)
    monkeypatch.setattr(host_dependencies, "tool_path", lambda: old.tool_path)
    monkeypatch.setattr(shared_state, "shared_state_home", lambda: old.state_home)
    monkeypatch.setattr(windows_files, "address", lambda value: value)
    for key in service.LAUNCH_ROOTS:
        monkeypatch.delenv(key, raising=False)
    assert service._new_spec(old.data_dir, old.workspace, SID, None).repeat_start is None
    assert service._new_spec(old.data_dir, old.workspace, SID, old).repeat_start is None


@pytest.mark.parametrize("change", ["interval", "boundary", "extra-trigger"])
def test_new_layout_reader_stays_strict(change):
    xml = ET.fromstring(service.task_xml(spec()))
    timer = xml.find(f"{service._q('Triggers')}/{service._q('TimeTrigger')}")
    if change == "interval":
        timer.find(f"{service._q('Repetition')}/{service._q('Interval')}").text = "PT1M"
    elif change == "boundary":
        timer.find(service._q("StartBoundary")).text = "2026-11-28T02:02:03+00:00"
    else:
        xml.find(service._q("Triggers")).append(ET.fromstring(ET.tostring(timer)))
    with pytest.raises(service.TaskNotConfirmed):
        service.owned_spec(ET.tostring(xml, encoding="unicode"), SID)


def test_launcher_saves_reservation_before_starting_a_child_and_blocks_on_save_failure(disk, monkeypatch):
    from amplifier_web import windows_jobs
    seed(disk)
    value = spec()
    monkeypatch.setattr(service, "_require_python", lambda: None)
    monkeypatch.setattr(service, "_owner", lambda: SID)
    monkeypatch.setattr(service, "_read", lambda _: ({"state": 4, "instances": 1}, value))
    monkeypatch.setattr(service.os, "chdir", lambda _: None)
    monkeypatch.setattr(windows_jobs, "contain_current_host", lambda: None)
    calls = []

    def supervise(_, *, intent):
        assert saved(disk)["starts"] == [disk["time"]]
        assert saved(disk)["intent"] == intent
        calls.append(intent)
        return 0

    monkeypatch.setattr(service, "_supervise", supervise)
    with pytest.raises(SystemExit) as ended:
        service._run(value.encoded())
    assert ended.value.code == 0 and len(calls) == 1
    disk["write_error"] = True
    from amplifier_web import windows_task_runtime
    monkeypatch.setattr(windows_task_runtime, "record_failure", lambda *a: None)
    with pytest.raises(SystemExit) as ended:
        service._run(value.encoded())
    assert ended.value.code == 0 and len(calls) == 1


def test_owner_stop_while_down_and_internal_maintenance_use_different_paths(disk, monkeypatch):
    value = spec()
    monkeypatch.setattr(service, "_read", lambda _: ({"instances": 0, "xml": "owned"}, value))
    service.command("stop", value.data_dir)
    assert saved(disk)["stopped"] is True
    before = disk["bytes"]
    monkeypatch.setattr(service, "_scheduler", lambda *a, **k: None)
    service.command("start", value.data_dir, internal=True)
    assert disk["bytes"] == before
    service.command("start", value.data_dir)
    assert saved(disk)["stopped"] is False


@pytest.mark.skipif(os.name != "nt", reason="Native Windows token and Scheduler account required")
def test_native_sign_in_is_readable_for_the_runner_account():
    from amplifier_web.windows_files import current_process_sid
    key = lifetime.native_sign_in(current_process_sid())
    assert lifetime.logon_key(key) == key