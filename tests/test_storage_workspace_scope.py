"""Remembered working directories must not turn personal files into app usage."""
import asyncio
from pathlib import Path

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.storage_catalog import workspace_selection


def test_selection_is_registered_descendants_not_root_or_session_presence(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'cli'))
    root = tmp_path / 'projects'
    home = tmp_path / 'app'
    paths = [tmp_path, root, root / 'registered', tmp_path / 'projects-other',
             root / '..' / 'OneDrive', home / 'workspaces' / 'old-project', home]
    rows = [{'id': str(i), 'path': str(p)} for i, p in enumerate(paths)]
    assert [r['id'] for r in workspace_selection(home, rows, root)] == ['2', '5']
    # A broad configured parent still does not admit the actual user's home.
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'user'))
    assert workspace_selection(home, [{'id': 'home', 'path': str(tmp_path / 'user')}], tmp_path) == []


@pytest.mark.parametrize('progressive', [False, True])
async def test_public_storage_excludes_home_and_unregistered_siblings(tmp_path, monkeypatch, progressive):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'cli'))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    root = tmp_path / 'projects'
    project = root / 'registered'
    unrelated = root / 'unregistered'
    outside = tmp_path / 'OneDrive'
    for path in (project, unrelated, outside):
        path.mkdir(parents=True)
        (path / 'content').write_bytes(b'x' * (17 if path == project else 10000))
    app.state['settings']['workspaces'] = {'defaultRoot': str(root)}
    await app.dispatch('workspace.add', {'path': str(root), 'name': 'Root'})
    await app.dispatch('workspace.add', {'path': str(project), 'name': 'Project without chats'})
    await app.dispatch('workspace.add', {'path': str(outside), 'name': 'Outside'})
    # Links inside the allowed area never import their outside targets.
    (project / 'personal-link').symlink_to(outside, target_is_directory=True)
    from amplifier_web import storage_walk
    original = storage_walk.metadata
    def guarded(entry):
        path = Path(entry.path) if hasattr(entry, 'path') else entry
        assert not path.is_relative_to(outside)
        assert not path.is_relative_to(unrelated)
        return original(entry)
    monkeypatch.setattr(storage_walk, 'metadata', guarded)
    async def read(args):
        result = (await app.dispatch('storage.inspect', {**args, 'progressive': progressive}))['result']
        while result.get('scanning'):
            await asyncio.sleep(.01)
            result = (await app.dispatch('storage.inspect', {**args, 'progressive': True, 'scanId': result['scanId']}))['result']
        assert not result.get('measurementError')
        return result
    try:
        overview = await read({})
        assert next(r for r in overview['rows'] if r['id'] == 'workspaces')['bytes'] == 17
        assert overview['workspaceScope']['root'] == str(root)
        category = await read({'category': 'workspaces', **({'fromScanId': overview['scanId']} if progressive else {})})
        assert category['bytes'] == 17
        assert [r['label'] for r in category['rows'] if r.get('workspaceId')] == ['Project without chats']
        outside_id = next(r['id'] for r in app.state['workspaces'] if r.get('path') == str(outside))
        with pytest.raises(AppError, match='Choose a workspace'):
            await app.dispatch('storage.inspect', {'scope': 'workspace', 'workspaceId': outside_id})
        # Changing root invalidates polls and inherited cached measurements.
        if progressive:
            app.state['settings']['workspaces']['defaultRoot'] = str(tmp_path / 'new-root')
            with pytest.raises(AppError, match='expired'):
                await app.dispatch('storage.inspect', {'scanId': overview['scanId']})
            fresh = await read({'category': 'workspaces', 'fromScanId': category['scanId']})
            assert not any(r.get('workspaceId') for r in fresh['rows'])
    finally:
        await app.close()
