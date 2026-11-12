import asyncio
import json
from types import SimpleNamespace

import pytest

from amplifier_web import windows_installation as setup
from amplifier_web import windows_service, updates


@pytest.fixture
def installation(tmp_path, monkeypatch):
    calls = []
    state = {"task": {"exists": False}, "spec": None,
             "runtime": {"ok": True, "prepared": True, "connectionRequired": True,
                         "workerReady": False},
             "health": {"ok": True, "responding": True, "processVerified": True}}
    monkeypatch.setattr(windows_service, "_read", lambda home: (state["task"], state["spec"]))
    monkeypatch.setattr(setup, "listener_present", lambda port: False)

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
    assert calls == [
        ("config", "set", "port", "8942"),
        ("config", "set", "bind", "[127.0.0.1]"),
        ("config", "set", "public_origins", "[https://127.0.0.1:8942]"),
        ("setup-tls",), ("runtime", "prepare", "--json"),
        ("service", "install"), ("service", "status", "--wait", "60", "--json")]


def test_native_listener_uses_os_table_not_tcp_timeout(monkeypatch):
    from amplifier_web import windows_task_runtime
    monkeypatch.setattr(setup.os, "name", "nt")
    calls = []
    def owners(host, port):
        calls.append((host, port))
        return {123}
    monkeypatch.setattr(windows_task_runtime, "listener_owners", owners)
    monkeypatch.setattr(setup.socket, "create_connection",
                        lambda *a, **kw: pytest.fail("Native connection timeout cannot establish absence"))
    assert setup.listener_present(8953)
    assert calls == [("127.0.0.1", 8953)]
    monkeypatch.setattr(windows_task_runtime, "listener_owners", lambda *a: set())
    assert setup.listener_present(8953) is False


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
    _, _, calls, run = installation
    run()
    assert calls == [("service", "start"), ("service", "status", "--wait", "60", "--json")]


def test_occupied_address_refused_before_any_mutation(installation, monkeypatch):
    monkeypatch.setattr(setup, "listener_present", lambda port: True)
    _, _, calls, run = installation
    with pytest.raises(RuntimeError, match="occupied"):
        run()
    assert calls == []


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