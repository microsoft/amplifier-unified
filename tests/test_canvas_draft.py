"""Canvas starts with a real chat; draft navigation keeps saved artifacts intact."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    service.clients.attach('one')
    yield service
    await service.close()


async def command(app, action, args, **kwargs):
    with app.clients.bind('one'):
        return await app.dispatch(action, args, **kwargs)


def visibility(app, open=True):
    client = app.clients.records['one']
    return {'open': open, 'sessionId': client.get('selectedSessionId'),
            'canvasId': client.get('canvas', {}).get('id')}


@pytest.mark.parametrize('origin', ['ui', 'agent'])
@pytest.mark.parametrize('action,args', [
    ('canvas.visibility', None),
    ('canvas.show', {'kind': 'text', 'content': 'No orphan artifact'}),
    ('canvas.show', {'kind': 'browser', 'url': 'https://example.com'}),
    ('canvas.reopen', {}),
    ('canvas.select', {'id': 'old-artifact'}),
    ('canvas.tabClose', {'id': 'old-artifact'}),
])
async def test_draft_open_actions_refuse_without_creating_session_or_artifact(app, action, args, origin):
    args = visibility(app) if args is None else args
    with pytest.raises(AppError, match='Start a chat before opening Canvas') as exc:
        await command(app, action, args, origin=origin, command_id='draft-open')
    assert exc.value.code == 'canvas_requires_session'
    assert app._state['sessions'] == []
    assert app._state['canvasArtifacts'] == []
    assert not app.clients.records['one']['canvas']['open']
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('draft-open',)).fetchone() is None
    # Closing remains harmless and uses the same durable visibility action.
    closed = await command(app, 'canvas.visibility', visibility(app, False), origin=origin)
    assert closed['accepted']
    assert app.clients.records['one']['selectedSessionId'] is None
    assert not app.clients.records['one']['canvas']['open']


@pytest.mark.parametrize('origin', ['ui', 'agent'])
async def test_explicit_null_draft_view_and_attachments_do_not_create_chat(app, origin):
    if origin == 'agent':
        before = deepcopy(app.clients.records['one'])
        with pytest.raises(AppError, match='calling conversation'):
            await command(app, 'view.update', {'sessionId': None, 'patch': {'draft': 'Before first send'}}, origin=origin)
        assert app.clients.records['one'] == before
        assert app._state['sessions'] == []
        return
    await command(app, 'view.update', {'sessionId': None, 'patch': {'draft': 'Before first send'}}, origin=origin)
    await command(app, 'attachment.add', {'sessionId': None, 'name': 'draft.txt', 'base64': 'aGVsbG8='}, origin=origin)
    with app.clients.bind('one'):
        assert app.state['view']['draft'] == 'Before first send'
        attachment = app.clients.attachments(None)[0]
        assert attachment['name'] == 'draft.txt'
    await command(app, 'attachment.remove', {'sessionId': None, 'id': attachment['id']}, origin=origin)
    with app.clients.bind('one'):
        assert app.clients.attachments(None) == []
    assert app._state['sessions'] == []
    assert app._state['canvasArtifacts'] == []
    assert app.clients.records['one']['selectedSessionId'] is None


@pytest.mark.parametrize('action,args', [
    ('canvas.visibility', {'open': False, 'canvasId': None}),
    ('view.update', {'patch': {'draft': 'Must not land'}}),
    ('attachment.add', {'name': 'draft.txt', 'base64': 'aGVsbG8='}),
    ('attachment.remove', {'id': 'not-an-attachment'}),
])
@pytest.mark.parametrize('identity', ['', ' \t', 7, {}])
async def test_nullable_draft_actions_still_refuse_malformed_identity(app, action, args, identity):
    before = deepcopy(app.clients.records['one'])
    with pytest.raises(AppError, match='nonempty') as exc:
        await command(app, action, {**args, 'sessionId': identity})
    assert exc.value.status == 400
    assert app.clients.records['one'] == before
    assert app._state['sessions'] == []
    assert app._state['canvasArtifacts'] == []


async def test_draft_navigation_preserves_history_artifacts_tabs_and_other_client(app):
    await command(app, 'session.create', {})
    session_id = app.clients.records['one']['selectedSessionId']
    session = app._session(session_id)
    app._message(session, 'user', 'Preserve this conversation')
    await command(app, 'canvas.show', {'kind': 'markdown', 'content': '# Saved'})
    artifact = deepcopy(app._state['canvasArtifacts'][0])
    tabs = deepcopy(app.clients.records['one']['canvasTabs'])
    history = deepcopy(session['messages'])
    app.clients.attach('two', resume='one')
    other = deepcopy(app.clients.records['two'])
    await command(app, 'view.update', {'patch': {'canvasFocused': True}})
    await command(app, 'session.draft', {})
    client = app.clients.records['one']
    assert client['selectedSessionId'] is None
    assert client['canvas']['placeholder'] and not client['canvas']['open']
    assert client['view']['canvasFocused'] is False
    assert client['canvasTabs'] == tabs
    assert app._state['canvasArtifacts'][0]['body'] == artifact['body']
    assert session['messages'] == history
    assert app.clients.records['two'] == other
    assert len(app._state['sessions']) == 1
    # Both normal restoration and the explicit open action still work later.
    await command(app, 'session.select', {'id': session_id})
    assert app.clients.records['one']['canvas']['id'] == artifact['id']
    await command(app, 'canvas.visibility', visibility(app))
    assert app.clients.records['one']['canvas']['content'] == '# Saved'
    assert app.clients.records['one']['canvas']['open']


@pytest.mark.parametrize('opener', ['canvas.reopen', 'canvas.visibility'])
async def test_background_agent_can_publish_to_real_session_while_client_is_drafting(app, opener):
    await command(app, 'session.create', {})
    session_id = app.clients.records['one']['selectedSessionId']
    assert app._session(session_id)['messages'] == []
    await command(app, 'session.draft', {})
    await command(app, 'canvas.show', {'sessionId': session_id, 'kind': 'text', 'content': 'Agent result'},
                  origin='agent', caller_session_id=session_id)
    assert app.clients.records['one']['selectedSessionId'] is None
    assert not app.clients.records['one']['canvas']['open']
    assert app._state['canvasArtifacts'][-1]['sessionId'] == session_id
    await command(app, 'session.select', {'id': session_id})
    args = {'sessionId': session_id, 'canvasId': app.clients.records['one']['canvas']['id'], 'open': True} if opener == 'canvas.visibility' else {}
    await command(app, opener, args)
    assert app.clients.records['one']['canvas']['open']
    assert app.clients.records['one']['canvas']['content'] == 'Agent result'


async def test_no_message_session_can_open_empty_canvas(app):
    await command(app, 'session.create', {})
    sid = app.clients.records['one']['selectedSessionId']
    assert app._session(sid)['messages'] == []
    await command(app, 'canvas.visibility', visibility(app))
    assert app.clients.records['one']['canvas']['open']
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'A real chat'})
    assert app._state['canvasArtifacts'][-1]['sessionId'] == sid


async def test_dirty_view_still_blocks_draft_navigation(app):
    await command(app, 'session.create', {})
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Do not discard edits'})
    with app.clients.bind('one'):
        view = app.canvas_views.summary('primary')
    target = {k: view[k] for k in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await command(app, 'canvas.views.dirty', {**target, 'dirty': True})
    before = deepcopy(app.clients.records['one'])
    with pytest.raises(AppError, match='viewer edit'):
        await command(app, 'session.draft', {})
    assert app.clients.records['one'] == before


async def test_restart_hides_legacy_draft_canvas_and_restore_does_not_reopen_it(app):
    from amplifier_web.canvas_library import remember, restore
    from amplifier_web.workspace_canvas import canvas_command
    with app.clients.bind('one'):
        # Emulate an artifact saved by the old no-session behavior.
        canvas_command(app.state, 'canvas.show', {'kind': 'text', 'content': 'Legacy preserved'}, 'ui')
        remember(app.state, app.db)
        artifact = deepcopy(app._state['canvasArtifacts'][-1])
        app.clients.save('one')
        app._save()
    await app.close()
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    try:
        restored.clients.attach('one')
        assert restored.clients.records['one']['selectedSessionId'] is None
        assert not restored.clients.records['one']['canvas']['open']
        assert restored._state['canvasArtifacts'] == app._state['canvasArtifacts']
        with restored.clients.bind('one'):
            restore(restored.state, restored.db, open_panel=True)
            assert restored.state['canvas']['placeholder']
            assert not restored.state['canvas']['open']
        assert restored._state['canvasArtifacts'][-1]['body'] == artifact['body']
    finally:
        await restored.close()


async def test_unattached_agent_draft_also_resets_presentation_without_creating_work(app):
    # A human creates the root; the unattached model only changes its presentation.
    await app.dispatch('session.create', {}, origin='ui')
    sid = app.state['selectedSessionId']
    await app.dispatch('canvas.show', {'sessionId': sid, 'kind': 'text', 'content': 'Retained source'},
                       origin='agent', caller_session_id=sid)
    saved = deepcopy(app._state['canvasArtifacts'])
    app.state['view']['canvasFocused'] = True
    await app.dispatch('session.draft', {}, origin='agent')
    assert app.state['selectedSessionId'] is None
    assert app.state['canvas']['placeholder'] and not app.state['canvas']['open']
    assert app.state['view']['canvasFocused'] is False
    assert app._state['canvasArtifacts'] == saved
    assert len(app.state['sessions']) == 1
    # Re-entering the same draft must also close a stale legacy presentation.
    app.state['canvas']['open'] = True
    await app.dispatch('session.draft', {}, origin='agent')
    assert not app.state['canvas']['open']
