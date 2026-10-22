import asyncio
import pytest
from amplifier_web import registered_test_runners as runners
from amplifier_web import task_environments as environments
from test_test_queue import app, queue, backend, submit, record

REAL_CONFIGURATION = environments.configuration
from amplifier_web.test_runner_routing import RoutingPass

@pytest.mark.parametrize('ceiling', ['backend-count', 'retained-count', 'machine-cpu', 'machine-memory', 'memory-headroom', 'build'])
async def test_one_pass_uses_second_free_approved_machine(app, queue, backend, monkeypatch, ceiling):
    first = {'id': 'first', 'name': 'First', 'enabled': True, 'connection': 'fixture-first', 'configPath': '',
        'machineId': 'a'*32, 'profiles': ['linux-python', 'python-project'], 'incus': {'project': 'first'}}
    second = {**first, 'id': 'second', 'name': 'Second', 'connection': 'fixture-second',
        'machineId': 'b'*32, 'incus': {'project': 'second'}}
    runners.publish(app, {'version': 1, 'runners': [first, second]})
    monkeypatch.setattr(environments, 'configuration', REAL_CONFIGURATION)
    calls = []
    profile = 'python-project' if ceiling == 'build' else 'linux-python'
    def capacity(target, payload, timeout=60):
        assert payload == {'action': 'capacity'}
        calls.append(target['id'])
        return {'ok': True, 'available': True,
            'capacity': {'limits': {'max_running': 1 if ceiling == 'backend-count' else 0, 'max_instances': 1 if ceiling == 'retained-count' else 0}, 'counts': {'active': 0}},
            'machineResources': {'supported': True, 'machineId': target['machineId'],
                'effective': {'maxReservedCpu': 2 if ceiling == 'machine-cpu' else 0, 'maxReservedMemoryMiB': 1024 if ceiling == 'machine-memory' else 0, 'minAvailableMemoryMiB': 1024,
                    'minFreeBytes': 1024, 'maxLoadPercent': 200, 'maxPreparing': 1 if ceiling == 'build' else 0},
                'reserved': {'cpus': 0, 'memoryMiB': 0, 'pendingMemoryMiB': 0, 'count': 0},
                'observed': {'availableMemoryMiB': 2048 if ceiling == 'memory-headroom' else 16000, 'freeBytes': 10000, 'loadPercent': 10}}}
    monkeypatch.setattr(runners, 'call', capacity)
    release = asyncio.Event()
    async def no_effect_run(*args):
        await release.wait()
    monkeypatch.setattr(queue, 'run', no_effect_run)
    await submit(app, 'first-check', independent=True, profileId=profile)
    await submit(app, 'second-check', independent=True, profileId=profile)
    try:
        await queue.tick()
        saved = [record(queue, app, name)['job'] for name in ('first-check','second-check')]
        chosen = [value.get('testMachine',{}).get('id') for value in saved]
        assert chosen == ['first', 'second'], {'chosen': chosen, 'observed': calls, 'scheduled': len(queue.tasks)}
        assert len(queue.tasks) == 2
        assert not backend
    finally:
        release.set()
        await asyncio.gather(*queue.tasks.values())


def observation(**limits):
    return {'available': True, 'limits': {'max_running': 0}, 'counts': {'active': 0},
        'machineResources': {'supported': True, 'machineId': 'cloned-os-id',
            'effective': {'maxReservedCpu': 2, 'maxReservedMemoryMiB': 0, 'minAvailableMemoryMiB': 1024,
                'minFreeBytes': 1024, 'maxLoadPercent': 200, 'maxPreparing': 0, **limits},
            'reserved': {'cpus': 0, 'memoryMiB': 0, 'pendingMemoryMiB': 0, 'count': 0},
            'observed': {'availableMemoryMiB': 16000, 'freeBytes': 10000, 'loadPercent': 10}}}


