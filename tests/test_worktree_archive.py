import asyncio
import os
from pathlib import Path
import threading
from unittest.mock import patch

import pytest
from amplifier_worktrees import GitWorktrees
from amplifier_worktrees.git import git
from amplifier_web.service import AppError
from test_worktrees import repository, create, fixture


def test_archive_restore_preserves_index_ignored_untracked_links_and_detached_commit(tmp_path):
    source = repository(tmp_path); manager = GitWorktrees(tmp_path / 'managed')
    record = create(manager, source, session_id='s'); path = Path(record['path'])
    git(path, 'config', 'user.name', 'Fixture'); git(path, 'config', 'user.email', 'fixture@example.invalid')
    (path/'a.txt').write_text('committed'); git(path, 'commit', '-am', 'detached work')
    commit = git(path, 'rev-parse', 'HEAD')
    (path/'a.txt').write_text('staged'); git(path, 'add', 'a.txt')
    (path/'a.txt').write_text('unstaged')
    (path/'ignored.txt').write_text('must retain'); (path/'new').write_text('new')
    (path/'link').symlink_to('new')
    before = git(path, 'status', '--porcelain=v1', '-z')
    archived = manager.relocate(record['id'], record['revision'], 'archive')
    target = Path(archived['path'])
    assert archived['status'] == 'archived' and not path.exists()
    assert (target/'ignored.txt').read_text() == 'must retain'
    assert (target/'a.txt').read_text() == 'unstaged' and git(target, 'show', ':a.txt') == b'staged'
    assert (target/'new').read_text() == 'new' and os.readlink(target/'link') == 'new'
    assert git(target, 'rev-parse', 'HEAD') == commit and git(target, 'status', '--porcelain=v1', '-z') == before
    with pytest.raises(ValueError, match='Restore'): manager.remove(record['id'], archived['revision'])
    restored = manager.relocate(record['id'], archived['revision'], 'restore', restore=True)
    assert restored['status'] == 'ready' and restored['path'] == str(path)
    assert (path/'ignored.txt').read_text() == 'must retain'
    duplicate = manager.relocate(record['id'], record['revision'], 'archive')
    assert duplicate['duplicate'] and path.exists() and not target.exists()
    with pytest.raises(ValueError, match='different contents'):
        manager.relocate(record['id'], restored['revision'], 'archive')


@pytest.mark.parametrize('effect', ['before', 'after', 'replacement'])
def test_lost_move_receipt_reconciles_without_replaying(effect, tmp_path):
    source = repository(tmp_path); manager = GitWorktrees(tmp_path/'managed')
    record = create(manager, source); original_save = manager.save
    def fail_after_intent(value):
        if value['status'] != 'archive_pending': raise OSError('lost receipt')
        original_save(value)
    def move(path, *args, **kwargs):
        if args[:2] == ('worktree','move'):
            if effect != 'before': git(path, *args, **kwargs)
            if effect == 'replacement': Path(path).mkdir(); (Path(path)/'unknown').write_text('retain')
            raise OSError('lost move acknowledgement')
        return git(path, *args, **kwargs)
    with patch('amplifier_worktrees.git.git', side_effect=move), patch.object(manager, 'save', side_effect=fail_after_intent):
        with pytest.raises(OSError): manager.relocate(record['id'],record['revision'],'archive')
    fresh = GitWorktrees(tmp_path/'managed')
    with patch('amplifier_worktrees.git.git', side_effect=lambda path,*args,**kw: pytest.fail('Move replayed') if args[:2]==('worktree','move') else git(path,*args,**kw)):
        result=fresh.relocate(record['id'],record['revision'],'archive')
    assert result['operation']['status'] == {'before':'not_applied','after':'applied','replacement':'unknown'}[effect]
    if effect=='replacement': assert (Path(record['path'])/'unknown').read_text()=='retain'


def test_attached_collision_and_symlink_refused(tmp_path):
    source=repository(tmp_path); manager=GitWorktrees(tmp_path/'managed')
    attached=manager.attach(source,source=source,command_id='attach')
    with pytest.raises(ValueError,match='Only app-created'): manager.relocate(attached['id'],attached['revision'],'archive-attached')
    record=create(manager,source); target=manager.directory/'archives'/record['id']; target.parent.mkdir();target.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError,match='destination'): manager.relocate(record['id'],record['revision'],'collision')
    assert Path(record['path']).exists() and target.is_symlink()


