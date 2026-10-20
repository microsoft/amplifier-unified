"""Real Git races across manager lookup, lock, source-read and publication waits."""
import asyncio
import copy
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from amplifier_web.service import AppError
from amplifier_worktrees import git as git_module
from test_collaborative_workspaces import app, agent_action
from test_manager_placement import prepared, checkout


def change_authority(app, source, target, change):
    if change == 'detached':
        assignment = app.assignments.current(source)
        assignment['members'][target['id']]['status'] = 'detached'
        app.assignments.save(assignment)
    elif change == 'working':
        target['status'] = 'working'
    elif change == 'preparing':
        app.runtime._preparations = {target['id']: True}
    elif change in {'configurationBusy', 'configurationPending', 'fileAccessPending'}:
        target[change] = True
    elif change == 'member_generation':
        target['collaborationGeneration'] = {'id': 'another-finished-turn', 'terminal': True}
    elif change == 'member_placement':
        target['executionRevision'] = target.get('executionRevision', 0) + 1
    elif change in {'manager_generation', 'manager_terminal'}:
        active = source['collaborationGeneration']
        if change == 'manager_generation':
            active['id'] = 'replacement-manager-turn'
        else:
            active['terminal'] = True
    elif change.startswith('pending_'):
        target['pendingConfiguration'] = {
            'operationId': 'queued-settings', 'requestHash': 'retained-request',
            'phase': change.removeprefix('pending_'), 'effect': 'unknown'}
    else:
        row = source if change.startswith('manager_') else target
        if change.endswith('_stop'):
            row['interruptionRevision'] = row.get('interruptionRevision', 0) + 1
            row['status'] = 'stopped'
        elif change.endswith('_human'):
            app._message(row, 'user', 'Wait for my next instruction.', inputOrigin='ui')
        elif change.endswith('_task'):
            row['task'] = {'id': 'replacement-task', 'revision': 1}
        else:
            raise AssertionError(change)


