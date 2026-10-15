"""Physical daemon observations and cooperating admission boundaries."""
import json
from types import SimpleNamespace

import pytest

from amplifier_web import test_environment_worker as worker


def observation(monkeypatch, tmp_path, states):
    monkeypatch.setattr(worker, 'docker_json', lambda *args: {'ID': 'same-daemon', 'DockerRootDir': str(tmp_path)})
    rows = [json.dumps({'Labels': 'com.docker.compose.project=dtu-'+str(i), 'State': state}) for i, state in enumerate(states)]
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='\n'.join(rows)))
    monkeypatch.setattr(worker.os, 'statvfs', lambda *args: SimpleNamespace(f_bavail=20, f_frsize=1024**3))
    return {'dockerHost': 'unix:///test/docker.sock', 'maxRunning': 2, 'minFreeBytes': 10*1024**3,
            'admissionRoot': str(tmp_path / 'admission')}


def test_stopped_environments_do_not_consume_execution_slots(monkeypatch, tmp_path):
    payload = observation(monkeypatch, tmp_path, ['running'] + ['exited']*15)
    result = worker.docker_capacity(payload)
    assert result['available']
    assert result['counts'] == {'active': 1, 'stopped_retained': 15, 'total': 16}


@pytest.mark.parametrize('change,code', [({'maxRunning': 1}, 'execution_capacity'),
    ({'maxInstances': 2}, 'retained_capacity'), ({'minFreeBytes': 30*1024**3}, 'storage_pressure')])
def test_policies_have_distinct_blockers(monkeypatch, tmp_path, change, code):
    payload = observation(monkeypatch, tmp_path, ['running', 'exited'])
    result = worker.docker_capacity({**payload, **change})
    assert not result['available']
    assert result['blockers'] == [{'code': code}]


def test_socket_aliases_share_the_actual_daemon_admission_lock(monkeypatch, tmp_path):
    payload = observation(monkeypatch, tmp_path, [])
    with worker.docker_admission(payload):
        with pytest.raises(BlockingIOError):
            with worker.docker_admission({**payload, 'dockerHost': 'unix:///alias/docker.sock'}):
                pytest.fail('second admission must not enter')
    with worker.docker_admission(payload) as result:
        assert result['available']


def test_capacity_is_rechecked_under_lock(monkeypatch, tmp_path):
    payload = observation(monkeypatch, tmp_path, [])
    reads = []
    def changed(args):
        reads.append(True)
        return {'available': len(reads)==1, 'scope': {'daemon': 'same-daemon'}}
    monkeypatch.setattr(worker, 'docker_capacity', changed)
    with worker.docker_admission(payload) as result:
        assert not result['available']
    assert len(reads) == 2
