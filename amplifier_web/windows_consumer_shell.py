"""Per-user Windows shell entries for one qualified consumer installation.

Native ShellLink COM and HKCU only: no PowerShell, administrator, machine PATH,
trust import or external runtime. Unknown/colliding effects are retained, not
silently overwritten. Removal unregisters this app and preserves user files.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

FILE = "consumer-shell.json"
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


@contextmanager
def registry_change(root, values, *, remove=False):
    """Prepare a conflict-detecting native registry transaction, then commit.

    A staged delete holds the key against same-account edits/replacement while
    the caller validates its other objects. No compare/close/path-delete gap.
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
        if remove:
            error = adv.RegOpenKeyTransactedW(
                hive, key, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE | 0x10000,
                C.byref(handle), transaction, None)
        else:
            error = adv.RegCreateKeyTransactedW(
                hive, key, 0, None, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE,
                None, C.byref(handle), C.byref(disposition), transaction, None)
        if error:
            raise C.WinError(error)
        with winreg.HKEYType(handle.value) as opened:
            if remove:
                expected = {name: (value, winreg.REG_DWORD if type(value) is int else winreg.REG_SZ)
                            for name, value in values.items()}
                if _registry_values(opened) != expected:
                    raise ValueError("The installed-app entry changed; removal refused.")
                error = adv.RegDeleteKeyTransactedW(hive, key, 0, 0, transaction, None)
                if error:
                    raise C.WinError(error)
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
        try:
            if not committed and not ktm.RollbackTransaction(transaction):
                raise C.WinError(C.get_last_error())
        finally:
            if not kernel.CloseHandle(transaction):
                raise C.WinError(C.get_last_error())


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


def install(root, version):
    from .windows_files import address, directory
    from .deployment import write_private
    root = address(root)
    manifest = root / FILE
    if manifest.exists():
        raise ValueError("Windows shell integration already has a receipt; inspect it before retrying.")
    folder = programs() / ("Amplifier Unified - " + identity(root))
    if folder.exists() or registry(root) is not None:
        raise ValueError("The Windows app entries already exist; nothing was overwritten.")
    python, links = entries(root)
    values = registration_values(root, version)
    row = {"schema": 1, "root": str(root), "folder": str(folder), "status": "preparing",
           "links": {}, "registry": values}
    write_private(manifest, json.dumps(row), owned_root=root)
    with directory(folder, owned_root=folder, create=True) as owned:
        for name, args in links.items():
            path = folder / name
            # The private directory is pinned, no pre-existing file is adopted.
            with path.open("xb"): pass
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


@contextmanager
def removal(root):
    from .windows_files import address, directory
    from .deployment import write_private
    root = address(root)
    with directory(root, owned_root=root) as owned:
        row = json.loads(owned.read_private(FILE, limit=65536))
    folder = programs() / ("Amplifier Unified - " + identity(root))
    python, links = entries(root)
    if (not isinstance(row, dict) or set(row) != {"schema", "root", "folder", "status", "links", "registry"}
            or type(row["schema"]) is not int or row["schema"] != 1
            or row["root"] != str(root) or row["folder"] != str(folder)
            or row["status"] != "installed" or not isinstance(row["links"], dict)
            or set(row["links"]) != set(links) or not isinstance(row["registry"], dict)
            or row["registry"] != registration_values(root, row["registry"].get("DisplayVersion"))):
        raise ValueError("The Windows app entry receipt is incomplete; inspect before removal.")
    with ExitStack() as stack:
        owned = stack.enter_context(directory(folder, owned_root=folder))
        removers = []
        # Keep ALL verified handles (deny WRITE/DELETE sharing) through effects.
        for name, args in links.items():
            with shell_link() as link:
                if link.read(folder / name) != (str(python), args):
                    raise ValueError("A Windows shortcut target changed; no entries were removed.")
            # COM can open without DELETE sharing. Close it first, then pin the
            # exact audited bytes: a change between those reads is refused.
            removers.append(stack.enter_context(owned.removal_private(name, row["links"][name])))
        commit = stack.enter_context(registry_change(root, row["registry"], remove=True))
        def finish():
            # Prepared conflicts refuse before task removal. Later genuine I/O
            # failures can leave partial effects; they do not imply rollback.
            commit()
            for remove_link in removers:
                remove_link()
            row["status"] = "removed"
            write_private(root / FILE, json.dumps(row), owned_root=root)
            return {"shortcutsRemoved": True, "installedAppEntryRemoved": True, "userDataRetained": True}
        yield finish


def remove(root):
    with removal(root) as finish:
        return finish()
