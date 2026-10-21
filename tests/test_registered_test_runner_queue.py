import asyncio
import threading
from types import SimpleNamespace

import pytest

from amplifier_web import task_environments as environments
from amplifier_web import registered_test_runners as runners
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
