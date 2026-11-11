"""Private, generation-bound control for the owner-logon host.

Scheduler state alone is not process identity. A receipt names the actual
interpreter PID/FILETIME/image and one random run identity. Stop requests match
that entire receipt; they never signal a PID or forcibly stop a scheduler task.
Only current TokenUser/SYSTEM can read or publish these files.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import ctypes as C
import io
import json
import os
from pathlib import PureWindowsPath
import re
import socket
import threading
import time
import uuid

LIMIT = 8192
RECEIPT = "task-runtime.json"
STOP = "task-stop.json"
RESTART = "task-restart.json"
RESTART_EXIT = 75
LOG = "task-output.log"
LOG_LIMIT = 128 * 1024
_FIELDS = {"schema", "registration", "run", "pid", "created", "image", "phase"}
_PHASES = {"starting", "listening", "stopping", "stopped", "failed"}


def validate(value, registration):
    """Closed public metadata; no environment, arguments or error text."""
    if (not isinstance(value, dict) or set(value) != _FIELDS
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["registration"] != registration
            or not isinstance(registration, str)
            or not re.fullmatch("[a-f0-9]{32}", registration)
            or not isinstance(value["run"], str)
            or not re.fullmatch("[a-f0-9]{32}", value["run"])
            or type(value["pid"]) is not int or not 0 < value["pid"] <= 0xFFFFFFFF
            or not isinstance(value["created"], str)
            or not re.fullmatch("[1-9][0-9]{0,19}", value["created"])
            or int(value["created"]) > 0xFFFFFFFFFFFFFFFF
            or not isinstance(value["phase"], str) or value["phase"] not in _PHASES):
        raise ValueError("Invalid Windows task runtime receipt.")
    image = value["image"]
    if (not isinstance(image, str) or len(image) > 32768
            or not re.fullmatch(r"[A-Za-z]:\\[^\x00-\x1f]+", image)
            or PureWindowsPath(image).name.lower() not in {"python.exe", "pythonw.exe"}):
        raise ValueError("Invalid Windows task interpreter identity.")
    return dict(value)


def _root(spec):
    from .windows_files import address
    return address(spec.data_dir)


def read(spec, name=RECEIPT):
    from .windows_files import directory
    root = _root(spec)
    try:
        with directory(root / "config/services", owned_root=root, create=False) as pinned:
            raw = pinned.read_private(name, limit=LIMIT)
    except FileNotFoundError:
        return None
    value = json.loads(raw)
    if isinstance(value, dict) and value.get("registration") != spec.registration_id:
        # A removed task may leave its last runtime/stop record. Validate the
        # closed old protocol, but never adopt it into a new registration.
        validate(value, value.get("registration"))
        return None
    return validate(value, spec.registration_id)


def publish(spec, value, name=RECEIPT):
    from .windows_files import directory
    value = validate(value, spec.registration_id)
    root = _root(spec)
    with directory(root / "config/services", owned_root=root, create=True) as pinned:
        pinned.publish_private(name, json.dumps(value).encode("utf-8"), replace=True)


def restart_record(value, registration):
    if (not isinstance(value, dict) or set(value) != {"schema", "run", "target"}
            or type(value["schema"]) is not int or value["schema"] != 1):
        raise ValueError("Invalid Windows task restart request.")
    run = validate(value["run"], registration)
    if run["phase"] != "listening":
        raise ValueError("Restart request must name a listening host.")
    target = value["target"]
    if (not isinstance(target, dict) or set(target) != {"revision", "generation", "attemptId"}
            or any(not isinstance(target[key], str) or not re.fullmatch(pattern, target[key])
                   for key, pattern in (("revision", "[a-f0-9]{40}"),
                                        ("generation", "[a-f0-9]{32}"),
                                        ("attemptId", "[a-f0-9]{32}")))):
        raise ValueError("Invalid Windows task restart target.")
    return {"schema": 1, "run": run, "target": dict(target)}


def read_restart(spec):
    from .windows_files import directory
    root = _root(spec)
    try:
        with directory(root / "config/services", owned_root=root, create=False) as pinned:
            raw = pinned.read_private(RESTART, limit=LIMIT)
    except FileNotFoundError:
        return None
    value = json.loads(raw)
    registration = (value.get("run") or {}).get("registration") if isinstance(value, dict) and isinstance(value.get("run"), dict) else None
    result = restart_record(value, registration)
    return result if registration == spec.registration_id else None


def request_restart(spec, target):
    """One exact current-host signal. Supervisor/readiness own the outcome."""
    from .windows_files import directory
    run = read(spec)
    if run is None or run["phase"] != "listening" or run["pid"] != os.getpid():
        raise RuntimeError("Only the current listening task host may request an app restart.")
    with process(run["pid"]) as actual:
        if not matches(run, actual) or actual["exited"]():
            raise RuntimeError("Task host identity changed before its restart request.")
        value = restart_record({"schema": 1, "run": run, "target": target}, spec.registration_id)
        root = _root(spec)
        with directory(root / "config/services", owned_root=root, create=False) as pinned:
            pinned.publish_private(RESTART, json.dumps(value).encode(), replace=True)
    return value


@contextmanager
def process(pid):
    """Retain a query/SYNCHRONIZE handle; access denied is not absence."""
    if os.name != "nt":
        raise RuntimeError("Windows process identity requires native Windows.")
    from ctypes import wintypes as W
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.WaitForSingleObject.restype = W.DWORD
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        if C.get_last_error() == 87:
            yield None
            return
        raise C.WinError(C.get_last_error())
    try:
        times = [W.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[C.byref(t) for t in times]):
            raise C.WinError(C.get_last_error())
        buffer, size = C.create_unicode_buffer(32768), W.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(size)):
            raise C.WinError(C.get_last_error())

        def exited():
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in {0, 258}:
                raise C.WinError(C.get_last_error())
            return result == 0

        yield {"pid": pid, "created": str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime),
               "image": buffer.value, "exited": exited}
    finally:
        if not kernel.CloseHandle(handle):
            raise C.WinError(C.get_last_error())


def matches(record, observed):
    return bool(observed and record["pid"] == observed["pid"]
                and record["created"] == observed["created"]
                and PureWindowsPath(record["image"]) == PureWindowsPath(observed["image"]))


def parent_pid(pid):
    """Read native process ancestry; a venv redirector is not the app PID."""
    if os.name != "nt":
        raise RuntimeError("Process ancestry requires native Windows.")
    from ctypes import wintypes as W
    class Entry(C.Structure):
        _fields_ = [("size", W.DWORD), ("usage", W.DWORD), ("pid", W.DWORD),
                    ("heap", C.c_size_t), ("module", W.DWORD), ("threads", W.DWORD),
                    ("parent", W.DWORD), ("priority", C.c_long), ("flags", W.DWORD),
                    ("image", W.WCHAR * 260)]
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    kernel.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [W.HANDLE, C.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        entry = Entry()
        entry.size = C.sizeof(entry)
        more = kernel.Process32FirstW(handle, C.byref(entry))
        while more:
            if entry.pid == pid:
                return entry.parent
            more = kernel.Process32NextW(handle, C.byref(entry))
        if C.get_last_error() != 18:
            raise C.WinError(C.get_last_error())
        raise ProcessLookupError("The child process is no longer in the native snapshot.")
    finally:
        if not kernel.CloseHandle(handle):
            raise C.WinError(C.get_last_error())


def observe(spec):
    record = read(spec)
    if record is None:
        return None
    with process(record["pid"]) as actual:
        if not matches(record, actual) or actual["exited"]():
            return None
    return record


def listener_owned(record, host, port):
    """IPv4 loopback listener ownership, not a health/authentication claim."""
    if os.name != "nt" or host != "127.0.0.1" or type(port) is not int or not 0 < port < 65536:
        return False
    return listener_owners(host, port) == {record["pid"]}


def listener_owners(host, port):
    """Read the native IPv4 listener table; timeout/connect is not absence."""
    if os.name != "nt" or host != "127.0.0.1" or type(port) is not int or not 0 < port < 65536:
        raise ValueError("Native listener inspection requires an IPv4 loopback TCP port.")
    from ctypes import wintypes as W
    api = C.WinDLL("iphlpapi", use_last_error=True)
    api.GetExtendedTcpTable.argtypes = [C.c_void_p, C.POINTER(W.DWORD), W.BOOL,
                                       W.DWORD, W.DWORD, W.DWORD]
    size = W.DWORD()
    result = api.GetExtendedTcpTable(None, C.byref(size), False, 2, 3, 0)
    if result not in {0, 122} or not 4 <= size.value <= 16 * 1024 * 1024:
        raise OSError("Cannot size Windows listener table.")
    buffer = C.create_string_buffer(size.value)
    if api.GetExtendedTcpTable(buffer, C.byref(size), False, 2, 3, 0) != 0:
        raise OSError("Cannot read Windows listener table.")
    class Row(C.Structure):
        _fields_ = [(name, W.DWORD) for name in
                    ("state", "address", "port", "remote", "remotePort", "pid")]
    count = C.cast(buffer, C.POINTER(W.DWORD))[0]
    if 4 + count * C.sizeof(Row) > len(buffer):
        raise OSError("Invalid Windows listener table.")
    owners = set()
    for index in range(count):
        row = Row.from_buffer(buffer, 4 + index * C.sizeof(Row))
        address = socket.inet_ntoa(row.address.to_bytes(4, "little"))
        number = int.from_bytes(row.port.to_bytes(4, "little")[:2], "big")
        if address in {host, "0.0.0.0"} and number == port:
            owners.add(row.pid)
    return owners


def request_stop(spec, *, timeout=60, expected=None):
    """One request and bounded observation, never retry or force termination."""
    record = read(spec)
    if record is None:
        raise RuntimeError("No verified task runtime receipt; reconcile task status before stopping.")
    if expected is not None and any(record[key] != expected[key] for key in _FIELDS - {"phase"}):
        raise RuntimeError("The expected task run changed; no stop request was sent.")
    with process(record["pid"]) as actual:
        if actual is None or actual["exited"]():
            return
        if not matches(record, actual):
            raise RuntimeError("Task PID identity changed; no stop request was sent.")
        publish(spec, record, STOP)
        deadline = time.monotonic() + timeout
        while not actual["exited"]():
            current = read(spec)
            if current and current["run"] != record["run"]:
                raise RuntimeError("Task runtime changed while stopping; reconcile before retrying.")
            if time.monotonic() >= deadline:
                raise RuntimeError("Task did not finish normal shutdown; custody retained, no force-stop attempted.")
            time.sleep(0.1)


class Runner:
    def __init__(self, spec):
        self.spec = spec
        self.exit_code = 0
        with process(os.getpid()) as actual:
            if not actual or actual["exited"]():
                raise RuntimeError("Cannot establish task runner process identity.")
            self.record = {"schema": 1, "registration": spec.registration_id,
                           "run": uuid.uuid4().hex, "phase": "starting",
                           **{key: actual[key] for key in ("pid", "created", "image")}}
        publish(spec, self.record)

    def phase(self, phase):
        self.record = {**self.record, "phase": phase}
        publish(self.spec, self.record)

    def stop_requested(self):
        request = read(self.spec, STOP)
        # Phase changes do not change this run's identity.
        return bool(request and all(request[key] == self.record[key] for key in _FIELDS - {"phase"}))

    async def watch(self):
        from aiohttp.web_runner import GracefulExit
        while True:
            # Explicit normal stop wins if it races a restart for the same run.
            if await asyncio.to_thread(self.stop_requested):
                break
            if getattr(self.spec, "supervised", False):
                request = await asyncio.to_thread(read_restart, self.spec)
                if (request and all(request["run"][key] == self.record[key]
                                    for key in _FIELDS - {"phase"})):
                    self.exit_code = RESTART_EXIT
                    break
            await asyncio.sleep(0.25)
        self.phase("stopping")
        raise GracefulExit()

    def attach(self, app):
        """aiohttp owns watcher cancellation and completes normal app cleanup."""
        async def lifetime(_app):
            watcher = asyncio.create_task(self.watch())
            try:
                yield
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
        app.cleanup_ctx.append(lifetime)


class PrivateLog(io.TextIOBase):
    """A private 128 KiB UTF-8 tail with at most one snapshot per second."""
    def __init__(self, spec):
        self.spec = spec
        self._tail = bytearray()
        self._lock = threading.RLock()
        self._last = 0.0
        self._dirty = True
        self.flush(force=True)

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return True

    def write(self, text):
        if not isinstance(text, str):
            raise TypeError("Task logs require text.")
        encoded = text.encode("utf-8", errors="replace")
        with self._lock:
            self._tail.extend(encoded[-LOG_LIMIT:])
            if len(self._tail) > LOG_LIMIT:
                del self._tail[:-LOG_LIMIT]
            self._dirty = True
            self.flush()
        return len(text)

    def flush(self, *, force=False):
        if not hasattr(self, "_lock"):
            return
        with self._lock:
            now = time.monotonic()
            if not self._dirty or not force and now - self._last < 1:
                return
            from .windows_files import directory
            root = _root(self.spec)
            with directory(root / "config/services", owned_root=root, create=True) as pinned:
                pinned.publish_private(LOG, bytes(self._tail), replace=True)
            self._last, self._dirty = now, False

    def close(self):
        if not self.closed:
            self.flush(force=True)
        super().close()


def read_log(spec):
    from .windows_files import directory
    root = _root(spec)
    with directory(root / "config/services", owned_root=root, create=False) as pinned:
        return pinned.read_private(LOG, limit=LOG_LIMIT).decode("utf-8", errors="replace")