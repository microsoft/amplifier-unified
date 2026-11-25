"""Current wait withdrawal is not cancellation or execution authority.

Scripted transport with the actual AppService and RuntimeControls owners; no live
provider, user history or legacy operation is exercised.
"""
import copy
import asyncio

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.automatic_history import display_identity
from amplifier_operations.coordination import fingerprint
from test_collaborative_workspaces import (
    app, agent_action, declared_result, finish, generation, send, settled, Runtime,
)
from test_coordination_revisions import revised
from test_outcome_acknowledgement import ready, acknowledge
from test_blocked_peer_input import task_app, waiting_dependency, mutate, send as send_to_owner
from test_cross_workspace_coordination import cross_app


async def unsubscribe(app, identity='request-1', command='unsubscribe-1', source=None, **patch):
    source = source or app.state['sessions'][0]
    return await agent_action(app, source, 'coordination.unsubscribe', {
        'sessionId': source['id'], 'requestId': identity, **patch,
    }, command)


async def test_unknown_wait_withdrawal_preserves_effects_and_never_replays(app):
    source, target = app.state['sessions']
    app.runtime.fail = True
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'unknown')
    before = copy.deepcopy(app.collaboration.receipt('request-1'))
    inputs = list(app.runtime.inputs)
    answer = await unsubscribe(app, reason='The new plan does not await this result')
    after = app.collaboration.receipt('request-1')
    assert after['subscription']['withdrawal']['operationId'] == 'unsubscribe-1'
    assert not after['subscription']['open']
    for field in ('delivery', 'cancellation', 'response', 'terminalOutcome'):
        assert after.get(field) == before.get(field)
    assert answer['result']['originalDelivery'] == 'unknown'
    assert answer['result']['executionStopped'] is False
    await app.collaboration.drain(target['id'])
    assert app.runtime.inputs == inputs


@pytest.mark.parametrize('change', ['completed', 'blocked', 'new-task', 'fresh-after-stop'])
@pytest.mark.parametrize('terminal_matches', [True, False])
async def test_confirmed_past_work_can_be_reported_from_current_fresh_generation(app, change, terminal_matches):
    source, target = app.state['sessions']
    await declared_result(app, kind='defer', outcome='deferred')
    await finish(app, target, 'result-request')
    if change == 'fresh-after-stop':
        target['interruptionRevision'] = 1
    await generation(app, target, 'later-report', ['fresh-authorized-input'],
                     interruption_revision=target.get('interruptionRevision', 0))
    target['task'] = {'id': 'new' if change == 'new-task' else 'old', 'revision': 3,
                      'status': change if change in {'completed', 'blocked'} else 'active'}
    result = await revised(app, 'result-request', 1)
    assert result['result']['responseRevision'] == 2
    await finish_later(app, target, terminal_matches=terminal_matches)
    response = app.collaboration.receipt('result-request')['response']
    assert response['qualified'] is terminal_matches
    assert response['status'] == ('sealed' if terminal_matches else 'rejected')
    assert response['interruptionRevision'] == target.get('interruptionRevision', 0)


async def finish_later(app, target, terminal_matches=True):
    anchor = {'messageId': display_identity(target, 9, 'assistant', 'Later evidence'),
        'nativeIndex': 9, 'nativeText': 'Later evidence', 'textDigest': fingerprint('Later evidence'),
        'rootSessionId': target['id'], 'generationId': 'later-report'}
    if not terminal_matches:
        anchor['textDigest'] = 'wrong'
    await finish(app, target, 'result-request', generation_id='later-report',
                 input_ids=['fresh-authorized-input'], text='Later evidence', nativeTerminal=anchor)


async def test_review_of_exact_outcome_is_not_old_task_execution(app):
    args, notification = await ready(app)
    source = app.state['sessions'][0]
    source['task'] = {'id': 'new-plan', 'revision': 3, 'status': 'blocked'}
    result = await acknowledge(app, args)
    assert result['result']['notificationRetired']
    assert app.collaboration.receipt(notification)['delivery'] == 'suppressed'
    assert source['task']['status'] == 'blocked'


