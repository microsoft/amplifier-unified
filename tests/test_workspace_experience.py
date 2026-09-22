"""Placement and native history use the same actions for users and agents."""
import asyncio
import copy
import json
from pathlib import Path
import uuid

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.session_files import amplifier_home, project_slug


@pytest.fixture
async def app(tmp_path):
    workspace = tmp_path / 'original'
    workspace.mkdir()
    service = AppService(tmp_path / 'app', workspace=workspace)
    yield service
    await service.close()


async def prepare(app, name='Launch plan', **args):
    return (await app.dispatch('workspace.prepare', {'name': name, **args}))['result']


@pytest.mark.parametrize('origin', ['ui', 'agent'])
async def test_name_only_creation_and_retry_preserve_identity(app, origin):
    plan = await prepare(app)
    assert plan['disposition'] == 'create'
    path = Path(plan['path'])
    assert path == app.data_dir / 'workspaces' / 'launch-plan'
    assert not path.exists() and not app.state['sessions']
    args = {'planId': plan['planId']}
    created = await app.dispatch('workspace.create', args, origin=origin, command_id='create-workspace')
    replay = await app.dispatch('workspace.create', args, origin=origin, command_id='create-workspace')
    assert replay['duplicate'] and replay['result'] == created['result']
    assert path.is_dir() and not list(path.iterdir())
    assert not app.state['sessions']
    assert app.state['view']['newSessionDraft']['workspace'] == str(path)
    assert (await prepare(app))['disposition'] == 'open'


async def test_root_change_only_affects_future_workspaces(app, tmp_path):
    stale = await prepare(app)
    root = tmp_path / 'dev'
    await app.dispatch('settings.update', {'patch': {'workspaces': {'defaultRoot': str(root)}}})
    with pytest.raises(AppError, match='default folder changed'):
        await app.dispatch('workspace.create', {'planId': stale['planId']}, command_id='stale')
    assert not Path(stale['path']).exists()
    plan = await prepare(app, 'Café reports')
    assert plan['path'] == str(root / 'café-reports')
    await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='new-root')
    identity = app.state['selectedWorkspaceId']
    await app.dispatch('workspace.rename', {'id': identity, 'name': 'Weekly reports'})
    await app.dispatch('settings.update', {'patch': {'workspaces': {'defaultRoot': ''}}})
    row = next(row for row in app.state['workspaces'] if row['id'] == identity)
    assert row['path'] == plan['path'] and row['name'] == 'Weekly reports'
    assert app.state['workspaceDefaults']['root'] == str(app.data_dir / 'workspaces')


@pytest.mark.parametrize('name', ['../escape', 'a/b', 'a\\b', '.', '..', 'CON', '   ', '\0bad'])
async def test_invalid_name_never_allocates(app, name):
    with pytest.raises(AppError):
        await prepare(app, name)
    assert not (app.data_dir / 'workspaces').exists()


async def test_existing_folder_collision_requires_explicit_attach(app, tmp_path):
    root = tmp_path / 'dev'; root.mkdir()
    folder = root / 'Launch-Plan'; folder.mkdir()
    important = folder / 'important.txt'; important.write_text('Keep this exact content')
    before = important.stat().st_mtime_ns
    plan = await prepare(app, root=str(root))
    assert plan['disposition'] == 'attach' and plan['path'] == str(folder)
    with pytest.raises(AppError, match='already exists'):
        await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='collision')
    await app.dispatch('workspace.add', {'path': str(folder)})
    assert important.read_text() == 'Keep this exact content' and important.stat().st_mtime_ns == before
    assert list(root.iterdir()) == [folder]


async def test_collision_after_prepare_and_symlink_swap_do_not_write_elsewhere(app, tmp_path):
    root = tmp_path / 'dev'; root.mkdir()
    plan = await prepare(app, root=str(root))
    root.rename(tmp_path / 'original-dev')
    outside = tmp_path / 'outside'; outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AppError, match='destination changed'):
        await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='swap')
    assert not list(outside.iterdir())


async def test_parallel_creates_never_adopt_or_suffix_collision(app):
    first, second = await prepare(app), await prepare(app)
    results = await asyncio.gather(*(app.dispatch('workspace.create', {'planId': p['planId']}, command_id=p['planId']) for p in [first, second]), return_exceptions=True)
    assert sum(isinstance(r, AppError) for r in results) == 1
    assert [p.name for p in (app.data_dir / 'workspaces').iterdir()] == ['launch-plan']


