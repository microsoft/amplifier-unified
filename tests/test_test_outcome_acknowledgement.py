"""Exact test completion reviews never replay checks or qualify unknown effects."""
import asyncio
import copy

import pytest

from amplifier_web import task_environments as environments
from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import Runtime, settled, generation
from test_coordination_generation import agent
from test_test_queue import app, backend, queue, drive, record, submit
from test_task_environments import scope


async def ready(app, queue, *, identity='check'):
    runtime = Runtime()
    runtime.app = app
    runtime.unavailable_boundaries.add(app.state["sessions"][0]["id"])
    app.runtime = runtime
    app.state['sessions'][0]['status'] = 'working'
    await submit(app, identity, notify=True)
    await drive(queue)
    receipt = record(queue, app, identity)
    assert receipt['job']['status'] not in {'queued', 'starting', 'checking', 'stopping', 'removing'}
    page = (await app.dispatch('workspace.tests.result', {**scope(app), 'requestId': identity}))['result']
    review = page['check']['notificationReview']
    return review['review']['args'], receipt['subscription']['continuationId']


async def acknowledge(app, args, identity='review', source=None):
    return await agent(app, source or app.state['sessions'][0],
        'coordination.acknowledge', args, identity)


async def test_queue_reads_offer_exact_review_without_consuming_it(app, queue, backend):
    args, notification_id = await ready(app, queue)
    receipt = copy.deepcopy(record(queue, app))
    operations = copy.deepcopy(app.operations.requests.list(args['sessionId']))
    row = (await app.dispatch('workspace.tests.read', scope(app)))['result']['items'][0]
    assert row['notification'] == 'claimed'
    assert row['notificationReview']['review']['args'] == args
    assert not row['notificationReview']['acknowledged']
    assert record(queue, app) == receipt
    # Peer results keep their existing scope; test reviews use the test routes.
    with pytest.raises(AppError, match='not a peer request'):
        await app.dispatch('coordination.result', {'requestId': args['requestId']})
    source = app.state['sessions'][0]
    task, messages = copy.deepcopy(source.get('task')), copy.deepcopy(source['messages'])
    result = await acknowledge(app, args)
    assert result['result']['notificationRetired'] is True
    assert not result['result']['workCancelled'] and not result['result']['resultQualificationChanged']
    after = record(queue, app)
    assert after['job'] == receipt['job'] and after['terminalOutcome'] == receipt['terminalOutcome']
    assert source.get('task') == task and source['messages'] == messages
    assert app.operations.requests.list(args['sessionId']) == operations
    assert app.collaboration.receipt(notification_id)['delivery'] == 'suppressed'
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    await queue.tick()
    assert not app.runtime.inputs
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']


async def test_review_unknown_test_keeps_unknown_check_and_original_effect(app, queue, backend, monkeypatch):
    invoke = environments.invoke
    def uncertain(config, home, row, action, args):
        if action == 'execute':
            backend.append((action, args))
            raise TimeoutError('Lost check acknowledgement')
        if action == 'job_read':
            backend.append((action, args))
            return {'ok': True, 'job': {'id': args['requestId'], 'state': 'unknown', 'terminal': False}}
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', uncertain)
    args, notification_id = await ready(app, queue)
    before = copy.deepcopy(record(queue, app))
    assert before['job']['status'] == 'unknown'
    operations = copy.deepcopy(app.operations.requests.list(args['sessionId']))
    await acknowledge(app, args)
    after = record(queue, app)
    assert after['job'] == before['job'] and after['terminalOutcome'] == before['terminalOutcome']
    assert after['terminalOutcome']['qualified'] is False
    assert app.operations.requests.list(args['sessionId']) == operations
    assert app.collaboration.receipt(notification_id)['delivery'] == 'suppressed'
    await queue.tick()
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'job_read']


@pytest.mark.parametrize('phase', ['accepted', 'submitting', 'unknown'])
async def test_admitted_or_unknown_notification_is_never_cancelled(app, queue, backend, phase):
    args, notification_id = await ready(app, queue)
    notification = app.collaboration.receipt(notification_id)
    notification['delivery'] = phase
    app.collaboration.save(notification_id, notification)
    app.db.commit()
    before = copy.deepcopy(notification)
    result = await acknowledge(app, args)
    assert not result['result']['notificationRetired']
    assert app.collaboration.receipt(notification_id) == before


@pytest.mark.parametrize('change', ['revision', 'key', 'stop', 'placement', 'directory', 'task', 'paused', 'foreign', 'generation', 'cancelled'])
async def test_changed_test_review_boundaries_do_not_write(app, queue, backend, change):
    args, notification_id = await ready(app, queue)
    source, other = app.state['sessions'][:2]
    caller = source
    if change == 'revision':
        args['outcomeRevision'] += 1
    elif change == 'key':
        args['reviewKey'] = 'other-result'
    elif change == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': source['id']})
    elif change == 'placement':
        source['executionRevision'] = source.get('executionRevision', 0) + 1
    elif change == 'directory':
        source['workingDirectory'] = source['workspace'] + '/other'
    elif change == 'task':
        source['task'] = {'id': 'new-objective', 'status': 'active', 'revision': 1}
    elif change == 'paused':
        source['task'] = {'status': 'paused'}
    elif change == 'foreign':
        await generation(app, other, 'peer-generation', ['peer-input'])
        caller = other
    elif change == 'generation':
        source['collaborationGeneration']['terminal'] = True
    else:
        receipt = record(queue, app)
        receipt['job']['cancelRequested'] = True
        queue.save(receipt)
    before = copy.deepcopy(record(queue, app))
    notification = copy.deepcopy(app.collaboration.receipt(notification_id))
    with pytest.raises(AppError):
        await acknowledge(app, args, source=caller)
    assert record(queue, app) == before
    assert app.collaboration.receipt(notification_id) == notification
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='review'").fetchone()


