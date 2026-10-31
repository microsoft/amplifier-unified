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
    assert result['counts'] == {'active': 1, 'stopped_retained': 15, 'pending_unobserved': 0, 'total': 16}


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


def test_interrupted_launch_keeps_slot_until_exact_owner_cleanup(monkeypatch, tmp_path):
    payload = {**observation(monkeypatch, tmp_path, []), 'maxRunning': 1,
        'universeId': 'dtu-0', 'operationId': 'launch', 'root': str(tmp_path/'owner')}
    worker.reserve_admission(payload, worker.docker_capacity(payload))
    result = worker.docker_capacity(payload)
    assert not result['available'] and result['counts']['pending_unobserved'] == 1
    with pytest.raises(FileExistsError):
        worker.reserve_admission(payload, result)
    with pytest.raises(ValueError, match='different owner'):
        worker.release_admission({**payload, 'root': '/another-owner'}, 'dtu-0')
    # Once the same physical project appears it is counted only once.
    observation(monkeypatch, tmp_path, ['running'])
    result = worker.docker_capacity(payload)
    assert result['counts']['active'] == 1 and result['counts']['pending_unobserved'] == 0
    worker.release_admission(payload, 'dtu-0')
    observation(monkeypatch, tmp_path, [])
    assert worker.docker_capacity(payload)['available']


def test_admission_rejects_symlinked_ancestor_and_public_lock(monkeypatch, tmp_path):
    payload = observation(monkeypatch, tmp_path, [])
    target = tmp_path/'real'
    target.mkdir()
    (tmp_path/'alias').symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        worker.docker_capacity({**payload, 'admissionRoot': str(tmp_path/'alias'/'state')})
    directory = worker.admission_directory(payload, 'same-daemon')
    lock = directory/'admission.lock'
    lock.write_text('')
    lock.chmod(0o644)
    with pytest.raises(ValueError, match='private regular'):
        with worker.docker_admission(payload):
            pytest.fail('public lock admitted')


@pytest.mark.parametrize('explicit_zero', [False, True])
def test_uncapped_counts_above_old_defaults_keep_storage_pressure(monkeypatch, tmp_path, explicit_zero):
    payload = observation(monkeypatch, tmp_path, ['running']*16 + ['exited']*257)
    payload.pop('maxRunning')
    if explicit_zero:
        payload.update(maxRunning=0, maxInstances=0)
    result = worker.docker_capacity(payload)
    assert result['available'] and result['counts']['active'] == 16
    assert result['counts']['total'] == 273
    monkeypatch.setattr(worker.os, 'statvfs', lambda *a: SimpleNamespace(f_bavail=1, f_frsize=1024**3))
    assert worker.docker_capacity(payload)['blockers'] == [{'code': 'storage_pressure'}]
