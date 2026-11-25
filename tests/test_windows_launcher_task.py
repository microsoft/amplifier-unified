"""Portable helper-task policy checks. No native Task Scheduler is contacted."""
import base64
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from amplifier_web import windows_launcher_task as helper, windows_service as service

SID = "S-1-5-21-100-200-300-1001"
HOME = r"C:\Synthetic owner\Unified data"
PYTHON = HOME + r"\updates\generation\tools\amplifier-unified\Scripts\pythonw.exe"


def spec(**overrides):
    values = dict(sid=SID, data_dir=HOME, registration="a" * 32, python=PYTHON,
                  request="b" * 32, registered="2026-11-17T23:58:00")
    return helper.HelperSpec(**{**values, **overrides})


def node(root, path):
    return root.find("/".join(service._q(part) for part in path.split("/")))


def text(root, path):
    found = node(root, path)
    return found.text if found is not None else None


def test_definition_roundtrip_action_payload_triggers_and_settings():
    value = spec()
    xml = helper.helper_xml(value)
    assert helper.owned_helper(xml, SID, HOME) == value
    tree = ET.fromstring(xml)
    assert helper.task_name(SID) == "AmplifierUnifiedLauncher-" + SID
    assert text(tree, "RegistrationInfo/URI") == "\\" + helper.task_name(SID)
    assert text(tree, "RegistrationInfo/Author") == SID
    assert text(tree, "RegistrationInfo/Source") == helper.SOURCE
    description = text(tree, "RegistrationInfo/Description")
    raw = base64.b64decode(description[len(helper.SOURCE) + 1:], validate=True)
    assert raw == json.dumps(asdict(value), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("ascii")
    assert text(tree, "Principals/Principal/UserId") == SID
    assert text(tree, "Principals/Principal/LogonType") == "InteractiveToken"
    assert text(tree, "Principals/Principal/RunLevel") == "LeastPrivilege"
    assert text(tree, "Actions/Exec/Command") == value.python
    assert text(tree, "Actions/Exec/WorkingDirectory") == PYTHON.rsplit("\\", 1)[0]
    arguments = text(tree, "Actions/Exec/Arguments").split()
    assert arguments[:4] == ["-I", "-m", "amplifier_web.windows_launcher_refresh", "--automatic"]
    assert json.loads(base64.urlsafe_b64decode(arguments[4])) == {"home": HOME, "request": value.request}
    assert text(tree, "Triggers/LogonTrigger/UserId") == SID
    assert len(node(tree, "Triggers")) == 3
    assert text(tree, "Triggers/TimeTrigger/StartBoundary") == "2026-11-18T00:03:00"
    assert text(tree, "Triggers/TimeTrigger/Repetition/Interval") == "PT5M"
    assert text(tree, "Triggers/TimeTrigger/Repetition/Duration") == "P1D"
    assert text(tree, "Triggers/TimeTrigger/Repetition/StopAtDurationEnd") == "false"
    assert text(tree, "Triggers/CalendarTrigger/StartBoundary") == "2026-11-18T23:58:00"
    assert text(tree, "Triggers/CalendarTrigger/ScheduleByDay/DaysInterval") == "1"
    for path, expected in {
        "MultipleInstancesPolicy": "IgnoreNew", "StartWhenAvailable": "true",
        "ExecutionTimeLimit": "PT1H", "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false", "AllowHardTerminate": "true",
        "RunOnlyIfNetworkAvailable": "false", "AllowStartOnDemand": "true",
        "Enabled": "true", "Hidden": "false", "RunOnlyIfIdle": "false",
        "WakeToRun": "false", "Priority": "7", "IdleSettings/StopOnIdleEnd": "false",
        "IdleSettings/RestartOnIdle": "false",
    }.items():
        assert text(tree, "Settings/" + path) == expected
    assert node(tree, "Settings/RestartOnFailure") is None
    assert node(tree, "Settings/DeleteExpiredTaskAfter") is None
    assert not list(tree.iter(service._q("EndBoundary")))


@pytest.mark.parametrize("field,value", [
    ("sid", "owner"), ("sid", "S-1-5-18"), ("sid", None),
    ("data_dir", "relative"), ("data_dir", r"\\server\share"), ("data_dir", r"C:\data\..\other"),
    ("data_dir", "C:\\bad\npath"), ("python", r"C:\Tools\python.exe"),
    ("python", r"C:\Tools\cmd.exe"), ("python", "pythonw.exe"),
    ("python", r"C:\Tools\literal%\pythonw.exe"), ("python", r"C:\Tools\%NAME%\pythonw.exe"),
    ("registration", "x" * 32), ("registration", None),
    ("request", "c" * 31), ("request", None),
    ("registered", "2026-11-17T00:00:00+01:00"), ("registered", "2026-02-30T00:00:00"),
    ("registered", "2026-11-17T00:00:00.1"), ("registered", None),
])
def test_unsafe_spec_is_rejected(field, value):
    with pytest.raises(ValueError):
        spec(**{field: value})


@pytest.mark.parametrize("path,value", [
    ("RegistrationInfo/Description", "foreign"),
    ("RegistrationInfo/Source", "foreign"),
    ("RegistrationInfo/URI", r"\Other"),
    ("Actions/Exec/Command", r"C:\evil.exe"),
    ("Actions/Exec/Arguments", "--other"),
    ("Actions/Exec/WorkingDirectory", r"C:\Other"),
    ("Triggers/LogonTrigger/UserId", "S-1-5-21-100-200-300-1002"),
    ("Triggers/TimeTrigger/StartBoundary", "2026-11-18T00:04:00"),
    ("Triggers/TimeTrigger/Repetition/Interval", "PT1M"),
    ("Triggers/TimeTrigger/Repetition/Duration", "P2D"),
    ("Triggers/TimeTrigger/Repetition/StopAtDurationEnd", "true"),
    ("Triggers/TimeTrigger/Enabled", "false"),
    ("Triggers/CalendarTrigger/Enabled", "false"),
    ("Triggers/CalendarTrigger/ScheduleByDay/DaysInterval", "2"),
    ("Principals/Principal/UserId", "S-1-5-21-100-200-300-1002"),
    ("Principals/Principal/LogonType", "Password"),
    ("Principals/Principal/RunLevel", "HighestAvailable"),
    ("Settings/StartWhenAvailable", "false"),
    ("Settings/ExecutionTimeLimit", "PT0S"),
    ("Settings/MultipleInstancesPolicy", "Parallel"),
])
def test_changed_definition_is_not_owned(path, value):
    tree = ET.fromstring(helper.helper_xml(spec()))
    node(tree, path).text = value
    with pytest.raises(helper.NotOurs):
        helper.owned_helper(ET.tostring(tree, encoding="unicode"), SID, HOME)


@pytest.mark.parametrize("parent,name", [
    ("Triggers", "BootTrigger"), ("Triggers", "LogonTrigger"),
    ("Actions", "Exec"), ("Settings", "RestartOnFailure"),
    ("Settings", "DeleteExpiredTaskAfter"), ("Settings", "UnknownSetting"),
    ("Triggers/TimeTrigger", "EndBoundary"),
])
def test_extra_definition_element_is_not_owned(parent, name):
    tree = ET.fromstring(helper.helper_xml(spec()))
    ET.SubElement(node(tree, parent), service._q(name))
    with pytest.raises(helper.NotOurs):
        helper.owned_helper(ET.tostring(tree, encoding="unicode"), SID, HOME)


@pytest.mark.parametrize("xml", [
    None, "<Task>", "<!DOCTYPE x><Task/>",
    # pytest puts the case's name into an environment variable, and Windows limits its length.
    pytest.param("x" * (service.LIMIT + 1), id="longer-than-the-limit"),
])
def test_invalid_xml_is_not_owned(xml):
    with pytest.raises(helper.NotOurs):
        helper.owned_helper(xml, SID, HOME)


def test_bad_encoded_metadata_is_not_owned():
    for description in (helper.SOURCE + ":%%", helper.SOURCE + ":W10=",
                        helper.SOURCE + ":" + base64.b64encode(b'{"unknown":1}').decode()):
        tree = ET.fromstring(helper.helper_xml(spec()))
        node(tree, "RegistrationInfo/Description").text = description
        with pytest.raises(helper.NotOurs):
            helper.owned_helper(ET.tostring(tree, encoding="unicode"), SID, HOME)


@pytest.mark.parametrize("sid,home,python", [
    (SID + "1", HOME, PYTHON), (SID, r"C:\Other", PYTHON),
    (SID, HOME, r"C:\Other\updates\pythonw.exe"),
    (SID, HOME, HOME + r"\updates-other\pythonw.exe"),
])
def test_other_owner_folder_or_outside_python_is_not_owned(sid, home, python):
    xml = helper.helper_xml(spec(python=python))
    with pytest.raises(helper.NotOurs):
        helper.owned_helper(xml, sid, home)


def test_windows_paths_are_compared_by_parts_without_case():
    value = spec(python=PYTHON.upper())
    assert helper.owned_helper(helper.helper_xml(value), SID, HOME.lower()) == value


def test_scheduler_form_date_account_and_omitted_defaults_pass(monkeypatch):
    tree = ET.fromstring(helper.helper_xml(spec()))
    ET.SubElement(node(tree, "RegistrationInfo"), service._q("Date")).text = "2026-11-18T00:00:00"
    node(tree, "Triggers/LogonTrigger/UserId").text = r"COMPUTER\name"
    calls = []

    def same_account(name, sid):
        calls.append((name, sid))

    monkeypatch.setattr(service, "_same_account", same_account)
    for path in (
        "Triggers/LogonTrigger/Enabled", "Triggers/TimeTrigger/Enabled",
        "Triggers/CalendarTrigger/Enabled", "Triggers/TimeTrigger/Repetition/StopAtDurationEnd",
        "Principals/Principal/RunLevel", "Settings/Enabled", "Settings/WakeToRun",
        "Settings/RunOnlyIfIdle", "Settings/RunOnlyIfNetworkAvailable",
        "Settings/AllowStartOnDemand", "Settings/Priority", "Settings/Hidden", "Settings/AllowHardTerminate",
    ):
        parent, _, name = path.rpartition("/")
        node(tree, parent).remove(node(tree, path))
    ET.indent(tree)
    assert helper.owned_helper(ET.tostring(tree, encoding="unicode"), SID, HOME) == spec()
    assert calls == [(r"COMPUTER\name", SID)]


def test_missing_nondefault_start_when_available_does_not_pass():
    tree = ET.fromstring(helper.helper_xml(spec()))
    node(tree, "Settings").remove(node(tree, "Settings/StartWhenAvailable"))
    with pytest.raises(helper.NotOurs):
        helper.owned_helper(ET.tostring(tree, encoding="unicode"), SID, HOME)


def test_account_lookup_failure_is_not_ownership(monkeypatch):
    tree = ET.fromstring(helper.helper_xml(spec()))
    node(tree, "Triggers/LogonTrigger/UserId").text = r"COMPUTER\name"

    def fail(*args):
        raise service._LookupFailed("no answer")

    monkeypatch.setattr(service, "_same_account", fail)
    with pytest.raises(helper.NotOurs):
        helper.owned_helper(ET.tostring(tree, encoding="unicode"), SID, HOME)


@pytest.mark.parametrize("change", ["arguments", "xml"])
def test_definition_size_is_bounded(change):
    value = spec(data_dir="C:\\" + "项" * 9000) if change == "arguments" else spec(
        python="C:\\" + "项" * 16000 + "\\pythonw.exe")
    with pytest.raises(ValueError, match="budget|large"):
        helper.helper_xml(value)


@pytest.fixture
def backend(monkeypatch):
    tasks, calls = {}, []
    monkeypatch.setattr(service, "_owner", lambda: SID)

    def scheduler(op, sid, *, previous=None, xml=None, name=None):
        calls.append((op, name, previous))
        assert sid == SID and name == helper.task_name(SID)
        task = tasks.get(name)
        if op != "query":
            assert previous == (task["xml"] if task else None)
            if op in {"remove", "replace"}:
                assert task["instances"] == 0
        if op in {"create", "replace"}:
            tasks[name] = {"exists": True, "xml": xml, "instances": 0, "state": 3}
        elif op in {"remove", "retire"}:
            del tasks[name]
        elif op == "start" and task["instances"] == 0:
            task.update(instances=1, state=4)
        return dict(tasks.get(name, {"exists": False}))

    monkeypatch.setattr(service, "_scheduler", scheduler)
    return SimpleNamespace(tasks=tasks, calls=calls, scheduler=scheduler)


def install(backend, value=None, *, instances=0):
    value = value or spec()
    backend.tasks[helper.task_name(SID)] = {
        "exists": True, "xml": helper.helper_xml(value), "state": 4 if instances else 3,
        "instances": instances,
    }


def test_create_readback_reuse_start_and_running(backend):
    assert helper.query(HOME) == ({"exists": False}, None)
    assert helper.running(HOME) is False
    backend.calls.clear()
    helper.ensure(HOME, spec().request, spec().registration, PYTHON)
    assert [op for op, _, _ in backend.calls] == ["query", "create", "query"]
    assert helper.query(HOME)[1].request == spec().request
    backend.calls.clear()
    helper.ensure(HOME, spec().request, spec().registration, PYTHON)
    assert [op for op, _, _ in backend.calls] == ["query"]
    assert helper.running(HOME) is False
    helper.start(HOME)
    assert helper.running(HOME) is True
    helper.start(HOME)
    assert helper.query(HOME)[0]["instances"] == 1
    assert all(name == helper.task_name(SID) for _, name, _ in backend.calls)


@pytest.mark.parametrize("change", [
    {"request": "c" * 32}, {"registration": "d" * 32},
    {"python": PYTHON.replace("generation", "older-generation")},
])
def test_leftover_passes_ownership_and_is_replaced(backend, change):
    old = replace(spec(), **change)
    install(backend, old)
    original = backend.tasks[helper.task_name(SID)]["xml"]
    assert helper.query(HOME)[1] == old
    backend.calls.clear()
    helper.ensure(HOME, spec().request, spec().registration, PYTHON)
    assert [op for op, _, _ in backend.calls] == ["query", "replace", "query"]
    assert backend.calls[1][2] == original
    new = helper.query(HOME)[1]
    assert (new.request, new.registration, new.python) == (spec().request, spec().registration, PYTHON)


def test_matching_running_task_is_left_alone(backend):
    install(backend, instances=1)
    helper.ensure(HOME, spec().request, spec().registration, PYTHON)
    assert [op for op, _, _ in backend.calls] == ["query"]


def test_running_leftover_is_not_replaced(backend):
    install(backend, spec(request="c" * 32), instances=1)
    original = dict(backend.tasks[helper.task_name(SID)])
    with pytest.raises(service.WindowsServiceError, match="still running"):
        helper.ensure(HOME, spec().request, spec().registration, PYTHON)
    assert backend.tasks[helper.task_name(SID)] == original
    assert [op for op, _, _ in backend.calls] == ["query"]


@pytest.mark.parametrize("operation", ["ensure", "start", "running", "remove_task", "retire_task"])
def test_foreign_task_is_never_changed(backend, operation):
    install(backend)
    task = backend.tasks[helper.task_name(SID)]
    task["xml"] = task["xml"].replace(helper.SOURCE, "foreign")
    original = dict(task)
    if operation in {"remove_task", "retire_task"}:
        assert getattr(helper, operation)(HOME) is False
    else:
        with pytest.raises(helper.NotOurs):
            if operation == "ensure":
                helper.ensure(HOME, spec().request, spec().registration, PYTHON)
            else:
                getattr(helper, operation)(HOME)
    assert task == original
    assert [op for op, _, _ in backend.calls] == ["query"]


def test_missing_start_does_not_create_task(backend):
    with pytest.raises(service.WindowsServiceError, match="not installed"):
        helper.start(HOME)
    assert not backend.tasks


def test_outside_python_is_refused_before_create(backend):
    with pytest.raises(helper.NotOurs):
        helper.ensure(HOME, spec().request, spec().registration, r"C:\Other\pythonw.exe")
    assert not backend.tasks
    assert [op for op, _, _ in backend.calls] == ["query"]


@pytest.mark.parametrize("observation", ["missing", "changed"])
def test_failed_registration_readback_is_not_replayed(backend, monkeypatch, observation):
    def altered(op, sid, **kwargs):
        result = backend.scheduler(op, sid, **kwargs)
        if op == "create":
            if observation == "missing":
                backend.tasks.clear()
            else:
                install(backend, spec(request="c" * 32))
        return result

    monkeypatch.setattr(service, "_scheduler", altered)
    with pytest.raises(service.WindowsServiceError, match="not verified"):
        helper.ensure(HOME, spec().request, spec().registration, PYTHON)
    assert [op for op, _, _ in backend.calls] == ["query", "create", "query"]


@pytest.mark.parametrize("operation", ["remove_task", "retire_task"])
@pytest.mark.parametrize("instances", [0, 1])
def test_removal_instance_rule_and_exact_xml(backend, operation, instances):
    install(backend, instances=instances)
    original = backend.tasks[helper.task_name(SID)]["xml"]
    expected = operation == "retire_task" or instances == 0
    assert getattr(helper, operation)(HOME) is expected
    assert bool(backend.tasks) is not expected
    if expected:
        assert backend.calls[-1] == (
            "retire" if operation == "retire_task" else "remove", helper.task_name(SID), original)
    else:
        assert [op for op, _, _ in backend.calls] == ["query"]


@pytest.mark.parametrize("operation", ["remove_task", "retire_task"])
def test_removal_missing_task_is_done(backend, operation):
    assert getattr(helper, operation)(HOME) is True
    assert [op for op, _, _ in backend.calls] == ["query"]


@pytest.mark.parametrize("operation", ["remove_task", "retire_task"])
@pytest.mark.parametrize("stage", ["query", "mutation", "receipt"])
def test_removal_never_raises_or_claims_an_unconfirmed_effect(backend, monkeypatch, operation, stage):
    install(backend)
    calls = []

    def fail(op, sid, **kwargs):
        calls.append(op)
        if stage == "query" or op != "query":
            if stage == "receipt":
                return {"exists": True}
            raise OSError("synthetic scheduler error")
        return backend.scheduler(op, sid, **kwargs)

    monkeypatch.setattr(service, "_scheduler", fail)
    assert getattr(helper, operation)(HOME) is False
    assert calls.count("remove" if operation == "remove_task" else "retire") <= 1