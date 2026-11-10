"""Real update owner/sequence with synthetic native observations.

These are scheduling, fence and proof-composition tests. Native private files,
TokenUser, uv launcher, wheel transport and the actual installed host are separate
qualification; no native handoff or component promotion is claimed here.
"""
import asyncio
import copy
import json
from pathlib import Path
import sys
import time

import pytest

from amplifier_web import app_updates, manual_app_handoff as handoff, updates, update_sequence, update_health
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager


class Runtime:
    async def close(self):
        pass


@pytest.fixture
async def manager(tmp_path, monkeypatch):
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    manager.running_identity.update(version='0.71.4', revision='b' * 40)
    pending = {'revision': 'a' * 40, 'generation': 'c' * 32, 'attemptId': 'd' * 32,
               'latest': 'v0.71.3', 'extras': []}
    manager.diagnostics.begin('application', pending['revision'], pending['attemptId'], application_extras=[])
    manager.diagnostics.record('restart-capability', 'failed', errorType='RuntimeError')
    service.state['updates'].update(phase='error', error='retained refusal', pendingApp=pending,
        application={'id': 'application', 'current': '0.71.4', 'runningRevision': 'b' * 40,
                     'latest': 'v0.72.0', 'revision': 'e' * 40, 'status': 'update'},
        appAvailable=True, sequence={'stage': 'application', 'install': True})
    manager._storage_last_pass = time.time()
    manager._storage_policy_due = manager._update_health_initial = False
    service.state['settings']['updates'].update(autoCheck=False, autoInstall=False)
    yield manager
    await service.close()


def target(manager):
    return {'hostInstanceId': manager.service.instance_id,
            **{key: manager.service.state['updates']['pendingApp'][key]
               for key in ('revision', 'generation', 'attemptId')}}


@pytest.fixture
def proof(manager, monkeypatch):
    binding = {'version': '0.71.4', 'revision': 'b' * 40, 'graph': [], 'extras': [],
               'ownerSid': 'fixture-owner', 'dataIdentity': 'fixture-data',
               'directories': [['1', '2'], ['1', '3']], 'installation': {'digest': 'f' * 64}}
    value = {'binding': binding, 'payload': {'members': 100, 'recordDigest': '1' * 64},
             'stagedDigest': '2' * 64, 'launcher': 'fixture-launcher', 'tlsPin': '3' * 64}
    async def qualify(*args):
        return copy.deepcopy(value)
    monkeypatch.setattr(handoff, 'qualify', qualify)
    monkeypatch.setattr(handoff, '_native_binding', lambda manager: copy.deepcopy(binding))
    monkeypatch.setattr(handoff, '_payload', lambda proof: copy.deepcopy(value['payload']))
    monkeypatch.setattr(handoff, '_staged', lambda *args: ({}, value['stagedDigest']))
    monkeypatch.setattr(handoff, '_read', Path.read_bytes)
    return value


async def test_exact_refusal_retires_only_pending_app_and_preserves_truth(manager, proof):
    state = manager.service.state['updates']
    before, args = copy.deepcopy(state), target(manager)
    result = await handoff.reconcile(manager, args, 'request-one')
    assert result['phase'] == handoff.PHASE and result['status'] == 'succeeded'
    assert state['pendingApp'] is None
    assert state['application'] == before['application'] and state['appAvailable']
    assert state['diagnostics'] == before['diagnostics']
    assert 'applicationResult' not in state and not updates.active_release(manager.home)
    assert state['sequence']['nextStage'] == 'included' and state['sequence']['install']
    assert update_health.current(state)['status'] == 'action-required'
    assert update_health.current(state, manual_verified='request-one')['status'] == 'available'


