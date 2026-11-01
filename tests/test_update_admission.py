"""Pending app updates defer themselves; only actual replacement holds input."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, update_drain
from test_app_updates import prepared_activation


@pytest.fixture
async def staged(tmp_path, monkeypatch):
    service, manager, _ = await prepared_activation(tmp_path, monkeypatch)
    await service.dispatch('session.create', {'title': 'Working elsewhere'})
    working = service._session()
    await service.dispatch('session.create', {'title': 'Continue here'})
    target = service._session()
    service.runtime.handoff_boundary = AsyncMock(
        side_effect=AssertionError('An automatic pending update must not freeze workers'))
    service.runtime.workers = {working['id']: {
        'runtimeOwnerId': 'existing-owner', 'process': SimpleNamespace(returncode=None)}}
    process = AsyncMock(return_value='99.0.0')
    monkeypatch.setattr(app_updates, 'process', process)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda home: True)
    restart = AsyncMock()
    monkeypatch.setattr(app_updates, 'request_managed_restart', restart)
    yield SimpleNamespace(service=service, manager=manager, working=working, target=target,
                          process=process, restart=restart)
    await service.close()


async def test_busy_peer_does_not_hold_human_message_or_freeze_delegation(staged):
    s = staged.service
    staged.working['status'] = 'working'
    await app_updates.activate(staged.manager)
    assert s.state['updates'].get('drain') is None
    assert not update_drain.held(s)
    s.runtime.handoff_boundary.assert_not_awaited()
    result = await s.dispatch('conversation.send', {
        'sessionId': staged.target['id'], 'text': 'Continue the review'}, command_id='new-work')
    assert result.get('delivery') != 'queued_for_update'
    assert [row[2] for row in s.runtime.sent] == ['new-work']
    assert staged.working['status'] == 'working'
    staged.process.assert_not_awaited()
    staged.restart.assert_not_awaited()


async def test_new_work_during_final_validation_postpones_replacement(staged, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = app_updates.installed_target
    async def validation():
        entered.set()
        await release.wait()
        return await original()
    monkeypatch.setattr(app_updates, 'installed_target', validation)
    activation = asyncio.create_task(app_updates.activate(staged.manager))
    await asyncio.wait_for(entered.wait(), 2)
    sent, finish = asyncio.Event(), asyncio.Event()
    native_send = staged.service.runtime.send
    async def send(*args):
        sent.set()
        await finish.wait()
        return await native_send(*args)
    staged.service.runtime.send = send
    sending = asyncio.create_task(staged.service.dispatch('conversation.send', {
        'sessionId': staged.target['id'], 'text': 'Start now'}, command_id='during-validation'))
    try:
        await asyncio.wait_for(sent.wait(), 2)
        release.set()
        await asyncio.wait_for(activation, 2)
        assert not staged.service.state['updates'].get('drain')
        assert not staged.service.state['updates'].get('pendingReplacement')
        staged.process.assert_not_awaited()
        staged.restart.assert_not_awaited()
    finally:
        release.set(); finish.set()
        await asyncio.gather(activation, sending)
    assert [row[2] for row in staged.service.runtime.sent] == ['during-validation']


async def test_only_committed_replacement_saves_then_releases_original_input_once(staged):
    s = staged.service
    async def replace(*args, **kwargs):
        assert s.state['updates']['pendingReplacement']
        assert s.state['updates']['drain']['phase'] == 'replacing'
        if not s.state['updates']['drain']['messages']:
            result = await s.dispatch('conversation.send', {
                'sessionId': staged.target['id'], 'text': 'Saved for restart'}, command_id='at-replacement')
            assert result['delivery'] == 'queued_for_update'
            assert not s.runtime.sent
        return '99.0.0'
    staged.process.side_effect = replace
    await app_updates.activate(staged.manager)
    staged.restart.assert_awaited_once()
    assert s.state['updates']['drain']['workers'] == {}
    assert not s.runtime.sent
    s.state['updates'].update(phase='installed', pendingRestart=None,
                             applicationResult=dict(s.state['updates']['drain']['target']))
    await update_drain.finish(staged.manager, 'installed')
    await update_drain.finish(staged.manager, 'installed')
    assert [row[2] for row in s.runtime.sent] == ['at-replacement']
    s.runtime.handoff_boundary.assert_not_awaited()
