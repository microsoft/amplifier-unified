"""An exhausted member can receive an authorized control correction, not a replay."""
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.service import AppError
from test_assignments import app, agent_action, child, generation
from test_task_continuity import controls, create


@pytest.fixture
async def limited(app, monkeypatch):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    native = controls(target['id'])
    await create(native, maxTurns=8, origin='agent', questionIds=['unresolved-choice'])
    native.coordinator.session_state['goal'].update(turns_used=8, continuations=7)
    native.persist(task_only=True)
    target['task'] = copy.deepcopy(native.tasks.record())
    prepared, calls = [], []

    async def prepare(session, *, restart_guard=None):
        if restart_guard:
            restart_guard()
        prepared.append(session['id'])

    async def control(sid, operation, args, *, guard=None):
        assert sid == target['id']
        if guard:
            guard()
        calls.append((operation, copy.deepcopy(args)))
        return await native.perform(operation, args)

    monkeypatch.setattr(app.runtime, 'control', control, raising=False)
    original_management = app.management
    app.management = SimpleNamespace(ensure_runtime=prepare)
    app.runtime.inputs.clear()
    ctx = SimpleNamespace(app=app, source=source, target=target, native=native,
                          prepared=prepared, calls=calls)
    try:
        yield ctx
    finally:
        app.management = original_management
        await native.close()


def args(ctx, **changes):
    task = ctx.native.tasks.record()
    return {'sessionId': ctx.source['id'], 'targetSessionId': ctx.target['id'],
        'expectedAssignmentRevision': ctx.app.assignments.current(ctx.source)['revision'],
        'taskId': task['id'], 'expectedRevision': task['revision'], 'maxTurns': None,
        'correction': 'Remove the planning cap as already directed; preserve all completed work.',
        **changes}


def human(ctx):
    return ctx.app._message(ctx.source, 'user', 'Remove this member’s planning turn limit and finish the work.',
                           inputId='authorized-limit-change', inputOrigin='ui')


async def change(ctx, values=None, identity='correct-limit'):
    return await agent_action(ctx.app, ctx.source, 'assignment.task.setLimit',
                              values or args(ctx), identity)


async def read(ctx, **values):
    return (await agent_action(ctx.app, ctx.source, 'assignment.task.read', {
        'sessionId': ctx.source['id'], 'targetSessionId': ctx.target['id'], **values}, 'read-limit'))['result']


async def full_access(ctx, mode='full'):
    app = ctx.app
    # Exercise the canonical user-owned setting, including its inherited mode.
    await app.dispatch('access.set', {'sessionId': ctx.source['id'], 'scope': 'session',
        'mode': mode, 'expectedRevision': app.access_mode.snapshot(ctx.source)['revision']},
        command_id='access-' + mode)
    ctx.source['fileAccessPending'] = ctx.source['configurationPending'] = False


async def test_exhausted_limit_correction_preserves_work_and_requires_no_member_turn(limited):
    ctx = limited
    before = copy.deepcopy(ctx.native.tasks.record())
    capacity = copy.deepcopy(ctx.native.capacity.policy)
    assignment = copy.deepcopy(ctx.app.assignments.current(ctx.source))
    view = copy.deepcopy(ctx.app.state['view'])
    current = await read(ctx)
    assert current['turnsUsed'] == 8 and current['task']['maxTurns'] == 8
    assert not ctx.prepared and not ctx.calls
    request = args(ctx, sourceMessageId=human(ctx)['id'])
    result = await change(ctx, request)
    task, goal = ctx.native.tasks.record(), ctx.native.coordinator.session_state['goal']
    assert task['id'] == before['id'] and task['revision'] == before['revision'] + 1
    assert goal['turns_used'] == 8 and goal['continuations'] == 7 and goal['cap'] is None
    for field in ('objective', 'status', 'constraints', 'questionIds', 'operationIds', 'artifactRefs'):
        assert task[field] == before[field]
    assert ctx.native.capacity.policy == capacity
    assert ctx.app.assignments.current(ctx.source) == assignment
    assert ctx.app.state['view'] == view and not ctx.app.runtime.inputs
    assert result['result']['inputSent'] is False
    assert result['result']['receipt']['managerLimit']['authorization']['kind'] == 'human-message'
    again = await change(ctx, request)
    assert again['duplicate'] and len(ctx.calls) == 1
    retained = await read(ctx, commandId='correct-limit')
    assert retained['receipt'] == result['result']['receipt'] and retained['turnsUsed'] == 8


