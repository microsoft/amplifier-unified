"""Per-user Windows shell entries for one qualified consumer installation.

Native ShellLink COM and HKCU only: no PowerShell, administrator, machine PATH,
trust import or external runtime. Unknown/colliding effects are retained, not
silently overwritten. Removal unregisters this app and preserves user files.
"""
from __future__ import annotations

from contextlib import contextmanager
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


def registry(root, values=None, *, remove=False):
    import winreg
    key = REGISTRY + identity(root)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
            existing = {}
            for index in range(winreg.QueryInfoKey(handle)[1]):
                name, value, kind = winreg.EnumValue(handle, index)
                existing[name] = (value, kind)
    except FileNotFoundError:
        existing = None
    if remove:
        if existing is None: return
        expected = {name: (value, winreg.REG_DWORD if type(value) is int else winreg.REG_SZ)
                    for name, value in values.items()}
        if existing != expected:
            raise ValueError("The installed-app entry changed; removal refused.")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    elif values is not None:
        if existing is not None:
            raise ValueError("The installed-app entry already exists; inspect it before retrying.")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            for name, value in values.items():
                winreg.SetValueEx(handle, name, 0, winreg.REG_DWORD if type(value) is int else winreg.REG_SZ, value)
    return existing


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


def remove(root):
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
    with directory(folder, owned_root=folder) as owned:
        # Validate ALL links before removing the app registration or any file.
        for name, args in links.items():
            raw = owned.read_private(name, limit=65536)
            if hashlib.sha256(raw).hexdigest() != row["links"][name]:
                raise ValueError("A Windows shortcut changed; no entries were removed.")
            with shell_link() as link:
                if link.read(folder / name) != (str(python), args):
                    raise ValueError("A Windows shortcut target changed; no entries were removed.")
        registry(root, row["registry"], remove=True)
        for name in links:
            owned.remove_private(name, row["links"][name])
    row["status"] = "removed"
    write_private(root / FILE, json.dumps(row), owned_root=root)
    # The empty private folder and all installation/user files are retained.
    return {"shortcutsRemoved": True, "installedAppEntryRemoved": True, "userDataRetained": True}
