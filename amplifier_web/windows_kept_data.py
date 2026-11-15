"""Kept chats and settings of a removed Windows installation.

Setup installs only into an absent or empty folder, so kept data cannot stay
in the installation folder. "Keep my chats and settings" moves the person's
data folders into the sibling folder `<root>-kept`. A later installation in
the same folder offers them once, at its first start, and takes them back.

Only whole folders are renamed on one disk. Nothing is merged: an entry never
replaces an entry. No chat, setting or account is deleted. This module deletes
only empty folders, its own two records and its lock file; the program state
inside the data (`app\\updates`, `app\\runtime`) is deleted by the caller's
link-safe deleter.

Links. A link at the top level (in the place of a data folder) is never moved
and never entered. A link inside a data folder moves with that folder as the
link itself, as in every rename; it is never followed.

Two records. `<root>-kept\\amplifier-kept.json` names the installation folder,
the newest app version that used the data, one transfer identity and a state:
`moving` (move-out not finished), `complete`, `adopting` (take-back not
finished), `returned` (everything is back; only tidying is left). An
`adopting` record also names the identity of the installation folder that
takes the set back, of each folder that goes back whole, and of each entry of
a folder that is filled: during a take-back every data folder of the
installation is empty, or is one of these.
`<root>\\amplifier-kept-return.json` is the installation's own note of a
take-back: it tells that a take-back began also when the record is lost;
with `done` only tidying is left; with `left` the person chose to start
without the rest, and this installation continues no take-back. When the
note is gone and the record still names this folder, the person is asked.
An unfinished transfer is finished first, in its own direction. A move-out
continues only the set that holds its own transfer identity (the uninstall
journal saves it), so two installations' data never share a set.

One start step or move-out runs at a time for an installation.
"""
from __future__ import annotations

import calendar
from contextlib import contextmanager, suppress
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

FOLDERS = ("shared", "app", "capture", "ownership", "import", "workspace", "export")
# A new launcher refuses the launch record of another launcher
# (windows_service._launch_python), and worker environments name the removed
# Python. So a kept set that still holds one of these is never used.
PROGRAM_STATE = ("updates", "runtime")
RECORD = "amplifier-kept.json"
MARKER = "amplifier-kept-return.json"
EARLIER = 9
LIMIT = 1024 * 1024  # The largest record. The private-file boundary reads no more.
NAMES = 512 * 1024  # The entry names of one folder, as JSON, that the record can hold.
WAIT = 15.0  # Seconds to wait for a program that holds a folder for a moment.
BUSY = 3.0  # Seconds to wait for another start step or move-out of this installation.
_FIELDS = {"schema", "root", "version", "keptAt", "state", "transfer"}
_HEX32 = "[a-f0-9]{32}"
_NATIVE = os.name == "nt"  # The private-folder boundary of windows_files.
_STAYING = []  # Locks of a launcher that starts the app on empty data folders. They end with the program.

# A move-out that cannot start, or stopped. Nothing was deleted in any of these.
_OCCUPIED = ("Amplifier cannot keep your chats and settings, because this folder exists and Amplifier "
             "cannot use it:\n{kept}\n\nNothing was deleted. Move or rename that folder. Then uninstall again.")
_FULL = ("Amplifier cannot keep your chats and settings, because nine earlier kept folders exist next to "
         "this folder:\n{kept}\n\nNothing was deleted. Move or rename one of them. Then uninstall again.")
_RENAMED = ("Amplifier cannot keep your chats and settings, because this folder has a changed name:\n{path}\n\n"
            "Nothing was deleted. Rename it to {name}. Then uninstall again.")
_STUCK = ("Amplifier could not finish moving your chats and settings to this folder:\n{kept}\n\n{stayed}"
          "Your chats and settings were not deleted. Restart this PC. Then uninstall again.")
_STAYED = "This stayed behind:\n{path}\n\n"
_BOTH = ("Amplifier cannot finish keeping your chats and settings, because this file or folder exists two "
         "times:\n{path}\n{twin}\n\nYour chats and settings were not deleted. Move the first one to a place "
         "outside these two folders. Then uninstall again.")
_KEPT_ENTRY = ("Amplifier cannot finish keeping your chats and settings, because this is in the way in the "
               "folder for your kept chats and settings:\n{path}\n\nYour chats and settings were not deleted. "
               "Move it to a place outside that folder. Then uninstall again.")
_LINK_STAYED = ("Amplifier cannot finish keeping your chats and settings, because this is a link, and "
                "Amplifier does not follow links:\n{path}\n\nYour chats and settings were not deleted. "
                "Remove the link. Then uninstall again.")
_RETURNING = ("Amplifier cannot keep your chats and settings yet. An earlier step that moves kept chats and "
              "settings back did not finish.\n\nNothing was deleted. Open Amplifier from the Start menu. "
              "It finishes that step, or it tells you what to do. Then uninstall again.")
_BUSY = ("Amplifier is already starting, or an Amplifier message waits for your answer.\n\n"
         "Answer that message, or wait a few minutes. Then try again.")
# A take-back that stopped.
_RETRY = ("Amplifier could not finish moving your kept chats and settings back.\n\n"
          "Nothing was deleted. They can be in both of these folders:\n{root}\n{kept}\n\n"
          "Restart this PC. Then open Amplifier again to finish.")
_BLOCKED = ("Amplifier cannot finish moving your kept chats and settings back.\n\n{cause}\n\n"
            "Nothing was deleted. Your chats and settings are in both of these folders. Keep both folders. "
            "Do not uninstall Amplifier: that can delete the part that is already back.\n\n{two}")
_TWO = "Amplifier's folder:\n{root}\nKept folder:\n{kept}"
_CAUSE = {
    "link": "This is a link, and Amplifier does not follow links:\n{path}\n"
            "Remove the link. Then open Amplifier again.",
    "program-state": "This folder was put there after Amplifier kept your chats and settings:\n{path}\n"
                     "Move it to a place outside the two folders below. Then open Amplifier again.",
    "renamed": "This folder has a changed name:\n{path}\nRename it to {name}. Then open Amplifier again.",
    "too-many": "This folder holds too many files and folders for Amplifier to move back:\n{path}\n"
                "Move the ones that were added after you uninstalled Amplifier to a place outside the two "
                "folders below. Then open Amplifier again.",
    "newer": "A newer version of Amplifier began this step, and this version cannot finish it. "
             "Install that version of Amplifier, or a newer one. Then open Amplifier again.",
    "unreadable": "Amplifier cannot read the kept folder below. Restart this PC. Then open Amplifier again.",
    "changed": "The kept folder below was changed, and Amplifier cannot use it as it is now. "
               "Restart this PC. Then open Amplifier again.",
}
_RESUME = ("Amplifier did not finish moving your kept chats and settings back. The rest is in this folder:\n"
           "{kept}\n\nFinish now?\n\nYes: move the rest back.\n"
           "No: start Amplifier with the part that is already back. Amplifier will not ask again.")
