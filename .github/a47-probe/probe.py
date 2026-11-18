"""A47 probe. Temporary, never merged.

Question: on a hosted Windows runner, which process holds a new Start-menu
shortcut of a throwaway installation, when after its creation, and how long?

It makes the real entries with the product's own `install`, then:
- `controlled`: a second handle and a mapped view on purpose (expected: 32 at
  the pin, 5 at the delete);
- `passive`: asks Windows which processes have each file open, with a handle
  that takes no part in sharing;
- `active`: the product's exact pin open, again and again;
- `com`: the product's order (read through ShellLink COM, then pin);
- `plain`: the product's whole removal once, at a chosen delay after creation.
"""
import ctypes as C
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from amplifier_web import windows_consumer_shell as shell, windows_files  # noqa: E402

k32 = C.WinDLL("kernel32", use_last_error=True)
ntdll = C.WinDLL("ntdll")
HANDLE = C.c_void_p
k32.CreateFileW.restype = HANDLE
k32.CreateFileW.argtypes = [C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p, C.c_uint32, C.c_uint32, HANDLE]
k32.CloseHandle.argtypes = [HANDLE]
k32.CloseHandle.restype = C.c_int
k32.SetFileInformationByHandle.argtypes = [HANDLE, C.c_int, C.c_void_p, C.c_uint32]
k32.SetFileInformationByHandle.restype = C.c_int
k32.SetFileAttributesW.argtypes = [C.c_wchar_p, C.c_uint32]
k32.SetFileAttributesW.restype = C.c_int
k32.OpenProcess.restype = HANDLE
k32.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
k32.QueryFullProcessImageNameW.argtypes = [HANDLE, C.c_uint32, C.c_wchar_p, C.POINTER(C.c_uint32)]
k32.QueryFullProcessImageNameW.restype = C.c_int
k32.ProcessIdToSessionId.argtypes = [C.c_uint32, C.POINTER(C.c_uint32)]
k32.ProcessIdToSessionId.restype = C.c_int
k32.CreateFileMappingW.restype = HANDLE
k32.CreateFileMappingW.argtypes = [HANDLE, C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32, C.c_wchar_p]
k32.MapViewOfFile.restype = C.c_void_p
k32.MapViewOfFile.argtypes = [HANDLE, C.c_uint32, C.c_uint32, C.c_uint32, C.c_size_t]
k32.UnmapViewOfFile.argtypes = [C.c_void_p]
k32.UnmapViewOfFile.restype = C.c_int
ntdll.NtQueryInformationFile.restype = C.c_int32
ntdll.NtQueryInformationFile.argtypes = [HANDLE, C.c_void_p, C.c_void_p, C.c_uint32, C.c_int]
INVALID = C.c_void_p(-1).value
OWN = os.getpid()
_NAMES = {}


def say(kind, **fields):
    print("A47_PROBE " + json.dumps({"kind": kind, **fields}, default=str), flush=True)


def bad(handle):
    return handle is None or handle == INVALID


def session(pid):
    value = C.c_uint32()
    return value.value if k32.ProcessIdToSessionId(pid, C.byref(value)) else None


def name(pid):
    if pid not in _NAMES:
        text = f"pid-{pid}"
        handle = k32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            try:
                buffer, size = C.create_unicode_buffer(1024), C.c_uint32(1024)
                if k32.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(size)):
                    text = Path(buffer.value).name + f"(session {session(pid)})"
            finally:
                k32.CloseHandle(handle)
        _NAMES[pid] = text
    return _NAMES[pid]


def holders(path):
    """Process IDs that have this file open (FileProcessIdsUsingFileInformation), or None and the error."""
    # FILE_READ_ATTRIBUTES only: such an open takes no part in sharing checks.
    handle = k32.CreateFileW(str(path), 0x80, 7, None, 3, 0x2200000, None)
    if bad(handle):
        return None, C.get_last_error()
    try:
        buffer, block = (C.c_size_t * 130)(), (C.c_size_t * 2)()
        status = ntdll.NtQueryInformationFile(handle, block, buffer, C.sizeof(buffer), 47)
        if status != 0:
            return None, status & 0xFFFFFFFF
        count = buffer[0] & 0xFFFFFFFF
        return [int(buffer[1 + index]) for index in range(min(count, 128))], 0
    finally:
        k32.CloseHandle(handle)


def others(path):
    pids, error = holders(path)
    if pids is None:
        return [f"query-error-{error}"]
    return sorted(name(pid) for pid in pids if pid != OWN)


def pin(path):
    """The product's pin of a shortcut: GENERIC_READ | DELETE | READ_CONTROL | attributes, FILE_SHARE_READ."""
    handle = k32.CreateFileW(str(path), 0x20080 | 0x80000000 | 0x10000, 1, None, 3, 0x2200000, None)
    return (None, C.get_last_error()) if bad(handle) else (handle, 0)


def reader(path):
    """The product's read of a private file: GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE."""
    handle = k32.CreateFileW(str(path), 0x20080 | 0x80000000, 5, None, 3, 0x2200000, None)
    return (None, C.get_last_error()) if bad(handle) else (handle, 0)


