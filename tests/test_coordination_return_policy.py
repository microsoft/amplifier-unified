"""Atomic opt-in peer returns through real app actions and scripted transport.

These checks do not establish native Worker/provider efficacy. The parent owns
joined workflow qualification; no admission/subscription guard is replaced here.
"""
import asyncio
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import (
    Runtime, agent_action, app, finish, generation, send, settled,
)
from test_cross_workspace_coordination import cross_app


async def result(app, identity='request-1', reader=None):
    source = reader or app.state['sessions'][0]
    return (await agent_action(app, source, 'coordination.result',
        {'requestId': identity}, 'inspect-' + identity))['result']


async def declare(app, identity='request-1', *, kind='result', outcome='success',
                  references=None, revision=0):
    target = app.state['sessions'][1]
    await generation(app, target, 'recipient-' + identity + (f'-{revision}' if revision else ''), [identity])
    return await agent_action(app, target, 'coordination.reply', {
        'requestId': identity, 'expectedResponseRevision': revision,
        'kind': kind, 'outcome': outcome, 'text': 'Exact candidate report',
        'references': ['urn:fixture:candidate'] if references is None else references,
    }, 'reply-' + identity + '-' + str(revision))


async def staged(app, **declaration):
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'accepted')
    await declare(app, **declaration)
    return await result(app)


def committed(app, identity):
    path = app.db.execute('PRAGMA database_list').fetchone()[2]
    with sqlite3.connect(path) as database:
        row = database.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def projections(app):
    keys = ('messages', 'historyManaged', 'coordinationReference', 'recentActivityAt')
    return {row['id']: {key: copy.deepcopy(row[key]) for key in keys if key in row}
            for row in app.state['sessions']}


@pytest.mark.parametrize('policy', [None, False, True])
async def test_send_policy_is_explicit_and_saved_before_drain(app, policy):
    source, target = app.state['sessions']
    target['status'] = 'working'
    patch = {} if policy is None else {'returnNotification': policy}
    sent = await send(app, **patch)
    raw = committed(app, 'request-1')
    assert raw['returnNotification'] is (policy is True)
    assert ('subscription' in raw) is (policy is True)
    assert 'returnState' not in raw  # A read projection, not another saved owner.
    state = sent['returnState']
    assert state['returnPolicy'] == ('requested' if policy else 'none')
    assert state['subscription']['registered'] is (policy is True)
    assert state['notification']['status'] == 'not_queued'
    assert state['notification']['consumption'] == 'unknown'
    message, = [m for m in target['messages'] if m.get('inputId') == 'request-1']
    contract = message['peerEnvelope']['returnContract']
    assert contract['requestId'] == 'request-1'
    assert contract['returnNotification'] is (policy is True)
    assert contract['subscriptionRegistered'] is (policy is True)
    if policy:
        assert raw['subscription']['status'] == 'waiting'
        assert contract['continuationId'] == raw['subscription']['continuationId']
    assert not app.runtime.inputs
    assert (await result(app))['receipt'] == raw
    target['status'] = 'idle'
    await app.collaboration.drain(target['id'])
    await settled(app, 'request-1', 'accepted')
    assert len(app.runtime.inputs) == 1
    assert '"returnContract"' in app.runtime.inputs[0][2]
    assert app.assignments.current(source) is None


async def test_optin_accepts_omitted_mode_as_queue(app):
    source, target = app.state['sessions']
    target['status'] = 'working'
    receipt = await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Return the exact result',
        'returnNotification': True}, 'default-mode')
    assert receipt['mode'] == 'queue' and receipt['subscription']['status'] == 'waiting'


@pytest.mark.parametrize('policy', [None, False])
async def test_one_way_result_does_not_gain_an_automatic_subscription(app, policy):
    await send(app, **({} if policy is None else {'returnNotification': policy}))
    await settled(app, 'request-1', 'accepted')
    await declare(app)
    await finish(app, app.state['sessions'][1], 'request-1')
    state = (await result(app))['returnState']
    assert state['qualification']['status'] == 'qualified'
    assert state['returnPolicy'] == 'none'
    assert state['subscription']['registered'] is False
    assert state['notification']['status'] == 'not_queued'
    assert 'subscription' not in app.collaboration.receipt('request-1')
    assert [row[1] for row in app.runtime.inputs] == ['request-1']


