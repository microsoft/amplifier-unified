"""Exact peer request withdrawal; no whole-chat stop or model efficacy claim."""
import asyncio
import copy

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, agent_action, generation, Runtime, send, settled


async def cancel(app, identity='request-1', command='cancel-1', **patch):
    source = app.state['sessions'][0]
    return await agent_action(app, source, 'coordination.cancel',
        {'sessionId': source['id'], 'requestId': identity, **patch}, command)


async def test_withdraw_only_one_queued_request_preserves_peer_and_survives_restart(app):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await send(app)
    await send(app, 'keep-request')
    before = copy.deepcopy(target['messages'])
    result = await cancel(app, reason='No longer needed')
    assert result['result']['status'] == 'withdrawn'
    assert app.collaboration.receipt('request-1')['terminalOutcome']['outcome'] == 'cancelled'
    assert app.collaboration.receipt('request-1')['terminalOutcome']['qualified'] is False
    assert app.collaboration.receipt('keep-request')['delivery'] == 'queued'
    assert target['status'] == 'working' and target['messages'] == before
    assert not app.runtime.inputs
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        restored = reopened._session(target['id'])
        restored['status'] = 'idle'
        await reopened.collaboration.drain(target['id'])
        assert [row[1] for row in reopened.runtime.inputs] == ['keep-request']
        assert reopened.collaboration.receipt('request-1')['delivery'] == 'cancelled'
    finally:
        await reopened.close()


async def test_active_request_is_cooperative_preserves_mixed_work_and_pending_return(app):
    source, target = app.state['sessions']
    await send(app)
    await settled(app, 'request-1', 'accepted')
    await generation(app, target, 'mixed-generation', ['request-1', 'human-input', 'other-request'])
    await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'request-1'}, 'subscribe')
    before = copy.deepcopy(target)
    inputs = copy.deepcopy(app.runtime.inputs)
    result = await cancel(app, reason='Keep the evidence; stop new checks')
    assert result['result']['status'] == 'requested'
    assert result['result']['interruptsOtherWork'] is False
    assert target['status'] == before['status']
    assert target['collaborationGeneration'] == before['collaborationGeneration']
    assert app.runtime.inputs == inputs
    request = app.collaboration.receipt('request-1')
    assert request['delivery'] == 'accepted' and not request.get('terminalOutcome')
    assert request['subscription']['open'] is False
    refs = target['coordinationReference']['cancellations']
    assert refs['items'][0]['requestId'] == 'request-1'
    assert refs['items'][0]['reason'] == 'Keep the evidence; stop new checks'
    reply = await agent_action(app, target, 'coordination.reply', {'requestId': 'request-1',
        'kind': 'result', 'outcome': 'cancelled', 'text': 'Stopped new checks; completed evidence retained.'}, 'reply-cancel')
    assert reply['result']['status'] == 'staged' and not reply['result']['qualified']
    assert target['coordinationReference']['cancellations']['items'][0]['status'] == 'reported_pending'


async def test_cancel_while_admission_in_flight_fences_native_authorization(app):
    entered, release = asyncio.Event(), asyncio.Event()
    async def pending(session, args, guard, emit):
        entered.set()
        await release.wait()
        allowed = app.collaboration.admission(session['id'], args)
        assert not allowed['admitted']
        return {'accepted': False, 'reason': allowed['reason']}
    app.runtime.collaboration_input = pending
    await send(app)
    await entered.wait()
    result = await cancel(app)
    assert result['result']['status'] == 'requested'  # No guessed absence during dispatch.
    release.set()
    await settled(app, 'request-1', 'suppressed')
    assert not app.runtime.inputs
    result = (await app.dispatch('coordination.result', {'requestId': 'request-1'}))['result']
    assert result['cancellation']['status'] == 'withdrawn'
    assert not app.state['sessions'][1]['coordinationReference']['cancellations']['items']


async def test_unknown_admission_never_claims_stopped_or_replays(app):
    app.runtime.fail = True
    await send(app)
    await settled(app, 'request-1', 'unknown')
    result = await cancel(app)
    assert result['result']['status'] == 'requested'
    assert app.collaboration.receipt('request-1')['delivery'] == 'unknown'
    await app.collaboration.drain(app.state['sessions'][1]['id'])
    assert len(app.runtime.inputs) == 1


