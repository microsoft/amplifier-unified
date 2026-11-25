"""Cancel an unfinished mount without undoing or replaying its native save."""
import asyncio
import copy
import json

import pytest

from amplifier_web.configuration_recovery import receipt_path
from amplifier_web.management import Management
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import AppError
from test_queued_configuration_boundary import (
    operation, plan, process_apply, process_boundary, recovery,
)


async def saved_mount_failure(b):
    original = b.runtime.control
    async def lost(sid, name, args, **kwargs):
        if name == 'configuration.inspect':
            raise RuntimeError('Synthetic lost loaded-plan readback')
        return await original(sid, name, args, **kwargs)
    b.runtime.control = lost
    try:
        assert (await process_apply(b))['phase'] == 'error'
    finally:
        b.runtime.control = original
    record = operation(b)
    assert record['state']['effect'] == 'saved_not_mounted'
    return record


async def stop_worker(b):
    stopped = asyncio.Event()
    original = b.runtime.stop
    async def stop(*args, **kwargs):
        await original(*args, **kwargs)
        stopped.set()
    b.runtime.stop = stop
    try:
        await b.app.dispatch('conversation.stop', {'sessionId': b.owner['id']})
        await asyncio.wait_for(stopped.wait(), 10)
    finally:
        b.runtime.stop = original
    assert b.owner['id'] not in b.runtime.workers


async def test_saved_mount_can_be_cancelled_after_stop_without_replaying(process_boundary, monkeypatch):
    b = process_boundary
    original = await saved_mount_failure(b)
    settings = override_path(b.owner['runtimeSessionId']).read_bytes()
    receipt = receipt_path(original['attempts'][-1]).read_bytes()
    await stop_worker(b)
    with pytest.raises(AppError):
        await recovery(b, 'resume')
    with pytest.raises(ValueError, match='stopped or moved'):
        b.app.management.check_configuration_operation(b.owner, original)
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    publish = b.app._publish_changes
    def held_through_publication(*args, **kwargs):
        with pytest.raises(SessionBusyError):
            SharedSessionStore(b.owner['workspace'], b.owner['runtimeSessionId']).acquire(app='racing-writer')
        return publish(*args, **kwargs)
    monkeypatch.setattr(b.app, '_publish_changes', held_through_publication)
    drains = []
    async def drain(sid):
        drains.append(sid)
    monkeypatch.setattr(b.app.collaboration, 'drain', drain)
    await b.app.management.perform('configuration.cancel', {'id': b.owner['id']})
    await asyncio.sleep(0)
    assert not drains
    record = b.app.management.configuration_record(b.owner['id'], original['id'])
    assert record['state']['phase'] == 'cancelled'
    assert record['state']['effect'] == 'saved_not_mounted'
    assert record['request'] == original['request']
    assert record['attempts'] == original['attempts']
    assert record['history'][:len(original['history'])] == original['history']
    assert override_path(b.owner['runtimeSessionId']).read_bytes() == settings
    assert receipt_path(original['attempts'][-1]).read_bytes() == receipt
    assert not b.owner['configurationBusy']
    assert 'configurationOperationFence' not in b.owner
    assert 'pendingConfiguration' not in b.owner
    assert not b.app.management.queued_path(b.owner['id']).exists()
    assert not b.runtime.workers


@pytest.mark.parametrize('change', ['running', 'saved', 'receipt', 'native', 'placement', 'refresh', 'writer'])
async def test_saved_cancel_refuses_unproven_or_active_boundary(process_boundary, change):
    b = process_boundary
    original = await saved_mount_failure(b)
    if change != 'running':
        await stop_worker(b)
    held = None
    if change == 'saved':
        override_path(b.owner['runtimeSessionId']).write_text(json.dumps(plan(3)))
    elif change == 'receipt':
        receipt_path(original['attempts'][-1]).unlink()
    elif change == 'native':
        b.owner['runtimeSessionId'] = 'changed-native'
    elif change == 'placement':
        b.owner['executionRevision'] = b.owner.get('executionRevision', 0) + 1
    elif change == 'refresh':
        b.owner['configurationRefresh'] = {'phase': 'applying'}
    elif change == 'writer':
        from amplifier_foundation.session import SharedSessionStore
        held = SharedSessionStore(b.owner['workspace'], b.owner['runtimeSessionId']).acquire(app='other-writer')
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await b.app.management.perform('configuration.cancel', {'id': b.owner['id']})
    finally:
        if held:
            held.release()
    assert operation(b) == original
    assert b.owner['configurationBusy']


@pytest.mark.parametrize('independent', [None, 'refresh', 'fence'])
async def test_saved_cancel_restart_finishes_only_its_durable_cancellation(process_boundary, monkeypatch, independent):
    b = process_boundary
    original = await saved_mount_failure(b)
    await stop_worker(b)
    before = copy.deepcopy(b.owner)
    publish = b.app._publish_changes
    def crash(*args, **kwargs):
        raise OSError('Synthetic crash before host projection publication')
    monkeypatch.setattr(b.app, '_publish_changes', crash)
    with pytest.raises(OSError, match='Synthetic crash'):
        await b.app.management.perform('configuration.cancel', {'id': b.owner['id']})
    monkeypatch.setattr(b.app, '_publish_changes', publish)
    # Operation commit survived; host projection and queue cleanup may lag it.
    b.owner.clear()
    b.owner.update(before)
    if independent == 'refresh':
        b.owner['configurationRefresh'] = {'phase': 'applying'}
    elif independent == 'fence':
        b.owner['configurationOperationFence'] = 'another-operation'
    b.app.management = Management(b.app)
    assert 'pendingConfiguration' not in b.owner
    assert b.owner['configurationBusy'] is bool(independent)
    if independent == 'fence':
        assert b.owner['configurationOperationFence'] == 'another-operation'
    assert b.app.management.configuration_record(b.owner['id'], original['id'])['state']['phase'] == 'cancelled'
    assert json.loads(override_path(b.owner['runtimeSessionId']).read_text()) == plan(2)
    assert not b.runtime.workers
