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