@pytest.mark.parametrize('install', [False, True])
async def test_713_to_714_with_720_available_reaches_normal_component_stage(manager, proof, monkeypatch, install):
    state = manager.service.state['updates']
    state['sequence']['install'] = install
    # Even automatic preference must not create missing intent for this receipt.
    manager.service.state['settings']['updates']['autoInstall'] = True
    await handoff.reconcile(manager, target(manager), 'request-components')
    calls = []
    async def inventory(**kwargs):
        calls.append('inventory')
        return [{'id': 'included', 'kind': 'runtime environment', 'status': 'update',
                 'eligible': True, 'updateTier': 'included',
                 'appSelection': update_sequence.app_selection(manager.home)['witness']}]
    async def never_app():
        pytest.fail('The verified app leg must not retarget to 0.72.0')
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(manager, 'app', never_app)
    # Stop at the real component staging boundary, before any fixture install.
    def stage_selection(*args, **kwargs):
        calls.append('component-stage')
        raise ValueError('fixture: reached ordinary component selection')
    monkeypatch.setattr(update_sequence, 'verify_app_selection', lambda *args, **kwargs: None)
    await manager.check(tier='included', install=install, automatic=True)
    assert state['appAvailable'] and state['available'] == 2
    assert state['sequence']['stage'] == 'included'
    monkeypatch.setattr(update_sequence, 'verify_app_selection', stage_selection)
    async def installed_target():
        return ('uv', 'fixture-launcher', Path(sys.prefix) / 'Scripts/python.exe',
                {'installation': sys.prefix})
    async def health(*args):
        return proof['tlsPin']
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(handoff, '_health', health)
    if install:
        with pytest.raises(ValueError, match='ordinary component selection'):
            await manager.install(automatic=True)
        assert calls == ['inventory', 'component-stage']
    else:
        await manager.tick()
        await manager.install(automatic=True)
        assert calls == ['inventory']


@pytest.mark.parametrize('key,value', [
    ('pendingRestart', {}), ('pendingRestart', {'unknown': True}),
    ('pendingReplacement', {}), ('pendingRelease', 'later-generation'), ('pendingRollback', {}),
    ('pendingSmartTools', [{'unknown': True}]), ('phase', 'activating'),
    ('drain', {'phase': 'replacing'}),
    ('featureResults', {'feature': {'phase': 'queued'}})])
async def test_other_owners_are_never_cleared(manager, proof, key, value):
    state = manager.service.state['updates']
    args = target(manager)
    state[key] = value
    before = copy.deepcopy(state)
    assert (await handoff.reconcile(manager, args, 'refused'))['status'] == 'rejected'
    assert {k: v for k, v in state.items() if k != handoff.RESULTS} == before


@pytest.mark.parametrize('mutation', ['pending', 'failure', 'configuration', 'intent', 'feature', 'sequence'])
async def test_change_during_verification_refuses_without_retiring_new_state(manager, proof, monkeypatch, mutation):
    state = manager.service.state['updates']
    async def changed(*args):
        if mutation == 'pending':
            state['pendingApp']['generation'] = 'f' * 32
        elif mutation == 'failure':
            manager.diagnostics.record('restart-capability', 'failed', errorType='RuntimeError')
        elif mutation == 'configuration':
            manager.service.state['configurationRevision'] = 99
        elif mutation == 'intent':
            state['intentRevision'] = 99
        elif mutation == 'feature':
            state['pendingApp']['featureSelection'] = {'changed': True}
        else:
            state['sequence']['install'] = False
        return copy.deepcopy(proof)
    monkeypatch.setattr(handoff, 'qualify', changed)
    assert (await handoff.reconcile(manager, target(manager), 'raced'))['status'] == 'rejected'
    assert state['pendingApp'] and 'manualHandoff' not in state


@pytest.mark.parametrize('field', ['ownerSid', 'dataIdentity', 'directories', 'installation', 'graph', 'extras', 'revision'])
async def test_current_installation_change_at_commit_refuses(manager, proof, monkeypatch, field):
    changed = {**proof['binding'], field: 'changed'}
    monkeypatch.setattr(handoff, '_native_binding', lambda manager: changed)
    assert (await handoff.reconcile(manager, target(manager), 'changed'))['status'] == 'rejected'
    assert manager.service.state['updates']['pendingApp']