async def test_saved_full_access_supports_unattended_owner_control(limited):
    ctx = limited
    await full_access(ctx)
    # The manager's admitted input is an automatic/peer wake, not a new human turn.
    await generation(ctx.app, ctx.source, 'unattended', ['completion-notice'])
    result = await change(ctx)
    assert result['result']['receipt']['managerLimit']['authorization']['kind'] == 'full-access'
    assert ctx.native.coordinator.session_state['goal']['turns_used'] == 8
    assert not ctx.app.runtime.inputs


async def answered_question(ctx):
    app = ctx.app
    original = app.management
    app.management = None
    try:
        question = (await app.dispatch('question.create', {
            'sessionId': ctx.source['id'], 'prompt': 'Remove the planning cap?',
            'dependency': 'Finish the existing member task', 'required': True,
        }))['result']
        await app.dispatch('question.answer', {'sessionId': ctx.source['id'], 'id': question['id'],
            'expectedRevision': question['revision'], 'text': 'Yes, remove that cap and complete the task.'},
            origin='ui', command_id='user-answer')
        return app.questions.store.get(ctx.source['id'], question['id'])
    finally:
        app.management = original


async def test_already_answered_question_does_not_need_user_to_answer_again(limited):
    ctx = limited
    question = await answered_question(ctx)
    await generation(ctx.app, ctx.source, 'later-manager-turn', ['completion-notice'])
    ctx.app.runtime.inputs.clear()
    result = await change(ctx, args(ctx, questionId=question['id'],
                                   expectedQuestionRevision=question['revision']))
    assert result['result']['receipt']['managerLimit']['authorization']['kind'] == 'question'
    assert ctx.app.questions.store.get(ctx.source['id'], question['id']) == question
    assert not ctx.app.runtime.inputs


@pytest.mark.parametrize('invalid', ['missing', 'peer', 'unknown-message', 'stale-question', 'unverified-answer'])
async def test_cap_relaxation_requires_actual_saved_authority(limited, invalid):
    ctx = limited
    request = args(ctx)
    if invalid == 'peer':
        message = human(ctx); message['inputOrigin'] = 'peer'
        request['sourceMessageId'] = message['id']
    elif invalid == 'unknown-message':
        request['sourceMessageId'] = 'not-a-human-message'
    elif invalid in {'stale-question', 'unverified-answer'}:
        question = await answered_question(ctx)
        request.update(questionId=question['id'], expectedQuestionRevision=question['revision'])
        if invalid == 'stale-question':
            request['expectedQuestionRevision'] -= 1
        else:
            question['answer']['provenance'] = {'origin': 'agent'}
            ctx.app.questions.store.put(question)
    before = copy.deepcopy(ctx.native.tasks.record())
    with pytest.raises(AppError):
        await change(ctx, request)
    assert ctx.native.tasks.record() == before and not ctx.calls


@pytest.mark.parametrize('invalid', ['nonowner', 'independent', 'detached', 'assignment', 'task-id', 'task-revision'])
async def test_only_current_owner_exact_member_and_task_can_change_limit(limited, invalid):
    ctx = limited
    request = args(ctx, sourceMessageId=human(ctx)['id'])
    assignment = ctx.app.assignments.current(ctx.source)
    if invalid == 'nonowner': assignment['ownerSessionId'] = ctx.target['id']
    elif invalid == 'independent': ctx.target['collaboration'].pop('assignmentId')
    elif invalid == 'detached': assignment['members'][ctx.target['id']]['status'] = 'detached'
    elif invalid == 'assignment': request['expectedAssignmentRevision'] += 1
    elif invalid == 'task-id': request['taskId'] = 'replaced-task'
    else: request['expectedRevision'] += 1
    ctx.app.assignments.save(assignment)
    with pytest.raises(AppError):
        await change(ctx, request)
    assert not ctx.calls


@pytest.mark.parametrize('changed', ['source-stop', 'target-stop', 'member', 'task-replaced', 'task-revision',
                                    'authorization', 'configuration', 'source-settings', 'target-settings'])
