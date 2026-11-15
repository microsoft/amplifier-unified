"""Prepared checks advertise actual tools/source and retain everything once."""
import copy
import json
import os
import signal
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import test_environment_profiles as profiles
from amplifier_web import test_environment_preparation as preparation
from amplifier_web import test_environment_prepare_guest as guest
from amplifier_web import test_environment_initialize_guest as initializer
from test_environment_preparation import archive, backend, config
from test_test_environment_worker import library, payload, worker


def test_profile_advertises_real_interpreter_artifacts_and_git_absence(tmp_path):
    browser = next(row for row in profiles.catalog() if row['id'] == 'browser-test')
    contract = browser['executionContract']
    assert contract['python'] == browser['environment']['AMPLIFIER_TEST_PYTHON']
    assert contract['manifest'] == browser['environment']['AMPLIFIER_TEST_CONTRACT']
    assert browser['environment']['AMPLIFIER_TEST_ARTIFACTS'] == '/artifacts'
    assert contract['source']['gitMetadataAvailable'] is False
    assert '.venv' not in contract['python']
    snapshot = {**archive(tmp_path), 'source_head': 'a' * 40, 'snapshot_commit': 'b' * 40, 'snapshot_tree': 'c' * 40}
    plan = preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project')
    assert plan['executionContract']['source'] == {
        'gitMetadataAvailable': False, 'archiveSha256': snapshot['archive_sha256'],
        'sourceHead': 'a' * 40, 'snapshotCommit': 'b' * 40, 'snapshotTree': 'c' * 40}
    assert 'source_branch' not in plan['executionContract']['source']


@pytest.mark.parametrize('profile_id,total', [('python-project', 512), ('browser-test', 1024),
                                             ('browser-test', 65), ('python-project', 64)])
def test_docker_runtime_workdir_splits_existing_temporary_budget(profile_id, total):
    request = profiles.freeze(profile_id, {'tmpMiB': total})
    contract = profiles.execution_contract(profile_id, backend='docker', resource_request=request)
    runtime = contract['runtimeWorkdir']
    assert runtime == {
        'path': '/unified-runtime', 'environmentVariable': 'AMPLIFIER_TEST_RUNTIME',
        'sizeMiB': total - total // 4, 'tmpMiB': total // 4,
        'uid': 65534, 'gid': 65534, 'mode': '0700', 'retained': False,
    }
    value = {'profileId': profile_id, 'resourceRequest': request,
             'rootOwner': 'owner', 'operationId': 'launch', 'preparedRuntime': True,
             'runtimeWorkdir': runtime}
    twin = worker.profile('fixture-image', value)['services']['twin']
    mounts = twin['tmpfs']
    assert mounts == [
        '/tmp:rw,noexec,nosuid,nodev,size=' + str(runtime['tmpMiB'] * 1024 ** 2),
        '/unified-runtime:rw,exec,nosuid,nodev,size=' + str(runtime['sizeMiB'] * 1024 ** 2)
        + ',mode=0700,uid=65534,gid=65534',
    ]
    assert runtime['tmpMiB'] + runtime['sizeMiB'] == total
    assert twin['read_only'] and twin['cap_drop'] == ['ALL']
    assert twin['security_opt'] == ['no-new-privileges:true']
    assert twin['volumes'] == ['workspace:/workspace', 'artifacts:/artifacts', 'control:/var/lib/unified-jobs']
    environment = worker.job_environment(value)
    assert environment['AMPLIFIER_TEST_RUNTIME'] == runtime['path']
    assert environment['HOME'] == '/tmp/unified-test/home'
    assert environment['XDG_CACHE_HOME'] == '/tmp/unified-test/cache'
    assert environment['PYTHONPATH'] == '/workspace'  # Source checks keep their existing default.


@pytest.mark.parametrize('total', [0, 1, 63, True, '1024', 1048577])
def test_undersized_or_invalid_runtime_budget_refused_before_preparation(tmp_path, total):
    snapshot = archive(tmp_path)
    request = profiles.freeze('python-project', {'tmpMiB': total})
    with pytest.raises(ValueError):
        preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project',
                         backend='docker', resource_request=request)


