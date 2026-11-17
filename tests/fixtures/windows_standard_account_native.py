"""What the consumer installation does for a standard Windows account.

A standard account is not a member of Administrators. The account of a
GitHub-hosted runner is the built-in Administrator, so this case makes its own
person: one new local user, member of Users (and of Remote Desktop Users, only
for the sign-in below), with a random password.

The user signs in with its name and password through Remote Desktop to this
same computer. Windows then gives the user a desktop session of its own, as at
a PC's screen. A helper task starts this file as that user in that session.
From there everything runs as the standard user, in the user's own profile:

- setup's own code (the compiled setup sources): its checks before any
  question, its folder checks and its read of the sign-in task;
- the installation in %LOCALAPPDATA%\\Amplifier in setup's layout, with the
  candidate; setup's check of it; the Start-menu and Apps-list entries;
- the first start: the task registration, the launcher, the host that Task
  Scheduler starts; status; "Stop Amplifier"; a second start;
- the launcher's certificate step: Amplifier's message, the Windows question
  about the certificate (the test records its text and selects Yes), then
  Windows trusts the connection;
- a sign-out and a new sign-in: the task's own trigger starts the host;
- uninstall with the choice that keeps the folder; the entries and a start
  again; uninstall with the choice that deletes the folder; then nothing of
  the installation is left.

Not shown: see NOT_SHOWN below. In short: the setup file itself (setup refuses
Windows Server, and every hosted x64 runner is Windows Server), a person's own
answer to the Windows certificate question, the browser, a click on a
Start-menu entry and the messages of the windowless launcher, a chat,
Windows 11, and a sign-in at the PC's own screen.

The delete choice removes the folder %LOCALAPPDATA%\\Amplifier of the account
that runs it. Here that is only the user that this run created: `standard`
refuses every other account. It reads that from the run's account record, a
file of the administrators group that the user cannot change.

The password is made in the `run` process. It goes to Windows for the new
user and, for each sign-in, to the credential store of the runner's own logon,
in memory only. It is never written to a file, an environment variable, a
command line or the log.

The fixture creates a Windows user and changes a Remote Desktop setting of the
runner's account. It runs only with the workflow's opt-in on a hosted runner.
That guard stops accidental use and does not prove the machine. Never run it
on a machine that is kept.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://github.com/microsoft/amplifier-unified"
OPT_IN = "AMPLIFIER_CREATE_A_STANDARD_USER_ON_THIS_RUNNER"
USERS, ADMINISTRATORS, REMOTE_DESKTOP_USERS = "S-1-5-32-545", "S-1-5-32-544", "S-1-5-32-555"
# Remote Desktop refuses a connection to the address of this computer's own
# screen session. Another loopback address reaches the same computer.
DESKTOP, DESKTOP_TARGET = "127.0.0.2", "TERMSRV/127.0.0.2"
HELPER = "AmplifierStandardAccountCheck"
# The comment of the account that a run creates, with that run's own number.
MARK = "Amplifier standard-account check, run "
FILES = ("tests/fixtures/windows_standard_account_native.py", "tests/fixtures/windows_start_again_native.py",
         "amplifier_web/windows_files.py")
# One line for each step. FIRST runs after the first sign-in, SECOND after the
# sign-out and the new sign-in.
FIRST = (
    "The account: a standard user, signed in, in a desktop session of its own",
    "Setup's own code: the checks before any question, the folder checks, the read of the sign-in task",
    "The installation folder and the app in setup's layout (the fixture's commands, not the setup file)",
    "Setup's check of the installation (setup's two commands)",
    "Start-menu and Apps-list entries",
    "First start: the task registration",
    "First start: the launcher",
    "First start: the host that Task Scheduler starts",
    "Status",
    "Stop Amplifier",
    "Second start",
    "The certificate: Amplifier's message, the Windows question (the test selects Yes), then Windows trusts "
    "the connection",
    "Setup's own code reads this installation as installed, with its sign-in task",
)
SECOND = (
    "Sign-out and sign-in: the task's own trigger starts the host, and Windows still trusts the connection",
    "Uninstall with the choice that keeps the folder (the test selects No at the Windows question; the "
    "certificate stays)",
    "Entries again and a start after that uninstall",
    "Uninstall with the choice that deletes the folder (the test selects Yes at the Windows question; the "
    "certificate is removed)",
)
THIRD = (
    "After the delete choice: no folder, no sign-in task, no Start-menu entry, no Apps-list entry, no certificate",
)
JOURNEY = FIRST[3:]
CERTIFICATE = JOURNEY[8]
NOT_SHOWN = (
    ("The setup file: its windows and its question, its Windows 11 check with the answer yes, and its "
     "downloads of uv, Python and Git",
     "the setup file refuses Windows Server, and every GitHub-hosted x64 runner is Windows Server. The "
     "fixture makes setup's layout itself: setup's two uv commands for a private Python, `uv tool "
     "install` of the candidate wheel (without the component list of a release), and a copy of the "
     "runner's Git."),
    ("A person's own answer to the Windows certificate question, and that the question is in front of "
     "other windows",
     "no person is present. The case calls the launcher's own certificate step, records the text of the "
     "Windows question, and gives the answer with a window message from the same program."),
    ("The browser",
     "the launcher runs with --no-open, and the certificate step ends before the launcher's `open` "
     "command. Nothing in the case reads a browser window."),
    ("A click on a Start-menu or Apps-list entry, and the messages of the windowless launcher",
     "a message waits for a person. The case runs the same launcher commands with the console Python. For "
     "Stop and Uninstall it calls the product's function and answers the product's question in the test."),
    ("A chat and a model request", "no AI account is connected."),
    ("Windows 11, and a sign-in at the PC's own screen",
     "the runner is Windows Server, and the user signs in through Remote Desktop to the same computer."),
)


def require_disposable_runner(environ=None, platform=None, interpreter=None):
    """Refuse before any effect unless this is the hosted workflow, with its opt-in.

    A guard against accidental use. It reads the environment that GitHub sets
    on a hosted runner; environment variables do not prove the machine. Never
    set them by hand on a machine that is kept.
    """
    environ = os.environ if environ is None else environ
    if not ((platform or sys.platform) == "win32" and environ.get("GITHUB_ACTIONS") == "true"
            and environ.get("RUNNER_ENVIRONMENT") == "github-hosted" and environ.get(OPT_IN) == "1"):
        raise SystemExit("Refused: this check creates a Windows user and changes a Remote Desktop setting. "
                         f"It runs only on a GitHub-hosted runner, with {OPT_IN}=1.")
    if (interpreter or (sys.version_info[:2], sys.maxsize > 2 ** 32)) != ((3, 13), True):
        raise SystemExit("Refused: this check needs 64-bit CPython 3.13.")


HOSTED_FACTS = ("GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", OPT_IN)


def admit_standard(account, token, owner, changeable, platform=None, interpreter=None):
    """Refuse unless this program is the standard user of one run of the hosted workflow.

    `standard` runs as the new user, which has no workflow environment. The
    account record of the run takes its place. The record counts only when an
    administrator wrote it: its file belongs to the administrators group, and
    this user can change neither the file nor its folder. A person without
    administrator rights cannot make such a file. The record names the user
    that the run created. It also carries the workflow's facts, for the same
    check as on the runner's account.
    """
    if not isinstance(account, dict) or token["sid"] != account.get("sid") or token["memberOfAdministrators"]:
        raise SystemExit("Refused: this program runs only as the standard user that this run created.")
    if changeable or owner != ADMINISTRATORS:
        raise SystemExit("Refused: the account record of this run is not a file of the administrators group "
                         "that this user cannot change.")
    require_disposable_runner(account.get("hosted") or {}, platform, interpreter)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def evidence():
    return Path(os.environ["RUNNER_TEMP"]).resolve() / "standard-account-evidence"


def save(name, value):
    evidence().mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2) + "\n"
    assert len(raw.encode()) <= 256 * 1024, name
    (evidence() / name).write_text(raw, encoding="utf-8")


def saved(name):
    try:
        return json.loads((evidence() / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def execute(argv, *, env=None, cwd=None, timeout=120, stdin=None):
    started = time.monotonic()
    result = subprocess.run(list(map(str, argv)), env=env, cwd=cwd, timeout=timeout, input=stdin,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        value = None
    return {"exitCode": result.returncode, "json": value, "stdout": result.stdout[-6000:],
            "stderr": result.stderr[-3000:], "seconds": round(time.monotonic() - started, 3)}


def need(condition, text, detail=None):
    """A failed step keeps its exact text: what was required, and what the program said."""
    if not condition:
        raise AssertionError(text if detail is None else text + ": " + json.dumps(detail, default=str)[-2500:])


def system(name):
    return Path(os.environ["SystemRoot"]) / "System32" / name


def execution_level(program):
    """What a program file asks Windows for at its start: (a 64-bit x64 program, the levels in its manifest).

    Windows asks for an administrator when a program's manifest says so. For a
    program without a level in a manifest, Windows guesses from a file name
    like "setup", but only for a 32-bit program. So a 64-bit program whose one
    level is `asInvoker` starts with the rights of the person who starts it,
    and a standard user gets no administrator question. This reads the file's
    bytes. The program does not run.
    """
    data = Path(program).read_bytes()
    header = int.from_bytes(data[0x3C:0x40], "little")
    x64 = data[:2] == b"MZ" and data[header:header + 6] == b"PE\0\0\x64\x86"
    levels = re.findall(rb"<requestedExecutionLevel\s+level=\"([A-Za-z]+)\"", data)
    return x64, [level.decode("ascii") for level in levels]


def powershell(script, value, timeout=120):
    """Static source only. JSON is data on stdin, never text in the command."""
    return execute([system("WindowsPowerShell/v1.0/powershell.exe"), "-NoLogo", "-NoProfile", "-NonInteractive",
                    "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                   stdin=json.dumps(value), timeout=timeout)


# ---- Windows accounts and tokens.
_adv = C.WinDLL("advapi32", use_last_error=True) if sys.platform == "win32" else None
_kernel = C.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None


def _failed():
    return C.WinError(C.get_last_error())


def sid_text(sid):
    text = W.LPWSTR()
    _adv.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    if not _adv.ConvertSidToStringSidW(sid, C.byref(text)):
        raise _failed()
    try:
        return text.value
    finally:
        _kernel.LocalFree.argtypes = [C.c_void_p]
        _kernel.LocalFree(text)


def account_sid(name):
    """The SID bytes of a local account name; None when Windows knows no such name."""
    size, domain_size, use = W.DWORD(0), W.DWORD(0), W.DWORD()
    _adv.LookupAccountNameW.argtypes = [W.LPCWSTR, W.LPCWSTR, C.c_void_p, C.POINTER(W.DWORD),
                                        W.LPWSTR, C.POINTER(W.DWORD), C.POINTER(W.DWORD)]
    if not _adv.LookupAccountNameW(None, name, None, C.byref(size), None, C.byref(domain_size), C.byref(use)):
        if C.get_last_error() == 1332:  # No mapping between account names and security IDs.
            return None
        if C.get_last_error() != 122:  # The first call only asks for the sizes.
            raise _failed()
    sid, domain = C.create_string_buffer(size.value), C.create_unicode_buffer(domain_size.value)
    if not _adv.LookupAccountNameW(None, name, sid, C.byref(size), domain, C.byref(domain_size), C.byref(use)):
        raise _failed()
    return sid


def sid_name(text):
    """The name of a built-in group on this computer. It differs by language."""
    sid = C.c_void_p()
    _adv.ConvertStringSidToSidW.argtypes = [W.LPCWSTR, C.POINTER(C.c_void_p)]
    if not _adv.ConvertStringSidToSidW(text, C.byref(sid)):
        raise _failed()
    name, domain = C.create_unicode_buffer(256), C.create_unicode_buffer(256)
    name_size, domain_size, use = W.DWORD(256), W.DWORD(256), W.DWORD()
    _adv.LookupAccountSidW.argtypes = [W.LPCWSTR, C.c_void_p, W.LPWSTR, C.POINTER(W.DWORD),
                                       W.LPWSTR, C.POINTER(W.DWORD), C.POINTER(W.DWORD)]
    if not _adv.LookupAccountSidW(None, sid, name, C.byref(name_size), domain, C.byref(domain_size), C.byref(use)):
        raise _failed()
    return name.value


class _Mapping(C.Structure):
    _fields_ = [("read", W.DWORD), ("write", W.DWORD), ("execute", W.DWORD), ("all", W.DWORD)]


# Every right that changes a file or a folder: write or add data, write its attributes, delete an
# entry of a folder, delete it, change its permissions, take it over.
CHANGE = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000


def _granted(descriptor):
    """Every right that Windows gives this program's own token on an object with these permissions."""
    token, copy = W.HANDLE(), W.HANDLE()
    _kernel.GetCurrentProcess.restype = W.HANDLE
    _kernel.CloseHandle.argtypes = [W.HANDLE]
    _adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    if not _adv.OpenProcessToken(_kernel.GetCurrentProcess(), 0x2 | 0x8, C.byref(token)):  # TOKEN_DUPLICATE | TOKEN_QUERY
        raise _failed()
    try:
        # Windows computes access for a copy of the token that stands for this program (SecurityImpersonation).
        _adv.DuplicateToken.argtypes = [W.HANDLE, C.c_int, C.POINTER(W.HANDLE)]
        if not _adv.DuplicateToken(token, 2, C.byref(copy)):
            raise _failed()
        try:
            mapping = _Mapping(0x120089, 0x120116, 0x1200A0, 0x1F01FF)  # A file's generic rights.
            privileges, size, granted, allowed = C.create_string_buffer(1024), W.DWORD(1024), W.DWORD(), W.BOOL()
            _adv.AccessCheck.argtypes = [C.c_void_p, W.HANDLE, W.DWORD, C.POINTER(_Mapping), C.c_void_p,
                                         C.POINTER(W.DWORD), C.POINTER(W.DWORD), C.POINTER(W.BOOL)]
            # MAXIMUM_ALLOWED: Windows reports every right that the permissions give this token.
            if not _adv.AccessCheck(descriptor, copy, 0x02000000, C.byref(mapping), privileges, C.byref(size),
                                    C.byref(granted), C.byref(allowed)):
                raise _failed()
            return granted.value if allowed.value else 0
        finally:
            _kernel.CloseHandle(copy)
    finally:
        _kernel.CloseHandle(token)


