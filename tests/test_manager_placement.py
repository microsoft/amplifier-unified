"""Manager placement uses commissioned membership and the native handoff owner."""
import asyncio
import copy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, agent_action, finish, generation
from test_peer_creation_operation import plan, commission, initial_accepted
from test_worktrees import repository, settle


async def prepared(app, tmp_path):
    root = repository(tmp_path)
    source, independent = app.state['sessions']
    plan(app, source)
    created = await commission(app, source, returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    target = app._session(created['sessionId'])
    await generation(app, target, 'recipient-' + created['initialInputId'], [created['initialInputId']])
    await finish(app, target, created['initialInputId'])
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})
    app.runtime.quiesce_for_handoff = AsyncMock(return_value={'quiesced': True})
    app.runtime.handoff_boundary = AsyncMock(return_value={'ready': True})
    app.runtime.inputs.clear()
    return source, target, independent, root


async def checkout(app, source, target, root):
    inspected = (await agent_action(app, source, 'worktree.inspect',
        {'sessionId': target['id'], 'repositoryPath': str(root)}, 'inspect'))['result']
    return (await agent_action(app, source, 'worktree.create', {
        'sessionId': target['id'], 'repositoryPath': str(root),
        'sourceRevision': inspected['repository']['sourceRevision']}, 'create'))['result']


async def move(app, source, target, record, identity='move'):
    return (await agent_action(app, source, 'worktree.handoff', {
        'sessionId': target['id'], 'id': record['id'], 'expectedExecutionRevision': 0},
        identity))['result']


async def test_manager_places_idle_member_without_moving_itself_or_independent_chat(app, tmp_path):
    source, target, independent, root = await prepared(app, tmp_path)
    before = copy.deepcopy(independent)
    original = {key: copy.deepcopy(target.get(key)) for key in
                ('workspace', 'messages', 'task', 'artifactRefs')}
    assignment = copy.deepcopy(app.assignments.current(source))
    view = copy.deepcopy(app.state['view'])
    record = await checkout(app, source, target, root)
    pending = await move(app, source, target, record)
    await settle(app)
    result = app.worktrees.read(target['id'], pending['id'])
    assert result['phase'] == 'applied'
    assert result['managerPlacement']['sourceSessionId'] == source['id']
    assert target['workingDirectory'] == record['path']
    assert Path(record['path'], 'a.txt').read_text() == 'original\n'
    assert source.get('workingDirectory', source['workspace']) == str(tmp_path)
    assert independent == before and app.state['view'] == view
    assert {key: target.get(key) for key in original} == original
    assert app.assignments.current(source) == assignment
    assert (await move(app, source, target, record))['duplicate']
    assert app.runtime.quiesce_for_handoff.await_count == 1
    # One notification belongs to the requester, never a replay in the member.
    notice = app.collaboration.receipt(result['continuation']['requestId'])
    assert notice['senderSessionId'] == source['id']
    assert notice['handoffSessionId'] == target['id']
    assert all(row[0] != target['id'] for row in app.runtime.inputs)


@pytest.mark.parametrize('invalid', ['independent', 'nonowner', 'detached', 'stopped_assignment',
                                   'working', 'stopped', 'error', 'child', 'stale_generation'])
async def test_manager_scope_does_not_authorize_other_work(app, tmp_path, invalid):
    source, target, independent, root = await prepared(app, tmp_path)
    if invalid == 'independent':
        target = independent
    elif invalid in {'nonowner', 'detached', 'stopped_assignment'}:
        assignment = app.assignments.current(source)
        if invalid == 'nonowner': assignment['ownerSessionId'] = target['id']
        elif invalid == 'detached': assignment['members'][target['id']]['status'] = 'detached'
        else: assignment['status'] = 'stopped'
        app.assignments.save(assignment)
    elif invalid in {'working', 'stopped', 'error'}:
        target['status'] = invalid
    elif invalid == 'stale_generation':
        source['collaborationGeneration']['terminal'] = True
    if invalid == 'child':
        with pytest.raises(AppError):
            await app.app_bridge('dispatch', {
                'action': 'worktree.create',
                'args': {'sessionId': target['id'], 'repositoryPath': str(root), 'sourceRevision': 'x'},
                '_runtimeSessionId': 'native-child'}, source['id'])
    else:
        with pytest.raises(AppError):
            await checkout(app, source, target, root)
    assert not app.worktrees.git.records()
    app.runtime.quiesce_for_handoff.assert_not_awaited()


@pytest.mark.parametrize('change', ['assignment', 'manager_stop', 'target_stop', 'manager_human',
                                  'target_human', 'manager_task', 'target_task'])
async def test_manager_authority_is_rechecked_at_native_boundary(app, tmp_path, change):
    source, target, independent, root = await prepared(app, tmp_path)
    record = await checkout(app, source, target, root)
    async def boundary(*args, **kwargs):
        if change == 'assignment':
            value = app.assignments.current(source)
            value['revision'] += 1
            app.assignments.save(value)
        elif change.endswith('_stop'):
            row = source if change.startswith('manager') else target
            row['interruptionRevision'] = row.get('interruptionRevision', 0) + 1
        elif change.endswith('_human'):
            app._message(source if change.startswith('manager') else target,
                         'user', 'Wait', inputOrigin='ui')
        else:
            (source if change.startswith('manager') else target)['task'] = {'id': 'new', 'revision': 1}
        return {'ready': True}
    app.runtime.handoff_boundary = boundary
    pending = await move(app, source, target, record)
    await settle(app)
    assert app.worktrees.read(target['id'], pending['id'])['phase'] == 'rejected'
    assert target.get('workingDirectory', target['workspace']) == str(tmp_path)
    app.runtime.quiesce_for_handoff.assert_not_awaited()
    notice = app.collaboration.receipt('worktree-wait:' + pending['id'])
    assert notice['subscription']['status'] == 'suppressed'
    assert not notice['subscription']['open']