def test_runtime_contract_is_backend_specific_and_bound_before_build(tmp_path, backend):
    snapshot = archive(tmp_path)
    request = profiles.freeze('python-project', {'tmpMiB': 65})
    receipt = preparation.prepare(config(tmp_path), tmp_path, snapshot,
                                  'python-project', 'runtime', resource_request=request)
    contract = receipt['executionContract']
    assert contract['runtimeWorkdir'] == receipt['intent']['executionContract']['runtimeWorkdir']
    assert contract['runtimeWorkdir']['sizeMiB'] == 49
    assert contract['runtimeWorkdir']['tmpMiB'] == 16
    incus = preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project', backend='incus')
    assert 'runtimeWorkdir' not in incus['executionContract']
    assert 'runtimeWorkdir' not in profiles.execution_contract('python-project')
    assert profiles.execution_contract('linux-python', backend='docker') is None
    with pytest.raises(ValueError):
        preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project', backend='remote-docker')


@pytest.mark.parametrize('change', ['path', 'size', 'owner', 'mode', 'retained', 'profile', 'custom'])
def test_runtime_contract_cannot_select_arbitrary_mount_or_environment(change):
    runtime = profiles.execution_contract('browser-test', backend='docker')['runtimeWorkdir']
    value = {'profileId': 'browser-test', 'rootOwner': 'owner', 'operationId': 'launch',
             'preparedRuntime': True, 'runtimeWorkdir': copy.deepcopy(runtime)}
    if change == 'path':
        value['runtimeWorkdir']['path'] = '/artifacts/installed'
    elif change == 'size':
        value['runtimeWorkdir']['sizeMiB'] += 1
    elif change == 'owner':
        value['runtimeWorkdir']['uid'] = 0
    elif change == 'mode':
        value['runtimeWorkdir']['mode'] = '0777'
    elif change == 'retained':
        value['runtimeWorkdir']['retained'] = True
    elif change == 'profile':
        value['profileId'] = 'linux-python'
    else:
        value['composeFile'] = 'compose.yaml'
    with pytest.raises(ValueError, match='runtime'):
        worker.profile('fixture', value)
    with pytest.raises(ValueError, match='runtime'):
        worker.job_environment(value)
    expected = profiles.execution_contract('browser-test', backend='docker')
    changed = {**expected, 'runtimeWorkdir': value['runtimeWorkdir'],
               'pythonBuild': {'available': False, 'reason': 'This source declares no Python build backend.'}}
    if change not in {'profile', 'custom'}:
        with pytest.raises(ValueError):
            preparation.validate_execution_contract(changed, expected)


@pytest.mark.parametrize('profile_id', ['linux-python', 'python-project', 'browser-test', 'app-preview'])
def test_legacy_and_unprepared_payloads_do_not_advertise_runtime(profile_id):
    value = {'profileId': profile_id, 'rootOwner': 'owner', 'operationId': 'launch'}
    assert 'AMPLIFIER_TEST_RUNTIME' not in worker.job_environment(value)
    assert len(worker.profile('fixture', value)['services']['twin']['tmpfs']) == 1
    value.update(composeFile='compose.yaml', preparedRuntime=False)
    assert 'AMPLIFIER_TEST_RUNTIME' not in worker.job_environment(value)


@pytest.fixture
def runtime_mount(tmp_path, monkeypatch):
    """Synthetic mount observations only: no mount/chown/container on the host."""
    path = tmp_path / 'runtime'
    path.mkdir(mode=0o700)
    value = profiles.execution_contract('python-project', backend='docker')['runtimeWorkdir']
    state = {'uid': 65534, 'gid': 65534, 'mount': True,
             'flags': os.ST_NOSUID | os.ST_NODEV, 'size': value['sizeMiB'] * 1024 ** 2}
    real_path, real_lstat, real_is_mount, real_statvfs = Path, Path.lstat, Path.is_mount, os.statvfs
    monkeypatch.setattr(initializer, 'Path', lambda name: path if str(name) == '/unified-runtime' else real_path(name))
    def lstat(self):
        info = real_lstat(self)
        return (SimpleNamespace(st_mode=info.st_mode, st_uid=state['uid'], st_gid=state['gid'])
                if self == path else info)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(Path, 'is_mount', lambda self: state['mount'] if self == path else real_is_mount(self))
    monkeypatch.setattr(initializer.os, 'statvfs', lambda name: SimpleNamespace(
        f_flag=state['flags'], f_frsize=4096, f_blocks=state['size'] // 4096
    ) if name == path else real_statvfs(name))
    return path, value, state


