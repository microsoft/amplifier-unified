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


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('waiting', ['waiting', 'unknown'])
async def test_runner_wait_does_not_prevent_app_install(app, monkeypatch, automatic, waiting):
    manager = app.update_manager
    pending = [{**item(), 'runnerWaiting': waiting}]
    await manager.publish(pendingSmartTools=pending, phase='staged')
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={
        'id': 'application', 'status': 'update', 'latest': '9.9.9'}))
    monkeypatch.setattr(manager, 'app', AsyncMock())
    monkeypatch.setattr(runner, 'advance', AsyncMock(return_value={'complete': False, 'waiting': waiting}))
    await manager.check()
    if automatic:
        await manager.tick()
    else:
        await manager.command('install')
    manager.app.assert_awaited_once()
    assert app.state['updates']['pendingSmartTools'] == pending
    assert app.state['updates']['sequence']['install']


async def test_runner_wait_keeps_checks_read_only_when_auto_install_disabled(app, monkeypatch):
    manager = app.update_manager
    app.state['settings']['updates'].update(autoInstall=False)
    await manager.publish(pendingSmartTools=[item()], phase='staged', lastCheck=0, lastAttempt=0)
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={
        'id': 'application', 'status': 'update', 'latest': '9.9.9'}))
    monkeypatch.setattr(manager, 'app', AsyncMock())
    monkeypatch.setattr(runner, 'advance', AsyncMock(return_value={'complete': False, 'waiting': 'waiting'}))
    await manager.tick()
    manager.app.assert_not_called()
    assert app.state['updates']['phase'] == 'available'
    assert not app.state['updates']['sequence']['install']


@pytest.mark.parametrize('tier', ['included', 'other'])
async def test_runner_wait_preserves_continuation_after_manager_reload(app, monkeypatch, tier):
    from amplifier_web.updates import UpdateManager
    manager = app.update_manager
    pending = [item()]
    await manager.publish(pendingSmartTools=pending, phase='installed', runnerAvailabilityCheck=True,
                          sequence={'stage': 'application', 'nextStage': tier, 'install': True})
    monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(return_value=[]))
    await manager.close()
    manager = app.update_manager = UpdateManager(app)
    monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, 'advance', AsyncMock(return_value={'complete': False, 'waiting': 'waiting'}))
    # The real check must receive continuation intent, not silently downgrade it.
    original = manager._check
    observed = []
    async def check(**kwargs):
        await original(**kwargs)
        observed.append(kwargs)
    monkeypatch.setattr(manager, '_check', check)
    await manager.tick()
    assert observed == [{'tier': tier, 'install': True}]
    assert app.state['updates']['sequence']['stage'] == 'complete'
    assert app.state['updates']['pendingSmartTools'][0]['runnerUpdate'] == pending[0]['runnerUpdate']


@pytest.mark.parametrize('complete', [False, True])
async def test_runner_result_does_not_replace_foreground_update_state(app, monkeypatch, complete):
    manager = app.update_manager
    foreground = {'phase': 'available', 'appAvailable': True, 'available': 1,
                  'sequence': {'stage': 'application', 'install': False},
                  'detail': 'New app ready', 'error': None}
    await manager.publish(**foreground, pendingSmartTools=[item()], runnerAvailabilityCheck=True)
    manager.inventory = [{'id': 'retained'}]
    monkeypatch.setattr(runner, 'advance', AsyncMock(return_value={'complete': complete, 'waiting': 'waiting'}))
    await batch.activate(manager)
    assert {key: app.state['updates'][key] for key in foreground} == foreground
    assert manager.inventory == [{'id': 'retained'}]
    assert bool(app.state['updates']['pendingSmartTools']) is not complete


async def test_components_activate_with_runner_pending_and_keep_owned_attempt(app, repo, monkeypatch):
    from test_updates import prepare, git
    from amplifier_web.updates import foundation_home
    manager, root = await prepare(app, repo)
    pending = [item()]
    await manager.publish(pendingSmartTools=pending, runnerAvailabilityCheck=True,
                          sequence={'stage': 'included', 'install': True}, phase='available')
    monkeypatch.setattr(manager, 'validate', AsyncMock())
    app.state['sessions'][0]['status'] = 'working'
    await manager.install()
    assert app.state['updates']['pendingRelease']
    assert git(root, 'rev-parse', 'HEAD') == repo[1]
    app.state['sessions'][0]['status'] = 'idle'
    await manager.tick()
    assert git(foundation_home(app.data_dir) / 'cache/repository', 'rev-parse', 'HEAD') == repo[2]
    assert app.state['updates']['pendingSmartTools'] == pending
    assert app.state['updates']['sequence']['nextStage'] == 'other'


