"""Small per-installation commands and one opt-out-respecting user PATH addition.

Only HKCU is used. Registry transactions refuse concurrent edits rather than
overwriting them. All entry points are best effort; none can fail its caller.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import json
import logging
import ntpath
import os
from pathlib import Path
import re
import shlex
import sys

RECORD = "cli-path.json"
LIMIT = 32767  # Environment value, including its terminating NUL, in UTF-16 units.
NAMES = {"amplifier-unified.cmd", "amplifier-unified"}
log = logging.getLogger(__name__)


def _length(value):
    return len(value.encode("utf-16-le")) // 2 + 1


def _key(value):
    # Expand for comparison ONLY. Never publish the expanded user's value.
    env = {key.casefold(): item for key, item in os.environ.items()}
    value = value.strip().strip('"')
    value = re.sub(r"%([^%]+)%", lambda m: env.get(m[1].casefold(), m[0]), value)
    return ntpath.normcase(value.replace("/", "\\").rstrip("\\"))


def append(value, kind, folder):
    """Return a new value, or None. Keep all original text and its registry type."""
    if kind not in (1, 2) or not isinstance(value, str) or "\0" in value:
        raise ValueError("The user Path is not a string.")
    if any(_key(entry) == _key(folder) for entry in value.split(";")):
        return None
    result = value + (";" if value else "") + folder
    if _length(result) > LIMIT:
        raise ValueError("The user Path is too long. Nothing was cut.")
    return result


def scripts(home, executable):
    """Use the batch file's own folder: no code-page-dependent absolute literals."""
    home, executable = Path(home), Path(executable)
    relative = os.path.relpath(executable, home / "bin")
    # CMD cannot safely store every possible path literal. Data-folder special
    # characters are expanded once by %~dp0, inside quotes, with delayed
    # expansion off. A literal in the relative executable path must be plain.
    if (any(c in str(home) for c in ';"\r\n\0')
            or not relative.isascii() or any(c in relative for c in '%!^&"\r\n\0')):
        raise ValueError("This folder cannot hold the Windows command files.")
    cmd = ('@echo off\r\n@setlocal DisableDelayedExpansion\r\n'
           '@for %%I in ("%~dp0..") do @set "AMPLIFIER_CLI_HOME=%%~fI"\r\n'
           f'@"%~dp0{relative}" --data-dir "%AMPLIFIER_CLI_HOME%" %*\r\n'
           '@exit /b %errorlevel%\r\n')
    bash = ('#!/bin/sh\nbin=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit\n'
            'home=$(CDPATH= cd -- "$bin/.." && pwd -W) || exit\n'
            f'exec "$bin"/{shlex.quote(relative.replace(chr(92), "/"))} '
            '--data-dir "$home" "$@"\n')
    return {"amplifier-unified.cmd": cmd.encode("ascii"),
            "amplifier-unified": bash.encode("ascii")}


@contextmanager
def environment(*, write=False):
    """Read HKCU, or stage a conflict-detecting edit of its existing Environment key."""
    import winreg
    if not write:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            yield handle, None
        return
    ktm, adv = C.WinDLL("ktmw32", use_last_error=True), C.WinDLL("advapi32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    ktm.CreateTransaction.argtypes = [C.c_void_p, C.c_void_p, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.LPWSTR]
    ktm.CreateTransaction.restype = W.HANDLE
    for name in ("CommitTransaction", "RollbackTransaction"):
        getattr(ktm, name).argtypes, getattr(ktm, name).restype = [W.HANDLE], W.BOOL
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [W.HANDLE], W.BOOL
    adv.RegOpenKeyTransactedW.argtypes = [
        W.HKEY, W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(W.HKEY), W.HANDLE, C.c_void_p]
    adv.RegOpenKeyTransactedW.restype = W.LONG
    transaction = ktm.CreateTransaction(None, None, 0, 0, 0, 0, None)
    if not transaction or transaction == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    handle, committed = W.HKEY(), False
    try:
        hive = W.HKEY(C.c_int32(int(winreg.HKEY_CURRENT_USER)).value)
        error = adv.RegOpenKeyTransactedW(hive, "Environment", 0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE, C.byref(handle), transaction, None)
        if error:
            raise C.WinError(error)
        def commit():
            nonlocal committed
            if not ktm.CommitTransaction(transaction):
                raise C.WinError(C.get_last_error())
            committed = True
        yield handle.value, commit
    finally:
        if handle.value:
            winreg.CloseKey(handle.value)
        if not committed:
            ktm.RollbackTransaction(transaction)
        kernel.CloseHandle(transaction)


def _read(handle):
    import winreg
    try:
        return winreg.QueryValueEx(handle, "Path")
    except FileNotFoundError:
        return None, 2  # An absent value is not the same as an empty value.


def _set(handle, value, kind):
    import winreg
    if value is None:
        winreg.DeleteValue(handle, "Path")
    else:
        winreg.SetValueEx(handle, "Path", 0, kind, value)


def _broadcast():
    send = C.WinDLL("user32", use_last_error=True).SendMessageTimeoutW
    send.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPCWSTR, W.UINT, W.UINT, C.POINTER(C.c_size_t)]
    send.restype = W.LPARAM
    # HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG; 100 ms per window.
    send(0xffff, 0x1a, 0, "Environment", 2, 100, C.byref(C.c_size_t()))