def test_runtime_mount_verified_before_marker_and_even_on_reinitialization(tmp_path, runtime_mount):
    path, runtime, state = runtime_mount
    prefix, workspace, artifacts = [tmp_path / name for name in ('prefix', 'workspace', 'artifacts')]
    (prefix / 'venv/bin').mkdir(parents=True); workspace.mkdir(); artifacts.mkdir()
    (prefix / 'venv/bin/python').write_text('fixture')
    (artifacts / 'report').write_text('retained')
    contract = profiles.execution_contract('python-project', backend='docker')
    (prefix / 'plan.json').write_text(json.dumps({
        'profileId': 'python-project', 'preparationId': 'exact', 'executionContract': contract}))
    (prefix / 'execution-contract.json').write_text(json.dumps(contract))
    initializer.initialize(prefix, workspace)
    marker = workspace / '.unified-prepared-dependencies.json'
    assert json.loads(marker.read_text()) == {'preparationId': 'exact'}
    assert list(path.iterdir()) == []  # No defaults/caches/dependencies were moved here.
    state['flags'] |= os.ST_NOEXEC
    with pytest.raises(ValueError, match='mount flags'):
        initializer.initialize(prefix, workspace)
    assert marker.exists() and (artifacts / 'report').read_text() == 'retained'


@pytest.mark.parametrize('damage', ['uid', 'gid', 'mode', 'symlink', 'not-mount',
                                   'noexec', 'suid', 'device', 'readonly', 'size'])
def test_runtime_mount_observations_fail_closed(runtime_mount, tmp_path, damage):
    path, runtime, state = runtime_mount
    if damage in ('uid', 'gid'):
        state[damage] = 0
    elif damage == 'mode':
        path.chmod(0o755)
    elif damage == 'symlink':
        path.rmdir()
        target = tmp_path / 'artifacts'; target.mkdir()
        path.symlink_to(target, target_is_directory=True)
    elif damage == 'not-mount':
        state['mount'] = False
    elif damage == 'noexec':
        state['flags'] |= os.ST_NOEXEC
    elif damage == 'suid':
        state['flags'] &= ~os.ST_NOSUID
    elif damage == 'device':
        state['flags'] &= ~os.ST_NODEV
    elif damage == 'readonly':
        state['flags'] |= os.ST_RDONLY
    else:
        state['size'] += 1024 ** 2
    with pytest.raises(ValueError, match='runtime workdir'):
        initializer.verify_runtime_workdir(runtime)


@pytest.mark.parametrize('path', ['/workspace/installed', '/artifacts/installed',
                                  '/var/lib/unified-jobs/runtime', '/root', '/tmp/runtime',
                                  '/unified-runtime/../artifacts', 'relative', '/unified-runtime\x00'])
def test_runtime_initializer_refuses_untrusted_or_overlapping_contract_paths(path):
    runtime = profiles.execution_contract('python-project', backend='docker')['runtimeWorkdir']
    with pytest.raises(ValueError, match='contract'):
        initializer.verify_runtime_workdir({**runtime, 'path': path})


def test_runtime_field_reaches_manual_and_durable_commands_only_from_owner(library, tmp_path):
    _, calls = library
    request = {**payload(tmp_path, 'execute'), 'profileId': 'browser-test', 'jobId': 'runtime-job',
               'preparedRuntime': True,
               'runtimeWorkdir': profiles.execution_contract('browser-test', backend='docker')['runtimeWorkdir']}
    worker.retain_source_ready(tmp_path, 'owned', request['snapshot'])
    worker.execute_call(request)
    manual = json.loads(shlex.split(calls[-1][1][1])[5])
    with pytest.raises(json.JSONDecodeError):  # Only the controlled observer response is absent.
        worker.execute_call({**request, 'action': 'job_start'})
    durable = json.loads(shlex.split(calls[-1][1][1])[5])
    assert manual['environment'] == durable['environment']
    assert manual['environment']['AMPLIFIER_TEST_RUNTIME'] == '/unified-runtime'
    assert manual['environment']['HOME'] == '/tmp/unified-test/home'
    assert len(calls) == 2


