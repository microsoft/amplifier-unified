import copy
import json
from pathlib import Path
import uuid

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.session_files import amplifier_home, project_slug
from amplifier_web import managed_deletion
from test_service import Runtime
from test_live_clients import command
from test_automatic_history import native_session


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    for client in ('web','agent'):
        service.clients.attach(client, kind='web' if client=='web' else 'api')
    yield service
    if not service.closed:
        await service.close()


async def create(app, command_id=None):
    result = await command(app, 'web', 'session.create', {'location':{'kind':'managed'}}, command_id=command_id)
    return app._session(result['sessionId'])


async def preview(app, sid, client='web'):
    return (await command(app, client, 'session.deletePreview', {'id':sid}, origin='agent' if client=='agent' else 'ui'))['result']


async def delete(app, value, client='web'):
    return await command(app, client, 'session.delete', {'id':value['id'],'confirmationToken':value['confirmationToken']}, origin='agent' if client=='agent' else 'ui')


@pytest.mark.parametrize('client',['web','agent'])
async def test_delete_owned_files_history_and_creation_receipt_without_replay(app, tmp_path, client):
    row = await create(app, 'first-send'); sid=row['id']; files=Path(row['workspace'])
    (files/'result.txt').write_text('Delete only this result')
    native = native_session(files, sid)
    await app.history.refresh()
    # Runtime settings are separate from managed user files and native history.
    runtime = app.data_dir/'sessions'/sid; runtime.mkdir(parents=True, exist_ok=True)
    (runtime/'configuration.json').write_text('{}')
    value = await preview(app, sid, client)
    assert value['summary']['conversationCount']==1 and value['summary']['fileBytes']>0
    assert files.is_dir() and app._session(sid)
    result = await delete(app, value, client)
    assert result['result']=={'id':sid,'deleted':True,'cleanupPending':False}
    assert not files.parent.exists() and not runtime.exists()
    assert not (amplifier_home()/'projects'/project_slug(files)/'sessions'/sid).exists()
    assert not app.state['sessions']
    assert not app.runtime.sent and not app.runtime.started
    again=await delete(app,value,client);assert again['result']['deleted']
    retry=await command(app,'web','session.create',{'location':{'kind':'managed'}},command_id='first-send')
    assert retry['deleted'] and retry['accepted'] is False
    with pytest.raises(AppError,match='permanently deleted'):
        await command(app,'web','session.create',{'id':sid,'location':{'kind':'managed'}})
    await app.history.refresh();assert not app.state['sessions']
    await app.close()
    restored=AppService(app.data_dir,Runtime(),workspace=tmp_path)
    try:
        await restored.history.refresh();assert not restored.state['sessions']
        assert not files.parent.exists()
    finally:await restored.close()


async def test_workspace_and_legacy_blank_location_cannot_delete(app):
    value=await command(app,'web','session.create',{})
    sid=value['sessionId'];before=copy.deepcopy(app._session(sid))
    with pytest.raises(AppError,match='Only chats created without a workspace'):
        await preview(app,sid)
    with pytest.raises(AppError):
        await command(app,'web','session.delete',{'id':sid})
    assert app._session(sid)==before


async def test_confirmation_expiry_changes_and_active_work_refuse(app):
    row=await create(app);files=Path(row['workspace']);sid=row['id']
    value=await preview(app,sid)
    (files/'new.txt').write_text('Changed after review')
    with pytest.raises(AppError,match='changed since'):
        await delete(app,value)
    value=await preview(app,sid)
    encoded=app.db.execute('SELECT value FROM managed_deletions WHERE id=?',(sid,)).fetchone()[0]
    saved=json.loads(encoded);saved['expiresAt']=0
    app.db.execute('UPDATE managed_deletions SET value=? WHERE id=?',(json.dumps(saved),sid));app.db.commit()
    with pytest.raises(AppError,match='expired'):await delete(app,value)
    row['status']='working'
    with pytest.raises(AppError,match='active work'):await preview(app,sid)
    row['status']='idle';row['approvals']=[{'id':'a','status':'pending'}]
    with pytest.raises(AppError,match='active work'):await preview(app,sid)
    assert (files/'new.txt').read_text()=='Changed after review'