@pytest.mark.parametrize('mode', ['notify', 'steer'])
async def test_return_notification_rejects_nonqueue_before_effects(app, mode):
    before = projections(app)
    with pytest.raises(AppError, match='requires queue mode') as denied:
        await send(app, mode=mode, returnNotification=True)
    assert denied.value.status == 400
    assert not committed(app, 'request-1') and not app.runtime.inputs
    assert projections(app) == before


@pytest.mark.parametrize('policy', ['true', 1, None, {}])
async def test_return_notification_requires_boolean(app, policy):
    with pytest.raises(AppError):
        await send(app, returnNotification=policy)
    assert not committed(app, 'request-1')
    assert not app.state['sessions'][1]['messages']


async def test_unsupported_return_refuses_before_input_or_wait(app, monkeypatch):
    monkeypatch.delattr(Runtime, 'collaboration_input')
    with pytest.raises(AppError, match='lacks guarded idle peer admission'):
        await send(app, returnNotification=True)
    assert not committed(app, 'request-1')
    assert not app.state['sessions'][1]['messages']
    ordinary = await send(app, 'ordinary')
    assert ordinary['accepted'] is False and ordinary['result']['effect'] == 'none'


@pytest.mark.parametrize('full_side', ['sender', 'recipient'])
async def test_preflight_both_inboxes_without_partial_send(app, monkeypatch, full_side):
    source, target = app.state['sessions']
    target['status'] = 'working'
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PENDING_INPUTS', 1)
    full = source if full_side == 'sender' else target
    await send(app, 'occupies-slot', sessionId=full['id'])
    before = projections(app)
    with pytest.raises(AppError, match='inbox is full') as denied:
        await send(app, returnNotification=True)
    assert denied.value.status == 429
    assert projections(app) == before and not committed(app, 'request-1')
    assert not app.runtime.inputs


async def test_self_send_reserves_input_and_return_together(app, monkeypatch):
    source = app.state['sessions'][0]
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PENDING_INPUTS', 1)
    with pytest.raises(AppError, match='inbox is full'):
        await send(app, sessionId=source['id'], returnNotification=True)
    assert not source['messages'] and not committed(app, 'request-1')
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PENDING_INPUTS', 2)
    sent = await send(app, sessionId=source['id'], returnNotification=True)
    assert sent['subscription']['status'] == 'waiting'
    assert len(source['messages']) == 1
    with pytest.raises(AppError, match='inbox is full'):
        await send(app, 'third-slot', sessionId=source['id'])
    assert not committed(app, 'third-slot')
    duplicate = await send(app, sessionId=source['id'], returnNotification=True)
    assert duplicate['duplicate'] and duplicate['subscription'] == sent['subscription']


async def test_concurrent_optins_cannot_oversubscribe(app, monkeypatch):
    app.state['sessions'][1]['status'] = 'working'
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PENDING_INPUTS', 1)
    responses = await asyncio.gather(
        send(app, 'first', returnNotification=True),
        send(app, 'second', returnNotification=True), return_exceptions=True)
    successes = [row for row in responses if isinstance(row, dict)]
    failures = [row for row in responses if isinstance(row, AppError)]
    assert len(successes) == len(failures) == 1 and failures[0].status == 429
    assert sum(committed(app, identity) is not None for identity in ('first', 'second')) == 1
    assert len(app.state['sessions'][1]['messages']) == 1
    assert not app.runtime.inputs


@pytest.mark.parametrize('fault', ['wait', 'message', 'insert', 'publish', 'storage'])
async def test_precommit_failure_restores_db_and_affected_projections(app, monkeypatch, fault):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await app.history.ensure_loaded(target['id'])
    before = projections(app)
    # Unrelated retained work must not be part of the send's rollback group.
    app.db.execute('INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)',
                   ('prior-operation', 'prior', json.dumps({'accepted': True})))
    owner, name = {
        'wait': (app.collaboration, 'saved_wait'),
        'message': (app, '_message'),
        'insert': (app.collaboration, 'insert'),
        'publish': (app.collaboration, 'publish_receipt'),
        'storage': (app.directory, 'sync'),
    }[fault]
    original = getattr(owner, name)

    def fail(*args, **kwargs):
        if fault == 'storage' and not app.db.execute(
                "SELECT 1 FROM commands WHERE id='request-1'").fetchone():
            return original(*args, **kwargs)
        assert app.db.in_transaction
        if fault != 'publish':
            original(*args, **kwargs)
        raise OSError('Synthetic precommit failure')

    with monkeypatch.context() as scoped:
        scoped.setattr(owner, name, fail)
        with pytest.raises(OSError, match='precommit'):
            await send(app, returnNotification=True)
    assert committed(app, 'prior-operation') == {'accepted': True}
    assert committed(app, 'request-1') is None
    assert not app.db.in_transaction
    assert projections(app) == before
    assert not app.runtime.inputs and 'request-1' not in app.collaboration.expiry_handles
    app._publish_changes(sessions={source['id'], target['id']})
    # A later publication/retry cannot persist a phantom earlier peer input.
    sent = await send(app, returnNotification=True)
    assert sent['subscription']['status'] == 'waiting'
    assert sum(m.get('inputId') == 'request-1' for m in target['messages']) == 1


