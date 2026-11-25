"""The delete choice of the Windows uninstall: portable order and rules, and the real thing on native Windows."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
import uuid

import pytest

from amplifier_web import windows_removal as removal, windows_uninstall as plan

from test_windows_consumer_shell import _install_native_entries, model, shell, uninstaller  # noqa: F401  (two fixtures)
from test_windows_removal import _finish_report, file, folder, link, opened  # noqa: F401  (opened is a fixture)

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


def test_holding_counts_another_spelling_of_a_name_from_the_same_listing_and_never_opens_it(opened):
    # `App` is the entry `app` to Windows. So are spellings with the dotless i and the long s.
    root = folder(App=folder(chat=file()), shared=folder(), **{"\u0131mport": folder(), "unified-\u017fetup-started.json": file()})
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        assert owned.remove(["app", "import", "unified-setup-started.json"]) == {"removed": 0, "left": [], "leftCount": 0}
        tree.opened.clear()
        listings = len(tree.listed)
        held = owned.holding(["app", "shared", "import", "unified-setup-started.json", "capture"])
        # One listing of the folder decided for every name; only the exact, real folder was opened.
        assert len(tree.listed) == listings + 2 and tree.opened == [("shared", True, False)]
    assert held == ["app", "import", "unified-setup-started.json"]
    assert sorted(root["children"]) == ["App", "shared", "unified-\u017fetup-started.json", "\u0131mport"]
    assert plan.altered(["App", "\u0131mport", "unified-\u017fetup-started.json", "shared", "mine.txt"]) == [
        "App", "\u0131mport", "unified-\u017fetup-started.json"]


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
# This fixture is an earlier installation: an earlier setup file made it, with the store `source-store`.
SETUP = {"unified-setup-started.json": "{}", "unified-setup-complete.json": '{"schema": 1, "version": "0.98.4"}'}
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
# The source store of a new installation, and of an earlier one (the fixtures above and below).
STORES = ("s", "source-store")


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
    cases above). Here the order and the choice of names matter. `held` files
    refuse the walk's open; `mapped` files refuse deletion but can be moved.
    `kept` folders stay although they are empty.
    """

    def __init__(self, root, state):
        self.root, self.state = root, state

    def names(self):
        if self.state.get("listing_fails") and self.state["registry"] is None:
            raise OSError(5, "The folder could not be listed")
        return sorted([path.name for path in self.root.iterdir()] + self.state.get("also_listed", []))

    def _delete(self, path, left, park):
        relative = path.relative_to(self.root).as_posix()
        if path.is_dir():
            for child in sorted(path.iterdir()):
                self._delete(child, left, park)
            if not any(path.iterdir()) and relative not in self.state["kept"]:
                path.rmdir()
            elif not any(path.iterdir()):
                left.append(relative.replace("/", "\\"))
        elif relative in self.state["held"]:
            left.append(relative.replace("/", "\\"))
        elif relative in self.state["mapped"]:
            if park is None:
                left.append(relative.replace("/", "\\"))
            else:
                park(path)
        else:
            path.unlink()

    def remove(self, names, *, settle=0, wait=None, park=None):
        assert plan.COMPLETE not in names, "Only the step after exit may delete setup's completion record."
        listed, left, parked = set(os.listdir(self.root)), [], []
        for name in names:
            if name in listed:  # The exact spelling of the listing, as the real walk; never a path lookup.
                # The rule that everything else rests on. (`journal_taken`: only the case in which a program
                # deletes the journal after the uninstall's look found it; the design's limit.)
                assert (self.root / plan.JOURNAL).is_file() or self.state.get("journal_taken"), (
                    f"{name} would be deleted before the journal exists")
                self.state["calls"].append(("delete", name))
                # Record each move, including files below the named folder.
                def move(path):
                    park(path)
                    parked.append(path)
                self._delete(self.root / name, left, move if park is not None else None)
        result = {"removed": 0, "left": left, "leftCount": len(left)}
        return result if park is None else {**result, "parked": len(parked)}

    @contextmanager
    def aside(self, name, hold):
        assert self.state["locked"] and self.state["pinned"] and self.state["holding"] is None
        held = self.root / name
        held.mkdir()
        kept = [self.root / (hold + ".hold"), held / (hold + ".hold")]
        for path in kept:
            path.touch(exist_ok=False)
        self.state["calls"].append(("hold", name))
        self.state["holding"] = name
        count = 0
        def park(path):
            nonlocal count
            count += 1
            path.rename(held / str(count))
        park.keep = (101, 202)
        try:
            yield park
        finally:
            # After ready the helper's stand-in keeps both files open.
            # Without that hand-over they vanish when this owner lets go.
            if self.state["after"] is None:
                for path in reversed(kept):
                    path.unlink()
            self.state["holding"] = None
            self.state["calls"].append(("release", name))

    def holding(self, names):
        wanted, held = {removal.same_name(name): name for name in names}, set()
        for name in os.listdir(self.root):  # One listing decides for every spelling, as the real folder.
            own = wanted.get(removal.same_name(name))
            if own is not None and (name != own or not (
                    (self.root / name).is_dir() and not any((self.root / name).iterdir()))):
                held.add(own)
        return sorted(held)


@pytest.fixture
def deleter(uninstaller, monkeypatch):
    """The public uninstall over a used installation folder, with every effect recorded in order."""
    from amplifier_web import deployment, windows_trust
    root, state = uninstaller.root, uninstaller.state
    for group in (SETUP, DATA, PROGRAM, LAST, RUNNING):
        for name, text in group.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
    state.update(choices=[], questions=[], held=set(), mapped=set(), kept=set(), locked=False,
                 pinned=False, holding=None, busy=False, pin_failure=None,
                 after=None, after_failure=None, lock_failure=None)

    @contextmanager
    def single(folder, **options):
        assert folder == root and not state["locked"]
        if state["lock_failure"]:
            raise state["lock_failure"]
        if state["busy"]:
            raise plan.Busy()
        state["locked"] = True
        state["calls"].append(("lock",))
        if state.get("on_lock"):
            state.pop("on_lock")()
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
        state["pinned"] = True
        try:
            yield Disk(root, state)
        finally:
            state["pinned"] = False
            state["calls"].append(("unpin",))
    monkeypatch.setattr(removal, "folder", pinned)

    def question(text, *, cancel=False):
        state["questions"].append((text, cancel))
        return state["choices"].pop(0)
    monkeypatch.setattr(plan, "question", question)

    def remove_root(der, store="ROOT"):
        # Windows asks while the installation is still whole: the app has
        # stopped, every entry exists, and nothing in the folder was deleted.
        # `certificate`: one root (bytes) or a list of roots. `windows_answer`: one answer, or one for each.
        held = state["certificate"] if isinstance(state["certificate"], list) else [state["certificate"]]
        assert der in held and state["task"] == "absent" and state["registry"] is not None
        assert not (root / plan.JOURNAL).exists()
        state["calls"].append(("certificate",))
        answer = state["windows_answer"]
        answer = answer.pop(0) if isinstance(answer, list) else answer
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(windows_trust, "remove_root", remove_root)

    inner = deployment.write_private
    def write_private(path, raw, **options):
        inner(path, raw, **options)
        state["calls"].append(("write", Path(path).relative_to(root).as_posix()))
        if Path(path).name == plan.JOURNAL and state.get("on_journal"):
            state.pop("on_journal")()
    monkeypatch.setattr(deployment, "write_private", write_private)

    def finish_after_exit(folder, token, **options):
        assert folder == root and state["locked"] and state["pinned"]
        assert state["holding"] == plan.PARK
        assert {path.name for path in (root / plan.PARK).iterdir()} == {token + ".hold"}
        assert (root / (token + ".hold")).is_file()
        assert options["keep"] == (101, 202)
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


def kept_names(deleter):
    return {plan.PARK, plan.JOURNAL, plan.COMPLETE, deleter.journal()["token"] + ".hold"}


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
                      "saved in its workspace folder", "Select No to change nothing"):
            assert words in second
        # Something in the folder that is not Amplifier's stays, so the question does not promise "everything".
        assert "certificate" not in second and "everything in it" not in second
        assert "everything that Amplifier keeps in this folder" in second


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
    # 3. After the journal: setup's unfinished mark, then the data, keys first. The completion record stays.
    assert deletes(state)[:8] == [plan.STARTED, "shared", "app", "capture", "ownership", "import",
                                  "workspace", "export"]
    # 4. Caches and program files, then the Windows entries, then what the launcher's own check needs.
    program, entries = calls.index(("delete", "export")) + 1, calls.index(("registry-remove",))
    assert {call[1] for call in calls[program:entries]} == top(PROGRAM)
    assert calls[entries + 1:entries + 5] == [("remove", name) for name in shell.LINKS]
    assert calls[entries + 5:] == [
        ("write", shell.FILE), ("delete", "git"), ("delete", "uv"), ("delete", "consumer-install.json"),
        ("delete", "consumer-qualified.json"), ("delete", shell.FILE), ("unpin",), ("unlock",),
        ("lock",), ("pin",), ("hold", plan.PARK), ("after-exit",), ("delete", "python"),
        ("delete", "tools"), ("release", plan.PARK), ("unpin",), ("unlock",)]
    assert state["task"] == "absent" and state["registry"] is None and not deleter.folder.exists()
    # The program folders went through the walk. Only the held folder and journal stay.
    assert {path.name for path in root.iterdir()} == kept_names(deleter)
    assert plan.COMPLETE not in deletes(state) and plan.completed(root)
    journal = deleter.journal()
    assert journal == {"schema": 1, "choice": "delete", "token": journal["token"]}
    assert re.fullmatch("[0-9a-f]{32}", journal["token"])
    assert state["after"] == {
        "token": journal["token"], "park": plan.PARK,
        "keep": (101, 202),
        "folders": plan.last_names(),
        "wait_for": (os.getpid(), os.getppid()), "seconds": 120, "choice": "delete", "notices": 1}
    # The result claims the start of the step after exit, never its end.
    assert result == {"ok": True, "operation": "uninstall", "userDataRetained": False, "certificate": "removed",
                      "kept": [], "programLeft": [], "afterExit": True}
    # One confirmation, before the step after exit starts. It says only what was observed.
    (confirmation, confirm), = state["notices"]
    assert not confirm and "Your chats, settings, saved accounts and stored keys are deleted" in confirmation
    # A step that has not run yet is announced, not claimed.
    assert "When you close this message, Amplifier starts to remove its last program files" in confirmation
    assert "are removed" not in confirmation and str(root) in confirmation
    assert "If this folder still exists a minute later, you can delete it" in confirmation
    assert "certificate" not in confirmation and "Reference:" not in confirmation


@pytest.mark.parametrize("roots,answer,outcome,said", [
    (1, "declined", "declined", ("declined", 1)), (1, "blocked", "blocked", ("blocked", 1)),
    (1, OSError("store"), "blocked", ("blocked", 1)), (1, "absent", "absent", None), (1, "removed", "removed", None),
    (2, ["removed", "declined"], "declined", ("declined", 2)), (2, ["declined", "declined"], "declined", ("declined", 2)),
    (2, [OSError("store"), "declined"], "declined", ("declined", 2)),
    (2, [OSError("store"), "removed"], "blocked", ("blocked", 2)), (2, ["blocked", "absent"], "blocked", ("blocked", 2)),
    (2, ["removed", "absent"], "removed", None), (2, ["absent", "absent"], "absent", None)])
def test_certificate_answer_never_stops_the_removal_and_is_said_in_the_confirmation(
        deleter, monkeypatch, roots, answer, outcome, said):
    """A No is Windows' own answer: it kept the certificate. After another failure the text does not say that."""
    from amplifier_web.windows_trust import ROOT_NAME
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], state["windows_answer"] = ["yes", "yes"], answer
    state["certificate"] = b"root" if roots == 1 else [b"current root", b"replaced root"]
    monkeypatch.setattr(consumer.sys, "stdout", None)
    assert deleter.run()["certificate"] == outcome and state["calls"].count(("certificate",)) == roots
    (confirmation, _), = state["notices"]
    sentence = CERTIFICATE_LEFT[said].format(certificate=ROOT_NAME) if said else ""
    assert confirmation == LAST_MESSAGES["delete"].format(folder=root) + sentence
    words = confirmation.replace(str(root), "")  # The folder's path is not the product's text.
    assert ("certificate" in words) is bool(said)
    # With two, the text gives the number that Windows was asked about, never a number of those that stay.
    assert roots == 1 or "the certificate" not in words


@pytest.mark.parametrize("roots", [1, 2])
@pytest.mark.parametrize("trust", ["declined", "blocked"])
def test_further_message_after_a_confirmation_that_windows_did_not_show_says_the_same_about_the_certificate(
        deleter, monkeypatch, roots, trust):
    from amplifier_web.windows_trust import ROOT_NAME
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], state["windows_answer"] = ["yes", "yes"], trust
    state["certificate"] = b"root" if roots == 1 else [b"current root", b"replaced root"]
    monkeypatch.setattr(consumer.sys, "stdout", None)
    refused = not_shown_once(monkeypatch, "Amplifier is uninstalled.")
    with pytest.raises(SystemExit):
        consumer.main(["--root", str(root), "--uninstall"])
    sentence = CERTIFICATE_LEFT[trust, roots].format(certificate=ROOT_NAME)
    (again, _), = state["notices"]
    assert refused[0] == LAST_MESSAGES["delete"].format(folder=root) + sentence
    assert again == (LAST_MESSAGES["delete, and Windows did not show the confirmation"].format(folder=root)
                     + sentence + "\n\nReference: message, OSError")


@pytest.mark.parametrize("failure,step,phrase", [
    ("busy", "busy", "in another window right now. Nothing was changed"),
    ("lock", "lock", "could not find out whether it is being opened or uninstalled"),
    ("pin", "folder", "could not check its own folder, so it deleted nothing"),
    ("proof", "folder", "could not check its own folder, so it deleted nothing"),
    ("entry", "entries-check", "This uninstall stopped before it changed anything"),
    ("task-read", "task-read", "This uninstall stopped before it changed anything"),
    ("stop", "stop", "Nothing was removed"),
    ("task-remove", "task-remove", "nothing else was removed"),
    ("journal", "journal", "This uninstall deleted nothing in the folder"),
    ("journal-lost", "journal", "This uninstall deleted nothing in the folder")])
def test_nothing_in_the_folder_changes_before_the_journal(deleter, monkeypatch, failure, step, phrase):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], state["certificate"] = ["yes", "yes"], b"this installation's root"
    if failure == "busy":
        state["busy"] = True
    elif failure == "lock":
        state["lock_failure"] = OSError(5, "The lock could not be created")
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
    elif failure == "journal-lost":
        # Another program takes the journal away directly after it was written. The writer reported it as
        # written. Without the journal Amplifier would open from this folder, so the removal must stop here.
        state["on_journal"] = lambda: (root / plan.JOURNAL).unlink()
    else:
        state["task_failure"] = failure
    before, registration = snapshot(root), state["registry"].copy()
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == step and phrase in stopped.value.notice
    assert isinstance(stopped.value, consumer.Refused) is (failure == "busy")
    # No journal, no deletion, every entry: the installation is whole. Only a private note of the failure is
    # new, beside the app's other service records, as in an uninstall that keeps the folder.
    after = snapshot(root)
    note = after.pop("app/config/services/" + consumer.RECORD, None)
    assert after == before and not deletes(state) and state["after"] is None
    assert (note is None) is (failure == "busy") and state["registry"] == registration
    assert len(list(deleter.folder.iterdir())) == 4 and not state["locked"] and not state["pins"]
    # Windows is asked about the certificate only when the app has stopped and its task is gone.
    assert (("certificate",) in state["calls"]) is (failure in {"journal", "journal-lost"})
    # The cause is gone: the same command now deletes the folder.
    if failure not in {"proof", "entry"}:
        state.update(busy=False, lock_failure=None, pin_failure=None, refuse_write=None, task_failure=None,
                     choices=["yes", "yes"])
        assert deleter.run()["afterExit"]
        assert {path.name for path in root.iterdir()} == kept_names(deleter)


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


def test_start_entry_that_is_not_confirmed_stops_the_delete_choice_before_anything_changes(deleter, monkeypatch):
    """The text for this cause, in the delete choice: the uninstall stopped before it changed anything."""
    from amplifier_web import windows_service
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    entry = "AmplifierUnified-S-1-5-21-1-2-3-1001"

    def refuse(home, *, progress):
        progress("task-read")
        raise windows_service.TaskNotConfirmed(entry)
    monkeypatch.setattr(windows_service, "uninstall", refuse)
    state["choices"], before, registration = ["yes", "yes"], snapshot(root), state["registry"].copy()
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "task-read" and not isinstance(stopped.value, consumer.Refused)
    notice = stopped.value.notice
    assert notice.startswith("This uninstall stopped before it changed anything.\n\n")
    assert "could not confirm that its start-at-sign-in entry" in notice and entry in notice
    assert notice.endswith("5. Uninstall Amplifier again.\n\nReference: task-read, TaskNotConfirmed")
    # No journal, no deletion, the Windows entries whole: only the private note of the failure was written.
    after = snapshot(root)
    after.pop("app/config/services/" + consumer.RECORD)
    assert after == before and not deletes(state) and state["after"] is None
    assert not (root / plan.JOURNAL).exists() and state["registry"] == registration and state["task"] == "running"


@pytest.mark.parametrize("retry", [False, True])
def test_start_entry_that_is_not_confirmed_in_a_second_uninstall_does_not_say_that_nothing_changed(
        deleter, monkeypatch, retry):
    """The first uninstall deleted data and stopped. The second one stops before it deletes more."""
    from amplifier_web import windows_service
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    entry = "AmplifierUnified-S-1-5-21-1-2-3-1001"
    state.update(choices=["yes", "yes"], commit_conflict=True)
    with pytest.raises(consumer.Unfinished):
        deleter.run()
    assert not (root / "shared").exists() and plan.pending(root)["choice"] == "delete"

    def refuse(home, *, progress):
        progress("task-read")
        raise windows_service.TaskNotConfirmed(entry, retry=retry)
    monkeypatch.setattr(windows_service, "uninstall", refuse)
    state.update(commit_conflict=False, choices=["yes", "yes"])
    state["calls"].clear()
    before = snapshot(root)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "task-read" and not deletes(state) and state["after"] is None
    notice = stopped.value.notice
    assert notice.startswith("Amplifier could not finish its removal from this PC. Do not open Amplifier "
                             "before it has finished: its folder can be incomplete.\n\n")
    assert "before it changed anything" not in notice and "You can keep using it" not in notice
    assert notice.endswith("Uninstall Amplifier again.\n\nReference: task-read, TaskNotConfirmed")
    # The cause and the way out are main's, as for a first uninstall.
    if retry:
        assert "Windows could not check the account" in notice and "2. Restart this PC.\n3. Uninstall" in notice
        assert entry not in notice and "delete" not in notice.lower()
    else:
        assert "could not confirm that its start-at-sign-in entry" in notice and notice.count(entry) == 2
        assert "Amplifier then no longer starts when you sign in.\n1. " in notice and "makes a new entry" not in notice
    # The journal stays; the folder is as the first uninstall left it.
    after = snapshot(root)
    assert json.loads(after.pop(consumer.RECORD))["step"] == "task-read" and before.pop(consumer.RECORD)
    assert after == before and plan.unfinished(root)


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
    assert "uninstall Amplifier again to finish" in notice and "does not open" not in notice
    assert "Do not open Amplifier before that: its folder can be partly deleted." in notice
    assert notice.endswith("Reference: data, Held") and "message.txt" not in notice
    # The stored keys went first. The file in use and the journal are there.
    assert deletes(state) == [plan.STARTED, *plan.DATA]
    assert not (root / "shared").exists() and (root / "app/chats/one/message.txt").read_text() == "hello"
    first = deleter.journal()
    assert first["choice"] == "delete" and plan.completed(root)
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

    # Yes and then No changes nothing either: the person chooses twice again, with No as each default.
    state["choices"] = ["yes", "no"]
    assert deleter.run() == {"ok": True, "cancelled": True} and not state["calls"] and snapshot(root) == before
    (asked, _), (warned, cancel) = state["questions"][-2:]
    assert "asks you once more before it deletes anything" in asked and not cancel
    assert "Delete everything that Amplifier keeps in this folder?" in warned and str(root) in warned
    assert "Select No to change nothing now." in warned and "stays installed" not in warned

    # Yes twice runs the whole path again, including the stop, with a new token.
    state["held"].clear()
    state.update(choices=["yes", "yes"], task="running")
    result = deleter.run()
    assert state["calls"][:6] == [("lock",), ("outside",), ("pin",), ("stop",), ("task-remove",),
                                  ("write", plan.JOURNAL)]
    assert result["afterExit"]
    assert {path.name for path in root.iterdir()} == kept_names(deleter)
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
        # The question says what this uninstall does, not what is in the folder: nobody knows that here.
        assert not state["questions"] and "This uninstall deletes none of your" in state["notices"][-1][0]
    state["answers"] = [True]
    assert deleter.run()["installationFilesRetained"] is True and not deletes(state)
    assert (root / "shared/keys.env").exists() and (root / plan.JOURNAL).read_text() == '{"schema": 2}'


def test_foreign_top_level_entries_stay_and_then_the_folder_stays(deleter, monkeypatch):
    state, root = deleter.state, deleter.root
    # Names that are distinct from this installation's own on a file system that ignores case, too.
    foreign = {"mine.txt": "not Amplifier's", "my folder/notes.txt": "a folder", "launcher-x/a": "no known form",
               "launcher-2.0.0-0A1B2C3E/a": "not the form of a kept launcher",
               "amplifier_unified-1.0.0-py3-none-any.whl.bak": "w"}
    for name, text in foreign.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    state["choices"] = ["yes", "yes"]
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    names = sorted(top(foreign))
    assert result["kept"] == names and result["afterExit"]
    assert {path.name for path in root.iterdir()} == set(names) | kept_names(deleter)
    assert all((root / name).read_text() == text for name, text in foreign.items())
    # Not deleted here, and not handed to the step after exit.
    handed = state["after"]["folders"]
    assert not set(names) & set(deletes(state)) and not set(names) & set(handed)
    confirmation = state["notices"][-1][0]
    assert "The folder itself stays" in confirmation and "They were not changed" in confirmation
    assert all(name in confirmation for name in names) and "you can delete it" not in confirmation


@pytest.mark.parametrize("name", ["App", "SHARED", "Workspace", "Tools", "Consumer-Install.json",
                                  "Unified-Uninstall.json", "Unified-Uninstall-Last", "Source-Store"])
def test_entry_that_differs_from_an_own_name_only_by_case_deletes_nothing(deleter, name):
    # Windows opens `App` under the name `app`, but it is not the entry that this installation made.
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state.update(choices=["yes", "yes"], also_listed=[name])
    before, registration = snapshot(root), state["registry"].copy()
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "folder" and "it deleted nothing" in stopped.value.notice
    # The person is not left without a way: an uninstall that keeps the folder still works.
    assert "select No in the first question" in stopped.value.notice
    after = snapshot(root)
    after.pop("app/config/services/" + consumer.RECORD)
    assert after == before and not deletes(state) and state["task"] == "running" and state["registry"] == registration
    assert plan.altered(["app", "mine.txt", name, "python", "launcher-x", "launcher-1.0.0-0A1B2C3D"]) == [name]
    assert plan.altered(list(plan._EXACT)) == []


@pytest.mark.parametrize("name,step", [(plan.STARTED, "setup-record"), ("app", "data")])
def test_entry_renamed_only_by_case_after_the_check_counts_as_still_there(deleter, name, step):
    # A program renames the entry after the check. To Windows and to setup it is still the same entry.
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    variant = name[0].upper() + name[1:]
    state["choices"] = ["yes", "yes"]
    state["on_journal"] = lambda: (root / name).rename(root / variant)
    registration = state["registry"].copy()
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == step and isinstance(stopped.value.error, plan.Held)
    assert stopped.value.error.names == [name] and "uninstall Amplifier again" in stopped.value.notice
    # The renamed entry is there, with everything in it. Nothing was deleted past it.
    assert (root / variant).exists() and variant not in deletes(state) and name not in deletes(state)
    if name == plan.STARTED:
        # Setup's completion record stays, so a later setup run sees a finished installation and removes nothing.
        assert deletes(state) == [] and (root / plan.COMPLETE).exists()
    if step == "setup-record":
        assert all((root / entry).exists() for entry in DATA)
    else:
        assert (root / "App/chats/one/message.txt").read_text() == "hello"
        assert "could not be deleted" in stopped.value.notice
    assert state["registry"] == registration and state["after"] is None
    # A second uninstall refuses at its first check and deletes nothing more.
    before = snapshot(root)
    state["choices"] = ["yes", "yes"]
    with pytest.raises(consumer.Unfinished) as again:
        deleter.run()
    assert again.value.step == "folder" and "could not finish its removal" in again.value.notice
    after = snapshot(root)
    after.pop(consumer.RECORD, None), before.pop(consumer.RECORD, None)
    assert after == before


def test_no_entry_shares_delete_while_the_walk_holds_it():
    # Otherwise a program could move an opened file out of the folder, and the walk would delete it there.
    native, calls = object.__new__(removal._Native), []
    native.create = lambda *arguments: calls.append(arguments) or 0
    for enter, delete in ((False, True), (True, True), (True, False)):
        removal._Native.open(native, 1, "entry", enter=enter, delete=delete)
    # FILE_SHARE_READ | FILE_SHARE_WRITE, never FILE_SHARE_DELETE; always the entry itself, never a link's target.
    assert [call[6] for call in calls] == [3, 3, 3] and [call[8] for call in calls] == [0x200020] * 3
    assert [bool(call[1] & 0x10000) for call in calls] == [True, True, False]


