"""Exact reviewed outcomes retire notifications, not work or uncertainty."""
from amplifier_web import test_execution_settings as execution_settings

import asyncio
import copy

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import (
    app, agent_action, declared_result, finish, generation, Runtime, settled,
)


async def ready(app, request='result-request'):
    source, target = app.state['sessions'][:2]
    app.runtime.unavailable_boundaries.add(source['id'])
    source['status'] = 'working'
    target['status'] = 'idle'
    await declared_result(app, request=request)
    wait = await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': request}, 'wait-' + request)
    await finish(app, target, request)
    result = (await app.dispatch('coordination.result', {'requestId': request}))['result']
    return result['notification']['review']['args'], wait['result']['continuationId']


async def acknowledge(app, args, identity='review-1', source=None):
    return await agent_action(app, source or app.state['sessions'][0],
        'coordination.acknowledge', args, identity)


async def test_review_retires_only_exact_notification_and_preserves_result(app):
    args, identity = await ready(app)
    other_args, other_identity = await ready(app, 'other-request')
    before = copy.deepcopy(app.collaboration.receipt(args['requestId']))
    source, target = app.state['sessions'][:2]
    task, messages, inputs = copy.deepcopy(source.get('task')), copy.deepcopy(source['messages']), list(app.runtime.inputs)
    result = await acknowledge(app, args)
    assert result['result']['notificationRetired'] is True
    assert result['result']['workCancelled'] is False
    assert result['result']['resultQualificationChanged'] is False
    after = app.collaboration.receipt(args['requestId'])
    assert after['response'] == before['response']
    assert after['delivery'] == before['delivery']
    assert after['subscription']['acknowledgedOutcome']['reviewKey'] == args['reviewKey']
    assert app.collaboration.receipt(identity)['delivery'] == 'suppressed'
    assert app.collaboration.receipt(other_identity)['delivery'] == 'queued'
    assert source.get('task') == task and source['messages'] == messages
    assert app.runtime.inputs == inputs
    current = (await app.dispatch('coordination.result', {'requestId': args['requestId']}))['result']
    assert current['notification']['acknowledged'] and current['qualified']
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    assert app.runtime.inputs[-1][1] == other_identity
    assert all(row[1] != identity for row in app.runtime.inputs)


async def test_reads_never_acknowledge_and_review_survives_restart(app):
    args, identity = await ready(app)
    before = app.collaboration.receipt(args['requestId'])
    for _ in range(2):
        await app.dispatch('coordination.result', {'requestId': args['requestId']})
    assert app.collaboration.receipt(args['requestId']) == before
    result = await acknowledge(app, args)
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        duplicate = await reopened.dispatch('coordination.acknowledge', args, origin='agent',
            caller_session_id=args['sessionId'], command_id='review-1')
        assert duplicate['duplicate'] and duplicate['result'] == result['result']
        reopened._session(args['sessionId'])['status'] = 'idle'
        await reopened.collaboration.drain(args['sessionId'])
        reopened.collaboration.enqueue_continuation(reopened.collaboration.receipt(args['requestId']))
        assert not reopened.runtime.inputs
        assert reopened.collaboration.receipt(identity)['delivery'] == 'suppressed'
    finally:
        await reopened.close()


@pytest.mark.parametrize('phase', ['submitting', 'accepted', 'applied', 'unknown', 'suppressed'])
async def test_already_submitted_or_settled_notification_remains_unchanged(app, phase):
    args, identity = await ready(app)
    notification = app.collaboration.receipt(identity)
    notification['delivery'] = phase
    app.collaboration.save(identity, notification)
    app.db.commit()
    result = await acknowledge(app, args)
    assert result['result']['notificationRetired'] is False
    assert app.collaboration.receipt(identity) == notification


@pytest.mark.parametrize('change', ['revision', 'key', 'stop', 'task', 'pause', 'workspace', 'foreign', 'generation'])
async def test_changed_identity_authority_or_boundary_refuses_without_writes(app, change):
    args, identity = await ready(app)
    source, target = app.state['sessions'][:2]
    caller = source
    if change == 'revision':
        args['outcomeRevision'] += 1
    elif change == 'key':
        args['reviewKey'] = 'not-the-saved-result'
    elif change == 'stop':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    elif change == 'task':
        source['task'] = {'id': 'different-objective', 'revision': 1, 'status': 'active'}
    elif change == 'pause':
        source['task'] = {**(source.get('task') or {}), 'status': 'paused'}
    elif change == 'workspace':
        target['workspace'] = '/unrelated-workspace'
    elif change == 'foreign':
        caller = target
    else:
        source['collaborationGeneration']['terminal'] = True
    before = app.collaboration.receipt(args['requestId'])
    notification = app.collaboration.receipt(identity)
    with pytest.raises(AppError):
        await acknowledge(app, args, source=caller)
    assert app.collaboration.receipt(args['requestId']) == before
    assert app.collaboration.receipt(identity) == notification
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='review-1'").fetchone()


