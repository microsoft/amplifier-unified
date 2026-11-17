"""The standard-account case: its guard, its account test, its lines and its workflow.

The case itself runs only on a GitHub-hosted Windows runner
(`.github/workflows/windows-standard-account.yml`). These checks run
everywhere. They keep the parts that decide what the case may claim: who
counts as a standard user, which step a failure belongs to, and what the
workflow can never do silently.
"""
from __future__ import annotations

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


def admit(case, record=RECORD, token=USER, owner=ADMINISTRATORS, writable=False):
    return case.admit_standard(record, token, owner, writable, "win32", SUPPORTED)


def test_the_standard_user_of_a_hosted_run_is_admitted(case):
    assert admit(case) is None


@pytest.mark.parametrize("change, text", [
    # Another account than the one that the run created, or an administrator.
    ({"token": {**USER, "sid": "S-1-5-21-1-2-3-1004"}}, "only as the standard user"),
    ({"token": {**USER, "memberOfAdministrators": True}}, "only as the standard user"),
    ({"record": {"hosted": HOSTED}}, "only as the standard user"),
    # A record that the user made, that another account made, or that the user can change.
    ({"owner": USER["sid"]}, "administrators group"),
    ({"owner": "S-1-5-21-1-2-3-500"}, "administrators group"),
    ({"writable": True}, "administrators group"),
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
    assert "admit_standard(account, token, file_owner(record), can_write(record))" in standard
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


def test_a_file_that_this_account_can_open_for_writing_is_read_as_such_and_stays_unchanged(case, tmp_path):
    record = tmp_path / "account.json"
    record.write_text("{}", encoding="utf-8")
    assert case.can_write(record) is True and record.read_text(encoding="utf-8") == "{}"
    assert case.can_write(tmp_path / "absent.json") is False and not (tmp_path / "absent.json").exists()


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


OWNED = {"user": "amp-std-000000", "mark": "mark-1", "sid": "S-1-5-21-1-2-3-1003"}
AMPLIFIER_TASK = "AmplifierUnified-" + OWNED["sid"]


def clean_up(case, monkeypatch, tmp_path, *, owned=OWNED, comment="mark-1", broken=(), profile="absent"):
    """Run the cleanup with stand-ins for Windows. `broken` names the parts that fail."""
    calls, files, exists = [], {"ownership.json": owned}, {"account": comment is not None}

    def fail(name):
        if name in broken:
            raise OSError(name + " failed")

    def delete_user(name):
        fail("account")
        calls.append("delete_user")
        exists["account"] = False
        return 0

    monkeypatch.setattr(case, "saved", files.get)
    monkeypatch.setattr(case, "save", files.__setitem__)
    monkeypatch.setattr(case, "system", lambda name: name)
    monkeypatch.setattr(case, "folders", lambda: (tmp_path, tmp_path))
    monkeypatch.setattr(case, "execute", lambda argv, **_: {"exitCode": 0})
    monkeypatch.setattr(case, "forget", lambda: fail("credential") or "absent")
    monkeypatch.setattr(case, "account_comment", lambda name: comment if exists["account"] else None)
    monkeypatch.setattr(case, "account_sid", lambda name: b"sid" if exists["account"] else None)
    monkeypatch.setattr(case, "sid_text", lambda sid: OWNED["sid"])
    monkeypatch.setattr(case, "sign_out", lambda name, viewer: fail("signedOut") or calls.append("sign_out") or True)
    monkeypatch.setattr(case, "task", lambda op, name, **_: fail(name) or calls.append(op + " " + name)
                        or {"exists": False})
    monkeypatch.setattr(case, "remove_profile", lambda sid: fail("profile") or calls.append("profile") or profile)
    monkeypatch.setattr(case, "delete_user", delete_user)
    return case.cleanup(), files["cleanup.json"], calls


def test_the_cleanup_passes_when_nothing_of_the_run_is_left(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path)
    assert passed is True
    assert {name: result[name] for name in ("credential", "signedOut", "helperTask", "amplifierTask", "profile",
                                            "account", "viewerSetting")} == {
        "credential": "absent", "signedOut": True, "helperTask": "absent", "amplifierTask": "absent",
        "profile": "absent", "account": "absent", "viewerSetting": "unchanged"}
    assert calls == ["sign_out", "remove AmplifierStandardAccountCheck", "remove " + AMPLIFIER_TASK, "profile",
                     "delete_user"]


@pytest.mark.parametrize("part, label", [
    ("credential", "credential"), ("signedOut", "signedOut"), ("AmplifierStandardAccountCheck", "helperTask"),
    (AMPLIFIER_TASK, "amplifierTask"), ("profile", "profile")])
def test_a_part_that_fails_does_not_stop_the_other_parts_and_fails_the_cleanup(case, monkeypatch, tmp_path, part, label):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, broken=(part,))
    assert passed is False
    # The account is removed all the same, and the result names the one part that is left.
    assert "delete_user" in calls and result["account"] == "absent" and result["viewerSetting"] == "unchanged"
    assert [name for name, value in result.items() if str(value).startswith("left: OSError")] == [label]


def test_a_profile_that_windows_keeps_fails_the_cleanup(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, profile="left: in use")
    assert passed is False and result["profile"] == "left: in use"
    assert "delete_user" in calls and result["account"] == "absent"


def test_an_account_that_windows_does_not_delete_fails_the_cleanup(case, monkeypatch, tmp_path):
    passed, result, _ = clean_up(case, monkeypatch, tmp_path, broken=("account",))
    assert passed is False and result["account"] == "left" and result["deleteStatus"].startswith("left: OSError")


def test_an_account_whose_sid_was_not_recorded_is_removed_by_its_mark(case, monkeypatch, tmp_path):
    # Windows created the account, and the line that records its SID did not run.
    owned = {"user": OWNED["user"], "mark": "mark-1"}
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=owned)
    assert passed is True and result["accountBefore"] == "this run's"
    assert "delete_user" in calls and "remove " + AMPLIFIER_TASK in calls and "profile" in calls


