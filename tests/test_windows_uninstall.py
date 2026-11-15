"""The delete choice of the Windows uninstall: portable order and rules, and the real thing on native Windows."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time

import pytest

from amplifier_web import windows_removal as removal, windows_uninstall as plan

from test_windows_consumer_shell import model, shell, uninstaller  # noqa: F401  (model and uninstaller are fixtures)
from test_windows_removal import file, folder, link, opened  # noqa: F401  (opened is a fixture)

REPOSITORY = Path(__file__).resolve().parents[1]


def test_holding_tells_an_empty_folder_in_use_from_an_entry_that_still_holds_something(opened):
    outside = folder(precious=file())
    root = folder(workspace=folder(_held=True), app=folder(_held=True, chat=file(held=True)), shared=folder(),
                  keys=file(), junction=link(outside), APP=folder(other=file()))
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        # What uninstall does: delete, then ask what of the data is still there.
        result = owned.remove(["workspace", "app"])
        assert result["left"] == ["workspace", "app\\chat"] and result["leftCount"] == 2
        tree.opened.clear()
        held = owned.holding(["workspace", "app", "shared", "keys", "junction", "absent"])
    # The empty folder that a program keeps open is not data. Exact names only.
    assert held == ["app", "junction", "keys"]
    assert sorted(root["children"]) == ["APP", "app", "junction", "keys", "shared", "workspace"]
    # Only real folders were opened, for listing and never for deletion; the link was not opened at all.
    assert sorted(tree.opened) == [("app", True, False), ("shared", True, False), ("workspace", True, False)]
    assert outside not in tree.listed and not tree.handles


@pytest.mark.parametrize("change", ["link", "refused"])
def test_holding_counts_what_it_cannot_examine_and_never_enters_a_link(opened, change):
    outside = folder(precious=file())
    root = folder(app=folder())
    tree = opened["use"](root)
    def cross(parent, name):
        if change == "refused":
            raise PermissionError(5, "Access is denied")
        parent["children"]["app"] = link(outside)  # Swapped after the listing.
    tree.before_open = cross
    with removal.folder(opened["path"]) as owned:
        assert owned.holding(["app"]) == ["app"]
    assert outside not in tree.listed and outside["children"].keys() == {"precious"} and not tree.handles


# What setup, the launcher and the launcher refresh leave in an installation
# folder after use, by the group that deletes it.
SETUP = {"unified-setup-started.json": "{}", "unified-setup-complete.json": '{"schema": 1, "version": "1.0.0"}'}
DATA = {"shared/keys.env": "KEY=not-a-real-key", "shared/settings.yaml": "a: 1",
        "app/chats/one/message.txt": "hello", "app/config/services/task.json": "{}",
        "capture/c.jsonl": "{}", "ownership/o.json": "{}", "import/i.txt": "i",
        "workspace/project/notes.txt": "the person's own file", "export/local-ca.cer": "certificate"}
PROGRAM = {"cache/wheels/x.whl": "x", "source-store/objects/s": "s", "temp/t.tmp": "t",
           "python-bin/python.exe": "MZ", "tool-bin/amplifier-unified.exe": "MZ",
           "components.txt": "c", "install.log": "l", "uv.zip": "z", "private-git.json": "{}",
           "private-git.py": "#", "amplifier_unified-1.0.0-py3-none-any.whl": "w",
           "Launch Unified.lnk": "l", "Remove Amplifier sign-in host.lnk": "l",
           "launcher-1.0.0-0a1b2c3d/Scripts/pythonw.exe": "MZ", "child-2.stderr.txt": "e"}
LAST = {"git/cmd/git.exe": "MZ", "uv/uv.exe": "MZ", "consumer-install.json": "{}", "consumer-qualified.json": "{}"}
RUNNING = {"python/cpython-3.13.17/python.exe": "MZ", "tools/amplifier-unified/Scripts/pythonw.exe": "MZ"}


def top(group):
    return {name.split("/")[0] for name in group}


def snapshot(root):
    """Every file with its bytes, and every empty folder."""
    found = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            found[relative] = path.read_bytes()
        elif not any(path.iterdir()):
            found[relative + "/"] = None
    return found


class Disk:
    """A stand-in for the proven, pinned folder: real files in a temporary folder.

    The link rules have their own cases (test_windows_removal.py and the two
    cases above). Here the order and the choice of names matter. `held` are
    files that a program still uses; `kept` are folders that Windows keeps
    although they are empty.
    """

    def __init__(self, root, state):
        self.root, self.state = root, state

    def names(self):
        return sorted(path.name for path in self.root.iterdir())

    def _delete(self, path):
        relative = path.relative_to(self.root).as_posix()
        if path.is_dir():
            for child in sorted(path.iterdir()):
                self._delete(child)
            if not any(path.iterdir()) and relative not in self.state["kept"]:
                path.rmdir()
        elif relative not in self.state["held"]:
            path.unlink()

    def remove(self, names, *, settle=0, wait=None):
        for name in names:
            if os.path.lexists(self.root / name):
                # The rule that everything else rests on.
                assert (self.root / plan.JOURNAL).is_file(), f"{name} would be deleted before the journal exists"
                self.state["calls"].append(("delete", name))
                self._delete(self.root / name)
        return {"removed": 0, "left": [], "leftCount": 0}

    def holding(self, names):
        return sorted(name for name in names if os.path.lexists(self.root / name) and not (
            (self.root / name).is_dir() and not any((self.root / name).iterdir())))


@pytest.fixture
def deleter(uninstaller, monkeypatch):
    """The public uninstall over a used installation folder, with every effect recorded in order."""
    from amplifier_web import deployment, windows_trust
    root, state = uninstaller.root, uninstaller.state
    for group in (SETUP, DATA, PROGRAM, LAST, RUNNING):
        for name, text in group.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
    state.update(choices=[], questions=[], held=set(), kept=set(), locked=False, busy=False, pin_failure=None,
                 after=None, after_failure=None)

    @contextmanager
    def single(folder, **options):
        assert folder == root and not state["locked"]
        if state["busy"]:
            raise plan.Busy()
        state["locked"] = True
        state["calls"].append(("lock",))
        try:
            yield
        finally:
            state["locked"] = False
            state["calls"].append(("unlock",))
    monkeypatch.setattr(plan, "single", single)
    monkeypatch.setattr(plan, "outside", lambda: state["calls"].append(("outside",)))

    @contextmanager
    def pinned(folder):
        assert folder == root and state["locked"], "the folder is proven and pinned under the lock"
        if state["pin_failure"]:
            raise state["pin_failure"]
        state["calls"].append(("pin",))
        try:
            yield Disk(root, state)
        finally:
            state["calls"].append(("unpin",))
    monkeypatch.setattr(removal, "folder", pinned)

    def question(text, *, cancel=False):
        state["questions"].append((text, cancel))
        return state["choices"].pop(0)
    monkeypatch.setattr(plan, "question", question)

    def remove_root(der):
        # Windows asks while the installation is still whole: the app has
        # stopped, every entry exists, and nothing in the folder was deleted.
        assert der == state["certificate"] and state["task"] == "absent" and state["registry"] is not None
        assert not (root / plan.JOURNAL).exists()
        state["calls"].append(("certificate",))
        return state["windows_answer"]
    monkeypatch.setattr(windows_trust, "remove_root", remove_root)

    inner = deployment.write_private
    def write_private(path, raw, **options):
        inner(path, raw, **options)
        state["calls"].append(("write", Path(path).relative_to(root).as_posix()))
    monkeypatch.setattr(deployment, "write_private", write_private)

    def finish_after_exit(folder, token, **options):
        assert folder == root and not state["locked"]
        if state["after_failure"]:
            raise state["after_failure"]
        state["calls"].append(("after-exit",))
        state["after"] = dict(options, token=token, notices=len(state["notices"]))
        return 4321
    monkeypatch.setattr(removal, "finish_after_exit", finish_after_exit)
    uninstaller.journal = lambda: json.loads((root / plan.JOURNAL).read_text())
    return uninstaller


def deletes(state):
    return [call[1] for call in state["calls"] if call[0] == "delete"]


def test_no_keeps_the_whole_folder_as_an_uninstall_did_before(deleter, monkeypatch):
    state, root = deleter.state, deleter.root
    state["choices"], before = ["no"], snapshot(root)
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    assert deleter.run() == {"ok": True, "operation": "uninstall", "userDataRetained": True,
                             "installationFilesRetained": True, "certificate": "none"}
    # One question with three buttons. It names the folder and what each button does.
    (text, cancel), = state["questions"]
    assert cancel and str(root) in text and "Select No to keep them" in text and "Select Cancel to keep Amplifier" in text
    assert "delete your chats, settings and saved accounts?" in text
    # The earlier uninstall, with the same lock as a start: nothing in the folder goes.
    assert state["calls"][:4] == [("lock",), ("stop",), ("task-remove",), ("registry-remove",)]
    assert not deletes(state) and state["after"] is None and not (root / plan.JOURNAL).exists()
    after = snapshot(root)
    receipt = json.loads(after.pop(shell.FILE))
    assert receipt["status"] == "removed" and {**after, shell.FILE: before[shell.FILE]} == before
    assert "are still in this folder" in state["notices"][-1][0]


@pytest.mark.parametrize("choices", [["cancel"], ["yes", "no"]])
def test_cancel_and_no_to_the_second_question_change_nothing(deleter, choices):
    state, root = deleter.state, deleter.root
    state["choices"], state["certificate"], before = list(choices), b"this installation's root", snapshot(root)
    registration = state["registry"].copy()
    assert deleter.run() == {"ok": True, "cancelled": True}
    assert not state["calls"] and state["task"] == "running" and state["registry"] == registration
    assert snapshot(root) == before and len(list(deleter.folder.iterdir())) == 4
    assert [cancel for _, cancel in state["questions"]] == [True, False][:len(choices)]
    # Yes is never asked for in the question about the data; Windows asks about the certificate itself.
    first = state["questions"][0][0]
    assert "That is a separate question" in first and "Select Yes there" not in first
    if len(choices) == 2:
        second = state["questions"][1][0]
        # The last warning names the folder and everything that goes, and it has no certificate text.
        for words in (str(root), "chats", "settings", "saved accounts", "stored keys", "This cannot be undone",
                      "Select No to change nothing"):
            assert words in second
        assert "certificate" not in second


def test_without_setups_completion_record_the_question_is_the_earlier_one(deleter):
    state, root = deleter.state, deleter.root
    (root / plan.COMPLETE).unlink()
    assert deleter.run()["installationFilesRetained"] is True
    # Never the delete choice for a folder that setup did not complete.
    assert not state["questions"] and [confirm for _, confirm in state["notices"]] == [True]
    assert "stay in this folder" in state["notices"][0][0] and not deletes(state)
    assert (root / "shared/keys.env").exists() and not (root / plan.JOURNAL).exists()


def test_yes_twice_deletes_in_the_one_order_that_keeps_every_interruption_safe(deleter, monkeypatch):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], state["certificate"] = ["yes", "yes"], b"this installation's root"
    monkeypatch.setattr(consumer.sys, "stdout", None)
    result = deleter.run()
    calls = state["calls"]
    # 1. Lock, leave, prove and pin. 2. Stop, task, certificate. Nothing in the folder has changed.
    assert calls[:7] == [("lock",), ("outside",), ("pin",), ("stop",), ("task-remove",), ("certificate",),
                         ("write", plan.JOURNAL)]
    # 3. After the journal: setup's mark of unfinished work, then its completion record, then the data, keys first.
    assert deletes(state)[:9] == [plan.STARTED, plan.COMPLETE, "shared", "app", "capture", "ownership", "import",
                                  "workspace", "export"]
    # 4. Caches and program files, then the Windows entries, then what the launcher's own check needs.
    program, entries = calls.index(("delete", "export")) + 1, calls.index(("registry-remove",))
    assert {call[1] for call in calls[program:entries]} == top(PROGRAM)
    assert calls[entries + 1:entries + 5] == [("remove", name) for name in shell.LINKS]
    assert calls[entries + 5:] == [
        ("write", shell.FILE), ("delete", "git"), ("delete", "uv"), ("delete", "consumer-install.json"),
        ("delete", "consumer-qualified.json"), ("delete", shell.FILE), ("unpin",), ("unlock",), ("after-exit",)]
    assert state["task"] == "absent" and state["registry"] is None and not deleter.folder.exists()
    # Only the running program and the journal are left, and only the step after exit removes them.
    assert {path.name for path in root.iterdir()} == {"python", "tools", plan.JOURNAL}
    journal = deleter.journal()
    assert journal == {"schema": 1, "choice": "delete", "token": journal["token"]}
    assert re.fullmatch("[0-9a-f]{32}", journal["token"])
    assert state["after"] == {
        "token": journal["token"], "contents": ["python", "tools"], "folders": [], "files": [],
        "wait_for": (os.getpid(), os.getppid()), "seconds": 120, "choice": "delete", "notices": 1}
    assert result == {"ok": True, "operation": "uninstall", "userDataRetained": False,
                      "installationFilesRetained": False, "certificate": "removed", "kept": [], "afterExit": True}
    # One confirmation, before the step after exit starts. It says only what was observed.
    (confirmation, confirm), = state["notices"]
    assert not confirm and "Your chats, settings, saved accounts and stored keys are deleted" in confirmation
    assert "removed when you close this message" in confirmation and str(root) in confirmation
    assert "If this folder still exists a minute later, you can delete it" in confirmation
    assert "certificate" not in confirmation and "Reference:" not in confirmation


@pytest.mark.parametrize("answer,kept", [("declined", True), ("blocked", True), ("absent", False)])
def test_certificate_answer_never_stops_the_removal_and_is_said_in_the_confirmation(deleter, monkeypatch, answer, kept):
    state = deleter.state
    state["choices"], state["certificate"], state["windows_answer"] = ["yes", "yes"], b"root", answer
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    assert deleter.run()["certificate"] == answer and state["calls"].count(("certificate",)) == 1
    assert ("Windows kept the certificate" in state["notices"][-1][0]) is kept


@pytest.mark.parametrize("failure,step,phrase", [
    ("busy", "busy", "in another window right now. Nothing was changed"),
    ("pin", "folder", "could not check its own folder, so it deleted nothing"),
    ("proof", "folder", "could not check its own folder, so it deleted nothing"),
    ("entry", "entries-check", "nothing was changed"),
    ("task-read", "task-read", "nothing was changed"),
    ("stop", "stop", "Nothing was removed"),
    ("task-remove", "task-remove", "nothing else was removed"),
    ("journal", "journal", "Nothing in the folder was deleted")])
def test_nothing_in_the_folder_changes_before_the_journal(deleter, monkeypatch, failure, step, phrase):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], state["certificate"] = ["yes", "yes"], b"this installation's root"
    if failure == "busy":
        state["busy"] = True
    elif failure == "pin":
        state["pin_failure"] = PermissionError(5, "Access is denied")
    elif failure == "proof":
        # The completion record is gone when the removal looks for it under the lock.
        answers = [True, False]
        monkeypatch.setattr(plan, "completed", lambda folder: answers.pop(0))
    elif failure == "entry":
        (deleter.folder / "Uninstall Amplifier.lnk").write_bytes(b"changed after setup")
    elif failure == "journal":
        state["refuse_write"] = lambda path: path.name == plan.JOURNAL
    else:
        state["task_failure"] = failure
    before, registration = snapshot(root), state["registry"].copy()
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == step and phrase in stopped.value.notice
    assert isinstance(stopped.value, consumer.Refused) is (failure == "busy")
    # No journal, no deletion, every entry: the installation is whole. Only a private note of the failure is new.
    after = snapshot(root)
    note = after.pop("app/config/services/" + consumer.RECORD, None)
    assert after == before and not deletes(state) and state["after"] is None
    assert (note is None) is (failure == "busy") and state["registry"] == registration
    assert len(list(deleter.folder.iterdir())) == 4 and not state["locked"] and not state["pins"]
    # Windows is asked about the certificate only when the app has stopped and its task is gone.
    assert (("certificate",) in state["calls"]) is (failure == "journal")
    # The cause is gone: the same command now deletes the folder.
    if failure not in {"proof", "entry"}:
        state.update(busy=False, pin_failure=None, refuse_write=None, task_failure=None, choices=["yes", "yes"])
        assert deleter.run()["afterExit"] and {path.name for path in root.iterdir()} == {"python", "tools", plan.JOURNAL}


def refusals(deleter, monkeypatch):
    """Open, Stop and Status as a person starts them; returns the notices and the exit codes."""
    from amplifier_web import windows_installation
    consumer, shown, codes = deleter.consumer, [], []
    def never(*args, **options):
        raise AssertionError("Nothing of a folder that is being removed is read, started or stopped.")
    async def prepare(*args, **options):
        never()
    monkeypatch.setattr(consumer, "environment", never)
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    monkeypatch.setattr(windows_installation, "owner_notice", lambda text, confirm=False: shown.append(text) or True)
    monkeypatch.setattr(consumer.sys, "stdout", None)
    for arguments in ([], ["--no-open"], ["--status"], ["--stop"]):
        with pytest.raises(SystemExit) as stopped:
            consumer.main(["--root", str(deleter.root), *arguments])
        codes.append(stopped.value.code)
    return shown, codes


def test_data_that_stays_keeps_the_entries_and_the_journal_and_a_second_uninstall_finishes(deleter, monkeypatch):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], state["held"] = ["yes", "yes"], {"app/chats/one/message.txt"}
    registration = state["registry"].copy()
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "data" and isinstance(stopped.value.error, plan.Held)
    assert stopped.value.error.names == ["app"]
    notice = stopped.value.notice
    assert "some of your chats, settings or saved accounts could not be deleted" in notice
    assert "uninstall Amplifier again to finish" in notice and "does not open" in notice
    assert notice.endswith("Reference: data, Held") and "message.txt" not in notice
    # The stored keys went first. The file in use and the journal are there.
    assert deletes(state) == [plan.STARTED, plan.COMPLETE, *plan.DATA]
    assert not (root / "shared").exists() and (root / "app/chats/one/message.txt").read_text() == "hello"
    first = deleter.journal()
    assert first["choice"] == "delete" and not (root / plan.COMPLETE).exists()
    # The Windows entries and the launcher are whole, so the person can uninstall again.
    assert state["registry"] == registration and {path.name for path in deleter.folder.iterdir()} == set(shell.LINKS)
    for group in (PROGRAM, LAST, RUNNING):
        assert all((root / name).exists() for name in group)
    assert state["after"] is None and not state["locked"] and not state["pins"]
    # The private note is in the installation folder, never in the app's folder that is being deleted.
    assert json.loads((root / consumer.RECORD).read_text())["step"] == "data"
    assert not (root / "app/config/services" / consumer.RECORD).exists()

    # Open, Stop and Status refuse with one plain message and change nothing.
    before = snapshot(root)
    with monkeypatch.context() as patch:
        shown, codes = refusals(deleter, patch)
    assert codes == [1, 1, 1, 1] and len(set(shown)) == 1 and len(shown) == 4
    assert "the removal did not finish" in shown[0] and "select Uninstall Amplifier" in shown[0]
    assert "Reference:" not in shown[0] and snapshot(root) == before

    # No to the second uninstall changes nothing.
    state["calls"].clear()
    state["choices"] = ["no"]
    assert deleter.run() == {"ok": True, "cancelled": True} and not state["calls"] and snapshot(root) == before
    (text, cancel) = state["questions"][-1]
    assert not cancel and "did not finish" in text and str(root) in text and "This cannot be undone" in text

    # Yes runs the whole path again, including the stop, with a new token.
    state["held"].clear()
    state.update(choices=["yes"], task="running")
    result = deleter.run()
    assert state["calls"][:6] == [("lock",), ("outside",), ("pin",), ("stop",), ("task-remove",),
                                  ("write", plan.JOURNAL)]
    assert result["afterExit"] and {path.name for path in root.iterdir()} == {"python", "tools", plan.JOURNAL}
    assert deleter.journal()["token"] not in {first["token"], None}
    assert state["after"]["token"] == deleter.journal()["token"]
    assert state["registry"] is None and not deleter.folder.exists()


@pytest.mark.parametrize("journal", ['{"schema": 1, "choice": "delete", "token": "' + "a" * 32 + '"}', "{", ""])
def test_open_stop_and_status_refuse_while_any_journal_exists(deleter, monkeypatch, journal):
    root = deleter.root
    (root / plan.JOURNAL).write_text(journal)
    before = snapshot(root)
    shown, codes = refusals(deleter, monkeypatch)
    assert codes == [1, 1, 1, 1] and len(shown) == 4 and len(set(shown)) == 1
    assert shown[0] == deleter.consumer._UNFINISHED["removing"]
    # The two ways to open took the lock, saw the journal and left. Nothing else ran.
    assert snapshot(root) == before and deleter.state["calls"] == [("lock",), ("unlock",)] * 2


def test_a_journal_that_does_not_prove_a_delete_choice_never_deletes(deleter):
    # A record that this version did not write, or a damaged one: only an uninstall that keeps the folder.
    state, root = deleter.state, deleter.root
    for text in ('{"schema": 1, "choice": "keep", "token": "' + "b" * 32 + '"}', "{", '{"schema": 2}'):
        (root / plan.JOURNAL).write_text(text)
        state["answers"] = [False]
        assert deleter.run() == {"ok": True, "cancelled": True}
        assert not state["questions"] and "stay in this folder" in state["notices"][-1][0]
    state["answers"] = [True]
    assert deleter.run()["installationFilesRetained"] is True and not deletes(state)
    assert (root / "shared/keys.env").exists() and (root / plan.JOURNAL).read_text() == '{"schema": 2}'


def test_foreign_top_level_entries_stay_and_then_the_folder_stays(deleter, monkeypatch):
    state, root = deleter.state, deleter.root
    foreign = {"mine.txt": "not Amplifier's", "App/notes.txt": "another spelling", "launcher-x/a": "no known form",
               "Unified-Uninstall.json": "not the journal", "amplifier_unified-1.0.0-py3-none-any.whl.bak": "w"}
    for name, text in foreign.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    state["choices"] = ["yes", "yes"]
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    names = sorted(top(foreign))
    assert result["kept"] == names and result["afterExit"]
    assert {path.name for path in root.iterdir()} == {*names, "python", "tools", plan.JOURNAL}
    assert all((root / name).read_text() == text for name, text in foreign.items())
    # Not deleted here, and not handed to the step after exit.
    handed = state["after"]["contents"] + state["after"]["folders"] + state["after"]["files"]
    assert not set(names) & set(deletes(state)) and not set(names) & set(handed)
    confirmation = state["notices"][-1][0]
    assert "The folder itself stays" in confirmation and "They were not changed" in confirmation
    assert all(name in confirmation for name in names) and "you can delete it" not in confirmation


def test_the_journal_and_the_running_program_are_in_no_delete_list(deleter):
    state, root = deleter.state, deleter.root
    owned = Disk(root, state)
    (root / plan.JOURNAL).write_text("{}")
    assert not any(plan.sort([plan.JOURNAL]).values())
    for names in ([plan.JOURNAL], ["app", plan.JOURNAL], ["mine.txt"], ["python"], ["tools"], ["APP"]):
        with pytest.raises(ValueError):
            plan._clear(owned, names, 0, None)
    assert not deletes(state)
    listed = plan.remaining(owned)
    everything = plan.remaining(None)
    for rest in (listed, everything):
        assert plan.JOURNAL not in rest["contents"] + rest["folders"] + rest["files"]
        # The step after exit deletes inside the running program's two folders only.
        assert rest["contents"] == ["python", "tools"]
    assert everything["foreign"] is None and not set(plan.RUNNING) & set(everything["folders"])
    state["choices"] = ["yes", "yes"]
    deleter.run()
    assert plan.JOURNAL not in deletes(state) and not set(plan.RUNNING) & set(deletes(state))
    assert (root / plan.JOURNAL).is_file() and all((root / name).exists() for name in RUNNING)


def test_emptied_folder_that_windows_keeps_is_not_data_and_a_program_file_in_use_does_not_stop(deleter):
    state, root = deleter.state, deleter.root
    # The launcher's start program keeps `workspace` as its current folder; another program uses a cache file.
    state.update(choices=["yes", "yes"], kept={"workspace"}, held={"cache/wheels/x.whl"})
    result = deleter.run()
    assert result["afterExit"] and result["userDataRetained"] is False
    assert list((root / "workspace").iterdir()) == [] and (root / "cache/wheels/x.whl").exists()
    # The step after exit removes such a folder only while it is empty; it deletes inside python and tools only.
    assert state["after"]["contents"] == ["python", "tools"]
    assert state["after"]["folders"] == ["workspace", "cache"] and state["after"]["files"] == []
    assert state["registry"] is None


@pytest.mark.parametrize("failure", ["commit", "shortcut"])
def test_failed_entry_removal_after_the_data_is_gone_is_finished_by_a_second_uninstall(deleter, failure):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"] = ["yes", "yes"]
    state["commit_conflict"], state["link_failure"] = failure == "commit", "Stop Amplifier.lnk" if failure == "shortcut" else None
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == ("apps-entry" if failure == "commit" else "shortcuts")
    # Not the text of an uninstall that keeps the folder: Amplifier cannot be opened again.
    assert "could not finish its removal" in stopped.value.notice and "open it from the Start menu" not in stopped.value.notice
    assert not any((root / name).exists() for name in top(DATA)) and state["after"] is None
    # The uninstall entry and the launcher's own files are there for the second uninstall.
    assert (deleter.folder / "Uninstall Amplifier.lnk").exists()
    assert all((root / name).exists() for name in (*LAST, *RUNNING, shell.FILE, plan.JOURNAL))
    assert json.loads((root / consumer.RECORD).read_text())["step"] == stopped.value.step
    state.update(commit_conflict=False, link_failure=None, choices=["yes"])
    assert deleter.run()["afterExit"] and state["registry"] is None and not deleter.folder.exists()
    assert {path.name for path in root.iterdir()} == {"python", "tools", plan.JOURNAL}


def test_step_after_exit_that_cannot_start_is_said_plainly(deleter, monkeypatch):
    state = deleter.state
    state["choices"], state["after_failure"] = ["yes", "yes"], OSError("Cannot locate Windows PowerShell.")
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    assert result["ok"] and result["afterExit"] is False and result["installationFilesRetained"] is True
    confirmation, leftover = (text for text, _ in state["notices"])
    assert "are deleted" in confirmation and "did not let Amplifier remove its last program files" in leftover
    assert "none of your data" in leftover and str(deleter.root) in leftover


def test_data_removal_gives_a_closing_program_time_and_then_stops(deleter):
    state, root = deleter.state, deleter.root
    owned, waits = Disk(root, state), []
    (root / plan.JOURNAL).write_text("{}")
    state["held"] = {"app/chats/one/message.txt", "shared/keys.env"}
    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            state["held"].discard("shared/keys.env")
    # Until the deadline every pass tries again what still holds something, in the same order.
    assert plan._clear(owned, plan.DATA, time.monotonic() + 0.4, lambda seconds: (wait(seconds), time.sleep(0.15))) == ["app"]
    assert waits and set(waits) == {0.5} and not (root / "shared").exists()
    assert deletes(state)[:7] == list(plan.DATA) and set(deletes(state)[7:]) <= {"shared", "app"}
    # After the deadline there is exactly one pass.
    state["calls"].clear()
    assert plan._clear(owned, plan.DATA, 0, None) == ["app"] and deletes(state) == ["app"]


def launch(deleter, monkeypatch):
    from amplifier_web import windows_installation
    state, consumer = deleter.state, deleter.consumer
    async def prepare(root, launcher, env, *, identity):
        assert state["locked"], "the start step runs under the installation's lock"
        state["calls"].append(("prepare",))
        return {"background": True, "created": False, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    return lambda: asyncio.run(consumer.launch(deleter.root, open_browser=False))


def test_open_takes_the_same_lock_around_its_start_step_and_refuses_when_it_is_taken(deleter, monkeypatch):
    state, consumer = deleter.state, deleter.consumer
    start = launch(deleter, monkeypatch)
    assert start()["health"]["ok"] and state["calls"] == [("lock",), ("prepare",), ("unlock",)]
    state["calls"].clear()
    state["busy"] = True
    with pytest.raises(consumer.Refused) as refused:
        start()
    assert refused.value.step == "busy" and not state["calls"]
    assert "Nothing was changed" in refused.value.notice and "Reference:" not in refused.value.notice
    # An uninstall that keeps the folder refuses in the same way, before any effect.
    state["choices"] = ["no"]
    with pytest.raises(consumer.Refused):
        deleter.run()
    assert not state["calls"] and state["task"] == "running" and state["registry"] is not None
    assert not (deleter.root / "app/config/services" / consumer.RECORD).exists()


def _setup_source():
    text = (REPOSITORY / "scripts/windows_bootstrap_native.cs").read_text(encoding="utf-8")
    # The prototype installer makes another kind of folder (capsule, host). Uninstall is not for it.
    start, end = text.index("static void Install(Dictionary<string,string> args"), text.index("internal static int Main(")
    return text[:start] + text[end:]


def test_setup_creates_no_top_level_name_that_uninstall_does_not_know():
    """Reads setup's source. A new top-level name there must be added to `windows_uninstall` first."""
    text = _setup_source()
    names, forms = set(), set()
    for argument in re.findall(r"Path\.Combine\((?:root|Storage\.Root),([^,)]*)", text):
        literal = re.fullmatch(r'"([^"]+)"', argument)
        (names if literal else forms).add(literal.group(1) if literal else argument)
    # Every way in which setup builds a name directly in the folder. A new way fails here.
    assert forms == {"Started", "Complete", "name", 'name+".zip"', 'name+".lnk"', 'name+".stderr.txt"',
                     'name+".failure.json"', "relative", "pair.Substring(split+1"}
    names.update(re.search(r'Started="([^"]+)", Complete="([^"]+)"', text).groups())
    lists = re.findall(r"foreach\((?:string|var) relative in new\[\]\{([^}]*)\}\)", text)
    pairs = re.search(r"foreach\(var pair in new\[\]\{([^}]*)\}\)", text).group(1)
    assert len(lists) == 2
    names.update(name for row in lists for name in re.findall(r'"([^"]*)"', row) if name)
    names.update(re.findall(r'"[A-Z_]+:([^"]+)"', pairs))
    helpers = re.findall(r'Helper\("([^"]+)"', text)
    shortcuts = re.findall(r'ConsumerShortcut\(python,root,"([^"]+)"', text)
    assert helpers and len(shortcuts) == 4
    names.update(helpers + [name + ".zip" for name in helpers] + [name + ".lnk" for name in shortcuts])
    assert 'string name="amplifier_unified-"+version+"-py3-none-any.whl";' in text
    assert 'string name="child-"+sequence.ToString(CultureInfo.InvariantCulture);' in text
    names.update(["amplifier_unified-10.20.30-py3-none-any.whl", "child-1.stderr.txt", "child-12.failure.json"])
    names = sorted({name.replace("\\\\", "\\").split("\\")[0] for name in names})
    assert len(names) > 30 and {"app", "uv.zip", "git.tar.bz2", "Launch Unified.lnk", plan.STARTED} <= set(names)
    groups = plan.sort(names)
    assert groups["foreign"] == [], "setup creates a top-level name that uninstall would leave behind"
    assert plan.JOURNAL not in names and sum(map(len, groups.values())) == len(names)
    # Setup's own two records are deleted first, and never written, by uninstall.
    assert groups["setup"] == [plan.COMPLETE, plan.STARTED]
    source = (REPOSITORY / "amplifier_web/windows_uninstall.py").read_text(encoding="utf-8")
    assert "write" not in source.split("def forget_setup")[1].split("\ndef ")[0]


