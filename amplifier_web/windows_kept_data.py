"""Kept chats and settings of a removed Windows installation.

Setup installs only into an absent or empty folder, so kept data cannot stay
in the installation folder. "Keep my chats and settings" moves the person's
data folders into the sibling folder `<root>-kept`. A later installation in
the same folder offers them once, at its first start, and takes them back.

Only whole folders are renamed on one disk. Nothing is copied and nothing is
merged: an entry never replaces an entry. No chat, setting or account is
deleted. This module deletes only empty folders and its own record; the
program state inside the data (`app\\updates`, `app\\runtime`) is deleted by
the caller's link-safe deleter. A link is never moved and never entered.

One record, `amplifier-kept.json`, names the installation folder, the newest
app version that used the data, one transfer identity and a state:
`moving` (move-out not finished), `complete`, `adopting` (take-back not
finished). An unfinished transfer is finished first, in its own direction.
A move-out continues only the set that holds its own transfer identity (the
uninstall journal saves it), so two installations' data never share a set.
"""
from __future__ import annotations

import calendar
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
EARLIER = 9
WAIT = 15.0  # Seconds to wait for a program that holds a folder for a moment.
LIMIT = 64 * 1024
_FIELDS = {"schema", "root", "version", "keptAt", "state", "transfer"}
_VERSION = r"\d+\.\d+\.\d+"
_NATIVE = os.name == "nt"  # The private-folder boundary of windows_files.

_NOTHING = "Nothing was changed. "
_OCCUPIED = ("Amplifier cannot keep your chats and settings, because this folder exists and Amplifier "
             "cannot use it:\n{kept}\n\n" + _NOTHING + "Move or rename that folder. Then uninstall again.")
_FULL = ("Amplifier cannot keep your chats and settings, because nine earlier kept folders exist next to "
         "this folder:\n{kept}\n\n" + _NOTHING + "Move or rename one of them. Then uninstall again.")
_STUCK = ("Amplifier could not finish moving your chats and settings to this folder:\n{kept}\n\n"
          "Nothing was deleted. Restart this PC. Then uninstall again.")
_STOPPED = ("Amplifier could not finish moving your kept chats and settings back.\n\n"
            "Nothing was deleted. They are in these two folders:\n{root}\n{kept}\n\n"
            "Restart this PC. Then open Amplifier again to finish. "
            "If you see this message again, keep both folders.")
_OFFER = ("Amplifier found the chats and settings that you kept on {date}.\n\nUse them?\n\n"
          "Yes: use them.\nNo: start fresh. You will not be asked again; they stay in this folder:\n{kept}")
_UNREADABLE = ("Amplifier found kept chats and settings in this folder, but it cannot read them:\n{kept}\n\n"
               "Start fresh without them?\n\nYes: start fresh. The folder stays as it is.\n"
               "No: do not start Amplifier now.")
_WAITING = ("Kept chats and settings are waiting in this folder:\n{kept}\n\n"
            "Open Amplifier from the Start menu to choose whether to use them.")
_NAMED = "Amplifier found kept chats and settings that it does not use:\n\n{sets}\n\nThey stay where they are."
_WHY = {
    "other-folder": "They belong to Amplifier in another folder.",
    "newer": "A newer version of Amplifier kept them. To use them, uninstall Amplifier and choose to delete "
             "everything. That does not change this folder. Then install the current version of Amplifier.",
    "moving": "Their move did not finish. Keep this folder.",
    "link": "This folder holds a link, and Amplifier does not follow links.",
    "earlier": "You kept them at an earlier uninstall.",
}
_WHY["program-state"] = _WHY["moving"]


class Refused(RuntimeError):
    """A move-out or take-back that did not start, or stopped; `notice` is the person's text."""

    def __init__(self, kind, notice):
        super().__init__(kind)
        self.kind, self.notice = kind, notice + "\n\nReference: kept-data, " + kind


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


def _write(kept, record):
    """Replace the record as one complete private file."""
    if _NATIVE:
        from .deployment import write_private
        write_private(kept / RECORD, json.dumps(record), owned_root=kept)
        return
    draft = kept / (RECORD + ".new")
    draft.write_text(json.dumps(record), encoding="utf-8")
    os.replace(draft, kept / RECORD)