async def test_symlink_and_unowned_marker_fail_without_touching_outside(app,tmp_path):
    row=await create(app);files=Path(row['workspace']);outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('keep')
    (files/'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(AppError,match='symbolic link'):await preview(app,row['id'])
    assert (outside/'keep').read_text()=='keep'
    (files/'link').unlink()
    marker=files.parent/'managed-chat.json';saved=marker.read_text();marker.write_text('{}')
    with pytest.raises(AppError,match='ownership'):await preview(app,row['id'])
    marker.write_text(saved)
    (files/'safe.txt').write_text('safe')
    value=await preview(app,row['id']);files.rename(files.with_name('old-files'));files.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AppError,match='symbolic link'):await delete(app,value)
    assert (outside/'keep').read_text()=='keep'


async def test_other_root_sharing_managed_folder_is_blocked_even_without_location(app):
    row=await create(app)
    second=copy.deepcopy(row);second['id']=str(uuid.uuid4());second.pop('location');app.state['sessions'].append(second)
    with pytest.raises(AppError,match='Another conversation uses'):
        await preview(app,row['id'])
    assert Path(row['workspace']).is_dir()


async def test_children_are_owned_and_unrelated_history_refuses(app):
    row=await create(app);files=Path(row['workspace'])
    native_session(files,row['id'])
    child=str(uuid.uuid4());native_session(files,child,metadata={'parent_id':row['id']})
    value=await preview(app,row['id']);assert value['summary']['workerCount']==1
    foreign=str(uuid.uuid4());native_session(files,foreign)
    with pytest.raises(AppError,match='Unrelated history'):await preview(app,row['id'])
    import shutil
    shutil.rmtree(amplifier_home()/'projects'/project_slug(files)/'sessions'/foreign)
    value=await preview(app,row['id']);await delete(app,value)
    assert not (amplifier_home()/'projects'/project_slug(files)/'sessions'/child).exists()


async def test_preserves_shared_attachments_and_artifact_bodies_revokes_share(app):
    from amplifier_web.attachments import save
    from amplifier_web.resource_files import put
    row=await create(app);other=await create(app)
    unique=save(app.data_dir,'unique.txt','dW5pcXVl');shared=save(app.data_dir,'shared.txt','c2hhcmVk')
    row['messages']=[{'id':'m','role':'user','text':'uploaded','attachments':[unique,shared]}]
    other['messages']=[{'id':'m2','role':'user','text':'independent copy','attachments':[shared]}]
    body=put(app.db,{'html':'shared artifact'});private=put(app.db,{'text':'private artifact'})
    app.state['canvasArtifacts']=[{'id':'a','sessionId':row['id'],'body':body},{'id':'b','sessionId':other['id'],'body':body},{'id':'c','sessionId':row['id'],'body':private}]
    app.db.execute('INSERT INTO conversation_shares VALUES (?,?,?,?,?)',('share','token',row['id'],0,json.dumps({'sessionId':row['id'],'content':private})))
    app._save()
    value=await preview(app,row['id']);assert value['summary']['attachmentCount']==1 and value['summary']['shareCount']==1
    await delete(app,value)
    assert not (app.data_dir/'attachments'/unique['id']).exists()
    assert (app.data_dir/'attachments'/shared['id']/'content').exists()
    assert (app.data_dir/'artifacts'/(body['$resource']+'.json')).exists()
    assert not (app.data_dir/'artifacts'/(private['$resource']+'.json')).exists()
    assert not app.db.execute('SELECT id FROM conversation_shares').fetchall()
    assert app._session(other['id'])['messages']


