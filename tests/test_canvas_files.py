import copy
import hashlib

import pytest

from amplifier_web.service import AppError
from test_canvas_views import app, command, target, view


def args(app, path, client='one'):
    with app.clients.bind(client):
        session = app._session()
        return {'sessionId': session['id'], 'workspace': session['workspace'], 'path': str(path)}


async def test_file_open_preserves_chat_draft_and_other_client(app, tmp_path):
    path = tmp_path / 'plan.md'
    path.write_text('# Opened')
    await command(app, 'view.update', {'patch': {'draft': 'Unsent'}})
    with app.clients.bind('one'):
        session_before = copy.deepcopy(app.state['sessions'])
    other = copy.deepcopy(app.clients.records['two'])
    result = await command(app, 'canvas.openFile', args(app, 'plan.md'))
    assert result['result']['status'] == 'opened'
    with app.clients.bind('one'):
        assert app.state['canvas']['content'] == '# Opened'
        assert app.state['view']['draft'] == 'Unsent'
        assert app.state['sessions'] == session_before
    assert app.clients.records['two'] == other


async def browse_files(app):
    with app.clients.bind('one'):
        workspace_id = app.state['selectedWorkspaceId']
    await command(app, 'view.update', {'patch': {
        'workSurface': 'workspace', 'workWorkspaceId': workspace_id, 'workWorkspaceTab': 'files'}})


async def test_named_file_preview_and_chat_presentation_commit_together(app, tmp_path):
    (tmp_path / 'plan.md').write_text('# Named preview')
    await command(app, 'view.update', {'patch': {'draft': 'Keep my draft'}})
    await browse_files(app)
    request = {**args(app, 'plan.md'), 'showConversation': True}
    other = copy.deepcopy(app.clients.records['two'])
    result = await command(app, 'canvas.openFile', request)
    assert result['result']['status'] == 'opened'
    with app.clients.bind('one'):
        assert app.state['view']['workSurface'] == 'chat'
        assert app.state['selectedSessionId'] == request['sessionId']
        assert app.state['view']['draft'] == 'Keep my draft'
        assert app.state['canvas']['id'] == result['result']['id']
        assert app.state['canvas']['content'] == '# Named preview'
    assert app.clients.records['two'] == other
    # Later navigation and a new Canvas are not overwritten by processing the
    # old opened receipt: there is no follow-up selection command.
    await command(app, 'view.update', {'patch': {'workSurface': 'chats'}})
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Newer artifact'})
    with app.clients.bind('one'):
        assert app.state['view']['workSurface'] == 'chats'
        assert app.state['canvas']['id'] != result['result']['id']


@pytest.mark.parametrize('patch', [
    {'workSurface': 'chats'},
    {'workSurface': 'chat'},
    {'workWorkspaceTab': 'details'},
    {'workWorkspaceId': None},
])
async def test_named_file_preview_rejects_a_changed_browser_before_mutating(app, tmp_path, patch):
    (tmp_path / 'plan.md').write_text('# Do not open')
    await browse_files(app)
    request = {**args(app, 'plan.md'), 'showConversation': True}
    await command(app, 'view.update', {'patch': patch})
    with app.clients.bind('one'):
        before = copy.deepcopy({key: app.state[key] for key in ('view', 'canvas', 'selectedSessionId')})
    result = await command(app, 'canvas.openFile', request)
    assert result['result']['status'] == 'unavailable'
    with app.clients.bind('one'):
        assert all(app.state[key] == value for key, value in before.items())


async def test_named_file_preview_missing_dirty_and_changed_chat_do_not_navigate(app, tmp_path):
    await browse_files(app)
    request = {**args(app, 'missing.md'), 'showConversation': True}
    result = await command(app, 'canvas.openFile', request)
    assert result['result']['status'] == 'unavailable'
    with app.clients.bind('one'):
        assert app.state['view']['workSurface'] == 'workspace'
    (tmp_path / 'plan.md').write_text('# Keep edit')
    request['path'] = 'plan.md'
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Unsaved editor'})
    await command(app, 'canvas.views.dirty', {**target(view(app)), 'dirty': True})
    with pytest.raises(AppError, match='viewer edit'):
        await command(app, 'canvas.openFile', request)
    with app.clients.bind('one'):
        assert app.state['view']['workSurface'] == 'workspace'
        assert app.state['canvas']['content'] == 'Unsaved editor'
    await command(app, 'canvas.views.dirty', {**target(view(app)), 'dirty': False})
    await command(app, 'session.create', {})
    with app.clients.bind('one'):
        current = app.state['selectedSessionId']
    await browse_files(app)
    result = await command(app, 'canvas.openFile', request)
    assert result['result']['status'] == 'unavailable'
    with app.clients.bind('one'):
        assert app.state['selectedSessionId'] == current
        assert app.state['view']['workSurface'] == 'workspace'


