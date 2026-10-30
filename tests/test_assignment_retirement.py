"""Finished participation releases capacity, not history or spending."""
import copy

import pytest

from amplifier_web.capacity import admission
from amplifier_web.execution import ingest
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError
from test_assignments import app, child, limits, call, agent_action, generation
from test_collaborative_workspaces import settled


async def setup(app):
    source = app.state['sessions'][0]
    target, created = await child(app, source)
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    await settle(app, target)
    return source, target, created


async def settle(app, target):
    active = target['collaborationGeneration']
    await app.on_runtime_event('runtime.generation', {
        'sessionId': target['id'], 'rootSessionId': target['id'],
        'generation_id': active['id'], 'input_ids': active['inputIds'],
        'event': 'generation.finished', 'text': 'Finished work',
        'disposition': 'manager_turn_finished', 'active_job_ids': [],
    })
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})


async def retire(app, source, target, identity='retire'):
    args = {'sessionId': source['id'], 'targetSessionId': target['id'],
            'expectedRevision': app.assignments.current(source)['revision']}
    return await agent_action(app, source, 'assignment.retire', args, identity)


async def adoption_args(app, source, target):
    human = app._message(source, 'user', 'Use the finished chat for the next follow-up.',
                         inputId='follow-up-human', inputOrigin='ui')
    await generation(app, source, 'follow-up-generation', [human['inputId']])
    observation = (await app.dispatch('assignment.adoption', {
        'sessionId': source['id'], 'targetSessionId': target['id']}))['result']
    assert observation['eligible'], observation['reason']
    return {'sessionId': source['id'], 'targetSessionId': target['id'],
            'expectedRevision': observation['assignmentRevision'],
            'expectedTargetRevision': observation['revision'], 'sourceMessageId': human['id']}


async def test_retirement_releases_slot_preserving_history_usage_and_stop(app):
    source, target, created = await setup(app)
    await limits(app, source, maxMembers=2)
    row = call(target, 'charged')
    assert (await admission(app, target['id'], {'call': row}))['allowed']
    ingest(target, {**row, 'phase': 'completed', 'endedAt': 4, 'revision': 2,
                   'usage': {'totalTokens': 15}})
    target.update(status='stopped', interruptionRevision=1, lastInterruption={'at': 1, 'kind': 'user_stop'})
    before = copy.deepcopy(target)
    original = copy.deepcopy(app.collaboration.receipt(created['initialInputId']))
    inputs = len(app.runtime.inputs)
    receipt = await retire(app, source, target)
    assignment = app.assignments.current(source)
    assert assignment['members'][target['id']]['status'] == 'retired'
    assert app.assignments.current(target) is None
    assert app.assignments.active_member_count(assignment) == 1
    assert receipt['result']['inputSent'] is False
    assert target['messages'] == before['messages']
    assert target.get('lastInterruption') == before.get('lastInterruption')
    assert target.get('interruptionRevision') == before.get('interruptionRevision')
    assert len(app.runtime.inputs) == inputs
    assert app.collaboration.receipt(created['initialInputId']) == original
    assert app.assignments.usage(assignment)['metrics']['grossTotalTokens']['value'] == 15
    newer, _ = await child(app, source, 'new-member')
    assignment = app.assignments.current(source)
    assert len(assignment['members']) == 3
    assert app.assignments.active_member_count(assignment) == 2
    assert assignment['members'][newer['id']]['status'] == 'bound'
    assert app.assignments.usage(assignment)['metrics']['grossTotalTokens']['value'] == 15