async def test_attach_returns_to_same_draft_with_files_model_and_bundle(app, tmp_path):
    app.clients.attach('web', kind='web')
    token = app.clients.current.set('web')
    await app.dispatch('session.draft', {'location': {'kind': 'managed'}})
    setup = {'workspace': '', 'location': {'kind': 'managed'}, 'bundle': 'work', 'selection': {'instance': 'provider', 'model': 'model'}}
    await app.dispatch('view.update', {'patch': {'draft': 'Keep my unsent request', 'newSessionDraft': setup}})
    attached = await app.dispatch('attachment.add', {'sessionId': None, 'name': 'notes.txt', 'base64': 'aGVsbG8='})
    attachments = copy.deepcopy(attached['state']['draftAttachments'])
    result = await app.dispatch('workspace.add', {'path': str(tmp_path), 'fromDraft': True})
    state = result['state']
    assert state['view']['draft'] == 'Keep my unsent request'
    assert state['draftAttachments'] == attachments and not state['sessions']
    assert state['view']['newSessionDraft'] == {**setup, 'workspace': str(tmp_path), 'location': {'kind': 'workspace'}}
    app.clients.current.reset(token)


async def test_attach_discovers_native_roots_and_children_without_import(app, tmp_path):
    folder = tmp_path / 'cli'; folder.mkdir()
    native = str(uuid.uuid4())
    directory = amplifier_home() / 'projects' / project_slug(folder) / 'sessions' / native
    directory.mkdir(parents=True)
    (directory / 'metadata.json').write_text(json.dumps({'session_id': native, 'working_dir': str(folder), 'name': 'Existing CLI chat', 'turn_count': 1}))
    transcript = directory / 'transcript.jsonl'
    transcript.write_text(json.dumps({'role': 'user', 'content': 'Original native text'}) + '\n')
    before = transcript.read_bytes()
    await app.dispatch('workspace.add', {'path': str(folder)})
    await asyncio.gather(*list(app.tasks))
    rows = [row for row in app.state['sessions'] if row.get('nativeIdentity') == native]
    assert len(rows) == 1
    caller = app._new_session({'workspace': str(folder)}); app.state['sessions'].append(caller)
    result = await app.app_bridge('history', {'action': 'read', 'session_id': native}, caller['id'])
    assert result['session']['id'] == native
    assert result['session']['sessionRef']['nativeProject'] == project_slug(folder)
    assert result['messages'][0]['text'] == 'Original native text'
    selected = await app.dispatch('session.select', {'id': native, 'nativeProject': project_slug(folder)})
    assert selected['state']['selectedSessionId'] == rows[0]['id']
    assert transcript.read_bytes() == before


async def test_duplicate_native_ids_require_scope(app, tmp_path):
    first = app._new_session({}); first.update(nativeIdentity='same-native', runtimeSessionId='same-native', nativeProject='-one')
    second = app._new_session({}); second.update(nativeIdentity='same-native', runtimeSessionId='same-native', nativeProject='-two')
    app.state['sessions'].extend([first, second])
    with pytest.raises(AppError, match='more than one project'):
        await app.dispatch('session.pin', {'id': 'same-native', 'pinned': True})
    await app.dispatch('session.pin', {'id': 'same-native', 'nativeProject': '-two', 'pinned': True})
    assert app.state['pinnedSessionIds'] == [second['id']]


async def test_workspace_list_includes_empty_folders_and_disambiguates(app, tmp_path):
    for parent in ['one', 'two']:
        folder = tmp_path / parent / 'reports'; folder.mkdir(parents=True)
        await app.dispatch('workspace.add', {'path': str(folder)})
    result = (await app.dispatch('workspace.list', {'query': 'reports'}))['result']
    assert len(result['items']) == 2
    assert all(row['path'] in row['label'] for row in result['items'])
    assert not app.state['sessions']


async def test_lost_ack_with_new_transport_id_reuses_plan(app):
    plan = await prepare(app)
    first = await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='first-transport')
    second = await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='second-transport')
    assert first['result'] == second['result']
    assert list((app.data_dir / 'workspaces').iterdir()) == [Path(plan['path'])]