def deletable(handle):
    """Ask for the delete and take it back at once. 0, or the Windows error of the refusal."""
    flag = C.c_ubyte(1)
    if not k32.SetFileInformationByHandle(handle, 4, C.byref(flag), 1):
        return C.get_last_error()
    flag = C.c_ubyte(0)
    if not k32.SetFileInformationByHandle(handle, 4, C.byref(flag), 1):
        return -C.get_last_error()
    return 0


def make():
    root = windows_files.address(Path.home() / ("au-probe-" + uuid.uuid4().hex[:10]))
    with windows_files.directory(root, owned_root=root, create=True):
        pass
    python, _ = shell.entries(root)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"MZ")
    shell.install(root, "1.0.0")
    with windows_files.directory(root / "app" / "config" / "services", owned_root=root, create=True):
        pass
    folder = shell.programs() / ("Amplifier Unified - " + shell.identity(root))
    return root, folder, time.perf_counter()


def failure(error):
    return {"type": type(error).__name__, "winerror": getattr(error, "winerror", None), "text": str(error)[:200]}


def clean(root, folder):
    import winreg
    try:
        shell.remove(root)
        outcome = "removed"
    except Exception as error:  # noqa: BLE001  (a probe records everything)
        outcome = failure(error)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, shell.REGISTRY + shell.identity(root))
    except OSError:
        pass
    shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)
    return outcome


def watched(root, folder):
    return [folder / link for link in shell.LINKS] + [root / shell.FILE]


def passive(files, start, window):
    seen, out, polls = {}, [], 0
    while True:
        now = time.perf_counter() - start
        if now >= window:
            break
        polls += 1
        for file in files:
            pids, _ = holders(file)
            current = set(pids or ()) - {OWN}
            for pid in current:
                if (file.name, pid) not in seen:
                    seen[(file.name, pid)] = now
                    name(pid)
            for key in [key for key in seen if key[0] == file.name and key[1] not in current]:
                out.append({"file": key[0], "process": name(key[1]), "from": round(seen.pop(key), 4), "to": round(now, 4)})
    for key, since in seen.items():
        out.append({"file": key[0], "process": name(key[1]), "from": round(since, 4), "to": None})
    return out, polls


def active(files, start, window, com=False):
    refused, undeletable, out, polls = {}, {}, [], 0
    while True:
        now = time.perf_counter() - start
        if now >= window:
            break
        polls += 1
        for file in files:
            link = file.suffix == ".lnk"
            if com and not link:
                continue
            if com:
                try:
                    with shell.shell_link() as owner:
                        owner.read(file)
                except OSError as error:
                    out.append({"file": file.name, "what": "com-read", "at": round(now, 4), **failure(error)})
            handle, code = pin(file) if link else reader(file)
            if handle is None:
                if file.name not in refused:
                    refused[file.name] = (now, code, others(file))
                continue
            if link and polls % 8 == 0:
                blocked = deletable(handle)
                if blocked and file.name not in undeletable:
                    undeletable[file.name] = (now, blocked, others(file))
                elif not blocked and file.name in undeletable:
                    since, code, who = undeletable.pop(file.name)
                    out.append({"file": file.name, "what": "delete-refused", "code": code, "holders": who,
                                "from": round(since, 4), "to": round(now, 4)})
            k32.CloseHandle(handle)
            if file.name in refused:
                since, code, who = refused.pop(file.name)
                out.append({"file": file.name, "what": "open-refused", "code": code, "holders": who,
                            "from": round(since, 4), "to": round(now, 4)})
    for table, what in ((refused, "open-refused"), (undeletable, "delete-refused")):
        for file, (since, code, who) in table.items():
            out.append({"file": file, "what": what, "code": code, "holders": who, "from": round(since, 4), "to": None})
    return out, polls


def cycle(mode, index, window):
    root, folder, start = make()
    try:
        files = watched(root, folder)
        if mode == "passive":
            events, polls = passive(files, start, window)
        else:
            events, polls = active(files, start, window, com=mode == "com")
    finally:
        removal = clean(root, folder)
    say("cycle", mode=mode, index=index, polls=polls, events=events[:120], more=max(0, len(events) - 120),
        removal=removal)
    return events


def plain(index, delay):
    """The product's removal once, `delay` seconds after the entries were made. No polling before it."""
    root, folder, start = make()
    notes = []
    opened, discard = windows_files._API.open, windows_files._API.discard

    def open_(self, path, **options):
        try:
            return opened(self, path, **options)
        except OSError as error:
            if getattr(error, "winerror", None) in (5, 32):
                notes.append({"call": "open", "file": Path(str(path)).name, "options": sorted(options),
                              "holders": others(path), **failure(error)})
            raise

    def discard_(self, handle, path, identity):
        try:
            return discard(self, handle, path, identity)
        except OSError as error:
            notes.append({"call": "discard", "file": Path(str(path)).name, "holders": others(path), **failure(error)})
            raise

    windows_files._API.open, windows_files._API.discard = open_, discard_
    steps = []
    try:
        time.sleep(max(0.0, delay - (time.perf_counter() - start)))
        at = round(time.perf_counter() - start, 4)
        try:
            with shell.removal(root, progress=steps.append) as finish:
                finish()
            outcome = "removed"
        except Exception as error:  # noqa: BLE001
            outcome = failure(error)
    finally:
        windows_files._API.open, windows_files._API.discard = opened, discard
        left = sorted(path.name for path in folder.iterdir()) if folder.exists() else None
        clean(root, folder)
    if outcome != "removed" or notes:
        say("plain", index=index, delay=delay, at=at, outcome=outcome, steps=steps, notes=notes, left=left)
    return outcome == "removed"