def read_protected(path, folder=False):
    """One open of a file or a folder: (its bytes, the SID of its owner, whether this program could change it).

    All three come from the same open object. So a path that is replaced
    between two reads cannot give the bytes of one file and the owner of
    another. While a file is open here, no program can open it for writing or
    for deleting. "Change" is one of the rights of `CHANGE`, as Windows itself
    computes them for this program's token from the object's own permissions.
    A call that fails is an error here, never an answer.
    """
    _kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    _kernel.CreateFileW.restype = W.HANDLE
    _kernel.CloseHandle.argtypes = [W.HANDLE]
    # A file: read access (GENERIC_READ), and other programs may only read it meanwhile (share: 1).
    # A folder: its attributes and its permissions only; FILE_FLAG_BACKUP_SEMANTICS opens a folder.
    handle = _kernel.CreateFileW(str(path), 0x20080 if folder else 0x80000000, 7 if folder else 1, None, 3,
                                 0x02000000 if folder else 0, None)
    if handle in (None, W.HANDLE(-1).value):
        raise _failed()
    try:
        data = b""
        if not folder:
            buffer, count = C.create_string_buffer(65536), W.DWORD()
            _kernel.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
            if not _kernel.ReadFile(handle, buffer, 65536, C.byref(count), None):
                raise _failed()
            data = buffer.raw[:count.value]
        sid, descriptor = C.c_void_p(), C.c_void_p()
        _adv.GetSecurityInfo.argtypes = [W.HANDLE, C.c_int, W.DWORD, C.POINTER(C.c_void_p), C.POINTER(C.c_void_p),
                                         C.POINTER(C.c_void_p), C.POINTER(C.c_void_p), C.POINTER(C.c_void_p)]
        _adv.GetSecurityInfo.restype = W.DWORD
        # SE_FILE_OBJECT; the owner, the group and the permissions.
        status = _adv.GetSecurityInfo(handle, 1, 7, C.byref(sid), None, None, None, C.byref(descriptor))
        if status:
            raise C.WinError(status)
        try:
            owner, granted = sid_text(sid), _granted(descriptor)
        finally:
            _kernel.LocalFree.argtypes = [C.c_void_p]
            _kernel.LocalFree(descriptor)
    finally:
        _kernel.CloseHandle(handle)
    return data, owner, bool(granted & CHANGE)


class _UserInfo1(C.Structure):
    _fields_ = [("name", W.LPWSTR), ("password", W.LPWSTR), ("password_age", W.DWORD),
                ("privilege", W.DWORD), ("home", W.LPWSTR), ("comment", W.LPWSTR),
                ("flags", W.DWORD), ("script", W.LPWSTR)]


class _Member(C.Structure):
    _fields_ = [("sid", C.c_void_p)]


def new_password():
    """Random, with every character class that a password policy can require. Never shown."""
    alphabet = string.ascii_letters + string.digits + "!#%+-_="
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(30))
        if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value) and any(not c.isalnum() for c in value)):
            return value


def join(name, group):
    net = C.WinDLL("netapi32")
    net.NetLocalGroupAddMembers.argtypes = [W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_void_p, W.DWORD]
    net.NetLocalGroupAddMembers.restype = W.DWORD
    sid = account_sid(name)
    status = net.NetLocalGroupAddMembers(None, sid_name(group), 0, C.byref(_Member(C.cast(sid, C.c_void_p))), 1)
    if status:
        raise OSError(f"NetLocalGroupAddMembers failed with status {status}")


def create_user(name, password, mark):
    """One new local user, with this run's mark as its comment. An existing name is refused by Windows, never adopted."""
    net = C.WinDLL("netapi32")
    net.NetUserAdd.argtypes = [W.LPCWSTR, W.DWORD, C.c_void_p, C.POINTER(W.DWORD)]
    net.NetUserAdd.restype = W.DWORD
    # USER_PRIV_USER; UF_SCRIPT | UF_DONT_EXPIRE_PASSWD.
    info = _UserInfo1(name, password, 0, 1, None, mark, 0x1 | 0x10000, None)
    status = net.NetUserAdd(None, 1, C.byref(info), C.byref(W.DWORD()))
    if status:
        raise OSError(f"NetUserAdd failed with status {status}")
    return sid_text(account_sid(name))


def account_comment(name):
    """The comment of a local account; None when Windows knows no such account."""
    net = C.WinDLL("netapi32")
    buffer = C.c_void_p()
    net.NetUserGetInfo.argtypes = [W.LPCWSTR, W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p)]
    net.NetUserGetInfo.restype = W.DWORD
    status = net.NetUserGetInfo(None, name, 1, C.byref(buffer))
    if status == 2221:  # No such user.
        return None
    if status:
        raise OSError(f"NetUserGetInfo failed with status {status}")
    try:
        # An account without a comment exists: it is never read as absent.
        return C.cast(buffer, C.POINTER(_UserInfo1)).contents.comment or ""
    finally:
        net.NetApiBufferFree.argtypes = [C.c_void_p]
        net.NetApiBufferFree(buffer)


def local_groups(name):
    """Every local group of the user, also through another group."""
    net = C.WinDLL("netapi32")
    buffer, read, total = C.c_void_p(), W.DWORD(), W.DWORD()
    net.NetUserGetLocalGroups.argtypes = [W.LPCWSTR, W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(C.c_void_p),
                                          W.DWORD, C.POINTER(W.DWORD), C.POINTER(W.DWORD)]
    net.NetUserGetLocalGroups.restype = W.DWORD
    status = net.NetUserGetLocalGroups(None, name, 0, 1, C.byref(buffer), 0xFFFFFFFF, C.byref(read), C.byref(total))
    if status:
        raise OSError(f"NetUserGetLocalGroups failed with status {status}")
    try:
        return sorted(C.cast(buffer, C.POINTER(W.LPWSTR * read.value)).contents) if read.value else []
    finally:
        net.NetApiBufferFree.argtypes = [C.c_void_p]
        net.NetApiBufferFree(buffer)


def delete_user(name):
    net = C.WinDLL("netapi32")
    net.NetUserDel.argtypes = [W.LPCWSTR, W.LPCWSTR]
    net.NetUserDel.restype = W.DWORD
    return net.NetUserDel(None, name)  # 0 removed; 2221 no such user.


class _Luid(C.Structure):
    _fields_ = [("low", W.DWORD), ("high", W.LONG)]


class _Statistics(C.Structure):
    _fields_ = [("token", _Luid), ("authentication", _Luid), ("expiration", C.c_int64),
                ("type", C.c_int), ("impersonation", C.c_int), ("dynamic", W.DWORD),
                ("available", W.DWORD), ("groups", W.DWORD), ("privileges", W.DWORD), ("modified", _Luid)]


class _LsaString(C.Structure):
    _fields_ = [("length", W.USHORT), ("maximum", W.USHORT), ("buffer", W.LPWSTR)]


class _LogonSession(C.Structure):
    _fields_ = [("size", W.ULONG), ("logon", _Luid), ("user", _LsaString), ("domain", _LsaString),
                ("package", _LsaString), ("type", W.ULONG), ("session", W.ULONG), ("sid", C.c_void_p)]


def token_facts():
    """What Windows says about this process's own token, logon and desktop session."""
    token = W.HANDLE()
    _kernel.GetCurrentProcess.restype = W.HANDLE
    _adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    if not _adv.OpenProcessToken(_kernel.GetCurrentProcess(), 0x8, C.byref(token)):  # TOKEN_QUERY
        raise _failed()
    _adv.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]

    def info(kind):
        size = W.DWORD()
        _adv.GetTokenInformation(token, kind, None, 0, C.byref(size))
        value = C.create_string_buffer(size.value)
        if not _adv.GetTokenInformation(token, kind, value, size, C.byref(size)):
            raise _failed()
        return value

    try:
        # Each buffer stays referenced while a pointer into it is read.
        owner, kind, raised, label, numbers = info(1), info(18), info(20), info(25), info(10)
        user = sid_text(C.c_void_p.from_buffer(owner).value)  # TokenUser: the SID pointer comes first.
        level = C.c_void_p.from_buffer(label).value  # TokenIntegrityLevel
        _adv.GetSidSubAuthorityCount.argtypes, _adv.GetSidSubAuthorityCount.restype = [C.c_void_p], C.POINTER(C.c_ubyte)
        _adv.GetSidSubAuthority.argtypes, _adv.GetSidSubAuthority.restype = [C.c_void_p, W.DWORD], C.POINTER(W.DWORD)
        integrity = _adv.GetSidSubAuthority(level, _adv.GetSidSubAuthorityCount(level).contents.value - 1).contents.value
        statistics = _Statistics.from_buffer(numbers)  # TokenStatistics
        logon = _Luid(statistics.authentication.low, statistics.authentication.high)
        facts = {"sid": user,
                 # 1: the account has one token and no administrator token beside it.
                 "elevationType": W.DWORD.from_buffer(kind).value,
                 "elevated": bool(W.DWORD.from_buffer(raised).value),
                 "integrityLevel": hex(integrity)}  # 0x2000 medium, 0x3000 high.
    finally:
        _kernel.CloseHandle.argtypes = [W.HANDLE]
        _kernel.CloseHandle(token)
    administrators, member = C.c_void_p(), W.BOOL()
    _adv.ConvertStringSidToSidW.argtypes = [W.LPCWSTR, C.POINTER(C.c_void_p)]
    _adv.CheckTokenMembership.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(W.BOOL)]
    if (not _adv.ConvertStringSidToSidW(ADMINISTRATORS, C.byref(administrators))
            or not _adv.CheckTokenMembership(None, administrators, C.byref(member))):
        raise _failed()
    facts["memberOfAdministrators"] = bool(member.value)
    secur = C.WinDLL("secur32")
    data = C.POINTER(_LogonSession)()
    secur.LsaGetLogonSessionData.argtypes = [C.POINTER(_Luid), C.POINTER(C.POINTER(_LogonSession))]
    if secur.LsaGetLogonSessionData(C.byref(logon), C.byref(data)) == 0:
        # 2 Interactive (at the screen), 10 RemoteInteractive (Remote Desktop).
        facts["logonType"] = data.contents.type
        secur.LsaFreeReturnBuffer.argtypes = [C.c_void_p]
        secur.LsaFreeReturnBuffer(data)
    session = W.DWORD()
    _kernel.ProcessIdToSessionId.argtypes = [W.DWORD, C.POINTER(W.DWORD)]
    if not _kernel.ProcessIdToSessionId(os.getpid(), C.byref(session)):
        raise _failed()
    facts["desktopSession"] = session.value
    return facts


