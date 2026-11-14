"""Portable ownership/composition controls; actual COM/HKCU checks are separate."""
from contextlib import contextmanager
import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web import windows_consumer_shell as shell


@pytest.fixture
def model(tmp_path, monkeypatch):
    from amplifier_web import windows_files, deployment
    root = tmp_path / "install"
    root.mkdir()
    programs = tmp_path / "Programs"
    programs.mkdir()
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    state = {"registry": None, "links": {}, "calls": [], "pins": set()}
    monkeypatch.setattr(windows_files, "address", Path)
    monkeypatch.setattr(shell, "programs", lambda: programs)
    monkeypatch.setattr(shell, "SETTLE_SECONDS", 0)  # One attempt; the wait has its own test.
    @contextmanager
    def directory(path, **kwargs):
        if kwargs.get("create"):
            path.mkdir(parents=True, exist_ok=True)
        elif not path.is_dir():
            raise FileNotFoundError(path)
        def read(name, limit):
            raw = (path / name).read_bytes()
            assert len(raw) <= limit
            return raw
        @contextmanager
        def removal(name, digest):
            if state.get("before_pin"):
                state["before_pin"](path, name)
            if hashlib.sha256(read(name, 65536)).hexdigest() != digest:
                raise ValueError("Owned private file changed; removal refused.")
            state["pins"].add(name)
            try:
                def remove():
                    assert name in state["pins"]
                    if state.get("link_failure") == name:
                        raise OSError("The shortcut could not be deleted")
                    state["calls"].append(("remove", name))
                    (path / name).unlink()
                yield remove
            finally:
                state["pins"].remove(name)
        yield SimpleNamespace(read_private=read, removal_private=removal)
    monkeypatch.setattr(windows_files, "directory", directory)
    def write_private(path, raw, **kw):
        if state.get("refuse_write") and state["refuse_write"](path):
            raise PermissionError(13, "The private write was refused", str(path))
        path.parent.mkdir(parents=True, exist_ok=True)  # The real writer creates private folders.
        path.write_text(raw)
    monkeypatch.setattr(deployment, "write_private", write_private)
    @contextmanager
    def link():
        def create(path, python, args, cwd):
            state["links"][str(path)] = (str(python), args)
            path.write_bytes(json.dumps({"python": str(python), "args": args, "cwd": str(cwd)}).encode())
        yield SimpleNamespace(create=create, read=lambda path: state["links"][str(path)])
    monkeypatch.setattr(shell, "shell_link", link)
    def registry(root, values=None, *, remove=False):
        if remove:
            assert values == state["registry"]
            state["calls"].append(("registry-remove",))
            state["registry"] = None
        elif values is not None:
            assert state["registry"] is None
            state["registry"] = values
        return state["registry"]
    monkeypatch.setattr(shell, "registry", registry)
    @contextmanager
    def registry_change(root, values, *, remove=False):
        # Every shortcut that still exists is pinned before the registry is staged.
        assert remove and state["pins"] == {name for name in shell.LINKS if (folder / name).exists()}
        if state["registry"] is None:
            raise FileNotFoundError(2, "The installed-app entry is absent")
        if state["registry"] != values:
            raise ValueError("Registry changed")
        def commit():
            if state.get("commit_conflict"):
                raise OSError("Registry transaction conflicted with a later edit")
            registry(root, values, remove=True)
        yield commit
    monkeypatch.setattr(shell, "registry_change", registry_change)
    return root, programs, state


