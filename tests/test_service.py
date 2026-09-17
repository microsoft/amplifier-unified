import asyncio
import json
import pytest
from amplifier_web.service import AppService, AppError, validate_theme


class Runtime:
    def __init__(self):
        self.sent = []
        self.stopped = []
    async def send(self, session, text, input_id, emit):
        self.sent.append((session["id"], text, input_id))
        await emit("assistant.message", {"sessionId": session["id"], "text": "Test transport result", "inputId": input_id})
        await emit("runtime.generation", {"sessionId": session["id"], "event": "generation.finished",
            "generation_id": "test-generation", "input_ids": [input_id], "text": "Test transport result",
            "active_job_ids": [], "disposition": "manager_turn_finished"})
        await emit("runtime.status", {"sessionId": session["id"], "status": "idle"})
    async def stop(self, sid):
        self.stopped.append(sid)
    async def close(self):
        pass


@pytest.fixture
async def service(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    await app.dispatch("session.create", {})
    yield app
    await app.close()


async def test_conversation_survives_restart_and_deduplication(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch("session.create", {}, command_id="create")
    await app.dispatch("conversation.send", {"text": "Remember this"}, command_id="send")
    await asyncio.gather(*app.tasks)
    original = app.get_state()["sessions"][0]
    await app.close()
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    duplicate = await restored.dispatch("conversation.send", {"text": "Remember this"}, command_id="send")
    assert duplicate["duplicate"] is True
    assert restored.get_state()["sessions"][0]["messages"] == original["messages"]
    assert not restored.runtime.sent
    with pytest.raises(AppError, match="different contents"):
        await restored.dispatch("conversation.send", {"text": "Changed"}, command_id="send")
    await restored.close()


async def test_shared_agent_control_state_and_stale_revisions(service):
    before = service.get_state()
    await service.app_bridge("dispatch", {"action": "view.update", "args": {"patch": {"scheme": "dark", "panel": "appearance"}}, "expectedRevision": before["revision"]}, before["selectedSessionId"])
    assert service.get_state()["view"]["scheme"] == "dark"
    with pytest.raises(AppError, match="app changed"):
        await service.dispatch("view.update", {"patch": {"scheme": "light"}}, expected_revision=before["revision"])
    snapshot = service.get_state()
    snapshot["view"]["scheme"] = "corrupted"
    assert service.get_state()["view"]["scheme"] == "dark"


async def test_voice_pins_session_and_end_does_not_stop_work(service):
    first = service.get_state()["selectedSessionId"]
    await service.dispatch("session.create", {})
    await service.record_voice_transcript("user", "Voice request", voice_id="call", item_id="one", session_id=first)
    await service.voice_delegate("Voice request", "voice-1", session_id=first)
    await asyncio.gather(*service.tasks)
    result = await service.wait_for_response(first, "voice-1", timeout=1)
    assert result["text"] == "Test transport result"
    assert service.runtime.sent[0][0] == first
    await service.dispatch("call.end", {})
    assert not service.runtime.stopped


async def test_skin_roundtrip_and_external_assets_rejected(service):
    css = '#amp-one {color: #4338f4; background: #e8eeff;}'
    await service.dispatch("theme.apply", {"name": "Converge custom", "css": css}, origin="agent")
    exported = await service.dispatch("theme.export", {})
    assert exported["effects"][0]["content"] == css
    for bad in ['@import "https://example.com/a.css";', '#amp-one {background:url("https://example.com/x")}']:
        with pytest.raises(AppError):
            validate_theme(bad)
    assert service.get_state()["theme"]["css"] == css


async def test_device_observation_and_agent_effect_delivery(service):
    revision = service.get_state()["revision"]
    await service.update_device({"clientId": "desktop", "visibleText": "My conversation", "controls": []})
    assert service.get_state()["revision"] == revision
    assert service.get_state()["devices"]["desktop"]["visibleText"] == "My conversation"
    receipt = await service.dispatch("notification.request", {}, origin="agent")
    assert receipt["effects"][0]["id"] == service.get_state()["deviceCommands"][-1]["id"]


async def test_tool_approval_cannot_be_self_granted(service):
    sid = service.get_state()["selectedSessionId"]
    await service.on_runtime_event("approval.requested", {"sessionId": sid, "id": "permission", "prompt": "Allow tool?"})
    with pytest.raises(AppError, match="user approval"):
        await service.dispatch("approval.respond", {"id": "permission", "decision": "allow"}, origin="agent")
    await service.on_runtime_event("approval.resolved", {"sessionId": sid, "id": "permission", "decision": "expired"})
    assert service.get_state()["sessions"][0]["approvals"][0]["status"] == "expired"


async def test_preparation_progress_is_visible_and_clears_when_work_begins(service):
    sid = service.get_state()["selectedSessionId"]
    await service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "starting", "phase": "bundle-preparation", "detail": "Loading your configured bundle and tools.", "elapsedSeconds": 25})
    session = service.get_state()["sessions"][0]
    assert session["progress"]["elapsedSeconds"] == 25
    assert session["progress"]["detail"] == "Loading your configured bundle and tools."
    await service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "working"})
    assert "progress" not in service.get_state()["sessions"][0]