def standard_token(token, account):
    """True for the token of the standard user of this run, in a desktop session of its own sign-in."""
    return (token["sid"] == account["sid"] and token["memberOfAdministrators"] is False
            # One token, no administrator token beside it; not elevated; medium integrity.
            and token["elevationType"] == 1 and token["elevated"] is False and token["integrityLevel"] == "0x2000"
            # A sign-in of this user itself, not in the session of services or of the runner's account.
            and token.get("logonType") in (2, 10) and token["desktopSession"] not in (0, account["runnerSession"]))


# ---- The user's own desktop session: Remote Desktop to this same computer.
class _Credential(C.Structure):
    _fields_ = [("flags", W.DWORD), ("type", W.DWORD), ("target", W.LPWSTR), ("comment", W.LPWSTR),
                ("written", W.FILETIME), ("blob_size", W.DWORD), ("blob", C.c_void_p), ("persist", W.DWORD),
                ("attribute_count", W.DWORD), ("attributes", C.c_void_p), ("alias", W.LPWSTR), ("user", W.LPWSTR)]


def remember(user, password):
    """Give Windows the password for the Remote Desktop target, in memory of this logon only."""
    blob = C.create_string_buffer(password.encode("utf-16-le"))
    # CRED_TYPE_GENERIC; CRED_PERSIST_SESSION: not saved to disk, gone at sign-out.
    value = _Credential(0, 1, DESKTOP_TARGET, None, W.FILETIME(), len(password) * 2, C.cast(blob, C.c_void_p),
                        1, 0, None, None, user)
    _adv.CredWriteW.argtypes = [C.POINTER(_Credential), W.DWORD]
    if not _adv.CredWriteW(C.byref(value), 0):
        raise _failed()


def forget():
    """Delete the saved sign-in. "absent" when Windows has none afterwards; else the failure."""
    _adv.CredDeleteW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD]
    _adv.CredDeleteW.restype = W.BOOL
    if _adv.CredDeleteW(DESKTOP_TARGET, 1, 0) or C.get_last_error() == 1168:  # Deleted, or Windows had none.
        return "absent"
    return f"left: CredDeleteW failed with error {C.get_last_error()}"


def windows_of(pid):
    """The texts of the windows of one process: what a person would read there."""
    user32 = C.WinDLL("user32", use_last_error=True)
    callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    found = []

    def text(handle):
        value = C.create_unicode_buffer(512)
        user32.GetWindowTextW(handle, value, 512)
        return value.value

    def child(handle, _):
        found.append(text(handle))
        return True

    def top(handle, _):
        owner = W.DWORD()
        user32.GetWindowThreadProcessId(handle, C.byref(owner))
        if owner.value == pid:
            found.append(text(handle))
            user32.EnumChildWindows(handle, callback(child), 0)
        return True

    user32.EnumWindows(callback(top), 0)
    return [item for item in found if item and "IME" not in item][:40]


def find_session(listing, name):
    """(number, state) of the user's desktop session in the list of Windows; None when it has none.

    The list has fixed columns below its heading. The user name is read only
    from the user column: a session name is never taken for a user.
    """
    rows = listing.splitlines()
    heading = next((row for row in rows if "USERNAME" in row and "STATE" in row), None)
    if heading is None:
        return None
    user, state = heading.index("USERNAME"), heading.index("STATE")
    for row in rows:
        if row is heading:
            continue
        fields, status = row[user:state].split(), row[state:].split()
        # With a user: its name and the number. Without: the number only.
        if len(fields) == 2 and fields[0].casefold() == name.casefold() and fields[1].isdigit() and status:
            return int(fields[1]), status[0]
    return None


def session_of(name, tries=3):
    """(number, state) of the user's desktop session; None when the list of Windows has none.

    A list that Windows does not give is an error here, never "no session".
    Windows is asked again before that, at most `tries` times in all.
    """
    listing = ""
    for attempt in range(tries):
        listing = execute([system("qwinsta.exe")])["stdout"]
        if "USERNAME" in listing and "STATE" in listing:
            return find_session(listing, name)
        if attempt + 1 < tries:
            time.sleep(2)
    raise AssertionError("Windows did not list the desktop sessions: " + json.dumps(listing[-500:]))


def sign_in(name, password):
    """A Remote Desktop sign-in of the user to this same computer. Returns the viewer and the session number."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Terminal Server Client") as key:
        # The viewer of the runner's own account must not ask about this
        # computer's own certificate: no person answers there.
        try:
            earlier = winreg.QueryValueEx(key, "AuthenticationLevelOverride")[0]
        except FileNotFoundError:
            earlier = None
        if saved("viewer-setting.json") is None:
            save("viewer-setting.json", {"earlier": earlier})
        winreg.SetValueEx(key, "AuthenticationLevelOverride", 0, winreg.REG_DWORD, 0)
    computer = os.environ["COMPUTERNAME"]
    remember(computer + "\\" + name, password)
    try:
        viewer = subprocess.Popen([str(system("mstsc.exe")), "/v:" + DESKTOP, "/w:1024", "/h:768"])
        try:
            # While `viewer` is held here, Windows gives its number to no other process.
            save("viewers.json", (saved("viewers.json") or []) + [process_record(viewer.pid)])
        except BaseException:
            viewer.kill()  # The cleanup step ends recorded viewers only.
            raise
        deadline, session = time.monotonic() + 180, None
        while time.monotonic() < deadline:
            time.sleep(3)
            session = session_of(name)
            if session and session[1] == "Active":
                return viewer, session[0]
            if viewer.poll() is not None:
                break
        raise RuntimeError("The Remote Desktop sign-in did not give the user a desktop session. Session: "
                           + json.dumps(session) + ". The viewer's windows: " + json.dumps(windows_of(viewer.pid)))
    finally:
        forget()


def sign_out(name, viewer):
    """Sign the user out. True when Windows lists no session of the user afterwards."""
    session = session_of(name)
    if session:
        execute([system("logoff.exe"), session[0]])
    deadline = time.monotonic() + 90
    while session_of(name) and time.monotonic() < deadline:
        time.sleep(2)
    if viewer is not None and viewer.poll() is None:
        viewer.kill()
    return session_of(name) is None


TASKS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$s = New-Object -ComObject 'Schedule.Service'; $s.Connect(); $f = $s.GetFolder('\')
function Find($name) {
    try { return $f.GetTask($name) }
    catch {
        $e = $_.Exception
        while ($null -ne $e.InnerException) { $e = $e.InnerException }
        if ($e.HResult -ne -2147024894) { throw }
        return $null
    }
}
if ($p.op -eq 'start') {
    $d = $s.NewTask(0)
    # The user's own token in the user's own desktop session; no password; not elevated.
    $d.Principal.UserId = $p.sid; $d.Principal.LogonType = 3; $d.Principal.RunLevel = 0
    $d.Settings.DisallowStartIfOnBatteries = $false; $d.Settings.StopIfGoingOnBatteries = $false
    $d.Settings.ExecutionTimeLimit = 'PT1H'
    $a = $d.Actions.Create(0); $a.Path = $p.program; $a.Arguments = $p.arguments; $a.WorkingDirectory = $p.folder
    $t = $f.RegisterTaskDefinition($p.name, $d, 6, $p.sid, $null, 3, $null)
    $null = $t.Run($null)
} elseif ($p.op -eq 'remove') {
    if ($null -ne (Find $p.name)) { $f.DeleteTask($p.name, 0) }
}
$t = Find $p.name
if ($null -eq $t) { @{exists=$false} | ConvertTo-Json -Compress }
else {
    @{exists=$true; state=[int]$t.State; lastResult=[long]$t.LastTaskResult;
      instances=[int]$t.GetInstances(0).Count} | ConvertTo-Json -Compress
}
"""


def task(op, name, **values):
    result = powershell(TASKS, {"op": op, "name": name, **values})
    need(result["exitCode"] == 0 and isinstance(result["json"], dict), "Task Scheduler refused " + op, result)
    return result["json"]


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # Absent, or the writer is at work.


def in_session(sid, program, phase, stage, exchange, *, timeout):
    """Start this file as the standard user in the user's desktop session; return its report."""
    arguments = subprocess.list2cmdline(["-I", "-B", str(stage / FILES[0]), "standard", "--stage", str(stage),
                                         "--exchange", str(exchange), "--phase", phase])
    task("start", HELPER, sid=sid, program=str(program), arguments=arguments, folder=str(exchange))
    deadline, idle = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        time.sleep(3)
        report = read_json(exchange / f"{phase}.json")
        if report and report.get("finished"):
            return report
        state = task("state", HELPER)
        idle = idle + 1 if state.get("instances") == 0 else 0
        if idle >= 5:
            report = read_json(exchange / f"{phase}.json")
            if report and report.get("finished"):
                return report
            raise RuntimeError("The program of the standard user ended without a result. Helper task: "
                               + json.dumps(state) + ". Report so far: " + json.dumps(report)[-3000:])
    raise TimeoutError("The program of the standard user did not end in time. Report so far: "
                       + json.dumps(read_json(exchange / f"{phase}.json"))[-3000:])


# ---- The Windows question about a certificate.
_user = C.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None


def dialogs(own=True):
    """The visible dialogs of this process (or of every program on this desktop), with their texts and buttons."""
    callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    _user.EnumWindows.argtypes = [callback, W.LPARAM]
    _user.EnumChildWindows.argtypes = [W.HWND, callback, W.LPARAM]
    _user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    _user.IsWindowVisible.argtypes = [W.HWND]
    _user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    _user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    _user.GetDlgCtrlID.argtypes = [W.HWND]
    found = []

    def text(handle, size=8192):
        value = C.create_unicode_buffer(size)
        _user.GetWindowTextW(handle, value, size)
        return value.value

    def kind(handle):
        value = C.create_unicode_buffer(32)
        _user.GetClassNameW(handle, value, 32)
        return value.value

    def top(handle, _):
        owner = W.DWORD()
        _user.GetWindowThreadProcessId(handle, C.byref(owner))
        if (owner.value == os.getpid()) is own and _user.IsWindowVisible(handle) and kind(handle) == "#32770":
            parts, buttons = [], {}

            def child(control, _):
                if kind(control) == "Button":
                    buttons[_user.GetDlgCtrlID(control)] = (control, text(control, 64).replace("&", ""))
                elif text(control):
                    parts.append(text(control))
                return True

            _user.EnumChildWindows(handle, callback(child), 0)
            found.append({"window": handle, "pid": owner.value, "title": text(handle, 256),
                          "text": "\n".join(parts), "buttons": buttons})
        return True

    _user.EnumWindows(callback(top), 0)
    return found


