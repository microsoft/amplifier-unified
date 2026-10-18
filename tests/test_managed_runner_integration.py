"""Shared-action authority and preparation/guest cleanup commit boundaries."""
# ruff: noqa: F401, F811
import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from amplifier_web import task_environments as owner
from amplifier_web.service import AppError
from test_configuration_authority import app, grant
from test_coordination_generation import agent, until
from test_task_environments import backend, scope, act
from test_test_queue import queue, record, submit, drive


@pytest.fixture
def prepared(monkeypatch):
    calls = []
    def prepare(config, root, snapshot, profile_id, request_id):
        calls.append((snapshot, profile_id, request_id))
        return {'version': 1, 'status': 'ready', 'profileId': profile_id,
                'sourceSha256': snapshot['archive_sha256'], 'backend': 'docker', 'image': 'sha256:'+'a'*64}
    monkeypatch.setattr(owner.dependencies, 'prepare', prepare)
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': True, 'status': 'removed'})
    return calls


async def start_prepared(app, identity='prepared'):
    return (await app.dispatch('workspace.environments.start', {**scope(app), 'profileId': 'python-project',
        'requestId': identity}, command_id='outer-'+identity))['result']


async def test_shared_setup_uses_narrow_host_authority_and_passive_reconcile(app, monkeypatch):
    source = app.state['sessions'][0]
    setup = AsyncMock(return_value={'operation': {'state': 'accepted'}})
    reconcile = AsyncMock(return_value={'operation': {'state': 'outcome_unknown'}})
    monkeypatch.setattr(owner.setup, 'request', setup)
    monkeypatch.setattr(owner.setup, 'reconcile', reconcile)
    args = {**scope(app), 'requestId': 'setup', 'backend': 'docker'}
    with pytest.raises(AppError, match='authority is required'):
        await agent(app, source, 'workspace.environments.setup', args, 'blocked')
    await grant(app, ['test_environments'], ['session'])
    with pytest.raises(AppError, match='authority is required'):
        await agent(app, source, 'workspace.environments.setup', args, 'still-blocked')
    await grant(app, ['test_host_setup'], ['global'], identity='host-grant')
    assert (await agent(app, source, 'workspace.environments.setup', args, 'agent-setup'))['accepted']
    assert (await app.dispatch('workspace.environments.setup', {**args, 'requestId': 'user-setup'}))['accepted']
    assert setup.await_count == 2
    await agent(app, source, 'workspace.environments.setup.reconcile',
                {**scope(app), 'requestId': 'setup'}, 'read-setup')
    reconcile.assert_awaited_once()


async def test_preparation_is_bound_to_exact_snapshot_and_removed_after_guest(app, backend, prepared, monkeypatch):
    started = await start_prepared(app)
    assert started['request']['state'] == 'completed'
    assert prepared == [({'snapshot_commit': 'snapshot', 'archive_sha256': 'archive'}, 'python-project', 'prepared')]
    row = started['resource']
    stopped = await act(app, 'stop', row, 'stop')
    def cleanup(*args):
        assert backend[-1][0] == 'remove'
        return {'complete': True, 'status': 'removed'}
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    removed = await act(app, 'remove', stopped['resource'], 'remove')
    assert removed['resource']['status'] == 'removed'
    saved = owner.get(app.data_dir, scope(app)['sessionId'], row['id'])
    assert saved['status'] == 'retired' and not saved['preparationPending']


async def test_failed_preparation_stops_owned_builder_before_retiring(app, backend, prepared, monkeypatch):
    def fail(*args):
        raise ValueError('Dependency download failed')
    cleanup = []
    monkeypatch.setattr(owner.dependencies, 'prepare', fail)
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: cleanup.append(True) or {'complete': True, 'status': 'removed'})
    result = await start_prepared(app)
    assert result['request']['state'] == 'failed' and cleanup == [True]
    assert [action for action, _ in backend] == ['prepare']
    assert not owner.get(app.data_dir, scope(app)['sessionId'], result['resource']['id'])['preparationPending']


async def test_unknown_preparation_cleanup_keeps_owner_and_recovery(app, backend, prepared, monkeypatch):
    started = await start_prepared(app)
    stopped = await act(app, 'stop', started['resource'], 'stop')
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': False, 'status': 'unknown'})
    removed = await act(app, 'remove', stopped['resource'], 'remove')
    saved = owner.get(app.data_dir, scope(app)['sessionId'], started['resource']['id'])
    assert removed['request']['state'] == 'outcome_unknown'
    assert saved['status'] != 'retired' and saved['guestRemoved'] and saved['preparationPending']
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': True, 'status': 'removed'})
    recovered = await act(app, 'remove', removed['resource'], 'finish-images')
    assert recovered['resource']['status'] == 'removed'
    assert [action for action, _ in backend].count('remove') == 1


async def test_restart_after_guest_removal_finishes_only_preparation_cleanup(app, backend, prepared, monkeypatch):
    started = await start_prepared(app)
    stopped = await act(app, 'stop', started['resource'], 'stop')
    original = owner.save
    def crash(home, row, **changes):
        updated = original(home, row, **changes)
        if changes.get('guestRemoved') and 'guestRemoval' in changes:
            raise OSError('host disappeared after guest removal commit')
        return updated
    monkeypatch.setattr(owner, 'save', crash)
    interrupted = await act(app, 'remove', stopped['resource'], 'remove')
    assert interrupted['request']['state'] == 'outcome_unknown'
    monkeypatch.setattr(owner, 'save', original)
    sid = scope(app)['sessionId']
    row = owner.get(app.data_dir, sid, started['resource']['id'])
    config, _, _ = owner.configuration(app)
    fixed = await asyncio.to_thread(owner.resume_preparation_removal, app, row, config)
    assert fixed['status'] == 'retired' and not fixed['preparationPending']
    assert app.operations.requests.read(sid, 'remove')['state'] == 'completed'
    assert [action for action, _ in backend].count('remove') == 1


async def test_long_preparation_does_not_hold_queue_admission(app, queue, backend, prepared, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = owner.dependencies.prepare
    def wait(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(owner.dependencies, 'prepare', wait)
    await submit(app, profileId='python-project', timeoutSeconds=1200)
    await queue.tick()
    assert await asyncio.to_thread(entered.wait, 5)
    assert not queue.admission.locked()
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
    release.set()
    await asyncio.wait_for(asyncio.gather(*queue.tasks.values()), 5)
    assert record(queue, app)['job']['status'] == 'cancelled'
    assert [action for action, _ in backend] == ['prepare']