async def test_parallel_tool_activity_tracks_completion_without_claiming_turn_finished(service):
    sid = service.get_state()["selectedSessionId"]
    await service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "working"})
    for identity in ("one", "two"):
        await service.on_runtime_event("runtime.tool", {"sessionId": sid, "tool": "delegate", "callId": identity, "phase": "pre"})
    activity = service.get_state()["sessions"][0]["activity"]
    assert len(activity["activeTools"]) == 2
    assert activity["label"] == "Delegating work"
    await service.on_runtime_event("runtime.tool", {"sessionId": sid, "tool": "delegate", "callId": "one", "phase": "post"})
    session = service.get_state()["sessions"][0]
    assert [t["callId"] for t in session["activity"]["activeTools"]] == ["two"]
    assert session["status"] == "working"
    await service.on_runtime_event("runtime.tool", {"sessionId": sid, "tool": "delegate", "callId": "two", "phase": "error"})
    activity = service.get_state()["sessions"][0]["activity"]
    assert activity["phase"] == "model"
    assert not activity["activeTools"]
    assert activity["lastEvent"]["phase"] == "error"
    assert activity["updatedAt"] >= activity["startedAt"]
    await service.on_runtime_event("runtime.status", {"sessionId": sid, "status": "idle"})
    assert service.get_state()["sessions"][0]["activity"]["phase"] == "idle"

async def test_voice_waits_for_identified_completion_not_early_text(service):
    sid = service.get_state()['selectedSessionId']
    await service.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'Starting the tools', 'inputId': 'voice:pending'})
    waiter = asyncio.create_task(service.wait_for_response(sid, 'voice:pending', timeout=2))
    await asyncio.sleep(0)
    assert not waiter.done()
    await service.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished',
        'generation_id': 'other', 'input_ids': ['different'], 'text': 'Unrelated completed result', 'active_job_ids': []})
    await asyncio.sleep(0)
    assert not waiter.done()
    await service.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished',
        'generation_id': 'correct', 'input_ids': ['voice:pending'], 'text': 'Worker launched; still running',
        'active_job_ids': ['worker-1'], 'disposition': 'manager_turn_finished'})
    result = await waiter
    assert result['generation_id'] == 'correct'
    assert result['active_job_ids'] == ['worker-1']