@pytest.mark.parametrize('phase', ['waiting', 'claimed', 'backpressured', 'cancelled', 'suppressed'])
async def test_withdrawal_is_sticky_across_cancel_review_subscribe_and_exact_send(app, phase):
    source, target = app.state['sessions']
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'accepted')
    request = app.collaboration.receipt('request-1')
    request['subscription'].update(status=phase, open=False)
    app.collaboration.save('request-1', request)
    app.db.commit()
    first = await unsubscribe(app)
    after = app.collaboration.receipt('request-1')
    assert after['subscription']['status'] == phase
    assert (await unsubscribe(app))['duplicate']
    with pytest.raises(AppError, match='different contents'):
        await unsubscribe(app, reason='Different command body')
    again = await unsubscribe(app, command='another-withdrawal')
    assert again['result']['withdrawal'] == first['result']['withdrawal']
    await agent_action(app, source, 'coordination.cancel',
        {'sessionId': source['id'], 'requestId': 'request-1'}, 'cancel-after-withdrawal')
    subscribed = await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'request-1'}, 'subscribe-after-withdrawal')
    assert subscribed['result']['withdrawal'] == first['result']['withdrawal']
    retry = await send(app, returnNotification=True)
    assert retry['duplicate'] and retry['returnState']['subscription']['withdrawal']
    assert not retry['returnState']['subscription']['open']


@pytest.mark.parametrize('phase', ['submitting', 'accepted', 'applied', 'unknown'])
async def test_unsubscribe_truthfully_lists_inflight_without_reclassifying(app, phase):
    args, queued = await ready(app)
    request = app.collaboration.receipt('result-request')
    # Additional exact returns model older revisions; only the queued one retires.
    base = app.collaboration.receipt(queued)
    pending = {**base, 'requestId': 'inflight', 'inputId': 'inflight', 'delivery': phase}
    app.collaboration.insert('inflight', 'fixture', pending)
    app.db.commit()
    answer = await unsubscribe(app, 'result-request')
    assert answer['result']['suppressedReturns'] == {'requestIds': [queued], 'count': 1, 'truncated': False}
    assert answer['result']['inFlightReturns']['items'] == [{'requestId': 'inflight', 'delivery': phase}]
    assert app.collaboration.receipt('inflight') == pending
    assert app.collaboration.receipt('result-request')['response'] == request['response']
    assert app.collaboration.receipt(queued)['delivery'] == 'suppressed'
    assert 'may still arrive' in answer['result']['detail']


async def test_late_qualified_result_and_review_do_not_revive_withdrawal(app):
    args, old_return = await ready(app)
    source, target = app.state['sessions']
    await unsubscribe(app, 'result-request')
    await acknowledge(app, args)
    await generation(app, target, 'later-report', ['fresh-authorized-input'])
    await revised(app, 'result-request', 1)
    await finish_later(app, target)
    request = app.collaboration.receipt('result-request')
    assert request['response']['qualified']
    assert request['subscription']['withdrawal']
    assert not request['subscription']['open']
    identity = app.collaboration.continuation_identity(request['subscription'], request['outcomeRevision'])
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
    source['status'] = 'idle'
    inputs = list(app.runtime.inputs)
    await app.collaboration.drain(source['id'])
    assert app.runtime.inputs == inputs
    assert app.collaboration.receipt(old_return)['delivery'] == 'suppressed'