def test_kept_file_is_new_unshared_deleted_on_close_and_never_made_behind_a_link():
    native, calls = object.__new__(removal._Native), []
    native.create = lambda *arguments: calls.append(arguments) or 0
    removal._Native.keep(native, 1, "0f" * 16 + ".hold")
    (_, access, _, _, _, attributes, share, disposition, options, _, _), = calls
    # Nobody else can open it at all. Otherwise a program could delete or move it and empty the folder.
    assert share == 0
    # FILE_CREATE: an existing entry of that name refuses; nothing is replaced or reused.
    assert disposition == 2
    # FILE_OPEN_REPARSE_POINT: a link of that name is an existing entry. Without it Windows follows a
    # dangling file link and creates the file at the link's target (measured on the Windows runner).
    assert options & 0x200000
    # FILE_DELETE_ON_CLOSE: Windows deletes it when the last handle closes. A file, never a folder.
    assert options & 0x1000 and options & 0x40 and not options & 0x1
    # DELETE is needed for delete-on-close; FILE_WRITE_DATA lets the step take the handle over as a file.
    assert access == 0x110082 and attributes == 0x80
    for name in ("", ".", "..", "a\\b", "a/b", "file:stream"):
        with pytest.raises(OSError):
            removal._Native.keep(native, 1, name)
    assert len(calls) == 1


def test_new_flat_folder_can_be_taken_back_by_its_creator_only():
    native, calls = object.__new__(removal._Native), []
    native.create = lambda *arguments: calls.append(arguments) or 0
    removal._Native.make(native, 1, "unified-uninstall-last")
    (_, access, _, _, _, _, share, disposition, options, _, _), = calls
    # A new folder (FILE_CREATE), never through a link, shared for read and write but not for delete.
    assert (share, disposition) == (3, 2) and options & 0x200000 and options & 0x1
    assert access & 0x10000 and access & 0x1


def test_the_journal_and_the_running_program_are_not_deleted_by_the_data_step(deleter):
    state, root = deleter.state, deleter.root
    owned = Disk(root, state)
    (root / plan.JOURNAL).write_text("{}")
    assert not any(plan.sort([plan.JOURNAL]).values())
    for names in ([plan.JOURNAL], ["app", plan.JOURNAL], ["mine.txt"], ["python"], ["tools"], [plan.PARK], ["APP"]):
        with pytest.raises(ValueError):
            plan._clear(owned, names, 0, None)
    assert not deletes(state)
    (root / plan.JOURNAL).unlink()
    state["choices"] = ["yes", "yes"]
    deleter.run()
    assert plan.JOURNAL not in deletes(state)
    assert deletes(state)[-2:] == list(plan.RUNNING)
    assert (root / plan.JOURNAL).is_file() and not any((root / name).exists() for name in RUNNING)


def test_emptied_folder_that_windows_keeps_is_not_data_and_a_program_file_in_use_does_not_stop(deleter):
    state, root = deleter.state, deleter.root
    # The launcher's start program keeps `workspace` as its current folder; another program uses a cache file.
    state.update(choices=["yes", "yes"], kept={"workspace"}, held={"cache/wheels/x.whl"})
    result = deleter.run()
    assert result["afterExit"] and result["userDataRetained"] is False
    assert list((root / "workspace").iterdir()) == [] and (root / "cache/wheels/x.whl").exists()
    # The step after exit can remove the cache only when it is empty.
    assert state["after"]["folders"] == plan.last_names()
    assert "files" not in state["after"]
    assert state["registry"] is None


def test_mapped_program_file_is_moved_and_its_folders_go(deleter):
    state, root = deleter.state, deleter.root
    state.update(choices=["yes", "yes"], mapped={"tools/amplifier-unified/Scripts/pythonw.exe"})
    result = deleter.run()
    assert result["afterExit"] and result["programLeft"] == []
    assert not (root / "python").exists() and not (root / "tools").exists()
    assert snapshot(root / plan.PARK) == {"1": b"MZ", deleter.journal()["token"] + ".hold": b""}


def test_program_file_held_without_delete_sharing_stays_in_its_folder(deleter):
    state, root = deleter.state, deleter.root
    state.update(choices=["yes", "yes"], held={"tools/amplifier-unified/Scripts/pythonw.exe"})
    result = deleter.run()
    assert result["afterExit"] and result["programLeft"] == [r"tools\amplifier-unified\Scripts\pythonw.exe"]
    assert (root / "tools/amplifier-unified/Scripts/pythonw.exe").read_text() == "MZ"
    assert snapshot(root / plan.PARK) == {deleter.journal()["token"] + ".hold": b""}


def test_mapped_data_is_never_moved(deleter):
    state, root = deleter.state, deleter.root
    state.update(choices=["yes", "yes"], mapped={"app/chats/one/message.txt"})
    with pytest.raises(deleter.consumer.Unfinished) as stopped:
        deleter.run()
    assert isinstance(stopped.value.error, plan.Held) and stopped.value.step == "data"
    assert (root / "app/chats/one/message.txt").read_text() == "hello"
    assert not (root / plan.PARK).exists() and state["after"] is None


@pytest.mark.parametrize("change", ["another-token", "gone", "another-choice"])
def test_finish_refuses_a_journal_that_is_no_longer_its_own(deleter, change):
    state, root = deleter.state, deleter.root
    token = plan.begin(root)
    if change == "gone":
        (root / plan.JOURNAL).unlink()
    else:
        plan.begin(root, "keep" if change == "another-choice" else "delete")
    before = snapshot(root)
    state["calls"].clear()
    with pytest.raises(ValueError, match="Another removal"):
        plan.finish(root, token)
    assert snapshot(root) == before and not deletes(state) and state["after"] is None
    assert not state["locked"] and not state["pinned"]


@pytest.mark.parametrize("change", ["another-token", "gone"])
def test_public_uninstall_reports_when_its_journal_changed_before_finish(deleter, monkeypatch, change):
    state, root = deleter.state, deleter.root
    finish = plan.finish
    def changed(folder, token, **options):
        if change == "gone":
            (root / plan.JOURNAL).unlink()
        else:
            plan.begin(root)
        return finish(folder, token, **options)
    monkeypatch.setattr(plan, "finish", changed)
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    state["choices"] = ["yes", "yes"]
    result = deleter.run()
    assert not result["afterExit"] and result["programLeft"] is None
    assert len(state["notices"]) == 2 and "could not remove its last program files" in state["notices"][1][0]
    assert all((root / name).exists() for name in RUNNING)


def test_finish_removes_an_earlier_flat_folder_before_making_a_new_one(deleter):
    state, root = deleter.state, deleter.root
    token = plan.begin(root)
    (root / plan.PARK).mkdir()
    (root / plan.PARK / "old").write_text("old program file")
    plan.finish(root, token)
    assert snapshot(root / plan.PARK) == {token + ".hold": b""}
    assert state["calls"].index(("delete", plan.PARK)) < state["calls"].index(("hold", plan.PARK))


def test_finish_stops_when_the_earlier_flat_folder_cannot_go(deleter):
    state, root = deleter.state, deleter.root
    token = plan.begin(root)
    (root / plan.PARK).mkdir()
    state["kept"].add(plan.PARK)
    before = snapshot(root)
    with pytest.raises(FileExistsError):
        plan.finish(root, token)
    assert snapshot(root) == before and state["after"] is None
    assert not set(plan.RUNNING) & set(deletes(state)) and state["holding"] is None


def test_finish_refuses_an_altered_name_before_it_deletes_anything(deleter):
    state, root = deleter.state, deleter.root
    token = plan.begin(root)
    state["also_listed"] = ["Tools"]
    before = snapshot(root)
    with pytest.raises(ValueError, match="differs"):
        plan.finish(root, token)
    assert snapshot(root) == before and not deletes(state) and state["after"] is None


def test_finish_removes_earlier_kept_files_but_not_a_foreign_hold_name(deleter):
    state, root = deleter.state, deleter.root
    token = plan.begin(root)
    earlier = "b" * 32 + ".hold"
    (root / earlier).write_text("earlier")
    (root / "X.hold").write_text("foreign")
    plan.finish(root, token)
    assert not (root / earlier).exists() and (root / "X.hold").read_text() == "foreign"
    assert state["calls"].index(("delete", earlier)) < state["calls"].index(("hold", plan.PARK))
    assert plan.sort([earlier])["program"] == [earlier]
    assert plan.sort(["X.hold", "B" * 32 + ".hold"])["foreign"] == ["X.hold", "B" * 32 + ".hold"]


def _only_store(root, store):
    """Give the installation the one source store that its setup file made."""
    if store != "source-store":
        (root / "source-store").rename(root / store)
        # Setup names the store `s` from this release, and says so in its completion record.
        (root / plan.COMPLETE).write_text('{"schema": 1, "version": "0.98.5"}')
    assert plan.short_store(root) == (store == "s")
    other, = set(STORES) - {store}
    assert (root / store / "objects" / "s").read_text() == "s" and not os.path.lexists(root / other)
    return other


@pytest.mark.parametrize("store", STORES)
def test_delete_everything_removes_the_source_store_of_a_new_and_of_an_earlier_installation(deleter, monkeypatch, store):
    state, root = deleter.state, deleter.root
    other = _only_store(root, store)
    state["choices"] = ["yes", "yes"]
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    # The store is program files: it goes with the caches, after the person's data.
    calls = deletes(state)
    assert "export" in calls and calls.index("export") < calls.index(store) < calls.index("git") and other not in calls
    assert not os.path.lexists(root / store)
    # It is not named as something that is not Amplifier's, so the folder does not stay because of it.
    assert result == {"ok": True, "operation": "uninstall", "userDataRetained": False, "certificate": "none",
                      "kept": [], "programLeft": [], "afterExit": True}
    assert {path.name for path in root.iterdir()} == kept_names(deleter)
    confirmation = state["notices"][-1][0]
    assert "The folder itself stays" not in confirmation and "you can delete it" in confirmation
    # The step after exit is asked about `s` only where setup made it. It removes a named folder only while it is empty.
    folders = state["after"]["folders"]
    assert "source-store" in folders and ("s" in folders) == (store == "s")


def _folder_s_of_someone_else(root, *, store_present=True):
    """An earlier installation that also holds a folder `s` which is not Amplifier's."""
    mine = root / "s" / "deep" / "mine.txt"
    mine.parent.mkdir(parents=True)
    mine.write_text("not Amplifier's")
    if not store_present:
        shutil.rmtree(root / "source-store")  # Something removed this installation's store.
    assert not plan.short_store(root)
    return mine


@pytest.mark.parametrize("store_present", [True, False])
def test_a_folder_s_in_an_earlier_installation_is_never_deleted(deleter, monkeypatch, store_present):
    state, root = deleter.state, deleter.root
    mine = _folder_s_of_someone_else(root, store_present=store_present)
    state["choices"] = ["yes", "yes"]
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    # Not deleted, not handed to the step after exit, and named to the person as something that stays.
    assert "s" not in deletes(state) and "s" not in state["after"]["folders"]
    assert mine.read_text() == "not Amplifier's" and not os.path.lexists(root / "source-store")
    assert result["kept"] == ["s"] and result["afterExit"] and result["programLeft"] == []
    assert {path.name for path in root.iterdir()} == {"s"} | kept_names(deleter)
    confirmation = state["notices"][-1][0]
    assert "The folder itself stays" in confirmation and "\ns\n" in confirmation and "you can delete it" not in confirmation


def test_second_uninstall_of_a_new_installation_finishes_and_deletes_its_store(deleter):
    """The first removal stopped before the program files. Setup's record still says that `s` is the store."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    _only_store(root, "s")
    state.update(choices=["yes", "yes"], held={"app/chats/one/message.txt"})
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "data" and "s" not in deletes(state)
    assert (root / "s" / "objects" / "s").read_text() == "s" and plan.short_store(root)
    state.update(held=set(), choices=["yes", "yes"])
    result = deleter.run()
    assert result == {"ok": True, "operation": "uninstall", "userDataRetained": False, "certificate": "none",
                      "kept": [], "programLeft": [], "afterExit": True}
    assert "s" in deletes(state) and "s" in state["after"]["folders"] and not os.path.lexists(root / "s")
    assert {path.name for path in root.iterdir()} == kept_names(deleter)


def test_second_uninstall_of_an_earlier_installation_still_keeps_a_folder_s(deleter):
    """The first removal deleted the store and then stopped. Setup's record still says which installation this is."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    mine = _folder_s_of_someone_else(root)
    state.update(choices=["yes", "yes"], commit_conflict=True)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "apps-entry"
    assert not os.path.lexists(root / "source-store") and mine.read_text() == "not Amplifier's"
    state.update(commit_conflict=False, choices=["yes", "yes"])
    result = deleter.run()
    assert result["kept"] == ["s"] and result["afterExit"] and "s" not in deletes(state)
    assert mine.read_text() == "not Amplifier's" and "s" not in state["after"]["folders"]
    assert {path.name for path in root.iterdir()} == {"s"} | kept_names(deleter)


def test_the_last_step_does_not_decide_again_whether_s_is_the_installations_own(deleter, monkeypatch):
    """A program rewrites setup's record after the removal decided. The folder `s` is still not handed on."""
    state, root = deleter.state, deleter.root
    mine = _folder_s_of_someone_else(root)
    state.update(choices=["yes", "yes"],
                 on_journal=lambda: (root / plan.COMPLETE).write_text('{"schema": 1, "version": "0.98.5"}'))
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    assert plan.short_store(root) and result["kept"] == ["s"] and "s" not in deletes(state)
    assert "s" not in state["after"]["folders"] and mine.read_text() == "not Amplifier's"
    # Called by itself, the last step treats `s` as not Amplifier's unless it is told otherwise.
    assert "s" not in plan.last_names(Disk(root, state)) and "s" in plan.last_names(Disk(root, state), True)


def test_a_store_file_in_use_in_a_new_installation_is_not_named_as_someone_elses(deleter, monkeypatch):
    """A program still uses a file of the store `s`. The folder stays, but it is Amplifier's own."""
    state, root = deleter.state, deleter.root
    _only_store(root, "s")
    state.update(choices=["yes", "yes"], held={"s/objects/s"})
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    assert (root / "s" / "objects" / "s").exists() and "s" in deletes(state)
    # Not in the list of entries that are not Amplifier's; the step after exit removes it when it is empty.
    assert result["kept"] == [] and "s" in state["after"]["folders"]
    assert "The folder itself stays" not in state["notices"][-1][0]


def test_new_installation_deletes_its_store_and_a_folder_with_the_earlier_name(deleter, monkeypatch):
    """`source-store` is program files by its name, as before: the launcher uses a folder of that name as its store."""
    state, root = deleter.state, deleter.root
    _only_store(root, "s")
    (root / "source-store" / "objects").mkdir(parents=True)
    (root / "source-store" / "objects" / "later").write_text("module source")
    state["choices"] = ["yes", "yes"]
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    assert {"s", "source-store"} <= set(deletes(state)) and result["kept"] == [] and result["programLeft"] == []
    assert {path.name for path in root.iterdir()} == kept_names(deleter)


def test_the_guard_of_every_deletion_refuses_a_folder_s_that_is_not_the_installations_own(deleter):
    state, root = deleter.state, deleter.root
    mine = _folder_s_of_someone_else(root)
    owned, later = Disk(root, state), time.monotonic() + 1
    for names in (["s"], ["cache", "s"], ["s", "source-store"]):
        for short in ((), (False,)):
            with pytest.raises(ValueError):
                plan._clear(owned, names, later, lambda seconds: None, *short)
    assert not deletes(state) and mine.read_text() == "not Amplifier's"
    assert (root / "source-store" / "objects" / "s").read_text() == "s"


@pytest.mark.parametrize("record,short", [
    ('{"schema": 1, "version": "0.98.5"}', True), ('{"schema": 1, "version": "0.98.6"}', True),
    ('{"schema": 1, "version": "0.99.0"}', True), ('{"schema": 1, "version": "1.0.0"}', True),
    ('{"schema": 1, "version": "10.2.3"}', True),
    ('{"schema": 1, "version": "0.98.4"}', False), ('{"schema": 1, "version": "0.9.99"}', False),
    ('{"schema": 1, "version": "0.98"}', False), ('{"schema": 1, "version": "0.98.5rc1"}', False),
    ('{"schema": 1, "version": " 0.98.5"}', False), ('{"schema": 1, "version": "0.98.5.0"}', False),
    ('{"schema": 1, "version": 0.985}', False), ('{"schema": 1, "version": ["0.98.5"]}', False),
    ('{"schema": 1, "version": "\\u0661.\\u0660.\\u0660"}', False), ('{"schema": 1}', False),
    ('{"schema": 2, "version": "0.98.5"}', False), ('{"schema": true, "version": "0.98.5"}', False),
    ("{", False), ('["0.98.5"]', False), (None, False)])
def test_a_folder_s_is_the_installations_own_only_when_setups_record_names_a_release_that_makes_it(deleter, record, short):
    root = deleter.root
    if record is None:
        (root / plan.COMPLETE).unlink()
    else:
        (root / plan.COMPLETE).write_text(record)
    assert plan.short_store(root) is short


@pytest.mark.parametrize("store", STORES)
def test_keep_leaves_the_source_store_of_a_new_and_of_an_earlier_installation(deleter, monkeypatch, store):
    state, root = deleter.state, deleter.root
    _only_store(root, store)
    state["choices"], before = ["no"], snapshot(root)
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    assert deleter.run() == {"ok": True, "operation": "uninstall", "userDataRetained": True,
                             "installationFilesRetained": True, "certificate": "none"}
    assert not deletes(state) and state["after"] is None and not (root / plan.JOURNAL).exists()
    after = snapshot(root)
    after.pop(shell.FILE)
    before.pop(shell.FILE)
    assert after == before and after[store + "/objects/s"] == b"s"


def test_the_two_names_of_the_source_store_in_their_exact_spelling():
    from amplifier_web import windows_consumer as consumer
    # The launcher's two names, and no third one. The earlier name is program files by its name, as before.
    assert STORES == (plan.STORE, "source-store") == (consumer.SOURCE_STORE, consumer.EARLIER_SOURCE_STORE)
    assert "source-store" in plan.CACHES and plan.STORE not in plan.CACHES and plan.STORE_SINCE == (0, 98, 5)
    # The sets of exact names are those of every installation. The folder `s` is added only by `short`.
    assert "source-store" in plan._FOLDERS and "source-store" in plan._EXACT
    assert plan.STORE not in plan._FOLDERS and plan.STORE not in plan._EXACT
    others = ["S", "Source-Store", "ss", "s.txt", "s ", "source-store-2", "source_store", "sourcestore"]
    new = plan.sort(list(STORES) + others, True)
    assert new["program"] == list(STORES) and new["foreign"] == others
    # In an earlier installation, and whenever setup's record does not say otherwise, a folder `s` is not Amplifier's.
    for earlier in (plan.sort(list(STORES) + others), plan.sort(list(STORES) + others, False)):
        assert earlier["program"] == ["source-store"] and earlier["foreign"] == ["s"] + others
        assert sum(map(len, earlier.values())) == len(STORES) + len(others)
    # Another spelling of an own name is not this installation's entry: uninstall then deletes nothing.
    assert plan.altered(list(STORES) + others, True) == ["S", "Source-Store"]
    # In an earlier installation `s` is not an own name, so `S` is only one more entry that is not Amplifier's.
    assert plan.altered(list(STORES) + others) == plan.altered(list(STORES) + others, False) == ["Source-Store"]


def test_a_folder_named_S_refuses_only_where_s_is_the_installations_own(deleter, monkeypatch):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    # An earlier installation: `S` is not Amplifier's. It stays, and the removal goes on.
    state.update(choices=["yes", "yes"], also_listed=["S"])
    result = deleter.run()
    assert result["kept"] == ["S"] and result["afterExit"] and "S" not in deletes(state)
    assert "S" not in state["after"]["folders"] and "s" not in state["after"]["folders"]


def test_a_folder_named_S_in_a_new_installation_deletes_nothing(deleter):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    _only_store(root, "s")
    (root / "s").rename(root / "kept-aside")  # The listing below reports the store under another spelling.
    state.update(choices=["yes", "yes"], also_listed=["S"])
    before = snapshot(root)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "folder" and "it deleted nothing" in stopped.value.notice
    after = snapshot(root)
    after.pop("app/config/services/" + consumer.RECORD)
    assert after == before and not deletes(state) and state["task"] == "running"


def test_last_names_has_every_own_folder_but_never_a_file_or_the_flat_folder(deleter):
    root = deleter.root
    (root / "mine.txt").write_text("foreign")
    for owned in (None, Disk(root, deleter.state)):
        folders = plan.last_names(owned)
        assert set(plan.DATA + plan.RUNNING + plan.CACHES + plan.LAST[:2]) <= set(folders)
        # The folder `s` is in the list only where setup made it.
        assert "s" not in folders and "s" in plan.last_names(owned, True)
        assert set(plan.last_names(owned, True)) - set(folders) == {"s"}
        assert not set(plan.FILES + plan.LAST[2:] + (plan.STARTED, plan.COMPLETE)) & set(folders)
        assert not {plan.JOURNAL, plan.PARK, "mine.txt"} & set(folders)
        assert len(folders) == len(set(folders))
        if owned:
            assert "launcher-1.0.0-0a1b2c3d" in folders
            assert "child-2.stderr.txt" not in folders
    assert plan.sort([plan.PARK])["running"] == [plan.PARK] and plan._folder(plan.PARK)


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
    state.update(commit_conflict=False, link_failure=None, choices=["yes", "yes"])
    assert deleter.run()["afterExit"] and state["registry"] is None and not deleter.folder.exists()
    assert {path.name for path in root.iterdir()} == kept_names(deleter)


def test_folder_that_still_cannot_be_listed_does_not_start_the_last_step(deleter, monkeypatch):
    # The Windows entries are gone by then, so an error would leave the person no way to uninstall again.
    state, root = deleter.state, deleter.root
    state.update(choices=["yes", "yes"], listing_fails=True)
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    assert not result["afterExit"] and result["kept"] == [] and state["registry"] is None
    assert not any((root / name).exists() for name in top(DATA))
    assert state["after"] is None and result["programLeft"] is None
    assert all((root / name).exists() for name in RUNNING)
    confirmation = state["notices"][0][0]
    assert "are deleted" in confirmation and "This folder can still exist afterwards" in confirmation
    assert "you can delete it" not in confirmation
    assert len(state["notices"]) == 2 and "You can delete" not in state["notices"][1][0]


def test_last_deletion_that_stops_with_an_error_gives_the_message_of_a_folder_that_was_not_listed(
        deleter, monkeypatch):
    """Not the listing fails, but the deletion of what the launcher's own check needs, directly before it."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"] = ["yes", "yes"]
    monkeypatch.setattr(consumer.sys, "stdout", None)
    program = plan.remove_program

    def last_refused(owned, *, last=False, **options):
        if last:
            raise OSError(5, "Windows refused the deletion.")
        return program(owned, last=last, **options)
    monkeypatch.setattr(plan, "remove_program", last_refused)
    result = deleter.run()
    # The removal has no last listing. It does not stop: the Windows entries are gone by then.
    assert result["ok"] and result["kept"] == [] and state["registry"] is None
    confirmation = state["notices"][0][0]
    assert confirmation == LAST_MESSAGES["delete, and the folder could not be listed"].format(folder=root)
    assert "This folder can still exist afterwards" in confirmation and "you can delete it" not in confirmation


def test_first_deletion_of_program_files_that_stops_with_an_error_stops_the_removal(deleter, monkeypatch):
    """The removal deletes program files in two attempts. An error of the first one is a failed step."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"] = ["yes", "yes"]
    monkeypatch.setattr(consumer.sys, "stdout", None)
    program = plan.remove_program

    def first_refused(owned, *, last=False, **options):
        if not last:
            raise OSError(5, "Windows refused the deletion.")
        return program(owned, last=last, **options)
    monkeypatch.setattr(plan, "remove_program", first_refused)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    # A failed step after the journal: the text of a removal that did not finish. Every Windows entry is there.
    assert stopped.value.step == "program" and state["registry"] is not None and plan.unfinished(root)
    assert stopped.value.notice == STOPS["removal"] + "\n\nReference: program, OSError"
    assert not any((root / name).exists() for name in top(DATA)) and not state["notices"]


def test_keeping_uninstall_refuses_when_another_window_began_to_delete_after_the_question(deleter):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], registration = ["no"], state["registry"].copy()
    state["on_lock"] = lambda: (root / plan.JOURNAL).write_text(
        json.dumps({"schema": 1, "choice": "delete", "token": "c" * 32}))
    with pytest.raises(consumer.Refused) as refused:
        deleter.run()
    assert refused.value.step == "removing" and state["calls"] == [("lock",), ("unlock",)]
    # The entries stay for the second uninstall, which finishes that removal.
    assert state["task"] == "running" and state["registry"] == registration
    assert {path.name for path in deleter.folder.iterdir()} == set(shell.LINKS)
    state["choices"] = ["yes", "yes"]
    assert deleter.run()["afterExit"] and state["registry"] is None


def test_journal_that_a_program_wrote_never_replaces_one_of_the_two_choices(deleter):
    # The journal is a private file, but a program of this account can write it. It is not the person's choice.
    state, root = deleter.state, deleter.root
    (root / plan.JOURNAL).write_text(json.dumps({"schema": 1, "choice": "delete", "token": "e" * 32}))
    before, registration = snapshot(root), state["registry"].copy()
    for choices in (["no"], ["yes", "no"]):
        state["choices"] = list(choices)
        assert deleter.run() == {"ok": True, "cancelled": True}
        assert not state["calls"] and snapshot(root) == before and state["registry"] == registration
    assert [cancel for _, cancel in state["questions"]] == [False, False, False]
    # Only two answers of Yes, given now, delete.
    state["choices"] = ["yes", "yes"]
    assert deleter.run()["afterExit"] and len(state["questions"]) == 5
    assert {path.name for path in root.iterdir()} == kept_names(deleter)


