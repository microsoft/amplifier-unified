"""Native chat actions stay scoped to the intended workspace/session."""
import asyncio
import json

import pytest

from amplifier_web.service import AppError
from test_automatic_history import app_factory, native_session, native_rows, select, finish_actions


async def test_workspace_chat_deletion_refuses_without_changing_selection(tmp_path, app_factory):
    native_session(tmp_path/'first', 'one')
    native_session(tmp_path/'first', 'two')
    app=app_factory(); await app.history.refresh()
    one=next(row for row in native_rows(app) if row['nativeIdentity']=='one')
    await select(app, one['id'])
    with pytest.raises(AppError, match='Only chats created without a workspace'):
        await app.dispatch('session.deletePreview', {'id':one['id']})
    assert app.state['selectedSessionId']==one['id']
    assert app._session()['historyLoaded']


async def test_selection_change_during_lazy_load_cannot_send_to_other_chat(tmp_path, app_factory, monkeypatch):
    native_session(tmp_path/'first', 'one')
    native_session(tmp_path/'other', 'two')
    app=app_factory(); await app.history.refresh()
    one,two=native_rows(app)
    app.state['selectedSessionId']=one['id']
    entered=asyncio.Event(); release=asyncio.Event()
    original=app.history.ensure_loaded
    async def delayed(sid):
        entered.set(); await release.wait(); await original(sid)
    monkeypatch.setattr(app.history,'ensure_loaded',delayed)
    action=asyncio.create_task(app.dispatch('conversation.send',{'text':'Do not send this to another chat'}))
    await entered.wait()
    app.state['selectedSessionId']=two['id']; release.set()
    with pytest.raises(AppError,match='selected chat changed'):
        await action
    assert not app.runtime.sent
    assert all(row.get('historyManaged') for row in app.state['sessions'] if row.get('nativeIdentity'))


async def test_invalid_send_does_not_promote_native_chat(tmp_path, app_factory):
    native_session(tmp_path/'first', 'one')
    app=app_factory(); await app.history.refresh()
    row=native_rows(app)[0]; await select(app,row['id'])
    with pytest.raises(AppError,match='Enter a message'):
        await app.dispatch('conversation.send',{'text':' '})
    assert app._session()['historyManaged']


async def test_removed_unresolved_workspace_can_be_restored_by_explicit_chat_selection(tmp_path, app_factory):
    folder=native_session(tmp_path/'unknown', 'old', metadata={'working_dir':None})
    (folder/'context-intelligence/metadata.json').write_text('{}')
    app=app_factory(); await app.history.refresh()
    row=native_rows(app)[0]
    assert row['workspace'] is None
    await select(app,row['id'])
    await app.dispatch('workspace.remove',{'id':row['workspaceId']}); await finish_actions(app)
    assert app.state['settings']['workspace']
    await select(app,row['id'])
    assert app.state['selectedWorkspaceId']==row['workspaceId']
    assert app._session()['messages']
    assert row['workspaceId'] not in app.state.get('hiddenNativeWorkspaces',[])


async def test_history_export_reads_native_files_in_unresolved_project(tmp_path, app_factory):
    from amplifier_web.management import Management
    folder=native_session(tmp_path/'unknown', 'root_child:worker', metadata={'working_dir':None})
    (folder/'context-intelligence/metadata.json').write_text('{}')
    app=app_factory(); await app.history.refresh()
    row=native_rows(app)[0]; await select(app,row['id'])
    manager=Management(app); app.management=manager
    before={str(p):p.stat().st_mtime_ns for p in folder.rglob('*') if p.is_file()}
    await manager.perform('history.export',{'sessionId':row['id'],'format':'jsonl'})
    effect=app.state['deviceCommands'][-1]
    assert [json.loads(line)['role'] for line in effect['content'].splitlines()]==['user','assistant']
    assert before=={str(p):p.stat().st_mtime_ns for p in folder.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='legacy|Worker|original workspace'):
        await manager.ensure_runtime(app._session())