def test_invalid_source_identity_never_becomes_guest_contract(tmp_path):
    snapshot = {**archive(tmp_path), 'source_head': 'not-a-commit'}
    with pytest.raises(ValueError, match='identity'):
        preparation.plan(tmp_path / 'source.tar', snapshot, 'python-project')


def test_build_prerequisites_are_separate_and_do_not_build_wheel(tmp_path, monkeypatch):
    project, prefix = tmp_path / 'project', tmp_path / 'prepared'
    project.mkdir(); prefix.mkdir()
    (project / 'pyproject.toml').write_text('[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"')
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if len(args) > 2 and args[1] == '-c' and 'get_requires_for_build' in args[2]:
            Path(args[-1]).write_text('["editables"]')
        return 'build==1.2.2\nhatchling==1.27.0' if 'freeze' in args else None
    monkeypatch.setattr(guest, 'run', run)
    result = guest.prepare_build('uv', '/test-environment/bin/python', project, prefix)
    assert result['available'] and result['dynamicRequirements'] == ['editables']
    assert result['python'] == str(prefix / 'build-venv/bin/python')
    assert '--no-isolation' in result['command'] and '/artifacts/dist' in result['command']
    installs = [row for row in calls if 'install' in row]
    assert len(installs) == 2 and all(str(prefix / 'build-venv/bin/python') in row for row in installs)
    assert not any('-m' in row and 'build' in row for row in calls)
    assert result['packages'] == ['build==1.2.2', 'hatchling==1.27.0']


def test_nonpackage_source_does_not_prepare_build_tools(tmp_path, monkeypatch):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="plain"\nversion="1"')
    monkeypatch.setattr(guest, 'run', lambda *a, **k: pytest.fail('unneeded preparation'))
    assert not guest.prepare_build('uv', 'python', tmp_path, tmp_path)['available']


@pytest.mark.parametrize('requires', [None, 'build', ['--python=/elsewhere'], ['a' * 2049], ['x'] * 65])
def test_build_requirements_are_bounded_package_arguments(requires):
    with pytest.raises(ValueError, match='bounded'):
        guest.build_requirements(requires)


@pytest.mark.parametrize('change', ['source', 'python', 'profile', 'runtime'])
def test_initializer_refuses_missing_or_mismatched_contract_before_marker(tmp_path, change):
    prefix, workspace, artifacts = [tmp_path / name for name in ('prefix', 'workspace', 'artifacts')]
    (prefix / 'venv/bin').mkdir(parents=True); workspace.mkdir()
    (prefix / 'venv/bin/python').write_text('fixture')
    contract = profiles.execution_contract('python-project', {'archiveSha256': '1' * 64})
    (prefix / 'plan.json').write_text(json.dumps({'profileId': 'python-project', 'preparationId': 'exact', 'executionContract': contract}))
    actual = copy.deepcopy(contract)
    if change == 'source':
        actual['source']['archiveSha256'] = '2' * 64
    elif change == 'profile':
        actual['profileId'] = 'browser-test'
    elif change == 'runtime':
        actual['runtimeWorkdir'] = profiles.execution_contract('python-project', backend='docker')['runtimeWorkdir']
    else:
        (prefix / 'venv/bin/python').unlink()
    (prefix / 'execution-contract.json').write_text(json.dumps(actual))
    with pytest.raises(ValueError, match='execution contract'):
        initializer.initialize(prefix, workspace)
    assert not (workspace / '.unified-prepared-dependencies.json').exists()


def prepared_receipt(tmp_path, request):
    contract = {'version': 1, 'profileId': 'python-project',
                'source': {'archiveSha256': request['snapshot']['archive_sha256']},
                'retention': {'mode': 'archive-only', 'maxBytesPerSelection': 4 * 1024 ** 3}}
    value = {'version': 1, 'status': 'ready', 'profileId': 'python-project',
             'sourceSha256': request['snapshot']['archive_sha256'], 'executionContract': contract}
    (tmp_path / 'preparation').mkdir()
    worker.write_private_record(tmp_path / 'preparation/receipt.json', value)
    return value


