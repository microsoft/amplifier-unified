"""Portable policy checks plus explicitly opted-in native task probes.

Native probes use a unique synthetic task and temporary workspace, never the
ordinary owner task, application data, listener or stored credentials.
"""
import base64
from dataclasses import replace
import json
import os
from contextlib import contextmanager
from pathlib import PureWindowsPath
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid
import xml.etree.ElementTree as ET

import pytest

from amplifier_web import windows_service as service

SID = "S-1-5-21-100-200-300-1001"


def spec(**overrides):
    values = dict(sid=SID, python=r"C:\Owner tools\Scripts\python.exe",
                  data_dir=r"C:\Synthetic owner\Unified data", workspace=r"C:\Work\项目 & café",
                  state_home=r"C:\Synthetic owner\shared state", tool_path=r"C:\Tools;C:\Git\bin",
                  registration_id="a" * 32)
    return service.TaskSpec(**{**values, **overrides})


def test_owner_uses_public_token_query_without_browser_or_filesystem(monkeypatch):
    import builtins
    from amplifier_web import windows_files
    calls = []
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(windows_files, "_API",
                        lambda: calls.append("TokenUser") or SimpleNamespace(sid=SID))
    monkeypatch.setattr(windows_files, "PinnedDirectory",
                        lambda *a, **kw: pytest.fail("SID lookup must not open the filesystem"))
    original_import = builtins.__import__
    def guard(name, *args, **kwargs):
        if "browser_entry" in name or "browser_entry" in (kwargs.get("fromlist") or ()):
            pytest.fail("Lifecycle must not depend on unpublished browser entry")
        if len(args) >= 3 and "browser_entry" in (args[2] or ()):
            pytest.fail("Lifecycle must not depend on unpublished browser entry")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guard)
    assert service._owner() == SID
    assert calls == ["TokenUser"]


@pytest.mark.parametrize("sid", ["S-1-5-18", "S-1-5-19", "S-1-5-20", "display-name", None])
def test_owner_refuses_system_or_invalid_token_identity(monkeypatch, sid):
    from amplifier_web import windows_files
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(windows_files, "_API", lambda: SimpleNamespace(sid=sid))
    with pytest.raises(service.WindowsServiceError, match="process-token SID"):
        service._owner()


def test_owner_token_query_failure_refuses_before_scheduler_transport(monkeypatch):
    from amplifier_web import windows_files
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="win32"))
    def fail():
        raise OSError("synthetic TokenUser failure")
    monkeypatch.setattr(windows_files, "_API", fail)
    monkeypatch.setattr(service.subprocess, "run",
                        lambda *a, **kw: pytest.fail("Failed token lookup must not reach COM"))
    with pytest.raises(service.WindowsServiceError, match="process-token SID"):
        service.task_state(spec().data_dir)


@pytest.mark.parametrize("operation", ["start", "stop", "restart", "status", "logs", "uninstall"])
def test_missing_data_root_refused_before_any_scheduler_query(monkeypatch, operation):
    monkeypatch.setattr(service, "_scheduler",
                        lambda *a, **kw: pytest.fail("Missing root must fail before scheduler query"))
    monkeypatch.setattr(service, "_owner", lambda: SID)
    with pytest.raises(service.WindowsServiceError, match="explicit data directory"):
        if operation == "uninstall":
            service.uninstall()
        else:
            service.command(operation)


@pytest.mark.skipif(os.name != "nt", reason="Read-only native Windows TokenUser oracle required")
def test_native_owner_sid_matches_independent_process_token():
    # Query only: no files, task registration, PowerShell, credentials or SID logs.
    import ctypes as C
    kernel = C.WinDLL("kernel32", use_last_error=True)
    security = C.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], C.c_void_p
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [C.c_void_p], C.c_int32
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [C.c_void_p], C.c_void_p
    security.OpenProcessToken.argtypes = [C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]
    security.OpenProcessToken.restype = C.c_int32
    security.GetTokenInformation.argtypes = [
        C.c_void_p, C.c_int32, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32)]
    security.GetTokenInformation.restype = C.c_int32
    security.ConvertStringSidToSidW.argtypes = [C.c_wchar_p, C.POINTER(C.c_void_p)]
    security.ConvertStringSidToSidW.restype = C.c_int32
    security.EqualSid.argtypes, security.EqualSid.restype = [C.c_void_p, C.c_void_p], C.c_int32
    token = C.c_void_p()
    assert security.OpenProcessToken(kernel.GetCurrentProcess(), 8, C.byref(token)), "Token query failed"
    try:
        size = C.c_uint32()
        assert not security.GetTokenInformation(token, 1, None, 0, C.byref(size)), "Unexpected sizing result"
        assert C.get_last_error() == 122 and 0 < size.value <= 65536, "Invalid TokenUser size"
        buffer = C.create_string_buffer(size.value)
        assert security.GetTokenInformation(token, 1, buffer, size, C.byref(size)), "TokenUser read failed"
        actual = C.c_void_p()
        converted = bool(security.ConvertStringSidToSidW(service._owner(), C.byref(actual)))
        if not converted:
            pytest.fail("SID conversion failed")  # Do not expose the SID in rewritten assertions.
        try:
            token_sid = C.cast(buffer, C.POINTER(C.c_void_p))[0]
            assert bool(security.EqualSid(token_sid, actual)), "Process-token SID mismatch"
        finally:
            kernel.LocalFree(actual)
    finally:
        assert kernel.CloseHandle(token), "Token handle close failed"