def test_journal_write_waits_for_a_closing_app_and_then_refuses(model, monkeypatch):
    root, _, state = model
    monkeypatch.setattr(shell, "SETTLE_SECONDS", 15)
    attempts, waits = [], []
    state["refuse_write"] = lambda path: attempts.append(path.name) or len(attempts) <= 2
    token = plan.begin(root, wait=waits.append)
    assert waits == [0.5, 0.5] and attempts == [plan.JOURNAL] * 3 and re.fullmatch("[0-9a-f]{32}", token)
    assert json.loads((root / plan.JOURNAL).read_text()) == {"schema": 1, "choice": "delete", "token": token}
    # Another choice saves what it needs to continue in the same record; a new write has a new token.
    kept = plan.begin(root, "keep", wait=waits.append, transfer="d" * 32)
    assert kept != token and json.loads((root / plan.JOURNAL).read_text()) == {
        "schema": 1, "choice": "keep", "token": kept, "transfer": "d" * 32}
    # A refusal that does not end is the failed step `journal`; nothing was deleted before it.
    monkeypatch.setattr(shell, "SETTLE_SECONDS", 0)
    state["refuse_write"] = lambda path: True
    with pytest.raises(PermissionError):
        plan.begin(root, wait=waits.append)
    assert len(waits) == 2


def test_both_questions_have_no_as_their_default_and_only_the_first_can_be_cancelled(monkeypatch):
    from types import SimpleNamespace
    shown, answers = [], [6, 7, 2, 6, 7, 2, 0]
    def dialog(window, text, title, flags):
        shown.append((window, text, title, flags))
        return answers.pop(0)
    monkeypatch.setattr(plan, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(plan.C, "WinDLL", lambda name, **options: SimpleNamespace(MessageBoxW=dialog), raising=False)
    assert [plan.question("one", cancel=True) for _ in range(3)] == ["yes", "no", "cancel"]
    # Without Cancel, anything but Yes is No.
    assert [plan.question("two") for _ in range(3)] == ["yes", "no", "no"]
    with pytest.raises(OSError):
        plan.question("not shown")
    first, second = shown[0][3], shown[3][3]
    # Buttons: Yes/No/Cancel and Yes/No. Icons: information and warning.
    assert (first & 0xF, first & 0xF0) == (0x3, 0x40) and (second & 0xF, second & 0xF0) == (0x4, 0x30)
    for flags in (first, second):
        assert flags & 0xF00 == 0x100, "the second button, No, is the default"
        assert flags & 0x51000 == 0x51000, "in front of other windows, as every launcher message"
    assert shown[0][:3] == (None, "one", "Amplifier Unified")


@pytest.mark.parametrize("error", [OSError("Cannot locate Windows PowerShell."),
                                  ValueError("Journal changed"), RuntimeError("Cannot start")])
def test_step_after_exit_that_cannot_start_is_said_plainly(deleter, monkeypatch, error):
    state = deleter.state
    state["choices"], state["after_failure"] = ["yes", "yes"], error
    monkeypatch.setattr(deleter.consumer.sys, "stdout", None)
    result = deleter.run()
    assert result["ok"] and result["afterExit"] is False and result["programLeft"] is None
    confirmation, leftover = (text for text, _ in state["notices"])
    assert "are deleted" in confirmation and "Amplifier could not remove its last program files" in leftover
    assert "Windows" not in leftover  # The cause can also be another Amplifier window.
    assert "none of your chats, settings or accounts" in leftover and str(deleter.root) in leftover
    assert leftover.endswith("You can delete that folder.")
    # With something in the folder that is not Amplifier's, there is no advice to delete the folder.
    state.update(choices=["yes", "yes"], notices=[])
    (deleter.root / "mine.txt").write_text("not Amplifier's")
    assert deleter.run()["kept"] == ["mine.txt"]
    assert "You can delete" not in state["notices"][-1][0] and "You can delete" not in state["notices"][0][0]


def test_data_removal_gives_a_closing_program_time_and_then_stops(deleter):
    state, root = deleter.state, deleter.root
    owned, waits = Disk(root, state), []
    (root / plan.JOURNAL).write_text("{}")
    state["held"] = {"app/chats/one/message.txt", "shared/keys.env"}
    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            state["held"].discard("shared/keys.env")
        if len(waits) == 3:
            state["held"].clear()
    # Before the deadline every pass tries again only what still holds something, in the same order.
    assert plan._clear(owned, plan.DATA, time.monotonic() + 3600, wait) == [] and waits == [0.5, 0.5, 0.5]
    assert deletes(state) == [*plan.DATA, "shared", "app", "shared", "app", "app"]
    # After the deadline there is exactly one pass and no wait.
    state["calls"].clear()
    (root / "app").mkdir()
    (root / "app" / "busy.db").write_text("in use")
    state["held"] = {"app/busy.db"}
    assert plan._clear(owned, ["app"], 0, None) == ["app"] and deletes(state) == ["app"]


def launch(deleter, monkeypatch):
    from amplifier_web import windows_installation
    state, consumer = deleter.state, deleter.consumer
    steps = []
    async def prepare(root, launcher, env, *, identity, progress):
        assert state["locked"], "the start step runs under the installation's lock"
        # The caller's own step record reaches the start step, as on main.
        assert progress == steps.append
        state["calls"].append(("prepare",))
        return {"background": True, "created": False, "health": {"ok": True}}
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    return lambda: asyncio.run(consumer.launch(deleter.root, open_browser=False, progress=steps.append))


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
    # A lock that cannot be taken at all is a failure with its own text, never "in another window".
    state.update(busy=False, lock_failure=OSError(5, "The lock could not be created"), choices=["no"])
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "lock" and not isinstance(stopped.value, consumer.Refused)
    assert stopped.value.notice.startswith("This uninstall stopped before it changed anything.\n\n")
    assert "in another window. Restart" in stopped.value.notice
    assert "right now" not in stopped.value.notice and stopped.value.notice.endswith("Reference: lock, OSError")
    # Only the private note of the failure was written.
    assert state["calls"] == [("write", "app/config/services/" + consumer.RECORD)]
    assert state["task"] == "running" and state["registry"] is not None


# The step `journal` can fail in four ways. After two of them a journal is in the folder (Amplifier does not
# open), after two it is not (Amplifier opens). The program cannot tell them apart when it writes its text.
JOURNAL_WAYS = ("refused", "published", "lost", "unseen")


def journal_stops(deleter, monkeypatch, way):
    """Make the next write of the journal fail in this way."""
    state, root = deleter.state, deleter.root
    if way == "refused":
        # Windows refuses the write before the file has its name.
        state["refuse_write"] = lambda path: path.name == plan.JOURNAL
    elif way == "published":
        # The private writer reports an error after the file has its name (`publish_private`:
        # "a reported error is not a promise of an unchanged destination").
        def failed():
            raise OSError("private file was published; post-publication verification failed")
        state["on_journal"] = failed
    elif way == "lost":
        # A program takes the name away directly after the write.
        state["on_journal"] = lambda: (root / plan.JOURNAL).unlink()
    else:
        # Windows refuses the one look for the journal. The journal is in the folder.
        def unseen():
            real = plan.unfinished
            def once(folder):
                monkeypatch.setattr(plan, "unfinished", real)
                return False
            monkeypatch.setattr(plan, "unfinished", once)
        state["on_journal"] = unseen


def opens(deleter, monkeypatch):
    """Whether Open starts Amplifier from this folder now, as a person selects it."""
    state, consumer = deleter.state, deleter.consumer
    with monkeypatch.context() as patch:
        start = launch(deleter, patch)
        state["calls"].clear()
        try:
            start()
        except consumer.Refused as refused:
            assert refused.step == "removing" and ("prepare",) not in state["calls"]
            return False
        assert ("prepare",) in state["calls"]
        return True


@pytest.mark.parametrize("way", JOURNAL_WAYS)
def test_first_uninstall_that_stops_at_the_journal_does_not_say_whether_amplifier_opens(deleter, monkeypatch, way):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], state["certificate"] = ["yes", "yes"], b"this installation's root"
    journal_stops(deleter, monkeypatch, way)
    before, registration = snapshot(root), state["registry"].copy()
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "journal" and not isinstance(stopped.value, consumer.Refused)
    error = {"refused": "PermissionError"}.get(way, "OSError")
    assert stopped.value.notice == consumer._UNFINISHED["journal"] + "\n\nReference: journal, " + error
    # What the text says is true in each of the four ways: stopped, no sign-in start, nothing deleted.
    assert state["task"] == "absent" and not deletes(state) and state["after"] is None
    after = snapshot(root)
    assert after.pop("app/config/services/" + consumer.RECORD)
    present = after.pop(plan.JOURNAL, None) is not None
    assert after == before and state["registry"] == registration
    assert "This uninstall deleted nothing in the folder." in stopped.value.notice
    # The same text appears with a journal in the folder and without one.
    assert present is (way in {"published", "unseen"}) and plan.unfinished(root) is present
    # With the journal Open refuses; without it Open starts. So the text promises neither.
    assert opens(deleter, monkeypatch) is not present
    for claim in ("open it from the Start menu", "keep using", "does not open"):
        assert claim not in stopped.value.notice
    assert stopped.value.notice.count("Amplifier might not open until then.") == 1
    # The next step of the text works in each way: the same command deletes the folder. (Windows deleted
    # the certificate at the person's Yes, so nothing of this installation is trusted any more.)
    state.update(refuse_write=None, choices=["yes", "yes"], certificate=None)
    assert deleter.run()["afterExit"]
    assert ("resumed" if present else "first") == (
        "resumed" if "did not finish" in state["questions"][-2][0] else "first")
    assert {path.name for path in root.iterdir()} == kept_names(deleter)


@pytest.mark.parametrize("way", JOURNAL_WAYS)
def test_second_uninstall_that_stops_at_the_journal_does_not_say_that_amplifier_stays_closed(
        deleter, monkeypatch, way):
    """An earlier uninstall deleted data and stopped. This one stops when it writes its new journal."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], state["held"] = ["yes", "yes"], {"app/chats/one/message.txt"}
    with pytest.raises(consumer.Unfinished):
        deleter.run()
    earlier = deleter.journal()
    assert not (root / "shared").exists() and earlier["choice"] == "delete"
    state["held"].clear()
    state["choices"] = ["yes", "yes"]
    state["calls"].clear()
    journal_stops(deleter, monkeypatch, way)
    before = snapshot(root)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "journal" and not deletes(state) and state["after"] is None
    error = {"refused": "PermissionError"}.get(way, "OSError")
    assert stopped.value.notice == consumer._UNFINISHED["removal"] + "\n\nReference: journal, " + error
    # Never the text of a first uninstall: files of this folder are already deleted.
    assert "deleted nothing" not in stopped.value.notice
    after = snapshot(root)
    assert json.loads(after.pop(consumer.RECORD))["step"] == "journal" and before.pop(consumer.RECORD)
    now = after.pop(plan.JOURNAL, None)
    assert before.pop(plan.JOURNAL) and after == before
    # The earlier journal stays when the write is refused. A new one is there after an error that follows
    # the publication, and when the look is refused. No journal is there when a program took it away.
    present = now is not None
    assert present is (way != "lost")
    assert (json.loads(now)["token"] == earlier["token"]) is (way == "refused") if present else True
    # Without the journal Open starts, on a folder that is partly deleted (the design's limit "the
    # journal is not held open"). So this text must not say that Amplifier does not open.
    assert opens(deleter, monkeypatch) is not present
    assert "does not open" not in stopped.value.notice and "Start menu" not in stopped.value.notice
    assert "Do not open Amplifier before that: its folder can be partly deleted." in stopped.value.notice
    # The next step of the text works in each way.
    state.update(refuse_write=None, choices=["yes", "yes"])
    assert deleter.run()["afterExit"]
    assert {path.name for path in root.iterdir()} == kept_names(deleter)


@pytest.mark.parametrize("foreign", [json.dumps({"schema": 1, "choice": "delete", "token": "f" * 32}), "{"])
def test_journal_that_a_program_replaces_in_the_instant_of_the_write_never_deletes_more_than_the_choice(
        deleter, monkeypatch, foreign):
    """The name exists, so the removal goes on. Its last step refuses a journal that is not its own."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"] = ["yes", "yes"]
    state["on_journal"] = lambda: (root / plan.JOURNAL).write_text(foreign)
    (root / "mine.txt").write_text("not Amplifier's")
    monkeypatch.setattr(consumer.sys, "stdout", None)
    result = deleter.run()
    # The person chose the deletion twice: the data and the program files went, and the Windows entries.
    assert result["ok"] and result["userDataRetained"] is False and result["kept"] == ["mine.txt"]
    assert not any((root / name).exists() for name in top(DATA) | top(PROGRAM) | top(LAST))
    assert state["registry"] is None and not deleter.folder.exists()
    # The last step saw another journal. It deleted nothing and did not start the step after exit.
    assert result["afterExit"] is False and result["programLeft"] is None and state["after"] is None
    assert {path.name for path in root.iterdir()} == {plan.JOURNAL, plan.COMPLETE, "mine.txt", *top(RUNNING)}
    assert (root / plan.JOURNAL).read_text() == foreign and (root / "mine.txt").read_text() == "not Amplifier's"
    # Both messages are true here: the data is deleted, and the last program files stay.
    (confirmation, _), (leftover, _) = state["notices"]
    assert "Your chats, settings, saved accounts and stored keys are deleted" in confirmation
    # One entry is not Amplifier's, so the text does not say that the folder can be deleted.
    assert leftover == LAST_MESSAGES["the last step could not start, entries that are not Amplifier's"].format(
        folder=root)
    # The name is there, so Amplifier does not open from this folder.
    assert opens(deleter, monkeypatch) is False
    # No second uninstall follows from here (the design's L3): no Windows entry is left to start one from, and
    # the records that the launcher's own check reads are deleted.
    assert state["registry"] is None and not deleter.folder.exists()
    assert not any((root / name).exists() for name in top(LAST))


def test_journal_that_a_program_replaces_in_the_instant_of_the_write_and_a_program_file_in_use_stays(
        deleter, monkeypatch):
    """The removal goes on to its end. It only tries to delete the program files: one that a program uses stays."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    foreign = json.dumps({"schema": 1, "choice": "delete", "token": "f" * 32})
    state.update(choices=["yes", "yes"], held={"cache/wheels/x.whl"})
    state["on_journal"] = lambda: (root / plan.JOURNAL).write_text(foreign)
    monkeypatch.setattr(consumer.sys, "stdout", None)
    result = deleter.run()
    # The person's data and the Windows entries went.
    assert result["ok"] and result["userDataRetained"] is False and result["kept"] == []
    assert not any((root / name).exists() for name in top(DATA)) and state["registry"] is None
    assert not deleter.folder.exists()
    # The program file in use stayed, and nothing stopped for it. The last step refused the other journal.
    assert (root / "cache/wheels/x.whl").exists()
    assert result["afterExit"] is False and result["programLeft"] is None and state["after"] is None
    assert {path.name for path in root.iterdir()} == {plan.JOURNAL, plan.COMPLETE, "cache", *top(RUNNING)}
    (confirmation, _), (leftover, _) = state["notices"]
    assert "Your chats, settings, saved accounts and stored keys are deleted" in confirmation
    assert leftover.startswith("Amplifier could not remove its last program files. They are in this folder, and "
                               "they hold none of your chats, settings or accounts:")


def test_journal_that_a_program_deletes_during_the_removal_stops_it_at_its_last_step(deleter, monkeypatch):
    """The journal was in the folder at the look. A program deletes it before the removal deletes the data."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"] = ["yes", "yes"]
    forget = plan.forget_setup

    def after_the_look(owned, **options):
        # The step after the journal: the uninstall's look found the journal a moment ago.
        assert deleter.journal()["choice"] == "delete" and not deletes(state)
        (root / plan.JOURNAL).unlink()
        state["journal_taken"] = True
        return forget(owned, **options)
    monkeypatch.setattr(plan, "forget_setup", after_the_look)
    monkeypatch.setattr(consumer.sys, "stdout", None)
    result = deleter.run()
    # The removal went on as with its journal: the data, the program files and the Windows entries went.
    assert result["ok"] and result["userDataRetained"] is False and result["kept"] == []
    assert not any((root / name).exists() for name in top(DATA) | top(PROGRAM) | top(LAST))
    assert state["registry"] is None and not deleter.folder.exists()
    # The last step found no journal. It deleted nothing and did not start the step after exit.
    assert result["afterExit"] is False and result["programLeft"] is None and state["after"] is None
    assert {path.name for path in root.iterdir()} == {plan.COMPLETE, *top(RUNNING)}
    (confirmation, _), (leftover, _) = state["notices"]
    assert confirmation == LAST_MESSAGES["delete"].format(folder=root)
    assert leftover == LAST_MESSAGES["the last step could not start"].format(folder=root)


@pytest.mark.parametrize("foreign", [json.dumps({"schema": 1, "choice": "delete", "token": "f" * 32}), "{"])
def test_second_uninstall_after_a_replaced_journal_reads_the_other_programs_journal(deleter, foreign):
    """A program replaced the journal, and a step of that removal failed. The next uninstall reads that file."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state.update(choices=["yes", "yes"], held={"app/chats/one/message.txt"})
    state["on_journal"] = lambda: (root / plan.JOURNAL).write_text(foreign)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    # The removal went on with the other file, deleted the stored keys and stopped at a data file in use.
    assert stopped.value.step == "data" and not (root / "shared").exists()
    assert (root / plan.JOURNAL).read_text() == foreign
    # Every Windows entry is still there, and the records that the launcher's own check reads.
    assert state["registry"] is not None and deleter.folder.exists()
    assert all((root / name).exists() for name in top(LAST))
    state["held"].clear()
    state.update(questions=[], notices=[])
    if foreign == "{":
        # The file proves no delete choice (L4): only the keeping uninstall is offered, and it says what it does.
        state["answers"] = [False]
        assert deleter.run() == {"ok": True, "cancelled": True}
        (question, confirm), = state["notices"]
        assert confirm and not state["questions"] and question.startswith("Uninstall Amplifier?")
        assert "This uninstall deletes none of your chats, settings, accounts or program files" in question
        assert (root / plan.JOURNAL).read_text() == foreign
    else:
        # The file records the delete choice: the person chooses again, twice, and the removal finishes.
        state["choices"] = ["yes", "yes"]
        assert deleter.run()["afterExit"]
        (first, _), (second, _) = state["questions"]
        assert first.startswith("The removal of Amplifier from this PC did not finish.")
        assert second.startswith("Delete everything that Amplifier keeps in this folder?")
        assert deleter.journal()["token"] != "f" * 32
        assert {path.name for path in root.iterdir()} == kept_names(deleter)


@pytest.mark.parametrize("journal", ["delete", "damaged"])
def test_uninstall_whose_look_before_its_questions_misses_a_journal_asks_as_for_a_folder_without_one(
        deleter, monkeypatch, journal):
    """A removal stopped and its journal is in the folder. Windows does not answer the next uninstall's looks."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    earlier_removal(deleter)
    if journal == "damaged":
        (root / plan.JOURNAL).write_text("{")
    before, real, looks = (root / plan.JOURNAL).read_text(), plan.unfinished, []

    def unanswered(folder):
        # The look of `maintenance` and the look of `_uninstall` before its questions: no for every error.
        looks.append(folder)
        if len(looks) == 2:
            monkeypatch.setattr(plan, "unfinished", real)
        return False
    monkeypatch.setattr(plan, "unfinished", unanswered)
    state.update(choices=["cancel"], questions=[], notices=[])
    assert deleter.run() == {"ok": True, "cancelled": True} and len(looks) == 2
    # The question of a folder without a journal: three buttons, and nothing about a removal that did not
    # finish. The uninstall did not read the file. (`environment` is a stand-in in these cases; on a real
    # folder that check comes before the questions and can fail on a folder that is partly deleted.)
    (first, cancel), = state["questions"]
    assert cancel and not state["notices"] and first.startswith("Uninstall Amplifier?\n\n")
    assert "Do you also want to delete your chats, settings and saved accounts?" in first
    assert "did not finish" not in first and "This uninstall deletes none" not in first
    assert (root / plan.JOURNAL).read_text() == before and plan.unfinished(root)