# What Windows asks about a root certificate of the person's own store, as a
# hosted runner shows it (English).
QUESTIONS = {"install": "Do you want to install this certificate?",
             "delete": "Do you want to DELETE the following certificate from the Root Store?"}


def is_question(dialog, operation, marks):
    """True when the dialog is the Windows question about this installation's certificate.

    It must show all three: the question of this operation, the certificate's
    name and its thumbprint. The name alone is not enough: every Amplifier
    installation has a certificate with this name. Spaces and letter case do
    not count: Windows groups a thumbprint in its own way.
    """
    plain = "".join((dialog["title"] + dialog["text"]).split()).casefold()
    wanted = [QUESTIONS[operation], *marks]
    return len(marks) == 2 and all(mark.strip() and "".join(mark.split()).casefold() in plain for mark in wanted)


def button_for(dialog, answer):
    """The dialog's Yes or No button: by its Windows number (6, 7), else by its English text."""
    number, word = (6, "yes") if answer == "yes" else (7, "no")
    if number in dialog["buttons"]:
        return number, dialog["buttons"][number][0]
    return next(((key, control) for key, (control, label) in dialog["buttons"].items()
                 if label.strip().casefold() == word), None)


@contextmanager
def answering(steps, asked, operation, answer, marks, *, limit=90, end=os._exit):
    """While the block runs, answer the one Windows question about this installation's certificate.

    Windows asks in a dialog when a program adds a root certificate to the
    person's own store, or deletes one. A person reads it and selects Yes or
    No. No person is present here: this thread records the text and gives the
    answer in the person's place, with a window message.

    It answers one dialog, once: a dialog of this process that `is_question`
    accepts. Each dialog window is one entry of `asked`, so two questions with
    the same text are two entries. Any other dialog, and a second question, is
    recorded and never answered. Nothing else would end that wait. So when a
    dialog of this process is open for `limit` seconds without an answer, with
    a text or without one, this thread fails the step with what the dialog
    shows and ends the program. It ends the program also when it cannot look
    at the dialogs, and when it cannot save the failure.
    """
    done, entries, posted, waiting, started = threading.Event(), {}, {}, {}, time.monotonic()
    _user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    _user.PostMessageW.restype = W.BOOL

    def record(key, entry, own=True):
        earlier = entries.get(key)
        if earlier == entry:
            return
        if earlier is None:
            entries[key] = entry
            if own:
                asked.append(entry)
            steps.report.setdefault("windowsQuestions", []).append(entry)
        else:  # The same window with another text: still one entry.
            earlier.clear()
            earlier.update(entry)
        steps.save()

    def stop(text, entry):
        try:
            steps.failed(RuntimeError(text + ": " + json.dumps(entry, default=str)[-2500:]))
            steps.finish()
        except Exception:
            pass  # The program ends all the same. Its caller then reports a program that ended without a result.
        finally:
            done.set()
            end(1)

    def look():
        now = time.monotonic()
        for dialog in dialogs():
            window = dialog["window"]
            if window in posted:
                if now - posted[window] > limit:
                    return stop("The answer did not close the Windows question", entries[window])
                continue
            # The limit runs from the first sight of the dialog, also while it has no text.
            first = waiting.setdefault(window, now)
            ours, button = is_question(dialog, operation, marks), button_for(dialog, answer)
            entry = {"window": window, "title": dialog["title"], "text": dialog["text"][:4000],
                     "buttons": sorted(label for _, label in dialog["buttons"].values()),
                     "theQuestion": ours, "answer": None}
            if dialog["text"]:  # Windows fills a dialog a moment after it shows it.
                # One answer in all. A second question is another case: it stays. An
                # answer counts only when Windows took the message (WM_COMMAND).
                if ours and button and not posted and _user.PostMessageW(window, 0x0111, button[0], button[1]):
                    posted[window], entry["answer"] = now, answer
                record(window, entry)
            if window not in posted and now - first > limit:
                record(window, entry)
                return stop("A Windows dialog of this program got no answer", entry)
        if not posted and now - started > 25:
            # For a failed step: the dialogs of other programs that this program can see.
            for dialog in dialogs(own=False):
                record(("other", dialog["window"]),
                       {"otherProgram": dialog["pid"], "title": dialog["title"], "text": dialog["text"][:2000],
                        "buttons": sorted(label for _, label in dialog["buttons"].values())}, own=False)

    def watch():
        while not done.wait(0.5):
            try:
                look()
            except BaseException as error:
                if not done.is_set():
                    stop("The test could not look at the dialogs of this program",
                         {"error": type(error).__name__ + ": " + str(error)[-500:]})

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(5)


def certificate_marks(home):
    """The name and the thumbprint that Windows shows for this installation's certificate."""
    from amplifier_web import windows_tls, windows_trust
    identity = windows_tls.keyless_identity(home)
    need(identity is not None, "This installation has no key-less certificate")
    return [windows_trust.ROOT_NAME, windows_trust.thumbprint(identity[0])]


ROOTS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$found = @(Get-ChildItem -LiteralPath 'Cert:\CurrentUser\Root' | Where-Object { $_.Thumbprint -eq $p.thumbprint })
@{found=$found.Count} | ConvertTo-Json -Compress
"""


def windows_root_has(thumbprint):
    """Windows' own answer: do this user's trusted root certificates hold this thumbprint?

    Windows PowerShell lists the store. No code of the product takes part.
    """
    listed = powershell(ROOTS, {"thumbprint": "".join(thumbprint.split()).upper()})
    need(listed["exitCode"] == 0 and isinstance(listed["json"], dict),
         "Windows did not list this user's root certificates", listed)
    return listed["json"]["found"] > 0


def one_question(asked, answer):
    """Exactly one dialog window: the question about this installation's certificate, with this answer."""
    return len(asked) == 1 and asked[0].get("theQuestion") is True and asked[0].get("answer") == answer


# ---- As the runner's account.
def folders():
    base = Path(os.environ["RUNNER_TEMP"]).resolve()
    return base / "standard-account-stage", base / "standard-account-exchange"


def prepare():
    """Build what the standard user installs and runs. Preparation is not the subject."""
    report = {"status": "FAIL", "stage": "candidate"}
    try:
        git = lambda *args: subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
        need(not git("status", "--porcelain"), "The candidate checkout must be clean")
        revision = git("rev-parse", "HEAD")
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        stage, exchange = folders()
        need(not stage.exists() and not exchange.exists(), "This runner already has this case's folders")
        stage.mkdir(parents=True)
        exchange.mkdir(parents=True)
        uv = shutil.which("uv")
        need(uv, "uv is not on PATH")
        report["stage"] = "candidate wheel"
        release = load("standard_account_release", ROOT / "scripts/release.py")
        release.stage_source(ROOT, "microsoft/amplifier-unified", revision, version)
        built = execute([uv, "build", "--wheel", "--out-dir", stage / "build"], cwd=ROOT, timeout=300)
        need(built["exitCode"] == 0, "The candidate wheel build failed", built)
        wheels = list((stage / "build").glob("*.whl"))
        need(len(wheels) == 1, "Expected exactly one candidate wheel")
        wheel = stage / wheels[0].name
        shutil.copyfile(wheels[0], wheel)
        report["stage"] = "setup's compiled checks"
        setup = load("standard_account_setup", ROOT / "tests/fixtures/windows_setup_native.py")
        shutil.copyfile(setup.build(stage / "setup"), stage / "native-setup-tests.exe")
        report["stage"] = "files"
        shutil.copyfile(uv, stage / "uv.exe")
        for name in FILES:
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, stage / name)
        # A lone Git wrapper cannot fetch. The user copies this tree, as setup unpacks one.
        git_tree = Path(git("--exec-path")).parents[2]
        need((git_tree / "cmd/git.exe").is_file(), "The runner's Git tree was not found")
        prepared = {"stage": str(stage), "exchange": str(exchange), "version": version, "revision": revision,
                    "wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                    "python": sys.executable, "gitTree": str(git_tree)}
        (stage / "prepared.json").write_text(json.dumps(prepared), encoding="utf-8")
        save("prepared.json", prepared)
        report.update(status="PASS", stage="prepared", revision=revision, version=version)
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error)[-2000:])
    save("preparation.json", report)
    print(json.dumps(report), flush=True)
    return report["status"] == "PASS"


def lines(report):
    """One line for each step, as the result states it."""
    out = []
    for step in report["steps"]:
        out.append(("PASSED as a standard user: " if step["status"] == "PASS" else "FAILED: ") + step["name"]
                   + ("" if step["status"] == "PASS" else " -- " + str(step.get("reason"))[:1500]))
    out.extend(f"NOT SHOWN: {name} -- {why}" for name, why in NOT_SHOWN)
    return out


