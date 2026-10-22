"""Exact read-side compute recovery never retries or resolves an uncertain test."""
import copy
import json

from filelock import FileLock
import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_host_resources as host
from amplifier_web import test_runner_controller as controller
from test_configuration_authority import app
from test_task_environments import backend, start, act, scope
from test_test_host_resources import machine, native_backend
from test_environment_recovery import observation
from test_registered_test_runners import target, prepared, request
from test_test_environment_worker import library, removal_main, real_docker_owner, real_docker_json


async def lost_check(app, monkeypatch):
    first = await start(app)
    def lost(*args):
        raise TimeoutError('lost check acknowledgement')
    monkeypatch.setattr(owner, 'invoke', lost)
    uncertain = await act(app, 'execute', first['resource'], 'check-once', command='check-once')
    return uncertain['resource'], uncertain['request']


def inspect(monkeypatch, state='stopped', **changes):
    calls = []
    def invoke(config, home, row, action, args):
        calls.append(action)
        if action == 'read':
            return {'ok': True, 'status': state, 'universeId': row['universeId']}
        assert action == 'reconcile'
        return observation(row, 'absent' if state == 'removed' else state, **changes)
    monkeypatch.setattr(owner, 'invoke', invoke)
    return calls


@pytest.mark.parametrize('state', ['stopped', 'removed'])
async def test_read_recovers_exact_compute_only_and_does_not_repeat(app, native_backend, monkeypatch, state):
    row, original = await lost_check(app, monkeypatch)
    assert host.snapshot()['reserved']['count'] == 1
    calls = inspect(monkeypatch, state)
    before = copy.deepcopy(app.operations.requests.list(row['sessionId']))
    first = (await app.dispatch('workspace.environments.read', scope(app)))['result']['items'][0]
    assert host.snapshot()['reserved']['count'] == 0
    assert first['lastOperation'] == original and first['status'] == state
    saved = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert saved['status'] != 'retired', 'Compute recovery does not claim cleanup or release storage ownership'
    assert saved['reservationRecovery']['state'] == 'released'
    assert saved['reservationRecovery']['observation']['admissionSettled'] is True
    assert app.operations.requests.list(row['sessionId']) == before
    await app.dispatch('workspace.environments.read', scope(app))
    assert calls == ['read', 'reconcile', 'read']
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('changes', [
    {'admissionSettled': False}, {'rootOwner': 'other'}, {'operationId': 'other'},
    {'universeId': 'other'}, {'backendIdentity': {'backend': 'docker', 'daemon': 'other'}},
    {'ownership': 'unknown'},
])
async def test_stopped_observation_never_frees_uncertain_or_foreign_owner(app, native_backend, monkeypatch, changes):
    row, original = await lost_check(app, monkeypatch)
    calls = inspect(monkeypatch, **changes)
    await app.dispatch('workspace.environments.read', scope(app))
    assert host.snapshot()['reserved']['count'] == 1
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original
    assert 'reservationRecovery' not in owner.get(app.data_dir, row['sessionId'], row['id'])
    assert calls == ['read', 'reconcile']