def _forget(kept):
    """Delete the record of a finished take-back: the exact private file that was read."""
    if _NATIVE:
        from .windows_files import directory
        with directory(kept, owned_root=kept) as owned:
            owned.remove_private(RECORD, hashlib.sha256(owned.read_private(RECORD, limit=LIMIT)).hexdigest())
    else:
        os.remove(kept / RECORD)


def _raw(kept):
    if _NATIVE:
        from .windows_files import directory
        with directory(kept, owned_root=kept) as owned:
            return owned.read_private(RECORD, limit=LIMIT)
    if _kind(kept / RECORD) != "file":
        raise ValueError("The kept-data record is not a plain file.")
    return (kept / RECORD).read_bytes()


def _parts(version):
    return tuple(map(int, version.split(".")))


def read(kept):
    """`(state, record)`: absent, none (a folder without a record), unreadable, or the record's state.

    A record that exists and cannot be read is `unreadable`, never "no kept data".
    """
    kind = _kind(kept)
    if kind is None:
        return "absent", None
    try:
        if kind != "folder":
            raise ValueError("The kept folder is not a real folder.")
        if RECORD not in _names(kept):
            return "none", None
        record = json.loads(_raw(kept))
        if (not isinstance(record, dict) or not _FIELDS <= set(record) <= _FIELDS | {"entries"}
                or type(record["schema"]) is not int or record["schema"] != 1
                or not isinstance(record["root"], str) or not record["root"]
                or not isinstance(record["version"], str) or not re.fullmatch(_VERSION, record["version"])
                or not isinstance(record["keptAt"], str)
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["keptAt"])
                or record["state"] not in ("moving", "complete", "adopting")
                or not isinstance(record["transfer"], str) or not re.fullmatch("[a-f0-9]{32}", record["transfer"])
                or record.get("entries") not in (None, *FOLDERS)):
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
    if record["root"] != str(root):
        return "other-folder", record
    if state == "moving":
        return "moving", record
    try:
        _private(kept, kept)
        for name in _names(kept):
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
    except OSError:
        return "unreadable", record
    if _parts(record["version"]) > _parts(version):
        return "newer", record
    return "use", record


def _fresh(root):
    """A first start: every data folder of this installation is absent, or a real and empty folder."""
    present = set(_names(root))
    return all(name not in present or (_kind(root / name) == "folder" and not _names(root / name))
               for name in FOLDERS)


def used_version(root, version):
    """The newest app version that used this data: the launcher's, or its active app update's."""
    pointer = Path(root) / "app" / "updates" / "application-active.json"
    if _kind(pointer) is None:
        return version
    try:
        if _kind(pointer) != "file" or pointer.stat().st_size > LIMIT:
            raise ValueError
        active = json.loads(pointer.read_text(encoding="utf-8"))["version"]
        if not isinstance(active, str) or not re.fullmatch(_VERSION, active):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("The active app version is not readable.") from error
    return max(version, active, key=_parts)


def _plan(root, version, transfer):
    """What a move-out does with an existing kept folder. Reads only; raises Refused."""
    kept = kept_folder(root)
    reason, record = _reason(root, kept, version)
    if reason is None:
        if _kind(kept) is not None:
            try:
                usable = _kind(kept) == "folder" and not _names(kept)
                _private(kept, kept)
            except OSError:
                usable = False
            if not usable:
                raise Refused("kept-folder", _OCCUPIED.format(kept=kept))
        return "new", None
    if record is None or reason == "other-folder":
        raise Refused("kept-folder", _OCCUPIED.format(kept=kept))
    if record["state"] == "moving":
        # Another transfer's unfinished set holds other data: never add to it, never rename it.
        if record["transfer"] != transfer:
            raise Refused("kept-folder", _OCCUPIED.format(kept=kept))
        try:
            _private(kept, kept)
        except OSError:
            raise Refused("kept-unsafe", _STUCK.format(kept=kept)) from None
        return "continue", record
    if record["state"] == "adopting":
        if reason != "use":
            raise Refused("take-back-" + reason, _STOPPED.format(root=root, kept=kept))
        return "take-back", record
    # Only a complete set is ever set aside. It is renamed whole and never changed.
    free = next((folder for folder in earlier_folders(root) if _kind(folder) is None), None)
    if free is None:
        raise Refused("kept-full", _FULL.format(kept=kept))
    return "aside", free


