import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from amplifier_web import app_updates
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager
from test_service import Runtime

async def test_release_check_requires_real_tag_revision(monkeypatch):
    monkeypatch.setattr(app_updates.shutil,'which',lambda _: '/bin/tool')
    calls=[]
    async def process(*args,**kwargs):
        calls.append(args)
        if args[0]=='gh':return json.dumps({'tag_name':'v99.0.0','html_url':'https://github.com/example/release'})
        return 'a'*40+'\trefs/tags/v99.0.0\n'+'b'*40+'\trefs/tags/v99.0.0^{}'
    monkeypatch.setattr(app_updates,'process',process)
    result=await app_updates.check()
    assert result['status']=='update' and result['revision']=='b'*40
    assert len(calls)==2
    async def fail(*args,**kwargs):raise RuntimeError('private credential detail')
    monkeypatch.setattr(app_updates,'process',fail)
    result=await app_updates.check()
    assert result['status']=='check_failed' and 'credential' not in result['detail']

async def test_candidate_isolated_validated_and_busy_host_not_replaced(tmp_path,monkeypatch):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    manager=UpdateManager(service);service.update_manager=manager
    service.state['updates']['application']={'status':'update','revision':'a'*40,'latest':'v99.0.0'}
    calls=[]
    monkeypatch.setattr(app_updates.shutil,'which',lambda _: '/bin/tool')
    async def process(*args,**kwargs):
        calls.append((args,kwargs))
        return '99.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    await app_updates.stage(manager)
    assert calls[0][1]['env']['UV_TOOL_DIR'].startswith(str(tmp_path/'updates/applications'))
    assert service.state['updates']['pendingApp']['revision']=='a'*40
    service._session()['status']='working'
    await app_updates.activate(manager)
    assert len(calls)==2
    assert service.state['updates']['phase']=='app-staged'
    await service.close()