def test_journal_that_a_program_replaces_with_the_same_choice_and_token_ends_as_without_a_replacement(
        deleter, monkeypatch):
    """The last step refuses a journal by what it records. Another file with the same record passes."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"], written = ["yes", "yes"], []

    def replace():
        path = root / plan.JOURNAL
        record = json.loads(path.read_text())
        written.append(path.read_text())
        path.unlink()
        path.write_text(json.dumps(dict(reversed(list(record.items()))), indent=2))
    state["on_journal"] = replace
    monkeypatch.setattr(consumer.sys, "stdout", None)
    result = deleter.run()
    # Another file, with other bytes and the same schema, choice and token.
    assert (root / plan.JOURNAL).read_text() != written[0] and deleter.journal() == json.loads(written[0])
    # The last step took it for this removal's journal: it started the step after exit with that token. The
    # step after exit is a stand-in here; what the real one does with the file is not shown.
    assert result == {"ok": True, "operation": "uninstall", "userDataRetained": False, "certificate": "none",
                      "kept": [], "afterExit": True, "programLeft": []}
    assert state["after"]["token"] == deleter.journal()["token"]
    assert {path.name for path in root.iterdir()} == kept_names(deleter)
    (confirmation, _), = state["notices"]
    assert confirmation == consumer._removed_notice(root, [], "none")


@pytest.mark.parametrize("failure", ["task-remove", "apps-entry"])
def test_keeping_uninstall_that_stops_with_a_journal_in_the_folder_does_not_send_the_person_to_open(
        deleter, monkeypatch, failure):
    """A journal that proves no delete choice (damaged): only the keeping uninstall is offered. Open refuses."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    (root / plan.JOURNAL).write_text("{")
    state["answers"] = [True]
    if failure == "task-remove":
        state["task_failure"] = failure
    else:
        state["commit_conflict"] = True
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == failure and not state["questions"] and not deletes(state)
    error = "WindowsServiceError" if failure == "task-remove" else "OSError"
    assert stopped.value.notice == consumer._UNFINISHED["removal"] + f"\n\nReference: {failure}, {error}"
    # The step's own text ends "To keep using Amplifier, open it from the Start menu." Open refuses here.
    assert consumer._UNFINISHED[failure].endswith("To keep using Amplifier, open it from the Start menu.")
    assert opens(deleter, monkeypatch) is False
    # The question said only what this uninstall does: an earlier removal can have deleted files.
    (question, confirm), = state["notices"]
    assert confirm and "This uninstall deletes none of your chats, settings, accounts or program files" in question
    assert "stay in this folder" not in question
    # Without a journal the step's own text is the right one, and Open starts.
    (root / plan.JOURNAL).unlink()
    state.update(answers=[True], notices=[], choices=["no"], task="running")
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.notice == consumer._UNFINISHED[failure] + f"\n\nReference: {failure}, {error}"
    assert opens(deleter, monkeypatch) is True


def test_keeping_uninstall_with_a_journal_in_the_folder_says_only_what_it_did(deleter, monkeypatch):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    shutil.rmtree(root / "shared")  # An earlier removal deleted the stored keys. Then its journal was damaged.
    (root / plan.JOURNAL).write_text("{")
    state["answers"] = [True]
    monkeypatch.setattr(consumer.sys, "stdout", None)
    assert deleter.run()["installationFilesRetained"] is True and not deletes(state)
    (question, _), (confirmation, _) = state["notices"]
    for text, verb in ((question, "deletes"), (confirmation, "deleted")):
        assert f"This uninstall {verb} none of your chats, settings, accounts or program files in this folder:" in text
        assert "stay in this folder" not in text and "are still in this folder" not in text


@pytest.mark.parametrize("journal", [True, False])
def test_uninstall_that_cannot_check_its_installation_does_not_say_that_amplifier_can_be_used(
        deleter, monkeypatch, journal):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    if journal:
        (root / plan.JOURNAL).write_text(json.dumps({"schema": 1, "choice": "delete", "token": "a" * 32}))
    def unreadable(folder):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(consumer, "installation", unreadable)
    before = snapshot(root)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "preparation" and snapshot(root) == before and not state["calls"]
    assert stopped.value.notice.startswith("This uninstall stopped before it changed anything.\n\n"
                                           "Amplifier could not check its own installation files.")
    # Open needs the same check, so it fails here too. (What Open does with a journal when this check passes:
    # `opens` in the cases of a stop at the journal.)
    with pytest.raises(PermissionError):
        asyncio.run(consumer.launch(root, open_browser=False))
    assert "keep using" not in stopped.value.notice and "open" not in stopped.value.notice


def earlier_removal(deleter):
    """An uninstall with two Yes answers that deletes the stored keys and stops at a data file in use."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state.update(choices=["yes", "yes"], held={"app/chats/one/message.txt"}, questions=[])
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "data" and not (root / "shared").exists()
    assert deleter.journal()["choice"] == "delete"
    state["held"].clear()
    state["calls"].clear()


def other_window_during_the_first_question(deleter, monkeypatch, then):
    """Another Amplifier window runs `earlier_removal` while this window's first question waits.

    `then()` runs directly afterwards, before this window's question returns its answer.
    """
    state, root = deleter.state, deleter.root
    ask, failed = plan.question, []

    def other_window():
        try:
            earlier_removal(deleter)
        except BaseException as error:
            failed.append(error)

    def question(text, *, cancel=False):
        if cancel:  # This window's first question is on screen. The other window runs now, to its stop.
            assert not plan.unfinished(root)
            monkeypatch.setattr(plan, "question", ask)
            window = threading.Thread(target=other_window)
            window.start()
            window.join(60)
            assert not window.is_alive() and not failed, failed
            state["questions"] = []
            then()
        return ask(text, cancel=cancel)
    monkeypatch.setattr(plan, "question", question)


@pytest.mark.parametrize("earlier", ["journal-deleted", "other-window"])
def test_uninstall_that_stops_at_its_journal_after_an_earlier_removal_that_its_questions_did_not_see(
        deleter, monkeypatch, earlier):
    """An earlier uninstall deleted data and stopped. This one asked its questions as for a whole installation.

    `journal-deleted`: a program of this account deleted the earlier journal (the design's limit "the journal
    is not held open"). Nothing shows this uninstall the earlier removal, so its text speaks only of itself.
    `other-window`: another Amplifier window ran that uninstall while the first question of this one waited.
    Its journal is in the folder when this uninstall is about to write its own, and the text says so.
    """
    state, root, consumer = deleter.state, deleter.root, deleter.consumer

    def this_window():
        state["choices"] = ["yes", "yes"]
        journal_stops(deleter, monkeypatch, "refused")

    if earlier == "journal-deleted":
        earlier_removal(deleter)
        (root / plan.JOURNAL).unlink()
        (root / consumer.RECORD).unlink()  # The note of the earlier stop: this case looks at the note of this one.
        state["questions"] = []
        this_window()
    else:
        other_window_during_the_first_question(deleter, monkeypatch, this_window)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    # Nothing showed this program the earlier removal when it asked: the questions of a whole installation.
    (first, cancel), (second, _) = state["questions"]
    assert cancel and "Do you also want to delete your chats, settings and saved accounts?" in first
    assert "Select No to change nothing. Amplifier stays installed." in second
    assert stopped.value.step == "journal" and not deletes(state) and not (root / "shared").exists()
    notice = stopped.value.notice
    if earlier == "journal-deleted":
        assert notice == consumer._UNFINISHED["journal"] + "\n\nReference: journal, PermissionError"
        # The stored keys went earlier. So the text speaks of this uninstall, not of the folder.
        assert "This uninstall deleted nothing in the folder." in notice
        assert "Nothing in the folder was deleted" not in notice
        # No journal is in the folder, so the launcher does not refuse. The text promised neither.
        assert opens(deleter, monkeypatch) is True and notice.count("Amplifier might not open until then.") == 1
        assert json.loads((root / "app/config/services" / consumer.RECORD).read_text())["step"] == "journal"
    else:
        # The other window's journal was in the folder before the write: files can already be deleted.
        assert notice == consumer._UNFINISHED["removal"] + "\n\nReference: journal, PermissionError"
        assert "deleted nothing" not in notice and "might not open" not in notice
        assert opens(deleter, monkeypatch) is False
        # The note of the failure is in the folder itself, never in the app's folder, which is being deleted.
        assert json.loads((root / consumer.RECORD).read_text())["step"] == "journal"
        assert not (root / "app/config").exists()


def test_question_that_windows_does_not_show_after_another_window_began_a_removal_keeps_its_note_out_of_app(
        deleter, monkeypatch):
    """Another window deleted data and stopped while the first question waited. Windows does not show the second."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer

    def this_window():
        state["choices"] = ["yes"]
        ask = plan.question

        def refuse(text, *, cancel=False):
            if text.startswith("Delete everything"):
                raise OSError("The uninstall question could not be displayed.")
            return ask(text, cancel=cancel)
        monkeypatch.setattr(plan, "question", refuse)
    other_window_during_the_first_question(deleter, monkeypatch, this_window)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    # No journal was in the folder when this uninstall began, and one is there now: the other window's.
    assert stopped.value.step == "questions" and plan.unfinished(root) and not deletes(state)
    # The text says only what this uninstall did.
    assert stopped.value.notice == consumer._UNFINISHED["questions"] + "\n\nReference: questions, OSError"
    # The note of the failure is in the folder itself, never in the app's folder, which is being deleted.
    assert json.loads((root / consumer.RECORD).read_text())["step"] == "questions"
    assert not (root / "app/config").exists()


@pytest.mark.parametrize("choice,failure", [("delete", "lock"), ("delete", "folder"), ("keep", "lock")])
def test_uninstall_that_stops_early_after_another_window_began_a_removal_does_not_say_that_nothing_changed(
        deleter, monkeypatch, choice, failure):
    """Another window deleted data and stopped while the first question waited. Then this uninstall fails early.

    This uninstall has written no journal, so the journal in the folder is the other window's. The step's
    own text says that this uninstall "stopped before it changed anything", which says too little, and the
    launcher refuses to open Amplifier. The person gets the text of a removal that did not finish.
    """
    state, root, consumer = deleter.state, deleter.root, deleter.consumer

    def this_window():
        state["choices"] = ["yes", "yes"] if choice == "delete" else ["no"]
        if failure == "lock":
            state["lock_failure"] = OSError(5, "The lock could not be created")
        else:
            state["pin_failure"] = PermissionError(5, "Access is denied")
    other_window_during_the_first_question(deleter, monkeypatch, this_window)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == failure and not isinstance(stopped.value, consumer.Refused)
    assert not deletes(state) and plan.unfinished(root) and not (root / "shared").exists()
    error = "OSError" if failure == "lock" else "PermissionError"
    assert stopped.value.notice == consumer._UNFINISHED["removal"] + f"\n\nReference: {failure}, {error}"
    assert "before it changed anything" not in stopped.value.notice
    # The step's own text is the one for a folder in which no removal began.
    assert consumer._UNFINISHED[failure].startswith("This uninstall stopped before it changed anything.")
    # The note of the failure is in the folder itself, never in the app's folder, which is being deleted.
    assert json.loads((root / consumer.RECORD).read_text())["step"] == failure
    assert not (root / "app/config").exists()
    state.update(lock_failure=None, pin_failure=None)
    assert opens(deleter, monkeypatch) is False


@pytest.mark.parametrize("retry", [False, True])
def test_start_entry_that_is_not_confirmed_after_another_window_began_a_removal_does_not_say_that_nothing_changed(
        deleter, monkeypatch, retry):
    """The delete choice. Another window deleted data and stopped while the first question waited."""
    from amplifier_web import windows_service
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    entry = "AmplifierUnified-S-1-5-21-1-2-3-1001"

    def this_window():
        state["choices"] = ["yes", "yes"]

        def refuse(home, *, progress):
            progress("task-read")
            raise windows_service.TaskNotConfirmed(entry, retry=retry)
        monkeypatch.setattr(windows_service, "uninstall", refuse)
    other_window_during_the_first_question(deleter, monkeypatch, this_window)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "task-read" and not deletes(state) and plan.unfinished(root)
    notice = stopped.value.notice
    assert notice == consumer.entry_notice("removal", entry, retry) + "\n\nReference: task-read, TaskNotConfirmed"
    assert notice.startswith("Amplifier could not finish its removal from this PC. Do not open Amplifier "
                             "before it has finished: its folder can be incomplete.\n\n")
    assert "before it changed anything" not in notice
    # The note of the failure is in the folder itself, never in the app's folder, which is being deleted.
    assert json.loads((root / consumer.RECORD).read_text())["step"] == "task-read"
    assert not (root / "app/config").exists()


@pytest.mark.parametrize("retry", [False, True])
def test_start_entry_that_is_not_confirmed_in_a_keeping_uninstall_with_a_journal_does_not_say_that_nothing_changed(
        deleter, monkeypatch, retry):
    """A journal that proves no delete choice (damaged): the keeping uninstall stops at the start entry."""
    from amplifier_web import windows_service
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    entry = "AmplifierUnified-S-1-5-21-1-2-3-1001"
    (root / plan.JOURNAL).write_text("{")
    state["answers"] = [True]

    def refuse(home, *, progress):
        progress("task-read")
        raise windows_service.TaskNotConfirmed(entry, retry=retry)
    monkeypatch.setattr(windows_service, "uninstall", refuse)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "task-read" and not state["questions"] and not deletes(state)
    notice = stopped.value.notice
    # The opening of a removal that did not finish, with the cause and the way out of this cause.
    assert notice.startswith("Amplifier could not finish its removal from this PC. Do not open Amplifier "
                             "before it has finished: its folder can be incomplete.\n\n")
    assert notice == consumer.entry_notice("removal", entry, retry) + "\n\nReference: task-read, TaskNotConfirmed"
    assert "before it changed anything" not in notice
    assert ("Windows could not check the account" if retry else
            "could not confirm that its start-at-sign-in entry") in notice
    assert notice.endswith("Uninstall Amplifier again.\n\nReference: task-read, TaskNotConfirmed")
    assert opens(deleter, monkeypatch) is False
    # Without a journal the opening is the one of an uninstall that changed nothing.
    (root / plan.JOURNAL).unlink()
    state.update(answers=[True], choices=["no"])
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.notice.startswith("This uninstall stopped before it changed anything.\n\n")


def not_shown_once(monkeypatch, opening):
    """Windows does not show the next message or two-button question that begins with `opening`."""
    from amplifier_web import windows_installation
    shown, refused = windows_installation.owner_notice, []

    def notice(text, confirm=False):
        if text.startswith(opening) and not refused:
            refused.append(text)
            raise OSError("The installation dialog could not be displayed.")
        return shown(text, confirm=confirm)
    monkeypatch.setattr(windows_installation, "owner_notice", notice)
    return refused


@pytest.mark.parametrize("message", ["keep", "delete", "last-step"])
def test_last_message_that_windows_does_not_show_is_never_the_text_of_an_uninstall_that_changed_nothing(
        deleter, monkeypatch, message):
    """The uninstall did its work. Windows does not show its last message once; the next message is shown."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    whole = snapshot(root)
    done = "Amplifier is uninstalled. It no longer runs, and it no longer starts when you sign in.\n\n"
    left = ("They are in this folder, and they hold none of your chats, settings or accounts:\n" + str(root)
            + "\n\nYou can delete that folder.")
    if message == "keep":
        state["choices"] = ["no"]
        refused = not_shown_once(monkeypatch, "Amplifier is uninstalled.")
    elif message == "delete":
        state["choices"] = ["yes", "yes"]
        refused = not_shown_once(monkeypatch, "Amplifier is uninstalled.")
    else:
        state["choices"], state["after_failure"] = ["yes", "yes"], OSError("Cannot locate Windows PowerShell.")
        refused = not_shown_once(monkeypatch, "Amplifier could not remove its last program files.")
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(root), "--uninstall"])
    assert stopped.value.code == 1 and len(refused) == 1
    # The uninstall did its work before that message.
    assert state["task"] == "absent" and state["registry"] is None and not deleter.folder.exists()
    again, confirm = state["notices"][-1]
    assert not confirm and again.endswith("\n\nReference: message, OSError")
    said = again.removesuffix("\n\nReference: message, OSError")
    # Never the text of a failed preparation, of a question that was not asked, or of a step that failed.
    for wrong in ("was not uninstalled", "before it changed anything", "nothing was changed", "installation files",
                  "its questions", "uninstall again", "Uninstall again", "Uninstall Amplifier again"):
        assert wrong not in again, wrong
    if message == "keep":
        # Everything of the message is still true, so the further attempt shows the same text.
        assert said == refused[0] == done + (
            "Your chats, settings, accounts and the program files are still in this folder:\n" + str(root))
        assert len(state["notices"]) == 1 and not deletes(state)
        after = snapshot(root)
        assert json.loads(after.pop("app/config/services/" + consumer.RECORD))["step"] == "message"
        after.pop(shell.FILE), whole.pop(shell.FILE)  # The entries' own record now says that they are removed.
        assert after == whole
    elif message == "delete":
        # The uninstall stopped before its last step, as before this change. So the text must not say that
        # the last program files go when the message is closed.
        assert "When you close this message, Amplifier starts to remove its last program files." in refused[0]
        assert said == done + ("Your chats, settings, saved accounts and stored keys are deleted.\n\n"
                               "Amplifier did not remove its last program files. " + left)
        assert len(state["notices"]) == 1 and state["after"] is None and ("hold", plan.PARK) not in state["calls"]
        assert {path.name for path in root.iterdir()} == {plan.JOURNAL, plan.COMPLETE, consumer.RECORD, *top(RUNNING)}
        assert json.loads((root / consumer.RECORD).read_text())["step"] == "message"
    else:
        # The confirmation was shown, and the last step could not start. The message about that was not shown.
        (confirmation, _), _ = state["notices"]
        assert confirmation.startswith(done + "Your chats, settings, saved accounts and stored keys are deleted.")
        assert said == refused[0] == "Amplifier could not remove its last program files. " + left
        assert state["after"] is None and all((root / name).exists() for name in RUNNING)
        assert not any((root / name).exists() for name in top(DATA))
        assert json.loads((root / consumer.RECORD).read_text())["step"] == "message"


@pytest.mark.parametrize("end", ["keep", "delete", "last-step", "stop"])
def test_uninstall_with_a_console_prints_its_result_and_shows_no_message(deleter, capsys, end):
    """With a console the uninstall asks its questions, prints one line, and shows no message."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    assert consumer.sys.stdout is not None
    state["choices"] = ["no"] if end == "keep" else ["yes", "yes"]
    if end == "last-step":
        state["after_failure"] = OSError("Cannot locate Windows PowerShell.")
    elif end == "stop":
        state["refuse_write"] = lambda path: path.name == plan.JOURNAL
    if end == "stop":
        with pytest.raises(SystemExit) as stopped:
            consumer.main(["--root", str(root), "--uninstall"])
        assert stopped.value.code == 1
        assert json.loads(capsys.readouterr().out) == {"ok": False, "step": "journal", "errorType": "PermissionError"}
    else:
        consumer.main(["--root", str(root), "--uninstall"])
        printed = json.loads(capsys.readouterr().out)
        assert printed["ok"] and printed["userDataRetained"] is (end == "keep")
        assert printed.get("afterExit") == {"keep": None, "delete": True, "last-step": False}[end]
    # The questions are the same dialogs; no last message and no text of a stop is shown.
    assert len(state["questions"]) == (1 if end == "keep" else 2) and not state["notices"]


@pytest.mark.parametrize("stop", ["step", "question", "refusal"])
def test_text_of_a_stop_that_windows_does_not_show_gets_one_attempt(deleter, monkeypatch, stop):
    """A step fails, Windows does not show a question, or the uninstall refuses. Windows does not show that text."""
    from amplifier_web import windows_installation
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    state["choices"] = ["yes", "yes"]
    if stop == "step":
        state["refuse_write"] = lambda path: path.name == plan.JOURNAL
        expected = consumer._UNFINISHED["journal"] + "\n\nReference: journal, "
    elif stop == "refusal":
        state["busy"] = True  # Another Amplifier window holds the lock. A refusal has no reference line.
        expected = consumer._UNFINISHED["busy"]
    else:
        state["working"] = 1  # The question about a working chat: a two-button question.
        expected = consumer._UNFINISHED["questions"] + "\n\nReference: questions, OSError"
    tried = []

    def notice(text, confirm=False):
        tried.append(text)
        raise OSError("The installation dialog could not be displayed.")
    monkeypatch.setattr(windows_installation, "owner_notice", notice)
    # The program ends as an error: there is no second attempt and no other text.
    with pytest.raises(OSError, match="could not be displayed"):
        consumer.main(["--root", str(root), "--uninstall"])
    text = tried[-1]
    assert text.startswith(expected) and len(tried) == (2 if stop == "question" else 1)
    if stop == "question":
        assert tried[0].startswith("Amplifier is working on a chat right now.")
    assert not state["notices"]
    note = root / "app/config/services" / consumer.RECORD
    if stop == "refusal":
        assert not note.exists() and not (root / consumer.RECORD).exists()  # Nothing failed: no note.
    else:
        assert json.loads(note.read_text())["step"] == ("journal" if stop == "step" else "questions")


def test_further_message_names_the_windows_error_of_the_message_that_was_not_shown():
    """The reference of the further message, with and without an error number of Windows."""
    from amplifier_web import windows_consumer as consumer
    error = OSError("The installation dialog could not be displayed.")
    assert consumer.NotShown("Amplifier is uninstalled.", error).notice == (
        "Amplifier is uninstalled.\n\nReference: message, OSError")
    error.winerror = 1400
    assert consumer.NotShown("Amplifier is uninstalled.", error).notice == (
        "Amplifier is uninstalled.\n\nReference: message, OSError 1400")


def test_last_message_of_a_keeping_uninstall_with_a_journal_that_windows_does_not_show_is_shown_once_more(
        deleter, monkeypatch):
    """A journal that proves no delete choice (damaged). The uninstall did its work; its last message fails once."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    (root / plan.JOURNAL).write_text("{")
    state["answers"] = [True]
    refused = not_shown_once(monkeypatch, "Amplifier is uninstalled.")
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(root), "--uninstall"])
    assert stopped.value.code == 1 and state["task"] == "absent" and state["registry"] is None
    (question, confirm), (again, plain) = state["notices"]
    assert confirm and not plain
    # The same text again: what this uninstall did, not what the folder holds.
    assert again == refused[0] + "\n\nReference: message, OSError" and refused[0] == (
        "Amplifier is uninstalled. It no longer runs, and it no longer starts when you sign in.\n\n"
        "This uninstall deleted none of your chats, settings, accounts or program files in this folder:\n" + str(root))
    # The note of the failure is in the folder itself: an earlier removal can have deleted the app's folder.
    assert json.loads((root / consumer.RECORD).read_text())["step"] == "message"
    assert not (root / "app/config/services" / consumer.RECORD).exists() and not deletes(state)


@pytest.mark.parametrize("path", ["keep", "delete", "last-step"])
def test_further_message_that_windows_does_not_show_either_ends_the_uninstall_without_a_further_message(
        deleter, monkeypatch, path):
    """The uninstall did its work. Windows shows neither its last message nor the one further attempt."""
    from amplifier_web import windows_installation
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    state["choices"] = ["no"] if path == "keep" else ["yes", "yes"]
    if path == "last-step":
        state["after_failure"] = OSError("Cannot locate Windows PowerShell.")
    shown, refused = windows_installation.owner_notice, []

    def notice(text, confirm=False):
        if path == "last-step" and text.startswith("Amplifier is uninstalled."):
            return shown(text, confirm=confirm)  # The confirmation of the delete; only the texts after it fail.
        refused.append(text)
        raise OSError("The installation dialog could not be displayed.")
    monkeypatch.setattr(windows_installation, "owner_notice", notice)
    # The second failure leaves the program as an error: there is no third attempt and no other text.
    with pytest.raises(OSError, match="could not be displayed"):
        consumer.main(["--root", str(root), "--uninstall"])
    assert state["task"] == "absent" and state["registry"] is None
    last, again = refused
    assert again.endswith("\n\nReference: message, OSError")
    if path == "last-step":
        # The person saw the confirmation, which says that the last program files go. The message that they
        # stay was tried twice and never shown: the person has no further message.
        (confirmation, _), = state["notices"]
        assert "When you close this message, Amplifier starts to remove its last program files." in confirmation
        assert last.startswith("Amplifier could not remove its last program files.")
        assert again == last + "\n\nReference: message, OSError"
        assert state["after"] is None and all((root / name).exists() for name in RUNNING)
        assert not any((root / name).exists() for name in top(DATA))
        assert json.loads((root / consumer.RECORD).read_text())["step"] == "message"
        return
    assert not state["notices"] and last.startswith("Amplifier is uninstalled.")
    if path == "keep":
        assert again == last + "\n\nReference: message, OSError" and not deletes(state)
        note = root / "app/config/services" / consumer.RECORD
    else:
        # As after one failure: the last step did not run, and nothing more was deleted.
        assert "Amplifier did not remove its last program files." in again and state["after"] is None
        assert {entry.name for entry in root.iterdir()} == {plan.JOURNAL, plan.COMPLETE, consumer.RECORD, *top(RUNNING)}
        note = root / consumer.RECORD
    assert json.loads(note.read_text())["step"] == "message"


@pytest.mark.parametrize("path", ["keep", "delete"])
def test_last_message_that_windows_does_not_show_gets_its_further_attempt_also_without_a_note(
        deleter, monkeypatch, path):
    """The private note of the failure cannot be written. The further message is the same, and no note exists."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    state["choices"] = ["no"] if path == "keep" else ["yes", "yes"]
    state["refuse_write"] = lambda target: target.name == consumer.RECORD
    refused = not_shown_once(monkeypatch, "Amplifier is uninstalled.")
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(root), "--uninstall"])
    assert stopped.value.code == 1 and len(refused) == 1
    (again, confirm), = state["notices"]
    assert not confirm and again.endswith("\n\nReference: message, OSError")
    assert (again.startswith(refused[0]) if path == "keep" else
            "Amplifier did not remove its last program files." in again)
    assert not (root / consumer.RECORD).exists() and not (root / "app/config/services" / consumer.RECORD).exists()


