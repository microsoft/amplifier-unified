"""PROBE (temporary): facts of a GitHub-hosted Windows runner for a standard account.

Creates one local user that is not a member of Administrators, logs it on with
its name and password (CreateProcessWithLogonW, the Secondary Logon service,
with its profile), and records what that logon can do. The password is made in
this process, is never written, printed or put on a command line, and the user
is removed again.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import secrets
import string
import subprocess
import sys
import time
import uuid

OPT_IN = "AMPLIFIER_CREATE_A_STANDARD_USER_ON_THIS_RUNNER"
USERS, ADMINISTRATORS = "S-1-5-32-545", "S-1-5-32-544"


def require_disposable_runner():
    """Refuse unless this is the hosted workflow, with its opt-in.

    A guard against accidental use. It reads the environment that GitHub sets
    on a hosted runner; environment variables do not prove the machine. Never
    set them by hand on a machine that is kept.
    """
    if not (sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted" and os.environ.get(OPT_IN) == "1"):
        raise SystemExit("Refused: this check creates a local Windows user. It runs only on a "
                         f"GitHub-hosted runner, with {OPT_IN}=1.")


def evidence():
    return Path(os.environ["RUNNER_TEMP"]).resolve() / "standard-account-evidence"


def save(name, value):
    evidence().mkdir(parents=True, exist_ok=True)
    (evidence() / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run(argv, *, timeout=60, stdin=None):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=timeout,
                            input=stdin, encoding="utf-8", errors="replace")
    return {"exitCode": result.returncode, "stdout": result.stdout[-6000:], "stderr": result.stderr[-2000:]}


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
    """The SID bytes of a local account name."""
    size, domain_size, use = W.DWORD(0), W.DWORD(0), W.DWORD()
    _adv.LookupAccountNameW.argtypes = [W.LPCWSTR, W.LPCWSTR, C.c_void_p, C.POINTER(W.DWORD),
                                        W.LPWSTR, C.POINTER(W.DWORD), C.POINTER(W.DWORD)]
    _adv.LookupAccountNameW(None, name, None, C.byref(size), None, C.byref(domain_size), C.byref(use))
    sid, domain = C.create_string_buffer(size.value), C.create_unicode_buffer(domain_size.value)
    if not _adv.LookupAccountNameW(None, name, sid, C.byref(size), domain, C.byref(domain_size), C.byref(use)):
        raise _failed()
    return sid


def sid_name(text):
    """The local name of a well-known SID (the group's name differs by language)."""
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


def create_user(name, password):
    """One local user, member of Users only: a standard account."""
    net = C.WinDLL("netapi32")
    net.NetUserAdd.argtypes = [W.LPCWSTR, W.DWORD, C.c_void_p, C.POINTER(W.DWORD)]
    net.NetUserAdd.restype = W.DWORD
    # USER_PRIV_USER; UF_SCRIPT | UF_DONT_EXPIRE_PASSWD.
    info = _UserInfo1(name, password, 0, 1, None, "Amplifier standard-account check (one run)", 0x1 | 0x10000, None)
    field = W.DWORD()
    status = net.NetUserAdd(None, 1, C.byref(info), C.byref(field))
    if status:
        raise OSError(f"NetUserAdd failed with status {status} (field {field.value})")
    sid = account_sid(name)
    net.NetLocalGroupAddMembers.argtypes = [W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_void_p, W.DWORD]
    net.NetLocalGroupAddMembers.restype = W.DWORD
    member = _Member(C.cast(sid, C.c_void_p))
    status = net.NetLocalGroupAddMembers(None, sid_name(USERS), 0, C.byref(member), 1)
    if status not in (0, 1378):  # Already a member.
        raise OSError(f"NetLocalGroupAddMembers failed with status {status}")
    return sid_text(sid)


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


class _Startup(C.Structure):
    _fields_ = [("size", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR), ("title", W.LPWSTR),
                ("x", W.DWORD), ("y", W.DWORD), ("width", W.DWORD), ("height", W.DWORD),
                ("columns", W.DWORD), ("rows", W.DWORD), ("fill", W.DWORD), ("flags", W.DWORD),
                ("show", W.WORD), ("reserved2", W.WORD), ("reserved3", C.c_void_p),
                ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]


class _Process(C.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]


def run_as(name, password, argv, *, cwd, timeout):
    """Log the user on with name and password and run one program as that user.

    CreateProcessWithLogonW: the Secondary Logon service makes a new logon of
    the user (as "Run as different user" does), loads the user's profile and
    gives the program the user's own environment. Returns the exit code.
    """
    command = C.create_unicode_buffer(subprocess.list2cmdline(list(map(str, argv))))
    startup, made = _Startup(), _Process()
    startup.size, startup.flags, startup.show = C.sizeof(startup), 0x1, 0  # A hidden console window.
    _adv.CreateProcessWithLogonW.argtypes = [
        W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.DWORD, W.LPCWSTR, W.LPWSTR, W.DWORD, C.c_void_p,
        W.LPCWSTR, C.POINTER(_Startup), C.POINTER(_Process)]
    _adv.CreateProcessWithLogonW.restype = W.BOOL
    # LOGON_WITH_PROFILE; CREATE_UNICODE_ENVIRONMENT. A null environment is the user's own.
    if not _adv.CreateProcessWithLogonW(name, ".", password, 0x1, None, command, 0x400, None,
                                        str(cwd), C.byref(startup), C.byref(made)):
        raise _failed()
    _kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    _kernel.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
    _kernel.TerminateProcess.argtypes = [W.HANDLE, C.c_uint]
    _kernel.CloseHandle.argtypes = [W.HANDLE]
    try:
        if _kernel.WaitForSingleObject(made.process, int(timeout * 1000)) != 0:
            _kernel.TerminateProcess(made.process, 1)
            raise TimeoutError("The program of the standard user did not end in time.")
        code = W.DWORD()
        if not _kernel.GetExitCodeProcess(made.process, C.byref(code)):
            raise _failed()
        return code.value
    finally:
        _kernel.CloseHandle(made.thread)
        _kernel.CloseHandle(made.process)


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
    """What Windows says about this process's own token and logon."""
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
        elevation_type = W.DWORD.from_buffer(kind).value
        elevated = W.DWORD.from_buffer(raised).value
        level = C.c_void_p.from_buffer(label).value  # TokenIntegrityLevel
        _adv.GetSidSubAuthorityCount.argtypes, _adv.GetSidSubAuthorityCount.restype = [C.c_void_p], C.POINTER(C.c_ubyte)
        _adv.GetSidSubAuthority.argtypes, _adv.GetSidSubAuthority.restype = [C.c_void_p, W.DWORD], C.POINTER(W.DWORD)
        integrity = _adv.GetSidSubAuthority(level, _adv.GetSidSubAuthorityCount(level).contents.value - 1).contents.value
        statistics = _Statistics.from_buffer(numbers)  # TokenStatistics
        logon = _Luid(statistics.authentication.low, statistics.authentication.high)
    finally:
        _kernel.CloseHandle.argtypes = [W.HANDLE]
        _kernel.CloseHandle(token)
    administrators, member = C.c_void_p(), W.BOOL()
    _adv.ConvertStringSidToSidW.argtypes = [W.LPCWSTR, C.POINTER(C.c_void_p)]
    _adv.CheckTokenMembership.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(W.BOOL)]
    if (not _adv.ConvertStringSidToSidW(ADMINISTRATORS, C.byref(administrators))
            or not _adv.CheckTokenMembership(None, administrators, C.byref(member))):
        raise _failed()
    facts = {"sid": user, "memberOfAdministrators": bool(member.value),
             # 1: the account has one token and no administrator token beside it.
             "elevationType": elevation_type, "elevated": bool(elevated), "integrityLevel": hex(integrity)}
    secur = C.WinDLL("secur32")
    data = C.POINTER(_LogonSession)()
    secur.LsaGetLogonSessionData.argtypes = [C.POINTER(_Luid), C.POINTER(C.POINTER(_LogonSession))]
    status = secur.LsaGetLogonSessionData(C.byref(logon), C.byref(data))
    if status == 0:
        # 2 Interactive, 3 Network, 4 Batch, 5 Service, 10 RemoteInteractive, 11 CachedInteractive.
        facts.update(logonType=data.contents.type, logonSession=data.contents.session,
                     logonPackage=data.contents.package.buffer)
        secur.LsaFreeReturnBuffer.argtypes = [C.c_void_p]
        secur.LsaFreeReturnBuffer(data)
    else:
        facts["logonTypeStatus"] = hex(status & 0xFFFFFFFF)
    session = W.DWORD()
    _kernel.ProcessIdToSessionId.argtypes = [W.DWORD, C.POINTER(W.DWORD)]
    _kernel.ProcessIdToSessionId(os.getpid(), C.byref(session))
    user32 = C.WinDLL("user32", use_last_error=True)
    user32.GetProcessWindowStation.restype = W.HANDLE
    user32.GetUserObjectInformationW.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    name, needed = C.create_unicode_buffer(256), W.DWORD()
    station = user32.GetProcessWindowStation()
    facts.update(desktopSession=session.value,
                 windowStation=name.value if station and user32.GetUserObjectInformationW(
                     station, 2, name, C.sizeof(name), C.byref(needed)) else None)
    return facts