async def test_later_outcome_still_notifies_and_old_review_key_cannot_hide_it(app):
    args, identity = await ready(app)
    await acknowledge(app, args)
    source, target = app.state['sessions'][:2]
    await generation(app, target, 'later-report', [args['requestId']])
    await agent_action(app, target, 'coordination.reply', {
        'requestId': args['requestId'], 'expectedResponseRevision': 1,
        'kind': 'question', 'outcome': 'question', 'text': 'New evidence needs a decision.'}, 'later-report')
    request = app.collaboration.receipt(args['requestId'])
    assert request['outcomeRevision'] > args['outcomeRevision']
    next_identity = app.collaboration.continuation_identity(request['subscription'], request['outcomeRevision'])
    assert app.collaboration.receipt(next_identity)['delivery'] == 'queued'
    assert app.collaboration.receipt(identity)['delivery'] == 'suppressed'
    with pytest.raises(AppError, match='exact result changed'):
        await acknowledge(app, args, 'stale-review')


async def test_review_and_admission_race_preserves_in_flight_notification(app):
    args, identity = await ready(app)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.runtime.collaboration_input
    async def held(*values):
        entered.set()
        await release.wait()
        return await original(*values)
    app.runtime.collaboration_input = held
    app.state['sessions'][0]['status'] = 'idle'
    task = asyncio.create_task(app.collaboration.drain(args['sessionId']))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        result = await acknowledge(app, args)
        assert not result['result']['notificationRetired']
        assert app.collaboration.receipt(identity)['delivery'] == 'submitting'
    finally:
        release.set()
        await task
    await settled(app, identity, 'accepted')
    assert sum(row[1] == identity for row in app.runtime.inputs) == 1


async def test_failed_commit_does_not_retire_and_exact_retry_is_idempotent(app, monkeypatch):
    args, identity = await ready(app)
    original = app.collaboration.publish_receipt
    def fail(receipt):
        raise OSError('Synthetic failed durable write')
    monkeypatch.setattr(app.collaboration, 'publish_receipt', fail)
    before = app.collaboration.receipt(args['requestId'])
    with pytest.raises(OSError, match='durable write'):
        await acknowledge(app, args)
    assert app.collaboration.receipt(args['requestId']) == before
    assert app.collaboration.receipt(identity)['delivery'] == 'queued'
    monkeypatch.setattr(app.collaboration, 'publish_receipt', original)
    result = await acknowledge(app, args)
    duplicate = await acknowledge(app, args)
    assert duplicate['duplicate'] and duplicate['result'] == result['result']
    with pytest.raises(AppError, match='different contents'):
        await acknowledge(app, {**args, 'reviewKey': 'different'})


def test_review_is_explicit_recovery_report_not_work_permission():
    from amplifier_web.workspace_work_effects import recovery_read
    assert recovery_read('app_control', {'operation': 'dispatch',
        'args': {'action': 'coordination.acknowledge', 'args': {'requestId': 'exact'}}})


@pytest.mark.parametrize('restart', [False, True])
async def test_reviewed_backpressured_outcome_never_enqueues_later(app, monkeypatch, restart):
    source, target = app.state['sessions'][:2]
    await declared_result(app)
    wait = await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'result-request'}, 'wait')
    original = app.collaboration.require_inbox_slot
    def full(*args, **kwargs):
        raise AppError('Inbox full', 429)
    monkeypatch.setattr(app.collaboration, 'require_inbox_slot', full)
    await finish(app, target, 'result-request')
    request = app.collaboration.receipt('result-request')
    assert request['subscription']['status'] == 'backpressured'
    result = (await app.dispatch('coordination.result', {'requestId': 'result-request'}))['result']
    answer = await acknowledge(app, result['notification']['review']['args'])
    assert answer['result']['notificationDelivery'] == 'not_queued'
    monkeypatch.setattr(app.collaboration, 'require_inbox_slot', original)
    app.collaboration.resume_outcomes(source['id'])
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?',
        (wait['result']['continuationId'],)).fetchone()
    assert app.collaboration.receipt('result-request')['subscription']['open'] is False
    assert app.collaboration.receipt('result-request')['subscription']['status'] == 'claimed'
    # Exercise actual admission: a final reviewed backpressured result must
    # release its reservation rather than permanently occupying this inbox.
    with monkeypatch.context() as bounded:
        bounded.setitem(execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputs', 1)
        original(source['id'])
    if restart:
        await app.close()
        reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
        reopened.runtime.app = reopened
        try:
            # Durable acknowledgment frees the slot and prevents automatic
            # replay after a restart, not just in the original process.
            with monkeypatch.context() as bounded:
                bounded.setitem(execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputs', 1)
                reopened.collaboration.require_inbox_slot(source['id'])
            reopened.collaboration.resume_outcomes(source['id'])
            assert not reopened.runtime.inputs
            assert not reopened.db.execute('SELECT 1 FROM commands WHERE id=?',
                (wait['result']['continuationId'],)).fetchone()
        finally:
            await reopened.close()
        return
    # Retiring this notification must not close later changed reports.
    source['status'] = 'working'
    await generation(app, target, 'later-backpressured-report', ['result-request'])
    await agent_action(app, target, 'coordination.reply', {
        'requestId': 'result-request', 'expectedResponseRevision': 1,
        'kind': 'question', 'outcome': 'question',
        'text': 'New evidence needs a decision.'}, 'later-backpressured-report')
    updated = app.collaboration.receipt('result-request')
    next_identity = app.collaboration.continuation_identity(updated['subscription'], updated['outcomeRevision'])
    assert app.collaboration.receipt(next_identity)['delivery'] == 'queued'
    assert updated['subscription']['open'] is True
