import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_web import update_retention as retention


def manager_fixture(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    directory = home / 'updates'
    directory.mkdir(parents=True)
    names = [str(i) * 32 for i in range(1, 5)]
    project = home / ('runtime/q-' + 'a' * 32)
    project.mkdir(parents=True)
    for name in names:
        folder = directory / 'releases' / name
        folder.mkdir(parents=True)
        (folder / 'validated.json').write_text(json.dumps({'hostVersion': '0.1.0', 'sources': 1}))
        (folder / 'runtime-installed.json').write_text('[]')
        (folder / 'runtime-project.json').write_text(json.dumps({'project': project.name}))
    (directory / 'active.json').write_text(json.dumps({'current': names[0], 'previous': names[1]}))
    state = {'updates': {'pendingRelease': names[2]}}
    manager = SimpleNamespace(home=home, directory=directory, service=SimpleNamespace(state=state), busy=lambda: False, awaiting_restart=lambda: False)
    monkeypatch.setattr(retention, 'process_references', lambda home: '')
    monkeypatch.setattr(retention, 'configuration_references', lambda manager: '')
    monkeypatch.setattr('amplifier_web.runtime_qualification.verify_recorded', lambda project, folder: None)
    return manager, names, project


async def test_cleanup_keeps_current_rollback_pending_and_shared_runtime(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    result = await retention.reclaim(manager)
    assert result == {'removed': 1, 'retained': 3}
    assert project.exists()
    for name in names[:3]:
        assert (manager.directory / 'releases' / name).exists()
    assert not (manager.directory / 'releases' / names[3]).exists()


async def test_unknown_references_or_modified_receipts_never_trigger_removal(tmp_path, monkeypatch):
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    def unavailable(home):
        raise ValueError('cannot inspect processes')
    monkeypatch.setattr(retention, 'process_references', unavailable)
    result = await retention.reclaim(manager)
    assert result['removed'] == 0
    monkeypatch.setattr(retention, 'process_references', lambda home: str(manager.directory / 'releases' / names[3]))
    assert (await retention.reclaim(manager))['removed'] == 0
    monkeypatch.setattr(retention, 'process_references', lambda home: '')
    (manager.directory / 'releases' / names[3] / 'validated.json').write_text('unknown receipt')
    assert (await retention.reclaim(manager))['removed'] == 0
    assert project.exists()


async def test_live_generation_lease_prevents_cleanup_while_unrelated_work_continues(tmp_path, monkeypatch):
    import os
    from amplifier_web.generation_leases import acquire
    manager, names, project = manager_fixture(tmp_path, monkeypatch)
    manager.busy = lambda: True
    folder=manager.directory/'releases'/names[3]
    marker=json.loads((folder/'validated.json').read_text())
    marker['generationSchema']=1
    (folder/'validated.json').write_text(json.dumps(marker))
    lease=acquire(manager.home,names[3],os.getpid())
    assert (await retention.reclaim(manager))['removed']==0
    lease.unlink()
    assert (await retention.reclaim(manager))['removed']==1
    assert project.exists()


async def test_retained_environment_keeps_earlier_editable_sources(tmp_path, monkeypatch):
    manager,names,project=manager_fixture(tmp_path,monkeypatch)
    current=manager.directory/'releases'/names[0]
    earlier=manager.directory/'releases'/names[3]
    (current/'runtime-installed.json').write_text(json.dumps([{'path':str(earlier/'foundation/cache/module')}]))
    assert (await retention.reclaim(manager))['removed']==0
    assert earlier.exists()
