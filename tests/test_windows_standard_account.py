"""The standard-account case: its guard, its account test, its lines and its workflow.

The case itself runs only on a GitHub-hosted Windows runner
(`.github/workflows/windows-standard-account.yml`). These checks need no
Windows: the second job of that workflow runs them on Linux with Python 3.13,
and no other workflow names this file. They keep the parts that decide what
the case may claim: who counts as a standard user, which step a failure
belongs to, and what the workflow can never do silently.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import io
import re
import string
import sys
import time
import tokenize
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/windows_standard_account_native.py"
WORKFLOW = ROOT / ".github/workflows/windows-standard-account.yml"
DESIGN = (ROOT / "docs/design/native-windows-installation.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def case():
    spec = importlib.util.spec_from_file_location("standard_account_case", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HOSTED = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
          "AMPLIFIER_CREATE_A_STANDARD_USER_ON_THIS_RUNNER": "1"}


SUPPORTED = ((3, 13), True)  # 64-bit CPython 3.13, on any interpreter that runs these checks.


def test_the_fixture_runs_on_a_hosted_runner_with_its_opt_in(case):
    assert case.require_disposable_runner(dict(HOSTED), "win32", SUPPORTED) is None


@pytest.mark.parametrize("interpreter", [((3, 12), True), ((3, 14), True), ((3, 13), False)])
def test_the_fixture_refuses_another_interpreter(case, interpreter):
    with pytest.raises(SystemExit) as refused:
        case.require_disposable_runner(dict(HOSTED), "win32", interpreter)
    assert "64-bit CPython 3.13" in str(refused.value)


@pytest.mark.parametrize("change", [
    {"GITHUB_ACTIONS": None}, {"RUNNER_ENVIRONMENT": "self-hosted"}, {"RUNNER_ENVIRONMENT": None},
    {"AMPLIFIER_CREATE_A_STANDARD_USER_ON_THIS_RUNNER": None},
    {"AMPLIFIER_CREATE_A_STANDARD_USER_ON_THIS_RUNNER": "true"}, {"platform": "linux"}])
def test_the_fixture_refuses_to_create_a_user_anywhere_else(case, change):
    environment = {key: value for key, value in {**HOSTED, **change}.items()
                   if value is not None and key != "platform"}
    with pytest.raises(SystemExit) as refused:
        case.require_disposable_runner(environment, change.get("platform", "win32"), SUPPORTED)
    assert "creates a Windows user" in str(refused.value)


ADMINISTRATORS = "S-1-5-32-544"
RECORD = {"sid": "S-1-5-21-1-2-3-1003", "runnerSession": 2, "hosted": HOSTED}
USER = {"sid": "S-1-5-21-1-2-3-1003", "memberOfAdministrators": False}


def admit(case, record=RECORD, token=USER, owner=ADMINISTRATORS, changeable=False):
    return case.admit_standard(record, token, owner, changeable, "win32", SUPPORTED)


def test_the_standard_user_of_a_hosted_run_is_admitted(case):
    assert admit(case) is None


@pytest.mark.parametrize("change, text", [
    # Another account than the one that the run created, or an administrator.
    ({"token": {**USER, "sid": "S-1-5-21-1-2-3-1004"}}, "only as the standard user"),
    ({"token": {**USER, "memberOfAdministrators": True}}, "only as the standard user"),
    ({"record": {"hosted": HOSTED}}, "only as the standard user"),
    ({"record": [RECORD]}, "only as the standard user"),  # Not a record at all.
    # A record that the user made, that another account made, or that the user can change.
    ({"owner": USER["sid"]}, "administrators group"),
    ({"owner": "S-1-5-21-1-2-3-500"}, "administrators group"),
    ({"changeable": True}, "administrators group"),
    # A record without the facts of the hosted workflow.
    ({"record": {**RECORD, "hosted": {}}}, "creates a Windows user"),
    ({"record": {"sid": RECORD["sid"]}}, "creates a Windows user"),
    ({"record": {**RECORD, "hosted": {**HOSTED, "RUNNER_ENVIRONMENT": "self-hosted"}}}, "creates a Windows user"),
])
def test_every_other_program_is_refused_before_any_phase(case, change, text):
    with pytest.raises(SystemExit) as refused:
        admit(case, **change)
    assert text in str(refused.value)


def test_every_operation_passes_an_admission_check_before_it_does_anything():
    source = FIXTURE.read_text(encoding="utf-8")
    entry = source[source.index('if __name__ == "__main__":'):]
    assert entry.index('if args.operation == "standard":') < entry.index("require_disposable_runner()") \
        < entry.index("globals()[args.operation]()")
    standard = source[source.index("def standard(stage, exchange, phase):"):source.index('if __name__ == "__main__":')]
    # The record is in the stage folder, which the user cannot change. It is never in the exchange folder.
    assert 'record = stage / "account.json"' in standard and 'exchange / "account.json"' not in source
    # One open file gives the record's text, its owner and its permissions. The folder is read too.
    assert "content, owner, changeable = read_protected(record)" in standard
    assert 'account = json.loads(content.decode("utf-8"))' in standard
    assert ("admit_standard(account, token, owner, changeable or read_protected(record.parent, folder=True)[2])"
            ) in standard
    before = standard[:standard.index("admit_standard(")]
    assert "read_text" not in before and "open(" not in before
    assert standard.index("admit_standard(") < standard.index(".started") < standard.index('"second": second')
    admit_source = source[source.index("def admit_standard("):source.index("def load(name, path):")]
    assert 'require_disposable_runner(account.get("hosted") or {}, platform, interpreter)' in admit_source


def test_the_account_record_is_written_where_the_user_cannot_change_it():
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("PROFILE = ")]
    # The stage takes nothing from the folder above: administrators and Windows change it, the user reads it.
    assert ('"/inheritance:r", "/grant:r",\n'
            '                             f"*{ADMINISTRATORS}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", f"*{sid}:(OI)(CI)RX", "/Q"])'
            ) in run
    assert run.index('"/inheritance:r"') < run.index('record = stage / "account.json"') \
        < run.index('need(owner == ADMINISTRATORS') < run.index("sign_in(name, password)")
    assert '"hosted": {fact: os.environ.get(fact) for fact in HOSTED_FACTS}' in run
    assert source.count("HOSTED_FACTS = (\"GITHUB_ACTIONS\", \"RUNNER_ENVIRONMENT\", OPT_IN)") == 1


def test_the_records_text_owner_and_permissions_come_from_one_open_file(case):
    source = FIXTURE.read_text(encoding="utf-8")
    reader = source[source.index("def read_protected(path, folder=False):"):source.index("class _UserInfo1(C.Structure):")]
    # One open. While it lasts, other programs may only read the file (share: 1).
    assert reader.count("CreateFileW(") == 1 and "0x20080 if folder else 0x80000000, 7 if folder else 1, None, 3," in reader
    # The same handle gives the bytes, the owner and the permissions.
    assert reader.index("CreateFileW(") < reader.index("_kernel.ReadFile(handle, ") \
        < reader.index("_adv.GetSecurityInfo(handle, 1, 7, ") < reader.index("_granted(descriptor)") \
        < reader.index("_kernel.CloseHandle(handle)")
    assert "return data, owner, bool(granted & CHANGE)" in reader
    # Windows computes what this program's token may do. A call that fails is an error, never an answer.
    granted = source[source.index("def _granted(descriptor):"):source.index("def read_protected(")]
    assert "_adv.AccessCheck(descriptor, copy, 0x02000000, " in granted
    assert "except" not in granted and "except" not in reader
    # Write or add data, write attributes, delete an entry, delete, change permissions, take over.
    assert case.CHANGE == 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000
    # No second read by a path is left.
    for gone in ("def can_write", "def file_owner", "GetNamedSecurityInfoW"):
        assert gone not in source, gone


def test_the_case_shows_on_windows_that_the_read_tells_the_users_file_from_the_record():
    source = FIXTURE.read_text(encoding="utf-8")
    first = source[source.index("def first(stage, exchange, account, token):"):source.index("def product(stage, root):")]
    # As the standard user: its own file is its own and changeable; the record and the stage folder are not.
    assert 'mine, theirs = read_protected(own), read_protected(stage / "account.json")' in first
    assert ('need(mine == (b"{}", token["sid"], True) and theirs[1:] == (ADMINISTRATORS, False)\n'
            '             and folders_read == (True, False),') in first
    assert first.index("read_protected(own)") < first.index("steps.passed(FIRST[0]")


ACCOUNT = {"sid": "S-1-5-21-1-2-3-1003", "runnerSession": 2}
STANDARD = {"sid": "S-1-5-21-1-2-3-1003", "memberOfAdministrators": False, "elevationType": 1,
            "elevated": False, "integrityLevel": "0x2000", "logonType": 10, "desktopSession": 1}


def test_a_standard_user_in_its_own_desktop_session_is_accepted(case):
    assert case.standard_token(STANDARD, ACCOUNT)
    assert case.standard_token({**STANDARD, "logonType": 2}, ACCOUNT)  # A sign-in at the screen.


@pytest.mark.parametrize("change", [
    {"sid": "S-1-5-21-1-2-3-500"},        # Another account, here the runner's own.
    {"memberOfAdministrators": True},     # An administrator.
    {"elevationType": 3},                 # A limited token: an administrator token exists beside it.
    {"elevationType": 2},                 # The administrator token itself.
    {"elevated": True},
    {"integrityLevel": "0x3000"},         # High integrity.
    {"logonType": 3}, {"logonType": 4}, {"logonType": 5},  # Network, batch, service: no sign-in of a person.
    {"logonType": None},
    {"desktopSession": 0},                # The session of services.
    {"desktopSession": 2},                # The desktop session of the runner's account.
])
def test_every_other_token_is_not_the_standard_user_of_the_case(case, change):
    token = {key: value for key, value in {**STANDARD, **change}.items() if value is not None}
    assert not case.standard_token(token, ACCOUNT)


SESSIONS = """ SESSIONNAME               USERNAME                 ID  STATE   TYPE        DEVICE
 services                                            0  Disc
 rdp-tcp#0                 amp-std-668419            1  Active
>console                   runneradmin               2  Active
 31c5ce94259d4006a9e4                            65536  Listen
 rdp-tcp                                         65537  Listen