async def test_postcommit_lost_ack_keeps_service_owned_drain(app, monkeypatch):
    source, target = app.state['sessions']
    publish = app.collaboration.publish_receipt
    lost = False

    def fail_once(receipt):
        nonlocal lost
        publish(receipt)
        if receipt.get('requestId') == 'request-1' and not lost:
            lost = True
            assert committed(app, 'request-1')['subscription']['status'] == 'waiting'
            raise OSError('Synthetic lost acknowledgement')

    monkeypatch.setattr(app.collaboration, 'publish_receipt', fail_once)
    with pytest.raises(OSError, match='lost acknowledgement'):
        await send(app, returnNotification=True)
    # No caller retry or manual drain is needed to deliver the committed input.
    retained = await settled(app, 'request-1', 'accepted')
    assert retained['subscription']['status'] == 'waiting'
    assert [row[1] for row in app.runtime.inputs] == ['request-1']
    duplicate = await send(app, returnNotification=True)
    assert duplicate['duplicate'] and duplicate['delivery'] == 'accepted'
    assert sum(m.get('inputId') == 'request-1' for m in target['messages']) == 1


async def test_exact_retry_keeps_cancelled_policy_at_full_capacity(app, monkeypatch):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await send(app, returnNotification=True)
    await agent_action(app, source, 'coordination.cancel', {
        'sessionId': source['id'], 'requestId': 'request-1'}, 'cancel-request')
    before = app.collaboration.receipt('request-1')
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PENDING_INPUTS', 0)
    retry = await send(app, returnNotification=True)
    assert retry['duplicate'] and retry['delivery'] == 'cancelled'
    assert retry['subscription'] == before['subscription']
    assert retry['returnState']['subscription']['status'] == 'cancelled'
    assert retry['returnState']['notification']['status'] == 'not_queued'
    with pytest.raises(AppError, match='different contents'):
        await send(app, returnNotification=False)
    late = await agent_action(app, source, 'coordination.subscribe', {
        'sessionId': source['id'], 'requestId': 'request-1'}, 'no-rearm')
    assert late['duplicate'] and late['result']['status'] == 'cancelled'
    assert app.collaboration.receipt('request-1') == before
    assert not app.runtime.inputs


async def test_legacy_retry_and_restart_never_add_a_return(app):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await send(app)
    legacy = app.collaboration.receipt('request-1')
    legacy.pop('returnNotification')
    app.collaboration.save('request-1', legacy)
    app._publish_changes(sessions={source['id'], target['id']})
    retry = await send(app)
    assert retry['duplicate'] and 'subscription' not in retry
    state = retry['returnState']
    assert state['returnPolicy'] == 'legacy_unknown'
    assert state['subscription']['status'] == state['notification']['status'] == 'unknown'
    assert state['response']['status'] == 'unknown'
    assert state['acknowledgement']['status'] == 'unknown'
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        reopened.collaboration.start()
        assert reopened.collaboration.receipt('request-1') == legacy
        assert 'subscription' not in reopened.collaboration.receipt('request-1')
    finally:
        await reopened.close()


@pytest.mark.parametrize('kind,outcome,ready', [
    ('result', 'success', False), ('ack', 'success', False),
    ('question', 'question', True), ('defer', 'deferred', True),
    ('result', 'failed', True),
])
async def test_staged_response_and_meaningful_outcome_are_independent(app, kind, outcome, ready):
    state = (await staged(app, kind=kind, outcome=outcome))['returnState']
    assert state['response']['status'] == 'staged'
    assert state['response']['outcome'] == outcome
    assert state['qualification']['status'] == 'unqualified'
    assert state['outcome']['status'] == ('staged' if ready else 'absent')
    assert state['notification']['status'] == ('queued' if ready else 'not_queued')
    if ready:
        notification = app.collaboration.receipt(state['notification']['requestId'])
        assert notification['dependencySatisfied'] is False
    assert len(app.runtime.inputs) == 1  # Busy requester has not received a return.