def test_first_prepared_remove_uses_bounded_archive_export_without_failed_retry(library, tmp_path, monkeypatch):
    lib, calls = library
    request = {**payload(tmp_path, 'remove'), 'profileId': 'python-project'}
    prepared_receipt(tmp_path, request)
    observations = iter([{'status': 'stopped', 'observation': {'ownership': 'matched'}},
                         {'status': 'stopped', 'observation': {'ownership': 'matched'}},
                         {'status': 'absent', 'observation': {'absenceConfirmed': True}}])
    monkeypatch.setattr(worker, 'docker_reconcile', lambda *a: next(observations))
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda path: SimpleNamespace(free=20 * 1024 ** 3))
    ordinary = lib.export_files
    def export(identity, source, destination, **kwargs):
        # Representative browser source is 498MiB; the old library default is
        # 256MiB. Scale fixture content, but enforce the real producer admission.
        if kwargs.get('max_bytes', 256 * 1024 ** 2) < 498 * 1024 ** 2:
            raise OSError('export maximum exceeded')
        return ordinary(identity, source, destination, **kwargs)
    lib.export_files = export
    result = worker.execute_call(request)
    assert result['ok'] and result['status'] == 'removed'
    exports = [entry for entry in calls if entry[0] == 'export']
    assert len(exports) == 2
    assert all(row[2]['max_bytes'] == worker.retention.RECOVERY_LIMIT and row[2]['timeout_seconds'] == 300 for row in exports)
    assert result['retention']['policy'] == 'archive-only'
    assert len([entry for entry in calls if entry[0] == 'destroy']) == 1


@pytest.mark.parametrize('change', ['source', 'profile', 'policy'])
def test_changed_prepared_retention_contract_never_exports_or_destroys(library, tmp_path, change):
    lib, calls = library
    request = {**payload(tmp_path, 'remove'), 'profileId': 'python-project'}
    value = prepared_receipt(tmp_path, request)
    if change == 'source':
        request['snapshot'] = {**request['snapshot'], 'archive_sha256': 'another'}
    elif change == 'profile':
        request['profileId'] = 'linux-python'
    else:
        value['executionContract']['retention']['maxBytesPerSelection'] *= 2
        path = tmp_path / 'preparation/receipt.json'
        path.write_text(json.dumps(value))
    with pytest.raises(worker.RemovalFailure):
        worker.execute_call(request)
    assert not any(name in {'export', 'destroy'} for name, _, _ in calls)


def test_old_prepared_receipt_keeps_original_custody_contract(tmp_path):
    request = {**payload(tmp_path, 'remove'), 'profileId': 'python-project'}
    (tmp_path / 'preparation').mkdir()
    worker.write_private_record(tmp_path / 'preparation/receipt.json', {'status': 'ready'})
    assert not worker.prepared_archive_custody(request)


def test_first_prepared_remove_low_space_keeps_all_files(library, tmp_path, monkeypatch):
    _, calls = library
    request = {**payload(tmp_path, 'remove'), 'profileId': 'python-project'}
    prepared_receipt(tmp_path, request)
    monkeypatch.setattr(worker, 'docker_reconcile', lambda *a: {'status': 'stopped', 'observation': {'ownership': 'matched'}})
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1024))
    result = worker.execute_call(request)
    assert not result['ok']
    assert not any(name in {'export', 'destroy'} for name, _, _ in calls)
    assert (tmp_path / 'source.tar').read_bytes() == b'source'


def test_first_prepared_remove_owner_change_after_export_never_destroys(library, tmp_path, monkeypatch):
    _, calls = library
    request = {**payload(tmp_path, 'remove'), 'profileId': 'python-project'}
    prepared_receipt(tmp_path, request)
    observations = iter([{'status': 'stopped', 'observation': {'ownership': 'matched'}},
                         {'status': 'stopped', 'observation': {'ownership': 'foreign'}}])
    monkeypatch.setattr(worker, 'docker_reconcile', lambda *a: next(observations))
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda path: SimpleNamespace(free=20 * 1024 ** 3))
    with pytest.raises(worker.RemovalFailure):
        worker.execute_call(request)
    assert len([entry for entry in calls if entry[0] == 'export']) == 2
    assert not any(name == 'destroy' for name, _, _ in calls)
    assert len(list(tmp_path.glob('retained-*/retention.json'))) == 1