@pytest.fixture
def uninstaller(model, monkeypatch):
    """The public uninstall over the model, with a recording Task Scheduler step."""
    from amplifier_web import installation_health, windows_consumer as consumer
    from amplifier_web import windows_installation, windows_service
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    state.update(notices=[], answers=[], working=None, task="running", task_failure=None)
    monkeypatch.setattr(consumer, "installation", lambda value: (value, {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    def notice(text, confirm=False):
        state["notices"].append((text, confirm))
        return state["answers"].pop(0) if confirm and state["answers"] else True
    monkeypatch.setattr(windows_installation, "owner_notice", notice)
    async def working(home):
        assert home == root / "app"
        return state["working"]
    monkeypatch.setattr(installation_health, "windows_working", working)
    def uninstall(home, *, progress):
        assert home == root / "app"
        # The app stops while every existing entry is still pinned and present.
        assert state["pins"] == {name for name in shell.LINKS if (folder / name).exists()}
        for step in ("task-read", "stop", "task-remove"):
            progress(step)
            if state["task"] == "absent":
                return
            if state["task_failure"] == step:
                raise windows_service.WindowsServiceError("synthetic-private-detail " + step)
            if step == "stop":
                state["task"] = "stopped"
                state["calls"].append(("stop",))
        state["task"] = "absent"
        state["calls"].append(("task-remove",))
    monkeypatch.setattr(windows_service, "uninstall", uninstall)
    run = lambda: asyncio.run(consumer.maintenance(root, "uninstall"))
    return SimpleNamespace(root=root, folder=folder, state=state, run=run, consumer=consumer)


def test_user_shell_entries_have_windowless_exact_root_and_normal_remove(model):
    root, programs, state = model
    result = shell.install(root, "1.0.0")
    assert result["shortcuts"] == 4 and result["installedAppEntry"]
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    assert set(p.name for p in folder.iterdir()) == set(shell.LINKS)
    for target, args in state["links"].values():
        assert target == str(root / "tools/amplifier-unified/Scripts/pythonw.exe")
        assert "amplifier_web.windows_consumer" in args and str(root) in args
        assert "powershell" not in args.lower() and "cmd.exe" not in args.lower()
    assert state["registry"]["InstallLocation"] == str(root)
    assert "--uninstall" in state["registry"]["UninstallString"]
    sentinel = root / "saved-user-data"
    sentinel.write_bytes(b"keep")
    removed = shell.remove(root)
    assert removed["userDataRetained"] and sentinel.read_bytes() == b"keep"
    # The emptied Start-menu folder goes too; nothing else in Programs does.
    assert state["registry"] is None and not folder.exists() and programs.is_dir()
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    assert state["calls"][0] == ("registry-remove",)
    # A completed removal can be asked again and changes nothing.
    before = list(state["calls"])
    assert shell.remove(root)["shortcutsRemoved"] and state["calls"] == before


def test_removed_folder_keeps_a_file_the_person_added(model):
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    (folder / "My own note.txt").write_text("mine")
    shell.remove(root)
    assert [path.name for path in folder.iterdir()] == ["My own note.txt"]


@pytest.mark.parametrize("change", ["bytes", "binding", "receipt", "null", "registry", "preparing"])
def test_modified_shell_entries_never_remove_anything(model, change):
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    name = next(iter(shell.LINKS))
    if change == "bytes": (folder / name).write_bytes(b"foreign")
    elif change == "binding": state["links"][str(folder / name)] = ("other.exe", "")
    elif change == "receipt":
        row = json.loads((root / shell.FILE).read_text())
        row["folder"] = str(programs / "foreign")
        (root / shell.FILE).write_text(json.dumps(row))
    elif change == "null":
        (root / shell.FILE).write_text("null")
    elif change == "preparing":
        row = json.loads((root / shell.FILE).read_text())
        row["status"] = "preparing"
        (root / shell.FILE).write_text(json.dumps(row))
    else:
        row = json.loads((root / shell.FILE).read_text())
        row["registry"]["UninstallString"] = "foreign program"
        (root / shell.FILE).write_text(json.dumps(row))
    with pytest.raises(ValueError): shell.remove(root)
    assert state["registry"] is not None and not state["calls"]
    assert len(list(folder.iterdir())) == 4


def test_existing_or_partial_registration_does_not_replay(model):
    root, programs, state = model
    shell.install(root, "1.0.0")
    with pytest.raises(ValueError, match="receipt"): shell.install(root, "1.0.0")
    assert not state["calls"]


@pytest.mark.parametrize("gone", [
    {"Amplifier Unified.lnk"}, set(shell.LINKS), {"registry"}, set(shell.LINKS) | {"registry"},
    {"folder", "registry"}])
def test_removal_finishes_what_an_interrupted_removal_left(model, gone):
    # The state after a removal that stopped part-way: some entries are gone and
    # the receipt still says "installed". The next removal takes the rest.
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    for name in gone & set(shell.LINKS):
        (folder / name).unlink()
    if "folder" in gone:
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
    if "registry" in gone:
        state["registry"] = None
    assert shell.remove(root)["installedAppEntryRemoved"]
    assert state["registry"] is None and not folder.exists() and not state["pins"]
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    removed = {name for kind, *rest in state["calls"] if kind == "remove" for name in rest}
    assert removed == set() if "folder" in gone else removed == set(shell.LINKS) - gone


def test_refused_receipt_write_after_removal_is_reported_not_raised(model):
    # The last step of removal is a private write into the installation folder.
    # Windows refuses it (sharing violation) while a conversation worker is alive.
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    state["refuse_write"] = lambda path: path.name == shell.FILE
    result = shell.remove(root)
    assert isinstance(result["receiptError"], PermissionError) and result["shortcutsRemoved"]
    assert state["registry"] is None and not folder.exists()
    assert json.loads((root / shell.FILE).read_text())["status"] == "installed"
    # The stale receipt does not block a later removal, which now records it.
    state["refuse_write"] = None
    assert "receiptError" not in shell.remove(root)
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"


@pytest.mark.parametrize("released_after,recorded", [(3, True), (None, False)])
def test_receipt_write_waits_for_a_closing_app_and_then_reports_instead_of_failing(
        model, monkeypatch, released_after, recorded):
    # The app's worker programs end a moment after its task reports that it
    # stopped. Until then Windows refuses the receipt; setup reads its status.
    root, programs, state = model
    shell.install(root, "1.0.0")
    folder = programs / ("Amplifier Unified - " + shell.identity(root))
    waits = []
    monkeypatch.setattr(shell, "SETTLE_SECONDS", 4)
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def refuse(path):
        # At every attempt the entries are already gone: the pins were released first.
        assert state["registry"] is None and not folder.exists() and not state["pins"]
        return path.name == shell.FILE and (released_after is None or len(waits) < released_after)
    state["refuse_write"] = refuse
    result = shell.remove(root)
    assert result["shortcutsRemoved"] and ("receiptError" not in result) is recorded
    assert json.loads((root / shell.FILE).read_text())["status"] == ("removed" if recorded else "installed")
    # Half a second between attempts; eight attempts and no sleep after the last one.
    assert waits == [0.5] * (released_after if recorded else 7)


def test_receipt_write_does_not_wait_for_a_failure_that_waiting_cannot_cure(model, monkeypatch):
    from amplifier_web import deployment
    root, programs, state = model
    shell.install(root, "1.0.0")
    waits = []
    monkeypatch.setattr(shell, "SETTLE_SECONDS", 4)
    monkeypatch.setattr(shell.time, "sleep", waits.append)
    def broken(path, raw, **kw):
        raise ValueError("private files require a bounded absolute local Windows drive path")
    monkeypatch.setattr(deployment, "write_private", broken)
    assert isinstance(shell.remove(root)["receiptError"], ValueError) and not waits


def saved_record(root):
    from amplifier_web import windows_consumer as consumer
    return root / "app/config/services" / consumer.RECORD


def test_public_uninstall_stops_the_app_before_it_removes_any_entry(uninstaller):
    state = uninstaller.state
    result = uninstaller.run()
    assert result == {"ok": True, "operation": "uninstall", "userDataRetained": True,
                      "installationFilesRetained": True}
    assert state["calls"][:3] == [("stop",), ("task-remove",), ("registry-remove",)]
    assert {call[1] for call in state["calls"][3:]} == set(shell.LINKS) and len(state["calls"]) == 7
    assert state["task"] == "absent" and state["registry"] is None and not uninstaller.folder.exists()
    assert not state["pins"] and not saved_record(uninstaller.root).exists()
    # One question. With a console attached the result is printed, not shown.
    assert [confirm for _, confirm in state["notices"]] == [True]
    assert str(uninstaller.root) in state["notices"][0][0]


@pytest.mark.parametrize("conflict", ["prechanged", "last-pin", "registry"])
def test_changed_entry_refuses_before_the_app_is_stopped(uninstaller, conflict):
    state, folder = uninstaller.state, uninstaller.folder
    last = list(shell.LINKS)[-1]
    if conflict == "prechanged":
        (folder / last).write_bytes(b"changed before uninstall")
    elif conflict == "last-pin":
        def cross(path, name):
            if name == last:
                assert len(state["pins"]) == len(shell.LINKS) - 1
                (path / name).write_bytes(b"changed during set preparation")
        state["before_pin"] = cross
    else:
        state["registry"] = {"foreign": "registration"}
    registration = state["registry"].copy()
    with pytest.raises(uninstaller.consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == "entries-check" and "nothing was changed" in stopped.value.notice
    assert not state["calls"] and state["task"] == "running" and state["registry"] == registration
    assert len(list(folder.iterdir())) == 4 and not state["pins"]


@pytest.mark.parametrize("step,stopped_app", [("task-read", False), ("stop", False), ("task-remove", True)])
def test_failed_stop_or_task_removal_leaves_every_entry_and_a_second_uninstall_works(
        uninstaller, step, stopped_app):
    state, folder, consumer = uninstaller.state, uninstaller.folder, uninstaller.consumer
    registration = state["registry"].copy()
    state["task_failure"] = step
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == step
    # The person keeps every entry: open, stop and uninstall still exist.
    assert state["registry"] == registration and {path.name for path in folder.iterdir()} == set(shell.LINKS)
    assert json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "installed"
    assert state["calls"] == ([("stop",)] if stopped_app else []) and not state["pins"]
    # Plain cause and next step; a short reference; never the exception text.
    notice = stopped.value.notice
    assert "uninstall again" in notice.lower() and f"Reference: {step}, WindowsServiceError" in notice
    assert "synthetic-private-detail" not in notice
    assert ("has stopped" in notice) is stopped_app
    record = json.loads(saved_record(uninstaller.root).read_text())
    assert (record["operation"], record["step"], record["errorType"]) == ("uninstall", step, "WindowsServiceError")
    assert record["where"] and record["schema"] == 1
    # The cause is gone: the same command now completes.
    state["task_failure"] = None
    assert uninstaller.run()["ok"]
    assert state["task"] == "absent" and state["registry"] is None and not folder.exists()


def test_registry_conflict_after_the_stop_keeps_all_entries_for_a_second_uninstall(uninstaller):
    state, folder, consumer = uninstaller.state, uninstaller.folder, uninstaller.consumer
    registration = state["registry"].copy()
    state["commit_conflict"] = True
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == "apps-entry" and "has stopped" in stopped.value.notice
    assert state["calls"] == [("stop",), ("task-remove",)] and state["task"] == "absent"
    assert state["registry"] == registration and len(list(folder.iterdir())) == 4
    state["commit_conflict"] = False
    assert uninstaller.run()["ok"]
    assert state["registry"] is None and not folder.exists()


def test_failed_shortcut_removal_is_finished_by_a_second_uninstall(uninstaller):
    state, folder, consumer = uninstaller.state, uninstaller.folder, uninstaller.consumer
    names = list(shell.LINKS)
    state["link_failure"] = names[1]
    with pytest.raises(consumer.Unfinished) as stopped:
        uninstaller.run()
    assert stopped.value.step == "shortcuts"
    # The uninstall shortcut is removed last, so it is still there to finish with.
    assert names[-1] == "Uninstall Amplifier.lnk" and (folder / names[-1]).exists()
    assert state["registry"] is None and state["task"] == "absent"
    assert json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "installed"
    state["link_failure"] = None
    assert uninstaller.run()["ok"]
    assert not folder.exists() and json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "removed"


def test_app_left_running_without_entries_by_the_earlier_order_can_be_uninstalled(uninstaller):
    # What clean-room runs 2 and 3 left behind: no entry, receipt "installed", app running.
    state, folder = uninstaller.state, uninstaller.folder
    for path in folder.iterdir():
        path.unlink()
    state["registry"] = None
    assert uninstaller.run()["ok"]
    assert state["calls"] == [("stop",), ("task-remove",)] and state["task"] == "absent"
    assert json.loads((uninstaller.root / shell.FILE).read_text())["status"] == "removed"


def test_folder_that_refuses_writes_while_the_app_runs_no_longer_strands_it(uninstaller):
    # Clean-room run 3: while a conversation worker lives, Windows refuses a new
    # or replaced file directly in the installation folder. The earlier order
    # met that refusal after it had removed every entry. Workers end with the app.
    state, root = uninstaller.state, uninstaller.root
    state["refuse_write"] = lambda path: path.parent == root and state["task"] == "running"
    assert uninstaller.run()["ok"]
    assert state["calls"][:3] == [("stop",), ("task-remove",), ("registry-remove",)]
    assert state["task"] == "absent" and state["registry"] is None and not uninstaller.folder.exists()
    assert json.loads((root / shell.FILE).read_text())["status"] == "removed"
    assert not saved_record(root).exists()


def test_failed_stop_is_recorded_although_the_folder_refuses_writes(uninstaller):
    state, root, consumer = uninstaller.state, uninstaller.root, uninstaller.consumer
    registration = state["registry"].copy()
    state["refuse_write"] = lambda path: path.parent == root and state["task"] == "running"
    state["task_failure"] = "stop"
    with pytest.raises(consumer.Unfinished):
        uninstaller.run()
    assert state["task"] == "running" and state["registry"] == registration
    assert len(list(uninstaller.folder.iterdir())) == 4
    # The note is beside the service records, which accept writes while the app runs.
    assert json.loads(saved_record(root).read_text())["step"] == "stop"
    assert not (root / consumer.RECORD).exists()


def test_refused_receipt_write_does_not_fail_a_finished_uninstall(uninstaller, monkeypatch):
    state, root, consumer = uninstaller.state, uninstaller.root, uninstaller.consumer
    state["refuse_write"] = lambda path: path.parent == root
    monkeypatch.setattr(consumer.sys, "stdout", None)
    assert uninstaller.run()["ok"]
    assert state["task"] == "absent" and state["registry"] is None and not uninstaller.folder.exists()
    record = json.loads(saved_record(root).read_text())
    assert (record["step"], record["errorType"]) == ("receipt", "PermissionError")
    assert not (root / consumer.RECORD).exists()
    # Windowless: one question, then exactly one confirmation that names the folder.
    assert [confirm for _, confirm in state["notices"]] == [True, False]
    assert "Amplifier is uninstalled" in state["notices"][1][0] and str(root) in state["notices"][1][0]


@pytest.mark.parametrize("working,answer", [(1, False), (3, False), (2, True), (0, None), (None, None)])
def test_running_chat_is_named_and_the_person_chooses_to_wait_or_stop(uninstaller, working, answer):
    state = uninstaller.state
    state["working"] = working
    state["answers"] = [True] + ([answer] if answer is not None else [])
    result = uninstaller.run()
    questions = [text for text, confirm in state["notices"] if confirm]
    if answer is None:
        assert len(questions) == 1 and result.get("operation") == "uninstall"
        return
    assert len(questions) == 2
    assert ("working on a chat right now" if working == 1 else f"working on {working} chats") in questions[1]
    assert "Select No to keep Amplifier installed" in questions[1]
    if answer:
        assert result["operation"] == "uninstall" and state["task"] == "absent"
    else:
        assert result == {"ok": True, "cancelled": True}
        assert not state["calls"] and state["task"] == "running" and state["registry"] is not None
        assert len(list(uninstaller.folder.iterdir())) == 4


def test_declined_first_question_changes_nothing(uninstaller):
    state = uninstaller.state
    state["answers"], state["working"] = [False], 5
    assert uninstaller.run() == {"ok": True, "cancelled": True}
    assert not state["calls"] and len(state["notices"]) == 1 and state["registry"] is not None


@pytest.mark.parametrize("windowless", [True, False])
def test_unfinished_uninstall_is_one_plain_notice_or_one_json_line(uninstaller, monkeypatch, capsys, windowless):
    consumer, state = uninstaller.consumer, uninstaller.state
    state["task_failure"] = "stop"
    if windowless:
        monkeypatch.setattr(consumer.sys, "stdout", None)
    with pytest.raises(SystemExit) as stopped:
        consumer.main(["--root", str(uninstaller.root), "--uninstall"])
    assert stopped.value.code == 1
    shown = [text for text, confirm in state["notices"] if not confirm]
    if windowless:
        assert len(shown) == 1 and "it did not stop" in shown[0] and "Reference: stop, WindowsServiceError" in shown[0]
        assert "synthetic-private-detail" not in shown[0]
    else:
        assert not shown
        assert json.loads(capsys.readouterr().out) == {"ok": False, "step": "stop",
                                                        "errorType": "WindowsServiceError"}


def test_every_step_that_can_fail_has_its_own_plain_text():
    from amplifier_web import windows_consumer as consumer
    for step in ("entries-check", "task-read", "stop", "task-remove", "apps-entry", "shortcuts"):
        notice = consumer.Unfinished(step, OSError(5, "private detail")).notice
        assert "uninstall again" in notice.lower() and f"Reference: {step}, OSError" in notice
        assert "private detail" not in notice and "evidence" not in notice
    # A Windows error code is part of the reference; the error text never is.
    refused = OSError("private detail")
    refused.winerror = 32
    assert consumer.Unfinished("shortcuts", refused).notice.endswith("Reference: shortcuts, OSError 32")


def test_failure_record_tries_the_service_folder_first_and_never_raises(tmp_path, monkeypatch):
    from amplifier_web import deployment, windows_consumer as consumer
    written = []
    def refuse(path, raw, **kw):
        written.append(path)
        raise PermissionError(5, "Access is denied", str(path))
    monkeypatch.setattr(deployment, "write_private", refuse)
    try:
        raise OSError(32, "used by another process", "C:\\secret\\place")
    except OSError as error:
        assert consumer._record(tmp_path, "uninstall", "receipt", error) is False
    assert written == [tmp_path / "app/config/services" / consumer.RECORD, tmp_path / consumer.RECORD]


def _seed_native_entries(root, folder):
    """The state that setup's install() leaves, also valid for an elevated runner.

    install() creates each shortcut file with an ordinary open. An elevated
    token gives such a file to the Administrators group, and the private-file
    boundary then refuses it; setup itself refuses an elevated account. So each
    real shortcut is saved by ShellLink COM in the throwaway folder and then
    published privately, byte for byte, into the real Start-menu folder.
    """
    from amplifier_web import windows_files
    from amplifier_web.deployment import write_private
    python, links = shell.entries(root)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"MZ")  # A shortcut target must exist. Nothing starts it.
    values = shell.registration_values(root, "1.0.0")
    row = {"schema": 1, "root": str(root), "folder": str(folder), "status": "installed",
           "links": {}, "registry": values}
    with windows_files.directory(folder, owned_root=folder, create=True) as owned:
        for name, args in links.items():
            scratch = root / ("scratch-" + name)
            with shell.shell_link() as link:
                link.create(scratch, python, args, root / "workspace")
            content = scratch.read_bytes()
            scratch.unlink()
            owned.publish_private(name, content)
            with shell.shell_link() as link:
                assert link.read(folder / name) == (str(python), args)
            row["links"][name] = hashlib.sha256(content).hexdigest()
    shell.registry(root, values)
    write_private(root / shell.FILE, json.dumps(row), owned_root=root)
    # A real installation has this folder before its app first runs.
    with windows_files.directory(root / "app" / "config" / "services", owned_root=root, create=True):
        pass
    return shell.registry(root)


@pytest.mark.skipif(os.name != "nt", reason="Real Start-menu shortcuts, HKCU transaction and private NTFS files")
def test_native_uninstall_order_failure_and_second_uninstall(monkeypatch):
    """The real Windows entries of a throwaway installation folder.

    Real: ShellLink COM, the per-user Start-menu folder, the HKCU registry
    transaction, pinned private files and NTFS sharing. Stand-ins: Task
    Scheduler (a runner cannot register the sign-in task), the dialogs and the
    installation receipt check. No app is started.

    While the stand-in app "runs", the test holds the installation folder open
    for listing without write sharing. That kind of handle gives the refusal
    that the clean room measured with a live conversation worker (a rename into
    the folder fails with WinError 32). Which handle a real worker holds is not
    known; this is the same refusal, not the same owner. The stand-in releases
    the folder about one second after its stop reports, because real worker
    programs end a moment after the task reports that it stopped.
    """
    import ctypes
    import shutil
    import threading
    import winreg
    from amplifier_web import installation_health, windows_consumer as consumer
    from amplifier_web import windows_files, windows_installation, windows_service
    from amplifier_web.deployment import write_private
    root = windows_files.address(Path.home() / ("au-uninstall-test-" + uuid.uuid4().hex[:12]))
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    folder = shell.programs() / ("Amplifier Unified - " + shell.identity(root))
    key = shell.REGISTRY + shell.identity(root)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                   ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    held, events, failure, present, timers = [], [], [], set(shell.LINKS), []

    def hold():
        # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_SHARE_READ only,
        # OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
        handle = kernel.CreateFileW(str(root), 0x100081, 1, None, 3, 0x2200000, None)
        assert handle and handle != ctypes.c_void_p(-1).value, ctypes.WinError(ctypes.get_last_error())
        held.append(handle)

    def release():
        while held:
            assert kernel.CloseHandle(held.pop())

    monkeypatch.setattr(consumer, "installation", lambda value: (windows_files.address(value), {}))
    monkeypatch.setattr(consumer, "environment", lambda *a: {})
    monkeypatch.setattr(windows_installation, "owner_notice", lambda text, confirm=False: True)
    async def working(home):
        return None
    monkeypatch.setattr(installation_health, "windows_working", working)
    def uninstall(home, *, progress):
        progress("task-read")
        progress("stop")
        # While the app stops, each remaining shortcut exists and real NTFS
        # sharing refuses another program that tries to change it.
        names = {path.name for path in folder.iterdir()} if folder.exists() else set()
        assert names == present
        if names:
            with pytest.raises(OSError):
                (folder / "Uninstall Amplifier.lnk").open("wb")
        if failure:
            raise windows_service.WindowsServiceError(failure[0])
        # A normal stop ends the app; its worker programs end a moment later.
        timers.append(threading.Timer(1.0, release))
        timers[-1].start()
        events.append("task")
    monkeypatch.setattr(windows_service, "uninstall", uninstall)
    try:
        registration = _seed_native_entries(root, folder)
        assert registration and {path.name for path in folder.iterdir()} == set(shell.LINKS)
        with windows_files.directory(root, owned_root=root) as owned:
            receipt = owned.read_private(shell.FILE, limit=65536)
        # 1. The "app runs": the folder refuses a replaced file, as in the clean room.
        hold()
        with pytest.raises(PermissionError) as refused:
            write_private(root / shell.FILE, receipt, owned_root=root)
        assert refused.value.winerror == 32
        # 2. The stop fails: nothing is removed, and the step is recorded privately
        #    beside the service records although the folder refuses writes.
        failure.append("Task still has a running instance; no force-stop attempted.")
        with pytest.raises(consumer.Unfinished) as stopped:
            asyncio.run(consumer.maintenance(root, "uninstall"))
        assert stopped.value.step == "stop" and not events
        assert "it did not stop" in stopped.value.notice and "Task still has" not in stopped.value.notice
        assert shell.registry(root) == registration
        assert {path.name for path in folder.iterdir()} == set(shell.LINKS)
        with windows_files.directory(root / "app" / "config" / "services", owned_root=root) as owned:
            record = json.loads(owned.read_private(consumer.RECORD, limit=65536))
        assert (record["step"], record["errorType"]) == ("stop", "WindowsServiceError")
        with windows_files.directory(root, owned_root=root) as owned:
            assert owned.read_private(shell.FILE, limit=65536) == receipt
        # 3. An interrupted removal took one shortcut and the Apps-list entry away.
        with windows_files.directory(folder, owned_root=folder) as owned:
            name = "Amplifier status.lnk"
            with owned.removal_private(name, hashlib.sha256(owned.read_private(name, limit=65536)).hexdigest()) as drop:
                drop()
        present.discard(name)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        # 4. The second uninstall completes: the stop first, then the remaining
        #    entries and the emptied Start-menu folder. The receipt is written
        #    although the folder is still held when the stop reports: the last
        #    step waits for the closing programs.
        failure.clear()
        result = asyncio.run(consumer.maintenance(root, "uninstall"))
        assert result["ok"] and events == ["task"] and not held
        assert shell.registry(root) is None and not folder.exists()
        with windows_files.directory(root, owned_root=root) as owned:
            assert json.loads(owned.read_private(shell.FILE, limit=65536))["status"] == "removed"
        with windows_files.directory(root / "app" / "config" / "services", owned_root=root) as owned:
            # No new note: the wait succeeded, so the earlier "stop" note is still the only one.
            assert json.loads(owned.read_private(consumer.RECORD, limit=65536))["step"] == "stop"
        # 5. Asking again changes nothing and is not an error.
        present.clear()
        assert asyncio.run(consumer.maintenance(root, "uninstall"))["ok"] and events == ["task", "task"]
    finally:
        # Only this test's own throwaway objects, by their exact names.
        for timer in timers:
            timer.cancel()
            timer.join()
        release()
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