async def test_sealed_return_is_revision_specific_and_review_is_not_consumption(app):
    await staged(app)
    source, target = app.state['sessions']
    await finish(app, target, 'request-1')
    current = await result(app)
    state = current['returnState']
    assert current['qualified'] and state['qualification']['status'] == 'qualified'
    assert state['response']['status'] == state['outcome']['status'] == 'sealed'
    assert state['outcome']['source'] == 'response'
    assert state['notification']['status'] == 'queued'
    assert state['artifacts']['status'] == 'not_requested'
    assert state['artifacts']['items'] == [{'reference': 'urn:fixture:candidate', 'status': 'citation'}]
    assert not state['qualification']['independentArtifactVerification']
    review = state['acknowledgement']['review']['args']
    assert review == current['notification']['review']['args']
    await agent_action(app, source, 'coordination.acknowledge', review, 'review-first')
    reviewed = (await result(app))['returnState']
    assert reviewed['acknowledgement']['status'] == 'recorded'
    assert reviewed['notification']['status'] == 'suppressed'
    assert reviewed['notification']['acknowledgementId'] == 'review-first'
    assert reviewed['notification']['consumption'] == 'unknown'
    # A new report remains independently visible; no old notification is rearmed.
    await declare(app, kind='question', outcome='question', revision=1)
    newer = (await result(app))['returnState']
    assert newer['outcome']['outcomeRevision'] == 2
    assert newer['notification']['requestId'] != state['notification']['requestId']
    assert newer['notification']['status'] == 'queued'
    assert newer['acknowledgement']['status'] == 'not_recorded'
    assert newer['acknowledgement']['lastRecorded']['outcomeRevision'] == 1
    assert app.collaboration.receipt(state['notification']['requestId'])['delivery'] == 'suppressed'


async def test_backpressured_review_without_notification_is_not_delivery(app, monkeypatch):
    source, target = app.state['sessions']
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PENDING_INPUTS', 1)
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'accepted')
    # An interim report keeps a reservation for the next revision; with one
    # slot it cannot also queue this report. Exercise the real slot accounting.
    await declare(app, kind='question', outcome='question')
    current = await result(app)
    state = current['returnState']
    assert state['subscription']['status'] == 'backpressured'
    assert state['notification']['status'] == 'not_queued'
    assert state['notification']['receiptPresent'] is False
    identity = state['notification']['expectedNotificationId']
    await agent_action(app, source, 'coordination.acknowledge',
        state['acknowledgement']['review']['args'], 'review-without-notification')
    reviewed = (await result(app))['returnState']
    assert reviewed['subscription']['status'] == 'claimed'
    assert reviewed['acknowledgement']['status'] == 'recorded'
    assert reviewed['notification']['status'] == 'not_queued'
    assert not committed(app, identity)
    monkeypatch.setattr('amplifier_web.collaboration.MAX_PENDING_INPUTS', 64)
    app.collaboration.resume_outcomes(source['id'])
    app.collaboration.enqueue_continuation(app.collaboration.receipt('request-1'))
    assert not committed(app, identity)


@pytest.mark.parametrize('boundary', ['stop', 'paused', 'completed', 'task', 'owner'])
async def test_automatic_return_keeps_stop_task_and_owner_guards(app, boundary):
    await staged(app)
    source, target = app.state['sessions']
    if boundary == 'stop':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    elif boundary in {'paused', 'completed'}:
        source['task'] = {'id': 'objective', 'revision': 1, 'status': boundary}
    elif boundary == 'task':
        source['task'] = {'id': 'different-objective', 'revision': 1, 'status': 'active'}
    else:
        source['ownership'] = {'status': 'yielded'}
    await finish(app, target, 'request-1')
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    state = (await result(app))['returnState']
    assert state['qualification']['status'] == 'qualified'
    if boundary == 'stop':
        assert state['subscription']['status'] == 'suppressed'
        assert state['notification']['status'] == 'not_queued'
    else:
        assert state['notification']['status'] == 'suppressed'
    await app.collaboration.drain(source['id'])
    assert [row[1] for row in app.runtime.inputs] == ['request-1']


