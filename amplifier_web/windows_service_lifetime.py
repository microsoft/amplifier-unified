"""Bounded owner intent for the Windows launcher, not another service.

Legacy layouts keep their old start path. The future repeating layout requires
a record. Only an explicit owner Start clears Stop. Reservation
times survive a new sign-in and a new Start; neither resets the rolling limit.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
import json
from pathlib import Path
import re
import time
import uuid

RECORD = "task-lifetime.json"
LOCK = "task-lifetime.lock"
LIMIT = 4096
WINDOW = 6 * 60 * 60
STARTS = 3


class Paused(RuntimeError):
    """An automatic entry cannot safely start an app."""


def logon_key(value):
    if (not isinstance(value, dict) or set(value) != {"authenticationId", "logonTime"}
            or not isinstance(value["authenticationId"], str)
            or not re.fullmatch(r"[0-9a-f]{8}:[0-9a-f]{8}", value["authenticationId"])
            or not isinstance(value["logonTime"], str)
            or not re.fullmatch(r"[1-9][0-9]{0,18}", value["logonTime"])
            or int(value["logonTime"]) >= 2**63):
        raise ValueError("Windows could not check this sign-in.")
    return dict(value)


def native_sign_in(sid):
    """Read this process token and require one interactive session of this SID."""
    from ctypes import wintypes as W
    from .windows_files import current_process_sid, _API
    from .windows_service import _lookup
    if current_process_sid() != sid:
        raise ValueError("The Windows account changed.")
    kernel, adv, lsa = (C.WinDLL(name, use_last_error=True)
                        for name in ("kernel32", "advapi32", "secur32"))

    class Luid(C.Structure):
        _fields_ = [("low", W.DWORD), ("high", W.LONG)]

    class Statistics(C.Structure):
        _fields_ = [("token", Luid), ("authentication", Luid), ("expiration", C.c_int64),
                    ("type", C.c_int), ("impersonation", C.c_int), ("dynamic", W.DWORD),
                    ("available", W.DWORD), ("groups", W.DWORD), ("privileges", W.DWORD),
                    ("modified", Luid)]

    class Text(C.Structure):
        _fields_ = [("length", W.USHORT), ("maximum", W.USHORT), ("buffer", W.LPWSTR)]

    class Session(C.Structure):
        _fields_ = [("size", W.ULONG), ("logon", Luid), ("user", Text), ("domain", Text),
                    ("package", Text), ("type", W.ULONG), ("session", W.ULONG),
                    ("sid", C.c_void_p), ("at", C.c_int64)]

    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    adv.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    lsa.LsaGetLogonSessionData.argtypes = [C.POINTER(Luid), C.POINTER(C.POINTER(Session))]
    lsa.LsaFreeReturnBuffer.argtypes = [C.c_void_p]
    token, size = W.HANDLE(), W.DWORD()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, C.byref(token)):
        raise C.WinError(C.get_last_error())
    try:
        stats = Statistics()
        if not adv.GetTokenInformation(token, 10, C.byref(stats), C.sizeof(stats), C.byref(size)):
            raise C.WinError(C.get_last_error())
        data = C.POINTER(Session)()
        if lsa.LsaGetLogonSessionData(C.byref(stats.authentication), C.byref(data)) != 0 or not data:
            raise ValueError("Windows could not read this sign-in.")
        try:
            session = W.DWORD()
            kernel.ProcessIdToSessionId.argtypes = [W.DWORD, C.POINTER(W.DWORD)]
            if not kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(), C.byref(session)):
                raise C.WinError(C.get_last_error())
            if (data.contents.type not in {2, 10} or not session.value
                    or data.contents.session != session.value
                    or _API().sid_text(data.contents.sid) != sid):
                raise ValueError("Windows could not confirm an interactive sign-in.")
            key = logon_key({
                "authenticationId": f"{stats.authentication.high & 0xffffffff:08x}:{stats.authentication.low:08x}",
                "logonTime": str(data.contents.at),
            })
        finally:
            lsa.LsaFreeReturnBuffer(data)
    finally:
        kernel.CloseHandle(token)

    # Include disconnected sessions: a second sign-in of the same owner is
    # ambiguous, not permission to clear the other session's Stop.
    wts = C.WinDLL("wtsapi32", use_last_error=True)

    class Desktop(C.Structure):
        _fields_ = [("id", W.DWORD), ("name", W.LPWSTR), ("state", C.c_int)]

    wts.WTSEnumerateSessionsW.argtypes = [
        W.HANDLE, W.DWORD, W.DWORD, C.POINTER(C.POINTER(Desktop)), C.POINTER(W.DWORD)]
    wts.WTSQuerySessionInformationW.argtypes = [
        W.HANDLE, W.DWORD, C.c_int, C.POINTER(W.LPWSTR), C.POINTER(W.DWORD)]
    wts.WTSFreeMemory.argtypes = [C.c_void_p]
    desktops, count = C.POINTER(Desktop)(), W.DWORD()
    if not wts.WTSEnumerateSessionsW(None, 0, 1, C.byref(desktops), C.byref(count)):
        raise C.WinError(C.get_last_error())

    def text(number, kind):
        pointer, length = W.LPWSTR(), W.DWORD()
        if not wts.WTSQuerySessionInformationW(None, number, kind, C.byref(pointer), C.byref(length)):
            raise C.WinError(C.get_last_error())
        try:
            return pointer.value or ""
        finally:
            wts.WTSFreeMemory(C.cast(pointer, C.c_void_p))

    try:
        if count.value > 1024:
            raise ValueError("Windows listed too many sign-ins.")
        own = []
        for index in range(count.value):
            number = desktops[index].id
            # Session 0 and listener pseudo-sessions have no interactive user.
            if not number or desktops[index].state in {6, 8, 9}:
                continue
            user = text(number, 5)
            if user:
                domain = text(number, 7)
                if _lookup(domain + "\\" + user if domain else user) == (sid, 1):
                    own.append(number)
        if own != [session.value]:
            raise ValueError("Windows has more than one sign-in for this account.")
    finally:
        wts.WTSFreeMemory(desktops)
    return key


def supported(spec):
    """Do not add a new state requirement to an unrecognised hand-built launcher."""
    if not spec.supervised:
        return False
    if spec.repeat_start is not None:
        return True
    from .application_generations import supports
    return supports(Path(spec.python).parent.parent)


def validate(value, spec):
    fields = {"schema", "registration", "ownerSid", "logon", "intent", "stopped", "manual", "starts", "lastTime"}
    if (not isinstance(value, dict) or set(value) != fields or type(value["schema"]) is not int
            or value["schema"] != 1 or value["registration"] != spec.registration_id
            or value["ownerSid"] != spec.sid or not isinstance(value["intent"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", value["intent"])
            or type(value["stopped"]) is not bool or type(value["manual"]) is not bool
            or value["stopped"] and value["manual"]
            or type(value["lastTime"]) is not int or not 0 <= value["lastTime"] < 2**53
            or not isinstance(value["starts"], list) or len(value["starts"]) > STARTS
            or any(type(n) is not int or not 0 <= n <= value["lastTime"] for n in value["starts"])
            or value["starts"] != sorted(value["starts"])):
        raise ValueError("Amplifier could not read its saved start choice.")
    if value["logon"] is not None:
        logon_key(value["logon"])
    return value


@contextmanager
def store(spec):
    from filelock import FileLock
    from .windows_files import address, directory
    root = address(spec.data_dir)
    with directory(root / "config/services", owned_root=root, create=True) as pinned:
        with FileLock(pinned.path / LOCK, timeout=5, mode=0o600):
            yield pinned


def _closed(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("The saved start choice has a repeated field.")
        value[key] = item
    return value


def read(pinned, spec):
    return validate(json.loads(pinned.read_private(RECORD, limit=LIMIT), object_pairs_hook=_closed), spec)


def write(pinned, spec, value):
    raw = json.dumps(validate(value, spec), sort_keys=True).encode("utf-8")
    if len(raw) > LIMIT:
        raise ValueError("The saved start choice is too large.")
    pinned.publish_private(RECORD, raw, replace=True)


def owner_choice(spec, *, stop, manual=True):
    """An explicit Start does not depend on reading a valid old record."""
    if not supported(spec):
        return None
    with store(spec) as pinned:
        try:
            previous = read(pinned, spec)
        except (OSError, ValueError):
            previous = None
        try:
            key = native_sign_in(spec.sid)
        except (OSError, ValueError, RuntimeError):
            key = None  # Only automatic starts require this new read to pass.
        now = int(time.time())
        starts = previous["starts"] if previous else []
        value = {
            "schema": 1, "registration": spec.registration_id, "ownerSid": spec.sid,
            "logon": key, "intent": uuid.uuid4().hex, "stopped": stop,
            "manual": bool(manual and not stop), "starts": starts,
            # A clock that went back must not create another automatic allowance.
            "lastTime": max(now, previous["lastTime"] if previous else 0),
        }
        write(pinned, spec, value)
        return value["intent"]


def admit(spec):
    """Reserve before any child starts. Each outer automatic entry shares this file."""
    if spec.repeat_start is None or not supported(spec):
        # Slice 1 never opts an old installation into new automatic behavior.
        # Its saved Stop is carried into the later, transactional migration.
        return None
    try:
        with store(spec) as pinned:
            value = read(pinned, spec)
            if value["stopped"]:
                raise Paused("Amplifier stays stopped until you start it.")
            now = int(time.time())
            if value["manual"]:
                value = {**value, "manual": False}
            else:
                key = native_sign_in(spec.sid)
                if value["logon"] is None or now < value["lastTime"]:
                    raise Paused("Amplifier could not check its saved start choice.")
                starts = [at for at in value["starts"] if at > now - WINDOW]
                if len(starts) >= STARTS:
                    raise Paused("Amplifier has tried to start three times in six hours.")
                value = {**value, "logon": key, "starts": [*starts, now], "lastTime": now}
            write(pinned, spec, value)
            return value["intent"]
    except Paused:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise Paused("Amplifier could not check its saved start choice.") from error


def allowed(spec, intent, *, exact=False):
    if intent is None:
        return True  # Legacy entry with no lifetime record; keep its old behavior.
    try:
        with store(spec) as pinned:
            value = read(pinned, spec)
            return not value["stopped"] and (not exact or value["intent"] == intent)
    except (OSError, ValueError, RuntimeError):
        return False