"""


def test_the_users_desktop_session_is_read_from_the_list_of_windows(case):
    # The list of a hosted runner while the user was signed in (the probe run).
    assert case.find_session(SESSIONS, "amp-std-668419") == (1, "Active")
    assert case.find_session(SESSIONS, "AMP-STD-668419") == (1, "Active")
    assert case.find_session(SESSIONS, "runneradmin") == (2, "Active")  # The line of the caller has ">".
    # A signed-out user has no line. A name that is part of another name is not that user.
    assert case.find_session(SESSIONS, "amp-std-000000") is None
    assert case.find_session(SESSIONS, "amp-std") is None
    # A session name is never taken for a user, also when a number follows it.
    for session in ("rdp-tcp", "services", "console", "rdp-tcp#0", "31c5ce94259d4006a9e4"):
        assert case.find_session(SESSIONS, session) is None, session
    assert case.find_session("no list", "amp-std-668419") is None
    assert case.find_session(SESSIONS.replace("1  Active", "1  Disc  "), "amp-std-668419") == (1, "Disc")


@pytest.mark.parametrize("listing", ["", "ERROR: no session list\r\n", " SESSIONNAME   ID\r\n"])
def test_a_session_list_that_windows_does_not_give_is_never_no_session(case, monkeypatch, listing):
    asked = []
    monkeypatch.setattr(case, "system", lambda name: name)
    monkeypatch.setattr(case.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(case, "execute",
                        lambda argv, **_: asked.append(argv) or {"exitCode": 1, "stdout": listing, "stderr": ""})
    with pytest.raises(AssertionError) as failed:
        case.session_of("amp-std-668419")
    assert "Windows did not list the desktop sessions" in str(failed.value)
    assert asked == [["qwinsta.exe"]] * 3  # Windows is asked again before it is an error.
    # A list at the second try is the answer.
    answers = [listing, SESSIONS]
    monkeypatch.setattr(case, "execute", lambda argv, **_: {"exitCode": 0, "stdout": answers.pop(0), "stderr": ""})
    assert case.session_of("amp-std-668419") == (1, "Active") and answers == []
    # With a list: the user's session, or none.
    monkeypatch.setattr(case, "execute", lambda argv, **_: {"exitCode": 0, "stdout": SESSIONS, "stderr": ""})
    assert case.session_of("amp-std-668419") == (1, "Active") and case.session_of("amp-std-000000") is None


def test_a_failed_step_keeps_its_exact_text_and_later_steps_are_not_reached(case, tmp_path):
    names = ("one", "two", "three")
    steps = case.Steps(tmp_path / "report.json", names)
    steps.begin("one")
    steps.passed("one", fact=1)
    steps.begin("two")
    steps.failed(RuntimeError("Task Scheduler refused the operation."))
    assert steps.finish() is False
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["finished"] is True
    assert report["steps"] == [
        {"name": "one", "status": "PASS", "fact": 1},
        {"name": "two", "status": "FAIL", "reason": "RuntimeError: Task Scheduler refused the operation."},
        {"name": "three", "status": "FAIL", "reason": "Not reached"}]
    # The lines of the result: passed, failed with the text, not reached, and what is not shown.
    lines = case.lines(report)
    assert lines[0] == "PASSED as a standard user: one"
    assert lines[1] == "FAILED: two -- RuntimeError: Task Scheduler refused the operation."
    assert lines[2] == "FAILED: three -- Not reached"
    assert [line for line in lines[3:] if not line.startswith("NOT SHOWN: ")] == []
    assert len(lines) == 3 + len(case.NOT_SHOWN)


def refused(times, error, tries, limit=50):
    """A stand-in for the replacement: it fails `times` times with `error`, then it replaces.

    A try after `limit` tries fails the test.
    """
    def replace(source, target):
        tries.append((source, target))
        assert len(tries) <= limit, "The replacement is tried without end"
        if len(tries) <= times:
            raise error
        Path(source).replace(target)
    return replace


def test_a_report_that_windows_does_not_let_replace_for_a_moment_is_replaced_at_a_later_try(case, tmp_path):
    partial, report = tmp_path / "second.partial", tmp_path / "second.json"
    partial.write_text("the new report")
    report.write_text("the report before")
    tries, waits = [], []
    denied = PermissionError(13, "Access is denied")
    case.replace_report(partial, report, replace=refused(2, denied, tries), wait=waits.append,
                        timer=lambda: float(len(waits)))
    assert tries == [(partial, report)] * 3 and waits == [0.05, 0.05]
    assert report.read_text() == "the new report" and not partial.exists()
    # Without a refusal: one try and no wait.
    partial.write_text("the next report")
    tries.clear(), waits.clear()
    case.replace_report(partial, report, replace=refused(0, denied, tries), wait=waits.append)
    assert len(tries) == 1 and waits == [] and report.read_text() == "the next report"
    # The clock is read after a refusal only. Here it passes the limit of 3 seconds during the pause: the try
    # after the pause is made, and it replaces the report.
    partial.write_text("the last report")
    tries.clear(), waits.clear()
    case.replace_report(partial, report, replace=refused(1, denied, tries), wait=waits.append,
                        timer=lambda: 100.0 * len(waits), limit=3)
    assert len(tries) == 2 and waits == [0.05] and report.read_text() == "the last report"


def test_a_report_that_windows_never_lets_replace_fails_with_that_error_after_the_limit(case, tmp_path):
    partial, report = tmp_path / "second.partial", tmp_path / "second.json"
    partial.write_text("the new report")
    report.write_text("the report before")
    tries, waits = [], []
    denied = PermissionError(13, "Access is denied")
    # The stand-in for the timer goes on by one second with each wait. The limit here is 3 seconds.
    with pytest.raises(PermissionError) as failed:
        case.replace_report(partial, report, replace=refused(1000, denied, tries), wait=waits.append,
                            timer=lambda: float(len(waits)), limit=3)
    assert failed.value is denied and len(tries) == 4 and waits == [0.05] * 3
    assert report.read_text() == "the report before" and partial.read_text() == "the new report"
    # An error that is not this refusal goes on at once: no second try, no wait.
    tries.clear(), waits.clear()
    full = OSError(28, "There is not enough space on the disk")
    with pytest.raises(OSError) as failed:
        case.replace_report(partial, report, replace=refused(1000, full, tries), wait=waits.append,
                            timer=lambda: float(len(waits)), limit=3)
    assert failed.value is full and len(tries) == 1 and waits == []


def test_every_save_of_the_report_replaces_the_file_through_those_tries(case, tmp_path, monkeypatch):
    # As the case uses it: the system's own replacement, the real clock, 5 seconds.
    assert case.replace_report.__kwdefaults__ == {
        "replace": case.os.replace, "wait": case.time.sleep, "timer": case.time.monotonic, "limit": 5.0}
    calls = []

    def replace(partial, path):
        calls.append((partial, path))
        Path(partial).replace(path)
    monkeypatch.setattr(case, "replace_report", replace)
    steps = case.Steps(tmp_path / "second.json", ("one",))
    steps.begin("one")
    assert calls == [(tmp_path / "second.partial", tmp_path / "second.json")] * 2
    assert json.loads((tmp_path / "second.json").read_text())["steps"][0]["reason"] == "In progress"


def test_a_step_passes_only_when_it_was_stated(case, tmp_path):
    steps = case.Steps(tmp_path / "report.json", ("one", "two"))
    steps.begin("one")
    steps.passed("one")
    assert steps.finish() is False  # "two" was never reached.
    assert json.loads((tmp_path / "report.json").read_text())["steps"][1]["reason"] == "Not reached"


# What the launcher said in run 44149453625 (November 17, 2026), where the start after the uninstall failed.
SAID = {"background": True, "created": True,
        "health": {"ok": True, "service": "running", "responding": True, "probeAttempted": True,
                   "processVerified": True, "pid": 4500, "created": "134394144230336231",
                   "url": "https://127.0.0.1:27595"},
        "runtime": {"ok": True, "prepared": True, "generation": None, "connectionRequired": True}}
STARTED = {"exitCode": 0, "json": SAID, "stdout": json.dumps(SAID), "stderr": "", "seconds": 9.9}
TASK = {"managed": True, "state": "running", "processVerified": False, "healthy": False,
        "taskPath": "\\AmplifierUnified-S-1-5-21-1-2-3-1003", "lastResult": 267009, "instances": 1}
STATUS = {"exitCode": 0, "json": dict(SAID["health"]), "stdout": "", "stderr": "", "seconds": 1.2}


@pytest.fixture(scope="module")
def start_rules():
    """The two rules of the maintained start-again case. The standard-account case uses them unchanged."""
    spec = importlib.util.spec_from_file_location(
        "start_again_rules", ROOT / "tests/fixtures/windows_start_again_native.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Reads:
    """The start-again case for one start: its two rules, and fixed answers for the reads five seconds later."""

    def __init__(self, rules, task=None, status=None, fails=None):
        self.running, self.healthy = rules.running, rules.healthy
        self.task, self.status, self.fails, self.calls = task or TASK, status or STATUS, fails, []

    def wait(self, seconds):
        self.calls.append(("wait", seconds))

    def task_facts(self, home):
        self.calls.append(("task", home))
        return self.task

    def cli(self, root, *args):
        self.calls.append(("cli", root, *args))
        if self.fails:
            raise self.fails
        return self.status


def accept(case, tmp_path, reads, started=STARTED, task=TASK, status=STATUS, text="The start failed",
           save_fails_at=None):
    """One call of the check: its text (None when the start is accepted), the saved record, and the steps."""
    steps = case.Steps(tmp_path / "report.json", ("one",))
    steps.begin("one")
    if save_fails_at:
        real, calls = steps.save, []

        def save():
            calls.append(1)
            if len(calls) == save_fails_at:
                raise OSError("There is not enough space on the disk")
            real()
        steps.save = save
    try:
        case.accept_start(steps, "theStart", reads, tmp_path / "app", tmp_path, started, task, status,
                          text, wait=reads.wait)
        said = None
    except AssertionError as error:
        said = str(error)
    return said, json.loads((tmp_path / "report.json").read_text(encoding="utf-8")).get("theStart"), steps


def detail_of(text):
    head, _, detail = text.partition(": {")
    return head, json.loads("{" + detail)


def test_a_start_with_the_three_required_answers_is_accepted_and_its_answers_are_saved(case, start_rules, tmp_path):
    reads = Reads(start_rules)
    text, saved, _ = accept(case, tmp_path, reads)
    assert text is None
    assert saved == {"launcher": STARTED, "task": TASK, "serviceStatus": STATUS}
    assert reads.calls == []  # No wait and no second read.


# What the rule of the status reads, in a status that is as required.
GOOD = {"exitCode": 0, "ok": True, "processVerified": True, "responding": True}
ASKED = dict(STATUS["json"])
WRONG = [
    ("the launcher", {"started": {**STARTED, "exitCode": 1}}, {"exitCode": 1, "background": True}),
    ("the launcher", {"started": {**STARTED, "json": {**SAID, "background": False}}},
     {"exitCode": 0, "background": False}),
    ("the launcher", {"started": {**STARTED, "json": None}}, {"exitCode": 0, "json": None}),
    ("Task Scheduler", {"task": {**TASK, "managed": False}}, {"managed": False, "state": "running", "instances": 1}),
    ("Task Scheduler", {"task": {**TASK, "state": "stopped"}}, {"managed": True, "state": "stopped", "instances": 1}),
    ("Task Scheduler", {"task": {**TASK, "instances": 0}}, {"managed": True, "state": "running", "instances": 0}),
    ("Task Scheduler", {"task": {**TASK, "instances": 2}}, {"managed": True, "state": "running", "instances": 2}),
    ("the app's status", {"status": {**STATUS, "exitCode": 1}}, {**GOOD, "exitCode": 1}),
    ("the app's status", {"status": {**STATUS, "json": {**ASKED, "ok": False}}}, {**GOOD, "ok": False}),
    ("the app's status", {"status": {**STATUS, "json": {**ASKED, "processVerified": False}}},
     {**GOOD, "processVerified": False}),
    ("the app's status", {"status": {**STATUS, "json": {**ASKED, "responding": False}}},
     {**GOOD, "responding": False}),
    ("the app's status", {"status": {**STATUS, "json": None}}, {"exitCode": 0, "json": None}),
]


@pytest.mark.parametrize("part, answers, values", WRONG)
def test_a_start_names_the_one_answer_that_was_not_as_required_and_the_values_that_its_rule_reads(
        case, start_rules, tmp_path, part, answers, values):
    text, saved, _ = accept(case, tmp_path, Reads(start_rules), **answers)
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: " + part
    assert list(detail) == [part, "fiveSecondsLater", "more"]  # In this order: the values come first.
    assert detail[part] == values
    # Then more of that answer: Task Scheduler's whole answer; of a command its JSON and the end of what it printed.
    given = {"started": STARTED, "task": TASK, "status": STATUS, **answers}
    whole = {"the launcher": given["started"], "Task Scheduler": given["task"], "the app's status": given["status"]}[part]
    assert detail["more"] == {part: whole if part == "Task Scheduler" else {
        "json": whole["json"], **({} if whole["json"] else {"stdout": whole["stdout"][-300:]}), "stderr": ""}}
    # The report holds the three answers as the case got them.
    assert {name: saved[name] for name in ("launcher", "task", "serviceStatus")} == {
        "launcher": given["started"], "task": given["task"], "serviceStatus": given["status"]}


def test_a_start_names_every_answer_that_was_not_as_required_in_one_text(case, start_rules, tmp_path):
    text, _, _ = accept(case, tmp_path, Reads(start_rules), started={**STARTED, "exitCode": 1},
                        task={**TASK, "instances": 0}, status={**STATUS, "exitCode": 1})
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: the launcher, Task Scheduler, the app's status"
    assert list(detail) == ["the launcher", "Task Scheduler", "the app's status", "fiveSecondsLater", "more"]
    assert list(detail["more"]) == ["the launcher", "Task Scheduler", "the app's status"]
    # The failure of November 17: the launcher said that the start worked. The text now says which read did not.
    text, _, _ = accept(case, tmp_path, Reads(start_rules), task={**TASK, "state": "stopped", "instances": 0})
    assert text.startswith('The start failed. Not as required: Task Scheduler: '
                           '{"Task Scheduler": {"managed": true, "state": "stopped", "instances": 0}, ')


@pytest.mark.parametrize("part, answers, values", [
    ("the launcher", {"started": {**STARTED, "json": [1, 2]}}, {"exitCode": 0, "json": "[1, 2]"}),
    ("the launcher", {"started": {**STARTED, "json": "started"}}, {"exitCode": 0, "json": "started"}),
    ("the app's status", {"status": {**STATUS, "json": [1, 2]}}, {"exitCode": 0, "json": "[1, 2]"}),
    ("Task Scheduler", {"task": {**TASK, "instances": "1"}}, {"managed": True, "state": "running", "instances": "1"}),
    ("Task Scheduler", {"task": {**TASK, "state": "s" * 100}},
     {"managed": True, "state": "'" + "s" * 59, "instances": 1}),
])
def test_an_answer_that_its_rule_cannot_read_or_that_is_long_is_not_as_required_and_its_values_are_short(
        case, start_rules, tmp_path, part, answers, values):
    # A rule that raises does not accept the start, and its error does not replace the step's text.
    text, _, _ = accept(case, tmp_path, Reads(start_rules), **answers)
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: " + part
    assert detail[part] == values


@pytest.mark.parametrize("later_task, later_status, expected", [
    (TASK, STATUS, {"Task Scheduler": True, "the app's status": True}),
    ({**TASK, "instances": 0}, STATUS, {"Task Scheduler": False, "the app's status": True}),
    (TASK, {**STATUS, "exitCode": 1}, {"Task Scheduler": True, "the app's status": False}),
])
def test_the_reads_five_seconds_later_are_saved_and_never_make_the_step_pass(
        case, start_rules, tmp_path, later_task, later_status, expected):
    reads = Reads(start_rules, task=later_task, status=later_status)
    text, saved, _ = accept(case, tmp_path, reads, task={**TASK, "state": "stopped"})
    # Also when both later answers are as required, the step fails with the first answer.
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: Task Scheduler"
    assert detail["Task Scheduler"] == {"managed": True, "state": "stopped", "instances": 1}
    assert detail["fiveSecondsLater"] == expected
    assert saved["fiveSecondsLater"] == {"task": later_task, "serviceStatus": later_status}
    # One wait of five seconds, then the same two reads as before.
    assert reads.calls == [("wait", 5), ("task", tmp_path / "app"), ("cli", tmp_path, "service", "status", "--json")]


def test_a_later_read_that_fails_keeps_the_first_answers_as_the_steps_text(case, start_rules, tmp_path):
    reads = Reads(start_rules, fails=RuntimeError("no answer " + "x" * 400 + " the end"))
    text, saved, _ = accept(case, tmp_path, reads, status={**STATUS, "exitCode": 1, "stderr": "x" * 500 + "the end"})
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: the app's status"
    assert detail["the app's status"] == {**GOOD, "exitCode": 1}
    assert detail["more"]["the app's status"]["stderr"] == "x" * 293 + "the end"  # The end of a long text.
    error = {"errorType": "RuntimeError", "error": "x" * 292 + " the end"}  # Its last 300 characters.
    assert detail["fiveSecondsLater"] == error and saved["fiveSecondsLater"] == error


def test_a_later_answer_that_its_rule_cannot_read_is_saved_and_the_text_holds_the_error(case, start_rules, tmp_path):
    later = {**STATUS, "json": [1, 2]}
    text, saved, _ = accept(case, tmp_path, Reads(start_rules, status=later), task={**TASK, "instances": 0})
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: Task Scheduler"
    assert detail["fiveSecondsLater"]["errorType"] == "AttributeError"
    assert saved["fiveSecondsLater"] == {"task": TASK, "serviceStatus": later}


def test_a_start_that_is_not_accepted_saves_the_end_of_the_hosts_log(case, start_rules, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(case, "host_output", lambda home: asked.append(home) or "line 99\nline 100")
    text, saved, _ = accept(case, tmp_path, Reads(start_rules), task={**TASK, "instances": 0})
    assert saved["taskOutput"] == "line 99\nline 100" and asked == [tmp_path / "app"]
    assert "line 100" not in text  # The log is in the report, not in the step's text.
    # A start that is accepted reads no log.
    asked.clear()
    assert accept(case, tmp_path, Reads(start_rules))[0] is None and asked == []


def test_a_hosts_log_that_cannot_be_read_does_not_replace_the_steps_text(case, start_rules, tmp_path, monkeypatch):
    def refused(home):
        raise PermissionError("The process cannot access the file")
    monkeypatch.setattr(case, "host_output", refused)
    text, saved, _ = accept(case, tmp_path, Reads(start_rules), task={**TASK, "instances": 0})
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: Task Scheduler"
    assert detail["Task Scheduler"] == {"managed": True, "state": "running", "instances": 0}
    assert detail["fiveSecondsLater"] == {"Task Scheduler": True, "the app's status": True}
    # The report says that the log was not read, and it still holds the later answers.
    assert saved["taskOutput"] == {"errorType": "PermissionError", "error": "The process cannot access the file"}
    assert saved["fiveSecondsLater"] == {"task": TASK, "serviceStatus": STATUS}


def test_a_report_that_cannot_be_saved_the_second_time_does_not_replace_the_steps_text(case, start_rules, tmp_path):
    wrong = {**TASK, "instances": 0}
    text, saved, steps = accept(case, tmp_path, Reads(start_rules), task=wrong, save_fails_at=2)
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: Task Scheduler"
    assert list(detail) == ["Task Scheduler", "fiveSecondsLater", "notSavedInTheReport", "more"]
    assert detail["Task Scheduler"] == {"managed": True, "state": "running", "instances": 0}
    assert detail["notSavedInTheReport"] == {"errorType": "OSError", "error": "There is not enough space on the disk"}
    # The first save holds the three answers. The later answers wait in the report for its next save.
    assert saved == {"launcher": STARTED, "task": wrong, "serviceStatus": STATUS}
    assert steps.report["theStart"]["fiveSecondsLater"] == {"task": TASK, "serviceStatus": STATUS}
    # A second save that works adds nothing to the text.
    text, _, _ = accept(case, tmp_path, Reads(start_rules), task=wrong)
    assert "notSavedInTheReport" not in text


def test_a_report_that_cannot_be_saved_before_the_check_fails_no_start_and_replaces_no_text(case, start_rules, tmp_path):
    no_space = {"errorType": "OSError", "error": "There is not enough space on the disk"}
    # Three answers that are as required: the start is accepted. The report keeps the error for its next save.
    reads = Reads(start_rules)
    text, saved, steps = accept(case, tmp_path, reads, save_fails_at=1)
    assert text is None and reads.calls == [] and saved is None  # The file does not hold this start yet.
    assert steps.report["theStart"] == {"launcher": STARTED, "task": TASK, "serviceStatus": STATUS,
                                        "notSavedBeforeTheCheck": no_space}
    # An answer that is not as required: the step's text names it, and says that the first save failed.
    wrong = {**TASK, "instances": 0}
    text, saved, _ = accept(case, tmp_path, Reads(start_rules), task=wrong, save_fails_at=1)
    head, detail = detail_of(text)
    assert head == "The start failed. Not as required: Task Scheduler"
    assert list(detail) == ["Task Scheduler", "fiveSecondsLater", "notSavedBeforeTheCheck", "more"]
    assert detail["Task Scheduler"] == {"managed": True, "state": "running", "instances": 0}
    assert detail["notSavedBeforeTheCheck"] == no_space
    # The second save worked: the file holds the answers, the error of the first save and the later pair.
    assert saved["task"] == wrong and saved["notSavedBeforeTheCheck"] == no_space
    assert saved["fiveSecondsLater"] == {"task": TASK, "serviceStatus": STATUS}
    # A first save that works adds nothing.
    text, saved, _ = accept(case, tmp_path, Reads(start_rules), task=wrong)
    assert "notSavedBeforeTheCheck" not in text and "notSavedBeforeTheCheck" not in saved


def test_an_answer_that_json_cannot_write_does_not_replace_the_text_of_a_refused_start(case, start_rules, tmp_path):
    # A key that is a pair, in each of the three answers and in Task Scheduler's answer five seconds later. No
    # answer of the product has such a key. Neither the step's text nor the save of the report may depend on that.
    wrong, later = {**TASK, "instances": 0, ("a", "pair"): 1}, {**TASK, ("b", "pair"): 2}
    started, status = {**STARTED, ("c", "pair"): 3}, {**STATUS, ("d", "pair"): 4}
    text, saved, steps = accept(case, tmp_path, Reads(start_rules, task=later), started=started, task=wrong,
                                status=status)
    # The Python form of the same detail, in the same order: the names, the values that the rules read, the rest.
    assert text.startswith("The start failed. Not as required: Task Scheduler: {'Task Scheduler': "
                           "{'managed': True, 'state': 'running', 'instances': 0}, 'fiveSecondsLater': "
                           "{'Task Scheduler': True, \"the app's status\": True}, ")
    assert "('a', 'pair'): 1" in text and len(text) <= 2900
    # The report keeps the Python form of each such answer. Both saves worked: the text names no save that
    # failed, and the file holds the record.
    assert "notSaved" not in text
    assert saved == {"launcher": repr(started), "task": repr(wrong), "serviceStatus": repr(status),
                     "taskOutput": saved["taskOutput"],
                     "fiveSecondsLater": repr({"task": later, "serviceStatus": STATUS})}
    # So the save of the failed step works too, and the phase ends with the step's text in its report.
    steps.failed(AssertionError(text))
    assert steps.finish() is False
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["steps"][0]["reason"] == "AssertionError: " + text and report["finished"] is True


def test_the_report_keeps_an_answer_itself_or_its_python_form_when_json_cannot_write_it(case):
    answer = {"state": "running", "folder": Path("a")}  # The save of the report writes a path as its text.
    assert case.kept(answer) is answer and case.kept(None) is None and case.kept("text") == "text"
    assert case.kept({("a", "pair"): 1}) == "{('a', 'pair'): 1}"
    long = {(number,): "x" * 100 for number in range(100)}
    assert case.kept(long) == repr(long)[:4000] and len(case.kept(long)) == 4000


@pytest.mark.parametrize("said", ["The second start failed", "The start after the uninstall failed"])
def test_the_text_of_a_start_keeps_its_start_when_the_answers_are_long(case, start_rules, tmp_path, said):
    started = {**STARTED, "json": {**SAID, "background": False, "note": "n" * 5000}}
    task = {"managed": False, "errorType": "RuntimeError", "error": "e" * 1000}  # Task Scheduler gave no answer.
    status = {**STATUS, "json": {**ASKED, "ok": False, "message": "m" * 5000}}
    text, _, steps = accept(case, tmp_path, Reads(start_rules), started=started, task=task, status=status, text=said)
    values = json.dumps({"the launcher": {"exitCode": 0, "background": False},
                         "Task Scheduler": {"managed": False, "state": None, "instances": None},
                         "the app's status": {**GOOD, "ok": False},
                         "fiveSecondsLater": {"Task Scheduler": True, "the app's status": True}})[:-1]
    start = (said + ". Not as required: the launcher, Task Scheduler, the app's status: " + values
             + ', "more": {"the launcher": {"json": {')
    assert text.startswith(start) and len(text) == 2900  # Cut at its end.
    # The report keeps the last 3000 characters of a step's text, and a line the first 1500 of that.
    steps.failed(AssertionError(text))
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["steps"][0]["reason"] == "AssertionError: " + text
    assert case.lines(report)[0].startswith("FAILED: one -- AssertionError: " + start)
    # The whole answers are in the report.
    assert report["theStart"]["launcher"] == started and report["theStart"]["task"] == task
    assert report["theStart"]["serviceStatus"] == status


# ---- The record of every read after a host start. It decides no step.
HOST = {"run": "a" * 32, "pid": 4500, "created": "134394144230336231", "phase": "listening"}
UPDATES = {"phase": "checked", "lastCheck": 1794940700.5, "lastAttempt": 1794940699.0}


@pytest.fixture
def installation(case, monkeypatch):
    """Stand-ins for the three reads of the installation that the record makes: the host's record, the update
    record and the end of the host's log. Each can be made to fail."""
    class Installation:
        calls, fails = [], set()

        def read(self, name, value):
            def reader(home):
                self.calls.append((name, home))
                if name in self.fails:
                    raise OSError(name + " cannot be read " + "x" * 400)
                return value() if callable(value) else value
            return reader
    there = Installation()
    there.calls, there.fails = [], set()
    monkeypatch.setattr(case, "host_record", there.read("host", lambda: dict(HOST)))
    monkeypatch.setattr(case, "update_record", there.read("updates", lambda: dict(UPDATES)))
    monkeypatch.setattr(case, "host_output", there.read("output", "the end of the log"))
    return there


def test_a_read_for_the_record_gives_its_answer_or_the_end_of_its_error(case):
    assert case.attempt(lambda: 5) == 5
    def fails():
        raise RuntimeError("a" * 400 + " the end")
    assert case.attempt(fails) == {"errorType": "RuntimeError", "error": "a" * 292 + " the end"}
    # An error of a required check is an answer here too. A stop of the program is not.
    assert case.attempt(lambda: case.need(False, "Required"))["errorType"] == "AssertionError"
    def stopped():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        case.attempt(stopped)


def test_the_seconds_since_a_hosts_start_come_from_its_windows_creation_time(case):
    # 116444736000000000 is January 1, 1970, in 100 nanoseconds since the year 1601.
    assert case.seconds_since("116444736000000000", 12.5) == 12.5
    assert case.seconds_since(str(116444736000000000 + 10 ** 7), 12.5) == 11.5
    assert case.seconds_since("134394144230336231", 1794940833.5) == 10.466


@pytest.mark.parametrize("answer, expected", [
    ("unreachable", "unreachable"), (None, None), (True, True), (7, 7),
    ("t" * 200, "'" + "t" * 119),
    ([1, 2], "[1, 2]"),
    ({**TASK, "errorType": "RuntimeError"}, {"managed": True, "state": "running", "instances": 1,
                                             "lastResult": 267009, "errorType": "RuntimeError"}),
    ({"managed": False}, {"managed": False}),
    ({"managed": False, "errorType": "OSError", "error": "e" * 1000 + " the end", "other": 1},
     {"managed": False, "errorType": "OSError", "error": "e" * 292 + " the end"}),
    (STATUS, {"exitCode": 0, "seconds": 1.2, "ok": True, "processVerified": True, "responding": True,
              "pid": 4500, "created": "134394144230336231"}),
    (STARTED, {"exitCode": 0, "seconds": 9.9, "background": True, "created": True}),
    ({**STATUS, "json": None}, {"exitCode": 0, "seconds": 1.2, "json": "None"}),
    ({**STATUS, "json": ["n" * 100]}, {"exitCode": 0, "seconds": 1.2, "json": "['" + "n" * 58}),
])
def test_the_record_of_a_read_keeps_the_part_of_an_answer_that_the_conditions_read(case, answer, expected):
    assert case.brief(answer) == expected


def observe(case, tmp_path, read, good, **more):
    steps = case.Steps(tmp_path / "report.json", ("one",))
    steps.begin("one")
    return steps, case.observed(steps, tmp_path / "app", "the read", read, good, **more)


def test_a_read_goes_back_to_its_step_as_it_is_and_the_record_keeps_the_host(case, tmp_path, installation, monkeypatch):
    times = iter([1794940833.5])
    monkeypatch.setattr(case.time, "time", lambda: next(times))
    answer = {"exitCode": 0, "json": {"ok": True}, "seconds": 1.5}
    steps, given = observe(case, tmp_path, lambda: answer, lambda value: value["json"]["ok"] is True)
    assert given is answer  # The same object: the step decides with what the read gave.
    [entry] = steps.report["reads"]
    assert entry.pop("seconds") >= 0
    assert entry == {"read": "the read", "at": 1794940833.5, "hostBefore": HOST, "hostAfter": HOST,
                     "secondsSinceHostStart": 10.466, "answer": {"exitCode": 0, "seconds": 1.5, "ok": True},
                     "asRequired": True}
    # The host's record is read once before the read and once after it. Nothing is read again later.
    assert installation.calls == [("host", tmp_path / "app")] * 2 and steps.again == []


@pytest.mark.parametrize("good", [lambda value: value == "trusted", lambda value: value["no such"],
                                  lambda value: "yes"])
def test_a_read_that_is_not_as_required_is_marked_and_goes_back_all_the_same(case, tmp_path, installation, good):
    made = []
    read = lambda: made.append(1) or "unreachable"
    steps, given = observe(case, tmp_path, read, good)
    assert given == "unreachable"  # No error here: the step's own condition decides.
    assert made == [1]  # And no second read: an answer that is not as required is not asked for again at once.
    [entry] = steps.report["reads"]
    assert entry["asRequired"] is False and entry["answer"] == "unreachable" and "later" not in entry
    assert steps.again == [(entry, read, good)]
    # The read to make later can be another one: a status read that does not wait.
    other = lambda: "later"
    steps, _ = observe(case, tmp_path, read, good, later=other)
    assert steps.again == [(steps.report["reads"][0], other, good)]


def test_an_error_of_a_read_goes_on_to_its_step_and_the_record_keeps_that_it_failed(case, tmp_path, installation):
    error = TimeoutError("The command did not end")
    def read():
        raise error
    steps = case.Steps(tmp_path / "report.json", ("one",))
    with pytest.raises(TimeoutError) as raised:
        case.observed(steps, tmp_path / "app", "the read", read, lambda value: True)
    assert raised.value is error
    [entry] = steps.report["reads"]
    assert entry["failed"] == "TimeoutError" and entry["seconds"] >= 0 and entry["hostBefore"] == HOST
    assert "answer" not in entry and "asRequired" not in entry and steps.again == []


def test_a_host_record_that_cannot_be_read_does_not_fail_the_read(case, tmp_path, installation):
    installation.fails = {"host"}
    steps, given = observe(case, tmp_path, lambda: "trusted", lambda value: value == "trusted")
    assert given == "trusted"
    [entry] = steps.report["reads"]
    unread = {"errorType": "OSError", "error": ("host cannot be read " + "x" * 400)[-300:]}
    assert entry["hostBefore"] == unread and entry["hostAfter"] == unread and entry["asRequired"] is True
    assert entry["secondsSinceHostStart"]["errorType"] == "KeyError"


