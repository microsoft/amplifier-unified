import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import windows_installation as setup
from amplifier_web import deployment, windows_ports, windows_service, updates


@pytest.fixture
def installation(tmp_path, monkeypatch):
    calls = []
    state = {"task": {"exists": False}, "spec": None,
             "runtime": {"ok": True, "prepared": True, "connectionRequired": True,
                         "workerReady": False},
             "health": {"ok": True, "responding": True, "processVerified": True},
             # No port saved yet, every address free, next selection 23456.
             "saved": None, "address": "free", "selected": 23456, "kinds": [],
             "config": {"port": 8942, "public_origins": ["https://127.0.0.1:8942"]}}
    monkeypatch.setattr(windows_service, "_read", lambda home: (state["task"], state["spec"]))
    monkeypatch.setattr(setup, "saved_port", lambda home: state["saved"])
    monkeypatch.setattr(setup, "address_kind", lambda port, spec=None:
                        state["kinds"].append((port, spec is not None)) or state["address"])
    monkeypatch.setattr(windows_ports, "select", lambda: state["selected"])
    monkeypatch.setattr(deployment, "load_server_config", lambda home: state["config"])

    async def command(*args, **kwargs):
        assert args[1:5] == ("--data-dir", str(tmp_path / "app"),
                             "--workspace", str(tmp_path / "workspace"))
        assert kwargs["env"] == {"PUBLIC": "only"} and kwargs["cwd"] == tmp_path
        action = args[5:]
        calls.append(action)
        if action[:2] == ("runtime", "prepare"):
            return json.dumps(state["runtime"])
        if action[:2] == ("service", "status"):
            return json.dumps(state["health"])
        return ""
    monkeypatch.setattr(updates, "process", command)
    run = lambda: asyncio.run(setup.prepare_background(
        tmp_path, tmp_path / "host/Scripts/amplifier-unified.exe", {"PUBLIC": "only"}))
    return tmp_path, state, calls, run


def test_initial_setup_uses_cli_and_accountless_dependencies_before_registration(installation):
    _, _, calls, run = installation
    result = run()
    assert result["created"] and result["runtime"]["connectionRequired"]
    # The port is selected last and saved with its origin in ONE write, just
    # before the host's own listener claims it.
    assert calls == [
        ("config", "set", "bind", "[127.0.0.1]"),
        ("setup-tls",), ("runtime", "prepare", "--json"),
        ("config", "set", "port", "23456", "public_origins", "[https://127.0.0.1:23456]"),
        ("service", "install"), ("service", "status", "--wait", "60", "--json")]


def test_two_installations_save_different_selected_ports(installation):
    _, value, calls, run = installation
    run()
    value["selected"] = 31007  # A second installation's own selection.
    run()
    saved = [action[3] for action in calls if action[:3] == ("config", "set", "port")]
    assert saved == ["23456", "31007"]


def test_saved_port_and_settings_are_kept_when_the_task_is_registered_again(installation):
    # For example after "Remove Amplifier sign-in host": the browser already
    # knows this origin, so setup never selects or rewrites it.
    _, value, calls, run = installation
    value["saved"] = 8942
    result = run()
    assert result["created"] and value["kinds"] == [(8942, False)]
    assert calls == [("setup-tls",), ("runtime", "prepare", "--json"),
                     ("service", "install"), ("service", "status", "--wait", "60", "--json")]


def test_explicit_port_keeps_the_fixed_prototype_address(installation):
    root, _, calls, _ = installation
    asyncio.run(setup.prepare_background(
        root, root / "host/Scripts/amplifier-unified.exe", {"PUBLIC": "only"}, port=8942))
    assert ("config", "set", "port", "8942", "public_origins", "[https://127.0.0.1:8942]") in calls
    with pytest.raises(ValueError, match="TCP port"):
        asyncio.run(setup.prepare_background(root, root / "x.exe", {}, port=0))


def owned(installation, *, state=4, instances=1):
    root, value, _, _ = installation
    value.update(task={"exists": True, "state": state, "instances": instances},
                 spec=SimpleNamespace(python=str(root / "host/Scripts/python.exe"),
                                      workspace=str(root / "workspace")))


def test_owned_running_reopen_never_reconfigures_resolves_or_restarts(installation):
    owned(installation)
    _, _, calls, run = installation
    result = run()
    assert result["created"] is False and result["runtime"] is None
    assert calls == [("service", "status", "--wait", "60", "--json")]


def test_owned_stopped_task_starts_once_without_reinstall(installation):
    owned(installation, state=3, instances=0)
    _, value, calls, run = installation
    run()
    assert calls == [("service", "start"), ("service", "status", "--wait", "60", "--json")]
    assert value["kinds"] == [(8942, True)]  # The saved port, never a new selection.


