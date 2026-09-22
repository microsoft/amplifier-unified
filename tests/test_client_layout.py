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


async def command(app, patch, **kwargs):
    with app.clients.bind('one'):
        return await app.dispatch('view.update', {'patch': patch}, **kwargs)


async def test_layout_is_client_local_durable_and_does_not_rewrite_catalog(app, monkeypatch):
    await command(app, {'draft': 'Keep my unsent words'})
    with app.clients.bind('one'):
        before = app.browser_state()
        own_queue = app.subscribe()
    with app.clients.bind('two'):
        other_queue = app.subscribe()
    other = deepcopy(app.clients.records['two'])
    histories = deepcopy(app._state['sessions'])
    artifacts = deepcopy(app._state['canvasArtifacts'])
    def forbidden(*args, **kwargs):
        pytest.fail('Layout must not persist or rebuild conversation catalogs')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', forbidden)
        patch.setattr(app, 'state_context', forbidden)
        patch.setattr('amplifier_web.canvas_library.remember', forbidden)
        result = await command(app, {'navExpanded': True, 'navWidth': 300}, command_id='layout-one')
    assert result['state']['view']['navExpanded'] is True
    assert result['state']['view']['draft'] == 'Keep my unsent words'
    assert result['state']['chatNavigation'] is before['chatNavigation']
    assert own_queue.get_nowait()['view']['navWidth'] == 300
    assert other_queue.empty()
    assert app.clients.records['two'] == other
    assert app._state['sessions'] == histories
    assert app._state['canvasArtifacts'] == artifacts
    app.unsubscribe(own_queue)
    app.unsubscribe(other_queue)
    restored = AppService(app.data_dir, workspace=app.default_workspace)
    try:
        assert restored.clients.records['one']['view']['navWidth'] == 300
        assert restored.clients.records['one']['view']['draft'] == 'Keep my unsent words'
    finally:
        await restored.close()


async def test_layout_retries_cas_and_failure_rollback(app, monkeypatch):
    first = await command(app, {'navExpanded': True}, command_id='expand')
    await command(app, {'navExpanded': False}, command_id='collapse')
    duplicate = await command(app, {'navExpanded': True}, command_id='expand')
    assert duplicate['duplicate'] is True
    assert duplicate['revision'] == first['revision']
    assert duplicate['state']['view']['navExpanded'] is False
    with pytest.raises(AppError, match='different contents'):
        await command(app, {'navExpanded': False}, command_id='expand')
    with pytest.raises(AppError, match='app changed'):
        await command(app, {'navExpanded': True}, expected_revision=first['revision'])
    saved = deepcopy(app.clients.records['one'])
    revision = app._state['revision']
    def failed(*args, **kwargs):
        raise OSError('Fixture disk unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(app.clients, 'save', failed)
        with pytest.raises(OSError):
            await command(app, {'navExpanded': True}, command_id='failed-save')
    assert app.clients.records['one'] == saved
    assert app._state['revision'] == revision
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('failed-save',)).fetchone() is None


@pytest.mark.parametrize('patch', [{'navExpanded': 1}, {'navWidth': 215}, {'canvasWidth': float('nan')}])
async def test_invalid_layout_is_rejected(app, patch):
    with pytest.raises(AppError):
        await command(app, patch)


async def test_layout_preserves_pending_progress_and_mixed_updates_use_normal_path(app):
    sid = app._state['selectedSessionId']
    await app.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'new progress'})
    pending = app._progress_publish_task
    assert app._progress_dirty
    await command(app, {'navExpanded': True})
    assert app._progress_dirty and app._progress_publish_task is pending
    await app._flush_pending_progress()
    with app.clients.bind('one'):
        assert app.browser_state()['sessions'][0]['streaming'] == 'new progress'
    mixed = await command(app, {'navExpanded': False, 'draft': 'Mixed draft'})
    assert mixed['state']['view']['draft'] == 'Mixed draft'
    assert mixed['state']['view']['navExpanded'] is False
