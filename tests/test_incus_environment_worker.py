"""Incus adapter admission, isolation, ownership, output and retention contracts."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import incus_environment_worker as worker
from amplifier_web.incus_environment_worker import cli as qualified_cli


def payload(tmp_path, action='read'):
    source = b'exact archive'
    (tmp_path / 'source.tar').write_bytes(source)
    return {'expectedBackendIdentity': {'backend': 'incus', 'daemon': 'd', 'project': 'tests'}, 'action': action, 'root': str(tmp_path), 'source': '/approved/worktree',
            'snapshot': {'snapshot_commit': 'exact', 'snapshot_tree': 'tree', 'archive_sha256': hashlib.sha256(source).hexdigest()},
            'operationId': 'stable-launch', 'rootOwner': 'parent-root', 'requestId': 'effect-request',
            'command': 'printf checked', 'timeoutSeconds': 2,
            'incus': {'executable': '/qualified/bin/legacy', 'incusExecutable': '/usr/bin/incus',
                      'project': 'tests', 'configDir': '/private/incus', 'admissionDir': '/private/admission',
                      'image': 'a' * 64}}


@pytest.fixture
def backend(monkeypatch, tmp_path):
    calls = []
    state = {'instance': 'Running', 'launch': 'ready', 'export_failed': False, 'owner': 'parent-root'}
    monkeypatch.setattr(worker, 'validate_configuration', lambda config: config)
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=20 * 1024**3))
    def cli(config, env, arguments, timeout=30):
        calls.append(('cli', arguments))
        if arguments[0] == 'capacity':
            return {'scope': {'project': 'tests', 'daemon': 'd'}, 'available': True, 'blockers': [], 'storage': {'free_bytes': 20 * 1024**3}}
        if arguments[0] == 'launch-status':
            return {'state': state['launch'], 'name': worker.identity(payload(tmp_path)),
                    'owner_root': state['owner'], 'instance_status': state['instance']}
        if arguments[0] == 'destroy':
            state['launch'] = 'removed'
        return {'id': worker.identity(payload(tmp_path))}
    def incus(config, env, arguments, timeout=30, watched_file=None):
        calls.append(('incus', arguments))
        output = ''
        if arguments == ['query', '/1.0']:
            output = json.dumps({'environment': {'project': 'tests', 'certificate_fingerprint': 'd'}})
        elif arguments[0] == 'query':
            output = json.dumps({'status': state['instance'], 'profiles': [],
                'config': {'user.dtu.managed-by': 'amplifier-digital-twin', 'user.dtu.owner-root': state['owner'],
                           'user.dtu.request-id': 'stable-launch', 'user.dtu.offline': 'true'},
                'expanded_config': {'security.privileged': 'false', 'security.nesting': 'false'},
                'expanded_devices': {'root': {'path': '/', 'type': 'disk', 'pool': 'default'}}})
        elif arguments[0] == 'stop':
            state['instance'], state['launch'] = 'Stopped', 'stopped'
        elif arguments[0] == 'export':
            Path(arguments[2]).write_bytes(b'opaque complete instance archive')
            if state['export_failed']:
                raise worker.BackendFailure('export_limit', 'export exceeded limit')
        elif arguments[0] == 'exec':
            output = 'checked'
        return {'exit_code': 0, 'stdout': output, 'stderr': '', 'truncated': False}
    monkeypatch.setattr(worker, 'cli', cli)
    monkeypatch.setattr(worker, 'incus', incus)
    def export_selected(config, env, name, destination, *, selections=('workspace', 'artifacts'), archive_only=False):
        import tarfile
        calls.append(('incus', ['export', name, '-']))
        if state['export_failed']:
            raise worker.BackendFailure('export_failed', 'fixture')
        result = []
        for selection in selections:
            path = destination / (selection + '.tar')
            with tarfile.open(path, 'w') as archive:
                item = tarfile.TarInfo(selection); item.type = tarfile.DIRTYPE; archive.addfile(item)
            result.append({'id': name, 'source': '/' + selection, 'destination': str(path)})
        return result
    monkeypatch.setattr(worker, 'export_selected', export_selected)
    return calls, state


def test_artifact_recovery_keeps_stopped_instance_and_exports_only_artifacts(tmp_path, backend):
    calls, state = backend
    state.update(instance='Stopped', launch='stopped')
    result = worker.execute_call(payload(tmp_path, 'exportArtifacts'))
    assert result['ok'] and result['status'] == 'stopped'
    assert result['artifactExport']['selection'] == ['/artifacts']
    assert {row['source'] for row in result['archives']} == {'/artifacts'}
    assert not any(args[0] in {'exec', 'stop', 'destroy', 'launch'} for _, args in calls)
    assert state['instance'] == 'Stopped' and state['launch'] == 'stopped'


@pytest.mark.parametrize('action', ['remove', 'finishCleanup'])
@pytest.mark.parametrize('boundary', ['filter', 'verify'])
@pytest.mark.parametrize('diagnostic_write_fails', [False, True])
def test_retention_validation_reason_keeps_stopped_owner(tmp_path, backend, monkeypatch, action, boundary, diagnostic_write_fails):
    import tarfile
    calls, state = backend
    state.update(instance='Stopped', launch='stopped')
    value = payload(tmp_path, action)
    item = tarfile.TarInfo('artifacts/fixture-pipe')
    # Keep ordinary FIFO rejection; archive-only diagnostic failures use a device.
    item.type = tarfile.FIFOTYPE if action == 'remove' else tarfile.CHRTYPE
    original_export = worker.export_selected
    def export(config, env, name, destination, **kwargs):
        if boundary == 'filter':
            # Use the real streaming verifier with synthetic headers only.
            from test_environment_retention import backup, ROOTS
            return worker.common.retention.filter_incus_stream(
                backup(ROOTS + [(item.name, item.type, '')]), destination, name,
                archive_only=kwargs.get('archive_only', False))
        archives = original_export(config, env, name, destination, **kwargs)
        with tarfile.open(destination / 'artifacts.tar', 'a') as archive:
            archive.addfile(item)
        return archives
    monkeypatch.setattr(worker, 'export_selected', export)
    original_write = Path.write_text
    def write(path, *args, **kwargs):
        if diagnostic_write_fails and path.name == 'incomplete.json':
            raise OSError('private diagnostic storage')
        return original_write(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', write)
    result = worker.execute_call(value)
    assert result['verification'] == {'version': 1, 'reasonCode': 'unsupported_type',
        'memberType': 'fifo' if action == 'remove' else 'character', 'relativePath': 'artifacts/fixture-pipe'}
    assert result['phase'] == ('preserving' if boundary == 'filter' else 'verifying')
    assert result['code'] == ('preservation_failed' if boundary == 'filter' else 'archive_verification_failed')
    assert result['status'] == 'stopped' and result['effectAdmitted'] is False
    assert not any(args[0] in {'exec', 'stop', 'destroy', 'launch'} for _, args in calls)
    assert state['instance'] == 'Stopped' and state['launch'] == 'stopped'
    assert not list(tmp_path.glob('retained-*/retention.json'))
    saved = list(tmp_path.glob('retained-*/incomplete.json'))
    assert bool(saved) is not diagnostic_write_fails
    if saved:
        assert json.loads(saved[0].read_text())['verification'] == result['verification']
    assert str(tmp_path) not in json.dumps(result) and 'private diagnostic' not in json.dumps(result)


@pytest.mark.parametrize('action', ['finishCleanup', 'remove'])
@pytest.mark.parametrize('fault,reason', [
    (None, None), ('nonzero', 'invalid_size'), ('device', 'unsupported_type'), ('header', 'invalid_header'),
])
def test_fifo_archive_custody_precedes_incus_destroy(tmp_path, backend, monkeypatch, action, fault, reason):
    import io
    import tarfile
    from test_environment_retention import fifo_selection, guard_fifo_io
    from test_test_environment_worker import retain_prepared_archive_contract
    guard_fifo_io(monkeypatch)
    calls, state = backend
    state.update(instance='Stopped', launch='stopped')
    value = payload(tmp_path, action)
    if action == 'remove':
        retain_prepared_archive_contract(value)
    order, retained_files = [], {}
    original_observe, original_cli = worker.observe, worker.cli
    def verify_complete():
        directory, = tmp_path.glob('retained-*')
        retained = json.loads((directory / 'retention.json').read_text())
        assert retained['verified'] and retained['archiveOnly']
        manifest = json.loads((directory / 'manifest.json').read_text())
        assert {row['source'] for row in manifest} == {'/workspace', '/artifacts'}
        for row in manifest:
            raw = Path(row['destination']).read_bytes()
            assert raw == retained_files[row['source']]
            assert row['verified'] and row['bytes'] == len(raw)
            assert row['sha256'] == hashlib.sha256(raw).hexdigest()
        with tarfile.open(directory / 'artifacts.tar') as archive:
            assert archive.getnames() == ['artifacts', 'artifacts/pipe', 'artifacts/after']
            item = archive.getmember('artifacts/pipe')
            assert (item.type, item.size, item.mode, item.uid, item.gid, item.uname, item.gname, item.mtime) == (
                tarfile.FIFOTYPE, 0, 0o640, 123, 456, 'fixture-user', 'fixture-group', 123456789)
            assert archive.extractfile('artifacts/after').read() == b'kept'
    def observe(*args, **kwargs):
        if order:
            assert order == ['stopped', 'export']
            verify_complete()  # Real verification and manifest precede owner recheck.
        result = original_observe(*args, **kwargs)
        order.append('rechecked' if order else 'stopped')
        return result
    def export(config, env, name, destination, **kwargs):
        assert kwargs == {'archive_only': True}
        calls.append(('export', [name]))
        raw = (fifo_selection('workspace', prefix=worker.common.retention.PREFIX)
               + fifo_selection('artifacts', prefix=worker.common.retention.PREFIX, fault=fault) + bytes(1024))
        order.append('export')
        archives = worker.common.retention.filter_incus_stream(
            io.BytesIO(raw), destination, name, archive_only=True)
        retained_files.update({row['source']: Path(row['destination']).read_bytes() for row in archives})
        return archives
    def cli(config, env, args, timeout=30):
        if args[0] == 'destroy':
            assert order[-1] == 'rechecked'
            verify_complete()
            order.append('destroy')
        result = original_cli(config, env, args, timeout)
        if args[0] == 'launch-status' and state['launch'] == 'removed':
            assert order[-1] == 'destroy'
            order.append('removed')
        return result
    def reconcile(*args):
        assert order[-1] == 'removed'
        order.append('absent')
        return {'status': 'absent'}
    monkeypatch.setattr(worker, 'observe', observe)
    monkeypatch.setattr(worker, 'export_selected', export)
    monkeypatch.setattr(worker, 'cli', cli)
    monkeypatch.setattr(worker, 'reconcile', reconcile)
    result = worker.execute_call(value)
    destroys = [args for kind, args in calls if kind == 'cli' and args[0] == 'destroy']
    if fault:
        assert result['verification']['reasonCode'] == reason
        assert result['phase'] == 'preserving' and result['effectAdmitted'] is False
        assert result['status'] == 'stopped' and not destroys
        assert not list(tmp_path.glob('retained-*/retention.json'))
        assert state['launch'] == 'stopped' and order == ['stopped', 'export']
    else:
        assert result['ok'] and result['status'] == 'removed' and result['retention']['archiveOnly']
        assert len(destroys) == 1
        assert order == ['stopped', 'export', 'rechecked', 'destroy', 'removed'] + (
            ['absent'] if action == 'finishCleanup' else [])
    assert not any(args[0] in {'exec', 'stop', 'launch'} for kind, args in calls if kind in {'cli', 'incus'})


@pytest.mark.parametrize('change', ['running', 'foreign', 'failed-export'])
def test_artifact_recovery_never_runs_or_removes_instance_on_failure(tmp_path, backend, change):
    calls, state = backend
    state.update(instance='Stopped', launch='stopped')
    if change == 'running':
        state.update(instance='Running', launch='ready')
    if change == 'foreign':
        state['owner'] = 'foreign-root'
    if change == 'failed-export':
        state['export_failed'] = True
    result = worker.execute_call(payload(tmp_path, 'exportArtifacts'))
    assert not result['ok']
    assert not any(args[0] in {'exec', 'stop', 'destroy', 'launch'} for _, args in calls)


def test_identity_is_stable_and_root_bound(tmp_path):
    value = payload(tmp_path)
    assert worker.identity(value) == worker.identity(dict(value))
    assert worker.identity(value) != worker.identity({**value, 'rootOwner': 'different'})


def test_environment_does_not_forward_secrets_or_drop_explicit_total_policy(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'not-forwarded')
    cfg = {**payload(tmp_path)['incus'], 'maxInstances': 15}
    env = worker.environment(cfg, tmp_path)
    assert 'OPENAI_API_KEY' not in env and 'PYTHONPATH' not in env
    assert env['AMPLIFIER_DTU_MAX_INSTANCES'] == '15'
    assert env['AMPLIFIER_DTU_ADMISSION_DIR'] == '/private/admission'
    assert env['AMPLIFIER_DTU_INCUS_PROJECT'] == 'tests'


def test_start_uses_exact_offline_profile_and_source_receipt(tmp_path, backend):
    value = payload(tmp_path, 'start')
    result = worker.execute_call(value)
    assert result['ok'] and result['sourceReady']
    profile = json.loads((tmp_path / 'incus-profile.json').read_text())
    assert profile['base']['image'] == 'local:' + 'a' * 64
    assert profile['base']['config']['security.privileged'] == 'false'
    assert profile['base']['config']['security.nesting'] == 'false'
    launch = next(args for kind, args in backend[0] if kind == 'cli' and args[0] == 'launch')
    assert '--offline' in launch and 'parent-root' in launch and 'stable-launch' in launch
    assert worker.common.source_ready(tmp_path, result['universeId'], value['snapshot'])


def test_capacity_refusal_is_definitive_without_source_attempt(tmp_path, backend, monkeypatch):
    original = worker.cli
    def cli(config, env, args, timeout=30):
        if args[0] == 'launch':
            raise worker.BackendFailure('execution_capacity', 'full', {'capacity': {'available': False}}, admitted=False)
        return original(config, env, args, timeout)
    monkeypatch.setattr(worker, 'cli', cli)
    result = worker.execute_call(payload(tmp_path, 'start'))
    assert result['status'] == 'not-started' and not result['effectAdmitted']
    assert result['admissionDenied']
    assert not (tmp_path / 'source-attempt.json').exists()
    assert not any(args[0] in {'exec','file'} for kind,args in backend[0] if kind == 'incus')


@pytest.mark.parametrize('code', ['approved_image_missing', 'approved_image_unavailable'])
def test_image_prerequisite_refusal_is_known_and_not_capacity_retry(tmp_path, backend, monkeypatch, code):
    original_cli = worker.cli
    # Exercise the actual CLI receipt classifier even though other backend
    # calls use the fixture. No raw backend diagnostic is exposed to the model.
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: {
        'exit_code': 1, 'truncated': False, 'stdout': json.dumps({'error': code}), 'stderr': 'private backend diagnostic'})
    def cli(config, env, args, timeout=30):
        if args[0] == 'launch':
            return qualified_cli(config, env, args, timeout)
        return original_cli(config, env, args, timeout)
    monkeypatch.setattr(worker, 'cli', cli)
    result = worker.execute_call(payload(tmp_path, 'start'))
    assert result['code'] == code and result['status'] == 'not-started'
    assert not result['effectAdmitted'] and not result['admissionDenied']
    assert 'approved local image' in result['message']
    assert 'private backend diagnostic' not in json.dumps(result)
    assert not (tmp_path / 'source-attempt.json').exists()
    assert not any(args[0] in {'exec', 'file'} for kind, args in backend[0] if kind == 'incus')


@pytest.mark.parametrize('observation,code', [([], 'approved_image_missing'), (None, 'approved_image_unavailable'), ('timeout', 'approved_image_unavailable'), ([{'fingerprint': 'a' * 64}], None)])
def test_old_qualified_backend_checks_exact_image_before_fresh_launch(tmp_path, backend, monkeypatch, observation, code):
    value = payload(tmp_path, 'start')
    value['incus']['packageSha256'] = worker.QUALIFIED_PACKAGES['0.4.0']
    backend[1]['launch'] = 'not_recorded'
    original = worker.incus
    def incus(config, env, args, *extra, **kwargs):
        if args[0] == 'image':
            backend[0].append(('incus', args))
            assert args == ['image', 'list', 'local:', 'a' * 64, '--format=json']
            if observation == 'timeout':
                raise worker.BackendFailure('timeout', 'The image lookup timed out.')
            return {'exit_code': 0, 'stdout': json.dumps(observation), 'truncated': False}
        return original(config, env, args, *extra, **kwargs)
    monkeypatch.setattr(worker, 'incus', incus)
    result = worker.execute_call(value)
    launches = [args for kind, args in backend[0] if kind == 'cli' and args[0] == 'launch']
    if code:
        assert result['code'] == code and not result['effectAdmitted'] and not result['admissionDenied']
        assert not launches
    else:
        assert result['ok'] and len(launches) == 1


def test_old_unknown_launch_is_not_reclassified_by_missing_image(tmp_path, backend, monkeypatch):
    value = payload(tmp_path, 'start')
    value['incus']['packageSha256'] = worker.QUALIFIED_PACKAGES['0.4.0']
    backend[1]['launch'] = 'unknown'
    original = worker.cli
    def cli(config, env, args, timeout=30):
        if args[0] == 'launch':
            raise worker.BackendFailure('launch_unknown', 'Original launch remains uncertain.')
        return original(config, env, args, timeout)
    monkeypatch.setattr(worker, 'cli', cli)
    result = worker.execute_call(value)
    assert result['code'] == 'launch_unknown' and result['effectAdmitted']
    assert not any(args[0] == 'image' for _, args in backend[0])


@pytest.mark.parametrize('action', ['read', 'stop', 'remove'])
def test_old_owner_inspection_and_cleanup_do_not_require_base_image(tmp_path, backend, action):
    value = payload(tmp_path, action)
    value['incus']['packageSha256'] = worker.QUALIFIED_PACKAGES['0.4.0']
    if action == 'remove':
        backend[1].update(instance='Stopped', launch='stopped')
    assert worker.execute_call(value)['ok']
    assert not any(args[0] == 'image' for _, args in backend[0])


def test_partial_source_is_not_installed_again(tmp_path, backend):
    (tmp_path / 'source-attempt.json').write_text('{}')
    result = worker.execute_call(payload(tmp_path, 'start'))
    assert not result['ok'] and result['code'] == 'source_unknown'
    assert not any(args[0] in {'exec','file'} for kind,args in backend[0] if kind == 'incus')


def test_lost_source_response_is_recovered_without_unpack(tmp_path, backend):
    value = payload(tmp_path, 'start')
    worker.execute_call(value)
    backend[0].clear()
    assert worker.execute_call({**value, 'action': 'read'})['status'] == 'running'
    assert not any(args[0] in {'exec','file','launch'} for _,args in backend[0])


def test_unconfirmed_source_prevents_execution(tmp_path, backend):
    result = worker.execute_call(payload(tmp_path, 'execute'))
    assert result['code'] == 'source_unknown' and not result['effectAdmitted']
    assert not any(args[0] == 'exec' for _,args in backend[0])


def test_owner_mismatch_prevents_stop_and_removal(tmp_path, backend):
    backend[1]['owner'] = 'another-task'
    for action in ('stop', 'remove', 'finishCleanup'):
        result = worker.execute_call(payload(tmp_path, action))
        assert result['code'] == 'ownership_conflict'
    assert not any(args[0] in {'stop','export','destroy'} for _,args in backend[0])


def test_stop_does_not_export_or_remove(tmp_path, backend):
    assert worker.execute_call(payload(tmp_path, 'stop'))['status'] == 'stopped'
    assert not any(args[0] in {'export','destroy'} for _,args in backend[0])


def test_remove_preserves_full_opaque_archive_before_owner_checked_destroy(tmp_path, backend):
    backend[1].update(instance='Stopped', launch='stopped')
    value = payload(tmp_path, 'remove')
    value['retentionMode'] = 'full'
    result = worker.execute_call(value)
    assert result['status'] == 'removed'
    archive = result['archives'][0]
    assert archive['retainedPaths'] == ['/workspace','/artifacts']
    assert Path(archive['destination']).is_file()
    manifest = Path(archive['destination']).parent / 'manifest.json'
    assert json.loads(manifest.read_text()) == result['archives']
    mutations = [args[0] for _,args in backend[0] if args[0] in {'export','destroy'}]
    assert mutations == ['export','destroy']
    destroy = next(args for _,args in backend[0] if args[0] == 'destroy')
    assert '--owner-root' in destroy and '--request-id' in destroy


def test_incomplete_export_never_destroys(tmp_path, backend):
    backend[1].update(instance='Stopped', launch='stopped', export_failed=True)
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert not result['ok']
    assert not any(args[0] == 'destroy' for _,args in backend[0])
    assert list(tmp_path.glob('retained-*/incomplete.json'))


@pytest.mark.parametrize('action', ['remove', 'finishCleanup'])
def test_running_remove_never_exports_or_stops(tmp_path, backend, action):
    assert worker.execute_call(payload(tmp_path, action))['code'] == 'not_stopped'
    assert not any(args[0] in {'stop','export','destroy'} for _,args in backend[0])


def test_storage_shortage_preserves_stopped_environment(tmp_path, backend, monkeypatch):
    backend[1].update(instance='Stopped', launch='stopped')
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=1))
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['code'] == 'retention_storage' and result['status'] == 'stopped'
    assert not any(args[0] in {'export','destroy'} for _,args in backend[0])


def test_real_output_overflow_is_bounded_known_completion():
    result = worker.run([sys.executable, '-c', 'print("x"*200000)'], {'PATH': os.defpath}, 5)
    assert result['exit_code'] == 0 and result['truncated']
    assert len(result['stdout']) == worker.OUTPUT_BYTES
    assert len(json.dumps(result).encode()) < 128 * 1024


def test_real_timeout_is_uncertain_and_kills_owned_client():
    with pytest.raises(worker.BackendFailure) as error:
        worker.run([sys.executable, '-c', 'import time;time.sleep(30)'], {'PATH': os.defpath}, 0.1)
    assert error.value.code == 'timeout' and error.value.admitted


def test_package_digest_changes_and_rejects_symlink(tmp_path):
    package = tmp_path / 'package'
    package.mkdir()
    code = package / '__init__.py'
    code.write_text('one')
    before = worker.package_digest(package)
    code.write_text('two')
    assert worker.package_digest(package) != before
    code.unlink()
    code.symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError):
        worker.package_digest(package)


@pytest.mark.parametrize('version', ['0.4.0', '0.4.1', '0.4.2', '0.4.3'])
@pytest.mark.parametrize('wrapper', [False, True])
def test_configuration_binds_exact_code_and_preserves_operator_total(tmp_path, monkeypatch, version, wrapper):
    package = tmp_path / 'installed'
    package.mkdir()
    (package / '__init__.py').write_text('# qualified source')
    launcher = tmp_path / 'legacy'
    launcher.write_text(("#!/bin/sh\n'''exec' '" + sys.executable + "' \"$0\" \"$@\"\n' '''\n")
                       if wrapper else '#!' + sys.executable + '\n# entry point\n')
    launcher.chmod(0o700)
    incus = tmp_path / 'incus'
    incus.write_text('#!/bin/sh\nexit 0\n')
    incus.chmod(0o700)
    for name in ('config', 'admission'):
        (tmp_path / name).mkdir(mode=0o700)
    config = {'executable': str(launcher), 'executableSha256': worker.file_digest(launcher)[0],
        'packageSha256': worker.package_digest(package), 'incusExecutable': str(incus),
        'project': 'qualified', 'image': 'a' * 64, 'configDir': str(tmp_path / 'config'),
        'admissionDir': str(tmp_path / 'admission')}
    monkeypatch.setattr(worker, 'QUALIFIED_PACKAGES', {version: config['packageSha256']})
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: {
        'exit_code': 0, 'truncated': False,
        'stdout': json.dumps({'version': version, 'directory': str(package)})})
    monkeypatch.setenv('AMPLIFIER_DTU_MAX_INSTANCES', '15')
    qualified = worker.validate_configuration(config)
    assert qualified == {**config, 'maxInstances': 15, 'interpreter': sys.executable, 'packageDirectory': str(package)}
    monkeypatch.setattr(worker, 'QUALIFIED_PACKAGES', {'other-version': config['packageSha256']})
    with pytest.raises(ValueError, match='qualified generation'):
        worker.validate_configuration(config)
    monkeypatch.setattr(worker, 'QUALIFIED_PACKAGES', {version: config['packageSha256']})
    (package / '__init__.py').write_text('# changed source')
    with pytest.raises(ValueError, match='qualified generation'):
        worker.validate_configuration(config)


def test_private_directory_rejects_public_or_symlink_path(tmp_path):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o755)
    with pytest.raises(ValueError):
        worker.private_directory(str(directory))
    directory.chmod(0o700)
    alias = tmp_path / 'alias'
    alias.symlink_to(directory)
    with pytest.raises(ValueError):
        worker.private_directory(str(alias))


def test_manifest_failure_preserves_stopped_instance(tmp_path, backend, monkeypatch):
    backend[1].update(instance='Stopped', launch='stopped')
    def fail(*args):
        raise OSError('retained manifest write failed')
    monkeypatch.setattr(worker.common, 'retain_manifest', fail)
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['phase'] == 'verifying' and result['effectAdmitted'] is False
    assert not any(args[0] == 'destroy' for _, args in backend[0])
    assert list(tmp_path.glob('retained-*/incomplete.json'))


def test_real_export_watchdog_bounds_spool_and_leaves_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, 'EXPORT_BYTES', 1024)
    target = tmp_path / 'partial.tar'
    code = 'from pathlib import Path;import time;Path(' + repr(str(target)) + ').write_bytes(b"x"*2048);time.sleep(30)'
    with pytest.raises(worker.BackendFailure) as error:
        worker.run([sys.executable, '-c', code], {'PATH': os.defpath}, 5, watched_file=target)
    assert error.value.code == 'export_limit' and target.stat().st_size == 2048


def test_control_output_has_separate_bound(monkeypatch):
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        return {'exit_code': 0, 'stdout': '{}', 'stderr': '', 'truncated': False}
    monkeypatch.setattr(worker, 'run', run)
    config = {'incusExecutable': '/usr/bin/incus', 'executable': '/qualified/legacy', 'project': 'owned'}
    worker.incus(config, {}, ['query', '/1.0'])
    worker.cli(config, {}, ['capacity'])
    assert all(call['output_bytes'] == worker.CONTROL_BYTES for call in calls)


def test_capacity_probe_needs_no_launch_identity(tmp_path, backend):
    value = payload(tmp_path, 'capacity')
    value.update(rootOwner=None, operationId=None, requestId=None)
    result = worker.execute_call(value)
    assert result['ok'] and result['available']
    assert backend[0] == [('cli', ['capacity'])]


def test_default_retention_is_selected_files(tmp_path, backend):
    backend[1].update(instance='Stopped', launch='stopped')
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['status'] == 'removed'
    assert [row['source'] for row in result['archives']] == ['/workspace', '/artifacts']
    assert result['retention']['verified'] and result['retention']['policy'] == 'files'
    assert result['retention']['totalBytes'] == result['retention']['archiveBytes'] + len(b'exact archive')


@pytest.mark.parametrize('state,confirmed', [('unknown', False), ('not_recorded', False), ('removed', True), ('ready', True)])
def test_reconcile_absence_requires_exact_settled_receipt(tmp_path, backend, monkeypatch, state, confirmed):
    backend[1]['launch'] = state
    previous = worker.incus
    def query(config, env, arguments, **kwargs):
        if arguments == ['query', '/1.0/instances']:
            return {'exit_code': 0, 'truncated': False, 'stdout': '[]'}
        return previous(config, env, arguments, **kwargs)
    monkeypatch.setattr(worker, 'incus', query)
    result = worker.execute_call(payload(tmp_path, 'reconcile'))
    assert result['observation']['absenceConfirmed'] is confirmed
    assert result['observation']['admissionSettled'] is confirmed
    assert result['status'] == ('absent' if confirmed else 'unknown')
    assert not any(args[0] in {'launch', 'stop', 'destroy'} for _, args in backend[0])


def test_reconcile_daemon_change_and_missing_legacy_identity_are_unverified(tmp_path, backend):
    value = payload(tmp_path, 'reconcile')
    value['expectedBackendIdentity']['daemon'] = 'another'
    assert worker.execute_call(value)['code'] == 'backend_unverified'
    value.pop('expectedBackendIdentity')
    assert worker.execute_call(value)['code'] == 'backend_unverified'


def test_reconcile_query_failure_never_proves_absence(tmp_path, backend, monkeypatch):
    previous = worker.incus
    def query(config, env, arguments, **kwargs):
        if arguments == ['query', '/1.0/instances']:
            raise worker.BackendFailure('timeout', 'fixture timeout')
        return previous(config, env, arguments, **kwargs)
    monkeypatch.setattr(worker, 'incus', query)
    result = worker.execute_call(payload(tmp_path, 'reconcile'))
    assert result['status'] == 'unknown' and not result.get('observation', {}).get('absenceConfirmed')


@pytest.mark.parametrize('version,state,request_matches,owner_matches,exists,settled', [
    ('0.4.3', 'failed', True, True, False, True),
    ('0.4.3', 'unknown', True, True, False, False),
    ('0.4.3', 'failed', False, True, False, False),
    ('0.4.3', 'failed', True, False, False, False),
    ('0.4.3', 'failed', True, True, True, False),
    ('0.4.2', 'failed', True, True, False, True),
    ('0.4.2', 'unknown', True, True, False, False),
    ('0.4.2', 'failed', False, True, False, False),
    ('0.4.2', 'failed', True, False, False, False),
    ('0.4.1', 'failed', True, True, False, False),
    ('0.4.2', 'failed', True, True, True, False),
])
def test_reconcile_terminal_creation_failure_requires_exact_generation_and_absence(
        tmp_path, backend, monkeypatch, version, state, request_matches, owner_matches, exists, settled):
    value = payload(tmp_path, 'reconcile')
    value['incus']['packageSha256'] = worker.QUALIFIED_PACKAGES[version]
    backend[1].update(launch=state, owner='parent-root' if owner_matches else 'peer')
    original_cli, original_incus = worker.cli, worker.incus
    def receipt(config, env, arguments, **kwargs):
        result = original_cli(config, env, arguments, **kwargs)
        if arguments[0] == 'launch-status':
            result['request_id'] = value['operationId'] if request_matches else 'another-request'
        return result
    def inventory(config, env, arguments, **kwargs):
        if arguments == ['query', '/1.0/instances']:
            instance = json.loads(original_incus(config, env, ['query', '/1.0/instances/owned'])['stdout'])
            instance['name'] = worker.identity(value)
            return {'exit_code': 0, 'truncated': False, 'stdout': json.dumps(['/1.0/instances/' + worker.identity(value)] if exists else [])}
        return original_incus(config, env, arguments, **kwargs)
    monkeypatch.setattr(worker, 'cli', receipt)
    monkeypatch.setattr(worker, 'incus', inventory)
    result = worker.execute_call(value)
    assert result['observation']['absenceConfirmed'] is settled
    assert result['observation']['admissionSettled'] is settled
    assert not any(args[0] in {'launch', 'stop', 'destroy'} for _, args in backend[0])


@pytest.mark.parametrize('version', ['0.4.2', '0.4.3'])
def test_terminal_failed_creation_preserves_owned_instance_for_cleanup(tmp_path, backend, version):
    value = payload(tmp_path, 'remove')
    value['incus']['packageSha256'] = worker.QUALIFIED_PACKAGES[version]
    backend[1].update(launch='failed', instance='Stopped')
    result = worker.execute_call(value)
    assert result['ok'] and result['status'] == 'removed'
    assert any(args[0] == 'destroy' for _, args in backend[0])
    assert not any(args[0] == 'launch' for _, args in backend[0])


@pytest.mark.parametrize('version', ['0.4.2', '0.4.3'])
def test_failed_creation_cannot_claim_peer_instance_for_cleanup(tmp_path, backend, version):
    value = payload(tmp_path, 'remove')
    value['incus']['packageSha256'] = worker.QUALIFIED_PACKAGES[version]
    backend[1].update(launch='failed', instance='Stopped', owner='peer-root')
    result = worker.execute_call(value)
    assert not result['ok']
    assert not any(args[0] in {'destroy', 'stop', 'launch'} for _, args in backend[0])


def test_reconcile_uses_small_inventory_and_reads_only_exact_owned_metadata(tmp_path, backend, monkeypatch):
    value = payload(tmp_path, 'reconcile')
    original = worker.incus
    calls = []
    def query(config, env, arguments, **kwargs):
        calls.append(arguments)
        if arguments == ['query', '/1.0/instances']:
            paths = ['/1.0/instances/peer-' + str(n) for n in range(100)]
            paths.append('/1.0/instances/' + worker.identity(value) + '?project=tests')
            return {'exit_code': 0, 'truncated': False, 'stdout': json.dumps(paths)}
        assert 'recursion' not in arguments[-1]
        if arguments[0] == 'query' and '/instances/' in arguments[-1]:
            assert arguments[-1] == '/1.0/instances/' + worker.identity(value)
        return original(config, env, arguments, **kwargs)
    monkeypatch.setattr(worker, 'incus', query)
    result = worker.execute_call(value)
    assert result['status'] == 'running' and result['observation']['admissionSettled']
    assert len([args for args in calls if '/instances/' in args[-1]]) == 1


@pytest.mark.parametrize('problem', ['foreign-project', 'malformed-list', 'disappeared', 'truncated-target'])
def test_reconcile_unverifiable_target_never_proves_absence(tmp_path, backend, monkeypatch, problem):
    value = payload(tmp_path, 'reconcile')
    original = worker.incus
    def query(config, env, arguments, **kwargs):
        if arguments == ['query', '/1.0/instances']:
            name = '/1.0/instances/' + worker.identity(value)
            entries = [{'name': name}] if problem == 'malformed-list' else [name + ('?project=other' if problem == 'foreign-project' else '')]
            return {'exit_code': 0, 'truncated': False, 'stdout': json.dumps(entries)}
        if '/instances/' in arguments[-1]:
            return {'exit_code': int(problem == 'disappeared'), 'truncated': problem == 'truncated-target', 'stdout': '{}'}
        return original(config, env, arguments, **kwargs)
    monkeypatch.setattr(worker, 'incus', query)
    result = worker.execute_call(value)
    assert result['status'] == 'unknown' and not result.get('observation', {}).get('absenceConfirmed')


def test_explicit_full_retry_preserves_failed_selection_receipt(tmp_path, backend):
    backend[1].update(instance='Stopped', launch='stopped', export_failed=True)
    assert worker.execute_call(payload(tmp_path, 'remove'))['status'] == 'stopped'
    assert not any(args[0] == 'destroy' for _, args in backend[0])
    backend[1]['export_failed'] = False
    value = payload(tmp_path, 'remove')
    value.update(requestId='explicit-full-retention', retentionMode='full')
    result = worker.execute_call(value)
    assert result['status'] == 'removed' and result['retention']['policy'] == 'full'
    assert list(tmp_path.glob('retained-*/incomplete.json'))


def test_actual_pool_space_failure_keeps_stopped_instance(tmp_path, backend, monkeypatch):
    backend[1].update(instance='Stopped', launch='stopped')
    original = worker.cli
    def capacity(config, env, arguments, timeout=30):
        result = original(config, env, arguments, timeout)
        if arguments == ['capacity']:
            result['storage']['free_bytes'] = 1
        return result
    monkeypatch.setattr(worker, 'cli', capacity)
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['code'] == 'retention_storage'
    assert not any(args[0] in {'export', 'destroy'} for _, args in backend[0])


def test_stream_export_is_quiet_bounded_and_never_extracts(tmp_path, monkeypatch):
    executable = tmp_path/'incus'
    executable.write_text('#!' + sys.executable + '\nimport sys,tarfile\nassert "--quiet" in sys.argv\nwith tarfile.open(fileobj=sys.stdout.buffer,mode="w|") as t:\n for name in ["workspace","artifacts"]:\n  i=tarfile.TarInfo("backup/container/rootfs/"+name);i.type=tarfile.DIRTYPE;t.addfile(i)\n')
    executable.chmod(0o700)
    destination=tmp_path/'selected';destination.mkdir()
    result=worker.export_selected({'incusExecutable':str(executable),'project':'owned'},{'PATH':os.defpath},'owned',destination)
    assert len(result)==2 and not (tmp_path/'workspace').exists()
    smaller=tmp_path/'bounded';smaller.mkdir()
    monkeypatch.setattr(worker,'EXPORT_BYTES',1024)
    with pytest.raises(worker.BackendFailure,match='bound'):
        worker.export_selected({'incusExecutable':str(executable),'project':'owned'},{'PATH':os.defpath},'owned',smaller)
    assert not (smaller/'retention.json').exists()


def test_recovery_cleanup_verifies_physical_absence_after_preservation(tmp_path, backend, monkeypatch):
    calls, state = backend
    state.update(instance='Stopped', launch='stopped')
    monkeypatch.setattr(worker, 'reconcile', lambda *args: {'status': 'absent' if state['launch']=='removed' else 'stopped'})
    result = worker.execute_call(payload(tmp_path, 'finishCleanup'))
    assert result['ok'] and result['retention']['archiveOnly']
    assert [args[0] for _, args in calls if args[0] in {'export','exec','stop','destroy','launch'}] == ['export','destroy']
    assert {path.stem for path in next(tmp_path.glob('cleanup-*')).glob('*.json')} == {'preserving','verifying','rechecking','removing','observing'}


def test_recovery_never_releases_owner_on_unknown_post_destroy_state(tmp_path, backend, monkeypatch):
    backend[1].update(instance='Stopped', launch='stopped')
    monkeypatch.setattr(worker, 'reconcile', lambda *args: {'status': 'unknown'})
    result = worker.execute_call(payload(tmp_path, 'finishCleanup'))
    assert not result['ok'] and result['status'] == 'unknown'
    assert result['retention']['verified']


def test_cleanup_owner_change_after_export_is_not_preservation_failure(tmp_path, backend, monkeypatch):
    backend[1].update(instance='Stopped', launch='stopped')
    previous = worker.observe
    observations = []
    def observe(*args, **kwargs):
        observations.append(args)
        if len(observations) > 1:
            raise worker.BackendFailure('ownership_conflict', 'owner changed')
        return previous(*args, **kwargs)
    monkeypatch.setattr(worker, 'observe', observe)
    result = worker.execute_call(payload(tmp_path, 'finishCleanup'))
    assert result['status'] == 'unknown' and result['effectAdmitted'] is False
    assert result['phase'] == 'rechecking' and result['code'] == 'ownership_conflict'
    assert not any(args[0] == 'destroy' for _, args in backend[0])


@pytest.mark.parametrize('profile_id', ['linux-python', 'python-project', 'browser-test'])
def test_manual_execution_selects_fixed_profile_and_preserves_transport(tmp_path, backend, monkeypatch, profile_id):
    value = {**payload(tmp_path, 'execute'), 'profileId': profile_id,
             'command': "printf '%s' 'literal ; $HOME \"'; exit 7", 'timeoutSeconds': 23,
             'jobEnvironment': {'HOME': '/root'}, 'environment': {'PATH': '/caller'}, 'user': 'root'}
    name = worker.identity(value)
    worker.common.retain_source_ready(tmp_path, name, value['snapshot'])
    original = worker.incus
    executions = []
    expected = {'exit_code': 7, 'stdout': 'partial', 'stderr': 'error', 'truncated': True}
    def incus(config, env, args, timeout=30, **kwargs):
        if args[0] == 'exec':
            executions.append((args, timeout, kwargs))
            return expected
        return original(config, env, args, timeout, **kwargs)
    monkeypatch.setattr(worker, 'incus', incus)
    result = worker.execute_call(value)
    assert result == {'ok': True, 'universeId': name, 'result': expected}
    assert len(executions) == 1
    args, timeout, kwargs = executions[0]
    assert args[:8] == ['exec', name, '--cwd', '/workspace', '--', 'python3', '-I', '-c']
    assert args[9] == 'execute'
    assert json.loads(args[10]) == {
        'command': value['command'],
        'environment': worker.common.selected_profile(value)['environment']}
    assert timeout == 23 and kwargs == {}
    assert not any(args[0] in {'launch', 'file', 'stop', 'destroy', 'export'} for _, args in backend[0])


@pytest.mark.parametrize('fault', ['owner', 'snapshot', 'stopped', 'backend'])
def test_manual_currentness_refusals_dispatch_nothing(tmp_path, backend, fault):
    value = payload(tmp_path, 'execute')
    worker.common.retain_source_ready(tmp_path, worker.identity(value), value['snapshot'])
    if fault == 'owner':
        backend[1]['owner'] = 'another-root'
    elif fault == 'snapshot':
        value['snapshot']['archive_sha256'] = 'changed'
    elif fault == 'stopped':
        backend[1].update(instance='Stopped', launch='stopped')
    else:
        value['expectedBackendIdentity']['daemon'] = 'changed'
    result = worker.execute_call(value)
    assert not result['ok']
    assert not any(args[0] in {'exec', 'launch', 'file', 'stop', 'destroy', 'export'} for _, args in backend[0])


def test_manual_timeout_keeps_unknown_without_redispatch(tmp_path, backend, monkeypatch):
    value = payload(tmp_path, 'execute')
    worker.common.retain_source_ready(tmp_path, worker.identity(value), value['snapshot'])
    original = worker.incus
    executions = []
    def incus(config, env, args, timeout=30, **kwargs):
        if args[0] == 'exec':
            executions.append(args)
            raise worker.BackendFailure('timeout', 'The operation may still be running.')
        return original(config, env, args, timeout, **kwargs)
    monkeypatch.setattr(worker, 'incus', incus)
    result = worker.execute_call(value)
    assert not result['ok'] and result['status'] == 'unknown' and result['effectAdmitted']
    assert result['code'] == 'timeout' and len(executions) == 1


def test_dynamic_inventory_reads_only_receipts_but_dispatch_checks_runtime(tmp_path, monkeypatch):
    package = tmp_path / 'installed'; package.mkdir()
    (package / '__init__.py').write_text('# candidate')
    launcher = tmp_path / 'engine'; launcher.write_text('#!' + sys.executable + '\n'); launcher.chmod(0o700)
    incus = tmp_path / 'incus'; incus.write_text('#!/bin/sh\n'); incus.chmod(0o700)
    for name in ('config', 'admission', 'owned'):
        (tmp_path / name).mkdir(mode=0o700)
    config = {'executable': str(launcher), 'executableSha256': worker.file_digest(launcher)[0],
        'packageSha256': worker.package_digest(package), 'incusExecutable': str(incus),
        'project': 'qualified', 'image': 'a' * 64, 'configDir': str(tmp_path / 'config'),
        'admissionDir': str(tmp_path / 'admission'), 'engineQualification': {'exact': 'receipt'}}
    monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: {'exit_code': 0, 'truncated': False,
        'stdout': json.dumps({'version': 'next', 'directory': str(package)})})
    checks = []
    def verify(config, *, owner, runtime):
        checks.append(runtime)
        if runtime:
            raise ValueError('runtime changed')
    monkeypatch.setattr(worker, 'engine_generations', lambda: SimpleNamespace(verify=verify))
    worker.validate_configuration(config)
    worker.validate_configuration(config)
    assert checks == [False, False]
    request = {'root': str(tmp_path / 'owned'), 'incus': config, 'action': 'capacity'}
    with pytest.raises(ValueError, match='runtime changed'):
        worker.execute_call(request)
    assert checks == [False, False, False, True]