async def test_legacy_file_open_does_not_change_browser_presentation(app, tmp_path):
    (tmp_path / 'plan.md').write_text('# Legacy preview')
    await browse_files(app)
    result = await command(app, 'canvas.openFile', args(app, 'plan.md'))
    assert result['result']['status'] == 'opened'
    with app.clients.bind('one'):
        assert app.state['view']['workSurface'] == 'workspace'


async def test_exact_file_preview_refuses_changed_bytes_without_replacing_view(app, tmp_path):
    path = tmp_path / 'candidate.md'
    path.write_text('original')
    request = {**args(app, path), 'expectedSha256': hashlib.sha256(b'original').hexdigest()}
    assert (await command(app, 'canvas.openFile', request))['result']['status'] == 'opened'
    original_view = view(app)['resourceId']
    path.write_text('different version')
    response = (await command(app, 'canvas.openFile', request))['result']
    assert response['status'] == 'changed'
    assert 'reported artifact version' in response['message']
    assert view(app)['resourceId'] == original_view
    with app.clients.bind('one'):
        assert app.state['canvas']['content'] == 'original'
        assert 'path' not in app.state['canvas']


@pytest.mark.parametrize('legacy', [False, True])
async def test_exact_peer_artifact_uses_retained_execution_folder(app, tmp_path, legacy):
    source = tmp_path / 'other-checkout'
    source.mkdir()
    (source / 'candidate.md').write_text('peer version')
    (tmp_path / 'candidate.md').write_text('main version')
    digest = hashlib.sha256(b'peer version').hexdigest()
    request = {**args(app, 'candidate.md'), 'expectedSha256': digest, 'referenceRequestId': 'exact-peer'}
    response = {'status': 'sealed', 'references': ['candidate.md@sha256:' + digest]}
    if not legacy:
        response['executionDirectory'] = str(source)
    reader = app._session(request['sessionId'])
    producer = app._new_session({'title': 'Peer producer', 'workspace': str(tmp_path)})
    app.state['sessions'].append(producer)
    app.collaboration.insert('exact-peer', 'fixture', {'requestId': 'exact-peer',
        'commandAction': 'coordination.send', 'senderSessionId': reader['id'],
        'target': {'sessionId': producer['id']}, 'workspace': str(tmp_path), 'response': response})
    result = (await command(app, 'canvas.openFile', request))['result']
    assert result['status'] == ('unavailable' if legacy else 'opened')
    if not legacy:
        with app.clients.bind('one'):
            assert app.state['canvas']['content'] == 'peer version'
            assert 'path' not in app.state['canvas']
        with pytest.raises(AppError, match='exact artifact reference'):
            await command(app, 'canvas.openFile', {**request, 'path': 'unreported.md'})


async def test_legacy_peer_file_rejects_moved_producer_before_path_read(app, tmp_path, monkeypatch):
    import amplifier_web.canvas_files as files
    request = {**args(app, 'candidate.md'), 'expectedSha256': 'a' * 64,
               'referenceRequestId': 'legacy-location'}
    reader = app._session(request['sessionId'])
    producer = app._new_session({'title': 'Peer producer', 'workspace': str(tmp_path)})
    app.state['sessions'].append(producer)
    app.collaboration.insert('legacy-location', 'fixture', {
        'requestId': 'legacy-location', 'commandAction': 'coordination.send',
        'senderSessionId': reader['id'], 'target': {'sessionId': producer['id']},
        'workspace': str(tmp_path), 'response': {'status': 'sealed',
            'executionDirectory': str(tmp_path), 'references': ['candidate.md@sha256:' + 'a' * 64]}})
    producer['workspace'] = str(tmp_path / 'moved')
    monkeypatch.setattr(files, 'canvas_command',
                        lambda *a, **kw: pytest.fail('A moved producer path must not be read'))
    with pytest.raises(AppError, match='workspace|location'):
        await command(app, 'canvas.openFile', request)


