"""Private composer storage is distinct from explicit model inspection."""
import copy
import json

import pytest

from amplifier_web.agent_state import read_state
from amplifier_web.directory_state import _ranked
from amplifier_web.service import AppError
from test_agent_canvas_scope import app, caller, command


def test_model_projection_preserves_lazy_cold_display_fields(monkeypatch):
    from amplifier_web import cold_display
    row = cold_display.ColdRecord({'id': 'caller', 'draft': 'PRIVATE_COLD',
        '_coldFields': {'messages': {'$resource': 'saved-body'}}}, None)
    monkeypatch.setattr(cold_display, 'load', lambda *_: pytest.fail('Scalar read hydrated a dormant body'))
    state = {'revision': 2, 'sessions': [row]}
    assert read_state(state, {'path': '/revision'}, session_id='caller')['value'] == 2
    assert row['draft'] == 'PRIVATE_COLD' and 'messages' in dict.__getitem__(row, '_coldFields')


async def private_clients(app):
    sid = caller(app)
    app.clients.attach('second-caller', resume='caller')
    for client, text in [('caller', 'PRIVATE_A'), ('second-caller', 'PRIVATE_B')]:
        await command(app, client, 'view.update', {'patch': {'draft': text}})
        record = app.clients.records[client]
        record['drafts'][''] = 'PRIVATE_NEW'
        record['view']['messageEdit'] = {'sessionId': sid, 'messageId': 'edit', 'text': 'PRIVATE_EDIT'}
        record['attachments'][sid] = [{'id': 'attachment', 'name': 'PRIVATE_ATTACHMENT'}]
        record['attachments'][''] = [{'id': 'new-attachment', 'name': 'PRIVATE_NEW_ATTACHMENT'}]
        await app.update_device({'clientId': client, 'sessionId': sid,
                                 'controls': [{'label': client}], 'visibleText': 'Safe interface'})
    await app.update_device({'clientId': 'other', 'sessionId': app.clients.records['other']['selectedSessionId'],
                             'visibleText': 'FOREIGN_OBSERVATION'})
    return sid


@pytest.mark.parametrize('path', [None, '', '/view', '/sessions', '/sessions/0', '/devices'])
async def test_ambient_model_paths_never_include_private_composer_aliases(app, path):
    sid = await private_clients(app)
    before = copy.deepcopy(app.clients.records)
    args = {'clientId': 'caller', **({'path': path} if path is not None else {})}
    value = await app.app_bridge('get_state', args, sid)
    serialized = json.dumps(value)
    assert 'PRIVATE_' not in serialized and 'FOREIGN_OBSERVATION' not in serialized
    assert 'Keep other draft' not in serialized and 'second-caller"' not in json.dumps(value.get('devices', {}))
    assert app.clients.records == before


async def test_explicit_draft_inspection_selects_exact_client_and_never_another_chat(app):
    sid = await private_clients(app)
    before = copy.deepcopy(app.clients.records)
    for client, expected in [('caller', 'PRIVATE_A'), ('second-caller', 'PRIVATE_B')]:
        result = await app.app_bridge('get_state', {'path': '/view/draft', 'clientId': client}, sid)
        assert result['value'] == expected
        edit = await app.app_bridge('get_state', {'path': '/view/messageEdit', 'clientId': client}, sid)
        assert next(row['value'] for row in edit['items'] if row['key'] == 'text') == 'PRIVATE_EDIT'
    for args in [{'path': '/view/draft'}, {'path': '/view/draft', 'clientId': None}]:
        with pytest.raises(ValueError, match='explicit clientId'):
            await app.app_bridge('get_state', args, sid)
    for client in ('other', 'missing'):
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('get_state', {'path': '/view/draft', 'clientId': client}, sid)
    assert app.clients.records == before
    app.clients.records['caller']['view']['messageEdit']['sessionId'] = 'another-chat'
    with pytest.raises(ValueError, match='calling conversation'):
        await app.app_bridge('get_state', {'path': '/view/messageEdit', 'clientId': 'caller'}, sid)
    # The same client identity no longer authorizes a read after its selection changes.
    await command(app, 'caller', 'session.select', {'id': app.clients.records['other']['selectedSessionId']})
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('get_state', {'path': '/view/draft', 'clientId': 'caller'}, sid)


async def test_aliases_cannot_opt_in_and_browser_private_state_remains_available(app):
    sid = await private_clients(app)
    with app.clients.bind('caller'):
        raw = app.state_context()
        assert read_state(raw, {'path': '/view/draft'})['value'] == 'PRIVATE_A'
        assert read_state(raw, {'path': '/draftAttachments/0/name'})['value'] == 'PRIVATE_NEW_ATTACHMENT'
    for path in ('/sessions/0/draft', '/sessions/0/draftAttachments', '/draftAttachments',
                 '/view/newChatText', '/view/messageEdit/text', '/devices/second-caller', '/devices/other'):
        with pytest.raises(ValueError, match='does not exist'):
            await app.app_bridge('get_state', {'path': path, 'clientId': 'caller'}, sid)
    result = await app.app_bridge('dispatch', {'action': 'session.rename',
        'args': {'id': sid, 'title': 'Renamed'}, 'clientId': 'caller'}, sid)
    assert 'PRIVATE_' not in json.dumps(result) and 'FOREIGN_OBSERVATION' not in json.dumps(result)
    assert app.clients.records['caller']['drafts'][sid] == 'PRIVATE_A'


async def test_lazy_owner_projection_does_not_reinsert_drafts_or_strip_user_json(app):
    sid = await private_clients(app)
    other = app._session(app.clients.records['other']['selectedSessionId'])
    other['structuredData'] = {'draft': 'Saved artifact data', 'path': '/view/draft'}
    app.clients.records['caller']['drafts'][other['id']] = 'PRIVATE_DORMANT'
    app._save()
    app._state['sessions'] = [row for row in app._state['sessions'] if row['id'] != other['id']]
    position = next(position for position, row in _ranked(app.directory) if row['id'] == other['id'])
    path = '/sessions/' + str(position)
    args = {'path': path, 'clientId': 'caller'}
    value = await app.app_bridge('get_state', args, sid)
    assert 'PRIVATE_' not in json.dumps(value)
    with pytest.raises(ValueError, match='does not exist'):
        await app.app_bridge('get_state', {**args, 'path': path + '/draft'}, sid)
    value = await app.app_bridge('get_state', {**args, 'path': path + '/structuredData/draft'}, sid)
    assert value['value'] == 'Saved artifact data'
    assert not any(row['id'] == other['id'] for row in app._state['sessions'])


@pytest.mark.parametrize('reported_session', [None, 'previous-chat'])
async def test_unattributed_or_previous_chat_browser_observation_is_not_model_context(app, reported_session):
    sid = await private_clients(app)
    await app.update_device({'clientId': 'caller', 'sessionId': reported_session, 'visibleText': 'STALE_OBSERVATION'})
    for path in (None, '/devices'):
        result = await app.app_bridge('get_state', {'clientId': 'caller', **({'path': path} if path else {})}, sid)
        assert 'STALE_OBSERVATION' not in json.dumps(result)
        assert 'visibleUI' not in result