# A take-back that began here, and nothing says how to go on. Only the person's Yes starts the app.
_GONE = ("Amplifier did not finish moving your kept chats and settings back, and it cannot find {them}. "
         "{they} in this folder:\n{kept}\n\nIf you moved or renamed that folder: select No, put it back, "
         "and open Amplifier again.\n\nStart Amplifier without {them}?\n\n"
         "Yes: start {how}.\nNo: do not start Amplifier now.")
_UNCLEAR = ("Amplifier did not finish moving your kept chats and settings back, and its notes about that "
            "step are missing or do not match. {they} in this folder:\n{kept}\n\nSelect No first. Restart this PC. "
            "Then open Amplifier again. If you see this message again, select Yes.\n\nStart Amplifier without {them}?"
            "\n\nYes: start {how}. That folder stays as it is.\nNo: do not start Amplifier now.")
_WORDS = {
    ("missing", True): {"them": "the rest", "they": "The rest was", "how": "with the part that is already back"},
    ("missing", False): {"them": "them", "they": "They were", "how": "fresh"},
    ("blocked", True): {"them": "the rest", "they": "The rest can still be",
                        "how": "with the part that is already back"},
    ("blocked", False): {"them": "them", "they": "They can still be", "how": "fresh"},
}
_IN_THE_WAY = ("Amplifier cannot move your kept chats and settings back, because this is in the way:\n{path}\n\n"
               "Nothing was deleted. Move it to a place outside these two folders. Then open Amplifier again."
               "{part}\n\n{two}")
_RENAMED_BACK = ("Amplifier cannot move your kept chats and settings back, because this folder has a changed "
                 "name:\n{path}\n\nNothing was deleted. Rename it to {name}. Then open Amplifier again.{part}")
_PART = " Until then, do not uninstall Amplifier: that can delete the part that is already back."
_PREPARE = ("Amplifier moved your kept chats and settings back, but it could not prepare them for this "
            "version.\n\nNothing was deleted. Restart this PC. Then open Amplifier again.\n\n"
            "If you see this message again: uninstall Amplifier and choose to keep your chats and settings. "
            "That moves them out again, unchanged.")
_LINKED = ("Amplifier moved your kept chats and settings back, but it cannot prepare this file for this "
           "version, because the file has a second name on this PC:\n{path}\n\nNothing was deleted, and "
           "Amplifier does not start with that file. To move your chats and settings out again, unchanged: "
           "uninstall Amplifier and choose to keep them.")
_CHECK = ("Amplifier could not check whether you kept chats and settings in this folder:\n{kept}\n\n"
          "Nothing was changed. Restart this PC. Then open Amplifier again.")
_UNUSABLE = ("Amplifier cannot use the chats and settings that you kept in this folder:\n{kept}\n\n"
             "Nothing was changed. Restart this PC. Then open Amplifier again.")
_ANSWER = ("Amplifier could not save your answer.\n\n"
           "Nothing was changed. Restart this PC. Then open Amplifier again.")
_OFFER = ("Amplifier found the chats and settings that you kept on {date}.\n\nUse them?\n\n"
          "Yes: use them.\nNo: start fresh. Amplifier will never offer them again. "
          "They stay in this folder:\n{kept}")
_UNREADABLE = ("Amplifier found kept chats and settings in this folder, but it cannot read them:\n{kept}\n\n"
               "Start Amplifier without them?\n\nYes: start without them. The folder stays as it is.\n"
               "No: do not start Amplifier now. Restart this PC. Then open Amplifier again.")
_WAITING = ("Kept chats and settings are waiting in this folder:\n{kept}\n\n"
            "Open Amplifier from the Start menu to choose whether to use them.")
_NAMED = "Amplifier found kept chats and settings that it does not use:\n\n{sets}\n\nThey stay where they are."
_WHY = {
    "other-folder": "They belong to Amplifier in another folder.",
    "newer": "A newer version of Amplifier ({version}) kept them, and this version cannot use them. "
             "To use them, you need Amplifier version {version} or newer in place of this one.",
    "moving": "Their move did not finish, so some chats or settings can be missing. "
              "Amplifier does not use them. Keep this folder.",
    "orphan": "Their move back into Amplifier did not finish, so a part of them can be in another place. "
              "Amplifier does not use them. Keep this folder.",
    "link": "This folder holds a link, and Amplifier does not follow links.",
    "program-state": "This folder was changed after Amplifier kept it, and Amplifier does not use it.",
    "too-many": "One of its folders holds too many files and folders for Amplifier to move back.",
    "declined": "You chose not to use them.",
    "earlier": "You kept them at an earlier uninstall.",
}

class Refused(RuntimeError):
    """A move-out or take-back that did not start, or stopped; `notice` is the person's text."""

    def __init__(self, kind, notice, error=None):
        super().__init__(kind)
        code = getattr(error, "winerror", None)
        detail = "" if error is None else f", {type(error).__name__}" + (f" {code}" if code else "")
        self.kind, self.error = kind, error
        self.notice = f"{notice}\n\nReference: kept-data, {kind}{detail}"


def kept_folder(root):
    root = Path(root)
    return root.with_name(root.name + "-kept")


def earlier_folders(root):
    """Where a complete set goes when a later uninstall keeps data again. Never offered, never deleted."""
    root = Path(root)
    return [root.with_name(f"{root.name}-kept-earlier-{number}") for number in range(1, EARLIER + 1)]


def _kind(path):
    """None, `folder`, `file` or `link`. Asks about the name itself; never follows a link."""
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:  # any reparse point
        return "link"
    return "folder" if stat.S_ISDIR(info.st_mode) else "file"


def _names(folder):
    """The exact names in the folder's own listing."""
    with os.scandir(folder) as entries:
        return sorted(entry.name for entry in entries)


