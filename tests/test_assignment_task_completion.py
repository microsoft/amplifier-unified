"""Managers close accepted contributions through native task CAS, without input."""
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.service import AppError
from amplifier_web.workspace_resources import reservation_command
from test_assignment_task_limits import limited, read
from test_assignments import app, agent_action, generation
from test_task_continuity import controls


def arguments(ctx, **extra):
    task = ctx.native.tasks.record()
    return {'sessionId': ctx.source['id'], 'targetSessionId': ctx.target['id'],
        'expectedAssignmentRevision': ctx.app.assignments.current(ctx.source)['revision'],
        'taskId': task['id'], 'expectedRevision': task['revision'],
        'evidence': ['Accepted exact contribution in reviewed release; original incident tracked separately.'],
        **extra}


async def complete(ctx, args=None, identity='accepted-contribution'):
    return await agent_action(ctx.app, ctx.source, 'assignment.task.complete',
                              args or arguments(ctx), identity)


@pytest.mark.parametrize('status', ['active', 'blocked', 'paused'])
async def test_owner_completes_exact_contribution_without_model_turn_or_custody_changes(limited, status):
    ctx = limited
    if status != 'active':
        await ctx.native.perform('task.block' if status == 'blocked' else 'task.pause', {
            'commandId': 'prior-stop', 'expectedRevision': 1, 'origin': 'ui',
            **({'reason': 'Waiting for final integration'} if status == 'blocked' else {})})
    ctx.target.update(status='stopped', interruptionRevision=3,
                      lastInterruption={'kind': 'user_stop', 'at': 1})
    actor = {'sessionId': ctx.target['id'], 'principal': ctx.target['id']}
    held = reservation_command(ctx.app.data_dir, ctx.target['workspace'],
        'workspace.resources.reserve', {'resourceKey': 'retained-old-evidence'}, actor, 'held-resource')
    resource_view = lambda: reservation_command(ctx.app.data_dir, ctx.target['workspace'],
        'workspace.resources.reservations', {}, actor)
    resources_before = resource_view()
    ctx.app.collaboration.insert('unknown-external-operation', 'unchanged', {
        'status': 'unknown', 'senderSessionId': ctx.target['id'], 'commandAction': 'prior-operation'})
    operation_before = ctx.app.collaboration.receipt('unknown-external-operation')
    task_before = copy.deepcopy(ctx.native.tasks.record())
    goal_before = copy.deepcopy(ctx.native.coordinator.session_state.get('goal') or task_before['goalState'])
    assignment_before = copy.deepcopy(ctx.app.assignments.current(ctx.source))
    budget_before = copy.deepcopy(ctx.native.capacity.policy)
    stop_before = copy.deepcopy(ctx.target['lastInterruption'])
    current = await read(ctx)
    assert current['task']['objective'] == task_before['objective']
    assert not ctx.prepared and not ctx.calls
    # An ordinary peer-awakened manager still owns its assignment; no new human grant.
    await generation(ctx.app, ctx.source, 'acceptance-return', ['verified-return'])
    ctx.app.runtime.inputs.clear()
    request = arguments(ctx)
    result = await complete(ctx, request)
    task = ctx.native.tasks.record()
    assert task['id'] == task_before['id'] and task['revision'] == task_before['revision'] + 1
    assert task['status'] == 'completed' and task['completionEvidence'] == request['evidence']
    assert task['goalState'] == goal_before and ctx.native.coordinator.session_state['goal'] is None
    for field in ('objective', 'constraints', 'questionIds', 'operationIds', 'artifactRefs', 'maxTurns'):
        assert task[field] == task_before[field]
    assert ctx.native.capacity.policy == budget_before
    assert ctx.app.assignments.current(ctx.source) == assignment_before
    assert ctx.target['status'] == 'stopped' and ctx.target['interruptionRevision'] == 3
    assert ctx.target['lastInterruption'] == stop_before and not ctx.app.runtime.inputs
    assert resource_view() == resources_before and held
    assert ctx.app.collaboration.receipt('unknown-external-operation') == operation_before
    assert result['result']['inputSent'] is False
    receipt = result['result']['receipt']
    assert receipt['action'] == 'task.complete'
    assert receipt['managerCompletion']['ownerSessionId'] == ctx.source['id']
    assert (await complete(ctx, request))['duplicate'] and len(ctx.calls) == 1
    assert (await read(ctx, commandId='accepted-contribution'))['receipt'] == receipt
    restored = controls(ctx.target['id'])
    try:
        await restored.restore()
        assert restored.tasks.record() == task
        assert restored.tasks.receipts['accepted-contribution']['receipt'] == receipt
        assert restored.coordinator.session_state['goal'] is None
    finally:
        await restored.close()


