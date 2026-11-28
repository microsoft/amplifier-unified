"""Synthetic Scheduler payload for W8. No product imports or installation access."""

import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


class Luid(C.Structure):
    _fields_ = [("low", W.DWORD), ("high", W.LONG)]


class Statistics(C.Structure):
    _fields_ = [
        ("token", Luid), ("authentication", Luid), ("expiration", C.c_int64),
        ("type", C.c_int), ("impersonation", C.c_int), ("dynamic", W.DWORD),
        ("available", W.DWORD), ("groups", W.DWORD), ("privileges", W.DWORD),
        ("modified", Luid),
    ]


class LsaString(C.Structure):
    _fields_ = [("length", W.USHORT), ("maximum", W.USHORT), ("buffer", W.LPWSTR)]


class LogonSession(C.Structure):
    _fields_ = [
        ("size", W.ULONG), ("logon", Luid), ("user", LsaString),
        ("domain", LsaString), ("package", LsaString), ("type", W.ULONG),
        ("session", W.ULONG), ("sid", C.c_void_p), ("logon_time", C.c_int64),
    ]


def identity():
    kernel = C.WinDLL("kernel32", use_last_error=True)
    adv = C.WinDLL("advapi32", use_last_error=True)
    lsa = C.WinDLL("secur32", use_last_error=True)
    user = C.WinDLL("user32", use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    adv.GetTokenInformation.argtypes = [
        W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD),
    ]
    adv.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    kernel.LocalFree.argtypes = [C.c_void_p]
    token = W.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, C.byref(token)):
        raise C.WinError(C.get_last_error())

    def info(kind):
        size = W.DWORD()
        adv.GetTokenInformation(token, kind, None, 0, C.byref(size))
        buffer = C.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, kind, buffer, size, C.byref(size)):
            raise C.WinError(C.get_last_error())
        return buffer

    def sid_text(pointer):
        text = W.LPWSTR()
        if not adv.ConvertSidToStringSidW(pointer, C.byref(text)):
            raise C.WinError(C.get_last_error())
        try:
            return text.value
        finally:
            kernel.LocalFree(C.cast(text, C.c_void_p))

    try:
        numbers, owner = info(10), info(1)
        stats = Statistics.from_buffer(numbers)
        logon = Luid(stats.authentication.low, stats.authentication.high)
        facts = {
            "sid": sid_text(C.c_void_p.from_buffer(owner).value),
            "authenticationId": f"{logon.high & 0xffffffff:08x}:{logon.low:08x}",
            "elevated": bool(W.DWORD.from_buffer(info(20)).value),
            "elevationType": W.DWORD.from_buffer(info(18)).value,
        }
        data = C.POINTER(LogonSession)()
        lsa.LsaGetLogonSessionData.argtypes = [
            C.POINTER(Luid), C.POINTER(C.POINTER(LogonSession)),
        ]
        lsa.LsaFreeReturnBuffer.argtypes = [C.c_void_p]
        status = lsa.LsaGetLogonSessionData(C.byref(logon), C.byref(data))
        facts["lsaStatus"] = status
        if status == 0:
            try:
                facts.update(
                    logonTime=str(data.contents.logon_time),
                    logonType=data.contents.type,
                    lsaSid=sid_text(data.contents.sid),
                    lsaSession=data.contents.session,
                )
            finally:
                lsa.LsaFreeReturnBuffer(data)
    finally:
        kernel.CloseHandle(token)
    session = W.DWORD()
    kernel.ProcessIdToSessionId(os.getpid(), C.byref(session))
    facts["processSession"] = session.value
    kernel.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
    times = [W.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *map(C.byref, times)):
        raise C.WinError(C.get_last_error())
    facts["birth"] = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    user.GetProcessWindowStation.restype = W.HANDLE
    user.GetThreadDesktop.argtypes = [W.DWORD]
    user.GetThreadDesktop.restype = W.HANDLE
    user.GetUserObjectInformationW.argtypes = [
        W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD),
    ]
    for name, handle in (
        ("windowStation", user.GetProcessWindowStation()),
        ("desktop", user.GetThreadDesktop(kernel.GetCurrentThreadId())),
    ):
        buffer, size = C.create_unicode_buffer(256), W.DWORD()
        facts[name] = buffer.value if user.GetUserObjectInformationW(
            handle, 2, buffer, C.sizeof(buffer), C.byref(size)
        ) else f"unavailable:{C.get_last_error()}"
    # This is the payload's own desktop, not a visual check of the user's screen.
    facts["visibleOwnWindows"] = []
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    user.IsWindowVisible.argtypes = [W.HWND]

    @callback_type
    def window(hwnd, _):
        pid = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if pid.value == os.getpid() and user.IsWindowVisible(hwnd):
            facts["visibleOwnWindows"].append(int(hwnd))
        return True

    user.EnumWindows.argtypes = [callback_type, W.LPARAM]
    facts["enumWindowsSucceeded"] = bool(user.EnumWindows(window, 0))
    return facts


