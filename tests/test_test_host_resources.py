"""Bounded native-host admission tests: no daemons, policies or live instances."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import struct
import time

import pytest

from amplifier_web import test_host_resources as host
from amplifier_web import test_environment_profiles as profiles
from amplifier_web import test_environment_preparation as preparation


@pytest.fixture
def machine(tmp_path, monkeypatch):
    target = {'id': 'machine-a'}
    monkeypatch.setattr(host, 'identity', lambda: target['id'])
    monkeypatch.setattr(host, 'directory', lambda: tmp_path / target['id'])
    observation = {'cpuCount': 16, 'memoryMiB': 65536, 'availableMemoryMiB': 60000,
                   'freeBytes': 100 * 1024**3, 'loadPercent': 20}
    monkeypatch.setattr(host, 'observe', lambda: copy.deepcopy(observation))
    return target, observation


def row(identity='one', backend='docker', profile='linux-python', preparing=False):
    return {'id': identity, 'sessionId': 'session-' + identity, 'launchOperationId': 'operation-' + identity,
            'backendKey': backend, 'resourceRequest': profiles.freeze(profile), 'preparationRequired': preparing}


def reserve(value):
    value['hostReservation'] = host.reserve(value, {'supported': True, 'machineId': host.identity()})
    return value['hostReservation']


def policy(**values):
    return host.save(host.snapshot()['revision'], values)


def test_account_authority_is_independent_of_home_xdg_and_installation(monkeypatch, tmp_path):
    import pwd
    monkeypatch.setattr(host, 'identity', lambda: 'physical-host')
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: type('User', (), {'pw_dir': str(tmp_path)})())
    monkeypatch.setenv('HOME', '/some/isolated/helper')
    monkeypatch.setenv('XDG_STATE_HOME', '/another/installation')
    assert host.directory() == tmp_path / '.local/state/amplifier-test-resources/physical-host'


def test_cross_backend_budget_and_machine_independence(machine):
    target, _ = machine
    policy(maxReservedCpu=2)
    first = row('first', 'docker-socket-alias-one')
    assert reserve(first)['admitted']
    assert not reserve(row('second', 'incus-project-two'))['admitted']
    # Same physical host across app data directories shares the policy; a
    # genuinely different observed host has its own independent authority.
    target['id'] = 'machine-b'
    assert reserve(row('third', 'docker-socket-alias-two'))['admitted']
    target['id'] = 'machine-a'
    assert host.snapshot()['reserved']['count'] == 1
    host.settle(first, release=True)
    assert reserve(row('fourth', 'incus'))['admitted']


def test_concurrent_admission_never_exceeds_shared_budget(machine):
    policy(maxReservedCpu=8)
    values = [row(str(i), 'incus' if i % 2 else 'docker') for i in range(20)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, values))
    assert sum(bool(result.get('admitted')) for result in results) == 4
    assert host.snapshot()['reserved']['cpus'] == 8
    # Bounded contention/throughput fixture, not a performance benchmark.
    assert time.monotonic() - started < 10
    for value in values:
        if value['hostReservation'].get('admitted'):
            host.settle(value, release=True)
    assert host.snapshot()['reserved']['count'] == 0
    assert reserve(row('replacement'))['admitted']


def test_lowering_preserves_active_unknown_and_exact_idempotency(machine):
    existing = row()
    receipt = reserve(existing)
    policy(maxReservedCpu=1)
    assert reserve(existing) == receipt
    assert not reserve(row('new'))['admitted']
    # Unknown reservations are never aged out. Only the exact owner settles.
    changed = {**existing, 'launchOperationId': 'another'}
    with pytest.raises(ValueError, match='another owner'):
        host.settle(changed, release=True)
    assert host.snapshot()['reserved']['count'] == 1
    host.settle(existing, release=True)
    host.settle(existing, release=True)
    assert host.snapshot()['reserved']['count'] == 0


def test_memory_cpu_disk_pressure_and_zero_overrides(machine):
    _, observed = machine
    observed.update(availableMemoryMiB=500, freeBytes=0, loadPercent=400)
    result = reserve(row())
    assert set(result['blockers']) == {'memoryPressure', 'diskPressure', 'cpuPressure'}
    policy(minAvailableMemoryMiB=0, minFreeBytes=0, maxLoadPercent=0,
           maxReservedCpu=0, maxReservedMemoryMiB=0, maxPreparing=0)
    assert reserve(row())['admitted']


def test_build_slots_release_on_confirmed_launch_not_time(machine):
    policy(maxPreparing=1)
    first = row('builder-one', preparing=True)
    assert reserve(first)['admitted']
    assert 'maxPreparing' in reserve(row('builder-two', preparing=True))['blockers']
    host.settle(first)  # Called only after canonical helper confirms launch.
    assert reserve(row('builder-two', preparing=True))['admitted']
    assert host.snapshot()['reserved']['count'] == 2


def test_default_preparation_uses_resource_reservations_not_a_two_build_cap(machine):
    values = [row('builder-' + str(i), profile='browser-test', preparing=True) for i in range(3)]
    assert all(reserve(value)['admitted'] for value in values)
    assert host.snapshot()['reserved']['preparing'] == 3
    # Pending allocations reserve memory before their usage is observable.
    machine[1]['availableMemoryMiB'] = 20000
    assert 'memoryPressure' in reserve(row('next', profile='browser-test', preparing=True))['blockers']


def test_unobserved_admissions_reserve_available_headroom(machine):
    _, observation = machine
    observation['availableMemoryMiB'] = 2500
    assert reserve(row('first'))['admitted']
    assert 'memoryPressure' in reserve(row('second'))['blockers']


def test_policy_compare_and_swap_and_validation(machine):
    old = host.snapshot()
    policy(maxReservedCpu=10)
    with pytest.raises(ValueError, match='another installation'):
        host.save(old['revision'], {'maxReservedCpu': 20})
    for values in ({'maxReservedCpu': -1}, {'maxReservedCpu': True}, {'madeUp': 5}, {'maxReservedCpu': 1.5}):
        with pytest.raises(ValueError):
            host.save(host.snapshot()['revision'], values)


def test_policy_and_lease_links_refused(machine, tmp_path):
    host.snapshot()
    target = tmp_path / 'outside.json'
    target.write_text('{}')
    (host.directory() / 'policy.json').symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        reserve(row())
    assert target.read_text() == '{}'


def test_remote_or_unsupported_target_never_consumes_local_authority(machine):
    assert not host.local_target('https://other-machine:8443', {'incusd'})['supported']
    assert not host.reserve(row(), {'supported': False})['supported']
    with pytest.raises(ValueError, match='machine changed'):
        host.reserve(row(), {'supported': True, 'machineId': 'other-machine'})
    assert host.snapshot()['reserved']['count'] == 0


def test_native_socket_aliases_share_host_and_proxies_do_not(monkeypatch, machine):
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def settimeout(self, _): pass
        def connect(self, _): pass
        def getsockopt(self, *_): return struct.pack('3i', 123, 1000, 1000)
    monkeypatch.setattr(socket, 'socket', lambda *_: Socket())
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **kw: 'dockerd' if str(self) == '/proc/123/comm' else original(self, *a, **kw))
    assert host.local_target('unix:///one.sock', {'dockerd'}) == host.local_target('unix:///two.sock', {'dockerd'})
    assert not host.local_target('unix:///one.sock', {'incusd'})['supported']


def test_non_linux_settings_report_unsupported_without_posix_access(monkeypatch):
    monkeypatch.setattr(host, 'identity', lambda: None)
    monkeypatch.setattr(host, 'directory', lambda: pytest.fail('must not create native authority'))
    assert host.snapshot()['supported'] is False


def test_resource_request_remains_frozen_across_profile_change(monkeypatch):
    frozen = profiles.freeze('browser-test')
    changed = copy.deepcopy(profiles._PROFILES)
    changed['browser-test']['memoryMiB'] = 1024
    monkeypatch.setattr(profiles, '_PROFILES', changed)
    assert profiles.resources('browser-test', frozen)['memoryMiB'] == 6144
    assert profiles.resources('browser-test')['memoryMiB'] == 1024
    with pytest.raises(ValueError, match='changed'):
        profiles.resources('browser-test', {**frozen, 'memoryMiB': 8192})


def test_incus_memory_uses_target_not_controller(tmp_path, monkeypatch):
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **kw: 'MemTotal: 2097152 kB\nMemAvailable: 100000 kB' if str(self) == '/proc/meminfo' else original(self, *a, **kw))
    monkeypatch.setattr(preparation, '_environment', lambda *_: {})
    monkeypatch.setattr(preparation, 'run', lambda *a, **kw: json.dumps({'memory': {'total': 64*1024**3, 'used': 1024**3}}))
    assert preparation.memory_available({'backend': 'incus', 'incus': {'incusExecutable': '/bin/incus', 'project': 'default'}}, tmp_path, 'browser-test')

# Full action/receipt integration uses the repository's isolated app and fake
# backend. The real allocation and operation ledgers remain authoritative.
from test_configuration_authority import app
from test_task_environments import backend, start, act, scope
from test_test_queue import queue, submit, drive, record
from amplifier_web import task_environments as environments


@pytest.fixture
def native_backend(backend, monkeypatch, machine):
    original = environments.invoke
    def invoke(config, home, value, action, args):
        result = original(config, home, value, action, args)
        if action == 'prepare':
            result['hostTarget'] = {'supported': True, 'machineId': host.identity()}
        return result
    monkeypatch.setattr(environments, 'invoke', invoke)
    return backend


async def test_shared_policy_save_wakes_queue_and_preserves_allocation(app, queue, native_backend):
    result = await start(app)
    assert host.snapshot()['reserved']['count'] == 1
    before = environments.get(app.data_dir, scope(app)['sessionId'], result['resource']['id'])
    current = (await app.dispatch('testing.machine.read', {}))['result']
    queue.backoff['test'] = 100
    await app.dispatch('testing.machine.save', {'expectedRevision': current['revision'], 'limits': {'maxReservedCpu': 1}})
    assert not queue.backoff and queue.wakeup.is_set()
    assert environments.get(app.data_dir, scope(app)['sessionId'], before['id']) == before
    stopped = await act(app, 'stop', result['resource'], 'confirmed-stop')
    assert stopped['request']['state'] == 'completed'
    assert host.snapshot()['reserved']['count'] == 0


async def test_queue_waits_then_uses_frozen_request_after_budget_increase(app, queue, native_backend, monkeypatch):
    policy(maxReservedCpu=1)
    await submit(app)
    frozen = copy.deepcopy(record(queue, app)['job']['resourceRequest'])
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'queued'
    assert not any(action == 'start' for action, _ in native_backend)
    changed = copy.deepcopy(profiles._PROFILES)
    changed['linux-python']['cpus'] = 20
    monkeypatch.setattr(profiles, '_PROFILES', changed)
    policy(maxReservedCpu=2)
    queue.backoff.clear()
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    allocation = environments.get(app.data_dir, scope(app)['sessionId'], record(queue, app)['job']['resourceId'])
    assert allocation['resourceRequest'] == frozen and allocation['status'] == 'retired'
    assert host.snapshot()['reserved']['count'] == 0


async def test_unknown_launch_keeps_reservation_until_exact_stop(app, native_backend, monkeypatch):
    original = environments.invoke
    def lost(config, home, value, action, args):
        if action == 'start':
            raise TimeoutError('Lost launch acknowledgement')
        return original(config, home, value, action, args)
    monkeypatch.setattr(environments, 'invoke', lost)
    result = await start(app)
    assert result['request']['state'] == 'outcome_unknown'
    assert host.snapshot()['reserved']['count'] == 1
    duplicate = await start(app)
    assert duplicate['request'] == result['request']
    stopped = await act(app, 'stop', result['resource'], 'stop-exact-owner')
    assert stopped['request']['state'] == 'completed'
    assert host.snapshot()['reserved']['count'] == 0


async def test_declined_host_admission_does_not_prepare_dependencies(app, native_backend, monkeypatch):
    policy(maxReservedCpu=1)
    monkeypatch.setattr(environments.dependencies, 'prepare', lambda *a, **kw: pytest.fail('builder must not start'))
    result = (await app.dispatch('workspace.environments.start', {**scope(app), 'profileId': 'browser-test', 'requestId': 'big'}, command_id='outer-big'))['result']
    assert result['request']['result']['admissionDenied']
    assert [action for action, _ in native_backend] == ['prepare']
    assert host.snapshot()['reserved']['count'] == 0


def test_commit_gap_reservation_released_only_after_owner_confirmation(machine):
    value = row('gap')
    receipt = host.reserve(value, {'supported': True, 'machineId': host.identity()})
    assert receipt['admitted'] and 'hostReservation' not in value
    assert host.snapshot()['reserved']['count'] == 1
    host.settle(value, release=True)
    assert host.snapshot()['reserved']['count'] == 0


def test_incus_target_observation_without_controller_proc(tmp_path, monkeypatch):
    original = Path.exists
    monkeypatch.setattr(Path, 'exists', lambda self: False if str(self) == '/proc/meminfo' else original(self))
    monkeypatch.setattr(preparation, '_environment', lambda *_: {})
    monkeypatch.setattr(preparation, 'run', lambda *a, **kw: json.dumps({'memory': {'total': 64*1024**3, 'used': 63*1024**3}}))
    assert not preparation.memory_available({'backend': 'incus', 'incus': {'incusExecutable': '/bin/incus', 'project': 'default'}}, tmp_path, 'browser-test')


async def test_agent_machine_policy_changes_require_global_authority(app, machine):
    from test_configuration_authority import grant
    from test_coordination_generation import agent
    from amplifier_web.service import AppError
    source = app.state['sessions'][0]
    current = (await app.dispatch('testing.machine.read', {}))['result']
    args = {'expectedRevision': current['revision'], 'limits': {'maxReservedCpu': 12}}
    with pytest.raises(AppError, match='authority'):
        await agent(app, source, 'testing.machine.save', args, 'without-grant')
    await grant(app, ['host_defaults'], ['global'])
    assert (await agent(app, source, 'testing.machine.save', args, 'with-grant'))['accepted']


async def test_machine_reservation_is_retained_before_dependency_builder(app, native_backend, monkeypatch):
    def prepare(config, root, snapshot, profile_id, request_id, *, resource_request, host_reservation, timing_request, dependency_store):
        assert dependency_store == app.data_dir / 'test-dependency-layers'
        assert host_reservation['admitted'] and host_reservation['phase'] == 'preparing'
        assert host.snapshot()['reserved']['preparing'] == 1
        assert profiles.resources(profile_id, resource_request)['memoryMiB'] == 6144
        assert profiles.timing(profile_id, timing_request)['preparationTimeoutSeconds'] > 0
        return {'fixture': 'prepared'}
    monkeypatch.setattr(environments.dependencies, 'prepare', prepare)
    result = (await app.dispatch('workspace.environments.start', {**scope(app), 'profileId': 'browser-test', 'requestId': 'prepared'}, command_id='outer-prepared'))['result']
    assert result['request']['state'] == 'completed'
    assert host.snapshot()['reserved']['preparing'] == 0
    assert host.snapshot()['reserved']['memoryMiB'] == 6144
    await act(app, 'stop', result['resource'], 'stop-prepared')
    assert host.snapshot()['reserved']['count'] == 0


def test_systemd_activation_requires_exact_socket_service_and_daemon(monkeypatch, tmp_path):
    from types import SimpleNamespace
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
    from types import SimpleNamespace
    socket = tmp_path / 'incus.socket'; socket.touch()
    pids = iter(['42', '43'])
    def run(args, **kwargs):
        field = args[3].split('=')[1]
        value = str(socket) + ' (Stream)' if field == 'Listen' else 'incus.service' if field == 'Triggers' else next(pids)
        return SimpleNamespace(returncode=0, stdout=value.encode())
    monkeypatch.setattr(host.subprocess, 'run', run)
    monkeypatch.setattr(Path, 'read_text', lambda p: 'incusd\n')
    assert not host.activated_daemon(socket, {'incusd'})


def test_incus_local_target_accepts_both_single_endpoint_formats(monkeypatch):
    from amplifier_web import incus_environment_worker as worker
    endpoint = {'Addr': 'unix://'}
    def run(args, env, **kwargs):
        content = 'local\n' if 'get-default' in args else json.dumps({'local': endpoint})
        return {'exit_code': 0, 'stdout': content, 'stderr': '', 'truncated': False}
    monkeypatch.setattr(worker, 'run', run)
    monkeypatch.setattr(worker.common.host_resources, 'local_target', lambda value, names: {'supported': value == 'unix://'})
    assert worker.host_target({'incusExecutable': '/usr/bin/incus'}, {})['supported']
    endpoint.clear(); endpoint['Addrs'] = ['unix://']
    assert worker.host_target({'incusExecutable': '/usr/bin/incus'}, {})['supported']
    endpoint['Addrs'].append('https://another-machine')
    assert not worker.host_target({'incusExecutable': '/usr/bin/incus'}, {})['supported']
    endpoint['Addr'] = 'unix://'
    assert not worker.host_target({'incusExecutable': '/usr/bin/incus'}, {})['supported']
    endpoint['Addrs'] = ['https://another-machine']
    assert not worker.host_target({'incusExecutable': '/usr/bin/incus'}, {})['supported']
    endpoint['Addrs'] = ['unix://']
    assert worker.host_target({'incusExecutable': '/usr/bin/incus'}, {})['supported']
    endpoint['addr'] = 'https://another-machine'
    assert not worker.host_target({'incusExecutable': '/usr/bin/incus'}, {})['supported']