def _load(home):
    from .windows_files import directory
    with directory(home / "config/services", owned_root=home) as owned:
        row = json.loads(owned.read_private(RECORD, limit=512 * 1024))
    if (not isinstance(row, dict) or row.get("schema") != 1
            or row.get("folder") != str(home / "bin")
            or row.get("state") not in {"files", "adding", "added", "present", "removed"}):
        raise ValueError("The Windows command record is invalid.")
    if row["state"] in {"adding", "added"}:
        before = row.get("before")
        if (row.get("kind") not in (1, 2) or (before is not None and not isinstance(before, str))
                or row.get("after") != (before + ";" if before else "") + row["folder"]):
            raise ValueError("The Windows PATH addition record is invalid.")
    return row


def _save(home, row):
    from .deployment import write_private
    write_private(home / "config/services" / RECORD, json.dumps(row), owned_root=home)


def _ensure(home, executable):
    from .windows_files import directory
    from .deployment import write_private
    content = scripts(home, executable)
    try:
        row = _load(home)
    except FileNotFoundError:
        row = {"schema": 1, "folder": str(home / "bin"), "state": "files"}
        with directory(home / "bin", owned_root=home, create=True):
            if list((home / "bin").iterdir()):
                raise ValueError("The command folder is not empty.")
        _save(home, row)  # No registry effect when a private record cannot be written.
    with directory(home / "bin", owned_root=home, create=True) as owned:
        if any(path.name not in NAMES for path in (home / "bin").iterdir()):
            raise ValueError("The command folder holds other files.")
        for name, raw in content.items():
            try:
                current = owned.read_private(name, limit=8192)
            except FileNotFoundError:
                current = None
            if current != raw:
                write_private(home / "bin" / name, raw, owned_root=home)
    if row["state"] != "files":
        return  # Includes manual removal and an interrupted/uncertain registry write.
    with environment(write=True) as (handle, commit):
        before, kind = _read(handle)
        after = append("" if before is None else before, kind, row["folder"])
        row.update(state="present" if after is None else "adding",
                   before=before, kind=kind, after=after)
        _save(home, row)  # Once only, even if the process ends during the registry commit.
        if after is None:
            return
        _set(handle, after, kind)
        commit()
    row["state"] = "added"
    try:
        _save(home, row)
    finally:
        _broadcast()


def _remove(home):
    row = _load(home)
    if row["state"] not in {"added", "adding"}:
        return
    with environment(write=True) as (handle, commit):
        value, kind = _read(handle)
        if value == row["after"] and kind == row["kind"]:
            result = row["before"]
        elif row["state"] == "adding":
            return  # Interrupted commit: a changed value does not prove ownership.
        elif isinstance(value, str) and kind in (1, 2):
            parts = value.split(";")
            exact = [index for index, entry in enumerate(parts) if entry == row["folder"]]
            if len(exact) != 1:
                return  # A changed spelling or duplicates are not adopted.
            del parts[exact[0]]
            result = ";".join(parts)
        else:
            return
        _set(handle, result, kind)
        commit()
    row["state"] = "removed"
    try:
        _save(home, row)
    finally:
        _broadcast()


def _best_effort(home, action):
    if sys.platform != "win32":
        return
    try:
        from .windows_files import address
        from .windows_uninstall import named
        home = address(home)
        # A busy setup/start must not wait for another optional command step.
        with named("Global\\AmplifierUnified-cli-path-", home, wait=0):
            action(home)
    except Exception as error:
        # No exception text: native errors can contain private paths.
        log.warning("Windows command setup did not complete (%s).", type(error).__name__)


def ensure(home):
    """Setup and each host start use their own app version, including after rollback."""
    _best_effort(home, lambda folder: _ensure(
        folder, Path(sys.prefix) / "Scripts/amplifier-unified.exe"))


def remove(home):
    """Optional uninstall step. A refusal never changes the uninstall's result."""
    _best_effort(home, _remove)


def doctor(home):
    folder = str(Path(home) / "bin")
    try:
        with environment() as (handle, _):
            value, kind = _read(handle)
        if isinstance(value, str) and kind in (1, 2) and any(
                _key(entry) == _key(folder) for entry in value.split(";")):
            return {"ok": True, "message": "Command folder is on your user PATH. "
                    "The first matching command on PATH is used. Open a new terminal window."}
    except Exception:
        pass
    return {"ok": False, "message": f"Command folder is not confirmed on your user PATH. "
            f"Add {folder} to your user Path in Environment Variables. Open a new terminal window."}