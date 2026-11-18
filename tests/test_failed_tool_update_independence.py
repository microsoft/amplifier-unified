"""Optional tool failures retain recovery without owning the application queue."""
import copy
from unittest.mock import AsyncMock

import pytest

from amplifier_web import app_updates, smart_tool_update_batch as batch
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.updates import UpdateManager
from test_updates import app, repo  # noqa: F401


async def failed_tool(app, phase='stage'):
    app.smart_tools = SmartToolsManager(app)
    manager = app.update_manager
    manager.diagnostics.begin('smart-tools', components=['stories'])
    event = manager.diagnostics.record('smart-tool-' + phase, 'failed',
                                       installationId='old', errorType='ValueError')
    item = {'previous': 'old', 'revision': 'b' * 40, 'component': 'stories',
            'failure': {'phase': phase, 'reason': 'smart-tool-update-failed',
                        'errorType': 'ValueError'}}
    if phase == 'activate':
        item['target'] = 'new'
    await manager.publish(pendingSmartTools=[item], phase='error', error='Tool needs attention')
    return copy.deepcopy(item), event


@pytest.mark.parametrize('phase', ['stage', 'activate'])
@pytest.mark.parametrize('automatic', [False, True])
async def test_failed_tool_allows_new_app_without_retry_or_losing_failure(app, monkeypatch, phase, automatic):
    pending, event = await failed_tool(app, phase)
    manager = app.update_manager
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={
        'id': 'application', 'status': 'update', 'latest': '9.9.9'}))
    monkeypatch.setattr(manager, 'app', AsyncMock())
    monkeypatch.setattr(manager, 'smartToolRetry', AsyncMock(side_effect=AssertionError('Implicit retry')))
    monkeypatch.setattr(app.smart_tools, 'activate_update', AsyncMock(side_effect=AssertionError('Activation replay')))
    monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(side_effect=AssertionError('App must check first')))
    if automatic:
        await manager.tick()
    else:
        await manager.check()
        manager.app.assert_not_called()
        await manager.command('install')
    manager.app.assert_awaited_once()
    assert app.state['updates']['pendingSmartTools'] == [pending]
    assert app.state['updates']['sequence']['install']
    assert event in manager.diagnostics.state['events']
    assert manager.diagnostics.state['lastFailure'] == event


async def test_failed_tool_check_is_read_only_with_automatic_install_disabled(app, monkeypatch):
    pending, _ = await failed_tool(app)
    app.state['settings']['updates']['autoInstall'] = False
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={
        'id': 'application', 'status': 'update', 'latest': '9.9.9'}))
    monkeypatch.setattr(app.update_manager, 'app', AsyncMock())
    await app.update_manager.tick()
    app.update_manager.app.assert_not_called()
    assert app.state['updates']['appAvailable']
    assert app.state['updates']['pendingSmartTools'] == [pending]
    assert not app.state['updates']['sequence']['install']


async def test_failed_tool_is_not_rediscovered_or_retried_after_reload(app, monkeypatch):
    pending, event = await failed_tool(app)
    manager = app.update_manager
    await manager.close()
    manager = app.update_manager = UpdateManager(app)
    monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(return_value=[{
        'id': 'smart-tool:old', 'installationId': 'old', 'kind': 'smart tool',
        'eligible': True, 'url': 'https://example.invalid/stories', 'ref': 'main'}]))
    monkeypatch.setattr(app.smart_tools, 'stage_update', AsyncMock(side_effect=AssertionError('Implicit stage')))
    monkeypatch.setattr(app.smart_tools, 'activate_update', AsyncMock(side_effect=AssertionError('Implicit activation')))
    await manager.check(install=True)
    assert manager.inventory == []
    await manager.install()
    await manager.tick()
    assert app.state['updates']['pendingSmartTools'] == [pending]
    assert event in manager.diagnostics.state['events']
    app.smart_tools.stage_update.assert_not_called()
    app.smart_tools.activate_update.assert_not_called()
    assert not manager.requires_idle(app.state['updates'])