def check(root, version, transfer=None):
    """Raise Refused when a move-out cannot start. Changes nothing; call it before the first change.

    `transfer` is the identity in the journal of an uninstall that continues, else None.
    """
    _plan(Path(root), version, transfer)


def _move(source, target, owned):
    """Rename a whole folder; if Windows refuses, move its entries one by one into its moved place.

    An entry never replaces an entry. Returns whether the source is now gone or empty.
    """
    kind = _kind(target)
    if kind is None:
        try:
            os.rename(source, target)
            return True
        except OSError:
            pass  # A program's current folder, or a closing program: move what can move.
    elif kind != "folder":
        return False
    try:
        _private(target, owned, create=kind is None)  # Also an existing one: this person's own real folder.
    except OSError:
        return False
    for name in _names(source):
        if _kind(target / name) is None:
            try:
                os.rename(source / name, target / name)
            except OSError:
                pass
    if _names(source):
        return False
    try:
        os.rmdir(source)
    except OSError:
        pass  # Another program's current folder: it stays, empty.
    return True


def move_out(root, *, version, transfer, remove=None, wait=15.0):
    """Move the data folders of a stopped installation into `<root>-kept`.

    The caller stopped the app, proved the folder, ran `check` and saved
    `transfer` in its own journal. `version` is `used_version`. `remove` is
    the caller's link-safe deleter for the program state inside the moved data.

    The set is `complete` only when every data folder is moved (or empty) and
    no program state is left in it. Otherwise it stays `moving` and the same
    call continues it. `left` names entries that are not real folders (a link,
    a file); they stay where they are.
    """
    root, kept = Path(root), kept_folder(root)
    if not re.fullmatch(_VERSION, version) or not re.fullmatch("[a-f0-9]{32}", transfer):
        raise ValueError("A kept set needs an app version and a transfer identity.")
    action, detail = _plan(root, version, transfer)
    if action == "take-back":  # Finish the other direction first, then begin.
        take_back(root, version)
        action, detail = _plan(root, version, transfer)
    if action == "aside":
        os.rename(kept, detail)
        action = "new"
    if action == "new":
        _private(kept, kept, create=True)
        record = {"schema": 1, "root": str(root), "version": version, "state": "moving", "transfer": transfer,
                  "keptAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write(kept, record)
    elif action == "continue":
        record = detail
    else:
        raise Refused("kept-folder", _OCCUPIED.format(kept=kept))
    here = Path.cwd()
    if here == root or root in here.parents:
        os.chdir(os.environ.get("SystemRoot") or root.anchor)  # This program's own current folder.
    deadline = time.monotonic() + wait
    while True:
        present, left, remaining = set(_names(root)), [], []
        for name in FOLDERS:
            if name not in present:
                continue
            if _kind(root / name) != "folder":
                left.append(name)  # A link, or a file: it stays and is reported.
            elif not _move(root / name, kept / name, kept):
                remaining.append(name)
        if not remaining or time.monotonic() >= deadline:
            break
        time.sleep(0.5)  # A closing program releases its files a moment after it stops.
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
    if done:
        _write(kept, {**record, "state": "complete", "version": max(record["version"], version, key=_parts)})
    return {"kept": str(kept), "state": "complete" if done else "moving", "transfer": record["transfer"],
            "left": left, "remaining": remaining, "programState": state,
            **({} if done else {"notice": _STUCK.format(kept=kept)})}


def _settle(root, transfer):
    """Retire the removed installation's saved update status, as the maintained repair does.

    An update that did not finish leaves a status in the database that stops
    every new message until that exact app proves itself; that app is gone.
    `reset.reset_update_state` copies the database first and removes only this
    status. A failure here never strands the data: the app's own repair remains.
    """
    home = root / "app"
    if _kind(home / "app.sqlite3") != "file":
        return "none"
    try:
        from .reset import reset_update_state
        backup = home / "reset-backups" / f"kept-{transfer}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        _private(backup, root, create=True)
        reset_update_state(home, backup)
        return "retired"
    except (OSError, ValueError, sqlite3.Error):
        return "unchanged"


def _in_the_way(root, kept, record):
    """Whether a folder of this installation holds anything that a kept folder would join.

    The one folder that an interrupted take-back was filling may hold the
    entries that it already moved; no kept entry may have a name that is there.
    """
    present = set(_names(root))
    for name in FOLDERS:
        if name not in present or _kind(kept / name) is None:
            continue
        if _kind(root / name) != "folder":
            return True
        held = set(_names(root / name))
        if held and (record.get("entries") != name or held & set(_names(kept / name))):
            return True
    return False


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


def take_back(root, version):
    """Move the kept set of this folder back into this installation's empty data folders.

    Runs every check each time, also when it continues an interrupted
    take-back. A folder of this installation that holds anything stops it.
    When Windows refuses to remove an empty folder (the current folder of the
    launcher), the kept entries are moved into it one by one; the record names
    that folder first, so that an interrupted fill can continue.
    """
    root, kept = Path(root), kept_folder(root)
    reason, record = _reason(root, kept, version)
    stopped = _STOPPED.format(root=root, kept=kept)
    if reason != "use":
        raise Refused("take-back-" + str(reason), stopped)
    # Before the first change, also of a continued take-back: never merge, never delete.
    if (record["state"] == "complete" and not _fresh(root)) or _in_the_way(root, kept, record):
        raise Refused("take-back-not-empty", stopped)
    if record["state"] == "complete":
        record = {**record, "state": "adopting"}
        _write(kept, record)
    present, taken, deadline = set(_names(root)), [], time.monotonic() + WAIT
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
        elif kind != "folder":
            raise Refused("take-back-not-empty", stopped)
        else:
            if record.get("entries") != name:
                if _names(target):
                    raise Refused("take-back-not-empty", stopped)  # It changed after the check above.
                record = {**record, "entries": name}
                _write(kept, record)
            for entry in _names(source):
                if _kind(target / entry) is not None:
                    raise Refused("take-back-not-empty", stopped)  # An entry never replaces an entry.
                os.rename(source / entry, target / entry)
            os.rmdir(source)
        taken.append(name)
    settled = _settle(root, record["transfer"])
    _forget(kept)
    try:
        os.rmdir(kept)
    except OSError:
        pass  # It holds something that is not Amplifier's: it stays.
    return {"taken": taken, "updateState": settled}


def _date(kept_at):
    moment = time.localtime(calendar.timegm(time.strptime(kept_at, "%Y-%m-%dT%H:%M:%SZ")))
    return f"{calendar.month_name[moment.tm_mon]} {moment.tm_mday}, {moment.tm_year}"


def first_start(root, version, *, ask):
    """The shortcut's step before anything writes into the data folders.

    Returns None when the app can start, or a small result when it must not
    start now. `version` is the app that this launcher starts. `ask` is false
    for a caller without a person: it gets a result, never a question.
    """
    root, kept = Path(root), kept_folder(root)
    earlier = [folder for folder in earlier_folders(root) if _kind(folder) is not None]
    if _kind(kept) is None and not earlier:
        return None
    from .windows_installation import owner_notice

    def waiting(kind, notice, *, show=True):
        if ask and show:
            owner_notice(notice)
        return {"taken": False, "reason": kind, "message": notice}

    state, record = read(kept)
    adopting = state == "adopting" and record["root"] == str(root)
    if not adopting and not _fresh(root):
        return None  # This installation has its own data. Kept sets stay where they are.
    try:
        if adopting:
            take_back(root, version)  # The person already said Yes. Never start before it is finished.
            reason = None
        else:
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
        # Every kept set that is not offered is named once, at this first start.
        named = [f"{kept}\n{_WHY[reason]}"] if reason in _WHY else []
        named += [f"{folder}\n{_WHY['earlier']}" for folder in earlier]
        if named and ask:
            owner_notice(_NAMED.format(sets="\n\n".join(named)))
        return None
    except Refused as refused:
        return waiting(refused.kind, refused.notice)
    except (OSError, ValueError, RuntimeError) as error:
        code = getattr(error, "winerror", None)
        return waiting("take-back", _STOPPED.format(root=root, kept=kept)
                       + f"\n\nReference: kept-data, {type(error).__name__}" + (f" {code}" if code else ""))
