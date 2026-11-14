"""What uninstall deletes when the person chose to delete the installation folder.

Deleting a person's folder cannot be undone. Three rules keep it inside this
one installation:

- Only exact top-level names that setup, the launcher or the launcher refresh
  create are ever deleted. Anything else in the folder is not Amplifier's: it
  stays, and then the folder stays.
- The journal `unified-uninstall.json` is written before the first deletion
  and is in no delete list. While it exists Amplifier does not start; a
  second uninstall finishes a removal that did not finish.
- One start or removal at a time for one installation, across processes.

The link-safe walk, and the step that removes the running program's own files
after this process has exited, are in `windows_removal`.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import time

JOURNAL = "unified-uninstall.json"
COMPLETE, STARTED = "unified-setup-complete.json", "unified-setup-started.json"  # Setup's two records.
# The person's data, in the order of deletion: the stored keys go first.
DATA = ("shared", "app", "capture", "ownership", "import", "workspace", "export")
# The running program. Only the step after exit removes what is inside these.
RUNNING = ("python", "tools")
CACHES = ("source-store", "cache", "temp", "python-bin", "tool-bin")
FILES = ("components.txt", "install.log", "private-git.json", "private-git.py", "uv.zip", "git.tar.bz2",
         "consumer-maintenance.json", "Launch Unified.lnk", "Amplifier status.lnk", "Stop Amplifier.lnk",
         "Remove Amplifier sign-in host.lnk")
# The launcher's own installation check needs these. They go last, so that a
# second uninstall can still run after an interruption.
LAST = ("git", "uv", "consumer-install.json", "consumer-qualified.json", "consumer-shell.json")
_VERSION = r"\d+\.\d+\.\d+"
# Folders that the launcher refresh keeps beside the installation's own.
_FOLDER_FORMS = (rf"launcher-{_VERSION}-[0-9a-f]{{8}}(-\d+)?", r"launcher-not-used(-\d+)?")
# The release file (also a partial download), the private writer's temporary
# file, and setup's notes about a helper program that failed.
_FILE_FORMS = (rf"amplifier_unified-{_VERSION}-py3-none-any\.whl(\.part)?", r"\.private-[0-9a-f]{32}\.tmp",
               r"child-\d+\.(stderr\.txt|failure\.json)")
_FOLDERS = frozenset(DATA + RUNNING + CACHES + LAST[:2])
SECONDS_AFTER_EXIT = 120  # About ten times the measured need for python and tools together.


def _form(name, forms):
    return any(re.fullmatch(form, name, re.ASCII) for form in forms)


def sort(names):
    """Sort a folder's own listing into what each step deletes.

    Names are compared exactly, with their case. `foreign` is everything that
    this installation did not create. The journal is in no group.
    """
    groups = {"setup": [], "data": [], "program": [], "last": [], "running": [], "foreign": []}
    for name in names:
        if name == JOURNAL:
            continue
        group = ("setup" if name in (STARTED, COMPLETE) else "data" if name in DATA
                 else "running" if name in RUNNING else "last" if name in LAST
                 else "program" if name in CACHES or name in FILES or _form(name, _FOLDER_FORMS + _FILE_FORMS)
                 else "foreign")
        groups[group].append(name)
    for group, order in (("data", DATA), ("last", LAST)):
        groups[group].sort(key=order.index)
    return groups


def unfinished(root):
    """Whether a removal of this folder started and did not finish.

    Any entry with the journal's name counts, also one that cannot be read:
    Amplifier must never start on a folder that may be partly deleted.
    """
    return os.path.lexists(os.path.join(root, JOURNAL))


def _private_json(root, name):
    from .windows_files import directory
    try:
        with directory(root, owned_root=root) as owned:
            value = json.loads(owned.read_private(name, limit=4096))
    except (OSError, ValueError, RuntimeError):
        return None
    return value if isinstance(value, dict) else None


def pending(root):
    """This program's own record of a removal that did not finish, else None.

    It is read through the private-file boundary. On a second uninstall it
    takes the place of setup's completion record as the proof of the folder.
    """
    value = _private_json(root, JOURNAL)
    if (value is not None and type(value.get("schema")) is int and value["schema"] == 1
            and isinstance(value.get("choice"), str) and isinstance(value.get("token"), str)
            and re.fullmatch("[0-9a-f]{32}", value["token"])):
        return value
    return None


def completed(root):
    """Whether setup completed this folder as an installation: its own private record."""
    value = _private_json(root, COMPLETE)
    return value is not None and type(value.get("schema")) is int and value["schema"] == 1


class Busy(RuntimeError):
    """Another Amplifier window starts or removes this installation right now."""


@contextmanager
def single(root, *, wait=10.0):
    """One start or removal at a time for one installation, across processes.

    A named semaphore, not a mutex: a mutex belongs to one thread, and these
    steps move between threads. It goes away with its last handle, so a
    program that ended without releasing it does not block the next one for
    long. When it stays taken this raises Busy and changes nothing.
    """
    if os.name != "nt":
        raise RuntimeError("The installation lock requires native Windows.")
    from .windows_consumer_shell import identity
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateSemaphoreW.argtypes = [C.c_void_p, C.c_long, C.c_long, C.c_wchar_p]
    kernel.CreateSemaphoreW.restype = C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
    kernel.WaitForSingleObject.restype = C.c_uint32
    kernel.ReleaseSemaphore.argtypes = [C.c_void_p, C.c_long, C.c_void_p]
    kernel.ReleaseSemaphore.restype = C.c_int
    kernel.CloseHandle.argtypes = [C.c_void_p]
    kernel.CloseHandle.restype = C.c_int
    # This sign-in session only: no other account can create or hold it.
    handle = kernel.CreateSemaphoreW(None, 1, 1, "Local\\AmplifierUnified-installation-" + identity(root))
    if not handle:
        raise C.WinError(C.get_last_error())
    try:
        state = kernel.WaitForSingleObject(handle, int(wait * 1000))
        if state == 0x102:  # WAIT_TIMEOUT
            raise Busy()
        if state != 0:
            raise C.WinError(C.get_last_error())
        try:
            yield
        finally:
            kernel.ReleaseSemaphore(handle, 1, None)
    finally:
        kernel.CloseHandle(handle)


def outside():
    """Leave the installation folder: Windows keeps a folder that is a program's current folder."""
    from .windows_service import _powershell
    os.chdir(PureWindowsPath(_powershell()).parents[2])  # The Windows system folder.


