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