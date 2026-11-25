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


@pytest.mark.parametrize('mode', ['queue', 'auto'])
async def test_cancel_while_admission_in_flight_fences_native_authorization(app, mode):
    entered, release = asyncio.Event(), asyncio.Event()
    async def pending(session, args, guard, emit):
        entered.set()
        await release.wait()
        allowed = app.collaboration.admission(session['id'], args)
        assert not allowed['admitted']
        return {'accepted': False, 'reason': allowed['reason']}
    app.runtime.collaboration_input = pending
    await send(app, mode=mode)
    await entered.wait()
    result = await cancel(app)
    assert result['result']['status'] == 'requested'  # No guessed absence during dispatch.
    release.set()
    await settled(app, 'request-1', 'suppressed')
    assert not app.runtime.inputs
    result = (await app.dispatch('coordination.result', {'requestId': 'request-1'}))['result']
    assert result['cancellation']['status'] == 'withdrawn'
    assert not app.state['sessions'][1]['coordinationReference']['cancellations']['items']


@pytest.mark.parametrize('mode', ['queue', 'auto'])
async def test_unknown_admission_never_claims_stopped_or_replays(app, mode):
    app.runtime.fail = True
    await send(app, mode=mode)
    await settled(app, 'request-1', 'unknown')
    result = await cancel(app)
    assert result['result']['status'] == 'requested'
    assert app.collaboration.receipt('request-1')['delivery'] == 'unknown'
    await app.collaboration.drain(app.state['sessions'][1]['id'])
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('mode', ['queue', 'auto'])
async def test_unknown_cancellation_cannot_be_settled_by_unrelated_recipient_turn(app, mode):
    """A clean later turn is not proof about an earlier lost acknowledgement."""
    from test_collaborative_workspaces import finish
    _, target = app.state['sessions']
    app.runtime.fail = True
    await send(app, mode=mode)
    await settled(app, 'request-1', 'unknown')
    await cancel(app)
    original = copy.deepcopy(app.collaboration.receipt('request-1'))
    # The fixture actually accepted the input before losing its acknowledgement.
    # Host-side cancellation fences fresh authorization, not that native input.
    inputs = copy.deepcopy(app.runtime.inputs)
    assert len(inputs) == 1
    assert app.collaboration.guard(original)
    assert not app.collaboration.admission(target['id'], {'inputId': 'request-1'})['admitted']

    await generation(app, target, 'recipient-read-only', ['read-only'])
    with pytest.raises(AppError, match='Declare only your exact delivered request'):
        await agent_action(app, target, 'coordination.reply', {
            'requestId': 'request-1', 'kind': 'result', 'outcome': 'cancelled',
            'text': 'No pending work observed in this later turn.'}, 'unknown-cancel-reply')
    # Even a successfully completed unrelated turn and worker exit cannot
    # manufacture the missing request-specific admission/settlement evidence.
    await finish(app, target, 'read-only')
    await app.on_runtime_event('runtime.ended', {'sessionId': target['id'], 'rootSessionId': target['id']})
    result = (await app.dispatch('coordination.result', {'requestId': 'request-1'}))['result']
    assert result['cancellation']['status'] == 'requested'
    current = app.collaboration.receipt('request-1')
    assert current['delivery'] == 'unknown' and current.get('responseRevision', 0) == 0
    assert current['terminalOutcome'] == original['terminalOutcome']
    assert not current.get('response')
    assert app.runtime.inputs == inputs


@pytest.mark.parametrize('mode', ['queue', 'auto'])
async def test_unknown_cancellation_keeps_exact_fence_and_receipts_after_reload(app, mode):
    app.runtime.fail = True
    await send(app, mode=mode)
    await settled(app, 'request-1', 'unknown')
    first = await cancel(app, reason='Withdraw future admission; past effects remain unknown.')
    original = copy.deepcopy(app.collaboration.receipt('request-1'))
    source, target = app.state['sessions']
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        duplicate = await reopened.dispatch('coordination.cancel', {
            'sessionId': source['id'], 'requestId': 'request-1',
            'reason': 'Withdraw future admission; past effects remain unknown.'},
            origin='agent', caller_session_id=source['id'], command_id='cancel-1')
        assert duplicate['duplicate'] and duplicate['result'] == first['result']
        restored = reopened.collaboration.receipt('request-1')
        assert restored['delivery'] == 'unknown'
        assert restored['terminalOutcome'] == original['terminalOutcome']
        assert restored['cancellation'] == original['cancellation']
        assert reopened.collaboration.guard(restored)
        assert not reopened.collaboration.admission(target['id'], {'inputId': 'request-1'})['admitted']
        reopened._session(target['id'])['status'] = 'idle'
        await reopened.collaboration.drain(target['id'])
        assert not reopened.runtime.inputs
    finally:
        await reopened.close()


