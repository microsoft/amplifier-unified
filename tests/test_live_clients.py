import asyncio
import json

import pytest

from amplifier_web.server import create_app
from amplifier_web.service import AppService, AppError
from test_service import Runtime


@pytest.fixture
async def live(tmp_path):
    service = AppService(tmp_path / "app", Runtime(), workspace=tmp_path)
    await service.dispatch("session.create", {"title": "First"})
    first = service.state["selectedSessionId"]
    await service.dispatch("session.create", {"title": "Second"})
    second = service.state["selectedSessionId"]
    for client in ("browser-a", "browser-b"):
        service.clients.attach(client)
    yield service, first, second
    await service.close()


async def command(service, client, action, args=None, **kwargs):
    with service.clients.bind(client):
        return await service.dispatch(action, args, **kwargs)


def snapshot(service, client):
    with service.clients.bind(client):
        return service.browser_state()

async def test_pre_conversation_drafts_are_private_and_survive_reload_and_restart(tmp_path):
    service = AppService(tmp_path / "app", Runtime(), workspace=tmp_path)
    for identity in ("browser-a", "browser-b"):
        service.clients.attach(identity)
    for identity, draft in (("browser-a", "Private first draft"), ("browser-b", "Other draft")):
        await command(service, identity, "view.update", {"sessionId": None, "patch": {"draft": draft}})
    assert snapshot(service, "browser-a")["sessions"] == []
    assert snapshot(service, "browser-a")["view"]["draft"] == "Private first draft"
    assert snapshot(service, "browser-b")["view"]["draft"] == "Other draft"
    service.clients.attach("reloaded", resume="browser-a")
    assert snapshot(service, "reloaded")["view"]["draft"] == "Private first draft"
    await service.close()
    restored = AppService(tmp_path / "app", Runtime(), workspace=tmp_path)
    try:
        assert snapshot(restored, "reloaded")["view"]["draft"] == "Private first draft"
        assert snapshot(restored, "browser-b")["view"]["draft"] == "Other draft"
        await command(restored, "reloaded", "session.create", {})
        selected = snapshot(restored, "reloaded")
        sid = selected["selectedSessionId"]
        assert selected["view"]["draft"] == "Private first draft"
        assert restored.clients.records["reloaded"]["drafts"].get("", "") == ""
        # A delayed empty-composer autosave must not overwrite the new chat.
        await command(restored, "reloaded", "view.update", {"sessionId": None, "patch": {"draft": "Late empty draft"}})
        assert snapshot(restored, "reloaded")["view"]["draft"] == "Private first draft"
        assert restored.clients.records["reloaded"]["drafts"][sid] == "Private first draft"
        assert snapshot(restored, "browser-b")["selectedSessionId"] is None
        assert snapshot(restored, "browser-b")["view"]["draft"] == "Other draft"
        assert restored.runtime.sent == []
    finally:
        await restored.close()


async def test_views_drafts_selection_and_device_effects_are_independent(live):
    service, first, second = live
    await command(service, "browser-a", "session.select", {"id": first})
    await command(service, "browser-a", "view.update", {"patch": {"draft": "Private A", "panel": "settings", "navWidth": 380}})
    await command(service, "browser-b", "view.update", {"patch": {"draft": "Private B", "panel": "appearance"}})
    a, b = snapshot(service, "browser-a"), snapshot(service, "browser-b")
    assert a["selectedSessionId"] == first and b["selectedSessionId"] == second
    assert a["view"]["draft"] == "Private A" and b["view"]["draft"] == "Private B"
    assert a["view"]["panel"] == "settings" and b["view"]["panel"] == "appearance"
    assert "Private A" not in json.dumps(b) and "Private B" not in json.dumps(a)
    await command(service, "browser-a", "session.select", {"id": second})
    assert snapshot(service, "browser-a")["view"]["draft"] == ""
    assert service._session(first).get("draft", "") == ""
    # A delayed save belongs to its original conversation, not current selection.
    await command(service, "browser-a", "view.update", {"sessionId": first, "patch": {"draft": "Later A"}})
    assert snapshot(service, "browser-a")["view"]["draft"] == ""
    await command(service, "browser-a", "session.select", {"id": first})
    assert snapshot(service, "browser-a")["view"]["draft"] == "Later A"
    await command(service, "browser-a", "notification.request")
    assert snapshot(service, "browser-a")["deviceCommands"][-1]["clientId"] == "browser-a"
    assert snapshot(service, "browser-b")["deviceCommands"] == []