def begin(root, choice="delete", *, wait=time.sleep, **fields):
    """Write the journal and return its token. Nothing may be deleted before this.

    From here Open, Stop and Status refuse. A second uninstall writes a new
    token, so that an earlier step after exit stops at its next check.
    `fields` are further values that a choice needs in order to continue.
    """
    from . import windows_consumer_shell as shell
    from .deployment import write_private
    token = secrets.token_hex(16)
    text = json.dumps({**fields, "schema": 1, "choice": choice, "token": token})
    deadline = time.monotonic() + shell.SETTLE_SECONDS
    while True:
        try:
            write_private(Path(root) / JOURNAL, text, owned_root=root)
            return token
        except PermissionError:
            # The app's worker programs end a moment after its task reports
            # that it stopped. Until then Windows refuses a new file here.
            if time.monotonic() >= deadline:
                raise
            wait(0.5)


class Held(OSError):
    """Entries that could not be deleted, for example because a program uses them."""

    def __init__(self, names):
        super().__init__(32, "in use: " + ", ".join(names))
        self.names = list(names)


def _clear(owned, names, deadline, wait):
    """Delete these entries in their order. Returns those that still hold something.

    Every deletion of this module goes through here. Until `deadline` a
    closing program gets time to release its files.
    """
    names = list(names)
    if JOURNAL in names or sort(names)["foreign"]:
        raise ValueError("Not an entry of this installation.")  # Never the journal, never a foreign name.
    left = names
    while True:
        for name in left:
            owned.remove([name])
        held = set(owned.holding(names))
        left = [name for name in names if name in held]
        if not left or time.monotonic() >= deadline:
            return left
        wait(0.5)


def forget_setup(owned, *, wait=time.sleep):
    """Delete setup's records, so that a later setup run stops at this folder.

    Setup's mark of unfinished work goes first. A finished installation can
    still have it; with that mark and without the completion record, setup
    would take the folder for its own unfinished work and empty it.
    """
    from . import windows_consumer_shell as shell
    deadline = time.monotonic() + shell.SETTLE_SECONDS
    for name in (STARTED, COMPLETE):
        if _clear(owned, [name], deadline, wait):
            raise Held([name])


def remove_data(owned, *, wait=time.sleep):
    """Delete the person's data, the stored keys first. Raises Held when any of it stays.

    An emptied folder that Windows still keeps for a program is not data.
    """
    from . import windows_consumer_shell as shell
    held = _clear(owned, DATA, time.monotonic() + shell.SETTLE_SECONDS, wait)
    if held:
        raise Held(held)


def remove_program(owned, *, wait=time.sleep):
    """Delete caches and program files; what the launcher's own check needs goes last.

    The two folders of the running program are not touched. Returns what
    stayed. Nothing here holds the person's data, so nothing here stops.
    """
    from . import windows_consumer_shell as shell
    groups, deadline = sort(owned.names()), time.monotonic() + shell.SETTLE_SECONDS
    return _clear(owned, groups["program"], deadline, wait) + _clear(owned, groups["last"], deadline, wait)


def remaining(owned):
    """What is in the folder now: this installation's own leftovers, and what is foreign."""
    groups = sort(owned.names())
    own = groups["setup"] + groups["data"] + groups["program"] + groups["last"]
    return {"folders": [name for name in own if name in _FOLDERS or _form(name, _FOLDER_FORMS)],
            "files": [name for name in own if name not in _FOLDERS and not _form(name, _FOLDER_FORMS)],
            "foreign": groups["foreign"]}


def finish_after_exit(root, token, rest):
    """Start the step that removes the running program's files after this process has exited.

    It also removes the named leftovers: a folder only when it is empty. After
    this call the process must write nothing into the installation and end.
    """
    from . import windows_removal
    return windows_removal.finish_after_exit(
        root, token, contents=RUNNING, folders=rest["folders"], files=rest["files"],
        wait_for=(os.getpid(), os.getppid()), seconds=SECONDS_AFTER_EXIT)
