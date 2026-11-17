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
import re
import sys

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


def test_the_fixture_runs_on_a_hosted_runner_with_its_opt_in(case):
    if sys.version_info[:2] == (3, 13) and sys.maxsize > 2 ** 32:
        case.require_disposable_runner(dict(HOSTED), "win32")


@pytest.mark.parametrize("change", [
    {"GITHUB_ACTIONS": None}, {"RUNNER_ENVIRONMENT": "self-hosted"}, {"RUNNER_ENVIRONMENT": None},
    {"AMPLIFIER_CREATE_A_STANDARD_USER_ON_THIS_RUNNER": None},
    {"AMPLIFIER_CREATE_A_STANDARD_USER_ON_THIS_RUNNER": "true"}, {"platform": "linux"}])
def test_the_fixture_refuses_to_create_a_user_anywhere_else(case, change):
    environment = {key: value for key, value in {**HOSTED, **change}.items()
                   if value is not None and key != "platform"}
    with pytest.raises(SystemExit) as refused:
        case.require_disposable_runner(environment, change.get("platform", "win32"))
    assert "creates a Windows user" in str(refused.value)


def test_only_the_three_operations_of_the_runner_account_pass_the_guard(case):
    # `standard` runs as the new user, which has no workflow environment. It
    # has its own refusal: only the account that this run created.
    source = FIXTURE.read_text(encoding="utf-8")
    entry = source[source.index('if __name__ == "__main__":'):]
    assert entry.index('if args.operation == "standard":') < entry.index("require_disposable_runner()") \
        < entry.index("globals()[args.operation]()")
    standard = source[source.index("def standard(stage, exchange, phase):"):source.index('if __name__ == "__main__":')]
    assert 'token["sid"] != account["sid"] or token["memberOfAdministrators"]' in standard
    assert "Refused: this program runs only as the standard user that this run created." in standard


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
    run = source[source.index("def run():"):source.index("def cleanup():")]
    # Made once, in the process that creates the user.
    assert source.count("new_password()") == 2 and run.count("password = new_password()") == 1
    # Its only uses: Windows creates the user with it, and each sign-in gets it.
    uses = re.findall(r"[^\n]*\bpassword\b[^\n]*", run)
    assert [line.strip() for line in uses] == [
        "password = new_password()", "sid = create_user(name, password)",
        "viewer, session = sign_in(name, password)", "viewer, session = sign_in(name, password)", "del password"]
    sign_in = source[source.index("def sign_in(name, password):"):source.index("def sign_out(name, viewer):")]
    assert [line.strip() for line in re.findall(r"[^\n]*\bpassword\b[^\n]*", sign_in)] == [
        "def sign_in(name, password):", 'remember(computer + "\\\\" + name, password)']
    # The sign-in keeps it in memory of the runner's own logon only, and forgets it in every case.
    remember = source[source.index("def remember(user, password):"):source.index("def forget():")]
    assert "1, 0, None, None, user)" in remember and "CRED_PERSIST_SESSION" in remember
    assert sign_in.rstrip().endswith("finally:\n        forget()")
    for name in ("execute(", "save(", "print(", "json.dumps(", "write_text(", "environ"):
        for line in re.findall(r"[^\n]*" + re.escape(name) + r"[^\n]*", source):
            assert "password" not in line, line


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


def test_the_case_fails_when_a_step_fails_and_cleanup_fails_when_the_account_is_left():
    source = FIXTURE.read_text(encoding="utf-8")
    run = source[source.index("def run():"):source.index("def cleanup():")]
    # The run passes only at its last statement, after every step of both sign-ins passed.
    assert run.count('report.update(status="PASS"') == 1
    assert run.index('"A step after the first sign-in failed"') < run.index('"A step after the second sign-in failed"') \
        < run.index('"The check after the delete choice failed"') < run.index('report.update(status="PASS"')
    assert 'return report["status"] == "PASS"' in run
    cleanup = source[source.index("def cleanup():"):source.index("# ---- As the standard user")]
    assert ('return not owned or (result["account"] == "absent" and result["amplifierTask"] == "absent"\n'
            '                         and result["helperTask"] == "absent")') in cleanup
    # The account is recorded for removal directly after Windows created it.
    assert re.search(r"sid = create_user\(name, password\)\n\s+#[^\n]*\n\s+save\(\"ownership\.json\"", run)


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
                  "no installation folder, no sign-in task, no Start-menu entry and no Apps-list entry"):
        assert claim in said, claim


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
    assert standard.index("Refused: this program runs only as the standard user") < standard.index('"second": second')
    # The folder is the one of the account's own profile, never an argument.
    folder = source[source.index("def installation_folder():"):source.index("def setup_reads(")]
    assert 'return Path(os.environ["LOCALAPPDATA"]) / "Amplifier"' in folder
    assert "--root" not in source[source.index('parser = argparse.ArgumentParser'):]