def publish(folder, label, role, **extra):
    value = {
        "label": label, "role": role, "pid": os.getpid(), "parent": os.getppid(),
        "at": time.time(), **identity(), **extra,
    }
    # New immutable file, one writer per filename. No shared append races.
    name = folder / f"{label}-{role}-{uuid.uuid4().hex}.json"
    with name.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
    return value


def containment(child):
    """A synthetic kill-on-close job models only the launcher's child lifetime."""
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [C.c_void_p, W.LPCWSTR]
    kernel.CreateJobObjectW.restype = W.HANDLE
    kernel.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]

    class Basic(C.Structure):
        _fields_ = [
            ("processTime", C.c_int64), ("jobTime", C.c_int64), ("flags", W.DWORD),
            ("minimum", C.c_size_t), ("maximum", C.c_size_t), ("active", W.DWORD),
            ("affinity", C.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD),
        ]

    class Extended(C.Structure):
        _fields_ = [
            ("basic", Basic), ("io", C.c_uint64 * 6), ("processMemory", C.c_size_t),
            ("jobMemory", C.c_size_t), ("peakProcess", C.c_size_t), ("peakJob", C.c_size_t),
        ]

    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x2000
    if not job or not kernel.SetInformationJobObject(
        job, 9, C.byref(limits), C.sizeof(limits)
    ) or not kernel.AssignProcessToJobObject(job, int(child._handle)):
        raise C.WinError(C.get_last_error())
    return job  # Retained until this synthetic action ends; not inherited.


def main():
    # Scheduler does not inherit the Actions step's environment.
    # The guarded controller makes this marker in its private random folder.
    if os.name != "nt":
        raise SystemExit("Native Windows probe only.")
    folder, label, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if (not folder.is_dir() or not folder.name.startswith("w8-recovery-")
            or not (folder / "synthetic-hosted-probe").is_file()
            or label not in {"runner", "ignore", "actionzero", "childzero",
                             "actionforce", "disabled", "retryonly", "retryrepeat",
                             "retryhighonly", "retryhighrepeat", "retrytimeonly"}
            or mode not in {"direct", "child", "fail", "failhigh", "long"}):
        raise SystemExit("Expected the probe's existing temporary folder.")
    if mode == "direct":
        publish(folder, label, "direct")
        return 0
    if mode == "child":
        publish(folder, label, "child")
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline and not (folder / f"{label}.stop").exists():
            time.sleep(0.2)
        return 0
    if mode in {"fail", "failhigh"}:
        publish(folder, label, "action")
        time.sleep(2)
        return 7 if mode == "fail" else -2147467259  # E_FAIL, 0x80004005.
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", __file__, str(folder), label, "child"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    job = containment(child)
    publish(folder, label, "action", childPid=child.pid, jobAssigned=bool(job))
    result = child.wait()
    publish(folder, label, "child-exit", childPid=child.pid, childExit=result)
    # A child ended with code 0 yields a code-0 action, as in the incident.
    return result


if __name__ == "__main__":
    raise SystemExit(main())