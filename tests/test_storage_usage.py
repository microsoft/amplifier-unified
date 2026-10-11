import json
from pathlib import Path
import pytest
from amplifier_web.service import AppService, AppError
from amplifier_web.storage_usage import inspect

@pytest.fixture
async def app(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'cli'))
    app=AppService(tmp_path/'app',workspace=tmp_path/'initial')
    yield app
    await app.close()

async def folder(app,tmp_path):
    workspace=tmp_path/'cleanup-project';workspace.mkdir()
    (workspace/'data.txt').write_text('keep history, remove files')
    await app.dispatch('workspace.add',{'path':str(workspace)})
    return workspace,next(row['id'] for row in app.state['workspaces'] if row.get('path')==str(workspace))

async def test_cleanup_retains_history_and_has_durable_no_replay_receipt(app,tmp_path):
    path,wid=await folder(app,tmp_path)
    await app.dispatch('session.create',{'title':'Retain me','workspace':str(path)})
    session=app._session();sid=session['id'];session['messages']=[{'id':'one','role':'user','text':'Retained text'}]
    app._publish_changes(sessions={sid})
    preview=(await app.dispatch('workspace.deleteFilesPreview',{'id':wid}))['result']
    assert preview['inventory']['fileCount']==1 and path.is_dir()
    args={'id':wid,'confirmationToken':preview['token'],'confirmationPath':str(path)}
    with pytest.raises(AppError):await app.dispatch('workspace.deleteFiles',{**args,'confirmationPath':'wrong'})
    response=await app.dispatch('workspace.deleteFiles',args)
    assert response['result']['status']=='completed' and not path.exists()
    assert app._session(sid)['messages'][0]['text']=='Retained text'
    assert sid in app.state['conversationOrganization']['archived']
    # Recreating an unrelated folder after deletion must not make retry remove it.
    path.mkdir();(path/'new.txt').write_text('new owner')
    await app.dispatch('workspace.deleteFiles',args)
    assert (path/'new.txt').read_text()=='new owner'

async def test_cleanup_refuses_changed_linked_active_overlapping_and_cli_folders(app,tmp_path):
    path,wid=await folder(app,tmp_path)
    preview=(await app.dispatch('workspace.deleteFilesPreview',{'id':wid}))['result']
    (path/'later').write_text('not reviewed')
    with pytest.raises(AppError,match='changed'):
        await app.dispatch('workspace.deleteFiles',{'id':wid,'confirmationToken':preview['token'],'confirmationPath':str(path)})
    (path/'link').symlink_to(tmp_path/'cli')
    with pytest.raises(AppError,match='symbolic'):await app.dispatch('workspace.deleteFilesPreview',{'id':wid})
    (path/'link').unlink();(path/'.amplifier').mkdir()
    with pytest.raises(AppError,match='CLI configuration'):await app.dispatch('workspace.deleteFilesPreview',{'id':wid})
    (path/'.amplifier').rmdir();child=path/'nested';child.mkdir()
    await app.dispatch('workspace.add',{'path':str(child)})
    with pytest.raises(AppError,match='overlaps'):await app.dispatch('workspace.deleteFilesPreview',{'id':wid})
    app.state['workspaces']=[r for r in app.state['workspaces'] if r.get('path')!=str(child)]
    await app.dispatch('session.create',{'title':'Busy','workspace':str(path)})
    app._session()['status']='working'
    with pytest.raises(AppError,match='active work'):await app.dispatch('workspace.deleteFilesPreview',{'id':wid})
    app._session()['status']='idle'

def test_storage_counts_once_and_drills_without_following_links(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'cli'))
    home=tmp_path/'app';workspace=home/'workspaces'/'one';workspace.mkdir(parents=True)
    (workspace/'data').write_bytes(b'12345');(workspace/'link').symlink_to(tmp_path)
    rows=[{'id':'one','path':str(workspace),'name':'One'}]
    result=inspect(home,rows,{})
    assert result['bytes']==5
    assert next(row for row in result['rows'] if row['id']=='workspaces')['bytes']==5
    drill=inspect(home,rows,{'scope':'workspace','workspaceId':'one'})
    assert drill['bytes']==5 and drill['incomplete']
    with pytest.raises(ValueError):inspect(home,rows,{'scope':'app','path':'../cli'})

