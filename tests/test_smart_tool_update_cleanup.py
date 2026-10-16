"""Failed qualification must not leave hidden staged packages after Skip."""
import asyncio
import copy
from pathlib import Path
import shlex
import sys

import pytest

from amplifier_web.service import AppService
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.updates import UpdateManager
from test_smart_tools import FIXTURE
from test_updates import Runtime, app, repo  # noqa: F401 — shared isolated app fixtures

OLD = 'a' * 24


async def package(smart, identity, revision, *, staged=False, broken=False):
    folder = smart.root / 'installs' / identity
    binary = folder / 'venv' / 'bin' / 'python'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nexit 12\n' if broken else
                      '#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
    binary.chmod(0o700)
    (folder / 'source').mkdir()
    (folder / 'source' / 'retained.txt').write_text('candidate source')
    row = {'id': identity, 'name': 'Example', 'repository': 'https://example.invalid/tool',
           'ref': 'main', 'commit': revision, 'binDir': str(binary.parent),
           'sourceDir': str(folder / 'source'), **({'stagedFrom': OLD} if staged else {})}
    await smart._change(lambda state: state['installations'].append(row))
    return row


async def configure(app):
    app.smart_tools = SmartToolsManager(app)
    await package(app.smart_tools, OLD, 'a' * 40)
    await app.smart_tools.configure({'id': 'board', 'name': 'Current board',
        'command': str(app.smart_tools.root / 'installs' / OLD / 'venv' / 'bin' / 'python'),
        'args': [str(FIXTURE), str(app.data_dir / 'tool-effects')], 'env': {}, 'installationId': OLD})
    return app.smart_tools


async def pending(app, revision, identity, **row_changes):
    smart = app.smart_tools
    row = await package(smart, identity, revision, staged=True)
    await smart._change(lambda _: row.update(row_changes))
    item = {'previous': OLD, 'revision': revision,
            'failure': {'phase': 'stage', 'reason': 'smart-tool-startup-failed'}}
    await app.update_manager.publish(phase='error', pendingSmartTools=[item])
    return smart.root / 'installs' / identity


async def test_failed_stage_restart_skip_removes_each_candidate_keeps_current_connection(app, monkeypatch, tmp_path):
    smart = await configure(app)
    async def install(args):
        return await package(smart, args['ref'][:24], args['ref'], staged=True, broken=True)
    monkeypatch.setattr(smart, 'install', install)
    app.update_manager.inventory = [{'installationId': OLD, 'label': 'example', 'kind': 'smart tool',
        'status': 'update', 'eligible': True, 'latest': 'b' * 40}]
    await app.update_manager.installSmartTools()
    failed = app.state['updates']['pendingSmartTools'][0]
    assert failed['failure']['phase'] == 'stage' and 'target' not in failed
    candidate = smart.root / 'installs' / ('b' * 24)
    assert candidate.is_dir()
    await app.close()

    restarted = AppService(app.data_dir, Runtime(), workspace=tmp_path)
    restarted.smart_tools = SmartToolsManager(restarted)
    restarted.update_manager = UpdateManager(restarted)
    try:
        smart = restarted.smart_tools
        await smart.connect('board')
        current = copy.deepcopy(smart._server('board'))
        connection = smart.connections['board']
        await restarted.update_manager.smartToolSkip(OLD)
        assert not candidate.exists()
        assert [p['id'] for p in smart.state['installations']] == [OLD]
        assert restarted.state['updates']['skippedSmartTools'] == {OLD: 'b' * 40}
        # A later failed revision must not accumulate another hidden install.
        for letter in ('c', 'd'):
            candidate = await pending(restarted, letter * 40, letter * 24)
            await restarted.update_manager.smartToolSkip(OLD)
            assert not candidate.exists()
            assert [p['id'] for p in smart.state['installations']] == [OLD]
        assert smart._server('board') == current
        assert smart.connections['board'] is connection and not connection.task.done()
        assert (await smart.call_tool('board', 'view_only', {}, origin='app'))['structuredContent']['view'] == 'compact'
        assert not (app.data_dir / 'tool-effects').exists()
        assert restarted.runtime.closed == 0
    finally:
        await restarted.close()