async def test_read_without_completion_or_requested_notification_has_no_review(app, queue, backend):
    await submit(app, notify=False)
    assert (await app.dispatch('workspace.tests.read', scope(app)))['result']['items'][0].get('notificationReview') is None
    await drive(queue)
    assert (await app.dispatch('workspace.tests.read', scope(app)))['result']['items'][0].get('notificationReview') is None


async def test_exact_test_review_retry_survives_reopen(app, queue, backend):
    args, notification_id = await ready(app, queue)
    result = await acknowledge(app, args)
    await app.close()
    runtime = Runtime()
    reopened = AppService(app.data_dir, runtime, workspace=app.default_workspace)
    runtime.app = reopened
    try:
        duplicate = await reopened.dispatch('coordination.acknowledge', args, origin='agent',
            caller_session_id=args['sessionId'], command_id='review')
        assert duplicate['duplicate'] and duplicate['result'] == result['result']
        reopened.collaboration.enqueue_continuation(reopened.collaboration.receipt(args['requestId']))
        assert reopened.collaboration.receipt(notification_id)['delivery'] == 'suppressed'
        assert not runtime.inputs
    finally:
        await reopened.close()


async def test_test_review_losing_admission_race_keeps_one_accepted_input(app, queue, backend):
    args, notification_id = await ready(app, queue)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.runtime.collaboration_input
    async def held(*values):
        entered.set()
        await release.wait()
        return await original(*values)
    app.runtime.collaboration_input = held
    source = app.state['sessions'][0]
    source['status'] = 'idle'
    task = asyncio.create_task(app.collaboration.drain(source['id']))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        result = await acknowledge(app, args)
        assert not result['result']['notificationRetired']
        assert app.collaboration.receipt(notification_id)['delivery'] == 'submitting'
    finally:
        release.set()
        await task
    await settled(app, notification_id, 'accepted')
    assert sum(row[1] == notification_id for row in app.runtime.inputs) == 1


async def test_changed_test_evidence_invalidates_earlier_review_key(app, queue, backend):
    args, notification_id = await ready(app, queue)
    receipt = record(queue, app)
    receipt['terminalOutcome']['text'] = 'The retained test evidence changed.'
    queue.save(receipt)
    before = copy.deepcopy(record(queue, app))
    with pytest.raises(AppError, match='exact result changed'):
        await acknowledge(app, args)
    assert record(queue, app) == before
    assert app.collaboration.receipt(notification_id)['delivery'] == 'queued'


async def test_reviewed_backpressured_test_never_wakes_on_queue_tick(app, queue, backend, monkeypatch):
    runtime = Runtime()
    runtime.app = app
    app.runtime = runtime
    source = app.state['sessions'][0]
    source['status'] = 'working'
    await submit(app, notify=True)
    original = app.collaboration.require_inbox_slot
    def full(*args, **kwargs):
        raise AppError('Inbox full', 429)
    monkeypatch.setattr(app.collaboration, 'require_inbox_slot', full)
    await drive(queue)
    receipt = record(queue, app)
    assert receipt['subscription']['status'] == 'backpressured'
    page = (await app.dispatch('workspace.tests.read', scope(app)))['result']['items'][0]
    result = await acknowledge(app, page['notificationReview']['review']['args'])
    assert result['result']['notificationDelivery'] == 'not_queued'
    monkeypatch.setattr(app.collaboration, 'require_inbox_slot', original)
    source['status'] = 'idle'
    await queue.tick()
    app.collaboration.enqueue_continuation(record(queue, app))
    assert not runtime.inputs
    wait = record(queue, app)['subscription']
    assert wait['status'] == 'claimed' and wait['open'] is False
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (wait['continuationId'],)).fetchone()
    assert [action for action, _ in backend].count('execute') == 1


async def test_later_test_outcome_revision_is_not_hidden_by_review(app, queue, backend):
    args, notification_id = await ready(app, queue)
    await acknowledge(app, args)
    receipt = record(queue, app)
    # Exercise the shared host outcome publisher with a newer exact observation;
    # this is not a second test execution or an inference from cleanup success.
    revision = args['outcomeRevision'] + 1
    receipt['terminalOutcome'] = {**receipt['terminalOutcome'], 'outcomeRevision': revision,
        'outcome': 'unknown', 'qualified': False, 'text': 'A later host observation requires review.'}
    receipt['outcomeRevision'] = revision
    app.collaboration.enqueue_continuation(receipt)
    next_id = app.collaboration.continuation_identity(receipt['subscription'], revision)
    assert app.collaboration.receipt(notification_id)['delivery'] == 'suppressed'
    assert app.collaboration.receipt(next_id)['delivery'] == 'queued'
    page = (await app.dispatch('workspace.tests.result', {**scope(app), 'requestId': 'check'}))['result']
    latest = page['check']['notificationReview']
    assert not latest['acknowledged'] and latest['review']['args']['outcomeRevision'] == revision
    with pytest.raises(AppError, match='exact result changed'):
        await acknowledge(app, args, 'old-review')
    assert app.collaboration.receipt(next_id)['delivery'] == 'queued'
    assert [action for action, _ in backend].count('execute') == 1
