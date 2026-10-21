"""Existing-chat responsibility is explicit, atomic, and distinct from creation."""
import copy
import pytest

from amplifier_web.service import AppError
from amplifier_web.runtime import RuntimeManager
from amplifier_web import manager_placement
from amplifier_web.capacity import admission
from amplifier_web.execution import ingest
from test_assignments import app, start, call, generation, agent_action
from test_peer_resume import submit
from test_collaborative_workspaces import settled


async def review(app, source, target):
    return (await app.dispatch('assignment.adoption', {
        'sessionId': source['id'], 'targetSessionId': target['id']}))['result']


async def setup(app):
    source, target = app.state['sessions']
    await start(app)
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    target['status'] = 'stopped'
    target['interruptionRevision'] = 1
    target['lastInterruption'] = {'at': 1}
    human = app._message(source, 'user', 'Take responsibility for the existing UI chat and resume its remaining work.',
                         inputId='adopt-human', inputOrigin='ui')
    await generation(app, source, 'adopt-generation', ['adopt-human'])
    observed = await review(app, source, target)
    args = {'sessionId': source['id'], 'targetSessionId': target['id'],
            'expectedRevision': observed['assignmentRevision'],
            'expectedTargetRevision': observed['revision'], 'sourceMessageId': human['id']}
    return source, target, human, args


async def test_adopt_existing_stopped_chat_then_resume_once_without_creating_history(app):
    source, target, human, args = await setup(app)
    history = copy.deepcopy(target['messages'])
    view = copy.deepcopy(app.state['view'])
    receipt = await agent_action(app, source, 'assignment.adopt', args, 'adopt')
    member = app.assignments.current(source)['members'][target['id']]
    assert member['role'] == 'adopted'
    assert 'creationOperationId' not in member
    assert not target.get('collaboration')
    assert target['status'] == 'stopped' and target['messages'] == history
    assert not app.runtime.inputs and app.state['view'] == view
    assert receipt['result']['inputSent'] is False
    assert (await agent_action(app, source, 'assignment.adopt', args, 'adopt'))['duplicate']
    assert manager_placement.member(app, source, target)['id'] == receipt['assignmentId']
    await submit(app, source, target, human)
    await settled(app, 'resume-member', 'accepted')
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('change', ['stop', 'human', 'generation', 'assignment', 'workspace'])
async def test_stale_preview_cannot_adopt_changed_target(app, change):
    source, target, human, args = await setup(app)
    if change == 'stop':
        target['interruptionRevision'] += 1
    elif change == 'human':
        app._message(target, 'user', 'New independent direction', inputId='target-human', inputOrigin='ui')
    elif change == 'generation':
        await generation(app, target, 'new-work', ['new-input'])
    elif change == 'assignment':
        await app.dispatch('assignment.start', {'sessionId': target['id']}, command_id='other-assignment')
    else:
        target['workspace'] += '-other'
    with pytest.raises(AppError):
        await agent_action(app, source, 'assignment.adopt', args, 'adopt')
    assert target['id'] not in app.assignments.current(source)['members']
    assert not app.runtime.inputs


@pytest.mark.parametrize('invalid', ['peer', 'old', 'schedule', 'older-stop'])
async def test_agent_adoption_needs_current_actual_human_input(app, invalid):
    source, target, human, args = await setup(app)
    if invalid == 'peer':
        human['inputOrigin'] = 'peer'
    elif invalid == 'old':
        args['sourceMessageId'] = 'old-message'
    elif invalid == 'schedule':
        human['scheduledRunId'] = 'schedule'
    else:
        target['lastInterruption']['at'] = human['createdAt'] + 1
    with pytest.raises(AppError):
        await agent_action(app, source, 'assignment.adopt', args, 'adopt')
    assert app.assignments.current(target) is None


async def test_ui_adoption_keeps_prior_usage_separate_and_detachment_revokes_placement(app):
    source, target, _, args = await setup(app)
    old = call(target, 'before')
    assert (await admission(app, target['id'], {'call': old}))['allowed']
    ingest(target, {**old, 'phase': 'completed', 'endedAt': 1, 'revision': 2, 'usage': {'totalTokens': 9}})
    observed = await review(app, source, target)
    args['expectedTargetRevision'] = observed['revision']
    await app.dispatch('assignment.adopt', args, command_id='ui-adopt')
    new = call(target, 'after')
    assert (await admission(app, target['id'], {'call': new}))['allowed']
    ingest(target, {**new, 'phase': 'completed', 'endedAt': 2, 'revision': 2, 'usage': {'totalTokens': 4}})
    assignment = app.assignments.current(source)
    assert app.assignments.usage(assignment)['metrics']['grossTotalTokens']['value'] == 4
    await app.dispatch('assignment.detach', {'sessionId': target['id'],
                       'expectedRevision': assignment['revision']}, command_id='detach')
    with pytest.raises(ValueError):
        manager_placement.member(app, source, target)
    assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 4