def _alias(names, wanted=FOLDERS):
    """A name that differs from a wanted name only in case, else None. Windows opens both as one name."""
    return next((name for name in names if name not in wanted and name.casefold() in wanted), None)


def _private(folder, owned, *, create=False):
    """Check, or create, a real folder that only this person can use."""
    if _NATIVE:
        from .windows_files import directory
        with directory(folder, owned_root=owned, create=create):
            return
    if create:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    if _kind(folder) != "folder":
        raise PermissionError(f"Not a private folder: {folder}")


@contextmanager
def _held(folder):
    """Keep a real folder in place: while this is open, no program can rename, replace or delete it.

    Other programs can still read and write in the folder. The check that it
    is a real folder with this exact spelling is made on the open handle.
    """
    if _kind(folder) != "folder":
        raise PermissionError(f"Not a real folder: {folder}")
    if not _NATIVE:
        yield
        return
    from . import windows_files
    api = windows_files._API()
    # List access; share read and write, not delete; a folder, opened itself and never through a link.
    handle = api.CreateFileW(str(folder), 0x100081, 3, None, 3, 0x2200000, None)
    if handle in (None, windows_files._INVALID):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        api.info(handle, folder, directory=True)
        yield
    finally:
        api.close(handle)


def _lock(root):
    """One start step or move-out at a time for this installation. Raises Refused while another one runs.

    The lock file is next to the kept folder, outside the installation folder,
    so it exists in every state of an uninstall. Windows releases the lock
    when its program ends.
    """
    from filelock import FileLock, Timeout
    path = root.with_name(root.name + "-kept.lock")
    lock = FileLock(str(path), timeout=BUSY)
    try:
        lock.acquire()
    except Timeout:
        raise Refused("busy", _BUSY) from None
    return lock, path


def _unlock(lock, path):
    lock.release()
    with suppress(OSError):  # On Windows the lock removes its file. A file that a program holds stays.
        os.remove(path)


@contextmanager
def _one_at_a_time(root):
    lock, path = _lock(root)
    try:
        yield
    finally:
        _unlock(lock, path)


def _write(folder, name, value):
    """Replace one of this module's records as one complete private file. `folder` owns it."""
    if _NATIVE:
        from .deployment import write_private
        write_private(folder / name, json.dumps(value), owned_root=folder)
        return
    draft = folder / (name + ".new")
    draft.write_text(json.dumps(value), encoding="utf-8")
    os.replace(draft, folder / name)


def _raw(folder, name):
    if _NATIVE:
        from .windows_files import directory
        with directory(folder, owned_root=folder) as owned:
            return owned.read_private(name, limit=LIMIT)
    if _kind(folder / name) != "file":
        raise ValueError("The record is not a plain file.")
    return (folder / name).read_bytes()


def _forget(folder, name):
    """Delete one of this module's records: the exact private file that was read."""
    if _NATIVE:
        from .windows_files import directory
        with directory(folder, owned_root=folder) as owned:
            owned.remove_private(name, hashlib.sha256(owned.read_private(name, limit=LIMIT)).hexdigest())
    else:
        os.remove(folder / name)


def _three(version):
    """The three numbers of a version as `X.Y.Z` (a build suffix is dropped), or None."""
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version) if isinstance(version, str) else None
    return ".".join(match.groups()) if match else None


def _parts(version):
    return tuple(map(int, version.split(".")))


