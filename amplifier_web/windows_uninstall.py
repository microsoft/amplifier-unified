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

from contextlib import ExitStack, contextmanager
import ctypes as C
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import sys
import time

JOURNAL = "unified-uninstall.json"
COMPLETE, STARTED = "unified-setup-complete.json", "unified-setup-started.json"  # Setup's two records.
# The person's data, in the order of deletion: the stored keys go first.
DATA = ("shared", "app", "capture", "ownership", "import", "workspace", "export")
# The running program. `finish` deletes these last, with one difference: a
# file that this running program has mapped is moved into PARK instead.
RUNNING = ("python", "tools")
# The flat folder that `finish` creates and holds for those few files. Only
# the step after exit deletes in it.
PARK = "unified-uninstall-last"
CACHES = ("source-store", "cache", "temp", "python-bin", "tool-bin")
# Setup names the source store "s" from release 0.98.5 (`windows_consumer`).
# An earlier setup file named it "source-store" and made no folder "s". So
# "s" is this installation's own only when setup's completion record names a
# release from 0.98.5 (`short_store`). In every other installation a folder
# "s" is not Amplifier's, and it stays.
STORE, STORE_SINCE = "s", (0, 98, 5)
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
# file, setup's notes about a helper program that failed, and the file that
# the last step of a removal keeps open (Windows deletes it with that step).
_FILE_FORMS = (rf"amplifier_unified-{_VERSION}-py3-none-any\.whl(\.part)?", r"\.private-[0-9a-f]{32}\.tmp",
               r"child-\d+\.(stderr\.txt|failure\.json)", r"[0-9a-f]{32}\.hold")
_FOLDERS = frozenset(DATA + RUNNING + CACHES + LAST[:2] + (PARK,))
_EXACT = frozenset(DATA + RUNNING + CACHES + FILES + LAST + (STARTED, COMPLETE, JOURNAL, PARK))
SECONDS_AFTER_EXIT = 120  # For a handful of files. A program that closes late gets this long.
# The steps of `remove` that come after the journal. A failure there leaves a
# folder that Amplifier does not start from; a second uninstall finishes it.
AFTER_JOURNAL = frozenset({"setup-record", "data", "program", "apps-entry", "shortcuts", "receipt", "program-last"})


def _form(name, forms):
    return any(re.fullmatch(form, name, re.ASCII) for form in forms)


def _folder(name):
    return name in _FOLDERS or _form(name, _FOLDER_FORMS)


def sort(names, short=False):
    """Sort a folder's own listing into what each step deletes.

    Names are compared exactly, with their case. `foreign` is everything that
    this installation did not create. The journal is in no group. `short`:
    setup made this installation's source store under the name "s"
    (`short_store`). Without it a folder "s" is foreign.
    """
    groups = {"setup": [], "data": [], "program": [], "last": [], "running": [], "foreign": []}
    for name in names:
        if name == JOURNAL:
            continue
        group = ("setup" if name in (STARTED, COMPLETE) else "data" if name in DATA
                 else "running" if name in RUNNING or name == PARK else "last" if name in LAST
                 else "program" if name in CACHES or name in FILES or (short and name == STORE)
                 or _form(name, _FOLDER_FORMS + _FILE_FORMS)
                 else "foreign")
        groups[group].append(name)
    for group, order in (("data", DATA), ("last", LAST)):
        groups[group].sort(key=order.index)
    return groups


def altered(names, short=False):
    """Names that differ from one of this installation's exact names only by case.

    Windows opens such an entry under this installation's name, but it is not
    the entry that this installation made, and nothing shows what it holds.
    It is never deleted. Uninstall then deletes nothing at all: it could not
    truthfully say that the person's data is gone. `short` as in `sort`: only
    then is "s" one of this installation's names.
    """
    from .windows_removal import same_name
    known = {same_name(name): name for name in _EXACT | ({STORE} if short else set())}
    return [name for name in names if known.get(same_name(name), name) != name]


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


def short_store(root):
    """Whether setup made this installation's source store under the name "s".

    Read from setup's own private completion record: `version` there is the
    release of the setup file. Setup writes that record once, and uninstall
    keeps it until the step after exit. A record that is missing, or that
    does not name a release from `STORE_SINCE`, answers no: a folder "s" is
    then not Amplifier's.
    """
    value = _private_json(root, COMPLETE)
    if value is None or type(value.get("schema")) is not int or value["schema"] != 1:
        return False
    version = value.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d{1,6}\.\d{1,6}\.\d{1,6}", version, re.ASCII):
        return False
    return tuple(int(part) for part in version.split(".")) >= STORE_SINCE