@pytest.fixture
def backend(monkeypatch):
    from amplifier_web import windows_task_runtime
    state = {"xml": None, "running": False, "calls": [], "backups": []}
    monkeypatch.setattr(service, "_owner", lambda: SID)
    monkeypatch.setattr(service, "_new_spec",
                        lambda data, workspace, sid, previous: replace(
                            spec(), data_dir=str(data), workspace=str(workspace or spec().workspace),
                            registration_id=previous.registration_id if previous else spec().registration_id))

    def scheduler(op, sid, *, previous=None, xml=None):
        state["calls"].append(op)
        assert sid == SID
        if op != "query":
            assert previous == state["xml"]
        if op in {"create", "replace"}:
            state["xml"] = xml
        elif op == "remove":
            state["xml"], state["running"] = None, False
        elif op == "stop":
            state["running"] = False
        elif op == "start":
            assert state["xml"] is not None
            state["running"] = True
        return {"exists": state["xml"] is not None, "xml": state["xml"],
                "state": 4 if state["running"] else 3, "lastResult": 0,
                "instances": int(state["running"])}

    monkeypatch.setattr(service, "_scheduler", scheduler)
    monkeypatch.setattr(service, "_backup", lambda old, xml:
                        state["backups"].append((old, xml)) or r"C:\private\task-backup.xml")
    def request_stop(value, **kwargs):
        state["calls"].append("request_stop")
        state["running"] = False
    monkeypatch.setattr(windows_task_runtime, "request_stop", request_stop)
    monkeypatch.setattr(windows_task_runtime, "observe", lambda spec: None)
    def missing_log(spec):
        raise FileNotFoundError("synthetic")
    monkeypatch.setattr(windows_task_runtime, "read_log", missing_log)
    return state


