"""Windows diagnostics must remain passive and cannot attest a registered task."""
import asyncio
import pytest

from amplifier_web import installation_health as health
from amplifier_web import windows_service


def test_windows_service_state_reads_scheduler_without_posix_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(health.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(windows_service, "task_state",
                        lambda root: calls.append(root) or {"state": "running"})
    monkeypatch.setattr(health.subprocess, "run", lambda *a, **kw: pytest.fail("no POSIX command"))
    assert health.service_state(tmp_path) == "running"
    assert calls == [tmp_path]


@pytest.mark.parametrize("state", ["not configured", "running", "starting", "stopped", "disabled"])
def test_windows_health_cannot_be_true_from_task_state_or_probe_untrusted_listener(tmp_path, monkeypatch, state):
    monkeypatch.setattr(health.sys, "platform", "win32")
    monkeypatch.setattr(health, "service_state", lambda root: state)
    monkeypatch.setattr(windows_service, "_read",
                        lambda root: ({"state": 4, "instances": 1}, None))
    from amplifier_web import deployment, update_readiness
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"version": "0.0.0", "revision": None})
    monkeypatch.setattr(deployment, "load_server_config", lambda *a, **kw: pytest.fail("no credential/probe path"))
    value = asyncio.run(health.status(tmp_path))
    assert not value["ok"] and not value["processVerified"]
    assert value["service"] == state
    assert value["responding"] is False and not value["probeAttempted"]
    assert "not ready" in value["message"]


def test_windows_doctor_does_not_try_pam_or_advise_linux_package_manager(tmp_path, monkeypatch):
    from amplifier_web import deployment, host_dependencies, tls
    import builtins
    import shutil
    monkeypatch.setattr(health.sys, "platform", "win32")
    monkeypatch.setattr(host_dependencies, "require_uv", lambda: "uv.exe")
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(deployment, "load_server_config", lambda root: {"tls": {"method": "none"}})
    monkeypatch.setattr(tls, "ssl_context", lambda *args: None)
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "pam":
            pytest.fail("PAM is not Windows sign-in")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    result = health.doctor(tmp_path)
    assert not result["ok"]
    messages = " ".join(row["message"] for row in result["checks"])
    assert "Git for Windows" in messages and "qualification remain pending" in messages
    assert "apt-get" not in messages


@pytest.fixture
def windows_health_chain(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import aiohttp
    from amplifier_web import auth, browser_entry, deployment, windows_task_runtime
    spec = SimpleNamespace(sid="S-1-5-21-100-200-300-1001")
    task = {"state": 4, "instances": 1}
    record = {"pid": 123, "created": "134000000000000001", "phase": "listening", "run": "a" * 32}
    state = {"reads": 0, "listener": True, "record": record, "requests": [],
             "credentialReads": 0, "pin": object(), "ready": True}
    monkeypatch.setattr(windows_service, "_read", lambda home: (dict(task), spec))
    monkeypatch.setattr(windows_task_runtime, "observe", lambda value: state["record"])
    monkeypatch.setattr(windows_task_runtime, "listener_owned", lambda *a: state["listener"])
    monkeypatch.setattr(deployment, "load_server_config", lambda home: {"public_origins": []})
    monkeypatch.setattr(auth, "data_identity", lambda home: "owned-data")
    monkeypatch.setattr(browser_entry, "_owner_probe_targets",
                        lambda *a: (["https://127.0.0.1:8942/api/health"], state["pin"]))
    def token(home, sid):
        state["credentialReads"] += 1
        assert state["listener"] and state["record"]
        return "synthetic-control"
    monkeypatch.setattr(browser_entry, "_control_token", token)

    class Response:
        status = 200
        async def json(self):
            return {"ok": state["ready"], "app": "amplifier-unified",
                    "version": "1.2.3", "revision": None, "dataIdentity": "owned-data"}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def get(self, url, **kwargs):
            assert kwargs["ssl"] is state["pin"] and kwargs["allow_redirects"] is False
            state["requests"].append((url, kwargs))
            return Response()
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    state.update(home=tmp_path, spec=spec, task=task)
    return state


def test_windows_health_requires_full_receipt_listener_and_pinned_authenticated_match(windows_health_chain):
    state = windows_health_chain
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert result["ok"] and result["processVerified"] and result["responding"]
    assert result["pid"] == 123 and result["created"] == "134000000000000001"
    assert state["credentialReads"] == 1 and len(state["requests"]) == 1


@pytest.mark.parametrize("missing", ["record", "listener", "task"])
def test_windows_health_rejects_unverified_process_before_credential_read(windows_health_chain, missing):
    state = windows_health_chain
    if missing == "task":
        state["task"]["instances"] = 2
    else:
        state[missing] = None if missing == "record" else False
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert not result["ok"] and not result["processVerified"]
    assert state["credentialReads"] == 0 and state["requests"] == []


def test_windows_health_does_not_accept_success_response_after_run_changes(windows_health_chain, monkeypatch):
    from amplifier_web import windows_task_runtime
    state = windows_health_chain
    calls = []
    def observe(spec):
        calls.append(None)
        return {**state["record"], "run": ("a" if len(calls) == 1 else "b") * 32}
    monkeypatch.setattr(windows_task_runtime, "observe", observe)
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert not result["ok"] and not result["processVerified"]
    assert len(state["requests"]) == 1


def test_windows_health_bad_pin_does_not_read_or_send_bearer(windows_health_chain, monkeypatch):
    from amplifier_web import browser_entry
    state = windows_health_chain
    def invalid(*args):
        raise browser_entry.BrowserEntryUnavailable("private pin unavailable")
    monkeypatch.setattr(browser_entry, "_owner_probe_targets", invalid)
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "1.2.3", "revision": None}, "running"))
    assert not result["ok"] and not result["probeAttempted"]
    assert state["credentialReads"] == 0 and state["requests"] == []


def test_windows_health_mismatched_version_is_not_ready(windows_health_chain):
    state = windows_health_chain
    result = asyncio.run(health._windows_status(state["home"],
                         {"version": "9.9.9", "revision": None}, "running"))
    assert not result["ok"] and not result["processVerified"]