def run():
    report = {"status": "FAIL", "stage": "the account",
              "steps": [{"name": name, "status": "FAIL", "reason": "Not reached"}
                        for name in FIRST + SECOND + THIRD],
              "notShown": [{"name": name, "why": why} for name, why in NOT_SHOWN]}
    viewer = None
    try:
        prepared = saved("prepared.json")
        need(prepared, "The preparation step did not finish")
        stage, exchange = Path(prepared["stage"]), Path(prepared["exchange"])
        runner = token_facts()
        need(saved("ownership.json") is None, "This runner already has an account of this case")
        report["stage"] = "the end of a recorded process"
        # How the cleanup step ends a recorded viewer, shown on Windows before the user exists.
        report["recordedProcess"] = recorded_process_check()
        need(report["recordedProcess"] == {"createdAtItsStart": True, "withAnotherCreationTime": "absent",
                                           "thenItStillRuns": True, "withItsOwnRecord": "ended",
                                           "thenItHasEnded": True},
             "A recorded process is not ended by its own record only", report["recordedProcess"])
        report["stage"] = "the account"
        name = "amp-std-" + uuid.uuid4().hex[:6]
        need(account_sid(name) is None, "An account with this name exists; it is not adopted")
        # Recorded before Windows is asked for the account: no record means no
        # request. The cleanup removes exactly the account that has this name
        # and this mark as its comment, also when a later line here fails.
        mark = MARK + uuid.uuid4().hex
        save("ownership.json", {"user": name, "mark": mark})
        password = new_password()
        sid = create_user(name, password, mark)
        save("ownership.json", {"user": name, "mark": mark, "sid": sid})
        need(account_comment(name) == mark, "Windows does not show this run's mark on the new account")
        join(name, USERS)
        join(name, REMOTE_DESKTOP_USERS)  # Only the right to sign in through Remote Desktop.
        groups = local_groups(name)
        need(sorted(groups) == sorted([sid_name(USERS), sid_name(REMOTE_DESKTOP_USERS)]),
             "The user's groups are not those of a standard account", groups)
        report["account"] = {"sid": sid, "localGroups": groups, "runnerAccount": runner}
        # The stage: administrators and Windows change it, the user reads and
        # runs it, and nothing comes from the folder above. The exchange: the
        # user's reports.
        protected = execute([system("icacls.exe"), stage, "/inheritance:r", "/grant:r",
                             f"*{ADMINISTRATORS}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", f"*{sid}:(OI)(CI)RX", "/Q"])
        need(protected["exitCode"] == 0, "This case's stage folder was not protected", protected)
        granted = execute([system("icacls.exe"), exchange, "/grant", f"*{sid}:(OI)(CI)M", "/T", "/Q"])
        need(granted["exitCode"] == 0, "The user was not given access to this case's exchange folder", granted)
        # Which user this run created, and the workflow's facts. `standard`
        # accepts it only as a file of the administrators group.
        record = stage / "account.json"
        record.write_text(json.dumps({"sid": sid, "runnerSession": runner["desktopSession"],
                                      "hosted": {fact: os.environ.get(fact) for fact in HOSTED_FACTS}}),
                          encoding="utf-8")
        if read_protected(record)[1] != ADMINISTRATORS:
            execute([system("icacls.exe"), record, "/setowner", f"*{ADMINISTRATORS}", "/Q"])
        owner = read_protected(record)[1]
        report["account"]["record"] = {"owner": owner}
        need(owner == ADMINISTRATORS, "The account record does not belong to the administrators group", owner)
        need(not task("state", "AmplifierUnified-" + sid)["exists"], "The new user already has an Amplifier task")

        report["stage"] = "the first sign-in"
        save("result.json", report)
        viewer, session = sign_in(name, password)
        report["firstSignIn"] = {"desktopSession": session}
        report["stage"] = "the steps after the first sign-in"
        first = in_session(sid, prepared["python"], "first", stage, exchange, timeout=40 * 60)
        report["steps"][:len(FIRST)] = first["steps"]
        report["first"] = {key: value for key, value in first.items() if key != "steps"}
        save("result.json", report)
        need(all(step["status"] == "PASS" for step in first["steps"]), "A step after the first sign-in failed")

        report["stage"] = "the sign-out"
        need(sign_out(name, viewer), "Windows still lists a desktop session of the user after the sign-out")
        viewer = None
        deadline = time.monotonic() + 60
        while True:
            between = task("state", "AmplifierUnified-" + sid)
            if between.get("instances") == 0 or time.monotonic() >= deadline:
                break
            time.sleep(2)
        need(between["exists"] and between["instances"] == 0,
             "After the sign-out the user's Amplifier task must exist and must not run", between)
        report["signOut"] = {"task": between}
        report["stage"] = "the second sign-in"
        viewer, session = sign_in(name, password)
        del password
        report["secondSignIn"] = {"desktopSession": session}
        report["stage"] = "the steps after the second sign-in"
        second = in_session(sid, first["python"], "second", stage, exchange, timeout=15 * 60)
        report["steps"][len(FIRST):len(FIRST) + len(SECOND)] = second["steps"]
        report["second"] = {key: value for key, value in second.items() if key != "steps"}
        save("result.json", report)
        need(all(step["status"] == "PASS" for step in second["steps"]), "A step after the second sign-in failed")
        report["stage"] = "the check after the delete choice"
        # Not with the installation's Python: the delete choice removed it.
        third = in_session(sid, prepared["python"], "third", stage, exchange, timeout=8 * 60)
        report["steps"][len(FIRST) + len(SECOND):] = third["steps"]
        report["third"] = {key: value for key, value in third.items() if key != "steps"}
        save("result.json", report)
        need(all(step["status"] == "PASS" for step in third["steps"]), "The check after the delete choice failed")
        need(not task("state", "AmplifierUnified-" + sid)["exists"],
             "After the uninstall Task Scheduler still has the user's Amplifier task")
        report.update(status="PASS", stage="Complete")
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error)[-4000:])
    finally:
        try:
            owned = saved("ownership.json")
            if owned:
                report["signedOutAtEnd"] = sign_out(owned["user"], viewer)
        except Exception as error:
            report["signedOutAtEnd"] = "failed: " + type(error).__name__
    report["lines"] = lines(report)
    save("result.json", report)
    print("\n".join(report["lines"]), flush=True)
    print(json.dumps({key: report.get(key) for key in ("status", "stage", "errorType", "error")}), flush=True)
    return report["status"] == "PASS"


PROFILE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $p.sid } | Remove-CimInstance
$left = @(Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $p.sid }).Count
@{left=$left} | ConvertTo-Json -Compress
"""


def remove_profile(sid):
    """Remove the user's profile. "absent" only when Windows lists none for the user afterwards."""
    said = None
    for _ in range(3):
        removed = powershell(PROFILE, {"sid": sid}, timeout=150)
        if removed["exitCode"] == 0 and (removed["json"] or {}).get("left") == 0:
            return "absent"
        said = removed["stderr"][-300:]
        time.sleep(5)  # Windows can hold the profile for a moment after the sign-out.
    return "left: " + str(said)


def whose(name, mark):
    """Whose account has this name: "this run's" (its comment is the run's mark), "another account" or "absent"."""
    comment = account_comment(name)
    return "absent" if comment is None else "this run's" if comment == mark else "another account"


def remove_account(name, mark):
    """Delete the account. Its mark is read again directly before: only this run's account is deleted."""
    state = whose(name, mark)
    return delete_user(name) if state == "this run's" else "not deleted: " + state


class _Processes:
    """Windows, for one process that is opened once: its creation time, its end, and the request to end it."""

    def __init__(self):
        _kernel.OpenProcess.argtypes, _kernel.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
        _kernel.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
        _kernel.WaitForSingleObject.argtypes, _kernel.WaitForSingleObject.restype = [W.HANDLE, W.DWORD], W.DWORD
        _kernel.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
        _kernel.CloseHandle.argtypes = [W.HANDLE]

    def open(self, number, end=True):
        """The process that has this number now, opened; None when Windows has no process with it."""
        # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, and PROCESS_TERMINATE for the request to end it.
        handle = _kernel.OpenProcess(0x1000 | 0x100000 | (0x1 if end else 0), False, number)
        if not handle:
            if C.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: no process has this number.
                return None
            raise _failed()
        return handle

    def created(self, handle):
        """When Windows created the process, in units of 100 nanoseconds."""
        times = [W.FILETIME() for _ in range(4)]
        if not _kernel.GetProcessTimes(handle, *[C.byref(item) for item in times]):
            raise _failed()
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def ended(self, handle, seconds):
        """True when the process has ended, after a wait of at most `seconds`."""
        status = _kernel.WaitForSingleObject(handle, int(seconds * 1000))
        if status not in (0, 0x102):  # WAIT_OBJECT_0: it has ended. WAIT_TIMEOUT: it runs.
            raise _failed()
        return status == 0

    def end(self, handle):
        """Ask Windows to end the process. True when Windows took the request."""
        return bool(_kernel.TerminateProcess(handle, 1))

    def close(self, handle):
        _kernel.CloseHandle(handle)


def process_record(number, windows=None):
    """What names one process for a later program: its number and the time at which Windows created it.

    Windows gives the number of an ended process to a later process. The
    creation time tells the two apart. Call this only while the starter of
    the process still holds it: until then no other process gets the number.
    """
    windows = windows or _Processes()
    handle = windows.open(number, end=False)
    need(handle is not None, "Windows has no process with the number of a program that this test started", number)
    try:
        return {"pid": number, "created": windows.created(handle)}
    finally:
        windows.close(handle)


def end_recorded(record, seconds=20, windows=None):
    """End the one process that this record names. Returns "ended", "absent" or "left: ...".

    The process that has the recorded number is opened once. Everything
    after that is done through that one open process: the read of its
    creation time, the request to end it, and the wait for its end. A
    process that got the number later has another creation time: it is
    "absent" here, and nothing is done to it. "absent" is also the answer
    when no process has the number.
    """
    windows = windows or _Processes()
    handle = windows.open(record["pid"])
    if handle is None:
        return "absent"
    try:
        if windows.created(handle) != record["created"]:
            return "absent"  # A later process has the number. It is not touched.
        if windows.ended(handle, 0):
            return "ended"
        taken = windows.end(handle)
        if windows.ended(handle, seconds):
            return "ended"
        return f"left: process {record['pid']} runs" + ("" if taken else "; Windows refused to end it")
    finally:
        windows.close(handle)


def end_viewers(records, seconds=20):
    """End the Remote Desktop viewers that this run recorded. "ended" only when each one is ended or absent.

    A viewer that fails does not stop the viewers after it. A viewer that
    the run started and did not record is not ended here.
    """
    left = []
    for record in records:
        try:
            result = end_recorded(record, seconds)
        except Exception as error:
            result = "left: " + type(error).__name__ + ": " + str(error)[-200:]
        if result not in ("ended", "absent"):
            left.append(result)
    return "ended" if not left else "left: " + json.dumps(left)


def filetime_now():
    """Now, in the units of a Windows creation time: 100 nanoseconds since the year 1601."""
    return int((time.time() + 11644473600) * 10 ** 7)


def recorded_process_check(windows=None, start=None):
    """Show on Windows, with a program of this test, how `end_recorded` decides.

    The recorded creation time is the time of the program's start (within
    two seconds): it is the program's own, and no constant. The same number
    with another creation time is another process: nothing is ended, and the
    program still runs. The program's own record ends it.
    """
    before = filetime_now()
    program = (start or subprocess.Popen)([sys.executable, "-I", "-c", "import time; time.sleep(600)"])
    try:
        record = process_record(program.pid, windows)
        at_start = before - 2 * 10 ** 7 <= record["created"] <= filetime_now() + 2 * 10 ** 7
        other = end_recorded({**record, "created": record["created"] + 1}, 5, windows)
        kept = program.poll() is None
        own = end_recorded(record, 20, windows)
        try:
            program.wait(20)
        except subprocess.TimeoutExpired:
            pass
        return {"createdAtItsStart": at_start, "withAnotherCreationTime": other, "thenItStillRuns": kept,
                "withItsOwnRecord": own, "thenItHasEnded": program.poll() is not None}
    finally:
        if program.poll() is None:
            program.kill()


def ask_to_end_programs(name):
    """Try once to ask Windows to end the programs that run under this user's name.

    A request only. When it cannot be sent, the answer says so. Nothing here
    reads whether a program of the user is left, and the answer does not
    decide whether the cleanup passes.
    """
    try:
        asked = execute([system("taskkill.exe"), "/F", "/FI", f"USERNAME eq {name}"])
    except Exception as error:
        return f"not asked: {type(error).__name__}: {str(error)[-200:]}. No read of what is left."
    return f"asked: taskkill ended with {asked['exitCode']}. No read of what is left."


def restore_setting(earlier, registry):
    """Put the viewer's setting back. "restored" only when Windows then has the earlier value, or none as before."""
    name = "AuthenticationLevelOverride"
    with registry.CreateKey(registry.HKEY_CURRENT_USER, r"Software\Microsoft\Terminal Server Client") as key:
        if earlier is None:
            try:
                registry.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        else:
            registry.SetValueEx(key, name, 0, registry.REG_DWORD, earlier)
        try:
            now = registry.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            now = None
    return "restored" if now == earlier else "left: Windows has the value " + repr(now)


# The run records the account's SID directly after Windows created the account, and it signs the
# user in only after that record. Windows names the user's Amplifier task and its profile by that
# SID. Without a SID the cleanup cannot read them: it counts them as left. It concludes nothing
# from an account that is absent.
# What each part of the cleanup must report. With an account record: also the account's parts.
# The request to end the user's programs is not here: nothing reads what it left.
CLEAN = {"credential": ("absent",), "viewers": ("ended",), "viewerSetting": ("restored", "unchanged")}
OWNED_CLEAN = {"signedOut": (True,), "helperTask": ("absent",), "amplifierTask": ("absent",),
               "profile": ("absent",), "account": ("absent",)}


def cleaned(result, owned):
    """True only when every part that `CLEAN` names is done, and with an account record every part of `OWNED_CLEAN`."""
    wanted = {**CLEAN, **(OWNED_CLEAN if owned else {})}
    return all(result.get(name) in values for name, values in wanted.items())


