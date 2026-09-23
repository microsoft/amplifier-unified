"""Readiness is a shared, scoped metadata read, never desktop activation."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web import desktop_readiness
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService


HOST = {
    "host": {"id": "app-host", "label": "Serving Mac"},
    "python": {"path": "/app/python", "version": "3.13.7"},
    "platform": "Darwin",
    "computerUsePackage": {"status": "missing", "version": None, "importVerified": False},
}
WORKER = {
    "status": "available",
    "observedAt": 12,
    "host": {"id": "worker-host", "label": "Worker host"},
    "python": {"path": "/worker/python", "version": "3.13.6"},
    "platform": "Linux",
    "computerUsePackage": {"status": "installed", "version": "0.1.0", "importVerified": False},
    "computerControl": {"status": "mounted", "doctorSupported": True},
    "mountedTools": ["desktop"],
}


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_readiness, "environment", lambda: copy.deepcopy(HOST))
    service = AppService(tmp_path / "app", workspace=tmp_path)
    await service.dispatch("session.create", {})
    service.first_session = service.state["selectedSessionId"]
    await service.dispatch("session.create", {})
    service.other_session = service.state["selectedSessionId"]
    service.voice_visual.native = SimpleNamespace(run=AsyncMock(return_value={
        "available": False, "status": "unavailable", "code": "backend_not_installed",
    }))
    service.runtime = SimpleNamespace(
        desktop_readiness=AsyncMock(side_effect=lambda sid: {**copy.deepcopy(WORKER), "sessionId": sid}),
        start=AsyncMock(side_effect=AssertionError("readiness started a worker")),
        prewarm=AsyncMock(side_effect=AssertionError("readiness prewarmed a worker")),
        control=AsyncMock(side_effect=AssertionError("readiness invoked a runtime control")),
        close=AsyncMock(),
    )
    yield service
    service.voice_service = None
    await service.close()


async def check(app, sid=None, **kwargs):
    return (await app.dispatch("desktop.readiness", {"sessionId": sid} if sid else {}, **kwargs))["result"]


async def test_native_unavailable_reason_is_preserved_but_bounded(app):
    app.voice_visual.native.run.return_value = {"available": False, "status": "unavailable", "reason": "zero active displays " + "x" * 500}
    native = (await check(app))["nativeObservation"]
    assert native["reason"].startswith("zero active displays ")
    assert len(native["reason"]) == 300


def test_unavailable_tool_explains_the_actual_backend_failure():
    tool = SimpleNamespace(description="Configured SSH target is unreachable. " + "x" * 2000)
    controls = SimpleNamespace(catalog_revision="test", coordinator=SimpleNamespace(get=lambda _: {"computer_use_unavailable": tool}), configuration=lambda: {"modules": []})
    report = desktop_readiness.worker_report(controls)
    assert report["computerControl"]["detail"].startswith("Configured SSH target is unreachable.")
    assert len(report["computerControl"]["detail"]) == 1600


async def connect_call(app, sid=None, call_id="call-one"):
    sid = sid or app.first_session
    app.clients.attach("owning-browser")
    call = SimpleNamespace(id=call_id, session_id=sid, client_id="owning-browser", closed=False, closing=False)
    app.voice_service = SimpleNamespace(call=call)
    await app.set_voice_status({"id": call.id, "sessionId": sid, "status": "connected"})
    return {"sessionId": sid, "callId": call.id}


async def grant_browser(app, target, kind="window", label="Synthetic selected window"):
    with app.clients.bind("owning-browser"):
        return await app.voice_visual.grant_source({**target, "source": {
            "kind": kind, "label": label, "url": "https://private.invalid", "account": "not-exposed",
        }})


async def test_ui_read_uses_selected_or_explicit_session_and_preserves_state(app):
    before = copy.deepcopy(app.state)
    selected = await check(app)
    explicit = await check(app, app.first_session)
    assert selected["sessionId"] == app.other_session
    assert explicit["sessionId"] == app.first_session
    assert app.state == before
    assert app.voice_visual.grant is None
    assert app.voice_visual.pending is None
    assert app.voice_visual.receipts == {}
    assert app.voice_visual.native.run.await_args_list[0].args == ("status",)
    assert all(call.args == ("status",) for call in app.voice_visual.native.run.await_args_list)
    app.runtime.start.assert_not_awaited()
    app.runtime.prewarm.assert_not_awaited()
    app.runtime.control.assert_not_awaited()


async def test_agent_bridge_and_direct_dispatch_use_calling_conversation(app):
    ui = await check(app, app.first_session)
    bridge = await app.app_bridge("dispatch", {"action": "desktop.readiness", "args": {}}, app.first_session)
    direct = await check(app, origin="agent", caller_session_id=app.first_session)
    for result in (bridge["result"], direct):
        assert result["sessionId"] == app.first_session
        assert result["worker"]["sessionId"] == app.first_session
        assert result["host"] == ui["host"]
        assert result["nativeObservation"] == ui["nativeObservation"]
        assert result["nextActions"] == ui["nextActions"]
    assert app.state["selectedSessionId"] == app.other_session


@pytest.mark.parametrize("entry", ["bridge", "direct"])
async def test_agent_cannot_inspect_another_conversation(app, entry):
    with pytest.raises(AppError, match="calling conversation"):
        if entry == "bridge":
            await app.app_bridge("dispatch", {"action": "desktop.readiness", "args": {
                "sessionId": app.other_session,
            }}, app.first_session)
        else:
            await check(app, app.other_session, origin="agent", caller_session_id=app.first_session)
    app.voice_visual.native.run.assert_not_awaited()
    app.runtime.desktop_readiness.assert_not_awaited()


@pytest.mark.parametrize("sid", [None, "explicit-session"])
async def test_direct_agent_dispatch_requires_caller_provenance(app, sid):
    with pytest.raises(AppError, match="calling conversation"):
        await check(app, sid, origin="agent")
    app.voice_visual.native.run.assert_not_awaited()


async def test_stale_revision_and_unknown_session_fail_before_native_check(app):
    with pytest.raises(AppError, match="app changed"):
        await check(app, expected_revision=app.state["revision"] - 1)
    with pytest.raises(AppError):
        await check(app, "missing-session")
    app.voice_visual.native.run.assert_not_awaited()
    app.runtime.desktop_readiness.assert_not_awaited()


async def test_host_and_worker_interpreters_and_snapshot_age_stay_distinct(app):
    result = await check(app, app.first_session)
    assert result["host"]["host"] == HOST["host"]
    assert result["host"]["python"]["path"] == "/app/python"
    assert result["host"]["computerUsePackage"]["status"] == "missing"
    assert result["host"]["instanceId"] == app.instance_id
    assert result["worker"]["python"]["path"] == "/worker/python"
    assert result["worker"]["computerUsePackage"]["status"] == "installed"
    assert result["worker"]["observedAt"] == 12
    assert result["observedAt"] > result["worker"]["observedAt"]
    assert result["nativeObservation"]["code"] == "backend_not_installed"
    assert "serving app environment" in result["nativeObservation"]["nextStep"]


async def test_native_preflight_drops_content_and_never_grants_or_captures(app):
    app.voice_visual.native.run.return_value = {
        "available": True, "status": "available", "permission": "granted", "backend": "synthetic",
        "image": "private-pixels", "window": {"title": "private-window"},
        "account": "private-account", "url": "https://private.invalid",
    }
    result = await check(app)
    assert result["nativeObservation"]["available"] is True
    assert result["voiceObservation"]["status"] == "not_shared"
    assert "private" not in json.dumps(result)
    app.voice_visual.native.run.assert_awaited_once_with("status")
    assert app.voice_visual.grant is None and app.voice_visual.pending is None


@pytest.mark.parametrize(("code", "instruction"), [
    ("backend_not_installed", "native-desktop"),
    ("permission_required", "Screen Recording"),
    ("permission_unknown", "Screen Recording"),
    ("unsupported", "browser device"),
])
async def test_missing_permission_and_unsupported_states_have_specific_next_steps(app, code, instruction):
    app.voice_visual.native.run.return_value = {"available": False, "status": "unavailable", "code": code}
    result = await check(app)
    native = result["nativeObservation"]
    assert native["code"] == code
    assert instruction in native["nextStep"]
    assert native["available"] is False
    if code.startswith("permission_"):
        assert "Accessibility permission is not required" in native["nextStep"]


@pytest.mark.parametrize("failure", [TimeoutError, ValueError, OSError])
async def test_native_preflight_failure_is_bounded_and_worker_report_survives(app, failure):
    app.voice_visual.native.run.side_effect = failure("private helper output")
    result = await check(app)
    assert result["nativeObservation"]["code"] == "native_status_failed"
    assert result["nativeObservation"]["available"] is False
    assert result["worker"]["status"] == "available"
    assert "private helper output" not in json.dumps(result)


async def test_browser_source_is_call_scoped_and_exposes_only_transport_context(app):
    target = await connect_call(app)
    grant = await grant_browser(app, target, kind="browser", label="Synthetic tab")
    result = await check(app, target["sessionId"])
    visual = result["voiceObservation"]
    assert visual["status"] == "source_selected"
    assert visual["callId"] == target["callId"]
    assert visual["source"] == {"kind": "browser", "label": "Synthetic tab", "reportedBy": "browser"}
    assert visual["expiresAt"] == grant["expiresAt"]
    assert "not exposed" in visual["browserContext"]
    other = (await check(app, app.other_session))["voiceObservation"]
    assert other["status"] == "not_shared" and other["source"] is None and other["callId"] is None
    assert app.voice_visual.grant == grant
    assert app.voice_visual.receipts == {}
    assert not app.clients.records["owning-browser"].get("deviceCommands")


@pytest.mark.parametrize("transition", ["expired", "ended", "changed-call", "changed-session", "revoked"])
async def test_old_voice_grant_is_not_reported_after_scope_changes(app, transition):
    target = await connect_call(app)
    await grant_browser(app, target)
    if transition == "expired":
        app.voice_visual.grant["expiresAt"] = 0
    elif transition == "ended":
        await app.set_voice_status({"status": "ending"})
    elif transition == "changed-call":
        await connect_call(app, call_id="call-two")
    elif transition == "changed-session":
        await connect_call(app, sid=app.other_session, call_id="call-two")
    else:
        app.voice_visual.revoke()
    visual = (await check(app, target["sessionId"]))["voiceObservation"]
    assert visual["status"] == "not_shared"
    assert visual["source"] is None and visual["expiresAt"] is None
    assert app.voice_visual.pending is None
    assert app.voice_visual.receipts == {}


async def test_source_replacement_reports_new_selection_without_capture(app):
    target = await connect_call(app)
    await grant_browser(app, target, label="First window")
    await grant_browser(app, target, kind="monitor", label="New display")
    visual = (await check(app, target["sessionId"]))["voiceObservation"]
    assert visual["source"]["kind"] == "monitor"
    assert visual["source"]["label"] == "New display"
    assert app.voice_visual.pending is None and app.voice_visual.receipts == {}


@pytest.mark.parametrize("change", [None, "host", "instance"])
async def test_native_selection_is_bound_to_exact_host_and_app_instance(app, monkeypatch, change):
    monkeypatch.setattr("amplifier_web.host_identity.local_host_identity", lambda: copy.deepcopy(HOST["host"]))
    app.voice_visual.native.run.return_value = {"available": True, "status": "ready", "permission": "granted"}
    target = await connect_call(app)
    with app.clients.bind("owning-browser"):
        await app.voice_visual.grant_source({**target, "source": {
            "kind": "native-foreground", "hostId": "app-host", "hostInstanceId": app.instance_id,
        }})
    if change == "host":
        monkeypatch.setattr("amplifier_web.host_identity.local_host_identity", lambda: {"id": "other-host"})
    elif change == "instance":
        app.instance_id = "replacement-instance"
    visual = (await check(app, target["sessionId"]))["voiceObservation"]
    if change:
        assert visual["status"] == "not_shared" and visual["source"] is None
    else:
        assert visual["status"] == "source_selected"
        assert visual["source"]["kind"] == "native-foreground"
        assert visual["source"]["host"] == HOST["host"]
        assert visual["source"]["hostInstanceId"] == app.instance_id
        assert "named host and app instance" in visual["browserContext"]
        assert "Capture screen" in visual["nextStep"]
        assert "browser picker" not in visual["nextStep"]
    assert all(call.args == ("status",) for call in app.voice_visual.native.run.await_args_list)
    assert app.voice_visual.pending is None and app.voice_visual.receipts == {}


async def test_no_selected_session_can_check_host_without_creating_worker(app):
    app.state["selectedSessionId"] = None
    result = await check(app)
    assert result["sessionId"] is None
    assert result["worker"]["status"] == "unavailable"
    app.runtime.desktop_readiness.assert_not_awaited()
    app.runtime.start.assert_not_awaited()


def controls_with(tools):
    return SimpleNamespace(
        configuration=Mock(return_value={"modules": [
            {"section": "tools", "module": "tool-computer-use", "id": "desktop-module", "enabled": True,
             "config": {"secret": "private-config"}},
            {"section": "tools", "module": "tool-browser", "id": "browser-module", "enabled": False},
            {"section": "providers", "module": "provider-fixture", "id": "provider", "enabled": True},
        ]}),
        coordinator={"tools": tools},
        catalog_revision=7,
        dispatch=AsyncMock(side_effect=AssertionError("metadata dispatched a tool")),
    )


def desktop_tool(actions):
    return SimpleNamespace(
        input_schema={"properties": {"action": {"enum": actions}}},
        execute=AsyncMock(side_effect=AssertionError("metadata executed desktop")),
    )


@pytest.mark.parametrize(("mounted", "actions", "status", "doctor"), [
    ("none", [], "not_mounted", False),
    ("unavailable", [], "unavailable", False),
    ("desktop", ["screenshot"], "mounted", False),
    ("desktop", ["doctor", "screenshot"], "mounted", True),
])
def test_worker_reports_actual_tools_and_doctor_enum_without_execution(monkeypatch, mounted, actions, status, doctor):
    monkeypatch.setattr(desktop_readiness, "environment", lambda: copy.deepcopy(WORKER))
    tool = desktop_tool(actions)
    tools = {} if mounted == "none" else {"computer_use_unavailable" if mounted == "unavailable" else "desktop": tool}
    controls = controls_with(tools)
    result = desktop_readiness.worker_report(controls)
    assert result["computerControl"]["status"] == status
    assert result["computerControl"]["doctorSupported"] is doctor
    assert result["mountedTools"] == sorted(tools)
    assert result["catalogRevision"] == 7
    assert result["modules"] == [
        {"module": "tool-computer-use", "id": "desktop-module", "enabled": True},
        {"module": "tool-browser", "id": "browser-module", "enabled": False},
    ]
    assert result["browserContext"]["status"] == "not_exposed"
    assert "private-config" not in json.dumps(result)
    tool.execute.assert_not_awaited()
    controls.dispatch.assert_not_awaited()


@pytest.mark.parametrize("mounted", [False, True])
async def test_worker_metadata_command_never_acquires_or_unparks(monkeypatch, mounted):
    monkeypatch.setattr(desktop_readiness, "environment", lambda: copy.deepcopy(HOST))
    replies = []
    monkeypatch.setattr("amplifier_web.runtime_worker.publish", replies.append)
    worker = Worker()
    worker.parked = True
    worker.ownership.yielding = True
    worker.acquire_for_mutation = AsyncMock(side_effect=AssertionError("metadata acquired ownership"))
    worker.bind_activation = Mock(side_effect=AssertionError("metadata activated a session"))
    worker.start = AsyncMock(side_effect=AssertionError("metadata started a session"))
    tool = desktop_tool(["doctor"])
    worker.controls = controls_with({"desktop": tool}) if mounted else None
    await worker.command({"op": "desktop.readiness", "id": "readiness-one"})
    assert len(replies) == 1 and "error" not in replies[0]
    assert replies[0]["result"]["status"] == ("available" if mounted else "unavailable")
    assert worker.parked is True
    assert worker.shared_handle is None and worker.activation is None
    worker.acquire_for_mutation.assert_not_awaited()
    worker.bind_activation.assert_not_called()
    worker.start.assert_not_awaited()
    tool.execute.assert_not_awaited()


def runtime_row(state="ready"):
    ready = asyncio.get_running_loop().create_future()
    if state == "cancelled":
        ready.cancel()
    elif state == "failed":
        ready.set_exception(RuntimeError("load failed"))
    elif state != "starting":
        ready.set_result({})
    return {"process": SimpleNamespace(returncode=0 if state == "exited" else None),
            "ready": ready, "parked": True, "parked_at": 12, "inflight": set(), "pending": {}}


@pytest.mark.parametrize("state", ["missing", "retired", "starting", "cancelled", "failed", "exited"])
async def test_manager_does_not_start_unready_or_retired_workers(state):
    manager = RuntimeManager()
    manager._start_locked = AsyncMock(side_effect=AssertionError("readiness started a worker"))
    manager._request = AsyncMock(side_effect=AssertionError("unready worker was queried"))
    if state == "retired":
        manager._retired["session"] = ({"id": "session"}, AsyncMock())
    elif state != "missing":
        manager.workers["session"] = runtime_row(state)
    result = await manager.desktop_readiness("session")
    assert result["status"] == "unavailable" and result["sessionId"] == "session"
    manager._start_locked.assert_not_awaited()
    manager._request.assert_not_awaited()


async def test_manager_metadata_request_keeps_parked_state_and_execution_fence():
    manager = RuntimeManager()
    row = runtime_row()
    manager.workers["session"] = row
    fence = {"fenced": True, "hostMatches": False, "revision": 4, "directory": "/other/host"}
    manager.bind_execution_state(lambda sid: fence)
    manager._start_locked = AsyncMock(side_effect=AssertionError("readiness started a worker"))
    sent = []

    async def write(worker, data):
        sent.append(data)
        worker["inflight"].discard(data["id"])
        worker["pending"][data["id"]].set_result(copy.deepcopy(WORKER))

    manager._write = write
    result = await manager.desktop_readiness("session")
    assert result["status"] == "available" and result["observedAt"] == 12
    assert [data["op"] for data in sent] == ["desktop.readiness"]
    assert row["parked"] is True and row["parked_at"] == 12
    assert fence["fenced"] is True
    assert row["pending"] == {} and row["inflight"] == set()
    manager._start_locked.assert_not_awaited()


async def test_manager_disappearing_worker_is_unknown_and_never_restarted():
    manager = RuntimeManager()
    manager.workers["session"] = runtime_row()
    manager._retired["session"] = ({"id": "session"}, AsyncMock())
    manager._start_locked = AsyncMock(side_effect=AssertionError("readiness revived a worker"))
    original_admit = manager._admit

    async def vanished(sid, op, args):
        manager.workers.pop(sid)
        return await original_admit(sid, op, args)

    manager._admit = vanished
    result = await manager.desktop_readiness("session")
    assert result["status"] == "unknown"
    assert manager.workers == {}
    manager._start_locked.assert_not_awaited()


@pytest.mark.parametrize("version", [None, "0.1.0"])
def test_environment_identifies_owning_interpreter_without_importing_optional_library(monkeypatch, version):
    def distribution(name):
        assert name == "amplifier-module-tool-computer-use"
        if version is None:
            raise desktop_readiness.metadata.PackageNotFoundError(name)
        return version

    monkeypatch.setattr(desktop_readiness.metadata, "version", distribution)
    monkeypatch.setattr(desktop_readiness, "local_host_identity", lambda: {"id": "synthetic-host"})
    monkeypatch.setattr(desktop_readiness.sys, "executable", "/exact/serving/python")
    result = desktop_readiness.environment()
    assert result["python"]["path"] == "/exact/serving/python"
    assert result["host"] == {"id": "synthetic-host"}
    assert result["computerUsePackage"] == {
        "status": "installed" if version else "missing", "version": version, "importVerified": False,
    }


async def test_readiness_reports_own_conversation_source_without_voice(app):
    app.clients.attach('sharing-browser')
    app.clients.attach('other-browser')
    with app.clients.bind('sharing-browser'):
        sid = app.state['selectedSessionId']
        visual = app.computer_visual.for_client(sid)
        await visual.grant_source({'sessionId':sid,'source':{'kind':'window','label':'Shared text window'}})
        result = await check(app, sid)
        assert result['voiceObservation']['status'] == 'source_selected'
        assert result['voiceObservation']['source']['label'] == 'Shared text window'
        assert 'Connect a voice call' not in result['voiceObservation']['nextStep']
    with app.clients.bind('other-browser'):
        result = await check(app, sid)
        assert result['voiceObservation']['status'] == 'not_shared'
        assert result['voiceObservation']['source'] is None