def test_the_watch_keeps_each_change_of_the_listeners_answer_and_of_the_owners_of_its_port(case):
    answers = iter(["unreachable", "unreachable", "unreachable", "trusted", "trusted", "unreachable", "trusted"])
    owners = iter([[], [], [4500], [4500], [4500], [4500], [5100]])
    # The time of an entry is the time at which its question began: the clock is read before the question.
    order = []
    def clock():
        order.append("clock")
        return 100 + order.count("clock") - 1
    def probe():
        order.append("probe")
        return next(answers)
    watch = case.ListenerWatch(probe, lambda: next(owners), clock=clock, timer=lambda: 0)
    for _ in range(5):
        watch.look()
    watch.mark("the second start")
    watch.look()
    watch.look()
    kept = watch.stop()
    assert order[:2] == ["clock", "probe"]
    # No answer and no listener; no answer with a listener (the host is there and does not answer); an answer;
    # no answer of the same process; an answer of another process. The mark is in the same list, in the order of time.
    assert "late" not in kept  # No gauge was given.
    assert kept == {"started": False, "ended": True, "questions": 7, "changes": [
        {"at": 100, "answer": "unreachable", "owner": [], "seconds": 0},
        {"at": 102, "answer": "unreachable", "owner": [4500], "seconds": 0},
        {"at": 103, "answer": "trusted", "owner": [4500], "seconds": 0},
        {"at": 105, "mark": "the second start"},
        {"at": 106, "answer": "unreachable", "owner": [4500], "seconds": 0},
        {"at": 107, "answer": "trusted", "owner": [5100], "seconds": 0}]}


def test_the_watch_keeps_how_long_a_question_took_and_the_slowest_one_while_nothing_changes(case):
    # Each question: the timer is read before it, after it, and at the end of the look. The difference of the
    # first two is the seconds of the question.
    seconds = [0.1, 0.09, 1.6, 0.1, 1.2, 2.0, 0.08, 0.5]
    ticks = iter([value for took in seconds for value in (10.0, 10.0 + took, 10.0 + took)])
    answers = iter(["trusted"] * 5 + ["unreachable"] + ["trusted"] * 2)
    times = iter(range(100, 200))
    watch = case.ListenerWatch(lambda: next(answers), clock=lambda: next(times), timer=lambda: next(ticks))
    for _ in range(4):
        watch.look()
    watch.mark("the uninstall")  # A mark is no answer: the slowest question stays with the answer before it.
    for _ in range(4):
        watch.look()
    assert watch.stop()["changes"] == [
        # The third question was the slowest of the five with this answer. A later one that took less changes nothing.
        {"at": 100, "answer": "trusted", "owner": None, "seconds": 0.1, "slowest": {"at": 102, "seconds": 1.6}},
        {"at": 104, "mark": "the uninstall"},
        {"at": 106, "answer": "unreachable", "owner": None, "seconds": 2.0},
        # A question that took longer than the one that brought the change.
        {"at": 107, "answer": "trusted", "owner": None, "seconds": 0.08, "slowest": {"at": 108, "seconds": 0.5}}]
    # The seconds are those of the question to the listener. The read of the port's owners is not in them.
    clock = [0.0]
    def question():
        clock[0] += 0.25
        return "trusted"
    def owners():
        clock[0] += 3.0
        return [4500]
    timed = case.ListenerWatch(question, owners, timer=lambda: clock[0])
    timed.look()
    assert timed.changes[0]["seconds"] == 0.25
    # After the limit of the list no entry gets the time of a question that belongs to an answer that is not kept.
    numbers, ticks = iter([1, 2, 2, 2]), iter([0, 0.1, 0.1, 0, 0.1, 0.1, 0, 5.0, 5.0, 0, 9.0, 9.0])
    full = case.ListenerWatch(lambda: next(numbers), limit=1, timer=lambda: next(ticks))
    for _ in range(4):
        full.look()
    assert [(change["answer"], "slowest" in change) for change in full.changes] == [(1, False)]


def test_the_processor_time_between_two_reads_says_how_busy_the_computer_and_each_process_were(case):
    before = {"busy": 100.0, "all": 400.0, "processes": {
        "4500": {"created": 11, "used": 2.0}, "77": {"created": 12, "used": 1.0}, "900": {"created": 13, "used": 50.0}}}
    after = {"busy": 103.0, "all": 404.0, "processes": {
        "4500": {"created": 11, "used": 3.25}, "88": {"created": 14, "used": 9.0}, "900": {"created": 15, "used": 0.5}}}
    # 4 processor-seconds passed (four processors, one second); 3 of them were work. Process 77 ended, 88 is new.
    # Number 900 has another creation time in the second read: Windows used the number again for another process.
    assert case.cpu_between(before, after) == {"computerBusy": 0.75, "processes": {"4500": 1.25}}
    assert case.cpu_between(before, before) == {"computerBusy": None,
                                                "processes": {"4500": 0.0, "77": 0.0, "900": 0.0}}


def test_the_processor_time_of_a_process_is_read_with_its_creation_time(case, monkeypatch):
    class Call:
        """A stand-in for a function of kernel32: the fixture sets its argument types, then calls it."""

        def __init__(self, act):
            self.act = act

        def __call__(self, *arguments):
            return self.act(*arguments)

    def fill(reference, value):  # Write a FILETIME through the reference that the fixture passed.
        reference._obj.dwHighDateTime, reference._obj.dwLowDateTime = value >> 32, value & 0xFFFFFFFF
    created, opened, closed = 134394144230336231, [], []

    def system_times(idle, kernel, user):  # In units of 100 nanoseconds. Windows' kernel time holds the idle time.
        for reference, seconds in ((idle, 300), (kernel, 340), (user, 60)):
            fill(reference, seconds * 10 ** 7)
        return 1

    def open_process(access, inherit, number):
        opened.append((access, inherit, number))
        return 9 if number == 4500 else None  # Windows opens only process 4500.

    def process_times(handle, creation, ended, kernel, user):
        for reference, value in ((creation, created), (ended, 0), (kernel, 125 * 10 ** 5), (user, 75 * 10 ** 5)):
            fill(reference, value)
        return 1

    class Kernel:
        GetSystemTimes, OpenProcess = Call(system_times), Call(open_process)
        GetProcessTimes, CloseHandle = Call(process_times), Call(lambda handle: closed.append(handle) or 1)
    monkeypatch.setattr(case.C, "WinDLL", lambda name, use_last_error=False: Kernel, raising=False)
    assert case.cpu_seconds([4500, 77]) == {
        "busy": 100.0, "all": 400.0, "processes": {"4500": {"created": created, "used": 2.0}}}
    assert opened == [(0x1000, False, 4500), (0x1000, False, 77)] and closed == [9]


def gauged(case, seconds, answers=None, **more):
    """A watch whose questions take the given seconds, with a stand-in for the read of the processor time."""
    now, counts, took = [50.0], iter(range(1000)), iter(seconds)
    answers, asked = iter(answers or ["trusted"] * len(seconds)), []

    def probe():
        now[0] += next(took)  # The timer goes on by the seconds of this question.
        return next(answers)

    def gauge(owners):
        asked.append(list(owners))
        number = next(counts)
        return {"busy": 10.0 + number, "all": 20.0 + 4 * number, "processes": {str(pid): {"created": 7, "used": number / 2} for pid in owners}}
    times = iter(range(100, 1000))
    watch = case.ListenerWatch(probe, lambda: [4500], gauge=gauge, clock=lambda: next(times),
                               timer=lambda: now[0], **more)
    return watch, asked


def test_the_watch_keeps_each_late_answer_with_what_was_busy_during_it(case):
    watch, asked = gauged(case, [0.1, 0.5, 0.2, 1.7], after=lambda: {"seconds": 0.03, "certificates": 60})
    for _ in range(4):
        watch.look()
    kept = watch.stop()
    # Only the questions of half a second or more. Each with the processor time during it: one read of the gauge
    # before the question, and for a late one a second read after it.
    assert kept["late"] == [
        {"at": 101, "seconds": 0.5, "answer": "trusted", "waitedBefore": 0.0,
         "used": {"computerBusy": 0.25, "processes": {"4500": 0.5}}, "after": {"seconds": 0.03, "certificates": 60}},
        {"at": 103, "seconds": 1.7, "answer": "trusted", "waitedBefore": 0.0,
         "used": {"computerBusy": 0.25, "processes": {"4500": 0.5}}, "after": {"seconds": 0.03, "certificates": 60}}]
    # The gauge reads the processes that owned the port at the question before: none at the first question.
    assert asked == [[], [4500], [4500], [4500], [4500], [4500]]


def test_the_time_that_the_watch_waited_before_a_late_question_is_kept(case):
    # The timer: a question from 10 to 11; its end is read at 11.5; the next question begins at 14 and takes 2.
    ticks = iter([10.0, 11.0, 11.5, 14.0, 16.0, 16.5])
    watch = case.ListenerWatch(lambda: "trusted", gauge=lambda owners: {"busy": 0, "all": 0, "processes": {}},
                               timer=lambda: next(ticks))
    watch.look()
    watch.look()
    first, second = watch.late
    assert first["waitedBefore"] is None  # Nothing before the first question.
    assert second["waitedBefore"] == 2.5 and second["seconds"] == 2.0  # 2.5 seconds, where the pause is 0.5.
    assert second["used"] == {"computerBusy": None, "processes": {}} and "after" not in second


def test_the_list_of_late_answers_has_a_limit_and_a_gauge_that_fails_is_kept_as_its_error(case):
    watch, _ = gauged(case, [1.0] * 45)
    for _ in range(45):
        watch.look()
    assert len(watch.late) == 40 and watch.questions == 45

    def no_gauge(owners):
        raise OSError("Access is denied")

    def no_store():
        raise OSError("The store cannot be opened")
    ticks = iter([0.0, 3.0, 3.5])
    broken = case.ListenerWatch(lambda: "unreachable", gauge=no_gauge, after=no_store, timer=lambda: next(ticks))
    broken.look()  # No error: the watch goes on.
    [entry] = broken.late
    assert entry["seconds"] == 3.0 and entry["answer"] == "unreachable"
    assert entry["used"] == {"errorType": "OSError", "error": "Access is denied"}  # The gauge's own error.
    assert entry["after"] == {"errorType": "OSError", "error": "The store cannot be opened"}
    # Without a gauge the watch keeps no such list, and its result has none.
    plain = case.ListenerWatch(lambda: "trusted", timer=iter([0.0, 9.0, 9.5]).__next__)  # A question of 9 seconds.
    plain.look()
    assert plain.late == [] and "late" not in plain.stop()
    # A limit of the seconds that is given is used.
    strict, _ = gauged(case, [0.3, 0.2], slow=0.25)
    strict.look()
    strict.look()
    assert [entry["seconds"] for entry in strict.late] == [0.3]


def test_a_question_of_the_watch_that_fails_is_an_answer_and_the_list_has_a_limit(case):
    def refused():
        raise ConnectionResetError("cut off")
    def no_table():
        raise OSError("Cannot read Windows listener table.")
    watch = case.ListenerWatch(refused, no_table, limit=3)
    watch.look()
    watch.look()  # The same two errors again: no change.
    first = watch.changes[0]
    assert watch.changes == [{"at": first["at"], "seconds": first["seconds"],
                              "answer": {"errorType": "ConnectionResetError", "error": "cut off"},
                              "owner": {"errorType": "OSError", "error": "Cannot read Windows listener table."},
                              **({"slowest": first["slowest"]} if "slowest" in first else {})}]
    numbers = iter(range(100))
    counting = case.ListenerWatch(lambda: next(numbers), limit=3)
    for _ in range(10):
        counting.look()
    assert [change["answer"] for change in counting.changes] == [0, 1, 2] and counting.questions == 10
    assert all(change["owner"] is None for change in counting.changes)  # No owner read was given.


def test_the_watch_asks_in_a_thread_of_its_own_and_ends_when_it_is_stopped(case):
    asked = []
    def probe():
        asked.append(case.threading.current_thread().name)
        if len(asked) == 2:
            raise OSError("one question failed")  # The watch goes on after it.
        return "trusted"
    watch = case.ListenerWatch(probe, every=0.01).start()
    thread = watch._thread
    assert watch.start() is watch and watch._thread is thread  # A second start adds no second thread.
    assert [one.name for one in case.threading.enumerate()].count("listener-watch") == 1
    deadline = time.monotonic() + 10
    while len(asked) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    kept = watch.stop()
    assert kept["started"] is True and kept["ended"] is True and kept["questions"] >= 5
    assert set(asked) == {"listener-watch"}
    assert [change["answer"] for change in kept["changes"]][:3] == [
        "trusted", {"errorType": "OSError", "error": "one question failed"}, "trusted"]
    count = len(asked)
    time.sleep(0.1)
    assert len(asked) == count and watch.stop() == kept  # Nothing is asked after the stop.


def test_a_watch_whose_thread_does_not_end_says_so(case):
    asking, release = case.threading.Event(), case.threading.Event()

    def probe():  # A question that does not come back, as a connection without a time limit would.
        asking.set()
        release.wait(30)
        return "trusted"
    watch = case.ListenerWatch(probe, every=0.01).start()
    assert asking.wait(10)
    kept = watch.stop(wait=0.05)
    assert kept["started"] is True and kept["ended"] is False
    release.set()
    watch._thread.join(10)
    assert watch.stop()["ended"] is True


def test_the_seconds_of_a_question_do_not_hold_the_read_of_the_processor_time_before_it(case):
    now = [0.0]

    def gauge(owners):  # A slow read of the processor time.
        now[0] += 0.6
        return {"busy": 0, "all": 0, "processes": {}}

    def question(seconds):
        def probe():
            now[0] += seconds
            return "trusted"
        return probe
    quick = case.ListenerWatch(question(0.1), gauge=gauge, timer=lambda: now[0])
    quick.look()
    quick.look()
    # 0.6 second for the read and 0.1 second for the question: the question was not late.
    assert quick.changes[0]["seconds"] == 0.1 and quick.late == []
    slow = case.ListenerWatch(question(0.7), gauge=gauge, timer=lambda: now[0])
    slow.look()
    slow.look()
    assert [entry["seconds"] for entry in slow.late] == [0.7, 0.7]
    # The read before a question is in the time that the watch waited before it. A slow read shows there.
    assert [entry["waitedBefore"] for entry in slow.late] == [None, 0.6]


def test_what_a_stopped_watch_gave_does_not_change_while_its_thread_still_asks(case):
    import copy
    asking, release, count = case.threading.Event(), case.threading.Event(), [0]

    def probe():  # The second question does not come back before the test lets it.
        count[0] += 1
        if count[0] == 2:
            asking.set()
            release.wait(30)
        return "trusted"
    watch = case.ListenerWatch(probe, every=0.01).start()
    assert asking.wait(10)
    kept = watch.stop(wait=0.05)
    assert kept["ended"] is False and [change["answer"] for change in kept["changes"]] == ["trusted"]
    assert "slowest" not in kept["changes"][0]
    given = copy.deepcopy(kept)
    release.set()
    watch._thread.join(10)
    # The second question had the same answer, and it took some time (the stop waited 0.05 second for its thread
    # meanwhile). So the entry now has it as `slowest`, the slowest of the questions that follow a change.
    assert watch.questions == 2 and "slowest" in watch.changes[0]
    # What the stop gave is as it was. The report that holds it can be saved while the thread still asks.
    assert kept == given and kept["changes"][0] is not watch.changes[0]
    assert watch.stop()["changes"][0]["slowest"]["seconds"] > 0


def test_the_watch_changes_and_copies_its_lists_under_one_lock(case):
    watch, done = case.ListenerWatch(lambda: "trusted"), []

    def waits_for_the_lock(act):
        """True when `act` waits while this test holds the watch's lock, and ends when the lock is free."""
        thread = case.threading.Thread(target=lambda: done.append(act()), daemon=True)
        with watch._lock:
            thread.start()
            thread.join(0.2)
            waited = thread.is_alive()
        thread.join(10)
        return waited and not thread.is_alive()
    assert waits_for_the_lock(watch.look) and watch.questions == 1
    assert waits_for_the_lock(lambda: watch.mark("a mark"))
    assert waits_for_the_lock(watch.stop)
    assert [change.get("mark") or change["answer"] for change in done[-1]["changes"]] == ["trusted", "a mark"]


def test_an_error_of_the_watchs_own_code_ends_the_watch_and_the_stop_says_it(case):
    # A read of the processor time that gives no record: the watch's own code fails at the late answer.
    ticks = iter([0.0, 3.0, 3.5])
    watch = case.ListenerWatch(lambda: "trusted", gauge=lambda owners: 5, timer=lambda: next(ticks), every=0.01)
    watch.start()._thread.join(10)
    kept = watch.stop()
    assert kept["started"] is True and kept["ended"] is True and kept["questions"] == 0 and kept["changes"] == []
    assert kept["endedBy"]["errorType"] == "TypeError"
    # A watch without such an error has no such entry.
    assert "endedBy" not in case.ListenerWatch(lambda: "trusted").stop()


def test_a_watch_is_called_ended_only_when_its_thread_had_ended_before_the_copies(case):
    order = []

    class Thread:
        """A stand-in for the watch's thread: each read of whether it runs takes the next answer."""

        def __init__(self, *running):
            self.running = list(running)

        def join(self, wait):
            order.append("the wait")

        def is_alive(self):
            order.append("does it run?")
            return self.running.pop(0)

    class Lock:
        def __enter__(self):
            order.append("the copies")

        def __exit__(self, *error):
            return False
    watch = case.ListenerWatch(lambda: "trusted")
    # The thread still runs after the wait, and it ends while the copies are made: the copies can lack its last
    # question, so the watch is not called ended.
    watch._thread, watch._lock = Thread(True, False, False), Lock()
    kept = watch.stop()
    assert order == ["the wait", "does it run?", "the copies"]
    assert kept["started"] is True and kept["ended"] is False
    # A thread that had ended after the wait: the copies hold everything, and the watch is called ended.
    order.clear()
    watch._thread = Thread(False, False)
    assert watch.stop()["ended"] is True and order == ["the wait", "does it run?", "the copies"]


def test_a_watch_without_a_question_never_starts_and_says_why(case, tmp_path, monkeypatch):
    idle = case.ListenerWatch().start()
    idle.mark("Stop Amplifier")
    kept = idle.stop()
    assert kept == {"started": False, "ended": True, "questions": 0,
                    "changes": [{"at": kept["changes"][0]["at"], "mark": "Stop Amplifier"}]}
    assert "seconds" not in kept["changes"][0]

    def no_identity(home):
        raise AssertionError("The installation's private HTTPS identity is unavailable")
    monkeypatch.setattr(case, "trust_targets", no_identity)
    watch = case.watch_listener(tmp_path)
    assert watch.stop() == {"started": False, "ended": True, "questions": 0, "changes": [], "notStarted": {
        "errorType": "AssertionError", "error": "The installation's private HTTPS identity is unavailable"}}


def test_the_watch_of_an_installation_asks_its_saved_address_and_reads_the_owner_of_its_port(case, tmp_path, monkeypatch):
    from amplifier_web import windows_installation, windows_task_runtime
    asked = []
    monkeypatch.setattr(case, "trust_targets", lambda home: (["https://127.0.0.1:27595/api/health"], "the pin"))
    monkeypatch.setattr(windows_installation, "saved_port", lambda home: 27595)
    monkeypatch.setattr(windows_installation, "trust_state",
                        lambda urls, pin: asked.append(("trust", urls, pin)) or "trusted")
    monkeypatch.setattr(windows_task_runtime, "listener_owners",
                        lambda host, port: asked.append(("owners", host, port)) or {4500, 77})
    gauges = []
    monkeypatch.setattr(case, "cpu_seconds", lambda owners: gauges.append(owners) or {"busy": 0, "all": 0,
                                                                                      "processes": {}})
    watch = case.watch_listener(tmp_path)
    # The watch of an installation reads the processor time and, after a late answer, Windows' root certificates.
    assert watch.after is case.root_store_read and watch.slow == 0.5
    deadline = time.monotonic() + 10
    while not watch.changes and time.monotonic() < deadline:
        time.sleep(0.01)
    kept = watch.stop()
    assert kept["started"] is True and kept["ended"] is True
    assert [(change["answer"], change["owner"]) for change in kept["changes"]] == [("trusted", [77, 4500])]
    # Nothing here requires that a question is quick. On a machine that gives this program no time for half a
    # second, a question takes that long and counts as late: the watch then asks the gauge a second time with the
    # same owners. That is the watch's own rule; other cases require it with a stand-in for the timer. The 10
    # seconds above only end a test whose watch keeps no answer.
    assert isinstance(kept["changes"][0]["seconds"], float) and isinstance(kept["late"], list)
    # The gauge was asked with each question: at the first question with no owner of the port (twice when that
    # question was late), then with the owners that the question before read.
    first = 2 if gauges[:2] == [[], []] else 1
    assert gauges[:first] == [[]] * first and all(owners == [77, 4500] for owners in gauges[first:])
    assert asked[:2] == [("trust", ["https://127.0.0.1:27595/api/health"], "the pin"), ("owners", "127.0.0.1", 27595)]


def test_the_watch_asks_with_the_products_function_of_its_start_and_not_with_one_put_in_its_place(
        case, tmp_path, monkeypatch):
    # After the second sign-in the trust read of the step (`sign_in_trust`) puts a function of its own in the
    # place of the product's `trust_state` while it reads, and keeps each call of it as a read of the step. The
    # watch asks beside it, from its own thread. Its questions are no reads of a step.
    from amplifier_web import windows_installation, windows_task_runtime
    asked = []
    monkeypatch.setattr(case, "trust_targets", lambda home: (["https://127.0.0.1:27595/api/health"], "the pin"))
    monkeypatch.setattr(windows_installation, "saved_port", lambda home: 27595)
    monkeypatch.setattr(windows_installation, "trust_state",
                        lambda urls, pin: asked.append(("the product's", urls, pin)) or "trusted")
    monkeypatch.setattr(windows_task_runtime, "listener_owners", lambda host, port: {4500})
    monkeypatch.setattr(case, "cpu_seconds", lambda owners: {"busy": 0, "all": 0, "processes": {}})
    watch = case.watch_listener(tmp_path)
    assert watch.stop()["ended"] is True
    asked.clear()
    monkeypatch.setattr(windows_installation, "trust_state",
                        lambda urls, pin: asked.append("the step's") or "unreachable")
    assert watch.probe() == "trusted"
    assert asked == [("the product's", ["https://127.0.0.1:27595/api/health"], "the pin")]