async def test_healthy_new_tool_can_finish_beside_retained_failure(app, monkeypatch):
    pending, event = await failed_tool(app)
    manager = app.update_manager
    await manager.publish(runnerAvailabilityCheck=True)
    manager.inventory = [{'installationId': name, 'kind': 'smart tool',
                          'status': 'update', 'eligible': True, 'latest': 'b' * 40}
                         for name in ('old', 'healthy')]
    monkeypatch.setattr(app.smart_tools, 'stage_update', AsyncMock(return_value={'id': 'healthy-new'}))
    monkeypatch.setattr(app.smart_tools, 'activate_update', AsyncMock(return_value=None))
    await manager.install()
    assert app.smart_tools.stage_update.await_args.args[0]['installationId'] == 'healthy'
    app.smart_tools.stage_update.assert_awaited_once()
    app.smart_tools.activate_update.assert_awaited_once_with('healthy', 'healthy-new')
    assert app.state['updates']['pendingSmartTools'] == [pending]
    assert event in manager.diagnostics.state['events']


@pytest.mark.parametrize('action', ['retry', 'skip'])
@pytest.mark.parametrize('fails', [False, True])
async def test_explicit_tool_recovery_preserves_foreground_app_state(app, monkeypatch, action, fails):
    pending, event = await failed_tool(app)
    manager = app.update_manager
    foreground = {'phase': 'available', 'appAvailable': True, 'available': 1,
                  'sequence': {'stage': 'application', 'install': True},
                  'detail': 'New app ready', 'error': None}
    await manager.publish(**foreground, runnerAvailabilityCheck=True)
    manager.inventory = [{'id': 'foreground'}]
    monkeypatch.setattr(app.smart_tools, 'stage_update', AsyncMock(
        side_effect=ValueError('fixture stage failure') if fails else None, return_value={'id': 'new'}))
    monkeypatch.setattr(app.smart_tools, 'activate_update', AsyncMock(return_value=None))
    monkeypatch.setattr(app.smart_tools, 'discard_update', AsyncMock(
        side_effect=ValueError('fixture cleanup failure') if fails else None, return_value=None))
    await getattr(batch, action)(manager, 'old')
    assert {key: app.state['updates'][key] for key in foreground} == foreground
    assert manager.inventory == [{'id': 'foreground'}]
    assert bool(app.state['updates']['pendingSmartTools']) == fails
    assert event in manager.diagnostics.state['events']
    if action == 'retry' and not fails:
        app.smart_tools.activate_update.assert_awaited_once_with('old', 'new')
    else:
        app.smart_tools.activate_update.assert_not_called()
    if action == 'skip' and not fails:
        app.smart_tools.discard_update.assert_awaited_once_with('old', pending['revision'])
        assert app.state['updates']['skippedSmartTools'] == {'old': pending['revision']}


@pytest.mark.parametrize('action', ['app', 'install'])
async def test_published_app_can_proceed_while_a_smart_tool_is_waiting(app, monkeypatch, action):
    from amplifier_web import __version__

    manager = app.update_manager
    pending = [{'previous': 'healthy', 'target': 'healthy-new'}]
    cached = {'id': 'application', 'current': __version__, 'latest': __version__,
              'revision': manager.running_identity['revision'], 'status': 'current'}
    published = {**cached, 'status': 'update', 'latest': 'v99.1.0', 'revision': 'b' * 40}
    await manager.publish(pendingSmartTools=copy.deepcopy(pending), phase='error',
                          application=cached, appAvailable=False, items=[cached], available=0)
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value=published))
    monkeypatch.setattr(manager, 'activateSmartTools', AsyncMock(side_effect=AssertionError('Tool activated')))
    monkeypatch.setattr(app_updates, 'stage', AsyncMock())
    monkeypatch.setattr(app_updates, 'activate', AsyncMock())

    if action == 'install':
        await manager.command('check')
        assert app.state['updates']['appAvailable'] and app.state['updates']['available'] == 1
        assert app.state['updates']['pendingSmartTools'] == pending
        app_updates.stage.assert_not_awaited()
    await manager.command(action)
    app_updates.check.assert_awaited_once()
    app_updates.stage.assert_awaited_once_with(manager)
    app_updates.activate.assert_awaited_once_with(manager)
    assert app.state['updates']['application']['revision'] == published['revision']
    assert app.state['updates']['pendingSmartTools'] == pending