def attempt(root, folder):
    """The product's removal once: what it raised, at which step, and what is left."""
    steps = []
    try:
        with shell.removal(root, progress=steps.append) as finish:
            finish()
        outcome = "removed"
    except Exception as error:  # noqa: BLE001
        outcome = failure(error)
    return {"outcome": outcome, "steps": steps,
            "left": sorted(path.name for path in folder.iterdir()) if folder.exists() else None,
            "appsEntry": shell.registry(root) is not None}


def controlled():
    """The two stops on purpose, and what else gives the same errors."""
    target = "Amplifier status.lnk"  # The second of four in the removal's order.
    # 1. A reader without delete sharing: GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE.
    root, folder, _ = make()
    try:
        second = k32.CreateFileW(str(folder / target), 0x80000000, 3, None, 3, 0, None)
        assert not bad(second), C.get_last_error()
        first = attempt(root, folder)
        k32.CloseHandle(second)
        say("controlled", case="second handle, no delete sharing", held=first, afterRelease=attempt(root, folder))
    finally:
        clean(root, folder)
    # 2. A reader with every sharing right keeps its handle open: no refusal expected.
    root, folder, _ = make()
    try:
        second = k32.CreateFileW(str(folder / target), 0x80000000, 7, None, 3, 0, None)
        assert not bad(second), C.get_last_error()
        first = attempt(root, folder)
        k32.CloseHandle(second)
        say("controlled", case="second handle, all sharing", held=first)
    finally:
        clean(root, folder)
    # 3. A mapped view. The file handle and the mapping handle are closed; the view stays.
    for keep_handle in (False, True):
        root, folder, _ = make()
        try:
            second = k32.CreateFileW(str(folder / target), 0x80000000, 7, None, 3, 0, None)
            assert not bad(second), C.get_last_error()
            mapping = k32.CreateFileMappingW(second, None, 2, 0, 0, None)  # PAGE_READONLY
            assert mapping, C.get_last_error()
            view = k32.MapViewOfFile(mapping, 4, 0, 0, 0)  # FILE_MAP_READ
            assert view, C.get_last_error()
            k32.CloseHandle(mapping)
            if not keep_handle:
                k32.CloseHandle(second)
            who = others(folder / target)
            first = attempt(root, folder)
            k32.UnmapViewOfFile(view)
            if keep_handle:
                k32.CloseHandle(second)
            say("controlled", case="mapped view" + (", handle kept" if keep_handle else ", handle closed"),
                seenByQuery=who, held=first, afterRelease=attempt(root, folder))
        finally:
            clean(root, folder)
    # 4. A read-only attribute, set by a path while the product can already hold its pin.
    root, folder, _ = make()
    try:
        assert k32.SetFileAttributesW(str(folder / target), 0x1), C.get_last_error()
        first = attempt(root, folder)
        k32.SetFileAttributesW(str(folder / target), 0x80)
        say("controlled", case="read-only attribute", held=first, afterRelease=attempt(root, folder))
    finally:
        clean(root, folder)


def main():
    say("start", pid=OWN, session=session(OWN), python=sys.version.split()[0], programs=str(shell.programs()))
    try:
        controlled()
    except Exception as error:  # noqa: BLE001
        say("controlled-failed", **failure(error))
    everything = []
    for mode, count, window in (("passive", 10, 8.0), ("active", 10, 8.0), ("com", 4, 5.0)):
        for index in range(count):
            try:
                everything += [{"mode": mode, **event} for event in cycle(mode, index, window)]
            except Exception as error:  # noqa: BLE001
                say("cycle-failed", mode=mode, index=index, **failure(error))
    fine = 0
    for index in range(40):
        try:
            fine += plain(index, (index % 20) * 0.3)
        except Exception as error:  # noqa: BLE001
            say("plain-failed", index=index, **failure(error))
    summary = {}
    for event in everything:
        who = event.get("process") or ",".join(event.get("holders") or ["nobody else"])
        key = f"{event['mode']} | {event.get('what', 'open')} | {event['file']} | {who}"
        row = summary.setdefault(key, {"count": 0, "earliest": event["from"], "latest": event["from"], "longest": 0.0})
        row["count"] += 1
        row["earliest"], row["latest"] = min(row["earliest"], event["from"]), max(row["latest"], event["from"])
        if event.get("to") is not None:
            row["longest"] = max(row["longest"], round(event["to"] - event["from"], 4))
    say("summary", plainRemoved=fine, plainCycles=40, rows=summary)


if __name__ == "__main__":
    main()
