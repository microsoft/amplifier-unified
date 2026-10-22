"""Runner policy, exact selection, and lifecycle failure boundaries."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import managed_runner_updates as updates
from amplifier_web import managed_runner_qualification as probe
from amplifier_web import task_environments as environments
from amplifier_web import test_environment_setup as private

OLD = environments.DTU_COMMIT
NEW = 'b' * 40
RUNTIME = {'version': 1, 'runtimeSha256': 'a' * 64, 'files': 3,
           'packageVersion': '0.6.5', 'pythonVersion': '3.13.0'}


@pytest.fixture
def app(tmp_path, monkeypatch):
    value = SimpleNamespace(data_dir=tmp_path, state={'settings': {}})
    value.smart_tools = SimpleNamespace(state={'installations': [], 'servers': []})
    def install(identity, commit):
        python = tmp_path / 'smart-tools/installs' / identity / 'venv/bin/python'
        python.parent.mkdir(parents=True, mode=0o700)
        python.write_text('fixture')
        item = {'id': identity, 'commit': commit, 'repository': environments.REPOSITORY,
                'extras': [], 'status': 'installed', 'python': str(python)}
        value.smart_tools.state['installations'].append(item)
        return item
    old = install('a' * 24, OLD)
    new = install('b' * 24, NEW)
    new['runnerCandidate'] = True
    value.smart_tools.install = AsyncMock(return_value=new)
    raw = {'enabled': True, 'installationId': old['id'], 'commit': OLD,
           'dockerHost': 'unix:///owned/docker.sock'}
    private.write_json(tmp_path / 'test-environments.json', raw)
    receipt = {'id': 'c' * 32, 'state': 'completed', 'priorConfiguration': None,
               'installationId': old['id'], 'candidateConfiguration': raw,
               'proof': {'probeRemoved': True}}
    private.write_json(private.root(value) / (receipt['id'] + '.json'), receipt)
    monkeypatch.setattr(updates, 'runtime_identity', lambda item: copy.deepcopy(RUNTIME))
    return value


def selected(app):
    return updates.raw_configuration(app)


def source(app):
    return {**updates.update_sources(app)[0], 'latest': NEW, 'status': 'update'}


def test_only_actual_new_setup_receipt_migrates(app):
    assert source(app)['eligible']
    path = private.root(app) / ('c' * 32 + '.json')
    receipt = private.read_json(path)
    receipt.pop('priorConfiguration')
    private.write_json(path, receipt)
    row = updates.update_sources(app)[0]
    assert not row['eligible'] and row['status'] == 'local'
    assert 'not been checked' in row['detail']


def test_explicit_setup_pin_remains_pinned(app):
    path = private.root(app) / ('c' * 32 + '.json')
    receipt = private.read_json(path)
    receipt['runnerSourcePolicy'] = {'version': 1, 'repository': environments.REPOSITORY,
                                     'ref': OLD, 'origin': 'explicit'}
    private.write_json(path, receipt)
    assert updates.update_sources(app)[0]['status'] == 'pinned' and not source(app)['eligible']


async def test_staging_never_selects_or_retargets_connections(app):
    before = selected(app)
    result = await updates.stage(app, source(app))
    assert selected(app) == before
    args = app.smart_tools.install.call_args.args[0]
    assert args['_runner_qualification'] and args['ref'] == NEW and args['extras'] == []
    record = private.read_json(updates.record_path(app, result['runnerUpdate']))
    assert record['state'] == 'qualifying' and record['runtime'] == RUNTIME
    with pytest.raises(ValueError, match='not passed'):
        environments.validate_configuration_value(app, record['candidate'])


async def test_stage_reuses_exact_attempt_and_does_not_reinstall(app):
    first = await updates.stage(app, source(app))
    assert await updates.stage(app, source(app)) == first
    assert app.smart_tools.install.await_count == 1


async def test_stale_source_policy_cannot_stage(app):
    request = source(app)
    raw = selected(app)
    raw['minFreeBytes'] = 123
    private.write_json(app.data_dir / 'test-environments.json', raw)
    with pytest.raises(updates.RunnerUpdateError):
        await updates.stage(app, request)
    app.smart_tools.install.assert_not_called()


async def test_failed_behavior_never_selects(app, monkeypatch):
    before = selected(app)
    result = await updates.stage(app, source(app))
    monkeypatch.setattr(probe, 'advance', lambda *args: 'failed')
    with pytest.raises(updates.RunnerUpdateError):
        await updates.advance(app, result['runnerUpdate'])
    assert selected(app) == before


async def test_unknown_result_stays_owned_and_does_not_select(app, monkeypatch):
    before = selected(app)
    result = await updates.stage(app, source(app))
    monkeypatch.setattr(probe, 'advance', lambda *args: 'unknown')
    assert await updates.advance(app, result['runnerUpdate']) == {
        'complete': False, 'waiting': 'unknown', 'progressed': False,
        'detail': 'The test runner check has an unconfirmed result. Its exact owner and evidence are retained; no check is repeated.'}
    assert selected(app) == before


def qualified(*args):
    _, path, record, _ = args
    probe.save(path, record, probeState='qualified', proof={
        'version': 1, 'probeRemoved': True, 'artifactVerified': True, 'checkPassed': True,
        'backendIdentity': {'backend': 'docker', 'daemon': 'owned'}})
    return 'qualified'


async def test_qualified_selection_cold_reopen_old_owner_and_rollback(app, monkeypatch):
    old = selected(app)
    old_normalized, _, _ = environments.validate_configuration_value(app, old)
    result = await updates.stage(app, source(app))
    monkeypatch.setattr(probe, 'advance', qualified)
    assert (await updates.advance(app, result['runnerUpdate']))['complete']
    new = selected(app)
    assert new['commit'] == NEW
    cold = SimpleNamespace(data_dir=app.data_dir, smart_tools=app.smart_tools)
    assert environments.configuration(cold)[1] == 'ready'
    assert environments.validate_configuration_value(cold, old)[0] == old_normalized
    normalized = environments.configuration(cold)[0]
    retained = {key: value for key, value in normalized.items() if key not in {'python', 'admissionRoot'}}
    assert environments.validate_configuration_value(cold, retained)[0] == normalized
    assert updates.rollback(cold, new['installationId'])
    assert selected(cold) == old
    assert updates.referenced(app.data_dir, old['installationId'])
    assert not updates.referenced(app.data_dir, new['installationId'])


async def test_selection_loss_is_observed_without_probe_replay(app, monkeypatch):
    result = await updates.stage(app, source(app))
    monkeypatch.setattr(probe, 'advance', qualified)
    await updates.advance(app, result['runnerUpdate'])
    monkeypatch.setattr(probe, 'advance', lambda *args: pytest.fail('behavior repeated'))
    assert (await updates.advance(app, result['runnerUpdate']))['complete']


async def test_concurrent_selected_configuration_wins(app, monkeypatch):
    result = await updates.stage(app, source(app))
    def change(*args):
        raw = selected(app)
        raw['minFreeBytes'] = 123
        private.write_json(app.data_dir / 'test-environments.json', raw)
        return qualified(*args)
    monkeypatch.setattr(probe, 'advance', change)
    with pytest.raises(updates.RunnerUpdateError):
        await updates.advance(app, result['runnerUpdate'])
    assert selected(app)['commit'] == OLD and selected(app)['minFreeBytes'] == 123


async def test_package_change_blocks_selection_and_execution_not_passive_display(app, monkeypatch):
    result = await updates.stage(app, source(app))
    monkeypatch.setattr(probe, 'advance', qualified)
    await updates.advance(app, result['runnerUpdate'])
    monkeypatch.setattr(updates, 'runtime_identity', lambda *args: pytest.fail('passive byte hashing'))
    normalized, status, _ = environments.configuration(app)
    assert status == 'ready'
    monkeypatch.setattr(updates, 'runtime_identity', lambda *args: {**RUNTIME, 'runtimeSha256': 'b' * 64})
    with pytest.raises(updates.RunnerUpdateError):
        updates.validate_execution(app.data_dir, normalized)


@pytest.fixture
def lifecycle(app, monkeypatch):
    calls = []
    backend = {'backend': 'docker', 'daemon': 'owned'}
    def invoke(config, app, row, action, args):
        calls.append(action)
        value = {'ok': True, 'universeId': 'owned-guest', 'snapshot': {'archive_sha256': 'a' * 64},
                 'backendIdentity': backend, 'hostTarget': {'supported': True, 'machineId': 'same'},
                 'status': {'start': 'running', 'read': 'running', 'stop': 'stopped', 'remove': 'removed',
                            'finishCleanup': 'removed', 'reconcile': 'absent'}.get(action, 'running'),
                 'sourceReady': True}
        if action in {'job_start', 'job_read'}:
            value.update(job={'id': args['jobId'], 'terminal': True, 'state': 'passed'},
                         result={'exit_code': 0, 'stdout': probe.SENTINEL})
        if action == 'reconcile':
            value['observation'] = {'rootOwner': row['sessionId'], 'operationId': row['launchOperationId'],
                'universeId': 'owned-guest', 'backendIdentity': backend, 'ownership': 'absent',
                'absenceConfirmed': True, 'admissionSettled': True}
        return value
    monkeypatch.setattr(probe, 'invoke', invoke)
    monkeypatch.setattr(environments, 'capacity', lambda *args, **kwargs: {'available': True})
    monkeypatch.setattr(probe.resources, 'reserve', lambda *args: {'supported': True, 'admitted': True})
    monkeypatch.setattr(probe.resources, 'settle', lambda *args, **kwargs: None)
    monkeypatch.setattr(probe, 'verify_artifact', lambda result: True)
    monkeypatch.setattr(probe.isolation, 'inspect', lambda *args: {'offline': True})
    return calls, invoke


async def run_probe(app, count=10):
    staged = await updates.stage(app, source(app))
    for _ in range(count):
        value = await updates.advance(app, staged['runnerUpdate'])
        if value.get('complete'):
            break
    return value, staged


async def test_actual_owner_state_machine_checks_and_cleans_once(app, lifecycle):
    value, staged = await run_probe(app)
    assert value['complete']
    calls, _ = lifecycle
    assert calls == ['prepare', 'start', 'job_start', 'stop', 'remove', 'reconcile']
    record = private.read_json(updates.record_path(app, staged['runnerUpdate']))
    row = environments.get(app.data_dir, 'runner-update-' + record['id'], record['resourceId'])
    assert row['status'] == 'retired' and record['proof']['artifactVerified']
    assert row.get('directoryIdentity') and environments.storage.directory(app.data_dir, row).is_dir()
    assert (await updates.advance(app, staged['runnerUpdate']))['complete']
    assert calls == ['prepare', 'start', 'job_start', 'stop', 'remove', 'reconcile']


async def test_backend_capacity_refusal_waits_without_guest(app, lifecycle, monkeypatch):
    monkeypatch.setattr(environments, 'capacity', lambda *args, **kwargs: {'available': False})
    value, _ = await run_probe(app, 4)
    assert not value['complete'] and value['waiting'] == 'waiting'
    assert lifecycle[0] == ['prepare']
    assert selected(app)['commit'] == OLD


async def test_lost_job_ack_reads_exact_job_and_never_executes_twice(app, lifecycle, monkeypatch):
    calls, original = lifecycle
    def lose(config, app, row, action, args):
        value = original(config, app, row, action, args)
        if action == 'job_start':
            raise TimeoutError()
        return value
    monkeypatch.setattr(probe, 'invoke', lose)
    value, _ = await run_probe(app)
    assert value['complete'] and calls.count('job_start') == 1 and calls.count('job_read') == 1


async def test_unknown_start_never_replays_or_retires(app, lifecycle, monkeypatch):
    calls, original = lifecycle
    def lose(config, app, row, action, args):
        value = original(config, app, row, action, args)
        if action == 'start':
            raise TimeoutError()
        if action == 'reconcile':
            return {'ok': False, 'status': 'unknown'}
        return value
    monkeypatch.setattr(probe, 'invoke', lose)
    value, _ = await run_probe(app, 8)
    assert value['waiting'] == 'unknown'
    assert calls.count('start') == 1 and 'remove' not in calls
    assert selected(app)['commit'] == OLD


def test_artifact_requires_exact_bounded_retained_bytes(tmp_path):
    destination = tmp_path / 'proof.tar'
    with tarfile.open(destination, 'w') as archive:
        item = tarfile.TarInfo('artifacts/runner-proof.txt')
        body = probe.SENTINEL.encode()
        item.size = len(body)
        archive.addfile(item, io.BytesIO(body))
    receipt = {'archives': [{'destination': str(destination),
                            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}]}
    assert probe.verify_artifact(receipt)
    receipt['archives'][0]['sha256'] = 'a' * 64
    assert not probe.verify_artifact(receipt)


async def test_config_write_lost_ack_settles_from_exact_receipt(app, monkeypatch):
    result = await updates.stage(app, source(app))
    monkeypatch.setattr(probe, 'advance', qualified)
    real = private.write_json
    path = updates.record_path(app, result['runnerUpdate'])
    def lose(target, value):
        if target == path and value.get('state') == 'selected':
            raise OSError('lost final acknowledgement')
        return real(target, value)
    monkeypatch.setattr(private, 'write_json', lose)
    with pytest.raises(OSError):
        await updates.advance(app, result['runnerUpdate'])
    assert selected(app)['commit'] == NEW
    monkeypatch.setattr(private, 'write_json', real)
    monkeypatch.setattr(probe, 'advance', lambda *args: pytest.fail('probe repeated'))
    assert (await updates.advance(app, result['runnerUpdate']))['complete']


async def test_runner_limit_overrides_survive_new_selection(app, monkeypatch):
    from amplifier_web import test_capacity_settings as limits
    before = environments.configuration(app)[0]
    identity = limits.execution_key(before)
    limits.write(app, {'version': 1, 'defaults': {}, 'runners': {identity: {'maxRunning': 31}}})
    result = await updates.stage(app, source(app))
    monkeypatch.setattr(probe, 'advance', qualified)
    await updates.advance(app, result['runnerUpdate'])
    after = environments.configuration(app)[0]
    assert limits.execution_key(after) == identity
    assert limits.effective(after, limits.load(app))[0]['maxRunning'] == 31
    # Admission applies mutable budgets without changing source authority.
    updates.validate_execution(app.data_dir, limits.apply(app, after))


@pytest.mark.parametrize('backend', ['docker', 'incus'])
def test_qualification_ignores_count_quotas_without_mutating_saved_policy(app, monkeypatch, backend):
    from amplifier_web import test_capacity_settings as limits
    original = {'backend': backend, 'installationId': 'fixed', 'commit': 'b' * 40}
    values = {'maxRunning': 2, 'maxInstances': 4, 'minFreeBytes': 4 * 1024**3}
    original.update({'incus': dict(values)} if backend == 'incus' else values)
    identity = limits.execution_key(original)
    policy = {'version': 1, 'defaults': {'maxRunning': 1},
              'runners': {identity: {'maxInstances': 1, 'minFreeBytes': 8 * 1024**3}}}
    limits.write(app, policy)
    before = copy.deepcopy(original)
    calls = []
    def invoke(config, home, row, action, args, **kwargs):
        calls.append((copy.deepcopy(config), action, kwargs))
        return {'ok': True}
    monkeypatch.setattr(environments, 'invoke', invoke)
    probe.invoke(original, app, {}, 'start', {})
    actual = calls[0][0]['incus'] if backend == 'incus' else calls[0][0]
    assert actual['maxRunning'] == actual['maxInstances'] == 0
    assert actual['minFreeBytes'] == 8 * 1024**3
    assert calls[0][2] == {'_qualification': True}
    assert limits.load(app) == policy and original == before
    ordinary = limits.apply(app, original)
    ordinary = ordinary['incus'] if backend == 'incus' else ordinary
    assert ordinary['maxRunning'] == ordinary['maxInstances'] == 1


async def test_update_capacity_uses_private_lane_but_keeps_disk_pressure(app, lifecycle, monkeypatch):
    observations = []
    def capacity(app, config, *, _qualification=False):
        observations.append(_qualification)
        # The ordinary lane is count-blocked; even the private lane must wait
        # until the real storage observation recovers.
        return {'available': _qualification and len(observations) > 2}
    monkeypatch.setattr(environments, 'capacity', capacity)
    value, staged = await run_probe(app, 3)
    assert value['waiting'] == 'waiting' and lifecycle[0] == ['prepare']
    for _ in range(9):
        value = await updates.advance(app, staged['runnerUpdate'])
        if value.get('complete'):
            break
    assert value.get('complete') and all(observations)
    assert lifecycle[0].count('start') == 1


async def test_update_allocation_is_independent_of_ordinary_owned_resource_quota(app, lifecycle):
    from amplifier_web import test_execution_settings as execution
    execution.write(app, {'version': 1, 'queue': {}, 'profiles': {},
        'concurrency': {'maxOwnedResources': 1}})
    row = environments.prepare(app.data_dir, 'ordinary', 'workspace', {}, '/fixture', 'binding', 'existing')
    with pytest.raises(ValueError, match='resource owner capacity'):
        environments.prepare(app.data_dir, 'ordinary-other', 'workspace', {}, '/fixture', 'binding', 'new')
    result, _ = await run_probe(app)
    assert result['complete']
    assert environments.get(app.data_dir, 'ordinary', row['id']) == row


async def test_runner_update_wait_does_not_acquire_chat_idle_barrier(app, monkeypatch):
    import asyncio
    from amplifier_web import smart_tool_update_batch as batch
    from amplifier_web.updates import UpdateManager
    staged = await updates.stage(app, source(app))
    pending = {'previous': 'a' * 24, 'target': 'b' * 24, 'runnerSource': source(app),
               'runnerUpdate': staged['runnerUpdate']}
    app.state['updates'] = {'pendingSmartTools': [pending], 'sequence': {}}
    app.lock = asyncio.Lock()
    app.runtime_lifecycle = lambda: pytest.fail('All-chat idle barrier acquired')
    manager = SimpleNamespace(service=app, lock=asyncio.Lock(), inventory=[],
                              busy=lambda: True)
    class Diagnostics:
        state = {}
        async def run(self, phase, operation, *args, **kwargs):
            return await operation(*args)
    manager.diagnostics = Diagnostics()
    async def publish(**values):
        async with app.lock:
            app.state['updates'].update(values)
    manager.publish = publish
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def waiting(*args):
        entered.set()
        await proceed.wait()
        return {'complete': False, 'waiting': 'waiting'}
    monkeypatch.setattr(updates, 'advance', waiting)
    task = asyncio.create_task(batch.activate(manager))
    await entered.wait()
    # A running capacity owner can still use the ordinary application lock to
    # publish completion/cleanup while this update waits.
    async with app.lock:
        app.state['ownerCleanupProgress'] = 'completed'
    proceed.set()
    await task
    assert app.state['ownerCleanupProgress'] == 'completed'
    assert app.state['updates']['phase'] == 'staged'
    assert not UpdateManager.requires_idle(app.state['updates'])


@pytest.mark.parametrize('phase', ['new', 'prepare', 'capacity', 'start'])
async def test_changed_or_disabled_selection_never_starts_a_guest(app, lifecycle, phase):
    staged = await updates.stage(app, source(app))
    for _ in range({'new': 0, 'prepare': 0, 'capacity': 1, 'start': 2}[phase]):
        await updates.advance(app, staged['runnerUpdate'])
    raw = selected(app)
    raw['enabled'] = False
    private.write_json(app.data_dir / 'test-environments.json', raw)
    with pytest.raises(updates.RunnerUpdateError) as raised:
        await updates.advance(app, staged['runnerUpdate'])
    assert raised.value.diagnostic_facts['reason'] == 'managed-runner-selection-changed'
    assert 'start' not in lifecycle[0] and 'job_start' not in lifecycle[0]
    record = private.read_json(updates.record_path(app, staged['runnerUpdate']))
    assert record['state'] == 'superseded' and record['probeState'] == 'failed'


async def test_changed_selection_cleans_running_owner_without_starting_check(app, lifecycle, monkeypatch):
    calls, original = lifecycle
    staged = await updates.stage(app, source(app))
    for _ in range(3):
        await updates.advance(app, staged['runnerUpdate'])
    assert calls == ['prepare', 'start']
    raw = selected(app)
    raw['enabled'] = False
    private.write_json(app.data_dir / 'test-environments.json', raw)
    def observed(config, app, row, action, args):
        value = original(config, app, row, action, args)
        if action == 'reconcile' and 'remove' not in calls:
            value['status'] = 'running'
            value['observation']['ownership'] = 'matched'
        return value
    monkeypatch.setattr(probe, 'invoke', observed)
    assert not (await updates.advance(app, staged['runnerUpdate']))['complete']
    with pytest.raises(updates.RunnerUpdateError):
        await updates.advance(app, staged['runnerUpdate'])
    assert calls.count('start') == 1 and 'job_start' not in calls
    assert calls.count('stop') == calls.count('remove') == 1
    assert not selected(app)['enabled']


async def test_changed_selection_retains_uncertain_start_without_replay(app, lifecycle, monkeypatch):
    calls, original = lifecycle
    def lost(config, app, row, action, args):
        value = original(config, app, row, action, args)
        if action == 'start':
            raise TimeoutError()
        if action == 'reconcile':
            return {'ok': False, 'status': 'unknown'}
        return value
    monkeypatch.setattr(probe, 'invoke', lost)
    staged = await updates.stage(app, source(app))
    for _ in range(3):
        await updates.advance(app, staged['runnerUpdate'])
    raw = selected(app)
    raw['enabled'] = False
    private.write_json(app.data_dir / 'test-environments.json', raw)
    for _ in range(2):
        assert (await updates.advance(app, staged['runnerUpdate']))['waiting'] == 'unknown'
    assert calls.count('start') == 1 and 'stop' not in calls and 'remove' not in calls
    assert updates.referenced(app.data_dir, staged['id'])


async def test_three_generations_retain_only_current_rollback_and_unsettled(app, monkeypatch):
    monkeypatch.setattr(probe, 'advance', qualified)
    first = selected(app)
    staged = await updates.stage(app, source(app))
    await updates.advance(app, staged['runnerUpdate'])
    middle = selected(app)
    next_install = copy.deepcopy(app.smart_tools.state['installations'][1])
    next_install.update(id='d' * 24, commit='d' * 40)
    python = app.data_dir / 'smart-tools/installs' / next_install['id'] / 'venv/bin/python'
    python.parent.mkdir(parents=True, mode=0o700)
    python.write_text('fixture')
    next_install['python'] = str(python)
    app.smart_tools.state['installations'].append(next_install)
    app.smart_tools.install = AsyncMock(return_value=next_install)
    staged = await updates.stage(app, {**source(app), 'latest': next_install['commit']})
    await updates.advance(app, staged['runnerUpdate'])
    assert not updates.referenced(app.data_dir, first['installationId'])
    assert updates.referenced(app.data_dir, middle['installationId'])
    assert updates.referenced(app.data_dir, next_install['id'])
    # Historical policy and qualification receipts remain intact as evidence.
    assert (updates.root(app) / ('selection-' + updates.key(first) + '.json')).is_file()

async def test_selection_change_plus_byte_drift_never_dispatches_cleanup(app, lifecycle, monkeypatch):
    staged = await updates.stage(app, source(app))
    for _ in range(3):
        await updates.advance(app, staged['runnerUpdate'])
    raw = selected(app)
    raw['enabled'] = False
    private.write_json(app.data_dir / 'test-environments.json', raw)
    monkeypatch.setattr(updates, 'runtime_identity', lambda item: {**RUNTIME, 'runtimeSha256': 'f' * 64})
    with pytest.raises(updates.RunnerUpdateError) as raised:
        await updates.advance(app, staged['runnerUpdate'])
    assert raised.value.diagnostic_facts['reason'] == 'managed-runner-package-changed'
    assert lifecycle[0] == ['prepare', 'start']


async def test_failed_identity_keeps_exact_unused_install_visible_for_skip(app, monkeypatch):
    app.smart_tools.state['installations'][1]['runnerCandidate'] = True
    app.smart_tools.uninstall = AsyncMock()
    request = source(app)
    def broken(*args):
        raise ValueError('identity rejected')
    monkeypatch.setattr(updates, 'runtime_identity', broken)
    with pytest.raises(ValueError, match='identity rejected'):
        await updates.stage(app, request)
    identity = updates.intent_id(request)
    record = private.read_json(updates.record_path(app, identity))
    assert record['candidate']['installationId'] == 'b' * 24
    assert updates.referenced(app.data_dir, 'b' * 24)
    assert 'resourceId' not in record
    await updates.skip(app, identity)
    app.smart_tools.uninstall.assert_awaited_once_with('b' * 24)
    assert not updates.referenced(app.data_dir, 'b' * 24)


@pytest.fixture
def identity_fixture(tmp_path, monkeypatch):
    import sys
    import importlib.metadata
    from amplifier_web import managed_runner_identity as probe_identity
    prefix = tmp_path / 'venv'
    package = prefix / 'Lib/site-packages/digital_twin_universe/__init__.py'
    package.parent.mkdir(parents=True)
    package.write_text('value = 1')
    (prefix / 'pyvenv.cfg').write_text('isolated test')
    executable = prefix / 'python'
    executable.write_text('fixed interpreter')
    monkeypatch.setattr(sys, 'prefix', str(prefix))
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setitem(sys.modules, 'digital_twin_universe', SimpleNamespace(__spec__=SimpleNamespace(origin=str(package))))
    monkeypatch.setattr(probe_identity.sysconfig, 'get_path', lambda _: str(package.parent.parent))
    monkeypatch.setattr(importlib.metadata, 'version', lambda _: '0.test')
    return probe_identity, prefix, package


def test_identity_uses_sysconfig_layout_and_requires_actual_import(identity_fixture, monkeypatch):
    identity, prefix, package = identity_fixture
    value = identity.identity()
    assert value['files'] == 1 and len(value['runtimeSha256']) == 64
    empty = prefix / 'empty'
    empty.mkdir()
    monkeypatch.setattr(identity.sysconfig, 'get_path', lambda _: str(empty))
    with pytest.raises(ValueError, match='outside'):
        identity.identity()


def test_identity_rejects_shared_dependency_links(identity_fixture):
    import os
    identity, prefix, package = identity_fixture
    os.link(package, prefix / 'hardlink')
    with pytest.raises(ValueError, match='shared'):
        identity.identity()


def test_identity_rejects_redirected_dependency_parent(identity_fixture, monkeypatch):
    identity, prefix, package = identity_fixture
    redirect = prefix / 'alias'
    redirect.symlink_to(prefix / 'Lib', target_is_directory=True)
    monkeypatch.setattr(identity.sysconfig, 'get_path', lambda _: str(redirect / 'site-packages'))
    with pytest.raises(ValueError):
        identity.identity()


def test_fixed_incus_identity_mode_uses_specific_package(identity_fixture, monkeypatch):
    import sys
    identity, prefix, package = identity_fixture
    monkeypatch.setitem(sys.modules, 'amplifier_bundle_digital_twin_universe', SimpleNamespace(__spec__=SimpleNamespace(origin=str(package))))
    assert identity.identity(incus_engine=True)['files'] == 1


async def test_retirement_defers_live_owner_and_keeps_user_installs(app):
    retired = app.smart_tools.state['installations'][1]
    retired['runnerCandidate'] = True
    raw = {'installationId': retired['id'], 'commit': NEW}
    private.write_json(updates.record_path(app, 'd' * 32), {
        'id': 'd' * 32, 'state': 'skipped', 'candidate': raw})
    app.smart_tools.uninstall = AsyncMock(side_effect=ValueError('active owner'))
    await updates.retire(app)
    app.smart_tools.uninstall.assert_awaited_once_with(retired['id'])
    app.smart_tools.uninstall = AsyncMock()
    await updates.retire(app)
    app.smart_tools.uninstall.assert_awaited_once_with(retired['id'])
    # Non-purpose-installed/user packages never enter automatic retirement.
    retired.pop('runnerCandidate')
    app.smart_tools.uninstall.reset_mock()
    await updates.retire(app)
    app.smart_tools.uninstall.assert_not_called()


async def test_isolation_failure_cleans_owner_without_running_check(app, lifecycle, monkeypatch):
    def fail(*args):
        raise ValueError('unexpected host mount')
    monkeypatch.setattr(probe.isolation, 'inspect', fail)
    with pytest.raises(updates.RunnerUpdateError):
        await run_probe(app)
    assert lifecycle[0].count('start') == lifecycle[0].count('stop') == lifecycle[0].count('remove') == 1
    assert 'job_start' not in lifecycle[0] and selected(app)['commit'] == OLD


def test_native_incus_isolation_requires_actual_frozen_limits(app, monkeypatch):
    from amplifier_web import incus_environment_worker as worker
    from amplifier_web import managed_runner_isolation as isolation
    from amplifier_web import test_environment_profiles as profiles
    config = {'backend': 'incus', 'incus': {}}
    row = {'profileId': 'linux-python', 'resourceRequest': profiles.freeze('linux-python', {'cpus': 1, 'memoryMiB': 512}),
           'sessionId': 'owner', 'launchOperationId': 'operation', 'universeId': 'guest'}
    limits = profiles.resources(row['profileId'], row['resourceRequest'])
    actual = {'limits.cpu': '1', 'limits.memory': '512MiB', 'limits.processes': str(limits['pids'])}
    monkeypatch.setattr(worker, 'environment', lambda *args: {})
    observed = []
    def observe(*args, **kwargs):
        observed.append(kwargs)
        return {}, {'expanded_config': actual}
    monkeypatch.setattr(worker, 'observe', observe)
    assert isolation.inspect(config, app.data_dir, row)['offline']
    assert observed == [{'confinement': True}]
    actual['limits.memory'] = '4096MiB'
    with pytest.raises(ValueError, match='limits'):
        isolation.inspect(config, app.data_dir, row)


async def test_same_package_qualified_on_new_backend_keeps_first_owner_proof(app, monkeypatch):
    monkeypatch.setattr(probe, 'advance', qualified)
    initial = selected(app)
    first = await updates.stage(app, source(app))
    await updates.advance(app, first['runnerUpdate'])
    first_config = environments.configuration(app)[0]
    other = {**initial, 'dockerHost': 'unix:///owned/other.sock'}
    private.write_json(app.data_dir / 'test-environments.json', other)
    private.write_json(private.root(app) / ('e' * 32 + '.json'), {
        'id': 'e' * 32, 'state': 'completed', 'priorConfiguration': None,
        'installationId': other['installationId'], 'candidateConfiguration': other,
        'proof': {'probeRemoved': True}})
    second = await updates.stage(app, source(app))
    await updates.advance(app, second['runnerUpdate'])
    second_config = environments.configuration(app)[0]
    assert first_config['installationId'] == second_config['installationId']
    assert updates.qualification_path(app.data_dir, first_config) != updates.qualification_path(app.data_dir, second_config)
    updates.validate_execution(app.data_dir, first_config)
    updates.validate_execution(app.data_dir, second_config)


async def test_passive_inventory_without_posix_private_file_capabilities(tmp_path, monkeypatch):
    app = SimpleNamespace(data_dir=tmp_path)
    for name in ('getuid', 'O_NOFOLLOW', 'O_DIRECTORY'):
        monkeypatch.delattr(updates.os, name)
    assert updates.update_sources(app) == []
    assert not updates.rollback(app, 'unrelated-mcp-installation')
    assert not updates.referenced(tmp_path, 'unrelated-mcp-installation')
    await updates.retire(app)
    (tmp_path / 'test-environments.json').write_text('{}')
    row = updates.update_sources(app)[0]
    assert not row['eligible'] and row['status'] == 'not_checked'
    assert 'not supported on this platform' in row['detail']
    assert 'not been checked' in row['detail']


@pytest.mark.parametrize('changed', [None, 'host-mount', 'extra-capability', 'extra-bind'])
def test_native_docker_inspection_named_volumes_and_normalized_capabilities(monkeypatch, changed):
    from amplifier_web import managed_runner_isolation as isolation
    from amplifier_web import test_environment_worker as worker
    from amplifier_web import test_environment_profiles as profiles
    row = {'profileId': 'linux-python', 'resourceRequest': profiles.freeze('linux-python'),
           'sessionId': 'owner', 'launchOperationId': 'operation', 'universeId': 'test-owned',
           'backendIdentity': {'daemon': 'daemon'}}
    limits = profiles.resources(row['profileId'], row['resourceRequest'])
    owner = {'io.amplifier.root-owner': 'owner', 'io.amplifier.operation-id': 'operation',
             'com.docker.compose.project': 'test-owned'}
    mounts = [{'Name': 'owned-' + str(i), 'Type': 'volume', 'Destination': value}
              for i, value in enumerate(['/workspace', '/artifacts', '/var/lib/unified-jobs'])]
    settings = {'Privileged': False, 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                'CapAdd': ['CAP_CHOWN', 'CAP_KILL', 'CAP_SETGID', 'CAP_SETUID'],
                'SecurityOpt': ['no-new-privileges:true'],
                'Binds': [item['Name'] + ':' + item['Destination'] + ':rw' for item in mounts],
                'Memory': limits['memoryMiB'] * 1024**2, 'NanoCpus': int(limits['cpus'] * 10**9),
                'PidsLimit': limits['pids'], 'ShmSize': limits['shmMiB'] * 1024**2,
                'Tmpfs': {'/tmp': 'rw,noexec,nosuid,size=' + str(limits['tmpMiB'] * 1024**2)}}
    if changed == 'host-mount':
        mounts[0]['Type'] = 'bind'
    if changed == 'extra-capability':
        settings['CapAdd'].append('CAP_SYS_ADMIN')
    if changed == 'extra-bind':
        settings['Binds'].append('/private:/private:rw')
    container = {'Config': {'Labels': owner}, 'HostConfig': settings, 'Mounts': mounts,
                 'NetworkSettings': {'Networks': {'owned-network': {}}}}
    def docker(payload, *args):
        if args[0] == 'info': return {'ID': 'daemon'}
        if args[0] == 'container': return {'ID': 'container'}
        if args[0] == 'inspect': return [container]
        if args[0] == 'volume': return [{'Labels': owner, 'Options': {}}]
        if args[0] == 'network': return [{'Labels': owner, 'Internal': True}]
        raise AssertionError(args)
    monkeypatch.setattr(worker, 'docker_json', docker)
    if changed:
        with pytest.raises(ValueError): isolation.inspect({'backend': 'docker'}, '.', row)
    else:
        assert isolation.inspect({'backend': 'docker'}, '.', row)['hostMounts'] is False


async def test_allowlisted_new_generation_still_requires_exact_runtime_and_retained_proof(app, monkeypatch):
    candidate = app.smart_tools.state['installations'][1]
    candidate.update(commit=OLD, runnerCandidate=True)
    request = {**source(app), 'latest': OLD}
    staged = await updates.stage(app, request)
    record = private.read_json(updates.record_path(app, staged['runnerUpdate']))
    assert record['candidate']['runnerQualificationRequired'] is True
    assert record['normalized']['runnerQualificationRequired'] is True
    with pytest.raises(ValueError, match='not passed'):
        environments.validate_configuration_value(app, record['candidate'])
    monkeypatch.setattr(probe, 'advance', qualified)
    await updates.advance(app, staged['runnerUpdate'])
    config, status, _ = environments.configuration(app)
    assert status == 'ready' and config['runnerQualificationRequired']
    updates.validate_execution(app.data_dir, config)
    from amplifier_web import test_environment_configs as retained
    identity = retained.retain(app.data_dir, config)
    assert retained.resolve(app, {'backendKey': identity, 'backendConfigRetained': True}, None) == config
    monkeypatch.setattr(updates, 'runtime_identity', lambda *_: {**RUNTIME, 'runtimeSha256': 'd' * 64})
    with pytest.raises(updates.RunnerUpdateError):
        updates.validate_execution(app.data_dir, config)
    # A removed proof cannot silently downgrade a known source revision to the
    # historical static allow-list path, including retained owner execution.
    updates.qualification_path(app.data_dir, config).unlink()
    with pytest.raises(updates.RunnerUpdateError):
        updates.validate_execution(app.data_dir, config)
    assert environments.configuration(app)[1] == 'unavailable'


def test_legacy_known_generation_keeps_existing_static_execution_path(app, monkeypatch):
    config, status, _ = environments.configuration(app)
    assert status == 'ready' and 'runnerQualificationRequired' not in config
    monkeypatch.setattr(updates, 'runtime_identity', lambda *_: pytest.fail('legacy byte hashing'))
    updates.validate_execution(app.data_dir, config)


@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('with_mcp', [False, True])
def test_public_tool_inventory_on_platform_without_posix_capabilities(tmp_path, monkeypatch, configured, with_mcp):
    from amplifier_web.smart_tool_updates import ManagedUpdates
    app = SimpleNamespace(data_dir=tmp_path)
    manager = SimpleNamespace(service=app, state={'installations': [], 'servers': []})
    app.smart_tools = manager
    if with_mcp:
        manager.state['installations'].append({'id': 'mcp', 'commit': 'a' * 40, 'repository': 'https://example.test/tool', 'binDir': '/tool/bin'})
        manager.state['servers'].append({'installationId': 'mcp', 'transport': 'stdio', 'command': '/tool/bin/tool'})
    if configured:
        (tmp_path / 'test-environments.json').write_text('{}')
    for name in ('getuid', 'O_NOFOLLOW', 'O_DIRECTORY'):
        monkeypatch.delattr(updates.os, name)
    rows = ManagedUpdates.update_sources(manager)
    ordinary = [row for row in rows if row.get('installationId') == 'mcp']
    assert len(ordinary) == int(with_mcp)
    rows = [row for row in rows if row.get('installationId') != 'mcp']
    if configured:
        assert {row['runnerKind'] for row in rows} == {'managed-dtu-core', 'managed-incus-engine'}
        assert all(row['status'] == 'not_checked' and not row['eligible'] for row in rows)
    else:
        assert rows == []


@pytest.mark.parametrize('policy_case',['disabled','grace','eligible'])
async def test_update_trigger_uses_global_cleanup_policy_and_guarded_owner(app,monkeypatch,policy_case):
    import asyncio
    import sqlite3
    from amplifier_web import storage_cleanup, update_retention
    from amplifier_web.smart_tool_lifecycle import Lifecycle
    from test_storage_cleanup_runner import git
    folder=app.data_dir/'smart-tools/installs'/('d'*24)
    checkout=folder/'source';checkout.mkdir(parents=True)
    git(checkout,'init','-q');git(checkout,'config','user.name','Fixture');git(checkout,'config','user.email','fixture@example.invalid')
    (checkout/'code.py').write_text('answer=42\n');git(checkout,'add','.');git(checkout,'commit','-qm','Installed source')
    oldest={'id':'d'*24,'commit':git(checkout,'rev-parse','HEAD'),
            'runnerCandidate':True,'repository':environments.REPOSITORY}
    app.smart_tools.state['installations'].append(oldest)
    private.write_json(updates.record_path(app,'e'*32),{'state':'superseded',
        'candidate':{'installationId':oldest['id']},'runtime':RUNTIME})
    app.state['updates']={'phase':'installed'}
    app.db=sqlite3.connect(':memory:')
    app.db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,fingerprint TEXT,receipt TEXT)')
    app.lock=asyncio.Lock()
    app.update_manager=SimpleNamespace(service=app,home=app.data_dir,directory=app.data_dir/'updates',
        wakeup=asyncio.Event(),lock=asyncio.Lock(),awaiting_restart=lambda:False)
    settings={**storage_cleanup.DEFAULTS,'enabled':policy_case!='disabled',
              'keepDays':3650 if policy_case=='grace' else 0,'maxUnusedBytes':0}
    storage_cleanup.store(app,storage_cleanup.POLICY,settings)
    app.smart_tools.root=app.data_dir/'smart-tools';app.smart_tools.service=app
    app.smart_tools.install_lock=asyncio.Lock()
    async def mutate(function):function(app.smart_tools.state)
    app.smart_tools._change=mutate
    calls=[]
    async def uninstall(identity,**kwargs):
        calls.append((identity,kwargs))
        return await Lifecycle.uninstall(app.smart_tools,identity,**kwargs)
    app.smart_tools.uninstall=uninstall
    original=update_retention._probe
    async def synthetic_runtime_only(operation,values,**kwargs):
        if operation=='runner':return None
        return await original(operation,values,**kwargs)
    monkeypatch.setattr(update_retention,'_probe',synthetic_runtime_only)
    async def no_process_references(*args,**kwargs):return ''
    monkeypatch.setattr(update_retention,'inspect_references',no_process_references)
    staged=await updates.stage(app,source(app))
    monkeypatch.setattr(probe,'advance',qualified)
    assert (await updates.advance(app,staged['runnerUpdate']))['complete']
    # Completion schedules the existing maintenance owner; it must not call
    # unselected retirement before that owner evaluates the global policy.
    assert not calls and folder.exists()
    assert app.update_manager.wakeup.is_set() and app.update_manager._storage_policy_due
    result=await storage_cleanup.run(app,'after-update-'+policy_case,automatic=True)
    if policy_case=='eligible':
        assert len(calls)==1 and calls[0][0]==oldest['id']
        assert calls[0][1]['cleanup_inventory']['id']==oldest['id']
        assert callable(calls[0][1]['cleanup_guard'])
        assert not folder.exists() and oldest not in app.smart_tools.state['installations']
        assert result['effects'][0]['status']=='completed' and result['removedFileBytes']>0
    else:
        assert not calls and folder.exists() and oldest in app.smart_tools.state['installations']
    assert updates.referenced(app.data_dir,selected(app)['installationId'])


def test_remote_only_inventory_without_private_file_primitives_stays_not_checked(tmp_path, monkeypatch):
    from amplifier_web.smart_tool_updates import ManagedUpdates
    from amplifier_web import registered_test_runners as runners
    app = SimpleNamespace(data_dir=tmp_path)
    manager = SimpleNamespace(service=app, state={'installations': [], 'servers': []})
    app.smart_tools = manager
    (tmp_path / 'test-runners.json').write_text('not opened on unsupported platform')
    for name in ('getuid', 'O_NOFOLLOW', 'O_DIRECTORY'):
        monkeypatch.delattr(updates.os, name)
    monkeypatch.setattr(runners, 'store', lambda *_: pytest.fail('unsupported private file read'))
    rows = ManagedUpdates.update_sources(manager)
    assert len(rows) == 1 and rows[0]['runnerKind'] == 'managed-incus-engine'
    assert rows[0]['status'] == 'not_checked' and not rows[0]['eligible']