def cleanup():
    """End the recorded viewers; sign the user out; remove its tasks, its profile, the account and the saved sign-in.

    Before anything of the account is removed, the account with the recorded
    name is read: only an account with this run's mark is this run's. When the
    name belongs to another account, or Windows does not answer, nothing of
    that account is touched and the step fails. Apart from that, a part that
    fails does not stop the parts after it, and each failure is kept. The step
    passes only when `cleaned` accepts the result. The step also tries to ask
    Windows to end the user's programs. A request that cannot be sent is kept
    in the result. Nothing reads whether a program is left, and `cleaned`
    does not name that request.
    """
    result, owned = {}, saved("ownership.json")

    def part(label, action):
        try:
            result[label] = action()
        except Exception as error:
            result[label] = "left: " + type(error).__name__ + ": " + str(error)[-300:]

    def gone(task_name):
        return "absent" if not task("remove", task_name)["exists"] else "left"

    def setting():
        earlier = saved("viewer-setting.json")
        if earlier is None:
            return "unchanged"
        import winreg
        return restore_setting(earlier["earlier"], winreg)

    def reports():
        # The user's own reports, beside the evidence of the runner's account.
        for name in ("first.json", "journey.json", "certificate.json", "second.json", "third.json"):
            report = read_json(folders()[1] / name)
            if report is not None:
                save("user-" + name, report)
        return "saved"

    part("credential", forget)
    part("viewers", lambda: end_viewers(saved("viewers.json") or []))
    if owned:
        name, mark, sid = owned.get("user"), owned.get("mark"), owned.get("sid")
        if not (isinstance(sid, str) and sid.startswith("S-1-")):
            sid = None  # No SID: the same as no record of it.
        if not name or not mark:
            result["account"] = "left: the record of the account is not complete"
        else:
            # The record is written before the account, so the account can be absent.
            part("accountBefore", lambda: whose(name, mark))
            state, unread = result["accountBefore"], "left: not read: the run recorded no SID for the account"
            if state == "this run's" and sid is None:
                # Windows created the account, and the line that records its SID did not run.
                try:
                    sid = sid_text(account_sid(name))
                except Exception as error:
                    unread = "left: the account's SID was not read: " + type(error).__name__

            def by_sid(action):
                """A part that Windows names by the account's SID: its Amplifier task, its profile.

                With a SID the part is done and read, also when the account is
                absent. Without a SID nothing can be read: the part counts as
                left. Nothing is concluded from an account that is absent.
                """
                return action(sid) if sid else unread

            if state in ("this run's", "absent"):
                part("signedOut", lambda: sign_out(name, None))
                part("programs", lambda: ask_to_end_programs(name) if state == "this run's"
                     else "not asked: Windows has no account with this name")
                part("helperTask", lambda: gone(HELPER))
                part("amplifierTask", lambda: by_sid(lambda sid: gone("AmplifierUnified-" + sid)))
                part("profile", lambda: by_sid(remove_profile))
                if state == "this run's":
                    part("deleteStatus", lambda: remove_account(name, mark))
                part("account", lambda: "absent" if account_sid(name) is None else "left")
            else:
                # Another account has this name, or Windows did not say whose it is: nothing of it is touched.
                result["account"] = "left: " + (state if state == "another account"
                                                else "Windows did not say whose account this is")
    part("viewerSetting", setting)
    part("reports", reports)
    part("saved", lambda: save("cleanup.json", result) or "yes")
    print(json.dumps(result), flush=True)
    # The runner is deleted after the job. What `cleaned` names must be done before it ends.
    return cleaned(result, owned)


# ---- As the standard user, in the user's own desktop session.
class Steps:
    """The user's report: one entry for each step, saved after each change."""

    def __init__(self, path, names, **facts):
        self.path, self.names, self.lock = Path(path), names, threading.Lock()
        self.report = {"finished": False, **facts,
                       "steps": [{"name": name, "status": "FAIL", "reason": "Not reached"} for name in names]}
        self.save()

    def save(self):
        with self.lock:
            partial = self.path.with_suffix(".partial")
            partial.write_text(json.dumps(self.report, indent=2, default=str) + "\n", encoding="utf-8")
            os.replace(partial, self.path)

    def begin(self, name):
        self.report["steps"][self.names.index(name)]["reason"] = "In progress"
        self.save()

    def passed(self, name, **facts):
        self.report["steps"][self.names.index(name)] = {"name": name, "status": "PASS", **facts}
        self.save()

    def failed(self, error):
        """The step that was in progress failed with this text. Later steps were not reached."""
        text = (type(error).__name__ + ": " + str(error))[-3000:]
        for step in self.report["steps"]:
            if step.get("reason") == "In progress":
                step["reason"] = text
        self.report.update(errorType=type(error).__name__, error=text)
        self.save()

    def finish(self):
        self.report["finished"] = True
        self.save()
        return all(step["status"] == "PASS" for step in self.report["steps"])


def accept_start(steps, key, again, home, root, started, state, status, text, *, wait=time.sleep):
    """Require one start of the launcher, and say which answer was not as required.

    A start has three answers: the launcher's own, Task Scheduler's, and the
    app's status. The report gets all three before the check. When that save
    fails, the step fails with its error. The rules are those of the
    start-again case. A step fails when an answer is not as its rule requires,
    and also when the rule cannot read the answer.

    The text of such a step has this order: the names of those parts; for each
    part the values that its rule reads; whether Task Scheduler and the status
    were as required five seconds later; then more of each answer. A value
    that is not short is given as the first 60 characters of its Python form.
    The text is cut at 2900 characters, at its end. So it keeps its start,
    also in the report, which keeps the last 3000 characters of a step's text.
    The report holds the three answers as the case got them.

    The read five seconds later never makes the step pass. It, the read of the
    task's output and the second save of the report can each fail. The step
    then keeps its text, and the text or the report holds that error.

    One run of this case failed at a start with only the launcher's answer in
    its text, and the launcher had said that the start worked (run 44149453625,
    November 17, 2026). That run does not say which other answer was wrong.
    """
    record = steps.report[key] = {"launcher": started, "task": state, "serviceStatus": status}
    steps.save()

    def attempt(read):
        try:
            return read()
        except Exception as error:
            return {"errorType": type(error).__name__, "error": str(error)[-300:]}

    rules = {"the launcher": lambda: started["exitCode"] == 0 and (started["json"] or {}).get("background") is True,
             "Task Scheduler": lambda: again.running(state),
             "the app's status": lambda: again.healthy(status)}
    wrong = [name for name, rule in rules.items() if attempt(rule) is not True]
    if not wrong:
        return
    answers = {"the launcher": (started, ("background",)),
               "the app's status": (status, ("ok", "processVerified", "responding"))}

    def short(value):
        plain = value is None or isinstance(value, (bool, int, str)) and len(str(value)) <= 60
        return value if plain else repr(value)[:60]

    def values(name):
        if name == "Task Scheduler":
            return {field: short(state.get(field)) for field in ("managed", "state", "instances")}
        result, fields = answers[name]
        said = result.get("json")
        return {"exitCode": short(result.get("exitCode")),
                **({field: short(said.get(field)) for field in fields} if isinstance(said, dict)
                   else {"json": short(said)})}

    def more(name):
        if name == "Task Scheduler":
            return state
        result = answers[name][0]
        printed = {} if isinstance(result.get("json"), dict) else {"stdout": str(result.get("stdout") or "")[-300:]}
        return {"json": result.get("json"), **printed, "stderr": str(result.get("stderr") or "")[-300:]}

    def later():
        wait(5)
        pair = {"task": again.task_facts(home), "serviceStatus": again.cli(root, "service", "status", "--json")}
        record["fiveSecondsLater"] = pair
        return {"Task Scheduler": again.running(pair["task"]), "the app's status": again.healthy(pair["serviceStatus"])}

    def output():
        log = home / "config/services/task-output.log"
        return ("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])[-6000:]
                if log.exists() else None)

    then = attempt(later)
    record.setdefault("fiveSecondsLater", then)
    record["taskOutput"] = attempt(output)
    unsaved = attempt(steps.save)
    detail = {**{name: attempt(lambda: values(name)) for name in wrong}, "fiveSecondsLater": then,
              **({"notSavedInTheReport": unsaved} if unsaved else {}),
              "more": {name: attempt(lambda: more(name)) for name in wrong}}
    raise AssertionError((text + ". Not as required: " + ", ".join(wrong) + ": "
                          + json.dumps(detail, default=str))[:2900])


def installation_folder():
    """Setup's folder for this account: %LOCALAPPDATA%\\Amplifier."""
    return Path(os.environ["LOCALAPPDATA"]) / "Amplifier"


def setup_reads(stage, exchange):
    """What setup's own compiled code reads for this account. It changes nothing."""
    result = execute([stage / "native-setup-tests.exe", "--account"], cwd=exchange, timeout=60)
    need(result["exitCode"] == 0 and isinstance(result["json"], dict), "Setup's own code could not read the account", result)
    return result["json"]


def first(stage, exchange, account, token):
    """Setup's own checks and the installation, with the runner's Python. Then the journey, with the app's own."""
    steps = Steps(exchange / "first.json", FIRST, token=token)
    try:
        prepared = json.loads((stage / "prepared.json").read_text(encoding="utf-8"))
        root = installation_folder()
        python = root / "tools/amplifier-unified/Scripts/python.exe"
        steps.report.update(root=str(root), python=str(python))

        steps.begin(FIRST[0])
        need(standard_token(token, account), "This is not a standard user in a desktop session of its own sign-in", token)
        need(root.parent.parent.parent == Path(os.environ["USERPROFILE"]) and not root.exists(),
             "The installation folder is not a new folder in the user's own profile")
        # What admitted this program, shown on Windows in both directions: a file
        # that this user makes is the user's own and changeable; the run's account
        # record and its folder are not.
        own = exchange / "own-file.json"
        own.write_text("{}", encoding="utf-8")
        mine, theirs = read_protected(own), read_protected(stage / "account.json")
        folders_read = (read_protected(exchange, folder=True)[2], read_protected(stage, folder=True)[2])
        need(mine == (b"{}", token["sid"], True) and theirs[1:] == (ADMINISTRATORS, False)
             and folders_read == (True, False),
             "Windows does not tell a file of this user from the run's account record",
             {"own": mine[1:], "record": theirs[1:], "exchangeAndStage": folders_read})
        steps.passed(FIRST[0], token=token, recordOwner=theirs[1], recordChangeable=theirs[2])

        steps.begin(FIRST[1])
        checks = execute([stage / "native-setup-tests.exe"], cwd=exchange, timeout=240)
        actual = checks["json"] or {}
        need(checks["exitCode"] == 0 and actual.get("ok") is True and actual.get("assertions", 0) >= 50,
             "Setup's compiled checks failed as a standard user", checks)
        # "none": setup's elevation check lets this account continue. The
        # runner's own account never reaches this value.
        need(actual.get("elevation") == "none", "Setup's elevation check did not read a standard account", actual)
        # Only a proven "absent" lets setup continue on an ordinary PC.
        need(actual.get("signInTask") == "absent", "Setup could not read Task Scheduler as a standard user", actual)
        # A folder that an ordinary program of this person made is this person's
        # own. For the runner's account it belongs to the administrators group.
        need(actual.get("ordinaryFolder") == "unfinished",
             "Setup did not read an ordinary folder of a standard user as that user's own", actual)
        # The one stop that is left is the Windows version: the runner is Windows Server.
        need(actual.get("precheckStop") == "windows-or-processor",
             "Setup's checks before any question stopped for another reason than the Windows version", actual)
        before = setup_reads(stage, exchange)
        need(before.get("elevation") == "none" and before.get("folder") == "absent"
             and before.get("signInTask") == "absent" and before["folderLength"] <= before["folderLimit"],
             "Setup's own code did not read a new account that it can install for", before)
        # The program of these checks is made by setup's compiler with setup's
        # options, and it just ran here. Its file asks Windows for the rights
        # of the person who starts it, so Windows asked this user for no
        # administrator. (The setup file itself is not compiled in this case.)
        asks = execution_level(stage / "native-setup-tests.exe")
        need(asks == (True, ["asInvoker"]),
             "The program compiled from setup's sources does not ask for the rights of the person who starts it", asks)
        steps.passed(FIRST[1], checks=actual, account=before, programAsksFor=asks[1][0])

        steps.begin(FIRST[2])
        files = load("standard_account_files", stage / "amplifier_web/windows_files.py")
        for name in ("", "app", "app/updates", "shared", "ownership", "capture", "import",
                     "s", "workspace", "cache", "python", "python-bin", "tools",
                     "tool-bin", "temp", "uv", "git", "git/cmd"):
            with files.directory(root / name, owned_root=root, create=True):
                pass
        shutil.copyfile(stage / "uv.exe", root / "uv/uv.exe")
        uv = root / "uv/uv.exe"
        # Setup's own folders and settings for uv, and setup's two commands for
        # a private Python. The runner's Python is not the app's Python.
        env = {**os.environ, "UV_NO_CONFIG": "1", "UV_NO_PROGRESS": "1", "UV_LINK_MODE": "copy",
               "UV_NO_BUILD_PACKAGE": "amplifier-core", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "UV_CACHE_DIR": str(root / "cache"), "UV_TOOL_DIR": str(root / "tools"),
               "UV_TOOL_BIN_DIR": str(root / "tool-bin"), "UV_PYTHON_INSTALL_DIR": str(root / "python"),
               "UV_PYTHON_BIN_DIR": str(root / "python-bin"), "TEMP": str(root / "temp"), "TMP": str(root / "temp")}
        fetched = execute([uv, "python", "install", "3.13", "--no-bin", "--no-registry"], env=env, cwd=root, timeout=600)
        need(fetched["exitCode"] == 0, "uv did not install a private Python", fetched)
        found = execute([uv, "python", "find", "--no-project", "--managed-python", "3.13"], env=env, cwd=root)
        base = Path(found["stdout"].strip().splitlines()[-1]) if found["exitCode"] == 0 and found["stdout"].strip() else None
        need(base is not None and base.is_file() and root / "python" in base.parents,
             "The private Python is not inside the installation folder", found)
        wheel = root / prepared["wheel"]
        shutil.copyfile(stage / prepared["wheel"], wheel)
        need(hashlib.sha256(wheel.read_bytes()).hexdigest() == prepared["sha256"], "The candidate wheel changed")
        installed = execute([uv, "tool", "install", "--python", base, "--link-mode", "copy", wheel],
                            env=env, cwd=root, timeout=900)
        need(installed["exitCode"] == 0 and python.is_file(), "The app was not installed into the user's folder", installed)
        shutil.copytree(prepared["gitTree"], root / "git", dirs_exist_ok=True)
        git = execute([root / "git/cmd/git.exe", "--version"])
        need(git["exitCode"] == 0, "The private Git does not run", git)
        steps.passed(FIRST[2], root=str(root), rootLength=len(str(root)), basePython=str(base),
                     installSeconds=installed["seconds"])

        # The rest needs the installed app. Its report continues this one.
        ran = execute([python, "-I", "-B", Path(__file__), "standard", "--stage", stage,
                       "--exchange", exchange, "--phase", "journey"], cwd=exchange, timeout=35 * 60)
        journey_report = read_json(exchange / "journey.json")
        need(journey_report and journey_report.get("finished"), "The journey ended without a result", ran)
        steps.report["steps"][len(FIRST) - len(JOURNEY):] = journey_report["steps"]
        steps.report["journey"] = {key: value for key, value in journey_report.items() if key != "steps"}
    except BaseException as error:
        steps.failed(error)
    return steps.finish()


