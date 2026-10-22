"""Fixed updater admission with real capacity policy/daemon projection owners."""
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as environments
from amplifier_web import test_capacity_settings as capacity
from amplifier_web import test_environment_worker as worker


@pytest.mark.parametrize('free_gib,available', [(80, True), (2, False)])
def test_retained_guests_do_not_block_update_but_disk_pressure_does(tmp_path, monkeypatch, free_gib, available):
    app = SimpleNamespace(data_dir=tmp_path)
    config = {'dockerHost': 'unix:///fixture.sock', 'maxRunning': 2, 'maxInstances': 4,
              'minFreeBytes': 4 * 1024**3, 'admissionRoot': str(tmp_path / 'admission')}
    original = copy.deepcopy(config)
    monkeypatch.setattr(worker, 'docker_json', lambda *args: {'ID': 'fixture', 'DockerRootDir': str(tmp_path)})
    rows = [json.dumps({'Labels': 'com.docker.compose.project=dtu-' + str(index),
                        'State': 'running' if index == 0 else 'exited'}) for index in range(5)]
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='\n'.join(rows)))
    monkeypatch.setattr(worker.os, 'statvfs', lambda *a: SimpleNamespace(f_bavail=free_gib, f_frsize=1024**3))
    monkeypatch.setattr(environments.host_resources, 'local_target', lambda *a: {'supported': False})
    def invoke(config, home, row, action, args, **kwargs):
        assert action == 'capacity'
        return {'ok': True, 'capacity': worker.docker_capacity(config)}
    monkeypatch.setattr(environments, 'invoke', invoke)
    ordinary = environments.capacity(app, config)
    assert not ordinary['available']
    assert {'code': 'retained_capacity'} in ordinary['blockers']
    updated = environments.capacity(app, config, _qualification=True)
    assert updated['available'] is available
    assert updated['counts'] == ordinary['counts']
    assert updated['blockers'] == ([] if available else [{'code': 'storage_pressure'}])
    assert config == original and capacity.load(app)['defaults'] == {}


def test_unknown_daemon_observation_still_blocks_the_update(tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path)
    def unknown(*args, **kwargs):
        raise ValueError('Storage or daemon identity could not be observed')
    monkeypatch.setattr(environments, 'invoke', unknown)
    assert environments.capacity(app, {'maxRunning': 1}, _qualification=True) == {
        'available': False, 'blockers': [{'code': 'capacity_unknown'}]}