async def test_readmit_same_chat_fences_old_requests_and_keeps_spend(app):
    source, target, created = await setup(app)
    await limits(app, source, maxMembers=2, maxTotalTokens=100)
    row = call(target, 'charged')
    assert (await admission(app, target['id'], {'call': row}))['allowed']
    ingest(target, {**row, 'phase': 'completed', 'endedAt': 4, 'revision': 2,
                   'usage': {'totalTokens': 15}})
    old = copy.deepcopy(app.collaboration.receipt(created['initialInputId']))
    await retire(app, source, target)
    assert 'retired' in app.collaboration.guard(old)
    before = len(app.runtime.inputs)
    args = await adoption_args(app, source, target)
    await agent_action(app, source, 'assignment.adopt', args, 'readmit')
    value = app.assignments.current(target)
    member = value['members'][target['id']]
    assert member['role'] == 'adopted' and member['previousRetirementId'] == 'retire'
    assert member['joinedAt'] > old['acceptedAt']
    assert 'earlier assignment membership' in app.collaboration.guard(old)
    assert len(app.runtime.inputs) == before
    assert app.collaboration.receipt(created['initialInputId']) == old
    assert app.assignments.usage(value)['metrics']['grossTotalTokens']['value'] == 15
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'mode': 'queue', 'text': 'Fresh follow-up',
        'returnNotification': False}, 'new-followup')
    await settled(app, 'new-followup', 'accepted')
    assert len(app.runtime.inputs) == before + 1


async def test_retirement_does_not_notify_old_completion_after_readmission(app):
    source, target, created = await setup(app)
    old = app.collaboration.receipt(created['initialInputId'])
    await retire(app, source, target)
    await agent_action(app, source, 'assignment.adopt',
                       await adoption_args(app, source, target), 'readmit')
    old['terminalOutcome'] = {'status': 'observed', 'kind': 'admission',
        'outcome': 'failed', 'qualified': False, 'requestId': old['requestId'],
        'observedAt': member_time(app, target), 'text': 'Late retained evidence'}
    before = len(app.runtime.inputs)
    app.collaboration.enqueue_continuation(old)
    saved = app.collaboration.receipt(old['requestId'])
    assert saved['subscription']['status'] == 'suppressed'
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?',
                             (old['subscription']['continuationId'],)).fetchone()
    assert len(app.runtime.inputs) == before


def member_time(app, target):
    return app.assignments.current(target)['members'][target['id']]['joinedAt'] + 1


@pytest.mark.parametrize('busy', ['generation', 'worker', 'preparation', 'settings',
                                 'pending', 'unknown', 'call', 'task', 'claim', 'check'])
async def test_retirement_refuses_unfinished_or_uncertain_work(app, busy):
    source, target, _ = await setup(app)
    if busy == 'generation':
        target['collaborationGeneration']['terminal'] = False
    elif busy == 'worker':
        target['workers'].append({'id': 'child', 'status': 'idle'})
    elif busy == 'preparation':
        app.runtime._preparations = {target['id']: object()}
    elif busy == 'settings':
        target['configurationBusy'] = True
    elif busy in {'pending', 'unknown'}:
        app.collaboration.insert('pending', 'fingerprint', {
            'commandAction': 'coordination.send', 'target': {'sessionId': target['id']},
            'delivery': 'queued' if busy == 'pending' else 'unknown'})
    elif busy == 'call':
        ingest(target, {**call(target, 'uncertain'), 'phase': 'outcome_unknown'})
    elif busy == 'task':
        target['task'] = {'id': 'unfinished', 'status': 'blocked'}
    elif busy == 'claim':
        value = app.assignments.current(source)
        value['tasks'] = {'claimed': {'status': 'claimed', 'claim': {'sessionId': target['id']}}}
        app.assignments.save(value)
    else:
        app.collaboration.insert('check', 'fingerprint', {
            'commandAction': 'workspace.tests.submit', 'senderSessionId': target['id'],
            'job': {'status': 'checking'}})
    before = copy.deepcopy(app.assignments.current(source))
    with pytest.raises(AppError):
        await retire(app, source, target)
    assert app.assignments.current(source) == before
    assert app.assignments.current(target)['id'] == before['id']