from test_incus_environment_worker import backend as incus_backend, payload as incus_payload, worker as incus_worker


def test_incus_first_prepared_remove_uses_same_archive_custody(tmp_path, incus_backend, monkeypatch):
    calls, state = incus_backend
    state.update(instance='Stopped', launch='stopped')
    request = {**incus_payload(tmp_path, 'remove'), 'profileId': 'python-project'}
    prepared_receipt(tmp_path, request)
    original = incus_worker.export_selected
    policies = []
    def exported(*args, **kwargs):
        policies.append(kwargs['archive_only'])
        return original(*args, **kwargs)
    monkeypatch.setattr(incus_worker, 'export_selected', exported)
    result = incus_worker.execute_call(request)
    assert result['ok'] and result['retention']['policy'] == 'archive-only'
    assert policies == [True]


@pytest.mark.parametrize('change', ['missing', 'python', 'source', 'command', 'build-status'])
def test_ready_inventory_cannot_advertise_missing_or_changed_invocations(change):
    expected = profiles.execution_contract('python-project', {'archiveSha256': 'a' * 64})
    value = copy.deepcopy(expected)
    value['pythonBuild'] = {'available': True, 'python': '/opt/unified-prepared/build-venv/bin/python',
                            'backend': 'hatchling.build', 'command': expected['pythonBuild']['command']}
    if change == 'missing':
        value = None
    elif change == 'python':
        value['python'] = '/workspace/.venv/bin/python'
    elif change == 'source':
        value['source']['archiveSha256'] = 'b' * 64
    elif change == 'command':
        value['pythonBuild']['command'] = 'other-command'
    else:
        value['pythonBuild']['available'] = 'yes'
    with pytest.raises(ValueError):
        preparation.validate_execution_contract(value, expected)


@pytest.mark.parametrize('error', [ValueError('unsupported backend'), subprocess.CalledProcessError(1, ['backend-probe'])])
def test_failed_packaging_prerequisite_does_not_block_source_checks(tmp_path, monkeypatch, error):
    monkeypatch.setattr(guest, 'prepare_build', lambda *a: (_ for _ in ()).throw(error))
    value = guest.optional_build('uv', 'python', tmp_path, tmp_path)
    assert value['available'] is False
    assert 'Source checks can run' in value['reason']
    assert json.loads((tmp_path / 'build-preparation.json').read_text())['status'] == 'unavailable'
    expected = profiles.execution_contract('python-project')
    assert preparation.validate_execution_contract({**expected, 'pythonBuild': value}, expected)
    observed = []
    monkeypatch.setattr(guest, 'run', lambda *args, **kwargs: observed.append(args))
    guest.verify_contract({**expected, 'pythonBuild': value})
    assert observed == [(expected['python'], '-c', 'import sys; assert sys.version_info >= (3, 13)'),
                        ('ps', '-axo', 'command=')]


@pytest.mark.parametrize('outcome', ['missing', 'nonzero', 'success'])
def test_contract_requires_successful_process_listing(tmp_path, monkeypatch, capfd, outcome):
    # Real subprocess execution with a synthetic executable, not proof that the
    # managed image has procps installed. No host process list is inspected.
    monkeypatch.setenv('PATH', str(tmp_path))
    if outcome != 'missing':
        executable = tmp_path / 'ps'
        executable.write_text(
            f'#!{sys.executable}\nimport sys\n'
            'assert sys.argv[1:] == ["-axo", "command="]\n'
            'print("synthetic-private-process-argument")\n'
            f'raise SystemExit({1 if outcome == "nonzero" else 0})\n')
        executable.chmod(0o700)
    contract = {**profiles.execution_contract('python-project'), 'python': sys.executable,
                'pythonBuild': {'available': False}}
    if outcome == 'success':
        guest.verify_contract(contract)
    else:
        expected = FileNotFoundError if outcome == 'missing' else subprocess.CalledProcessError
        with pytest.raises(expected):
            guest.verify_contract(contract)
    captured = capfd.readouterr()
    assert 'synthetic-private-process-argument' not in captured.out + captured.err