async def test_missing_escape_and_symlink_do_not_replace_canvas(app, tmp_path):
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Preserve me'})
    original = view(app)['resourceId']
    outside = tmp_path.parent / ('outside-' + tmp_path.name + '.md')
    outside.write_text('Private')
    (tmp_path / 'escape.md').symlink_to(outside)
    try:
        for path in ['missing.md', str(outside), 'escape.md']:
            result = await command(app, 'canvas.openFile', args(app, path))
            assert result['result']['status'] == 'unavailable'
            assert 'Private' not in str(result['result'])
            assert view(app)['resourceId'] == original
    finally:
        outside.unlink()


async def test_stale_session_or_workspace_does_not_retarget(app, tmp_path):
    (tmp_path / 'plan.md').write_text('Original')
    request = args(app, 'plan.md')
    wrong = await command(app, 'canvas.openFile', {**request, 'workspace': '/changed'})
    assert wrong['result']['status'] == 'unavailable'
    await command(app, 'session.create', {})
    with app.clients.bind('one'):
        selected = app.state['selectedSessionId']
        before = copy.deepcopy(app.state['canvas'])
    result = await command(app, 'canvas.openFile', request)
    assert result['result']['status'] == 'unavailable'
    with app.clients.bind('one'):
        assert app.state['selectedSessionId'] == selected
        assert app.state['canvas'] == before


async def test_dirty_view_guard_and_agent_scope(app, tmp_path):
    (tmp_path / 'plan.md').write_text('# Opened')
    request = args(app, 'plan.md')
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Editing'})
    await command(app, 'canvas.views.dirty', {**target(view(app)), 'dirty': True})
    with pytest.raises(AppError, match='viewer edit'):
        await command(app, 'canvas.openFile', request)
    await command(app, 'canvas.views.dirty', {**target(view(app)), 'dirty': False})
    result = await app.app_bridge('dispatch', {'action': 'canvas.openFile', 'args': {**request, 'clientId': 'one'}}, request['sessionId'])
    assert result['result']['status'] == 'opened'
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'canvas.openFile', 'args': {**request, 'sessionId': 'foreign'}}, request['sessionId'])


async def test_sent_attachment_opens_without_sending_and_keeps_other_client(app):
    import base64
    from amplifier_web.attachments import save
    attachment = save(app.data_dir, 'Review notes.md', base64.b64encode(b'# Review notes\nOriginal attachment').decode())
    with app.clients.bind('one'):
        session = app._session()
        session['messages'].append({'id': 'attached', 'role': 'user', 'text': 'Review this', 'attachments': [attachment]})
        sid = session['id']
        messages = copy.deepcopy(session['messages'])
    await command(app, 'view.update', {'patch': {'draft': 'Keep private draft'}})
    other = copy.deepcopy(app.clients.records['two'])
    result = await command(app, 'canvas.openAttachment', {'sessionId': sid, 'id': attachment['id']})
    assert result['result']['status'] == 'opened'
    with app.clients.bind('one'):
        assert app.state['canvas']['kind'] == 'markdown'
        assert app.state['canvas']['title'] == 'Review notes.md'
        assert app.state['canvas']['content'] == '# Review notes\nOriginal attachment'
        assert app.state['view']['draft'] == 'Keep private draft'
        assert app._session()['messages'] == messages
    assert app.clients.records['two'] == other
    await command(app, 'canvas.views.dirty', {**target(view(app)), 'dirty': True})
    with pytest.raises(AppError, match='viewer edit'):
        await command(app, 'canvas.openAttachment', {'sessionId': sid, 'id': attachment['id']})


async def test_attachment_cannot_cross_chat_or_preview_unsent_files(app):
    import base64
    from amplifier_web.attachments import save
    attachment = save(app.data_dir, 'Private.md', base64.b64encode(b'# Private').decode())
    with app.clients.bind('one'):
        sid = app._session()['id']
    request = {'sessionId': sid, 'id': attachment['id']}
    with pytest.raises(AppError, match='not available'):
        await command(app, 'canvas.openAttachment', request)
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'canvas.openAttachment', 'args': {**request, 'sessionId': 'foreign'}}, sid)
    await command(app, 'session.create', {})
    with pytest.raises(AppError, match='chat changed'):
        await command(app, 'canvas.openAttachment', request)