def read(kept):
    """`(state, record)`: absent, none (no kept data and no record), unreadable, or the record's state.

    A record that exists and cannot be read is `unreadable`, never "no kept
    data". So is a folder that holds a data folder and no record.
    """
    kind = _kind(kept)
    if kind is None:
        return "absent", None
    try:
        if kind != "folder":
            raise ValueError("The kept folder is not a real folder.")
        names = _names(kept)
        if RECORD not in names:
            if _alias(names, (RECORD,)) or any(name.casefold() in FOLDERS for name in names):
                raise ValueError("Kept data without a record.")
            return "none", None
        record = json.loads(_raw(kept, RECORD))
        if (not isinstance(record, dict)
                or not _FIELDS <= set(record) <= _FIELDS | {"declined", "into", "back", "filled"}
                or type(record.get("into", 0)) is not int
                or type(record["schema"]) is not int or record["schema"] != 1
                or not isinstance(record["root"], str) or not record["root"]
                or not isinstance(record["version"], str) or _three(record["version"]) != record["version"]
                or not isinstance(record["keptAt"], str)
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["keptAt"])
                or record["state"] not in ("moving", "complete", "adopting", "returned")
                or not isinstance(record["transfer"], str) or not re.fullmatch(_HEX32, record["transfer"])
                or record.get("declined", True) is not True):
            raise ValueError("The kept-data record is invalid.")
        back, filled = record.get("back", {}), record.get("filled", {})
        if (not isinstance(back, dict) or not isinstance(filled, dict) or not set(back) | set(filled) <= set(FOLDERS)
                or not all(type(number) is int for number in back.values())
                or not all(isinstance(names, dict) and all(isinstance(name, str) and type(number) is int
                                                           for name, number in names.items())
                           for names in filled.values())):
            raise ValueError("The kept-data record is invalid.")
        time.strptime(record["keptAt"], "%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, RecursionError):
        return "unreadable", None
    return record["state"], record


def _reason(root, kept, version):
    """`(reason, record)`: `use` when every check of a first offer passes, else why not.

    Every continuation of an unfinished transfer runs these same checks.
    """
    state, record = read(kept)
    if record is None:
        return ("unreadable" if state == "unreadable" else None), None
    if record["root"] != str(root):  # Exact spelling: the launcher accepts its folder in no other spelling.
        return "other-folder", record
    if state in ("moving", "returned"):
        return state, record
    try:
        _private(kept, kept)
        names = _names(kept)
        if _alias(names) is not None:
            return "unreadable", record
        for name in names:
            if name == RECORD:
                continue
            kind = _kind(kept / name)
            if kind == "link":
                return "link", record
            if name in FOLDERS:
                if kind != "folder":
                    return "unreadable", record
                _private(kept / name, kept)
        if any(_kind(kept / "app" / name) is not None for name in PROGRAM_STATE):
            return "program-state", record
        # A folder that must be filled entry by entry has its entry names saved in the record first.
        large = sum(len(json.dumps(entries)) + 24 * len(entries)
                    for entries in (_names(kept / name) for name in FOLDERS if name in names)) > NAMES
    except OSError:
        return "unreadable", record
    if _parts(record["version"]) > _parts(version):
        return "newer", record
    if record.get("declined"):
        return "declined", record
    return ("too-many" if large else "use"), record


def _used(root):
    """The first data folder of this installation that holds anything, or has a changed name, else None."""
    names = _names(root)
    alias = _alias(names)
    if alias is not None:
        return root / alias
    return next((root / name for name in FOLDERS
                 if name in names and (_kind(root / name) != "folder" or _names(root / name))), None)


def _fresh(root):
    """A first start: every data folder of this installation is absent, or a real and empty folder."""
    return _used(root) is None


def _identity(path):
    """The file number of an entry on its disk. A rename on one disk keeps it; no other entry has it."""
    return os.lstat(path).st_ino


def _foreign(planned, source, target):
    """The first entry in `target` that this take-back did not move there from `source`, else None.

    `planned` has, for each entry that the kept folder had before the fill,
    its name and its identity. An entry counts as moved only with both:
    another file or folder that got the same name is not it. An entry that
    was removed is not in the way.
    """
    waiting = set(_names(source)) if _kind(source) == "folder" else set()
    return next((target / entry for entry in _names(target)
                 if entry in waiting or planned.get(entry) != _identity(target / entry)), None)


def _obstacle(root, kept, record):
    """The first entry of this installation that is not this take-back's own, else None.

    During a take-back every data folder of the installation is one of these:
    empty; the folder that this take-back moved there whole (the saved
    identity); a folder that this take-back fills (only entries with a saved
    identity, and none whose name still waits in the kept folder).
    """
    names = _names(root)
    alias = _alias(names)
    if alias is not None:
        return root / alias
    back, filled = record.get("back", {}), record.get("filled", {})
    for name in FOLDERS:
        if name not in names:
            continue
        target = root / name
        if _kind(target) != "folder":
            return target  # A link or a file with a data folder's name is never used or passed over.
        if name in filled:
            foreign = _foreign(filled[name], kept / name, target)
            if foreign is not None:
                return foreign
        elif _kind(kept / name) is None:
            if back.get(name) != _identity(target) and _names(target):
                return target  # Another folder took the place of the one that came back.
        elif _names(target):
            return target / _names(target)[0]  # Its kept folder still waits: this one must be empty.
    # And every kept data folder is the one that this take-back saw at its start.
    return next((kept / name for name in FOLDERS
                 if _kind(kept / name) == "folder" and back.get(name) != _identity(kept / name)), None)


def _fault(kept, reason):
    """Why a take-back that began cannot go on: the cause, its path, and the step that repairs it."""
    try:
        names = _names(kept)
        alias = _alias(names)
        if reason == "link":
            return _CAUSE["link"].format(path=next(kept / name for name in names if _kind(kept / name) == "link"))
        if reason == "program-state":
            return _CAUSE["program-state"].format(path=next(
                kept / "app" / name for name in PROGRAM_STATE if _kind(kept / "app" / name) is not None))
        if reason == "unreadable" and alias is not None:
            return _CAUSE["renamed"].format(path=kept / alias, name=alias.casefold())
        if reason == "too-many":
            return _CAUSE["too-many"].format(path=max(
                (kept / name for name in FOLDERS if name in names), key=lambda folder: len(_names(folder))))
    except (OSError, ValueError, StopIteration):
        pass
    return _CAUSE[reason if reason in ("newer", "unreadable") else "changed"]


def _marker(root):
    """The installation's own note of a take-back: None, the note, or `unreadable`."""
    names = _names(root)
    if MARKER not in names:
        return "unreadable" if _alias(names, (MARKER,)) else None
    try:
        note = json.loads(_raw(root, MARKER))
        if (not isinstance(note, dict)
                or not {"schema", "transfer", "kept"} <= set(note) <= {
                    "schema", "transfer", "kept", "done", "left", "prepare"}
                or any(note.get(flag, True) is not True for flag in ("left", "prepare"))
                or type(note["schema"]) is not int or note["schema"] != 1
                or not isinstance(note["transfer"], str) or not re.fullmatch(_HEX32, note["transfer"])
                or note["kept"] != str(kept_folder(root)) or note.get("done", True) is not True):
            raise ValueError("The take-back note is invalid.")
    except (OSError, ValueError, RecursionError):
        return "unreadable"
    return note


def _drop_marker(root):
    """Remove the note of a finished take-back. Never raises: a note that stays is tidied at the next start."""
    try:
        if MARKER in _names(root):
            _forget(root, MARKER)
    except (OSError, ValueError, RuntimeError):
        pass


def _returning(root, kept):
    """A take-back in this installation: None, or one of these.

    `done`: it finished (the record says `returned`, or the note says
    `done`); only tidying is left.
    `prepare`: the person chose to start without the rest (`left`), and the
    part that is back is not prepared for this version yet. With `left` this
    installation continues no take-back by itself.
    `finish`: the record says how to go on, and it is this installation's: an
    `adopting` record names the identity of this installation folder, and the
    note names the same transfer; or a `complete` record has the note of the
    same transfer (the take-back wrote its note and stopped before the record).
    `confirm`: an `adopting` record names this installation folder, and the
    note is gone or cannot be read. Nothing tells whether the person chose to
    start without the rest: the person is asked.
    `missing`: the note is there and the kept folder is not. `blocked`: the
    note is there and the record does not say how to go on.
    With `finish`, `confirm`, `missing` and `blocked` the installation can
    hold a part of the kept data: the app never starts by itself, and an
    uninstall does not begin.
    """
    marker = _marker(root)
    noted = isinstance(marker, dict)
    state, record = read(kept)
    mine = record is not None and record["root"] == str(root)
    if (mine and state == "returned") or (noted and marker.get("done")):
        return "done"
    if noted and marker.get("left"):
        return "prepare" if marker.get("prepare") else None
    if mine and state == "adopting" and record.get("into") == _identity(root):
        if noted and marker["transfer"] == record["transfer"]:
            return "finish"
        if not noted:
            return "confirm"
    if mine and state == "complete" and noted and marker["transfer"] == record["transfer"]:
        return "finish"
    if marker is None:
        return None
    return "missing" if state == "absent" else "blocked"


def unfinished_take_back(root):
    """Whether this installation can hold a part of kept data: a take-back began and did not finish.

    For an uninstall that deletes the installation folder: it must not begin
    while this is true. Reads only. A note that says `done` is not such a state.
    """
    root = Path(root)
    return _returning(root, kept_folder(root)) in ("finish", "confirm", "missing", "blocked")


def _finished(root, kept):
    """Tidy after a take-back that finished: its record, the empty kept folder, then the note.

    Never raises and never opens a database: the app can be running by now.
    Each file is read once. Every step that fails keeps the proof that the
    take-back finished: first the note says `done`, then the record goes,
    then the note goes. A note stays while a record cannot be read.
    """
    try:
        marker = _marker(root)
        state, record = read(kept)
        if state == "unreadable":
            return
        if state == "returned" and record["root"] == str(root):
            if marker is not None and not (isinstance(marker, dict) and marker.get("done")):
                _write(root, MARKER, {"schema": 1, "transfer": record["transfer"], "kept": str(kept), "done": True})
            if any(name.casefold() in FOLDERS for name in _names(kept)):
                return  # A data folder is beside that record: nothing is removed.
            _forget(kept, RECORD)
            with suppress(OSError):  # It holds something that is not Amplifier's: it stays.
                os.rmdir(kept)
        elif not (isinstance(marker, dict) and marker.get("done")):
            return
        _drop_marker(root)
    except (OSError, ValueError, RuntimeError):
        return


def _tidy(root, kept):
    if _returning(root, kept) == "done":
        _finished(root, kept)


def _version(version):
    three = _three(version)
    if three is None:
        raise ValueError("A kept set needs an app version.")
    return three


def used_version(root, version):
    """The newest app version that used this data: the launcher's, or its active app update's."""
    version = _version(version)
    home = Path(root) / "app"
    pointer = home / "updates" / "application-active.json"
    if _kind(home) != "folder" or _kind(home / "updates") != "folder" or _kind(pointer) is None:
        return version  # Nothing to read. A link on the way is never read through.
    try:
        if _kind(pointer) != "file" or pointer.stat().st_size > LIMIT:
            raise ValueError
        active = _version(json.loads(pointer.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("The active app version is not readable.") from error
    return max(version, active, key=_parts)


def _plan(root, version, transfer):
    """What a move-out does first: `new`, `continue`, `take-back` or `aside`. Reads only; raises Refused."""
    kept = kept_folder(root)
    alias = _alias(_names(root))
    if alias is not None:
        raise Refused("kept-name", _RENAMED.format(path=root / alias, name=alias.casefold()))
    occupied = Refused("kept-folder", _OCCUPIED.format(kept=kept))
    returning = _returning(root, kept)
    if returning in ("confirm", "missing", "blocked"):
        raise Refused("take-back-record", _RETURNING)
    reason, record = _reason(root, kept, version)
    if returning == "finish":
        if reason != "use":
            raise Refused("take-back-" + (reason or "missing"), _RETURNING)
        return "take-back", record
    if record is None:
        if reason == "unreadable":
            raise occupied
        if _kind(kept) is not None:
            try:
                usable = _kind(kept) == "folder" and not _names(kept)
                _private(kept, kept)
            except OSError:
                usable = False
            if not usable:
                raise occupied
        return "new", None
    if reason == "other-folder":
        raise occupied
    if record["transfer"] == transfer and record["state"] in ("moving", "complete"):
        # This uninstall's own set, also when it is already complete: go on with it, never set it aside.
        try:
            _private(kept, kept)
        except OSError:
            raise Refused("kept-unsafe", _STUCK.format(kept=kept, stayed="")) from None
        return "continue", record
    if record["state"] != "complete":
        raise occupied  # Another transfer's unfinished set holds other data: never add to it or rename it.
    # Only a complete set is ever set aside. It is renamed whole and never changed.
    free = next((folder for folder in earlier_folders(root) if _kind(folder) is None), None)
    if free is None:
        raise Refused("kept-full", _FULL.format(kept=kept))
    return "aside", free


def check(root, version, transfer=None):
    """Raise Refused when a move-out cannot start. Moves nothing; call it before the first change.

    `transfer` is the identity in the journal of an uninstall that continues,
    else None. The only thing that it can remove is the note and the record of
    a take-back that had finished.
    """
    root, version = Path(root), _version(version)
    try:
        with _one_at_a_time(root):
            _tidy(root, kept_folder(root))
            _plan(root, version, transfer)
    except Refused:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise Refused("check", _STUCK.format(kept=kept_folder(root), stayed=""), error) from error


def _move_entries(source, target):
    """Move the entries of `source` into `target`; both are real folders and are held in place meanwhile.

    An entry never replaces an entry. Where both sides have a real folder of
    one name, its entries move in the same way and the emptied folder is
    removed: a program that closes late can write into a folder again whose
    first part already moved. A file or a link with a name that the target
    has stays where it is. A link is never entered.
    """
    with _held(source), _held(target):
        for name in _names(source):
            kind = _kind(target / name)
            if kind is None:
                with suppress(OSError):
                    os.rename(source / name, target / name)
            elif kind == "folder" and _kind(source / name) == "folder":
                with suppress(OSError):
                    _move_entries(source / name, target / name)
                    os.rmdir(source / name)


def _stayed(folder):
    """The first entry that is still below `folder`, as deep as it can be named; never through a link."""
    while _kind(folder) == "folder":
        names = _names(folder)
        if not names:
            break
        folder = folder / names[0]
    return folder


def _move(source, target, owned):
    """Rename a whole folder; if Windows refuses, move its entries one by one into its moved place.

    Returns None when the source is gone or empty, else what stayed: the
    folder, or the first entry that is still in it. The source can still get
    a file from a closing program, so its entries are not fixed in advance as
    in a take-back; nothing but this move writes into the target, which is
    this transfer's own set. "Empty" is decided by a listing that is made last.
    """
    kind = _kind(target)
    if kind is None:
        try:
            os.rename(source, target)
            return None if _kind(target) == "folder" else target  # A link that took the folder's place moved.
        except OSError:
            pass  # A program's current folder, or a closing program: move what can move.
    elif kind != "folder":
        return target
    try:
        _private(target, owned, create=kind is None)  # Also an existing one: this person's own real folder.
        _move_entries(source, target)
        with suppress(OSError):  # Another program's current folder: it stays, empty.
            os.rmdir(source)
        if _kind(source) is None:
            return None
        return _stayed(source) if _names(source) else None
    except OSError:
        return source


def _stuck(root, kept, stayed):
    """The stop text of a move-out: what stayed, and the step that can cure it."""
    if not stayed:
        return _STUCK.format(kept=kept, stayed="")
    path = stayed[0]
    if path.parent == kept:
        return _KEPT_ENTRY.format(path=path)  # A file or a link where the set needs a folder.
    if _kind(path) == "link":
        return _LINK_STAYED.format(path=path)
    if root in path.parents:
        # Below a data folder: the same name on both sides, and not two real folders. A program that
        # closed late wrote it again. A top-level data folder itself is never named here: it must move.
        parts = path.relative_to(root).parts
        for depth in range(2, len(parts) + 1):
            here, there = root.joinpath(*parts[:depth]), kept.joinpath(*parts[:depth])
            if _kind(there) is not None and (_kind(here), _kind(there)) != ("folder", "folder"):
                return _BOTH.format(path=here, twin=there)
    return _STUCK.format(kept=kept, stayed=_STAYED.format(path=path))


def move_out(root, *, version, transfer, remove=None, wait=WAIT):
    """Move the data folders of a stopped installation into `<root>-kept`.

    The caller stopped the app, proved the folder, ran `check` and saved
    `transfer` in its own journal; a repeated call passes the same value.
    `version` is `used_version`. `remove` is the caller's link-safe deleter
    for the program state inside the moved data.

    The set is `complete` only when every data folder is moved (or empty) and
    no program state is left in it. Otherwise it is `moving` and the same call
    continues it. `left` names entries that are not real folders (a link, a
    file); they stay where they are. `stayed` names what could not move.
    Every refusal and every stop is `Refused`.

    After `complete` the caller removes a data folder of the installation only
    when it is empty: a program that closes late can still write into it.
    """
    root, kept, version = Path(root), kept_folder(root), _version(version)
    if not isinstance(transfer, str) or not re.fullmatch(_HEX32, transfer):
        raise ValueError("A kept set needs a transfer identity.")
    try:
        with _one_at_a_time(root):
            return _move_out(root, kept, version, transfer, remove, wait)
    except Refused:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise Refused("move-out", _STUCK.format(kept=kept, stayed=""), error) from error


def _move_out(root, kept, version, transfer, remove, wait):
    _tidy(root, kept)
    action, detail = _plan(root, version, transfer)
    if action == "take-back":  # Finish the other direction first, then begin.
        try:
            take_back(root, version, settle=False)  # The data leaves again; the next take-back prepares it.
        except Refused as refused:
            raise Refused(refused.kind, _RETURNING, refused.error) from None
        action, detail = _plan(root, version, transfer)
    if action == "aside":
        os.rename(kept, detail)
        action = "new"
    if action == "new":
        _private(kept, kept, create=True)
        record = {"schema": 1, "root": str(root), "version": version, "state": "moving", "transfer": transfer,
                  "keptAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write(kept, RECORD, record)
    elif action == "continue":
        record = detail
    else:
        raise Refused("kept-folder", _OCCUPIED.format(kept=kept))
    here = Path.cwd()
    if here == root or root in here.parents:
        os.chdir(os.environ.get("SystemRoot") or root.anchor)  # This program's own current folder.
    deadline = time.monotonic() + wait
    with _held(root), _held(kept):
        while True:
            present, left, remaining, stayed = set(_names(root)), [], [], []
            for name in FOLDERS:
                if _kind(kept / name) not in (None, "folder"):
                    remaining.append(name)  # Not a real folder in the kept set: the set is never complete.
                    stayed.append(kept / name)
                elif name not in present:
                    continue
                elif _kind(root / name) != "folder":
                    left.append(name)  # A link, or a file: it stays and is reported.
                else:
                    what = _move(root / name, kept / name, kept)
                    if what is not None:
                        remaining.append(name)
                        stayed.append(what)
            if not remaining or time.monotonic() >= deadline:
                break
            time.sleep(0.5)  # A closing program releases its files a moment after it stops.
        if not remaining:
            # A closing program can write once more after its folder moved: look again before `complete`.
            for name in FOLDERS:
                if _kind(root / name) == "folder" and _names(root / name):
                    remaining.append(name)
                    stayed.append(_stayed(root / name))
    app = _kind(kept / "app")
    if app not in (None, "folder"):
        state = list(PROGRAM_STATE)  # Not a real folder: never entered, never complete.
    else:
        state = [name for name in PROGRAM_STATE if app and _kind(kept / "app" / name) is not None]
        if state and not remaining and remove is not None:
            for name in state:
                if _kind(kept / "app" / name) == "folder":
                    remove(kept / "app" / name)
            state = [name for name in state if _kind(kept / "app" / name) is not None]
    done = not remaining and not state
    newest = max(record["version"], version, key=_parts)
    if done and (record["state"] != "complete" or record["version"] != newest):
        _write(kept, RECORD, {**record, "state": "complete", "version": newest})
    elif not done and record["state"] != "moving":
        _write(kept, RECORD, {**record, "state": "moving"})  # A repeated run found something new to move.
    if done:
        _drop_marker(root)  # The data left: a note of an earlier take-back says nothing any more.
    if not remaining:
        stayed += [kept / "app" / name for name in state]  # Only the program state keeps the set unfinished.
    return {"kept": str(kept), "state": "complete" if done else "moving", "transfer": record["transfer"],
            "left": left, "remaining": remaining, "programState": state, "stayed": [str(path) for path in stayed],
            **({} if done else {"notice": _stuck(root, kept, stayed)})}


def _settle(root, transfer):
    """Retire the removed installation's saved update status, as the maintained repair does.

    An update that did not finish leaves a status in the database that stops
    every new message until that exact app proves itself; that app is gone.
    `reset.reset_update_state` saves the database first and removes only this
    status. Each attempt saves into its own new folder. Returns `retired`,
    `none` (no database), `linked` (the file has a second name somewhere, so
    it is also another file's content: never changed) or `unchanged` (it
    failed; the caller tries again at the next start).
    """
    home = root / "app"
    if _kind(home / "app.sqlite3") != "file":
        return "none"
    backup = None
    try:
        if os.stat(home / "app.sqlite3").st_nlink != 1:
            return "linked"
        from .reset import reset_update_state
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = home / "reset-backups" / f"kept-{transfer}-{stamp}-{os.urandom(4).hex()}"
        if _kind(backup) is not None:
            backup = None
            return "unchanged"  # Never write over a database that was saved earlier.
        _private(backup, root, create=True)
        reset_update_state(home, backup)
        return "retired"
    except (OSError, ValueError, sqlite3.Error):
        if backup is not None:
            with suppress(OSError):  # An attempt that saved nothing leaves no folder.
                os.rmdir(backup)
        return "unchanged"


def _rename(source, target, deadline):
    """Rename a kept folder into its place; wait for a program that holds it for a moment."""
    while True:
        try:
            os.rename(source, target)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def take_back(root, version, *, settle=True, confirmed=False):
    """Move the kept set of this folder back into this installation's empty data folders.

    The caller holds the installation's lock (`first_start`, `move_out`).
    Runs every check each time, also when it continues an interrupted
    take-back. Before the first change, every data folder of the installation
    must be empty, or be this take-back's own. When Windows refuses to remove
    an empty folder (the current folder of the launcher), the kept entries are
    moved into it one by one. The record first names every folder and entry
    that moves, with its identity.
    The lock does not stop a program that is not Amplifier; the checks do.

    Last it retires the removed program's saved update status. If that fails,
    the take-back is not finished and the next start tries again. A move-out
    passes `settle=False`: the data leaves again at once. Then the record says
    `returned`: from there only tidying is left.
    `confirmed`: the person said to finish a take-back whose note is gone.
    """
    root, kept = Path(root), kept_folder(root)
    try:
        return _take_back(root, kept, _version(version), settle, confirmed)
    except Refused:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise Refused("take-back", _RETRY.format(root=root, kept=kept), error) from error


def _take_back(root, kept, version, settle, confirmed):
    reason, record = _reason(root, kept, version)
    began = record is not None and record["state"] == "adopting"
    two = _TWO.format(root=root, kept=kept)
    if reason != "use":
        if not began:  # Nothing is back, and nothing was changed.
            raise Refused("take-back-" + (reason or "missing"), _UNUSABLE.format(kept=kept))
        raise Refused("take-back-" + reason, _BLOCKED.format(cause=_fault(kept, reason), two=two))
    note = {"schema": 1, "transfer": record["transfer"], "kept": str(kept)}
    if began:
        returning = _returning(root, kept)
        if returning == "confirm" and confirmed:
            _write(root, MARKER, note)  # The person said to finish it: the installation's note is there again.
        elif returning != "finish":
            # Its way back began into another installation folder, or the person chose to start without it.
            raise Refused("take-back-orphan", _UNUSABLE.format(kept=kept))
    part = [_PART if began else ""]

    def stop(path, kind="take-back-not-empty"):
        if path.parent == root and path.name not in FOLDERS and path.name.casefold() in FOLDERS:
            return Refused(kind, _RENAMED_BACK.format(path=path, name=path.name.casefold(), part=part[0]))
        return Refused(kind, _IN_THE_WAY.format(path=path, part=part[0], two=two))

    # Before the first change, also of a continued take-back: never merge, never delete.
    blocker = _obstacle(root, kept, record) if began else _used(root)
    if blocker is not None:
        raise stop(blocker)
    taken, deadline = [], time.monotonic() + WAIT
    with _held(root), _held(kept):
        if not began:
            # The installation's own note first: from here it can hold a part of the kept data.
            _write(root, MARKER, note)
            record = {**record, "state": "adopting", "into": _identity(root), "back": {
                name: _identity(kept / name) for name in FOLDERS if _kind(kept / name) == "folder"}}
            _write(kept, RECORD, record)
            part[0] = _PART
        back, present = record.get("back", {}), set(_names(root))
        for name in FOLDERS:
            source, target = kept / name, root / name
            if _kind(source) is None:
                continue
            kind = _kind(target) if name in present else None
            if kind == "folder" and not _names(target):
                try:
                    os.rmdir(target)
                    kind = None
                except OSError:
                    pass  # A current folder: fill it instead.
            if kind is None:
                if back.get(name) != _identity(source):  # It came into the kept folder later.
                    raise stop(source)
                _rename(source, target, deadline)
                if _kind(target) != "folder" or _identity(target) != back[name]:
                    raise stop(target, "take-back-link")  # A link, or another folder, took its place and moved.
            elif kind != "folder":
                raise stop(target)
            else:
                if name not in record.get("filled", {}):
                    if _names(target):
                        raise stop(target)
                    record = {**record, "filled": {**record.get("filled", {}), name: {
                        entry: _identity(source / entry) for entry in _names(source)}}}
                    _write(kept, RECORD, record)
                planned = record["filled"][name]
                with _held(source), _held(target):
                    foreign = _foreign(planned, source, target)
                    if foreign is not None:  # Something else is in that folder.
                        raise stop(foreign)
                    for entry in _names(source):
                        if planned.get(entry) != _identity(source / entry):  # It came into the kept folder later.
                            raise stop(source / entry)
                        if _kind(target / entry) is not None:  # An entry never replaces an entry.
                            raise stop(target / entry)
                        os.rename(source / entry, target / entry)
                    # Once more after the last entry: only what this take-back moved is in that folder.
                    late = next((entry for entry in _names(target)
                                 if planned.get(entry) != _identity(target / entry)), None)
                    if late is not None:
                        raise stop(target / late)
                    rest = _names(source)
                    if rest:  # It came into the kept folder during the moves.
                        raise stop(source / rest[0])
                if _kind(source) == "folder":
                    os.rmdir(source)
            taken.append(name)
    settled = _settle(root, record["transfer"]) if settle else "skipped"
    if settled == "unchanged":
        raise Refused("take-back-prepare", _PREPARE)  # Record and note stay: the next start tries again.
    if settled == "linked":
        raise Refused("take-back-prepare", _LINKED.format(path=root / "app" / "app.sqlite3"))
    _write(kept, RECORD, {**record, "state": "returned"})  # Finished. A stop after this line leaves only tidying.
    with suppress(OSError, ValueError, RuntimeError):  # The tidy step writes it again when this fails.
        _write(root, MARKER, {**note, "done": True})
    _finished(root, kept)
    return {"taken": taken, "updateState": settled}


def _prepared(root, kept):
    """Prepare the part that is back, after the person chose to start without the rest. Raises Refused."""
    marker = _marker(root)
    transfer = marker["transfer"] if isinstance(marker, dict) else "0" * 32
    settled = _settle(root, transfer)
    if settled == "unchanged":
        raise Refused("take-back-prepare", _PREPARE)
    if settled == "linked":
        raise Refused("take-back-prepare", _LINKED.format(path=root / "app" / "app.sqlite3"))
    try:
        _write(root, MARKER, {"schema": 1, "transfer": transfer, "kept": str(kept), "left": True})
    except (OSError, ValueError, RuntimeError) as error:
        raise Refused("take-back", _RETRY.format(root=root, kept=kept), error) from error


def _abandon(root, kept):
    """The person chose to start without the rest of a take-back that did not finish. Raises Refused.

    The choice is saved first: in the installation's note, and, when the kept
    record can be read, in that record (it then names no installation folder,
    so no installation continues it). Only then the part that is back is
    prepared. If that fails, this start stops; the choice stays, the next
    start prepares again, and an uninstall can begin.
    """
    marker = _marker(root)
    state, record = read(kept)
    mine = record is not None and record["root"] == str(root) and state == "adopting"
    transfer = marker["transfer"] if isinstance(marker, dict) else record["transfer"] if mine else "0" * 32
    part = not _fresh(root)
    note = {"schema": 1, "transfer": transfer, "kept": str(kept), "left": True}
    try:
        _write(root, MARKER, {**note, "prepare": True} if part else note)
        if mine and "into" in record:
            _write(kept, RECORD, {key: value for key, value in record.items() if key != "into"})
    except (OSError, ValueError, RuntimeError) as error:
        raise Refused("take-back", _RETRY.format(root=root, kept=kept), error) from error
    if part:
        _prepared(root, kept)


def _date(kept_at):
    moment = time.localtime(calendar.timegm(time.strptime(kept_at, "%Y-%m-%dT%H:%M:%SZ")))
    return f"{calendar.month_name[moment.tm_mon]} {moment.tm_mday}, {moment.tm_year}"


def first_start(root, version, *, ask, hold=False):
    """The shortcut's step before anything writes into the data folders.

    Returns None when the app can start, or a small result when it must not
    start now. `version` is the app that this launcher starts. `ask` is false
    for a caller without a person: it gets a result, never a question.

    `hold` is true for the launcher itself. When the app then starts on empty
    data folders while kept data exists, the lock stays until this program
    ends: no second launcher can ask, or take anything back, before this
    start has written its first file.
    """
    root, kept = Path(root), kept_folder(root)
    version = _three(version) or "0.0.0"  # A version that cannot be compared uses no kept set.
    from .windows_installation import owner_notice

    def waiting(kind, notice, *, show=True):
        if ask and show:
            owner_notice(notice)
        return {"taken": False, "reason": kind, "message": notice}

    taken = []
    try:
        if (_kind(kept) is None and not any(name.casefold() == MARKER for name in _names(root))
                and not any(_kind(folder) is not None for folder in earlier_folders(root))):
            return None  # The usual start: nothing was kept.
        lock, path = _lock(root)
        staying = False
        try:
            result = _first(root, kept, version, ask, owner_notice, waiting, taken)
            staying = bool(hold and result is None and _fresh(root))
            if staying:
                _STAYING.append(lock)
            return result
        finally:
            if not staying:
                _unlock(lock, path)
    except Refused as refused:
        return waiting(refused.kind, refused.notice)
    except (OSError, ValueError, RuntimeError) as error:
        if taken:
            return None  # The kept data is back. Only a message or a check after that failed: the app starts.
        return waiting("check", Refused("check", _CHECK.format(kept=kept), error).notice)


def _first(root, kept, version, ask, owner_notice, waiting, taken):
    returning = _returning(root, kept)
    if returning == "done":
        _finished(root, kept)  # Only tidying is left. The app can be running now: no database is opened.
        return None
    if returning == "prepare":
        _prepared(root, kept)  # The person chose to start without the rest; the first try did not work.
        return None
    if returning == "finish":
        take_back(root, version)  # The person already said Yes. Never start before it is finished.
        taken.append(True)
        return None
    if returning == "confirm":
        # The record names this installation folder, and the installation's own note is gone: ask.
        notice = _RESUME.format(kept=kept)
        if not ask:
            return waiting("take-back-record", notice, show=False)
        if owner_notice(notice, confirm=True):
            take_back(root, version, confirmed=True)
            taken.append(True)
        else:
            _abandon(root, kept)
        return None
    if returning in ("missing", "blocked"):
        # A take-back began here, and nothing says how to go on: the kept folder is gone, or the records
        # do not match. Nothing more comes back by itself. Only the person's Yes starts the app.
        notice = (_GONE if returning == "missing" else _UNCLEAR).format(
            kept=kept, **_WORDS[returning, not _fresh(root)])
        if not ask or not owner_notice(notice, confirm=True):
            return waiting("take-back-record", notice, show=False)
        _abandon(root, kept)
        return None
    if not _fresh(root):
        return None  # This installation has its own data. Kept sets stay where they are.
    reason, record = _reason(root, kept, version)
    if reason == "use" and record["state"] != "complete":
        reason = "orphan"  # Its way back began into another installation folder: never continued, never offered.
    if reason == "unreadable":
        notice = _UNREADABLE.format(kept=kept)
        if not ask or not owner_notice(notice, confirm=True):
            return waiting("unreadable", notice, show=False)
    elif reason == "use":
        if not ask:
            return waiting("question", _WAITING.format(kept=kept), show=False)
        if owner_notice(_OFFER.format(date=_date(record["keptAt"]), kept=kept), confirm=True):
            take_back(root, version)
            taken.append(True)
        else:
            try:  # The answer is saved with the set before the app starts: no installation asks again.
                _write(kept, RECORD, {**record, "declined": True})
            except (OSError, ValueError, RuntimeError) as error:
                raise Refused("answer", _ANSWER, error) from error
    # Every kept set that is not offered is named once, at this first start.
    named = [f"{kept}\n{_WHY[reason].format(version=record['version'])}"] if reason in _WHY else []
    named += [f"{folder}\n{_WHY['earlier']}" for folder in earlier_folders(root) if _kind(folder) is not None]
    if named and ask:
        owner_notice(_NAMED.format(sets="\n\n".join(named)))
    return None