@pytest.mark.parametrize('limit', ['maxTotalTokens', 'maxCostUsd'])
async def test_capped_assignment_refusal_does_not_register_or_transfer_return(cross_app, limit):
    app = cross_app
    source, target = app.state['sessions']
    assignment = app.assignments.ensure(source, 'bounded-source')
    assignment['limits'][limit] = 1000
    app.assignments.save(assignment)
    app.db.commit()
    before = projections(app)
    with pytest.raises(AppError, match='request-scoped assignment budget'):
        await send(app, returnNotification=True)
    assert projections(app) == before and not committed(app, 'request-1')
    assert app.assignments.current(target) is None and not app.runtime.inputs
    assert app.assignments.current(source)['limits'][limit] == 1000


@pytest.mark.parametrize('usage', [{'totalTokens': 100}, {}], ids=['exhausted', 'unknown'])
async def test_optin_return_does_not_override_saved_budget_readiness(app, usage):
    await staged(app)
    source, target = app.state['sessions']
    await finish(app, target, 'request-1')
    task = source.get('task') or app.coordination.task(source['id'])
    args = {'taskId': task.get('id'), 'taskRevision': task.get('revision'),
            '_runtimeSessionId': source.get('runtimeSessionId') or source['id'],
            '_generationId': source['collaborationGeneration']['id'],
            '_inputBindings': [{'inputId': value} for value in source['collaborationGeneration']['inputIds']]}
    assert (await app.app_bridge('coordination.continuation_ready', args, source['id']))['ready']
    # Synthetic persisted controller/usage evidence, consumed by the real
    # continuation_ready boundary; no capacity or subscription guard is patched.
    path = app.data_dir / 'sessions' / (source.get('runtimeSessionId') or source['id']) / 'control-state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    controls = json.loads(path.read_text()) if path.exists() else {}
    controls['capacity'] = {'revision': 1, 'enabled': True, 'maxTotalTokens': 100, 'unknownPolicy': 'pause'}
    path.write_text(json.dumps(controls))
    from amplifier_web.execution import ingest
    ingest(source, {'id': 'bounded-call', 'kind': 'llm', 'sessionId': source['id'],
        'rootSessionId': source['id'], 'phase': 'completed', 'revision': 1,
        'startedAt': 1, 'endedAt': 2, 'usage': usage})
    before = app.collaboration.receipt('request-1')
    assert not (await app.app_bridge('coordination.continuation_ready', args, source['id']))['ready']
    assert app.collaboration.receipt('request-1') == before
    assert path.read_text() == json.dumps(controls)
    assert [row[1] for row in app.runtime.inputs] == ['request-1']


async def test_terminal_failure_takes_precedence_over_the_typed_response(app):
    await staged(app)
    await finish(app, app.state['sessions'][1], 'request-1',
                 event='generation.failed', nativeTerminal=None)
    state = (await result(app))['returnState']
    assert state['response']['status'] == 'rejected'
    assert state['response']['outcome'] == 'success'  # Retained declaration, not the host verdict.
    assert state['outcome']['source'] == 'terminalOutcome'
    assert state['outcome']['outcome'] == 'failed'
    assert state['qualification']['status'] == 'unqualified'
    assert state['notification']['status'] == 'queued'


async def test_host_admission_unknown_is_projected_without_inventing_a_reply(app):
    app.runtime.fail = True
    await send(app, returnNotification=True)
    await settled(app, 'request-1', 'unknown')
    state = (await result(app))['returnState']
    assert state['response']['status'] == 'absent'
    assert state['outcome']['source'] == 'terminalOutcome'
    assert state['outcome']['status'] == 'observed'
    assert state['outcome']['outcome'] == 'unknown'
    assert state['qualification']['status'] == 'unqualified'
    assert state['notification']['status'] == 'queued'
    retry = await send(app, returnNotification=True)
    assert retry['duplicate'] and retry['delivery'] == 'unknown'
    await app.collaboration.drain(app.state['sessions'][1]['id'])
    assert [row[1] for row in app.runtime.inputs] == ['request-1']