def test_pass_debits_shared_target_account_across_projects_but_not_cloned_machines():
    from copy import deepcopy
    from amplifier_web.test_environment_profiles import freeze
    request = freeze('linux-python')
    first = {'remoteRunner': {'machineId': 'authority-a', 'accountId': 'account', 'backendIdentity': {'daemon': 'daemon', 'project': 'one'}}}
    alias = deepcopy(first)
    alias['remoteRunner']['backendIdentity']['project'] = 'two'
    other = deepcopy(first)
    other['remoteRunner']['machineId'] = 'authority-b'
    observed = observation()
    original = deepcopy(observed)
    batch = RoutingPass()
    assert batch.room('one', first, observed, request)
    batch.scheduled('one', first, observed, request)
    assert not batch.room('two', alias, observed, request)
    assert batch.room('other', other, observed, request)
    assert observed == original  # Planned demand is never presented as an actual observation.


def test_pass_backend_count_is_shared_by_known_aliases_without_machine_authority():
    from amplifier_web.test_environment_profiles import freeze
    request = freeze('linux-python')
    observed = {'available': True, 'scope': {'backend': 'docker', 'daemon': 'same-daemon'},
        'limits': {'max_running': 1}, 'counts': {'active': 0}}
    batch = RoutingPass()
    batch.scheduled('socket-one', {}, observed, request)
    assert not batch.room('socket-two', {}, observed, request)


@pytest.mark.parametrize('change', ['disk', 'load', 'unknown', 'preparing'])
def test_physical_pressure_and_existing_preparation_still_hold(change):
    from amplifier_web.test_environment_profiles import freeze
    observed = observation()
    batch = RoutingPass({'key': 1} if change == 'preparing' else {})
    if change == 'disk':
        observed['machineResources']['observed']['freeBytes'] = 1023
    elif change == 'load':
        observed['machineResources']['observed']['loadPercent'] = 201
    elif change == 'unknown':
        observed['available'] = False
    else:
        observed['limits']['max_running'] = 1
    assert not batch.room('key', {}, observed, freeze('linux-python'))


def test_zero_ceilings_remain_disabled_and_running_memory_is_not_double_counted():
    from amplifier_web.test_environment_profiles import freeze
    request = freeze('linux-python')
    observed = observation(maxReservedCpu=0, maxReservedMemoryMiB=0, minAvailableMemoryMiB=0,
                           minFreeBytes=0, maxLoadPercent=0, maxPreparing=0)
    observed['machineResources']['reserved'].update(cpus=1000, memoryMiB=100000)
    observed['machineResources']['observed']['availableMemoryMiB'] = 0
    batch = RoutingPass()
    assert batch.room('key', {}, observed, request)
    batch.scheduled('key', {}, observed, request)
    assert batch.room('key', {}, observed, request)
    observed['machineResources']['effective']['minAvailableMemoryMiB'] = 1024
    observed['machineResources']['observed']['availableMemoryMiB'] = 3072
    # Existing running memory is already represented by MemAvailable. Only this
    # pass's one not-yet-reserved request is added to pending memory.
    headroom = RoutingPass()
    headroom.scheduled('key', {}, observed, request)
    assert headroom.room('key', {}, observed, request)
    observed['machineResources']['observed']['availableMemoryMiB'] = 3071
    lower = RoutingPass()
    lower.scheduled('key', {}, observed, request)
    assert not lower.room('key', {}, observed, request)


async def test_no_room_leaves_waiting_job_unbound(app, queue, backend, monkeypatch):
    first = {'id': 'first', 'name': 'First', 'enabled': True, 'connection': 'fixture-first', 'configPath': '',
        'machineId': 'a'*32, 'profiles': ['linux-python'], 'incus': {'project': 'first'}}
    runners.publish(app, {'version': 1, 'runners': [first]})
    monkeypatch.setattr(environments, 'configuration', REAL_CONFIGURATION)
    monkeypatch.setattr(runners, 'call', lambda *args, **kw: {'ok': True, 'available': False})
    await submit(app, independent=True)
    await queue.tick()
    saved = record(queue, app)['job']
    assert saved['status'] == 'queued' and not saved.get('targetBound')
    assert not queue.tasks and not backend