async def test_unvalidated_app_cannot_replace_installed_host(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    service.state['updates']['pendingApp']={'revision':'a'*40}
    with pytest.raises(ValueError,match='validation'):await app_updates.activate(manager)
    await service.close()


@pytest.mark.parametrize("marker_kind", ["missing", "corrupt-json", "list", "null", "number", "string",
                                        "wrong-revision", "parent-is-file"])
async def test_invalid_validation_marker_fails_before_activation_side_effects(tmp_path, monkeypatch, marker_kind):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    revision='a'*40
    service.state['updates']['pendingApp']={'revision':revision,'latest':'v99.0.0'}
    marker=manager.directory/'applications'/revision/'validated.json'
    contents={'corrupt-json':'{','list':'[]','null':'null','number':'42','string':'"text"',
              'wrong-revision':json.dumps({'revision':'b'*40,'version':'99.0.0'})}
    if marker_kind=='parent-is-file':
        marker.parent.parent.mkdir(parents=True,exist_ok=True)
        marker.parent.write_text('not a directory')
    elif marker_kind in contents:
        marker.parent.mkdir(parents=True,exist_ok=True)
        marker.write_text(contents[marker_kind])
    target=AsyncMock(side_effect=AssertionError('must not inspect an installation for an invalid marker'))
    process=AsyncMock(side_effect=AssertionError('must not install or restart for an invalid marker'))
    close=AsyncMock()
    monkeypatch.setattr(app_updates,'installed_target',target)
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr(service.runtime,'close',close)
    try:
        with pytest.raises(ValueError,match='must pass isolated validation'):
            await app_updates.activate(manager)
        target.assert_not_awaited()
        process.assert_not_awaited()
        close.assert_not_awaited()
        assert service.state['updates']['phase']!='activating'
        assert not (manager.directory/'previous-app.json').exists()
    finally:
        await service.close()


async def test_development_host_cannot_overwrite_global_uv_tool(tmp_path,monkeypatch):
    monkeypatch.setattr(app_updates.shutil,'which',lambda name: str(tmp_path/'bin'/name))
    async def process(*args,**kwargs):return str(tmp_path/('bin' if '--bin' in args else 'tools'))
    monkeypatch.setattr(app_updates,'process',process)
    with pytest.raises(ValueError,match='not running from the active uv tool'):
        await app_updates.installed_target()

async def prepared_activation(tmp_path,monkeypatch):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    service.port=8941
    manager=UpdateManager(service);service.update_manager=manager
    release={'status':'update','revision':'a'*40,'latest':'v99.0.0'}
    service.state['updates']['pendingApp']=release
    folder=manager.directory/'applications'/release['revision'];folder.mkdir(parents=True)
    (folder/'validated.json').write_text(json.dumps({'revision':release['revision'],'version':'99.0.0'}))
    candidate=folder/'tools/amplifier-unified/bin/python';candidate.parent.mkdir(parents=True);candidate.touch()
    async def target():return '/fixture/uv','/fixture/launcher',Path('/fixture/python'),{'version':'old','source':'file:///actual/previous/source'}
    monkeypatch.setattr(app_updates,'installed_target',target)
    return service,manager,candidate

async def test_manually_launched_host_with_installed_unit_uses_helper_and_termination(tmp_path,monkeypatch):
    service,manager,candidate=await prepared_activation(tmp_path,monkeypatch)
    calls=[]
    async def process(*args,**kwargs):
        assert service.state['updates']['phase']=='activating'
        calls.append(args)
        return '99.0.0' if '-c' in args else ''
    async def spawn(*args,**kwargs):
        assert service.state['updates']['phase']=='activating'
        assert args[0]==str(candidate)
        with pytest.raises(Exception,match='update is activating'):
            await service.dispatch('conversation.send',{'text':'Must not start during restart'})
        calls.append(('helper',))
    def kill(*args):
        assert service.state['updates']['phase']=='activating'
        calls.append(('terminate',))
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr('amplifier_web.deployment_service.managed_unit', lambda home: True)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda home: False)
    monkeypatch.setattr(app_updates.asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(app_updates.os,'kill',kill)
    await app_updates.activate(manager)
    assert calls[-2:]==[('helper',),('terminate',)]
    assert json.loads((manager.directory/'previous-app.json').read_text())['source']=='file:///actual/previous/source'
    assert service.state['updates']['pendingRestart']['version']=='99.0.0'
    await service.close()


async def test_managed_service_restart_does_not_spawn_a_second_host(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    calls=[]
    async def process(*args,**kwargs):
        calls.append(args)
        return '99.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed',lambda home: True)
    monkeypatch.setattr(app_updates.asyncio,'create_subprocess_exec',lambda *args,**kwargs: pytest.fail('managed service must not spawn a helper'))
    monkeypatch.setattr(app_updates.os,'kill',lambda *args: pytest.fail('managed service must not terminate itself'))
    await app_updates.activate(manager)
    assert calls[-1]==('systemctl','--user','restart','amplifier-unified.service')
    assert service.state['updates']['pendingRestart']['version']=='99.0.0'
    await service.close()


async def test_failed_installed_probe_does_not_terminate_host(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def process(*args,**kwargs):return '98.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('The retained host must not be terminated'))
    await app_updates.activate(manager)
    assert service.state['updates']['phase']=='error'
    assert service.state['updates']['pendingApp'] is None
    await service.close()

async def test_new_host_recognizes_successful_application_restart(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    from amplifier_web import __version__
    service.state['updates']={'phase':'activating','pendingRestart':{'version':__version__},'pendingApp':{'revision':'old'}}
    manager=UpdateManager(service);service.update_manager=manager
    assert service.state['updates']['phase']=='installed'
    assert service.state['updates']['pendingRestart'] is None
    await service.close()

async def test_installed_application_removed_from_pending_updates_but_sources_preserved(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    from amplifier_web import __version__
    app={'id':'application','latest':'v'+__version__,'current':'0.0.1','status':'update'}
    service.state['updates']={'application':app,'items':[app,{'id':'community','status':'update'}],'available':2,'appAvailable':True}
    manager=UpdateManager(service);service.update_manager=manager
    assert service.state['updates']['available']==1
    assert service.state['updates']['items'][0]['status']=='current'
    assert service.state['updates']['items'][1]['status']=='update'
    assert not service.state['updates']['appAvailable']
    await service.close()
