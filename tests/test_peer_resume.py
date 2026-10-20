"""Human-directed recovery uses ordinary peer admission, never forged UI input."""
import copy

import pytest

from amplifier_web.service import AppError, AppService
from test_assignments import app, child, generation, agent_action, until
from test_collaborative_workspaces import settled


async def paused_member(app):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='stop-member')
    await until(lambda: target['status'] == 'stopped')
    await app.on_runtime_event('runtime.ended', {'sessionId': target['id'], 'rootSessionId': target['id']})
    human = app._message(source, 'user', 'Resume the chats we paused for the update.',
                         inputId='human-resume', inputOrigin='ui')
    await generation(app, source, 'resume-generation', ['human-resume'])
    app.runtime.inputs.clear()
    return source, target, human


def arguments(target, human):
    return {'sessionId': target['id'], 'text': 'Reconcile finished work, then continue the existing task.',
            'mode': 'queue', 'resume': {'sourceMessageId': human['id'],
                                     'expectedInterruptionRevision': target['interruptionRevision']}}


async def submit(app, source, target, human, identity='resume-member', **changes):
    return await agent_action(app, source, 'coordination.send',
                              {**arguments(target, human), **changes}, identity)


async def test_resume_is_one_attributed_input_and_preserves_state(app):
    source, target, human = await paused_member(app)
    # A blocked goal is not silently rewritten by delivery.
    target['task'] = {'id': 'saved-task', 'revision': 7, 'status': 'blocked',
                      'maxTurns': 8, 'goalState': {'turns_used': 3},
                      'operationIds': ['unknown-effect']}
    before = copy.deepcopy(target['task'])
    assignment = copy.deepcopy(app.assignments.current(source))
    view = copy.deepcopy(app.state['view'])
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    await submit(app, source, target, human)
    assert len(app.runtime.inputs) == 1
    assert receipt['humanResume']['sourceMessageId'] == human['id']
    assert target['interruptionRevision'] == 1 and target['task'] == before
    assert app.assignments.current(source) == assignment and app.state['view'] == view
    message = next(m for m in target['messages'] if m.get('inputId') == 'resume-member')
    assert message['inputOrigin'] == 'peer' and message['peerEnvelope']['humanResume']
    with pytest.raises(AppError, match='already has receipt'):
        await submit(app, source, target, human, identity='do-not-replay')
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('invalid', ['peer', 'old_input', 'wrong_revision', 'older_than_stop',
    'question', 'schedule', 'nonowner', 'independent', 'detached', 'completed', 'error',
    'stopping', 'notify', 'steer'])
async def test_resume_requires_exact_human_and_commissioned_scope(app, invalid):
    source, target, human = await paused_member(app)
    args = arguments(target, human)
    if invalid == 'peer': human['inputOrigin'] = 'peer'
    elif invalid == 'old_input': await generation(app, source, 'later', ['unrelated'])
    elif invalid == 'wrong_revision': args['resume']['expectedInterruptionRevision'] = 0
    elif invalid == 'older_than_stop': human['createdAt'] = target['lastInterruption']['at'] - 1
    elif invalid == 'question': human['questionId'] = 'pending-question'
    elif invalid == 'schedule': human['scheduledRunId'] = 'scheduled'
    elif invalid in {'nonowner', 'independent', 'detached'}:
        value = app.assignments.current(source)
        if invalid == 'nonowner': value['ownerSessionId'] = target['id']
        elif invalid == 'independent': target['collaboration'].pop('assignmentId')
        else: value['members'][target['id']]['status'] = 'detached'
        app.assignments.save(value)
    elif invalid == 'completed': target['task'] = {'id': 'done', 'revision': 3, 'status': 'completed'}
    elif invalid in {'error', 'stopping'}: target['status'] = invalid
    else: args['mode'] = invalid
    with pytest.raises(AppError):
        await agent_action(app, source, 'coordination.send', args, 'invalid')
    assert not app.runtime.inputs
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='invalid'").fetchone()


@pytest.mark.parametrize('change', ['target_stop', 'source_stop', 'human', 'target_human',
                                  'assignment', 'task', 'permission'])
async def test_resume_rechecks_after_preparation(app, change):
    source, target, human = await paused_member(app)
    async def delayed(session, args, guard, emit):
        if change == 'target_stop': target['interruptionRevision'] += 1
        elif change == 'source_stop': source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
        elif change == 'human': app._message(source, 'user', 'Wait, do not continue.', inputOrigin='ui')
        elif change == 'target_human': app._message(target, 'user', 'New instructions', inputOrigin='ui')
        elif change == 'assignment':
            value = app.assignments.current(source); value['revision'] += 1; app.assignments.save(value)
        elif change == 'task': target['task'] = {'id': 'changed', 'revision': 1, 'status': 'active'}
        else: target['fileAccessPending'] = True
        assert guard()
        permission = app.collaboration.admission(target['id'], args)
        assert not permission['admitted']
        return {'accepted': False, 'reason': guard()}
    app.runtime.collaboration_input = delayed
    await submit(app, source, target, human)
    await settled(app, 'resume-member', 'suppressed')
    assert not app.runtime.inputs


async def test_ordinary_send_still_cannot_resume_and_old_suppression_stays_saved(app):
    source, target, human = await paused_member(app)
    args = arguments(target, human); args.pop('resume')
    await agent_action(app, source, 'coordination.send', args, 'old-input')
    before = copy.deepcopy(await settled(app, 'old-input', 'suppressed'))
    await submit(app, source, target, human)
    await settled(app, 'resume-member', 'accepted')
    assert app.collaboration.receipt('old-input') == before
    assert len(app.runtime.inputs) == 1 and app.runtime.inputs[0][1] == 'resume-member'


async def test_unknown_resume_is_not_replayed_after_restart(app):
    source, target, human = await paused_member(app)
    app.runtime.fail = True
    await submit(app, source, target, human)
    before = copy.deepcopy(await settled(app, 'resume-member', 'unknown'))
    assert len(app.runtime.inputs) == 1
    await app.close()
    other = AppService(app.data_dir, workspace=source['workspace'])
    try:
        assert other.collaboration.receipt('resume-member') == before
        assert not other.collaboration.fair_rows(target['id'])
        assert other.collaboration.duplicate('resume-member', other.db.execute(
            "SELECT fingerprint FROM commands WHERE id='resume-member'").fetchone()[0])['duplicate']
    finally:
        await other.close()
