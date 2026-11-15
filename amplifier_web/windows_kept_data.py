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
finished). `<root>\\amplifier-kept-return.json` is the installation's own note
of a take-back: without `done` the installation can hold a part of the kept
data and the app must not start; with `done` only tidying is left.
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
          "Nothing was deleted. Restart this PC. Then uninstall again.")
_STAYED = "This stayed behind:\n{path}\n\n"
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
            "Nothing was deleted. They are in both of these folders. Keep both folders. "
            "Do not uninstall Amplifier: that can delete the part that is already back.\n{root}\n{kept}")
_CAUSE = {
    "link": "The second folder below holds a link, and Amplifier does not follow links. "
            "Remove the link from that folder. Then open Amplifier again.",
    "changed": "The second folder below was changed, and Amplifier cannot use it as it is now. "
               "Restart this PC. Then open Amplifier again.",
}
_MISSING = ("Amplifier did not finish moving your kept chats and settings back, and it cannot find the rest. "
            "They were in this folder:\n{kept}\n\nIf you moved or renamed that folder: select No, put it "
            "back, and open Amplifier again.\n\nStart Amplifier without the rest?\n\n"
            "Yes: start with the part that is already back.\nNo: do not start Amplifier now.")
_DAMAGED = ("Amplifier did not finish moving your kept chats and settings back, and it cannot read its notes "
            "about that step.\n\nNothing was deleted. Restart this PC. Then open Amplifier again.\n\n"
            "If you see this message again, keep both of these folders. Do not uninstall Amplifier: "
            "that can delete the part that is already back.\n{root}\n{kept}")
_IN_THE_WAY = ("Amplifier cannot finish moving your kept chats and settings back, because this is in the way:\n"
               "{path}\n\nNothing was deleted. Move it to another place, or rename it. Then open Amplifier "
               "again. Until then, do not uninstall Amplifier: that can delete a part that is already back.")
_PREPARE = ("Amplifier moved your kept chats and settings back, but it could not prepare them for this "
            "version.\n\nNothing was deleted. Restart this PC. Then open Amplifier again.")
_CHECK = ("Amplifier could not check the chats and settings that you kept in this folder:\n{kept}\n\n"
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
             "To use them: uninstall this Amplifier and choose to delete everything. That deletes the chats "
             "that you make in this Amplifier from now on. Your kept chats in the folder above stay. "
             "Then install Amplifier version {version} or newer.",
    "moving": "Their move did not finish, so some chats or settings can be missing. "
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
        if (not isinstance(record, dict) or not _FIELDS <= set(record) <= _FIELDS | {"entries", "declined"}
                or type(record["schema"]) is not int or record["schema"] != 1
                or not isinstance(record["root"], str) or not record["root"]
                or not isinstance(record["version"], str) or _three(record["version"]) != record["version"]
                or not isinstance(record["keptAt"], str)
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["keptAt"])
                or record["state"] not in ("moving", "complete", "adopting")
                or not isinstance(record["transfer"], str) or not re.fullmatch(_HEX32, record["transfer"])
                or record.get("declined", True) is not True):
            raise ValueError("The kept-data record is invalid.")
        if "entries" in record:
            entries = record["entries"]
            if (not isinstance(entries, dict) or set(entries) != {"folder", "names"}
                    or entries["folder"] not in FOLDERS or not isinstance(entries["names"], list)
                    or not all(isinstance(name, str) for name in entries["names"])):
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
    if state == "moving":
        return "moving", record
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
        large = any(len(json.dumps(_names(kept / name))) > NAMES for name in FOLDERS if name in names)
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


def _foreign(record, name, source, target):
    """The first entry in `target` that this take-back did not move there from `source`, else None.

    The record names the one folder that is filled entry by entry and the
    entries that the kept folder had at the start. An entry that was removed
    since then is not in the way.
    """
    here, entries = _names(target), record.get("entries")
    if not here:
        return None
    if not entries or entries["folder"] != name:
        return target / here[0]
    planned = set(entries["names"])
    waiting = set(_names(source)) if _kind(source) == "folder" else set()
    return next((target / entry for entry in here if entry not in planned or entry in waiting), None)


def _obstacle(root, kept, record):
    """The first entry of this installation that a kept folder would join or replace, else None."""
    names = _names(root)
    alias = _alias(names)
    if alias is not None:
        return root / alias
    for name in FOLDERS:
        if name not in names:
            continue
        if _kind(root / name) != "folder":
            return root / name  # A link or a file with a data folder's name is never used or passed over.
        if _kind(kept / name) is None:
            continue
        foreign = _foreign(record, name, kept / name, root / name)
        if foreign is not None:
            return foreign
    return None