async def test_failed_adoption_commit_restores_target_and_membership(app, monkeypatch):
    source, target, _, args = await setup(app)
    before = copy.deepcopy(app.assignments.current(source))
    old_publish = app._publish_changes
    def fail(**kwargs):
        raise OSError('Synthetic disk failure')
    monkeypatch.setattr(app, '_publish_changes', fail)
    with pytest.raises(OSError):
        await app.dispatch('assignment.adopt', args, command_id='failed-save')
    monkeypatch.setattr(app, '_publish_changes', old_publish)
    assert app.assignments.current(target) is None
    assert app.assignments.current(source) == before
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='failed-save'").fetchone()


async def test_stopped_target_with_live_native_owner_is_not_adopted(app):
    from amplifier_foundation.session import SharedSessionStore
    source, target, _, args = await setup(app)
    owner = SharedSessionStore(target['workspace'], target['id']).acquire(app='still-settling')
    try:
        with pytest.raises(AppError, match='confirm'):
            await app.dispatch('assignment.adopt', args, command_id='adopt')
        assert app.assignments.current(target) is None
    finally:
        owner.release()


async def test_adoption_receipt_cannot_be_forged_by_membership_metadata(app):
    source, target, _, args = await setup(app)
    await app.dispatch('assignment.adopt', args, command_id='adopt')
    value = app.assignments.current(source)
    value['members'][target['id']]['adoptionOperationId'] = 'missing'
    app.assignments.save(value)
    with pytest.raises(ValueError, match='receipt'):
        manager_placement.member(app, source, target)


async def test_stop_targets_adopted_member_without_touching_independent_chat(app):
    source, target, _, args = await setup(app)
    independent = app._new_session({'title': 'Independent'})
    app.state['sessions'].append(independent)
    before = copy.deepcopy(independent)
    await app.dispatch('assignment.adopt', args, command_id='adopt')
    result = await app.dispatch('assignment.stop', {
        'sessionId': source['id'], 'expectedRevision': app.assignments.current(source)['revision']},
        command_id='stop-adopted')
    assert set(result['interruptions']) == {source['id'], target['id']}
    assert independent == before


async def adoption_args(app, source, target, args):
    current = await review(app, source, target)
    return {**args, 'expectedRevision': current['assignmentRevision'],
            'expectedTargetRevision': current['revision']}


async def test_canonically_archived_chat_is_ineligible_without_side_effects(app):
    source, target, _, args = await setup(app)
    await app.dispatch('session.archive', {'id': target['id']}, command_id='archive-existing')
    observed = await review(app, source, target)
    assert not observed['eligible'] and 'Unarchive' in observed['reason']
    with pytest.raises(AppError, match='Unarchive'):
        await app.dispatch('assignment.adopt', args, command_id='archived-adopt')
    assert target['id'] in app.state['conversationOrganization']['archived']
    assert app.assignments.current(target) is None


@pytest.mark.parametrize('pending', [
    {'phase': 'error', 'effect': 'unknown', 'recoveryRequired': True, 'operationId': 'edit', 'requestHash': 'hash'},
    {'phase': 'error', 'effect': 'unknown', 'operationId': 'edit', 'requestHash': 'hash'},
    {'phase': 'error', 'effect': 'none'},
])
async def test_unresolved_configuration_is_ineligible(app, pending):
    source, target, _, args = await setup(app)
    target['pendingConfiguration'] = pending
    observed = await review(app, source, target)
    assert not observed['eligible'] and 'settings' in observed['reason']
    with pytest.raises(AppError, match='settings'):
        await app.dispatch('assignment.adopt', args, command_id='uncertain-settings')
    assert target['pendingConfiguration'] == pending
    assert app.assignments.current(target) is None


async def test_definite_no_effect_settings_failure_does_not_block_adoption(app):
    source, target, _, args = await setup(app)
    target['pendingConfiguration'] = {'phase': 'error', 'effect': 'none',
                                      'operationId': 'edit', 'requestHash': 'hash'}
    observed = await review(app, source, target)
    assert observed['eligible']
    assert (await app.dispatch('assignment.adopt', args, command_id='definite-settings'))['accepted']