@pytest.mark.parametrize("journal", [False, True])
def test_note_of_a_refused_entries_record_in_a_keeping_uninstall_is_not_put_into_a_folder_that_can_be_deleted(
        deleter, monkeypatch, journal):
    """The entries are gone and Windows refuses their record. With a journal in the folder, the note stays out of `app`."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    if journal:
        # A journal that proves no delete choice: an earlier removal can have deleted the app's folder.
        (root / plan.JOURNAL).write_text("{")
        state["answers"] = [True]
    else:
        state["choices"] = ["no"]
    state["refuse_write"] = lambda target: target.name == shell.FILE
    result = deleter.run()
    # The uninstall finished: every entry is gone, and only their record could not be written.
    assert result["ok"] and result["installationFilesRetained"] and state["task"] == "absent"
    assert state["registry"] is None and not deletes(state)
    beside_the_app, in_the_folder = root / "app/config/services" / consumer.RECORD, root / consumer.RECORD
    here, not_here = (in_the_folder, beside_the_app) if journal else (beside_the_app, in_the_folder)
    assert json.loads(here.read_text())["step"] == "receipt" and not not_here.exists()


@pytest.mark.parametrize("note", ["record", "lock", "message"])
def test_keeping_uninstall_does_not_look_again_after_the_removal_of_its_entries(deleter, monkeypatch, note):
    """No journal before the questions and none under the lock. One appears when the entries are gone."""
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    state["choices"] = ["no"]

    def appears():
        # Another window that begins a removal once the lock is free, or a program of this account.
        (root / plan.JOURNAL).write_text(json.dumps({"schema": 1, "choice": "delete", "token": "f" * 32}))
        return True
    if note == "record":
        # Windows refuses the entries' record, and the journal's name is there from that moment.
        state["refuse_write"] = lambda target: target.name == shell.FILE and appears()
        result = deleter.run()
        assert result["ok"] and result["installationFilesRetained"]
    elif note == "lock":
        # The entries are gone. The removal fails when it lets go of the lock, and the journal's name is there then.
        lock = consumer._one_at_a_time

        @contextmanager
        def fails_at_its_end(folder):
            with lock(folder):
                yield
            appears()
            raise OSError("The lock could not be released.")
        monkeypatch.setattr(consumer, "_one_at_a_time", fails_at_its_end)
        result = deleter.run()
        assert result["ok"] and result["installationFilesRetained"]
    else:
        # The journal's name is there before the last message, which Windows does not show.
        forget = consumer._forget_root
        monkeypatch.setattr(consumer, "_forget_root", lambda certificate: appears() and forget(certificate))
        not_shown_once(monkeypatch, "Amplifier is uninstalled.")
        with pytest.raises(consumer.NotShown):
            deleter.run()
    assert state["task"] == "absent" and state["registry"] is None and plan.unfinished(root)
    # The uninstall did not look again: the place of the note follows the look before the questions.
    beside_the_app, in_the_folder = root / "app/config/services" / consumer.RECORD, root / consumer.RECORD
    # (An error after the entries are gone is recorded with the step of the entries' record.)
    assert json.loads(beside_the_app.read_text())["step"] == ("message" if note == "message" else "receipt")
    assert not in_the_folder.exists()


@pytest.mark.parametrize("moment", ["before-a-change", "after-the-removal", "with-a-journal"])
def test_failure_inside_the_uninstall_that_no_step_names_does_not_say_what_was_changed(deleter, monkeypatch, moment):
    """Not a step and not a question: this program does not know whether it came before or after a change."""
    from amplifier_web import installation_health
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    state["choices"] = ["no"]
    if moment == "with-a-journal":
        # A journal that proves no delete choice: the two-button question, then the look for a working chat.
        (root / plan.JOURNAL).write_text("{")
        state["answers"] = [True]
    if moment != "after-the-removal":
        # The look for a working chat: the real function catches its own errors. This one does not.
        async def working(home):
            raise RuntimeError("The app did not answer.")
        monkeypatch.setattr(installation_health, "windows_working", working)
    else:
        # The certificate step of the keep path comes after the entries are gone. The real function catches
        # its own errors. This one does not.
        def forget(certificate):
            raise RuntimeError("Windows did not answer.")
        monkeypatch.setattr(consumer, "_forget_root", forget)
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "uninstall" and not isinstance(stopped.value, (consumer.Refused, consumer.NotShown))
    assert stopped.value.notice == ("Amplifier could not finish this uninstall.\n\n"
                                    "Restart this PC, then uninstall again.\n\nReference: uninstall, RuntimeError")
    # The text is true in both: it says neither that something was changed nor that nothing was.
    changed = moment == "after-the-removal"
    assert (state["task"] == "absent") is changed and (state["registry"] is None) is changed
    for claim in ("before it changed anything", "its questions", "installation files", "was not uninstalled",
                  "is uninstalled", "Nothing was", "Start menu", "Apps list"):
        assert claim not in stopped.value.notice, claim
    # With a journal in the folder the note of the failure is in the folder itself: an earlier removal can
    # have deleted the app's own folder.
    beside_the_app, in_the_folder = root / "app/config/services" / consumer.RECORD, root / consumer.RECORD
    here, not_here = (in_the_folder, beside_the_app) if moment == "with-a-journal" else (beside_the_app, in_the_folder)
    assert json.loads(here.read_text())["step"] == "uninstall" and not not_here.exists()


@pytest.mark.parametrize("question", ["first", "second", "working", "earlier", "resumed", "damaged"])
def test_question_that_windows_does_not_show_stops_the_uninstall_and_names_no_other_cause(
        deleter, monkeypatch, question):
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    if question == "resumed":
        earlier_removal(deleter)  # A journal that records the delete choice: the first question of a second uninstall.
    state["choices"] = ["yes", "yes"]
    if question in ("first", "second", "resumed"):
        ask, opening = plan.question, {"first": "Uninstall Amplifier?", "second": "Delete everything",
                                       "resumed": "The removal of Amplifier from this PC did not finish."}[question]

        def refuse(text, *, cancel=False):
            if text.startswith(opening):
                raise OSError("The uninstall question could not be displayed.")
            return ask(text, cancel=cancel)
        monkeypatch.setattr(plan, "question", refuse)
    elif question == "working":
        state["working"] = 1
        not_shown_once(monkeypatch, "Amplifier is working on a chat right now.")
    else:
        if question == "earlier":
            (root / plan.COMPLETE).unlink()  # Not an installation that setup completed: the two-button question.
        else:
            (root / plan.JOURNAL).write_text("{")  # A journal that proves no delete choice: the same question.
        not_shown_once(monkeypatch, "Uninstall Amplifier?")
    journal = question in ("resumed", "damaged")
    before, registration, task = snapshot(root), state["registry"].copy(), state["task"]
    with pytest.raises(consumer.Unfinished) as stopped:
        deleter.run()
    assert stopped.value.step == "questions" and not isinstance(stopped.value, consumer.Refused)
    assert stopped.value.notice == (
        "This uninstall stopped before it changed anything.\n\n"
        "Amplifier could not show one of its questions. Restart this PC, then uninstall again.\n\n"
        "Reference: questions, OSError")
    # Not the cause of a failed installation check: the installation was checked.
    assert "installation files" not in stopped.value.notice
    after = snapshot(root)
    # With a journal in the folder an earlier removal can have deleted the app's own folder: the note of the
    # failure is in the installation folder then, and this uninstall puts nothing into `app`.
    beside_the_app = "app/config/services/" + consumer.RECORD
    here, not_here = (consumer.RECORD, beside_the_app) if journal else (beside_the_app, consumer.RECORD)
    assert json.loads(after.pop(here))["step"] == "questions" and not_here not in after
    before.pop(here, None)  # The earlier removal's own note, of its step `data`.
    assert after == before and state["registry"] == registration and state["task"] == task
    assert task == ("absent" if question == "resumed" else "running")
    assert [call for call in state["calls"] if call[0] != "write"] == []


def test_design_entry_on_the_journal_names_what_the_cases_above_show():
    """The entry "The journal is not held open" of the design, against the product's two texts of that step."""
    from amplifier_web import windows_consumer as consumer
    design = " ".join((REPOSITORY / "docs/design/native-windows-installation.md").read_text(encoding="utf-8").split())
    limits = design.split("**Known limits.**", 1)[1].split("**Rule for setup.**", 1)[0]
    entry = limits.split("- **The journal is not held open.**", 1)[1].split("Under PowerShell constrained", 1)[0]
    for stated in (
            # The look, and what a stop at this step leaves open (the four ways of `JOURNAL_WAYS`).
            "looks once, by its name, whether it is in the folder",
            "When that look does not find it, this uninstall stops, and it has deleted nothing in the folder. "
            "An earlier uninstall can have deleted files there.",
            "A stop at this step leaves it open whether a journal is in the folder. This part needs no other program.",
            "The writer can report an error after the journal has its name.",
            "The look answers no for every error, not only for a name that is not there.",
            "The launcher's look is the same one: when it finds a journal, the launcher refuses to open Amplifier; "
            "when it finds none, or gets no answer, the launcher does not refuse for that reason. So the two texts "
            "of this step say neither.",
            # A journal that a program deletes: also after a removal that stopped (the intro sentence below).
            "A program of this account deletes the journal: during a removal, or at any time after a removal that "
            "stopped.",
            "If the removal did not finish, then, for as long as no look finds a journal, the launcher's commands do "
            "not refuse because of one, Amplifier can open on a folder that is partly deleted, and a next uninstall "
            "that reaches its questions asks the questions of a folder without a journal.",
            # A journal that a program replaces in the instant of the write.
            # `test_journal_that_a_program_deletes_during_the_removal_stops_it_at_its_last_step`.
            "A removal whose journal a program deletes after that look goes on up to its last step as it does with "
            "its journal: it does not look for the journal in between. When none of its steps fails and step 9 did "
            "not stop the uninstall, the last step follows. It reads the journal once it has the lock and the "
            "folder. When it finds no journal then, it refuses and deletes nothing: not `python`, not `tools` and "
            "not setup's completion record.",
            # After the last step's read the journal is not read again by the uninstall program (`finish`).
            "A journal that a program deletes after that read does not by itself stop the last step.",
            "A program of this account replaces the journal with its own file in the instant of the write.",
            "reads the journal again through the private-file boundary, before it starts the step after exit.",
            # `test_journal_that_a_program_replaces_in_the_instant_of_the_write_...` and `test_finish_refuses_...`.
            "It refuses a file that it cannot read there, a file that is not a journal, a journal that records "
            "another choice, and a journal with another token.",
            # The look after the write finds a name; it does not read the file (`begin`).
            "When the writer reports no error and the look after the write finds the name, the removal goes on as it "
            "does with its own journal: the uninstall program does not read the file at that look.",
            # What goes and what is only tried (`remove_program` returns what stayed, and nothing stops there):
            # `test_journal_that_a_program_replaces_..._and_a_program_file_in_use_stays`. The Windows entries
            # are removed only through setup's record of them (`remove`: `finish` is None without that record;
            # `windows_consumer_shell.removal` changes nothing when the record says `removed`).
            "When none of its steps fails, it deletes the person's data, as the person chose twice. It removes the "
            "Windows entries through setup's record of them: when it finds no such record in the folder, or the "
            "record says that they are removed already, it removes none.",
            # Two attempts (`remove`). An error of the first leaves `remove`
            # (`test_first_deletion_of_program_files_that_stops_with_an_error_stops_the_removal`); an error of the
            # second is caught there (`test_last_deletion_that_stops_with_an_error_gives_the_message_of_...`).
            "It also tries to delete the caches and the other program files, in two attempts: the first before its "
            "step for the Windows entries, the second after that step, for what the launcher's own check needs. "
            "When an attempt ends with files still there, the removal goes on. An error of the first attempt stops "
            "the removal. An error of the second that the removal catches (`OSError`, `ValueError` or "
            "`RuntimeError`) does not stop it: the removal then has no last listing of the folder (step 9).",
            # A later uninstall: `test_second_uninstall_after_a_replaced_journal_reads_the_other_programs_journal`,
            # and `test_uninstall_whose_look_before_its_questions_misses_a_journal_asks_as_for_a_folder_without_one`.
            "For a later uninstall that reaches the look before its questions, the other program's file is a "
            "journal like any other: what that uninstall does follows that look and its own read (see \"While the "
            "journal exists\" above, and L4).",
            # When Windows does not show the confirmation, the uninstall stops there (step 9; the cases of a
            # last message that Windows does not show). With a console no message is shown and the last step runs.
            "When none of its steps fails and step 9 did not stop the uninstall, the last step "
            "(`windows_uninstall.finish`) follows. Once it has "
            "the lock and the folder, it reads the journal again through the private-file boundary, before it starts "
            "the step after exit.",
            # The message is tried, twice at most: `test_further_message_..._without_a_further_message`.
            # After the last step's refusal: `test_journal_that_a_program_replaces_..._never_deletes_more_...`
            # (no step after exit, and nothing in the folder but what the last step did not delete).
            "Then the last step deletes nothing: not `python`, not `tools`, not that file and not setup's completion "
            "record. It makes no flat folder and does not start the step after exit. The uninstall program tries to "
            "show a message that the last program files could not be removed. "
            "When Windows does not show that message, the program tries once more. When Windows does not show it "
            "then either, the person gets no message after the confirmation of step 9.",
            # `test_journal_that_a_program_replaces_with_the_same_choice_and_token_...` (the last step), and
            # the three comparisons of the step after exit, asserted below.
            "A file that the last step can read there as a journal (`schema` 1), and that records the delete choice "
            "and this removal's own token, passes. The step after exit (`FINISH_SCRIPT` in `windows_removal.py`) "
            "looks at the entry with the journal's name and refuses it when the creation time that Windows reports "
            "for it is later than the step's own start. Then it reads the file by its path and compares the same "
            "values: schema, choice, token. (L10 is about the time between that look and that read.) When it "
            "accepts the file too, the removal goes on as it "
            "does without a replacement, and the step after exit treats the other program's file as the journal. "
            "In each case the uninstall deletes no entry that it does not delete with its own journal.",
            "After that look the uninstall program holds no handle on the journal, and it does not read the journal "
            "again before its last step (`windows_uninstall.finish`).",
            # What the three parts say, and what they do not: a program of the account can act at any time.
            "They say what the uninstall program does and what it does not delete. A program of this account can "
            "change the folder at any time, so they do not say what the folder holds afterwards."):
        assert stated in entry, stated
    # What the step after exit compares in the journal, and the journal that it refuses by its time.
    for compared in ("$record.schema -eq 1", "$record.choice -ceq $p.choice", "$record.token -ceq $p.token",
                     "(Later $journal)", "$entry.CreationTimeUtc -gt $startedUtc"):
        assert compared in removal.FINISH_SCRIPT, compared
    # It looks at the entry (its creation time) first, and reads the text by the path afterwards.
    authorized = removal.FINISH_SCRIPT.split("function Authorized {", 1)[1].split("\nfunction ", 1)[0]
    assert authorized.index("(Later $journal)") < authorized.index("Get-Content -LiteralPath $journal.FullName")
    # The two texts as the entry describes them are the texts that the product has for this step.
    first, second = consumer._UNFINISHED["journal"], consumer._UNFINISHED["removal"]
    # Which text: what this uninstall saw (`earlier` in `_delete`), not what is in the folder at the write.
    assert ("Which of the two it is follows what this uninstall saw, not what is in the folder at the write. It saw "
            "a journal when its read before its questions gave a journal that records the delete choice (a second "
            "uninstall), and when its look directly before it writes its own journal finds an entry with that name "
            "(for example, another window began a removal while a question waited). A journal is no proof that a "
            "removal began (a program of this account can write one). The uninstall takes it as the sign of "
            "one.") in entry
    assert "When the uninstall saw no journal in either way, the text says that this uninstall deleted nothing in " \
           "the folder, and that Amplifier might not open until the next uninstall." in entry
    assert "This uninstall deleted nothing in the folder." in first and first.endswith(
        "then uninstall again. Amplifier might not open until then.")
    assert ("Then the text is the one of a removal that did not finish: it tells the person not to open Amplifier, "
            "because its folder can be partly deleted. A journal that a program deleted after the uninstall saw it "
            "still counts.") in entry
    # The entry promises no outcome that the program only tries, and says of no program file that it is gone.
    for wrong in ("a message says", "the program files and the Windows entries go", "Amplifier cannot start",
                  "goes on to its end. The person's data", "data and the Windows entries go",
                  "If one of that removal's steps fails, a second uninstall reads", "every later check takes it",
                  "ends as it does without a replacement", "it has deleted nothing. What",
                  "goes on to its end without its journal", "with the other program's file still in the folder",
                  "one that Windows does not let go stays",
                  "so none of them is left to start an uninstall from", "none of this applies",
                  "By then the removal has removed the Windows entries", "stay too, and the uninstall program",
                  "The name is there, so the removal goes on", "the Windows entries that setup recorded",
                  "made after its own start", "asks them as for a whole installation",
                  "its last step finds no journal and refuses: the leftover", "leftover of L3",
                  "nothing more is deleted than the person chose", "stays deleted",
                  "goes on when Windows does not let one of them go", "It saw an earlier removal",
                  "saw no earlier removal", "an error of the attempt is a failed step",
                  "refuses a journal whose creation time", "When step 9 did not stop the uninstall, the last step",
                  "reads the journal again only when its last step begins"):
        assert wrong not in entry, wrong
    # What the native case of a replaced journal accepts (`left <= ... <= left | {"workspace"}`).
    proven = design.split("**Proven", 1)[1]
    assert ("A journal that the test replaces in that instant lets the removal go on to its last step, which "
            "refuses it (the test's journal has another token): "
            "`python`, `tools`, that journal and setup's completion record stay, and no step after exit starts. "
            "The emptied `workspace` can stay too (it is the current folder of the test's uninstall "
            "process).") in proven
    assert "with the emptied `workspace`" not in design
    assert second.endswith("Do not open Amplifier before that: its folder can be partly deleted.")
    for text in (first, second):
        assert "does not open" not in text and "Start menu" not in text and "keep using" not in text
    # When a program must act: not only "in the moment of the uninstall" for this entry.
    assert ("Except for L3, L4, L14, L15 and the first part of the entry without a number, each needs a program "
            "that runs as the same person.") in limits
    assert ("For the entry without a number it can act during the uninstall, or at any time after an uninstall "
            "that stopped. For the others it must act in the moment of the uninstall.") in limits
    # L4: what an uninstall says that found a journal which proves no delete choice.
    # The look finds a name; what the uninstall offers follows what its read of the file gave (`pending`).
    assert ("When the look that an uninstall makes before its questions finds a journal, the uninstall reads it "
            "through the private-file boundary. When that read gives no journal that records the delete choice, "
            "the uninstall offers only to keep the folder, and it deletes none of the person's chats, settings, "
            "accounts or program files in it. Afterwards the launcher refuses to open Amplifier from that folder, "
            "as long as its look finds that file.") in limits
    # The words of that question: `test_two_button_question_and_last_message_of_a_keeping_uninstall_...`.
    assert "that uninstall deletes nothing and offers only to keep the folder" not in limits
    assert ("The question and the messages of that uninstall say what it does, not what the folder holds: an "
            "earlier removal can have deleted files. When one of its steps from the lock to the removal of the "
            "Windows entries fails, it shows a text of a removal that did not finish, which tells the person not "
            "to open Amplifier. Its other stops show other texts. A failed check of the installation files, and a "
            "question that Windows does not show, show the text of an uninstall that stopped before it changed "
            "anything. While another Amplifier window holds the lock, the text says that. For a last message that "
            "Windows does not show, the uninstall program tries once more to show it. A failure that no step "
            "names shows a text that does not say what was changed.") in limits
    # "a text": the one of the step's cause. Both tell the person not to open Amplifier, and neither says that
    # Amplifier does not open: the launcher refuses only when its look finds the journal.
    assert consumer._UNFINISHED["removal"].endswith(
        "Do not open Amplifier before that: its folder can be partly deleted.")
    assert consumer.entry_notice("removal", "entry", False).startswith(
        "Amplifier could not finish its removal from this PC. Do not open Amplifier before it has finished: its "
        "folder can be incomplete.\n\n")
    for text in (consumer._UNFINISHED["removal"], consumer._UNFINISHED["data"],
                 consumer.entry_notice("removal", "entry", False), consumer.entry_notice("removal", "entry", True)):
        assert "does not open" not in text and "not open until" not in text, text
    # What the order and the paragraph on the journal say about the texts, against the cases above.
    for stated in (
            # Step 7 of the keeping order: which outcome gives which sentence (`CERTIFICATE_LEFT`).
            "7. One message says that uninstall is complete. When the result of step 6 is `declined` (Windows "
            "reported the person's No for at least one certificate), it says that Windows kept the certificate. "
            "When it is `blocked` (at least one was not confirmed as deleted for another reason), it says that "
            "Windows can still hold it.",
            # Step 9: what the confirmation says in each of its three forms (asserted below against the product).
            "9. One message. It says that the data is deleted, which the removal observed, and that Amplifier "
            "starts to remove its last program files when the person closes the message. When the last listing of "
            "the folder "
            "showed entries that are not Amplifier's, the message names up to five of them, with the number of the "
            "others when there are more, and says that the folder stays. When that listing showed none, the "
            "message says that the person can delete the folder if it still exists a minute later.",
            # `test_folder_that_still_cannot_be_listed_...` and `test_last_deletion_that_stops_with_an_error_...`.
            "When the removal has no such listing, because the listing or the deletion directly before it stopped "
            "with an error, the message says that the folder can still exist afterwards.",
            # What the uninstall does not delete then, not what the folder holds.
            "If Windows does not show that message, the uninstall stops there, before its last step. So it does not "
            "delete `python`, `tools`, the journal or setup's completion record, and it tries to write a private "
            "note of that failure.",
            "The uninstall program then tries once more to show a message. That message says that the data is "
            "deleted and that the last program files were not removed. It is never the message of an uninstall "
            "that changed nothing.",
            "the further message is that last message again",
            "When Windows does not show the further message either, the person gets no message of the uninstall "
            "program after its questions.",
            "A journal can appear while an uninstall asks its questions, for example when another window began a "
            "removal while a question waited. (A program of this account can also write a file with that name. Every "
            "look takes an entry with that name for a journal.)",
            "An uninstall that keeps the folder and found no journal before its questions looks again under the "
            "lock, and refuses when that look finds one (step 1 of the order). It looks once more when one of its "
            "steps, from the lock to the removal of the Windows entries, stops with an error before those steps "
            "gave their result. When that look finds a journal, the failed step shows a text of a removal that did "
            "not finish, and the uninstall tries to write the failure note into the installation folder itself, not "
            "beside the app's service records.",
            # `test_keeping_uninstall_does_not_look_again_after_the_removal_of_its_entries`.
            "Three failures of a keeping uninstall get no new look: a refused write of setup's entry record (the "
            "entries are gone by then), an error when the removal afterwards lets go of the entries or of the lock, "
            "and a last message that Windows does not show. For them the uninstall tries to write the note where "
            "the look before the questions chose.",
            # No check of the delete path refuses a folder because a journal is in it (`windows_uninstall.remove`).
            "An uninstall with the delete choice has no such refusal: a journal that appeared does not stop it, as "
            "it does not stop a second uninstall. Its own checks can still stop it.",
            "It looks for the journal's name when a step before the write of its own journal stops with an error, "
            "and directly before that write. If the first of these looks finds a journal, the failed step shows a "
            "text of a removal that did not finish, not the step's own text. If the second finds one, a failure of "
            "that write shows the text of a removal that did not finish too.",
            # `Refused("busy")` leaves both paths before their look.
            "A lock that another window holds is a refusal, not such an error: neither uninstall looks then.",
            # `test_question_that_windows_does_not_show_after_another_window_began_a_removal_...`.
            "An uninstall that found no journal before its questions also looks when Windows does not show one of "
            "them; that look chooses only the place of the failure note.",
            # The look under the lock is main's (`Refused("removing")` in the keep path).
            # The look of `maintenance` at a failure: the parameter `with-a-journal` of the unnamed-failure case.
            "So does the look at a failure that no step and no question names, and at a preparation that fails "
            "after the check of the installation files.",
            "Of the looks that this paragraph names, only the one under the lock decides a refusal, as it did "
            "before the others were added. The others choose only a text and the place of the failure note. They "
            "change nothing that an uninstall deletes or removes.",
            # `_record` never raises, and it can write no note: the case of a further attempt without a note.
            "\"The place of the failure note\" is the folder into which the uninstall first tries to write that "
            "note. The write is only tried: when Windows refuses it, there can be no note.",
            "Each of these looks, and the launcher's look before it opens Amplifier, asks only whether the journal's "
            "name is in the folder. A look that Windows does not answer counts as \"no journal\". Where this section "
            "says that the launcher refuses, or does not open Amplifier, while the journal exists, that is the "
            "condition: its look finds the journal. So no text of a failed step of an uninstall says that Amplifier "
            "does not open.",
            # Which texts tell the person not to open Amplifier: asserted below against the product.
            "The texts of a removal that did not finish (`removal` and `data` in `windows_consumer.py`, and the "
            "opening for a start-at-sign-in entry that is not confirmed during a removal) tell the person not to "
            "open Amplifier, because its folder can be partly deleted or incomplete. The text of the launcher's own "
            "refusal is not changed: it says that Amplifier does not open until the removal has finished, and the "
            "program that shows it has just found the journal.",
            "The first question of a second uninstall is not changed either: its last sentence says the same, and "
            "the program that asks it has just read the journal.",
            # `test_uninstall_whose_look_before_its_questions_misses_a_journal_asks_as_for_a_folder_without_one`.
            "An uninstall's own look has the same condition. An uninstall looks twice before its questions. The "
            "first look (`maintenance`) decides only whether the uninstall prepares the environment of a start, "
            "which checks folders that a removal deletes. The second (`_uninstall`) chooses the questions, and \"the "
            "look before its questions\" in this document is that one.",
            "Where this document says what an uninstall does with a journal in the folder (a second uninstall; L4), "
            "that look has found the journal's name, and the uninstall has then read the file through the "
            "private-file boundary. A second uninstall is one whose read gave a journal that records the delete "
            "choice. L4 is one whose read gave anything else.",
            # `test_without_setups_completion_record_the_question_is_the_earlier_one`, and the case above.
            "When that look finds no journal, the uninstall asks the questions of a folder without a journal: when "
            "it reads a valid completion record of setup, it offers both choices, and otherwise only to keep the "
            "folder.",
            # What "shows" means: `test_text_of_a_stop_that_windows_does_not_show_gets_one_attempt`, and the
            # cases of a last message that Windows does not show.
            "Where this document says that an uninstall shows a text or a message, or says something to the "
            "person, the uninstall program asks Windows to show that text. Windows can fail to show it.",
            # The cases of a last message that Windows does not show, once and twice (three messages each).
            "Three kinds of message can come after an uninstall did its work: the last message of an uninstall "
            "that keeps the folder; the confirmation of step 9; and, when the last step stops with an error, the "
            "message that the last program files could not be removed. When Windows does not show one of them, "
            "the program tries once more. The further text is "
            "the same text again with a reference line, except after the confirmation: step 9 has that text. "
            "When Windows does not show the further text either, the person gets no further message.",
            "The text of an uninstall that stops (for example a step that failed, a question that Windows did not "
            "show, a refusal) gets one attempt: when Windows does not show it, the person sees no text of that "
            "stop.",
            # `test_uninstall_with_a_console_prints_its_result_and_shows_no_message`, and the entries' program.
            "The uninstall program shows these texts only when its process has no standard output. That is the "
            "uninstall that a person starts from the Start menu or the Apps list: those entries start the "
            "windowless Python. With a standard output (a console Python; the automated native cases start the "
            "uninstall so) the uninstall asks the same questions and prints its result. It then shows no last "
            "message and no text of a stop.",
            "a question, a last message or the text of a stop that Windows does not show",
            # The sentence about a certificate that Windows did not delete: `CERTIFICATE_LEFT`, and what
            # `windows_trust.remove_root` returns `blocked` for (also a certificate that the store still held).
            "For `blocked` an uninstall says that Windows can still hold the certificate: after a failed search the "
            "program does not know whether Windows holds it, and one text serves every case of `blocked`. Only "
            "for `declined`, when Windows reported the person's No, does it say that Windows kept the certificate.",
            # Where the sentence is, and where it is not (asserted below against every text of a stop).
            "These sentences are part of the last message of an uninstall that keeps the folder, of the "
            "confirmation of an uninstall that deletes it, and of the further message for either. An uninstall "
            "with the delete choice does its certificate step before it writes its journal. When a step after "
            "that one fails before the confirmation, the text of the failed step has no sentence about a "
            "certificate.",
            # `test_no_that_an_error_follows_in_the_same_step_is_blocked_not_declined` (test_windows_trust.py).
            "The result is `declined` when the step for at least one certificate ended with Windows' report of the "
            "person's No.",
            # Every way to `blocked`: `remove_root`, `forget_roots`, `_forget_root` (the cases of `test_windows_trust`,
            # and `OSError("store")` in `test_certificate_answer_never_stops_the_removal_...`).
            "Otherwise it is `blocked` when at least one was not confirmed as deleted for another reason: the store "
            "could not be opened, a search failed, Windows refused a deletion, the store still held a certificate "
            "afterwards, a check afterwards failed, or the step ended with another error, also after such a "
            "report. In some of those cases the program does not know whether Windows still holds that one."):
        assert stated in design, stated
    for wrong in ("because the program does not know it", "the program then does not know whether",
                  "the last program files go when the message is closed", "is named and stays",
                  "After another failure it says", "When one of its steps fails, it looks too",
                  "it goes on, as a second uninstall does", "a step that failed after a removal began does not say",
                  "stay, and the private note of that failure when it could be written",
                  "another window began a removal while a question waited. An uninstall",
                  "Three messages come after", "asks Windows before it writes its journal",
                  "After that result the uninstall does not look again", "Only for the person's No"):
        assert wrong not in design, wrong
    # "the windowless Python": the program of every Start-menu entry and of the Apps-list entry.
    python, links = shell.entries(Path("installation"))
    assert python.name == "pythonw.exe" and links["Uninstall Amplifier.lnk"].endswith(" --uninstall")
    assert shell.registration_values(Path("installation"), "1.0.0")["UninstallString"].startswith(str(python) + " ")
    # Exactly that. Of the texts of a stop, only the launcher's refusal says that Amplifier does not open. Of
    # the questions, only the first question of a second uninstall says it. No last message says it.
    assert [name for name, text in consumer._UNFINISHED.items()
            if "does not open" in text and name not in ("stop-app", "status")] == ["removing"]
    assert [name for name, text in QUESTIONS.items() if "does not open" in text] == [
        "first question of the next uninstall"]
    assert QUESTIONS["first question of the next uninstall"].endswith(
        "Select No to change nothing now. Amplifier does not open until the removal has finished.")
    assert [name for name, text in STOPS.items() if "does not open" in text] == ["removing"]
    assert not [name for name, text in LAST_MESSAGES.items() if "does not open" in text]
    # Step 9 of the order, against the three forms of the confirmation.
    folder = Path("installation")
    close = "When you close this message, Amplifier starts to remove its last program files. "
    seven = consumer._removed_notice(folder, list("abcdefg"), "removed")
    assert close + "The folder itself stays, because it holds entries that are not Amplifier's." in seven
    assert "They were not changed:\na\nb\nc\nd\ne\nand 2 more\n" in seven and "\nf\n" not in seven
    five = consumer._removed_notice(folder, list("abcde"), "removed")
    assert "They were not changed:\na\nb\nc\nd\ne\n\nThe folder:" in five
    assert close + "This folder can still exist afterwards:\n" in consumer._removed_notice(folder, None, "removed")
    assert close + "If this folder still exists a minute later, you can delete it." in consumer._removed_notice(
        folder, [], "removed")
    for listed in ([], None, ["a"]):
        assert "Your chats, settings, saved accounts and stored keys are deleted." in consumer._removed_notice(
            folder, listed, "removed")
    # The texts of a removal that did not finish tell the person not to open Amplifier. The text of the step
    # `journal` of an uninstall that saw no earlier removal does not: it says "might not open".
    assert consumer._UNFINISHED["data"].endswith("Do not open Amplifier before that: its folder can be partly deleted.")
    assert "Do not open" not in consumer._UNFINISHED["journal"]
    # No text of a stop, and no text for a start-at-sign-in entry that is not confirmed, names a certificate.
    stops = list(consumer._UNFINISHED.values()) + [consumer.entry_notice(purpose, "entry", retry)
                                                   for purpose in ("uninstall", "removal") for retry in (False, True)]
    assert not [text for text in stops if "certificate" in text.lower()]
    assert "certificate" in consumer._removed_notice(folder, [], "blocked", last=False, certificate=[b"one"])