def product_database(folder, state):
    """An app database as the product writes its state: its tables and its own save of a global record."""
    import sqlite3
    from amplifier_web import state_records
    database = sqlite3.connect(folder / "app.sqlite3")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    state_records.initialize(database)
    state_records.save(database, {"revision": 1, "sessions": [], "runtimeControl": {}, **state}, {}, (), set(state))
    database.commit()
    return database  # Open, as the running host has it.


def test_the_update_record_is_read_where_the_product_saves_it_and_holds_the_times_of_the_checks(case, tmp_path):
    check = {"startedAt": 1794940699.0, "finishedAt": 1794940704.5, "elapsedMs": 5500}
    updates = {"phase": "checked", "lastCheck": 1794940704.5, "lastAttempt": 1794940699.0, "checkTiming": check,
               "recentChecks": [check], "available": 0, "appAvailable": False, "initialPreparation": True,
               "pendingApp": None, "pendingRestart": {"revision": "r"}, "pendingSmartTools": [{"id": 1}],
               "sequence": {"stage": "included", "nextStage": "other", "install": False, "included": {"n": 1}},
               "application": {"status": "current", "latest": "v0.106.3", "releaseNotes": ["n" * 5000],
                               "componentUpdates": [{"a": 1}, {"b": 2}]},
               "drain": {"phase": "draining", "target": {"revision": "r"}},
               "diagnostics": {"events": ["e" * 5000], "latest": {
                   "id": "i", "kind": "application", "phase": "stage", "status": "started", "at": 1794940705.0}}}
    database = product_database(tmp_path, {"updates": updates, "settings": {"bundle": "work"}})
    try:
        record = case.update_record(tmp_path)
    finally:
        database.close()
    assert record == {
        "phase": "checked", "lastCheck": 1794940704.5, "lastAttempt": 1794940699.0, "checkTiming": check,
        "recentChecks": [check], "available": 0, "appAvailable": False, "initialPreparation": True,
        "restartPhase": "draining",
        "latestEvent": {"kind": "application", "phase": "stage", "status": "started", "at": 1794940705.0},
        "waiting": ["pendingRestart", "pendingSmartTools"],
        "sequence": {"stage": "included", "nextStage": "other", "install": False},
        "application": {"status": "current", "latest": "v0.106.3", "checkStage": None, "checkReason": None,
                        "componentUpdates": 2}}
    assert len(json.dumps(record)) < 1000  # Not the release notes and not the events.


def test_the_read_of_the_update_record_changes_nothing_and_says_when_there_is_no_record(case, tmp_path):
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        case.update_record(tmp_path)  # No database.
    assert list(tmp_path.iterdir()) == []  # And the read made none.
    database = product_database(tmp_path, {"settings": {"bundle": "work"}})
    try:
        with pytest.raises(AssertionError, match="The app's database has no update state"):
            case.update_record(tmp_path)
    finally:
        database.close()
    source = FIXTURE.read_text(encoding="utf-8")
    reader = source[source.index("def update_record(home):"):source.index("def seconds_since(")]
    assert '.as_uri() + "?mode=ro", uri=True, timeout=2)' in reader and "connection.close()" in reader
    assert reader.count("connection.execute(") == 1 and "SELECT value FROM state_records" in reader


def test_the_update_record_is_kept_at_a_moment_of_the_case_and_fails_no_step(case, tmp_path, installation):
    steps = case.Steps(tmp_path / "report.json", ("one",))
    case.note_updates(steps, tmp_path / "app", "after the first start")
    # The call saved the report.
    [saved] = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["updateRecords"]
    assert saved.pop("at") > 0 and saved == {"when": "after the first start", "host": HOST, "updates": UPDATES}
    installation.fails = {"host", "updates"}

    def full():
        raise OSError("There is not enough space on the disk")
    steps.save = full
    case.note_updates(steps, tmp_path / "app", "after the sign-in")  # Nothing can be read or saved: no error.
    first, second = steps.report["updateRecords"]
    assert first["when"] == "after the first start" and first["updates"] == UPDATES
    assert second.pop("at") > 0 and second == {
        "when": "after the sign-in",
        "host": {"errorType": "OSError", "error": ("host cannot be read " + "x" * 400)[-300:]},
        "updates": {"errorType": "OSError", "error": ("updates cannot be read " + "x" * 400)[-300:]}}
    # The report file still holds the one record of the first call.
    assert len(json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["updateRecords"]) == 1


def failed_step(case, tmp_path):
    steps = case.Steps(tmp_path / "report.json", ("one", "two"))
    steps.begin("one")
    steps.evidence = tmp_path / "app"
    return steps


def test_after_a_failed_step_the_record_keeps_the_next_seconds_and_changes_no_text(case, tmp_path, installation):
    steps = failed_step(case, tmp_path)
    bad = case.observed(steps, tmp_path / "app", "trust", lambda: "unreachable", lambda value: value == "trusted",
                        later=lambda: "trusted")
    case.observed(steps, tmp_path / "app", "status", lambda: "fine", lambda value: True)
    still = case.observed(steps, tmp_path / "app", "task", lambda: {"managed": False}, lambda value: value["managed"])
    assert bad == "unreachable" and still == {"managed": False}
    steps.failed(AssertionError("Windows does not trust the connection. It read: \"unreachable\""))
    watch, waited = case.ListenerWatch(), []

    def wait(seconds):
        # The failed step's text and the first records are saved before the wait.
        report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        assert report["steps"][0]["reason"].endswith('It read: "unreachable"')
        assert report["afterFailure"]["host"] == HOST and report["afterFailure"]["updates"] == UPDATES
        assert "hostLater" not in report["afterFailure"] and watch.changes[-1]["mark"] == "a step failed"
        waited.append(seconds)
    installation.calls.clear()
    assert case.keep_after_failure(steps, watch, wait=wait) is None
    assert waited == [20]
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    kept = report["afterFailure"]
    assert kept.pop("at") > 0
    assert kept == {"host": HOST, "updates": UPDATES, "hostLater": HOST, "updatesLater": UPDATES,
                    "hostOutput": "the end of the log"}
    trust, status, task = report["reads"]
    # Each read whose answer was not as required is made once more: the given later read, or the same read.
    assert trust["later"].pop("at") > 0 and task["later"].pop("at") > 0
    assert trust["later"] == {"answer": "trusted", "asRequired": True, "host": HOST}
    assert task["later"] == {"answer": {"managed": False}, "asRequired": False, "host": HOST}
    assert "later" not in status
    # A later answer that is as required changes neither the step nor its text.
    assert report["steps"][0] == {"name": "one", "status": "FAIL", "reason": (
        'AssertionError: Windows does not trust the connection. It read: "unreachable"')}
    assert steps.finish() is False
    assert [name for name, _ in installation.calls] == ["host", "updates", "host", "host", "host", "updates", "output"]


def test_after_a_failed_step_nothing_of_the_record_can_raise_or_pass_a_step(case, tmp_path, installation):
    steps = failed_step(case, tmp_path)
    def read():
        raise TimeoutError("no answer")
    steps.again.append(({"read": "status"}, read, lambda value: True))
    steps.failed(AssertionError("The start failed"))
    installation.fails = {"host", "updates", "output"}
    real, saves = steps.save, []

    def save():
        saves.append(1)
        raise OSError("There is not enough space on the disk")
    steps.save = save

    class Broken:
        def mark(self, text):
            raise RuntimeError("no watch")

    def wait(seconds):
        raise RuntimeError("no clock")
    assert case.keep_after_failure(steps, Broken(), wait=wait) is None
    assert len(saves) == 2
    entry = steps.again[0][0]
    assert entry["later"]["answer"] == {"errorType": "TimeoutError", "error": "no answer"}
    assert entry["later"]["asRequired"] is True  # Of the later read only. The step stays failed:
    steps.save = real
    assert steps.finish() is False and steps.report["steps"][0]["reason"] == "AssertionError: The start failed"
    assert {key: value["errorType"] for key, value in steps.report["afterFailure"].items() if key != "at"} == {
        "host": "OSError", "updates": "OSError", "hostLater": "OSError", "updatesLater": "OSError",
        "hostOutput": "OSError"}


def test_without_an_app_folder_that_is_safe_to_read_nothing_is_read_after_a_failure(case, tmp_path, installation):
    steps = case.Steps(tmp_path / "report.json", ("one",))
    assert steps.evidence is None and steps.again == []  # As every phase starts.
    waited = []
    steps.again.append(({"read": "status"}, lambda: waited.append("read"), lambda value: True))
    assert case.keep_after_failure(steps, case.ListenerWatch(), wait=waited.append) is None
    assert waited == [] and installation.calls == [] and "afterFailure" not in steps.report


def phase(source, name, following):
    return source[source.index(f"def {name}(stage, exchange, account, token):"):source.index(following)]


def test_each_later_start_is_accepted_through_the_check_that_names_the_answer():
    source = FIXTURE.read_text(encoding="utf-8")
    journey = phase(source, "journey", "def trust_targets(home):")
    second = phase(source, "second", "def delete_everything(")
    assert ('accept_start(steps, "secondStart", again, home, root, again_started, state, status, '
            '"The second start failed")') in journey
    assert ('accept_start(steps, "startAfterUninstall", again, home, root, started, state, status,\n'
            '                     "The start after the uninstall failed")') in second
    assert source.count("        accept_start(steps, ") == 2  # The two calls; the definition is not indented.
    # No later start is accepted with a text that holds only the launcher's answer.
    assert "start failed. The launcher said" not in source
    # Each call follows the two reads of its start. The second start keeps the save that it had before the record:
    # a report that cannot be saved there fails the step with the save's error. The start after the uninstall never
    # had such a save, and it has none.
    reads = '                          lambda: again.cli(root, "service", "status", "--json"), again.healthy)\n'
    required = ("        # As before this record: the report must hold the three answers, or the step fails with "
                "the save's error.\n"
                '        steps.report["secondStart"] = {"launcher": again_started, "task": state, '
                '"serviceStatus": status}\n'
                "        steps.save()\n")
    assert reads + required + "        accept_start(steps, " in journey
    assert reads + "        accept_start(steps, " in second and 'steps.report["startAfterUninstall"]' not in source


def test_every_read_after_a_host_start_is_made_through_the_record_and_keeps_its_condition():
    source = FIXTURE.read_text(encoding="utf-8")
    journey = phase(source, "journey", "def trust_targets(home):")
    certificate = phase(source, "certificate", "def sign_in_snapshot(")
    second = phase(source, "second", "def delete_everything(")
    # Each start: Task Scheduler's answer and the app's status, each with its read and with the rule of its step.
    # The line breaks of a call do not count here.
    waiting = ('lambda: again.cli(root, "service", "status", "--wait", "120", "--json", timeout=180), again.healthy, '
               'later=lambda: again.cli(root, "service", "status", "--json"))')
    for part, starts in ((journey, ("the first start", "the second start")),
                         (second, ("the sign-in", "the start that follows the uninstall"))):
        flat = " ".join(part.split())
        for start in starts:
            assert (f'observed(steps, home, "Task Scheduler after {start}", lambda: again.task_facts(home), '
                    "again.running)") in flat
            status = waiting if start == "the sign-in" else (
                'lambda: again.cli(root, "service", "status", "--json"), again.healthy)')
            assert f'observed(steps, home, "the app\'s status after {start}", {status}' in flat
    # No further read of the status or of Task Scheduler: those of the record, and one pair after each stop.
    assert journey.count('again.cli(root, "service", "status"') == 3 and journey.count("again.task_facts(home)") == 3
    assert second.count('again.cli(root, "service", "status"') == 4 and second.count("again.task_facts(home)") == 3
    assert ('observed(steps, home, "the launcher\'s status", lambda: again.launcher(root, "--status"), '
            'lambda value: value["exitCode"] == 0 and (value["json"] or {}).get("ok") is True)'
            ) in " ".join(journey.split())
    # Each read of trust, with the answer that its step requires.
    once = "lambda: windows_installation.trust_state(urls, pin)"
    # After the sign-in the read is `sign_in_trust`: it waits as the product waits and keeps its own observation.
    settled = "lambda: sign_in_trust(steps, again, root, home, urls, pin, status, state)"
    for part, name, read, required in ((certificate, "trust before the Windows question", once, "untrusted"),
                                       (certificate, "trust after Yes", once, "trusted"),
                                       (second, "trust after the sign-in", settled, "trusted"),
                                       (second, "trust after the start that follows the uninstall", once, "trusted")):
        call = part[part.index(f'observed(steps, home, "{name}"'):]
        call = call[:call.index("\n        need(")]
        assert read in call and (once in call) == (read is once)
        assert f'lambda value: value == "{required}")' in call
    # That read is made once, and only through the record: its definition and this one call.
    assert second.count("sign_in_trust(") == 1 and source.count("sign_in_trust(") == 2
    # No read of trust, of Task Scheduler together with the status, or of the waiting status is made beside the record.
    for part in (journey, certificate, second):
        assert part.count("windows_installation.trust_state(urls, pin)") == part.count(
            "lambda: windows_installation.trust_state(urls, pin)")
    assert journey.count("again.task_facts(home), again.cli(") == 1  # After Stop: the app must not run then.
    assert second.count("again.task_facts(home), again.cli(") == 1  # After the uninstall that keeps the folder.
    assert second.count('"--wait", "120"') == 1 and 'later=lambda: again.cli(root, "service", "status", "--json"))' in second
    # The steps' own conditions stand as they were.
    for part, condition in (
            (journey, 'need(again.running(state) and again.healthy(status),\n'
                      '             "Task Scheduler did not start a host that answers"'),
            (journey, 'need(shown["exitCode"] == 0 and (shown["json"] or {}).get("ok") is True,\n'
                      '             "Status did not report a working app"'),
            (certificate, 'need(before == "untrusted", "Before the question Windows must not trust the connection yet.'),
            (certificate, 'need(accepted is True and after == "trusted",\n'
                          '             "After Yes, Windows does not trust the connection"'),
            (second, 'need(again.running(state) and again.healthy(status),\n'
                     '             "After the sign-in the task did not start a host that answers"'),
            (second, 'need(again.running(after["task"]) and again.healthy(after["serviceStatus"]),\n'
                     '             "After the TLS probe the task no longer has a verified responsive host", after)'),
            (second, 'need(after["host"] != list(before["hostBeforeSignOut"]) and after["savedPort"] == before["port"],\n'
                     '             "After the TLS probe the host is not a new host on the same port", after)'),
            (second, 'need(trusted == "trusted", "After the start Windows does not trust the connection. It read",'
                     ' trusted)')):
        assert part.count(condition) == 1


def test_the_watch_and_the_reads_for_the_record_end_before_the_folder_is_deleted():
    source = FIXTURE.read_text(encoding="utf-8")
    journey = phase(source, "journey", "def trust_targets(home):")
    second = phase(source, "second", "def delete_everything(")
    # The watch starts after the reads of the first start, and before the first read after the sign-in.
    assert journey.index('"the app\'s status after the first start"') < journey.index("watch = watch_listener(home)") \
        < journey.index('steps.report["firstStart"] = ')
    assert second.index("watch = watch_listener(home)") < second.index('"the app\'s status after the sign-in"')
    assert source.count("watch_listener(home)") == 3  # Its definition and the two starts.
    # Before the delete choice: the watch is stopped (`stop` waits at most 10 seconds for its thread), and no later
    # failure reads the installation for the record.
    end = 'steps.report["listener"], steps.evidence = attempt(watch.stop), None\n'
    assert second.index("steps.begin(SECOND[3])") < second.index(end) < second.index("delete_everything(again, root")
    assert second.count("steps.evidence = ") == 2 and journey.count("steps.evidence = home") == 1
    # A failed step gets its text first (`steps.failed`). Then the record is kept. After the steps and after a
    # failed step, `stop` is called on the watch once, and not again when the report holds its result already.
    ending = ('    except BaseException as error:\n        steps.failed(error)\n'
              '        attempt(lambda: keep_after_failure(steps, watch))\n'
              '    # Once: `second` stops the watch before the delete choice, and the report then holds its result '
              'already.\n'
              '    if "listener" not in steps.report:\n'
              '        steps.report["listener"] = attempt(watch.stop)\n    return steps.finish()\n')
    assert source.count("attempt(watch.stop)") == 3 and source.count("watch.stop") == 3
    assert journey.count(ending) == 1 and second.count(ending) == 1
    assert source.count("keep_after_failure(steps, watch)") == 2
    # The update record is kept only after a step has passed, never between a start and its reads.
    for part, moments in ((journey, ("after the first start", "at the end of the steps after the first sign-in")),
                          (second, ("after the sign-in", "after the start that follows the uninstall"))):
        for moment in moments:
            lines = part[:part.index(f'note_updates(steps, home, "{moment}")')].rstrip().splitlines()
            assert lines[-1].lstrip().startswith(("steps.passed(", "trust=trusted)"))
        assert part.count("note_updates(") == 2
    assert source.count("note_updates(") == 5  # Its definition and those four.


@pytest.fixture
def services(monkeypatch):
    """A stand-in for the product's reader of its private folder. It keeps how it was asked."""
    from amplifier_web import windows_files

    class Folder:
        files, asked = {}, []

        def read_private(self, name, *, limit):
            self.asked.append(("read", name, limit))
            return self.files[name]

    folder = Folder()
    folder.files, folder.asked = {}, []

    @contextmanager
    def directory(path, *, owned_root, create=False):
        folder.asked.append(("directory", Path(path), Path(owned_root), create))
        yield folder
    monkeypatch.setattr(windows_files, "directory", directory)
    return folder


def test_the_hosts_record_is_read_with_the_products_reader_and_keeps_its_run_and_its_process(case, tmp_path, services):
    record = {"schema": 1, "registration": "b" * 32, "run": "a" * 32, "pid": 4500, "created": "134394144230336231",
              "image": "C:\\Users\\someone\\python.exe", "phase": "listening"}
    services.files["task-runtime.json"] = json.dumps(record).encode()
    assert case.host_record(tmp_path / "app") == {"run": "a" * 32, "pid": 4500, "created": "134394144230336231",
                                                   "phase": "listening"}
    # The folder of the host's own files, never created by this read; the file's name and the product's limit.
    assert services.asked == [("directory", tmp_path / "app/config/services", tmp_path / "app", False),
                              ("read", "task-runtime.json", 8192)]
    # A record without a field gives None for it; no record is an error for `attempt`.
    services.files["task-runtime.json"] = b'{"pid": 7}'
    assert case.host_record(tmp_path / "app") == {"run": None, "pid": 7, "created": None, "phase": None}
    del services.files["task-runtime.json"]
    assert case.attempt(lambda: case.host_record(tmp_path / "app"))["errorType"] == "KeyError"


def test_the_end_of_the_hosts_log_is_read_with_the_products_reader(case, tmp_path, services):
    services.files["task-output.log"] = ("\n".join(f"line {number}" for number in range(1, 101)) + "\n").encode()
    assert case.host_output(tmp_path / "app").splitlines() == [f"line {number}" for number in range(41, 101)]
    assert services.asked == [("directory", tmp_path / "app/config/services", tmp_path / "app", False),
                              ("read", "task-output.log", 131072)]
    # Of 60 long lines: the last 8000 characters. Bytes that are no text do not stop the read.
    long = [f"{number:03}" + "y" * 397 for number in range(1, 101)]
    services.files["task-output.log"] = "\n".join(long).encode() + b"\xff"
    kept = case.host_output(tmp_path / "app")
    assert kept == ("\n".join(long[-60:]) + "\ufffd")[-8000:] and len(kept) == 8000


def test_a_file_of_the_running_installation_is_read_only_with_the_products_reader():
    source = FIXTURE.read_text(encoding="utf-8")
    for name, following in (("host_record", "def host_output(home):"), ("host_output", "UPDATE_FACTS = ")):
        reader = source[source.index(f"def {name}(home):"):source.index(following)]
        assert "with directory(root / \"config/services\", owned_root=root, create=False) as pinned:" in reader
        assert "pinned.read_private(" in reader and "read_text(" not in reader and "open(" not in reader
    # The fixture opens the host's log only in `host_output`, and never with an ordinary open: the host replaces
    # that file. (Main's `sign_in_snapshot` asks the product's own command `service logs` for the log.)
    assert source.count("task-output.log") == 0
    assert source.count("host_output(home)") == 4  # Its definition, the first start, a later start, a failed step.
    watch = source[source.index("def cpu_seconds("):source.index("def note_updates(")]
    assert "read_text(" not in watch and " open(" not in watch and "directory(" not in watch
    # The read of the processor time opens a process only to ask Windows for its times, and closes it.
    gauge = source[source.index("def cpu_seconds("):source.index("def cpu_between(")]
    assert "kernel.OpenProcess(0x1000, False, number)" in gauge and gauge.count("OpenProcess(") == 1
    assert "finally:\n                kernel.CloseHandle(handle)" in gauge


def test_the_steps_are_the_consumer_journey_in_setups_order(case):
    names = case.FIRST + case.SECOND + case.THIRD
    assert len(names) == len(set(names)) == 18 and case.JOURNEY == case.FIRST[3:]
    order = ("The account", "Setup's own code: the checks", "The installation folder", "Setup's check of the installation",
             "Start-menu and Apps-list entries", "First start: the task registration", "First start: the launcher",
             "First start: the host", "Status", "Stop Amplifier", "Second start", "The certificate:",
             "Setup's own code reads", "Sign-out and sign-in", "Uninstall with the choice that keeps",
             "Entries again and a start", "Uninstall with the choice that deletes", "After the delete choice")
    assert [name.startswith(start) for name, start in zip(names, order, strict=True)] == [True] * 18
    assert case.CERTIFICATE == case.FIRST[11] and "the test selects Yes" in case.CERTIFICATE
    # What a runner does not show is said in the result, each with its reason.
    shown = " ".join(name for name, _ in case.NOT_SHOWN)
    for part in ("The setup file", "certificate question", "browser", "windowless launcher", "chat", "Windows 11"):
        assert part in shown
    assert "delete choice" not in shown  # The case runs it.
    assert all(len(why) > 20 for _, why in case.NOT_SHOWN)


