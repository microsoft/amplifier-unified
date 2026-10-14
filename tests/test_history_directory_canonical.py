"""Dormant app presentation cannot mask newer canonical native metadata."""
import json

import pytest

from amplifier_web.native_history import NativeHistory
from amplifier_web.session_files import amplifier_home, project_slug
from test_automatic_history import app_factory, native_session


async def saved_overlay(app_factory, tmp_path, *, title_source='native', loaded=False):
    workspace = tmp_path / 'native-project'
    path = native_session(workspace, 'dormant-native', metadata={'name': 'Original canonical name'})
    app = app_factory()
    index = NativeHistory(amplifier_home(), cache_path=app.data_dir / 'native-catalog.sqlite3')
    discovered = index.scan()
    app._state['workspaces'].extend(discovered['workspaces'])
    index.close()
    owner = app._session('dormant-native')
    owner.update(title='Saved local title', titleSource=title_source, historyLoaded=loaded)
    if loaded:
        owner['messages'] = [{'id': 'old-message', 'role': 'assistant', 'text': 'Saved loaded transcript'}]
    old_revision = owner['nativeRevision']
    active = app._new_session({'title': 'Unrelated selected owner'})
    app._state['sessions'].append(active)
    app._state['selectedSessionId'] = active['id']
    app._save()
    await app.close()
    return app.data_dir, path, workspace, old_revision


def refresh_cache(home, workspace, path, *, name='Current canonical name', available=True):
    metadata = json.loads((path / 'metadata.json').read_text())
    if name is None:
        metadata.pop('name', None)
    else:
        metadata.update(name=name, name_source='manual')
    metadata['bundle'] = 'bundle:new-canonical'
    (path / 'metadata.json').write_text(json.dumps(metadata))
    with (path / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'assistant', 'content': 'New canonical transcript body'}) + '\n')
    if not available:
        workspace.rename(workspace.with_name('unavailable-project'))
    index = NativeHistory(amplifier_home(), cache_path=home / 'native-catalog.sqlite3')
    index.scan()
    current = index._catalog.get(project_slug(workspace), 'dormant-native')
    index.close()
    return current


@pytest.mark.parametrize('title_source,name', [('native', None), ('native', 'Current canonical name'),
                                              ('manual', None), ('manual', 'Current canonical name')])
async def test_dormant_overlay_uses_canonical_title_authority_before_admission(app_factory, tmp_path, title_source, name):
    home, path, workspace, _ = await saved_overlay(app_factory, tmp_path, title_source=title_source)
    current = refresh_cache(home, workspace, path, name=name)
    restored = app_factory(home=home)
    expected = 'Saved local title' if title_source == 'manual' and name is None else current['title']
    before = {row['id'] for row in restored._state['sessions']}
    assert 'dormant-native' not in before
    metadata = restored.directory.resolve('dormant-native')
    assert metadata['title'] == expected
    state = {**restored._state, 'view': {**restored._state['view'], 'navChatScope': 'all', 'navFilter': expected}}
    page = restored.directory.chats(state)
    assert [row['id'] for row in page['items']] == ['dormant-native']
    assert page['items'][0]['title'] == expected
    assert {row['id'] for row in restored._state['sessions']} == before
    assert restored._session('dormant-native', hydrate=False)['title'] == expected
    assert not restored.runtime.started and not restored.runtime.sent


@pytest.mark.parametrize('loaded', [False, True])
async def test_dormant_metadata_refresh_keeps_loaded_transcript_freshness_boundary(app_factory, tmp_path, loaded):
    home, path, workspace, old_revision = await saved_overlay(app_factory, tmp_path, loaded=loaded, title_source='manual')
    current = refresh_cache(home, workspace, path)
    assert current['transcriptRevision'] != old_revision
    restored = app_factory(home=home)
    metadata = restored.directory.resolve('dormant-native')
    assert metadata['bundle'] == current['bundle']
    assert metadata['nativeRevision'] == (old_revision if loaded else current['transcriptRevision'])
    assert 'dormant-native' not in {row['id'] for row in restored._state['sessions']}
    admitted = restored._session('dormant-native', hydrate=False)
    assert admitted['nativeRevision'] == metadata['nativeRevision']
    assert admitted['bundle'] == current['bundle']
    if loaded:
        # Native presentations are disposable. Admission cannot revive a
        # metadata-only loaded flag after $native hydration cleared its body.
        assert admitted['historyLoaded'] is False
        await restored.history.load(admitted['id'])
        assert admitted['messages'][-1]['text'] == 'New canonical transcript body'
    assert not restored.runtime.started and not restored.runtime.sent


async def test_newly_observed_native_rename_is_not_reverted_by_older_cache(app_factory, tmp_path):
    home, path, workspace, _ = await saved_overlay(app_factory, tmp_path, title_source='manual')
    app = app_factory(home=home)
    # The normal rename writes canonical metadata and records its revision
    # before the asynchronous native discovery watcher can update its cache.
    await app.dispatch('session.rename', {'id': 'dormant-native', 'title': 'Newest user rename'}, include_state=False)
    current = json.loads((path / 'metadata.json').read_text())
    assert current['name'] == 'Newest user rename' and current['name_revision'] > 0
    assert app.directory.resolve('dormant-native')['title'] == 'Newest user rename'
    await app.close()
    restarted = app_factory(home=home)
    assert 'dormant-native' not in {row['id'] for row in restarted._state['sessions']}
    assert restarted.directory.resolve('dormant-native')['title'] == 'Newest user rename'
    # A later external canonical rename has a newer revision and takes over.
    current.update(name='Later external rename', name_revision=current['name_revision'] + 1)
    (path / 'metadata.json').write_text(json.dumps(current))
    index = NativeHistory(amplifier_home(), cache_path=home / 'native-catalog.sqlite3')
    index.scan(); index.close()
    assert restarted.directory.resolve('dormant-native')['title'] == 'Later external rename'
    assert not restarted.runtime.started and not restarted.runtime.sent


async def test_dormant_native_overlay_uses_current_workspace_availability(app_factory, tmp_path):
    home, path, workspace, _ = await saved_overlay(app_factory, tmp_path)
    refresh_cache(home, workspace, path, available=False)
    restored = app_factory(home=home)
    metadata = restored.directory.resolve('dormant-native')
    assert metadata['workspace'] == str(workspace)
    assert metadata['workspaceAvailable'] is False
    assert 'dormant-native' not in {row['id'] for row in restored._state['sessions']}
    assert not restored.runtime.started and not restored.runtime.sent