async def test_interrupted_placement_preserves_unknown_without_replay(app, monkeypatch):
    from amplifier_web import workspace_placement
    plan = await prepare(app)
    original = workspace_placement.os.mkdir
    def crash(path, *args, **kwargs):
        if path == 'launch-plan':
            original(path, *args, **kwargs)
            raise OSError('simulated failure after mkdir')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(workspace_placement.os, 'mkdir', crash)
    with pytest.raises(AppError, match='Inspect the destination'):
        await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='interrupted')
    assert Path(plan['path']).is_dir()
    monkeypatch.setattr(workspace_placement.os, 'mkdir', original)
    with pytest.raises(AppError, match='interrupted'):
        await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='retry-new-transport')
    assert (await prepare(app))['disposition'] == 'attach'


async def test_global_new_chat_can_choose_no_workspace_and_local_chat_selects_folder(app):
    await app.dispatch('session.draft', {'location': {'kind': 'managed'}})
    assert app.state['view']['newSessionDraft']['location'] == {'kind': 'managed'}
    workspace = app.state['workspaces'][0]
    await app.dispatch('session.draft', {'workspace': workspace['path']})
    assert app.state['view']['newSessionDraft']['workspace'] == workspace['path']
    assert app.state['view']['newSessionDraft']['location'] == {'kind': 'workspace'}
    assert not app.state['sessions']


async def test_agent_native_alias_keeps_existing_caller_authority(app):
    own = app._new_session({}); own.update(nativeIdentity='native-own', runtimeSessionId='native-own')
    peer = app._new_session({}); peer.update(nativeIdentity='native-peer', runtimeSessionId='native-peer')
    app.state['sessions'].extend([own, peer])
    result = await app.app_bridge('action.dispatch', {'action': 'worktree.list', 'args': {'sessionId': 'native-own'}}, own['id'])
    assert result['accepted'] and result['result']['historyHome'] == own['workspace']
    with pytest.raises(AppError, match='calling task'):
        await app.app_bridge('action.dispatch', {'action': 'worktree.list', 'args': {'sessionId': 'native-peer'}}, own['id'])


async def test_unavailable_native_store_keeps_index_and_reports_incomplete(app, tmp_path):
    folder = Path(app.state['workspaces'][0]['path'])
    home = amplifier_home()
    directory = home / 'projects' / project_slug(folder) / 'sessions' / 'native-history'
    directory.mkdir(parents=True)
    (directory / 'metadata.json').write_text(json.dumps({'working_dir': str(folder), 'turn_count': 1}))
    (directory / 'transcript.jsonl').write_text(json.dumps({'role': 'user', 'content': 'Keep me'})+'\n')
    await app.history.refresh()
    (home / 'projects').rename(home / 'temporarily-unavailable')
    result = await app.app_bridge('history', {'action': 'list'}, 'native-history')
    assert not result['complete']
    assert result['discovery']['issues'][0]['kind'] == 'unavailable-root'
    assert result['items'][0]['id'] == 'native-history'
    with pytest.raises(ValueError, match='unavailable'):
        await app.app_bridge('history', {'action': 'read', 'session_id': 'native-history'}, 'native-history')


async def test_replaying_plan_does_not_undo_a_later_workspace_rename(app):
    plan = await prepare(app)
    first = await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='before-rename')
    await app.dispatch('workspace.rename', {'id': first['result']['workspaceId'], 'name': 'New display name'})
    await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='after-rename')
    assert next(row for row in app.state['workspaces'] if row['id'] == first['result']['workspaceId'])['name'] == 'New display name'


async def test_destination_replaced_while_opening_is_not_used(app, tmp_path, monkeypatch):
    import os
    root = tmp_path / 'dev'; root.mkdir()
    plan = await prepare(app, root=str(root))
    original_open = os.open
    swapped = False

    def replace_at_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(path) == root:
            swapped = True
            root.rename(tmp_path / 'reviewed-dev')
            root.mkdir()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', replace_at_open)
    with pytest.raises(AppError, match='destination changed'):
        await app.dispatch('workspace.create', {'planId': plan['planId']}, command_id='replace-on-open')
    assert swapped and not list(root.iterdir()) and not list((tmp_path / 'reviewed-dev').iterdir())