def test_launcher_and_launcher_refresh_names_are_known_and_exact():
    from amplifier_web import windows_consumer as consumer, windows_consumer_shell
    refresh = (REPOSITORY / "amplifier_web/windows_launcher_refresh.py").read_text(encoding="utf-8")
    environment = (REPOSITORY / "amplifier_web/windows_consumer.py").read_text(encoding="utf-8")
    # The forms that the launcher refresh uses for what it keeps beside the installation's own folders.
    for form in ("""f"launcher-{old['version']}-{old['identity']['digest'][:8]}\"""", '"launcher-not-used"',
                 'Path(f"{path}-{count}")', 'Path(target).name + ".part"', "store / filename(version)"):
        assert form in refresh, form
    roots = re.search(r"roots = \{(.*?)\}\n", environment, re.S).group(1)
    known = [consumer.RECEIPT, consumer.QUALIFICATION, consumer.RECORD, windows_consumer_shell.FILE, "export",
             "workspace", *re.findall(r': "([a-z-]+)"', roots),
             "launcher-0.95.0-0a1b2c3d", "launcher-0.95.0-0a1b2c3d-2", "launcher-not-used", "launcher-not-used-3",
             "amplifier_unified-0.96.0-py3-none-any.whl", "amplifier_unified-0.96.0-py3-none-any.whl.part",
             ".private-" + "0f" * 16 + ".tmp"]
    assert len(known) > 20 and plan.sort(known)["foreign"] == []
    # Exact spelling and exact forms. Anything else is not this installation's own and stays.
    foreign = ["App", "SHARED", "Workspace", "launcher-0.95.0-0A1B2C3D", "launcher-0.95.0-0a1b2c3", "launcher-x",
               "launcher-not-used-", "amplifier_unified-0.96.0-py3-none-any.whl.bak", "amplifier_unified-0.96-py3-none-any.whl",
               ".private-0f.tmp", "child-.stderr.txt", "Unified-Uninstall.json", "my notes.txt", "python.exe",
               "launcher-٠.٩٥.٠-0a1b2c3d", "app ", "app."]
    assert plan.sort(foreign)["foreign"] == foreign
    assert plan.sort(list(plan.DATA))["data"] == list(plan.DATA) == ["shared", "app", "capture", "ownership", "import",
                                                                    "workspace", "export"]