class Busy(RuntimeError):
    """Another Amplifier window starts or removes this installation right now."""


def question(message, *, cancel=False):
    """Ask with No as the default answer. Returns "yes", "no" or "cancel".

    With `cancel` there are three buttons. Without it the question carries the
    warning icon: Yes there deletes the person's data. Closing a three-button
    question counts as Cancel; a two-button question cannot be closed.
    """
    if os.name != "nt":
        raise RuntimeError("The uninstall question requires native Windows.")
    dialog = C.WinDLL("user32", use_last_error=True).MessageBoxW
    dialog.argtypes = [C.c_void_p, C.c_wchar_p, C.c_wchar_p, C.c_uint]
    dialog.restype = C.c_int
    # In front of other windows, as `windows_installation.owner_notice`
    # (system-modal, foreground, topmost); the second button is the default.
    flags = 0x51000 | 0x100 | (0x40 | 0x3 if cancel else 0x30 | 0x4)
    result = dialog(None, message, "Amplifier Unified", flags)
    if not result:
        raise OSError("The uninstall question could not be displayed.")
    return {6: "yes", 7: "no"}.get(result, "cancel" if cancel else "no")


@contextmanager
def single(root, *, wait=10.0):
    """One start or removal at a time for one installation, across processes and sign-in sessions.

    A named mutex. It has no count, and only the thread that took it can
    release it, so no other program can let a second one in. Enter and leave
    this block on one thread. A program that ended without releasing it
    leaves it to the next taker. When it stays taken this raises Busy and
    changes nothing.

    The name is in the name space that every session of this PC shares, so
    another account can create it first. The lock is therefore this person's
    own: it is created with this person as its owner and with no access for
    another account, and an existing one with another owner is refused with
    OSError. Another account can then make Amplifier refuse to open or
    uninstall while its program runs; it can never hold or release the lock.
    """
    if os.name != "nt":
        raise RuntimeError("The installation lock requires native Windows.")
    from .windows_consumer_shell import identity
    from .windows_files import PVOID, _API
    api = _API()
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
    kernel.CreateMutexW.restype = C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
    kernel.WaitForSingleObject.restype = C.c_uint32
    kernel.ReleaseMutex.argtypes = [C.c_void_p]
    kernel.ReleaseMutex.restype = C.c_int
    kernel.CloseHandle.argtypes = [C.c_void_p]
    kernel.CloseHandle.restype = C.c_int
    # One name for every sign-in session of this person on this PC. Windows
    # needs no privilege for a mutex in this name space. Not owned at creation.
    with api.private_security() as security:
        handle = kernel.CreateMutexW(C.byref(security), 0,
                                     "Global\\AmplifierUnified-installation-" + identity(root))
        if not handle:
            raise C.WinError(C.get_last_error())
    try:
        owner, descriptor = PVOID(), PVOID()
        # SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION: who made this object.
        error = api.GetSecurityInfo(handle, 6, 1, C.byref(owner), None, None, None, C.byref(descriptor))
        if error:
            raise C.WinError(error)
        try:
            if api.sid_text(owner) != api.sid:
                raise PermissionError(5, "The installation lock belongs to another account.")
        finally:
            api.LocalFree(descriptor)
        state = kernel.WaitForSingleObject(handle, int(wait * 1000))
        if state == 0x102:  # WAIT_TIMEOUT
            raise Busy()
        # 0x80, WAIT_ABANDONED: its owner ended without releasing it. This
        # thread owns it now; every step checks the folder itself again.
        if state not in (0, 0x80):
            raise C.WinError(C.get_last_error())
        try:
            yield
        finally:
            kernel.ReleaseMutex(handle)
    finally:
        kernel.CloseHandle(handle)


