import tarfile
import pytest
from amplifier_web.service import AppService
from amplifier_web.management import Management
from amplifier_web.updates import UpdateManager
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_retention import DEFAULT_RETENTION
from test_service import Runtime

async def test_full_backup_and_selected_reset_are_private_and_reversible(tmp_path):
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    manager=Management(service);service.management=manager;service.update_manager=UpdateManager(service)
    await service.dispatch('session.create',{})
    config=service.data_dir/'config';config.mkdir(exist_ok=True);(config/'fixture.txt').write_text('private fixture')
    await manager.perform('maintenance.backup',{})
    from pathlib import Path
    archive=Path(service.state['maintenance']['backup'])
    assert archive.stat().st_mode&0o777==0o600
    with tarfile.open(archive) as output:assert 'config/fixture.txt' in output.getnames() and 'app.sqlite3' in output.getnames()
    await manager.perform('maintenance.reset',{'parts':['settings']})
    assert (config/'fixture.txt').exists()
    with pytest.raises(ValueError):await manager.perform('maintenance.reset',{'parts':['settings'],'apply':True})
    await manager.perform('maintenance.reset',{'parts':['settings'],'apply':True,'confirmation':'RESET'})
    assert not (config/'fixture.txt').exists()
    assert not (config/'settings.yaml').exists()
    assert (Path(service.state['maintenance']['retained'])/'config/fixture.txt').read_text()=='private fixture'
    assert len(service.state['sessions'])==1
    assert service.state['settings']['updates']=={'autoCheck':True,'autoInstall':False,'intervalHours':4}
    await service.close()


async def test_settings_reset_restores_default_runtime_retention(tmp_path):
    original = RuntimeManager(retention={**DEFAULT_RETENTION, 'max_warm_workers': 0,
                                         'idle_timeout_hours': 0, 'prewarm_on_select': False})
    service = AppService(tmp_path/'app', original, workspace=tmp_path)
    manager = Management(service)
    service.management = manager
    service.update_manager = UpdateManager(service)

    await manager.perform('maintenance.reset', {'parts': ['settings'], 'apply': True,
                                                 'confirmation': 'RESET'})

    assert original._closed
    assert service.runtime is not original
    assert service.runtime.retention.settings == DEFAULT_RETENTION
    await service.close()


async def test_backup_skips_native_index_files_and_keeps_owned_artifacts(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from amplifier_web import recovery
    from amplifier_web.session_files import amplifier_home
    from amplifier_web.host.storage import SessionStore
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    owned=service._session()
    SessionStore.for_app(service.data_dir,owned['workspace']).save(owned['id'],[{'role':'user','content':'App work'}],{})
    await service.dispatch('canvas.show',{'kind':'text','title':'Saved app artifact','content':'Keep this snapshot'})
    native_root=amplifier_home()/'projects'/'historical-project'/'sessions'/'root:worker'
    native_root.mkdir(parents=True)
    (native_root/'transcript.jsonl').write_text('{"role":"user","content":"CLI-only history"}\n')
    original=(native_root/'transcript.jsonl').read_bytes()
    service.state['sessions'].extend([
        {'id':'index-null','runtimeSessionId':'root:worker','nativeIdentity':'root:worker','nativeProject':'historical-project',
         'workspace':None,'historyManaged':True,'status':'idle','messages':[],'workers':[]},
        {'id':'index-known','runtimeSessionId':'old-root','nativeProject':'unrelated-project',
         'workspace':str(tmp_path/'unknown-folder'),'historyManaged':True,'status':'idle','messages':[],'workers':[]}])
    owned['workers']=[{'id':'unsupported:worker'}]
    added=[]
    original_add=tarfile.TarFile.add
    def add(archive,path,*args,**kwargs):
        assert not Path(path).is_relative_to(native_root), 'Backup read untouched native history'
        added.append(str(path))
        return original_add(archive,path,*args,**kwargs)
    monkeypatch.setattr(tarfile.TarFile,'add',add)
    result=await recovery.backup(service)
    with tarfile.open(result['backup']) as archive:
        names=archive.getnames()
        assert any(name.endswith('/'+owned['id']+'/transcript.jsonl') for name in names)
        assert not any('historical-project' in name or 'unrelated-project' in name or 'root:worker' in name for name in names)
        assert any(name.startswith('artifacts/') for name in names)
    assert (native_root/'transcript.jsonl').read_bytes()==original
    assert not (tmp_path/'unknown-folder').exists()
    assert service.backup_in_progress is False
    await service.close()


async def test_backup_path_failure_releases_in_progress_flag(tmp_path, monkeypatch):
    from amplifier_web import recovery, session_files
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    def broken(_):raise OSError('Fixture path unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(session_files,'sessions_dir',broken)
        with pytest.raises(OSError,match='Fixture path unavailable'):
            await recovery.backup(service)
    assert service.backup_in_progress is False
    await recovery.backup(service)
    await service.close()


async def test_reset_skips_unknown_workspace_settings_and_hides_native_chats(tmp_path):
    import hashlib
    from amplifier_web import recovery
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    manager=Management(service);service.management=manager;service.update_manager=UpdateManager(service)
    await service.dispatch('session.create',{})
    native={'id':'index-only','runtimeSessionId':'root:worker','nativeIdentity':'root:worker',
            'nativeProject':'unknown-project','workspace':None,'historyManaged':True,
            'status':'idle','messages':[],'workers':[]}
    untouched={'id':'index-known','runtimeSessionId':'historical-root','nativeProject':'untouched-project',
               'workspace':str(tmp_path/'untouched'),'historyManaged':True,'status':'idle','messages':[],'workers':[]}
    service.state['sessions'].extend([native,untouched])
    await recovery.reset(manager,{'parts':['settings'],'apply':True,'confirmation':'RESET'})
    assert not native.get('configurationPending') and not untouched.get('configurationPending')
    key=hashlib.sha256(untouched['workspace'].encode()).hexdigest()[:20]
    assert not (service.data_dir/'config/workspaces'/(key+'.yaml')).exists()
    await recovery.reset(manager,{'parts':['conversations'],'apply':True,'confirmation':'RESET'})
    from amplifier_web.automatic_history import identity
    assert identity('unknown-project','root:worker') in service.state['hiddenNativeSessions']
    assert identity('untouched-project','historical-root') in service.state['hiddenNativeSessions']
    assert not service.state['sessions']
    await service.close()