TASK = r"""
$ErrorActionPreference = 'Stop'
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$s = New-Object -ComObject 'Schedule.Service'; $s.Connect(); $f = $s.GetFolder('\')
$d = $s.NewTask(0)
$d.Principal.UserId = $p.sid; $d.Principal.LogonType = 3; $d.Principal.RunLevel = 0
$d.Settings.DisallowStartIfOnBatteries = $false; $d.Settings.StopIfGoingOnBatteries = $false
$a = $d.Actions.Create(0); $a.Path = $env:ComSpec; $a.Arguments = '/d /c echo ran> "' + $p.marker + '"'
$out = @{}
try { $t = $f.RegisterTaskDefinition($p.name, $d, 2, $p.sid, $null, 3, $null); $out.registered = $true }
catch { $out.registered = $false; $out.registerHresult = $_.Exception.HResult }
if ($out.registered) {
  try { $null = $t.Run($null); $out.run = $true } catch { $out.run = $false; $out.runHresult = $_.Exception.HResult }
  Start-Sleep -Seconds 8
  $t = $f.GetTask($p.name)
  $out.state = [int]$t.State; $out.lastResult = [long]$t.LastTaskResult
  $out.marker = Test-Path $p.marker
  $f.DeleteTask($p.name, 0)
}
$out | ConvertTo-Json -Compress
"""