def outside():
    """Leave the installation folder, and write nothing more into its program.

    Windows keeps a folder that is a program's current folder. The step after
    exit keeps every entry that appears after it started, so this process
    must not save a compiled module beside the program from here on.
    """
    from .windows_service import _powershell
    sys.dont_write_bytecode = True
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
            if not unfinished(root):
                # The writer reports a publication as done also when the file
                # lost its name directly afterwards (windows_files,
                # PrivateFileUnlinked). Without the journal Amplifier would
                # open from this folder, so nothing may be deleted.
                raise OSError("The removal's journal is not in the folder after it was written.")
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


def _clear(owned, names, deadline, wait, short=False):
    """Delete these entries in their order. Returns those that still hold something.

    Every deletion of this module goes through here. Until `deadline` a
    closing program gets time to release its files. `short` as in `sort`:
    without it this refuses a folder "s", like every foreign name.

    An entry whose name now differs from a wanted name only by case is the
    same entry to Windows and to setup. A program renamed it after the check.
    It is not deleted, and `holding` counts it as still there from the same
    listing that looks for the exact name. So the caller stops: setup's
    completion record is never deleted past such an entry, and the data is
    never reported as deleted.
    """
    names, groups = list(names), sort(names, short)
    # Never the journal, never a foreign name, never the running program.
    if JOURNAL in names or groups["foreign"] or groups["running"]:
        raise ValueError("Not an entry that uninstall deletes in this step.")
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
    """Delete setup's mark of unfinished work. Setup's completion record stays.

    A finished installation can still have that mark. With the mark and
    without the completion record, setup would take a folder without data for
    its own unfinished work and empty it, with everything in it.

    So the completion record stays for as long as anything is left in the
    folder: setup then installs and removes nothing there, whatever else a
    program puts into the folder. Only the step after exit deletes the
    record, immediately before the journal, when nothing but this removal's
    own entries is left.
    """
    from . import windows_consumer_shell as shell
    if _clear(owned, [STARTED], time.monotonic() + shell.SETTLE_SECONDS, wait):
        raise Held([STARTED])


def remove_data(owned, *, wait=time.sleep):
    """Delete the person's data, the stored keys first. Raises Held when any of it stays.

    An emptied folder that Windows still keeps for a program is not data.
    """
    from . import windows_consumer_shell as shell
    held = _clear(owned, DATA, time.monotonic() + shell.SETTLE_SECONDS, wait)
    if held:
        raise Held(held)


def remove_program(owned, *, last=False, wait=time.sleep, short=False):
    """Delete caches and program files. With `last`: what the launcher's own check needs.

    Uninstall removes the Windows entries between the two calls: until then a
    second uninstall can still start from its entry and finish the removal.
    The two folders of the running program are not touched. Returns what
    stayed. Nothing here holds the person's data, so nothing here stops.
    """
    from . import windows_consumer_shell as shell
    names = sort(owned.names(), short)["last" if last else "program"]
    return _clear(owned, names, time.monotonic() + shell.SETTLE_SECONDS, wait, short)


def last_names(owned=None, short=False):
    """The folders that the step after exit is asked about, by exact name.

    Every exact folder name of this installation, and every folder in it now
    that has one of its name forms. That step removes a named folder only
    while it is empty. So an emptied folder that Windows still keeps for a
    program goes when that program has ended, and no file is deleted. The
    held folder is not in this list. A file of this installation that is
    still there (a program uses it) stays, and then the folder stays.
    """
    groups = sort([] if owned is None else owned.names(), short)
    own = groups["program"] + groups["last"]
    # The folder "s" only where it is the installation's own (`short_store`).
    store = (STORE,) if short else ()
    folders = list(DATA + RUNNING + CACHES + store + LAST[:2]) + [name for name in own if _folder(name)]
    return list(dict.fromkeys(folders))