async def test_interrupted_cleanup_preserves_receipt_and_never_replays(app,tmp_path,monkeypatch):
    from amplifier_web import storage_usage
    path,wid=await folder(app,tmp_path)
    review=(await app.dispatch('workspace.deleteFilesPreview',{'id':wid}))['result']
    def fail(path,inventory,quarantine):path.rename(quarantine);raise OSError('test interruption')
    monkeypatch.setattr(storage_usage,'_remove',fail)
    args={'id':wid,'confirmationToken':review['token'],'confirmationPath':str(path)}
    result=(await app.dispatch('workspace.deleteFiles',args))['result']
    assert result['status']=='interrupted' and result['deletedBytes'] is None
    assert (Path(result['cleanupPath'])/'data.txt').exists()
    with pytest.raises(AppError,match='inspection'):await app.dispatch('workspace.deleteFilesPreview',{'id':wid})
    assert (await app.dispatch('workspace.deleteFiles',args))['result']==result

async def test_workspace_archive_and_cleanup_receipt_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'cli'))
    app=AppService(tmp_path/'app',workspace=tmp_path/'initial')
    path,wid=await folder(app,tmp_path)
    await app.dispatch('session.create',{'title':'Retained after restart','workspace':str(path)})
    sid=app._session()['id'];app._session()['messages']=[{'id':'saved','role':'assistant','text':'Durable history'}]
    app._publish_changes(sessions={sid})
    preview=(await app.dispatch('workspace.deleteFilesPreview',{'id':wid}))['result']
    args={'id':wid,'confirmationToken':preview['token'],'confirmationPath':str(path)}
    await app.dispatch('workspace.deleteFiles',args)
    await app.close()
    app=AppService(tmp_path/'app',workspace=tmp_path/'initial')
    try:
        assert next(row for row in app.state['workspaces'] if row['id']==wid)['archived']
        assert sid in app.state['conversationOrganization']['archived']
        assert app._session(sid)['messages'][0]['text']=='Durable history'
        path.mkdir();(path/'new').write_text('Keep new owner')
        assert (await app.dispatch('workspace.deleteFiles',args))['result']['status']=='completed'
        assert (path/'new').exists()
    finally:await app.close()

async def test_cleanup_unavailable_before_mutation_without_safe_directory_removal(app,tmp_path,monkeypatch):
    import shutil
    path,wid=await folder(app,tmp_path)
    monkeypatch.setattr(shutil.rmtree,'avoids_symlink_attacks',False)
    with pytest.raises(AppError,match='unavailable on this host'):
        await app.dispatch('workspace.deleteFilesPreview',{'id':wid})
    assert (path/'data.txt').exists()


def test_content_categories_exclude_nested_roots_cli_config_and_duplicate_inodes(tmp_path, monkeypatch):
    import os
    from amplifier_web.storage_catalog import inspect as categories
    home=tmp_path/'app';cli=tmp_path/'cli';workspace=tmp_path/'projects'
    monkeypatch.setenv('AMPLIFIER_HOME',str(cli))
    values={'app/runtime/worker/data':11,'app/chats/chat/files/image':7,'app/backups/other/backup':13,
            'app/foundation/cache/data':3,'app/config/settings':2,'cli/projects/proj/history':5,
            'cli/keys.env':100,'projects/code':17,'projects/nested/file':19}
    for name,count in values.items():
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x'*count)
    os.link(home/'runtime/worker/data',home/'runtime/worker/hardlink')
    workspaces=[{'id':'all','path':str(tmp_path),'name':'All'},
                {'id':'project','path':str(workspace),'name':'Project'},
                {'id':'nested','path':str(workspace/'nested'),'name':'Nested'}]
    result=categories(home,workspaces,{})
    assert result['bytes']==77
    assert {row['id']:row['bytes'] for row in result['rows']}=={
        'runtime':11,'sessions':12,'backups':13,'cache':3,'other':2,'workspaces':36}
    assert not result['incomplete']
    drill=categories(home,workspaces,{'category':'workspaces'})
    assert [row['label'] for row in drill['rows']]==['Nested','Project']
    assert [row['bytes'] for row in drill['rows']]==[19,17]
    assert not (cli/'config').exists()


def test_storage_bounded_scan_and_protected_root_navigation(tmp_path,monkeypatch):
    from amplifier_web.storage_catalog import inspect as categories
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'cli'))
    home=tmp_path/'app';(home/'runtime').mkdir(parents=True)
    (home/'runtime/data').write_bytes(b'x'*10)
    for i in range(10):(home/'runtime'/str(i)).write_bytes(b'x')
    result=categories(home,[],{},limit=2)
    assert result['incomplete'] and result['bytes']<=20
    root=categories(home,[],{'category':'runtime'})['rows'][0]
    with pytest.raises(ValueError):categories(home,[],{'category':'runtime','rootId':root['id'],'path':'../config'})
    with pytest.raises(ValueError):categories(home,[],{'category':'sessions','rootId':root['id']})
    (home/'linked').symlink_to(tmp_path/'outside')
    assert categories(home,[],{})['incomplete']