@pytest.mark.parametrize('mode', ['queue', 'auto'])
@pytest.mark.parametrize('accepted', [False, True])
async def test_cancelled_unknown_requires_exact_native_evidence_not_cancellation_intent(app, mode, accepted):
    from test_peer_admission_reconciliation import unknown_request
    target = await unknown_request(app, identity='request-1', mode=mode)
    await cancel(app)
    original = copy.deepcopy(app.collaboration.receipt('request-1'))
    evidence = ({'accepted': True, 'inputId': 'request-1', 'mode': 'queue'} if accepted else
                {'accepted': False, 'inputId': 'request-1', 'effect': 'none'})
    await app.on_runtime_event('runtime.delivery', {'sessionId': target['id'], 'inputId': 'request-1',
        'interruptionRevision': 0, 'peerAdmission': evidence})
    result = (await app.dispatch('coordination.result', {'requestId': 'request-1'}))['result']
    assert result['receipt']['delivery'] == ('accepted' if accepted else 'not_sent')
    assert result['cancellation']['status'] == ('requested' if accepted else 'withdrawn')
    current = copy.deepcopy(app.collaboration.receipt('request-1'))
    assert current['cancellation'] == original['cancellation']
    assert current['admission'] == evidence and not current.get('response')
    assert app.collaboration.guard(current)  # Late acceptance cannot reopen admission.
    # Repeated native observations are idempotent; no native input is resubmitted.
    await app.on_runtime_event('runtime.delivery', {'sessionId': target['id'], 'inputId': 'request-1',
        'interruptionRevision': 0, 'peerAdmission': evidence})
    await app.collaboration.drain(target['id'])
    assert app.collaboration.receipt('request-1') == current
    assert not app.runtime.inputs


@pytest.mark.parametrize('mode', ['queue', 'auto', 'steer'])
async def test_shutdown_during_admission_keeps_committed_cancellation_and_closed_wait(app, mode):
    entered = asyncio.Event()
    async def held(*args):
        entered.set()
        await asyncio.Event().wait()
    source, target = app.state['sessions']
    if mode == 'steer':
        await generation(app, target, 'target-generation', ['unrelated-input'])
        app.runtime.collaboration_steer = held
    else:
        app.runtime.collaboration_input = held
    dispatch = asyncio.create_task(send(app, mode=mode))
    await asyncio.wait_for(entered.wait(), 5)
    await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': 'request-1'}, 'wait-before-shutdown')
    accepted = await cancel(app, reason='Stop only this request')
    saved = copy.deepcopy(app.collaboration.receipt('request-1'))
    assert saved['subscription']['open'] is False
    # Queue/auto dispatch is service-owned; steer is held by the transport caller.
    dispatch.cancel()
    await asyncio.gather(dispatch, return_exceptions=True)
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        current = reopened.collaboration.receipt('request-1')
        assert current['delivery'] == 'unknown'
        assert current['cancellation'] == saved['cancellation']
        assert current['subscription'] == saved['subscription']
        retry = await reopened.dispatch('coordination.cancel',
            {'sessionId': source['id'], 'requestId': 'request-1', 'reason': 'Stop only this request'},
            origin='agent', caller_session_id=source['id'], command_id='cancel-1')
        assert retry['duplicate'] and retry['result'] == accepted['result']
        assert current['cancellation']['operationId'] == retry['result']['operationId']
        reopened._session(target['id'])['status'] = 'idle'
        await reopened.collaboration.drain(target['id'])
        assert not reopened.runtime.inputs
    finally:
        await reopened.close()


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
    await send(app, expiresInSeconds=60)
    original = copy.deepcopy(app.collaboration.receipt('request-1'))
    retained = app.db.execute('SELECT receipt FROM commands WHERE id=?', ('request-1',)).fetchone()[0]
    handle = app.collaboration.expiry_handles['request-1']
    deadline = handle.when()
    assert original['delivery'] == 'queued' and original['expiresAt']
    assert deadline > asyncio.get_running_loop().time() and not handle.cancelled()
    def fail(*args):
        raise RuntimeError('Cannot retain operation receipt')
    monkeypatch.setattr(app.collaboration, 'insert', fail)
    with pytest.raises(RuntimeError, match='Cannot retain'):
        await cancel(app)
    assert app.collaboration.receipt('request-1') == original
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('request-1',)).fetchone()[0] == retained
    assert 'request-1' in app.collaboration.expiry_handles
    assert app.collaboration.expiry_handles['request-1'] is handle
    assert handle.when() == deadline and not handle.cancelled()
    assert not app.state['sessions'][1]['coordinationReference']['cancellations']['items']


