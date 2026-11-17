"""The standard-account case: its guard, its account test, its lines and its workflow.

The case itself runs only on a GitHub-hosted Windows runner
(`.github/workflows/windows-standard-account.yml`). These checks run
everywhere. They keep the parts that decide what the case may claim: who
counts as a standard user, which step a failure belongs to, and what the
workflow can never do silently.
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


def test_a_step_passes_only_when_it_was_stated(case, tmp_path):
    steps = case.Steps(tmp_path / "report.json", ("one", "two"))
    steps.begin("one")
    steps.passed("one")
    assert steps.finish() is False  # "two" was never reached.
    assert json.loads((tmp_path / "report.json").read_text())["steps"][1]["reason"] == "Not reached"


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
    job, = workflow["jobs"].values()
    assert job["runs-on"] == "windows-latest" and "if" not in job and "continue-on-error" not in job
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
                 "tests/fixtures/windows_start_again_native.py", "tests/windows/native_setup.Tests.cs",
                 "scripts/windows_bootstrap_native.cs", "scripts/install.ps1", "amplifier_web/windows_consumer.py",
                 "amplifier_web/windows_consumer_shell.py", "amplifier_web/windows_installation.py",
                 "amplifier_web/windows_service.py", "amplifier_web/windows_task_runtime.py",
                 "amplifier_web/windows_files.py", "amplifier_web/windows_uninstall.py"):
        assert path in paths and (ROOT / path).is_file(), path


def test_the_jobs_time_limit_holds_every_step_and_the_run_step_holds_its_phases():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    job, = workflow["jobs"].values()
    limits = [int(step["timeout-minutes"]) for step in job["steps"]]
    # The cleanup step is not cut short when every step before it used its whole limit.
    assert int(job["timeout-minutes"]) >= sum(limits) + 5
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