def shown_by_the_product(deleter, monkeypatch, *, as_shown=False):
    """Every question and message that the README item speaks about, as the public uninstall shows it.

    One installation, in the order of a person's journey: the questions with answers that change nothing,
    a delete that stops three times, a second uninstall, and the delete whose last step cannot start.
    """
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    shown = {}

    def run(*choices, answers=()):
        state.update(choices=list(choices), answers=list(answers), questions=[], notices=[])
        try:
            deleter.run()
        except consumer.Unfinished as stopped:
            return stopped.notice
        return None

    # A folder that setup did not complete as an installation: the earlier question, with two buttons.
    record = (root / plan.COMPLETE).read_text()
    (root / plan.COMPLETE).unlink()
    assert run(answers=[False]) is None and not state["questions"]
    (shown["earlier question"], confirm), = state["notices"]
    assert confirm
    (root / plan.COMPLETE).write_text(record)
    # A whole installation, a trusted certificate, one working chat. No to the last question changes nothing.
    state.update(certificate=b"this installation's root", working=1)
    before = snapshot(root)
    assert run("yes", "yes", answers=[False]) is None
    (shown["first question"], cancel), (shown["second question"], plain) = state["questions"]
    (shown["working question"], confirm), = state["notices"]
    assert cancel and not plain and confirm and snapshot(root) == before and not state["calls"]
    # Three working chats. No to that question changes nothing.
    state["working"] = 3
    assert run("no", answers=[False]) is None and snapshot(root) == before and not state["calls"]
    (shown["working question with three chats"], confirm), = state["notices"]
    assert confirm
    state["working"] = None
    # Windows holds two certificates of this installation (the person ran `setup-tls force` and kept the
    # replaced one). Cancel changes nothing.
    state["certificate"] = [b"this installation's root", b"a root that it replaced"]
    assert run("cancel") is None and snapshot(root) == before and not state["calls"]
    (shown["first question with two certificates"], cancel), = state["questions"]
    assert cancel
    state["certificate"] = b"this installation's root"
    # A delete that stops when it writes its journal.
    state["refuse_write"] = lambda path: path.name == plan.JOURNAL
    shown["stop at the journal"] = run("yes", "yes")
    state.update(refuse_write=None, certificate=None)
    # A delete that stops at the person's data: a program uses one file.
    state["held"] = {"app/chats/one/message.txt"}
    shown["stop at the data"] = run("yes", "yes")
    # The next uninstall: another first question, and then the same second question. No changes nothing.
    assert run("yes", "no") is None
    (shown["first question of the next uninstall"], plain), (again, _) = state["questions"]
    assert not plain and again.replace("Select No to change nothing now.", "") == shown["second question"].replace(
        "Select No to change nothing. Amplifier stays installed.", "")
    # The next uninstall stops when it writes its new journal.
    state["refuse_write"] = lambda path: path.name == plan.JOURNAL
    shown["next uninstall stops at the journal"] = run("yes", "yes")
    state["refuse_write"] = None
    # The file is free. The delete stops later, at the Apps-list entry.
    state["held"].clear()
    state["commit_conflict"] = True
    shown["stop after the data"] = run("yes", "yes")
    state["commit_conflict"] = False
    # The delete goes to its end. One entry in the folder is not Amplifier's. The last step cannot start.
    (root / "mine.txt").write_text("not Amplifier's")
    state["after_failure"] = OSError("Cannot locate Windows PowerShell.")
    assert run("yes", "yes") is None
    (shown["confirmation of a delete"], confirm), (shown["last step that cannot start"], plain) = state["notices"]
    assert not confirm and not plain and all(isinstance(text, str) and text for text in shown.values())
    return shown if as_shown else {name: " ".join(text.split()) for name, text in shown.items()}


# Every question of an uninstall and every text of an uninstall that stops, complete. The README case below
# looks for words in these texts; a sentence that someone adds to one of them fails here.
QUESTIONS = {
    "first question":
        "Uninstall Amplifier?\n\n"
        "Amplifier will stop and will no longer start when you sign in. Its entries in the Start menu and the Apps "
        "list will be removed.\n\n"
        "Do you also want to delete your chats, settings and saved accounts?\n\n"
        "Select No to keep them. They stay in this folder, together with the program files:\n{folder}\n\n"
        "Select Yes to delete them, together with the program files. Amplifier asks you once more before it "
        "deletes anything.\n\n"
        "Select Cancel to keep Amplifier installed.\n\n"
        'During the uninstall, Windows asks whether to delete the certificate "{certificate}". That is a '
        "separate question.",
    "second question":
        "Delete everything that Amplifier keeps in this folder?\n\n{folder}\n\n"
        "This deletes all your Amplifier chats, your settings, your saved accounts and your stored keys. It also "
        "deletes the files that Amplifier or you saved in its workspace folder there. This cannot be undone.\n\n"
        "Select Yes to delete all of that and uninstall Amplifier.\n"
        "Select No to change nothing. Amplifier stays installed.",
    "working question":
        "Amplifier is working on a chat right now.\n\n"
        "Select Yes to stop that work and uninstall now.\n"
        "Select No to keep Amplifier installed. Uninstall again when the work has finished.",
    "first question of the next uninstall":
        "The removal of Amplifier from this PC did not finish.\n\n"
        "Select Yes to finish it now. This deletes everything that Amplifier still keeps in this folder, including "
        "chats, settings, saved accounts and stored keys. This cannot be undone.\n{folder}\n\n"
        "Amplifier asks you once more before it deletes anything.\n\n"
        "Select No to change nothing now. Amplifier does not open until the removal has finished.",
    "earlier question":
        "Uninstall Amplifier?\n\n"
        "Amplifier will stop and will no longer start when you sign in. Its entries in the Start menu and the Apps "
        "list will be removed.\n\n"
        "Your chats, settings, accounts and the program files stay in this folder:\n{folder}",
}
STOPS = {
    "preparation": "This uninstall stopped before it changed anything.\n\n"
                   "Amplifier could not check its own installation files. Restart this PC, then uninstall again.",
    "questions": "This uninstall stopped before it changed anything.\n\n"
                 "Amplifier could not show one of its questions. Restart this PC, then uninstall again.",
    "lock": "This uninstall stopped before it changed anything.\n\n"
            "Amplifier could not find out whether it is being opened or uninstalled in another window. "
            "Restart this PC, then uninstall again.",
    "folder": "This uninstall stopped before it changed anything.\n\n"
              "Amplifier could not check its own folder, so it deleted nothing. Restart this PC, then uninstall "
              "again. If this message returns, select No in the first question: Amplifier is then uninstalled, and "
              "its folder stays.",
    "entries-check": "This uninstall stopped before it changed anything.\n\n"
                     "Amplifier's entries in the Start menu or the Apps list are in use, or they were changed after "
                     "setup. If another Amplifier message is open, answer it first. Otherwise restart this PC, then "
                     "uninstall again.",
    "task-read": "This uninstall stopped before it changed anything.\n\n"
                 "Amplifier could not read its start-at-sign-in entry from Windows Task Scheduler. Restart this PC, "
                 "then uninstall again.",
    "journal": "Amplifier has stopped and no longer starts when you sign in, but it could not begin to delete its "
               "folder. This uninstall deleted nothing in the folder.\n\n"
               "Wait a minute or restart this PC, then uninstall again. Amplifier might not open until then.",
    "data": "Amplifier has stopped, but some of your chats, settings or saved accounts could not be deleted. "
            "A program may still use them.\n\n"
            "Close other programs or restart this PC. Then uninstall Amplifier again to finish. "
            "Do not open Amplifier before that: its folder can be partly deleted.",
    # Also the text of a stop at the journal when a journal was in the folder before the write.
    "removal": "Amplifier could not finish its removal from this PC.\n\n"
               "Close other programs or restart this PC. Then uninstall Amplifier again to finish. "
               "Do not open Amplifier before that: its folder can be partly deleted.",
    "uninstall": "Amplifier could not finish this uninstall.\n\nRestart this PC, then uninstall again.",
    # Not changed by the work on the texts of a stop; here so that every text of an uninstall is in this table.
    "stop": "Amplifier was not uninstalled, because it did not stop. Nothing was removed.\n\n"
            "Wait a few minutes or restart this PC, then uninstall again.",
    "task-remove": "Amplifier has stopped, but it is not uninstalled yet. Windows did not remove its "
                   "start-at-sign-in entry, and nothing else was removed.\n\n"
                   "Uninstall again to finish. To keep using Amplifier, open it from the Start menu.",
    "entries": "Amplifier has stopped and no longer starts when you sign in, but some of its entries in the Start "
               "menu or the Apps list could not be removed.\n\n"
               "Uninstall again to finish. To keep using Amplifier, open it from the Start menu.",
    "busy": "Amplifier is being opened or uninstalled in another window right now. Nothing was changed.\n\n"
            "If another Amplifier message is open, answer it first. Otherwise wait a minute, then try again.",
    "removing": "Amplifier is being removed from this PC, and the removal did not finish. Amplifier does not open "
                "until it has finished.\n\n"
                "To finish, select Uninstall Amplifier in the Start menu or in the Windows Apps list. If Amplifier "
                "is in neither, it is already uninstalled.",
}
# The last messages of an uninstall, complete. `{folder}` is the installation folder.
UNINSTALLED = "Amplifier is uninstalled. It no longer runs, and it no longer starts when you sign in.\n\n"
DELETED = UNINSTALLED + "Your chats, settings, saved accounts and stored keys are deleted.\n\n"
# The sentence about a certificate that Windows did not delete, by what Windows answered and for how many it
# was asked. `declined`: Windows reported the person's No for at least one. `blocked`: no No, and for at
# least one the deletion failed in another way. In some of those cases the program does not know whether
# Windows still holds it, so the text does not say that Windows kept it.
CERTIFICATE_LEFT = {
    ("declined", 1): '\n\nWindows kept the certificate "{certificate}". It works only for Amplifier on this PC.',
    ("blocked", 1): '\n\nWindows can still hold the certificate "{certificate}". '
                    "It works only for Amplifier on this PC.",
    ("declined", 2): '\n\nWindows kept at least one of the 2 certificates named "{certificate}". '
                     "Each works only for Amplifier on this PC.",
    ("blocked", 2): '\n\nWindows can still hold one or more of the 2 certificates named "{certificate}". '
                    "Each works only for Amplifier on this PC.",
}
LAST_MESSAGES = {
    "keep": UNINSTALLED + "Your chats, settings, accounts and the program files are still in this folder:\n{folder}",
    "keep, with a journal that proves no delete choice":
        UNINSTALLED + "This uninstall deleted none of your chats, settings, accounts or program files in this "
                      "folder:\n{folder}",
    "delete": DELETED + "When you close this message, Amplifier starts to remove its last program files. If this "
                        "folder still exists a minute later, you can delete it. It holds none of your data:\n{folder}",
    "delete, with entries that are not Amplifier's":
        DELETED + "When you close this message, Amplifier starts to remove its last program files. The folder itself "
                  "stays, because it holds entries that are not Amplifier's. They were not changed:\n"
                  "a\nb\nc\nd\ne\nand 2 more\n\nThe folder:\n{folder}",
    "delete, and the folder could not be listed":
        DELETED + "When you close this message, Amplifier starts to remove its last program files. This folder can "
                  "still exist afterwards:\n{folder}",
    "delete, and Windows did not show the confirmation":
        DELETED + "Amplifier did not remove its last program files. They are in this folder, and they hold none of "
                  "your chats, settings or accounts:\n{folder}\n\nYou can delete that folder.",
    "delete, Windows did not show the confirmation, entries that are not Amplifier's":
        DELETED + "Amplifier did not remove its last program files. They are in this folder, and they hold none of "
                  "your chats, settings or accounts:\n{folder}",
    "the last step could not start":
        "Amplifier could not remove its last program files. They are in this folder, and they hold none of your "
        "chats, settings or accounts:\n{folder}\n\nYou can delete that folder.",
    "the last step could not start, entries that are not Amplifier's":
        "Amplifier could not remove its last program files. They are in this folder, and they hold none of your "
        "chats, settings or accounts:\n{folder}",
}


def test_every_question_and_every_text_of_a_stop_is_exactly_this_text(deleter, monkeypatch):
    from amplifier_web.windows_trust import ROOT_NAME
    consumer = deleter.consumer
    shown = shown_by_the_product(deleter, monkeypatch, as_shown=True)
    for name, text in QUESTIONS.items():
        assert shown[name] == text.format(folder=deleter.root, certificate=ROOT_NAME), name
    # With several working chats only the first sentence of that question differs.
    assert shown["working question with three chats"] == QUESTIONS["working question"].replace(
        "Amplifier is working on a chat right now.", "Amplifier is working on 3 chats right now.")
    # With two certificates only the last sentence of the first question differs.
    one, two = shown["first question"], shown["first question with two certificates"]
    assert one.rsplit("\n\n", 1)[0] == two.rsplit("\n\n", 1)[0] and two.rsplit("\n\n", 1)[1] == (
        f'During the uninstall, Windows asks 2 times whether to delete a certificate named "{ROOT_NAME}". '
        "Windows holds 2 of them. Those are separate questions.")
    for name, text in STOPS.items():
        assert consumer._UNFINISHED[name] == text, name
    # The table has every text of an uninstall. The two others are the texts of Stop and of Status.
    assert set(consumer._UNFINISHED) - set(STOPS) == {"apps-entry", "shortcuts", "stop-app", "status"}
    assert consumer._UNFINISHED["apps-entry"] == consumer._UNFINISHED["shortcuts"] == STOPS["entries"]
    # What the journey showed for each stop is that text and the reference of the step that failed.
    for case, name, reference in (("stop at the journal", "journal", "journal, PermissionError"),
                                  ("next uninstall stops at the journal", "removal", "journal, PermissionError"),
                                  ("stop at the data", "data", "data, Held"),
                                  ("stop after the data", "removal", "apps-entry, OSError")):
        assert shown[case] == STOPS[name] + "\n\nReference: " + reference, case
    # A stop at the journal, and a stop after a removal began, say one thing about opening Amplifier. It is not
    # that Amplifier opens, and it is not that Amplifier does not open: the launcher refuses only when its look
    # finds the journal.
    for name, only in (("journal", "Amplifier might not open until then."),
                       ("removal", "Do not open Amplifier before that: its folder can be partly deleted."),
                       ("data", "Do not open Amplifier before that: its folder can be partly deleted.")):
        about_opening = [sentence for sentence in re.split(r"(?<=\.)\s+", STOPS[name]) if "open" in sentence.lower()]
        assert about_opening == [only], name
    # A text of an uninstall that changed nothing does not say that Amplifier opens, is installed or can be used.
    unchanged = [name for name, text in consumer._UNFINISHED.items()
                 if text.startswith("This uninstall stopped before it changed anything.\n\n")]
    assert sorted(unchanged) == ["entries-check", "folder", "lock", "preparation", "questions", "task-read"]
    for name in unchanged:
        assert not re.search(r"open (it|Amplifier)|Amplifier opens|keep using|can use|not uninstalled|nothing was",
                             consumer._UNFINISHED[name]), name


def test_every_last_message_and_both_forms_of_the_two_questions_are_exactly_these_texts(deleter, monkeypatch):
    """The texts that `test_every_question_...` does not reach: they need another answer or another folder."""
    from amplifier_web.windows_trust import ROOT_NAME
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)

    def text(name, table=LAST_MESSAGES):
        return table[name].format(folder=root, certificate=ROOT_NAME)

    # What `_removed_notice` and `_last_files` build, for each kind of folder at the end.
    several = list("abcdefg")
    assert consumer._removed_notice(root, [], "removed") == text("delete")
    assert consumer._removed_notice(root, several, "absent") == text("delete, with entries that are not Amplifier's")
    assert consumer._removed_notice(root, None, "none") == text("delete, and the folder could not be listed")
    assert consumer._removed_notice(root, [], "removed", last=False) == text(
        "delete, and Windows did not show the confirmation")
    for foreign in (several, None):
        assert consumer._removed_notice(root, foreign, "none", last=False) == text(
            "delete, Windows did not show the confirmation, entries that are not Amplifier's")
    assert consumer._last_files(root, [], "Amplifier could not remove its last program files.") == text(
        "the last step could not start")
    # Entries that are not Amplifier's, and a folder that could not be listed: no advice to delete the folder.
    for foreign in (several, None):
        assert consumer._last_files(root, foreign, "Amplifier could not remove its last program files.") == text(
            "the last step could not start, entries that are not Amplifier's")
    two = [b"current root", b"replaced root"]
    for (answer, count), sentence in CERTIFICATE_LEFT.items():
        asked = two if count == 2 else [b"root"]
        left = sentence.format(certificate=ROOT_NAME)
        assert consumer._certificate_left(asked, answer) == left
        assert consumer._removed_notice(root, [], answer, certificate=asked) == text("delete") + left
        assert consumer._removed_notice(root, None, answer, last=False, certificate=asked) == text(
            "delete, Windows did not show the confirmation, entries that are not Amplifier's") + left
    # Three certificates: only the number differs.
    for answer in ("declined", "blocked"):
        assert consumer._certificate_left([b"a", b"b", b"c"], answer) == CERTIFICATE_LEFT[answer, 2].format(
            certificate=ROOT_NAME).replace("of the 2 certificates", "of the 3 certificates")
    # Every other answer says nothing about a certificate, for one and for several.
    for answer in ("removed", "absent", "none"):
        assert consumer._certificate_left(two, answer) == consumer._certificate_left(None, answer) == ""
    # The public uninstall shows these texts: the delete choice, without a certificate that Windows trusts,
    # and a last step that cannot start.
    state.update(choices=["yes", "yes"], after_failure=OSError("Cannot locate Windows PowerShell."))
    deleter.run()
    (first, cancel), (second, _) = state["questions"]
    # Without a trusted certificate the first question ends with its Cancel sentence.
    with_certificate = QUESTIONS["first question"].format(folder=root, certificate=ROOT_NAME)
    assert cancel and first == with_certificate.rsplit("\n\n", 1)[0]
    assert first.endswith("Select Cancel to keep Amplifier installed.") and "certificate" not in first
    assert second == QUESTIONS["second question"].format(folder=root)
    assert state["notices"] == [(text("delete"), False), (text("the last step could not start"), False)]


@pytest.mark.parametrize("journal", [False, True])
@pytest.mark.parametrize("certificate", [None, "removed", "declined", "blocked"])
def test_two_button_question_and_last_message_of_a_keeping_uninstall_are_exactly_these_texts(
        deleter, monkeypatch, journal, certificate):
    """The keep path: a whole installation (No to the first question), and a journal that proves no delete choice."""
    from amplifier_web import windows_trust
    from amplifier_web.windows_trust import ROOT_NAME
    state, root, consumer = deleter.state, deleter.root, deleter.consumer
    monkeypatch.setattr(consumer.sys, "stdout", None)
    if certificate:
        state["certificate"] = b"this installation's root"
        # The stand-in of the delete path expects Windows' question before the entries go. The keep path asks last.
        monkeypatch.setattr(windows_trust, "remove_root", lambda der, store="ROOT": certificate)
    if journal:
        (root / plan.JOURNAL).write_text("{")
        state["answers"] = [True]
    else:
        state["choices"] = ["no"]
    assert deleter.run()["installationFilesRetained"] is True and not deletes(state)
    last, plain = state["notices"][-1]
    expected = LAST_MESSAGES["keep, with a journal that proves no delete choice" if journal else "keep"].format(
        folder=root)
    assert not plain and last == expected + (
        CERTIFICATE_LEFT[certificate, 1].format(certificate=ROOT_NAME) if certificate in ("declined", "blocked") else "")
    if journal:
        # The only question: two buttons. It says what this uninstall does, and what Windows asks at the end.
        (question, confirm), _ = state["notices"]
        assert confirm and not state["questions"] and question == (
            "Uninstall Amplifier?\n\n"
            "Amplifier will stop and will no longer start when you sign in. Its entries in the Start menu and the "
            "Apps list will be removed.\n\n"
            "This uninstall deletes none of your chats, settings, accounts or program files in this folder:\n"
            + str(root) + (f'\n\nAt the end, Windows asks whether to delete the certificate "{ROOT_NAME}". '
                           "Select Yes there to remove it." if certificate else ""))
    else:
        assert len(state["notices"]) == 1 and len(state["questions"]) == 1


# The README item "Uninstall", complete: every paragraph, every list entry, every sentence, in its order.
# This table is what a person reviews: a change of the README item is a change of this table, and each row
# puts the sentence beside the product's words. The case cannot tell whether a sentence follows from its
# words; it tells that the README is this table and that the product shows these words in these cases.
# Beside each sentence: the texts that the product shows (the case, and the words in it) on which the
# sentence rests. A sentence that rests on no text of the product says why. A sentence that is not in this
# table cannot be in the README, and a sentence of the product counts only in the case that shows it.
WINDOWS, ABOUT = "Windows' own screens", "about the sentences of this item"
README_UNINSTALL = [
    [  # The first paragraph and the three buttons of the first question.
        [("open **Settings > Apps > Installed apps**, find **Amplifier Unified (unsigned preview)**, select "
          "**More options (…)**, then **Uninstall**, then **Uninstall** again.", WINDOWS),
         ('Amplifier first asks: "Do you also want to delete your chats, settings and saved accounts?"',
          [("first question", "Do you also want to delete your chats, settings and saved accounts?")])],
        [("**No** uninstalls Amplifier and keeps the folder.",
          [("first question", "Select No to keep them. They stay in this folder, together with the program files:")]),
         ("Your chats, settings and accounts stay there, together with the program files (1.4 GB to 1.9 GB in "
          "our tests).",
          [("first question", "Select No to keep them. They stay in this folder, together with the program files:")])],
        [('**Yes** leads to a second question: "Delete everything that Amplifier keeps in this folder?"',
          [("first question", "Select Yes to delete them, together with the program files. Amplifier asks you once "
                              "more before it deletes anything."),
           ("second question", "Delete everything that Amplifier keeps in this folder?")]),
         ("**Yes** there uninstalls Amplifier and deletes your chats, settings, saved accounts and stored keys, the "
          "files that Amplifier or you saved in its workspace folder, and the program files.",
          [("second question", "This deletes all your Amplifier chats, your settings, your saved accounts and your "
                               "stored keys. It also deletes the files that Amplifier or you saved in its workspace "
                               "folder there."),
           ("second question", "Select Yes to delete all of that and uninstall Amplifier."),
           ("first question", "Select Yes to delete them, together with the program files.")]),
         ("This cannot be undone.", [("second question", "This cannot be undone.")]),
         ("**No** there changes nothing.",
          [("second question", "Select No to change nothing. Amplifier stays installed.")])],
        [("**Cancel** changes nothing.", [("first question", "Select Cancel to keep Amplifier installed.")]),
         ("Amplifier stays installed.", [("first question", "Select Cancel to keep Amplifier installed.")])],
    ],
    [[("These are the results of an uninstall that finishes.", ABOUT),
      # "finds": the program looks once, after the answers, and not in an uninstall that goes on with a removal.
      ("If Amplifier finds that it is working on a chat, it asks before it stops that work.",
       [("working question", "Amplifier is working on a chat right now."),
        ("working question", "Select Yes to stop that work and uninstall now."),
        ("working question with three chats", "Amplifier is working on 3 chats right now.")]),
      ("An uninstall stops Amplifier and removes it from the Start menu and the Apps list.",
       [("first question", "Amplifier will stop and will no longer start when you sign in. Its entries in the "
                           "Start menu and the Apps list will be removed.")]),
      # Windows asks only when it trusts a certificate of this installation, and once for each that it holds.
      ("If Windows asks whether to delete a certificate, select **Yes** each time.",
       [("first question", "During the uninstall, Windows asks whether to delete the certificate"),
        ("first question with two certificates", "During the uninstall, Windows asks 2 times whether to delete a "
                                                 "certificate named")])]],
    [[('After a delete, Amplifier says "Amplifier is uninstalled."',
       [("confirmation of a delete", "Amplifier is uninstalled. It no longer runs, and it no longer starts when "
                                     "you sign in.")]),
      ("When you close that message, it starts to remove its last program files and then the folder.",
       [("confirmation of a delete", "When you close this message, Amplifier starts to remove its last program "
                                     "files.")]),
      ("A file or folder directly in the Amplifier folder whose name Amplifier does not use stays, and then the "
       "folder stays too.",
       [("confirmation of a delete", "The folder itself stays, because it holds entries that are not Amplifier's. "
                                     "They were not changed: mine.txt")]),
      # "that last step": the removal of the last program files and then of the folder. It also does not
      # finish when a program file that another program uses stayed: the folder then stays with that file
      # (`test_emptied_folder_that_windows_keeps_is_not_data_and_a_program_file_in_use_does_not_stop`).
      ("If that last step does not finish, the folder stays, and some program files can stay in it.",
       [("last step that cannot start", "Amplifier could not remove its last program files. They are in this "
                                        "folder,")])]],
    [[("An uninstall can stop before its end.", ABOUT),
      # "If": when Windows shows neither the message of a stop nor one further attempt, the person sees none.
      ("If Amplifier then shows a message, the message says what happened and what to do next.",
       [("stop at the journal", "Wait a minute or restart this PC, then uninstall again."),
        ("stop at the data", "Close other programs or restart this PC. Then uninstall Amplifier again to finish."),
        ("stop after the data", "Close other programs or restart this PC. Then uninstall Amplifier again to finish."),
        ("next uninstall stops at the journal",
         "Close other programs or restart this PC. Then uninstall Amplifier again to finish.")]),
      ('If it says that Amplifier "could not begin to delete its folder", that uninstall deleted nothing in the '
       "folder.",
       [("stop at the journal", "it could not begin to delete its folder. This uninstall deleted nothing in the "
                                "folder.")]),
      ('If it says that Amplifier "could not finish its removal from this PC", or that some of your chats, settings '
       'or saved accounts "could not be deleted", a part of the folder can already be deleted: do not open '
       "Amplifier, and uninstall again.",
       [("stop after the data", "Amplifier could not finish its removal from this PC."),
        ("stop after the data", "Then uninstall Amplifier again to finish. Do not open Amplifier before that: its "
                                "folder can be partly deleted."),
        ("stop at the data", "some of your chats, settings or saved accounts could not be deleted."),
        ("stop at the data", "Then uninstall Amplifier again to finish. Do not open Amplifier before that: its "
                             "folder can be partly deleted."),
        ("next uninstall stops at the journal", "Amplifier could not finish its removal from this PC."),
        ("next uninstall stops at the journal",
         "Do not open Amplifier before that: its folder can be partly deleted.")]),
      ('At that next uninstall, the first question can be another one: "The removal of Amplifier from this PC did '
       'not finish."',
       [("first question of the next uninstall", "The removal of Amplifier from this PC did not finish.")]),
      ("**Yes** there leads to the second question above, and **No** changes nothing.",
       [("first question of the next uninstall", "Select Yes to finish it now."),
        ("first question of the next uninstall", "Amplifier asks you once more before it deletes anything."),
        ("first question of the next uninstall", "Select No to change nothing now.")])]],
    [[('Amplifier can also ask only "Uninstall Amplifier?" with **Yes** and **No**, for example in an '
       "installation from an earlier setup file.",
       [("earlier question", "Uninstall Amplifier?")]),
      ("There, **Yes** uninstalls and leaves the folder, and **No** changes nothing.",
       [("earlier question", "Your chats, settings, accounts and the program files stay in this folder:")])]],
]