async def test_continuing_component_check_retains_install_intent_and_excludes_owned_runner(app, monkeypatch):
    from amplifier_web import update_checks
    manager = app.update_manager
    source = {'id': 'runner', 'kind': 'smart tool', 'runnerKind': runner.KIND,
              'url': 'https://example.invalid/runner', 'ref': 'main', 'eligible': True}
    pending = [{**item(), 'runnerSource': source}]
    component = {'id': 'component', 'kind': 'runtime dependency', 'label': 'component',
                 'url': 'https://example.invalid/component', 'ref': 'main', 'current': 'a' * 40,
                 'eligible': True, 'updateTier': 'included'}
    await manager.publish(pendingSmartTools=pending, phase='installed')
    monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(return_value=[source, component]))
    monkeypatch.setattr(update_checks, 'ref_policy', AsyncMock(return_value={'kind': 'branch', 'revision': 'b' * 40}))
    monkeypatch.setattr(update_checks, 'git_revision', AsyncMock(side_effect=AssertionError('Owned runner rediscovered')))
    await manager.check(tier='included', install=True)
    assert app.state['updates']['sequence']['install'] is True
    assert [row['id'] for row in manager.inventory] == ['component']
    assert app.state['updates']['pendingSmartTools'] == pending


async def test_ordinary_tool_batch_keeps_its_existing_admission_gate(app, monkeypatch):
    manager = app.update_manager
    await manager.publish(pendingSmartTools=[item(), {'previous': 'ordinary'}],
                          appAvailable=True, phase='staged')
    monkeypatch.setattr(manager, 'app', AsyncMock())
    monkeypatch.setattr(manager, 'activateSmartTools', AsyncMock())
    await manager.install()
    manager.app.assert_not_called()
    manager.activateSmartTools.assert_awaited_once()


async def test_runner_failure_and_retry_leave_component_error_and_continuation_intact(app, monkeypatch):
    manager = app.update_manager
    foreground = {'phase': 'error', 'error': 'Included source failed',
                  'sequence': {'stage': 'included', 'install': True}, 'detail': 'Check components again'}
    await manager.publish(**foreground, runnerAvailabilityCheck=True, pendingSmartTools=[item()])
    monkeypatch.setattr(runner, 'advance', AsyncMock(side_effect=ValueError('fixture failure')))
    await batch.activate(manager)
    assert app.state['updates']['pendingSmartTools'][0]['failure']['phase'] == 'qualify'
    assert {key: app.state['updates'][key] for key in foreground} == foreground
    monkeypatch.setattr(runner, 'advance', AsyncMock(return_value={'complete': True}))
    await batch.retry(manager, item()['previous'])
    assert not app.state['updates']['pendingSmartTools']
    assert {key: app.state['updates'][key] for key in foreground} == foreground


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


async def test_skip_is_suppressed_on_recheck_for_same_source_not_other_selection(app, monkeypatch):
    from amplifier_web import managed_runner_backend as routing
    from amplifier_web import update_checks
    manager = app.update_manager
    source = {'id': 'managed-runner:dtu', 'kind': 'smart tool', 'runnerKind': runner.KIND,
              'label': 'Managed test runner core', 'installationId': 'a' * 24,
              'url': 'https://example.invalid/runner', 'ref': 'main', 'runnerSelection': 'a' * 64,
              'runnerPolicy': {'ref': 'main'}, 'current': 'a' * 40, 'latest': 'b' * 40,
              'eligible': True, 'status': 'update'}
    pending = {**item(), 'runnerSource': source, 'resultId': 'attempt-only', 'revision': 'b' * 40,
               'failure': {'phase': 'qualify', 'reason': 'managed-runner-qualification-failed'}}
    await manager.publish(pendingSmartTools=[pending], phase='error')
    monkeypatch.setattr(runner, 'skip', AsyncMock())
    await batch.skip(manager, pending['resultId'])
    assert app.state['updates']['skippedSmartTools'] == {routing.skip_key(source): 'b' * 40}
    monkeypatch.setattr(manager, 'inventory_sources', AsyncMock(side_effect=lambda **_: [copy.deepcopy(source)]))
    monkeypatch.setattr(update_checks, 'git_revision', AsyncMock(return_value='b' * 40))
    await manager.check(tier='other')
    assert manager.inventory[0]['status'] == 'skipped'
    source['runnerSelection'] = 'c' * 64
    await manager.check(tier='other')
    assert manager.inventory[0]['status'] == 'update'


