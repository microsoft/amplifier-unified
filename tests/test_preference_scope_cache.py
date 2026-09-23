"""Derived workspace preferences stay fresh and detached between clients."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web.service import AppService
from amplifier_web import shared_settings


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path, monkeypatch):
    shared = tmp_path / 'shared'
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    write(shared / 'settings.yaml', {'bundle': {'active': 'shared-bundle'},
        'voice': {'preferred_model': 'shared-voice'}, 'credentials': {'token': 'must-not-be-cached'}})
    rows = []
    for name in ('a', 'b'):
        workspace = tmp_path / name
        write(workspace / '.amplifier/settings.yaml', {
            'bundle': {'active': 'bundle-' + name}, 'voice': {'preferred_model': 'voice-' + name}})
        rows.append({'id': name, 'path': str(workspace)})
    app = SimpleNamespace(data_dir=tmp_path / 'app', state={'workspaces': rows,
        'selectedWorkspaceId': 'a', 'settings': {'workspace': str(tmp_path), 'appBundle': 'app-bundle'}})
    return app, shared, rows


def read(app, identity):
    app.state['selectedWorkspaceId'] = identity
    AppService._refresh_shared_preferences(app)
    return app.state['bundleDefaults'], app.state['settings']['preferredVoice']


def watch_reads(monkeypatch):
    original, calls = shared_settings.read_settings, []
    def recorded(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(shared_settings, 'read_settings', recorded)
    return calls


def test_alternating_scopes_reuse_only_detached_derived_preferences(tmp_path, monkeypatch):
    app, _, _ = fixture(tmp_path, monkeypatch)
    calls = watch_reads(monkeypatch)
    for _ in range(3):
        assert read(app, 'a')[1] == 'voice-a'
        assert read(app, 'b')[1] == 'voice-b'
    assert len(calls) == 2
    read(app, 'a')[0]['effective'] = 'poisoned by caller'
    read(app, 'b')
    assert read(app, 'a')[0]['effective'] == 'bundle-a'
    assert 'credentials' not in repr(app._shared_preferences_cache)
    assert 'must-not-be-cached' not in repr(app._shared_preferences_cache)


def test_same_size_edit_replacement_creation_deletion_and_app_choice_invalidate(tmp_path, monkeypatch):
    app, _, rows = fixture(tmp_path, monkeypatch)
    path = Path(rows[0]['path']) / '.amplifier/settings.yaml'
    read(app, 'a'); read(app, 'b')
    old = path.stat()
    path.write_text(path.read_text().replace('voice-a', 'voice-z'))
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert path.stat().st_size == old.st_size and path.stat().st_mtime_ns == old.st_mtime_ns
    assert read(app, 'a')[1] == 'voice-z'
    replacement = path.with_suffix('.replacement')
    write(replacement, {'voice': {'preferred_model': 'replaced'}})
    replacement.replace(path)
    assert read(app, 'a')[1] == 'replaced'
    local = path.with_name('settings.local.yaml')
    write(local, {'voice': {'preferred_model': 'local'}, 'bundle': {'active': 'local-bundle'}})
    assert read(app, 'a')[1] == 'local'
    assert read(app, 'a')[0]['effective'] == 'local-bundle'
    local.unlink(); path.unlink()
    assert read(app, 'a')[0]['effective'] == 'app-bundle'
    app.state['settings']['appBundle'] = 'another-app'
    assert read(app, 'a')[0]['effective'] == 'another-app'


def test_symlink_retarget_and_managed_scope_changes_are_observed(tmp_path, monkeypatch):
    app, _, rows = fixture(tmp_path, monkeypatch)
    link = tmp_path / 'linked'
    link.symlink_to(rows[0]['path'], target_is_directory=True)
    app.state['workspaces'].append({'id': 'link', 'path': str(link)})
    assert read(app, 'link')[1] == 'voice-a'
    link.unlink(); link.symlink_to(rows[1]['path'], target_is_directory=True)
    assert read(app, 'link')[1] == 'voice-b'
    identity = str(uuid.uuid4())
    workspace = tmp_path / 'chats' / identity / 'files'
    write(workspace / '.amplifier/settings.yaml', {'voice': {'preferred_model': 'ordinary'}})
    app.state['workspaces'].append({'id': 'managed', 'path': str(workspace)})
    assert read(app, 'managed')[1] == 'ordinary'
    marker = workspace.parent / 'managed-chat.json'
    write(marker, {'version': 1, 'id': identity, 'workspace': str(workspace)})
    assert read(app, 'managed')[1] == 'shared-voice'
    assert read(app, 'managed')[0]['workspacePath'] == str(workspace)
    marker.unlink()
    assert read(app, 'managed')[1] == 'ordinary'


def test_distinct_global_only_workspaces_keep_their_output_path(tmp_path, monkeypatch):
    app, _, _ = fixture(tmp_path, monkeypatch)
    for number in range(2):
        identity = str(uuid.uuid4())
        workspace = tmp_path / 'chats' / identity / 'files'
        workspace.mkdir(parents=True)
        write(workspace.parent / 'managed-chat.json', {'version': 1, 'id': identity, 'workspace': str(workspace)})
        app.state['workspaces'].append({'id': str(number), 'path': str(workspace)})
    paths = [read(app, str(i))[0]['workspacePath'] for i in (0, 1, 0)]
    assert paths[0] != paths[1] and paths[0] == paths[2]


def test_permission_and_parse_errors_are_not_hidden_by_cached_preferences(tmp_path, monkeypatch):
    app, _, rows = fixture(tmp_path, monkeypatch)
    path = Path(rows[0]['path']) / '.amplifier/settings.yaml'
    read(app, 'a'); read(app, 'b')
    original = Path.stat
    def denied(value, *args, **kwargs):
        if value == path:
            raise PermissionError('changed access')
        return original(value, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'stat', denied)
        with pytest.raises(PermissionError):
            read(app, 'a')
    path.write_text('voice: [invalid')
    from yaml import YAMLError
    with pytest.raises(YAMLError):
        read(app, 'a')
    write(path, {'voice': {'preferred_model': 'repaired'}})
    assert read(app, 'a')[1] == 'repaired'


def test_change_during_read_is_not_cached_or_short_circuited(tmp_path, monkeypatch):
    app, _, rows = fixture(tmp_path, monkeypatch)
    path = Path(rows[0]['path']) / '.amplifier/settings.yaml'
    original, calls = shared_settings.read_settings, []
    def changing(*args, **kwargs):
        value = original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            write(path, {'voice': {'preferred_model': 'after-read'}})
        return value
    monkeypatch.setattr(shared_settings, 'read_settings', changing)
    assert read(app, 'a')[1] == 'voice-a'
    assert app._shared_preferences_stamp is None and not app._shared_preferences_cache
    assert read(app, 'a')[1] == 'after-read'
    assert len(calls) == 2


def test_preference_cache_is_bounded_and_evicted_scopes_are_reread(tmp_path, monkeypatch):
    app, _, _ = fixture(tmp_path, monkeypatch)
    calls = watch_reads(monkeypatch)
    for number in range(130):
        workspace = tmp_path / f'empty-{number}'
        workspace.mkdir()
        app.state['workspaces'].append({'id': str(number), 'path': str(workspace)})
        assert read(app, str(number))[1] == 'shared-voice'
    assert len(app._shared_preferences_cache) == 128 and len(calls) == 130
    read(app, '0')
    assert len(calls) == 131 and len(app._shared_preferences_cache) == 128
