"""A client can configure a new chat without creating history or starting work."""
import base64
import copy

import pytest

from amplifier_web.service import AppError, AppService
from test_service import Runtime
from test_live_clients import command, snapshot


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    for client in ('web', 'agent'):
        service.clients.attach(client, kind='web' if client == 'web' else 'api')
    yield service
    if not getattr(service, "test_closed", False):
        await service.close()


async def test_setup_is_private_durable_and_does_not_create_a_session(app, tmp_path):
    setup = {'workspace': str(tmp_path / 'not-chosen-yet'), 'title': 'Planned work',
             'bundle': 'custom', 'selection': {'instance': 'provider-a', 'model': 'model-b'}}
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'For later'}})
    await command(app, 'web', 'attachment.add', {'sessionId': None, 'name': 'notes.txt', 'base64': base64.b64encode(b'Keep these notes').decode()})
    for _ in range(3):
        await command(app, 'web', 'session.draft')
    state = snapshot(app, 'web')
    assert state['sessions'] == [] and state['selectedSessionId'] is None
    assert state['view']['newSessionDraft'] == setup
    assert state['view']['draft'] == 'For later'
    assert state['draftAttachments'][0]['name'] == 'notes.txt'
    other = snapshot(app, 'agent')
    assert not other['view'].get('newSessionDraft') and other['draftAttachments'] == []
    assert not app.runtime.started and not app.runtime.sent
    assert not (tmp_path / 'not-chosen-yet').exists()
    assert not list((tmp_path / 'amplifier-home').rglob('transcript.jsonl'))
    app.clients.attach('reload', resume='web')
    assert snapshot(app, 'reload')['view']['newSessionDraft'] == setup
    await app.close()
    app.test_closed = True
    restored = AppService(app.data_dir, Runtime(), workspace=tmp_path)
    try:
        state = snapshot(restored, 'web')
        assert state['view']['newSessionDraft'] == setup and state['view']['draft'] == 'For later'
        assert state['draftAttachments'][0]['name'] == 'notes.txt'
        assert not state['sessions']
    finally:
        await restored.close()


async def test_first_submission_uses_chosen_setup_and_transfers_draft_once(app, tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    setup = {'workspace': str(project), 'title': 'My chosen name', 'bundle': 'chosen-bundle',
             'selection': {'instance': 'provider-a', 'model': 'chosen-model', 'effort': 'high'}}
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'Next thought'}})
    added = await command(app, 'web', 'attachment.add', {'sessionId': None, 'name': 'notes.txt', 'base64': 'bm90ZXM='})
    attachment = added['state']['draftAttachments'][0]
    created = await command(app, 'web', 'session.create', {**setup, 'fromDraft': True}, command_id='first-creation')
    sid = created['sessionId']
    session = next(row for row in created['state']['sessions'] if row['id'] == sid)
    assert session['selection'] == setup['selection'] and session['bundle'] == setup['bundle']
    assert session['workspace'] == str(project) and session['title'] == setup['title']
    assert session['draft'] == 'Next thought' and session['draftAttachments'] == [attachment]
    assert created['state']['draftAttachments'] == []
    assert 'newSessionDraft' not in created['state']['view']
    await command(app, 'web', 'session.draft')
    app.clients.attach('reloaded-sender', resume='web')
    duplicate = await command(app, 'reloaded-sender', 'session.create', {**setup, 'fromDraft': True}, command_id='first-creation')
    assert duplicate['duplicate'] and duplicate['sessionId'] == sid
    assert len(app.state['sessions']) == 1
    # A replayed creation receipt identifies its original chat even after navigation.
    assert duplicate['state']['selectedSessionId'] is None
    await command(app, 'web', 'conversation.send', {'sessionId': sid, 'text': 'Start here', 'attachmentIds': [attachment['id']], 'preserveDraft': True}, command_id='first-message')
    await command(app, 'web', 'conversation.send', {'sessionId': sid, 'text': 'Start here', 'attachmentIds': [attachment['id']], 'preserveDraft': True}, command_id='first-message')
    assert len(app.runtime.sent) == 1 and app.runtime.sent[0][0] == sid
    assert app._session(sid)['title'] == 'My chosen name'
    assert snapshot(app, 'web')['selectedSessionId'] is None


async def test_delayed_upload_stays_with_unsent_chat_after_navigation(app, tmp_path):
    existing = await command(app, 'web', 'session.create', {'title': 'Existing'})
    sid = existing['sessionId']
    await command(app, 'web', 'view.update', {'patch': {'draft': 'Existing unsent text'}})
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'draft': 'Future chat'}})
    await command(app, 'web', 'session.select', {'id': sid})
    added = await command(app, 'web', 'attachment.add', {'sessionId': None, 'name': 'future.txt', 'base64': 'ZnV0dXJl'})
    assert added['state']['sessions'][0]['draftAttachments'] == []
    assert added['state']['view']['draft'] == 'Existing unsent text'
    aid = added['state']['draftAttachments'][0]['id']
    await command(app, 'web', 'attachment.remove', {'sessionId': None, 'id': aid})
    assert snapshot(app, 'web')['draftAttachments'] == []
    await command(app, 'web', 'session.draft')
    assert snapshot(app, 'web')['view']['draft'] == 'Future chat'


@pytest.mark.parametrize('setup', [{'workspace': '/nonexistent-new-chat-folder'}, {'selection': {'instance': 'missing-model'}}])
async def test_invalid_first_creation_preserves_config_and_text(app, setup):
    await command(app, 'web', 'session.draft')
    await command(app, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'Keep me'}})
    with pytest.raises(AppError):
        await command(app, 'web', 'session.create', {**setup, 'fromDraft': True})
    state = snapshot(app, 'web')
    assert not state['sessions'] and state['view']['newSessionDraft'] == setup
    assert state['view']['draft'] == 'Keep me'


async def test_legacy_launcher_and_agent_share_draft_and_dirty_canvas_guard(app):
    created = await command(app, 'agent', 'session.create', {})
    sid = created['sessionId']
    await command(app, 'agent', 'view.update', {'patch': {'panel': 'new-session'}})
    assert snapshot(app, 'agent')['selectedSessionId'] is None
    assert snapshot(app, 'agent')['view']['panel'] is None
    assert len(app.state['sessions']) == 1
    await command(app, 'agent', 'session.select', {'id': sid})
    shown = await command(app, 'agent', 'canvas.show', {'kind': 'markdown', 'content': 'Keep editor draft'})
    aid = shown['state']['canvas']['id']
    record = app.clients.records['agent']
    record['canvasViews'] = {'preferences': {'primary:' + aid: {'dirty': True}}}
    before = copy.deepcopy(record)
    with pytest.raises(AppError, match='viewer edit'):
        await command(app, 'agent', 'session.draft')
    assert record['selectedSessionId'] == sid and record['canvas'] == before['canvas']
