"""Prospective exact-buffer proof; no real Docker, credentials or live owner."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import signal
import subprocess
import sys
from types import SimpleNamespace
import venv
import zipfile

import pytest

from amplifier_web import app_release_artifacts as artifacts
from amplifier_web import environment_cleanup_execution as execution
from amplifier_web import environment_cleanup_launcher as launcher
from amplifier_web import task_environments as owner, test_environment_recovery as recovery
from test_configuration_authority import app
from test_task_environments import act, backend, start, scope

VERSION, REVISION = '99.0.0', 'a' * 40
MEMBER = 'amplifier_web/test_environment_worker.py'


def archive(tmp_path, members):
    path = tmp_path / artifacts.filename(VERSION)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name, raw in members.items():
            output.writestr(name, raw)
        output.writestr(artifacts.STAMP, json.dumps({
            'schema': 1, 'repository': artifacts.REPOSITORY,
            'version': VERSION, 'revision': REVISION}))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'url': artifacts.SOURCE + '/releases/download/v' + VERSION + '/' + path.name}


def test_original_member_binding_rejects_self_consistent_wrong_wheel(tmp_path):
    original = archive(tmp_path, {MEMBER: b'original'})
    assert artifacts.captured_members(original, VERSION, REVISION, [MEMBER]) == {MEMBER: b'original'}
    archive(tmp_path, {MEMBER: b'changed'})
    with pytest.raises(ValueError, match='checksum'):
        artifacts.captured_members(original, VERSION, REVISION, [MEMBER])


def test_archive_mutation_after_member_capture_is_not_blessed_by_later_hash(tmp_path, monkeypatch):
    descriptor = archive(tmp_path, {MEMBER: b'original'})
    read = zipfile.ZipFile.read
    def changed(archive_file, name, *args, **kwargs):
        raw = read(archive_file, name, *args, **kwargs)
        if name == artifacts.STAMP:
            with open(descriptor['path'], 'r+b') as stream:
                stream.seek(0)
                stream.write(b'changed-after-member-read')
        return raw
    monkeypatch.setattr(zipfile.ZipFile, 'read', changed)
    # A buffered descriptor can still contain the original authenticated
    # snapshot after replacement. Either reject or return ONLY original bytes.
    try:
        result = artifacts.captured_members(descriptor, VERSION, REVISION, [MEMBER])
    except ValueError:
        pass
    else:
        assert result == {MEMBER: b'original'}
    with pytest.raises(ValueError):
        artifacts.captured_members(descriptor, VERSION, REVISION, [MEMBER])


@pytest.mark.parametrize('boundary', ['release', 'member', 'duplicate', 'oversize', 'symlink'])
def test_member_capture_refuses_false_proofs(tmp_path, boundary):
    descriptor = archive(tmp_path, {MEMBER: b'original'})
    if boundary == 'duplicate':
        with zipfile.ZipFile(descriptor['path'], 'a') as output:
            output.writestr(MEMBER, b'duplicate')
        descriptor['sha256'] = hashlib.sha256(Path(descriptor['path']).read_bytes()).hexdigest()
    if boundary == 'symlink':
        path = Path(descriptor['path'])
        path.rename(path.with_suffix('.retained'))
        path.symlink_to(path.with_suffix('.retained'))
    with pytest.raises(ValueError):
        artifacts.captured_members(descriptor, VERSION, 'b' * 40 if boundary == 'release' else REVISION,
            [MEMBER + '.absent' if boundary == 'member' else MEMBER],
            limit=1 if boundary == 'oversize' else 262144)


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    """Separate Smart Tool interpreter; only its fake backend writes a sentinel."""
    root = tmp_path / 'installed' / 'amplifier_web'
    root.mkdir(parents=True)
    installed = Path(launcher.__file__).parent
    names = (*execution.NAMES, execution.LAUNCHER, 'test_job_transport.py',
             'test_host_resources.py', 'test_source_transfer.py')
    for name in names:
        (root / name).write_bytes((installed / name).read_bytes())
    monkeypatch.setattr(execution, '__file__', str(root / 'environment_cleanup_execution.py'))
    tool = tmp_path / 'smart-tool'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(tool)
    python = str(tool / 'bin' / 'python')
    package = next((tool / 'lib').glob('python*/site-packages')) / 'digital_twin_universe'
    package.mkdir()
    (package / '__init__.py').write_text('from . import lib\n')
    (package / 'lib.py').write_text('# Controlled backend, no container or network calls.\n')
    sentinel = tmp_path / 'backend-effect'
    (package / 'schemas.py').write_text(
        "from pathlib import Path\n"
        "class DigitalTwinUniverseError(Exception): pass\n"
        "class BackendConfig:\n"
        " def __init__(self, **kwargs):\n"
        f"  Path({str(sentinel)!r}).write_text('backend-entered')\n"
        "  raise RuntimeError('controlled backend lost reply')\n")
    descriptor = archive(tmp_path, {'amplifier_web/' + name: (root / name).read_bytes()
                                  for name in (*execution.NAMES, execution.LAUNCHER)})
    manager = SimpleNamespace(running_identity={'version': VERSION, 'revision': REVISION, 'instanceId': 'serving-instance'},
        _running_artifact={'qualification': {'artifact': descriptor}, 'installation': {'prefix': 'serving'}})
    observed = {'artifact': descriptor, 'qualification': manager._running_artifact['qualification'],
                'installation': {'prefix': 'serving'}}
    monkeypatch.setattr(execution.update_readiness, '_observed_artifact', lambda *a: dict(observed))
    app = SimpleNamespace(update_manager=manager, instance_id='host-instance')
    gate = execution.Gate(app)
    process = subprocess.Popen([python, '-I', '-B', '-c', execution.BOOTSTRAP],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = (gate.bootstrap(), {'nonce': gate.nonce, 'members': gate.members})
    for frame in frames:
        process.stdin.write(json.dumps(frame).encode() + b'\n')
        process.stdin.flush()
    yield SimpleNamespace(process=process, gate=gate, python=python, sentinel=sentinel,
                          root=root, app=app, descriptor=descriptor)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    process.stdin.close()
    process.stdout.close()
    process.stderr.close()


def permit(invocation, ready, **changes):
    frame = {'nonce': ready['nonce'], 'readySha256': execution.digest(ready),
             'payload': {'action': 'remove', 'root': str(invocation.root.parent),
                         'dockerHost': 'unix:///not-real'}}
    invocation.process.stdin.write(json.dumps({**frame, **changes}).encode() + b'\n')
    invocation.process.stdin.flush()
    invocation.process.stdin.close()


def test_real_helper_loads_then_waits_then_same_process_enters_backend(invocation):
    item = invocation
    ready = item.gate.receive(item.process)
    lifecycle = item.gate.validate(ready, item.process, item.python)
    assert lifecycle[0] == str(item.process.pid)
    assert item.python != sys.executable
    assert not item.sentinel.exists()
    assert ready['members'] == item.gate.members
    assert all(member['capture'] == 'executed-source-buffer' for member in ready['members'])
    # Source replacement after capture must not change what the loaded objects do.
    (item.root / 'environment_retention.py').write_text("raise RuntimeError('late replacement')")
    (item.root / 'test_environment_worker.py').write_text("raise RuntimeError('late replacement')")
    permit(item, ready)
    assert item.process.wait(timeout=5) == 1  # Controlled post-permit lost reply, NOT a cleanup pass.
    assert item.sentinel.read_text() == 'backend-entered'


@pytest.mark.parametrize('field,value', [('nonce', 'b' * 64), ('readySha256', 'b' * 64),
                                        ('payload', {'action': 'execute'})])
def test_wrong_permit_never_enters_backend(invocation, field, value):
    ready = invocation.gate.receive(invocation.process)
    permit(invocation, ready, **{field: value})
    assert invocation.process.wait(timeout=5) == 1
    assert not invocation.sentinel.exists()


def test_disconnect_before_permit_never_enters_backend(invocation):
    invocation.gate.receive(invocation.process)
    invocation.process.stdin.close()
    assert invocation.process.wait(timeout=5) == 1
    assert not invocation.sentinel.exists()


@pytest.mark.parametrize('field,value', [('schema', True), ('phase', 'probed'), ('nonce', 'b' * 64),
    ('pid', -1), ('start', 'wrong'), ('interpreter', '/wrong/python'),
    ('prefix', '/wrong/prefix'), ('members', []), ('extra', 'not allowed')])
def test_host_rejects_wrong_invocation_proof(invocation, field, value):
    ready = invocation.gate.receive(invocation.process)
    with pytest.raises(ValueError):
        invocation.gate.validate({**ready, field: value}, invocation.process, invocation.python)
    assert not invocation.sentinel.exists()


def test_dead_helper_is_not_permission(invocation):
    ready = invocation.gate.receive(invocation.process)
    invocation.process.kill()
    invocation.process.wait(timeout=5)
    with pytest.raises(ValueError):
        invocation.gate.validate(ready, invocation.process, invocation.python)
    assert not invocation.sentinel.exists()


def test_serving_change_invalidates_cached_member_gate(invocation):
    item = invocation
    item.app.update_manager.running_identity['instanceId'] = 'replacement-instance'
    with pytest.raises(ValueError, match='serving application changed'):
        item.gate.recheck()
    assert not item.sentinel.exists()


def test_warm_member_cache_never_skips_current_serving_eligibility(invocation, monkeypatch):
    item = invocation
    monkeypatch.setattr(artifacts, 'captured_members', lambda *a, **k: pytest.fail('whole wheel rescanned'))
    another = execution.Gate(item.app)
    assert another.members == item.gate.members and another.nonce != item.gate.nonce
    monkeypatch.setattr(execution.update_readiness, '_observed_artifact', lambda *a: None)
    with pytest.raises(ValueError, match='qualified serving'):
        execution.Gate(item.app)


@pytest.mark.parametrize('boundary', ['source', 'late-stop', 'revision', 'receipt', 'permit-persist'])
async def test_durable_host_gate_rechecks_after_wait_and_never_replays(
        app, backend, monkeypatch, boundary):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'loaded-stop'))['resource']
    events = []
    original_finish = app.operations.requests.finish
    revoked = False

    def guard():
        events.append('authority')
        if revoked:
            raise ValueError('The task moved or stopped before environment admission.')

    def finish(request, state, **fields):
        nonlocal revoked
        if 'cleanupExecution' in fields:
            events.append('durable-permit')
            if boundary == 'permit-persist':
                raise OSError('retained simulated persistence failure')
            value = original_finish(request, state, **fields)
            if boundary == 'late-stop':
                revoked = True
            if boundary == 'revision':
                owner.save(app.data_dir, owner.get(app.data_dir, row['sessionId'], row['id']), reason='changed')
            if boundary == 'receipt':
                original_finish(value, 'admitting', message='changed while waiting')
            return value
        return original_finish(request, state, **fields)

    from amplifier_web import test_source_transfer
    def source_check(*args):
        events.append('source')
        if boundary == 'source':
            raise ValueError('Source differs from snapshot')
    monkeypatch.setattr(test_source_transfer, 'preflight', source_check)
    monkeypatch.setattr(app.operations.requests, 'finish', finish)
    def invoke(app, config, current, action, args, permit):
        events.append('ready')
        try:
            permit({'schema': 1, 'ready': {'phase': 'loaded'}}, ('123', '456'))
        except (ValueError, OSError):
            return {**owner.cleanup_not_started(current), 'code': 'cleanup_loaded_inputs_refused'}
        events.append('effect')
        raise AssertionError('late boundary was not guarded')
    monkeypatch.setattr(owner, 'invoke_cleanup', invoke)
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'], 'requestId': 'loaded-refusal'}
    request, _ = owner.begin_removal(app, raw, 'workspace.environments.remove', args,
        raw['owner'], config=owner.configuration(app)[0])
    result = owner.effect(app, raw, 'remove', args,
        owner.configuration(app)[0], request, guard)
    assert result['request']['state'] == 'failed'
    assert result['request']['result']['effectAdmitted'] is False
    assert 'effect' not in events and 'ready' in events
    if boundary != 'source':
        assert 'durable-permit' in events
        assert events.index('ready') < events.index('durable-permit')
    else:
        assert 'durable-permit' not in events
    monkeypatch.setattr(owner, 'invoke_cleanup', lambda *a: pytest.fail('refused request replayed'))
    duplicate = await act(app, 'remove', row, 'loaded-refusal')
    assert duplicate['request'] == result['request']


def test_exact_capture_ignores_unchecked_pyc_and_module_aliases(tmp_path, monkeypatch):
    root = Path(launcher.__file__).parent
    raw = {name: (root / name).read_bytes() for name in execution.NAMES}
    # The retained object need not be registered in sys.modules at all.
    worker = launcher.load(raw)
    assert worker.retention.__name__ not in sys.modules
    assert worker.retention.verify_retention.__code__.co_filename.endswith('/environment_retention.py')
    path = tmp_path / 'environment_retention.py'
    path.write_text("raise RuntimeError('unchecked cache was executed')")
    py_compile.compile(str(path), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    path.write_bytes(raw['environment_retention.py'])
    (tmp_path / 'test_environment_worker.py').write_bytes(raw['test_environment_worker.py'])
    monkeypatch.setattr(launcher, '__file__', str(tmp_path / execution.LAUNCHER))
    assert launcher.load(launcher.capture()).retention.__name__ == '_captured_environment_retention'


def test_passive_retained_execution_does_not_import_or_start_helper(monkeypatch):
    ready = {'schema': 1, 'phase': 'loaded', 'nonce': 'a' * 64,
        'pid': 123, 'start': '456', 'interpreter': '/smart-tool/bin/python',
        'prefix': '/smart-tool', 'members': [{'name': 'amplifier_web/' + name,
            'bytes': 100, 'sha256': 'b' * 64, 'capture': 'executed-source-buffer'}
            for name in execution.NAMES]}
    proof = {'schema': 1, 'status': 'permit-recorded', 'delivery': 'not-observed',
             'serving': {'version': VERSION, 'revision': REVISION, 'instanceId': 'serving'},
             'hostInstanceId': 'host', 'installationSha256': 'c' * 64,
             'qualificationSha256': 'd' * 64, 'wheelSha256': 'e' * 64,
             'ready': ready, 'readySha256': execution.digest(ready)}
    monkeypatch.setattr(owner, 'invoke', lambda *a: pytest.fail('passive helper invocation'))
    monkeypatch.setattr(execution, 'Gate', lambda *a: pytest.fail('passive gate construction'))
    assert recovery._retained_execution({'cleanupExecution': proof}) == {
        'status': 'retained_permit', 'currentExecutor': 'not_observed',
        'dispatch': 'not_established', 'proof': proof}
    assert recovery._retained_execution({}) == {'status': 'not_retained'}
    assert recovery._retained_execution({'cleanupExecution': {
        'schema': 1, 'status': 'permit-recorded', 'delivery': 'not-observed',
        'ready': {'phase': 'loaded'}, 'readySha256': execution.digest({'phase': 'loaded'})}
        }) == {'status': 'unverified'}


def test_permit_transport_rechecks_before_commit_delimiter(invocation):
    item = invocation
    ready = item.gate.receive(item.process)
    called = []
    def late_stop():
        called.append('final-check')
        assert not item.sentinel.exists()
        raise ValueError('Stop changed while transport was waiting')
    with pytest.raises(ValueError, match='Stop changed'):
        execution.send(item.process, {'nonce': ready['nonce'],
            'readySha256': execution.digest(ready), 'payload': {'action': 'remove'}},
            before_commit=late_stop)
    assert called == ['final-check'] and not item.sentinel.exists()
    item.process.stdin.close()
    assert item.process.wait(timeout=5) == 1


def test_owned_adapter_durable_permit_then_same_process_effect_and_unknown(invocation, monkeypatch, tmp_path):
    item = invocation
    # Fixture's first helper remains payload-free; this is a distinct NEW owned
    # normal invocation with its own nonce, not readiness reused from that probe.
    item.process.stdin.close()
    assert item.process.wait(timeout=5) == 1
    # Use the actual package's adjacent inputs, not the tamper-test copy. In the
    # installed lane these are the wheel-installed member paths outside source.
    monkeypatch.setattr(execution, '__file__', str(Path(launcher.__file__).with_name('environment_cleanup_execution.py')))
    gate = execution.Gate(item.app)
    monkeypatch.setattr(owner.storage, 'directory', lambda *a: tmp_path)
    events = []
    def permit_saved(proof, lifecycle):
        assert not item.sentinel.exists()
        assert proof['ready']['pid'] == int(lifecycle[0])
        assert proof['ready']['members'] == gate.members
        events.append('durable-permit')
        # Real fsync, not a mocked completion check.
        with (tmp_path / 'permit.json').open('w') as stream:
            json.dump(proof, stream)
            stream.flush()
            os.fsync(stream.fileno())
        def final():
            assert json.loads((tmp_path / 'permit.json').read_text()) == proof
            assert not item.sentinel.exists()
            events.append('final-recheck')
        return final
    with pytest.raises(ValueError, match='exited without a valid result'):
        owner.invoke({'python': item.python, 'dockerHost': 'unix:///not-real'},
            tmp_path, {'snapshot': {}, 'source': str(tmp_path)}, 'remove',
            {'requestId': 'new-permitted-operation'}, _cleanup_gate=gate,
            _cleanup_permit=permit_saved)
    assert events == ['durable-permit', 'final-recheck']
    assert item.sentinel.read_text() == 'backend-entered'


def test_installed_lane_import_and_exact_member_join():
    expected = os.environ.get('CLEANUP_EXPECT_INSTALLED_ROOT')
    if expected:
        assert Path(launcher.__file__).is_relative_to(expected)
        assert Path(execution.__file__).is_relative_to(expected)
        assert not Path(launcher.__file__).is_relative_to('/workspace')
        wheel = Path(os.environ['CLEANUP_TESTED_WHEEL'])
        with zipfile.ZipFile(wheel) as archive_file:
            for name in (*execution.NAMES, execution.LAUNCHER, 'environment_cleanup_execution.py'):
                assert archive_file.read('amplifier_web/' + name) == Path(launcher.__file__).with_name(name).read_bytes()


@pytest.mark.parametrize('output', [b'not-json\n', b'[]\n', b'x' * 16385])
def test_bounded_malformed_readiness_refuses(output):
    process = subprocess.Popen([sys.executable, '-I', '-c',
        'import os,sys; os.write(1,bytes.fromhex(sys.argv[1]))', output.hex()],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        gate = object.__new__(execution.Gate)
        with pytest.raises((ValueError, TypeError)):
            ready = gate.receive(process, seconds=2)
            gate.validate(ready, process, sys.executable)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def test_stalled_readiness_timeout_is_not_restarted():
    process = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(10)'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with pytest.raises(ValueError, match='timed out'):
            object.__new__(execution.Gate).receive(process, seconds=0.01)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()

def test_launch_selected_candidate_wheel_uses_real_cleanup_gate(tmp_path, monkeypatch):
    installed = Path(execution.__file__).parent
    members = {'amplifier_web/' + name: (installed / name).read_bytes()
               for name in (*execution.NAMES, execution.LAUNCHER)}
    descriptor = archive(tmp_path, members)
    manager = SimpleNamespace(running_identity={'version': VERSION, 'revision': REVISION, 'instanceId': 'instance'},
                              _running_artifact=None)
    app = SimpleNamespace(update_manager=manager, instance_id='host')
    with pytest.raises(ValueError, match='checksum'):
        execution.bind_candidate(app, descriptor['path'], '0' * 64)
    wrong = archive(tmp_path, {**members, MEMBER: b'other installed candidate'})
    with pytest.raises(ValueError, match='differs from the running installation'):
        execution.bind_candidate(app, wrong['path'], wrong['sha256'])
    descriptor = archive(tmp_path, members)
    execution.bind_candidate(app, descriptor['path'], descriptor['sha256'])
    gate = execution.Gate(app)
    assert gate.raw == members
    assert gate.evidence({})['wheelSha256'] == descriptor['sha256']
    manager.running_identity['instanceId'] = 'replacement'
    with pytest.raises(ValueError, match='candidate changed'):
        gate.recheck()