def hold(monkeypatch, owner, name, *, context=False):
    original = getattr(owner, name)
    entered, release = threading.Event(), threading.Event()

    def waiting(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'Test did not release the held operation'
        return original(*args, **kwargs)

    if context:
        @contextmanager
        def waiting_context(*args, **kwargs):
            with waiting(*args, **kwargs):
                yield
        monkeypatch.setattr(owner, name, waiting_context)
    else:
        monkeypatch.setattr(owner, name, waiting)
    return entered, release


def preparation(app, target, root, action):
    inspected = app.worktrees.git.inspect(root)
    args = {'sessionId': target['id'], 'repositoryPath': str(root)}
    if action == 'worktree.create':
        args['sourceRevision'] = inspected['sourceRevision']
    else:
        args['path'] = str(root)
    return args


@pytest.mark.parametrize('action', ['worktree.create', 'worktree.attach'])
@pytest.mark.parametrize('wait', ['repository', 'git_lock', 'source_inspection'])
@pytest.mark.parametrize('change', ['manager_stop', 'working', 'detached'])
async def test_changed_authority_before_git_admission_has_no_effect(app, tmp_path, monkeypatch, action, wait, change):
    source, target, _, root = await prepared(app, tmp_path)
    args = preparation(app, target, root, action)
    if wait == 'repository':
        entered, release = hold(monkeypatch, app.worktrees, 'repository_path')
    elif wait == 'git_lock':
        entered, release = hold(monkeypatch, app.worktrees.git, 'lock', context=True)
    else:
        entered, release = hold(monkeypatch, git_module, 'snapshot')
    pending = asyncio.create_task(agent_action(app, source, action, args, 'prepare'))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            change_authority(app, source, target, change)
        release.set()
        with pytest.raises(AppError):
            await pending
        assert not app.worktrees.git.records()
        assert not list((app.worktrees.git.directory / 'checkouts').glob('*'))
        assert not list((app.worktrees.git.directory / 'manifests').glob('*'))
        assert (root / 'a.txt').read_text() == 'original\n'
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('action', ['worktree.create', 'worktree.attach'])
@pytest.mark.parametrize('change', ['manager_human', 'manager_task', 'member_stop',
    'member_human', 'member_task', 'member_generation', 'member_placement', 'preparing',
    'configurationBusy', 'configurationPending', 'fileAccessPending',
    'manager_generation', 'manager_terminal', 'pending_queued', 'pending_applying', 'pending_error'])
async def test_preparation_keeps_original_instruction_and_idle_settings_scope(app, tmp_path, monkeypatch, action, change):
    source, target, _, root = await prepared(app, tmp_path)
    args = preparation(app, target, root, action)
    entered, release = hold(monkeypatch, git_module, 'snapshot')
    pending = asyncio.create_task(agent_action(app, source, action, args, 'scope'))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            change_authority(app, source, target, change)
        release.set()
        with pytest.raises(AppError):
            await pending
        assert not app.worktrees.git.records()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('action', ['worktree.inspect', 'worktree.status'])
@pytest.mark.parametrize('change', ['detached', 'manager_stop', 'manager_human', 'manager_task', 'member_placement', 'manager_generation'])
async def test_cross_member_read_does_not_publish_after_scope_changes(app, tmp_path, monkeypatch, action, change):
    source, target, _, root = await prepared(app, tmp_path)
    record = await checkout(app, source, target, root)
    field = 'worktreeInspection' if action == 'worktree.inspect' else 'worktreeStatus'
    before = copy.deepcopy(target.get(field))
    args = {'sessionId': target['id'], 'repositoryPath': str(root)} if action == 'worktree.inspect' else {'sessionId': target['id'], 'id': record['id']}
    entered, release = hold(monkeypatch, app.worktrees.git, action.split('.')[1])
    pending = asyncio.create_task(agent_action(app, source, action, args, 'read'))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            change_authority(app, source, target, change)
        release.set()
        with pytest.raises(AppError):
            await pending
        assert target.get(field) == before
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('action', ['worktree.create', 'worktree.attach'])
async def test_cancel_before_admission_does_not_create_an_operation(app, tmp_path, monkeypatch, action):
    source, target, _, root = await prepared(app, tmp_path)
    args = preparation(app, target, root, action)
    entered, release = hold(monkeypatch, git_module, 'snapshot')
    cancelled = asyncio.Event()
    original = app.worktrees.preparation_admission
    def observe_cancel(*args):
        admit, cancel = original(*args)
        def observed():
            cancel()
            cancelled.set()
        return admit, observed
    monkeypatch.setattr(app.worktrees, 'preparation_admission', observe_cancel)
    pending = asyncio.create_task(agent_action(app, source, action, args, 'cancel'))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        pending.cancel()
        await asyncio.wait_for(cancelled.wait(), 5)
        assert app.worktrees.mutations.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not app.worktrees.git.records()
        assert not app.worktrees.mutations.locked()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_admitted_creation_finishes_without_holding_chat_state_lock(app, tmp_path, monkeypatch):
    source, target, independent, root = await prepared(app, tmp_path)
    args = preparation(app, target, root, 'worktree.create')
    original = git_module.git
    entered, release = threading.Event(), threading.Event()
    def slow_git(path, *argv, **kwargs):
        if argv[:2] == ('worktree', 'add'):
            entered.set()
            assert release.wait(10)
        return original(path, *argv, **kwargs)
    monkeypatch.setattr(git_module, 'git', slow_git)
    pending = asyncio.create_task(agent_action(app, source, 'worktree.create', args, 'admitted'))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert len(app.worktrees.git.records()) == 1
        assert app.worktrees.git.records()[0]['status'] == 'creating'
        # Stop and unrelated chats remain responsive during slow Git. The
        # admitted checkout settles; cancellation never releases its owner early.
        async def stop_and_publish():
            async with app.lock:
                change_authority(app, source, target, 'manager_stop')
                app._publish_changes(sessions={independent['id']})
        await asyncio.wait_for(stop_and_publish(), 2)
        pending.cancel()
        await asyncio.sleep(0)
        assert app.worktrees.mutations.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        record, = app.worktrees.git.records()
        assert record['status'] == 'ready'
        assert Path(record['path'], 'a.txt').read_text() == 'original\n'
        assert not app.worktrees.mutations.locked()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_manager_status_never_settles_members_uncertain_archive(app, tmp_path, monkeypatch):
    source, target, _, root = await prepared(app, tmp_path)
    record = await checkout(app, source, target, root)
    record.update(status='archive_unknown', archiveOperations={
        'unfinished': {'status': 'unknown', 'source': record['path'], 'target': record['path'] + '-archive'}})
    app.worktrees.git.save(record)
    path = app.worktrees.git.record_path(record['id'])
    before = path.read_bytes()
    settle = Mock(side_effect=AssertionError('Manager status must be observational'))
    monkeypatch.setattr(app.worktrees.git, '_settle_relocation', settle)
    result = await agent_action(app, source, 'worktree.status',
        {'sessionId': target['id'], 'id': record['id']}, 'status')
    assert result['result']['status'] == 'archive_unknown'
    assert path.read_bytes() == before
    settle.assert_not_called()


@pytest.mark.parametrize('action', ['worktree.create', 'worktree.attach'])
async def test_duplicate_preparation_rechecks_scope_without_new_effect(app, tmp_path, monkeypatch, action):
    source, target, _, root = await prepared(app, tmp_path)
    args = preparation(app, target, root, action)
    await agent_action(app, source, action, args, 'same-command')
    before = app.worktrees.git.records()
    entered, release = hold(monkeypatch, app.worktrees.git, 'lock', context=True)
    pending = asyncio.create_task(agent_action(app, source, action, args, 'same-command'))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with app.lock:
            change_authority(app, source, target, 'detached')
        release.set()
        with pytest.raises(AppError):
            await pending
        assert app.worktrees.git.records() == before
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_list_rechecks_relationship_after_waiting_for_publication_lock(app, tmp_path, monkeypatch):
    source, target, _, root = await prepared(app, tmp_path)
    entered = asyncio.Event()
    original = app.worktrees.git.records
    def observed():
        entered.set()
        return original()
    monkeypatch.setattr(app.worktrees.git, 'records', observed)
    before = copy.deepcopy(target.get('worktreeInspection'))
    async with app.lock:
        pending = asyncio.create_task(agent_action(app, source, 'worktree.list',
            {'sessionId': target['id']}, 'list'))
        await asyncio.wait_for(entered.wait(), 5)
        change_authority(app, source, target, 'detached')
    with pytest.raises(AppError):
        await pending
    assert target.get('worktreeInspection') == before