async def test_explicit_native_rename_is_shared_without_promoting_history(tmp_path, app_factory):
    from amplifier_web.naming import automatic
    folder=native_session(tmp_path/'unknown', 'root_child:worker', metadata={'working_dir':None})
    (folder/'context-intelligence/metadata.json').write_text('{}')
    app=app_factory(); await app.history.refresh()
    row=native_rows(app)[0]
    before=(folder/'transcript.jsonl').read_bytes()
    await app.dispatch('session.rename', {'id':row['id'],'title':'My saved worker'})
    assert not (folder/'naming.json').exists()  # No competing title authority.
    assert json.loads((folder/'metadata.json').read_text())['name']=='My saved worker'
    assert json.loads((folder/'metadata.json').read_text())['session_id']=='root_child:worker'
    assert app._session(row['id'])['historyManaged']
    assert (folder/'transcript.jsonl').read_bytes()==before
    assert not automatic({'titleSource':'native','nativeNameSource':'manual'})


async def test_explicit_workspace_add_unhides_both_unresolved_and_resolved_ids(tmp_path, app_factory):
    workspace=tmp_path/'unknown'
    folder=native_session(workspace, 'saved', metadata={'working_dir':None})
    (folder/'context-intelligence/metadata.json').write_text('{}')
    app=app_factory(); await app.history.refresh()
    row=native_rows(app)[0]
    await app.dispatch('workspace.remove',{'id':row['workspaceId']}); await finish_actions(app)
    metadata=json.loads((folder/'metadata.json').read_text()); metadata['working_dir']=str(workspace)
    (folder/'metadata.json').write_text(json.dumps(metadata))
    await app.history.refresh()
    assert not any(w.get('path')==str(workspace) for w in app.state['workspaces'])
    await app.dispatch('workspace.add',{'path':str(workspace)}); await finish_actions(app)
    await app.history.refresh()
    assert sum(w.get('path')==str(workspace) for w in app.state['workspaces'])==1
    assert app._session(row['id'])['workspace']==str(workspace)


async def test_unresolved_chat_cannot_redirect_project_settings_to_another_folder(tmp_path, app_factory):
    from amplifier_web.management import Management
    folder=native_session(tmp_path/'unknown', 'saved', metadata={'working_dir':None})
    (folder/'context-intelligence/metadata.json').write_text('{}')
    app=app_factory(); await app.history.refresh()
    await select(app,native_rows(app)[0]['id'])
    manager=Management(app); app.management=manager
    for scope in ('project','local'):
        with pytest.raises(ValueError,match='existing workspace'):
            manager.configuration_session({'scope':scope})
    assert manager.configuration_session({'scope':'global'})['workspace']==app.default_workspace


async def test_settings_changes_do_not_mount_or_republish_thousands_of_indexed_chats(tmp_path, app_factory, monkeypatch):
    from amplifier_web.management import Management
    app=app_factory()
    app.state['sessions']=[{'id':str(i),'title':'CLI chat','status':'idle','workspace':None,'nativeProject':'unknown','nativeIdentity':str(i),'historyManaged':True,'messages':[],'workers':[],'approvals':[]} for i in range(5000)]
    manager=Management(app); app.management=manager
    refreshed=[]
    async def record(identity): refreshed.append(identity)
    monkeypatch.setattr(app,'refresh_configuration',record)
    revision=app.state['revision']
    await manager.invalidate_configuration()
    assert refreshed==[]
    assert app.state['revision']==revision+1
    assert not any(s.get('configurationPending') for s in app.state['sessions'])


async def test_workspace_and_delete_choose_roots_without_hiding_explicit_worker_history(tmp_path, app_factory):
    native_session(tmp_path/'first', 'root')
    native_session(tmp_path/'first', 'root-child', metadata={'parent_id':'root'})
    app=app_factory(); await app.history.refresh()
    root=next(s for s in native_rows(app) if s['nativeIdentity']=='root')
    child=next(s for s in native_rows(app) if s['nativeIdentity']=='root-child')
    # Works with the pre-upgrade index too, before the next classification scan.
    app._session(child['id'])['nativeParentId']='root'
    app.state['sessions'].sort(key=lambda s:s['id']!=child['id'])
    await app.dispatch('workspace.select',{'id':root['workspaceId']}); await finish_actions(app)
    assert app.state['selectedSessionId']==root['id']
    await select(app,child['id'])
    assert app._session()['id']==child['id'] and app._session()['messages']
    await app.dispatch('workspace.select',{'id':root['workspaceId']}); await finish_actions(app)
    assert app.state['selectedSessionId']==root['id']
    with pytest.raises(AppError, match='Only chats created without a workspace'):
        await app.dispatch('session.deletePreview',{'id':root['id']})
    assert app.state['selectedSessionId'] == root['id']
    assert app._session(child['id'])['messages']