async def test_failed_cancellation_commit_preserves_ordinary_queue_without_expiry(app, monkeypatch):
    app.state['sessions'][1]['status'] = 'working'
    await send(app)
    original = copy.deepcopy(app.collaboration.receipt('request-1'))
    retained = app.db.execute('SELECT receipt FROM commands WHERE id=?', ('request-1',)).fetchone()[0]
    assert original['delivery'] == 'queued' and 'expiresAt' not in original
    assert 'request-1' not in app.collaboration.expiry_handles
    def fail(*args):
        raise RuntimeError('Cannot retain operation receipt')
    monkeypatch.setattr(app.collaboration, 'insert', fail)
    with pytest.raises(RuntimeError, match='Cannot retain'):
        await cancel(app)
    assert app.collaboration.receipt('request-1') == original
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', ('request-1',)).fetchone()[0] == retained
    assert 'request-1' not in app.collaboration.expiry_handles
    assert not app.state['sessions'][1]['coordinationReference']['cancellations']['items']


async def test_a_terminal_deferred_report_does_not_close_the_request(app):
    from test_collaborative_workspaces import declared_result, finish
    await declared_result(app, kind='result', outcome='deferred')
    await finish(app, app.state['sessions'][1], 'result-request')
    result = await cancel(app, identity='result-request')
    assert result['result']['status'] == 'requested'


@pytest.mark.parametrize('ending', ['failed', 'restart'])
async def test_unconfirmed_report_does_not_claim_an_ended_turn_is_still_running(app, ending):
    from amplifier_web.request_cancellation import status
    from test_collaborative_workspaces import declared_result, finish
    await declared_result(app, outcome='cancelled')
    await cancel(app, identity='result-request')
    if ending == 'failed':
        await finish(app, app.state['sessions'][1], 'result-request', event='generation.failed')
        value = status(app.collaboration.receipt('result-request'))
    else:
        await app.close()
        reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
        try:
            value = status(reopened.collaboration.receipt('result-request'))
        finally:
            await reopened.close()
    assert value['status'] == 'reported_unconfirmed'
    assert 'completion could not be confirmed' in value['detail']
    assert 'not settled yet' not in value['detail']


async def test_current_generation_cancellation_precedes_old_unknowns(app):
    from amplifier_web.request_cancellation import references
    await send(app)
    await settled(app, 'request-1', 'accepted')
    await cancel(app)
    original = app.collaboration.receipt('request-1')
    for index in range(165):
        row = {**original, 'requestId': f'older-{index}', 'delivery': 'unknown'}
        app.collaboration.insert(row['requestId'], row['requestId'], row)
    active = {**original, 'requestId': 'active-cancel'}
    app.collaboration.insert('active-cancel', 'active-cancel', active)
    app.state['sessions'][1]['collaborationGeneration'] = {'inputIds': ['active-cancel']}
    result = references(app.collaboration, app.state['sessions'][1]['id'])
    assert result['items'][0]['requestId'] == 'active-cancel'
    assert len(result['items']) == 160 and result['truncated']


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