@pytest.mark.parametrize("kind", ["in-use", "reserved"])
@pytest.mark.parametrize("registered", [False, True])
def test_taken_saved_address_is_refused_before_any_mutation(installation, kind, registered):
    _, value, calls, run = installation
    if registered:
        owned(installation, state=3, instances=0)
    else:
        value["saved"] = 8942
    value["address"] = kind
    with pytest.raises(setup.AddressUnavailable) as refused:
        run()
    assert (refused.value.kind, refused.value.port) == (kind, 8942)
    assert "adopted or stopped" in str(refused.value)
    assert calls == []  # No start, selection, reconfiguration or stop.


def test_own_host_outside_its_task_is_not_another_program(installation):
    owned(installation, state=3, instances=0)
    _, value, calls, run = installation
    value["address"] = "own"
    with pytest.raises(RuntimeError, match="safe start") as refused:
        run()
    assert not isinstance(refused.value, setup.AddressUnavailable) and calls == []


@pytest.mark.parametrize("kind,typed", [("in-use", True), ("reserved", True), ("own", False), ("free", False)])
def test_port_lost_between_check_and_listener_is_named_not_generic(installation, monkeypatch, kind, typed):
    # The earlier test bind is only a filter; the host's listener claims the port.
    owned(installation, state=3, instances=0)
    _, value, calls, run = installation
    value["health"]["responding"] = False
    answers = iter(["free", kind])
    monkeypatch.setattr(setup, "address_kind", lambda port, spec=None: next(answers))
    with pytest.raises(RuntimeError) as failed:
        run()
    assert isinstance(failed.value, setup.AddressUnavailable) is typed
    if typed:
        assert (failed.value.kind, failed.value.port) == (kind, 8942)
    else:
        assert "not ready" in str(failed.value)
    assert calls == [("service", "start"), ("service", "status", "--wait", "60", "--json")]


def test_confirmed_repair_moves_port_and_its_origins_in_one_write(installation):
    owned(installation, state=3, instances=0)
    root, value, calls, _ = installation
    value["config"] = {"port": 8942, "public_origins": [
        "https://127.0.0.1:8942", "https://pc.example.ts.net", "https://192.0.2.7:8942",
        "https://[2001:db8::7]:8942", "https://other.example:9000"]}
    result = asyncio.run(setup.change_address(
        root, root / "host/Scripts/amplifier-unified.exe", {"PUBLIC": "only"}))
    assert result == {"port": 23456, "previous": 8942}
    assert calls == [("config", "set", "port", "23456", "public_origins", json.dumps([
        "https://127.0.0.1:23456", "https://pc.example.ts.net", "https://192.0.2.7:23456",
        "https://[2001:db8::7]:23456", "https://other.example:9000"]))]


@pytest.mark.parametrize("state,instances", [(4, 1), (2, 0), (3, 1)])
def test_repair_never_moves_a_host_that_is_not_stopped(installation, state, instances):
    owned(installation, state=state, instances=instances)
    root, _, calls, _ = installation
    with pytest.raises(RuntimeError, match="stopped"):
        asyncio.run(setup.change_address(root, root / "host/Scripts/amplifier-unified.exe", {}))
    assert calls == []


def test_owner_text_names_the_cause_and_the_cost_of_the_repair():
    used = setup.address_message(setup.AddressUnavailable("in-use", 23456))
    reserved = setup.address_message(setup.AddressUnavailable("reserved", 23456))
    for text in (used, reserved):
        assert "port 23456" in text and "select Yes" in text.replace("Select", "select")
        assert "chats, settings and accounts stay" in text and "not carried over" in text
    assert "another program is using" in used and "Close that program" in used
    # No program holds a reserved port, so "close it" would be wrong advice.
    assert "Windows has reserved" in reserved and "nothing to close" in reserved
    assert "Close that program" not in reserved


@pytest.fixture
def private_home(tmp_path_factory):
    # Native Windows refuses a runner temp folder with a broad inherited ACL;
    # use a private profile child there instead of repairing or skipping.
    if os.name != "nt":
        yield tmp_path_factory.mktemp("home")
        return
    import shutil
    import uuid
    from amplifier_web.windows_files import address, directory
    root = address(Path.home() / (".unified-port-test-" + uuid.uuid4().hex))
    with directory(root, owned_root=root, create=True):
        pass
    try:
        yield root
    finally:
        shutil.rmtree(root)


def test_saved_port_is_the_one_whose_loopback_origin_setup_wrote(private_home):
    home = private_home
    assert setup.saved_port(home) is None  # Defaults are not a setup choice.
    config = deployment.load_server_config(home)
    deployment.save_server_config(home, {
        **config, "port": 23456, "public_origins": ["https://pc.example.ts.net", "https://127.0.0.1:23456"]})
    assert setup.saved_port(home) == 23456
    # The same file is what a restart and an update read: the port is unchanged.
    assert deployment.load_server_config(home)["port"] == 23456
    deployment.save_server_config(home, {**config, "port": 23457,
                                         "public_origins": ["https://127.0.0.1:23456"]})
    assert setup.saved_port(home) is None