async def test_next_provider_boundary_receives_current_cancellation_without_a_new_turn(app):
    from amplifier_web.coordination_delivery import CoordinationDelivery
    from test_coordination_delivery import Previous, request
    source, target = app.state['sessions']
    await send(app)
    await settled(app, 'request-1', 'accepted')
    async def bridge(operation, args):
        return await app.app_bridge(operation, {'_runtimeSessionId': target['id']}, target['id'])
    delivery = CoordinationDelivery(Previous(), bridge)
    original = request()
    before = await delivery.prepare(original, None)
    await cancel(app, reason='Preserve results and stop new checks')
    after = await delivery.prepare(before, None)
    assert 'Preserve results and stop new checks' in after.model_dump_json()
    assert sum(bool((m.metadata or {}).get('coordinationObservation')) for m in after.messages) == 1
    assert len(app.runtime.inputs) == 1
    delivery.commit(after)


async def test_failed_cancellation_commit_preserves_queued_owner_and_expiry(app, monkeypatch):
    app.state['sessions'][1]['status'] = 'working'
    await send(app)
    original = copy.deepcopy(app.collaboration.receipt('request-1'))
    def fail(*args):
        raise RuntimeError('Cannot retain operation receipt')
    monkeypatch.setattr(app.collaboration, 'insert', fail)
    with pytest.raises(RuntimeError, match='Cannot retain'):
        await cancel(app)
    assert app.collaboration.receipt('request-1') == original
    assert 'request-1' in app.collaboration.expiry_handles
    assert not app.state['sessions'][1]['coordinationReference']['cancellations']['items']


async def test_a_terminal_deferred_report_does_not_close_the_request(app):
    from test_collaborative_workspaces import declared_result, finish
    await declared_result(app, kind='result', outcome='deferred')
    await finish(app, app.state['sessions'][1], 'result-request')
    result = await cancel(app, identity='result-request')
    assert result['result']['status'] == 'requested'


async def test_cancel_suppresses_only_its_unadmitted_return(app):
    from test_collaborative_workspaces import declared_result
    source, _ = app.state['sessions']
    await declared_result(app, kind='question', outcome='question')
    answer = await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'result-request'}, 'wait-result')
    return_id = answer['result']['continuationId']
    assert app.collaboration.receipt(return_id)['delivery'] == 'queued'
    await cancel(app, identity='result-request')
    assert app.collaboration.receipt(return_id)['delivery'] == 'suppressed'
    assert app.collaboration.guard(app.collaboration.receipt(return_id))
    assert return_id not in app.collaboration.expiry_handles


async def test_retry_same_operation_and_new_id_do_not_rewrite_reason(app):
    app.state['sessions'][1]['status'] = 'working'
    await send(app)
    first = await cancel(app, reason='Original reason')
    repeated = await cancel(app, reason='Original reason')
    assert repeated['duplicate'] and repeated['result'] == first['result']
    with pytest.raises(AppError, match='different contents'):
        await cancel(app, reason='Changed reason')
    newer = await cancel(app, command='new-cancel', reason='Different reason')
    assert newer['result'] == first['result']


async def test_foreign_root_child_and_workspace_move_cannot_cancel(app):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await send(app)
    await generation(app, target, 'target-generation', ['target-input'])
    with pytest.raises(AppError):
        await agent_action(app, target, 'coordination.cancel',
            {'sessionId': target['id'], 'requestId': 'request-1'}, 'foreign-cancel')
    source['sessionKind'] = 'worker'
    with pytest.raises(AppError):
        await cancel(app)
    source['sessionKind'] = 'root'
    target['workspace'] += '/moved'
    with pytest.raises(AppError):
        await cancel(app)
    assert app.collaboration.receipt('request-1')['delivery'] == 'queued'


async def test_cancellation_not_lost_behind_newer_receipts_or_submitted_twice(app):
    from amplifier_web.request_cancellation import references
    await send(app)
    await settled(app, 'request-1', 'accepted')
    await cancel(app)
    original = app.collaboration.receipt('request-1')
    # Unrelated historical observations can push the cancelled request out of
    # the recent list without hiding its outstanding cooperative instruction.
    for index in range(35):
        row = {**original, 'requestId': f'new-{index}', 'delivery': 'notified'}
        row.pop('cancellation')
        app.collaboration.insert(row['requestId'], row['requestId'], row)
    assert 'request-1' not in [row['requestId'] for row in app.collaboration.current(app.state['sessions'][1]['id'])['requests']]
    assert references(app.collaboration, app.state['sessions'][1]['id'])['items'][0]['requestId'] == 'request-1'
    # pending_steer withdrawal must be observed before the transport is called.
    row = {**original, 'requestId': 'steer-withdrawn', 'delivery': 'cancelled'}
    app.collaboration.insert('steer-withdrawn', 'steer-withdrawn', row)
    assert (await app.collaboration.submit_steer('steer-withdrawn'))['delivery'] == 'cancelled'
    assert len(app.runtime.inputs) == 1
