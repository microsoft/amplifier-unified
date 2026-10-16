"""Inspection acknowledgement names completed retained work, never queued facts."""
import asyncio
import json
import threading

import pytest

from amplifier_web import workspace_provisioning
from amplifier_web.service import AppError
from test_workspace_starters import service, create


async def test_inspection_ack_waits_without_holding_service_lock_and_replay_is_final(service, monkeypatch):
    _, row = await create(service, 'development', 'Inspection')
    before = row['setup']['revision']
    entered, release = threading.Event(), threading.Event()
    original = workspace_provisioning.reconcile
    calls = []

    def delayed(*args):
        calls.append(args[1])
        entered.set()
        assert release.wait(5), 'Test did not release inspection'
        return original(*args)

    monkeypatch.setattr(workspace_provisioning, 'reconcile', delayed)
    args = {'workspaceId': row['id']}
    first = asyncio.create_task(service.dispatch('workspace.setup.reconcile', args, command_id='inspect-once'))
    duplicate = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(service.dispatch('view.update', {'patch': {'draft': 'Still responsive'}}), 1)
        assert not first.done(), 'HTTP acknowledgement must wait for actual inspection'
        duplicate = asyncio.create_task(service.dispatch('workspace.setup.reconcile', args, command_id='inspect-once'))
        await asyncio.sleep(.01)
        assert not duplicate.done(), 'Concurrent retry must wait on the original inspection'
        release.set()
        result, repeated = await asyncio.gather(first, duplicate)
        assert result['result']['revision'] > before
        assert result['result']['inspectionStatus'] == 'complete'
        assert repeated['duplicate'] and repeated['result'] == result['result']
        saved = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?', ('inspect-once',)).fetchone()[0])
        assert saved['result'] == result['result']
        assert len(calls) == 1
        assert row['setup']['revision'] == result['result']['revision']
    finally:
        release.set()
        await asyncio.gather(first, *([duplicate] if duplicate else []), return_exceptions=True)


async def test_inspection_failure_retains_failed_receipt_without_replaying(service, monkeypatch):
    _, row = await create(service, 'development', 'Failed inspection')
    calls = []
    def fail(*args):
        calls.append(args)
        raise ValueError('Retained folder cannot be inspected')
    monkeypatch.setattr(workspace_provisioning, 'reconcile', fail)
    args = {'workspaceId': row['id']}
    with pytest.raises(AppError, match='cannot be inspected'):
        await service.dispatch('workspace.setup.reconcile', args, command_id='failed-inspect')
    repeated = await service.dispatch('workspace.setup.reconcile', args, command_id='failed-inspect')
    assert repeated['accepted'] is False
    assert repeated['result']['inspectionStatus'] == 'failed'
    assert len(calls) == 1


async def test_interrupted_ack_retains_uncertainty_instead_of_replaying_inspection(service, monkeypatch):
    _, row = await create(service, 'development', 'Interrupted inspection')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = workspace_provisioning.reconcile
    calls = []
    def delayed(*args):
        calls.append(args[1])
        entered.set()
        try:
            assert release.wait(5)
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(workspace_provisioning, 'reconcile', delayed)
    args = {'workspaceId': row['id']}
    task = asyncio.create_task(service.dispatch('workspace.setup.reconcile', args, command_id='interrupted-inspect'))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        repeated = await service.dispatch('workspace.setup.reconcile', args, command_id='interrupted-inspect')
        assert repeated['duplicate']
        assert repeated['result']['inspectionStatus'] == 'pending'
        assert len(calls) == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