async def test_confirmed_cleanup_recovers_before_view_hydration(app,tmp_path,monkeypatch):
    row=await create(app,'recover');sid=row['id'];files=Path(row['workspace'])
    value=await preview(app,sid)
    original=managed_deletion._purge
    monkeypatch.setattr(managed_deletion,'_purge',lambda *a: (_ for _ in ()).throw(OSError('simulated disk interruption')))
    result=await delete(app,value);assert result['result']['cleanupPending']
    assert not app.state['sessions'] and not files.exists()
    assert list((app.data_dir/'chats').glob('.unified-delete-*'))
    await app.close();monkeypatch.setattr(managed_deletion,'_purge',original)
    restored=AppService(app.data_dir,Runtime(),workspace=tmp_path)
    try:
        assert not files.parent.exists() and not restored.state['sessions']
        assert restored.db.execute('SELECT phase FROM managed_deletions WHERE id=?',(sid,)).fetchone()[0]=='done'
    finally:await restored.close()


async def test_independent_recovery_copy_can_delete_without_deleting_original(app):
    original = await create(app)
    result = await command(app, 'web', 'session.recover', {'id': original['id']})
    copied = app._session(result['result']['sessionId'])
    assert copied.get('parentId') == original['id']
    await delete(app, await preview(app, copied['id']))
    assert Path(original['workspace']).is_dir() and app._session(original['id'])
    await delete(app, await preview(app, original['id']))


async def test_shared_checkpoint_is_deleted_but_other_host_lock_refuses(app):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    row = await create(app)
    store = SharedSessionStore(row['workspace'], row['id'])
    held = store.acquire(app='test-other-host')
    held.write([{'role':'user','content':'private saved history'}], bundle='work')
    value = await preview(app, row['id'])
    try:
        with pytest.raises(AppError, match='Another application owns'):
            await delete(app, value)
        assert store.checkpoint_path.is_file()
    finally:
        held.release()
    await delete(app, await preview(app, row['id']))
    assert not store.checkpoint_path.exists()


async def test_new_shared_reference_during_retirement_requires_new_preview(app, monkeypatch):
    from amplifier_web.resource_files import put
    row = await create(app); other = await create(app)
    body = put(app.db, {'html':'must survive the new reference'})
    app.state['canvasArtifacts'] = [{'id':'owned','sessionId':row['id'],'body':body}]
    app._save()
    value = await preview(app,row['id'])
    original_stop = app.runtime.stop
    async def stop(sid):
        await original_stop(sid)
        if sid == row['id']:
            app.state['canvasArtifacts'].append({'id':'copy','sessionId':other['id'],'body':body})
            app._publish()
    monkeypatch.setattr(app.runtime,'stop',stop)
    with pytest.raises(AppError,match='changed'):
        await delete(app,value)
    assert (app.data_dir/'artifacts'/(body['$resource']+'.json')).is_file()
    assert app._session(row['id']) and app._session(other['id'])


async def test_new_identical_resource_after_staging_is_preserved(app, monkeypatch):
    from amplifier_web.resource_files import put
    row = await create(app);other = await create(app)
    content = {'text':'identical new resource'}
    body = put(app.db,content)
    app.state['canvasArtifacts'] = [{'id':'owned','sessionId':row['id'],'body':body}]
    app._save();value = await preview(app,row['id'])
    original_stage = managed_deletion._stage
    def stage(plan):
        original_stage(plan)
        # Simulate another publication after the atomic staging boundary.
        if not any(r.get('id')=='new' for r in app.state['canvasArtifacts']):
            fresh = put(app.db,content)
            app.state['canvasArtifacts'].append({'id':'new','sessionId':other['id'],'body':fresh})
            app._save()
    monkeypatch.setattr(managed_deletion,'_stage',stage)
    result = await delete(app,value)
    assert not result['result']['cleanupPending']
    assert (app.data_dir/'artifacts'/(body['$resource']+'.json')).is_file()
    assert app.db.execute('SELECT id FROM state_resources WHERE id=?',(body['$resource'],)).fetchone()