def test_an_account_that_was_recorded_and_never_created_is_nothing_to_delete(case, monkeypatch, tmp_path):
    owned = {"user": OWNED["user"], "mark": "mark-1"}
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=owned, comment=None)
    assert passed is True and result["accountBefore"] == "absent"
    assert calls == ["sign_out", "remove AmplifierStandardAccountCheck"]


def test_an_account_of_another_program_under_this_name_is_not_touched(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, comment="A person's own account")
    assert passed is False and calls == []
    assert result["accountBefore"] == "another account" and result["account"] == "left: another account"


def test_no_record_means_that_no_account_was_requested(case, monkeypatch, tmp_path):
    passed, result, calls = clean_up(case, monkeypatch, tmp_path, owned=None)
    assert passed is True and calls == [] and "account" not in result
    assert result["credential"] == "absent" and result["viewerSetting"] == "unchanged"


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


def test_clean_means_every_part(case):
    done = {"credential": "absent", "viewerSetting": "restored", "signedOut": True, "helperTask": "absent",
            "amplifierTask": "absent", "profile": "absent", "account": "absent"}
    assert case.cleaned(done, OWNED) and case.cleaned({"credential": "absent", "viewerSetting": "unchanged"}, None)
    for name in done:
        assert not case.cleaned({**done, name: "left: something"}, OWNED), name
        assert not case.cleaned({key: value for key, value in done.items() if key != name}, OWNED), name
    assert not case.cleaned({"credential": "left: something", "viewerSetting": "unchanged"}, None)
    assert not case.cleaned({"credential": "absent"}, None)


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
                  "the step fails when one of them is left", "names the certificate and shows the thumbprint",
                  "Windows' own list of the user's root certificates no longer holds the certificate",
                  "the Windows version or the processor"):
        assert claim in said, claim
    # The cleanup's promise is what its code requires, and no more.
    assert "its profile and the account in every case" not in said
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


def answer_with(case, monkeypatch, shown, operation, answer, *, post=None, limit=90, wait=0.7):
    import types
    post, ended = Posted() if post is None else post, []
    monkeypatch.setattr(case, "_user", types.SimpleNamespace(PostMessageW=post))
    monkeypatch.setattr(case, "dialogs", lambda own=True: list(shown) if own else [])
    steps, asked = Report(), []
    with case.answering(steps, asked, operation, answer, MARKS, limit=limit, end=ended.append):
        deadline = time.monotonic() + 5
        while not (post.messages or (asked and steps.saved) or ended) and time.monotonic() < deadline:
            time.sleep(0.05)
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