async def test_both_clients_receive_shared_progress_with_their_own_view(live):
    service, first, _ = live
    for client in ("browser-a", "browser-b"):
        await command(service, client, "session.select", {"id": first})
    with service.clients.bind("browser-a"):
        a = service.subscribe()
    with service.clients.bind("browser-b"):
        b = service.subscribe()
    try:
        await command(service, "browser-a", "view.update", {"patch": {"draft": "A's next message"}})
        await command(service, "browser-b", "conversation.send", {"sessionId": first, "text": "Shared input"}, command_id="send-once")
        await command(service, "browser-b", "conversation.send", {"sessionId": first, "text": "Shared input"}, command_id="send-once")
        assert len(service.runtime.sent) == 1
        await service.on_runtime_event("assistant.delta", {"sessionId": first, "text": "Partial response"})
        await service._flush_pending_progress()
        for queue, client in ((a, "browser-a"), (b, "browser-b")):
            while not queue.empty():
                latest = queue.get_nowait()
            session = next(s for s in latest["sessions"] if s["id"] == first)
            assert session["streaming"] == "Partial response"
            assert sum(m["text"] == "Shared input" for m in session["messages"]) == 1
            assert latest["client"]["id"] == client
        assert snapshot(service, "browser-a")["view"]["draft"] == "A's next message"
    finally:
        service.unsubscribe(a)
        service.unsubscribe(b)
    assert service.runtime.stopped == []


async def test_explicit_stop_does_not_follow_another_client_selection(live):
    service, first, second = live
    await command(service, "browser-a", "session.select", {"id": second})
    await command(service, "browser-b", "session.select", {"id": first})
    await command(service, "browser-a", "conversation.stop", {"sessionId": first})
    await asyncio.gather(*service.tasks)
    assert service.runtime.stopped == [first]
    assert snapshot(service, "browser-a")["selectedSessionId"] == second


async def test_reload_and_duplicate_tab_clone_presentation_without_sharing_it(live):
    service, first, _ = live
    await command(service, "browser-a", "session.select", {"id": first})
    await command(service, "browser-a", "view.update", {"patch": {"draft": "Saved draft"}})
    service.clients.attach("reloaded", resume="browser-a")
    service.clients.attach("duplicated", resume="browser-a")
    assert snapshot(service, "reloaded")["view"]["draft"] == "Saved draft"
    await command(service, "duplicated", "view.update", {"patch": {"draft": "Independent"}})
    assert snapshot(service, "reloaded")["view"]["draft"] == "Saved draft"
    assert snapshot(service, "browser-a")["view"]["draft"] == "Saved draft"


async def test_client_draft_and_command_receipt_survive_host_restart(tmp_path):
    service = AppService(tmp_path / "app", Runtime(), workspace=tmp_path)
    await service.dispatch("session.create", {})
    first = service.state["selectedSessionId"]
    service.clients.attach("terminal", kind="tui")
    await command(service, "terminal", "conversation.send", {"sessionId": first, "text": "Once"}, command_id="durable-input")
    await command(service, "terminal", "view.update", {"patch": {"draft": "Keep for later"}})
    instance = service.instance_id
    await service.close()
    restored = AppService(tmp_path / "app", Runtime(), workspace=tmp_path)
    try:
        result = await command(restored, "terminal", "conversation.send", {"sessionId": first, "text": "Once"}, command_id="durable-input")
        assert result["duplicate"] and restored.runtime.sent == []
        assert snapshot(restored, "terminal")["view"]["draft"] == "Keep for later"
        assert restored.instance_id != instance
    finally:
        await restored.close()


