"""Real queued MCP admission and canonical host auxiliary readiness."""
import asyncio
from types import SimpleNamespace

import pytest

from amplifier_web.maintenance_auxiliary import snapshot, notify_task_settled
from amplifier_web.update_blockers import auxiliary_blockers, blockers
from test_smart_tools import manager, register  # Reuse the actual stdio MCP fixture.


def test_auxiliary_projection_uses_live_owners_and_keeps_remote_limits_explicit():
    app = SimpleNamespace(state={
        'voice': {'status': 'connected', 'sessionId': 'chat'},
        'voicePreviewBusy': True,
        'smartTools': {'operations': [{'status': 'running'}] * 500},
        'namingBackfill': {'status': 'running'},  # Display alone is not authority.
    }, naming_backfill=SimpleNamespace(running=False))
    result = snapshot(app)
    assert not result['settled']
    assert len(result['items']) == 3
    assert not result['remoteCancellationConfirmed']
    assert all(row['pauseSupported'] is False for row in result['items'])
    app.state['voice']['status'] = 'ended'
    app.state['voicePreviewBusy'] = False
    app.state['smartTools']['operations'] = []
    assert snapshot(app)['settled']
    app.naming_backfill.running = True
    assert [row['kind'] for row in snapshot(app)['items']] == ['naming']
    assert [row['kind'] for row in blockers(app)] == ['naming']
    app.naming_backfill.running = False
    assert snapshot(app)['settled']


async def test_live_host_task_blocks_even_without_overview_row():
    pending = asyncio.get_running_loop().create_future()
    app = SimpleNamespace(state={}, smart_tool_tasks={pending})
    assert auxiliary_blockers(app) == [
        {'kind': 'smart-tools', 'label': 'A Smart Tool operation is still running'}]
    pending.set_result(None)
    assert snapshot(app)['settled']


async def test_finished_task_republishes_after_canonical_removal_without_polling():
    pending = asyncio.get_running_loop().create_future()
    observed, publications = [], []
    app = SimpleNamespace(state={}, smart_tool_tasks={pending}, closed=False,
                          maintenance=object(), lock=asyncio.Lock())
    app._task = lambda work: publications.append(asyncio.create_task(work))
    app._publish_changes = lambda **_: observed.append(snapshot(app)['settled'])
    pending.set_result(None)
    app.smart_tool_tasks.discard(pending)
    notify_task_settled(app)
    await asyncio.gather(*publications)
    assert observed == [True]
    app.closed = True
    notify_task_settled(app)
    assert len(publications) == 1


def test_readiness_does_not_scan_or_hydrate_chat_catalog():
    class Unreadable:
        def __iter__(self):
            raise AssertionError('Auxiliary readiness must not scan chats')
    assert snapshot(SimpleNamespace(state={'sessions': Unreadable()}))['settled']


async def test_account_wait_rechecks_hold_before_real_mcp_effect(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect('board')
    connection = manager.connections['board']
    entered, release = asyncio.Event(), asyncio.Event()

    async def account_guard(_):
        # Ordinary calls must not acquire observer-only capability restrictions.
        assert connection.observation_active is False
        entered.set()
        await release.wait()

    connection.account_guard = account_guard
    call = asyncio.create_task(manager.command('smartTools.call', {
        'id': 'board', 'name': 'board_set', 'arguments': {'value': 'not sent'}}, 'held'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not snapshot(manager.service)['settled']
        manager.service.state['hostMaintenance'] = {'held': True}
        release.set()
        assert await asyncio.wait_for(call, 5) is None
        assert not (tmp_path / 'board-value').exists()
        receipt = manager.operation('held')
        assert receipt['failureReason'] == 'maintenance_paused'
        assert receipt['requestState'] == 'not_sent'
        assert receipt['status'] == 'failed'
        # Receipt reads and an existing transport remain usable during a hold.
        assert manager.read_operation({'operationId': 'held'})['status'] == 'failed'
        assert snapshot(manager.service)['settled']
        manager.service.state['hostMaintenance']['held'] = False
        result = await manager.command('smartTools.call', {
            'id': 'board', 'name': 'board_set', 'arguments': {'value': 'new request'}}, 'fresh')
        assert result['structuredContent']['value'] == 'new request'
        assert (tmp_path / 'board-value').read_text() == 'new request'
    finally:
        release.set()
        await asyncio.gather(call, return_exceptions=True)


async def test_second_queued_call_cannot_cross_hold(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect('board')
    connection = manager.connections['board']
    entered, release = asyncio.Event(), asyncio.Event()

    async def account_guard(_):
        entered.set()
        await release.wait()

    connection.account_guard = account_guard
    calls = []
    try:
        calls.append(asyncio.create_task(manager.call_tool('board', 'board_set', {'value': 'one'})))
        await asyncio.wait_for(entered.wait(), 5)
        calls.append(asyncio.create_task(manager.call_tool('board', 'board_set', {'value': 'two'})))
        async with asyncio.timeout(5):
            while connection.queue.qsize() != 1:
                await asyncio.sleep(0)
        manager.service.state['hostMaintenance'] = {'held': True}
        release.set()
        results = await asyncio.gather(*calls, return_exceptions=True)
        assert all(getattr(row, 'code', None) == 'maintenance_paused' for row in results)
        assert not (tmp_path / 'board-value').exists()
    finally:
        release.set()
        await asyncio.gather(*calls, return_exceptions=True)


async def test_already_dispatched_effect_settles_without_cancel_or_replay(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect('board')
    call = asyncio.create_task(manager.command('smartTools.call', {
        'id': 'board', 'name': 'slow'}, 'in-progress'))
    path = tmp_path / 'board-value'
    async with asyncio.timeout(5):
        while not path.exists():
            await asyncio.sleep(.01)
    manager.service.state['hostMaintenance'] = {'held': True}
    assert not snapshot(manager.service)['settled']
    result = await asyncio.wait_for(call, 5)
    assert not result.get('isError')
    assert path.read_text() == 'finished'
    assert manager.operation('in-progress')['status'] == 'completed'
    assert snapshot(manager.service)['settled']


async def test_existing_observer_dispatch_guard_keeps_its_semantics(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect('board')
    connection = manager.connections['board']
    seen = []

    def revoked():
        seen.append(connection.observation_active)
        raise ValueError('Original observation was revoked')

    with pytest.raises(ValueError, match='Original observation was revoked'):
        await manager.call_tool('board', 'board_set', {'value': 'not sent'}, dispatch_guard=revoked)
    assert seen == [True]
    assert not (tmp_path / 'board-value').exists()


async def test_naming_provider_resolution_wait_rechecks_hold(tmp_path, monkeypatch):
    import threading
    from unittest.mock import AsyncMock
    from amplifier_web import naming_backfill, setup
    entered, release = threading.Event(), threading.Event()
    probe = AsyncMock(return_value={'text': 'not called'})
    app = SimpleNamespace(state={}, data_dir=tmp_path)

    def resolve(*_):
        entered.set()
        assert release.wait(5)
        return 'provider', 'model'

    monkeypatch.setattr(naming_backfill, 'default_provider', resolve)
    monkeypatch.setattr(setup, 'SetupManager', lambda _: SimpleNamespace(probe=probe))
    task = asyncio.create_task(naming_backfill.NamingBackfill(app)._probe_complete(
        {'workspace': str(tmp_path)}, 'Name this chat'))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.01)
        app.state['hostMaintenance'] = {'held': True}
        release.set()
        with pytest.raises(Exception) as rejected:
            await task
        assert rejected.value.code == 'maintenance_paused'
        probe.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