def test_xml_carries_owner_interactive_least_privilege_and_frozen_roots():
    value = spec()
    xml = service.task_xml(value)
    assert service.owned_spec(xml, SID, value.data_dir) == value
    assert "项目 &amp; café" in xml
    tree = ET.fromstring(xml)
    ns = {"t": service.NAMESPACE}
    text = lambda path: tree.findtext(path, namespaces=ns)
    assert text("t:Principals/t:Principal/t:UserId") == SID
    assert text("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert text("t:Triggers/t:LogonTrigger/t:UserId") == SID
    assert text("t:Settings/t:ExecutionTimeLimit") == "PT0S"
    assert text("t:Settings/t:RestartOnFailure/t:Count") == "3"
    assert text("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert text("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text("t:Actions/t:Exec/t:Command") == value.python
    assert text("t:Actions/t:Exec/t:WorkingDirectory") == value.workspace
    assert text("t:Actions/t:Exec/t:Arguments") == value.arguments()
    assert "--run" in value.arguments()
    assert value.encoded() in value.arguments()
    assert service.TaskSpec.decode(value.encoded()) == value


@pytest.mark.parametrize("field,value", [
    ("sid", "S-1-5-18"), ("sid", "owner"), ("python", r"C:\Windows\cmd.exe"),
    ("python", "python.exe"), ("workspace", r"\\server\share"),
    ("workspace", r"C:\work\..\other"), ("data_dir", r"C:relative"),
    ("state_home", "C:\\work\nbad"), ("state_home", r"C:\work:stream"),
    ("tool_path", "C:\\tools\r\ninjected"), ("registration_id", "not-an-id"),
    ("github_access", "private"),
])
def test_unsafe_spec_is_rejected(field, value):
    with pytest.raises(ValueError):
        spec(**{field: value})


@pytest.mark.parametrize("field", ["python", "workspace"])
@pytest.mark.parametrize("component", ["%USERNAME%", "literal%"])
def test_scheduler_expansion_paths_rejected_at_construction(field, component):
    value = rf"C:\Tools\{component}\Scripts\python.exe" if field == "python" else rf"C:\Work\{component}"
    with pytest.raises(ValueError, match="percent"):
        spec(**{field: value})


def _legacy_expansion_xml(field):
    """Matching metadata/action from the pre-repair generator, not mere tampering."""
    valid = spec()
    values = json.loads(base64.b64decode(valid.encoded()))
    values[field] = (r"C:\Tools\%USERNAME%\Scripts\python.exe" if field == "python"
                     else r"C:\Work\%USERNAME%")
    encoded = base64.b64encode(json.dumps(
        values, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).decode("ascii")
    tree = ET.fromstring(service.task_xml(valid))
    tree.find(f"{service._q('RegistrationInfo')}/{service._q('Description')}").text = service.SOURCE + ":" + encoded
    action = tree.find(f"{service._q('Actions')}/{service._q('Exec')}")
    action.find(service._q("Command" if field == "python" else "WorkingDirectory")).text = values[field]
    action.find(service._q("Arguments")).text = subprocess.list2cmdline(
        ["-I", "-m", "amplifier_web.windows_service", "--run", encoded])
    return ET.tostring(tree, encoding="unicode")


@pytest.mark.parametrize("field", ["python", "workspace"])
def test_scheduler_expansion_definition_rejected_at_adoption(field):
    with pytest.raises(service.WindowsServiceError, match="unmanaged"):
        service.owned_spec(_legacy_expansion_xml(field), SID)


@pytest.mark.parametrize("field", ["python", "workspace"])
@pytest.mark.parametrize("operation", ["install", "replace", "start", "stop", "restart", "uninstall"])
def test_scheduler_expansion_existing_task_refused_before_mutation(backend, field, operation):
    backend["xml"] = _legacy_expansion_xml(field)
    original = backend["xml"]
    with pytest.raises(service.WindowsServiceError, match="unmanaged"):
        if operation in {"install", "replace"}:
            service.install(spec().data_dir, replace=operation == "replace")
        elif operation == "uninstall":
            service.uninstall(spec().data_dir)
        else:
            service.command(operation, spec().data_dir)
    assert backend["calls"] == ["query"]
    assert backend["xml"] == original and not backend["backups"]


@pytest.mark.parametrize("field", ["python", "workspace"])
def test_scheduler_expansion_new_install_refused_before_scheduler_mutation(monkeypatch, field):
    from amplifier_web import host_dependencies, shared_state, windows_files
    # The read-only lookup found no task. Any later scheduler call is a mutation.
    monkeypatch.setattr(service, "_read", lambda: ({"exists": False}, None))
    monkeypatch.setattr(service, "_owner", lambda: SID)
    monkeypatch.setattr(service, "_require_python", lambda: None)
    monkeypatch.setattr(service, "_scheduler", lambda *a, **kw: pytest.fail("invalid launch must not reach scheduler"))
    monkeypatch.setattr(windows_files, "address", lambda path: PureWindowsPath(path))
    monkeypatch.setattr(shared_state, "shared_state_home", lambda: PureWindowsPath(spec().state_home))
    monkeypatch.setattr(host_dependencies, "tool_path", lambda: spec().tool_path)
    for name in service.LAUNCH_ROOTS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(service.sys, "executable",
                        r"C:\Tools\%USERNAME%\Scripts\python.exe" if field == "python" else spec().python)
    monkeypatch.setattr(service, "_background_python",
                        lambda: str(PureWindowsPath(service.sys.executable).with_name("pythonw.exe")))
    workspace = r"C:\Work\%USERNAME%" if field == "workspace" else spec().workspace
    with pytest.raises(ValueError, match="percent"):
        service.install(spec().data_dir, workspace)


def test_scheduler_expansion_literal_percent_preserved_in_encoded_roots(backend):
    value = spec(data_dir=r"C:\Data\%USERNAME%\literal%", state_home=r"C:\State\%USERNAME%\literal%")
    xml = service.task_xml(value)
    assert service.owned_spec(xml, SID, value.data_dir) == value
    assert service.TaskSpec.decode(value.encoded()).data_dir == value.data_dir
    assert service.TaskSpec.decode(value.encoded()).state_home == value.state_home
    # The scheduler sees only base64, not environment expansion syntax.
    assert "%" not in value.arguments()
    backend["xml"] = xml
    assert service.managed(value.data_dir)
    assert backend["calls"] == ["query"]


@pytest.mark.parametrize("before,after", [
    ("InteractiveToken", "S4U"), ("LeastPrivilege", "HighestAvailable"),
    (f"<UserId>{SID}</UserId>", "<UserId>S-1-5-18</UserId>"),
    ("<Command>C:\\Owner tools\\Scripts\\python.exe</Command>", "<Command>C:\\evil.exe</Command>"),
    ("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", "<ExecutionTimeLimit>PT72H</ExecutionTimeLimit>"),
    ("<URI>\\AmplifierUnified-", "<URI>\\Other-"),
    ("<Source>" + service.SOURCE, "<Source>foreign"),
    ("</Actions>", "<Exec><Command>C:\\evil.exe</Command></Exec></Actions>"),
    ("</Triggers>", "<BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>"),
    ("<WorkingDirectory>", "<WorkingDirectory>C:\\extra"),
    ("--run ", "--other "),
])
def test_full_definition_tampering_is_not_owned(before, after):
    xml = service.task_xml(spec())
    assert before in xml
    with pytest.raises(service.WindowsServiceError, match="unmanaged"):
        service.owned_spec(xml.replace(before, after), SID)


def test_wrong_owner_data_root_malformed_and_doctype_refused():
    xml = service.task_xml(spec())
    for candidate, sid, root in [(xml, SID + "1", None), (xml, SID, r"C:\Other"),
                                  ("<Task>", SID, None), ("<!DOCTYPE x>" + xml, SID, None)]:
        with pytest.raises(service.WindowsServiceError):
            service.owned_spec(candidate, sid, root)


def test_scheduler_date_and_formatting_do_not_create_false_ownership_failure():
    tree = ET.fromstring(service.task_xml(spec()))
    info = tree.find(service._q("RegistrationInfo"))
    ET.SubElement(info, service._q("Date")).text = "2026-01-01T00:00:00"
    ET.indent(tree)
    assert service.owned_spec(ET.tostring(tree, encoding="unicode"), SID) == spec()


def test_scheduler_native_omitted_defaults_and_account_spelling_are_equivalent(monkeypatch):
    tree = ET.fromstring(service.task_xml(spec()))
    tree.find(f"{service._q('Triggers')}/{service._q('LogonTrigger')}/{service._q('UserId')}").text = "SYNTHETIC\\Owner"
    omitted = {"Enabled", "RunLevel", "StartWhenAvailable", "WakeToRun", "RunOnlyIfIdle",
               "RunOnlyIfNetworkAvailable", "AllowStartOnDemand", "Priority",
               "Hidden", "AllowHardTerminate"}
    for parent in tree.iter():
        for node in list(parent):
            if node.tag.split("}")[-1] in omitted:
                parent.remove(node)
    calls = []
    monkeypatch.setattr(service, "_account_sid", lambda name: calls.append(name) or SID)
    assert service.owned_spec(ET.tostring(tree, encoding="unicode"), SID) == spec()
    assert calls == ["SYNTHETIC\\Owner"]
    monkeypatch.setattr(service, "_account_sid", lambda name: SID + "1")
    with pytest.raises(service.WindowsServiceError):
        service.owned_spec(ET.tostring(tree, encoding="unicode"), SID)


def test_scheduler_default_normalization_does_not_accept_unsafe_or_unknown_settings():
    xml = service.task_xml(spec())
    for before, after in (
        ("<Enabled>true</Enabled>", "<Enabled>false</Enabled>"),
        ("<Priority>7</Priority>", "<Priority>4</Priority>"),
        ("<RunLevel>LeastPrivilege</RunLevel>", "<RunLevel>HighestAvailable</RunLevel>"),
        ("</Settings>", "<UnknownSecuritySetting>true</UnknownSecuritySetting></Settings>"),
        ("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>", ""),
    ):
        with pytest.raises(service.WindowsServiceError):
            service.owned_spec(xml.replace(before, after), SID)


def test_install_registration_is_separate_from_health_and_rerun_reuses(backend):
    result = service.install(spec().data_dir, spec().workspace)
    assert result["status"] == "installed"
    assert result["healthy"] is False and result["processVerified"] is False
    assert backend["calls"] == ["query", "create", "start"]
    original = backend["xml"]
    backend["calls"].clear()
    assert service.install(spec().data_dir, spec().workspace)["status"] == "current"
    assert backend["calls"] == ["query", "start"]
    assert backend["xml"] == original and not backend["backups"]


def test_owned_changed_roots_require_replace_and_private_backup_first(backend):
    service.install(spec().data_dir)
    original = backend["xml"]
    backend["calls"].clear()
    with pytest.raises(service.WindowsServiceError, match="--replace"):
        service.install(r"C:\Changed data")
    assert backend["calls"] == ["query"] and backend["xml"] == original
    assert not backend["backups"]
    result = service.install(r"C:\Changed data", replace=True)
    assert result["status"] == "replaced" and result["backup"] == r"C:\private\task-backup.xml"
    assert backend["backups"] == [(spec(), original)]
    assert service.owned_spec(backend["xml"], SID).data_dir == r"C:\Changed data"


@pytest.mark.parametrize("operation", ["install", "replace", "start", "stop", "restart", "uninstall"])
def test_foreign_or_tampered_task_is_never_stopped_or_overwritten(backend, operation):
    backend["xml"] = service.task_xml(spec()).replace("InteractiveToken", "Password")
    with pytest.raises(service.WindowsServiceError, match="unmanaged"):
        if operation in {"install", "replace"}:
            service.install(spec().data_dir, replace=operation == "replace")
        elif operation == "uninstall":
            service.uninstall(spec().data_dir)
        else:
            service.command(operation, spec().data_dir)
    assert backend["calls"] == ["query"] and not backend["backups"]


def test_failed_backup_prevents_stop_or_registration(backend, monkeypatch):
    service.install(spec().data_dir)
    backend["calls"].clear()
    def fail(*args):
        raise PermissionError("unsafe private backup directory")
    monkeypatch.setattr(service, "_backup", fail)
    with pytest.raises(PermissionError):
        service.install(r"C:\Changed data", replace=True)
    assert backend["calls"] == ["query"]


def test_status_and_managed_are_passive_and_logs_are_truthful(backend):
    assert service.task_state()["state"] == "not configured"
    assert not service.managed()
    service.install(spec().data_dir)
    backend["calls"].clear()
    assert service.managed(spec().data_dir)
    assert not service.managed(r"C:\Other")
    state = service.command("status", spec().data_dir)
    assert state["state"] == "running" and not state["healthy"]
    assert not state["processVerified"] and state["logsAvailable"]
    with pytest.raises(service.WindowsServiceError, match="recorded yet"):
        service.command("logs", spec().data_dir)
    assert set(backend["calls"]) == {"query"}


def test_command_restart_stop_remove_only_exact_owned_definition(backend):
    service.uninstall(spec().data_dir)
    assert backend["calls"] == ["query"]
    service.install(spec().data_dir)
    backend["calls"].clear()
    service.command("restart", spec().data_dir)
    service.uninstall(spec().data_dir)
    assert backend["calls"] == ["query", "request_stop", "query", "start",
                                "query", "request_stop", "query", "remove"]
    assert backend["xml"] is None


def test_wrong_root_and_unknown_command_do_not_mutate(backend):
    service.install(spec().data_dir)
    backend["calls"].clear()
    with pytest.raises(service.WindowsServiceError):
        service.command("stop", r"C:\Other")
    with pytest.raises(ValueError):
        service.command("kill-all")
    assert backend["calls"] == ["query"]


def test_current_process_never_uses_environment_or_engine_pid_as_proof(monkeypatch):
    monkeypatch.setenv("AMPLIFIER_UNIFIED_TASK", "anything")
    assert service.current_process_is_managed(spec().data_dir) is False
    with pytest.raises(service.WindowsServiceError, match="correlation"):
        service.managed_restart_command()


def test_bridge_uses_static_encoded_script_and_json_stdin_not_interpolation(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "_owner", lambda: SID)
    monkeypatch.setattr(service, "_powershell", lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    xml = service.task_xml(spec(workspace=r"C:\Work\café ' $() & 项目"))
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"exists": True, "xml": xml, "state": 3, "instances": 0}).encode(), stderr=b"")
    monkeypatch.setattr(service.subprocess, "run", run)
    service._scheduler("create", SID, xml=xml)
    argv, kwargs = calls[0]
    assert base64.b64decode(argv[-1]).decode("utf-16-le") == service._SCRIPT
    assert xml not in " ".join(argv)
    assert json.loads(kwargs["input"])["xml"] == xml
    assert kwargs["shell"] is False and kwargs["timeout"] == 20
    assert "-NoProfile" in argv and "-NonInteractive" in argv
    assert "RegisterTask($p.name, $p.xml, 2, $p.sid, $null, 3, $null)" in service._SCRIPT
    assert "$task.Xml -cne $p.previous" in service._SCRIPT


@pytest.mark.parametrize("response", [
    SimpleNamespace(returncode=1, stdout=b'{"error":"denied"}'),
    SimpleNamespace(returncode=0, stdout=b"not JSON"),
    SimpleNamespace(returncode=0, stdout=b'{"exists":1}'),
])
def test_bridge_policy_and_invalid_receipts_are_not_success(monkeypatch, response):
    monkeypatch.setattr(service, "_owner", lambda: SID)
    monkeypatch.setattr(service, "_powershell", lambda: r"C:\Windows\powershell.exe")
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **kw: response)
    with pytest.raises(service.WindowsServiceError):
        service._scheduler("query", SID)