def product(stage, root):
    """Bind this process to the installation, as its launcher does, and load the maintained helpers."""
    home = root / "app"
    need(Path(sys.prefix) == root / "tools/amplifier-unified", "Run this phase with the installation's own Python")
    # Bind homes before importing code that can read the user's profile.
    os.environ.update(AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(root / "shared"),
                      AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"), AMPLIFIER_GITHUB_ACCESS="anonymous")
    # The maintained start-again case owns the launcher calls, the message-box
    # guard and the uninstall question. This case adds only the account.
    return home, load("standard_account_start_again", stage / "tests/fixtures/windows_start_again_native.py")


def stop(again, root, notices):
    """The product's "Stop Amplifier", with its one question answered Yes."""
    from amplifier_web import windows_consumer, windows_installation
    original = windows_installation.owner_notice

    def yes(message, *, confirm=False):
        notices.append({"confirm": confirm, "message": message})
        return True

    with again.no_message_box():
        need(again.guard_refuses(original), "The guard did not refuse the product's message box")
        windows_installation.owner_notice = yes
        try:
            return asyncio.run(windows_consumer.maintenance(root, "stop"))
        finally:
            windows_installation.owner_notice = original


def journey(stage, exchange, account, token):
    """Setup's last steps and the starts, with the installation's own Python."""
    steps = Steps(exchange / "journey.json", JOURNEY)
    try:
        prepared = json.loads((stage / "prepared.json").read_text(encoding="utf-8"))
        root = installation_folder()
        home, again = product(stage, root)
        from amplifier_web import app_component_graph as components
        from amplifier_web import windows_consumer as consumer
        from amplifier_web import windows_consumer_shell as shell, windows_installation, windows_service
        from amplifier_web import windows_uninstall
        from amplifier_web.deployment import write_private
        from amplifier_web.updates import process

        steps.begin(JOURNEY[0])
        artifact = {"url": f"{SOURCE}/releases/download/v{prepared['version']}/{prepared['wheel']}",
                    "path": str(root / prepared["wheel"]), "sha256": prepared["sha256"]}
        graph = asyncio.run(components.read_graph(process, sys.executable, artifact=artifact))
        write_private(root / consumer.RECEIPT, json.dumps({
            "schema": 1, "kind": "windows-consumer-preview",
            "version": prepared["version"], "revision": prepared["revision"],
            "artifact": artifact, "graph": graph,
            "uv": str(root / "uv/uv.exe"), "git": str(root / "git/cmd/git.exe")}), owned_root=root)
        os.environ.update(consumer.environment(root, consumer.installation(root)[1]))
        need(not windows_service._read()[0]["exists"], "A pre-existing task must not be changed")
        # Setup's two commands of its step "Checking the installation", then
        # setup's completion record, where setup writes it.
        audit = again.launcher(root, "--audit")
        need(audit["exitCode"] == 0 and (audit["json"] or {}).get("ok") is True, "Setup's audit failed", audit)
        packages = again.execute([root / "uv/uv.exe", "pip", "check", "--python", sys.executable], cwd=root)
        need(packages["exitCode"] == 0, "The package check failed", packages)
        write_private(root / windows_uninstall.COMPLETE,
                      json.dumps({"schema": 1, "version": prepared["version"]}), owned_root=root)
        need(windows_uninstall.completed(root), "Setup's completion record was not accepted")
        steps.passed(JOURNEY[0], audit=audit["json"])

        steps.begin(JOURNEY[1])
        entries = again.launcher(root, "--integrate")
        need(entries["exitCode"] == 0 and shell.listed(root), "The entries were not added", entries)
        folder = Path(json.loads((root / shell.FILE).read_text())["folder"])
        need({child.name for child in folder.iterdir()} == set(shell.LINKS)
             and Path(os.environ["APPDATA"]) in folder.parents, "The Start-menu folder is not the user's own", str(folder))
        steps.passed(JOURNEY[1], startMenuFolder=str(folder), listed=True)

        # One launcher command. Its three parts get one line each.
        for name in JOURNEY[2:5]:
            steps.begin(name)
        started = again.launcher(root, "--no-open", timeout=1500)
        state, status = again.task_facts(home), again.cli(root, "service", "status", "--json")
        steps.report["firstStart"] = {"launcher": started, "task": state, "serviceStatus": status}
        if started["exitCode"] or not again.running(state) or not again.healthy(status):
            log = home / "config/services/task-output.log"
            steps.report["firstStart"]["taskOutput"] = "\n".join(
                log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])[-6000:] if log.exists() else None
        steps.save()
        need(state.get("managed") is True, "The task was not registered. The launcher said", started["json"] or started)
        steps.passed(JOURNEY[2], task=state.get("taskPath"))
        need(started["exitCode"] == 0 and (started["json"] or {}).get("background") is True,
             "The launcher did not finish the start. It said", started["json"] or started)
        steps.passed(JOURNEY[3], seconds=started["seconds"])
        need(again.running(state) and again.healthy(status),
             "Task Scheduler did not start a host that answers", {"task": state, "status": status["json"]})
        port = windows_installation.saved_port(home)
        need(isinstance(port, int), "No port was saved")
        steps.passed(JOURNEY[4], task=state, port=port, host=again.process_identity(started["json"]["health"]))

        steps.begin(JOURNEY[5])
        shown = again.launcher(root, "--status")
        need(shown["exitCode"] == 0 and (shown["json"] or {}).get("ok") is True,
             "Status did not report a working app", shown["json"] or shown)
        steps.passed(JOURNEY[5], message=shown["json"].get("message"))

        steps.begin(JOURNEY[6])
        notices = []
        stopped = stop(again, root, notices)
        need(notices == [{"confirm": True, "message": "Stop Amplifier's background app?"}],
             "Stop asked something else than its one question", notices)
        state, status = again.task_facts(home), again.cli(root, "service", "status", "--json")
        need(stopped.get("ok") is True and state.get("managed") is True and state.get("instances") == 0
             and not (status["json"] or {}).get("responding", False)
             and not asyncio.run(again.listener_answers(home)),
             "The app still runs after Stop", {"result": stopped, "task": state, "status": status["json"]})
        steps.passed(JOURNEY[6], result=stopped, task=state)

        steps.begin(JOURNEY[7])
        again_started = again.launcher(root, "--no-open")
        state, status = again.task_facts(home), again.cli(root, "service", "status", "--json")
        accept_start(steps, "secondStart", again, home, root, again_started, state, status, "The second start failed")
        need(again.process_identity(status["json"]) != again.process_identity(started["json"]["health"])
             and windows_installation.saved_port(home) == port,
             "The second start did not give a new host on the same port")
        steps.report.update(hostBeforeSignOut=again.process_identity(status["json"]), port=port)
        steps.passed(JOURNEY[7], seconds=again_started["seconds"], port=port)

        steps.begin(CERTIFICATE)
        # In a program of its own, with a time limit: a Windows question that
        # nothing answers would wait without end.
        try:
            ran = again.execute([sys.executable, "-I", "-B", Path(__file__), "standard", "--stage", stage,
                                 "--exchange", exchange, "--phase", "certificate"], cwd=exchange, timeout=240)
        except subprocess.TimeoutExpired:
            ran = "The certificate step did not end in 240 seconds"
        shown = read_json(exchange / "certificate.json") or {}
        result = (shown.get("steps") or [{}])[0]
        need(shown.get("finished") is True and result.get("status") == "PASS",
             "The certificate step failed", {"reason": result.get("reason"), "program": ran,
                                             "windowsQuestions": shown.get("windowsQuestions")})
        steps.passed(CERTIFICATE, **{key: value for key, value in result.items() if key not in ("name", "status")})

        steps.begin(JOURNEY[9])
        # A second setup run reads exactly this: it would open this installation.
        reads = setup_reads(stage, exchange)
        need(reads.get("folder") == "installed" and reads.get("signInTask") == "present"
             and reads.get("taskProgramExists") is True,
             "Setup's own code does not read this installation and its sign-in task", reads)
        steps.passed(JOURNEY[9], account=reads)
    except BaseException as error:
        steps.failed(error)
    return steps.finish()


def trust_targets(home):
    from amplifier_web import browser_entry
    from amplifier_web.deployment import load_server_config
    urls, pin = browser_entry._owner_probe_targets(home, load_server_config(home))
    need(pin is not None and urls, "The installation's private HTTPS identity is unavailable")
    return urls, pin