async def test_inflight_admission_race_may_finish_but_never_restarts_request(app):
    args, notification = await ready(app)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.runtime.collaboration_input
    async def held(*values):
        entered.set()
        await release.wait()
        return await original(*values)
    app.runtime.collaboration_input = held
    source = app.state['sessions'][0]
    source['status'] = 'idle'
    drain = asyncio.create_task(app.collaboration.drain(source['id']))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        answer = await unsubscribe(app, 'result-request')
        assert answer['result']['inFlightReturns']['items'] == [
            {'requestId': notification, 'delivery': 'submitting'}]
        assert answer['result']['suppressedReturns']['count'] == 0
    finally:
        release.set()
        await drain
    await settled(app, notification, 'accepted')
    assert sum(row[1] == notification for row in app.runtime.inputs) == 1
    app.collaboration.enqueue_continuation(app.collaboration.receipt('result-request'))
    assert sum(row[1] == notification for row in app.runtime.inputs) == 1


async def test_withdrawal_survives_lost_ack_restart_and_failed_commit(app, monkeypatch):
    args, queued = await ready(app)
    before = copy.deepcopy(app.collaboration.receipt('result-request'))
    original_publish = app.collaboration.publish_receipt
    def failed_publish(*args):
        raise OSError('Synthetic precommit failure')
    monkeypatch.setattr(app.collaboration, 'publish_receipt', failed_publish)
    with pytest.raises(OSError, match='precommit'):
        await unsubscribe(app, 'result-request')
    assert app.collaboration.receipt('result-request') == before
    assert app.collaboration.receipt(queued)['delivery'] == 'queued'
    monkeypatch.setattr(app.collaboration, 'publish_receipt', original_publish)
    answer = await unsubscribe(app, 'result-request')
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        # Exact retry is receipt inspection; no new current generation is minted.
        duplicate = await reopened.dispatch('coordination.unsubscribe',
            {'sessionId': args['sessionId'], 'requestId': 'result-request'},
            origin='agent', caller_session_id=args['sessionId'], command_id='unsubscribe-1')
        assert duplicate['duplicate'] and duplicate['result'] == answer['result']
        request = reopened.collaboration.receipt('result-request')
        reopened.collaboration.enqueue_continuation(request)
        await reopened.collaboration.drain(args['sessionId'])
        assert not reopened.runtime.inputs
        assert request['subscription']['withdrawal'] == answer['result']['withdrawal']
    finally:
        await reopened.close()


@pytest.mark.parametrize('change', ['foreign', 'workspace', 'stale', 'stop', 'protocol', 'child'])
async def test_unsubscribe_never_borrows_other_authority(app, change):
    source, target = app.state['sessions']
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'accepted')
    caller = source
    if change == 'foreign':
        await generation(app, target, 'foreign', ['input'])
        caller = target
    elif change == 'workspace':
        target['workspace'] += '/changed'
    elif change == 'stale':
        source['collaborationGeneration']['terminal'] = True
    elif change == 'stop':
        source['interruptionRevision'] = 1
    elif change == 'child':
        source['sessionKind'] = 'worker'
    else:
        row = app.collaboration.receipt('request-1')
        row['protocol'] = 1
        app.collaboration.save('request-1', row)
    before = copy.deepcopy(app.collaboration.receipt('request-1'))
    with pytest.raises(AppError):
        await unsubscribe(app, source=caller)
    assert app.collaboration.receipt('request-1') == before


async def test_unknown_original_admission_cannot_be_fabricated_by_later_report(app):
    _, target = app.state['sessions']
    app.runtime.fail = True
    await send(app)
    await settled(app, 'request-1', 'unknown')
    await generation(app, target, 'fresh', ['new-authorized-input'])
    with pytest.raises(AppError, match='exact delivered'):
        await agent_action(app, target, 'coordination.reply', {
            'requestId': 'request-1', 'kind': 'result', 'outcome': 'success',
            'text': 'Not proof of historical admission', 'references': ['urn:fixture:evidence']}, 'unknown-reply')
    assert app.collaboration.receipt('request-1')['delivery'] == 'unknown'
    assert not app.collaboration.receipt('request-1').get('response')