def test_timeout_is_unknown_not_automatic_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "_owner", lambda: SID)
    monkeypatch.setattr(service, "_powershell", lambda: r"C:\Windows\powershell.exe")
    def run(*a, **kw):
        calls.append(a)
        raise subprocess.TimeoutExpired(a[0], 20)
    monkeypatch.setattr(service.subprocess, "run", run)
    with pytest.raises(service.WindowsServiceError, match="unknown"):
        service._scheduler("start", SID, previous=service.task_xml(spec()))
    assert len(calls) == 1


def test_wrong_platform_refuses_before_any_subprocess(monkeypatch):
    monkeypatch.setattr(service.sys, "platform", "linux")
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **kw: pytest.fail("no service mutation"))
    with pytest.raises(service.WindowsServiceError, match="native Windows"):
        service._scheduler("query", SID)


def test_action_enters_existing_cli_in_same_process_with_exact_environment(backend, monkeypatch):
    from amplifier_web import cli, host_dependencies, windows_task_runtime
    monkeypatch.setattr(service, "_require_python", lambda: None)
    service.install(spec().data_dir)
    calls = []
    monkeypatch.setattr(service.os, "chdir", lambda path: calls.append(("cwd", path)))
    for name in ("PATH", "AMPLIFIER_SESSION_STATE_HOME", "AMPLIFIER_GITHUB_ACCESS"):
        monkeypatch.setenv(name, "prior")
    monkeypatch.setattr(service.sys, "argv", [])
    monkeypatch.setattr(host_dependencies, "prepare_environment", lambda: None)
    monkeypatch.setattr(service.Path, "samefile", lambda *a: True)
    monkeypatch.setattr(cli, "_parse", lambda: "parsed")
    class Runner:
        exit_code = 0
        def __init__(self, value):
            assert value == spec()
        def phase(self, phase):
            calls.append(("phase", phase))
    monkeypatch.setattr(windows_task_runtime, "Runner", Runner)
    @contextmanager
    def private_log(spec):
        import io
        yield io.StringIO()
    monkeypatch.setattr(windows_task_runtime, "PrivateLog", private_log)
    monkeypatch.setattr(cli, "_serve", lambda *a, **kw: calls.append(("serve", a[0], str(a[1]))))
    service._run(spec().encoded())
    assert service.sys.argv == [spec().python, "--no-open", "--data-dir", spec().data_dir,
                                "--workspace", spec().workspace, "serve"]
    assert os.environ["AMPLIFIER_SESSION_STATE_HOME"] == spec().state_home
    assert os.environ["PATH"] == spec().tool_path
    assert calls == [("cwd", spec().workspace), ("serve", "parsed", spec().data_dir),
                     ("phase", "stopped")]