async def test_cleanup_failure_remains_actionable_and_skip_retries_cleanup_only(app, monkeypatch):
    smart = await configure(app)
    folder = await pending(app, 'b' * 40, 'b' * 24)
    from amplifier_web import task_environments
    remove = task_environments.remove_installation
    def fail(*args, **kwargs):
        raise OSError('private-path-secret')
    monkeypatch.setattr(task_environments, 'remove_installation', fail)
    await app.update_manager.smartToolSkip(OLD)
    state = app.state['updates']
    assert state['pendingSmartTools'][0]['failure']['phase'] == 'cleanup'
    assert 'private-path-secret' not in str(state)
    assert not state.get('skippedSmartTools') and folder.is_dir()
    monkeypatch.setattr(task_environments, 'remove_installation', remove)
    await app.update_manager.smartToolSkip(OLD)
    assert not state['pendingSmartTools'] and not folder.exists()
    assert [p['id'] for p in smart.state['installations']] == [OLD]


async def test_skip_recovers_file_removal_before_state_commit_and_keeps_other_revision(app):
    smart = await configure(app)
    folder = await pending(app, 'b' * 40, 'b' * 24)
    other = await package(smart, 'c' * 24, 'c' * 40, staged=True)
    from amplifier_web.task_environments import remove_installation
    remove_installation(app.data_dir, 'b' * 24, folder)
    await app.update_manager.smartToolSkip(OLD)
    assert [p['id'] for p in smart.state['installations']] == [OLD, other['id']]
    assert (smart.root / 'installs' / other['id']).is_dir()
    # Guarded removal is idempotent after a completed state commit, too.
    await smart.uninstall('b' * 24, staged_from=OLD, revision='b' * 40)


async def test_cancelled_skip_retains_failure_and_does_not_activate(app, monkeypatch):
    smart = await configure(app)
    folder = await pending(app, 'b' * 40, 'b' * 24)
    before = copy.deepcopy(app.state['updates']['pendingSmartTools'])
    async def cancel(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(smart, 'discard_update', cancel)
    with pytest.raises(asyncio.CancelledError):
        await app.update_manager.smartToolSkip(OLD)
    assert app.state['updates']['pendingSmartTools'] == before and folder.is_dir()
    await app.update_manager.activateSmartTools()
    assert app.state['updates']['pendingSmartTools'] == before


@pytest.mark.parametrize('guard', ['configured', 'symlink', 'rollback', 'superseded', 'changed-revision', 'changed-owner'])
async def test_guarded_cleanup_never_removes_reused_or_unowned_install(app, guard, tmp_path):
    smart = await configure(app)
    folder = await pending(app, 'b' * 40, 'b' * 24)
    candidate = smart.state['installations'][-1]
    sentinel = folder / 'source' / 'retained.txt'
    if guard == 'configured':
        await smart.configure({'id': 'candidate', 'name': 'Used candidate',
            'command': str(folder / 'venv' / 'bin' / 'python'), 'args': [], 'env': {},
            'installationId': candidate['id']})
    elif guard == 'symlink':
        moved = tmp_path / 'unowned'
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
    elif guard == 'rollback':
        candidate['previousInstallationId'] = OLD
    elif guard == 'superseded':
        candidate['supersededBy'] = 'c' * 24
    elif guard == 'changed-revision':
        candidate['commit'] = 'c' * 40
    elif guard == 'changed-owner':
        candidate['stagedFrom'] = 'd' * 24
    with pytest.raises(ValueError):
        await smart.uninstall(candidate['id'], staged_from=OLD, revision='b' * 40)
    assert sentinel.read_text() == 'candidate source'
    assert len(smart.state['installations']) == 2
    assert (smart.root / 'installs' / OLD).is_dir()