async def test_existing_bound_waiter_keeps_exact_target(app, queue, backend, monkeypatch):
    first = {'id': 'first', 'name': 'First', 'enabled': True, 'connection': 'fixture-first', 'configPath': '',
        'machineId': 'a'*32, 'profiles': ['linux-python'], 'incus': {'project': 'first'}}
    second = {**first, 'id': 'second', 'machineId': 'b'*32, 'incus': {'project': 'second'}}
    runners.publish(app, {'version': 1, 'runners': [first, second]})
    monkeypatch.setattr(environments, 'configuration', REAL_CONFIGURATION)
    await submit(app, independent=True)
    saved = record(queue, app)
    saved['job']['targetBound'] = True
    saved['job']['testMachine'] = {key: first[key] for key in ('id','name','machineId')}
    queue.save(saved)
    monkeypatch.setattr(environments, 'capacity', lambda *args: {'available': False})
    monkeypatch.setattr(runners, 'call', lambda *args, **kw: pytest.fail('A retained owner cannot be moved'))
    await queue.tick()
    assert record(queue, app)['job']['testMachine']['id'] == 'first'
    assert not queue.tasks and not backend


def test_later_project_observation_does_not_double_count_this_pass():
    from copy import deepcopy
    from amplifier_web.test_environment_profiles import freeze
    request = freeze('linux-python')
    config = {'remoteRunner': {'machineId': 'authority-a', 'accountId': 'account',
        'backendIdentity': {'daemon': 'daemon', 'project': 'one'}}}
    other = deepcopy(config)
    other['remoteRunner']['backendIdentity']['project'] = 'two'
    initial = observation(maxReservedCpu=4)
    batch = RoutingPass()
    batch.scheduled('one', config, initial, request)
    later = deepcopy(initial)
    later['machineResources']['reserved'].update(cpus=2, memoryMiB=1024, pendingMemoryMiB=1024)
    assert batch.room('two', other, later, request)
    batch.scheduled('two', other, later, request)
    assert not batch.room('three', other, later, request)


async def test_historical_request_without_frozen_resources_keeps_original_profile(app, queue, backend):
    await submit(app)
    saved = record(queue, app)
    saved['job'].pop('resourceRequest')
    queue.save(saved)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values())
    result = record(queue, app)
    assert result['job']['status'] == 'passed'
    assert 'resourceRequest' not in result['job']
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']


@pytest.mark.parametrize('phase', ['admitted', 'preparing'])
async def test_prior_pass_start_counts_only_unadmitted_demand_against_retained_limit(app, queue, backend, monkeypatch, phase):
    """A delayed start reply remains starting after the backend counts its owner."""
    import threading
    from test_coordination_generation import until
    invoke, run = environments.invoke, queue.run
    entered, release_native = threading.Event(), threading.Event()
    release_second = asyncio.Event()
    held_action = 'start' if phase == 'admitted' else 'prepare'

    def held(config, home, row, action, args):
        if action == held_action:
            # The admitted case has really passed the host admission boundary
            # and reached the fake native start. Preparation has not.
            result = invoke(config, home, row, action, args) if phase == 'admitted' else None
            entered.set()
            assert release_native.wait(10)
            return result if result is not None else invoke(config, home, row, action, args)
        return invoke(config, home, row, action, args)

    async def hold_second(identity, config):
        if identity == queue.identity(app.state['sessions'][0]['id'], 'next'):
            await release_second.wait()
        else:
            await run(identity, config)

    def capacity(*args):
        # Once first is held, there is one observed native owner. In the
        # preparation case that owner is unrelated: first still needs a slot.
        return {'available': True, 'limits': {'max_running': 0, 'max_instances': 2},
                'counts': {'active': int(entered.is_set()), 'total': int(entered.is_set())}}

    monkeypatch.setattr(environments, 'invoke', held)
    monkeypatch.setattr(environments, 'capacity', capacity)
    monkeypatch.setattr(queue, 'run', hold_second)
    await submit(app, 'prior', independent=True)
    try:
        await queue.tick()
        await until(entered.is_set)
        prior = record(queue, app, 'prior')
        owned = environments.get(app.data_dir, prior['senderSessionId'], prior['job']['resourceId'])
        assert prior['job']['status'] == 'starting'
        assert bool(owned.get('backendAdmitted')) == (phase == 'admitted')
        await submit(app, 'next', independent=True)
        await queue.tick()
        scheduled = record(queue, app, 'next')['requestId'] in queue.tasks
        assert scheduled == (phase == 'admitted')
        assert record(queue, app, 'prior')['job']['resourceId'] == owned['id']
    finally:
        release_native.set()
        release_second.set()
        await asyncio.gather(*queue.tasks.values())