def test_action_refuses_mismatched_registered_definition(backend, monkeypatch):
    monkeypatch.setattr(service, "_require_python", lambda: None)
    service.install(spec().data_dir)
    with pytest.raises(service.WindowsServiceError, match="does not match"):
        service._run(replace(spec(), workspace=r"C:\Other").encoded())


def test_leaf_whitespace_and_duplicate_actions_cannot_hide_definition_change():
    xml = service.task_xml(spec())
    for changed in (xml.replace("<Command>", "<Command> "),
                    xml.replace("</Arguments>", " </Arguments>"),
                    xml.replace("</Actions>", "<Exec /></Actions>")):
        with pytest.raises(service.WindowsServiceError):
            service.owned_spec(changed, SID)


def test_action_command_line_has_finite_native_budget():
    oversized = spec(tool_path="x" * 16384, workspace="C:\\" + "项" * 16000)
    with pytest.raises(ValueError, match="command-line budget"):
        service.task_xml(oversized)


def test_private_backup_uses_existing_pinned_no_replace_api(monkeypatch):
    from amplifier_web import windows_files
    calls = []
    monkeypatch.setattr(windows_files, "address", lambda value: PureWindowsPath(value))
    class Pinned:
        def publish_private(self, name, content):
            calls.append(("publish", name, content))
    @contextmanager
    def directory(path, **kwargs):
        calls.append(("pin", path, kwargs))
        yield Pinned()
    monkeypatch.setattr(windows_files, "directory", directory)
    xml = service.task_xml(spec())
    result = service._backup(spec(), xml)
    assert calls[0] == ("pin", PureWindowsPath(spec().data_dir) / "config/services",
                        {"owned_root": PureWindowsPath(spec().data_dir), "create": True})
    assert calls[1][0] == "publish" and calls[1][2] == xml.encode("utf-8")
    assert PureWindowsPath(result).name == calls[1][1]