def test_the_password_is_never_written_printed_or_put_on_a_command_line():
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("PROFILE = ")]
    # Made once, in the process that creates the user.
    assert source.count("new_password()") == 2 and run.count("password = new_password()") == 1
    # Every line of code that uses the name, in the order of the file. A new use fails here until it is read.
    lines, used = source.splitlines(), []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string == "password" and token.start[0] not in used:
            used.append(token.start[0])
    assert [lines[number - 1].strip() for number in used] == [
        # Windows creates the user with it.
        "def create_user(name, password, mark):",
        "info = _UserInfo1(name, password, 0, 1, None, mark, 0x1 | 0x10000, None)",
        # Each sign-in gives it to the credential store of the runner's own logon, in memory only.
        "def remember(user, password):",
        'blob = C.create_string_buffer(password.encode("utf-16-le"))',
        "value = _Credential(0, 1, DESKTOP_TARGET, None, W.FILETIME(), len(password) * 2, C.cast(blob, C.c_void_p),",
        "def sign_in(name, password):",
        'remember(computer + "\\\\" + name, password)',
        "password = new_password()", "sid = create_user(name, password, mark)",
        "viewer, session = sign_in(name, password)", "viewer, session = sign_in(name, password)", "del password"]
    sign_in = source[source.index("def sign_in(name, password):"):source.index("def sign_out(name, viewer):")]
    # The sign-in keeps it in memory of the runner's own logon only, and forgets it in every case.
    remember = source[source.index("def remember(user, password):"):source.index("def forget():")]
    assert "1, 0, None, None, user)" in remember and "CRED_PERSIST_SESSION" in remember
    assert sign_in.rstrip().endswith("finally:\n        forget()")
    for name in ("execute(", "save(", "print(", "json.dumps(", "write_text(", "environ", "raise ", "Error("):
        for line in re.findall(r"[^\n]*" + re.escape(name) + r"[^\n]*", source):
            assert "password" not in line, line


def test_the_password_is_random_long_and_has_every_character_class(case):
    first, second = case.new_password(), case.new_password()
    assert first != second and len(first) == len(second) == 30
    for value in (first, second):
        assert any(c.islower() for c in value) and any(c.isupper() for c in value)
        assert any(c.isdigit() for c in value) and any(not c.isalnum() for c in value)
        assert set(value) <= set(string.ascii_letters + string.digits + "!#%+-_=")


def test_the_workflow_cannot_skip_and_always_removes_the_account():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch", "pull_request"}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"windows-standard-account", "portable-checks"}
    job = workflow["jobs"]["windows-standard-account"]
    assert job["runs-on"] == "windows-latest" and "if" not in job and "continue-on-error" not in job and "needs" not in job
    assert job["env"]["AMPLIFIER_CREATE_A_STANDARD_USER_ON_THIS_RUNNER"] == "1"
    steps = job["steps"]
    assert all("continue-on-error" not in step for step in steps)
    command = "python -I -B tests/fixtures/windows_standard_account_native.py "
    prepare, run, cleanup, retain = steps[-4:]
    for step, operation in ((prepare, "prepare"), (run, "run"), (cleanup, "cleanup")):
        lines = step["run"].splitlines()
        assert lines[0] == command + operation
        # A failed fixture fails its step.
        assert lines[1].startswith("if ($LASTEXITCODE -ne 0) { throw ")
    assert "if" not in prepare and "if" not in run
    assert cleanup["if"] == "always()" and retain["if"] == "always()"
    assert retain["with"]["path"] == "${{ runner.temp }}/standard-account-evidence/"
    # The candidate of a pull request, not its merge with main.
    assert steps[0]["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    # A change of the product code that the journey runs starts the case.
    paths = workflow["on"]["pull_request"]["paths"]
    for path in (".github/workflows/windows-standard-account.yml", "tests/fixtures/windows_standard_account_native.py",
                 "tests/test_windows_standard_account.py",
                 "tests/fixtures/windows_start_again_native.py", "tests/windows/native_setup.Tests.cs",
                 "scripts/windows_bootstrap_native.cs", "scripts/install.ps1", "amplifier_web/windows_consumer.py",
                 "amplifier_web/windows_consumer_shell.py", "amplifier_web/windows_installation.py",
                 "amplifier_web/windows_service.py", "amplifier_web/windows_task_runtime.py",
                 "amplifier_web/windows_files.py", "amplifier_web/windows_uninstall.py"):
        assert path in paths and (ROOT / path).is_file(), path


def test_the_workflow_runs_every_case_of_this_file_and_a_case_that_did_not_run_fails_it():
    """No other workflow names this file.

    Read from the workflows on November 17, 2026 and not checked here: the
    workflows that run a whole `tests` folder run a component's own folder,
    and the full Python suite (`python-checks.yml`) does not start on a pull
    request.
    """
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    name = Path(__file__).resolve().relative_to(ROOT).as_posix()
    assert name == "tests/test_windows_standard_account.py" and name in workflow["on"]["pull_request"]["paths"]
    others = [path.name for path in sorted(WORKFLOW.parent.iterdir())
              if path.suffix in (".yml", ".yaml") and path != WORKFLOW
              and Path(name).name in path.read_text(encoding="utf-8")]
    assert others == []  # When another workflow names the file, change the sentences here and in the workflow.
    job = workflow["jobs"]["portable-checks"]
    assert job["runs-on"] == "ubuntu-latest"
    assert all(key not in part for part in (job, *job["steps"]) for key in ("if", "continue-on-error", "needs"))
    checkout, python, _, install, run = job["steps"]
    # The candidate of a pull request, not its merge with main, as in the other job; the Python of CI.
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert python["with"]["python-version"] == "3.13" and install["run"] == "uv sync --locked --group dev"
    # GitHub runs the named shell `bash` with `-e -o pipefail`: a failed pytest fails the step through the pipe.
    assert run["shell"] == "bash"
    first, last = run["run"].splitlines()
    log = '"$RUNNER_TEMP/portable-checks.log"'
    # The whole file, with no selection of cases.
    assert first == f"uv run --no-sync pytest -q -ra -p no:cacheprovider {name} | tee {log}"
    start = f"tail -n 1 {log} | grep -E '"
    assert last.startswith(start) and last.endswith("'") and "'" not in last[len(start):-1]
    wanted = last[len(start):-1]
    # pytest's last line. Only the first three pass: the passed cases, then warnings or nothing, then the time, and
    # nothing after it.
    assert wanted.startswith("^") and wanted.endswith("$")
    lines = ["302 passed in 31.07s", "302 passed, 8 warnings in 31.07s", "302 passed, 1 warning in 61.07s (0:01:01)",
             "302 passed in 31.07s and something more", "302 passed in 61.07s (0:01:01) and something more",
             "301 passed, 1 skipped in 31.07s", "300 passed, 2 deselected in 31.07s", "1 failed, 301 passed in 31.07s",
             "301 passed, 1 xfailed in 31.07s", "301 passed, 1 xpassed in 31.07s", "301 passed, 1 error in 31.07s",
             "301 passed, 8 warnings, 2 errors in 31.07s", "1 skipped in 0.07s", "no tests ran in 0.07s",
             "........................................................................ [ 23%]"]
    assert [line for line in lines if re.search(wanted, line)] == lines[:3]


def test_the_jobs_time_limit_holds_every_step_and_the_run_step_holds_its_phases():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    for job in workflow["jobs"].values():
        limits = [int(step["timeout-minutes"]) for step in job["steps"]]
        # The limit of each job is at least the sum of the limits of its steps plus 5 minutes. So a step's own limit
        # ends it before the job's limit does, when GitHub needs no more than those 5 minutes between the steps. In
        # the Windows job that is for the cleanup step.
        assert int(job["timeout-minutes"]) >= sum(limits) + 5
    job = workflow["jobs"]["windows-standard-account"]
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("PROFILE = ")]
    phases = [int(minutes) for minutes in re.findall(r"timeout=(\d+) \* 60\)", run)]
    assert phases == [40, 15, 8]
    # Beside the phases: two sign-ins (3 minutes each at most) and the sign-out (3 minutes at most).
    assert sum(phases) + 2 * 3 + 3 <= int(job["steps"][-3]["timeout-minutes"])


def test_the_case_fails_when_a_step_fails():
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("PROFILE = ")]
    # The run passes only at its last statement, after every step of both sign-ins passed.
    assert run.count('report.update(status="PASS"') == 1
    assert run.index('"A step after the first sign-in failed"') < run.index('"A step after the second sign-in failed"') \
        < run.index('"The check after the delete choice failed"') < run.index('report.update(status="PASS"')
    assert 'return report["status"] == "PASS"' in run


def test_the_account_is_recorded_for_removal_before_windows_creates_it():
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("PROFILE = ")]
    assert run.index('save("ownership.json", {"user": name, "mark": mark})') < run.index("create_user(name, password, mark)") \
        < run.index('save("ownership.json", {"user": name, "mark": mark, "sid": sid})') \
        < run.index("need(account_comment(name) == mark")
    assert run.count("create_user(") == 1 and "mark = MARK + uuid.uuid4().hex" in run


def test_the_user_signs_in_only_after_the_accounts_sid_is_recorded():
    # So the cleanup has the SID of every account that signed in. It names the user's task and profile by it.
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("PROFILE = ")]
    recorded = run.index('save("ownership.json", {"user": name, "mark": mark, "sid": sid})')
    for start in ("sign_in(", "in_session(", 'task("start"'):
        assert start not in run[:recorded], start
    assert recorded < run.index("sign_in(name, password)") < run.index("in_session(")
    # Nothing else starts a program as the user.
    for other in ("CreateProcessWithLogonW", "LogonUserW"):
        assert other not in source, other


def test_an_account_without_a_comment_is_never_read_as_absent():
    source = FIXTURE.read_text(encoding="utf-8")
    comment = source[source.index("def account_comment(name):"):source.index("def local_groups(name):")]
    assert "if status == 2221:  # No such user.\n        return None" in comment
    assert 'return C.cast(buffer, C.POINTER(_UserInfo1)).contents.comment or ""' in comment
    assert comment.count("return None") == 1


def test_the_end_of_a_recorded_process_is_required_on_windows_before_the_user_exists():
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("PROFILE = ")]
    assert run.index('report["recordedProcess"] = recorded_process_check()') < run.index("create_user(")
    assert ('need(report["recordedProcess"] == {"createdAtItsStart": True, "withAnotherCreationTime": "absent",\n'
            '                                           "thenItStillRuns": True, "withItsOwnRecord": "ended",\n'
            '                                           "thenItHasEnded": True},') in run
    check = source[source.index("def recorded_process_check(windows=None, start=None):"):
                   source.index("def ask_to_end_programs(name):")]
    # Another creation time first, while the program runs. Then its own record.
    assert check.index('at_start = before - 2 * 10 ** 7 <= record["created"] <= filetime_now() + 2 * 10 ** 7') \
        < check.index('other = end_recorded({**record, "created": record["created"] + 1}, 5, windows)') \
        < check.index("kept = program.poll() is None") < check.index("own = end_recorded(record, 20, windows)")


OWNED = {"user": "amp-std-000000", "mark": "mark-1", "sid": "S-1-5-21-1-2-3-1003"}
AMPLIFIER_TASK = "AmplifierUnified-" + OWNED["sid"]


def clean_up(case, monkeypatch, tmp_path, *, owned=OWNED, comment="mark-1", later=None, broken=(),
             profile="absent", setting=None, programs="asked"):
    """Run the cleanup with stand-ins for Windows. `broken` names the parts that fail.

    `comment` is the comment of the account with the recorded name (None: no
    such account). `later` is its comment at every read after the first.
    `setting` is (the earlier value of the viewer's setting, the registry).
    """
    calls, files, exists, reads = [], {"ownership.json": owned}, {"account": comment is not None}, []
    if setting:
        files["viewer-setting.json"] = {"earlier": setting[0]}
        monkeypatch.setitem(sys.modules, "winreg", setting[1])

    def fail(name):
        if name in broken:
            raise OSError(name + " failed")

    def account_comment(name):
        fail("identify")
        reads.append(name)
        if not exists["account"]:
            return None
        return comment if len(reads) == 1 or later is None else later

    def delete_user(name):
        fail("account")
        calls.append("delete_user")
        exists["account"] = False
        return 0

    def ask_to_end_programs(name):
        assert name == OWNED["user"]
        return fail("programs") or calls.append("programs") or programs

    monkeypatch.setattr(case, "saved", files.get)
    monkeypatch.setattr(case, "save", files.__setitem__)
    monkeypatch.setattr(case, "system", lambda name: name)
    monkeypatch.setattr(case, "folders", lambda: (tmp_path, tmp_path))
    monkeypatch.setattr(case, "forget", lambda: fail("credential") or "absent")
    monkeypatch.setattr(case, "end_viewers", lambda records: fail("viewers") or calls.append("viewers") or "ended")
    monkeypatch.setattr(case, "ask_to_end_programs", ask_to_end_programs)
    monkeypatch.setattr(case, "account_comment", account_comment)
    monkeypatch.setattr(case, "account_sid", lambda name: b"sid" if exists["account"] else None)
    monkeypatch.setattr(case, "sid_text", lambda sid: fail("sid") or OWNED["sid"])
    monkeypatch.setattr(case, "sign_out", lambda name, viewer: fail("signedOut") or calls.append("sign_out") or True)
    monkeypatch.setattr(case, "task", lambda op, name, **_: fail(name) or calls.append(op + " " + name)
                        or {"exists": False})
    monkeypatch.setattr(case, "remove_profile", lambda sid: fail("profile") or calls.append("profile") or profile)
    monkeypatch.setattr(case, "delete_user", delete_user)
    return case.cleanup(), files["cleanup.json"], calls


def test_the_cleanup_passes_when_every_part_is_done(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path)
    assert passed is True
    assert {name: result[name] for name in ("credential", "viewers", "signedOut", "helperTask",
                                            "amplifierTask", "profile", "account", "viewerSetting")} == {
        "credential": "absent", "viewers": "ended", "signedOut": True, "helperTask": "absent",
        "amplifierTask": "absent", "profile": "absent", "account": "absent", "viewerSetting": "unchanged"}
    assert calls == ["viewers", "sign_out", "programs", "remove AmplifierStandardAccountCheck",
                     "remove " + AMPLIFIER_TASK, "profile", "delete_user"]


@pytest.mark.parametrize("part, label", [
    ("credential", "credential"), ("viewers", "viewers"), ("signedOut", "signedOut"),
    ("AmplifierStandardAccountCheck", "helperTask"), (AMPLIFIER_TASK, "amplifierTask"), ("profile", "profile")])
def test_a_part_that_fails_does_not_stop_the_other_parts_and_fails_the_cleanup(case, monkeypatch, tmp_path, part, label):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, broken=(part,))
    assert passed is False
    # The account is removed all the same, and the result names the one part that is left.
    assert "delete_user" in calls and result["account"] == "absent" and result["viewerSetting"] == "unchanged"
    assert [name for name, value in result.items() if str(value).startswith("left: OSError")] == [label]


def test_the_request_to_end_the_users_programs_does_not_decide_whether_the_cleanup_passes(case, monkeypatch, tmp_path):
    # The request is sent for this run's account. Nothing reads what it left, so it is no part of `cleaned`.
    passed, result, calls = clean_up(case, monkeypatch, tmp_path)
    assert passed is True and result["programs"] == "asked" and "programs" in calls
    assert "programs" not in case.CLEAN and "programs" not in case.OWNED_CLEAN
    assert "processes" not in case.CLEAN and "processes" not in case.OWNED_CLEAN and "processes" not in result
    # A request that could not be sent is kept in the result, and the other parts are done.
    unsent = "not asked: OSError: taskkill did not start. No read of what is left."
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, programs=unsent)
    assert passed is True and result["programs"] == unsent and "delete_user" in calls
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, broken=("programs",))
    assert passed is True and result["programs"].startswith("left: OSError") and "delete_user" in calls


def test_the_request_to_end_programs_is_one_command_by_the_users_name(case, monkeypatch):
    commands = []
    monkeypatch.setattr(case, "system", lambda name: name)
    monkeypatch.setattr(case, "execute", lambda argv, **_: commands.append(list(argv)) or {"exitCode": 128})
    said = case.ask_to_end_programs("amp-std-000000")
    assert commands == [["taskkill.exe", "/F", "/FI", "USERNAME eq amp-std-000000"]]
    # The answer says what it is: a request, with no read. It never says that the programs ended.
    assert said == "asked: taskkill ended with 128. No read of what is left."


def test_a_request_that_cannot_be_sent_says_so_and_raises_nothing(case, monkeypatch):
    def execute(argv, **_):
        raise OSError("taskkill did not start")

    monkeypatch.setattr(case, "system", lambda name: name)
    monkeypatch.setattr(case, "execute", execute)
    assert case.ask_to_end_programs("amp-std-000000") == (
        "not asked: OSError: taskkill did not start. No read of what is left.")


def test_a_profile_that_windows_keeps_fails_the_cleanup(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, profile="left: in use")
    assert passed is False and result["profile"] == "left: in use"
    assert "delete_user" in calls and result["account"] == "absent"


def test_an_account_that_windows_does_not_delete_fails_the_cleanup(case, monkeypatch, tmp_path):
    passed, result, _ = clean_up(case, monkeypatch, tmp_path, broken=("account",))
    assert passed is False and result["account"] == "left" and result["deleteStatus"].startswith("left: OSError")


@pytest.mark.parametrize("recorded", [{}, {"sid": None}, {"sid": ""}, {"sid": "amp-std-000000"}, {"sid": 1003}])
def test_an_account_whose_sid_was_not_recorded_is_removed_by_its_mark(case, monkeypatch, tmp_path, recorded):
    # Windows created the account, and the line that records its SID did not run, or the record holds no SID.
    owned = {"user": OWNED["user"], "mark": "mark-1", **recorded}
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=owned)
    assert passed is True and result["accountBefore"] == "this run's"
    # The SID comes from Windows. The task and the profile are removed by it.
    assert "delete_user" in calls and "remove " + AMPLIFIER_TASK in calls and "profile" in calls
    assert result["amplifierTask"] == result["profile"] == "absent"


@pytest.mark.parametrize("recorded", [{}, {"sid": ""}])
def test_this_runs_account_is_deleted_also_when_its_sid_cannot_be_read(case, monkeypatch, tmp_path, recorded):
    owned = {"user": OWNED["user"], "mark": "mark-1", **recorded}
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=owned, broken=("sid",))
    # The sign-out, the helper task and the account itself are done. The task and the profile
    # that are named by the SID count as left, so the step fails.
    assert calls == ["viewers", "sign_out", "programs", "remove AmplifierStandardAccountCheck", "delete_user"]
    assert result["account"] == "absent" and passed is False
    assert result["amplifierTask"] == result["profile"] == "left: the account's SID was not read: OSError"


@pytest.mark.parametrize("recorded", [{}, {"sid": ""}])
def test_without_a_sid_the_task_and_the_profile_are_not_read_and_the_cleanup_fails(case, monkeypatch, tmp_path, recorded):
    # No SID record and no account with the name. The cleanup concludes nothing from the absent account.
    owned = {"user": OWNED["user"], "mark": "mark-1", **recorded}
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=owned, comment=None)
    assert passed is False and result["accountBefore"] == "absent" and result["account"] == "absent"
    assert calls == ["viewers", "sign_out", "remove AmplifierStandardAccountCheck"]
    assert result["amplifierTask"] == result["profile"] == "left: not read: the run recorded no SID for the account"
    assert result["programs"] == "not asked: Windows has no account with this name"
    assert not hasattr(case, "NEVER")


def test_a_second_cleanup_after_one_that_deleted_the_account_without_its_sid_fails_too(case, monkeypatch, tmp_path):
    owned = {"user": OWNED["user"], "mark": "mark-1"}
    # The first cleanup cannot read the SID. It deletes the account, and it fails.
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=owned, broken=("sid",))
    assert passed is False and "delete_user" in calls and result["account"] == "absent"
    # A second cleanup finds no account and no SID. The profile can still exist: it is not read, so no pass.
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=owned, comment=None)
    assert passed is False and "profile" not in calls
    assert result["profile"] == "left: not read: the run recorded no SID for the account"


def test_the_task_and_the_profile_of_a_deleted_account_are_read_by_its_sid(case, monkeypatch, tmp_path):
    # A first cleanup did not remove the profile, deleted the account, and failed.
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, profile="left: in use")
    assert passed is False and result["account"] == "absent" and "delete_user" in calls
    # The next cleanup finds no account. It still reads the task and the profile by the recorded SID.
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, comment=None, profile="left: in use")
    assert calls == ["viewers", "sign_out", "remove AmplifierStandardAccountCheck", "remove " + AMPLIFIER_TASK, "profile"]
    assert passed is False and result["profile"] == "left: in use"
    # When Windows lists none of them any more, that cleanup passes.
    passed, result, _ = clean_up(case, monkeypatch, tmp_path, comment=None)
    assert passed is True and result["amplifierTask"] == result["profile"] == "absent"


