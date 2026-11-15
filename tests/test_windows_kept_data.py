"""Kept chats and settings of a removed Windows installation.

Every case checks the same promise with the bytes of every file: nothing is
lost, nothing is merged, and nothing of the person's is deleted. The portable
cases use plain folders (the module's stand-in for the private-folder
boundary); they run on every system, also on NTFS. The native cases at the end
use the real boundary, another program's current folder, a held file and a
junction. `shutil.rmtree` stands in for the caller's link-safe deleter.
"""
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web import windows_kept_data as kept

T1, T2 = "1" * 32, "2" * 32
VERSION = "0.96.1"
SETUP = ("app", "shared", "ownership", "capture", "import", "workspace")  # Setup creates these, empty.
FENCE = {"phase": "activating", "pendingRestart": {"version": "0.97.0"}}  # An update that did not finish.


class Crash(BaseException):
    """The program ends here: the PC loses power, or the person signs out."""


@pytest.fixture(autouse=True)
def plain_folders(monkeypatch):
    monkeypatch.setattr(kept, "_NATIVE", False)
    monkeypatch.setattr(kept, "WAIT", 0)  # One attempt; the wait has its own test.
    yield
    while kept._STAYING:  # A lock that a launcher keeps until it ends.
        kept._STAYING.pop().release()


@pytest.fixture
def notices(monkeypatch):
    """The person: every message in order, and the answers to the questions."""
    from amplifier_web import windows_installation
    box = SimpleNamespace(shown=[], answers=[])

    def owner_notice(text, *, confirm=False):
        box.shown.append((confirm, text))
        return box.answers.pop(0) if confirm else True
    monkeypatch.setattr(windows_installation, "owner_notice", owner_notice)
    return box


def tree(folder):
    """Every entry below a folder with the bytes of each file. A link is named, never entered."""
    found = {}

    def walk(path, prefix):
        for name in sorted(os.listdir(path)):
            kind = kept._kind(path / name)
            found[prefix + name] = (hashlib.sha256((path / name).read_bytes()).hexdigest()
                                    if kind == "file" else kind)
            if kind == "folder":
                walk(path / name, prefix + name + "/")
    if kept._kind(folder) == "folder":
        walk(Path(folder), "")
    return found


def data(folder):
    """The data folders in an installation folder, or in its kept folder."""
    return {key: value for key, value in tree(folder).items() if key.split("/")[0] in kept.FOLDERS}


def files(entries):
    return {key: value for key, value in entries.items() if value not in ("folder", "link")}


def person(entries):
    """The person's own data: without the program state, and without the database that a take-back settles."""
    drop = ("app/updates", "app/runtime", "app/reset-backups", "app/app.sqlite3")
    return {key: value for key, value in entries.items() if not key.startswith(drop)}


def link(target, path):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(path))
    else:
        os.symlink(target, path, target_is_directory=True)


def database(home):
    with closing(sqlite3.connect(home / "app.sqlite3")) as db, db:
        db.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE state_records (kind TEXT, id TEXT, value TEXT)")
        db.execute("INSERT INTO state VALUES (1, ?)", (json.dumps({"sessions": ["first"], "updates": FENCE}),))
        db.executemany("INSERT INTO state_records VALUES (?, ?, ?)",
                       [("global", "updates", json.dumps(FENCE)), ("session", "first", "{}")])


def saved_state(home):
    with closing(sqlite3.connect(home / "app.sqlite3")) as db:
        return (json.loads(db.execute("SELECT value FROM state WHERE id=1").fetchone()[0]),
                sorted(db.execute("SELECT kind, id FROM state_records")))


def used(root, chat="first"):
    """An installation that ran: a chat, settings, a stored key, a database, program state, program files."""
    for name in (*SETUP, "export", "tools"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for path, text in {
            "app/config/server.yaml": "port: 51234\n", f"app/sessions/{chat}/transcript.jsonl": f"chat {chat}\n",
            "shared/keys.env": "KEY=secret\n", "shared/settings.yaml": "bundle: mine\n",
            "ownership/owner.json": '{"pid": 4242}', "workspace/notes.txt": "mine\n",
            "workspace/project/readme.md": "project\n", "export/out.md": "exported\n",
            "app/updates/application-active.json": json.dumps({"schema": 1, "version": "0.97.0"}),
            "app/updates/a/generation/validated.json": "{}", "app/runtime/q-1/pyproject.toml": "",
            "tools/program.txt": "program"}.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    database(root / "app")
    return root


def fresh(root):
    """The folders that setup leaves before the first start."""
    for name in (*SETUP, "tools"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def kept_then_installed(base, version=VERSION):
    """Uninstall with "keep", then a later setup run into the same folder."""
    root = used(base / "Amplifier")
    expected = person(data(root))
    result = kept.move_out(root, version=version, transfer=T1, remove=shutil.rmtree, wait=0)
    assert result["state"] == "complete" and "notice" not in result
    shutil.rmtree(root)  # The rest of the uninstall removes the program files and the folder.
    return fresh(root), kept.kept_folder(root), expected


def record(folder, **changes):
    """A kept folder with a hand-made record."""
    folder.mkdir(parents=True, exist_ok=True)
    value = {"schema": 1, "root": str(folder.with_name(folder.name.removesuffix("-kept"))), "version": VERSION,
             "keptAt": "2026-11-14T20:00:00Z", "state": "complete", "transfer": T1, **changes}
    (folder / kept.RECORD).write_text(json.dumps(value))
    return value


def crash_after(monkeypatch, function, count):
    real, calls = getattr(os, function), [0]

    def crashing(*args, **kwargs):
        if calls[0] >= count:
            raise Crash()
        calls[0] += 1
        return real(*args, **kwargs)
    monkeypatch.setattr(os, function, crashing)
    return lambda: monkeypatch.setattr(os, function, real)


def refuse(monkeypatch, function, *paths):
    """Windows refuses this for these paths: a program's current folder, or a file that a program holds."""
    real = getattr(os, function)

    def refusing(path, *args, **kwargs):
        if Path(path) in paths:
            raise PermissionError(13, "The process cannot access the file", str(path))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(os, function, refusing)
    return lambda: monkeypatch.setattr(os, function, real)


# Move-out


def test_move_out_moves_every_data_folder_and_changes_no_byte(tmp_path):
    root = used(tmp_path / "Amplifier")
    before, folder = data(root), kept.kept_folder(root)
    assert kept.used_version(root, VERSION) == "0.97.0"  # The active app update is newer than the launcher.
    kept.check(root, "0.97.0")
    result = kept.move_out(root, version="0.97.0", transfer=T1, remove=shutil.rmtree, wait=0)
    assert result == {"kept": str(folder), "state": "complete", "transfer": T1, "left": [],
                      "remaining": [], "programState": [], "stayed": []}
    state, saved = kept.read(folder)
    assert state == "complete" and saved["root"] == str(root) and saved["version"] == "0.97.0"
    assert saved["transfer"] == T1 and set(saved) == kept._FIELDS
    assert set(os.listdir(folder)) == {*kept.FOLDERS, kept.RECORD}
    assert data(root) == {} and tree(root) == {"tools": "folder", "tools/program.txt": tree(root)["tools/program.txt"]}
    moved = data(folder)
    assert not any(key.startswith(("app/updates", "app/runtime")) for key in moved)
    # Every other byte, also the database with its unfinished update, is exactly as it was.
    assert moved == {key: value for key, value in before.items()
                     if not key.startswith(("app/updates", "app/runtime"))}


def test_used_version_is_the_newest_app_that_used_the_data(tmp_path):
    root = fresh(tmp_path / "Amplifier")
    assert kept.used_version(root, VERSION) == VERSION
    pointer = root / "app/updates/application-active.json"
    pointer.parent.mkdir()
    pointer.write_text(json.dumps({"version": "0.9.9"}))
    assert kept.used_version(root, "0.10.0") == "0.10.0"  # Numbers, not text.
    for unreadable in ("{", "[]", json.dumps({"version": "next"}), json.dumps({"version": 3})):
        pointer.write_text(unreadable)
        with pytest.raises(ValueError):
            kept.used_version(root, VERSION)


def test_without_a_deleter_a_set_with_program_state_is_never_complete_and_never_offered(tmp_path, notices):
    root = used(tmp_path / "Amplifier")
    before, folder = data(root), kept.kept_folder(root)
    result = kept.move_out(root, version=VERSION, transfer=T1, wait=0)
    assert result["state"] == "moving" and result["programState"] == ["updates", "runtime"]
    assert str(folder) in result["notice"] and "Nothing was deleted" in result["notice"]
    assert data(folder) == before and kept.read(folder)[0] == "moving"
    shutil.rmtree(root)
    fresh(root)
    assert kept.first_start(root, VERSION, ask=True) is None
    assert [confirm for confirm, _ in notices.shown] == [False]
    assert str(folder) in notices.shown[0][1] and "did not finish" in notices.shown[0][1]
    assert data(folder) == before and data(root) == {name: "folder" for name in SETUP}
    # The same uninstall, now with its deleter, finishes the set.
    done = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert done["state"] == "complete" and person(data(folder)) == person(before)


def test_interrupted_move_out_loses_nothing_and_the_same_uninstall_finishes_it(tmp_path, monkeypatch):
    finished = False
    for count in range(12):
        root = used(tmp_path / str(count) / "Amplifier")
        before, folder = data(root), kept.kept_folder(root)
        restore = crash_after(monkeypatch, "rename", count)
        try:
            kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
            finished = True
        except Crash:
            pass
        restore()
        if finished:
            break
        assert kept.read(folder)[0] == "moving"
        here, there = files(data(root)), files(data(folder))
        assert not set(here) & set(there) and {**here, **there} == files(before)  # Each file is in one place.
        again = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
        assert again["state"] == "complete" and data(root) == {}
        assert person(data(folder)) == person(before)
    assert finished and count == len(kept.FOLDERS)  # One rename for each data folder.


def test_folder_that_windows_refuses_to_rename_is_moved_entry_by_entry(tmp_path, monkeypatch):
    root = used(tmp_path / "Amplifier")
    before, folder = data(root), kept.kept_folder(root)
    refuse(monkeypatch, "rename", root / "workspace")  # Another program's current folder.
    refuse(monkeypatch, "rmdir", root / "workspace")
    result = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert result["state"] == "complete" and result["remaining"] == []
    assert data(root) == {"workspace": "folder"}  # It stays, empty.
    assert person(data(folder)) == person(before)


def test_held_entry_stays_and_a_second_uninstall_finishes(tmp_path, monkeypatch):
    root = used(tmp_path / "Amplifier")
    before, folder = data(root), kept.kept_folder(root)
    release = refuse(monkeypatch, "rename", root / "app", root / "app" / "config")  # A closing program holds a file.
    removed = []
    result = kept.move_out(root, version=VERSION, transfer=T1, remove=removed.append, wait=0)
    assert result["state"] == "moving" and result["remaining"] == ["app"] and "notice" in result
    assert removed == []  # Nothing is deleted before everything is moved.
    assert files(data(root)) == {"app/config/server.yaml": before["app/config/server.yaml"]}
    assert {**files(data(root)), **files(data(folder))} == files(before)
    # Another uninstall's data never enters this unfinished set.
    snapshot = tree(root), tree(folder)
    for attempt in (lambda: kept.check(root, VERSION), lambda: kept.check(root, VERSION, T2),
                    lambda: kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)):
        with pytest.raises(kept.Refused) as refused:
            attempt()
        assert refused.value.kind == "kept-folder" and str(folder) in refused.value.notice
    assert (tree(root), tree(folder)) == snapshot
    release()
    kept.check(root, VERSION, T1)
    done = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert done["state"] == "complete" and data(root) == {} and person(data(folder)) == person(before)


def test_set_is_not_complete_while_any_folder_holds_an_entry(tmp_path, monkeypatch):
    root = used(tmp_path / "Amplifier")
    shutil.rmtree(root / "app" / "updates")  # An installation without program state.
    shutil.rmtree(root / "app" / "runtime")
    before, folder = data(root), kept.kept_folder(root)
    release = refuse(monkeypatch, "rename", root / "workspace", root / "workspace" / "notes.txt")
    result = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert result["state"] == "moving" and result["remaining"] == ["workspace"] and result["programState"] == []
    assert kept.read(folder)[0] == "moving" and (root / "workspace" / "notes.txt").read_text() == "mine\n"
    release()
    done = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert done["state"] == "complete" and data(folder) == before


def test_entry_never_replaces_an_entry_in_the_kept_folder(tmp_path, monkeypatch):
    root = used(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    release = refuse(monkeypatch, "rename", root / "app", root / "app" / "config")
    kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    release()
    other = root / "app/sessions/first/transcript.jsonl"  # The same name appears again in the installation.
    other.parent.mkdir(parents=True)
    other.write_text("another chat\n")
    result = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert result["state"] == "moving" and result["remaining"] == ["app"]
    assert other.read_text() == "another chat\n"
    assert (folder / "app/sessions/first/transcript.jsonl").read_text() == "chat first\n"
    assert (folder / "app/updates").is_dir()  # Still unfinished: nothing was deleted.


def test_link_or_file_with_a_data_folder_name_stays_and_is_reported(tmp_path):
    root = used(tmp_path / "Amplifier")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "theirs.txt").write_text("not Amplifier's\n")
    os.rmdir(root / "capture")
    link(outside, root / "capture")
    os.rmdir(root / "import")
    (root / "import").write_text("a file\n")
    result = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    folder = kept.kept_folder(root)
    assert result["state"] == "complete" and result["left"] == ["capture", "import"]
    assert kept._kind(root / "capture") == "link" and (root / "import").read_text() == "a file\n"
    assert os.listdir(outside) == ["theirs.txt"] and (outside / "theirs.txt").read_text() == "not Amplifier's\n"
    assert not {"capture", "import"} & set(os.listdir(folder))


def test_program_state_that_is_a_link_is_never_removed_and_the_set_stays_unfinished(tmp_path):
    root = used(tmp_path / "Amplifier")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "theirs.txt").write_text("not Amplifier's\n")
    shutil.rmtree(root / "app/updates")
    link(outside, root / "app/updates")
    removed = []
    result = kept.move_out(root, version=VERSION, transfer=T1,
                           remove=lambda path: (removed.append(path.name), shutil.rmtree(path)), wait=0)
    assert removed == ["runtime"] and result["state"] == "moving" and result["programState"] == ["updates"]
    assert (outside / "theirs.txt").read_text() == "not Amplifier's\n"