def test_new_spec_freezes_lexical_paths_and_keeps_anonymous_choice(monkeypatch):
    from amplifier_web import host_dependencies, shared_state, windows_files
    monkeypatch.setattr(service.sys, "executable", spec().python)
    monkeypatch.setattr(service, "_background_python", lambda: spec().python)
    monkeypatch.setattr(service.sys, "version_info", (3, 13))
    monkeypatch.setattr(service.sysconfig, "get_config_var", lambda key: None)
    monkeypatch.setattr(windows_files, "address", lambda path: PureWindowsPath(path))
    monkeypatch.setattr(shared_state, "shared_state_home", lambda: PureWindowsPath(spec().state_home))
    monkeypatch.setattr(host_dependencies, "tool_path", lambda: spec().tool_path)
    monkeypatch.delenv("AMPLIFIER_GITHUB_ACCESS", raising=False)
    for name in service.LAUNCH_ROOTS:
        monkeypatch.delenv(name, raising=False)
    previous = spec(github_access="anonymous")
    result = service._new_spec(spec().data_dir, spec().workspace, SID, previous)
    assert result == replace(previous, supervised=True)
    monkeypatch.setenv("AMPLIFIER_GITHUB_ACCESS", "auto")
    assert service._new_spec(spec().data_dir, spec().workspace, SID, previous).github_access == "auto"
    monkeypatch.setattr(service.sys, "version_info", (3, 14))
    with pytest.raises(service.WindowsServiceError, match="3.13"):
        service._new_spec(spec().data_dir, spec().workspace, SID, previous)


