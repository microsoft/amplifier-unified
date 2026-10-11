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
    assert result['bytes']==5 and result['rows'][-1]['includedIn']=='Amplifier Unified'
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
