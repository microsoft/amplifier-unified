"""Managed storage is durable, isolated by identity, and never called a sandbox."""
import asyncio
import copy
import json
from pathlib import Path
import uuid

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.managed_chats import allocate, metadata
from amplifier_web.session_files import amplifier_home, project_slug
from amplifier_web.host.config import read_config
from amplifier_web.shared_settings import settings_paths, routing_dirs
from amplifier_web.chat_navigation import snapshot as navigation
from test_service import Runtime
from test_live_clients import command, snapshot
from test_automatic_history import native_session


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    for client in ('web', 'agent'):
        service.clients.attach(client, kind='web' if client == 'web' else 'api')
    yield service
    if not service.closed:
        await service.close()


@pytest.mark.parametrize('client', ['web', 'agent'])
async def test_managed_first_send_uses_private_folder_and_shared_actions(app, tmp_path, client):
    original = copy.deepcopy(app.state['workspaces'])
    await command(app, client, 'session.draft', {'location': {'kind': 'managed'}})
    draft = snapshot(app, client)['view']['newSessionDraft']
    assert draft['workspace'] == '' and draft['location'] == {'kind': 'managed'}
    await command(app, client, 'view.update', {'patch': {'draft': 'Only on send'}})
    await command(app, client, 'attachment.add', {'sessionId': None, 'name': 'notes.txt', 'base64': 'bm90ZXM='})
    assert not (app.data_dir/'chats').exists() and not app.state['sessions']
    created = await command(app, client, 'session.create', {**draft, 'fromDraft': True}, command_id='managed-first')
    sid = created['sessionId']; row = app._session(sid); folder = Path(row['workspace'])
    assert folder == app.data_dir/'chats'/sid/'files' and folder.is_dir()
    assert row['location'] == {'kind': 'managed'} and metadata(folder)['id'] == sid
    assert folder.stat().st_mode & 0o777 == 0o700
    assert app.state['workspaces'] == original
    assert created['state']['selectedWorkspaceId'] is None
    assert not app.runtime.started and not app.runtime.sent
    aid = created['state']['sessions'][0]['draftAttachments'][0]['id']
    await command(app, client, 'conversation.send', {'sessionId': sid, 'text': 'Only on send', 'attachmentIds': [aid]}, command_id='managed-message')
    await command(app, client, 'conversation.send', {'sessionId': sid, 'text': 'Only on send', 'attachmentIds': [aid]}, command_id='managed-message')
    assert len(app.runtime.sent) == 1
    duplicate = await command(app, client, 'session.create', {**draft, 'fromDraft': True}, command_id='managed-first')
    assert duplicate['duplicate'] and duplicate['sessionId'] == sid
    assert len(list((app.data_dir/'chats').iterdir())) == 1


async def test_global_settings_only_even_after_files_add_local_settings(app, tmp_path):
    global_file = amplifier_home()/'settings.yaml'; global_file.parent.mkdir(parents=True, exist_ok=True)
    global_file.write_text('bundle:\n  active: global-bundle\nconfig:\n  providers:\n    - module: provider-global\n      config:\n        model: global-model\n')
    (tmp_path/'.amplifier').mkdir()
    (tmp_path/'.amplifier'/'settings.yaml').write_text('bundle:\n  active: prior-workspace\nconfig:\n  providers:\n    - module: provider-private\n')
    (app.data_dir/'.amplifier').mkdir()
    (app.data_dir/'.amplifier'/'settings.yaml').write_text('bundle:\n  active: app-path-project\n')
    created = await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}}, command_id='scope')
    row = app._session(created['sessionId']); folder = Path(row['workspace'])
    assert row['bundle'] == 'global-bundle'
    (folder/'.amplifier').mkdir(); (folder/'.amplifier'/'settings.yaml').write_text('bundle:\n  active: generated-file-must-not-be-settings\n')
    config = read_config(folder, home=app.data_dir)
    assert config.active_bundle == 'global-bundle'
    assert [p['module'] for p in config.providers] == ['provider-global']
    assert set(settings_paths(folder)) == {'global'}
    assert routing_dirs(folder) == [amplifier_home()/'routing']
    assert set(settings_paths(folder, session_id=row['id'])) == {'global', 'session'}
    from amplifier_web.preferences import SettingsStore
    with pytest.raises(ValueError):
        SettingsStore(app.data_dir).path(folder, 'project')
    # The app's own root may also contain settings; draft discovery ignores them.
    from amplifier_web.setup import SetupManager
    probe = SetupManager(app.data_dir, global_only=True)
    assert probe.config(app.data_dir).active_bundle == 'global-bundle'


async def test_global_provider_discovery_before_managed_creation(app, tmp_path, monkeypatch):
    from amplifier_web.management import Management
    from amplifier_web.setup import SetupManager
    app.management = Management(app)
    seen = []
    def rows(self, workspace):
        seen.append((self.global_only, workspace))
        return []
    monkeypatch.setattr(SetupManager, 'provider_rows', rows)
    await command(app, 'agent', 'providers.list', {'workspace': '', 'location': {'kind': 'managed'}})
    for _ in range(100):
        if seen:
            break
        await asyncio.sleep(.01)
    assert seen and all(global_only and workspace == str(app.data_dir) for global_only, workspace in seen)
    assert app.state['setup']['providersLocation'] == {'kind': 'managed'}
    assert not (app.data_dir/'chats').exists() and not app.state['sessions']