def _marker(root):
    """The installation's own note of a take-back: None, the note, or `unreadable`."""
    names = _names(root)
    if MARKER not in names:
        return "unreadable" if _alias(names, (MARKER,)) else None
    try:
        note = json.loads(_raw(root, MARKER))
        if (not isinstance(note, dict)
                or not {"schema", "transfer", "kept"} <= set(note) <= {"schema", "transfer", "kept", "done"}
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
    """A take-back in this installation: None, `finish`, `done`, `missing` or `blocked`.

    `done`: the note says that the take-back finished; only tidying is left.
    `finish`: the record says how to go on; a readable note must name the
    record's transfer. `missing`: the note is there and the kept folder is
    not. `blocked`: the note is there and the record does not say how to go on.
    With `missing` and `blocked` the installation can hold a part of the kept
    data: the app must not start and an uninstall must not begin.
    """
    marker = _marker(root)
    noted = isinstance(marker, dict)
    if noted and marker.get("done"):
        return "done"
    state, record = read(kept)
    if (record is not None and record["root"] == str(root)
            and (not noted or marker["transfer"] == record["transfer"])
            and (state == "adopting" or (state == "complete" and noted))):
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
    return _returning(root, kept_folder(root)) in ("finish", "missing", "blocked")


def _leftover(root, kept):
    """Whether the record of a take-back whose note says `done` is still in the kept folder."""
    marker = _marker(root)
    state, record = read(kept)
    return bool(state == "adopting" and isinstance(marker, dict) and marker.get("done")
                and record["root"] == str(root) and record["transfer"] == marker["transfer"])


def _finished(root, kept):
    """Tidy after a take-back whose note says `done`: its record, the empty kept folder, then the note.

    Never raises and never opens a database: the app can be running by now.
    While the record cannot be removed, the note stays, so that this record
    is never taken for an unfinished take-back.
    """
    if _leftover(root, kept):
        try:
            _forget(kept, RECORD)
        except (OSError, ValueError, RuntimeError):
            return
        with suppress(OSError):  # It holds something that is not Amplifier's: it stays.
            os.rmdir(kept)
    _drop_marker(root)


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
    pointer = Path(root) / "app" / "updates" / "application-active.json"
    if _kind(pointer) is None:
        return version
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
    if returning in ("missing", "blocked"):
        raise Refused("take-back-record", _RETURNING)
    if returning == "done" and _leftover(root, kept):
        raise Refused("kept-record", _STUCK.format(kept=kept, stayed=""))  # A held file: tidied at the next try.
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
    if not remaining:
        stayed += [kept / "app" / name for name in state]  # Only the program state keeps the set unfinished.
    return {"kept": str(kept), "state": "complete" if done else "moving", "transfer": record["transfer"],
            "left": left, "remaining": remaining, "programState": state, "stayed": [str(path) for path in stayed],
            **({} if done else {"notice": _STUCK.format(kept=kept, stayed=_STAYED.format(path=stayed[0]))})}


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


def take_back(root, version, *, settle=True):
    """Move the kept set of this folder back into this installation's empty data folders.

    The caller holds the installation's lock (`first_start`, `move_out`).
    Runs every check each time, also when it continues an interrupted
    take-back. A folder of this installation that holds anything stops it
    before the first change. When Windows refuses to remove an empty folder
    (the current folder of the launcher), the kept entries are moved into it
    one by one; the record first names that folder and its entries, so that
    an interrupted fill can continue and accepts nothing else in that folder.
    The lock does not stop a program that is not Amplifier; the checks do.

    Last it retires the removed program's saved update status. If that fails,
    the take-back is not finished and the next start tries again. A move-out
    passes `settle=False`: the data leaves again at once. Then the note gets
    `done`: from there only tidying is left.
    """
    root, kept = Path(root), kept_folder(root)
    try:
        return _take_back(root, kept, _version(version), settle)
    except Refused:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise Refused("take-back", _RETRY.format(root=root, kept=kept), error) from error


def _take_back(root, kept, version, settle):
    reason, record = _reason(root, kept, version)
    if reason != "use":
        cause = _CAUSE.get(reason, _CAUSE["changed"])
        raise Refused("take-back-" + (reason or "missing"), _BLOCKED.format(root=root, kept=kept, cause=cause))
    # Before the first change, also of a continued take-back: never merge, never delete.
    blocker = _used(root) if record["state"] == "complete" else _obstacle(root, kept, record)
    if blocker is not None:
        raise Refused("take-back-not-empty", _IN_THE_WAY.format(path=blocker))
    note = {"schema": 1, "transfer": record["transfer"], "kept": str(kept)}
    taken, deadline = [], time.monotonic() + WAIT
    with _held(root), _held(kept):
        if record["state"] == "complete":
            # The installation's own note first: from here it can hold a part of the kept data.
            _write(root, MARKER, note)
            record = {**record, "state": "adopting"}
            _write(kept, RECORD, record)
        present = set(_names(root))
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
                _rename(source, target, deadline)
                if _kind(target) != "folder":  # A link took the kept folder's place and moved as a link.
                    raise Refused("take-back-link", _IN_THE_WAY.format(path=target))
            elif kind != "folder":
                raise Refused("take-back-not-empty", _IN_THE_WAY.format(path=target))
            else:
                if (record.get("entries") or {}).get("folder") != name:
                    if _names(target):
                        raise Refused("take-back-not-empty", _IN_THE_WAY.format(path=target))
                    record = {**record, "entries": {"folder": name, "names": _names(source)}}
                    _write(kept, RECORD, record)
                planned = set(record["entries"]["names"])
                with _held(source), _held(target):
                    foreign = _foreign(record, name, source, target)
                    if foreign is not None:  # Something else is in that folder.
                        raise Refused("take-back-not-empty", _IN_THE_WAY.format(path=foreign))
                    for entry in _names(source):
                        if entry not in planned:  # It came into the kept folder later: it is not the person's.
                            raise Refused("take-back-not-empty", _IN_THE_WAY.format(path=source / entry))
                        if _kind(target / entry) is not None:  # An entry never replaces an entry.
                            raise Refused("take-back-not-empty", _IN_THE_WAY.format(path=target / entry))
                        os.rename(source / entry, target / entry)
                    # Once more after the last entry: nothing else came into that folder in the meantime.
                    late = next((entry for entry in _names(target) if entry not in planned), None)
                    if late is not None:
                        raise Refused("take-back-not-empty", _IN_THE_WAY.format(path=target / late))
                    rest = _names(source)
                    if rest:  # It came into the kept folder during the moves.
                        raise Refused("take-back-not-empty", _IN_THE_WAY.format(path=source / rest[0]))
                if _kind(source) == "folder":
                    os.rmdir(source)
            taken.append(name)
    settled = _settle(root, record["transfer"]) if settle else "skipped"
    if settled == "unchanged":
        raise Refused("take-back-prepare", _PREPARE)  # Record and note stay: the next start tries again.
    _write(root, MARKER, {**note, "done": True})  # Finished. A stop after this line leaves only tidying.
    _finished(root, kept)
    return {"taken": taken, "updateState": settled}


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

    try:
        if (_kind(kept) is None and not any(name.casefold() == MARKER for name in _names(root))
                and not any(_kind(folder) is not None for folder in earlier_folders(root))):
            return None  # The usual start: nothing was kept.
        lock, path = _lock(root)
        staying = False
        try:
            result = _first(root, kept, version, ask, owner_notice, waiting)
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
        return waiting("check", Refused("check", _CHECK.format(kept=kept), error).notice)


def _first(root, kept, version, ask, owner_notice, waiting):
    returning = _returning(root, kept)
    if returning == "done":
        _finished(root, kept)  # Only tidying is left. The app can be running now: no database is opened.
        return None
    if returning == "blocked":
        # This installation can hold a part of the kept data, and the records do not say how to go on.
        raise Refused("take-back-record", _DAMAGED.format(root=root, kept=kept))
    if returning == "missing":
        # The kept folder is gone, so nothing more can come back. Only the person's Yes starts the app.
        notice = _MISSING.format(kept=kept)
        if not ask or not owner_notice(notice, confirm=True):
            return waiting("take-back-record", notice, show=False)
        marker = _marker(root)
        if _settle(root, marker["transfer"] if isinstance(marker, dict) else "0" * 32) == "unchanged":
            raise Refused("take-back-prepare", _PREPARE)
        try:
            _forget(root, MARKER)
        except (OSError, ValueError, RuntimeError) as error:
            raise Refused("take-back", _RETRY.format(root=root, kept=kept), error) from error
        return None
    if returning == "finish":
        take_back(root, version)  # The person already said Yes. Never start before it is finished.
        return None
    if not _fresh(root):
        return None  # This installation has its own data. Kept sets stay where they are.
    reason, record = _reason(root, kept, version)
    if reason == "unreadable":
        notice = _UNREADABLE.format(kept=kept)
        if not ask or not owner_notice(notice, confirm=True):
            return waiting("unreadable", notice, show=False)
    elif reason == "use":
        if not ask:
            return waiting("question", _WAITING.format(kept=kept), show=False)
        if owner_notice(_OFFER.format(date=_date(record["keptAt"]), kept=kept), confirm=True):
            take_back(root, version)
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
