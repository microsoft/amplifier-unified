"""Composed update manager boundaries: progress without chat/capacity deadlocks."""
import copy
from unittest.mock import AsyncMock
import pytest

from amplifier_web import managed_runner_updates as runner
from amplifier_web import smart_tool_update_batch as batch
from amplifier_web import app_updates
from test_updates import app, repo  # noqa: F401


def item():
    return {'previous': 'a' * 24, 'target': 'b' * 24, 'runnerUpdate': 'c' * 32,
            'runnerSource': {'runnerKind': runner.KIND}}


async def test_waiting_runner_does_not_hide_new_app_or_replace_attempt(app, monkeypatch):
    manager = app.update_manager
    pending = [item()]
    await manager.publish(pendingSmartTools=pending, phase='staged')
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={
        'id': 'application', 'label': 'Amplifier Unified', 'status': 'update', 'latest': '9.9.9'}))
    monkeypatch.setattr(manager, 'install', AsyncMock(side_effect=AssertionError('discovery installed')))
    await manager.check()
    assert app.state['updates']['pendingSmartTools'] == pending
    assert app.state['updates']['appAvailable']
    assert app.state['updates']['runnerAvailabilityCheck']
    assert not app.state['updates']['sequence']['install']
    manager.install.assert_not_called()


async def test_waiting_runner_tick_still_checks_availability(app, monkeypatch):
    manager = app.update_manager
    await manager.publish(pendingSmartTools=[item()], phase='staged', lastCheck=0, lastAttempt=0)
    monkeypatch.setattr(runner, 'advance', AsyncMock(return_value={'complete': False, 'waiting': 'waiting'}))
    monkeypatch.setattr(manager, 'check', AsyncMock())
    await manager.tick()
    manager.check.assert_awaited_once_with(fresh=False)


async def test_progress_runs_promptly_without_an_extra_minute_per_step(app, monkeypatch):
    manager = app.update_manager
    await manager.publish(pendingSmartTools=[item()], phase='staged')
    advance = AsyncMock(side_effect=[
        {'complete': False, 'waiting': 'waiting', 'progressed': True},
        {'complete': False, 'waiting': 'waiting', 'progressed': True},
        {'complete': True}])
    monkeypatch.setattr(runner, 'advance', advance)
    app.state['sessions'][0]['status'] = 'working'
    await batch.activate(manager)
    assert advance.await_count == 3
    assert not app.state['updates']['pendingSmartTools']
    assert app.state['sessions'][0]['status'] == 'working'


async def test_failed_other_tool_cannot_freeze_runner_owner(app, monkeypatch):
    manager = app.update_manager
    failed = {'previous': 'other', 'failure': {'phase': 'stage', 'reason': 'smart-tool-update-failed'}}
    await manager.publish(pendingSmartTools=[item(), failed], phase='error', lastCheck=1e20)
    monkeypatch.setattr(runner, 'advance', AsyncMock(return_value={'complete': False, 'waiting': 'waiting'}))
    monkeypatch.setattr(app, 'runtime_lifecycle', lambda: pytest.fail('Unrelated failed tool took lifecycle lock'))
    await manager.tick()
    runner.advance.assert_awaited_once()
    assert app.state['updates']['pendingSmartTools'][1] == failed
    assert app.state['updates']['phase'] == 'staged'


async def test_interrupted_runner_stage_reports_failure_and_preserves_intent(app, monkeypatch):
    manager = app.update_manager
    pending = item()
    pending.pop('target')
    await manager.publish(pendingSmartTools=[pending], phase='staged')
    monkeypatch.setattr(runner, 'advance', AsyncMock(side_effect=AssertionError('unfinished stage advanced')))
    await batch.activate(manager)
    result = app.state['updates']['pendingSmartTools'][0]
    assert result['failure']['phase'] == 'stage' and result['runnerUpdate'] == pending['runnerUpdate']
    runner.advance.assert_not_called()