async def test_managed_history_refresh_restart_artifacts_and_retry(app, tmp_path):
    args = {'location': {'kind': 'managed'}, 'fromDraft': True}
    created = await command(app, 'web', 'session.create', args, command_id='restart-receipt')
    sid = created['sessionId']; folder = Path(app._session(sid)['workspace'])
    (folder/'result.md').write_text('# Keep this result')
    shown = await command(app, 'web', 'canvas.show', {'kind': 'auto', 'path': 'result.md', 'sessionId': sid})
    artifact = shown['state']['canvas']['id']
    native_session(folder, sid)
    await app.history.refresh()
    assert app._session(sid)['location'] == {'kind': 'managed'}
    assert app._session(sid)['workspaceAvailable'] is True
    assert not any(w.get('path') == str(folder) for w in app.state['workspaces'])
    await app.close()
    restored = AppService(app.data_dir, Runtime(), workspace=tmp_path)
    try:
        await restored.history.refresh()
        row = restored._session(sid)
        assert row['location'] == {'kind': 'managed'} and row['workspace'] == str(folder)
        assert (folder/'result.md').read_text() == '# Keep this result'
        assert any(a['id'] == artifact for a in restored.state['canvasArtifacts'])
        duplicate = await command(restored, 'web', 'session.create', args, command_id='restart-receipt')
        assert duplicate['duplicate'] and duplicate['sessionId'] == sid
        assert not restored.runtime.started and not restored.runtime.sent
        assert not any(w.get('path') == str(folder) for w in restored.state['workspaces'])
    finally:
        await restored.close()


async def test_all_chats_and_managed_filter_are_authoritative(app):
    workspace = (await command(app, 'web', 'session.create', {'title': 'Project chat'}))['sessionId']
    managed = (await command(app, 'web', 'session.create', {'title': 'Managed chat', 'location': {'kind': 'managed'}}))['sessionId']
    await command(app, 'web', 'view.update', {'patch': {'navChatScope': 'all', 'navLocationFilter': 'all'}})
    state = snapshot(app, 'web'); page = state['chatNavigation']
    assert {r['id'] for r in page['items']} == {workspace, managed}
    row = next(r for r in page['items'] if r['id'] == managed)
    assert row['workspaceId'] is None and row['workspaceLabel'] == 'No workspace'
    await command(app, 'web', 'view.update', {'patch': {'navLocationFilter': 'managed'}})
    assert [r['id'] for r in snapshot(app, 'web')['chatNavigation']['items']] == [managed]
    assert snapshot(app, 'web')['chatNavigation']['scope']['locationFilter'] == 'managed'
    await command(app, 'agent', 'view.update', {'patch': {'navChatScope': 'all'}})
    assert len(snapshot(app, 'agent')['chatNavigation']['items']) == 2


async def test_managed_copy_keeps_original_files_and_history(app):
    result = await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}})
    source = app._session(result['sessionId']); folder = Path(source['workspace'])
    (folder/'keep.md').write_text('Original file')
    native_session(folder, source['id'])
    await app.history.refresh()
    result = await command(app, 'web', 'session.recover', {'id': source['id']})
    target = app._session(result['result']['sessionId'])
    assert target['location'] == {'kind': 'managed'}
    assert target['workspace'] == str(app.data_dir/'chats'/target['id']/'files')
    assert target['workspace'] != source['workspace']
    assert (folder/'keep.md').read_text() == 'Original file'
    assert target['recovery']['workReplayed'] is False
    assert not app.runtime.started and not app.runtime.sent
    assert not any(w.get('path') in {source['workspace'], target['workspace']} for w in app.state['workspaces'])


async def test_invalid_managed_creation_never_allocates_or_overwrites(app, tmp_path):
    with pytest.raises(AppError):
        await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}, 'workspace': str(tmp_path)})
    with pytest.raises(AppError):
        await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}, 'selection': {'model': 'incomplete'}})
    assert not (app.data_dir/'chats').exists()
    identity = str(uuid.uuid4()); existing = app.data_dir/'chats'/identity
    existing.mkdir(parents=True); (existing/'keep.txt').write_text('Never replace')
    with pytest.raises(ValueError, match='allocation record'):
        allocate(app.data_dir, identity, 'attempt')
    assert (existing/'keep.txt').read_text() == 'Never replace'


def test_managed_allocation_refuses_symlink_and_preserves_existing_files(tmp_path):
    home = tmp_path/'app'; home.mkdir(); outside = tmp_path/'outside'; outside.mkdir()
    (home/'chats').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic link'):
        allocate(home, str(uuid.uuid4()), 'attempt')
    assert not list(outside.iterdir())