async def test_backup_cleanup_preserves_newest_and_refuses_changes_busy_and_replay(app):
    import os
    root=app.data_dir/'backups'
    old=root/('a'*32);new=root/('b'*32)
    for index,path in enumerate([old,new]):
        path.mkdir(parents=True);(path/'private-state.tar.gz').write_bytes(b'private backup')
        os.utime(path,(10+index,10+index))
        os.utime(path/'private-state.tar.gz',(10+index,10+index))
    with pytest.raises(AppError,match='newest'):
        await app.dispatch('storage.backupDeletePreview',{'id':new.name})
    review=(await app.dispatch('storage.backupDeletePreview',{'id':old.name}))['result']
    args={'id':old.name,'confirmationToken':review['token']}
    (old/'changed').write_text('keep')
    with pytest.raises(AppError,match='changed'):await app.dispatch('storage.backupDelete',args)
    review=(await app.dispatch('storage.backupDeletePreview',{'id':old.name}))['result']
    args['confirmationToken']=review['token']
    app.backup_in_progress=True
    with pytest.raises(AppError,match='finish'):await app.dispatch('storage.backupDelete',args)
    app.backup_in_progress=False
    result=(await app.dispatch('storage.backupDelete',args))['result']
    assert result['status']=='completed' and not old.exists() and new.exists()
    old.mkdir();(old/'unrelated').write_text('keep')
    assert (await app.dispatch('storage.backupDelete',args))['result']==result
    assert (old/'unrelated').exists()


async def test_backup_interruption_and_symlinks_are_not_retried(app,monkeypatch):
    import os
    from amplifier_web import storage_usage
    root=app.data_dir/'backups';old=root/('c'*32);new=root/('d'*32)
    for index,path in enumerate([old,new]):
        path.mkdir(parents=True);(path/'private-state.tar.gz').write_bytes(b'backup')
        os.utime(path,(20+index,20+index))
        os.utime(path/'private-state.tar.gz',(20+index,20+index))
    (old/'linked').symlink_to(app.data_dir/'config')
    os.utime(old,(20,20))
    with pytest.raises(AppError,match='symbolic'):await app.dispatch('storage.backupDeletePreview',{'id':old.name})
    (old/'linked').unlink()
    # Removing the link changed old's timestamp, so restore its old age.
    os.utime(old,(20,20))
    review=(await app.dispatch('storage.backupDeletePreview',{'id':old.name}))['result']
    def fail(path,inventory,quarantine):path.rename(quarantine);raise OSError('interruption')
    monkeypatch.setattr(storage_usage,'_remove',fail)
    args={'id':old.name,'confirmationToken':review['token']}
    result=(await app.dispatch('storage.backupDelete',args))['result']
    assert result['status']=='interrupted' and Path(result['cleanupPath']).exists()
    assert (await app.dispatch('storage.backupDelete',args))['result']==result
    with pytest.raises(AppError,match='receipt'):await app.dispatch('storage.backupDeletePreview',{'id':old.name})


def test_managed_chat_groups_owned_locations_under_session_data(tmp_path, monkeypatch):
    from amplifier_web.storage_catalog import inspect as categories
    from amplifier_web.session_files import sessions_dir
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'cli'))
    home=tmp_path/'app';sid='11111111-1111-4111-8111-111111111111'
    workspace=home/'chats'/sid/'files'
    paths=[workspace/'image',home/'sessions'/sid/'record',sessions_dir(workspace)/sid/'transcript.jsonl']
    for index,path in enumerate(paths):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x'*(index+1)*10)
    sessions=[{'id':sid,'title':'One logical chat','workspace':str(workspace),'location':{'kind':'managed'}}]
    workspaces=[{'id':'registered','path':str(workspace),'name':'Managed files'}]
    overview=categories(home,workspaces,{},sessions)
    assert overview['bytes']==60
    assert next(r for r in overview['rows'] if r['id']=='workspaces')['bytes']==0
    drill=categories(home,workspaces,{'category':'sessions'},sessions)
    assert len(drill['rows'])==1 and drill['rows'][0]['bytes']==60
    locations=categories(home,workspaces,drill['rows'][0]['destination'],sessions)
    assert len(locations['rows'])==3 and locations['bytes']==60
    assert {r['label'] for r in locations['rows']}=={'Chat files','Conversation records','Native conversation history'}
    assert not any(r.get('sessionId') for r in locations['rows']), 'Per-folder controls must not masquerade as whole-chat deletion'