@pytest.mark.parametrize("owned", [{"mark": "mark-1"}, {"user": OWNED["user"]}, {"user": "", "mark": "mark-1"}])
def test_a_record_without_a_name_or_a_mark_touches_no_account_and_the_other_parts_run(case, monkeypatch, tmp_path, owned):
    registry = Registry(0)
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=owned, setting=(None, registry))
    assert passed is False and calls == ["viewers"]
    assert result["account"] == "left: the record of the account is not complete"
    assert result["viewerSetting"] == "restored" and registry.value is None


def test_an_account_of_another_program_under_this_name_is_not_touched(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, comment="A person's own account")
    assert passed is False and calls == ["viewers"]
    assert result["accountBefore"] == "another account" and result["account"] == "left: another account"


def test_nothing_of_an_account_is_touched_when_windows_does_not_say_whose_it_is(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, broken=("identify",))
    assert passed is False and calls == ["viewers"]
    assert result["accountBefore"].startswith("left: OSError")
    assert result["account"] == "left: Windows did not say whose account this is"
    # The parts that are not the account's are done all the same.
    assert result["credential"] == "absent" and result["viewers"] == "ended" and result["viewerSetting"] == "unchanged"


def test_an_account_that_is_another_one_directly_before_the_deletion_is_not_deleted(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, later="A person's own account")
    assert "delete_user" not in calls and result["deleteStatus"] == "not deleted: another account"
    assert result["account"] == "left" and passed is False


def test_no_record_means_that_no_account_was_requested(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=None)
    assert passed is True and calls == ["viewers"] and "account" not in result
    assert result["credential"] == "absent" and result["viewerSetting"] == "unchanged"


class Windows:
    """The processes of Windows, by their numbers. `refuses`: Windows does not take a request to end one."""

    def __init__(self, processes, refuses=False, survives=False, fails=None):
        # `survives`: Windows takes the request, and the process still runs. `fails`: the call that fails.
        self.processes = {number: dict(facts) for number, facts in processes.items()}
        self.refuses, self.survives, self.fails, self.calls, self.open_now = refuses, survives, fails, [], 0

    def open(self, number, end=True):
        self.calls.append(("open", number, end))
        if number not in self.processes:
            return None
        self.open_now += 1
        return ("handle", number)

    def created(self, handle):
        if self.fails == "created":
            raise OSError("Access is denied")
        return self.processes[handle[1]]["created"]

    def ended(self, handle, seconds):
        self.calls.append(("ended?", handle[1], seconds))
        if self.fails == "ended":
            raise OSError("The wait failed")
        return not self.processes[handle[1]]["runs"]

    def end(self, handle):
        self.calls.append(("end", handle[1]))
        if not self.refuses and not self.survives:
            self.processes[handle[1]]["runs"] = False
        return not self.refuses

    def close(self, handle):
        self.open_now -= 1


VIEWER = {"pid": 4321, "created": 134394059807909376}


def test_a_recorded_process_is_ended_through_the_one_process_that_was_opened(case):
    windows = Windows({4321: {"created": VIEWER["created"], "runs": True}})
    assert case.end_recorded(VIEWER, 20, windows) == "ended"
    # One open with the right to end it; its end is read before the request and after it; the handle is closed.
    assert windows.calls == [("open", 4321, True), ("ended?", 4321, 0), ("end", 4321), ("ended?", 4321, 20)]
    assert windows.processes[4321]["runs"] is False and windows.open_now == 0


def test_a_later_process_with_the_recorded_number_is_never_ended(case):
    # The viewer ended, and Windows gave its number to another program, also one with the same name.
    windows = Windows({4321: {"created": VIEWER["created"] + 1, "runs": True}})
    assert case.end_recorded(VIEWER, 20, windows) == "absent"
    assert windows.calls == [("open", 4321, True)]  # No request to end it, and no wait.
    assert windows.processes[4321]["runs"] is True and windows.open_now == 0


def test_a_recorded_process_that_no_longer_exists_is_absent(case):
    windows = Windows({99: {"created": VIEWER["created"], "runs": True}})
    assert case.end_recorded(VIEWER, 20, windows) == "absent"
    assert windows.calls == [("open", 4321, True)] and windows.processes[99]["runs"] is True


def test_a_recorded_process_that_has_ended_already_gets_no_request(case):
    windows = Windows({4321: {"created": VIEWER["created"], "runs": False}})
    assert case.end_recorded(VIEWER, 20, windows) == "ended"
    assert ("end", 4321) not in windows.calls and windows.open_now == 0


def test_a_recorded_process_that_windows_does_not_end_is_left(case):
    windows = Windows({4321: {"created": VIEWER["created"], "runs": True}}, refuses=True)
    assert case.end_recorded(VIEWER, 20, windows) == "left: process 4321 runs; Windows refused to end it"
    assert windows.processes[4321]["runs"] is True and windows.open_now == 0


def test_a_process_that_still_runs_after_windows_took_the_request_is_left(case):
    windows = Windows({4321: {"created": VIEWER["created"], "runs": True}}, survives=True)
    assert case.end_recorded(VIEWER, 20, windows) == "left: process 4321 runs"  # Not refused: it did not end in time.
    assert windows.calls == [("open", 4321, True), ("ended?", 4321, 0), ("end", 4321), ("ended?", 4321, 20)]
    assert windows.open_now == 0


@pytest.mark.parametrize("fails", ["created", "ended"])
def test_the_open_process_is_closed_when_a_read_fails_and_the_viewer_counts_as_left(case, monkeypatch, fails):
    windows = Windows({4321: {"created": VIEWER["created"], "runs": True}}, fails=fails)
    with pytest.raises(OSError):
        case.end_recorded(VIEWER, 20, windows)
    assert windows.open_now == 0 and ("end", 4321) not in windows.calls and windows.processes[4321]["runs"] is True
    monkeypatch.setattr(case, "_Processes", lambda: windows)
    assert case.end_viewers([VIEWER]).startswith('left: ["left: OSError: ')


class Program:
    """A program that the test started, as `subprocess.Popen` gives it, on the stand-in for Windows."""

    def __init__(self, windows, pid):
        self.windows, self.pid, self.killed = windows, pid, 0

    def poll(self):
        return None if self.windows.processes[self.pid]["runs"] else 1

    def wait(self, seconds):
        return self.poll()

    def kill(self):
        self.killed += 1
        self.windows.processes[self.pid]["runs"] = False


EXPECTED = {"createdAtItsStart": True, "withAnotherCreationTime": "absent", "thenItStillRuns": True,
            "withItsOwnRecord": "ended", "thenItHasEnded": True}


def test_the_check_of_a_recorded_process_gives_the_result_that_the_run_requires(case):
    windows = Windows({77: {"created": case.filetime_now(), "runs": True}})
    program = Program(windows, 77)
    assert case.recorded_process_check(windows, lambda argv: program) == EXPECTED
    assert program.killed == 0 and windows.open_now == 0


@pytest.mark.parametrize("created", [0, 5, 116444736000000000, 10 ** 18])
def test_a_creation_time_that_is_not_the_time_of_the_start_fails_the_check(case, created):
    # A constant, the year 1970, a far later time: none is the time at which the test started the program.
    windows = Windows({77: {"created": created, "runs": True}})
    assert case.recorded_process_check(windows, lambda argv: Program(windows, 77)) == {
        **EXPECTED, "createdAtItsStart": False}


def test_the_check_ends_its_program_also_when_windows_does_not(case):
    windows = Windows({77: {"created": case.filetime_now(), "runs": True}}, refuses=True)
    program = Program(windows, 77)
    result = case.recorded_process_check(windows, lambda argv: program)
    assert result["withItsOwnRecord"] == "left: process 77 runs; Windows refused to end it"
    assert result["thenItHasEnded"] is False and program.killed == 1


def test_the_time_of_now_is_in_the_units_of_a_windows_creation_time(case):
    # 100 nanoseconds since January 1, 1601 (UTC), computed here in another way.
    from datetime import datetime, timezone
    since = datetime.now(timezone.utc) - datetime(1601, 1, 1, tzinfo=timezone.utc)
    assert abs(case.filetime_now() - (since.days * 86400 + since.seconds) * 10 ** 7) < 2 * 10 ** 7


class Viewer:
    pid = 4321

    def __init__(self):
        self.killed = 0

    def kill(self):
        self.killed += 1

    def poll(self):
        return None


@pytest.mark.parametrize("fails", ["nothing", "record", "save"])
def test_a_viewer_whose_record_fails_is_ended_by_its_starter(case, monkeypatch, fails):
    viewer, files, forgotten = Viewer(), {}, []

    def save(name, value):
        if name == "viewers.json" and fails == "save":
            raise OSError("The disk is full")
        files[name] = value

    def process_record(number):
        if fails == "record":
            raise OSError("Access is denied")
        return {"pid": number, "created": 10}

    monkeypatch.setitem(sys.modules, "winreg", Registry())
    monkeypatch.setenv("COMPUTERNAME", "RUNNER")
    monkeypatch.setattr(case, "system", lambda name: name)
    monkeypatch.setattr(case, "remember", lambda user, secret: None)
    monkeypatch.setattr(case, "forget", lambda: forgotten.append(True) or "absent")
    monkeypatch.setattr(case, "saved", files.get)
    monkeypatch.setattr(case, "save", save)
    monkeypatch.setattr(case, "process_record", process_record)
    monkeypatch.setattr(case, "session_of", lambda name: (1, "Active"))
    monkeypatch.setattr(case.subprocess, "Popen", lambda argv: viewer)
    monkeypatch.setattr(case.time, "sleep", lambda seconds: None)
    if fails == "nothing":
        assert case.sign_in("amp-std-000000", "x") == (viewer, 1)
        assert viewer.killed == 0 and files["viewers.json"] == [{"pid": 4321, "created": 10}]
    else:
        with pytest.raises(OSError):
            case.sign_in("amp-std-000000", "x")
        # The cleanup step ends recorded viewers only, so the starter ends this one at once.
        assert viewer.killed == 1 and "viewers.json" not in files
    assert forgotten == [True]  # The saved sign-in is deleted in every case.


def test_a_process_is_recorded_with_its_number_and_its_creation_time(case):
    windows = Windows({4321: {"created": VIEWER["created"], "runs": True}})
    assert case.process_record(4321, windows) == VIEWER
    # Opened without the right to end it, and closed again.
    assert windows.calls == [("open", 4321, False)] and windows.open_now == 0
    with pytest.raises(AssertionError) as failed:
        case.process_record(5, windows)
    assert "Windows has no process with the number of a program that this test started" in str(failed.value)


def test_the_viewers_are_ended_only_when_each_recorded_one_is_ended_or_absent(case, monkeypatch):
    answers, asked = {}, []

    def end_recorded(record, seconds):
        asked.append(record)
        answer = answers[record["pid"]]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(case, "end_recorded", end_recorded)
    first, second = {"pid": 1, "created": 10}, {"pid": 2, "created": 20}
    answers.update({1: "ended", 2: "absent"})
    assert case.end_viewers([first, second]) == "ended" and asked == [first, second]
    # One viewer that Windows does not end: left.
    answers.update({1: "left: process 1 runs", 2: "ended"})
    assert case.end_viewers([first, second]) == 'left: ["left: process 1 runs"]'
    # A viewer that fails does not stop the viewer after it.
    asked.clear()
    answers.update({1: OSError("Access is denied"), 2: "ended"})
    assert case.end_viewers([first, second]) == 'left: ["left: OSError: Access is denied"]' and asked == [first, second]
    # No viewer was recorded: nothing to end.
    assert case.end_viewers([]) == "ended"


def test_a_record_that_does_not_name_a_process_is_left(case, monkeypatch):
    monkeypatch.setattr(case, "end_recorded", lambda record, seconds: record["pid"] and "ended")
    assert case.end_viewers([4321]).startswith('left: ["left: TypeError')  # A number alone names no process.


def test_a_viewer_is_recorded_while_its_starter_holds_it_and_ended_when_the_record_fails():
    source = FIXTURE.read_text(encoding="utf-8")
    sign_in = source[source.index("def sign_in(name, password):"):source.index("def sign_out(name, viewer):")]
    assert ('        try:\n'
            '            # While `viewer` is held here, Windows gives its number to no other process.\n'
            '            save("viewers.json", (saved("viewers.json") or []) + [process_record(viewer.pid)])\n'
            '        except BaseException:\n'
            '            viewer.kill()  # The cleanup step ends recorded viewers only.\n'
            '            raise\n') in sign_in
    assert sign_in.index("viewer = subprocess.Popen(") < sign_in.index("process_record(viewer.pid)")


def test_windows_is_asked_about_one_open_process_and_never_by_a_number_again():
    source = FIXTURE.read_text(encoding="utf-8")
    windows = source[source.index("class _Processes:"):source.index("def process_record(number, windows=None):")]
    # The rights: read its times, wait for its end, and end it only where that is asked.
    assert "_kernel.OpenProcess(0x1000 | 0x100000 | (0x1 if end else 0), False, number)" in windows
    assert "if C.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: no process has this number." in windows
    # A wait that fails is an error, never "it has ended".
    assert "if status not in (0, 0x102):" in windows and "return status == 0" in windows
    assert windows.count("OpenProcess(") == 1
    ended = source[source.index("def end_recorded("):source.index("def end_viewers(records, seconds=20):")]
    assert ended.count("windows.open(") == 1 and "taskkill" not in ended
    # No program is ended by a number on a command line anywhere.
    assert '"/PID"' not in source
    assert source.count("taskkill.exe") == 1 and '"/F", "/FI", f"USERNAME eq {name}"' in source


class Registry:
    """The part of Windows' registry that the viewer's setting uses. `keeps`: Windows does not take a change."""
    HKEY_CURRENT_USER, REG_DWORD = "HKCU", 4

    def __init__(self, value=None, keeps=False):
        self.value, self.keeps = value, keeps

    @contextmanager
    def CreateKey(self, root, path):
        assert (root, path) == ("HKCU", r"Software\Microsoft\Terminal Server Client")
        yield "key"

    def DeleteValue(self, key, name):
        assert (key, name) == ("key", "AuthenticationLevelOverride")
        if self.value is None:
            raise FileNotFoundError(name)
        if not self.keeps:
            self.value = None

    def SetValueEx(self, key, name, reserved, kind, value):
        assert (key, name, reserved, kind) == ("key", "AuthenticationLevelOverride", 0, 4)
        if not self.keeps:
            self.value = value

    def QueryValueEx(self, key, name):
        if self.value is None:
            raise FileNotFoundError(name)
        return self.value, 4


@pytest.mark.parametrize("now, keeps, earlier, expected, after", [
    (0, False, None, "restored", None),  # The job made the value; before, there was none.
    (0, False, 2, "restored", 2),  # The job changed the value.
    (None, False, None, "restored", None),  # Nothing to do.
    (0, True, None, "left: Windows has the value 0", 0),
    (0, True, 2, "left: Windows has the value 0", 0)])
def test_the_setting_is_restored_only_when_windows_has_the_earlier_value_again(case, now, keeps, earlier, expected, after):
    registry = Registry(now, keeps)
    assert case.restore_setting(earlier, registry) == expected and registry.value == after


@pytest.mark.parametrize("answers, expected, calls", [
    ([{"exitCode": 0, "json": {"left": 0}, "stderr": ""}], "absent", 1),
    # Windows holds the profile for a moment: the second try removes it.
    ([{"exitCode": 1, "json": None, "stderr": "in use"}, {"exitCode": 0, "json": {"left": 0}, "stderr": ""}], "absent", 2),
    # The command ended well, and Windows still lists the profile: not removed.
    ([{"exitCode": 0, "json": {"left": 1}, "stderr": ""}] * 3, "left: ", 3),
    ([{"exitCode": 1, "json": None, "stderr": "Access is denied"}] * 3, "left: Access is denied", 3)])
def test_the_profile_is_absent_only_when_windows_lists_none_afterwards(case, monkeypatch, answers, expected, calls):
    asked, answers = [], list(answers)
    monkeypatch.setattr(case, "powershell", lambda script, value, timeout=120: asked.append(value) or answers.pop(0))
    monkeypatch.setattr(case.time, "sleep", lambda seconds: None)
    assert case.remove_profile("S-1-5-21-1-2-3-1003") == expected
    assert asked == [{"sid": "S-1-5-21-1-2-3-1003"}] * calls


@pytest.mark.parametrize("now, keeps, earlier, expected, passes", [
    (0, False, None, "restored", True), (0, False, 2, "restored", True),
    (0, True, None, "left: Windows has the value 0", False)])
def test_the_cleanup_restores_the_setting_that_the_job_changed(case, monkeypatch, tmp_path, now, keeps, earlier,
                                                              expected, passes):
    registry = Registry(now, keeps)
    passed, result, _ = clean_up(case, monkeypatch, tmp_path, setting=(earlier, registry))
    assert result["viewerSetting"] == expected and passed is passes
    assert registry.value == (now if keeps else earlier)


def test_clean_means_every_part(case):
    done = {"credential": "absent", "viewers": "ended", "viewerSetting": "restored", "signedOut": True,
            "helperTask": "absent", "amplifierTask": "absent", "profile": "absent", "account": "absent"}
    without = {"credential": "absent", "viewers": "ended", "viewerSetting": "unchanged"}
    assert case.cleaned(done, OWNED) and case.cleaned(without, None)
    assert set(done) == set(case.CLEAN) | set(case.OWNED_CLEAN) and set(without) == set(case.CLEAN)
    for name in done:
        assert not case.cleaned({**done, name: "left: something"}, OWNED), name
        assert not case.cleaned({key: value for key, value in done.items() if key != name}, OWNED), name
    for name in without:
        assert not case.cleaned({**without, name: "left: something"}, None), name
        assert not case.cleaned({key: value for key, value in without.items() if key != name}, None), name
    # Each part has one answer that counts as done (the setting: two). "Not read" is none of them.
    for name in done:
        assert not case.cleaned({**done, name: "left: not read: the run recorded no SID for the account"}, OWNED), name


def test_the_saved_sign_in_is_absent_only_when_windows_says_so():
    source = FIXTURE.read_text(encoding="utf-8")
    forget = source[source.index("def forget():"):source.index("def windows_of(pid):")]
    assert 'if _adv.CredDeleteW(DESKTOP_TARGET, 1, 0) or C.get_last_error() == 1168:' in forget
    assert 'return f"left: CredDeleteW failed with error {C.get_last_error()}"' in forget
    cleanup = source[source.index("def cleanup():"):source.index("# ---- As the standard user")]
    assert 'part("credential", forget)' in cleanup and "return cleaned(result, owned)" in cleanup


def test_setups_native_checks_report_which_check_stopped_and_read_one_account():
    harness = (ROOT / "tests/windows/native_setup.Tests.cs").read_text(encoding="utf-8")
    # From setup's own platform check, not from a text of the stop.
    assert "try { NativeInstaller.Platform(); } catch(SetupStop error) { platform=error; }" in harness
    assert ('reached=stop==null?"none":platform==null || stop.Message!=platform.Message?"after-platform"\n'
            '                    :elevation!="none"?"elevation":"windows-or-processor";') in harness
    account = harness[harness.index("static int Account() {"):harness.index("static int Main(string[] args) {")]
    for call in ("NativeInstaller.Elevation()", "NativeInstaller.RootState(root)", "NativeInstaller.SignInTask(out program)",
                 "InstallStorage.Check(local)", "NativeInstaller.RootName", "NativeInstaller.MaxRootLength"):
        assert call in account
    # It only reads: no folder, file, task or registry change.
    for change in ("Create", "Delete", "Write", "Make(", "Register", "Process.Start"):
        assert change not in account.replace("Console.WriteLine", ""), change
    assert 'if(args.Length==1 && args[0]=="--account") return Account();' in harness


ROOT_FOLDER = Path("C:/Users/amp-std-000000/AppData/Local/Amplifier")
FIRST_QUESTION = {"kind": "question", "cancel": True, "answer": "yes",
                  "message": "Uninstall Amplifier?\n\nSelect Yes to delete them, together with the program files. More."}
SECOND_QUESTION = {"kind": "question", "cancel": False, "answer": "yes",
                   "message": "Delete everything that Amplifier keeps in this folder?\n\n" + str(ROOT_FOLDER)
                              + "\n\nThis cannot be undone."}


def test_the_delete_choice_is_exactly_its_two_questions(case):
    assert case.delete_questions([FIRST_QUESTION, SECOND_QUESTION], ROOT_FOLDER)


@pytest.mark.parametrize("notices", [
    [FIRST_QUESTION],                                              # No second question before a deletion.
    [SECOND_QUESTION, FIRST_QUESTION],                             # Another order.
    [FIRST_QUESTION, SECOND_QUESTION, SECOND_QUESTION],            # One question more.
    [FIRST_QUESTION, {**SECOND_QUESTION, "cancel": True}],
    [{**FIRST_QUESTION, "cancel": False}, SECOND_QUESTION],        # The question of a folder that setup did not complete.
    [FIRST_QUESTION, {**SECOND_QUESTION, "answer": "no"}],
    [FIRST_QUESTION, {**SECOND_QUESTION, "kind": "message"}],
    [FIRST_QUESTION, {**SECOND_QUESTION, "message": SECOND_QUESTION["message"].replace(str(ROOT_FOLDER), "C:/other")}],
    [FIRST_QUESTION, {**SECOND_QUESTION, "message": SECOND_QUESTION["message"].replace("This cannot be undone.", "")}],
    [{**FIRST_QUESTION, "message": "Uninstall Amplifier?\n\nSelect No to keep them."}, SECOND_QUESTION],
])
def test_any_other_set_of_questions_is_not_the_delete_choice(case, notices):
    assert not case.delete_questions(notices, ROOT_FOLDER)