@pytest.mark.parametrize('delivery', ['accepted', 'unknown', 'not_sent'])
async def test_notification_projection_follows_actual_transport_receipt(app, delivery):
    await staged(app)
    source, target = app.state['sessions']
    await finish(app, target, 'request-1')
    identity = app.collaboration.receipt('request-1')['subscription']['continuationId']
    entered, release = asyncio.Event(), asyncio.Event()
    transport = app.runtime.collaboration_input

    async def held(session, args, guard, emit):
        entered.set()
        await release.wait()
        if delivery == 'not_sent':
            return {'accepted': False, 'delivery': 'not_sent', 'reason': 'Scripted pre-admission refusal'}
        app.runtime.fail = delivery == 'unknown'
        return await transport(session, args, guard, emit)

    app.runtime.collaboration_input = held
    source['status'] = 'idle'
    drain = asyncio.create_task(app.collaboration.drain(source['id']))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        in_flight = (await result(app))['returnState']
        assert in_flight['notification']['status'] == 'submitting'
        assert in_flight['notification']['requestId'] == identity
    finally:
        release.set()
        await drain
    await settled(app, identity, delivery)
    state = (await result(app))['returnState']
    assert state['notification']['status'] == delivery
    assert state['notification']['consumption'] == 'unknown'
    assert state['acknowledgement']['status'] == 'not_recorded'
    assert state['qualification']['status'] == 'qualified'
    before = list(app.runtime.inputs)
    await app.collaboration.drain(source['id'])
    assert app.runtime.inputs == before


async def test_cross_workspace_saved_retention_is_separate_from_qualification(cross_app):
    app = cross_app
    source, target = app.state['sessions']
    path = Path(target['workingDirectory']) / 'report.txt'
    path.write_bytes(b'Exact retained report\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    reference = 'report.txt@sha256:' + digest
    state = (await staged(app, references=[reference]))['returnState']
    assert state['artifacts']['status'] == 'retained'
    assert state['qualification']['status'] == 'unqualified'
    item, = state['artifacts']['items']
    assert item['reference'] == reference and item['sha256'] == digest and item['outputId']
    path.unlink()
    saved = (await agent_action(app, source, 'coordination.artifact', {
        'sessionId': source['id'], 'requestId': 'request-1', 'responseRevision': 1,
        'reference': reference}, 'read-retained'))['result']
    assert saved['text'] == 'Exact retained report\n' and not saved['qualified']
    await finish(app, target, 'request-1')
    state = (await result(app))['returnState']
    assert state['artifacts']['items'] == [item]
    assert state['qualification']['status'] == 'qualified'
    assert state['notification']['status'] == 'queued'
    assert app.assignments.current(source) is None and app.assignments.current(target) is None


async def test_partial_retention_never_qualifies_success(app):
    target = app.state['sessions'][1]
    path = Path(target.get('workingDirectory') or target['workspace']) / 'report.txt'
    path.write_bytes(b'Retained evidence')
    good = 'report.txt@sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
    missing = 'missing.txt@sha256:' + 'a' * 64
    state = (await staged(app, references=[good, missing]))['returnState']
    assert state['artifacts']['status'] == 'partial'
    assert [item['status'] for item in state['artifacts']['items']] == ['retained', 'unavailable']
    await finish(app, target, 'request-1')
    state = (await result(app))['returnState']
    assert state['response']['status'] == 'sealed'
    assert state['qualification']['status'] == 'unqualified'
    assert state['notification']['status'] == 'queued'
    assert app.collaboration.receipt(state['notification']['requestId'])['dependencySatisfied'] is False


async def test_large_unicode_references_are_bounded_without_changing_raw_result(app):
    references = ['urn:fixture:' + str(index) + ':' + '\u754c' * 1900 for index in range(16)]
    current = await staged(app, references=references)
    raw = copy.deepcopy(app.collaboration.receipt('request-1'))
    state = current['returnState']
    assert len(json.dumps(state).encode()) <= 8192
    assert len(json.dumps(state, ensure_ascii=False).encode()) <= 8192
    assert state['artifacts']['omittedCount'] > 0
    assert state['artifacts']['referenceCount'] == len(references)
    assert state['observation']['coverage'] == 'bounded'
    assert state['resultRef']['args']['requestId'] == 'request-1'
    assert state['artifacts']['responseRevision'] == 1
    assert all(row['reference'] in references for row in state['artifacts']['items'])
    assert current['receipt']['response']['references'] == references
    assert current['revisions'][0]['references'] == references
    await result(app)
    assert app.collaboration.receipt('request-1') == raw