def readme_uninstall_item():
    """The item as the README has it: paragraphs, and in each its first part and its list entries."""
    readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n### Windows 11 (preview)\n", 1)[1].split("\n## ", 1)[0]
    # The item ends where the next list item or the next text at the left edge begins.
    (item,) = re.findall(r"\n- \*\*Uninstall:\*\* (.*?)(?=\n- \*\*|\n\n\S)", section, re.S)
    return [[" ".join(part.split()) for part in paragraph.split("\n  - ")] for paragraph in item.split("\n\n")]


def test_readme_uninstall_item_is_sentence_for_sentence_what_the_product_shows(deleter, monkeypatch):
    shown = shown_by_the_product(deleter, monkeypatch)
    # 1. The README item is exactly the sentences of the table. A second list entry for a button, a sentence
    #    that someone adds, a sentence that is gone or changed: each one fails here.
    assert readme_uninstall_item() == [[" ".join(sentence for sentence, _ in part) for part in paragraph]
                                       for paragraph in README_UNINSTALL]
    # 2. Each sentence rests on words that the product shows in the named case, not on words anywhere.
    used = set()
    for paragraph in README_UNINSTALL:
        for part in paragraph:
            for sentence, rests_on in part:
                if isinstance(rests_on, str):
                    assert rests_on in (WINDOWS, ABOUT), sentence
                    continue
                assert rests_on, sentence
                for case, words in rests_on:
                    assert words in shown[case], (sentence, case, words)
                    used.add(case)
    assert used == set(shown)
    #    Only these sentences rest on no text of the product.
    assert [sentence for paragraph in README_UNINSTALL for part in paragraph for sentence, rests_on in part
            if isinstance(rests_on, str)] == [
        "open **Settings > Apps > Installed apps**, find **Amplifier Unified (unsigned preview)**, select "
        "**More options (…)**, then **Uninstall**, then **Uninstall** again.",
        "These are the results of an uninstall that finishes.",
        "An uninstall can stop before its end."]
    # 3. The three buttons of the first question, each once and in the order of the question.
    assert [part.split("**")[1] for part in readme_uninstall_item()[0][1:]] == ["No", "Yes", "Cancel"]
    #    The earlier question has the buttons Yes and No: `owner_notice(confirm=True)` adds MB_YESNO (0x4).
    assert "0x51040 | (0x4 if confirm else 0)" in (REPOSITORY / "amplifier_web" / "windows_installation.py").read_text(
        encoding="utf-8")
    # 4. What the README says about a stop is true in the cases that it names.
    #    "deleted nothing in the folder" is said only of the stop at the journal of a first uninstall.
    assert "This uninstall deleted nothing in the folder." in shown["stop at the journal"]
    for case in ("stop at the data", "stop after the data", "next uninstall stops at the journal"):
        assert "deleted nothing" not in shown[case]
    #    The README does not say whether Amplifier opens after a stop: the product does not always know it.
    stops = " ".join(sentence for sentence, _ in README_UNINSTALL[3][0])
    assert "does not open" not in stops and "Start menu" not in stops
    #    The earlier README said of every uninstall: "It leaves the folder with your chats ...". Nowhere now.
    assert "It leaves the folder with your chats" not in " ".join(
        (REPOSITORY / "README.md").read_text(encoding="utf-8").split())