@pytest.mark.parametrize('invalid', ['owner', 'member', 'assignment-revision', 'task-id',
                                    'task-revision', 'evidence', 'private-proof'])
async def test_closeout_rejects_wrong_owner_or_changed_task(limited, invalid):
    ctx = limited
    args = arguments(ctx)
    assignment = ctx.app.assignments.current(ctx.source)
    if invalid == 'owner': assignment['ownerSessionId'] = ctx.target['id']
    elif invalid == 'member': assignment['members'][ctx.target['id']]['status'] = 'detached'
    elif invalid == 'assignment-revision': args['expectedAssignmentRevision'] += 1
    elif invalid == 'task-id': args['taskId'] = 'replaced'
    elif invalid == 'task-revision': args['expectedRevision'] += 1
    elif invalid == 'evidence': args['evidence'] = []
    else: args['_managerCompletion'] = {'ownerSessionId': ctx.source['id']}
    ctx.app.assignments.save(assignment)
    before = copy.deepcopy(ctx.native.tasks.record())
    with pytest.raises(AppError):
        await complete(ctx, args)
    assert ctx.native.tasks.record() == before and not ctx.calls


@pytest.mark.parametrize('change', ['source-stop', 'target-stop', 'member', 'settings', 'objective'])
async def test_closeout_rechecks_scope_and_native_task_after_preparation(limited, change):
    ctx = limited
    async def prepare(session, *, restart_guard=None):
        if change == 'source-stop': ctx.source['interruptionRevision'] = 1
        elif change == 'target-stop': ctx.target['interruptionRevision'] = 1
        elif change == 'settings': ctx.target['configurationPendingRevision'] = 1
        elif change == 'member':
            value = ctx.app.assignments.current(ctx.source)
            value['members'][ctx.target['id']]['status'] = 'retired'
            ctx.app.assignments.save(value)
        else:
            await ctx.native.perform('task.update', {'commandId': 'new-objective',
                'expectedRevision': 1, 'objective': 'A newer unfinished objective'})
    ctx.app.management.ensure_runtime = prepare
    with pytest.raises(AppError):
        await complete(ctx)
    assert ctx.native.tasks.record()['status'] != 'completed'
    assert 'accepted-contribution' not in ctx.native.tasks.receipts


@pytest.mark.parametrize('busy', ['generation', 'queued-input', 'child'])
async def test_closeout_uses_actual_native_idle_proof(limited, busy):
    ctx = limited
    if busy == 'generation': ctx.native.runtime.generation = SimpleNamespace(id='running')
    elif busy == 'queued-input': ctx.native.runtime.queued_inputs = 1
    else: ctx.native.coordinator.register_capability('live.children', SimpleNamespace(rows={'child': {'status': 'running'}}))
    before = copy.deepcopy(ctx.native.tasks.record())
    with pytest.raises(AppError):
        await complete(ctx)
    assert ctx.native.tasks.record() == before and not ctx.app.runtime.inputs


async def test_lost_closeout_reply_reads_committed_receipt_without_repeating_effect(limited):
    ctx = limited
    request = arguments(ctx)
    original = ctx.app.runtime.control
    async def lost(sid, operation, values, *, guard=None):
        await original(sid, operation, values, guard=guard)
        raise TimeoutError('Lost after native commit')
    ctx.app.runtime.control = lost
    with pytest.raises(TimeoutError):
        await complete(ctx, request)
    saved = await read(ctx, commandId='accepted-contribution')
    assert saved['task']['status'] == 'completed'
    assert saved['receipt']['managerCompletion']['targetSessionId'] == ctx.target['id']
    assert (await complete(ctx, request))['duplicate'] and len(ctx.calls) == 1
    with pytest.raises(AppError, match='different contents'):
        await complete(ctx, {**request, 'evidence': ['Different claimed acceptance']})
    assert not ctx.app.runtime.inputs


async def test_general_cross_chat_completion_stays_caller_owned(limited):
    ctx = limited
    with pytest.raises(AppError, match='calling conversation'):
        await agent_action(ctx.app, ctx.source, 'task.complete', {
            'sessionId': ctx.target['id'], 'expectedRevision': 1,
            'condition': ctx.native.tasks.record()['objective'], 'evidence': ['Claimed']}, 'foreign-complete')
    assert not ctx.calls