async def test_bulk_install_retries_opaque_runner_result_id(app, monkeypatch):
    manager = app.update_manager
    pending = {**item(), 'resultId': 'managed-runner-result', 'failure': {'phase': 'qualify'}}
    await manager.publish(pendingSmartTools=[pending], phase='error')
    monkeypatch.setattr(runner, 'advance', AsyncMock(return_value={'complete': True}))
    await manager.command('install')
    runner.advance.assert_awaited_once_with(app, pending['runnerUpdate'])
    assert not app.state['updates']['pendingSmartTools']


async def test_actual_installer_copy_mode_and_interpreter_failed_pip_cleanup(app, tmp_path, monkeypatch):
    import sys
    from amplifier_web.smart_tools import SmartToolsManager
    smart = SmartToolsManager(app)
    checkout = tmp_path / 'runner-checkout'
    checkout.mkdir()
    (checkout / 'pyproject.toml').write_text('[project]\nname="digital-twin-universe"\nversion="0.test"\n')
    monkeypatch.setattr(smart, '_checkout', AsyncMock(return_value=(checkout, 'b' * 40)))
    monkeypatch.setattr(smart, '_inspect_checkout', lambda *args: {
        'pythonSupported': True, 'path': '.', 'repository': 'https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe',
        'commit': 'b' * 40, 'name': 'DTU', 'ref': 'b' * 40})
    commands = []
    async def run(argv, **kwargs):
        commands.append(argv)
        assert kwargs['env']['UV_LINK_MODE'] == 'copy'
        if 'venv' in argv:
            assert argv[argv.index('--python') + 1] == sys.executable
            binary = __import__('pathlib').Path(argv[-1]) / 'bin/python'
            binary.parent.mkdir(parents=True)
            binary.write_text('partial interpreter')
            return ''
        raise RuntimeError('bounded fixture install failed')
    monkeypatch.setattr(smart, '_run', run)
    try:
        with pytest.raises(RuntimeError, match='fixture install failed'):
            await smart.install({'repository': 'https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe',
                                 'ref': 'b' * 40, 'path': '.', '_runner_qualification': True})
        assert len(commands) == 2
        assert not list((smart.root / 'installs').iterdir())
        assert not smart.state['installations']
    finally:
        await smart.close()


async def test_manual_check_waits_for_inflight_runner_observation(app, monkeypatch):
    import asyncio
    manager = app.update_manager
    pending = [item()]
    await manager.publish(pendingSmartTools=pending, phase='staged')
    observing, release = asyncio.Event(), asyncio.Event()
    async def observe(*args):
        observing.set()
        await release.wait()
        return {'complete': False, 'waiting': 'waiting'}
    monkeypatch.setattr(runner, 'advance', observe)
    monkeypatch.setattr(app_updates, 'check', AsyncMock(return_value={
        'id': 'application', 'status': 'update', 'latest': '9.9.9'}))
    observation = asyncio.create_task(batch.activate(manager))
    await observing.wait()
    check = asyncio.create_task(manager.command('check'))
    try:
        for _ in range(5):
            await asyncio.sleep(0)
        assert not check.done(), 'Accepted refresh was dropped while the runner owned the lock'
        app_updates.check.assert_not_called()
    finally:
        release.set()
        await observation
        await check
    app_updates.check.assert_awaited_once()
    assert app.state['updates']['appAvailable']
    assert app.state['updates']['pendingSmartTools'] == pending


@pytest.mark.parametrize('handoff', [
    {'pendingRelease': {'id': 'new-handoff'}},
    {'pendingApp': {'featureSelection': ['native-desktop']}},
    {'pendingSmartTools': [{'previous': 'ordinary'}]},
])
async def test_queued_check_rechecks_new_pending_component_handoff(app, monkeypatch, handoff):
    import asyncio
    manager = app.update_manager
    await manager.publish(pendingSmartTools=[item()], phase='staged')
    monkeypatch.setattr(app_updates, 'check', AsyncMock())
    async with manager.lock:
        check = asyncio.create_task(manager.check())
        for _ in range(5):
            await asyncio.sleep(0)
        await manager.publish(**handoff)
    await check
    app_updates.check.assert_not_called()
    assert {key: app.state['updates'][key] for key in handoff} == handoff


@pytest.mark.parametrize('pending,other,expected', [
    ([item()], {}, False),
    ([item(), {'previous': 'ordinary'}], {}, True),
    ([item()], {'pendingApp': {'revision': 'f' * 40}}, True),
    ([item()], {'pendingRelease': {'id': 'component'}}, True),
])
async def test_idle_notification_does_not_spin_capacity_only_runner(app, pending, other, expected):
    manager = app.update_manager
    await manager.publish(pendingSmartTools=pending, phase='staged', **other)
    manager.wakeup.clear()
    manager.notify_idle()
    assert manager.wakeup.is_set() is expected