NAME, THUMBPRINT = "Amplifier Unified (this PC only)", "0A1B2C3D 4E5F6071 8293A4B5 C6D7E8F9 00112233"
MARKS = [NAME, THUMBPRINT]


def dialog(text, *, title="Security Warning", buttons=None, window=100):
    return {"window": window, "pid": 1, "title": title, "text": text,
            "buttons": {6: (61, "Yes"), 7: (71, "No")} if buttons is None else buttons}


def test_a_dialog_is_this_certificates_question_only_by_its_name_or_thumbprint(case):
    assert case.names_certificate(dialog("You are about to install a certificate ... " + NAME), MARKS)
    # Windows groups the thumbprint in its own way; spaces and case do not count.
    assert case.names_certificate(dialog("Thumbprint (sha1): 0a1b2c3d4e5f60718293a4b5c6d7e8f900112233"), MARKS)
    assert case.names_certificate(dialog("Subject : Amplifier  Unified (this PC only)"), MARKS)
    assert not case.names_certificate(dialog("You are about to install a certificate from Another Root"), MARKS)
    assert not case.names_certificate(dialog("Thumbprint (sha1): FFFFFFFF 4E5F6071 8293A4B5 C6D7E8F9 00112233"), MARKS)
    assert not case.names_certificate(dialog(""), MARKS)
    assert not case.names_certificate(dialog("anything"), ["", "  "])  # An empty mark names nothing.


def test_the_answer_is_the_yes_or_no_button_of_the_dialog(case):
    assert case.button_for(dialog("x"), "yes") == (6, 61) and case.button_for(dialog("x"), "no") == (7, 71)
    # A dialog with other numbers: by the button's text.
    other = dialog("x", buttons={1: (11, "Yes"), 2: (21, "No"), 9: (91, "Help")})
    assert case.button_for(other, "yes") == (1, 11) and case.button_for(other, "no") == (2, 21)
    assert case.button_for(dialog("x", buttons={1: (11, "OK")}), "yes") is None


class Posted:
    def __init__(self):
        self.messages = []

    def __call__(self, window, message, number, control):
        self.messages.append((window, message, number, control))
        return 1


class Report:
    def __init__(self):
        self.report, self.saved = {}, 0

    def save(self):
        self.saved += 1


def answer_with(case, monkeypatch, shown, answer):
    import types
    post = Posted()
    monkeypatch.setattr(case, "_user", types.SimpleNamespace(PostMessageW=post))
    monkeypatch.setattr(case, "dialogs", lambda own=True: list(shown) if own else [])
    steps, asked = Report(), []
    with case.answering(steps, asked, answer, MARKS):
        import time
        deadline = time.monotonic() + 5
        while not (post.messages or (asked and steps.saved)) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.7)  # One more look: an answered dialog is not answered twice.
    return post.messages, asked, steps


def test_the_question_about_this_certificate_is_recorded_and_answered_once(case, monkeypatch):
    shown = [dialog("You are about to install a certificate claiming to represent: " + NAME + "\nThumbprint (sha1): "
                    + THUMBPRINT)]
    messages, asked, steps = answer_with(case, monkeypatch, shown, "yes")
    assert messages == [(100, 0x0111, 6, 61)]  # One WM_COMMAND for Yes.
    assert case.one_question(asked, "yes") and not case.one_question(asked, "no")
    assert asked[0]["text"].startswith("You are about to install") and asked[0]["buttons"] == ["No", "Yes"]
    assert steps.report["windowsQuestions"] == asked
    messages, asked, _ = answer_with(case, monkeypatch, shown, "no")
    assert messages == [(100, 0x0111, 7, 71)] and case.one_question(asked, "no")


def test_another_dialog_is_recorded_and_never_answered(case, monkeypatch):
    shown = [dialog("You are about to install a certificate claiming to represent: Another Root")]
    messages, asked, _ = answer_with(case, monkeypatch, shown, "yes")
    assert messages == []
    assert len(asked) == 1 and asked[0]["namesThisCertificate"] is False and asked[0]["answer"] is None
    assert not case.one_question(asked, "yes")
    # This certificate's question without the button for the answer is not answered either.
    shown = [dialog(NAME, buttons={1: (11, "OK")})]
    messages, asked, _ = answer_with(case, monkeypatch, shown, "yes")
    assert messages == [] and asked[0]["answer"] is None and not case.one_question(asked, "yes")


def test_one_question_means_exactly_one(case):
    yes = {"namesThisCertificate": True, "answer": "yes"}
    assert case.one_question([yes], "yes")
    assert not case.one_question([], "yes") and not case.one_question([yes, yes], "yes")
    assert not case.one_question([{**yes, "namesThisCertificate": False}], "yes")
