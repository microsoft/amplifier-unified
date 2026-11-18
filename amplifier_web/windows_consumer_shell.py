"""Per-user Windows shell entries for one qualified consumer installation.

Native ShellLink COM and HKCU only: no PowerShell, administrator, machine PATH,
trust import or external runtime. Unknown/colliding effects are retained, not
silently overwritten. Removal unregisters this app and preserves user files.
It can be repeated: entries that are already gone are skipped.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

FILE = "consumer-shell.json"
NOTE = "apps-list-version.json"
SETTLE_SECONDS = 15  # Longest wait for a closing app to release the installation folder.
# The waits for another program to close a Start-menu shortcut add up to at
# most this many seconds: once for the step that pins the shortcuts, and once
# for the step that deletes them. The Windows calls of each attempt take their
# own time, so a step that waits can take longer than this.
# Measured on hosted Windows runners, about 3 seconds after the shortcuts were
# made: the pin was refused for 0.01 s, and the delete was refused for at most
# 0.25 s. Asked which program had the file open, Windows named explorer.exe
# after 7 of 92 refused pins, and no program after the others and after the
# refused deletes. The waits are twenty times the longest of those refusals.
# A shortcut that is still held then stops the uninstall as before.
HOLD_SECONDS = 5
LINKS = {"Amplifier Unified.lnk": "", "Amplifier status.lnk": " --status",
         "Stop Amplifier.lnk": " --stop", "Uninstall Amplifier.lnk": " --uninstall"}
REGISTRY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AmplifierUnified-"


def programs():
    if sys.platform != "win32":
        raise OSError("Windows shell integration requires native Windows.")
    from .windows_files import address
    shell = C.WinDLL("shell32", use_last_error=True)
    shell.SHGetFolderPathW.argtypes = [W.HWND, C.c_int, W.HANDLE, W.DWORD, W.LPWSTR]
    shell.SHGetFolderPathW.restype = C.c_long
    path = C.create_unicode_buffer(32768)
    if shell.SHGetFolderPathW(None, 2, None, 0, path) != 0:  # CSIDL_PROGRAMS, current user
        raise OSError("The user's Start menu Programs directory is unavailable.")
    return address(path.value)


def identity(root):
    return hashlib.sha256(str(root).casefold().encode()).hexdigest()[:16]


def entries(root):
    python = Path(root) / "tools/amplifier-unified/Scripts/pythonw.exe"
    args = subprocess.list2cmdline(["-I", "-m", "amplifier_web.windows_consumer", "--root", str(root)])
    return python, {name: args + suffix for name, suffix in LINKS.items()}

def registration_values(root, version):
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("The consumer app version is invalid.")
    python, _ = entries(root)
    return {"DisplayName": "Amplifier Unified (unsigned preview)", "DisplayVersion": version,
            "InstallLocation": str(root), "UninstallString": subprocess.list2cmdline(
                [str(python), "-I", "-m", "amplifier_web.windows_consumer", "--root", str(root), "--uninstall"]),
            "NoModify": 1, "NoRepair": 1}


@contextmanager
def shell_link():
    """A small typed IShellLinkW/IPersistFile owner; no arbitrary script bridge."""
    ole = C.OleDLL("ole32")
    ole.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
    ole.CoInitializeEx.restype = C.c_long
    status = ole.CoInitializeEx(None, 2)
    if status not in (0, 1):
        raise OSError("The Windows shell COM apartment could not be initialized.")
    class GUID(C.Structure):
        _fields_ = [("bytes", C.c_ubyte * 16)]
    def guid(text):
        return GUID((C.c_ubyte * 16).from_buffer_copy(uuid.UUID(text).bytes_le))
    def call(pointer, index, restype, types, *args):
        table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(restype, C.c_void_p, *types)(table[index])(pointer, *args)
    pointer, persist = C.c_void_p(), C.c_void_p()
    try:
        ole.CoCreateInstance.argtypes = [C.POINTER(GUID), C.c_void_p, W.DWORD, C.POINTER(GUID), C.POINTER(C.c_void_p)]
        ole.CoCreateInstance.restype = C.c_long
        clsid = guid("00021401-0000-0000-c000-000000000046")
        iid = guid("000214f9-0000-0000-c000-000000000046")
        if ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid), C.byref(pointer)) != 0:
            raise OSError("The native Windows shortcut owner is unavailable.")
        ipersist = guid("0000010b-0000-0000-c000-000000000046")
        if call(pointer, 0, C.c_long, [C.POINTER(GUID), C.POINTER(C.c_void_p)], C.byref(ipersist), C.byref(persist)) != 0:
            raise OSError("The Windows shortcut persistence owner is unavailable.")
        class Link:
            def create(self, path, python, args, cwd):
                for slot, text in ((20, str(python)), (11, args), (9, str(cwd)),
                                   (7, "Amplifier Unified - your Windows account")):
                    if call(pointer, slot, C.c_long, [W.LPCWSTR], text) != 0:
                        raise OSError("The Windows shortcut binding could not be set.")
                if call(persist, 6, C.c_long, [W.LPCWSTR, W.BOOL], str(path), True) != 0:
                    raise OSError("The Windows shortcut could not be saved.")
            def read(self, path):
                if call(persist, 5, C.c_long, [W.LPCWSTR, W.DWORD], str(path), 0) != 0:
                    raise OSError("The Windows shortcut could not be read.")
                target, args = C.create_unicode_buffer(32768), C.create_unicode_buffer(32768)
                if call(pointer, 3, C.c_long, [W.LPWSTR, C.c_int, C.c_void_p, W.DWORD], target, len(target), None, 4) != 0:
                    raise OSError("The Windows shortcut target could not be verified.")
                if call(pointer, 10, C.c_long, [W.LPWSTR, C.c_int], args, len(args)) != 0:
                    raise OSError("The Windows shortcut arguments could not be verified.")
                return target.value, args.value
        yield Link()
    finally:
        if persist: call(persist, 2, W.ULONG, [])
        if pointer: call(pointer, 2, W.ULONG, [])
        ole.CoUninitialize()


def _registry_values(handle):
    import winreg
    if winreg.QueryInfoKey(handle)[0]:
        raise ValueError("The installed-app entry has unexpected children.")
    return {name: (value, kind) for name, value, kind in (
        winreg.EnumValue(handle, index) for index in range(winreg.QueryInfoKey(handle)[1]))}


def _typed_registry(values):
    # REG_SZ = 1, REG_DWORD = 4. Keep reads comparable on non-Windows test hosts.
    return {name: (value, 4 if type(value) is int else 1) for name, value in values.items()}


@contextmanager
def _registry_transaction(root, values, *, remove=False, version=None):
    """Prepare a conflict-detecting native registry transaction, then commit.

    A staged delete detects conflicting same-account edits/replacement on
    commit. Commit MUST precede other removals; no compare/close/path-delete gap.
    Unsupported KTM/registry transactions refuse; never fall back to path writes.
    """
    import winreg
    kernel = C.WinDLL("kernel32", use_last_error=True)
    ktm = C.WinDLL("ktmw32", use_last_error=True)
    adv = C.WinDLL("advapi32", use_last_error=True)
    signatures = {
        "CreateTransaction": (W.HANDLE, [C.c_void_p, C.c_void_p, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.LPWSTR]),
        "CommitTransaction": (W.BOOL, [W.HANDLE]),
        "RollbackTransaction": (W.BOOL, [W.HANDLE]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(ktm, name)
        fn.restype, fn.argtypes = result, args
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [W.HANDLE], W.BOOL
    adv.RegCreateKeyTransactedW.argtypes = [
        W.HKEY, W.LPCWSTR, W.DWORD, W.LPWSTR, W.DWORD, W.DWORD,
        C.c_void_p, C.POINTER(W.HKEY), C.POINTER(W.DWORD), W.HANDLE, C.c_void_p]
    adv.RegOpenKeyTransactedW.argtypes = [
        W.HKEY, W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(W.HKEY), W.HANDLE, C.c_void_p]
    adv.RegDeleteKeyTransactedW.argtypes = [
        W.HKEY, W.LPCWSTR, W.DWORD, W.DWORD, W.HANDLE, C.c_void_p]
    for name in ("RegCreateKeyTransactedW", "RegOpenKeyTransactedW", "RegDeleteKeyTransactedW"):
        getattr(adv, name).restype = W.LONG
    transaction = ktm.CreateTransaction(None, None, 0, 0, 0, 0, None)
    if not transaction or transaction == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    committed = False
    try:
        key = REGISTRY + identity(root)
        hive = W.HKEY(C.c_int32(int(winreg.HKEY_CURRENT_USER)).value)
        handle, disposition = W.HKEY(), W.DWORD()
        if remove or version is not None:
            error = adv.RegOpenKeyTransactedW(
                hive, key, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE | (0x10000 if remove else 0),
                C.byref(handle), transaction, None)
        else:
            error = adv.RegCreateKeyTransactedW(
                hive, key, 0, None, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE,
                None, C.byref(handle), C.byref(disposition), transaction, None)
        if error:
            raise C.WinError(error)
        # PyHKEY cannot be constructed from a native handle. winreg accepts the
        # integer handle directly; retain and close our original owner exactly.
        opened = handle.value
        try:
            if remove or version is not None:
                if _registry_values(opened) != _typed_registry(values):
                    raise ValueError("The installed-app entry changed; the write was refused.")
                if remove:
                    error = adv.RegDeleteKeyTransactedW(hive, key, 0, 0, transaction, None)
                    if error:
                        raise C.WinError(error)
                else:
                    winreg.SetValueEx(opened, "DisplayVersion", 0, winreg.REG_SZ, version)
            else:
                if disposition.value != 1:  # REG_CREATED_NEW_KEY, never adopt an existing key
                    raise ValueError("The installed-app entry already exists; nothing was overwritten.")
                for name, value in values.items():
                    winreg.SetValueEx(opened, name, 0,
                                     winreg.REG_DWORD if type(value) is int else winreg.REG_SZ, value)
            def commit():
                nonlocal committed
                if committed:
                    raise RuntimeError("The registry transaction is already committed.")
                if not ktm.CommitTransaction(transaction):
                    raise C.WinError(C.get_last_error())
                committed = True
            yield commit
        finally:
            winreg.CloseKey(opened)
    finally:
        try:
            if not committed and not ktm.RollbackTransaction(transaction):
                raise C.WinError(C.get_last_error())
        finally:
            if not kernel.CloseHandle(transaction):
                raise C.WinError(C.get_last_error())


@contextmanager
def registry_change(root, values, *, remove=False):
    """Stage an exact create or removal; the caller chooses when to commit."""
    with _registry_transaction(root, values, remove=remove) as commit:
        yield commit


def registry_version(root, earlier, version):
    """Change only the version, if the entire earlier key still matches."""
    registration_values(root, version)
    with _registry_transaction(root, earlier, version=version) as commit:
        commit()


def registry(root, values=None, *, remove=False):
    import winreg
    if values is not None or remove:
        with registry_change(root, values, remove=remove) as commit:
            commit()
        return
    key = REGISTRY + identity(root)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
            return _registry_values(handle)
    except FileNotFoundError:
        return None


def _receipt(root):
    from .windows_files import directory
    with directory(root, owned_root=root) as owned:
        return json.loads(owned.read_private(FILE, limit=65536))


def _entry_record(root):
    row = _receipt(root)
    folder = programs() / ("Amplifier Unified - " + identity(root))
    _, links = entries(root)
    if (not isinstance(row, dict) or set(row) != {"schema", "root", "folder", "status", "links", "registry"}
            or type(row["schema"]) is not int or row["schema"] != 1
            or row["root"] != str(root) or row["folder"] != str(folder)
            or row["status"] not in ("installed", "removed") or not isinstance(row["links"], dict)
            or set(row["links"]) != set(links) or not isinstance(row["registry"], dict)):
        raise ValueError("The Windows app entry receipt is incomplete; inspect before removal.")
    expected = registration_values(root, row["registry"].get("DisplayVersion"))
    if (row["registry"] != expected
            or any(type(row["registry"][name]) is not type(value) for name, value in expected.items())):
        raise ValueError("The Windows app entry receipt is incomplete; inspect before removal.")
    return row


def _version_record(row, version):
    return {**row, "registry": {**row["registry"], "DisplayVersion": version}}


def _finish_version(home, result):
    try:
        (home / "config/services" / NOTE).unlink(missing_ok=True)
    except OSError:
        return {**result, "noteRetained": True}
    return result


def _restore_version(root, earlier):
    from .deployment import write_private
    try:
        write_private(root / FILE, json.dumps(earlier), owned_root=root)
    except (OSError, ValueError, RuntimeError):
        pass
    # A write returning (or raising) does not prove what is now on disk.
    try:
        return _receipt(root) == earlier
    except (OSError, ValueError, RuntimeError):
        return False


def set_version(root, version, *, home) -> dict:
    """Record a confirmed app version without changing shortcuts or other values."""
    from .deployment import write_private
    from .windows_files import address
    root, home = address(root), address(home)
    try:
        earlier = _entry_record(root)
    except (OSError, ValueError, RuntimeError):
        return {"changed": False, "reason": "invalid-record"}
    if earlier["status"] != "installed":
        return {"changed": False, "reason": "not-installed"}
    try:
        registration_values(root, version)
    except ValueError:
        return {"changed": False, "reason": "invalid-version"}
    previous = earlier["registry"]["DisplayVersion"]
    if previous == version:
        return {"changed": False, "reason": "current"}
    try:
        if registry(root) != _typed_registry(earlier["registry"]):
            return {"changed": False, "reason": "registry-changed"}
        note = {"schema": 1, "root": str(root), "from": previous, "to": version}
        write_private(home / "config/services" / NOTE, json.dumps(note), owned_root=home)
    except (OSError, ValueError, RuntimeError):
        return {"changed": False, "reason": "failed"}
    try:
        write_private(root / FILE, json.dumps(_version_record(earlier, version)), owned_root=root)
    except PermissionError:
        return _finish_version(home, {"changed": False, "reason": "refused"})
    except (OSError, ValueError, RuntimeError):
        return {"changed": False, "reason": "failed", "noteRetained": True}
    try:
        registry_version(root, earlier["registry"], version)
    except (OSError, ValueError, RuntimeError) as error:
        result = {"changed": False, "reason": "refused" if isinstance(error, PermissionError) else "failed"}
        if _restore_version(root, earlier):
            return _finish_version(home, result)
        return {**result, "noteRetained": True}
    return _finish_version(home, {"changed": True, "from": previous, "to": version})


def settle_version(root, *, home) -> dict | None:
    """Settle an interrupted record/key write from the files present at startup."""
    from .windows_files import address, directory
    root, home = address(root), address(home)
    try:
        with directory(home / "config/services", owned_root=home) as owned:
            note = json.loads(owned.read_private(NOTE, limit=65536))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RuntimeError):
        return _finish_version(home, {"changed": False, "state": "unreadable-note"})
    try:
        if (not isinstance(note, dict) or set(note) != {"schema", "root", "from", "to"}
                or type(note["schema"]) is not int or note["schema"] != 1
                or not isinstance(note["root"], str)):
            raise ValueError("The Apps-list version note is invalid.")
        registration_values(root, note["from"])
        registration_values(root, note["to"])
    except ValueError:
        return _finish_version(home, {"changed": False, "state": "invalid-note"})
    if note["root"] != str(root):
        return _finish_version(home, {"changed": False, "state": "foreign-note"})
    try:
        row = _entry_record(root)
        live = registry(root)
    except (OSError, ValueError, RuntimeError):
        return _finish_version(home, {"changed": False, "state": "unreadable-entries"})
    version = row["registry"]["DisplayVersion"]
    if row["status"] != "installed" or version not in (note["from"], note["to"]):
        return _finish_version(home, {"changed": False, "state": "discarded"})
    if live == _typed_registry(row["registry"]):
        state = "agreed-from" if version == note["from"] else "agreed-to"
        return _finish_version(home, {"changed": False, "state": state})
    if version == note["to"]:
        earlier = _version_record(row, note["from"])
        if live == _typed_registry(earlier["registry"]):
            try:
                registry_version(root, earlier["registry"], note["to"])
            except (OSError, ValueError, RuntimeError):
                pass
            else:
                return _finish_version(home, {"changed": True, "state": "completed",
                                              "from": note["from"], "to": note["to"]})
        if _restore_version(root, earlier):
            return _finish_version(home, {"changed": False, "state": "rolled-back"})
        return {"changed": False, "state": "rollback-pending", "noteRetained": True}
    return _finish_version(home, {"changed": False, "state": "discarded"})


def listed(root) -> bool:
    """The Start-menu entry that opens Amplifier exists, and the entry record says so.

    The record alone is not enough. The last write of an uninstall can be
    refused after every entry is gone, and a person can delete the folder.
    """
    from .windows_files import address
    try:
        row = _receipt(address(root))
        # The folder that the record names: setup's second run reads the same one.
        if (not isinstance(row, dict) or row.get("status") != "installed"
                or not isinstance(row.get("folder"), str)):
            return False
        return (Path(row["folder"]) / next(iter(LINKS))).is_file()
    except (OSError, ValueError):
        return False


def _free(folder):
    """Setup can add its entries at this Start-menu folder's path: none of them is there.

    True when nothing has that name. True also for the folder that stayed
    after an uninstall, when it is this account's own private folder (the
    check that every later step makes again) and no entry directly in it has
    one of the names of LINKS. Such an entry counts whatever it is (a file, a
    folder, a link), and names are compared without letter case. The folder
    stays when the person put a file into it, and when another program still
    had a shortcut open. An entry of another name is the person's: it is not
    opened, changed or removed, here or later.

    Every doubt is False: a link or a file at that path, a folder that
    another account owns or can change, a folder that cannot be listed.
    """
    from .windows_files import directory
    from .windows_removal import same_name
    if not os.path.lexists(folder):
        return True
    ours = {same_name(name) for name in LINKS}
    try:
        with directory(folder, owned_root=folder), os.scandir(folder) as listing:
            return not any(same_name(entry.name) in ours for entry in listing)
    except (OSError, ValueError, RuntimeError):
        return False


def install(root, version):
    from .windows_files import address, directory
    from .deployment import write_private
    root = address(root)
    manifest = root / FILE
    if manifest.exists():
        # The entries can be added again over the record of a finished
        # removal. A record that still says `installed` counts the same when
        # the entries are gone: the last write of an uninstall can be refused
        # after every entry is removed. "Gone" means the folder that this
        # record names, so that a record of entries in another place is never
        # replaced. The check below also refuses when the Apps-list entry
        # exists, or when today's Start-menu folder holds one of the
        # shortcuts or is not this account's own (`_free`). So nothing is
        # ever overwritten. Every other record is refused.
        try:
            row = _receipt(root)
            status = row.get("status") if isinstance(row, dict) else None
            again = status == "removed" or (
                status == "installed" and isinstance(row.get("folder"), str)
                and not Path(row["folder"]).exists())
        except (OSError, ValueError):
            again = False
        if not again:
            raise ValueError("Windows shell integration already has a receipt; inspect it before retrying.")
    folder = programs() / ("Amplifier Unified - " + identity(root))
    # Before the first write: a refusal here leaves the record as it was.
    if registry(root) is not None or not _free(folder):
        raise ValueError("The Windows app entries already exist; nothing was overwritten.")
    python, links = entries(root)
    values = registration_values(root, version)
    row = {"schema": 1, "root": str(root), "folder": str(folder), "status": "preparing",
           "links": {}, "registry": values}
    write_private(manifest, json.dumps(row), owned_root=root)
    with directory(folder, owned_root=folder, create=True) as owned:
        for name, args in links.items():
            path = folder / name
            # The private directory is pinned, no pre-existing file is adopted:
            # the writer refuses a name that exists, also one that appeared
            # after the check above.
            owned.publish_private(name, b"")
            with shell_link() as link:
                link.create(path, python, args, root / "workspace")
                if link.read(path) != (str(python), args):
                    raise ValueError("The actual shortcut binding differs from its intended entry.")
            row["links"][name] = hashlib.sha256(owned.read_private(name, limit=65536)).hexdigest()
            write_private(manifest, json.dumps(row), owned_root=root)
    registry(root, values)
    row["status"] = "installed"
    write_private(manifest, json.dumps(row), owned_root=root)
    return {"startMenu": str(folder), "installedAppEntry": True, "shortcuts": len(links)}


def _held(code):
    """Try again, for a bounded time, while another program has a shortcut open.

    Windows refuses the pin of a shortcut with error 32 while another open of
    that file does not share deleting; the Windows shell opens a new shortcut
    like that for a moment. It refuses the delete through the pinned handle
    with error 5 while a view of the file is mapped. It gives the same error 5
    for a shortcut with a read-only attribute, which no wait cures: that one
    stops the uninstall after the wait. Only a PermissionError that carries
    exactly `code` is tried again. Every other refusal, and a shortcut that
    differs from setup's record, raises at once.

    The returned function counts its waits for all shortcuts of one step
    together: HOLD_SECONDS in all, a tenth of a second each. It limits the
    number of waits, not the time of the step: the Windows calls of each
    attempt take their own time.
    """
    waits = [HOLD_SECONDS * 10]
    def attempt(action):
        while True:
            try:
                return action()
            except PermissionError as refused:
                if getattr(refused, "winerror", None) != code or waits[0] <= 0:
                    raise
            waits[0] -= 1
            time.sleep(0.1)
    return attempt


@contextmanager
def removal(root, *, progress=None):
    """Prepare removal of this installation's own entries; `finish` removes them.

    Entering changes nothing. It checks and pins every entry that still exists,
    so the caller can stop the app first and remove the entries last. An entry
    that an earlier, interrupted removal already took away is skipped: a second
    uninstall completes. An entry that differs from setup's record refuses.
    A shortcut that another program has open is tried again, in the pin and in
    the delete, with waits of HOLD_SECONDS in all for each (`_held`).

    `progress(name)` names the step that is about to run: `apps-entry`,
    `shortcuts`, `receipt`. `finish` releases the pins itself, so the entries
    are gone before it records the removal.
    """
    from .windows_files import address, directory
    from .deployment import write_private
    step = progress or (lambda name: None)
    root = address(root)
    row = _entry_record(root)
    folder = Path(row["folder"])
    python, links = entries(root)
    result = {"shortcutsRemoved": True, "installedAppEntryRemoved": True, "userDataRetained": True}
    if row["status"] == "removed":
        # A completed earlier removal. A second removal changes nothing.
        yield lambda: dict(result)
        return
    with ExitStack() as stack:
        removers, commit = [], None
        try:
            owned = stack.enter_context(directory(folder, owned_root=folder))
        except FileNotFoundError:
            owned = None  # The whole Start-menu folder is already gone.
        # Keep ALL verified handles (deny WRITE/DELETE sharing) through effects.
        pin = _held(32)
        for name, args in links.items() if owned else ():
            if not (folder / name).exists():
                continue
            with shell_link() as link:
                if link.read(folder / name) != (str(python), args):
                    raise ValueError("A Windows shortcut target changed; no entries were removed.")
            # COM can open without DELETE sharing. Close it first, then pin the
            # exact audited bytes: a change between those reads is refused.
            # Each further attempt reads and compares the bytes again.
            removers.append(pin(lambda: stack.enter_context(owned.removal_private(name, row["links"][name]))))
        try:
            commit = stack.enter_context(registry_change(root, row["registry"], remove=True))
        except FileNotFoundError:
            pass  # The installed-app entry is already gone.
        def finish():
            # The commit can refuse because another program edited the key; it
            # runs before any shortcut is removed. Later genuine I/O failures
            # can leave partial effects, which the next removal completes.
            step("apps-entry")
            if commit is not None:
                commit()
            step("shortcuts")
            delete = _held(5)
            for remove_link in removers:
                delete(remove_link)
            stack.close()  # Release the pins: Windows removes the shortcuts now.
            try:
                os.rmdir(folder)  # Only when empty; a file the person added stays.
            except OSError:
                pass
            step("receipt")
            row["status"] = "removed"
            # Setup reads this status on a later run. Every entry is gone, so a
            # refused write does not fail the removal; it is reported instead.
            for attempt in range(max(1, SETTLE_SECONDS * 2)):
                try:
                    write_private(root / FILE, json.dumps(row), owned_root=root)
                    return dict(result)
                except PermissionError as refused:
                    # The app's worker programs end a moment after its task
                    # reports that it stopped. Until then Windows refuses a
                    # replaced file in the installation folder.
                    error = refused
                    if attempt < SETTLE_SECONDS * 2 - 1:
                        time.sleep(0.5)
                except (OSError, ValueError, RuntimeError) as failed:
                    error = failed
                    break
            return {**result, "receiptError": error}
        yield finish


def remove(root):
    with removal(root) as finish:
        return finish()