async def test_only_owner_can_retire_current_helper(app):
    source, target, _ = await setup(app)
    with pytest.raises(AppError, match='owner'):
        await app.dispatch('assignment.retire', {'sessionId': target['id'], 'targetSessionId': source['id'],
            'expectedRevision': app.assignments.current(source)['revision']})
    with pytest.raises(AppError, match='owner'):
        await retire(app, source, source)


async def test_readmission_rechecks_capacity(app):
    source, target, _ = await setup(app)
    await limits(app, source, maxMembers=2)
    await retire(app, source, target)
    await child(app, source, 'replacement')
    args = await adoption_args(app, source, target)
    with pytest.raises(AppError, match='member limit'):
        await agent_action(app, source, 'assignment.adopt', args, 'full-readmission')
    assert app.assignments.current(target) is None
    assert app.assignments.current(source)['members'][target['id']]['status'] == 'retired'


async def test_failed_retirement_save_restores_membership(app, monkeypatch):
    source, target, _ = await setup(app)
    before = copy.deepcopy(app.assignments.current(source))
    def fail(**kwargs):
        raise OSError('Synthetic durable save failure')
    monkeypatch.setattr(app, '_publish_changes', fail)
    with pytest.raises(OSError):
        await retire(app, source, target)
    assert app.assignments.current(source) == before
    assert app.assignments.current(target)['id'] == before['id']
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='retire'").fetchone()


async def test_retirement_retries_same_receipt_without_repeating_changes(app):
    source, target, _ = await setup(app)
    args = {'sessionId': source['id'], 'targetSessionId': target['id'],
            'expectedRevision': app.assignments.current(source)['revision']}
    first = await agent_action(app, source, 'assignment.retire', args, 'same-retirement')
    duplicate = await agent_action(app, source, 'assignment.retire', args, 'same-retirement')
    assert duplicate == {**first, 'duplicate': True}
    assert app.assignments.current(source)['revision'] == first['result']['assignment']['revision']
    assert app.collaboration.creation_guard(app.collaboration.receipt('helper')) is not None


async def test_retirement_refuses_foreign_native_writer(app):
    from amplifier_foundation.session import SharedSessionStore
    source, target, _ = await setup(app)
    owner = SharedSessionStore(target['workspace'], target['id']).acquire(app='other-native-writer')
    try:
        with pytest.raises(AppError, match='stopped'):
            await retire(app, source, target)
        assert app.assignments.current(target) is not None
    finally:
        owner.release()


@pytest.mark.parametrize('status', ['detached', 'not_created'])
async def test_historical_members_do_not_consume_current_capacity(app, status):
    source, target, _ = await setup(app)
    await limits(app, source, maxMembers=2)
    value = app.assignments.current(source)
    value['members'][target['id']]['status'] = status
    app.assignments.save(value)
    newer, _ = await child(app, source, 'replacement')
    value = app.assignments.current(source)
    assert value['members'][newer['id']]['status'] == 'bound'
    assert len(value['members']) == 3 and app.assignments.active_member_count(value) == 2


@pytest.mark.parametrize('state,blocked', [('running', True), ('unknown', True), ('stopped', False)])
async def test_retirement_observes_environment_owner_after_terminal_queue(app, state, blocked):
    from contextlib import closing
    from amplifier_web import task_environments as environments
    source, target, _ = await setup(app)
    row = {'id': 'retained-environment', 'kind': environments.KIND,
           'sessionId': target['id'], 'workspaceId': 'workspace',
           'status': 'ready', 'environmentState': state}
    with closing(environments.storage.connect(app.data_dir)) as db, db:
        environments.storage.save(db, row)
    app.collaboration.insert('terminal-check', 'fingerprint', {
        'commandAction': 'workspace.tests.submit', 'senderSessionId': target['id'],
        'job': {'status': 'unknown', 'resourceId': row['id']}})
    if blocked:
        with pytest.raises(AppError, match='test environment'):
            await retire(app, source, target)
    else:
        await retire(app, source, target)
    with closing(environments.storage.connect(app.data_dir)) as db:
        assert environments.storage.get(db, target['id'], row['id']) == row