def test_selected_port_and_origin_reach_the_private_config_through_the_real_command(private_home):
    # The exact two settings first setup saves, through the real CLI handler
    # and (on native Windows) the real private configuration boundary.
    from argparse import Namespace
    from amplifier_web import cli
    home = private_home
    cli._config(Namespace(config_command="set", key="bind", value="[127.0.0.1]"), home)
    assert setup.saved_port(home) is None
    cli._config(Namespace(config_command="set", key="port", value="23456",
                          more=["public_origins", "[https://127.0.0.1:23456]"]), home)
    saved = deployment.load_server_config(home)
    assert (saved["port"], saved["public_origins"], saved["bind"]) == (
        23456, ["https://127.0.0.1:23456"], ["127.0.0.1"])
    assert setup.saved_port(home) == 23456
    # The owner-confirmed repair writes the moved origins as JSON in the same way.
    cli._config(Namespace(config_command="set", key="port", value="31007", more=[
        "public_origins", json.dumps(["https://127.0.0.1:31007", "https://pc.example.ts.net"])]), home)
    assert setup.saved_port(home) == 31007
    assert deployment.load_server_config(home)["public_origins"] == [
        "https://127.0.0.1:31007", "https://pc.example.ts.net"]


def test_address_kind_recognizes_only_this_installations_recorded_listener(monkeypatch):
    from amplifier_web import windows_task_runtime
    monkeypatch.setattr(windows_ports, "state", lambda port: "in-use")
    assert setup.address_kind(23456) == "in-use"
    monkeypatch.setattr(setup.os, "name", "nt")
    spec, record = object(), {"pid": 77}
    monkeypatch.setattr(windows_task_runtime, "observe", lambda value: record if value is spec else None)
    owned_by = {"value": True}
    monkeypatch.setattr(windows_task_runtime, "listener_owned", lambda found, host, port:
                        found is record and (host, port) == ("127.0.0.1", 23456) and owned_by["value"])
    assert setup.address_kind(23456, spec) == "own"
    owned_by["value"] = False  # Another process holds the port: still a conflict.
    assert setup.address_kind(23456, spec) == "in-use"
    monkeypatch.setattr(windows_task_runtime, "observe", lambda value: None)
    assert setup.address_kind(23456, spec) == "in-use"
    monkeypatch.setattr(windows_ports, "state", lambda port: "reserved")
    assert setup.address_kind(23456, spec) == "reserved"


def test_other_installation_task_is_not_replaced(installation):
    owned(installation)
    _, value, calls, run = installation
    value["spec"].python = "/other/python.exe"
    with pytest.raises(RuntimeError, match="another installed"):
        run()
    assert calls == []


@pytest.mark.parametrize("state,instances", [(1, 0), (2, 0), (4, 2), (3, 1)])
def test_ambiguous_task_state_is_not_replayed(installation, state, instances):
    owned(installation, state=state, instances=instances)
    _, _, calls, run = installation
    with pytest.raises(RuntimeError, match="safe start"):
        run()
    assert calls == []


def test_dependency_failure_does_not_register_or_hide_cleanup(installation):
    _, value, calls, run = installation
    value["runtime"]["ok"] = False
    with pytest.raises(RuntimeError, match="no task"):
        run()
    assert not any(action[0] == "service" for action in calls)


@pytest.mark.parametrize("key", ["ok", "responding", "processVerified"])
def test_failed_health_is_not_host_success_or_automatic_force_cleanup(installation, key):
    owned(installation)
    _, value, calls, run = installation
    value["health"][key] = False
    with pytest.raises(RuntimeError, match="not ready"):
        run()
    assert calls == [("service", "status", "--wait", "60", "--json")]


@pytest.mark.parametrize("result,expected", [(6, True), (7, False)])
def test_windowless_notice_uses_owner_dialog_without_shell_or_trust_change(monkeypatch, result, expected):
    import ctypes
    calls = []
    class Dialog:
        def __call__(self, parent, message, title, flags):
            calls.append((parent, message, title, flags))
            return result
    dialog = Dialog()
    monkeypatch.setattr(setup.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(MessageBoxW=dialog), raising=False)
    assert setup.owner_notice("Review the local certificate?", confirm=True) is expected
    assert calls == [(None, "Review the local certificate?", "Amplifier Unified", 0x44)]
    assert dialog.restype is ctypes.c_int


