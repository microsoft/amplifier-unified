"""Inventory reuse preserves Foundation settings semantics and fresh inputs."""
import json

import pytest

from amplifier_web.host.config import read_config
from amplifier_web.shared_settings import SettingsReadCache, read_settings, settings_paths


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_missing_session_scopes_share_reads_and_results_stay_detached(tmp_path, monkeypatch):
    import amplifier_foundation.settings as foundation
    shared = tmp_path / 'shared'
    write(shared / 'settings.yaml', {'config': {'tools': [{'module': 'tool-example', 'config': {'items': ['original']}}]}})
    original, calls = foundation.read_settings, []

    def read(paths):
        calls.append(tuple(paths))
        return original(paths)

    monkeypatch.setattr(foundation, 'read_settings', read)
    cache = SettingsReadCache()
    for number in range(20):
        workspace = tmp_path / f'workspace-{number}'
        workspace.mkdir()
        value = read_settings(workspace, shared_home=shared, session_id=f'session-{number}', cache=cache)
        assert value['config']['tools'][0]['config']['items'] == ['original']
        value['config']['tools'][0]['config']['items'].append('caller edit')
    assert len(calls) == 1
    # An inventory's cache is discarded; a new inventory performs a fresh read.
    read_settings(tmp_path, shared_home=shared, cache=SettingsReadCache())
    assert len(calls) == 2


def test_cached_scopes_keep_provider_identity_merge_list_replacement_and_validation(tmp_path):
    shared = tmp_path / 'shared'
    paths = settings_paths(tmp_path, shared_home=shared, session_id='saved')
    for scope, path in paths.items():
        write(path, {'config': {'providers': [{'id': 'instance', 'module': 'provider-example',
                    'config': {scope: True, 'model': scope}}]}, 'items': [scope]})
    cache = SettingsReadCache()
    cached = read_config(tmp_path, home=tmp_path / 'app', shared_home=shared, session_id='saved', settings_cache=cache)
    normal = read_config(tmp_path, home=tmp_path / 'app', shared_home=shared, session_id='saved')
    assert cached.settings == normal.settings
    assert cached.providers[0]['config'] == {
        'global': True, 'project': True, 'local': True, 'session': True, 'model': 'session'}
    assert cached.settings['items'] == ['session']
    assert read_settings(tmp_path, shared_home=shared, global_only=True, cache=cache)['items'] == ['global']
    with pytest.raises(ValueError):
        read_config(tmp_path, shared_home=shared, session_id='../unsafe', settings_cache=cache)
    with pytest.raises(FileNotFoundError):
        read_config(tmp_path / 'missing-workspace', shared_home=shared, settings_cache=cache)


def test_creation_replacement_deletion_and_malformed_settings_invalidate(tmp_path):
    shared = tmp_path / 'shared'
    paths = settings_paths(tmp_path, shared_home=shared, session_id='saved')
    write(paths['global'], {'value': 'global'})
    cache = SettingsReadCache()

    def read():
        return read_settings(tmp_path, shared_home=shared, session_id='saved', cache=cache)

    assert read() == {'value': 'global'}
    write(paths['session'], {'value': 'session'})
    assert read() == {'value': 'session'}
    replacement = paths['session'].with_suffix('.replacement')
    write(replacement, {'value': 'replaced'})
    replacement.replace(paths['session'])
    assert read() == {'value': 'replaced'}
    paths['session'].unlink()
    assert read() == {'value': 'global'}
    paths['global'].write_text('not: [valid')
    from yaml import YAMLError
    with pytest.raises(YAMLError):
        read()
    write(paths['global'], {'value': 'repaired'})
    assert read() == {'value': 'repaired'}


def test_changed_during_read_is_not_reused(tmp_path, monkeypatch):
    import amplifier_foundation.settings as foundation
    shared = tmp_path / 'shared'
    path = shared / 'settings.yaml'
    write(path, {'value': 'before'})
    original, calls = foundation.read_settings, []

    def read(paths):
        calls.append(True)
        result = original(paths)
        if len(calls) == 1:
            write(path, {'value': 'after'})
        return result

    monkeypatch.setattr(foundation, 'read_settings', read)
    cache = SettingsReadCache()
    assert read_settings(tmp_path, shared_home=shared, cache=cache) == {'value': 'before'}
    assert read_settings(tmp_path, shared_home=shared, cache=cache) == {'value': 'after'}
    assert read_settings(tmp_path, shared_home=shared, cache=cache) == {'value': 'after'}
    assert len(calls) == 2


def test_inventory_cache_is_bounded(tmp_path, monkeypatch):
    import amplifier_foundation.settings as foundation
    shared = tmp_path / 'shared'
    original, calls = foundation.read_settings, []

    def read(paths):
        calls.append(True)
        return original(paths)

    monkeypatch.setattr(foundation, 'read_settings', read)
    cache = SettingsReadCache(limit=2)
    for number in range(3):
        paths = settings_paths(tmp_path, shared_home=shared, session_id=f'saved-{number}')
        write(paths['session'], {'value': number})
        assert read_settings(tmp_path, shared_home=shared, session_id=f'saved-{number}', cache=cache) == {'value': number}
    assert len(cache._values) == 2
    assert read_settings(tmp_path, shared_home=shared, session_id='saved-0', cache=cache) == {'value': 0}
    assert len(calls) == 4


def test_symlink_target_changes_and_unreadable_inputs_cannot_reuse_cache(tmp_path, monkeypatch):
    from pathlib import Path
    shared = tmp_path / 'shared'
    first, second = tmp_path / 'first.yaml', tmp_path / 'second.yaml'
    write(first, {'value': 'first'})
    write(second, {'value': 'second'})
    shared.mkdir()
    path = shared / 'settings.yaml'
    path.symlink_to(first)
    cache = SettingsReadCache()
    assert read_settings(tmp_path, shared_home=shared, cache=cache) == {'value': 'first'}
    path.unlink()
    path.symlink_to(second)
    assert read_settings(tmp_path, shared_home=shared, cache=cache) == {'value': 'second'}
    original = Path.stat

    def denied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError('denied')
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', denied)
    with pytest.raises(PermissionError):
        read_settings(tmp_path, shared_home=shared, cache=cache)
