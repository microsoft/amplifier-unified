from copy import deepcopy

import pytest

from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    await service.dispatch('session.create', {})
    service.clients.attach('one')
    service.clients.attach('two')
    yield service
    await service.close()


async def command(app, action, args, client='one', **kwargs):
    with app.clients.bind(client):
        return await app.dispatch(action, args, **kwargs)


def visibility(app, open):
    client = app.clients.records['one']
    return {'open': open, 'sessionId': client['selectedSessionId'], 'canvasId': client['canvas']['id']}


async def test_visibility_retains_generation_dirty_edits_and_client_isolation(app, monkeypatch):
    await command(app, 'canvas.show', {'kind': 'markdown', 'content': '# Retained'})
    with app.clients.bind('one'):
        original = app.canvas_views.summary('primary')
    target = {k: original[k] for k in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
    await command(app, 'canvas.views.dirty', {**target, 'dirty': True})
    other = deepcopy(app.clients.records['two'])
    histories = deepcopy(app._state['sessions'])
    artifacts = deepcopy(app._state['canvasArtifacts'])
    def forbidden(*args, **kwargs):
        pytest.fail('Visibility must not discover workspaces or persist the shared catalog')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', forbidden)
        patch.setattr('amplifier_web.workspace_canvas.refresh_workspace_availability', forbidden)
        patch.setattr('amplifier_web.canvas_library.remember', forbidden)
        for open in (False, True, False):
            result = await command(app, 'canvas.visibility', visibility(app, open))
            assert result['state']['canvas']['open'] is open
            current = result['state']['canvasWorkspace']['views'][0]
            assert current['generation'] == original['generation']
            assert current['dirty'] is True
    assert app.clients.records['two'] == other
    assert app._state['sessions'] == histories
    assert app._state['canvasArtifacts'] == artifacts
    for action, args in [('session.draft', {}), ('canvas.show', {'kind': 'text', 'content': 'replacement'}), ('canvas.tabClose', {'id': target['resourceId']})]:
        with pytest.raises(AppError, match='viewer edit'):
            await command(app, action, args)


async def test_visibility_receipts_scope_and_restart(app):
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Kept'})
    args = visibility(app, False)
    first = await command(app, 'canvas.visibility', args, command_id='hide')
    await command(app, 'canvas.visibility', visibility(app, True), command_id='show')
    duplicate = await command(app, 'canvas.visibility', args, command_id='hide')
    assert duplicate['duplicate'] is True
    assert duplicate['state']['canvas']['open'] is True
    assert duplicate['revision'] == first['revision']
    with pytest.raises(AppError, match='different contents'):
        await command(app, 'canvas.visibility', {**args, 'open': True}, command_id='hide')
    with pytest.raises(AppError, match='artifact changed'):
        await command(app, 'canvas.visibility', {**args, 'canvasId': 'stale'})
    with pytest.raises(AppError, match='Attach a client'):
        await app.dispatch('canvas.visibility', args)
    # Agent uses the same action with an explicit attached-client target.
    await app.dispatch('canvas.visibility', {**args, 'clientId': 'one'}, origin='agent')
    await app.close()
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    try:
        assert restored.clients.records['one']['canvas']['open'] is False
        assert restored.clients.records['one']['canvas']['id'] == args['canvasId']
    finally:
        await restored.close()


async def test_first_reopen_and_legacy_close_have_distinct_mount_semantics(app):
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Legacy closed viewer'})
    await command(app, 'canvas.close', {})
    with app.clients.bind('one'):
        before = app.canvas_views.summary('primary')
    result = await command(app, 'canvas.visibility', visibility(app, True))
    assert result['state']['canvasWorkspace']['views'][0]['generation'] == before['generation']
    await command(app, 'canvas.visibility', visibility(app, False))
    await command(app, 'canvas.close', {})
    await command(app, 'canvas.reopen', {})
    with app.clients.bind('one'):
        assert app.canvas_views.summary('primary')['generation'] > before['generation']


async def test_visibility_uses_cached_catalog_projection_and_rolls_back_failed_save(app, monkeypatch):
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Cached'})
    with app.clients.bind('one'):
        before = app.browser_state()
    def forbidden(*args, **kwargs):
        pytest.fail('Cached visibility must not rebuild the chat and workspace catalog')
    with monkeypatch.context() as patch:
        patch.setattr(app, 'state_context', forbidden)
        result = await command(app, 'canvas.visibility', visibility(app, False))
    assert result['state']['chatNavigation'] is before['chatNavigation']
    saved = deepcopy(app.clients.records['one'])
    revision = app._state['revision']
    def failed(*args, **kwargs):
        raise OSError('Fixture disk unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(app.clients, 'save', failed)
        with pytest.raises(OSError):
            await command(app, 'canvas.visibility', visibility(app, True), command_id='failed-save')
    assert app.clients.records['one'] == saved
    assert app._state['revision'] == revision
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('failed-save',)).fetchone() is None