async def test_preparation_cannot_cross_changed_scope(limited, changed):
    ctx = limited
    message = human(ctx)
    request = args(ctx, sourceMessageId=message['id'])

    async def prepare(session, *, restart_guard=None):
        if changed == 'source-stop': ctx.source['interruptionRevision'] = 1
        elif changed == 'target-stop': ctx.target['interruptionRevision'] = 1
        elif changed == 'member':
            value = ctx.app.assignments.current(ctx.source)
            value['members'][ctx.target['id']]['status'] = 'detached'
            ctx.app.assignments.save(value)
        elif changed == 'task-replaced':
            ctx.native.tasks.record()['id'] = 'different-task'
        elif changed == 'task-revision':
            ctx.native.tasks.record()['revision'] += 1
        elif changed == 'authorization': message['text'] = 'Keep the limit.'
        elif changed == 'source-settings': ctx.source['configurationPendingRevision'] = 1
        elif changed == 'target-settings': ctx.target['configurationPendingRevision'] = 1
        else: ctx.target['configurationBusy'] = True

    ctx.app.management.ensure_runtime = prepare
    with pytest.raises(AppError):
        await change(ctx, request)
    assert ctx.native.tasks.record()['maxTurns'] == 8


async def test_full_access_revoked_during_preparation_does_not_apply(limited):
    ctx = limited
    await full_access(ctx)
    async def prepare(session, *, restart_guard=None):
        await full_access(ctx, 'ask')
    ctx.app.management.ensure_runtime = prepare
    with pytest.raises(AppError):
        await change(ctx)
    assert ctx.native.tasks.record()['maxTurns'] == 8


async def test_limit_correction_keeps_pause_stop_and_receipt_after_native_restart(limited):
    ctx = limited
    await ctx.native.perform('task.pause', {'commandId': 'pause', 'expectedRevision': 1, 'origin': 'ui'})
    ctx.target.update(status='stopped', interruptionRevision=3, lastInterruption={'at': 1, 'reason': 'user'})
    interruption = copy.deepcopy(ctx.target['lastInterruption'])
    result = await change(ctx, args(ctx, sourceMessageId=human(ctx)['id'], maxTurns=12))
    assert ctx.native.tasks.record()['status'] == 'paused'
    assert ctx.native.coordinator.session_state['goal'] is None
    assert ctx.target['status'] == 'stopped' and ctx.target['interruptionRevision'] == 3
    assert ctx.target['lastInterruption'] == interruption and not ctx.app.runtime.inputs
    restored = controls(ctx.target['id'])
    try:
        await restored.restore()
        assert restored.tasks.record()['id'] == ctx.native.tasks.record()['id']
        assert restored.tasks.record()['goalState']['turns_used'] == 8
        assert restored.tasks.receipts['correct-limit']['receipt'] == result['result']['receipt']
        assert restored.coordinator.session_state['goal'] is None
    finally:
        await restored.close()


async def test_lost_response_reads_original_receipt_without_repeating_work(limited):
    ctx = limited
    request = args(ctx, sourceMessageId=human(ctx)['id'])
    original = ctx.app.runtime.control
    async def lose(sid, operation, fields, *, guard=None):
        await original(sid, operation, fields, guard=guard)
        raise TimeoutError('Receipt lost after native commit')
    ctx.app.runtime.control = lose
    with pytest.raises(TimeoutError):
        await change(ctx, request)
    retained = await read(ctx, commandId='correct-limit')
    assert retained['receipt']['revision'] == 2 and retained['turnsUsed'] == 8
    assert (await change(ctx, request))['duplicate']
    assert len(ctx.calls) == 1 and not ctx.app.runtime.inputs


async def test_general_cross_chat_task_update_remains_unavailable(limited):
    ctx = limited
    await full_access(ctx)
    with pytest.raises(AppError, match='calling conversation'):
        await agent_action(ctx.app, ctx.source, 'task.update', {
            'sessionId': ctx.target['id'], 'expectedRevision': 1, 'maxTurns': None}, 'arbitrary-update')
    assert not ctx.calls


async def test_committed_receipt_is_not_hidden_by_an_unrelated_late_progress_flush(limited, monkeypatch):
    ctx = limited
    original = ctx.app._flush_pending_progress
    async def flush():
        if ctx.calls:
            raise OSError('Unrelated progress publication failed')
        await original()
    monkeypatch.setattr(ctx.app, '_flush_pending_progress', flush)
    result = await change(ctx, args(ctx, sourceMessageId=human(ctx)['id']))
    assert result['accepted'] and result['result']['receipt']['revision'] == 2
    assert ctx.native.tasks.record()['maxTurns'] is None