async def test_runtime_control_task_change_invalidates_review(app):
    source, target, _, args = await setup(app)
    app.state.setdefault('runtimeControl', {}).setdefault(target['id'], {})['task.get'] = {
        'task': {'id': 'saved-task', 'revision': 1, 'status': 'paused'}}
    args = await adoption_args(app, source, target, args)
    app.state['runtimeControl'][target['id']]['task.get']['task']['revision'] = 2
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('assignment.adopt', args, command_id='adopt-stale-task')
    assert app.assignments.current(target) is None


@pytest.mark.parametrize('status', ['idle', 'stopped', 'error'])
async def test_native_foreign_writer_blocks_every_eligible_status(app, monkeypatch, status):
    from amplifier_foundation.session import SharedSessionStore
    from unittest.mock import AsyncMock
    source, target, _, args = await setup(app)
    target['status'] = status
    args = await adoption_args(app, source, target, args)
    release = AsyncMock(side_effect=AssertionError('must not release another owner'))
    monkeypatch.setattr('amplifier_foundation.session.request_release', release)
    held = SharedSessionStore(target['workspace'], target['id']).acquire(app='independent-cli')
    try:
        with pytest.raises(AppError, match='confirm'):
            await app.dispatch('assignment.adopt', args, command_id='adopt-live-native')
        assert held.active and app.assignments.current(target) is None
        release.assert_not_awaited()
    finally:
        held.release()


@pytest.mark.parametrize('status', ['idle', 'stopped', 'error'])
async def test_native_lease_is_held_through_membership_commit(app, monkeypatch, status):
    from amplifier_foundation.session import SharedSessionStore, SessionBusyError
    source, target, _, args = await setup(app)
    target['status'] = status
    args = await adoption_args(app, source, target, args)
    store = SharedSessionStore(target['workspace'], target['id'])
    publish = app._publish_changes
    checked = []
    def check_commit(**kwargs):
        with pytest.raises(SessionBusyError):
            store.acquire(app='competing-writer')
        checked.append(True)
        return publish(**kwargs)
    monkeypatch.setattr(app, '_publish_changes', check_commit)
    assert (await app.dispatch('assignment.adopt', args, command_id='held-commit'))['accepted']
    assert checked == [True]
    held = store.acquire(app='after-commit')
    held.release()


async def test_historical_detached_membership_preview_reports_why_it_cannot_be_added(app):
    source, target, _, args = await setup(app)
    await app.dispatch('assignment.adopt', args, command_id='adopt')
    assignment = app.assignments.current(source)
    await app.dispatch('assignment.detach', {'sessionId': target['id'],
                       'expectedRevision': assignment['revision']}, command_id='detach')
    observed = await review(app, source, target)
    assert not observed['eligible'] and 'earlier membership' in observed['reason']


async def warm_idle(app, target, monkeypatch, during_retirement=None):
    """Real retirement owner/locking, synthetic transport; never starts a model."""
    import os
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    manager = RuntimeManager()
    sid = target['id']
    row = {'parked': True, 'process': SimpleNamespace(returncode=None, pid=os.getpid()),
           'start_session': target, 'emit': app.on_runtime_event}
    manager.workers[sid] = row
    app.runtime.workers = manager.workers
    app.runtime._admission = manager._admission
    app.runtime.retention = manager.retention
    app.runtime.stopped_boundary = manager.stopped_boundary
    target['status'] = 'idle'
    async def retired(*args, **kwargs):
        assert not app.lock.locked(), 'retirement must allow runtime events to settle'
        if during_retirement:
            await during_retirement()
        return {'retired': True}
    async def stopped(identity, *, reason='stopped'):
        assert reason == 'retention'
        manager.workers.pop(identity)
        await app.on_runtime_event('runtime.warmth', {'sessionId': identity, 'status': 'cold'})
    monkeypatch.setattr(manager, '_request_unlocked', AsyncMock(side_effect=retired))
    monkeypatch.setattr(manager, 'stop', AsyncMock(side_effect=stopped))
    return manager


