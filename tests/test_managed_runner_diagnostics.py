"""Exact retained-owner diagnosis is not runner acceptance or guest recovery."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest

from amplifier_web import managed_runner_diagnostics as diagnostic
from amplifier_web import managed_runner_updates as updates
from amplifier_web import task_environments as environments
from amplifier_web import test_environment_configs as configs
from amplifier_web import test_environment_setup as private
from amplifier_web.service import ACTION_DEFINITIONS, AppError
from test_configuration_authority import app
from test_coordination_generation import agent
from test_task_environments import backend, scope, start, act

ACTION = diagnostic.ACTION


@pytest.fixture
async def owned(app, backend, monkeypatch):
    prefix = app.data_dir / 'smart-tools/installs' / ('a' * 24) / 'venv'
    # Synthetic supported layout: private installation, no shared write grants.
    old_mask = os.umask(0o077)
    try:
        host_base = app.data_dir / 'trusted-base'
        host_base.mkdir()
        host_python = host_base / 'python'
        shutil.copyfile(sys._base_executable, host_python)
        host_python.chmod(0o700)
        # Standard-library custody is the same host's, not target packages.
        import sysconfig
        (host_base / 'lib').mkdir()
        (host_base / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}').symlink_to(
            sysconfig.get_path('stdlib'), target_is_directory=True)
        venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
        (prefix / 'bin/python').unlink()
        (prefix / 'bin/python').symlink_to(host_python)
        base = prefix / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
        package = base / 'digital_twin_universe'
        package.mkdir()
        (package / '__init__.py').write_text('VALUE = 1\n')
        metadata = base / 'digital_twin_universe-0.1.dist-info'
        metadata.mkdir()
        (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: digital-twin-universe\nVersion: 0.1\n')
    finally:
        os.umask(old_mask)
    monkeypatch.setattr(diagnostic.sys, '_base_executable', str(host_python))
    config = {'enabled': True, 'installationId': 'a' * 24, 'commit': 'b' * 40,
              'runnerQualificationRequired': True, 'python': str(prefix / 'bin/python'),
              'dockerHost': 'unix:///synthetic-private/docker.sock', 'image': environments.APPROVED_IMAGE}
    monkeypatch.setattr(environments, 'configuration', lambda app: (config, 'ready', 'Fixture'))
    row = (await start(app))['resource']
    value = {'version': 1, 'normalized': copy.deepcopy(config), 'runtime': updates.runtime_identity(config),
             'proof': {'probeRemoved': True, 'checkPassed': True, 'artifactVerified': True}}
    path = updates.qualification_path(app.data_dir, config)
    path.parent.mkdir(mode=0o700)
    private.write_json(path, value)
    return row, config, value, path, base


def args(app, row):
    return {**scope(app), 'id': row['id'], 'expectedRevision': row['revision']}


async def read(app, row):
    return (await app.dispatch(ACTION, args(app, row), include_state=False))['result']


def stamps(home):
    return {str(p.relative_to(home)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
            for p in home.rglob('*') if p.is_file() and not p.is_symlink()}


def forbid_effects(app, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail('Diagnostic invoked configuration, candidate code, backend or persistence')
    for name in ('invoke', 'save', 'configuration', 'resolve_configuration'):
        monkeypatch.setattr(environments, name, forbidden)
    monkeypatch.setattr(updates, 'runtime_identity', forbidden)
    monkeypatch.setattr(private, 'write_json', forbidden)
    monkeypatch.setattr(app.operations.requests, 'begin', forbidden)
    monkeypatch.setattr(app.operations.requests, 'finish', forbidden)
    monkeypatch.setattr(app, '_save', forbidden)
    monkeypatch.setattr(app, '_session', forbidden)
    monkeypatch.setattr(app.history, 'refresh', forbidden)


async def test_exact_owned_proof_compatible_inventory_and_no_writes(app, owned, monkeypatch):
    row, config, proof, path, base = owned
    expected = proof['runtime']
    before = stamps(app.data_dir)
    with monkeypatch.context() as guard:
        forbid_effects(app, guard)
        value = await read(app, row)
    assert value['code'] == 'runtime_identity_matches'
    assert value['runtime'] == {'collection': 'collected', 'recorded': expected, 'current': expected}
    assert value['qualification'] == {'status': 'found', 'behavioralProof': 'recorded'}
    assert value['binding']['expectedSha256'] == value['binding']['proofSha256']
    assert value['readOnly'] and value['effectsAuthorized'] is False and value['physical'] == 'not_observed'
    assert stamps(app.data_dir) == before
    assert str(app.data_dir) not in json.dumps(value) and 'synthetic-private' not in json.dumps(value)
    assert len(json.dumps(value)) < 4096


async def test_unknown_receipts_and_original_retained_route_survive_selection_change(app, owned, monkeypatch):
    row, config, proof, path, base = owned
    def lost(*a, **kw):
        raise updates.RunnerUpdateError('managed-runner-package-changed')
    with monkeypatch.context() as fault:
        fault.setattr(environments, 'invoke', lost)
        failed = await act(app, 'execute', row, 'original-unknown', command='fixture-check')
    row, original = failed['resource'], failed['request']
    assert original['state'] == 'outcome_unknown'
    private.write_json(app.data_dir / 'test-environments.json', {'installationId': 'f' * 24})
    before = stamps(app.data_dir)
    with monkeypatch.context() as guard:
        forbid_effects(app, guard)
        value = await read(app, row)
    assert value['code'] == 'runtime_identity_matches'
    assert stamps(app.data_dir) == before
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('change,code', [
    ('absent', 'qualification_missing'), ('corrupt', 'qualification_rejected'),
    ('mode', 'qualification_rejected'), ('symlink', 'qualification_rejected'),
    ('hardlink', 'qualification_rejected'), ('directory-mode', 'qualification_rejected'),
    ('directory-link', 'qualification_rejected'), ('owner', 'qualification_rejected'),
    ('format', 'qualification_corrupt'), ('runtime-extra', 'qualification_corrupt'),
])
async def test_missing_is_distinct_from_rejected_private_proof(app, owned, monkeypatch, change, code):
    row, config, proof, path, base = owned
    if change == 'absent':
        path.unlink()
    elif change == 'corrupt':
        path.write_text('{SECRET_RAW_EXCEPTION')
    elif change == 'mode':
        path.chmod(0o644)
    elif change == 'symlink':
        other = path.with_suffix('.other')
        path.rename(other)
        path.symlink_to(other)
    elif change == 'hardlink':
        os.link(path, path.with_suffix('.other'))
    elif change == 'directory-mode':
        path.parent.chmod(0o755)
    elif change == 'directory-link':
        other = path.parent.with_name('redirected-proofs')
        path.parent.rename(other)
        path.parent.symlink_to(other, target_is_directory=True)
    elif change == 'owner':
        check = diagnostic.files.private_info
        def changed_owner(info, limit):
            if limit == configs.MAX_BYTES:
                raise ValueError('SECRET_PRIVATE_OWNER_CHANGED')
            return check(info, limit)
        monkeypatch.setattr(diagnostic.files, 'private_info', changed_owner)
    elif change == 'format':
        private.write_json(path, [])
    else:
        proof['runtime']['SECRET_UNKNOWN_FIELD'] = 'SECRET_VALUE'
        private.write_json(path, proof)
    value = await read(app, row)
    assert value['status'] == 'blocked' and value['code'] == code
    assert 'SECRET' not in json.dumps(value)
    assert 'current' not in value['runtime']


@pytest.mark.parametrize('field', ['dockerHost', 'backend', 'admissionRoot', 'retentionMode'])
async def test_binding_mismatch_publishes_only_closed_names_and_digests(app, owned, field):
    row, config, proof, path, base = owned
    proof['normalized'][field] = 'SECRET_AUTH_VALUE' if field != 'admissionRoot' else None
    private.write_json(path, proof)
    value = await read(app, row)
    assert value['code'] == 'execution_binding_mismatch'
    assert value['binding']['differingFields'] == [field]
    assert value['binding']['expectedSha256'] != value['binding']['proofSha256']
    assert 'SECRET' not in json.dumps(value)
    assert value['runtime']['collection'] == 'not_attempted'


@pytest.mark.parametrize('field,new', [('files', 99), ('packageVersion', '0.2'), ('pythonVersion', '3.13.99'), ('runtimeSha256', 'f' * 64)])
async def test_complete_runtime_object_not_just_hash_is_compared(app, owned, field, new):
    row, config, proof, path, base = owned
    proof['runtime'][field] = new
    private.write_json(path, proof)
    value = await read(app, row)
    assert value['code'] == 'runtime_identity_changed'
    assert value['runtime']['recorded'][field] != value['runtime']['current'][field]


async def test_changed_package_bytes_and_bytecode_are_inventory_only(app, owned):
    row, config, proof, path, base = owned
    (base / 'digital_twin_universe/__init__.py').write_text("raise RuntimeError('SECRET_CHANGED_CODE_EXECUTED')\n")
    (base / 'digital_twin_universe/stale.pyc').write_bytes(b'changed executable bytecode')
    (base / 'digital_twin_universe/stale.pyc').chmod(0o600)
    value = await read(app, row)
    assert value['code'] == 'runtime_identity_changed'
    assert 'SECRET' not in json.dumps(value)


@pytest.mark.parametrize('trap', ['.pth', 'sitecustomize.py', 'python._pth', 'intermediate-mode',
                                'executable', 'dependency-link', 'dependency-hardlink', 'prefix-link'])
async def test_unsafe_runtime_is_not_executed_or_reported_current(app, owned, trap, tmp_path):
    row, config, proof, path, base = owned
    marker = tmp_path / 'must-not-run'
    payload = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\n"
    if trap == '.pth':
        (base / 'unsafe.pth').write_text('import pathlib; ' + payload.removeprefix('from pathlib import Path; '))
    elif trap == 'sitecustomize.py':
        (base / trap).write_text(payload)
    elif trap == 'python._pth':
        (Path(config['python']).parent / trap).write_text('/synthetic/outside\nimport site\n')
    elif trap == 'intermediate-mode':
        (Path(config['python']).parent.parent / 'lib').chmod(0o777)
    elif trap == 'executable':
        python = Path(config['python'])
        python.unlink()
        python.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
        python.chmod(0o700)
    elif trap == 'dependency-link':
        (base / 'redirect.py').symlink_to(base / 'digital_twin_universe/__init__.py')
    elif trap == 'dependency-hardlink':
        os.link(base / 'digital_twin_universe/__init__.py', base / 'shared.py')
    else:
        prefix = Path(config['python']).parent.parent
        other = prefix.with_name('other-venv')
        prefix.rename(other)
        prefix.symlink_to(other, target_is_directory=True)
    value = await read(app, row)
    assert value['code'] in {'runtime_inventory_custody_unavailable', 'runtime_inventory_rejected',
                             'runtime_inventory_startup_unsupported'}
    assert value['runtime']['collection'] == 'unavailable' and 'current' not in value['runtime']
    assert not marker.exists()


async def test_late_dependency_insertion_invalidates_inventory(app, owned, monkeypatch):
    from amplifier_web import managed_runner_identity as collector
    row, config, proof, path, base = owned
    stat = collector.os.stat
    injected = []
    calls = []
    def late(pathname, *a, **kw):
        # During final file revalidation, after the walk and executable read.
        if pathname == 'METADATA' and kw.get('dir_fd') is not None:
            calls.append(True)
            if len(calls) == 2:
                injected.append(True)
                (base / 'late-startup.pth').write_text('import SECRET_NEVER_EXECUTED\n')
        return stat(pathname, *a, **kw)
    monkeypatch.setattr(collector.sys, 'executable', sys._base_executable)
    monkeypatch.setattr(collector.os, 'stat', late)
    with pytest.raises(collector.InventoryUnavailable, match='unavailable') as caught:
        collector.retained_identity(Path(config['python']).parent.parent)
    assert injected and caught.value.code == 'runtime_inventory_changed'


async def test_proof_changed_during_inventory_does_not_publish_current(app, owned, monkeypatch):
    row, config, proof, path, base = owned
    current = copy.deepcopy(proof['runtime'])
    def changed(prefix):
        private.write_json(path, {**proof, 'changed': True})
        return current
    monkeypatch.setattr(diagnostic, 'probe', changed)
    value = await read(app, row)
    assert value['code'] == 'record_changed' and value['status'] == 'blocked'
    assert 'current' not in value['runtime'] and value['runtime']['collection'] == 'unavailable'


async def test_no_other_proof_from_same_revision_is_borrowed(app, owned):
    row, config, proof, path, base = owned
    path.rename(path.with_name('qualified-unrelated-same-commit.json'))
    assert (await read(app, row))['code'] == 'qualification_missing'


@pytest.mark.parametrize('mode', ['malformed', 'oversize', 'timeout'])
async def test_probe_bounds_and_errors_never_leak_native_output(app, owned, monkeypatch, mode):
    row, config, proof, path, base = owned
    popen = subprocess.Popen
    observed = []
    def fake(command, **kw):
        observed.append((command, kw))
        code = {'malformed': "print('SECRET_NATIVE_OUTPUT')", 'oversize': "print('X'*5000)", 'timeout': 'import time; time.sleep(60)'}[mode]
        return popen([sys._base_executable, '-I', '-S', '-B', '-c', code], **kw)
    monkeypatch.setattr(diagnostic.subprocess, 'Popen', fake)
    if mode == 'timeout':
        clock = iter(range(0, 1000, 10))
        monkeypatch.setattr(diagnostic.time, 'monotonic', lambda: next(clock))
    value = await read(app, row)
    assert value['code'] == 'runtime_probe_' + mode
    assert 'SECRET' not in json.dumps(value)
    command, kw = observed[0]
    assert command[1:4] == ['-I', '-S', '-B'] and command[0] != config['python']
    assert set(kw['env']) == {'PATH', 'LANG'}


async def test_scope_revision_and_schema_refuse_cross_owner_and_aliases(app, owned):
    row, *_ = owned
    source, peer = app.state['sessions']
    with pytest.raises(AppError):
        await app.dispatch(ACTION, {**args(app, row), 'sessionId': peer['id']}, include_state=False)
    with pytest.raises(AppError):
        await agent(app, source, ACTION, {**args(app, row), 'sessionId': peer['id']}, 'cross-root')
    for extra in ({'expectedRevision': row['revision'] + 1}, {'nativeProject': 'SECRET_PATH'}, {'requestId': 'no-admission'}):
        with pytest.raises(AppError):
            await app.dispatch(ACTION, {**args(app, row), **extra}, include_state=False)
    from amplifier_web.workspace_work_effects import SAFE_ACTIONS, recovery_read
    assert ACTION in ACTION_DEFINITIONS and ACTION in SAFE_ACTIONS
    assert recovery_read('app_control', {'operation': 'dispatch', 'args': {'action': ACTION}})
    assert set(ACTION_DEFINITIONS[ACTION][1]['properties']) == {'workspaceId', 'sessionId', 'id', 'expectedRevision'}


async def test_authenticated_agent_can_diagnose_during_update_hold_without_hydration(app, owned, monkeypatch):
    row, *_ = owned
    source = app.state['sessions'][0]
    app.state['updates'] = {**app.state.get('updates', {}), 'phase': 'activating'}
    before = stamps(app.data_dir)
    with monkeypatch.context() as guard:
        forbid_effects(app, guard)
        value = await agent(app, source, ACTION, args(app, row), 'passive-runner')
    assert value['result']['code'] == 'runtime_identity_matches'
    assert stamps(app.data_dir) == before


@pytest.mark.parametrize('target', ['ledger', 'configuration', 'configuration-parent', 'lock'])
async def test_resource_storage_aliases_are_rejected_without_effects(app, owned, target):
    row, config, *_ = owned
    root = app.data_dir / 'workspace-resources'
    path = {'ledger': root / 'reservations.sqlite3',
            'configuration': root / 'backend-configs' / (configs.key(config) + '.json'),
            'configuration-parent': root / 'backend-configs',
            'lock': root / ('environment-' + row['id'] + '.lock')}[target]
    other = path.with_name(path.name + '-original')
    path.rename(other)
    path.symlink_to(other, target_is_directory=other.is_dir())
    before = stamps(app.data_dir)
    if target in {'ledger', 'lock'}:
        with pytest.raises(AppError) as caught:
            await read(app, row)
        assert str(app.data_dir) not in str(caught.value)
    else:
        value = await read(app, row)
        assert value['code'] == 'retained_binding_rejected'
    assert stamps(app.data_dir) == before


async def test_capacity_only_fields_do_not_change_execution_binding(app, owned):
    row, config, proof, path, base = owned
    proof['normalized'].update(maxRunning=9, maxInstances=10, minFreeBytes=100)
    private.write_json(path, proof)
    value = await read(app, row)
    assert value['code'] == 'runtime_identity_matches' and value['binding']['differingFields'] == []
    assert diagnostic.differences({'incus': {'configDir': 'SECRET'}}, {'incus': {'configDir': None}}) == ['incus.configDir']


async def test_owner_snapshot_is_rechecked_after_inventory(app, owned, monkeypatch):
    row, *_ = owned
    real = diagnostic.inspect
    def moved(home, resource):
        result = real(home, resource)
        source = app.state['sessions'][0]
        source['executionRevision'] = source.get('executionRevision', 0) + 1
        return result
    monkeypatch.setattr(diagnostic, 'inspect', moved)
    # Revision drift in the root must not return stale facts about its old binding.
    with pytest.raises(AppError):
        await read(app, row)