def test_earlier_complete_set_is_set_aside_whole_and_is_named_once(tmp_path, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    notices.answers.append(False)  # No: start fresh. The answer is saved with the set.
    assert kept.first_start(root, VERSION, ask=True) is None
    first = tree(folder)
    used(root, chat="second")  # The new installation is used, then removed with "keep" again.
    second = person(data(root))
    kept.check(root, VERSION)
    result = kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
    earlier = kept.earlier_folders(root)
    assert result["state"] == "complete" and tree(earlier[0]) == first  # Renamed whole, not one byte changed.
    assert person(data(folder)) == second and kept.read(folder)[1]["transfer"] == T2
    shutil.rmtree(root)
    fresh(root)
    notices.shown.clear()
    notices.answers.append(False)
    assert kept.first_start(root, VERSION, ask=True) is None
    assert [confirm for confirm, _ in notices.shown] == [True, False]
    assert str(earlier[0]) in notices.shown[1][1] and "earlier uninstall" in notices.shown[1][1]
    assert tree(earlier[0]) == first


def test_ninth_earlier_set_is_the_last_and_the_refusal_changes_nothing(tmp_path):
    root = used(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    record(folder)
    for earlier in kept.earlier_folders(root):
        earlier.mkdir()
    snapshot = tree(tmp_path)
    for attempt in (lambda: kept.check(root, VERSION),
                    lambda: kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)):
        with pytest.raises(kept.Refused) as refused:
            attempt()
        assert refused.value.kind == "kept-full" and "Nothing was deleted" in refused.value.notice
    assert tree(tmp_path) == snapshot
    os.rmdir(kept.earlier_folders(root)[4])
    kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
    assert kept.read(kept.earlier_folders(root)[4])[1]["transfer"] == T1  # The first free number.


@pytest.mark.parametrize("occupied", ["foreign", "unreadable", "other-folder", "other-transfer", "file", "link"])
def test_move_out_refuses_a_kept_folder_that_it_cannot_use_before_any_change(tmp_path, occupied):
    root = used(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    if occupied == "foreign":
        folder.mkdir()
        (folder / "photos.zip").write_text("the person's own file")
    elif occupied == "unreadable":
        folder.mkdir()
        (folder / kept.RECORD).write_text("{")
    elif occupied == "other-folder":
        record(folder, root=str(tmp_path / "Programs" / "Amplifier"))
    elif occupied == "other-transfer":
        record(folder, state="moving", transfer=T2)
    elif occupied == "file":
        folder.write_text("a file")
    else:
        (tmp_path / "elsewhere").mkdir()
        link(tmp_path / "elsewhere", folder)
    snapshot = tree(tmp_path)
    for attempt in (lambda: kept.check(root, VERSION, T1),
                    lambda: kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)):
        with pytest.raises(kept.Refused) as refused:
            attempt()
        assert refused.value.kind == "kept-folder"
        assert str(folder) in refused.value.notice and "Nothing was deleted" in refused.value.notice
        assert "Then uninstall again." in refused.value.notice
    assert tree(tmp_path) == snapshot


def test_empty_kept_folder_is_used(tmp_path):
    root = used(tmp_path / "Amplifier")
    kept.kept_folder(root).mkdir()
    assert kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)["state"] == "complete"


def test_uninstall_finishes_an_interrupted_take_back_before_its_own_move_out(tmp_path, monkeypatch, notices):
    root, folder, expected = kept_then_installed(tmp_path)
    notices.answers.append(True)
    restore = crash_after(monkeypatch, "rename", 2)
    with pytest.raises(Crash):
        kept.first_start(root, VERSION, ask=True)
    restore()
    assert kept.read(folder)[0] == "adopting" and data(root) and data(folder)
    kept.check(root, VERSION)
    result = kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
    assert result["state"] == "complete" and result["transfer"] == T2
    assert person(data(folder)) == expected and data(root) == {}  # One set holds everything again.
    assert not any(earlier.exists() for earlier in kept.earlier_folders(root))
    # The data left again at once, so its database was not touched; the take-back's note is gone.
    assert saved_state(folder / "app")[0]["updates"] == FENCE and not (folder / "app" / "reset-backups").exists()
    assert not (root / kept.MARKER).exists()


# First start of a later installation


def test_first_start_offers_once_and_yes_takes_everything_back(tmp_path, notices):
    root, folder, expected = kept_then_installed(tmp_path)
    notices.answers.append(True)
    assert kept.first_start(root, VERSION, ask=True) is None  # The app starts.
    (confirm, text), = notices.shown
    assert confirm and kept._date("2026-11-14T20:00:00Z")[-4:] in text and str(folder) in text
    assert "Yes: use them." in text and "Amplifier will never offer them again. They stay in this folder" in text
    assert person(data(root)) == expected and not folder.exists()
    assert not (root / kept.MARKER).exists() and not root.with_name("Amplifier-kept.lock").exists()
    # The unfinished update of the removed app no longer stops new messages; the chats in the database stay.
    state, rows = saved_state(root / "app")
    assert state == {"sessions": ["first"]} and rows == [("session", "first")]
    backup, = (root / "app" / "reset-backups").iterdir()
    assert backup.name.startswith(f"kept-{T1}-") and saved_state(backup)[0]["updates"] == FENCE
    notices.shown.clear()
    assert kept.first_start(root, VERSION, ask=True) is None and notices.shown == []