async def test_voice_acceptance_is_durable_and_never_replays(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app.get_state()['selectedSessionId']
    await app.voice_delegate('One operation', 'voice:once', sid)
    await asyncio.gather(*app.tasks)
    await app.close()
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    assert (await restored.voice_delegate('One operation', 'voice:once', sid))['duplicate']
    assert not restored.runtime.sent
    with pytest.raises(AppError, match='different contents'):
        await restored.voice_delegate('Different operation', 'voice:once', sid)
    await restored.close()


async def test_settings_navigation_and_filters_are_agent_visible(service):
    patch = {"settingsSection": "capabilities", "settingsExpanded": ["loaded-modules"], "settingsFilters": {"loaded-modules": "tool-*"}}
    await service.app_bridge("dispatch", {"action": "view.update", "args": {"patch": patch}}, service.get_state()["selectedSessionId"])
    view = service.get_state()["view"]
    assert all(view[key] == value for key, value in patch.items())
    await service.dispatch("view.update", {"patch": {"settingsExpanded": []}})
    assert service.get_state()["view"]["settingsFilters"]["loaded-modules"] == "tool-*"


async def test_attention_acknowledgement_is_shared_and_changed_items_reappear(service):
    row={'id':'repo','label':'Community bundle','status':'update','current':'old','latest':'new'}
    service.state['updates']={'items':[row]}
    initial=service.get_state()['attention']
    assert initial['unread']==1
    assert initial['sections']['maintenance']==initial['pages']['updates']==1
    await service.app_bridge('dispatch',{'action':'attention.read','args':{'ids':['source:repo']}},service.get_state()['selectedSessionId'])
    assert service.get_state()['attention']['unread']==0
    assert service.get_state()['updates']['items'][0]['status']=='update'
    service.state['updates']['items'][0]['latest']='newer'
    assert service.get_state()['attention']['unread']==1
    service.state['updates']['items'][0]['status']='current'
    assert service.get_state()['attention']['items']==[]
    with pytest.raises(AppError):await service.dispatch('attention.read',{'ids':['not-real']})


async def test_attention_read_persists_and_errors_route_to_their_page(tmp_path):
    app=AppService(tmp_path,Runtime(),workspace=tmp_path)
    app.state['actionStatus']={'providers.test':{'phase':'error','error':'Connection timed out','commandId':'one'}}
    await app.dispatch('attention.read',{'ids':['action:providers.test']})
    await app.close()
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    assert restored.get_state()['attention']['unread']==0
    restored.state['actionStatus']['providers.test']['commandId']='two'
    attention=restored.get_state()['attention']
    assert attention['sections']['setup']==attention['pages']['providers']==1
    await restored.close()


async def test_successful_session_start_clears_persisted_error_and_attention(service):
    sid=service.get_state()['selectedSessionId']
    await service.on_runtime_event('runtime.error',{'sessionId':sid,'error':'ValueError: contextTokens must be a positive integer'})
    assert service.get_state()['attention']['unread']==1
    await service.on_runtime_event('runtime.status',{'sessionId':sid,'status':'ready','phase':'ready'})
    assert 'error' not in service.get_state()['sessions'][0]
    assert service.get_state()['attention']['unread']==0
    await service.on_runtime_event('runtime.status',{'sessionId':sid,'status':'idle'})
    assert 'error' not in service.get_state()['sessions'][0]


@pytest.mark.parametrize('status',['starting','idle','stopping','stopped'])
async def test_non_ready_status_does_not_hide_unresolved_runtime_failure(service,status):
    sid=service.get_state()['selectedSessionId']
    await service.on_runtime_event('runtime.error',{'sessionId':sid,'error':'Provider authentication failed'})
    await service.on_runtime_event('runtime.status',{'sessionId':sid,'status':status})
    assert service.get_state()['sessions'][0]['error']=='Provider authentication failed'
    assert service.get_state()['attention']['unread']==1


async def test_send_does_not_clear_newer_or_other_session_draft(service):
    original = service.state['selectedSessionId']
    await service.dispatch('view.update', {'patch': {'draft': 'Already typing another message'}})
    await service.dispatch('conversation.send', {'sessionId': original, 'text': 'Previously submitted text'})
    assert service.state['view']['draft'] == 'Already typing another message'
    await service.dispatch('session.create', {})
    await service.dispatch('view.update', {'patch': {'draft': 'Other conversation text'}})
    await service.dispatch('conversation.send', {'sessionId': original, 'text': 'Other conversation text'})
    assert service.state['view']['draft'] == 'Other conversation text'
