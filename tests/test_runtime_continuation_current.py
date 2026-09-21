"""Current-host regressions: maintenance and failed app replacement must admit work."""
import pytest
from amplifier_web.service import AppService
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_retention import DEFAULT_RETENTION
from amplifier_web.management import Management
from amplifier_web.updates import UpdateManager
from amplifier_web import app_updates, updates
from test_app_updates import prepared_activation

@pytest.mark.parametrize('locked',[False,True])
async def test_repair_preserves_project_and_live_runtime(tmp_path,monkeypatch,locked):
    runtime=RuntimeManager()
    service=AppService(tmp_path/'app',runtime,workspace=tmp_path)
    manager=Management(service);service.management=manager;service.update_manager=UpdateManager(service)
    command=['/fixture/uv','run',*(['--locked'] if locked else []),'--project','/fixture/project','--python','3.13','python','/fixture/worker.py']
    monkeypatch.setattr(RuntimeManager,'_command',lambda self,*args,**kwargs:list(command))
    calls=[]
    async def process(*args,**kwargs):calls.append(args);return ''
    monkeypatch.setattr(updates,'process',process)
    try:
        await manager.perform('maintenance.repair',{})
        assert calls==[('/fixture/uv','sync',*(('--locked',) if locked else ()), '--project','/fixture/project','--python','3.13','--reinstall')]
        assert not service.runtime._closed
    finally:await service.close()

async def test_settings_reset_keeps_runtime_available_with_defaults(tmp_path):
    runtime=RuntimeManager(retention={**DEFAULT_RETENTION,'max_warm_workers':0})
    service=AppService(tmp_path/'app',runtime,workspace=tmp_path)
    manager=Management(service);service.management=manager;service.update_manager=UpdateManager(service)
    try:
        await manager.perform('maintenance.reset',{'parts':['settings'],'apply':True,'confirmation':'RESET'})
        assert not service.runtime._closed
        assert service.runtime.retention.settings==DEFAULT_RETENTION
    finally:await service.close()

async def test_failed_app_probe_keeps_actual_runtime_available(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    service.runtime=RuntimeManager()
    async def process(*args,**kwargs):return '98.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('Retained host must not terminate'))
    try:
        await app_updates.activate(manager)
        assert service.state['updates']['phase']=='error'
        assert not service.runtime._closed
    finally:await service.close()
