"""Select and inspect the Windows consumer installation's saved loopback port.

Selection reads this computer's dynamic and reserved TCP ranges and stays out
of both: Hyper-V and WSL reserve blocks inside the dynamic range, and those
blocks move at each restart. The test bind is only a filter. The host's own
listener claims the port; nothing here adopts or stops another program.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess

PREFERRED = range(20000, 40000)
_FALLBACK = range(10000, 65536)
# The POSIX default and the earlier fixed Windows port stay free for those installs.
_AVOID = frozenset({8941, 8942})
_MINIMUM_POOL = 1000
_ACCESS_DENIED = 10013


def parse_dynamic(text):
    """Return the inclusive dynamic range from `netsh ... show dynamicport tcp`.

    Labels are localized; the two values are always `label : number`, start first.
    """
    values = [int(match[1]) for line in text.splitlines()
              if (match := re.search(r":\s*(\d{1,5})\s*$", line))]
    if len(values) != 2:
        return None
    start, count = values
    if not 1 <= start <= 65535 or count < 1 or start + count - 1 > 65535:
        return None
    return start, start + count - 1


def parse_excluded(text):
    """Return inclusive reserved ranges from `netsh ... show excludedportrange`."""
    found = []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(\d{1,5})\s+(\d{1,5})\s*\*?\s*", line)
        if match and 1 <= int(match[1]) <= int(match[2]) <= 65535:
            found.append((int(match[1]), int(match[2])))
    return found


def _netsh(*arguments):
    """Read-only query through the system's own netsh, never a PATH lookup."""
    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(260)
    length = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        return None
    try:
        result = subprocess.run(
            [str(Path(buffer.value) / "netsh.exe"), *arguments], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=15, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.decode("ascii", "replace") if result.returncode == 0 else None


def ranges():
    """Read `(dynamic, reserved)`; either is None when Windows did not answer."""
    dynamic = _netsh("int", "ipv4", "show", "dynamicport", "tcp")
    reserved = _netsh("int", "ipv4", "show", "excludedportrange", "protocol=tcp")
    return (parse_dynamic(dynamic) if dynamic is not None else None,
            parse_excluded(reserved) if reserved is not None else None)


def pool(dynamic, reserved):
    """Candidate ports outside the reserved and dynamic ranges, 20000-39999 first."""
    reserved = list(reserved or ())
    both = [*reserved, *([dynamic] if dynamic else [])]

    def outside(ports, blocked):
        return [port for port in ports if port not in _AVOID
                and not any(start <= port <= end for start, end in blocked)]
    # Some computers set the dynamic range to start at 1024. Then no port is
    # outside it; stay out of the reserved blocks and rely on the saved port's
    # later check and repair.
    for ports, blocked in ((PREFERRED, both), (_FALLBACK, both), (PREFERRED, reserved)):
        found = outside(ports, blocked)
        if len(found) >= _MINIMUM_POOL:
            return found
    return outside(PREFERRED, reserved) or list(PREFERRED)


def _bind(port):
    """Briefly bind loopback; return None, or the refusal's error code."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            return getattr(error, "winerror", None) or error.errno or -1
    return None


def _listening(port):
    if os.name != "nt":
        return False
    from .windows_task_runtime import listener_owners
    return bool(listener_owners("127.0.0.1", port))


def state(port):
    """Classify one loopback port as `free`, `in-use` or `reserved`.

    `reserved`: no program listens there, yet Windows refuses the bind with
    access denied. That is a port reservation held by a Windows feature, so
    "close the other program" would be wrong advice. Reads no ranges and
    starts no process: every shortcut click may call this.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("A TCP port number is required.")
    if _listening(port):
        return "in-use"
    refusal = _bind(port)
    if refusal is None:
        return "free"
    return "reserved" if refusal == _ACCESS_DENIED else "in-use"


def select(*, attempts=64, choose=None):
    """Return a currently free loopback port outside this computer's ranges."""
    candidates = pool(*ranges())
    choose = choose or secrets.SystemRandom().sample
    for port in choose(candidates, min(attempts, len(candidates))):
        if state(port) == "free":
            return port
    raise RuntimeError("No free local address could be selected for the installation.")