def test_the_lock_is_this_persons_own_and_has_one_name_for_every_sign_in_session(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from amplifier_web import windows_files
    person, other = "S-1-5-21-1-2-3-1001", "S-1-5-21-9-9-9-1002"
    names, waits, owners = [], [0, 0x102, 0x80, 0xffffffff], [person, person, other, person, person]

    class API:
        sid = person
        @contextmanager
        def private_security(self):
            yield plan.C.c_int(0)  # Stands for: this person is the owner; no access for another account.
        def GetSecurityInfo(self, handle, kind, wanted, owner, group, dacl, sacl, descriptor):
            assert (handle, kind, wanted, group, dacl, sacl) == (77, 6, 1, None, None, None)
            return 0
        def sid_text(self, owner):
            return owners.pop(0)
        def LocalFree(self, descriptor):
            names.append("freed")
    monkeypatch.setattr(windows_files, "_API", API)
    kernel = SimpleNamespace(
        CreateMutexW=lambda security, owned, name: names.append((security is not None, owned, name)) or 77,
        WaitForSingleObject=lambda handle, milliseconds: waits.pop(0),
        ReleaseMutex=lambda handle: names.append("released") or 1,
        CloseHandle=lambda handle: names.append("closed") or 1)
    monkeypatch.setattr(plan, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(plan.C, "WinDLL", lambda name, **options: kernel, raising=False)
    with plan.single(tmp_path):
        pass
    private, owned, name = names[0]
    # One holder at a time, created as this person's own. The shared name space: a second session of this
    # person meets the same lock.
    assert private and owned == 0
    assert name == "Global\\AmplifierUnified-installation-" + shell.identity(tmp_path)
    assert names[1:] == ["freed", "released", "closed"]
    # Taken by another window: Busy, and this window releases nothing that it does not hold.
    del names[:]
    with pytest.raises(plan.Busy):
        with plan.single(tmp_path):
            raise AssertionError("not entered")
    assert names[1:] == ["freed", "closed"]
    # A lock of this name that another account made is refused before any wait: never held, never released.
    del names[:]
    with pytest.raises(PermissionError):
        with plan.single(tmp_path):
            raise AssertionError("not entered")
    assert names[1:] == ["freed", "closed"] and waits == [0x80, 0xffffffff]
    # An abandoned mutex is taken and released by this thread.
    del names[:]
    with plan.single(tmp_path):
        pass
    assert names[1:] == ["freed", "released", "closed"]
    del names[:]
    monkeypatch.setattr(plan.C, "get_last_error", lambda: 6, raising=False)
    monkeypatch.setattr(plan.C, "WinError", lambda code: OSError(code, "wait failed"), raising=False)
    with pytest.raises(OSError, match="wait failed"):
        with plan.single(tmp_path):
            pytest.fail("A failed wait must not enter.")
    assert names[1:] == ["freed", "closed"] and not waits


def test_step_after_exit_removes_only_empty_named_folders():
    # There is no tree walk after exit. A non-empty folder must stay.
    script = removal.FINISH_SCRIPT
    body = script.split("function Clear-Folder($entry) {")[1].split("function Get-Record")[0]
    assert "if ($entry.PSIsContainer) { $entry.Delete() } else { Keep $entry.FullName }" in body
    assert "Get-ChildItem" not in body and "Remove-Item" not in body
    assert "Clear-File" not in body


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
    assert forms == {"Started", "Complete", "SourceStore", "Journal", "name", 'name+".zip"', 'name+".lnk"',
                     'name+".stderr.txt"', 'name+".failure.json"', "relative", "pair.Substring(split+1"}
    # Setup builds the journal's name in two places, and it never writes or deletes the journal. The
    # first reads that file: the look that chooses the text for a folder with no launcher program
    # (`Emptied`). In the same look it builds each of uninstall's data names, to look at it. The second
    # place only looks at the attributes of the entry with that name (`Signs`, the looks of a second
    # setup run): it does not open the entry.
    look = text.split("internal static bool Emptied(", 1)[1].split("internal static string Signs(", 1)[0]
    assert text.count("Journal") == 3 and 'internal const string Journal="' + plan.JOURNAL + '";' in text
    assert "string journal=Path.Combine(root,Journal);" in look
    signs = text.split("internal static string Signs(", 1)[1].split("internal static string Unopened(", 1)[0]
    assert 'try { File.GetAttributes(Path.Combine(root,Journal)); return "removal"; }' in signs
    assert "FileStream" not in signs and "File.Read" not in signs and "File.Open" not in signs
    assert "foreach(string name in Data) {\n                    string entry=Path.Combine(root,name);" in look
    # Its one open is for reading. The sharing modes let another program write or delete the file meanwhile.
    opened = "new FileStream(journal,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)"
    assert look.count("FileStream(") == 1 and look.count(opened) == 1
    for change in ("Write", "Create", "Move", "PrivateText", "Delete", "SetAttributes", "FileMode", "FileAccess"):
        assert change not in look.replace(opened, ""), change
    names.update(re.search(r'Started="([^"]+)", Complete="([^"]+)"', text).groups())
    # Setup names the source store through two constants: the folder that it creates in a new
    # installation, and the earlier name that its check of a folder still knows.
    stores = re.search(r'SourceStore="([^"]+)", EarlierSourceStore="([^"]+)"', text).groups()
    assert stores == STORES == (plan.STORE, "source-store")
    names.update(stores)
    # Setup creates only the first, in a new installation. The earlier name is in its check of a folder alone.
    assert text.count("EarlierSourceStore") == 2 and "SourceStore,EarlierSourceStore}.Any(" in text
    # Uninstall reads the release of the setup file from setup's completion record.
    assert 'PrivateText(Path.Combine(root,Complete),Json.Serialize(new Dictionary<string,object>{{"schema",1},{"version",version}}));' in text
    assert 'string version=Text(plan,"version");' in text
    # A setup file that is built from this source names its own release there, and that release makes `s`.
    release = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"$', (REPOSITORY / "pyproject.toml").read_text(), re.M)
    assert tuple(map(int, release.groups())) >= plan.STORE_SINCE
    lists = re.findall(r"foreach\((?:string|var) relative in new\[\]\{([^}]*)\}\)", text)
    pairs = re.search(r"foreach\(var pair in new\[\]\{([^}]*)\}\)", text).group(1)
    assert len(lists) == 2
    # In those lists only the new store's name is not a quoted text.
    assert {item for row in lists for item in row.split(",") if not re.fullmatch(r'"[^"]*"', item)} == {"SourceStore"}
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
    groups = plan.sort(names, True)  # An installation that this setup source makes.
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
    # The launcher names the source store beside that mapping, by two constants.
    assert "source_store(root)" in environment and "source-store" not in roots
    known = [consumer.RECEIPT, consumer.QUALIFICATION, consumer.RECORD, windows_consumer_shell.FILE, "export",
             "workspace", consumer.SOURCE_STORE, consumer.EARLIER_SOURCE_STORE, *re.findall(r': "([a-z-]+)"', roots),
             "launcher-0.95.0-0a1b2c3d", "launcher-0.95.0-0a1b2c3d-2", "launcher-not-used", "launcher-not-used-3",
             "amplifier_unified-0.96.0-py3-none-any.whl", "amplifier_unified-0.96.0-py3-none-any.whl.part",
             ".private-" + "0f" * 16 + ".tmp"]
    assert len(known) > 20 and plan.sort(known, True)["foreign"] == []
    # In an earlier installation the launcher uses `source-store`; a folder `s` there is not Amplifier's.
    assert plan.sort(known)["foreign"] == [consumer.SOURCE_STORE]
    # Exact spelling and exact forms. Anything else is not this installation's own and stays.
    foreign = ["App", "SHARED", "Workspace", "launcher-0.95.0-0A1B2C3D", "launcher-0.95.0-0a1b2c3", "launcher-x",
               "launcher-not-used-", "amplifier_unified-0.96.0-py3-none-any.whl.bak", "amplifier_unified-0.96-py3-none-any.whl",
               ".private-0f.tmp", "child-.stderr.txt", "Unified-Uninstall.json", "my notes.txt", "python.exe",
               "launcher-٠.٩٥.٠-0a1b2c3d", "app ", "app."]
    assert plan.sort(foreign)["foreign"] == foreign
    assert plan.sort(list(plan.DATA))["data"] == list(plan.DATA) == ["shared", "app", "capture", "ownership", "import",
                                                                    "workspace", "export"]


# One Open, Status, Stop or Uninstall as the launcher runs it: a new process
# behind the launcher's start program, with the installation's workspace as its
# current folder. Stand-ins: the check of the installed release (the throwaway
# folder holds no release), Task Scheduler, and the dialogs with their answers.
# Everything that deletes is real.
NATIVE_COMMAND = r"""
import asyncio, ctypes, hashlib, json, os, sys
from pathlib import Path
repository, packages, folder, evidence, report, operation, answers, settle, instant = sys.argv[1:10]
sys.path[:0] = [repository, packages]
from amplifier_web import installation_health, windows_consumer as consumer, windows_consumer_shell as shell
from amplifier_web import windows_files, windows_installation, windows_removal, windows_service
from amplifier_web import windows_uninstall as plan
root, answers, events = windows_files.address(folder), json.loads(answers), []
images = {}
for name, path in (("start", root / "tools/amplifier-unified/Scripts/python.exe"),
                   ("dll", root / "python/probe/probe.dll")):
    if path.exists():
        images[name] = hashlib.sha256(path.read_bytes()).hexdigest()
library = ctypes.WinDLL(str(root / "python/probe/probe.dll")) if "dll" in images else None
consumer.installation = lambda value: (windows_files.address(value), {})
consumer.environment = lambda *values: {}
async def working(home):
    return None
installation_health.windows_working = working
def uninstall(home, *, progress):
    for step in ("task-read", "stop", "task-remove"):
        progress(step)
    events.append("stop")
windows_service.uninstall = uninstall
async def prepare(*values, **options):
    if instant != "start-expected":
        raise AssertionError("Amplifier must not start from this folder.")
    events.append("start")
    return {"background": True}
windows_installation.prepare_background = prepare
if instant in ("journal-lost", "journal-replaced"):
    # Another program acts in the instant after the real writer renamed the journal into place and
    # before the writer's own checks: it deletes the journal, or it publishes its own file under that name.
    rename, once = windows_files._API.move, [instant]
    def move(self, handle, destination, *, replace):
        rename(self, handle, destination, replace=replace)
        if Path(str(destination)).name != plan.JOURNAL or not once:
            return
        way = once.pop()
        try:
            if way == "journal-lost":
                os.unlink(str(destination))
            else:
                with windows_files.directory(root, owned_root=root) as other:
                    other.publish_private(plan.JOURNAL, json.dumps(
                        {"schema": 1, "choice": "delete", "token": "f" * 32}).encode(), replace=True)
            events.append(way)
        except Exception as error:
            events.append(way + " failed: " + type(error).__name__ + " " + str(error)[:200])
    windows_files._API.move = move
def question(text, *, cancel=False):
    events.append("question")
    return answers.pop(0)
plan.question = question
windows_installation.owner_notice = lambda text, confirm=False: True
shell.SETTLE_SECONDS = int(settle)
lock = plan.single
plan.single = lambda folder, wait=1.0: lock(folder, wait=1.0)
start = windows_removal.finish_after_exit
def finish(*values, **options):
    events.append("after-exit")
    return start(*values, report=report, **options)
windows_removal.finish_after_exit = finish
outcome = {"folder": os.getcwd(), "process": os.getpid(), "parent": os.getppid()}
try:
    if operation == "open":
        outcome["result"] = asyncio.run(consumer.launch(root, open_browser=False))
    else:
        outcome["result"] = asyncio.run(consumer.maintenance(root, operation))
except consumer.Unfinished as stopped:
    outcome.update(step=stopped.step, refused=isinstance(stopped, consumer.Refused),
                   error=type(stopped.error).__name__, notice=stopped.notice,
                   detail=str(stopped.error)[:400], code=getattr(stopped.error, "winerror", None))
parked = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
          for path in (root / plan.PARK).iterdir()
          if path.is_file() and not path.name.endswith(".hold")} if (root / plan.PARK).exists() else {}
outcome.update(events=events, folderAfter=os.getcwd(), left=sorted(os.listdir(root)),
               images=images, parked=parked)
Path(evidence).write_text(json.dumps(outcome))
"""


@pytest.fixture
def native_installation(tmp_path, request):
    """A used installation in a private throwaway folder, with its real Windows entries.

    Its source store has the name that an earlier setup file gave it, or the
    name that a case asks for (`STORES`).

    Real: the private folder and its records, the Start-menu shortcuts
    (ShellLink COM), the Apps-list entry (HKCU), the launcher's start chain (a
    venv start program in `tools`, whose child is the real Python), junctions,
    a long path. The outside folder stands for the rest of the person's disk.
    """
    import _winapi
    import winreg
    from amplifier_web import windows_files
    from amplifier_web.deployment import write_private
    store = getattr(request, "param", "source-store")
    assert store in STORES
    tag = uuid.uuid4().hex[:10]
    root = windows_files.address(Path.home() / ("au-delete-test-" + tag))
    outside = Path.home() / ("au-delete-outside-" + tag)
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    entries = shell.programs() / ("Amplifier Unified - " + shell.identity(root))
    links = []
    try:
        registration = _install_native_entries(root)
        made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / "tools" / "amplifier-unified")],
                              capture_output=True, text=True, timeout=120)
        assert made.returncode == 0, made.stderr[-1500:]
        (outside / "deep").mkdir(parents=True)
        (outside / "keep.txt").write_text("keep outside")
        (outside / "deep" / "also.txt").write_text("keep deep outside")
        tree = {"shared/keys.env": "KEY=not-a-real-key", "shared/settings.yaml": "a: 1",
                "app/chats/one/message.txt": "hello", "app/app.sqlite3": "data", "capture/c.jsonl": "{}",
                "ownership/o.json": "{}", "import/i.txt": "i", "workspace/project/notes.txt": "the person's own file",
                "export/local-ca.cer": "certificate", "cache/wheels/x.whl": "x", store + "/objects/s": "s",
                "temp/t.tmp": "t", "python-bin/python.exe": "MZ", "tool-bin/amplifier-unified.exe": "MZ",
                "components.txt": "c", "install.log": "l", "uv.zip": "z", "private-git.json": "{}",
                "private-git.py": "#", "amplifier_unified-1.0.0-py3-none-any.whl": "w", "Launch Unified.lnk": "l",
                "launcher-1.0.0-0a1b2c3d/Scripts/pythonw.exe": "MZ", "git/cmd/git.exe": "MZ", "uv/uv.exe": "MZ",
                "consumer-install.json": "{}", "consumer-qualified.json": "{}",
                "python/cpython-3.13.17-windows-x86_64-none/python.exe": "MZ, never started",
                "python/cpython-3.13.17-windows-x86_64-none/Lib/os.py": "#"}
        for name, text in tree.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        (root / "python/probe").mkdir()
        source = next(path for path in (Path(sys.base_prefix) / "vcruntime140.dll",
                                        Path(os.environ["SystemRoot"]) / "System32/version.dll") if path.exists())
        shutil.copyfile(source, root / "python/probe/probe.dll")
        # A path far beyond 260 characters in a cache.
        deep = "\\\\?\\" + str(root / "cache" / "long")
        for index in range(7):
            deep += "\\" + (f"segment-{index:02d}-" + "x" * 40)
            os.makedirs(deep)
        with open(deep + "\\leaf.txt", "w") as stream:
            stream.write("deep")
        assert len(deep) > 400
        # Links that lead out of the folder, and the link that uv makes inside `python`.
        for link_path, target in ((root / "app" / "junction", outside), (root / "tools" / "junction", outside),
                                  (root / "python" / "cpython-3.13-windows-x86_64-none",
                                   root / "python" / "cpython-3.13.17-windows-x86_64-none")):
            _winapi.CreateJunction(str(target), str(link_path))
            links.append(link_path)
        assert (root / "app" / "junction" / "keep.txt").read_text() == "keep outside"
        # Setup's completion record, private to this person, as setup writes it.
        # The release of the setup file: the first that names the store `s`, or the one before it.
        release = "0.98.5" if store == "s" else "0.98.4"
        write_private(root / plan.COMPLETE, json.dumps({"schema": 1, "version": release}), owned_root=root)
        assert plan.short_store(root) == (store == "s")
        packages = sysconfig.get_paths()["purelib"]

        def run(operation, answers=(), *, settle=15, instant=""):
            """Returns what the process observed. Its exit is awaited; the step after exit is not.

            `instant`: `journal-lost` or `journal-replaced` lets another program act once inside the real
            writer, directly after it renamed the journal into place. `start-expected`: Open may reach its
            start step, and the process records that; otherwise a start fails the process.
            """
            evidence = tmp_path / f"evidence-{uuid.uuid4().hex[:8]}.json"
            environment = os.environ.copy()
            environment.update(TEMP=str(root / "temp"), TMP=str(root / "temp"))
            process = subprocess.run(
                [str(root / "tools" / "amplifier-unified" / "Scripts" / "python.exe"), "-I", "-c", NATIVE_COMMAND,
                 str(REPOSITORY), packages, str(root), str(evidence), str(tmp_path / "after-exit.json"),
                 operation, json.dumps(list(answers)), str(settle), instant],
                cwd=root / "workspace", env=environment, capture_output=True, timeout=180)
            assert process.returncode == 0, process.stderr.decode("utf-8", errors="replace")[-3000:]
            return json.loads(evidence.read_text())

        yield dict(root=root, outside=outside, entries=entries, registration=registration, run=run,
                   report=tmp_path / "after-exit.json", store=store)
    finally:
        for path in links:  # A link that a failed run left: the link itself, never its target.
            if os.path.lexists(path):
                os.rmdir(path)
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, shell.REGISTRY + shell.identity(root))
        except OSError:
            pass
        shutil.rmtree(entries, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
def _outside_unchanged(outside):
    assert sorted(str(path.relative_to(outside)) for path in outside.rglob("*")) == [
        "deep", str(Path("deep") / "also.txt"), "keep.txt"]
    assert (outside / "keep.txt").read_text() == "keep outside"
    assert (outside / "deep" / "also.txt").read_text() == "keep deep outside"


def _tree(root):
    return sorted(str(Path(folder, name).relative_to(root)) for folder, folders, files in os.walk(root)
                  for name in folders + files)


def _finished(done):
    """The result of a run that finished. A run that stopped fails here, with what its process observed."""
    assert "result" in done, {key: done.get(key) for key in (
        "step", "error", "code", "detail", "refused", "notice", "events", "left")}
    return done["result"]


def _without_kept_file(names):
    kept = [name for name in names if re.fullmatch(r"[0-9a-f]{32}\.hold", name)]
    assert len(kept) == 1, names
    return set(names) - set(kept)


@pytest.mark.skipif(os.name != "nt", reason="Real folder, Windows entries, start chain and step after exit")
@pytest.mark.parametrize("native_installation", STORES, indirect=True)
def test_native_delete_choice_removes_the_folder_and_nothing_outside_it(native_installation):
    """Yes, Yes on a whole installation: afterwards the folder does not exist.

    Once with the source store of a new installation (`s`) and once with the
    store of an earlier one (`source-store`). Neither is something that is not
    Amplifier's (`kept` is empty), and no store is left.
    """
    native, root = native_installation, native_installation["root"]
    other, = set(STORES) - {native["store"]}
    assert (root / native["store"] / "objects" / "s").read_text() == "s" and not os.path.lexists(root / other)
    done = native["run"]("uninstall", ["yes", "yes"])
    assert _finished(done) == {"ok": True, "operation": "uninstall", "userDataRetained": False,
                               "certificate": "none", "kept": [], "afterExit": True, "programLeft": []}
    assert set(done["images"]) == {"start", "dll"}
    assert set(done["images"].values()) <= set(done["parked"].values())
    assert done["events"] == ["question", "question", "stop", "after-exit"]
    # The process started in the installation's workspace, behind the start program, and left the folder.
    assert Path(done["folder"]) == root / "workspace" and not Path(done["folderAfter"]).is_relative_to(root)
    assert done["process"] != done["parent"]
    # Before exit only the flat folder, journal and at most the emptied workspace stayed.
    assert {plan.PARK, plan.JOURNAL, plan.COMPLETE} <= _without_kept_file(done["left"]) <= {
        plan.PARK, plan.JOURNAL, plan.COMPLETE, "workspace"}
    # The Windows entries are gone.
    assert shell.registry(root) is None and not native["entries"].exists()
    # The step after exit removes the running program's files, then the journal, then the empty folder.
    report = _finish_report(native["report"], timeout=200)
    assert report["reason"] == "" and report["folderRemoved"] and not report["left"], report
    assert not os.path.lexists(root)
    _outside_unchanged(native["outside"])
    print("UNINSTALL_CHAIN " + json.dumps({"process": done, "afterExit": report}, separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="Real folder, Windows entries, start chain and step after exit")
def test_native_delete_choice_keeps_a_folder_s_in_an_earlier_installation(native_installation):
    """Yes, Yes on an earlier installation that also holds a folder `s`: only `source-store` goes.

    The folder `s` is not Amplifier's there. It stays with its file, and so
    the installation folder stays with both records.
    """
    native, root = native_installation, native_installation["root"]
    assert native["store"] == "source-store" and not plan.short_store(root)
    mine = root / "s" / "deep" / "mine.txt"
    mine.parent.mkdir(parents=True)
    mine.write_text("not Amplifier's")
    done = native["run"]("uninstall", ["yes", "yes"])
    assert _finished(done)["afterExit"] and done["result"]["kept"] == ["s"], done
    assert done["result"]["programLeft"] == [] and done["result"]["userDataRetained"] is False
    assert {plan.PARK, plan.JOURNAL, plan.COMPLETE, "s"} <= _without_kept_file(done["left"]) <= {
        plan.PARK, plan.JOURNAL, plan.COMPLETE, "s", "workspace"}
    assert shell.registry(root) is None and not native["entries"].exists()
    report = _finish_report(native["report"], timeout=200)
    assert report["reason"] == "" and not report["folderRemoved"], report
    # The folder keeps both records, as with every entry that is not Amplifier's.
    assert sorted(os.listdir(root)) == sorted(["s", plan.COMPLETE, plan.JOURNAL])
    assert mine.read_text() == "not Amplifier's" and sorted(os.listdir(root / "s")) == ["deep"]
    assert plan.completed(root) and not plan.short_store(root)
    _outside_unchanged(native["outside"])
    print("UNINSTALL_FOLDER_S " + json.dumps({"process": done, "afterExit": report}, separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="Real folder, Windows entries and start chain")
@pytest.mark.parametrize("native_installation", STORES, indirect=True)
def test_native_keep_choice_leaves_the_source_store_and_every_file(native_installation):
    """No on a whole installation: the Windows entries go, and nothing in the folder is deleted."""
    native, root = native_installation, native_installation["root"]
    store = root / native["store"] / "objects" / "s"
    before = _tree(root)
    assert str(store.relative_to(root)) in before
    done = native["run"]("uninstall", ["no"])
    assert _finished(done) == {"ok": True, "operation": "uninstall", "userDataRetained": True,
                              "installationFilesRetained": True, "certificate": "none"}
    assert done["events"] == ["question", "stop"]
    # Every entry that was in the folder is still there, the store with its bytes. No removal began.
    assert set(before) <= set(_tree(root)) and store.read_text() == "s"
    assert (root / "shared" / "keys.env").read_text() == "KEY=not-a-real-key"
    assert not plan.unfinished(root) and plan.completed(root) and not os.path.lexists(root / plan.PARK)
    assert not native["report"].exists()
    # The Windows entries are gone.
    assert shell.registry(root) is None and not native["entries"].exists()
    _outside_unchanged(native["outside"])


@pytest.mark.skipif(os.name != "nt", reason="The running Python and installed package are deleted on native Windows")
def test_native_uninstall_deletes_the_python_it_runs_from_and_still_ends_cleanly(tmp_path):
    """No repository or outside standard library can hide an import after deletion."""
    import winreg
    from amplifier_web import windows_files
    from amplifier_web.deployment import write_private

    started = time.monotonic()
    tag = uuid.uuid4().hex[:10]
    root = windows_files.address(Path.home() / ("au-self-test-" + tag))
    outside = Path.home() / ("au-self-outside-" + tag)
    entries = shell.programs() / ("Amplifier Unified - " + shell.identity(root))
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    try:
        _install_native_entries(root)
        base, venv = root / "python/cpython-self", root / "tools/amplifier-unified"
        shutil.copytree(sys.base_prefix, base, ignore=shutil.ignore_patterns(
            "test", "site-packages", "idlelib", "tkinter", "ensurepip", "tcl", "Doc",
            "include", "libs", "Tools", "__pycache__", "*.pdb"))
        made = subprocess.run([str(base / "python.exe"), "-I", "-m", "venv", "--without-pip", str(venv)],
                              capture_output=True, timeout=30)
        assert made.returncode == 0, made.stderr.decode("utf-8", errors="replace")[-1500:]
        shutil.copytree(REPOSITORY / "amplifier_web", venv / "Lib/site-packages/amplifier_web",
                        ignore=shutil.ignore_patterns("static", "__pycache__"))
        for name, text in DATA.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        write_private(root / plan.COMPLETE, json.dumps({"schema": 1, "version": "1.0.0"}), owned_root=root)
        (outside / "deep").mkdir(parents=True)
        (outside / "keep.txt").write_text("keep outside")
        (outside / "deep/also.txt").write_text("keep deep outside")
        outside_before = {str(path.relative_to(outside)): path.stat().st_file_attributes
                          for path in outside.rglob("*")}
        evidence, report = tmp_path / "self.json", tmp_path / "self-after-exit.json"
        command = r"""
import asyncio, json, os, sys
from pathlib import Path
packages, folder, evidence, report = sys.argv[1:5]
# Only third-party packages come from outside; the installed package wins.
sys.path.append(packages)
import amplifier_web
from amplifier_web import installation_health, windows_consumer as consumer
from amplifier_web import windows_consumer_shell as shell, windows_files, windows_installation
from amplifier_web import windows_removal as removal, windows_service, windows_uninstall as plan
root = windows_files.address(folder)
paths = dict(package=amplifier_web.__file__, base=sys.base_prefix,
             stdlib=os.__file__, executable=sys.executable)
assert all(Path(path).is_relative_to(root) for path in paths.values()), paths
consumer.installation = lambda value: (windows_files.address(value), {})
consumer.environment = lambda *values: {}
async def working(home):
    return None
installation_health.windows_working = working
def uninstall(home, *, progress):
    for step in ("task-read", "stop", "task-remove"):
        progress(step)
windows_service.uninstall = uninstall
answers = iter(("yes", "yes"))
plan.question = lambda text, cancel=False: next(answers)
windows_installation.owner_notice = lambda text, confirm=False: True
shell.SETTLE_SECONDS = 2
start = removal.finish_after_exit
def after_exit(*values, **options):
    return start(*values, report=report, **options)
removal.finish_after_exit = after_exit
# Observe the walk's own count without listing or importing after deletion.
remove, parked = removal.Folder.remove, []
def counted(self, *values, **options):
    result = remove(self, *values, **options)
    if "parked" in result:
        parked.append(result["parked"])
    return result
removal.Folder.remove = counted
result = asyncio.run(consumer.maintenance(root, "uninstall"))
# This file is written only after the public call and asyncio shutdown returned.
with open(evidence, "w", encoding="utf-8") as stream:
    json.dump(dict(paths=paths, result=result, parked=sum(parked),
                   left=sorted(os.listdir(root))), stream)
"""
        environment = os.environ.copy()
        environment.update(TEMP=str(root / "workspace"), TMP=str(root / "workspace"),
                           PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.run(
            [str(venv / "Scripts/python.exe"), "-I", "-c", command, sysconfig.get_paths()["purelib"],
             str(root), str(evidence), str(report)],
            cwd=root / "workspace", env=environment, capture_output=True, timeout=45)
        stderr = process.stderr.decode("utf-8", errors="replace")
        assert process.returncode == 0 and not stderr, f"exit={process.returncode}\n{stderr[-1500:]}"
        done = json.loads(evidence.read_text(encoding="utf-8"))
        assert set(done["paths"]) == {"package", "base", "stdlib", "executable"}
        assert all(Path(path).is_relative_to(root) for path in done["paths"].values())
        assert Path(done["paths"]["base"]) == base
        assert Path(done["paths"]["package"]) == venv / "Lib/site-packages/amplifier_web/__init__.py"
        assert done["result"] == {"ok": True, "operation": "uninstall", "userDataRetained": False,
                                  "certificate": "none", "kept": [], "afterExit": True, "programLeft": []}
        assert done["parked"] >= 4, done
        assert {plan.PARK, plan.JOURNAL, plan.COMPLETE} <= _without_kept_file(done["left"]) <= {
            plan.PARK, plan.JOURNAL, plan.COMPLETE, "workspace"}
        assert not (root / "python").exists() and not (root / "tools").exists()
        # The helper can already have finished. It may only remove these entries.
        try:
            after = set(os.listdir(root))
        except FileNotFoundError:
            after = set()
        assert {name for name in after if not re.fullmatch(r"[0-9a-f]{32}\.hold", name)} <= {
            plan.PARK, plan.JOURNAL, plan.COMPLETE, "workspace"}
        finished = _finish_report(report, timeout=30)
        assert finished["folderRemoved"] and finished["reason"] == "", finished
        assert finished["left"] == [] and finished["leftCount"] == 0, finished
        assert not os.path.lexists(root) and shell.registry(root) is None and not entries.exists()
        _outside_unchanged(outside)
        assert {str(path.relative_to(outside)): path.stat().st_file_attributes
                for path in outside.rglob("*")} == outside_before
        elapsed = round(time.monotonic() - started, 2)
        print("UNINSTALL_SELF " + json.dumps({**done, "report": finished, "elapsed": elapsed},
                                            separators=(",", ":")))
        assert elapsed < 90, f"The self-contained uninstall took {elapsed} seconds."
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, shell.REGISTRY + shell.identity(root))
        except OSError:
            pass
        shutil.rmtree(entries, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


@pytest.mark.skipif(os.name != "nt", reason="Real folder, Windows entries, start chain and step after exit")
def test_native_interrupted_removal_refuses_to_start_and_a_second_uninstall_finishes(native_installation):
    """A data file in use stops the removal; Open, Status and Stop refuse; a second uninstall finishes.

    A file that is not Amplifier's is in the folder: it stays, and so the folder stays.
    """
    from amplifier_web import windows_consumer as consumer, windows_files
    native, root = native_installation, native_installation["root"]
    (root / "mine.txt").write_text("not Amplifier's")
    busy = open(root / "app" / "busy.db", "w")  # Another program: a handle without delete sharing.
    try:
        first = native["run"]("uninstall", ["yes", "yes"], settle=2)
        assert (first["step"], first["error"], first["refused"]) == ("data", "Held", False), first
        assert "could not be deleted" in first["notice"] and first["events"] == ["question", "question", "stop"]
        journal = plan.pending(root)
        assert journal is not None and journal["choice"] == "delete" and plan.unfinished(root)
        # The stored keys went first; the file in use and setup's completion record stay.
        assert not (root / "shared").exists() and (root / "app" / "busy.db").exists()
        assert (root / plan.COMPLETE).exists() and plan.completed(root)
        # The Windows entries and the launcher are whole, so the person can uninstall again.
        assert shell.registry(root) == native["registration"]
        assert {path.name for path in native["entries"].iterdir()} == set(shell.LINKS)
        for name in ("tools", "python", "git", "uv", "consumer-install.json", shell.FILE, "cache", "mine.txt"):
            assert (root / name).exists(), name
        # The private note of the failure is in the folder itself, never in the app's folder.
        with windows_files.directory(root, owned_root=root) as owned:
            assert json.loads(owned.read_private(consumer.RECORD, limit=65536))["step"] == "data"
        assert not (root / "app" / "config").exists() and not native["report"].exists()
        # Open, Status and Stop refuse and change nothing.
        before = _tree(root)
        for operation in ("open", "status", "stop"):
            refused = native["run"](operation)
            assert (refused["step"], refused["refused"]) == ("removing", True), refused
            assert "select Uninstall Amplifier" in refused["notice"] and not refused["events"]
        # No to the second uninstall changes nothing.
        declined = native["run"]("uninstall", ["no"])
        assert _finished(declined) == {"ok": True, "cancelled": True} and declined["events"] == ["question"]
        assert _tree(root) == before and plan.pending(root) == journal
        # While another Amplifier window holds the installation's lock, Open and Uninstall refuse.
        with plan.single(root):
            _busy_on_another_thread(root)
            for operation, answers in (("open", []), ("uninstall", ["yes", "yes"])):
                waiting = native["run"](operation, answers)
                assert (waiting["step"], waiting["refused"], waiting["error"]) == ("busy", True, "Busy"), waiting
        assert _tree(root) == before and plan.pending(root) == journal
        # Yes and then No changes nothing either.
        declined = native["run"]("uninstall", ["yes", "no"])
        assert _finished(declined) == {"ok": True, "cancelled": True} and declined["events"] == ["question", "question"]
        assert _tree(root) == before and plan.pending(root) == journal
    finally:
        busy.close()
    # The file is free: Yes twice finishes the removal, with the stop again and a new token.
    done = native["run"]("uninstall", ["yes", "yes"])
    assert _finished(done)["afterExit"] and done["result"]["kept"] == ["mine.txt"], done
    assert done["result"]["programLeft"] == []
    assert set(done["images"].values()) <= set(done["parked"].values())
    assert {plan.PARK, plan.JOURNAL, plan.COMPLETE, "mine.txt"} <= _without_kept_file(done["left"]) <= {
        plan.PARK, plan.JOURNAL, plan.COMPLETE, "mine.txt", "workspace"}
    assert done["events"] == ["question", "question", "stop", "after-exit"]
    assert shell.registry(root) is None and not native["entries"].exists()
    report = _finish_report(native["report"], timeout=200)
    assert report["reason"] == "" and not report["folderRemoved"], report
    # A foreign entry keeps both records. Setup must never empty this folder.
    assert sorted(os.listdir(root)) == ["mine.txt", plan.COMPLETE, plan.JOURNAL]
    assert plan.completed(root)
    assert (root / "mine.txt").read_text() == "not Amplifier's"
    resumed = plan.pending(root)
    assert resumed["choice"] == "delete" and resumed["token"] != journal["token"]
    _outside_unchanged(native["outside"])
    print("UNINSTALL_RESUME " + json.dumps({"process": done, "afterExit": report}, separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="The real private writer, a real rename and the real look for the journal")
def test_native_journal_that_loses_its_name_or_is_replaced_in_the_instant_of_its_write(native_installation):
    """Inside the real writer, directly after its rename: a program deletes the journal, or replaces it.

    Deleted: the writer reports the journal as written, the look does not find it, and the uninstall stops
    before it deletes anything. A first uninstall and a second one show their own text. No journal is in the
    folder then, so Open reaches its start step. Replaced: the name is there, the removal goes on to its
    end, and its last step refuses the journal that is not its own.
    """
    from amplifier_web import windows_consumer as consumer
    native, root = native_installation, native_installation["root"]

    def tree():
        # Without the installation's own temporary folder: a process of this test has it as TEMP.
        return {name for name in _tree(root) if Path(name).parts[0] != "temp"}

    def data():
        """Every file below the person's data folders, with its bytes."""
        return {str(Path(folder, name).relative_to(root)): Path(folder, name).read_bytes()
                for top_level in plan.DATA for folder, _, files in os.walk(root / top_level) for name in files}

    whole, whole_data = tree(), data()
    note = Path("app") / "config" / "services" / consumer.RECORD
    assert len(whole_data) >= 9 and str(note) not in whole_data

    def reaches_its_start_step():
        opened = native["run"]("open", instant="start-expected")
        assert _finished(opened) == {"background": True} and opened["events"] == ["start"], opened

    # A first uninstall.
    first = native["run"]("uninstall", ["yes", "yes"], instant="journal-lost")
    assert first["events"] == ["question", "question", "stop", "journal-lost"], first
    assert (first["step"], first["error"], first["refused"], first["code"]) == ("journal", "OSError", False, None), first
    assert first["detail"] == "The removal's journal is not in the folder after it was written."
    assert first["notice"] == consumer._UNFINISHED["journal"] + "\n\nReference: journal, OSError"
    assert "This uninstall deleted nothing in the folder." in first["notice"]
    assert not plan.unfinished(root) and plan.pending(root) is None
    # Nothing is gone. New: only the private note of the failure, beside the app's other service records.
    assert whole <= tree() and tree() - whole == {str(note)}, sorted(tree() ^ whole)
    kept = data()
    assert json.loads(kept.pop(str(note)))["step"] == "journal" and kept == whole_data
    assert shell.registry(root) == native["registration"]
    assert {path.name for path in native["entries"].iterdir()} == set(shell.LINKS)
    # The text says that Amplifier might not open. Here no journal is in the folder: Open goes on.
    reaches_its_start_step()

    # A second uninstall. The earlier one deleted the stored keys and stopped at a data file in use.
    busy = open(root / "app" / "busy.db", "w")
    try:
        stopped = native["run"]("uninstall", ["yes", "yes"], settle=2)
        assert (stopped["step"], stopped["error"]) == ("data", "Held"), stopped
        earlier = plan.pending(root)
        assert earlier is not None and earlier["choice"] == "delete" and not (root / "shared").exists()
        partly, partly_data = tree(), data()
        second = native["run"]("uninstall", ["yes", "yes"], settle=2, instant="journal-lost")
    finally:
        busy.close()
    assert second["events"] == ["question", "question", "stop", "journal-lost"], second
    assert (second["step"], second["error"], second["refused"]) == ("journal", "OSError", False), second
    assert second["notice"] == consumer._UNFINISHED["removal"] + "\n\nReference: journal, OSError"
    assert "deleted nothing" not in second["notice"] and "does not open" not in second["notice"]
    # This uninstall deleted nothing more. Its write took the earlier journal's place, and that journal is gone.
    assert tree() == partly - {plan.JOURNAL}, sorted(tree() ^ partly)
    assert data() == partly_data and "shared" not in {Path(name).parts[0] for name in partly_data}
    assert not plan.unfinished(root) and (root / "app" / "busy.db").exists()
    assert shell.registry(root) == native["registration"]
    # The design's limit, on a real folder: without the journal Open goes on, on a folder that is partly deleted.
    reaches_its_start_step()

    # The next uninstall finds no journal, so it is a first one again. A program replaces its journal.
    done = native["run"]("uninstall", ["yes", "yes"], instant="journal-replaced")
    assert done["events"] == ["question", "question", "stop", "journal-replaced"], done
    # The removal went on to its end: the data, the program files and the Windows entries are gone.
    assert _finished(done) == {"ok": True, "operation": "uninstall", "userDataRetained": False,
                               "certificate": "none", "kept": [], "afterExit": False, "programLeft": None}
    assert shell.registry(root) is None and not native["entries"].exists()
    # Its last step refused the journal that is not its own: no flat folder, no kept file, no step after exit.
    left = {plan.JOURNAL, plan.COMPLETE, "python", "tools"}
    assert left <= set(done["left"]) <= left | {"workspace"}, done["left"]
    assert set(os.listdir(root)) == set(done["left"]) and not native["report"].exists()
    assert plan.pending(root) == {"schema": 1, "choice": "delete", "token": "f" * 32}
    # The name is there, so the look that Open makes for a journal refuses. (The launcher's check of its
    # own records is a stand-in in this command; on a real folder that check fails first, because the
    # removal deleted those records. The command starts in the workspace folder.)
    os.makedirs(root / "workspace", exist_ok=True)
    refused = native["run"]("open")
    assert (refused["step"], refused["refused"]) == ("removing", True) and not refused["events"], refused
    _outside_unchanged(native["outside"])
    print("UNINSTALL_JOURNAL_INSTANT " + json.dumps({"first": first, "second": second, "replaced": done},
                                                    separators=(",", ":")))


@pytest.mark.skipif(os.name != "nt", reason="A real case-only rename on NTFS and the real folder listing")
def test_native_data_folder_renamed_only_by_case_deletes_nothing(native_installation):
    """`app` renamed to `App`: Windows still opens it as `app`, but it is not the entry that setup made."""
    native, root = native_installation, native_installation["root"]
    os.rename(root / "app", root / "App")
    assert "App" in os.listdir(root) and "app" not in os.listdir(root) and (root / "app" / "app.sqlite3").exists()
    before = _tree(root)
    refused = native["run"]("uninstall", ["yes", "yes"])
    assert (refused["step"], refused["error"], refused["refused"]) == ("folder", "ValueError", False), refused
    assert "it deleted nothing" in refused["notice"] and "select No in the first question" in refused["notice"]
    assert refused["events"] == ["question", "question"]  # The app was not stopped.
    # Nothing was deleted and no journal was written; only the private note of the failure is new. It is
    # in the folder itself: the private-file boundary refuses the altered spelling of the app's own folder.
    after, note = _tree(root), "consumer-maintenance.json"
    assert [name for name in after if name != note] == before and note in after
    assert not plan.unfinished(root) and plan.completed(root)
    assert shell.registry(root) == native["registration"]
    assert {path.name for path in native["entries"].iterdir()} == set(shell.LINKS)
    assert not native["report"].exists()
    # The real folder: the walk matches the exact name only, and one listing reports the other spelling as held.
    with removal.folder(root) as owned:
        assert owned.remove(["app"]) == {"removed": 0, "left": [], "leftCount": 0}
        assert owned.holding(["app", "shared", "absent"]) == ["app", "shared"]
    assert (root / "App" / "app.sqlite3").read_text() == "data"
    _outside_unchanged(native["outside"])
    print("UNINSTALL_CASE " + json.dumps({"step": refused["step"], "events": refused["events"],
                                        "unchanged": True}))


@pytest.mark.skipif(os.name != "nt", reason="The real named lock and its real owner check")
def test_native_lock_refuses_an_object_of_its_name_that_another_owner_made():
    """Another account can create the lock's name first. Amplifier then refuses; it never uses that object."""
    import ctypes
    from amplifier_web import windows_files
    root = windows_files.address(Path.home() / ("au-lock-test-" + uuid.uuid4().hex[:10]))
    api = windows_files._API()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CreateSemaphoreW.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_long, ctypes.c_wchar_p]
    kernel.CreateSemaphoreW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    name = "Global\\AmplifierUnified-installation-" + shell.identity(root)

    def owner_of(handle):
        owner, descriptor = windows_files.PVOID(), windows_files.PVOID()
        assert api.GetSecurityInfo(handle, 6, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor)) == 0
        try:
            return api.sid_text(owner)
        finally:
            api.LocalFree(descriptor)

    # 1. The lock that Amplifier makes is this person's own, also for an elevated account, and it excludes.
    with plan.single(root):
        mine = kernel.CreateMutexW(None, 0, name)
        existed = ctypes.get_last_error()
        assert mine and existed == 183 and owner_of(mine) == api.sid  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(mine)
        _busy_on_another_thread(root)
    with plan.single(root, wait=0.2):  # Released: it can be taken again.
        pass
    # 2. An object of the same name with another owner. This lane's account is elevated, so an object
    #    with default security belongs to the Administrators group, not to this person.
    foreign = kernel.CreateMutexW(None, 0, name)
    existed = ctypes.get_last_error()
    assert foreign and existed != 183
    try:
        assert owner_of(foreign) != api.sid, "This lane needs an account whose default owner is not the person."
        with pytest.raises(PermissionError) as refused:
            with plan.single(root, wait=0.2):
                raise AssertionError("not entered")
        assert "another account" in str(refused.value)
    finally:
        kernel.CloseHandle(foreign)
    with plan.single(root, wait=0.2):  # The foreign object is gone: the lock works again.
        pass
    # A semaphore of this name cannot pretend to be a mutex, whatever its count.
    semaphore = kernel.CreateSemaphoreW(None, 2, 2, name)
    assert semaphore
    try:
        with pytest.raises(OSError) as refused:
            with plan.single(root, wait=0.2):
                pytest.fail("A semaphore must never be used as the lock.")
        assert not isinstance(refused.value, plan.Busy)
        semaphore_error = refused.value.winerror
    finally:
        kernel.CloseHandle(semaphore)
    # Keep the object alive while its owner thread ends without releasing it.
    with api.private_security() as security:
        abandoned = kernel.CreateMutexW(ctypes.byref(security), 0, name)
    assert abandoned
    waits = []
    try:
        owner = threading.Thread(target=lambda: waits.append(kernel.WaitForSingleObject(abandoned, 1000)))
        owner.start()
        owner.join(timeout=5)
        assert not owner.is_alive() and waits == [0]
        with plan.single(root, wait=0.2):
            _busy_on_another_thread(root)
    finally:
        kernel.CloseHandle(abandoned)
    print("UNINSTALL_LOCK " + json.dumps({"otherThreadBusy": True, "otherOwnerRefused": True,
                                         "semaphoreError": semaphore_error, "abandonedTaken": True}))


def _busy_on_another_thread(root):
    result = []
    def take():
        try:
            with plan.single(root, wait=0.2):
                result.append("taken")
        except BaseException as error:
            result.append(type(error).__name__)
    thread = threading.Thread(target=take)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and result == ["Busy"], result


@pytest.mark.skipif(os.name != "nt", reason="Real NTFS sharing: an entry that the walk holds cannot be moved")
def test_native_entry_that_the_walk_holds_cannot_be_moved_out_of_the_folder():
    """Between the walk's open of an entry and its deletion, no program can move that entry elsewhere."""
    import _winapi
    from amplifier_web import windows_files
    tag = uuid.uuid4().hex[:10]
    root = windows_files.address(Path.home() / ("au-hold-test-" + tag))
    outside = Path.home() / ("au-hold-outside-" + tag)
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    try:
        outside.mkdir()
        (outside / "keep.txt").write_text("keep outside")
        (root / "app").mkdir()
        (root / "app" / "chat.txt").write_text("data")
        _winapi.CreateJunction(str(outside), str(root / "app" / "junction"))
        with removal.folder(root) as owned:
            api = owned._api
            parent = api.open(owned._handle, "app", enter=True)
            try:
                for name in ("chat.txt", "junction"):
                    held = api.open(parent, name, enter=False)
                    try:
                        # Another program cannot move the entry out now ...
                        with pytest.raises(OSError):
                            os.rename(root / "app" / name, outside / ("moved-" + name))
                        assert sorted(os.listdir(outside)) == ["keep.txt"]
                        # ... and the walk deletes it where it is.
                        api.delete(held)
                    finally:
                        api.close(held)
                    assert not os.path.lexists(root / "app" / name)
            finally:
                api.close(parent)
        assert sorted(os.listdir(outside)) == ["keep.txt"] and (outside / "keep.txt").read_text() == "keep outside"
        # Without the walk's handle the same move works: the refusal came from the handle.
        (root / "app" / "free.txt").write_text("x")
        os.rename(root / "app" / "free.txt", outside / "moved-free.txt")
        assert (outside / "moved-free.txt").read_text() == "x"
        print("UNINSTALL_HOLD a held file and a held junction could not be moved out; both were deleted in place")
    finally:
        link = root / "app" / "junction"
        if os.path.lexists(link):
            os.rmdir(link)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