async def test_known_warm_idle_worker_retires_and_preserves_task_without_stop_or_input(app, monkeypatch):
    source, target, _, args = await setup(app)
    target['task'] = {'id': 'retained-task', 'status': 'paused', 'revision': 3}
    manager = await warm_idle(app, target, monkeypatch)
    args = await adoption_args(app, source, target, args)
    previous = copy.deepcopy(target)
    assert (await app.dispatch('assignment.adopt', args, command_id='warm-adopt'))['accepted']
    for key in ('task', 'status', 'messages', 'interruptionRevision', 'lastInterruption'):
        assert target[key] == previous[key]
    manager._request_unlocked.assert_awaited_once_with(target['id'], 'retire')
    assert not app.runtime.inputs and target['id'] not in manager.workers


@pytest.mark.parametrize('change', ['human', 'task', 'settings', 'stop', 'assignment', 'source-human'])
async def test_retirement_revalidates_late_changes_before_adoption(app, monkeypatch, change):
    source, target, _, args = await setup(app)
    async def changed():
        async with app.lock:
            if change == 'human':
                app._message(target, 'user', 'New independent instruction', inputOrigin='ui', inputId='late-human')
            elif change == 'task':
                app.state.setdefault('runtimeControl', {}).setdefault(target['id'], {})['task.get'] = {
                    'task': {'id': 'new-task', 'revision': 1}}
            elif change == 'settings':
                target['pendingConfiguration'] = {'phase': 'error', 'recoveryRequired': True}
            elif change == 'stop':
                target['interruptionRevision'] += 1
            elif change == 'source-human':
                app._message(source, 'user', 'Different manager direction', inputOrigin='ui', inputId='new-manager-human')
            else:
                value = app.assignments.current(source)
                value['revision'] += 1
                app.assignments.save(value)
    await warm_idle(app, target, monkeypatch, changed)
    args = await adoption_args(app, source, target, args)
    with pytest.raises(AppError):
        await agent_action(app, source, 'assignment.adopt', args, 'late-change')
    assert app.assignments.current(target) is None
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='late-change'").fetchone()


async def test_settled_dead_worker_row_does_not_block_adoption(app, monkeypatch):
    from types import SimpleNamespace
    source, target, _, args = await setup(app)
    manager = RuntimeManager()
    manager.workers[target['id']] = {'process': SimpleNamespace(returncode=1),
                                     'executionEnded': True}
    app.runtime.workers = manager.workers
    app.runtime.stopped_boundary = manager.stopped_boundary
    target['status'] = 'error'
    args = await adoption_args(app, source, target, args)
    assert (await app.dispatch('assignment.adopt', args, command_id='dead-row-adopt'))['accepted']
    assert target['status'] == 'error' and not app.runtime.inputs


async def test_native_writer_arriving_during_retirement_is_not_adopted(app, monkeypatch):
    from amplifier_foundation.session import SharedSessionStore
    source, target, _, args = await setup(app)
    owners = []
    async def racing_owner():
        owners.append(SharedSessionStore(target['workspace'], target['id']).acquire(app='racing-writer'))
    await warm_idle(app, target, monkeypatch, racing_owner)
    args = await adoption_args(app, source, target, args)
    try:
        with pytest.raises(AppError, match='confirm'):
            await app.dispatch('assignment.adopt', args, command_id='racing-native')
        assert owners[0].active and app.assignments.current(target) is None
    finally:
        for owner in owners:
            owner.release()


async def test_retirement_refusal_keeps_membership_unchanged(app, monkeypatch):
    from unittest.mock import AsyncMock
    source, target, _, args = await setup(app)
    manager = await warm_idle(app, target, monkeypatch)
    monkeypatch.setattr(manager, '_request_unlocked', AsyncMock(return_value={'retired': False}))
    args = await adoption_args(app, source, target, args)
    with pytest.raises(AppError, match='confirm'):
        await app.dispatch('assignment.adopt', args, command_id='retirement-refused')
    assert app.assignments.current(target) is None
    manager.stop.assert_not_awaited()


async def test_concurrent_exact_retry_behind_retirement_returns_committed_receipt(app, monkeypatch):
    import asyncio
    source, target, _, args = await setup(app)
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed():
        entered.set()
        await release.wait()
    manager = await warm_idle(app, target, monkeypatch, delayed)
    args = await adoption_args(app, source, target, args)
    first = asyncio.create_task(app.dispatch('assignment.adopt', args, command_id='same-adoption'))
    await entered.wait()
    second = asyncio.create_task(app.dispatch('assignment.adopt', args, command_id='same-adoption'))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)
    assert all(row['accepted'] for row in results)
    assert sum(bool(row.get('duplicate')) for row in results) == 1
    assert results[0]['membership'] == results[1]['membership']
    manager._request_unlocked.assert_awaited_once_with(target['id'], 'retire')