async def test_current_stopped_recipient_cannot_report_even_with_stale_live_marker(app):
    _, target = app.state['sessions']
    await declared_result(app, kind='defer', outcome='deferred')
    await finish(app, target, 'result-request')
    await generation(app, target, 'fresh-before-stop', ['input'])
    target['status'] = 'stopped'
    with pytest.raises(AppError, match='stopped'):
        await revised(app, 'result-request', 1)
    assert app.collaboration.receipt('result-request')['responseRevision'] == 1


async def test_bounded_return_overflow_is_counted_without_limiting_work(app):
    args, queued = await ready(app)
    source = app.state['sessions'][0]
    base = app.collaboration.receipt(queued)
    for i in range(20):
        app.collaboration.insert(f'queued-{i}', f'q-{i}',
            {**base, 'requestId': f'queued-{i}', 'inputId': f'queued-{i}'})
        app.collaboration.insert(f'unknown-{i}', f'u-{i}',
            {**base, 'requestId': f'unknown-{i}', 'inputId': f'unknown-{i}', 'delivery': 'unknown'})
    app.db.commit()
    answer = (await unsubscribe(app, 'result-request'))['result']
    assert answer['suppressedReturns']['count'] == 21
    assert len(answer['suppressedReturns']['requestIds']) == 16
    assert answer['suppressedReturns']['truncated']
    assert answer['inFlightReturns']['count'] == 20
    assert len(answer['inFlightReturns']['items']) == 16 and answer['inFlightReturns']['truncated']
    assert all(app.collaboration.receipt(f'queued-{i}')['delivery'] == 'suppressed' for i in range(20))
    assert all(app.collaboration.receipt(f'unknown-{i}')['delivery'] == 'unknown' for i in range(20))


async def test_direct_user_can_withdraw_wait_without_a_running_model(app):
    source, _ = app.state['sessions']
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'accepted')
    source['collaborationGeneration']['terminal'] = True
    result = await app.dispatch('coordination.unsubscribe',
        {'sessionId': source['id'], 'requestId': 'request-1'}, origin='ui', command_id='human-withdraw')
    assert result['result']['withdrawal']['operationId'] == 'human-withdraw'
    assert not result['result']['executionStopped']


async def test_review_after_separately_authorized_fresh_stop_boundary_never_wakes_old_wait(app):
    args, _ = await ready(app)
    source = app.state['sessions'][0]
    source['interruptionRevision'] = 1
    await generation(app, source, 'authorized-after-stop', ['fresh-human-input'], interruption_revision=1)
    before = list(app.runtime.inputs)
    answer = await acknowledge(app, args)
    assert answer['result']['workCancelled'] is False
    assert source['interruptionRevision'] == 1
    assert app.runtime.inputs == before


async def test_withdrawn_wait_frees_explicit_inbox_reservation_without_new_count_limit(app, monkeypatch):
    from amplifier_web import test_execution_settings
    source, _ = app.state['sessions']
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'accepted')
    await unsubscribe(app)
    monkeypatch.setitem(test_execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputs', 1)
    app.collaboration.require_inbox_slot(source['id'])


async def test_withdrawal_return_lookup_cost_tracks_exact_request_not_all_receipts(app):
    source, target = app.state['sessions']
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'accepted')
    app.db.executemany('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)', [
        (f'old-{i}', str(i), '{"commandAction":"coordination.send","dependencyRequestId":"elsewhere","delivery":"unknown","text":"' + 'large retained body' * 100 + '"}')
        for i in range(2000)])
    app.db.commit()
    from amplifier_web.request_cancellation import return_summary
    steps = 0
    def count():
        nonlocal steps
        steps += 1
        return 0
    app.db.set_progress_handler(count, 1)
    try:
        value = return_summary(app.collaboration, app.collaboration.receipt('request-1'))
    finally:
        app.db.set_progress_handler(None, 0)
    assert value['count'] == 0 and steps < 200, steps