def test_windowless_notice_failure_is_not_approval(monkeypatch):
    import ctypes
    class Dialog:
        def __call__(self, *args):
            return 0
    monkeypatch.setattr(setup.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(MessageBoxW=Dialog()), raising=False)
    with pytest.raises(OSError, match="could not be displayed"):
        setup.owner_notice("Review?", confirm=True)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("exit_code", [0, 2])
async def test_captured_cli_children_never_need_a_windows_console(monkeypatch, native, exit_code):
    from amplifier_web.update_diagnostics import CommandFailure
    calls = []

    class Child:
        returncode = exit_code

        async def communicate(self):
            return b"fixture output", b""

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return Child()

    monkeypatch.setattr(updates, "os", SimpleNamespace(
        name="nt" if native else "posix", environ={}))
    monkeypatch.setattr(updates.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(updates.asyncio, "create_subprocess_exec", spawn)
    if exit_code:
        with pytest.raises(CommandFailure):
            await updates.process("fixture.exe", "status", env={})
    else:
        assert await updates.process("fixture.exe", "status", env={}) == "fixture output"
    args, kwargs = calls[0]
    assert args == ("fixture.exe", "status")
    assert kwargs["stdout"] == kwargs["stderr"] == asyncio.subprocess.PIPE
    assert kwargs["start_new_session"] is not native
    assert kwargs.get("creationflags", 0) == (0x08000000 if native else 0)


@pytest.mark.parametrize("trusted,match,expected", [
    (True, True, True), (False, True, False), (True, False, False)])
def test_browser_trust_requires_windows_roots_and_exact_private_leaf(monkeypatch, trusted, match, expected):
    import hashlib
    from contextlib import contextmanager
    events = []
    leaf = b"synthetic-leaf"
    pin = SimpleNamespace(fingerprint=hashlib.sha256(leaf if match else b"other").digest())
    class Context:
        def load_verify_locations(self, **kwargs):
            events.append(("roots", kwargs))

        @contextmanager
        def wrap_socket(self, transport, *, server_hostname):
            events.append(("tls", server_hostname))
            if not trusted:
                raise setup.ssl.SSLError("Untrusted")
            yield SimpleNamespace(getpeercert=lambda **kw: leaf)
    @contextmanager
    def connect(address, timeout):
        events.append(("connect", address, timeout))
        yield object()
    monkeypatch.setattr(setup.ssl, "SSLContext", lambda protocol: Context())
    monkeypatch.setattr(setup.ssl, "enum_certificates",
                        lambda store: [(b"root", "x509_asn", True)], raising=False)
    monkeypatch.setattr(setup.ssl, "DER_cert_to_PEM_cert", lambda value: "public-root")
    monkeypatch.setattr(setup.socket, "create_connection", connect)
    assert setup.browser_trust_ready(["https://127.0.0.1:8953/api/health"], pin) is expected
    assert events == [("roots", {"cadata": "public-root"}),
                      ("connect", ("127.0.0.1", 8953), 2), ("tls", "127.0.0.1")]
    with pytest.raises(ValueError, match="loopback"):
        setup.browser_trust_ready(["https://example.invalid/api/health"], pin)


@pytest.mark.parametrize("review,recheck,expected", [(False, False, False), (True, False, False), (True, True, True)])
def test_certificate_review_is_public_only_and_never_imports_trust(tmp_path, monkeypatch, review, recheck, expected):
    from contextlib import contextmanager
    from cryptography import x509
    from amplifier_web import tls, windows_files
    checks = iter([False, recheck])
    monkeypatch.setattr(setup, "browser_trust_ready", lambda *a: next(checks))
    monkeypatch.setattr(tls, "exported_ca_bytes", lambda home: b"public-ca")
    monkeypatch.setattr(x509, "load_pem_x509_certificate",
                        lambda content: SimpleNamespace(fingerprint=lambda algorithm: b"\x12" * 32))
    events = []
    @contextmanager
    def directory(path, **kwargs):
        assert path == tmp_path / "export" and kwargs == {"owned_root": tmp_path, "create": True}
        yield SimpleNamespace(publish_private=lambda *a, **kw: events.append(("export", a, kw)))
    monkeypatch.setattr(windows_files, "directory", directory)
    monkeypatch.setattr(setup.os, "startfile", lambda path: events.append(("viewer", path)), raising=False)
    notices = []
    monkeypatch.setattr(setup, "owner_notice", lambda text, **kw:
                        notices.append((text, kw)) or review)
    assert setup.review_browser_trust(tmp_path, ["verified-url"], "private-pin") is expected
    assert events[0] == ("export", ("local-ca.cer", b"public-ca"), {"replace": True})
    assert [row[0] for row in events] == ["export"] + (["viewer"] if review else [])
    assert "Current User" in notices[0][0] and notices[0][1] == {"confirm": True}
    assert "12:12:" in notices[0][0] and "will not import" in notices[0][0]