@pytest.mark.parametrize('held', ['builder', 'lock', 'running', 'row-changed'])
async def test_read_preserves_active_owner_and_rechecks_after_observation(app, native_backend, monkeypatch, held):
    row, _ = await lost_check(app, monkeypatch)
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    if held == 'builder':
        owner.save(app.data_dir, raw, preparationPending=True)
    calls = inspect(monkeypatch, 'running' if held == 'running' else 'stopped')
    if held == 'row-changed':
        previous = owner.invoke
        def changed(config, home, value, action, args):
            result = previous(config, home, value, action, args)
            if action == 'reconcile':
                owner.save(home, owner.get(home, value['sessionId'], value['id']), launchOperationId='new-launch')
            return result
        monkeypatch.setattr(owner, 'invoke', changed)
    lock = FileLock(str(app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')))
    if held == 'lock':
        with lock:
            await app.dispatch('workspace.environments.read', scope(app))
    else:
        await app.dispatch('workspace.environments.read', scope(app))
    assert host.snapshot()['reserved']['count'] == 1
    assert calls == ([] if held == 'lock' else ['read', 'reconcile'] if held in {'row-changed', 'running'} else ['read'])


async def test_explicit_stopped_inspection_does_not_release_unsettled_launch(app, native_backend, monkeypatch):
    row, original = await lost_check(app, monkeypatch)
    inspect(monkeypatch, admissionSettled=False)
    result = await act(app, 'reconcile', row, 'inspect-stopped')
    assert result['request']['state'] == 'completed' and result['resource']['status'] == 'stopped'
    assert result['resource']['lastOperation'] == original
    assert host.snapshot()['reserved']['count'] == 1


@pytest.mark.parametrize('initial', ['completed', 'unknown'])
def test_target_read_settles_its_own_lease_without_rewriting_effect(target, monkeypatch, initial):
    value = prepared(target)
    value['expectedBackendIdentity'] = target[1]
    calls = []
    def effect(payload):
        calls.append(payload['action'])
        if payload['action'] == 'start':
            return {'ok': initial == 'completed', 'status': 'running' if initial == 'completed' else 'unknown'}
        if payload['action'] == 'read':
            return {'ok': True, 'status': 'stopped'}
        assert payload['action'] == 'reconcile'
        proof = controller.worker.common.observation(payload, controller.worker.identity(payload), target[1], 'retained-launch', 'matched', True)
        return {'ok': True, 'status': 'stopped', 'observation': proof}
    monkeypatch.setattr(controller.worker, 'execute_call', effect)
    first = request(target, value, 'start')
    root = target[0] / ('owner-' + controller.digest(controller.owner(value)))
    path = root / ('effect-' + controller.digest([value['requestId'], 'start']) + '.json')
    original = path.read_bytes()
    assert host.snapshot()['reserved']['count'] == 1
    request(target, value, 'read')
    assert host.snapshot()['reserved']['count'] == 0
    assert path.read_bytes() == original
    assert controller.read(root / 'reservation-recovery.json')['state'] == 'released'
    assert request(target, value, 'start') == first
    request(target, value, 'read')
    assert calls == ['start', 'read', 'reconcile', 'read']


@pytest.mark.parametrize('change', ['unsettled', 'foreign', 'preparing', 'absent-inventory'])
def test_target_read_retains_uncertain_capacity(target, monkeypatch, change):
    value = prepared(target)
    value['expectedBackendIdentity'] = target[1]
    calls = []
    def effect(payload):
        calls.append(payload['action'])
        if payload['action'] == 'start':
            return {'ok': False, 'status': 'unknown'}
        if payload['action'] == 'read':
            return {'ok': True, 'status': 'stopped'}
        proof = controller.worker.common.observation(payload, controller.worker.identity(payload), target[1], 'retained-launch', 'matched', change != 'unsettled')
        if change == 'foreign':
            proof['operationId'] = 'foreign'
        return {'ok': True, 'status': 'absent' if change == 'absent-inventory' else 'stopped', 'observation': proof}
    monkeypatch.setattr(controller.worker, 'execute_call', effect)
    request(target, value, 'start')
    if change == 'preparing':
        root = target[0] / ('owner-' + controller.digest(controller.owner(value)))
        (root / 'preparation').mkdir(mode=0o700)
        controller.write(root / 'preparation/intent.json', {'pending': True})
    request(target, value, 'read')
    assert host.snapshot()['reserved']['count'] == 1
    assert calls == (['start', 'read'] if change == 'preparing' else ['start', 'read', 'reconcile'])


async def test_unknown_cached_state_uses_native_observation_and_handles_reserve_commit_gap(app, native_backend, monkeypatch):
    row, original = await lost_check(app, monkeypatch)
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    raw.pop('hostReservation')
    raw = owner.save(app.data_dir, raw)
    calls = []
    def invoke(config, home, value, action, args):
        calls.append(action)
        return {'ok': True, 'status': 'unknown', 'universeId': value['universeId']} if action == 'read' else observation(value, 'stopped')
    monkeypatch.setattr(owner, 'invoke', invoke)
    result = (await app.dispatch('workspace.environments.read', scope(app)))['result']['items'][0]
    assert result['status'] == 'stopped' and result['lastOperation'] == original
    assert host.snapshot()['reserved']['count'] == 0
    assert calls == ['read', 'reconcile']


async def test_probe_rotation_skips_released_rows_and_does_not_starve_unknown(app, native_backend, monkeypatch):
    first = (await start(app, 'first'))['resource']
    second = (await start(app, 'second'))['resource']
    third = (await start(app, 'third'))['resource']
    host.settle(owner.get(app.data_dir, third['sessionId'], third['id']), release=True)
    calls = []
    def invoke(config, home, row, action, args):
        if action == 'read':
            return {'ok': True, 'status': 'unknown', 'universeId': row['universeId']}
        calls.append(row['id'])
        return observation(row, 'unknown' if row['id'] == second['id'] else 'stopped')
    monkeypatch.setattr(owner, 'invoke', invoke)
    await app.dispatch('workspace.environments.read', scope(app))
    assert calls == [second['id']]
    await app.dispatch('workspace.environments.read', scope(app))
    assert calls == [second['id'], first['id']]
    assert host.snapshot()['reserved']['count'] == 1


@pytest.mark.parametrize('mode', ['explicit', 'read-unknown'])
def test_target_completed_launch_recovery_preserves_original_and_is_additive(target, monkeypatch, mode):
    value = prepared(target)
    value['expectedBackendIdentity'] = target[1]
    def effect(payload):
        if payload['action'] == 'start':
            return {'ok': True, 'status': 'running'}
        if payload['action'] == 'read':
            return {'ok': True, 'status': 'unknown'}
        proof = controller.worker.common.observation(payload, controller.worker.identity(payload), target[1], 'retained-launch', 'matched', True)
        return {'ok': True, 'status': 'stopped', 'observation': proof}
    monkeypatch.setattr(controller.worker, 'execute_call', effect)
    original = request(target, value, 'start')
    recovered = request(target, value, 'reconcile' if mode == 'explicit' else 'read')
    assert recovered['status'] == 'stopped' and host.snapshot()['reserved']['count'] == 0
    assert request(target, value, 'start') == original


def test_held_reservation_inspection_does_not_create_authority(machine):
    from test_test_host_resources import row
    assert not host.directory().exists()
    assert host.held(row()) is False
    assert not host.directory().exists()


async def test_saved_observation_before_interrupted_release_can_recover_without_effect_replay(app, native_backend, monkeypatch):
    row, original = await lost_check(app, monkeypatch)
    calls = inspect(monkeypatch)
    settle = host.settle
    def interrupted(*args, **kwargs):
        raise OSError('Interrupted before lease release')
    monkeypatch.setattr(host, 'settle', interrupted)
    await app.dispatch('workspace.environments.read', scope(app))
    assert host.snapshot()['reserved']['count'] == 1
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['reservationRecovery']['state'] == 'observed'
    monkeypatch.setattr(host, 'settle', settle)
    del app._last_reservation_probe
    await app.dispatch('workspace.environments.read', scope(app))
    assert host.snapshot()['reserved']['count'] == 0
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original
    assert calls == ['read', 'reconcile', 'read', 'reconcile']


def test_actual_docker_stopped_inventory_can_have_unsettled_launch(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web import test_environment_worker as worker
    backend = {'backend': 'docker', 'daemon': 'owned'}
    payload = {'root': str(tmp_path), 'rootOwner': 'session', 'operationId': 'launch',
               'dockerHost': 'unix:///owned.sock', 'admissionRoot': str(tmp_path / 'admission')}
    labels = {'com.docker.compose.project': 'universe', 'io.amplifier.root-owner': 'session',
              'io.amplifier.operation-id': 'launch'}
    def docker(command, **kwargs):
        return SimpleNamespace(stdout=json.dumps({'State': 'exited', 'Labels': labels}) if 'ps' in command else '')
    monkeypatch.setattr(worker.subprocess, 'run', docker)
    observed = worker.docker_reconcile(payload, 'universe', backend, 'retained-launch')
    assert observed['status'] == 'stopped' and observed['observation']['ownership'] == 'matched'
    assert observed['observation']['admissionSettled'] is False


def test_held_reservation_rejects_changed_saved_machine_or_resources(machine):
    from test_test_host_resources import row, reserve
    value = row()
    reserve(value)
    for field, changed in [('machineId', 'other'), ('resources', {'cpus': 500, 'memoryMiB': 4096})]:
        altered = {**value, 'hostReservation': {**value['hostReservation'], field: changed}}
        with pytest.raises(ValueError, match='another owner'):
            host.held(altered)
    assert host.snapshot()['reserved']['count'] == 1


# Use the real helper readers and sanitized protocol boundaries. Only external
# daemon wire responses and the initially lost effect reply are simulated.


@pytest.mark.parametrize('condition', ['absent', 'unavailable', 'pending', 'foreign'])
async def test_actual_docker_reader_recovers_absence_after_failed_owner_read(
        app, native_backend, library, monkeypatch, condition):
    from types import SimpleNamespace
    from amplifier_web import test_environment_worker as worker
    row, original = await lost_check(app, monkeypatch)
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    root = owner.storage.directory(app.data_dir, raw)
    value = {'root': str(root), 'source': raw['source'], 'dockerHost': 'unix:///fixture.sock',
             'rootOwner': raw['sessionId'], 'operationId': raw['launchOperationId'],
             'universeId': raw['universeId'], 'expectedBackendIdentity': raw['backendIdentity'],
             'admissionRoot': str(root / 'admission')}
    worker.retain_settlement(value, raw['universeId'], raw['backendIdentity'], 'removed')
    if condition == 'pending':
        worker.reserve_admission(value, {'scope': raw['backendIdentity']})
    monkeypatch.setattr(worker, 'docker_owner', real_docker_owner)
    monkeypatch.setattr(worker, 'docker_json', real_docker_json)
    def docker(command, **kwargs):
        if 'info' in command:
            return SimpleNamespace(stdout=json.dumps({'ID': raw['backendIdentity']['daemon']}))
        if condition == 'unavailable':
            raise worker.subprocess.CalledProcessError(1, command)
        if condition == 'foreign' and 'ps' in command:
            return SimpleNamespace(stdout=json.dumps({'State': 'exited', 'Labels': {
                'com.docker.compose.project': raw['universeId'],
                'io.amplifier.root-owner': 'foreign-owner',
                'io.amplifier.operation-id': raw['launchOperationId']}}))
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(worker.subprocess, 'run', docker)
    ordinary = removal_main(monkeypatch, {**value, 'action': 'read'})
    assert ordinary['ok'] is False and ordinary['status'] == 'unknown'
    assert host.snapshot()['reserved']['count'] == 1
    calls = []
    def invoke(config, home, current, action, args):
        assert current['id'] == raw['id'] and action in {'read', 'reconcile'}
        calls.append(action)
        return removal_main(monkeypatch, {**value, 'action': action})
    monkeypatch.setattr(owner, 'invoke', invoke)
    before = copy.deepcopy(app.operations.requests.list(row['sessionId']))
    result = (await app.dispatch('workspace.environments.read', scope(app)))['result']['items'][0]
    assert calls == ['read', 'reconcile']
    assert host.snapshot()['reserved']['count'] == (0 if condition == 'absent' else 1)
    saved = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert saved['status'] != 'retired' and result['lastOperation'] == original
    assert app.operations.requests.list(row['sessionId']) == before
    assert library[1] == [], 'No native lifecycle operation may be replayed'
    if condition == 'absent':
        assert result['status'] == 'removed'
        assert saved['reservationRecovery']['observation']['absenceConfirmed'] is True
        assert saved['reservationRecovery']['state'] == 'released'
    else:
        assert result['status'] == 'unknown' and 'reservationRecovery' not in saved


@pytest.mark.parametrize('condition', ['absent', 'unavailable', 'pending', 'foreign'])
def test_actual_target_reader_recovers_absence_after_failed_instance_read(target, monkeypatch, condition):
    worker = controller.worker
    value = prepared(target)
    value['expectedBackendIdentity'] = target[1]
    real_execute = worker.execute_call
    # The original start was admitted but its reply did not establish an outcome.
    monkeypatch.setattr(worker, 'execute_call', lambda payload: {'ok': False, 'status': 'unknown'})
    original = request(target, value, 'start')
    root = target[0] / ('owner-' + controller.digest(controller.owner(value)))
    effect = root / ('effect-' + controller.digest([value['requestId'], 'start']) + '.json')
    original_bytes = effect.read_bytes()
    monkeypatch.setattr(worker, 'execute_call', real_execute)
    monkeypatch.setattr(worker, 'validate_configuration', lambda config: config)
    monkeypatch.setattr(worker, 'environment', lambda config, root: {})
    name = worker.identity(value)
    queries = []
    def incus(config, env, args, *a, **kw):
        assert args[0] == 'query', 'Read recovery cannot mutate the backend'
        queries.append(args[1])
        if args[1] == '/1.0':
            result = {'environment': {'project': 'test', 'certificate_fingerprint': 'daemon-a'}}
        elif args[1] == '/1.0/instances':
            if condition == 'unavailable':
                return {'exit_code': 1, 'stdout': '', 'truncated': False}
            result = []
        else:
            assert args[1] == '/1.0/instances/' + name
            return {'exit_code': 1, 'stdout': '', 'truncated': False}
        return {'exit_code': 0, 'stdout': json.dumps(result), 'truncated': False}
    def cli(config, env, args, *a, **kw):
        assert args == ['launch-status', value['operationId']]
        return {'name': name, 'owner_root': 'foreign-owner' if condition == 'foreign' else value['rootOwner'],
                'request_id': value['operationId'], 'state': 'unknown' if condition == 'pending' else 'stopped',
                'instance_status': 'Stopped'}
    monkeypatch.setattr(worker, 'incus', incus)
    monkeypatch.setattr(worker, 'cli', cli)
    ordinary = real_execute({**value, 'root': str(root), 'incus': {'project': 'test'}, 'action': 'read'})
    assert ordinary['ok'] is False and ordinary['status'] == 'unknown'
    assert host.snapshot()['reserved']['count'] == 1
    queries.clear()
    result = request(target, value, 'read')
    assert '/1.0/instances' in queries, 'The failed read must still reach exact native reconciliation'
    assert host.snapshot()['reserved']['count'] == (0 if condition == 'absent' else 1)
    assert effect.read_bytes() == original_bytes
    assert request(target, value, 'start') == original
    if condition == 'absent':
        assert result['ok'] is True and result['status'] == 'removed' and result['universeId'] == name
        assert 'code' not in result and 'effectAdmitted' not in result
        proof = controller.read(root / 'reservation-recovery.json')
        assert proof['state'] == 'released' and proof['observation']['absenceConfirmed'] is True
    else:
        assert result['ok'] is False and result['status'] == 'unknown'
        assert not (root / 'reservation-recovery.json').exists()
