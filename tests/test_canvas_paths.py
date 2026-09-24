from pathlib import Path

import pytest

from amplifier_web.canvas_paths import paths
from amplifier_web.service import AppError
from test_canvas_views import app, command, view, target, renderer, validate_fixture


async def test_copy_paths_retains_original_workspace_after_restart_and_deleted_file(app):
    root = Path(app.default_workspace)
    source = root / 'notes' / 'hello world.md'
    source.parent.mkdir()
    source.write_text('# Preserved file')
    await command(app, 'canvas.show', {'kind': 'markdown', 'path': str(source)})
    current = view(app)
    source.unlink()
    assert current['filePaths'] == {'absolute': str(source.resolve()), 'relative': 'notes/hello world.md'}
    history = list(app.state['sessions'][0]['messages'])
    copied = await command(app, 'canvas.copyPath', {'id': current['resourceId'], 'format': 'relative'})
    assert copied['result']['path'] == 'notes/hello world.md'
    assert copied['effects'][0]['content'] == 'notes/hello world.md'
    assert app.state['sessions'][0]['messages'] == history
    await app.close()
    from amplifier_web.service import AppService
    restored = AppService(app.data_dir, workspace=root)
    try:
        assert view(restored)['filePaths'] == current['filePaths']
    finally:
        await restored.close()


async def test_agent_copy_uses_shared_target_and_only_intended_client(app):
    source = Path(app.default_workspace) / 'file.txt'
    source.write_text('content')
    await command(app, 'canvas.show', {'kind': 'text', 'path': str(source)})
    current = view(app)
    digest = await renderer(app, resourceKinds=['text'])
    validate_fixture(app, digest)
    await command(app, 'canvas.views.renderer', {**target(current), 'renderer': digest})
    current = view(app)
    response = await app.app_bridge('dispatch', {'action': 'canvas.views.command', 'args': {
        'clientId': 'one', **target(current), 'action': 'canvas.copyPath', 'args': {'format': 'absolute'}}}, current['resource']['sessionId'])
    assert response['result']['path'] == str(source.resolve())
    assert app.clients.records['one']['deviceCommands'][-1]['content'] == str(source.resolve())
    assert not app.clients.records['two']['deviceCommands']
    await command(app, 'canvas.show', {'kind': 'text', 'content': 'Inline'})
    with pytest.raises(AppError, match='changed'):
        await command(app, 'canvas.views.command', {**target(current), 'action': 'canvas.copyPath', 'args': {'format': 'absolute'}})
    with pytest.raises(AppError, match='no saved'):
        await command(app, 'canvas.copyPath', {'id': view(app)['resourceId'], 'format': 'absolute'})


def test_unknown_or_outside_workspace_does_not_invent_a_relative_path():
    assert paths({}, {'path': '/other/file.txt', 'workspacePath': '/workspace'}) == {'absolute': '/other/file.txt'}
    assert paths({}, {'path': 'relative/legacy.txt'}) == {}
    assert paths({}, {}) == {}