@pytest.mark.parametrize('status', ['current', 'check_failed'])
async def test_explicit_app_request_with_waiting_tool_does_not_stage_a_stale_selection(app, monkeypatch, status):
    manager = app.update_manager
    tool = {'previous': 'healthy', 'target': 'healthy-new'}
    await manager.publish(pendingSmartTools=[tool.copy()], phase='staged', appAvailable=False,
                          applicationIntent={'id': 'old', 'scope': 'whole'},
                          sequence={'stage': 'other', 'install': True, 'nextStage': 'other'})
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={'id': 'application', 'status': status}))
    monkeypatch.setattr(app_updates, 'stage', AsyncMock())
    monkeypatch.setattr(manager, 'activateSmartTools', AsyncMock())
    await manager.command('app')
    app_updates.check.assert_awaited_once()
    app_updates.stage.assert_not_awaited()
    manager.activateSmartTools.assert_not_awaited()
    assert app.state['updates']['pendingSmartTools'] == [tool]
    assert app.state['updates']['phase'] == 'staged'
    assert app.state['updates']['applicationIntent']['scope'] == 'app'
    assert app.state['updates']['sequence'] == {'stage': 'application', 'install': False}


@pytest.mark.parametrize('auto_install', [False, True])
async def test_due_app_check_respects_install_policy_while_tool_is_waiting(app, monkeypatch, auto_install):
    manager = app.update_manager
    app.state['settings']['updates'].update(autoCheck=True, autoInstall=auto_install)
    tool = {'previous': 'healthy', 'target': 'healthy-new'}
    await manager.publish(pendingSmartTools=[tool.copy()], phase='staged', lastCheck=0, lastAttempt=0)
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={
        'id': 'application', 'status': 'update', 'latest': 'v99.1.0', 'revision': 'b' * 40}))
    monkeypatch.setattr(manager, 'app', AsyncMock())
    monkeypatch.setattr(manager, 'activateSmartTools', AsyncMock())
    await manager.tick()
    app_updates.check.assert_awaited_once()
    assert app.state['updates']['appAvailable'] is True
    assert app.state['updates']['pendingSmartTools'] == [tool]
    assert manager.app.await_count == int(auto_install)
    assert manager.activateSmartTools.await_count == int(not auto_install)


async def test_tool_handoff_protects_inventory_and_known_release_during_checks(app, monkeypatch):
    manager = app.update_manager
    tool = {'previous': 'healthy', 'target': 'healthy-new'}
    cache = {'id': 'application', 'status': 'update', 'latest': 'v99.1.0', 'revision': 'b' * 40}
    other = {'id': 'other', 'status': 'update'}
    await manager.publish(pendingSmartTools=[tool.copy()], phase='staged', appAvailable=True,
                          application=cache.copy(), items=[cache.copy(), other], available=2)
    manager.inventory = [other.copy()]
    monkeypatch.setattr(app_updates, 'check', AsyncMock(side_effect=[
        {**cache, 'revision': 'c' * 40}, {**cache, 'latest': 'v99.0.0', 'revision': 'd' * 40},
        {**cache, 'latest': 'v99.2.0', 'revision': 'e' * 40, 'canInstall': False},
        {'status': 'check_failed'}]))
    for tier in ['included', 'other']:
        await manager.check(tier=tier)
    app_updates.check.assert_not_awaited()
    for _ in range(4):
        await manager.command('check')
    assert app.state['updates']['items'] == [cache, other]
    assert app.state['updates']['application'] == cache
    assert app.state['updates']['available'] == 2
    assert app.state['updates']['pendingSmartTools'] == [tool]
    assert manager.inventory == [other]


async def test_app_staging_rechecks_tool_target_under_its_lock(app, monkeypatch):
    manager = app.update_manager
    await manager.publish(appAvailable=True, pendingSmartTools=[{'previous': 'ordinary'}],
                          application={'status': 'update', 'latest': 'v99.1.0', 'revision': 'b' * 40})
    monkeypatch.setattr(app_updates, 'stage', AsyncMock())
    monkeypatch.setattr(app_updates, 'activate', AsyncMock())
    await manager.app()
    app_updates.stage.assert_not_awaited()
    app_updates.activate.assert_not_awaited()


async def test_healthy_unfinished_tool_keeps_its_install_when_there_is_no_new_app(app, monkeypatch):
    await failed_tool(app)
    manager = app.update_manager
    await manager.publish(pendingSmartTools=[*app.state['updates']['pendingSmartTools'],
                                           {'previous': 'healthy', 'target': 'healthy-new'}],
                          appAvailable=True, phase='staged')
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={'id': 'application', 'status': 'current'}))
    monkeypatch.setattr(manager, 'app', AsyncMock())
    monkeypatch.setattr(manager, 'activateSmartTools', AsyncMock())
    await manager.check()
    await manager.install()
    app_updates.check.assert_awaited_once()
    manager.app.assert_not_called()
    manager.activateSmartTools.assert_awaited_once()