async def test_managed_default_resolution_is_global_and_has_separate_cache_key(app, monkeypatch):
    from amplifier_web.management import Management
    from amplifier_web import draft_defaults
    app.management = Management(app)
    seen = []
    async def resolve(home, workspace, bundle, app_bundle, **kwargs):
        seen.append((workspace, kwargs))
        return {'bundle': 'work', 'effective': {'model': 'global-model'}}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    await app.management.command('configuration.defaults', {'location': {'kind': 'managed'}})
    assert seen == [(str(app.data_dir), {'global_only': True})]
    assert app.state['draftDefaults']['["","","managed"]']['effective']['model'] == 'global-model'
    assert not (app.data_dir/'chats').exists() and not app.state['sessions']


async def test_workspace_selection_remains_usable_after_managed_chat(app):
    created = await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}})
    original = app.state['workspaces'][0]
    await command(app, 'web', 'workspace.select', {'id': original['id']})
    await command(app, 'web', 'view.update', {'patch': {'navChatScope': 'workspace'}})
    assert snapshot(app, 'web')['selectedWorkspaceId'] == original['id']
    await command(app, 'web', 'session.draft')
    draft = snapshot(app, 'web')['view']['newSessionDraft']
    assert draft['workspace'] == original['path'] and draft.get('location', {}).get('kind') != 'managed'


async def test_agent_bridge_creates_and_inspects_same_managed_location(app):
    source=(await app.dispatch('session.create', {}))['sessionId']
    result=await app.app_bridge('dispatch', {'action':'session.create','args':{'location':{'kind':'managed'},'select':False}}, source)
    row=next(row for row in app.state['sessions'] if row['id'] != source)
    assert row['location'] == {'kind':'managed'}
    observed=await app.app_bridge('get_state', {'path':'/sessions'}, source)
    assert row['id'] in str(observed) and 'managed' in str(observed)
    assert not app.runtime.started and not app.runtime.sent


@pytest.mark.parametrize('kind', ['fifo', 'directory', 'oversized', 'symlink'])
def test_managed_marker_reader_rejects_special_or_unbounded_files(tmp_path, kind, monkeypatch):
    import os
    home = tmp_path/'app'; home.mkdir()
    identity = str(uuid.uuid4())
    files = Path(allocate(home, identity, 'marker-test'))
    marker = files.parent/'managed-chat.json'
    assert metadata(files)['id'] == identity
    marker.unlink()
    if kind == 'fifo':
        if not hasattr(os, 'mkfifo'):
            pytest.skip('FIFO fixtures require POSIX')
        os.mkfifo(marker)
    elif kind == 'directory':
        marker.mkdir()
    elif kind == 'oversized':
        marker.write_bytes(b' ' * 4097)
    else:
        target = tmp_path/'outside-marker.json'; target.write_text('{}')
        marker.symlink_to(target)
    # None of these may even be opened, including a FIFO with no writer.
    def forbidden_open(*args, **kwargs):
        raise AssertionError('Invalid marker was opened')
    monkeypatch.setattr(os, 'open', forbidden_open)
    assert metadata(files) is None


def test_managed_marker_reader_bounds_read_and_rejects_replacement(tmp_path, monkeypatch):
    import os
    if not hasattr(os, 'mkfifo') or not hasattr(os, 'O_NOFOLLOW'):
        pytest.skip('No-follow FIFO race fixture requires POSIX')
    home=tmp_path/'app';home.mkdir()
    files=Path(allocate(home,str(uuid.uuid4()),'marker-race'))
    marker=files.parent/'managed-chat.json'
    original_open=os.open; original_read=os.read; opened=[]
    def replace_on_open(path, flags):
        # Keep the expected inode alive, so the replacement cannot reuse it.
        marker.rename(marker.with_name('old-marker.json'))
        os.mkfifo(marker)
        opened.append(flags)
        return original_open(path, flags)
    monkeypatch.setattr(os, 'open', replace_on_open)
    def forbidden_read(*args):
        raise AssertionError('A substituted FIFO was read')
    monkeypatch.setattr(os, 'read', forbidden_read)
    assert metadata(files) is None
    assert opened[0] & os.O_NONBLOCK and opened[0] & os.O_NOFOLLOW
    monkeypatch.setattr(os,'open',original_open)
    marker.unlink();marker.with_name('old-marker.json').rename(marker)
    def bounded_read(fd, count):
        assert count == 4097
        return original_read(fd,count)
    monkeypatch.setattr(os,'read',bounded_read)
    assert metadata(files) is not None


def test_managed_marker_rejects_noncanonical_layout_before_filesystem_access(monkeypatch):
    def forbidden(*args):
        raise AssertionError('Unrecognized chat layout touched the filesystem')
    monkeypatch.setattr(Path,'is_symlink',forbidden)
    assert metadata('/app/chats/not-a-uuid/files') is None
    assert metadata('/app/chats/00000000000000000000000000000000/files') is None
    assert metadata('/app/workspaces/00000000-0000-0000-0000-000000000000/files') is None