def probe_child(exchange):
    """Runs as the standard user."""
    exchange = Path(exchange)
    report = {"stage": "token"}
    try:
        report["token"] = token_facts()
        local = Path(os.environ["LOCALAPPDATA"])
        report.update(stage="profile", localAppData=str(local), userProfile=os.environ.get("USERPROFILE"),
                      temp=os.environ.get("TEMP"), python=sys.executable, cwd=os.getcwd(),
                      githubActionsSet="GITHUB_ACTIONS" in os.environ)
        (local / "standard-account-probe.txt").write_text("own profile is writable\n")
        report["profileWritable"] = True
        report["whoamiGroups"] = run(["whoami", "/groups", "/fo", "csv", "/nh"])
        report["stage"] = "task"
        marker = local / "standard-account-task-ran.txt"
        import base64
        report["task"] = run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                              base64.b64encode(TASK.encode("utf-16-le")).decode("ascii")],
                             stdin=json.dumps({"sid": report["token"]["sid"], "marker": str(marker),
                                               "name": "AmplifierStandardAccountProbe-" + uuid.uuid4().hex[:8]}))
        report["stage"] = "complete"
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error)[-1000:])
    (exchange / "child.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report["stage"] == "complete"


def probe():
    """Runs as the runner's account."""
    report = {"stage": "runner"}
    name = "amp-std-" + uuid.uuid4().hex[:6]
    try:
        report["runner"] = {"token": token_facts(), "python": sys.executable,
                            "sessions": run(["qwinsta.exe"]),
                            "os": run(["powershell.exe", "-NoProfile", "-Command",
                                       "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,"
                                       "BuildNumber,ProductType | ConvertTo-Json -Compress"]),
                            "remoteDesktop": run(["powershell.exe", "-NoProfile", "-Command",
                                "@{deny=(Get-ItemProperty 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server')"
                                ".fDenyTSConnections; service=(Get-Service TermService).Status.ToString(); "
                                "nla=(Get-ItemProperty 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server\\"
                                "WinStations\\RDP-Tcp').UserAuthentication; listening=[bool](Get-NetTCPConnection "
                                "-LocalPort 3389 -State Listen -ErrorAction SilentlyContinue); "
                                "mstsc=(Test-Path $env:SystemRoot\\System32\\mstsc.exe)} | ConvertTo-Json -Compress"])}
        save("probe.json", report)
        exchange = evidence() / "exchange"
        exchange.mkdir(parents=True, exist_ok=True)
        script = exchange / Path(__file__).name
        script.write_bytes(Path(__file__).read_bytes())
        report["stage"] = "user"
        password = new_password()
        sid = create_user(name, password)
        save("ownership.json", {"user": name, "sid": sid})
        report["user"] = {"name": name, "sid": sid, "localGroups": local_groups(name)}
        grant = run(["icacls.exe", exchange, "/grant", f"*{sid}:(OI)(CI)M"])
        report["grant"] = grant["exitCode"]
        report["stage"] = "logon"
        report["childExitCode"] = run_as(name, password, [sys.executable, "-I", "-B", script, "probe-child",
                                                           "--exchange", exchange], cwd=exchange, timeout=180)
        report["sessionsDuring"] = run(["qwinsta.exe"])
        child = exchange / "child.json"
        report["child"] = json.loads(child.read_text(encoding="utf-8")) if child.exists() else None
        report["stage"] = "complete"
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error)[-1000:])
    finally:
        report["userRemoved"] = delete_user(name)
    save("probe.json", report)
    print(json.dumps(report, indent=2), flush=True)
    return report["stage"] == "complete"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("probe", "probe-child"))
    parser.add_argument("--exchange")
    args = parser.parse_args()
    if args.operation == "probe-child":
        raise SystemExit(0 if probe_child(args.exchange) else 1)
    require_disposable_runner()
    raise SystemExit(0 if probe() else 1)