def program(machine=b"\x64\x86", manifest=b'<requestedExecutionLevel level="asInvoker" uiAccess="false"/>'):
    """The bytes of a program file: the two headers that Windows reads first, then a manifest."""
    return b"MZ" + bytes(0x3A) + (0x80).to_bytes(4, "little") + bytes(0x40) + b"PE\0\0" + machine + bytes(64) + manifest


def test_a_64_bit_program_that_asks_for_the_persons_own_rights_is_read_as_such(case, tmp_path):
    (tmp_path / "setup.exe").write_bytes(program())
    assert case.execution_level(tmp_path / "setup.exe") == (True, ["asInvoker"])


@pytest.mark.parametrize("change, expected", [
    # A 32-bit program: Windows can guess from a file name like "setup" and ask for an administrator.
    ({"machine": b"\x4c\x01"}, (False, ["asInvoker"])),
    # A program that asks for an administrator: a standard user gets a question for a name and a password.
    ({"manifest": b'<requestedExecutionLevel level="requireAdministrator" uiAccess="false"/>'},
     (True, ["requireAdministrator"])),
    ({"manifest": b'<requestedExecutionLevel  level="highestAvailable"/>'}, (True, ["highestAvailable"])),
    # No level at all, and two levels: neither is the one answer that the case accepts.
    ({"manifest": b""}, (True, [])),
    ({"manifest": b'<requestedExecutionLevel level="asInvoker"/><requestedExecutionLevel level="asInvoker"/>'},
     (True, ["asInvoker", "asInvoker"])),
])
def test_every_other_program_file_is_not_read_as_such(case, tmp_path, change, expected):
    (tmp_path / "setup.exe").write_bytes(program(**change))
    assert case.execution_level(tmp_path / "setup.exe") == expected != (True, ["asInvoker"])


def test_the_case_requires_that_setups_compiled_program_asks_for_no_administrator():
    source = FIXTURE.read_text(encoding="utf-8")
    first = source[source.index("def first(stage, exchange, account, token):"):source.index("def product(stage, root):")]
    assert ('asks = execution_level(stage / "native-setup-tests.exe")\n'
            '        need(asks == (True, ["asInvoker"]),') in first
    # Setup and its checks are compiled for x64 with the compiler's own manifest: no option replaces or removes it.
    for name in ("scripts/windows_bootstrap_native.py", "scripts/windows_consumer_release.py",
                 "tests/fixtures/windows_setup_native.py"):
        assert "win32manifest" not in (ROOT / name).read_text(encoding="utf-8").casefold(), name
    assert '"/platform:x64", "/optimize+"],' in (ROOT / "scripts/windows_bootstrap_native.py").read_text(encoding="utf-8")


def section(text, start, end):
    return " ".join(text[text.index(start):text.index(end)].split())


def test_the_design_document_says_what_the_case_shows_and_what_it_does_not(case):
    said = section(DESIGN, "### A standard Windows account", "## Process-local Proactor cleanup backport")
    assert "`.github/workflows/windows-standard-account.yml`" in said
    assert "`tests/fixtures/windows_standard_account_native.py`" in said
    # The number of steps, and each part that the result names as not shown.
    assert f"the case has {len(case.FIRST + case.SECOND + case.THIRD)} steps" in said
    for name, _ in case.NOT_SHOWN:
        assert name in said, name
    # The document does not say any more that no case shows these.
    assert "The Windows confirmation, and Edge" not in " ".join(DESIGN.split())
    for claim in ("Its user is a member of Administrators", "Windows does not trust the connection",
                  "The test selects Yes with a window message", "the task's own trigger starts a new host",
                  "no installation folder, no sign-in task, no Start-menu entry and no Apps-list entry",
                  "the step fails when one of these parts is not done", "names the certificate and shows the thumbprint",
                  "In every case: end the Remote Desktop viewers that the run recorded",
                  "For the run's account: sign the user out, try to ask Windows to end the programs of the user",
                  "A viewer that the run started and did not record is not ended by this step.",
                  "When its creation time is the recorded one and the process still runs, the step asks Windows "
                  "to end it and waits up to 20 seconds for its end, through that same open process.",
                  "When the job recorded no account, the step has only the three parts of every case.",
                  "A record without a name or without a mark fails the step, and the step touches no account.",
                  "When Windows has no account with the recorded name, the step sends no request to end programs "
                  "and deletes no account; it does the other parts with the recorded name and the recorded SID.",
                  "A viewer that still runs then counts as left.",
                  "When no process has the number, or the process with the number has another creation time, "
                  "the viewer is gone, and the step ends nothing.",
                  "For the run's account, the step tries once to ask Windows to end the programs that run under "
                  "the user's name (`taskkill`).",
                  "When the request cannot be sent, the result says so, and the step does not fail for that.",
                  "The step does not read whether a program of the user is left, and that does not decide "
                  "whether the step passes.",
                  "the recorded creation time is the time of the program's start (within two seconds); under "
                  "another creation time the program is left alone; and its own record ends it",
                  "A list that Windows does not give is a failed part.",
                  "The step also copies the user's reports to the results and saves its own result; neither "
                  "decides whether the step passes.",
                  "It records the account's SID before it signs the user in.",
                  "it counts the task and the profile as not read, does the other parts, and fails",
                  "It concludes nothing from an account that is absent.",
                  "The job's cleanup step (the step before the one that keeps the results)",
                  "When the name belongs to another account, or Windows does not answer, the step removes "
                  "nothing of that account and fails.",
                  "A dialog of this program that is not this question, and a second question, get no answer.",
                  "from one open file",
                  "The step does not delete this case's folders in the runner's temporary folder",
                  "Windows' own list of the user's root certificates no longer holds the certificate",
                  "the Windows version or the processor"):
        assert claim in said, claim
    # The cleanup's promise is what its code requires, and no more.
    assert "its profile and the account in every case" not in said
    assert "The last step of the job" not in said and "Each part runs also when another part failed" not in said
    assert "it ends the Remote Desktop viewers of the run" not in said
    # The document does not say that the step reads the user's programs.
    for earlier in ("every program that runs with the account's SID", "the user's programs, the tasks",
                    "ends the programs that run as the account"):
        assert earlier not in said, earlier
    # It does not say that the request is sent in every case, and it concludes nothing from an absent account.
    for earlier in ("asks Windows once", "asks Windows to end the programs", "it ends the Remote Desktop viewers",
                    "the run stopped before it signed the user in"):
        assert earlier not in said, earlier
    # The parts that the document names as deciding are the parts of `cleaned`, and no other.
    assert set(case.CLEAN) == {"credential", "viewers", "viewerSetting"}
    assert set(case.OWNED_CLEAN) == {"signedOut", "helperTask", "amplifierTask", "profile", "account"}
    # The waits that the document names are the waits of the code.
    source = FIXTURE.read_text(encoding="utf-8")
    assert "def end_recorded(record, seconds=20, windows=None):" in source
    assert "def end_viewers(records, seconds=20):" in source and "result = end_recorded(record, seconds)" in source
    assert "If GitHub ends the job at its time limit, the step may not finish." in said


def test_the_workflow_and_the_fixture_name_the_same_parts_as_not_shown(case):
    heading = WORKFLOW.read_text(encoding="utf-8").split("\non:\n")[0]
    workflow = " ".join(line.removeprefix("#").strip() for line in heading[heading.index("# Not shown:"):].splitlines())
    fixture = " ".join(FIXTURE.read_text(encoding="utf-8").split('"""')[1].split("Not shown: see NOT_SHOWN below.")[1]
                       .split("The delete choice removes")[0].split())
    for text in (workflow, fixture):
        # The case runs the delete choice and the certificate step. Only a person's own answer is not shown.
        assert "delete choice" not in text and "the certificate question" not in text, text
        for part in ("the setup file itself", "a person's own answer to the Windows certificate question",
                     "the browser", "a click on a Start-menu entry", "a chat", "Windows 11",
                     "a sign-in at the PC's own screen"):
            assert part in text, part


def test_the_delete_choice_runs_only_as_the_user_that_the_run_created():
    source = FIXTURE.read_text(encoding="utf-8")
    # One call, in the phase after the second sign-in. `standard` starts every phase and refuses other accounts.
    assert source.count("delete_everything(") == 2  # Its definition and its one call.
    second = source[source.index("def second(stage, exchange, account, token):"):source.index("def delete_everything(")]
    assert "removed = delete_everything(again, root, notices)" in second
    assert source.count('"second": second') == 1 and "installation_folder()" in second
    standard = source[source.index("def standard(stage, exchange, phase):"):source.index('if __name__ == "__main__":')]
    assert standard.index("admit_standard(") < standard.index('"second": second')
    # The folder is the one of the account's own profile, never an argument.
    folder = source[source.index("def installation_folder():"):source.index("def setup_reads(")]
    assert 'return Path(os.environ["LOCALAPPDATA"]) / "Amplifier"' in folder
    assert "--root" not in source[source.index('parser = argparse.ArgumentParser'):]


NAME, THUMBPRINT = "Amplifier Unified (this PC only)", "0A1B2C3D 4E5F6071 8293A4B5 C6D7E8F9 00112233"
MARKS = [NAME, THUMBPRINT]
OTHER = "FFFFFFFF 4E5F6071 8293A4B5 C6D7E8F9 00112233"
# The two Windows questions, in the form that the first native runs recorded.
INSTALL = ("You are about to install a certificate from a certification authority (CA) claiming to represent:\r\n\r\n"
           + NAME + "\r\n\r\nThumbprint (sha1): " + THUMBPRINT + "\r\n\r\nDo you want to install this certificate?\r\n")
DELETE = ("Do you want to DELETE the following certificate from the Root Store?\r\n\r\nSubject : " + NAME
          + "\r\nIssuer : Self Issued\r\nThumbprint (sha1) : " + THUMBPRINT + "\r\n")


def dialog(text, *, title="Security Warning", buttons=None, window=100):
    return {"window": window, "pid": 1, "title": title, "text": text,
            "buttons": {6: (61, "Yes"), 7: (71, "No")} if buttons is None else buttons}


def test_a_dialog_is_the_question_only_with_the_operation_the_name_and_the_thumbprint(case):
    assert case.is_question(dialog(INSTALL), "install", MARKS)
    assert case.is_question(dialog(DELETE, title="Root Certificate Store"), "delete", MARKS)
    # Windows groups the thumbprint in its own way; spaces and case do not count.
    assert case.is_question(dialog(INSTALL.replace(THUMBPRINT, THUMBPRINT.replace(" ", "").lower())), "install", MARKS)
    # The question of the other operation.
    assert not case.is_question(dialog(INSTALL), "delete", MARKS)
    assert not case.is_question(dialog(DELETE), "install", MARKS)
    # Another certificate with the same name: every Amplifier installation has one.
    assert not case.is_question(dialog(INSTALL.replace(THUMBPRINT, OTHER)), "install", MARKS)
    # The thumbprint without the name, the name without the thumbprint, nothing.
    assert not case.is_question(dialog(INSTALL.replace(NAME, "Another Root")), "install", MARKS)
    assert not case.is_question(dialog(INSTALL.replace(THUMBPRINT, "")), "install", MARKS)
    assert not case.is_question(dialog(""), "install", MARKS)
    # A missing mark names nothing.
    assert not case.is_question(dialog(INSTALL), "install", [NAME, " "])
    assert not case.is_question(dialog(INSTALL), "install", [NAME])
    assert not case.is_question(dialog(INSTALL), "install", [THUMBPRINT])


def test_the_answer_is_the_yes_or_no_button_of_the_dialog(case):
    assert case.button_for(dialog("x"), "yes") == (6, 61) and case.button_for(dialog("x"), "no") == (7, 71)
    # A dialog with other numbers: by the button's text.
    other = dialog("x", buttons={1: (11, "Yes"), 2: (21, "No"), 9: (91, "Help")})
    assert case.button_for(other, "yes") == (1, 11) and case.button_for(other, "no") == (2, 21)
    assert case.button_for(dialog("x", buttons={1: (11, "OK")}), "yes") is None


class Posted:
    """Windows, as it takes a window message. `taken=0`: Windows does not take it."""

    def __init__(self, taken=1):
        self.messages, self.taken = [], taken

    def __call__(self, window, message, number, control):
        self.messages.append((window, message, number, control))
        return self.taken


class Report:
    def __init__(self):
        self.report, self.saved, self.failure, self.finished = {}, 0, None, False

    def save(self):
        self.saved += 1

    def failed(self, error):
        self.failure = str(error)

    def finish(self):
        self.finished = True


def answer_with(case, monkeypatch, shown, operation, answer, *, post=None, limit=90, wait=0.7, steps=None,
                look=None, during=None, first=5):
    import types
    post, ended = Posted() if post is None else post, []
    monkeypatch.setattr(case, "_user", types.SimpleNamespace(PostMessageW=post))
    monkeypatch.setattr(case, "dialogs", look or (lambda own=True: list(shown) if own else []))
    steps, asked = Report() if steps is None else steps, []
    with case.answering(steps, asked, operation, answer, MARKS, limit=limit, end=ended.append):
        deadline = time.monotonic() + first
        while not (post.messages or (asked and steps.saved) or ended) and time.monotonic() < deadline:
            time.sleep(0.05)
        if during:
            during(shown, list(post.messages), list(asked))
        time.sleep(wait)  # More looks: an answered dialog is not answered twice.
    return post.messages, asked, steps, ended


def test_the_question_about_this_certificate_is_recorded_and_answered_once(case, monkeypatch):
    messages, asked, steps, ended = answer_with(case, monkeypatch, [dialog(INSTALL)], "install", "yes")
    assert messages == [(100, 0x0111, 6, 61)] and ended == []  # One WM_COMMAND for Yes.
    assert case.one_question(asked, "yes") and not case.one_question(asked, "no")
    assert asked[0]["text"].startswith("You are about to install") and asked[0]["buttons"] == ["No", "Yes"]
    assert steps.report["windowsQuestions"] == asked
    shown = [dialog(DELETE, title="Root Certificate Store")]
    messages, asked, _, _ = answer_with(case, monkeypatch, shown, "delete", "no")
    assert messages == [(100, 0x0111, 7, 71)] and case.one_question(asked, "no")


def test_a_second_question_with_the_same_text_is_a_second_entry_and_gets_no_answer(case, monkeypatch):
    shown = [dialog(INSTALL, window=100), dialog(INSTALL, window=200)]
    messages, asked, _, _ = answer_with(case, monkeypatch, shown, "install", "yes")
    assert messages == [(100, 0x0111, 6, 61)]
    assert [(entry["window"], entry["answer"]) for entry in asked] == [(100, "yes"), (200, None)]
    assert not case.one_question(asked, "yes")


@pytest.mark.parametrize("shown, operation", [
    # A certificate with the same name and another thumbprint.
    ([dialog(INSTALL.replace(THUMBPRINT, OTHER))], "install"),
    # The question of the other operation about this certificate.
    ([dialog(DELETE, title="Root Certificate Store")], "install"),
    ([dialog(INSTALL)], "delete"),
    # A dialog that is no certificate question.
    ([dialog("Something else happened.", title="Error")], "install")])
def test_another_dialog_is_recorded_and_never_answered(case, monkeypatch, shown, operation):
    messages, asked, _, ended = answer_with(case, monkeypatch, shown, operation, "yes")
    assert messages == [] and ended == []
    assert len(asked) == 1 and asked[0]["theQuestion"] is False and asked[0]["answer"] is None
    assert not case.one_question(asked, "yes")


def test_the_question_without_the_button_for_the_answer_is_not_answered(case, monkeypatch):
    shown = [dialog(INSTALL, buttons={1: (11, "OK")})]
    messages, asked, _, _ = answer_with(case, monkeypatch, shown, "install", "yes")
    assert messages == [] and asked[0]["theQuestion"] is True and asked[0]["answer"] is None
    assert not case.one_question(asked, "yes")


def test_a_dialog_without_an_answer_fails_the_step_with_its_text_and_ends_the_program(case, monkeypatch):
    shown = [dialog("Something else happened.", title="Error")]
    messages, asked, steps, ended = answer_with(case, monkeypatch, shown, "install", "yes", limit=0.6, wait=2.5)
    assert messages == [] and ended == [1] and steps.finished is True
    assert steps.failure.startswith("A Windows dialog of this program got no answer: ")
    assert "Something else happened." in steps.failure


def test_an_answer_that_windows_does_not_take_is_not_counted(case, monkeypatch):
    post = Posted(taken=0)
    messages, asked, steps, ended = answer_with(case, monkeypatch, [dialog(INSTALL)], "install", "yes", post=post,
                                                limit=0.6, wait=2.5)
    assert messages and set(messages) == {(100, 0x0111, 6, 61)}
    assert asked[0]["answer"] is None and not case.one_question(asked, "yes")
    assert ended == [1] and steps.failure.startswith("A Windows dialog of this program got no answer: ")


def test_an_answer_that_does_not_close_the_question_fails_the_step(case, monkeypatch):
    messages, asked, steps, ended = answer_with(case, monkeypatch, [dialog(INSTALL)], "install", "yes",
                                                limit=0.6, wait=2.5)
    assert messages == [(100, 0x0111, 6, 61)] and ended == [1] and steps.finished is True
    assert steps.failure.startswith("The answer did not close the Windows question: ")


def test_a_dialog_that_stays_without_a_text_fails_the_step_at_the_limit(case, monkeypatch):
    # Windows fills a dialog a moment after it shows it. One that stays empty gets no answer and no end.
    shown = [dialog("", title="Security Warning")]
    messages, asked, steps, ended = answer_with(case, monkeypatch, shown, "install", "yes", limit=0.6, wait=2.5)
    assert messages == [] and ended == [1] and steps.finished is True
    assert steps.failure.startswith("A Windows dialog of this program got no answer: ")
    assert '"title": "Security Warning"' in steps.failure
    assert [(entry["text"], entry["answer"]) for entry in asked] == [("", None)]


def test_a_dialog_that_gets_its_text_later_is_one_entry_and_gets_the_answer(case, monkeypatch):
    shown = [dialog("One moment.", window=100)]
    messages, asked, _, ended = answer_with(case, monkeypatch, shown, "install", "yes",
                                            during=lambda shown, *_: shown.__setitem__(0, dialog(INSTALL, window=100)))
    assert messages == [(100, 0x0111, 6, 61)] and ended == []
    assert len(asked) == 1 and asked[0]["text"].startswith("You are about to install") and case.one_question(asked, "yes")


def test_a_dialog_that_is_empty_at_first_gets_the_answer_when_windows_has_filled_it(case, monkeypatch):
    shown, while_empty = [dialog("", window=100)], []

    def fill(shown, messages, asked):
        while_empty.append((messages, asked))
        shown[0] = dialog(INSTALL, window=100)

    messages, asked, _, ended = answer_with(case, monkeypatch, shown, "install", "yes", first=1.2, during=fill)
    # No answer and no entry while it is empty.
    assert while_empty == [([], [])]
    # Then one answer and one entry.
    assert messages == [(100, 0x0111, 6, 61)] and ended == []
    assert len(asked) == 1 and asked[0]["text"].startswith("You are about to install") and case.one_question(asked, "yes")


def test_the_program_ends_also_when_the_failure_cannot_be_saved(case, monkeypatch):
    class Unsaved(Report):
        def failed(self, error):
            raise OSError("The disk is full")

    shown = [dialog("Something else happened.", title="Error")]
    _, _, steps, ended = answer_with(case, monkeypatch, shown, "install", "yes", limit=0.6, wait=2.5, steps=Unsaved())
    assert ended == [1] and steps.finished is False


def test_the_program_ends_when_the_test_cannot_look_at_the_dialogs(case, monkeypatch):
    def look(own=True):
        raise RuntimeError("EnumWindows failed")

    messages, asked, steps, ended = answer_with(case, monkeypatch, [], "install", "yes", look=look, wait=1.2)
    assert messages == [] and asked == [] and ended == [1]
    assert steps.failure.startswith("The test could not look at the dialogs of this program: ")
    assert "RuntimeError: EnumWindows failed" in steps.failure


def test_one_question_means_exactly_one(case):
    yes = {"theQuestion": True, "answer": "yes"}
    assert case.one_question([yes], "yes")
    assert not case.one_question([], "yes") and not case.one_question([yes, yes], "yes")
    assert not case.one_question([{**yes, "theQuestion": False}], "yes")
    assert not case.one_question([{**yes, "answer": None}], "yes")


def test_the_steps_require_the_question_of_their_operation_and_the_thumbprint_of_amplifiers_message():
    source = FIXTURE.read_text(encoding="utf-8")
    certificate = source[source.index("def certificate(stage, exchange, account, token):"):
                         source.index("def second(stage, exchange, account, token):")]
    assert 'answering(steps, asked, "install", "yes", marks)' in certificate
    assert 'need(marks[1] in asked[0]["text"],' in certificate
    second = source[source.index("def second(stage, exchange, account, token):"):source.index("def delete_everything(")]
    assert 'answering(steps, asked, "delete", "no", marks)' in second
    assert 'answering(steps, asked, "delete", "yes", marks)' in second
    assert source.count('answering(steps, asked, "') == 3  # The three calls; each names its operation.


