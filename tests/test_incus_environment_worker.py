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
    def export_selected(config, env, name, destination):
        import tarfile
        calls.append(('incus', ['export', name, '-']))
        if state['export_failed']:
            raise worker.BackendFailure('export_failed', 'fixture')
        result = []
        for selection in ('workspace', 'artifacts'):
            path = destination / (selection + '.tar')
            with tarfile.open(path, 'w') as archive:
                item = tarfile.TarInfo(selection); item.type = tarfile.DIRTYPE; archive.addfile(item)
            result.append({'id': name, 'source': '/' + selection, 'destination': str(path)})
        return result
    monkeypatch.setattr(worker, 'export_selected', export_selected)
    return calls, state


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
    def export_selected(config, env, name, destination):
        import tarfile
        calls.append(('incus', ['export', name, '-']))
        if state['export_failed']:
            raise worker.BackendFailure('export_failed', 'fixture')
        result = []
        for selection in ('workspace', 'artifacts'):
            path = destination / (selection + '.tar')
            with tarfile.open(path, 'w') as archive:
                item = tarfile.TarInfo(selection); item.type = tarfile.DIRTYPE; archive.addfile(item)
            result.append({'id': name, 'source': '/' + selection, 'destination': str(path)})
        return result
    monkeypatch.setattr(worker, 'export_selected', export_selected)
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
    for action in ('stop', 'remove'):
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


def test_running_remove_never_exports_or_stops(tmp_path, backend):
    assert worker.execute_call(payload(tmp_path, 'remove'))['code'] == 'not_stopped'
    assert not any(args[0] in {'stop','export','destroy'} for _,args in backend[0])


def test_storage_shortage_preserves_stopped_environment(tmp_path, backend, monkeypatch):
    backend[1].update(instance='Stopped', launch='stopped')
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda root: SimpleNamespace(free=1))
    result = worker.execute_call(payload(tmp_path, 'remove'))
    assert result['code'] == 'retention_storage' and result['status'] == 'unknown'
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


@pytest.mark.parametrize('version', ['0.4.0', '0.4.1'])
def test_configuration_binds_exact_code_and_preserves_operator_total(tmp_path, monkeypatch, version):
    package = tmp_path / 'installed'
    package.mkdir()
    (package / '__init__.py').write_text('# qualified source')
    launcher = tmp_path / 'legacy'
    launcher.write_text('#!' + sys.executable + '\n# entry point\n')
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
    with pytest.raises(OSError):
        worker.execute_call(payload(tmp_path, 'remove'))
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
        if arguments == ['query', '/1.0/instances?recursion=1']:
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
        if arguments == ['query', '/1.0/instances?recursion=1']:
            raise worker.BackendFailure('timeout', 'fixture timeout')
        return previous(config, env, arguments, **kwargs)
    monkeypatch.setattr(worker, 'incus', query)
    result = worker.execute_call(payload(tmp_path, 'reconcile'))
    assert result['status'] == 'unknown' and not result.get('observation', {}).get('absenceConfirmed')


def test_explicit_full_retry_preserves_failed_selection_receipt(tmp_path, backend):
    backend[1].update(instance='Stopped', launch='stopped', export_failed=True)
    assert worker.execute_call(payload(tmp_path, 'remove'))['status'] == 'unknown'
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