def certificate(stage, exchange, account, token):
    """The launcher's own certificate step, as at a later open. Windows asks; the test selects Yes."""
    steps = Steps(exchange / "certificate.json", (CERTIFICATE,))
    try:
        root = installation_folder()
        home, again = product(stage, root)
        from amplifier_web import windows_consumer as consumer, windows_installation, windows_trust
        os.environ.update(consumer.environment(root, consumer.installation(root)[1]))
        steps.begin(CERTIFICATE)
        urls, pin = trust_targets(home)
        before = windows_installation.trust_state(urls, pin)
        need(before == "untrusted", "Before the question Windows must not trust the connection yet. It read", before)
        need(windows_trust.user_roots_disabled() is False, "This computer's policy disables a person's own certificates")
        marks = certificate_marks(home)
        need(windows_root_has(marks[1]) is False,
             "Before the question this user's root certificates already hold this certificate")
        notices, asked = [], []
        original = windows_installation.owner_notice

        def note(message, *, confirm=False):
            notices.append({"confirm": confirm, "message": message})
            return True

        with again.no_message_box(), answering(steps, asked, "install", "yes", marks):
            windows_installation.owner_notice = note
            try:
                accepted = windows_installation.review_browser_trust(root, urls, pin)
            finally:
                windows_installation.owner_notice = original
        # Amplifier's one message before the question, with the thumbprint that Windows then shows.
        need(len(notices) == 1 and notices[0]["confirm"] is False
             and notices[0]["message"].startswith("Windows will now ask one question:")
             and marks[1] in notices[0]["message"], "Amplifier did not show its one message before the question", notices)
        need(one_question(asked, "yes"), "Windows did not ask its one question about this certificate", asked)
        # Amplifier tells the person which thumbprint the Windows message shows.
        need(marks[1] in asked[0]["text"],
             "The Windows question does not show the thumbprint as Amplifier's message shows it", asked)
        after = windows_installation.trust_state(urls, pin)
        need(accepted is True and after == "trusted",
             "After Yes, Windows does not trust the connection", {"accepted": accepted, "state": after})
        need(windows_root_has(marks[1]) is True,
             "After Yes, Windows does not list the certificate among this user's root certificates")
        steps.passed(CERTIFICATE, amplifierMessage=notices[0]["message"], windowsQuestion=asked[0],
                     before=before, after=after, windowsListsTheCertificate=True)
    except BaseException as error:
        steps.failed(error)
    return steps.finish()


def second(stage, exchange, account, token):
    """After the sign-out and the new sign-in. No launcher command runs before the first check."""
    steps = Steps(exchange / "second.json", SECOND, token=token)
    try:
        before = json.loads((exchange / "journey.json").read_text(encoding="utf-8"))
        root = installation_folder()
        home, again = product(stage, root)
        from amplifier_web import windows_consumer as consumer
        from amplifier_web import windows_consumer_shell as shell, windows_installation, windows_uninstall
        os.environ.update(consumer.environment(root, consumer.installation(root)[1]))

        steps.begin(SECOND[0])
        need(standard_token(token, account), "This is not a standard user in a desktop session of its own sign-in", token)
        status = again.cli(root, "service", "status", "--wait", "120", "--json", timeout=180)
        state = again.task_facts(home)
        need(again.running(state) and again.healthy(status),
             "After the sign-in the task did not start a host that answers", {"task": state, "status": status["json"]})
        # The sign-out ended the earlier host. This one is new, and only the sign-in started it.
        need(list(again.process_identity(status["json"])) != list(before["hostBeforeSignOut"])
             and windows_installation.saved_port(home) == before["port"],
             "The host after the sign-in is not a new host on the same port")
        urls, pin = trust_targets(home)
        marks = certificate_marks(home)
        trusted = windows_installation.trust_state(urls, pin)
        need(trusted == "trusted", "After the new sign-in Windows does not trust the connection. It read", trusted)
        need(windows_root_has(marks[1]) is True,
             "After the new sign-in Windows does not list the certificate among this user's root certificates")
        steps.report["thumbprint"] = marks[1]  # For the check after the delete choice.
        steps.passed(SECOND[0], task=state, host=again.process_identity(status["json"]), port=before["port"],
                     trust=trusted)

        steps.begin(SECOND[1])
        notices, asked = [], []
        with answering(steps, asked, "delete", "no", marks):
            removed = again.uninstall(root, notices)
        need(again.kept_folder_question(notices), "Uninstall did not ask its one question", notices)
        need("Windows asks whether to delete the certificate" in notices[0]["message"],
             "The uninstall question did not say that Windows asks about the certificate", notices)
        need(one_question(asked, "no") and removed.get("certificate") == "declined",
             "Windows did not ask once about the certificate, or No did not keep it",
             {"asked": asked, "certificate": removed.get("certificate")})
        state, status = again.task_facts(home), again.cli(root, "service", "status", "--json")
        need(removed.get("ok") is True and not state.get("managed")
             and not (status["json"] or {}).get("responding", False) and not shell.listed(root)
             and not asyncio.run(again.listener_answers(home)),
             "The uninstall left a part in place", {"result": removed, "task": state, "status": status["json"]})
        need(root.is_dir() and windows_uninstall.completed(root) and not windows_uninstall.unfinished(root),
             "The choice that keeps the folder did not keep the installation as it was")
        need(windows_root_has(marks[1]) is True,
             "After No, Windows does not list the certificate among this user's root certificates")
        reads = setup_reads(stage, exchange)
        need(reads.get("folder") == "installed" and reads.get("signInTask") == "absent",
             "After the uninstall setup's own code does not read a kept installation without a sign-in task", reads)
        steps.passed(SECOND[1], result=removed, notices=notices, folderKept=True, account=reads, windowsQuestion=asked[0])

        steps.begin(SECOND[2])
        # As setup's second run: the entries come back, then the start.
        entries = again.launcher(root, "--integrate")
        need(entries["exitCode"] == 0 and shell.listed(root), "The entries were not added again", entries)
        started = again.launcher(root, "--no-open")
        state, status = again.task_facts(home), again.cli(root, "service", "status", "--json")
        accept_start(steps, "startAfterUninstall", again, home, root, started, state, status,
                     "The start after the uninstall failed")
        need(windows_installation.saved_port(home) == before["port"], "The start after the uninstall changed the port")
        # The certificate stayed (No). The connection needs no new question.
        trusted = windows_installation.trust_state(urls, pin)
        need(trusted == "trusted", "After the start Windows does not trust the connection. It read", trusted)
        steps.passed(SECOND[2], seconds=started["seconds"], task=state, port=before["port"], trust=trusted)

        steps.begin(SECOND[3])
        notices, asked = [], []
        with answering(steps, asked, "delete", "yes", marks):
            removed = delete_everything(again, root, notices)
        # From here this program starts nothing from the installation: its files are gone.
        need(delete_questions(notices, root), "Uninstall did not ask its two questions of the delete choice", notices)
        need(removed.get("ok") is True and removed.get("userDataRetained") is False and removed.get("kept") == []
             and removed.get("afterExit") is True and not removed.get("programLeft"),
             "The delete choice did not finish", removed)
        need(one_question(asked, "yes") and removed.get("certificate") == "removed",
             "Windows did not ask once about the certificate, or Yes did not remove it",
             {"asked": asked, "certificate": removed.get("certificate")})
        steps.passed(SECOND[3], result=removed, notices=notices, windowsQuestion=asked[0])
    except BaseException as error:
        steps.failed(error)
    return steps.finish()


def delete_everything(again, root, notices):
    """The product's uninstall with the choice that deletes the folder: Yes, and Yes again.

    It deletes %LOCALAPPDATA%\\Amplifier of the account that runs it. `standard`
    lets only the user of this run reach it.
    """
    from amplifier_web import windows_consumer, windows_installation, windows_uninstall
    message, question = windows_installation.owner_notice, windows_uninstall.question

    def note(text, *, confirm=False):
        notices.append({"kind": "message", "confirm": confirm, "message": text})
        return True

    def yes(text, *, cancel=False):
        notices.append({"kind": "question", "cancel": cancel, "answer": "yes", "message": text})
        return "yes"

    with again.no_message_box():
        windows_installation.owner_notice, windows_uninstall.question = note, yes
        try:
            return asyncio.run(windows_consumer.maintenance(root, "uninstall"))
        finally:
            windows_installation.owner_notice, windows_uninstall.question = message, question


def delete_questions(notices, root):
    """Exactly the two questions of the delete choice, in their order, and no message.

    The first has three buttons and says what Yes does. The second has two
    buttons, names the folder and says that this cannot be undone. Another
    question, a message, or one question more is another case: it fails here.
    """
    return (len(notices) == 2 and all(notice["kind"] == "question" and notice["answer"] == "yes" for notice in notices)
            and notices[0]["cancel"] is True and notices[0]["message"].startswith("Uninstall Amplifier?")
            and "Select Yes to delete them, together with the program files." in notices[0]["message"]
            and notices[1]["cancel"] is False
            and notices[1]["message"].startswith("Delete everything that Amplifier keeps in this folder?")
            and str(root) in notices[1]["message"] and "This cannot be undone." in notices[1]["message"])


def third(stage, exchange, account, token):
    """After the delete choice, with the runner's Python: nothing of the installation is left."""
    steps = Steps(exchange / "third.json", THIRD, token=token)
    try:
        import winreg
        root = installation_folder()
        steps.begin(THIRD[0])
        need(standard_token(token, account), "This is not a standard user in a desktop session of its own sign-in", token)
        # The last program files go after the uninstall program has ended.
        deadline = time.monotonic() + 240
        while root.exists() and time.monotonic() < deadline:
            time.sleep(3)
        left = sorted(child.name for child in root.iterdir())[:40] if root.exists() else None
        need(left is None, "The installation folder still exists. It holds", left)
        reads = setup_reads(stage, exchange)
        # What setup reads on a PC that never had Amplifier: it would install.
        need(reads.get("folder") == "absent" and reads.get("signInTask") == "absent",
             "Setup's own code does not read an account without an installation", reads)
        programs = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
        menu = sorted(child.name for child in programs.iterdir() if child.name.startswith("Amplifier"))
        need(menu == [], "A Start-menu entry is left", menu)
        listed = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall") as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    name = winreg.EnumKey(key, index)
                    if name.startswith("AmplifierUnified"):
                        listed.append(name)
        except FileNotFoundError:
            pass  # This account has no Apps-list entry of its own at all.
        need(listed == [], "An Apps-list entry is left", listed)
        before = read_json(exchange / "second.json") or {}
        need(before.get("thumbprint"), "The phase before did not record the certificate's thumbprint")
        need(windows_root_has(before["thumbprint"]) is False,
             "After Yes, Windows still lists the certificate among this user's root certificates")
        steps.passed(THIRD[0], account=reads, folderExists=False, windowsListsTheCertificate=False)
    except BaseException as error:
        steps.failed(error)
    return steps.finish()


def standard(stage, exchange, phase):
    stage, exchange = Path(stage), Path(exchange)
    # In the stage folder, which this user cannot change. Not in the exchange folder.
    record = stage / "account.json"
    # The record's text, its owner and its permissions come from one open file.
    content, owner, changeable = read_protected(record)
    account = json.loads(content.decode("utf-8"))
    token = token_facts()
    admit_standard(account, token, owner, changeable or read_protected(record.parent, folder=True)[2])
    # One run for each phase: a second start of the helper task changes nothing.
    try:
        with open(exchange / f"{phase}.started", "x", encoding="utf-8") as marker:
            marker.write(str(os.getpid()))
    except FileExistsError:
        raise SystemExit("Refused: this phase already ran.") from None
    return {"first": first, "journey": journey, "certificate": certificate, "second": second, "third": third}[phase](
        stage, exchange, account, token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "run", "cleanup", "standard"))
    parser.add_argument("--stage")
    parser.add_argument("--exchange")
    parser.add_argument("--phase", choices=("first", "journey", "certificate", "second", "third"))
    args = parser.parse_args()
    if args.operation == "standard":
        raise SystemExit(0 if standard(args.stage, args.exchange, args.phase) else 1)
    require_disposable_runner()
    raise SystemExit(0 if globals()[args.operation]() else 1)