def test_windows_own_list_is_read_where_a_line_says_that_the_certificate_stays_or_is_removed():
    source = FIXTURE.read_text(encoding="utf-8")
    certificate = source[source.index("def certificate(stage, exchange, account, token):"):
                         source.index("def second(stage, exchange, account, token):")]
    assert certificate.index("need(windows_root_has(marks[1]) is False") < certificate.index("answering(") \
        < certificate.index("need(windows_root_has(marks[1]) is True")
    second = source[source.index("def second(stage, exchange, account, token):"):source.index("def delete_everything(")]
    # After the new sign-in, and after No at the delete question.
    assert second.count("need(windows_root_has(marks[1]) is True") == 2
    third = source[source.index("def third(stage, exchange, account, token):"):
                   source.index("def standard(stage, exchange, phase):")]
    assert 'need(windows_root_has(before["thumbprint"]) is False' in third
    # Windows PowerShell lists the store. No code of the product takes part.
    reader = source[source.index("ROOTS = "):source.index("def one_question(")]
    assert "Get-ChildItem -LiteralPath 'Cert:\\CurrentUser\\Root'" in reader and "amplifier_web" not in reader
    assert "no certificate" in source[source.index("THIRD = ("):source.index("JOURNEY = ")]


@pytest.mark.parametrize("answer, expected", [
    ({"exitCode": 0, "json": {"found": 0}}, False), ({"exitCode": 0, "json": {"found": 1}}, True),
    ({"exitCode": 0, "json": {"found": 2}}, True)])
def test_windows_own_list_holds_the_certificate_only_when_windows_finds_it(case, monkeypatch, answer, expected):
    asked = []
    monkeypatch.setattr(case, "powershell", lambda script, value, timeout=120: asked.append((script, value)) or answer)
    assert case.windows_root_has("0a1b2c3d 4E5F6071 8293A4B5 C6D7E8F9 00112233") is expected
    # Windows gets the thumbprint without spaces, in capital letters.
    assert asked == [(case.ROOTS, {"thumbprint": "0A1B2C3D4E5F60718293A4B5C6D7E8F900112233"})]


@pytest.mark.parametrize("answer", [
    {"exitCode": 1, "json": None, "stdout": "", "stderr": "Cannot find path"},
    {"exitCode": 0, "json": None, "stdout": "", "stderr": ""}])
def test_a_list_that_windows_does_not_give_is_not_read_as_no_certificate(case, monkeypatch, answer):
    monkeypatch.setattr(case, "powershell", lambda script, value, timeout=120: answer)
    with pytest.raises(AssertionError) as failed:
        case.windows_root_has(THUMBPRINT)
    assert "Windows did not list this user's root certificates" in str(failed.value)


@pytest.fixture
def sign_in_probe(case, monkeypatch, tmp_path):
    """The real settling loop and public predicates, with every Windows read replaced."""
    from amplifier_web import windows_installation as installation
    again = case.load("standard_account_observation_helpers", ROOT / "tests/fixtures/windows_start_again_native.py")
    steps = case.Steps(tmp_path / "second.json", case.SECOND)
    steps.begin(case.SECOND[0])
    root, home, urls, pin = tmp_path / "installation", tmp_path / "installation/app", ["https://127.0.0.1:443"], object()
    state = {"managed": True, "state": "running", "instances": 1, "taskPath": r"\AmplifierUnified-test",
             "lastResult": 0}
    status = {"exitCode": 0, "json": {"ok": True, "processVerified": True, "responding": True,
                                    "pid": 22, "created": "220", "token": "PRIVATE-SENTINEL"},
              "stdout": "PRIVATE-SENTINEL", "stderr": "PRIVATE-SENTINEL"}
    run = SimpleNamespace(steps=steps, root=root, home=home, urls=urls, pin=pin, status=status, state=state,
                          again=again, installation=installation, calls=[], sleeps=[], now=0.0,
                          states=["trusted"], seen=[], post_status=None, ports=[443, 443], broken=None)

    def cli(folder, *args, **kwargs):
        assert folder == root
        run.calls.append(("cli", args, kwargs))
        if args == ("service", "logs"):
            if run.broken == "logs":
                raise OSError("PRIVATE-SENTINEL")
            return {"exitCode": 0, "stdout": "PRIVATE-SENTINEL\nStarted server process [42] PRIVATE-SENTINEL\n"
                                          "Application startup complete.\n"}
        assert args in (("service", "status", "--json"), ("service", "status", "--wait", "120", "--json"))
        return run.post_status or status

    async def listener(folder):
        assert folder == home
        if run.broken == "listener":
            raise RuntimeError("PRIVATE-SENTINEL")
        return True

    def probe(targets, fingerprint):
        assert targets is urls and fingerprint is pin  # No replacement pin, URL or trust path.
        saved = json.loads(steps.path.read_text())
        assert saved["signInTrust"]["stage"] == "BEFORE PROBE" and "before" in saved["signInTrust"]
        run.seen.append(saved["signInTrust"])
        value = run.states[min(len(run.seen) - 1, len(run.states) - 1)]
        if isinstance(value, Exception):
            raise value
        return value

    def sleep(seconds):
        run.sleeps.append(seconds)
        run.now += seconds

    def forbidden(*args, **kwargs):
        pytest.fail("An observation tried to launch, sign in, or mutate certificate/trust state")

    monkeypatch.setattr(again, "cli", cli)
    monkeypatch.setattr(again, "listener_answers", listener)
    monkeypatch.setattr(again, "task_facts", lambda folder: dict(state))
    monkeypatch.setattr(again, "preparation_facts", lambda folder: {
        "releaseCount": 1, "install-failure.json": {"locals": "PRIVATE-SENTINEL"},
        "install-progress.json": ["install: started", "PRIVATE-SENTINEL", "qualify: passed"]})
    monkeypatch.setattr(installation, "saved_port", lambda folder: run.ports.pop(0))
    monkeypatch.setattr(installation, "trust_state", probe)
    monkeypatch.setattr(case.time, "monotonic", lambda: run.now)
    monkeypatch.setattr(case.time, "sleep", sleep)
    for owner, name in ((again, "launcher"), (case, "sign_in"), (case, "certificate"),
                        (installation, "review_browser_trust"), (installation, "_export_certificate"),
                        (installation, "owner_notice")):
        monkeypatch.setattr(owner, name, forbidden)

    def observe():
        try:
            return case.sign_in_trust(steps, again, root, home, urls, pin, status, state)
        except Exception as error:
            steps.failed(error)
            raise
        finally:
            assert installation.trust_state is probe

    run.observe = observe
    return run


def test_second_sign_in_settles_only_unreachable_with_the_maintained_defaults(sign_in_probe):
    run = sign_in_probe
    run.states = ["unreachable", "unreachable", "trusted"]
    # A host can restart during observation; retain both identities, not a claimed cause.
    run.post_status = {"exitCode": 0, "json": {**run.status["json"], "pid": 33, "created": "330"}}
    assert run.observe() == "trusted"
    observation = json.loads(run.steps.path.read_text())["signInTrust"]
    assert observation["probes"] == [{"seconds": 0.0, "state": "unreachable"},
                                     {"seconds": 1.0, "state": "unreachable"},
                                     {"seconds": 2.0, "state": "trusted"}]
    assert run.sleeps == [1.0, 1.0] and observation["seconds"] == 2.0
    assert observation["stage"] == "AFTER PROBE" and observation["hostChanged"] is True
    assert observation["before"]["host"] == [22, "220"] and observation["after"]["host"] == [33, "330"]
    for side in ("before", "after"):
        facts = observation[side]
        assert facts["serviceStatus"]["json"]["processVerified"] is True
        assert facts["task"] == run.state and facts["savedPort"] == 443 and facts["listenerAnswers"] is True
        assert facts["captureErrors"] == []
    # Two bounded snapshots, no polling of diagnostics inside the TLS loop.
    assert [args for _, args, _ in run.calls] == [
        ("service", "logs"), ("service", "status", "--json"), ("service", "logs")]


@pytest.mark.parametrize("states, seconds, probes, message", [
    (["untrusted", "trusted"], 0, 1, "Windows returned an untrusted connection"),
    (["unreachable", "untrusted", "trusted"], 1, 2, "Windows returned an untrusted connection"),
    (["unreachable"], 60, 61, "listener stayed unreachable during the bounded wait; trust was not established"),
])
def test_second_sign_in_trust_failures_keep_pre_post_and_never_retry_untrusted(sign_in_probe, states, seconds, probes, message):
    run = sign_in_probe
    run.states = states
    with pytest.raises(AssertionError, match=message):
        run.observe()
    report = json.loads(run.steps.path.read_text())
    observation = report["signInTrust"]
    assert observation["stage"] == "FAIL" and observation["seconds"] == seconds
    assert len(observation["probes"]) == probes and run.sleeps == [1.0] * seconds
    assert observation["before"]["host"] == observation["after"]["host"] == [22, "220"]
    assert report["steps"][0]["status"] == "FAIL" and message in report["steps"][0]["reason"]
    assert all(step["reason"] == "Not reached" for step in report["steps"][1:])


def test_second_sign_in_probe_error_restores_the_reader_and_saves_failure_observations(sign_in_probe):
    run = sign_in_probe
    run.states = [OSError("PRIVATE-SENTINEL")]
    with pytest.raises(RuntimeError, match="TLS trust probe or its evidence capture failed: OSError"):
        run.observe()
    observation = json.loads(run.steps.path.read_text())["signInTrust"]
    assert observation["probeErrorType"] == "OSError" and observation["stage"] == "FAIL"
    assert observation["before"]["host"] == observation["after"]["host"] == [22, "220"]
    assert run.sleeps == [] and "PRIVATE-SENTINEL" not in run.steps.path.read_text()


@pytest.mark.parametrize("broken", ["logs", "listener"])
def test_second_sign_in_evidence_capture_error_is_explicit_and_not_a_pass(sign_in_probe, broken):
    run = sign_in_probe
    run.broken = broken
    with pytest.raises(AssertionError, match="Before-probe evidence capture failed"):
        run.observe()
    observation = json.loads(run.steps.path.read_text())["signInTrust"]
    assert observation["before"]["captureErrors"] and observation["probes"] == [] and observation["stage"] == "FAIL"
    assert run.seen == [] and run.sleeps == []
    assert "PRIVATE-SENTINEL" not in run.steps.path.read_text()


def test_second_sign_in_post_probe_evidence_error_cannot_turn_trusted_into_a_pass(sign_in_probe, monkeypatch):
    run = sign_in_probe
    cli = run.again.cli
    reads = []

    def read(root, *args, **kwargs):
        if args == ("service", "logs"):
            reads.append(args)
            if len(reads) == 2:
                raise OSError("PRIVATE-SENTINEL")
        return cli(root, *args, **kwargs)

    monkeypatch.setattr(run.again, "cli", read)
    with pytest.raises(AssertionError, match="After-probe evidence capture failed"):
        run.observe()
    observation = json.loads(run.steps.path.read_text())["signInTrust"]
    assert observation["stage"] == "FAIL" and observation["state"] == "trusted"
    assert observation["before"]["captureErrors"] == []
    assert observation["after"]["captureErrors"] == [{"part": "taskOutputTail", "errorType": "OSError"}]
    assert observation["before"]["host"] == observation["after"]["host"] == [22, "220"]
    assert "PRIVATE-SENTINEL" not in run.steps.path.read_text()


def test_second_sign_in_observations_omit_private_fields_messages_and_raw_tails(sign_in_probe):
    run = sign_in_probe
    assert run.observe() == "trusted"
    text = run.steps.path.read_text()
    assert "PRIVATE-SENTINEL" not in text and '"token"' not in text and '"locals"' not in text
    facts = json.loads(text)["signInTrust"]["before"]
    assert facts["preparation"] == {"releaseCount": 1, "failureRecorded": True,
                                     "phaseTail": ["install: started", "[phase omitted]", "qualify: passed"]}
    assert facts["taskOutputTail"] == {
        "linesExamined": 3, "rawTextOmitted": True, "markers": [
            {"tailLine": 1, "marker": "Started server process"},
            {"tailLine": 2, "marker": "Application startup complete."}]}


@pytest.mark.parametrize("change, message", [
    ("token", "not a standard user"),
    ("same-host", "not a new host on the same port"),
    ("port", "not a new host on the same port"),
    ("post-host", "After the TLS probe the host is not a new host on the same port"),
    ("post-port", "After the TLS probe the host is not a new host on the same port"),
    ("post-ready", "no longer has a verified responsive host"),
    ("root", "does not list the certificate among this user's root certificates"),
])
def test_second_sign_in_keeps_standard_user_identity_port_readiness_and_root_oracles(
        case, monkeypatch, tmp_path, sign_in_probe, change, message):
    import amplifier_web
    run = sign_in_probe
    for name in ("windows_consumer", "windows_consumer_shell", "windows_uninstall"):
        monkeypatch.setattr(amplifier_web, name, SimpleNamespace(
            environment=lambda *args: {}, installation=lambda root: (None, {})), raising=False)
    monkeypatch.setattr(case, "installation_folder", lambda: run.root)
    monkeypatch.setattr(case, "product", lambda stage, root: (run.home, run.again))
    monkeypatch.setattr(case, "trust_targets", lambda home: (run.urls, run.pin))
    monkeypatch.setattr(case, "certificate_marks", lambda home: MARKS)
    monkeypatch.setattr(case, "windows_root_has", lambda thumbprint: False)
    # The record beside the steps is not the subject here. Its watch would read the saved port once more and ask
    # the stand-in for the trust read from a thread of its own: here it never starts. After a failed step the
    # record is kept as in the case, without its wait of 20 seconds, so that `run.seen` below also counts a trust
    # read that the record would make again.
    monkeypatch.setattr(case, "watch_listener", lambda home: case.ListenerWatch())
    keep = case.keep_after_failure
    monkeypatch.setattr(case, "keep_after_failure", lambda steps, watch: keep(steps, watch, wait=lambda seconds: None))
    before = {"hostBeforeSignOut": [22, "220"] if change == "same-host" else [11, "110"], "port": 443}
    (tmp_path / "journey.json").write_text(json.dumps(before))
    if change == "port":
        run.ports = [444]
    elif change == "post-port":
        run.ports = [443, 443, 444]  # Original oracle, pre snapshot, post snapshot.
    else:
        run.ports = [443] * 3
    if change == "post-host":
        run.post_status = {"exitCode": 0, "json": {**run.status["json"], "pid": 11, "created": "110"}}
    elif change == "post-ready":
        run.post_status = {"exitCode": 1, "json": {**run.status["json"], "ok": False}}
    # The initial --wait read is the original ready host, not the post-probe observation.
    cli = run.again.cli
    monkeypatch.setattr(run.again, "cli", lambda root, *args, **kwargs:
                        run.status if "--wait" in args else cli(root, *args, **kwargs))
    token = {**STANDARD, "memberOfAdministrators": True} if change == "token" else STANDARD
    assert case.second(tmp_path, tmp_path, ACCOUNT, token) is False
    report = json.loads((tmp_path / "second.json").read_text())
    assert message in report["steps"][0]["reason"]
    assert report["finished"] is True and all(step["reason"] == "Not reached" for step in report["steps"][1:])
    if change in ("token", "same-host", "port"):
        assert run.seen == []  # No retry can erase the original identity/port refusal.


@pytest.mark.parametrize("states, probes, said, kept", [
    (["trusted"], 1, "does not list the certificate among this user's root certificates",
     {"answer": "trusted", "asRequired": True}),
    (["unreachable"], 61, "the TLS listener stayed unreachable during the bounded wait", {"failed": "AssertionError"}),
])
def test_the_record_keeps_the_trust_read_after_the_sign_in_and_never_makes_it_a_second_time(
        case, monkeypatch, tmp_path, sign_in_probe, states, probes, said, kept):
    import amplifier_web
    run = sign_in_probe
    for name in ("windows_consumer", "windows_consumer_shell", "windows_uninstall"):
        monkeypatch.setattr(amplifier_web, name, SimpleNamespace(
            environment=lambda *args: {}, installation=lambda root: (None, {})), raising=False)
    monkeypatch.setattr(case, "installation_folder", lambda: run.root)
    monkeypatch.setattr(case, "product", lambda stage, root: (run.home, run.again))
    monkeypatch.setattr(case, "trust_targets", lambda home: (run.urls, run.pin))
    monkeypatch.setattr(case, "certificate_marks", lambda home: MARKS)
    # After a trust read that gives its answer, the step fails at this condition (the first parameter). In the
    # second parameter the trust read itself ends with the step's error.
    monkeypatch.setattr(case, "windows_root_has", lambda thumbprint: False)
    # The watch has its own cases. Here it never starts: its thread would ask the stand-in for the trust read.
    monkeypatch.setattr(case, "watch_listener", lambda home: case.ListenerWatch())
    waits, keep = [], case.keep_after_failure
    monkeypatch.setattr(case, "keep_after_failure", lambda steps, watch: keep(steps, watch, wait=waits.append))
    (tmp_path / "journey.json").write_text(json.dumps({"hostBeforeSignOut": [11, "110"], "port": 443}))
    run.ports, run.states = [443] * 3, states
    cli = run.again.cli
    monkeypatch.setattr(run.again, "cli", lambda root, *args, **kwargs:
                        run.status if "--wait" in args else cli(root, *args, **kwargs))
    assert case.second(tmp_path, tmp_path, ACCOUNT, STANDARD) is False
    report = json.loads((tmp_path / "second.json").read_text())
    # The step has the text of its own condition, or of the trust read: the record changes neither.
    assert said in report["steps"][0]["reason"] and report["finished"] is True
    reads = report["reads"]
    assert [read["read"] for read in reads] == [
        "the app's status after the sign-in", "Task Scheduler after the sign-in", "trust after the sign-in"]
    assert {name: reads[2].get(name) for name in kept} == kept and isinstance(reads[2]["seconds"], float)
    # The trust read kept its own observation, once. After the failed step the record waits its 20 seconds and
    # makes no read of trust again: a second one would put another observation in the place of the first.
    assert len(report["signInTrust"]["probes"]) == len(run.seen) == probes
    assert waits == [20] and all("later" not in read for read in reads)
    assert len([call for call in run.calls if call[1] == ("service", "logs")]) == 2  # Before and after, once each.


@pytest.mark.parametrize("created", ["134394376612506112", "18446744073709551615"])
def test_second_sign_in_preserves_exact_decimal_string_creation_identities(sign_in_probe, created):
    run = sign_in_probe
    run.status["json"]["created"] = created
    # A one-tick change must survive JSON and comparison, without float/numeric normalization.
    run.post_status = {"exitCode": 0, "json": {**run.status["json"], "created": "134394376612506113"}}
    assert run.observe() == "trusted"
    observation = json.loads(run.steps.path.read_text())["signInTrust"]
    assert observation["before"]["host"] == [22, created]
    assert observation["after"]["host"] == [22, "134394376612506113"]
    assert observation["before"]["serviceStatus"]["json"]["created"] == created
    assert observation["hostChanged"] is True
    assert observation["before"]["captureErrors"] == observation["after"]["captureErrors"] == []


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("change", [
    {}, {"created": None}, {"created": ""}, {"created": 134394376612506112}, {"created": True},
    {"created": "0"}, {"created": "0220"}, {"created": "-220"}, {"created": "220.0"},
    {"created": "２２０"}, {"created": "220\n"}, {"created": "18446744073709551616"},
    {"created": "1" * 21}, {"created": "PRIVATE-SENTINEL"},
])
def test_second_sign_in_missing_or_invalid_creation_identity_is_a_capture_failure(sign_in_probe, side, change):
    run = sign_in_probe
    value = {key: item for key, item in run.status["json"].items() if key != "created"}
    value.update(change)
    if side == "before":
        run.status["json"] = value
    else:
        run.post_status = {"exitCode": 0, "json": value}
    with pytest.raises(AssertionError, match=("Before" if side == "before" else "After") + "-probe evidence capture failed"):
        run.observe()
    observation = json.loads(run.steps.path.read_text())["signInTrust"]
    assert observation["stage"] == "FAIL"
    assert observation[side]["captureErrors"] == [{"part": "host", "errorType": "AssertionError"}]
    assert observation[side]["host"] is None
    assert "created" not in observation[side]["serviceStatus"]["json"]
    assert len(run.seen) == (0 if side == "before" else 1)
    assert "PRIVATE-SENTINEL" not in run.steps.path.read_text()


def test_second_sign_in_log_snapshot_accepts_the_real_public_helpers_plain_text_shape(
        case, sign_in_probe, monkeypatch, capsys):
    from amplifier_web import windows_service, windows_task_runtime
    run = sign_in_probe
    text = "Started server process [42] PRIVATE-SENTINEL\nApplication startup complete.\n"
    spec = object()
    monkeypatch.setattr(windows_service, "_read", lambda home: ({}, spec))
    monkeypatch.setattr(windows_task_runtime, "read_log", lambda actual: text if actual is spec else pytest.fail("wrong spec"))
    windows_service.command("logs", run.home)  # No Windows call: task read and log read are replaced.
    output = capsys.readouterr().out
    assert output == text

    helper = case.load("standard_account_log_shape_helper", ROOT / "tests/fixtures/windows_start_again_native.py")
    calls = []

    def completed(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(helper.subprocess, "run", completed)
    result = helper.cli(run.root, "service", "logs", timeout=30)
    assert result["exitCode"] == 0 and result["json"] is None and result["stdout"] == text
    assert calls[0][0] == list(map(str, [
        run.root / "tools/amplifier-unified/Scripts/amplifier-unified.exe",
        "--data-dir", run.home, "--workspace", run.root / "workspace", "service", "logs"]))
    assert calls[0][1]["timeout"] == 30 and calls[0][1]["cwd"] == run.root
    monkeypatch.setattr(run.again, "cli", helper.cli)
    facts = case.sign_in_snapshot(run.again, run.root, run.home, status=run.status, state=run.state)
    assert facts["captureErrors"] == []
    assert facts["taskOutputTail"]["markers"] == [
        {"tailLine": 0, "marker": "Started server process"},
        {"tailLine": 1, "marker": "Application startup complete."}]
    assert "PRIVATE-SENTINEL" not in json.dumps(facts)
