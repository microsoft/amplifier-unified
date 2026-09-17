import tarfile
import pytest
from amplifier_web.service import AppService
from amplifier_web.management import Management
from amplifier_web.updates import UpdateManager
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
    assert (config/'settings.yaml').exists()
    assert (Path(service.state['maintenance']['retained'])/'config/fixture.txt').read_text()=='private fixture'
    assert len(service.state['sessions'])==1
    await service.close()