async def test_archive_agent_ui_parity_active_and_foreign_owner_refused(tmp_path,monkeypatch):
    app,runtime,root,sid=await fixture(tmp_path,monkeypatch)
    try:
        record=create(app.worktrees.git,root,session_id=sid)
        args={'id':record['id'],'expectedRevision':record['revision']}
        row=app._session(sid); row['workingDirectory']=record['path']
        with pytest.raises(AppError,match='still uses'): await app.app_bridge('dispatch',{'action':'worktree.archive','id':'archive','args':args},sid)
        row['workingDirectory']=str(root)
        other=(await app.dispatch('session.create',{'select':False}))['sessionId']
        with pytest.raises(AppError,match='another task'): await app.app_bridge('dispatch',{'action':'worktree.archive','id':'foreign','args':args},other)
        archived=(await app.app_bridge('dispatch',{'action':'worktree.archive','id':'archive','args':args},sid))['result']
        result=await app.dispatch('worktree.restore',{'sessionId':sid,'id':record['id'],'expectedRevision':archived['revision']},command_id='restore')
        assert result['result']['status']=='ready' and row['workingDirectory']==str(root)
        runtime.quiesce_for_handoff.assert_not_called()
    finally: await app.close()


async def test_archive_and_restore_through_attached_session_commands(authenticated_client,tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    source=repository(tmp_path)
    app=await create_app(tmp_path/'app',workspace=source,runtime=Runtime(),voice=False,background_updates=False,preload_providers=False)
    client=await authenticated_client(app);service=app['service']
    await service.dispatch('session.create',{});sid=service._session()['id']
    record=create(service.worktrees.git,source,session_id=sid)
    await client.post('/api/clients/attach',json={'clientId':'archive-terminal','kind':'tui'})
    endpoint=f'/api/sessions/{sid}/commands';headers={'X-Amplifier-Client':'archive-terminal'}
    payload={'id':'archive','action':'worktree.archive','args':{'id':record['id'],'expectedRevision':record['revision']}}
    first=await client.post(endpoint,json=payload,headers=headers)
    assert first.status==200
    archived=(await first.json())['result'];assert archived['status']=='archived'
    duplicate=await client.post(endpoint,json=payload,headers=headers)
    assert (await duplicate.json())['result']['duplicate']
    payload={'id':'restore','action':'worktree.restore','args':{'id':record['id'],'expectedRevision':archived['revision']}}
    restored=await client.post(endpoint,json=payload,headers=headers)
    assert (await restored.json())['result']['path']==record['path']
    assert service._session()['id']==sid and not service.runtime.sent


def test_inspection_reconciles_pending_archive_without_retrying_move(tmp_path):
    source=repository(tmp_path);manager=GitWorktrees(tmp_path/'managed');record=create(manager,source)
    save=manager.save
    def lost(value):
        if value['status']=='archive_pending':save(value)
        else:raise OSError('lost final receipt')
    with patch.object(manager,'save',side_effect=lost),pytest.raises(OSError):
        manager.relocate(record['id'],record['revision'],'archive')
    restored=GitWorktrees(tmp_path/'managed')
    with patch('amplifier_worktrees.git.git',side_effect=lambda path,*args,**kw:pytest.fail('Move replayed') if args[:2]==('worktree','move') else git(path,*args,**kw)):
        checked=restored.status(record['id'])
    assert checked['status']=='archived' and checked['archiveOperations']['archive']['status']=='applied'


@pytest.mark.parametrize('placement', ['nested', 'alias', 'history-home'])
@pytest.mark.parametrize('action', ['worktree.archive', 'worktree.remove'])
async def test_retirement_refuses_nested_and_aliased_chat_placements(tmp_path, monkeypatch, placement, action):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        record = create(app.worktrees.git, root, session_id=sid)
        nested = Path(record['path']) / 'nested'; nested.mkdir()
        alias = tmp_path / 'alias'; alias.symlink_to(nested, target_is_directory=True)
        other = (await app.dispatch('session.create', {'select': False}))['sessionId']
        row = app._session(other)
        if placement == 'history-home':
            row.update(workspace=str(alias), workingDirectory=str(root))
        else:
            row['workingDirectory'] = str(alias if placement == 'alias' else nested)
        with pytest.raises(AppError, match='still uses'):
            await app.dispatch(action, {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']})
        assert nested.is_dir() and app.worktrees.git.get(record['id'])['status'] == 'ready'
        runtime.quiesce_for_handoff.assert_not_called()
    finally:
        await app.close()


@pytest.mark.parametrize('cancel', [False, True])
async def test_archive_fences_creation_until_git_thread_finishes(tmp_path, monkeypatch, cancel):
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    release = threading.Event(); started = asyncio.Event(); task = None
    loop = asyncio.get_running_loop()
    try:
        record = create(app.worktrees.git, root, session_id=sid)
        source = Path(record['path']); nested = source / 'nested'; nested.mkdir()
        alias = tmp_path / 'alias'; alias.symlink_to(nested, target_is_directory=True)
        relocate = app.worktrees.git.relocate
        def paused(*args, **kwargs):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(10), 'Test did not release Git operation'
            return relocate(*args, **kwargs)
        monkeypatch.setattr(app.worktrees.git, 'relocate', paused)
        task = asyncio.create_task(app.dispatch('worktree.archive',
            {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}, command_id='archive-race'))
        await asyncio.wait_for(started.wait(), 5)
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        for destination in (nested, alias, app.worktrees.git.directory / 'archives' / record['id']):
            with pytest.raises(AppError, match='being moved'):
                await app.dispatch('session.create', {'workspace': str(destination), 'select': False})
        # This path-specific fence must not stall the app or unrelated chats.
        other = await app.dispatch('session.create', {'workspace': str(root), 'select': False})
        assert other['sessionId'] != sid
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task)['result']['status'] == 'archived'
        assert not source.exists() and not app.worktrees._moving_paths
        assert app.worktrees.git.get(record['id'])['status'] == 'archived'
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await app.close()


@pytest.mark.parametrize('cancel', [False, True])
async def test_preparing_chat_reserves_checkout_until_directory_thread_finishes(tmp_path, monkeypatch, cancel):
    from amplifier_web.workspace_canvas import _create_workspace_folder
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    release = threading.Event(); started = asyncio.Event(); task = None
    loop = asyncio.get_running_loop()
    try:
        record = create(app.worktrees.git, root, session_id=sid)
        nested = Path(record['path']) / 'new-folder'
        def paused(path):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(10), 'Test did not release directory preparation'
            return _create_workspace_folder(path)
        monkeypatch.setattr('amplifier_web.workspace_canvas._create_workspace_folder', paused)
        task = asyncio.create_task(app.dispatch('session.create', {'workspace': str(nested), 'select': False}))
        await asyncio.wait_for(started.wait(), 5)
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        with pytest.raises(AppError, match='being created'):
            await app.dispatch('worktree.archive', {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']})
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert app._session((await task)['sessionId'])['workspace'] == str(nested)
        assert nested.is_dir() and not app.worktrees._creation_paths
        assert app.worktrees.git.get(record['id'])['status'] == 'ready'
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await app.close()


async def test_unresolved_archive_fences_both_paths_after_restart(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    app, runtime, root, sid = await fixture(tmp_path, monkeypatch)
    try:
        record = create(app.worktrees.git, root, session_id=sid)
        source = Path(record['path'])
        def move_and_replace(path, *args, **kwargs):
            result = git(path, *args, **kwargs)
            if args[:2] == ('worktree', 'move'):
                source.mkdir(); (source / 'unknown').write_text('retain')
            return result
        with patch('amplifier_worktrees.git.git', side_effect=move_and_replace):
            moved = (await app.dispatch('worktree.archive', {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}))['result']
        assert moved['status'] == 'archive_unknown'
        await app.close()
        app = AppService(tmp_path / 'app', runtime, workspace=root)
        for destination in (source, Path(moved['operation']['target'])):
            with pytest.raises(AppError, match='unresolved'):
                await app.dispatch('session.create', {'workspace': str(destination), 'select': False})
        assert (source / 'unknown').read_text() == 'retain'
    finally:
        await app.close()