def test_new_background_host_uses_installed_windowless_sibling(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.touch()
    monkeypatch.setattr(service.sys, "executable", str(python))
    with pytest.raises(service.WindowsServiceError, match="windowless"):
        service._background_python()
    windowless = tmp_path / "pythonw.exe"
    windowless.touch()
    assert service._background_python() == str(windowless)
    monkeypatch.setattr(service.sys, "executable", str(windowless))
    assert service._background_python() == str(windowless)
    value = spec(python=r"C:\Owner tools\Scripts\pythonw.exe")
    assert service.owned_spec(service.task_xml(value), SID) == value
    # Older console definitions remain inspectable, not silently rewritten.
    assert service.owned_spec(service.task_xml(spec()), SID) == spec()


@pytest.mark.parametrize("supervised", [False, True])
def test_task_host_protocol_roundtrip_and_legacy_definition_identity(supervised):
    value = spec(supervised=supervised)
    raw = json.loads(base64.b64decode(value.encoded()))
    assert raw.get("supervised", False) is supervised
    assert ("supervised" in raw) is supervised
    assert service.TaskSpec.decode(value.encoded()) == value
    assert service.owned_spec(service.task_xml(value), SID) == value


@pytest.mark.parametrize("supervised", [None, 1, "true", []])
def test_unknown_supervision_protocol_is_not_adopted(supervised):
    with pytest.raises(ValueError, match="protocol"):
        spec(supervised=supervised)


def test_supervisor_normal_stop_does_not_restart_or_infer_generation(monkeypatch):
    from amplifier_web import windows_task_runtime as runtime
    value = spec(supervised=True)
    monkeypatch.setattr(service, "_read", lambda home: ({"state": 4, "instances": 1}, value))
    monkeypatch.setattr(service, "_launch_python", lambda spec: (spec.python, None))
    events = []
    class Child:
        pid = 123
        def __init__(self, argv, **kwargs):
            events.append((argv, kwargs))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def wait(self):
            return 0
        def poll(self):
            return None
    monkeypatch.setattr(service.subprocess, "Popen", Child)
    @contextmanager
    def process(pid):
        yield {"pid": pid, "created": "134000000000000001",
               "image": r"C:\Python\pythonw.exe", "exited": lambda: True}
    monkeypatch.setattr(runtime, "process", process)
    records = [None, {"schema": 1, "registration": value.registration_id,
        "run": "b" * 32, "pid": 123, "created": "134000000000000001",
        "image": r"C:\Python\pythonw.exe", "phase": "starting"}]
    monkeypatch.setattr(runtime, "read", lambda spec: records.pop(0))
    @contextmanager
    def log(spec):
        yield SimpleNamespace(write=lambda *a: None)
    monkeypatch.setattr(runtime, "PrivateLog", log)
    monkeypatch.setattr(runtime, "read_restart", lambda spec: pytest.fail("normal stop must not request a restart"))
    assert service._supervise(value) == 0
    assert len(events) == 1
    assert events[0][0] == [value.python, "-I", "-m", "amplifier_web.windows_service",
                            "--host", value.encoded()]
    assert events[0][1]["stdin"] == subprocess.DEVNULL
    assert events[0][1]["cwd"] == value.workspace


def test_unqualified_supervised_update_owner_is_closed_before_any_native_read(monkeypatch):
    assert service.SUPERVISED_UPDATES_QUALIFIED is False
    monkeypatch.setattr(service, "_read", lambda *a: pytest.fail("closed gate must not inspect native tasks"))
    assert service.update_owner(spec().data_dir) is None


@pytest.mark.parametrize("change", ["legacy", "instances", "missing", "other-pid", "starting"])
def test_supervised_update_requires_the_actual_listening_host(monkeypatch, change):
    from amplifier_web import application_generations, windows_task_runtime
    value = spec(supervised=change != "legacy")
    task = {"state": 4, "instances": 2 if change == "instances" else 1}
    record = None if change == "missing" else {
        "pid": os.getpid() + (1 if change == "other-pid" else 0),
        "phase": "starting" if change == "starting" else "listening"}
    monkeypatch.setattr(service, "SUPERVISED_UPDATES_QUALIFIED", True)
    monkeypatch.setattr(service, "_read", lambda home: (task, value))
    monkeypatch.setattr(windows_task_runtime, "observe", lambda spec: record)
    monkeypatch.setattr(application_generations, "supports", lambda *a: pytest.fail("unverified owner cannot inspect candidate"))
    assert service.update_owner(value.data_dir) is None


def test_supervised_update_owner_preserves_stable_bootstrap(monkeypatch):
    from amplifier_web import application_generations, windows_task_runtime
    value = spec(supervised=True)
    monkeypatch.setattr(service, "SUPERVISED_UPDATES_QUALIFIED", True)
    monkeypatch.setattr(service, "_read", lambda home: ({"state": 4, "instances": 1}, value))
    monkeypatch.setattr(windows_task_runtime, "observe",
                        lambda spec: {"pid": os.getpid(), "phase": "listening"})
    seen = []
    monkeypatch.setattr(application_generations, "supports", lambda path: seen.append(str(path)) or True)
    assert service.update_owner(value.data_dir) == value
    assert len(seen) == 1


def test_launch_roots_are_public_paths_not_arbitrary_environment():
    value = spec(launch_roots={"AMPLIFIER_HOME": r"C:\Private\shared",
                              "UV_CACHE_DIR": r"C:\Private\cache"})
    assert service.TaskSpec.decode(value.encoded()) == value
    for roots in ({"OPENAI_API_KEY": "secret"}, {"PATH": r"C:\Other"},
                  {"AMPLIFIER_HOME": "relative"}, {"UV_CACHE_DIR": "C:\\bad\npath"}):
        with pytest.raises(ValueError):
            spec(launch_roots=roots)


def test_free_threaded_interpreter_is_not_ordinary_python(monkeypatch):
    monkeypatch.setattr(service.sys, "version_info", (3, 13))
    monkeypatch.setattr(service.sysconfig, "get_config_var", lambda key: 1)
    with pytest.raises(service.WindowsServiceError, match="ordinary CPython"):
        service._require_python()


def test_failed_stop_cannot_start_successor_or_remove_task(backend, monkeypatch):
    from amplifier_web import windows_task_runtime
    service.install(spec().data_dir)
    def fail(*args, **kwargs):
        raise service.WindowsServiceError("stop outcome unknown")
    monkeypatch.setattr(windows_task_runtime, "request_stop", fail)
    backend["calls"].clear()
    with pytest.raises(service.WindowsServiceError, match="unknown"):
        service.command("restart", spec().data_dir)
    assert backend["calls"] == ["query"] and backend["xml"] is not None


def test_failed_registration_verification_does_not_start_task(backend, monkeypatch):
    original = service._scheduler
    def altered(op, sid, **kwargs):
        value = original(op, sid, **kwargs)
        if op == "create":
            value["xml"] = value["xml"].replace("InteractiveToken", "S4U")
        return value
    monkeypatch.setattr(service, "_scheduler", altered)
    with pytest.raises(service.WindowsServiceError):
        service.install(spec().data_dir)
    assert backend["calls"] == ["query", "create"]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows command-line parser required")
def test_native_argv_unicode_and_quoting_roundtrip():
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    expected = [spec().python, "-I", "-m", "amplifier_web.windows_service", "--run", spec().encoded()]
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(subprocess.list2cmdline(expected), ctypes.byref(count))
    assert pointer
    try:
        assert list(pointer[:count.value]) == expected
    finally:
        kernel.LocalFree(pointer)


@pytest.mark.skipif(os.name != "nt" or os.environ.get("AMPLIFIER_TEST_WINDOWS_TASKS") != "1",
                    reason="Opt-in only: AMPLIFIER_TEST_WINDOWS_TASKS=1 on an interactive Windows test account")
def test_native_synthetic_task_create_run_exit_remove(tmp_path):
    # This is a transport probe, NOT Unified host/sign-out/readiness acceptance.
    # Never register the production task name or run the real application.
    sid = service._owner()
    name = "AmplifierUnified-Test-" + uuid.uuid4().hex
    probe = spec(sid=sid, python=sys.executable, data_dir=str(tmp_path / "synthetic-data"),
                 workspace=str(tmp_path), state_home=str(tmp_path / "synthetic-state"))
    root = ET.fromstring(service.task_xml(probe))
    root.find(f"{service._q('RegistrationInfo')}/{service._q('URI')}").text = "\\" + name
    root.find(f"{service._q('RegistrationInfo')}/{service._q('Description')}").text = "synthetic transport probe"
    action = root.find(f"{service._q('Actions')}/{service._q('Exec')}/{service._q('Arguments')}")
    action.text = subprocess.list2cmdline(["-I", "-S", "-c", "import time; time.sleep(5)"])
    xml = ET.tostring(root, encoding="unicode")
    assert service._scheduler("query", sid, name=name)["exists"] is False
    # Cleanup always reconciles the exact unique task after a lost receipt.
    try:
        created = service._scheduler("create", sid, name=name, xml=xml)
        # Exercise the real scheduler's XML normalization against the ownership
        # parser, without ever registering or running the ordinary task.
        normalized = ET.fromstring(created["xml"])
        normalized.find(f"{service._q('RegistrationInfo')}/{service._q('URI')}").text = "\\" + service.task_name(sid)
        normalized.find(f"{service._q('RegistrationInfo')}/{service._q('Description')}").text = (
            service.SOURCE + ":" + probe.encoded())
        normalized.find(f"{service._q('Actions')}/{service._q('Exec')}/{service._q('Arguments')}").text = probe.arguments()
        assert service.owned_spec(ET.tostring(normalized, encoding="unicode"), sid) == probe
        service._scheduler("start", sid, name=name, previous=created["xml"])
        deadline = time.monotonic() + 10
        while True:
            current = service._scheduler("query", sid, name=name)
            if current["state"] == 4 and current["instances"] == 1:
                break
            assert time.monotonic() < deadline, "Synthetic task did not run in this interactive account"
            time.sleep(.1)
        deadline = time.monotonic() + 15
        while current["instances"]:
            assert time.monotonic() < deadline, "Synthetic task did not finish; exact task retained"
            time.sleep(.1)
            current = service._scheduler("query", sid, name=name)
    finally:
        current = service._scheduler("query", sid, name=name)
        if current["exists"]:
            actual = ET.fromstring(current["xml"])
            for date in actual.findall(f"{service._q('RegistrationInfo')}/{service._q('Date')}"):
                actual.find(service._q("RegistrationInfo")).remove(date)
            # Do not stop/remove a task that changed into something else during
            # the probe, even if a foreign definition kept our random task name.
            assert service._tree(actual) == service._tree(root), (
                "Synthetic task changed; retained task needs explicit reconciliation: " + name)
            service._scheduler("remove", sid, name=name, previous=current["xml"])
    assert service._scheduler("query", sid, name=name)["exists"] is False