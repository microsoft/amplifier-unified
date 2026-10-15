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
    calls = []
    def record(name, value):
        def call(*args, **kwargs):
            calls.append((name, args, kwargs))
            return value
        return call
    lib = SimpleNamespace(
        export_repository_snapshot=record('snapshot', Value(snapshot_commit='exact', snapshot_tree='tree')),
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
            'operationId': 'durable-launch', 'universeId': 'owned', 'dockerHost': 'unix:///approved/docker.sock',
            'image': owner.APPROVED_IMAGE, 'requestId': 'stable-request', 'timeoutSeconds': 12, 'command': 'python -V'}


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
