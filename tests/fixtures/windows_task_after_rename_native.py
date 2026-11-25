"""Native check: the app uses its own sign-in task after the computer's name changes.

Run by .github/workflows/windows-task-after-rename.yml with the Python of an
installed candidate. The real CLI registers the real task for the runner's
account, and Task Scheduler starts the real app, on a loopback port in a
throwaway folder. No browser, account, provider or trust change is used.

A computer rename takes effect at a restart, and a hosted job has none. The
check makes the one state that the rename leaves for an account name: it
changes the NAME of the computer's account domain in the local security policy
(the SID stays) and puts it back. In that state Windows maps the name that Task
Scheduler stored, `OLDNAME\\user`, to nothing, as after a real rename. What stays
different from a real rename: the computer's own name does not change, and
Windows still gives the old name for a SID.

THIS CHANGES THE SECURITY POLICY OF THE MACHINE THAT RUNS IT. A stop between
the change and the restore leaves every task of that machine's local accounts
with a name that maps to nothing. It is therefore for the hosted workflow
only (a machine that is deleted after the job): it refuses to run unless the
environment is that of a GitHub-hosted runner and the workflow opted in. It
saves the original name before the change and restores it in every case, and
`cleanup` restores it once more from the saved record. A job that is ended
by force between the change and the restore leaves the changed name; the
runner's deletion is then the last containment.

This is not clean-room acceptance: no setup file, no restart, no sign-in.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OPT_IN = "AMPLIFIER_RENAME_THIS_RUNNERS_ACCOUNT_DOMAIN"


def require_disposable_runner():
    """Refuse before any import or effect unless this is the hosted workflow, with its opt-in.

    A guard against accidental use. It reads the environment that GitHub sets
    on a hosted runner; environment variables do not prove the machine. Never
    set them by hand on a machine that is kept.
    """
    if not (sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted" and os.environ.get(OPT_IN) == "1"):
        raise SystemExit("Refused: this check changes the machine's account-domain name. It runs only on a "
                         f"GitHub-hosted runner, with {OPT_IN}=1.")
    if sys.version_info[:2] != (3, 13) or sys.maxsize <= 2 ** 32:
        raise SystemExit("Refused: this check needs 64-bit CPython 3.13.")


def evidence():
    return Path(os.environ["RUNNER_TEMP"]).resolve() / "task-after-rename-evidence"


def save(name, value):
    evidence().mkdir(parents=True, exist_ok=True)
    (evidence() / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def saved(name):
    try:
        return json.loads((evidence() / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def canonical_temp():
    # The maintained fixture owns the temporary-folder check.
    spec = importlib.util.spec_from_file_location(
        "task_after_rename_helpers", ROOT / "tests/fixtures/manual_app_handoff_native.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.canonical_temp()


# ---- The account-domain name in the local security policy (LSA).
class _Text(C.Structure):
    _fields_ = [("Length", W.USHORT), ("MaximumLength", W.USHORT), ("Buffer", C.c_void_p)]


class _Attributes(C.Structure):
    _fields_ = [("Length", W.ULONG), ("RootDirectory", W.HANDLE), ("ObjectName", C.c_void_p),
                ("Attributes", W.ULONG), ("SecurityDescriptor", C.c_void_p), ("Quality", C.c_void_p)]


class _AccountDomain(C.Structure):
    _fields_ = [("DomainName", _Text), ("DomainSid", C.c_void_p)]


def account_domain(new_name=None):
    """Read the account-domain name; with new_name, set it. The domain SID is never changed."""
    api = C.WinDLL("advapi32", use_last_error=True)
    api.LsaOpenPolicy.argtypes = [C.c_void_p, C.POINTER(_Attributes), W.ULONG, C.POINTER(W.HANDLE)]
    api.LsaQueryInformationPolicy.argtypes = [W.HANDLE, C.c_int, C.POINTER(C.c_void_p)]
    api.LsaSetInformationPolicy.argtypes = [W.HANDLE, C.c_int, C.c_void_p]
    api.LsaFreeMemory.argtypes = [C.c_void_p]
    api.LsaClose.argtypes = [W.HANDLE]
    for name in ("LsaOpenPolicy", "LsaQueryInformationPolicy", "LsaSetInformationPolicy"):
        getattr(api, name).restype = C.c_long
    handle, attributes = W.HANDLE(), _Attributes()
    attributes.Length = C.sizeof(attributes)
    # POLICY_VIEW_LOCAL_INFORMATION, and POLICY_TRUST_ADMIN for the change.
    status = api.LsaOpenPolicy(None, C.byref(attributes), 0x1 | (0x8 if new_name else 0), C.byref(handle))
    if status:
        raise OSError(f"LsaOpenPolicy {status & 0xFFFFFFFF:#x}")
    try:
        buffer = C.c_void_p()
        status = api.LsaQueryInformationPolicy(handle, 5, C.byref(buffer))  # PolicyAccountDomainInformation
        if status:
            raise OSError(f"LsaQueryInformationPolicy {status & 0xFFFFFFFF:#x}")
        try:
            info = C.cast(buffer, C.POINTER(_AccountDomain)).contents
            current = C.wstring_at(info.DomainName.Buffer, info.DomainName.Length // 2)
            if new_name is not None:
                text = C.create_unicode_buffer(new_name)
                changed = _AccountDomain()
                changed.DomainName.Length = len(new_name) * 2
                changed.DomainName.MaximumLength = len(new_name) * 2 + 2
                changed.DomainName.Buffer = C.cast(text, C.c_void_p)
                changed.DomainSid = info.DomainSid
                status = api.LsaSetInformationPolicy(handle, 5, C.byref(changed))
                if status:
                    raise OSError(f"LsaSetInformationPolicy {status & 0xFFFFFFFF:#x}")
            return current
        finally:
            api.LsaFreeMemory(buffer)
    finally:
        api.LsaClose(handle)


def restore_account_domain():
    """Put the saved account-domain name back and prove it. True when nothing is left to restore."""
    record = saved("account-domain.json")
    if record is None:
        return True
    from amplifier_web import windows_service as service
    if account_domain() != record["original"]:
        account_domain(record["original"])
    deadline = time.monotonic() + 15
    while True:
        restored = (account_domain() == record["original"]
                    and service._lookup(record["qualified"]) == (record["sid"], 1))
        if restored or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    save("account-domain-restored.json", {"restored": restored, "name": account_domain()})
    if restored:
        (evidence() / "account-domain.json").unlink()
    return restored


def powershell(script):
    from amplifier_web import windows_service as service
    result = subprocess.run(
        [service._powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(("$ProgressPreference='SilentlyContinue'; $ErrorActionPreference='Stop'; "
                           + script).encode("utf-16-le")).decode("ascii")],
        capture_output=True, timeout=120, check=False)
    return result.returncode, result.stdout.decode("utf-8", "replace").strip()


def run():
    report = {"status": "FAIL", "stage": "start", "cleanRoomAcceptance": False, "checks": []}

    def need(condition, label):
        report["stage"] = label
        if not condition:  # Not `assert`: a check must not vanish under python -O.
            raise AssertionError(label)
        report["checks"].append(label)

    try:
        # A record of an earlier run means that its restore did not finish.
        need(restore_account_domain(), "no earlier account-domain change is left")
        from amplifier_web import windows_files, windows_ports, windows_service as service
        need(Path(service.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
             "the code under test is the installed candidate, not the checkout")
        sid = service._owner()
        need(service._scheduler("query", sid)["exists"] is False, "this account has no Amplifier task yet")
        root = canonical_temp() / ("task-after-rename-" + uuid.uuid4().hex[:12])
        home = root / "app"
        save("ownership.json", {"root": str(root), "sid": sid})
        for path in (root, home, root / "workspace", root / "shared", root / "ownership", root / "capture",
                     root / "import", root / "temp"):
            with windows_files.directory(path, owned_root=root, create=True):
                pass
        scripts = Path(sys.executable).parent
        # The account's own environment, as for an installed app, without the
        # variables that select another Python, package source or Amplifier folder.
        # Not a reduced environment: without PSModulePath, Windows PowerShell on a
        # hosted runner needs 12 to 17 seconds for each Task Scheduler call
        # (measured), and the app allows 20.
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("UV_", "PIP_", "AMPLIFIER_", "GITHUB_", "RUNNER_", "ACTIONS_"))
               and key.upper() not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}}
        git = shutil.which("git")
        env.update({
            "PATH": os.pathsep.join([str(scripts), str(Path(sys._base_executable).parent),
                                     *([str(Path(git).parent)] if git else []),
                                     r"C:\Windows\System32", r"C:\Windows"]),
            "AMPLIFIER_WEB_HOME": str(home), "AMPLIFIER_HOME": str(root / "shared"),
            "AMPLIFIER_SESSION_STATE_HOME": str(root / "ownership"),
            "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(root / "capture"),
            "AMPLIFIER_UNIFIED_IMPORT_HOME": str(root / "import"),
            "TEMP": str(root / "temp"), "TMP": str(root / "temp"),
            "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "GIT_TERMINAL_PROMPT": "0"})
        scope = [str(scripts / "amplifier-unified.exe"), "--data-dir", str(home),
                 "--workspace", str(root / "workspace")]

        def cli(label, *args, expect=0, timeout=150):
            began = time.monotonic()
            result = subprocess.run([*scope, *args], cwd=root / "workspace", env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=timeout)
            output = result.stdout.decode("utf-8", "replace")
            report.setdefault("seconds", {})[label] = round(time.monotonic() - began, 1)
            if (result.returncode == 0) != (expect == 0):
                # A throwaway folder on a throwaway runner: show why the command ended.
                print(f"[{label}] exit {result.returncode}\n{output[-3000:]}\n"
                      f"{result.stderr.decode('utf-8', 'replace')[-3000:]}", flush=True)
                if args[:1] == ("service",):
                    logs = subprocess.run([*scope, "service", "logs"], cwd=root / "workspace", env=env,
                                          stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
                    print("[task output]\n" + logs.stdout.decode("utf-8", "replace")[-4000:], flush=True)
            need((result.returncode == 0) == (expect == 0), label)
            return output if expect == 0 else result.stderr.decode("utf-8", "replace")

        def ready(label):
            lines = cli(label, "service", "status", "--wait", "90", "--json").splitlines()
            health = json.loads(next(line for line in reversed(lines) if line.startswith("{")))
            need(health.get("ok") is True and health.get("processVerified") is True
                 and health.get("responding") is True, label + ": the app answers as this installation")
            return health

        def stopped(label):
            cli(label, "service", "stop")
            need(service._scheduler("query", sid)["instances"] == 0, label + ": no task instance is left")

        def stored(xml):
            return ET.fromstring(xml).findtext(
                f"{service._q('Triggers')}/{service._q('LogonTrigger')}/{service._q('UserId')}")

        def variant(spec, change):
            tree = ET.fromstring(service.task_xml(spec))
            change(lambda path: tree.find("/".join(service._q(part) for part in path.split("/"))))
            return ET.tostring(tree, encoding="unicode")

        def refused(label, task):
            """The app does not accept `task`, does not start it and does not change it."""
            try:
                service._read(home)
                need(False, label + ": the app's check refuses this task")
            except service.TaskNotConfirmed as error:
                need(error.retry is False and error.entry == service.task_name(sid),
                     label + ": the app's check refuses this task")
            for operation in ("start", "stop", "uninstall"):
                said = cli(f"{label}: service {operation} ends with an error", "service", operation, expect=1)
                need("could not confirm that its start-at-sign-in task" in said,
                     f"{label}: service {operation} says that the task was not confirmed")
            now = service._scheduler("query", sid)
            need(now["exists"] and now["xml"] == task["xml"] and now["instances"] == 0,
                 label + ": the task is neither started nor changed nor removed")

        # ---- 1. A normal installation: the real CLI registers and starts the real task.
        port = windows_ports.select()
        for key, value in (("port", str(port)), ("bind", "[127.0.0.1]"),
                           ("public_origins", f"[https://127.0.0.1:{port}]")):
            cli("config " + key, "config", "set", key, value)
        cli("setup-tls", "setup-tls")
        cli("service install", "service", "install")
        first = ready("first start")
        task, spec = service._read(home)
        need(spec is not None and spec.sid == sid and spec.supervised, "the task is this installation's")
        name = stored(task["xml"])
        authority, _, user = name.partition("\\")
        need(authority and user and "\\" not in user and not name.startswith("S-1-"),
             "Task Scheduler stored the sign-in trigger's user as COMPUTER\\user, not as the SID")
        need(service._lookup(name) == (sid, 1), "that name is this account today")
        stopped("stop before the changes")

        # ---- 2. A second account, for a trigger that names another person.
        other = "amp" + uuid.uuid4().hex[:10]
        save("ownership.json", {"root": str(root), "sid": sid, "user": other})
        code, _ = powershell(f"New-LocalUser -Name '{other}' -NoPassword -Disabled "
                             "-Description 'Amplifier CI: temporary, never signs in' | Out-Null")
        need(code == 0, "a second local account exists for the comparison")
        found = service._lookup(authority + "\\" + other)
        need(found is not None and found[1] == 1 and found[0] != sid, "the second account has its own SID")
        other_sid = found[0]
        original = account_domain()
        need(original.casefold() == authority.casefold(), "the stored name's first part is the account domain")

        @contextmanager
        def renamed(label):
            """The state that a computer rename leaves for a stored account name. Always restored."""
            temporary = ("RENAMED-" + uuid.uuid4().hex[:6]).upper()
            # Saved before the change: `cleanup` restores from this record after any stop.
            save("account-domain.json", {"original": original, "temporary": temporary, "qualified": name, "sid": sid})
            try:
                account_domain(temporary)
                deadline = time.monotonic() + 15
                while service._lookup(name) is not None and time.monotonic() < deadline:
                    time.sleep(0.5)
                need(account_domain() == temporary, label + ": the account domain has another name")
                need(service._lookup(name) is None and service._lookup(authority) is None,
                     label + ": Windows maps the stored name and the old computer name to nothing")
                need(service._lookup(user) == (sid, 1) == service._lookup(temporary + "\\" + user),
                     label + ": the account itself is unchanged")
                yield
            finally:
                restored = restore_account_domain()
            need(restored and service._lookup(name) == (sid, 1),
                 label + ": the account domain has its own name again")

        def other_trigger(node):
            node("Triggers/LogonTrigger/UserId").text = other_sid

        def arguments(node):
            node("Actions/Exec/Arguments").text = "-I -S -c pass"

        def program(node):
            node("Actions/Exec/Command").text = str(Path(spec.python).with_name("python.exe"))

        # ---- 3. A task for another account, or with a changed action, is refused:
        # with the computer's name, and with the old name stored. Each one is
        # registered BEFORE the name changes, as on a renamed PC.
        current = task
        for label, change in (("another account's trigger", other_trigger),
                              ("changed arguments", arguments), ("changed program", program)):
            current = service._scheduler("replace", sid, previous=current["xml"], xml=variant(spec, change))
            kept = stored(current["xml"])
            expected = other if change is other_trigger else user
            need(kept.casefold() == (authority + "\\" + expected).casefold(),
                 label + ": Task Scheduler stored the trigger's user under this computer's name")
            refused(label, current)
            with renamed(label):
                need(service._scheduler("query", sid)["xml"] == current["xml"] and service._lookup(kept) is None,
                     label + ", old name stored: Windows maps the trigger's user to nothing")
                refused(label + ", old name stored", current)

        # ---- 4. The app's own task, registered before the name changes.
        current = service._scheduler("replace", sid, previous=current["xml"], xml=service.task_xml(spec))
        need(service._read(home)[1] == spec and stored(current["xml"]).casefold() == name.casefold(),
             "the app's own task is back, with this computer's name stored")
        with renamed("own task"):
            now = service._scheduler("query", sid)
            need(now["xml"] == current["xml"] and service._lookup(stored(now["xml"])) is None,
                 "Task Scheduler still holds the old name")
            # The defect: every one of these refused the app's own task.
            need(service._read(home)[1] == spec, "the app's check accepts its own task")
            cli("service start with the old name stored", "service", "start")
            second = ready("start with the old name stored")
            need(second["pid"] != first["pid"] or second["created"] != first["created"], "this is a new app process")
            now = service._scheduler("query", sid)
            need(now["xml"] == current["xml"] and now["instances"] == 1,
                 "the task runs, and its definition is the same text as before the start (old name stored)")
            stopped("stop with the old name stored")
            # Uninstall of the task, which the earlier check refused at its first step.
            cli("service uninstall with the old name stored", "service", "uninstall")
            need(service._scheduler("query", sid)["exists"] is False, "the task is removed")
        need(service._lookup(name) == (sid, 1), "the stored name is this account again")
        report.update(status="PASS", stage="complete")
    except BaseException as error:
        report["errorType"] = type(error).__name__
        if not isinstance(error, AssertionError):
            report["detail"] = str(error)[:300]
    finally:
        try:
            if not restore_account_domain():
                report.update(status="FAIL", stage="the account domain was NOT restored")
        except BaseException as error:
            report.update(status="FAIL", stage="the account domain was NOT restored", errorType=type(error).__name__)
        save("result.json", report)
        print(json.dumps(report, indent=2), flush=True)
    return report["status"] == "PASS"


def cleanup():
    """Restore the account-domain name, then remove this fixture's task, account and folder."""
    result = {"accountDomain": restore_account_domain()}
    owned = saved("ownership.json")
    if owned:
        from amplifier_web import windows_service as service
        root, sid = Path(owned["root"]), owned["sid"]
        try:
            task = service._scheduler("query", sid)
            if task["exists"]:
                description = ET.fromstring(task["xml"]).findtext(
                    f"{service._q('RegistrationInfo')}/{service._q('Description')}", "")
                spec = service.TaskSpec.decode(description[len(service.SOURCE) + 1:])
                # Only the task that this fixture registered for its own folder.
                if Path(spec.data_dir) != root / "app":
                    raise RuntimeError("This task is not the fixture's.")
                if task["instances"]:
                    task = service._stop(task, spec)
                service._scheduler("remove", sid, previous=task["xml"])
            result["task"] = "absent"
        except Exception as error:
            result["task"] = "left: " + type(error).__name__
        if owned.get("user"):
            code, _ = powershell(f"if (Get-LocalUser -Name '{owned['user']}' -ErrorAction SilentlyContinue) "
                                 f"{{ Remove-LocalUser -Name '{owned['user']}' }}")
            result["account"] = "absent" if code == 0 else "left"
        if root.parent != canonical_temp() or not root.name.startswith("task-after-rename-"):
            raise RuntimeError("This folder is not the fixture's.")
        shutil.rmtree("\\\\?\\" + str(root), ignore_errors=True)
        result["folder"] = "absent" if not root.exists() else "left"
    save("cleanup.json", result)
    print(json.dumps(result), flush=True)
    # The runner is deleted after the job. Only the security policy must be right before it ends.
    return result["accountDomain"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("run", "cleanup"))
    operation = parser.parse_args().operation
    require_disposable_runner()
    raise SystemExit(0 if globals()[operation]() else 1)
