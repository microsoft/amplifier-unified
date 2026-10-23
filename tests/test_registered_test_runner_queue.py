import asyncio
import copy
import threading
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as environments
from amplifier_web import registered_test_runners as runners
from amplifier_web import test_runner_enrollment as enrollment
from test_test_queue import app, backend, queue, submit, record
from test_task_environments import scope
from test_coordination_generation import until


async def test_pause_during_capacity_observation_prevents_new_target_binding(app, queue, backend, monkeypatch):
    config = environments.configuration(app)[0]
    runner = {'id': 'runner', 'name': 'Other machine', 'enabled': True, 'connection': 'fixture', 'configPath': '',
              'machineId': 'a'*32, 'profiles': ['linux-python'], 'incus': {'project': 'fixture'}}
    runners.publish(app, {'version': 1, 'runners': [runner]})
    entered, release = threading.Event(), threading.Event()
    def capacity_call(target, payload, timeout=60):
        entered.set()
        assert release.wait(3)
        return {'ok': True, 'available': True}
    monkeypatch.setattr(runners, 'call', capacity_call)
    monkeypatch.setattr(environments, 'capacity', lambda app, config: {'available': bool(config.get('remoteRunner'))})
    await submit(app)
    task = asyncio.create_task(queue.tick())
    await until(entered.is_set)
    current = runners.snapshot(app)
    await app.dispatch('testing.runners.enable', {'id': runner['id'], 'enabled': False,
                                                'expectedRevision': current['revision']}, command_id='pause-remote')
    release.set()
    await task
    await asyncio.gather(*queue.tasks.values())
    receipt = record(queue, app)
    assert not receipt['job'].get('targetBound'), receipt['job']
    assert not backend


async def test_remote_cancel_does_not_hold_global_host_lock(app, queue, backend, monkeypatch):
    config = {**environments.configuration(app)[0], 'backend': 'incus', 'incus': {'project': 'test'},
              'remoteRunner': {'id': 'runner', 'name': 'Other machine', 'machineId': 'a'*32, 'incus': {'project': 'test'}}}
    monkeypatch.setattr(environments, 'configuration', lambda app: (config, 'ready', 'fixture'))
    entered, release = threading.Event(), threading.Event()
    cancel_entered, cancel_release = threading.Event(), threading.Event()
    def invoke(config, home, row, action, args):
        if action == 'prepare':
            return {'ok': True, 'universeId': 'owned-universe', 'backendIdentity': {'backend': 'incus', 'daemon': 'daemon', 'project': 'test'},
                    'snapshot': {'snapshot_commit': 'snapshot', 'archive_sha256': 'archive'}}
        if action == 'prepareDependencies':
            entered.set(); assert release.wait(3)
            return {'ok': True, 'preparedDependencies': {}}
        if action == 'cancelPreparation':
            cancel_entered.set(); assert cancel_release.wait(3)
            return {'ok': True, 'status': 'cancellation-requested'}
        if action == 'cleanupDependencies':
            return {'ok': True, 'complete': True, 'status': 'removed'}
        raise AssertionError(action)
    monkeypatch.setattr(environments, 'invoke', invoke)
    await submit(app, profileId='python-project')
    await queue.tick()
    await until(entered.is_set)
    cancel = asyncio.create_task(app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'}))
    await until(cancel_entered.is_set)
    try:
        assert not app.lock.locked(), 'Remote cancellation must not block unrelated host work'
    finally:
        cancel_release.set(); release.set()
        await cancel
        await asyncio.gather(*queue.tasks.values(), return_exceptions=True)


def test_remote_cleanup_unknown_reply_retains_owner_without_projection_error(app, backend, monkeypatch):
    config = {**environments.configuration(app)[0], 'backend': 'incus', 'incus': {'project': 'test'},
              'remoteRunner': {'id': 'runner', 'name': 'Other machine', 'machineId': 'a'*32, 'incus': {'project': 'test'}}}
    current = scope(app)
    row = environments.prepare(app.data_dir, current['sessionId'], current['workspaceId'],
        {'sessionId': current['sessionId']}, app.state['sessions'][0]['workspace'],
        environments.config_key(config), 'start-review', None, config, 'python-project')
    row = environments.save(app.data_dir, row, preparationPending=True)
    monkeypatch.setattr(environments, 'invoke', lambda *args: {'ok': False, 'status': 'unknown',
         'code': 'owner_busy', 'message': 'The original target operation is still active. No new operation started.'})
    updated, result = environments.cleanup_preparation(app.data_dir, row, config)
    assert result['complete'] is False
    assert updated['preparationPending'] is True


async def test_unavailable_remote_observation_uses_existing_queue_backoff(app, queue, backend, monkeypatch):
    runner = {'id': 'runner', 'name': 'Other machine', 'enabled': True, 'connection': 'fixture', 'configPath': '',
              'machineId': 'a'*32, 'profiles': ['linux-python'], 'incus': {'project': 'fixture'}}
    runners.publish(app, {'version': 1, 'runners': [runner]})
    calls = []
    monkeypatch.setattr(runners, 'call', lambda target, payload, timeout=60: calls.append(target['id']) or {'ok': False})
    monkeypatch.setattr(environments, 'capacity', lambda *args: {'available': False})
    await submit(app)
    await queue.tick()
    assert calls == ['runner']
    await queue.tick()  # Neither time nor capacity notification changed.
    assert calls == ['runner'], 'Unavailable targets should back off, not open another connection for every queued check'


# Preserve the real configuration chooser before fixture monkeypatches it.
_real_origin_configuration = environments.configuration


async def test_first_remote_runner_submits_and_cleans_up_without_local_backend(app, queue, backend, monkeypatch):
    runner = {'id': 'remote-only', 'name': 'Remote only', 'enabled': True, 'connection': 'fixture', 'configPath': '',
              'machineId': 'a'*32, 'profiles': ['linux-python'], 'incus': {'project': 'test'}}
    runners.publish(app, {'version': 1, 'runners': [runner]})
    monkeypatch.setattr(environments, 'configuration', _real_origin_configuration)
    monkeypatch.setattr(environments, 'SUPPORTED_HOST', False)
    original_invoke = environments.invoke
    def invoke(config, home, row, action, args):
        assert config['originSnapshotVersion'] == 1
        assert 'python' not in config and 'installationId' not in config
        value = original_invoke(config, home, row, action, args)
        return {**value, 'backendIdentity': {'backend': 'incus', 'daemon': 'remote', 'project': 'test'}}
    monkeypatch.setattr(environments, 'invoke', invoke)
    monkeypatch.setattr(runners, 'call', lambda *a, **kw: {'ok': True, 'available': True})
    await submit(app)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values())
    saved = record(queue, app)
    assert saved['job']['status'] == 'passed', saved
    assert saved['job']['targetBound']
    assert saved['job']['runnerCandidates']
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']
    row = environments.get(app.data_dir, scope(app)['sessionId'], saved['job']['resourceId'])
    assert row['status'] == 'retired' and row['backendInstallation'] is None


