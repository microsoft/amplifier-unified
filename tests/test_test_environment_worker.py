"""Managed helper protocol, constrained profile and preservation boundaries."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_worker as worker
from amplifier_web import task_resources as storage


class Value:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, mode=None):
        return self.__dict__.copy()


class Failure(Exception):
    code = 'fixture-failure'
    universe_id = 'owned'


@pytest.fixture
def library(monkeypatch):
    monkeypatch.setattr(worker, 'docker_capacity', lambda payload: {'available': True, 'scope': {'daemon': 'fixture-daemon'}})
    calls = []
    def record(name, value):
        def call(*args, **kwargs):
            calls.append((name, args, kwargs))
            return value
        return call
    lib = SimpleNamespace(
        export_repository_snapshot=record('snapshot', Value(snapshot_commit='exact', snapshot_tree='tree', archive_sha256='digest')),
        launch_id=record('identity', 'owned'),
        launch=record('launch', Value(id='owned', state='running', services=[])),
        push_files=record('push', Value()),
        execute=record('execute', Value(stdout='ok', stderr='', exit_code=0)),
        status=record('status', Value(id='owned', state='stopped', services=[])),
        stop=record('stop', Value(complete=True, outcome='stopped')),
        export_files=record('export', Value(bytes=100, sha256='abc')),
        destroy=record('destroy', Value(complete=True, outcome='removed')),
    )
    monkeypatch.setitem(sys.modules, 'digital_twin_universe', SimpleNamespace(lib=lib))
    monkeypatch.setitem(sys.modules, 'digital_twin_universe.schemas',
        SimpleNamespace(BackendConfig=lambda **args: args, DigitalTwinUniverseError=Failure))
    return lib, calls


def payload(tmp_path, action):
    return {'action': action, 'root': str(tmp_path), 'source': '/approved/task/worktree',
            'snapshot': {'snapshot_commit': 'exact', 'snapshot_tree': 'tree', 'archive_sha256': 'digest'},
            'operationId': 'durable-launch', 'universeId': 'owned', 'dockerHost': 'unix:///approved/docker.sock',
            'image': owner.APPROVED_IMAGE, 'requestId': 'stable-request', 'timeoutSeconds': 12, 'command': 'python -V',
            'admissionRoot': str(tmp_path / 'admission')}


def test_fixed_profile_has_no_host_mount_ports_or_privilege_escalation():
    profile = worker.profile(owner.APPROVED_IMAGE)
    twin = profile['services']['twin']
    assert twin['volumes'] == ['workspace:/workspace', 'artifacts:/artifacts']
    assert 'ports' not in twin and 'privileged' not in twin and 'build' not in twin
    assert twin['read_only'] and twin['cap_drop'] == ['ALL']
    assert twin['security_opt'] == ['no-new-privileges:true']
    assert profile['networks'] == {'default': {'internal': True}}
    assert 'environment' not in twin and 'env_file' not in twin


def test_prepare_preserves_source_before_launch_and_uses_pure_identity(library, tmp_path):
    lib, calls = library
    result = worker.execute_call(payload(tmp_path, 'prepare'))
    assert result['universeId'] == 'owned' and result['snapshot']['snapshot_commit'] == 'exact'
    assert [name for name, _, _ in calls] == ['snapshot', 'identity']
    assert json.loads((tmp_path / 'snapshot.json').read_text())['snapshot_tree'] == 'tree'


def test_start_copies_exact_snapshot_without_host_mount(library, tmp_path):
    lib, calls = library
    result = worker.execute_call(payload(tmp_path, 'start'))
    assert result['ok']
    launch = next(item for item in calls if item[0] == 'launch')
    assert launch[2]['operation_id'] == 'durable-launch'
    assert launch[2]['config']['state_root'] == tmp_path / 'backend'
    assert next(item for item in calls if item[0] == 'push')[1] == ('owned', tmp_path / 'source.tar', '/artifacts/.source.tar')
    assert next(item for item in calls if item[0] == 'execute')[2]['max_output_bytes'] == 32768
    assert "filter='data'" in next(item for item in calls if item[0] == 'execute')[1][1]
    assert worker.source_ready(tmp_path, 'owned', payload(tmp_path, 'read')['snapshot'])


def test_failed_unpack_never_promotes_running_container_to_ready(library, tmp_path):
    lib, calls = library
    executions = []
    def unpack(*args, **kwargs):
        executions.append(args[1])
        return Value(stdout='', stderr='outside symlink refused', exit_code=1)
    lib.execute = unpack
    lib.status = lambda *a, **k: Value(state='running', services=[])
    assert not worker.execute_call(payload(tmp_path, 'start'))['ok']
    observed = worker.execute_call(payload(tmp_path, 'read'))
    assert observed['status'] == 'unknown' and not observed['sourceReady']
    assert not worker.execute_call(payload(tmp_path, 'execute'))['ok']
    assert len(executions) == 1  # Only the original unpack attempt, never a check or replay.
    assert not (tmp_path / 'source-ready.json').exists()


def test_helper_loss_after_launch_cannot_enable_checks_or_reinstall_source(library, tmp_path):
    lib, calls = library
    def lost(*args, **kwargs):
        raise Failure()
    lib.push_files = lost
    lib.status = lambda *a, **k: Value(state='running', services=[])
    assert not worker.execute_call(payload(tmp_path, 'start'))['ok']
    assert worker.execute_call(payload(tmp_path, 'read'))['status'] == 'unknown'
    assert not worker.execute_call(payload(tmp_path, 'execute'))['ok']
    assert not any(name == 'execute' for name, _, _ in calls)


@pytest.mark.parametrize('change', ['snapshot', 'universe', 'missing'])
def test_readiness_receipt_requires_exact_snapshot_and_universe(library, tmp_path, change):
    lib, calls = library
    assert worker.execute_call(payload(tmp_path, 'start'))['ok']
    lib.status = lambda *a, **k: Value(state='running', services=[])
    altered = payload(tmp_path, 'read')
    if change == 'snapshot':
        altered['snapshot']['archive_sha256'] = 'another-digest'
    elif change == 'universe':
        altered['universeId'] = 'another-universe'
    else:
        (tmp_path / 'source-ready.json').unlink()
    assert worker.execute_call(altered)['status'] == 'unknown'
    assert not worker.execute_call({**altered, 'action': 'execute'})['ok']
    assert sum(name == 'execute' for name, _, _ in calls) == 1


def test_confirmed_source_survives_lost_host_response_without_reextraction(library, tmp_path):
    lib, calls = library
    worker.execute_call(payload(tmp_path, 'start'))  # Simulate a lost response after the durable source receipt.
    lib.status = lambda *a, **k: Value(state='running', services=[])
    observed = worker.execute_call(payload(tmp_path, 'read'))
    assert observed['status'] == 'running' and observed['sourceReady']
    assert sum(name == 'execute' for name, _, _ in calls) == 1


def test_partial_source_can_still_be_stopped_and_preserved(library, tmp_path):
    lib, calls = library
    assert not (tmp_path / 'source-ready.json').exists()
    assert worker.execute_call(payload(tmp_path, 'stop'))['status'] == 'stopped'
    observed = worker.execute_call(payload(tmp_path, 'read'))
    assert observed['status'] == 'stopped' and not observed['sourceReady']
    assert worker.execute_call(payload(tmp_path, 'remove'))['status'] == 'removed'


@pytest.mark.parametrize('failure_at', [1, 2])
def test_uncertain_export_never_destroys_environment(library, tmp_path, failure_at):
    lib, calls = library
    exports = []
    def export(*args, **kwargs):
        exports.append(args[1])
        if len(exports) == failure_at:
            raise Failure()
        return Value(bytes=100)
    lib.export_files = export
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert not result['ok'] and not any(name == 'destroy' for name, _, _ in calls)
    assert exports == ['/workspace', '/artifacts'][:failure_at]


def test_remove_exports_both_roots_before_destroy_and_retains_manifest(library, tmp_path):
    lib, calls = library
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['ok'] and result['status'] == 'removed'
    assert [name for name, _, _ in calls] == ['status', 'export', 'export', 'destroy']
    assert len(list(tmp_path.glob('retained-*/manifest.json'))) == 1


def test_retention_persistence_failure_never_destroys(library, tmp_path, monkeypatch):
    lib, calls = library
    def fail(directory, archives):
        raise OSError('cannot persist archive receipt')
    monkeypatch.setattr(worker, 'retain_manifest', fail)
    with pytest.raises(OSError):
        worker.execute_call(payload(tmp_path, 'remove'))
    assert [name for name, _, _ in calls] == ['status', 'export', 'export']


def test_remove_never_stops_or_removes_running_environment(library, tmp_path):
    lib, calls = library
    lib.status = lambda *a, **k: Value(state='running', services=[])
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert not result['ok'] and calls == []


def test_incomplete_stop_is_never_labeled_stopped(library, tmp_path):
    lib, calls = library
    lib.stop = lambda *a, **k: Value(complete=False, outcome='unknown')
    result = worker.execute_call(payload(tmp_path, 'stop'))
    assert not result['ok'] and result['status'] == 'unknown'


def allocated(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    row = owner.prepare(home, 'root', 'workspace', {'sessionId': 'root'}, '/source', 'key', 'request')
    return home, storage.allocate(home, row)


def test_real_helper_subprocess_does_not_inherit_host_secrets(tmp_path, monkeypatch):
    home, row = allocated(tmp_path)
    monkeypatch.setenv('OPENAI_API_KEY', 'never-forward-this')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'never-forward-this-either')
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'test_environment_worker.py').write_text(
        "import json,os,sys\njson.load(sys.stdin)\nprint(json.dumps({'ok':True,'leaked':any(key in os.environ for key in ['OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY','PYTHONPATH'])}))\n")
    value = owner.invoke({'python': sys.executable, 'image': owner.APPROVED_IMAGE, 'dockerHost': 'unix:///socket'}, home, row, 'read', {})
    assert value == {'ok': True, 'leaked': False}


def test_real_helper_output_is_bounded_and_stopped_on_overflow(tmp_path, monkeypatch):
    home, row = allocated(tmp_path)
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'test_environment_worker.py').write_text("import sys\nsys.stdout.write('x' * 1048576)\nsys.stdout.flush()\n")
    with pytest.raises(ValueError, match='exceeded its bound'):
        owner.invoke({'python': sys.executable, 'image': owner.APPROVED_IMAGE, 'dockerHost': 'unix:///socket'}, home, row, 'read', {})
