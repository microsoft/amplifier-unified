"""Registered target admission and dispatch fences, without live machine effects."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('fcntl', reason='Registered target controller is native Linux only')

from amplifier_web import registered_test_runners as client
from amplifier_web import test_runner_controller as controller
from amplifier_web import test_host_resources as host
from amplifier_web import test_environment_profiles as profiles


@pytest.fixture
def target(tmp_path, monkeypatch):
    directory = tmp_path / 'target'
    directory.mkdir(mode=0o700)
    identity = {'backend': 'incus', 'daemon': 'daemon-a', 'project': 'test'}
    monkeypatch.setattr(controller, 'state', lambda: directory)
    controller.write(directory / 'authority.json', {'version': 1, 'id': 'a'*32})
    monkeypatch.setattr(controller, 'configuration', lambda: {'project': 'test'})
    monkeypatch.setattr(controller, 'backend', lambda config, root: (identity, {'supported': True, 'machineId': 'machine-a'}))
    monkeypatch.setattr(host, 'identity', lambda: 'machine-a')
    monkeypatch.setattr(host, 'directory', lambda: tmp_path / 'machine')
    monkeypatch.setattr(host, 'observe', lambda: {'cpuCount': 8, 'memoryMiB': 32768, 'availableMemoryMiB': 30000, 'freeBytes': 100 * 1024**3, 'loadPercent': 5})
    return directory, identity


def payload(identity='one'):
    content = b'frozen source archive'
    return {'rootOwner': 'session-' + identity, 'resourceId': identity, 'operationId': 'launch-' + identity,
            'backendKey': 'origin-config', 'requestId': 'start-' + identity,
            'profileId': 'linux-python', 'resourceRequest': profiles.freeze('linux-python'),
            'snapshot': {'archive_sha256': hashlib.sha256(content).hexdigest()}}, content


def request(target, value, action, **extra):
    return controller.dispatch({'version': 1, 'machineId': 'a'*32, 'backendIdentity': target[1], 'payload': {**value, 'action': action, **extra}})


def prepared(target, identity='one'):
    value, content = payload(identity)
    assert request(target, value, 'source', offset=0, content=base64.b64encode(content).decode())['ok']
    assert request(target, value, 'prepare')['ok']
    return value


def test_source_chunks_retry_exact_bytes_and_reject_conflicting_or_incomplete_source(target):
    value, content = payload()
    result = request(target, value, 'source', offset=0, content=base64.b64encode(content[:5]).decode())
    assert result['offset'] == 5
    assert request(target, value, 'source', offset=0, content=base64.b64encode(content[:5]).decode()) == result
    with pytest.raises(ValueError, match='complete frozen source'):
        request(target, value, 'prepare')
    with pytest.raises(ValueError, match='conflicts'):
        request(target, value, 'source', offset=0, content=base64.b64encode(b'wrong').decode())
    request(target, value, 'source', offset=5, content=base64.b64encode(content[5:]).decode())
    assert request(target, value, 'prepare')['ok']


def test_target_fence_precedes_actual_create_and_duplicate_returns_receipt(target, monkeypatch):
    value = prepared(target)
    effects = []
    def effect(p):
        receipts = list(Path(p['root']).glob('effect-*.json'))
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text())['state'] == 'admitted'
        assert host.snapshot()['reserved']['count'] == 1
        effects.append(p['action'])
        return {'ok': True, 'status': 'running', 'universeId': 'owned'}
    monkeypatch.setattr(controller.worker, 'execute_call', effect)
    first = request(target, value, 'start')
    assert request(target, value, 'start') == first
    assert effects == ['start']
    assert host.snapshot()['reserved']['count'] == 1


def test_unknown_create_never_replays_or_releases_on_absent_inventory(target, monkeypatch):
    value = prepared(target)
    effects = []
    def effect(p):
        effects.append(p['action'])
        if p['action'] == 'start':
            raise TimeoutError('Reply lost after create')
        return {'ok': True, 'status': 'absent'}
    monkeypatch.setattr(controller.worker, 'execute_call', effect)
    with pytest.raises(TimeoutError):
        request(target, value, 'start')
    assert request(target, value, 'start')['status'] == 'unknown'
    assert request(target, value, 'reconcile')['status'] == 'absent'
    assert effects == ['start', 'reconcile']
    assert host.snapshot()['reserved']['count'] == 1


def test_confirmed_stop_releases_and_repeated_launch_receipt_does_not_reacquire(target, monkeypatch):
    value = prepared(target)
    monkeypatch.setattr(controller.worker, 'execute_call', lambda p: {'ok': True, 'status': 'running' if p['action'] == 'start' else 'stopped'})
    request(target, value, 'start')
    request(target, value, 'stop', requestId='stop-one')
    assert host.snapshot()['reserved']['count'] == 0
    assert request(target, value, 'start')['status'] == 'running'
    assert host.snapshot()['reserved']['count'] == 0


def test_aliases_and_origin_configs_share_one_machine_budget(target, monkeypatch):
    host.save(host.snapshot()['revision'], {'maxReservedCpu': 2})
    first, second = prepared(target, 'first'), prepared(target, 'second')
    second['backendKey'] = 'other-origin-alias'
    # Owner binding must remain immutable; establish second under its own config.
    third = prepared(target, 'third')
    monkeypatch.setattr(controller.worker, 'execute_call', lambda p: {'ok': True, 'status': 'running'})
    assert request(target, first, 'start')['ok']
    result = request(target, third, 'start')
    assert result['admissionDenied'] and 'maxReservedCpu' in result['machineResources']['blockers']
    assert not list((target[0] / ('owner-' + controller.digest(controller.owner(third)))).glob('effect-*.json'))


def test_different_machine_or_unregistered_operation_is_rejected_before_effect(target):
    value = prepared(target)
    with pytest.raises(ValueError, match='changed'):
        controller.dispatch({'version': 1, 'machineId': 'other-machine', 'backendIdentity': target[1], 'payload': {**value, 'action': 'start'}})
    with pytest.raises(ValueError, match='Unsupported'):
        request(target, value, 'shell', command='anything')
    with pytest.raises(ValueError, match='Unsupported'):
        request(target, value, 'start', executable='/tmp/not-approved')


def test_controller_package_verification_checks_exact_source_bytes():
    package = client.bundle()
    assert package['digest'] == client.digest(package['files'])
    assert 'test_runner_controller.py' in client.FILES
    assert "hashlib.sha256" in client.VERIFY
    # Actual source-only loading and bootstrap behavior are exercised by
    # test_registered_runner_bootstrap, including executable cache injection.


def test_connection_options_and_arbitrary_controller_path_are_refused():
    with pytest.raises(ValueError, match='SSH connection'):
        client.ssh('-oProxyCommand=bad', [], {})
    with pytest.raises(ValueError, match='path changed'):
        client.call({'packageDigest': 'a'*64, 'controller': '/tmp/other.py'}, {'action': 'capacity'})


def test_read_only_routing_selects_free_machine_and_keeps_frozen_request(tmp_path, monkeypatch):
    from amplifier_web import test_environment_configs as configs
    from amplifier_web import task_environments as environments
    app = SimpleNamespace(data_dir=tmp_path)
    local = {'backend': 'incus', 'incus': {}}
    remote = {'backend': 'incus', 'incus': {}, 'remoteRunner': {'id': 'target', 'name': 'Second machine'}}
    keys = {configs.retain(tmp_path, local): local, configs.retain(tmp_path, remote): remote}
    monkeypatch.setattr(configs, 'resolve', lambda app, row, active: keys[row['backendKey']])
    monkeypatch.setattr(client, 'enabled', lambda app, config: True)
    monkeypatch.setattr(environments, 'capacity', lambda app, config: {'available': False})
    observed = {'supported': True, 'effective': {'maxReservedCpu': 4, 'maxReservedMemoryMiB': 8192, 'minAvailableMemoryMiB': 1024, 'minFreeBytes': 1024, 'maxLoadPercent': 200}, 'reserved': {'cpus': 0, 'memoryMiB': 0}, 'observed': {'availableMemoryMiB': 16000, 'freeBytes': 10000, 'loadPercent': 10}}
    calls = []
    monkeypatch.setattr(client, 'call', lambda runner, p, timeout=60: calls.append(p) or {'ok': True, 'available': True, 'machineResources': observed})
    job = {'runnerCandidates': list(keys), 'resourceRequest': profiles.freeze('linux-python')}
    original = copy.deepcopy(job)
    assert client.select(app, job, local) == remote
    assert calls == [{'action': 'capacity'}] and job == original
    observed['reserved']['cpus'] = 4
    assert client.select(app, job, local) is None


def test_global_setup_and_remote_machine_settings_require_host_authority():
    from amplifier_web import configuration_authority as authority
    for action in ('testing.runners.register', 'testing.runners.enable', 'testing.runners.machine.save'):
        # Same explicit action category as other global resource settings.
        assert authority.requirement(action, {}) == ('host_defaults', 'global')


def test_systemd_activation_requires_exact_socket_service_and_daemon(monkeypatch, tmp_path):
    socket = tmp_path / 'incus.socket'
    socket.touch()
    alias = tmp_path / 'alias'; alias.symlink_to(socket)
    values = {('incus.socket', 'Listen'): str(socket) + ' (Stream)', ('incus.socket', 'Triggers'): 'incus.service', ('incus.service', 'MainPID'): '42'}
    def run(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=values[(args[2], args[3].split('=')[1])].encode())
    monkeypatch.setattr(host.subprocess, 'run', run)
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw: 'incusd\n' if str(p) == '/proc/42/comm' else original(p, *a, **kw))
    assert host.activated_daemon(alias, {'incusd'})
    assert not host.activated_daemon(tmp_path / 'other.socket', {'incusd'})
    values['incus.socket', 'Triggers'] = 'socket-proxy.service'
    assert not host.activated_daemon(alias, {'incusd'})


def test_systemd_daemon_restart_during_proof_is_not_accepted(monkeypatch, tmp_path):
    socket = tmp_path / 'incus.socket'; socket.touch()
    pids = iter(['42', '43'])
    def run(args, **kwargs):
        field = args[3].split('=')[1]
        value = str(socket) + ' (Stream)' if field == 'Listen' else 'incus.service' if field == 'Triggers' else next(pids)
        return SimpleNamespace(returncode=0, stdout=value.encode())
    monkeypatch.setattr(host.subprocess, 'run', run)
    monkeypatch.setattr(Path, 'read_text', lambda p: 'incusd\n')
    assert not host.activated_daemon(socket, {'incusd'})


def test_cloned_machine_ids_have_distinct_account_authorities_and_alias_reuse(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    first = tmp_path / 'first'; first.mkdir(mode=0o700)
    second = tmp_path / 'second'; second.mkdir(mode=0o700)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: controller.authority_id(first, create=True), range(8)))
    assert len(set(values)) == 1
    assert controller.authority_id(second, create=True) != values[0]
    assert controller.authority_id(first) == values[0]


@pytest.mark.parametrize("lost_reply", ["raised", "returned"])
def test_reconcile_exact_terminal_launch_settles_fence_without_replay(target, monkeypatch, lost_reply):
    value = prepared(target)
    value['expectedBackendIdentity'] = target[1]
    effects = []
    def effect(p):
        effects.append(p['action'])
        if p['action'] == 'start':
            if lost_reply == 'returned':
                return {'ok': False, 'status': 'unknown', 'effectAdmitted': True}
            raise TimeoutError('reply lost')
        observation = controller.worker.common.observation(p, controller.worker.identity(p), target[1], 'retained-launch', 'matched', True)
        return {'ok': True, 'status': 'stopped', 'observation': observation}
    monkeypatch.setattr(controller.worker, 'execute_call', effect)
    if lost_reply == 'raised':
        with pytest.raises(TimeoutError):
            request(target, value, 'start')
    else:
        assert request(target, value, 'start')['status'] == 'unknown'
    assert host.snapshot()['reserved']['count'] == 1
    assert request(target, value, 'reconcile')['status'] == 'stopped'
    assert host.snapshot()['reserved']['count'] == 0
    settled = request(target, value, 'start')
    assert settled['creationSettled'] and not settled['ok']
    assert effects == ['start', 'reconcile']


def test_cancellation_marker_reaches_builder_while_effect_lock_is_held(target):
    value = prepared(target)
    root = target[0] / ('owner-' + controller.digest(controller.owner(value)))
    with controller.locked(root):
        result = request(target, value, 'cancelPreparation')
        assert result['status'] == 'cancellation-requested'
        assert controller.preparation.cancelled(root / 'preparation')
    assert host.snapshot()['reserved']['count'] == 0
    with pytest.raises(ValueError, match='exact original'):
        request(target, {**value, 'snapshot': {'archive_sha256': 'b'*64}}, 'cancelPreparation')


def test_frozen_preparation_deadline_controls_target_and_transport(target, monkeypatch, tmp_path):
    value, content = payload()
    value.update(profileId='python-project', resourceRequest=profiles.freeze('python-project'), timingRequest=profiles.freeze_timing('python-project', {'preparationTimeoutSeconds': 4000}))
    request(target, value, 'source', offset=0, content=base64.b64encode(content).decode())
    request(target, value, 'prepare')
    seen = []
    def prepare(*args, **kwargs):
        seen.append(kwargs['timing_request'])
        return {'status': 'ready'}
    monkeypatch.setattr(controller.preparation, 'prepare', prepare)
    assert request(target, value, 'prepareDependencies')['ok']
    assert seen == [value['timingRequest']]
    monkeypatch.setattr(client, 'call', lambda runner, payload, timeout: {'ok': True, 'timeout': timeout})
    result = client.invoke({'remoteRunner': {}}, tmp_path, {**value, 'action': 'prepareDependencies'})
    assert result['timeout'] == 4200