async def read_event(response):
    while True:
        line = await asyncio.wait_for(response.content.readline(), 2)
        if line.startswith(b"data: "):
            return json.loads(line[6:])


async def test_http_session_contract_sse_reconnect_and_command_target(authenticated_client, tmp_path):
    runtime = Runtime()
    app = await create_app(tmp_path / "app", workspace=tmp_path, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app["service"]
    await service.dispatch("session.create", {})
    sid = service.state["selectedSessionId"]
    response = await client.post("/api/clients/attach", json={"clientId": "tui", "kind": "tui", "protocolVersion": 1})
    assert response.status == 200
    headers = {"X-Amplifier-Client": "tui"}
    endpoint = f"/api/sessions/{sid}"
    stream = await client.get(endpoint + "/events?clientId=tui")
    initial = await read_event(stream)
    stream.close()
    assert initial["session"]["id"] == sid
    payload = {"id": "one-input", "action": "conversation.send", "args": {"text": "While detached"}}
    response = await client.post(endpoint + "/commands", json=payload, headers=headers)
    assert response.status == 200
    response = await client.post(endpoint + "/commands", json=payload, headers=headers)
    assert (await response.json())["duplicate"]
    assert len(runtime.sent) == 1 and runtime.stopped == []
    stream = await client.get(endpoint + "/events?clientId=tui", headers={"Last-Event-ID": "old-instance:0"})
    latest = await read_event(stream)
    stream.close()
    assert any(m["text"] == "While detached" for m in latest["session"]["messages"])
    response = await client.post(endpoint + "/commands", json={**payload, "id": "bad-target", "args": {"text": "Wrong", "sessionId": "elsewhere"}}, headers=headers)
    assert response.status == 400 and len(runtime.sent) == 1
    response = await client.post(endpoint + "/commands", json={"action": "conversation.send", "args": {"text": "Missing ID"}}, headers=headers)
    assert response.status == 400


async def test_client_state_is_not_an_authentication_bypass(authenticated_client, tmp_path):
    app = await create_app(tmp_path / "app", workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    client.session.headers.pop("Authorization")
    response = await client.post("/api/clients/attach", json={"clientId": "not-authenticated"})
    assert response.status in {401, 403}
    assert "not-authenticated" not in app["service"].clients.records


async def test_tui_can_prepare_explicit_chat_without_changing_selection_or_submitting(authenticated_client, tmp_path):
    runtime = Runtime()
    warmed = []
    arrived = asyncio.Event()
    async def prewarm(session, emit):
        warmed.append(session['id'])
        await emit('runtime.warmth', {'sessionId': session['id'], 'status': 'warm'})
        arrived.set()
    runtime.prewarm = prewarm
    app = await create_app(tmp_path/'app', workspace=tmp_path, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    await service.dispatch('session.create', {})
    target = service.state['selectedSessionId']
    await service.dispatch('session.create', {})
    selected = service.state['selectedSessionId']
    await client.post('/api/clients/attach', json={'clientId': 'terminal', 'kind': 'tui'})
    headers = {'X-Amplifier-Client': 'terminal'}
    endpoint = f'/api/sessions/{target}/commands'
    payload = {'id': 'prepare-once', 'action': 'session.warm', 'args': {}}
    response = await client.post(endpoint, json=payload, headers=headers)
    assert response.status == 200 and (await response.json())['accepted']
    await asyncio.wait_for(arrived.wait(), 2)
    response = await client.post(endpoint, json=payload, headers=headers)
    assert (await response.json())['duplicate']
    response = await client.get('/api/state', headers=headers)
    assert (await response.json())['selectedSessionId'] == selected
    assert warmed == [target] and not runtime.sent
    assert service._session(target)['preparation']['status'] == 'warm'


async def test_delayed_send_survives_navigation_disconnect_and_late_drafts(live):
    service, first, second = live
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.runtime.send
    async def delayed(*args):
        entered.set()
        await release.wait()
        await original(*args)
    service.runtime.send = delayed
    await command(service, "browser-a", "session.select", {"id": first})
    await command(service, "browser-a", "view.update", {"patch": {"draft": "Sent from first"}})
    task = asyncio.create_task(command(service, "browser-a", "conversation.send",
        {"text": "Sent from first"}, command_id="slow-input"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await command(service, "browser-a", "session.select", {"id": second})
        await command(service, "browser-a", "view.update", {"sessionId": second, "patch": {"draft": "Keep second"}})
        await command(service, "browser-b", "session.select", {"id": first})
        await command(service, "browser-b", "view.update", {"patch": {"draft": "Keep other device"}})
    finally:
        release.set()
        await task
    assert service.runtime.sent[0][0] == first
    assert snapshot(service, "browser-a")["view"]["draft"] == "Keep second"
    await command(service, "browser-a", "session.select", {"id": first})
    assert snapshot(service, "browser-a")["view"]["draft"] == ""
    assert snapshot(service, "browser-b")["view"]["draft"] == "Keep other device"


async def test_concurrent_approval_answers_accept_only_one(live):
    service, first, _ = live
    accepted = []
    async def approve(*args):
        accepted.append(args)
    service.runtime.approval = approve
    await service.on_runtime_event("approval.requested", {"sessionId": first, "id": "permission", "prompt": "Allow tool?"})
    outcomes = await asyncio.gather(*[
        command(service, client, "approval.respond", {"sessionId": first, "id": "permission", "decision": decision},
                command_id="answer-" + client)
        for client, decision in (("browser-a", "allow"), ("browser-b", "deny"))
    ], return_exceptions=True)
    await asyncio.gather(*service.tasks)
    assert sum(isinstance(result, AppError) and result.status == 409 for result in outcomes) == 1
    assert len(accepted) == 1
    for client in ("browser-a", "browser-b"):
        with service.clients.bind(client):
            state = service.browser_state(session_id=first)
        assert next(s for s in state["sessions"] if s["id"] == first)["approvals"][0]["status"] == accepted[0][2]


async def test_attachments_and_canvas_controls_stay_with_client(live):
    service, first, _ = live
    for client in ("browser-a", "browser-b"):
        await command(service, client, "session.select", {"id": first})
    result = await command(service, "browser-a", "attachment.add",
        {"sessionId": first, "name": "private.txt", "base64": "cHJpdmF0ZQ=="})
    attached = next(s for s in result["state"]["sessions"] if s["id"] == first)["draftAttachments"][0]["id"]
    assert next(s for s in snapshot(service, "browser-b")["sessions"] if s["id"] == first)["draftAttachments"] == []
    with pytest.raises(AppError):
        await command(service, "browser-b", "conversation.send", {"sessionId": first, "text": "Other", "attachmentIds": [attached]})
    await command(service, "browser-a", "canvas.show", {"kind": "text", "title": "Shared artifact", "content": "Shared contents"})
    identity = snapshot(service, "browser-a")["canvas"]["id"]
    await command(service, "browser-b", "canvas.select", {"id": identity})
    await command(service, "browser-a", "canvas.view", {"id": identity, "patch": {"reload": 77}})
    assert snapshot(service, "browser-b")["canvas"].get("view", {}).get("reload") != 77
    await command(service, "browser-a", "canvas.tabClose", {"id": identity})
    a, b = snapshot(service, "browser-a"), snapshot(service, "browser-b")
    assert not next(row for row in a["canvasArtifacts"] if row["id"] == identity)["tabOpen"]
    assert next(row for row in b["canvasArtifacts"] if row["id"] == identity)["tabOpen"]
    assert b["canvas"]["id"] == identity


async def test_shell_shares_client_identity_and_does_not_pollute_session_stream(live):
    service, first, second = live
    await command(service, "browser-a", "session.select", {"id": first})
    await command(service, "browser-b", "session.select", {"id": second})
    for client, selected in (("browser-a", first), ("browser-b", second)):
        result = await command(service, client, "shell.query", {"clientId": client, "instanceId": "chats"})
        assert result["result"]["selectedSessionId"] == selected
    with service.clients.bind("browser-a"):
        stream = service.subscribe(session_id=first)
        shell_stream = service.subscribe()
    try:
        await command(service, "browser-a", "shell.view.update",
            {"clientId": "browser-a", "instanceId": "chats", "patch": {"navFilter": "First"}})
        assert stream.empty()
        assert shell_stream.get_nowait()["shellClientId"] == "browser-a"
        service.clients.attach("reloaded-shell", resume="browser-a")
        result = await command(service, "reloaded-shell", "shell.query", {"clientId": "reloaded-shell", "instanceId": "chats"})
        assert result["result"]["view"]["navFilter"] == "First"
        with pytest.raises(AppError, match="different client"):
            await command(service, "browser-b", "shell.query", {"clientId": "browser-a", "instanceId": "chats"})
    finally:
        service.unsubscribe(stream)
        service.unsubscribe(shell_stream)


async def test_python_terminal_adapter_uses_real_http_without_owning_runtime(authenticated_client, tmp_path):
    from amplifier_web.session_client import SessionClient
    runtime = Runtime()
    app = await create_app(tmp_path / "app", workspace=tmp_path, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    transport = await authenticated_client(app)
    async with SessionClient(str(transport.make_url("")).rstrip("/"), app["control_token"], "python-tui") as client:
        created = await client.create_session({"title": "Terminal session"}, command_id="create-terminal")
        identity = created["state"]["selectedSessionId"]
        assert any(row["id"] == identity for row in (await client.sessions())["items"])
        stream = client.snapshots(identity, reconnect=False)
        assert (await anext(stream))["session"]["id"] == identity
        await stream.aclose()
        for _ in range(2):
            await client.command(identity, "conversation.send", {"text": "Terminal input"}, command_id="terminal-once")
        await app["service"].on_runtime_event("assistant.message", {"sessionId": identity, "text": "Large 雪" * 40000})
        stream = client.snapshots(identity, reconnect=False)
        large = await anext(stream)
        await stream.aclose()
        assert large["session"]["messages"][-1]["text"] == "Large 雪" * 40000
        current = await client.snapshot(identity)
        assert sum(message["text"] == "Terminal input" for message in current["session"]["messages"]) == 1
    assert len(runtime.sent) == 1 and runtime.stopped == []


async def test_shared_configuration_refresh_invalidates_all_client_views(live):
    from amplifier_web.preferences import SettingsStore
    service, first, _ = live
    for client in ("browser-a", "browser-b"):
        await command(service, client, "session.select", {"id": first})
        await command(service, client, "view.update", {"patch": {"draft": client + " private draft"}})
        snapshot(service, client)  # Populate independent cached projections.
    SettingsStore(service.data_dir).update(service.default_workspace, "global",
        lambda settings: settings.update(bundle={"active": "changed-by-cli"}))
    for client in ("browser-a", "browser-b"):
        current = snapshot(service, client)
        assert current["settings"]["bundle"] == "changed-by-cli"
        assert current["view"]["draft"] == client + " private draft"
    result = await command(service, "browser-a", "session.create", {})
    created = next(row for row in result["state"]["sessions"] if row["id"] == result["state"]["selectedSessionId"])
    assert created["bundle"] == "changed-by-cli"
    assert snapshot(service, "browser-b")["selectedSessionId"] == first