def remove(root, *, stop, certificate, progress, choice="delete", data=remove_data, wait=time.sleep, **fields):
    """Delete this installation's folder, in the one order that keeps every interruption safe.

    1. One start or removal at a time. Leave the folder. Prove it and pin it:
       setup's completion record, or this removal's own journal, read through
       the private-file boundary; then the folder's own handle. An entry
       whose name differs from this installation's own only by case refuses.
    2. Check and pin the Windows entries. `stop()` stops the app and removes
       its sign-in task. `certificate()` lets Windows ask, once for each
       certificate of this installation that it holds.
       Up to here nothing in the folder has changed.
    3. The journal. Nothing in the folder is deleted before it exists. From
       here Amplifier does not start from this folder.
    4. Setup's mark of unfinished work, then `data`: the person's data. If
       any of it stays, this stops. The Windows entries and the launcher are
       intact, so the person can uninstall again. Setup's completion record
       stays until the step after exit: with it, setup changes nothing here.
    5. Caches and program files; then the Windows entries; then what the
       launcher's own check needs.

    `progress(name)` names the step that is about to run. Returns the
    journal's token, the certificate outcome, the entries that are not
    Amplifier's (None when the folder could not be listed at the end) and
    `short`, this removal's one answer of `short_store`, for `finish`. The
    journal and the running program's two folders stay; `finish` is their step.
    """
    from . import windows_consumer_shell as shell, windows_removal
    progress("lock")
    with single(root):
        progress("folder")
        outside()
        with windows_removal.folder(root) as owned:
            record = pending(root)
            if not completed(root) and (record is None or record["choice"] != choice):
                raise ValueError("Neither setup's completion record nor this removal's journal proves the folder.")
            short = short_store(root)  # From setup's record, which stays until the step after exit.
            if altered(owned.names(), short):
                raise ValueError("An entry differs from this installation's own name only by case.")
            progress("entries-check")
            with ExitStack() as entries:
                finish = (entries.enter_context(shell.removal(root, progress=progress))
                          if (Path(root) / shell.FILE).exists() else None)
                stop()
                trust = certificate()
                progress("journal")
                token = begin(root, choice, wait=wait, **fields)
                progress("setup-record")
                forget_setup(owned, wait=wait)
                progress("data")
                data(owned, wait=wait)
                progress("program")
                remove_program(owned, wait=wait, short=short)
                if finish is not None:
                    finish()  # The Apps-list entry, the shortcuts, their receipt.
            progress("program-last")
            try:
                remove_program(owned, last=True, wait=wait, short=short)
                foreign = sort(owned.names(), short)["foreign"]
            except (OSError, ValueError, RuntimeError):
                foreign = None  # Every entry is gone already; only the last listing failed.
    return {"token": token, "certificate": trust, "foreign": foreign, "short": short}


def finish(root, token, *, short=False, choice="delete", wait=time.sleep):
    """The last step of a removal: the two folders that this running program runs from.

    A running program cannot delete the files that it runs from. It can move
    them. So, under the lock, with the installation folder proven and pinned
    again and the journal still this removal's own:

    1. Create the flat folder `unified-uninstall-last` and two kept files,
       `<token>.hold` in the installation folder and in the flat folder,
       through handles. Nobody else can open them.
    2. Start the step after exit. It inherits the two open files. From here
       no program can empty, rename or replace the flat folder, the
       installation folder or a folder above it, also when this program has
       ended: Windows turns only an empty folder into a link.
    3. Delete `python` and `tools` through handles, as everything before. A
       file that this running program has mapped is moved, through its
       handle, into the held folder instead.

    The step after exit deletes those few files where they are held, then
    the emptied folders, the journal, setup's completion record and the
    installation folder. It walks no tree. Returns what the walk reports for
    the two folders. After this the process imports nothing more, writes
    nothing into the installation, and ends. `short` is what `remove`
    returned: this step does not decide again whether "s" is the
    installation's own.
    """
    from . import windows_consumer_shell as shell, windows_removal
    with single(root):
        with windows_removal.folder(root) as owned:
            record = pending(root)
            if record is None or record["choice"] != choice or record["token"] != token:
                raise ValueError("Another removal of this folder began.")
            if altered(owned.names(), short):
                raise ValueError("An entry differs from this installation's own name only by case.")
            # What an earlier last step left: its flat folder, and a kept file
            # after a power loss. They are this installation's own; new ones
            # are made, or the name refuses.
            earlier = [name for name in owned.names() if re.fullmatch(r"[0-9a-f]{32}\.hold", name, re.ASCII)]
            owned.remove([PARK, *earlier], settle=shell.SETTLE_SECONDS, wait=wait)
            folders = last_names(owned, short)
            with owned.aside(PARK, token) as park:
                windows_removal.finish_after_exit(
                    root, token, park=PARK, keep=park.keep, folders=folders,
                    wait_for=(os.getpid(), os.getppid()), seconds=SECONDS_AFTER_EXIT, choice=choice)
                return owned.remove(RUNNING, settle=shell.SETTLE_SECONDS, wait=wait, park=park)
