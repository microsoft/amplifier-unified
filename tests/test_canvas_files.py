import copy

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