@pytest.mark.parametrize('before', [False, True])
async def test_withdrawal_and_late_exact_admission_ack_preserve_separate_facts(app, before):
    from test_peer_admission_reconciliation import unknown_request
    target = await unknown_request(app, mode='queue')
    source = app.state['sessions'][0]
    await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'late'}, 'wait-late')
    async def late():
        await app.on_runtime_event('runtime.delivery', {'sessionId': target['id'],
            'inputId': 'late', 'interruptionRevision': 0,
            'peerAdmission': {'accepted': True, 'inputId': 'late', 'mode': 'queue'}})
    if before:
        await late()
    await unsubscribe(app, 'late')
    if not before:
        await late()
    row = app.collaboration.receipt('late')
    assert row['delivery'] == 'accepted' and row['subscription']['withdrawal']
    assert not row.get('response') and not row['subscription']['open']


async def test_full_owner_explicitly_resumes_after_last_wait_withdrawal_without_human(task_app):
    ctx = task_app
    from test_access_mode import save
    ctx.app.runtime.workers = {}
    await save(ctx.app, ctx.target)
    assert ctx.app.access_mode.full(ctx.target)
    identity = await waiting_dependency(ctx)
    from amplifier_web.navigation_summary import activity
    assert activity(ctx.target, task=ctx.target['task'], db=ctx.app.db).get('waiting')
    prior = copy.deepcopy(ctx.target['task'])
    result = await unsubscribe(ctx.app, identity, source=ctx.target)
    assert not result['result']['executionStopped']
    assert ctx.target['task'] == prior and not await ctx.owner.tasks.continuation_allowed()
    assert not activity(ctx.target, task=ctx.target['task'], db=ctx.app.db).get('waiting')
    await mutate(ctx, 'task.resume')
    assert await ctx.owner.tasks.continuation_allowed()
    assert ctx.target['task']['id'] == prior['id']
    assert ctx.target['task']['continuationEpoch'] == prior['continuationEpoch']
    await send_to_owner(ctx, 'fresh-after-deliberate-resume')
    await settled(ctx.app, 'fresh-after-deliberate-resume', 'accepted')
    assert sum(row[1] == 'fresh-after-deliberate-resume' for row in ctx.app.runtime.inputs) == 1


@pytest.mark.parametrize('boundary', ['pause', 'human-block', 'stop'])
async def test_withdrawal_cannot_clear_human_or_stop_boundary(task_app, boundary):
    ctx = task_app
    identity = await waiting_dependency(ctx)
    if boundary == 'pause':
        await mutate(ctx, 'task.pause', origin='ui')
    elif boundary == 'human-block':
        await mutate(ctx, 'task.block', origin='ui', reason='Human decision required')
    else:
        ctx.target['interruptionRevision'] = 1
    prior = copy.deepcopy(ctx.target['task'])
    if boundary == 'stop':
        with pytest.raises(AppError):
            await unsubscribe(ctx.app, identity, source=ctx.target)
    else:
        await unsubscribe(ctx.app, identity, source=ctx.target)
    assert ctx.target['task'] == prior
    assert not await ctx.owner.tasks.continuation_allowed()
    assert ctx.target.get('interruptionRevision', 0) == (1 if boundary == 'stop' else 0)


async def test_selected_browser_payload_updates_from_actual_withdrawal_without_client_change(task_app):
    ctx = task_app
    identity = await waiting_dependency(ctx)
    ctx.app.state['selectedSessionId'] = ctx.target['id']
    ctx.app._publish_changes(sessions={ctx.target['id']}, globals={'selectedSessionId'})
    before = ctx.app.browser_state()['selectedActivity']
    assert before['sessionId'] == ctx.target['id'] and before['waiting']['targets']
    await unsubscribe(ctx.app, identity, source=ctx.target)
    after = ctx.app.browser_state()['selectedActivity']
    assert after['sessionId'] == ctx.target['id'] and 'waiting' not in after
    assert ctx.target['task']['status'] == 'blocked'
