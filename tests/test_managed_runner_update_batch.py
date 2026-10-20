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
