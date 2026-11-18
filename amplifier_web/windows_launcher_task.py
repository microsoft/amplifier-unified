"""Owned, temporary Task Scheduler entry for launcher refresh recovery."""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
from pathlib import PureWindowsPath
import re
import subprocess
import xml.etree.ElementTree as ET

from . import windows_service as service

SOURCE = "amplifier-unified launcher-refresh v1"


class NotOurs(RuntimeError):
    """The named helper task did not pass the full ownership check."""


def task_name(sid) -> str:
    return "AmplifierUnifiedLauncher-" + service._sid(sid)


def _encoded(value, *, urlsafe=False):
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    return encode(raw).decode("ascii")


@dataclass(frozen=True)
class HelperSpec:
    sid: str
    data_dir: str
    registration: str
    python: str
    request: str
    registered: str

    def __post_init__(self):
        service._sid(self.sid)
        service._path(self.data_dir)
        service._path(self.python)
        # Command and WorkingDirectory are expanded by Task Scheduler.
        if "%" in self.python:
            raise ValueError("The helper Python path must not contain percent characters.")
        if PureWindowsPath(self.python).name.lower() != "pythonw.exe":
            raise ValueError("The helper requires a windowless Python interpreter.")
        for identity in (self.registration, self.request):
            if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity):
                raise ValueError("Invalid helper request or registration identity.")
        if (not isinstance(self.registered, str)
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", self.registered)):
            raise ValueError("The helper registration time must be local time to the second.")
        datetime.fromisoformat(self.registered)


def helper_xml(spec) -> str:
    root = ET.Element(service._q("Task"), version="1.2")

    def child(parent, name, text=None, **attributes):
        node = ET.SubElement(parent, service._q(name), attributes)
        node.text = text
        return node

    info = child(root, "RegistrationInfo")
    child(info, "Author", spec.sid)
    child(info, "Description", SOURCE + ":" + _encoded(asdict(spec)))
    child(info, "URI", "\\" + task_name(spec.sid))
    child(info, "Source", SOURCE)
    triggers = child(root, "Triggers")
    logon = child(triggers, "LogonTrigger")
    child(logon, "Enabled", "true")
    child(logon, "UserId", spec.sid)
    registered = datetime.fromisoformat(spec.registered)
    timed = child(triggers, "TimeTrigger")
    repeat = child(timed, "Repetition")
    child(repeat, "Interval", "PT5M")
    child(repeat, "Duration", "P1D")
    child(repeat, "StopAtDurationEnd", "false")
    child(timed, "StartBoundary", (registered + timedelta(minutes=5)).isoformat())
    child(timed, "Enabled", "true")
    daily = child(triggers, "CalendarTrigger")
    child(daily, "StartBoundary", (registered + timedelta(days=1)).isoformat())
    child(daily, "Enabled", "true")
    child(child(daily, "ScheduleByDay"), "DaysInterval", "1")
    principal = child(child(root, "Principals"), "Principal", id="Owner")
    child(principal, "UserId", spec.sid)
    child(principal, "LogonType", "InteractiveToken")
    child(principal, "RunLevel", "LeastPrivilege")
    settings = child(root, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false", "StopIfGoingOnBatteries": "false",
        "AllowHardTerminate": "true", "StartWhenAvailable": "true",
        "RunOnlyIfNetworkAvailable": "false", "AllowStartOnDemand": "true",
        "Enabled": "true", "Hidden": "false", "RunOnlyIfIdle": "false",
        "WakeToRun": "false", "ExecutionTimeLimit": "PT1H", "Priority": "7",
    }.items():
        child(settings, key, value)
    idle = child(settings, "IdleSettings")
    child(idle, "StopOnIdleEnd", "false")
    child(idle, "RestartOnIdle", "false")
    action = child(child(root, "Actions", Context="Owner"), "Exec")
    child(action, "Command", spec.python)
    payload = _encoded({"home": spec.data_dir, "request": spec.request}, urlsafe=True)
    arguments = subprocess.list2cmdline([
        "-I", "-m", "amplifier_web.windows_launcher_refresh", "--automatic", payload])
    if len(arguments.encode("utf-16-le")) // 2 > 30000:
        raise ValueError("Helper action exceeds the Windows command-line budget.")
    child(action, "Arguments", arguments)
    child(action, "WorkingDirectory", str(PureWindowsPath(spec.python).parent))
    xml = ET.tostring(root, encoding="unicode")
    if len(xml.encode("utf-8")) > service.LIMIT:
        raise ValueError("Helper definition is too large.")
    return xml


def _defaults(root, sid):
    service._scheduler_defaults(root, sid)
    # TriggerType.Enabled defaults to true, also for time and calendar triggers.
    # RepetitionPattern.StopAtDurationEnd defaults to false.
    for path, name, value in (
        ("Triggers/TimeTrigger", "Enabled", "true"),
        ("Triggers/CalendarTrigger", "Enabled", "true"),
        ("Triggers/TimeTrigger/Repetition", "StopAtDurationEnd", "false"),
    ):
        for parent in root.findall("/".join(service._q(part) for part in path.split("/"))):
            if parent.find(service._q(name)) is None:
                ET.SubElement(parent, service._q(name)).text = value


def owned_helper(xml, sid, data_dir) -> HelperSpec:
    try:
        if not isinstance(xml, str) or len(xml.encode("utf-8")) > service.LIMIT or "<!" in xml:
            raise ValueError("Invalid helper XML.")
        root = ET.fromstring(xml)
        info = root.find(service._q("RegistrationInfo"))
        description = root.findtext(
            f"{service._q('RegistrationInfo')}/{service._q('Description')}", "")
        if not description.startswith(SOURCE + ":"):
            raise ValueError("No helper ownership metadata.")
        spec = HelperSpec(**json.loads(base64.b64decode(
            description[len(SOURCE) + 1:], validate=True).decode("utf-8")))
        if (spec.sid != service._sid(sid)
                or PureWindowsPath(spec.data_dir) != PureWindowsPath(str(data_dir))):
            raise ValueError("The helper belongs to another owner or data folder.")
        updates = PureWindowsPath(spec.data_dir) / "updates"
        python = PureWindowsPath(spec.python)
        if updates not in python.parents:
            raise ValueError("The helper Python must be below this data folder's updates folder.")
        for date in list(info.findall(service._q("Date"))):
            info.remove(date)
        _defaults(root, spec.sid)
        if service._tree(root) != service._tree(ET.fromstring(helper_xml(spec))):
            raise ValueError("The helper definition does not match its ownership metadata.")
        return spec
    except (ValueError, TypeError, KeyError, OverflowError, ET.ParseError, service._LookupFailed) as error:
        raise NotOurs("Amplifier could not confirm that the launcher helper task belongs to this installation.") from error


def query(home) -> tuple[dict, HelperSpec | None]:
    sid = service._owner()
    value = service._scheduler("query", sid, name=task_name(sid))
    return value, owned_helper(value.get("xml"), sid, home) if value["exists"] else None


def ensure(home, request_id, registration, python) -> None:
    value, previous = query(home)
    if (previous is not None and previous.request == request_id
            and previous.registration == registration and previous.python == str(python)):
        return
    if previous is not None and value["instances"] != 0:
        raise service.WindowsServiceError("The launcher helper is still running. Its task was not replaced.")
    sid = previous.sid if previous is not None else service._owner()
    spec = HelperSpec(sid, str(home), registration, str(python), request_id,
                      datetime.now().isoformat(timespec="seconds"))
    xml = helper_xml(spec)
    owned_helper(xml, sid, home)  # Refuse an out-of-folder Python before creating a task.
    service._scheduler("replace" if previous is not None else "create", sid,
                       previous=value.get("xml"), xml=xml, name=task_name(sid))
    _, installed = query(home)
    if installed != spec:
        raise service.WindowsServiceError("The launcher helper registration was not verified. Read its task before retrying.")


def start(home) -> None:
    value, spec = query(home)
    if spec is None:
        raise service.WindowsServiceError("The launcher helper task is not installed.")
    service._scheduler("start", spec.sid, previous=value["xml"], name=task_name(spec.sid))


def running(home) -> bool:
    value, spec = query(home)
    return spec is not None and value["instances"] != 0


def _remove(home, operation):
    try:
        value, spec = query(home)
        if spec is None:
            return True
        if operation == "remove" and value["instances"] != 0:
            return False
        result = service._scheduler(operation, spec.sid, previous=value["xml"], name=task_name(spec.sid))
        return result["exists"] is False
    except Exception:
        return False


def remove_task(home) -> bool:
    return _remove(home, "remove")


def retire_task(home) -> bool:
    return _remove(home, "retire")