def test_take_back_leaves_no_database_open(tmp_path, monkeypatch):
    """Windows refuses to rename a folder while this program holds a file in it open.

    A `with` block of a database connection ends its transaction and leaves the
    files open. An uninstall that finishes a take-back and then moves the data
    out could not move `app`. Measured on a Windows runner.
    """
    root, folder, _ = kept_then_installed(tmp_path)
    opened, real = [], sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kw: (opened.append(real(*args, **kw)), opened[-1])[1])
    assert kept.take_back(root, VERSION)["updateState"] == "retired"
    assert len(opened) == 2  # The database and its copy.
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_no_is_saved_with_the_set_and_no_installation_asks_again(tmp_path, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    before = data(folder)
    notices.answers.append(False)
    assert kept.first_start(root, VERSION, ask=True) is None
    assert [confirm for confirm, _ in notices.shown] == [True]
    state, saved = kept.read(folder)
    assert state == "complete" and saved["declined"] is True and data(folder) == before
    assert data(root) == {name: "folder" for name in SETUP}
    # A second launcher, or a start that failed before it wrote anything: no question. The set is named.
    assert kept.first_start(root, VERSION, ask=True) is None
    assert [confirm for confirm, _ in notices.shown] == [True, False]
    assert str(folder) in notices.shown[1][1] and "You chose not to use them." in notices.shown[1][1]
    with pytest.raises(kept.Refused) as refused:
        kept.take_back(root, VERSION)
    assert refused.value.kind == "take-back-declined"
    # A later installation in the same folder does not ask either.
    shutil.rmtree(root)
    fresh(root)
    assert kept.first_start(root, VERSION, ask=True) is None
    assert [confirm for confirm, _ in notices.shown] == [True, False, False] and data(folder) == before
    # An installation that holds data starts without any message.
    (root / "app" / "config").mkdir()
    assert kept.first_start(root, VERSION, ask=True) is None and len(notices.shown) == 3


def test_caller_without_a_person_gets_a_result_and_no_question(tmp_path, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    before = tree(folder)
    result = kept.first_start(root, VERSION, ask=False)
    assert result["taken"] is False and result["reason"] == "question" and str(folder) in result["message"]
    assert notices.shown == [] and tree(folder) == before and data(root) == {name: "folder" for name in SETUP}


def test_used_installation_starts_without_a_question(tmp_path, notices):
    root = used(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    record(folder)
    (folder / "app").mkdir()
    (folder / "app" / "chat.txt").write_text("kept")
    snapshot = tree(tmp_path)
    assert kept.first_start(root, VERSION, ask=True) is None
    assert notices.shown == [] and tree(tmp_path) == snapshot


@pytest.mark.parametrize("why,launcher,text", [
    ("other-folder", VERSION, "another folder"), ("newer", VERSION, "newer version"),
    ("newer-by-number", "0.99.9", "newer version"), ("moving", VERSION, "did not finish"),
    ("link", VERSION, "holds a link"), ("program-state", VERSION, "was changed after")])
def test_set_that_cannot_be_used_is_not_offered_and_is_named_once(tmp_path, notices, why, launcher, text):
    root, folder, _ = kept_then_installed(tmp_path)
    if why == "other-folder":
        record(folder, root=str(tmp_path / "Programs" / "Amplifier"))
    elif why == "newer":
        record(folder, version="0.97.0")
    elif why == "newer-by-number":
        record(folder, version="0.100.0")
    elif why == "moving":
        record(folder, state="moving")
    elif why == "link":
        (tmp_path / "elsewhere").mkdir()
        link(tmp_path / "elsewhere", folder / "linked")
    else:
        (folder / "app" / "updates").mkdir()
    before = tree(folder)
    assert kept.first_start(root, launcher, ask=True) is None
    (confirm, shown), = notices.shown
    assert not confirm and str(folder) in shown and text in shown and "They stay where they are." in shown
    assert tree(folder) == before and data(root) == {name: "folder" for name in SETUP}
    with pytest.raises(kept.Refused):  # The take-back itself runs every check too.
        kept.take_back(root, launcher)
    assert tree(folder) == before and data(root) == {name: "folder" for name in SETUP}
    (root / "app" / "config").mkdir()
    assert kept.first_start(root, launcher, ask=True) is None and len(notices.shown) == 1  # Named once.


@pytest.mark.parametrize("saved,launcher", [(VERSION, VERSION), ("0.9.9", "0.10.0"), ("0.96.1", "1.0.0")])
def test_set_of_the_same_or_an_older_version_is_offered(tmp_path, notices, saved, launcher):
    root, folder, expected = kept_then_installed(tmp_path, version=saved)
    notices.answers.append(True)
    assert kept.first_start(root, launcher, ask=True) is None
    assert notices.shown[0][0] and person(data(root)) == expected


@pytest.mark.parametrize("damage", ["json", "missing", "extra", "state", "version", "transfer", "date", "folder",
                                    "deep"])
@pytest.mark.parametrize("answer", [True, False])
def test_record_that_cannot_be_read_is_never_no_kept_data(tmp_path, notices, damage, answer):
    root, folder, _ = kept_then_installed(tmp_path)
    good = kept.read(folder)[1]
    path = folder / kept.RECORD
    if damage == "folder":
        os.remove(path)
        path.mkdir()
    else:
        path.write_text({"json": "{", "missing": json.dumps({k: v for k, v in good.items() if k != "transfer"}),
                         "extra": json.dumps({**good, "more": 1}), "state": json.dumps({**good, "state": "done"}),
                         "version": json.dumps({**good, "version": "next"}),
                         "transfer": json.dumps({**good, "transfer": "x"}),
                         "date": json.dumps({**good, "keptAt": "2026-13-45T99:99:99Z"}),
                         "deep": "[" * 100000}[damage])
    before = tree(folder)
    assert kept.read(folder) == ("unreadable", None)
    notices.answers.append(answer)
    result = kept.first_start(root, VERSION, ask=True)
    (confirm, text), = notices.shown
    assert confirm and str(folder) in text and "cannot read them" in text and "Start Amplifier without them?" in text
    assert (result is None) if answer else (result["taken"] is False and result["reason"] == "unreadable")
    assert tree(folder) == before and data(root) == {name: "folder" for name in SETUP}
    silent = kept.first_start(root, VERSION, ask=False)
    assert silent["reason"] == "unreadable" and len(notices.shown) == 1


def test_take_back_never_merges_into_a_folder_that_holds_something(tmp_path):
    root, folder, _ = kept_then_installed(tmp_path)
    (root / "workspace" / "new.txt").write_text("written by the new installation")  # Late in the order.
    snapshot = tree(tmp_path)
    with pytest.raises(kept.Refused) as refused:
        kept.take_back(root, VERSION)
    assert refused.value.kind == "take-back-not-empty"
    assert str(root / "workspace") in refused.value.notice and "in the way" in refused.value.notice
    assert "Nothing was deleted" in refused.value.notice
    assert tree(tmp_path) == snapshot and kept.read(folder)[0] == "complete"  # Not one folder moved.


@pytest.mark.parametrize("function", ["rename", "rmdir"])
@pytest.mark.parametrize("current", [False, True])
def test_interrupted_take_back_is_finished_before_the_app_starts(tmp_path, monkeypatch, notices, function, current):
    finished = False
    for count in range(20):
        root, folder, expected = kept_then_installed(tmp_path / str(count))
        if current:  # The launcher's current folder: Windows refuses to remove it, so it is filled.
            refuse(monkeypatch, "rmdir", root / "workspace")
        notices.shown.clear()
        notices.answers.append(True)
        restore = crash_after(monkeypatch, function, count)
        try:
            assert kept.first_start(root, VERSION, ask=True) is None
            finished = True
        except Crash:
            pass
        restore()
        if not finished:
            assert kept.read(folder)[0] in ("adopting", "none")  # `none`: only the empty folder was left.
            here, there = files(person(data(root))), files(person(data(folder)))
            assert not set(here) & set(there) and {**here, **there} == files(expected)
            # No second question: the person already said Yes. A caller without a person finishes it too.
            assert kept.first_start(root, VERSION, ask=False) is None
        assert [confirm for confirm, _ in notices.shown] == [True]
        assert person(data(root)) == expected and "updates" not in saved_state(root / "app")[0]
        assert kept.read(folder)[0] in ("absent", "none") and data(folder) == {}
        if finished:
            break
    assert finished and count >= len(SETUP)


@pytest.mark.parametrize("released", [True, False])
def test_take_back_waits_for_a_held_folder_and_else_stops_until_the_next_start(
        tmp_path, monkeypatch, notices, released):
    root, folder, expected = kept_then_installed(tmp_path)
    real, refusals, pauses = os.rename, [], []

    def rename(source, target):
        if Path(source) == folder / "app" and len(refusals) < (2 if released else 99):
            refusals.append(source)
            raise PermissionError(13, "The process cannot access the file", str(source))
        return real(source, target)
    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(kept, "WAIT", 15.0 if released else 0)
    monkeypatch.setattr(kept.time, "sleep", pauses.append)
    notices.answers.append(True)
    result = kept.first_start(root, VERSION, ask=True)
    if released:
        assert result is None and pauses == [0.5, 0.5] and person(data(root)) == expected
        return
    assert result["taken"] is False and result["reason"] == "take-back"
    assert "Reference: kept-data, take-back, PermissionError" in notices.shown[-1][1] and str(folder) in notices.shown[-1][1]
    assert kept.read(folder)[0] == "adopting"
    here, there = files(person(data(root))), files(person(data(folder)))
    assert not set(here) & set(there) and {**here, **there} == files(expected)
    monkeypatch.setattr(os, "rename", real)
    assert kept.first_start(root, VERSION, ask=True) is None and person(data(root)) == expected
    assert [confirm for confirm, _ in notices.shown] == [True, False]  # One question, one stop message.


def test_continued_move_out_never_adds_to_a_kept_folder_that_is_not_private(tmp_path, monkeypatch):
    root = used(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    release = refuse(monkeypatch, "rename", root / "app", root / "app" / "config")
    kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    release()
    real = kept._private

    def private(path, owned, *, create=False):
        if Path(path) == folder / "app" and not create:
            raise PermissionError(13, "Another account can use this folder", str(path))
        return real(path, owned, create=create)
    monkeypatch.setattr(kept, "_private", private)
    snapshot = tree(root), tree(folder)
    result = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert result["state"] == "moving" and result["remaining"] == ["app"]
    assert (tree(root), tree(folder)) == snapshot


def test_continued_take_back_stops_at_a_folder_that_holds_anything(tmp_path, monkeypatch, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    notices.answers.append(True)
    restore = crash_after(monkeypatch, "rename", 2)
    with pytest.raises(Crash):
        kept.first_start(root, VERSION, ask=True)
    restore()
    (root / "workspace" / "new.txt").write_text("appeared in between")
    snapshot = tree(tmp_path)
    notices.shown.clear()
    result = kept.first_start(root, VERSION, ask=True)
    assert result["taken"] is False and result["reason"] == "take-back-not-empty"
    (confirm, text), = notices.shown
    assert not confirm and str(root / "workspace") in text and "Nothing was deleted" in text
    assert tree(tmp_path) == snapshot and kept.read(folder)[0] == "adopting"


def test_filled_folder_never_replaces_an_entry(tmp_path, monkeypatch, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    refuse(monkeypatch, "rmdir", root / "workspace")
    notices.answers.append(True)
    restore = crash_after(monkeypatch, "rename", 6)  # Five folders, then one entry of the filled folder.
    with pytest.raises(Crash):
        kept.first_start(root, VERSION, ask=True)
    restore()
    assert kept.read(folder)[1]["entries"]["folder"] == "workspace"
    assert os.listdir(root / "workspace") == ["notes.txt"] and os.listdir(folder / "workspace") == ["project"]
    (root / "workspace" / "project").write_text("appeared in between")
    snapshot = tree(tmp_path)
    result = kept.first_start(root, VERSION, ask=False)
    assert result["reason"] == "take-back-not-empty" and tree(tmp_path) == snapshot


def test_entry_that_appears_while_a_folder_is_filled_is_never_replaced(tmp_path, monkeypatch, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    refuse(monkeypatch, "rmdir", root / "workspace")
    real = kept._write

    def write(owner, name, value):
        real(owner, name, value)
        if (value.get("entries") or {}).get("folder") == "workspace":  # After every check, before the first move.
            (root / "workspace" / "notes.txt").write_text("appeared in between")
    monkeypatch.setattr(kept, "_write", write)
    notices.answers.append(True)
    result = kept.first_start(root, VERSION, ask=True)
    assert result["taken"] is False and result["reason"] == "take-back-not-empty"
    assert (root / "workspace" / "notes.txt").read_text() == "appeared in between"
    assert (folder / "workspace" / "notes.txt").read_text() == "mine\n"  # Neither file replaced the other.
    assert kept.read(folder)[0] == "adopting"


@pytest.mark.parametrize("change,kind", [("link", "take-back-link"), ("newer", "take-back-newer"),
                                         ("program-state", "take-back-program-state")])
def test_continued_take_back_runs_every_check_again(tmp_path, monkeypatch, notices, change, kind):
    root, folder, _ = kept_then_installed(tmp_path)
    notices.answers.append(True)
    restore = crash_after(monkeypatch, "rename", 1)  # `shared` is back; `app` is not.
    with pytest.raises(Crash):
        kept.first_start(root, VERSION, ask=True)
    restore()
    if change == "link":
        (tmp_path / "elsewhere").mkdir()
        link(tmp_path / "elsewhere", folder / "linked")
    elif change == "program-state":
        (folder / "app" / "runtime").mkdir()
    snapshot = tree(tmp_path)
    result = kept.first_start(root, "0.96.0" if change == "newer" else VERSION, ask=False)
    assert result["taken"] is False and result["reason"] == kind and tree(tmp_path) == snapshot
    with pytest.raises(kept.Refused):  # An uninstall cannot begin either.
        kept.check(root, "0.96.0" if change == "newer" else VERSION)


# One step at a time, repeated calls, and the installation's own note


def test_usual_start_without_kept_data_touches_nothing(tmp_path, notices):
    root = fresh(tmp_path / "Amplifier")
    snapshot = tree(tmp_path)
    assert kept.first_start(root, VERSION, ask=True) is None
    assert notices.shown == [] and tree(tmp_path) == snapshot  # Also no lock file.


def test_second_start_step_or_move_out_is_refused_while_one_runs(tmp_path, monkeypatch, notices):
    from filelock import FileLock
    root, folder, _ = kept_then_installed(tmp_path)
    monkeypatch.setattr(kept, "BUSY", 0.2)
    snapshot = tree(root), tree(folder)
    with FileLock(str(root.with_name(root.name + "-kept.lock"))):  # Another launcher is at its question.
        result = kept.first_start(root, VERSION, ask=True)
        assert result["taken"] is False and result["reason"] == "busy"
        assert notices.shown == [(False, result["message"])] and "Answer that message" in result["message"]
        assert kept.first_start(root, VERSION, ask=False)["reason"] == "busy" and len(notices.shown) == 1
        with pytest.raises(kept.Refused) as refused:
            kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
        assert refused.value.kind == "busy" and (tree(root), tree(folder)) == snapshot
    notices.answers.append(True)
    assert kept.first_start(root, VERSION, ask=True) is None and not folder.exists()


@pytest.mark.parametrize("ending", ["normal", "killed"])
def test_lock_holds_between_two_programs_and_is_free_when_the_other_program_ends(
        tmp_path, monkeypatch, notices, ending):
    root, folder, expected = kept_then_installed(tmp_path)
    monkeypatch.setattr(kept, "BUSY", 0.2)
    snapshot = tree(root), tree(folder)
    other = subprocess.Popen(
        [sys.executable, "-c", "import sys\nfrom filelock import FileLock\nwith FileLock(sys.argv[1]):\n"
         "    print('held', flush=True)\n    sys.stdin.read()\n", str(root.with_name(root.name + "-kept.lock"))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert other.stdout.readline().strip() == b"held"
        assert kept.first_start(root, VERSION, ask=False)["reason"] == "busy"
        with pytest.raises(kept.Refused) as refused:
            kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
        assert refused.value.kind == "busy" and (tree(root), tree(folder)) == snapshot
        if ending == "killed":  # The other launcher ends without a release, as at a sign-out.
            other.kill()
        else:
            other.stdin.close()
        other.wait(20)
    finally:
        if other.poll() is None:
            other.kill()
            other.wait(20)
        other.stdout.close()
    notices.answers.append(True)
    assert kept.first_start(root, VERSION, ask=True) is None and person(data(root)) == expected


@pytest.mark.parametrize("answer", [True, False])
def test_second_launcher_cannot_start_or_answer_while_the_first_one_asks(tmp_path, monkeypatch, answer):
    """A double click. Without this, Yes in one launcher and a fresh start in the other mix two installations."""
    from amplifier_web import windows_installation
    root, folder, expected = kept_then_installed(tmp_path)
    monkeypatch.setattr(kept, "BUSY", 0.2)
    second, shown = [], []

    def owner_notice(text, *, confirm=False):
        shown.append(confirm)
        if confirm:  # While the first launcher's question is open, the second launcher starts.
            second.append(kept.first_start(root, VERSION, ask=False))
        return answer
    monkeypatch.setattr(windows_installation, "owner_notice", owner_notice)
    assert kept.first_start(root, VERSION, ask=True) is None
    assert second[0]["reason"] == "busy" and shown == [True]
    # The second launcher again, after the first one's answer: it starts, and it asks nothing.
    assert kept.first_start(root, VERSION, ask=True) is None
    if answer:
        assert person(data(root)) == expected and not folder.exists() and shown == [True]
    else:
        assert data(root) == {name: "folder" for name in SETUP} and kept.read(folder)[1]["declined"] is True
        assert shown == [True, False]  # Named, not asked.


def test_repeated_move_out_of_its_own_finished_set_never_sets_it_aside(tmp_path):
    """An uninstall that ended after the set was complete and before its own journal said so."""
    root = used(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    first = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    snapshot = tree(folder)
    kept.check(root, VERSION, T1)
    again = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert again == first and tree(folder) == snapshot
    assert not any(earlier.exists() for earlier in kept.earlier_folders(root))
    # Something new in the installation: the same uninstall moves it too. Until then the set is unfinished.
    (root / "workspace").mkdir()
    (root / "workspace" / "late.txt").write_text("late\n")
    (root / "workspace" / "notes.txt").write_text("another\n")  # The set holds this name already.
    stopped = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert stopped["state"] == "moving" and stopped["remaining"] == ["workspace"]
    assert kept.read(folder)[0] == "moving"
    assert (folder / "workspace" / "late.txt").read_text() == "late\n"
    assert (folder / "workspace" / "notes.txt").read_text() == "mine\n"
    assert (root / "workspace" / "notes.txt").read_text() == "another\n"
    os.remove(root / "workspace" / "notes.txt")
    done = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert done["state"] == "complete" and kept.read(folder)[0] == "complete"


def test_continued_fill_accepts_nothing_that_it_did_not_move(tmp_path, monkeypatch, notices):
    root, folder, expected = kept_then_installed(tmp_path)
    refuse(monkeypatch, "rmdir", root / "workspace")  # The launcher's current folder: it is filled.
    notices.answers.append(True)
    restore = crash_after(monkeypatch, "rename", 6)  # Five folders, then one entry of the filled folder.
    with pytest.raises(Crash):
        kept.first_start(root, VERSION, ask=True)
    restore()
    assert kept.read(folder)[1]["entries"] == {"folder": "workspace", "names": ["notes.txt", "project"]}
    (root / "workspace" / "new.txt").write_text("another program wrote this")  # A name that the set does not hold.
    snapshot = tree(tmp_path)
    result = kept.first_start(root, VERSION, ask=False)
    assert result["reason"] == "take-back-not-empty" and tree(tmp_path) == snapshot
    assert str(root / "workspace" / "new.txt") in result["message"] and "in the way" in result["message"]
    assert "Move it to another place, or rename it." in result["message"]  # The entry is named, with the next step.
    os.remove(root / "workspace" / "new.txt")
    os.remove(root / "workspace" / "notes.txt")  # An entry that the person removed in between is not in the way.
    expected = {key: value for key, value in expected.items() if key != "workspace/notes.txt"}
    assert kept.first_start(root, VERSION, ask=False) is None and person(data(root)) == expected


@pytest.mark.parametrize("where", ["target", "kept"])
def test_entry_that_comes_in_while_a_folder_is_filled_stops_the_take_back(tmp_path, monkeypatch, notices, where):
    """Every name passed its check. Then another program writes a file with a new name, during the moves."""
    root, folder, expected = kept_then_installed(tmp_path)
    refuse(monkeypatch, "rmdir", root / "workspace")
    real, late = os.rename, (root if where == "target" else folder) / "workspace" / "zz-late.txt"

    def rename(old, new):
        if Path(old) == folder / "workspace" / "notes.txt":
            late.write_text("written during the fill")
        return real(old, new)
    monkeypatch.setattr(os, "rename", rename)
    notices.answers.append(True)
    result = kept.first_start(root, VERSION, ask=True)
    assert result["taken"] is False and result["reason"] == "take-back-not-empty" and str(late) in result["message"]
    assert kept.read(folder)[0] == "adopting" and (root / kept.MARKER).exists()  # Not finished: nothing is tidied.
    assert late.read_text() == "written during the fill"
    monkeypatch.setattr(os, "rename", real)
    os.remove(late)
    assert kept.first_start(root, VERSION, ask=False) is None and person(data(root)) == expected


def test_entry_that_appears_after_the_last_check_is_never_replaced(tmp_path, monkeypatch, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    refuse(monkeypatch, "rmdir", root / "workspace")
    real = kept._foreign

    def foreign(record, name, source, target):
        result = real(record, name, source, target)
        if result is None and not os.listdir(target):  # The check passed. Then a program writes the same name.
            (target / "notes.txt").write_text("appeared after the check")
        return result
    monkeypatch.setattr(kept, "_foreign", foreign)
    notices.answers.append(True)
    result = kept.first_start(root, VERSION, ask=True)
    assert result["taken"] is False and result["reason"] == "take-back-not-empty"
    assert (root / "workspace" / "notes.txt").read_text() == "appeared after the check"
    assert (folder / "workspace" / "notes.txt").read_text() == "mine\n"


@pytest.mark.parametrize("direction", ["take-back", "move-out"])
def test_link_that_takes_a_folders_place_at_the_last_moment_is_never_used(tmp_path, monkeypatch, notices, direction):
    """A whole-folder rename cannot hold its own source. A link that is swapped in moves as a link, unused."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "theirs.txt").write_text("not Amplifier's\n")
    if direction == "take-back":
        root, folder, _ = kept_then_installed(tmp_path)
        source = folder / "ownership"
    else:
        root = used(tmp_path / "Amplifier")
        folder, source = kept.kept_folder(root), root / "ownership"
    real = os.rename

    def rename(old, new):
        if Path(old) == source and kept._kind(source) == "folder":  # After every check: the swap.
            real(source, tmp_path / "taken-away")
            link(outside, source)
        return real(old, new)
    monkeypatch.setattr(os, "rename", rename)
    if direction == "take-back":
        notices.answers.append(True)
        result = kept.first_start(root, VERSION, ask=True)
        assert result["taken"] is False and result["reason"] == "take-back-link"
        assert str(root / "ownership") in notices.shown[-1][1] and kept.read(folder)[0] == "adopting"
        assert "do not uninstall Amplifier" in notices.shown[-1][1] and kept._kind(root / "ownership") == "link"
        monkeypatch.setattr(os, "rename", real)
        again = kept.first_start(root, VERSION, ask=False)  # The link is never passed over.
        assert again["reason"] == "take-back-not-empty" and str(root / "ownership") in again["message"]
        assert kept.read(folder)[0] == "adopting"
    else:
        result = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
        assert result["state"] == "moving" and kept.read(folder)[0] == "moving"
        assert kept._kind(folder / "ownership") == "link"
        again = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
        assert again["state"] == "moving" and again["remaining"] == ["ownership"]  # Never complete with a link.
    assert os.listdir(outside) == ["theirs.txt"]  # Never entered.


def test_installation_knows_that_a_take_back_began_also_when_the_record_is_lost(tmp_path, monkeypatch, notices):
    root, folder, expected = kept_then_installed(tmp_path)
    notices.answers.append(True)
    restore = crash_after(monkeypatch, "rename", 2)  # `shared` and `app` are back.
    with pytest.raises(Crash):
        kept.first_start(root, VERSION, ask=True)
    restore()
    assert json.loads((root / kept.MARKER).read_text()) == {"schema": 1, "transfer": T1, "kept": str(folder)}
    assert kept.unfinished_take_back(root)
    good = (folder / kept.RECORD).read_text()
    for damage in ("{", json.dumps({**json.loads(good), "transfer": T2}), None):
        if damage is None:
            os.remove(folder / kept.RECORD)
        else:
            (folder / kept.RECORD).write_text(damage)
        snapshot = tree(tmp_path)
        notices.shown.clear()
        result = kept.first_start(root, VERSION, ask=True)  # The app never starts on half of the data.
        assert result["taken"] is False and result["reason"] == "take-back-record"
        (confirm, text), = notices.shown
        assert not confirm and "Do not uninstall Amplifier" in text and str(folder) in text and str(root) in text
        assert kept.unfinished_take_back(root)
        for attempt in (lambda: kept.check(root, VERSION),
                        lambda: kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)):
            with pytest.raises(kept.Refused) as refused:
                attempt()
            assert refused.value.kind == "take-back-record"
            assert "Open Amplifier from the Start menu." in refused.value.notice
        assert tree(tmp_path) == snapshot
    (folder / kept.RECORD).write_text(good)
    assert kept.first_start(root, VERSION, ask=False) is None
    assert person(data(root)) == expected and not (root / kept.MARKER).exists() and not folder.exists()
    assert not kept.unfinished_take_back(root)


@pytest.mark.parametrize("answer", [False, True])
def test_kept_folder_that_is_gone_during_a_take_back_never_counts_as_finished(tmp_path, monkeypatch, notices, answer):
    """The person renames or deletes the kept folder while a part is already back."""
    root, folder, expected = kept_then_installed(tmp_path)
    notices.answers.append(True)
    restore = crash_after(monkeypatch, "rename", 2)  # `shared` and `app` are back.
    with pytest.raises(Crash):
        kept.first_start(root, VERSION, ask=True)
    restore()
    away = tmp_path / "renamed by the person"
    os.rename(folder, away)
    snapshot = tree(tmp_path)
    silent = kept.first_start(root, VERSION, ask=False)  # A caller without a person never starts the app here.
    assert silent["taken"] is False and silent["reason"] == "take-back-record" and tree(tmp_path) == snapshot
    for attempt in (lambda: kept.check(root, VERSION),
                    lambda: kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)):
        with pytest.raises(kept.Refused) as refused:
            attempt()
        assert refused.value.kind == "take-back-record" and tree(tmp_path) == snapshot
    notices.shown.clear()
    notices.answers.append(answer)
    result = kept.first_start(root, VERSION, ask=True)
    (confirm, text), = notices.shown
    assert confirm and str(folder) in text and "put it back" in text and "Start Amplifier without the rest?" in text
    if not answer:  # No: nothing changes. The folder comes back, and the next start finishes.
        assert result["taken"] is False and tree(tmp_path) == snapshot
        os.rename(away, folder)
        assert kept.first_start(root, VERSION, ask=False) is None and person(data(root)) == expected
        return
    # Yes: the app starts on the part that is back. Its saved update status is retired; the note goes.
    assert result is None and not (root / kept.MARKER).exists() and not kept.unfinished_take_back(root)
    assert "updates" not in saved_state(root / "app")[0]
    assert tree(away) == {key.split("/", 1)[1]: value for key, value in snapshot.items()
                          if key.startswith(away.name + "/")}  # The rest is where the person put it, unchanged.


def test_note_of_a_finished_take_back_is_only_tidied_and_never_blocks(tmp_path, monkeypatch, notices):
    root, folder, expected = kept_then_installed(tmp_path)
    notices.answers.append(True)
    real = kept._drop_marker
    monkeypatch.setattr(kept, "_drop_marker", lambda root: None)  # Windows refuses the removal, or the program ends.
    assert kept.first_start(root, VERSION, ask=True) is None
    note = json.loads((root / kept.MARKER).read_text())
    assert note == {"schema": 1, "transfer": T1, "kept": str(folder), "done": True} and not folder.exists()
    assert not kept.unfinished_take_back(root)
    # A later uninstall with "keep" is not blocked by that note, also while it cannot be removed.
    (root / "app" / "held.txt").write_text("held")
    release = refuse(monkeypatch, "rename", root / "app", root / "app" / "held.txt")
    kept.check(root, VERSION)
    first = kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
    assert first["state"] == "moving" and first["stayed"] == [str(root / "app" / "held.txt")]
    assert str(root / "app" / "held.txt") in first["notice"]
    release()
    second = kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
    assert second["state"] == "complete" and (root / kept.MARKER).exists()
    # When the removal works again, the next step removes the note. No database is opened for that.
    monkeypatch.setattr(kept, "_drop_marker", real)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("The database was opened."))
    kept.check(root, VERSION, T2)
    assert not (root / kept.MARKER).exists()


def test_stop_between_the_last_step_and_the_tidying_leaves_only_tidying(tmp_path, monkeypatch, notices):
    root, folder, expected = kept_then_installed(tmp_path)
    notices.answers.append(True)
    real = kept._finished
    monkeypatch.setattr(kept, "_finished", lambda root, kept_folder: None)  # The program ends after `done`.
    assert kept.first_start(root, VERSION, ask=True) is None
    monkeypatch.setattr(kept, "_finished", real)
    assert kept.read(folder)[0] == "adopting" and json.loads((root / kept.MARKER).read_text())["done"] is True
    assert person(data(root)) == expected and not kept.unfinished_take_back(root)
    # The app can be running at the next start: the record and the note go, and no database is opened.
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("The database was opened."))
    assert kept.first_start(root, VERSION, ask=False) is None
    assert not folder.exists() and not (root / kept.MARKER).exists() and person(data(root)) == expected
    assert len(list((root / "app" / "reset-backups").iterdir())) == 1 and len(notices.shown) == 1


def test_take_back_is_not_finished_until_the_saved_update_status_is_retired(tmp_path, monkeypatch, notices):
    from amplifier_web import reset
    root, folder, expected = kept_then_installed(tmp_path)
    real, calls = reset.reset_update_state, []

    def failing(home, backup):
        calls.append(backup)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(home, backup)
    monkeypatch.setattr(reset, "reset_update_state", failing)
    notices.answers.append(True)
    result = kept.first_start(root, VERSION, ask=True)
    assert result["taken"] is False and result["reason"] == "take-back-prepare"
    assert "could not prepare them" in notices.shown[-1][1] and "Nothing was deleted" in notices.shown[-1][1]
    assert kept.read(folder)[0] == "adopting" and (root / kept.MARKER).exists()
    assert person(data(root)) == expected and saved_state(root / "app")[0]["updates"] == FENCE
    assert kept.first_start(root, VERSION, ask=True) is None  # The next start tries again.
    assert "updates" not in saved_state(root / "app")[0] and not folder.exists()
    assert not (root / kept.MARKER).exists() and [confirm for confirm, _ in notices.shown] == [True, False]
    assert len(calls) == 2 and calls[0] != calls[1]  # Never the same folder twice.


def test_uninstall_can_keep_the_data_again_while_that_last_step_fails(tmp_path, monkeypatch, notices):
    from amplifier_web import reset

    def failing(home, backup):
        raise sqlite3.DatabaseError("database disk image is malformed")
    root, folder, expected = kept_then_installed(tmp_path)
    monkeypatch.setattr(reset, "reset_update_state", failing)
    notices.answers.append(True)
    assert kept.first_start(root, VERSION, ask=True)["reason"] == "take-back-prepare"
    kept.check(root, VERSION)
    result = kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
    assert result["state"] == "complete" and person(data(folder)) == expected and data(root) == {}
    assert saved_state(folder / "app")[0]["updates"] == FENCE and not (root / kept.MARKER).exists()


def test_folder_whose_name_differs_only_in_case_is_never_joined_and_never_skipped(tmp_path, notices):
    """Windows opens `App` and `app` as one name. A listing shows only the spelling on the disk."""
    root, folder, _ = kept_then_installed(tmp_path)
    os.rmdir(root / "app")
    (root / "App").mkdir()
    (root / "App" / "new.txt").write_text("new")
    snapshot = tree(tmp_path)
    assert not kept._fresh(root)
    assert kept.first_start(root, VERSION, ask=True) is None and notices.shown == []  # Not a first start.
    with pytest.raises(kept.Refused) as refused:
        kept.take_back(root, VERSION)
    assert refused.value.kind == "take-back-not-empty" and str(root / "App") in refused.value.notice
    for attempt in (lambda: kept.check(root, VERSION),
                    lambda: kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)):
        with pytest.raises(kept.Refused) as refused:
            attempt()
        assert refused.value.kind == "kept-name" and str(root / "App") in refused.value.notice
        assert "Rename it to app." in refused.value.notice
    assert tree(tmp_path) == snapshot


def test_kept_folder_with_a_changed_name_inside_cannot_be_read(tmp_path, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    os.rename(folder / "app", folder / "App")
    before = tree(folder)
    notices.answers.append(False)
    result = kept.first_start(root, VERSION, ask=True)
    assert result["reason"] == "unreadable" and "cannot read them" in notices.shown[0][1]
    with pytest.raises(kept.Refused):
        kept.take_back(root, VERSION)
    assert tree(folder) == before and data(root) == {name: "folder" for name in SETUP}


@pytest.mark.parametrize("holds,asked", [("app", True), ("photos.zip", False)])
def test_folder_without_a_record_is_kept_data_only_when_it_holds_a_data_folder(tmp_path, notices, holds, asked):
    root = fresh(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    folder.mkdir()
    if holds == "app":
        (folder / "app").mkdir()
        (folder / "app" / "chat.txt").write_text("kept")
    else:
        (folder / holds).write_text("the person's own file")
    before = tree(folder)
    notices.answers.append(False)
    result = kept.first_start(root, VERSION, ask=True)
    if asked:
        assert result["reason"] == "unreadable" and notices.shown[0][0] and str(folder) in notices.shown[0][1]
    else:
        assert result is None and notices.shown == []
    assert tree(folder) == before and data(root) == {name: "folder" for name in SETUP}


def test_every_stop_is_one_plain_message_with_a_reference_and_never_an_exception(tmp_path, monkeypatch, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    # A take-back for which nothing is kept: the reference names the cause, never "None".
    os.rename(folder, tmp_path / "moved-away")
    with pytest.raises(kept.Refused) as refused:
        kept.take_back(root, VERSION)
    assert refused.value.kind == "take-back-missing" and "None" not in refused.value.notice
    os.rename(tmp_path / "moved-away", folder)
    # A move-out whose rename of the earlier set fails: refused, with the next step.
    used(root)
    release = refuse(monkeypatch, "rename", folder)
    with pytest.raises(kept.Refused) as refused:
        kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
    assert refused.value.kind == "move-out" and "Then uninstall again." in refused.value.notice
    assert "Reference: kept-data, move-out, PermissionError" in refused.value.notice
    release()
    # A start that cannot even list a folder: a result and one message, not an exception.
    shutil.rmtree(root)
    fresh(root)

    def names(path):
        raise PermissionError(13, "Access is denied", str(path))
    monkeypatch.setattr(kept, "_names", names)
    result = kept.first_start(root, VERSION, ask=True)
    assert result["taken"] is False and result["reason"] == "check" and "Nothing was changed" in result["message"]
    assert "Reference: kept-data, check, PermissionError" in result["message"]
    assert notices.shown == [(False, result["message"])]


# Round 2 of the review: a late write, the saved answer, a linked database, versions


def test_file_that_a_closing_program_writes_after_the_last_entry_moved_is_never_left_out(tmp_path, monkeypatch):
    """`workspace` is a program's current folder: its entries move one by one, and the empty folder stays.

    A closing program writes one more file into it after the last entry moved.
    The set must not become complete: the uninstall would delete that file.
    """
    root = used(tmp_path / "Amplifier")
    before, folder = data(root), kept.kept_folder(root)
    refuse(monkeypatch, "rename", root / "workspace")
    real = os.rmdir

    def rmdir(path, *args, **kwargs):
        if Path(path) == root / "workspace":
            if not (folder / "workspace" / "late.txt").exists():
                (root / "workspace" / "late.txt").write_text("written late\n")
            raise PermissionError(13, "The process cannot access the file", str(path))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(os, "rmdir", rmdir)
    first = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert first["state"] == "moving" and first["remaining"] == ["workspace"]
    assert first["stayed"] == [str(root / "workspace" / "late.txt")] and first["stayed"][0] in first["notice"]
    assert kept.read(folder)[0] == "moving" and (folder / "app" / "updates").is_dir()  # Nothing was deleted yet.
    second = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert second["state"] == "complete" and second["stayed"] == []
    assert (folder / "workspace" / "late.txt").read_text() == "written late\n"
    assert person(data(folder)) == {**person(before), "workspace/late.txt": tree(folder)["workspace/late.txt"]}


def test_data_folder_that_comes_back_after_it_moved_keeps_the_set_unfinished(tmp_path, monkeypatch):
    root = used(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    real, wrote = kept._move, []

    def move(source, target, owned):
        result = real(source, target, owned)
        if source.name == "export" and not wrote:  # The last folder moved. A closing program makes one again.
            wrote.append((root / "app" / "sessions" / "late").mkdir(parents=True))
            (root / "app" / "sessions" / "late" / "transcript.jsonl").write_text("late chat\n")
        return result
    monkeypatch.setattr(kept, "_move", move)
    first = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert first["state"] == "moving" and first["remaining"] == ["app"]
    assert first["stayed"] == [str(root / "app" / "sessions" / "late" / "transcript.jsonl")]
    assert kept.read(folder)[0] == "moving"
    # The set has `app\sessions` already. The late chat joins it; no entry replaces an entry.
    second = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
    assert second["state"] == "complete" and data(root) == {}
    assert (folder / "app" / "sessions" / "late" / "transcript.jsonl").read_text() == "late chat\n"
    assert (folder / "app" / "sessions" / "first" / "transcript.jsonl").read_text() == "chat first\n"


def test_late_file_with_a_name_that_the_set_holds_stays_and_is_named(tmp_path, monkeypatch):
    root = used(tmp_path / "Amplifier")
    folder = kept.kept_folder(root)
    assert kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)["state"] == "complete"
    late = root / "app" / "sessions" / "first" / "transcript.jsonl"  # The same chat file again, with other text.
    late.parent.mkdir(parents=True)
    late.write_text("written late\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    link(outside, root / "app" / "config")  # A link with a name that the set holds as a folder: never entered.
    for _ in range(2):
        result = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)
        assert result["state"] == "moving" and result["remaining"] == ["app"]
        assert result["stayed"] == [str(root / "app" / "config")] and kept.read(folder)[0] == "moving"
    assert late.read_text() == "written late\n" and os.listdir(outside) == []
    assert (folder / "app" / "sessions" / "first" / "transcript.jsonl").read_text() == "chat first\n"
    assert (folder / "app" / "config" / "server.yaml").read_text() == "port: 51234\n"


def test_answer_no_that_cannot_be_saved_never_starts_the_app(tmp_path, monkeypatch, notices):
    root, folder, expected = kept_then_installed(tmp_path)
    before, real = tree(folder), kept._write

    def write(owner, name, value):
        if value.get("declined"):
            raise PermissionError(13, "The private write was refused", str(owner / name))
        return real(owner, name, value)
    monkeypatch.setattr(kept, "_write", write)
    notices.answers.append(False)
    result = kept.first_start(root, VERSION, ask=True, hold=True)
    assert result["taken"] is False and result["reason"] == "answer" and not kept._STAYING
    assert [confirm for confirm, _ in notices.shown] == [True, False]
    assert "could not save your answer" in notices.shown[1][1] and "Nothing was changed" in notices.shown[1][1]
    assert tree(folder) == before and data(root) == {name: "folder" for name in SETUP}
    # The next start asks again, and Yes still works.
    monkeypatch.setattr(kept, "_write", real)
    notices.answers.append(True)
    assert kept.first_start(root, VERSION, ask=True, hold=True) is None and person(data(root)) == expected
    assert not kept._STAYING  # The data is back: the lock is free.


@pytest.mark.parametrize("answered", ["no", "unreadable-yes", "named-only"])
def test_launcher_that_starts_fresh_keeps_the_lock_until_it_ends(tmp_path, monkeypatch, notices, answered):
    """Until this start wrote its first file, a second launcher would see empty folders and a kept set."""
    root, folder, _ = kept_then_installed(tmp_path)
    monkeypatch.setattr(kept, "BUSY", 0.2)
    if answered == "no":
        notices.answers.append(False)
    elif answered == "unreadable-yes":
        (folder / kept.RECORD).write_text("{")
        notices.answers.append(True)  # Yes: start without them. Nothing can be saved in a set that is unreadable.
    else:
        record(folder, version="9.0.0")  # A newer set: named, not offered.
    assert kept.first_start(root, VERSION, ask=True, hold=True) is None
    assert len(kept._STAYING) == 1 and len(notices.shown) == 1  # One question, or one message that names the set.
    shown = len(notices.shown)
    second = kept.first_start(root, VERSION, ask=True, hold=True)  # A second launcher, before the first file.
    assert second["taken"] is False and second["reason"] == "busy" and len(notices.shown) == shown + 1
    with pytest.raises(kept.Refused):
        kept.move_out(root, version=VERSION, transfer=T2, remove=shutil.rmtree, wait=0)
    # The first launcher wrote the app's settings and ended.
    (root / "app" / "config").mkdir()
    kept._STAYING.pop().release()
    assert kept.first_start(root, VERSION, ask=True, hold=True) is None
    assert len(notices.shown) == shown + 1 and not kept._STAYING  # No question, no lock: it has its own data.


def test_caller_that_is_not_the_launcher_never_keeps_the_lock(tmp_path, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    notices.answers.append(False)
    assert kept.first_start(root, VERSION, ask=True) is None and not kept._STAYING


def test_database_with_a_second_name_is_never_changed(tmp_path, notices):
    """A hard link: the same file is also another installation's database. A link check does not see it."""
    root, folder, expected = kept_then_installed(tmp_path)
    other = tmp_path / "another-installation.sqlite3"
    os.link(folder / "app" / "app.sqlite3", other)
    before = other.read_bytes()
    notices.answers.append(True)
    assert kept.first_start(root, VERSION, ask=True) is None
    assert other.read_bytes() == before and (root / "app" / "app.sqlite3").read_bytes() == before
    with closing(sqlite3.connect(other)) as db:
        assert json.loads(db.execute("SELECT value FROM state WHERE id=1").fetchone()[0])["updates"] == FENCE
    assert not (root / "app" / "reset-backups").exists()
    assert person(data(root)) == expected and not folder.exists() and not (root / kept.MARKER).exists()


def test_failed_attempt_to_retire_the_update_status_leaves_no_empty_folder(tmp_path, monkeypatch, notices):
    from amplifier_web import reset

    def failing(home, backup):
        raise sqlite3.OperationalError("database is locked")
    root, folder, _ = kept_then_installed(tmp_path)
    monkeypatch.setattr(reset, "reset_update_state", failing)
    notices.answers.append(True)
    for _ in range(3):
        assert kept.first_start(root, VERSION, ask=True)["reason"] == "take-back-prepare"
    assert list((root / "app" / "reset-backups").iterdir()) == []


def test_set_with_a_folder_that_holds_too_many_entries_is_not_offered_and_is_named(tmp_path, monkeypatch, notices):
    root, folder, _ = kept_then_installed(tmp_path)
    monkeypatch.setattr(kept, "NAMES", 16)  # `workspace` holds two entries: more than these 16 characters.
    before = tree(folder)
    assert kept.first_start(root, VERSION, ask=True) is None
    (confirm, text), = notices.shown
    assert not confirm and str(folder) in text and "too many files and folders" in text
    with pytest.raises(kept.Refused) as refused:
        kept.take_back(root, VERSION)
    assert refused.value.kind == "take-back-too-many"
    assert tree(folder) == before and data(root) == {name: "folder" for name in SETUP}


@pytest.mark.parametrize("launcher,offered", [("0.96.1rc1", True), ("v0.96.1", True), ("0.96.1+local.5", True),
                                              ("0.96.0.dev3", False), ("next", False), ("", False)])
def test_version_with_a_suffix_is_compared_by_its_three_numbers(tmp_path, notices, launcher, offered):
    root, folder, expected = kept_then_installed(tmp_path)
    notices.answers.append(True)
    assert kept.first_start(root, launcher, ask=True) is None  # Never an error, with any version text.
    if offered:
        assert notices.shown[0][0] and person(data(root)) == expected
    else:  # Not comparable, or older than the set: the set is named and stays.
        assert [confirm for confirm, _ in notices.shown] == [False] and "newer version" in notices.shown[0][1]
        assert data(root) == {name: "folder" for name in SETUP}


def test_move_out_takes_a_version_with_a_suffix_and_refuses_one_without_numbers(tmp_path):
    root = used(tmp_path / "Amplifier")
    assert kept.used_version(root, "0.98.0rc2") == "0.98.0"
    kept.check(root, "0.98.0rc2")
    result = kept.move_out(root, version="0.98.0rc2", transfer=T1, remove=shutil.rmtree, wait=0)
    assert result["state"] == "complete" and kept.read(kept.kept_folder(root))[1]["version"] == "0.98.0"
    for call in (lambda: kept.check(root, "next"), lambda: kept.used_version(root, "next"),
                 lambda: kept.move_out(root, version="next", transfer=T1, wait=0),
                 lambda: kept.move_out(root, version=VERSION, transfer="not hex", wait=0)):
        with pytest.raises(ValueError):
            call()


def test_error_inside_a_take_back_is_its_own_message_and_never_nothing_was_changed(tmp_path, monkeypatch, notices):
    root, folder, expected = kept_then_installed(tmp_path)
    real, calls = kept._write, []

    def write(owner, name, value):
        calls.append(name)
        if name == kept.MARKER and value.get("done"):  # After every folder is back.
            raise ValueError("The private write was refused")
        return real(owner, name, value)
    monkeypatch.setattr(kept, "_write", write)
    notices.answers.append(True)
    result = kept.first_start(root, VERSION, ask=True)
    assert result["taken"] is False and result["reason"] == "take-back"
    assert "Nothing was changed" not in result["message"] and "Nothing was deleted" in result["message"]
    assert "Reference: kept-data, take-back, ValueError" in result["message"]
    assert kept.read(folder)[0] == "adopting" and kept.unfinished_take_back(root)
    monkeypatch.setattr(kept, "_write", real)
    assert kept.first_start(root, VERSION, ask=False) is None and person(data(root)) == expected
    assert len(list((root / "app" / "reset-backups").iterdir())) == 2  # Each attempt saved the database anew.


# The reason for the rule, the launcher's step, and what the rule rests on


def test_kept_launch_record_of_another_launcher_stops_every_start(tmp_path):
    """Why a set that still holds `app\\updates` is never used under a new program."""
    from amplifier_web import application_generations, windows_service
    home, row = tmp_path / "app", {"revision": "a" * 40, "generation": "e" * 32, "version": "0.97.0",
                                   "componentDigest": "f" * 64}
    generation = application_generations.directory(home, row["revision"], row["generation"])
    generation.mkdir(parents=True)
    (generation / "validated.json").write_text(json.dumps(row))
    (home / "updates" / "application-active.json").write_text(json.dumps(
        {"schema": 1, "bootstrap": {"prefix": "the removed launcher", "digest": "0" * 64}, **row}))
    prefix = tmp_path / "tools" / "amplifier-unified"
    (prefix / "Lib/site-packages/amplifier_unified-0.98.0.dist-info").mkdir(parents=True)
    (prefix / "Lib/site-packages/amplifier_unified-0.98.0.dist-info/METADATA").write_text("Version: 0.98.0\n")
    spec = SimpleNamespace(python=str(prefix / "Scripts" / "python.exe"), data_dir=str(home))
    with pytest.raises(windows_service.WindowsServiceError, match="another installation"):
        windows_service._launch_python(spec)
    shutil.rmtree(home / "updates")
    assert windows_service._launch_python(spec) == (spec.python, None)


@pytest.mark.parametrize("outcome", [None, {"taken": False, "reason": "question", "message": "waiting"}])
def test_launch_asks_about_kept_data_before_anything_touches_a_data_folder(tmp_path, monkeypatch, outcome):
    from amplifier_web import windows_consumer as consumer, windows_installation
    order = []
    monkeypatch.setattr(consumer, "installation",
                        lambda root: (order.append("installation"), (Path(root), {"version": VERSION}))[1])
    monkeypatch.setattr(kept, "first_start",
                        lambda root, version, *, ask, hold: (
                            order.append(("kept", root, version, ask, hold)), outcome)[1])
    monkeypatch.setattr(consumer, "environment", lambda root, value: (order.append("environment"), {})[1])

    async def prepare(*args, **kwargs):
        order.append("prepare")
        return {"background": True}
    monkeypatch.setattr(windows_installation, "prepare_background", prepare)
    result = asyncio.run(consumer.launch(tmp_path, open_browser=False))
    asked = ("kept", tmp_path, VERSION, False, True)  # The launcher keeps the lock when it starts fresh.
    if outcome is None:
        assert order == ["installation", asked, "environment", "prepare"] and result == {"background": True}
    else:
        assert order == ["installation", asked] and result == {"background": False, "keptData": outcome}


def test_setup_creates_the_data_folders_that_a_first_start_expects_empty():
    source = (Path(__file__).parents[1] / "scripts" / "windows_bootstrap_native.cs").read_text(encoding="utf-8")
    created = set(re.findall(r'"([^"]+)"', re.search(
        r'foreach\(string relative in new\[\]\{([^}]*)\}\)', source).group(1)))
    assert set(kept.FOLDERS) & created == set(SETUP) and "export" not in created


def test_module_deletes_only_empty_folders_its_own_records_and_its_lock_file():
    source = Path(kept.__file__).read_text(encoding="utf-8")
    assert not re.search(r"rmtree|unlink|removedirs|truncate|copy", source)
    assert re.findall(r"os\.remove\(([^)]*)\)", source) == ["path", "folder / name"]  # The lock; a record.
    assert re.findall(r"os\.replace\(([^)]*)\)", source) == ["draft, folder / name"]
    assert re.findall(r"\.remove_private\((\w+)", source) == ["name"]
    # The two records are the only files that are ever written or deleted.
    assert set(re.findall(r"_forget\(([^)]*)\)", source)) == {"folder, name", "kept, RECORD", "root, MARKER"}
    assert set(re.findall(r"_write\((\w+, \w+),", source)) == {"folder, name", "kept, RECORD", "root, MARKER"}


# Native Windows


native = pytest.mark.skipif(os.name != "nt", reason="NTFS renames, current folders, a held file, a junction "
                                                    "and the private-folder boundary")


@pytest.fixture
def installation(monkeypatch):
    """A throwaway installation folder in this person's profile, behind the real private-folder boundary."""
    from amplifier_web import windows_files
    monkeypatch.setattr(kept, "_NATIVE", True)
    root = windows_files.address(Path.home() / ("au-keep-test-" + uuid.uuid4().hex[:12]))
    outside = root.with_name(root.name + "-outside")
    programs = []

    def program_in(folder):
        """Another program whose current folder is `folder`, as the launcher's parent has at a first start."""
        program = subprocess.Popen(
            [getattr(sys, "_base_executable", sys.executable), "-c", "import sys; print('ready', flush=True); "
             "sys.stdin.read()"], cwd=str(folder), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        assert program.stdout.readline().strip() == b"ready"
        programs.append(program)

        def end():
            program.stdin.close()
            program.wait(20)
        return end
    try:
        yield SimpleNamespace(root=root, outside=outside, program_in=program_in)
    finally:
        os.chdir(Path.home())
        for program in programs:
            if program.poll() is None:
                program.kill()
                program.wait(20)
        for folder in (root, outside, kept.kept_folder(root), *kept.earlier_folders(root)):
            if kept._kind(folder) == "link":
                os.rmdir(folder)
            shutil.rmtree(folder, ignore_errors=True)


def native_folders(root):
    from amplifier_web import windows_files
    for name in SETUP:
        with windows_files.directory(root / name, owned_root=root, create=True):
            pass


def native_used(root):
    from amplifier_web import windows_files
    from amplifier_web.deployment import write_private
    native_folders(root)
    for path, text in {
            "app/config/server.yaml": "port: 51234\n", "app/sessions/first/transcript.jsonl": "chat first\n",
            "shared/keys.env": "KEY=secret\n", "shared/settings.yaml": "bundle: mine\n",
            "ownership/owner.json": '{"pid": 4242}', "workspace/notes.txt": "mine\n",
            "workspace/project/readme.md": "project\n",
            "app/updates/application-active.json": json.dumps({"schema": 1, "version": VERSION}),
            "app/runtime/q-1/pyproject.toml": ""}.items():
        write_private(root / path, text, owned_root=root)
    database(root / "app")
    with windows_files.directory(root / "tools", owned_root=root, create=True):
        pass


@native
def test_native_round_trip_with_current_folders_a_held_file_a_junction_and_an_interruption(
        installation, monkeypatch, notices):
    """The whole path on NTFS, with the refusals that Windows gives.

    Real: renames, another program's current folder, this program's current
    folder, a file that a program holds open, a junction, private folders and
    files with their access lists, the private record writer and reader.
    Stand-ins: the dialogs, the deleter (`shutil.rmtree`), and the uninstall
    steps around the move (no app, no task, no setup).
    """
    from amplifier_web import windows_files
    root, outside = installation.root, installation.outside
    folder = kept.kept_folder(root)
    native_used(root)
    with windows_files.directory(outside, owned_root=outside, create=True):
        pass
    (outside / "theirs.txt").write_text("not Amplifier's\n")
    os.rmdir(root / "capture")
    link(outside, root / "capture")  # A junction as a top-level data entry.
    before = data(root)

    # Move-out. Another program's current folder is `workspace`; so is this program's.
    end = installation.program_in(root / "workspace")
    os.chdir(root / "workspace")
    held = open(root / "app" / "config" / "server.yaml", "rb")  # A closing program still holds a file.
    try:
        first = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=1)
    finally:
        held.close()
    assert root not in Path.cwd().parents and Path.cwd() != root  # It left the installation folder itself.
    assert first["state"] == "moving" and first["remaining"] == ["app"] and first["left"] == ["capture"]
    assert kept.read(folder)[0] == "moving"
    here, there = files(data(root)), files(data(folder))
    assert not set(here) & set(there) and {**here, **there} == files(before)  # Nothing lost, nothing twice.
    assert set(here) == {"app/config/server.yaml"}
    # `workspace` could not be renamed: its entries moved one by one, and the empty folder stays.
    assert os.listdir(root / "workspace") == [] and (folder / "workspace" / "project" / "readme.md").is_file()

    second = kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=1)
    assert second["state"] == "complete" and second["left"] == ["capture"] and second["programState"] == []
    assert person(data(folder)) == person({k: v for k, v in before.items() if not k.startswith("capture")})
    assert kept._kind(root / "capture") == "link" and (outside / "theirs.txt").read_text() == "not Amplifier's\n"
    state, saved = kept.read(folder)  # Read through the private-file boundary.
    assert state == "complete" and saved["root"] == str(root) and saved["transfer"] == T1

    # The rest of the uninstall, then a later setup run: six empty private folders.
    end()
    os.rmdir(root / "capture")
    shutil.rmtree(root)
    native_folders(root)
    assert kept._fresh(root)
    expected = person(data(folder))

    # First start. `workspace` is the current folder of the launcher and of its parent.
    end = installation.program_in(root / "workspace")
    os.chdir(root / "workspace")
    notices.answers.append(True)
    restore = crash_after(monkeypatch, "rename", 2)
    with pytest.raises(Crash):
        kept.first_start(root, VERSION, ask=True)
    restore()
    assert kept.read(folder)[0] == "adopting"
    assert kept.first_start(root, VERSION, ask=False) is None  # Finished without a second question.
    assert [confirm for confirm, _ in notices.shown] == [True]
    assert "capture" not in expected  # It was a junction and never moved: setup's own empty folder stays.
    assert person(data(root)) == {**expected, "capture": "folder"} and not folder.exists()
    assert sorted(os.listdir(root / "workspace")) == ["notes.txt", "project"]  # Filled, not replaced.
    end()

    # The folders that came back pass the checks that the launcher and the app make.
    for name in SETUP:
        with windows_files.directory(root / name, owned_root=root):
            pass
    with windows_files.directory(root / "app" / "config", owned_root=root) as config:
        assert config.read_private("server.yaml", limit=4096) == b"port: 51234\n"
    with windows_files.directory(root / "shared", owned_root=root) as shared:
        assert shared.read_private("keys.env", limit=4096) == b"KEY=secret\n"
    assert "updates" not in saved_state(root / "app")[0]
    backup, = (root / "app" / "reset-backups").iterdir()
    with windows_files.directory(backup, owned_root=root):
        pass
    assert saved_state(backup)[0]["updates"] == FENCE


@native
def test_native_kept_folder_that_another_account_can_use_is_never_trusted(installation, notices):
    """A record that looks right in a folder that is not private: never read, never used, never changed."""
    from amplifier_web import windows_files
    root = installation.root
    folder = kept.kept_folder(root)
    native_used(root)
    with windows_files.directory(folder, owned_root=folder, create=True):
        pass
    record(folder, root=str(root))
    # Everyone (S-1-1-0) gets full access to the folder and to what is in it.
    subprocess.run(["icacls", str(folder), "/grant", "*S-1-1-0:(OI)(CI)F"], check=True, capture_output=True)
    snapshot = tree(root), tree(folder)
    assert kept.read(folder) == ("unreadable", None)
    for attempt in (lambda: kept.check(root, VERSION),
                    lambda: kept.move_out(root, version=VERSION, transfer=T1, remove=shutil.rmtree, wait=0)):
        with pytest.raises(kept.Refused) as refused:
            attempt()
        assert refused.value.kind == "kept-folder" and str(folder) in refused.value.notice
    assert (tree(root), tree(folder)) == snapshot
    # A later installation does not use it either. It starts fresh only on the person's Yes.
    shutil.rmtree(root)
    native_folders(root)
    notices.answers.append(False)
    result = kept.first_start(root, VERSION, ask=True)
    assert result["reason"] == "unreadable" and "cannot read them" in notices.shown[0][1]
    assert tree(folder) == snapshot[1] and data(root) == {name: "folder" for name in SETUP}


@native
def test_native_held_folder_stays_in_place_while_its_entries_move(installation):
    """What a fill relies on: nobody can swap a folder that is held, and entries still move in and out."""
    root = installation.root
    native_folders(root)
    (root / "workspace" / "a.txt").write_text("a")
    with kept._held(root), kept._held(root / "workspace"), kept._held(root / "app"):
        with pytest.raises(OSError):
            os.rename(root / "workspace", root / "elsewhere")
        with pytest.raises(OSError):
            os.rmdir(root / "app")
        os.rename(root / "workspace" / "a.txt", root / "app" / "a.txt")
        os.rmdir(root / "capture")  # A folder that is not held is not affected.
    os.rename(root / "workspace", root / "elsewhere")  # Released.
    assert (root / "app" / "a.txt").read_text() == "a"
    link(root / "elsewhere", root / "workspace")
    with pytest.raises(OSError):
        with kept._held(root / "workspace"):  # A junction is never held or entered.
            pass
    os.rmdir(root / "workspace")