@pytest.mark.parametrize('mode', ['cold', 'finalize'])
@pytest.mark.parametrize('failure', ['missing', 'nonzero'])
def test_failed_process_preflight_never_publishes_usable_contract(tmp_path, monkeypatch, mode, failure):
    prefix = tmp_path / 'prepared'
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps({
        'profileId': 'python-project', 'preparationId': 'fixture',
        'sourceSha256': 'a' * 64, 'lockfiles': [], 'pythonProject': False,
        'dependencyMode': mode, 'executionContract': profiles.execution_contract('python-project'),
    }))
    monkeypatch.setattr(guest, 'PREFIX', prefix)
    monkeypatch.setattr(guest, 'Path', lambda value: {
        '/prepare/plan.json': plan_path, '/prepare/build-home': tmp_path / 'build-home',
        '/prepare/tmp': tmp_path / 'build-tmp',
    }.get(str(value), Path(value)))
    monkeypatch.setattr(guest.os, 'environ', dict(os.environ))
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        # Warm guests may still be clearing /tmp while the first uv interpreter
        # probe starts. Its HOME/cache and temporary module must survive that.
        assert guest.os.environ['HOME'] == '/prepare/build-home'
        assert guest.os.environ['TMPDIR'] == '/prepare/tmp'
        assert (tmp_path / 'build-home').is_dir() and (tmp_path / 'build-tmp').is_dir()
        if args == ('ps', '-axo', 'command='):
            assert kwargs == {'capture': True}
            if failure == 'missing':
                raise FileNotFoundError('ps')
            raise subprocess.CalledProcessError(1, args)
        return 'fixture' if kwargs.get('capture') else None

    monkeypatch.setattr(guest, 'run', run)
    with pytest.raises(FileNotFoundError if failure == 'missing' else subprocess.CalledProcessError):
        guest.main()
    assert calls[-1] == ('ps', '-axo', 'command=')
    if mode == 'cold':
        assert any(args[:2] == ('apt-get', 'install') and 'procps' in args for args in calls)
    else:
        assert not any(args[0] == 'apt-get' for args in calls)
    assert not (prefix / 'execution-contract.json').exists()
    assert not (prefix / 'inventory.json').exists()
    assert not (prefix / 'plan.json').exists()


@pytest.mark.parametrize('error', [TimeoutError('deadline'), subprocess.TimeoutExpired('probe', 1), KeyboardInterrupt()])
def test_optional_build_never_swallows_deadline_or_cancellation(tmp_path, monkeypatch, error):
    monkeypatch.setattr(guest, 'prepare_build', lambda *a: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)):
        guest.optional_build('uv', 'python', tmp_path, tmp_path)
    assert not (tmp_path / 'build-preparation.json').exists()


@pytest.mark.skipif(os.name != 'posix', reason='Negative subprocess signal status is POSIX-specific')
@pytest.mark.parametrize('signal_name', ['SIGTERM', 'SIGKILL'])
def test_optional_build_propagates_real_terminated_helper(tmp_path, signal_name):
    child_signal = getattr(signal, signal_name)
    project, prefix = tmp_path / 'project', tmp_path / 'prepared'
    project.mkdir(); prefix.mkdir()
    (project / 'pyproject.toml').write_text(
        '[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"')
    uv = tmp_path / 'uv'
    uv.write_text(f'#!{sys.executable}\nimport os, signal\nos.kill(os.getpid(), {int(child_signal)})\n')
    uv.chmod(0o700)

    # Exercise the real prepare_build -> run -> subprocess path. Only the
    # disposable child terminates; the test runner and other work remain alive.
    with pytest.raises(subprocess.CalledProcessError) as failure:
        guest.optional_build(str(uv), sys.executable, project, prefix)

    assert failure.value.returncode == -child_signal
    assert failure.value.cmd[0] == str(uv)
    assert not (prefix / 'build-preparation.json').exists()