async def test_remote_only_waiting_check_can_choose_second_frozen_machine_after_pause(app, queue, backend, monkeypatch):
    first = {'id': 'first', 'name': 'First', 'enabled': True, 'connection': 'fixture', 'configPath': '',
             'machineId': 'a'*32, 'profiles': ['linux-python'], 'incus': {'project': 'first'}}
    second = {**first, 'id': 'second', 'name': 'Second', 'incus': {'project': 'second'}}
    runners.publish(app, {'version': 1, 'runners': [first, second]})
    monkeypatch.setattr(environments, 'configuration', _real_origin_configuration)
    await submit(app)
    runners.publish(app, {'version': 1, 'runners': [{**first, 'enabled': False}, second]})
    saved = record(queue, app)
    config = queue.guard(saved)
    calls = []
    monkeypatch.setattr(runners, 'call', lambda runner, *a, **kw: calls.append(runner['id']) or {'ok': True, 'available': True})
    selected = runners.select(app, saved['job'], config)
    assert selected['remoteRunner']['id'] == 'second'
    assert calls == ['second']
    assert backend == []


@pytest.mark.parametrize('changed', [None, 'controller', 'packageDigest', 'connection', 'backendIdentity', 'incus'])
async def test_waiting_remote_check_survives_only_observation_metadata_refresh(app, queue, backend, monkeypatch, changed):
    runner = {'id': 'remote-only', 'name': 'Remote only', 'enabled': True, 'connection': 'fixture', 'configPath': '',
              'machineId': 'a'*32, 'accountId': 'account', 'profiles': ['linux-python'], 'incus': {'project': 'test'},
              'controller': '/private/old-controller', 'packageDigest': 'a'*64,
              'backendIdentity': {'backend': 'incus', 'daemon': 'remote', 'project': 'test'},
              'lastCheck': {'requestId': 'old-check', 'checkedAt': 1, 'status': 'passed'}}
    runners.publish(app, {'version': 1, 'runners': [runner]})
    monkeypatch.setattr(environments, 'configuration', _real_origin_configuration)
    monkeypatch.setattr(environments, 'SUPPORTED_HOST', False)
    available = False
    monkeypatch.setattr(runners, 'call', lambda *a, **kw: {'ok': True, 'available': available})
    invoke = environments.invoke
    def remote_invoke(config, home, row, action, args):
        result = invoke(config, home, row, action, args)
        return {**result, 'backendIdentity': runner['backendIdentity']}
    monkeypatch.setattr(environments, 'invoke', remote_invoke)
    await submit(app, waitSeconds=0)
    original = copy.deepcopy(record(queue, app)['job'])
    await queue.tick()
    assert record(queue, app)['job']['status'] == 'queued' and not backend

    # The qualification effects are covered separately through the actual
    # controller. Here use their completed receipt at the real publication
    # boundary while a normal remote-only queued check retains its old route.
    response = await runners.dispatch(app, 'testing.runners.refresh', {'id': runner['id'], 'requestId': 'recheck',
        'expectedRevision': runners.snapshot(app)['revision']}, None)
    identity = response['result']['enrollments'][0]['id']
    receipt = enrollment.read(app, identity)
    candidate = copy.deepcopy(runner)
    if changed:
        candidate[changed] = {'project': 'different'} if changed == 'incus' else (
            {**runner['backendIdentity'], 'daemon': 'different'} if changed == 'backendIdentity' else 'different')
    enrollment.qualification.save(enrollment.path(app, identity), receipt, runner=candidate,
        probeState='qualified', proof={'checkPassed': True, 'artifactVerified': True, 'probeRemoved': True})
    await enrollment.finish(app, receipt)
    assert enrollment.read(app, identity)['status'] == 'connected'
    assert runners.store(app)['runners'][0]['lastCheck']['requestId'] == identity

    available = True
    queue.backoff.clear()
    await queue.tick()
    await asyncio.gather(*queue.tasks.values())
    saved = record(queue, app)['job']
    assert saved['runnerCandidates'] == original['runnerCandidates']
    if changed:
        assert saved['status'] == 'queued' and not backend
        return
    assert saved['status'] == 'passed', saved
    assert saved['backendKey'] == original['backendKey']
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']
    assert (await submit(app, waitSeconds=0))['duplicate']
    await queue.tick()
    assert [action for action, _ in backend].count('execute') == 1