@pytest.mark.parametrize('action', ['worktree.remove', 'worktree.archive', 'worktree.restore',
                                  'worktree.reconcile'])
async def test_manager_placement_does_not_grant_cleanup_or_reconciliation(app, tmp_path, action):
    source, target, _, root = await prepared(app, tmp_path)
    with pytest.raises(AppError):
        await agent_action(app, source, action, {'sessionId': target['id'], 'id': 'unknown',
            'expectedRevision': 1, 'destination': 'source', 'evidence': 'guess'}, 'denied')


async def test_pending_retry_reads_same_receipt_and_reserves_one_manager_return(app, tmp_path):
    source, target, _, root = await prepared(app, tmp_path)
    record = await checkout(app, source, target, root)
    ready = asyncio.Event()
    async def boundary(*args, **kwargs):
        await ready.wait()
        return {'ready': True, 'continuationAllowed': False}
    app.runtime.handoff_boundary = boundary
    pending = await move(app, source, target, record)
    try:
        assert (await move(app, source, target, record))['duplicate']
        saved = app.collaboration.receipt('worktree-wait:' + pending['id'])
        assert saved['subscription']['open'] and 'terminalOutcome' not in saved
        assert saved['sourceInputIds'] == ['incoming-peer']
        assert not app.runtime.inputs
    finally:
        ready.set()
    await settle(app)
    saved = app.collaboration.receipt('worktree-wait:' + pending['id'])
    assert saved['terminalOutcome']['outcome'] == 'checkout-applied'
    assert app.runtime.quiesce_for_handoff.await_count == 1


async def test_unknown_release_reports_to_manager_without_replaying_member_work(app, tmp_path):
    source, target, _, root = await prepared(app, tmp_path)
    record = await checkout(app, source, target, root)
    app.runtime.quiesce_for_handoff.side_effect = RuntimeError('Lost release acknowledgement')
    pending = await move(app, source, target, record)
    await settle(app)
    saved = app.worktrees.read(target['id'], pending['id'])
    assert saved['phase'] == 'unknown' and target['configurationBusy']
    notice = app.collaboration.receipt(saved['continuation']['requestId'])
    assert notice['terminalOutcome']['outcome'] == 'checkout-unknown'
    assert not notice['terminalOutcome']['qualified']
    assert (await move(app, source, target, record))['duplicate']
    assert app.runtime.quiesce_for_handoff.await_count == 1
    assert all(row[0] != target['id'] for row in app.runtime.inputs)


async def test_manager_can_attach_then_place_its_idle_member(app, tmp_path):
    source, target, _, root = await prepared(app, tmp_path)
    record = (await agent_action(app, source, 'worktree.attach', {
        'sessionId': target['id'], 'repositoryPath': str(root), 'path': str(root)}, 'attach'))['result']
    pending = await move(app, source, target, record)
    await settle(app)
    assert app.worktrees.read(target['id'], pending['id'])['phase'] == 'applied'
    assert target['workingDirectory'] == str(root)


async def test_restart_never_replays_an_unknown_manager_move(app, tmp_path, monkeypatch):
    source, target, _, root = await prepared(app, tmp_path)
    record = await checkout(app, source, target, root)
    monkeypatch.setattr(app.worktrees, 'launch', Mock())
    pending = await move(app, source, target, record)
    # Crash after beginning release, before any confirmation.
    pending['step'] = 'releasing'
    app.worktrees.save(pending)
    await app.close()
    runtime = app.runtime
    other = runtime.app = AppService(app.data_dir, runtime, workspace=tmp_path)
    try:
        other.worktrees.start()
        await settle(other)
        saved = other.worktrees.read(target['id'], pending['id'])
        assert saved['phase'] == 'unknown'
        assert other.worktrees.execution_state(target['id'])['fenced']
        assert other._session(target['id']).get('workingDirectory', str(tmp_path)) == str(tmp_path)
        runtime.quiesce_for_handoff.assert_not_awaited()
        assert all(row[0] != target['id'] for row in runtime.inputs)
    finally:
        await other.close()


@pytest.mark.parametrize('user_stop', [False, True])
async def test_native_release_stop_is_distinct_from_a_new_user_stop(app, tmp_path, user_stop):
    source, target, _, root = await prepared(app, tmp_path)
    record = await checkout(app, source, target, root)
    async def release(*args):
        await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'stopped'})
        if user_stop:
            target['interruptionRevision'] = target.get('interruptionRevision', 0) + 1
        return {'quiesced': True}
    app.runtime.quiesce_for_handoff = release
    pending = await move(app, source, target, record)
    await settle(app)
    saved = app.worktrees.read(target['id'], pending['id'])
    assert saved['phase'] == ('rejected' if user_stop else 'applied')
    assert target.get('workingDirectory', str(tmp_path)) == (str(tmp_path) if user_stop else record['path'])