async def test_exact_retry_and_reload_do_not_reexecute_verifier(manager, proof, monkeypatch):
    args = target(manager)
    first = await handoff.reconcile(manager, args, 'exact')
    async def never(*args):
        pytest.fail('An exact retry must read the durable result')
    monkeypatch.setattr(handoff, 'qualify', never)
    assert await handoff.reconcile(manager, args, 'exact') == first
    manager.service._save_changes(globals={'updates'})
    restored = UpdateManager(manager.service)
    assert await handoff.reconcile(restored, args, 'exact') == first
    assert getattr(restored, '_manual_handoff_verified', None) is None
    calls = []
    async def installed_target():
        calls.append('launcher')
        return ('uv', 'fixture-launcher', Path(sys.prefix) / 'Scripts/python.exe', {'installation': sys.prefix})
    async def health(*args):
        calls.append('health')
        return proof['tlsPin']
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(handoff, '_health', health)
    assert await handoff.verify_continuation(restored)
    assert calls == ['launcher', 'health']
    proof['binding']['directories'] = [['copied', 'home']]
    with pytest.raises(ValueError):
        await handoff.verify_continuation(restored)
    assert restored._manual_handoff_verified is None


async def test_cancelled_verification_is_not_replayed(manager, proof, monkeypatch):
    args = target(manager)
    async def cancelled(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(handoff, 'qualify', cancelled)
    with pytest.raises(asyncio.CancelledError):
        await handoff.reconcile(manager, args, 'cancelled')
    assert manager.service.state['updates']['pendingApp']
    assert (await handoff.reconcile(manager, args, 'cancelled'))['status'] == 'interrupted'


async def test_new_deliberate_check_restores_app_first(manager, proof, monkeypatch):
    await handoff.reconcile(manager, target(manager), 'completed')
    async def application():
        return {'id': 'application', 'status': 'update', 'latest': 'v0.72.0'}
    async def inventory(**kwargs):
        pytest.fail('A fresh cycle must check the app first')
    monkeypatch.setattr(app_updates, 'check', application)
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    await manager.check()
    assert manager.service.state['updates']['sequence']['stage'] == 'application'
    assert not handoff.continuing(manager)


def test_non_native_installation_cannot_supply_proof(manager):
    if sys.platform != 'win32':
        with pytest.raises(ValueError, match='native Windows'):
            handoff._native_binding(manager)


@pytest.fixture
def native_observations(manager, monkeypatch, tmp_path):
    """Only I/O observations are synthetic; run the actual qualify composition."""
    from amplifier_web import app_release_artifacts as artifacts
    wheel = tmp_path / artifacts.filename('0.71.4')
    binding = {'version': '0.71.4', 'revision': 'b' * 40, 'graph': [], 'extras': [],
               'ownerSid': 'fixture-owner', 'direct': {'url': wheel.as_uri(),
                   'archive_info': {'hashes': {'sha256': 'f' * 64}}}}
    staged = {'version': '0.71.3', 'revision': 'a' * 40, 'extras': [],
              'artifact': {'sha256': 'f' * 64}, 'componentGraph': []}
    calls = []
    monkeypatch.setattr(handoff, '_native_binding', lambda manager: copy.deepcopy(binding))
    monkeypatch.setattr(handoff, '_staged', lambda *args: (copy.deepcopy(staged), 'staged-digest'))
    monkeypatch.setattr(handoff, '_payload', lambda proof: {'members': 5})
    async def installed_target():
        calls.append('target')
        return ('uv', 'fixture-launcher', Path(sys.prefix) / 'Scripts/python.exe',
                {'installation': sys.prefix})
    async def prepare(folder, version, revision):
        calls.append(('official-wheel', version, revision))
        return {'url': artifacts.SOURCE + '/releases/download/v' + version + '/' + artifacts.filename(version),
                'sha256': 'f' * 64, 'path': str(folder / artifacts.filename(version))}
    async def process(python, isolated, code_arg, script, *args, **kwargs):
        assert isolated == '-I' and code_arg == '-c'
        calls.append('probe' if script == app_updates.PROBE else 'dependencies')
        return 'AMPLIFIER_UPDATE_PROBE=' + json.dumps({'ok': True, 'isolated': True,
                'stage': 'complete', 'version': binding['version']})
    async def health(*args):
        calls.append('health')
        return 'pin'
    monkeypatch.setattr(app_updates, 'installed_target', installed_target)
    monkeypatch.setattr(artifacts, 'prepare', prepare)
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr(handoff, '_health', health)
    return binding, staged, calls


@pytest.mark.parametrize('version', ['0.71.3', '0.71.4', '0.72.0'])
async def test_each_running_version_requires_its_own_official_payload(manager, native_observations, version, tmp_path):
    from amplifier_web import app_release_artifacts as artifacts
    binding, staged, calls = native_observations
    binding.update(version=version, revision=staged['revision'] if version == '0.71.3' else 'b' * 40)
    binding['direct']['url'] = (tmp_path / artifacts.filename(version)).as_uri()
    value = await handoff.qualify(manager, manager.service.state['updates']['pendingApp'])
    assert value['binding'] == binding
    assert calls == ['target', ('official-wheel', version, binding['revision']),
                     'probe', 'dependencies', 'health', 'target']


@pytest.mark.parametrize('mutation', ['older', 'same-version-revision', 'same-version-hash',
                                    'same-version-graph', 'extras', 'custom', 'missing-hash', 'wrong-hash'])
async def test_invalid_manual_install_never_qualifies(manager, native_observations, mutation, tmp_path):
    from amplifier_web import app_release_artifacts as artifacts
    binding, staged, calls = native_observations
    if mutation == 'older':
        binding['version'] = '0.71.2'
    elif mutation.startswith('same-version'):
        binding.update(version='0.71.3', revision=staged['revision'])
        binding['direct']['url'] = (tmp_path / artifacts.filename('0.71.3')).as_uri()
        if mutation == 'same-version-revision':
            binding['revision'] = 'c' * 40
        elif mutation == 'same-version-hash':
            staged['artifact']['sha256'] = 'c' * 64
        else:
            staged['componentGraph'] = [{'name': 'different', 'version': '1.0'}]
    elif mutation == 'extras':
        binding['extras'] = ['tui']
    elif mutation == 'custom':
        binding['direct'] = {'url': 'https://example.invalid/custom.whl', 'archive_info': {}}
    elif mutation == 'missing-hash':
        binding['direct']['archive_info'] = {}
    else:
        binding['direct']['archive_info']['hashes']['sha256'] = 'c' * 64
    with pytest.raises(ValueError):
        await handoff.qualify(manager, manager.service.state['updates']['pendingApp'])
    assert 'probe' not in calls and manager.service.state['updates']['pendingApp']


@pytest.mark.parametrize('boundary', ['probe', 'dependencies', 'health', 'final-binding', 'final-payload'])
async def test_failed_or_mutated_current_proof_remains_failure(manager, native_observations, monkeypatch, boundary):
    binding, _, calls = native_observations
    original_process, original_health = app_updates.process, handoff._health
    if boundary in {'probe', 'dependencies'}:
        async def failing(*args, **kwargs):
            is_probe = args[3] == app_updates.PROBE
            if is_probe == (boundary == 'probe'):
                raise ValueError('fixture failed')
            return await original_process(*args, **kwargs)
        monkeypatch.setattr(app_updates, 'process', failing)
    elif boundary == 'health':
        async def failing(*args):
            raise ValueError('fixture wrong listener')
        monkeypatch.setattr(handoff, '_health', failing)
    else:
        async def mutate(*args):
            if boundary == 'final-binding':
                binding['graph'] = [{'name': 'changed', 'version': '1'}]
            else:
                monkeypatch.setattr(handoff, '_payload', lambda proof: {'members': 6})
            return await original_health(*args)
        monkeypatch.setattr(handoff, '_health', mutate)
    with pytest.raises(ValueError):
        await handoff.qualify(manager, manager.service.state['updates']['pendingApp'])