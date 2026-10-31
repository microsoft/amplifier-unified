"""Revision, queue and watch protocol evidence; scripted runtime, no model efficacy claim."""
import asyncio
import copy
import json
import time

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, agent_action, declared_result, finish, generation, Runtime, send, settled, until


async def revised(app, request, revision, kind='result', outcome='success', command='later-reply'):
    target = app.state['sessions'][1]
    return await agent_action(app, target, 'coordination.reply', {
        'requestId': request, 'expectedResponseRevision': revision, 'kind': kind,
        'outcome': outcome, 'text': 'An explicitly correlated later report', 'references': ['opaque-evidence']}, command)


async def subscribe(app, request='result-request'):
    source = app.state['sessions'][0]
    return await agent_action(app, source, 'coordination.subscribe',
        {'sessionId': source['id'], 'requestId': request}, 'wait-'+request)


async def test_question_later_generation_result_distinct_revision_once_and_old_receipts_retained(app):
    source, target = app.state['sessions']
    await declared_result(app, kind='question', outcome='question')
    wait = await subscribe(app)
    first = app.collaboration.receipt(wait['result']['continuationId'])
    assert first['dependencySatisfied'] is False and first['continuationReason'] == 'question'
    assert first['outcomeSnapshot']['status'] == 'staged'
    await finish(app, target, 'result-request')
    assert app.collaboration.receipt('result-request')['subscription']['lastClaimedRevision'] == 1
    await generation(app, target, 'followup-generation', ['followup-input'])
    reply = await revised(app, 'result-request', 1)
    assert reply['result']['responseRevision'] == 2
    # Matching terminal evidence binds the current actual input, while the
    # declaration's request ID keeps the original request relationship.
    from amplifier_web.automatic_history import display_identity
    from amplifier_operations.coordination import fingerprint
    anchor = {'messageId': display_identity(target, 9, 'assistant', 'Later result'),
        'nativeIndex': 9, 'nativeText': 'Later result', 'textDigest': fingerprint('Later result'),
        'rootSessionId': target['id'], 'generationId': 'followup-generation'}
    await finish(app, target, 'result-request', generation_id='followup-generation',
        input_ids=['followup-input'], text='Later result', nativeTerminal=anchor, active_job_ids=['unrelated-job'])
    result = (await app.dispatch('coordination.result', {'requestId': 'result-request'}))['result']
    assert result['qualified'] and result['responseRevision'] == 2
    assert [row['responseRevision'] for row in result['revisions']] == [2, 1]
    assert result['revisions'][1]['outcome'] == 'question' and result['revisions'][1]['status'] == 'sealed'
    request = app.collaboration.receipt('result-request')
    second_id = app.collaboration.continuation_identity(request['subscription'], 2)
    second = app.collaboration.receipt(second_id)
    assert second_id != first['requestId'] and second['dependencySatisfied']
    # Duplicate events and even a later read of the original operation cannot
    # re-claim either revision, nor can changed payload reuse the same operation.
    app.collaboration.enqueue_continuation(request)
    assert app.db.execute("SELECT count(*) FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')='result-request'").fetchone()[0] == 2
    await generation(app, target, 'third-generation', ['another-input'])
    duplicate = await revised(app, 'result-request', 1)
    assert duplicate['duplicate'] and duplicate['result']['status'] == 'sealed'
    with pytest.raises(AppError, match='different contents'):
        await revised(app, 'result-request', 2)
    with pytest.raises(AppError, match='revision changed'):
        await revised(app, 'result-request', 1, command='stale-reply')


@pytest.mark.parametrize('change', ['stop', 'task', 'workspace', 'child', 'stale'])
async def test_later_reply_requires_current_recipient_and_original_boundary(app, change):
    source, target = app.state['sessions']
    await declared_result(app, kind='defer', outcome='deferred')
    await finish(app, target, 'result-request')
    await generation(app, target, 'later', ['next-input'])
    if change == 'stop':
        target['interruptionRevision'] = 1
    elif change == 'task':
        target['task'] = {'id': 'new-task', 'revision': 2, 'status': 'active'}
    elif change == 'workspace':
        target['workspace'] += '/changed'
    elif change == 'child':
        target['sessionKind'] = 'worker'
    else:
        target['collaborationGeneration']['terminal'] = True
    with pytest.raises(AppError):
        await revised(app, 'result-request', 1)
    assert app.collaboration.receipt('result-request')['responseRevision'] == 1


async def test_stopped_requester_late_reply_is_retained_but_cannot_restart_it(app):
    source, target = app.state['sessions']
    await declared_result(app, kind='defer', outcome='deferred')
    await subscribe(app)
    await finish(app, target, 'result-request')
    source['interruptionRevision'] = 1
    source['status'] = 'idle'
    messages = copy.deepcopy(source['messages'])
    inputs = copy.deepcopy(app.runtime.inputs)
    await generation(app, target, 'later', ['followup'])
    await revised(app, 'result-request', 1, kind='question', outcome='question')
    request = app.collaboration.receipt('result-request')
    identity = app.collaboration.continuation_identity(request['subscription'], 2)
    await app.collaboration.drain(source['id'])
    # Stop closes the saved wait before a new command or message is admitted.
    assert request['subscription']['status'] == 'suppressed'
    assert request['subscription']['open'] is False
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
    result = (await app.dispatch('coordination.result', {'requestId': 'result-request'}))['result']
    assert result['responseRevision'] == 2
    assert [row['responseRevision'] for row in result['revisions']] == [2, 1]
    assert result['revisions'][0]['outcome'] == 'question'
    assert result['revisions'][0]['text'] == 'An explicitly correlated later report'
    assert source['messages'] == messages
    assert len(inputs) == 1 and app.runtime.inputs == inputs
    assert source['status'] == 'idle' and source['interruptionRevision'] == 1


async def test_more_than_32_replies_notify_once_with_provenance_and_bounded_result_view(app):
    from amplifier_operations.coordination import fingerprint
    from amplifier_web.automatic_history import display_identity
    from amplifier_web.peer_attribution import PeerAttribution

    source, target = app.state['sessions']
    await declared_result(app, kind='question', outcome='question')
    await subscribe(app)
    identities = []
    for revision in range(1, 35):
        current = 'recipient-result-request' if revision == 1 else f'report-{revision}'
        inputs = ['result-request'] if revision == 1 else [f'followup-{revision}']
        if revision > 1:
            await generation(app, target, current, inputs)
            reply = await revised(app, 'result-request', revision - 1,
                kind='question', outcome='question', command=f'reply-{revision}')
            assert reply['result']['responseRevision'] == revision
        text = f'Report {revision}'
        anchor = {'messageId': display_identity(target, revision, 'assistant', text),
            'nativeIndex': revision, 'nativeText': text, 'textDigest': fingerprint(text),
            'rootSessionId': target['id'], 'generationId': current}
        await finish(app, target, 'result-request', generation_id=current,
            input_ids=inputs, text=text, nativeTerminal=anchor)
        request = app.collaboration.receipt('result-request')
        identity = app.collaboration.continuation_identity(request['subscription'], revision)
        identities.append(identity)
        source['status'] = 'idle'
        await app.collaboration.drain(source['id'])
        notice = await settled(app, identity, 'accepted')
        assert notice['dependencyOutcomeRevision'] == revision
        assert notice['dependencySatisfied'] is False
        row = next(row for row in source['messages'] if row.get('inputId') == identity)
        projected = PeerAttribution(app.db).resolve(source, [row])[0]
        assert projected['attribution']['caption'] == 'Amplifier update'
        app.collaboration.enqueue_continuation(request)
    assert [row[1] for row in app.runtime.inputs] == ['result-request', *identities]
    result = (await app.dispatch('coordination.result', {'requestId': 'result-request'}))['result']
    assert result['responseRevision'] == 34
    assert [row['responseRevision'] for row in result['revisions']] == list(range(34, 2, -1))
    assert app.collaboration.receipt('declare-result-request')['result']['status'] == 'sealed'
    await generation(app, target, 'later', ['later-input'])
    duplicate = await revised(app, 'result-request', 33, kind='question',
        outcome='question', command='reply-34')
    assert duplicate['duplicate'] and duplicate['result']['responseRevision'] == 34
    with pytest.raises(AppError, match='different contents'):
        await revised(app, 'result-request', 34, command='reply-34')
    with pytest.raises(AppError, match='revision changed'):
        await revised(app, 'result-request', 33, command='stale-after-32')
    assert [row[1] for row in app.runtime.inputs] == ['result-request', *identities]


@pytest.mark.parametrize('change', [
    'none', 'stop', 'cancelled', 'expired', 'nonmatching-limit', 'counter',
    'protocol', 'legacy-subscription', 'already-claimed', 'unknown-delivery',
])
async def test_restart_recovers_only_verified_retired_notification_limit(app, monkeypatch, change):
    source, target = app.state['sessions']
    await declared_result(app)
    await subscribe(app)
    enqueue = app.collaboration.enqueue_continuation
    monkeypatch.setattr(app.collaboration, 'enqueue_continuation', lambda request: None)
    await finish(app, target, 'result-request')
    request = app.collaboration.receipt('result-request')
    request['outcomeRevision'] = request['response']['outcomeRevision'] = 33
    wait = request['subscription']
    wait.update(status='limit_reached', open=False, lastClaimedRevision=32,
        detail='Automatic outcome notification limit reached; inspect the retained result.')
    identity = app.collaboration.continuation_identity(wait, 33)
    if change == 'stop':
        source['interruptionRevision'] = 1
    elif change in {'cancelled', 'expired'}:
        wait['status'] = change
    elif change == 'nonmatching-limit':
        wait['detail'] = 'A different deliberate limit was reached.'
    elif change == 'counter':
        request['outcomeRevision'] = request['response']['outcomeRevision'] = 32
    elif change == 'protocol':
        request['protocol'] = 1
    elif change == 'legacy-subscription':
        wait['notificationVersion'] = 1
    elif change == 'already-claimed':
        wait['lastClaimedRevision'] = 33
    elif change == 'unknown-delivery':
        # An existing delivery with an uncertain acknowledgement remains exact;
        # recovery must neither replay it nor invent a replacement identity.
        app.collaboration.insert(identity, 'retained-unknown', {
            'accepted': True, 'requestId': identity, 'inputId': identity,
            'commandAction': 'coordination.send', 'delivery': 'unknown',
            'dependencyRequestId': 'result-request', 'dependencyOutcomeRevision': 33,
            'target': {'sessionId': source['id']}})
    app.collaboration.save('result-request', request)
    app._publish()
    monkeypatch.setattr(app.collaboration, 'enqueue_continuation', enqueue)
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        reopened._session(source['id'])['status'] = 'idle'
        reopened.collaboration.start()
        if change == 'none':
            returned = await settled(reopened, identity, 'accepted')
            assert returned['dependencyOutcomeRevision'] == 33
            assert returned['dependencySatisfied'] is True
            saved = reopened.collaboration.receipt('result-request')['subscription']
            assert saved['continuationId'] == wait['continuationId']
            assert saved['lastClaimedRevision'] == 33 and 'detail' not in saved
            reopened.collaboration.start()
            await reopened.collaboration.drain(source['id'])
            assert [row[1] for row in reopened.runtime.inputs] == [identity]
        else:
            await reopened.collaboration.drain(source['id'])
            assert not reopened.runtime.inputs
            saved = reopened.collaboration.receipt('result-request')['subscription']
            assert saved['lastClaimedRevision'] == wait['lastClaimedRevision']
            assert saved['status'] == ('suppressed' if change == 'stop' else wait['status'])
            row = reopened.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if change == 'unknown-delivery':
                assert json.loads(row[0])['delivery'] == 'unknown'
            else:
                assert row is None
        assert reopened.collaboration.receipt('declare-result-request')['result']['status'] == 'sealed'
    finally:
        await reopened.close()


async def watch(app, identity='watch', condition='status_changed', expires=60):
    source, target = app.state['sessions'][:2]
    return await agent_action(app, source, 'coordination.watch', {'sessionId': source['id'],
        'targetSessionId': target['id'], 'condition': condition, 'expiresInSeconds': expires}, identity)


async def test_one_shot_watch_ignores_activity_notices_claims_once_and_never_runs_target(app):
    source, target = app.state['sessions']
    before = copy.deepcopy(target['messages'])
    result = await watch(app)
    assert result['watch']['status'] == 'waiting'
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle', 'activityOnly': True})
    assert app.collaboration.receipt('watch')['watch']['status'] == 'waiting'
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'working'})
    watch_receipt = app.collaboration.receipt('watch')
    assert watch_receipt['watch']['status'] == 'changed'
    identity = watch_receipt['subscription']['continuationId']
    assert app.collaboration.receipt(identity)['dependencySatisfied'] is False
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})
    assert target['messages'] == before and not app.runtime.inputs
    assert app.db.execute("SELECT count(*) FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')='watch'").fetchone()[0] == 1
    read = await app.dispatch('coordination.watch.read', {'sessionId': source['id'], 'watchId': 'watch'})
    assert read['result']['watch']['status'] == 'changed'


async def test_watch_expiry_and_cancel_survive_restart_without_replaying(app):
    source, target = app.state['sessions']
    first = await watch(app, 'expires', expires=1)
    second = await watch(app, 'cancelled', expires=1)
    await agent_action(app, source, 'coordination.watch.cancel',
        {'sessionId': source['id'], 'watchId': 'cancelled'}, 'cancel-operation')
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        reopened.collaboration.start()
        await until(lambda: reopened.collaboration.receipt('expires')['watch']['status'] == 'expired')
        assert reopened.collaboration.receipt('cancelled')['watch']['status'] == 'cancelled'
        assert reopened.collaboration.receipt(first['subscription']['continuationId'])['dependencySatisfied'] is False
        assert not reopened.db.execute('SELECT 1 FROM commands WHERE id=?', (second['subscription']['continuationId'],)).fetchone()
        reopened.collaboration.start()
        assert reopened.db.execute("SELECT count(*) FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')='expires'").fetchone()[0] == 1
        assert not reopened.runtime.inputs
    finally:
        await reopened.close()


async def test_watch_cancellation_suppresses_unsubmitted_notification_and_foreign_root_denied(app):
    source, target = app.state['sessions']
    result = await watch(app, condition='idle')
    identity = result['subscription']['continuationId']
    assert app.collaboration.receipt(identity)['delivery'] == 'queued'
    await agent_action(app, source, 'coordination.watch.cancel', {'sessionId': source['id'], 'watchId': 'watch'}, 'cancel')
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    assert app.collaboration.receipt(identity)['delivery'] == 'suppressed' and not app.runtime.inputs
    with pytest.raises(AppError, match='different caller'):
        await app.dispatch('coordination.watch.read', {'sessionId': target['id'], 'watchId': 'watch'})


async def test_sender_quota_reserves_capacity_for_an_independent_sender_and_round_robin(app, monkeypatch):
    from amplifier_web import test_execution_settings as execution_settings
    monkeypatch.setitem(execution_settings.CONCURRENCY_DEFAULTS, 'maxPendingPeerInputsPerSender', 16)
    source, target = app.state['sessions']
    target['status'] = 'working'
    other = app._new_session({'title': 'Independent'})
    app.state['sessions'].append(other)
    await generation(app, other, 'other-generation', ['human-input'])
    for index in range(16):
        await send(app, f'queued-{index}')
    with pytest.raises(AppError, match='16 pending') as error:
        await send(app, 'too-many')
    assert error.value.status == 429
    await agent_action(app, other, 'coordination.send', {'sessionId': target['id'], 'text': 'Independent task'}, 'other-input')
    target['status'] = 'idle'
    await app.collaboration.drain(target['id'])
    assert app.runtime.inputs[-1][1] == 'queued-0'
    target['status'] = 'idle'
    await app.collaboration.drain(target['id'])
    assert app.runtime.inputs[-1][1] == 'other-input'
    target['status'] = 'idle'
    await app.collaboration.drain(target['id'])
    assert app.runtime.inputs[-1][1] == 'queued-1'


async def test_queue_expiry_records_negative_outcome_without_admitting_or_losing_input(app):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await send(app, 'expiring-input', expiresInSeconds=1)
    await subscribe(app, 'expiring-input')
    await until(lambda: app.collaboration.receipt('expiring-input')['delivery'] == 'expired')
    retained = app.collaboration.receipt('expiring-input')
    assert retained['terminalOutcome']['outcome'] == 'failed'
    assert app.collaboration.receipt(retained['subscription']['continuationId'])['dependencySatisfied'] is False
    assert any(message['inputId'] == 'expiring-input' for message in target['messages'])
    target['status'] = 'idle'
    await app.collaboration.drain(target['id'])
    assert not app.runtime.inputs


@pytest.mark.parametrize('failure', ['before', 'after'])
async def test_continuation_commit_failure_preserves_report_without_duplicate_or_phantom(app, monkeypatch, failure):
    source, target = app.state['sessions']
    await declared_result(app)
    result = await subscribe(app)
    identity = result['result']['continuationId']
    publish = app._publish_changes
    def fail(**kwargs):
        # Fail only this new continuation's commit, not earlier observation saves.
        if app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
            if failure == 'after':
                publish(**kwargs)
            raise OSError('Injected notification commit failure')
        return publish(**kwargs)
    monkeypatch.setattr(app, '_publish_changes', fail)
    with pytest.raises(OSError):
        await finish(app, target, 'result-request')
    monkeypatch.setattr(app, '_publish_changes', publish)
    request = app.collaboration.receipt('result-request')
    assert request['response']['qualified']
    if failure == 'before':
        assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
        assert not any(m.get('inputId') == identity for m in source['messages'])
        app.collaboration.enqueue_continuation(request)
    assert app.collaboration.receipt(identity)['dependencySatisfied']
    app.collaboration.enqueue_continuation(app.collaboration.receipt('result-request'))
    assert sum(m.get('inputId') == identity for m in source['messages']) == 1


@pytest.mark.parametrize('failure', ['before', 'after'])
async def test_watch_lost_commit_ack_exact_retry_retains_single_observation(app, monkeypatch, failure):
    source, _ = app.state['sessions']
    publish = app.collaboration.publish_receipt
    def fail(receipt):
        if receipt.get('requestId') == 'watch':
            if failure == 'after':
                publish(receipt)
            raise OSError('Injected watch commit failure')
        return publish(receipt)
    monkeypatch.setattr(app.collaboration, 'publish_receipt', fail)
    with pytest.raises(OSError):
        await watch(app)
    monkeypatch.setattr(app.collaboration, 'publish_receipt', publish)
    assert bool(app.db.execute("SELECT 1 FROM commands WHERE id='watch'").fetchone()) is (failure == 'after')
    retry = await watch(app)
    assert retry.get('duplicate', False) is (failure == 'after')
    assert app.db.execute("SELECT count(*) FROM commands WHERE id='watch'").fetchone()[0] == 1
    assert not app.runtime.inputs


async def test_deep_peer_chain_keeps_lineage_without_requiring_human_continue(app):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await send(app, 'ancestor', mode='notify')
    ancestor = app.collaboration.receipt('ancestor')
    ancestor.update(causalDepth=16, target={'sessionId': source['id']})
    app.collaboration.save('ancestor', ancestor)
    await generation(app, source, 'peer-loop', ['ancestor'])
    continued = await send(app, 'different-id-and-text', text='Continue the delegated work')
    assert continued['delivery'] == 'queued' and continued['causalDepth'] == 17
    assert continued['sourceInputIds'] == ['ancestor']
    # Information does not start another model turn and remains available.
    assert (await send(app, 'loop-note', mode='notify'))['delivery'] == 'notified'
    app._message(source, 'user', 'Continue with this new human direction', inputId='actual-human', inputOrigin='user')
    await generation(app, source, 'human-turn', ['actual-human', 'ancestor'])
    accepted = await send(app, 'after-human')
    assert accepted['causalDepth'] == 0


async def test_deep_report_still_returns_once_without_requiring_human_continue(app):
    source, target = app.state['sessions']
    await declared_result(app)
    request = app.collaboration.receipt('result-request')
    request['causalDepth'] = 40
    app.collaboration.save(request['requestId'], request)
    wait = await subscribe(app)
    await finish(app, target, 'result-request')
    identity = wait['result']['continuationId']
    returned = app.collaboration.receipt(identity)
    assert returned['causalDepth'] == 41
    assert returned['dependencySatisfied'] is True
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    await settled(app, identity, 'accepted')
    app.collaboration.enqueue_continuation(app.collaboration.receipt('result-request'))
    assert len([row for row in app.runtime.inputs if row[1] == identity]) == 1


async def test_deep_peer_chain_can_commission_a_task_without_resetting_provenance(app):
    from test_coordination_generation import source_plan
    source, _ = app.state['sessions']
    source_plan(app, source)
    await send(app, 'ancestor', mode='notify')
    ancestor = app.collaboration.receipt('ancestor')
    ancestor.update(causalDepth=40, target={'sessionId': source['id']})
    app.collaboration.save('ancestor', ancestor)
    await generation(app, source, 'peer-followup', ['ancestor'])
    created = await agent_action(app, source, 'coordination.create',
        {'title': 'Delegated follow-up', 'text': 'Complete the remaining authorized correction'}, 'deep-create')
    await settled(app, 'deep-create', 'created')
    brief = await settled(app, created['initialInputId'], 'accepted')
    assert brief['causalDepth'] == 41
    assert brief['sourceInputIds'] == ['ancestor']


async def test_deep_peer_chain_can_watch_without_waking_the_target(app):
    source, target = app.state['sessions']
    await send(app, 'ancestor', mode='notify')
    ancestor = app.collaboration.receipt('ancestor')
    ancestor.update(causalDepth=40, target={'sessionId': source['id']})
    app.collaboration.save('ancestor', ancestor)
    await generation(app, source, 'peer-followup', ['ancestor'])
    before = copy.deepcopy(target['messages'])
    await watch(app)
    assert app.collaboration.receipt('watch')['causalDepth'] == 41
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'working'})
    result = app.collaboration.receipt('watch')
    assert result['watch']['status'] == 'changed'
    assert app.collaboration.receipt(result['subscription']['continuationId'])['causalDepth'] == 42
    assert target['messages'] == before and not app.runtime.inputs


@pytest.mark.parametrize('change', ['none', 'stop', 'task', 'revision-limit'])
async def test_restart_recovers_only_valid_waits_held_by_old_hop_limit(app, monkeypatch, change):
    source, target = app.state['sessions']
    source['task'] = {'id': 'original-task', 'revision': 1, 'continuationEpoch': 1, 'status': 'active'}
    await declared_result(app)
    wait = await subscribe(app)
    enqueue = app.collaboration.enqueue_continuation
    monkeypatch.setattr(app.collaboration, 'enqueue_continuation', lambda request: None)
    await finish(app, target, 'result-request')
    request = app.collaboration.receipt('result-request')
    request['causalDepth'] = 40
    request['subscription'].update(status='limit_reached', open=False,
        detail='The causal peer-input chain reached 16 hops; its report remains inspectable.')
    if change == 'stop':
        source['interruptionRevision'] = 1
    elif change == 'task':
        source['task'] = {'id': 'another-task', 'revision': 1, 'continuationEpoch': 1, 'status': 'active'}
    elif change == 'revision-limit':
        request['subscription']['detail'] = 'Automatic outcome notification limit reached; inspect the retained result.'
    app.collaboration.save('result-request', request)
    app._publish()
    monkeypatch.setattr(app.collaboration, 'enqueue_continuation', enqueue)
    await app.close()
    reopened = AppService(app.data_dir, Runtime(), workspace=app.default_workspace)
    reopened.runtime.app = reopened
    try:
        reopened._session(source['id'])['status'] = 'idle'
        reopened.collaboration.start()
        identity = wait['result']['continuationId']
        if change == 'none':
            returned = await settled(reopened, identity, 'accepted')
            assert returned['causalDepth'] == 41 and returned['dependencySatisfied']
            reopened.collaboration.start()
            await reopened.collaboration.drain(source['id'])
            assert [row[1] for row in reopened.runtime.inputs] == [identity]
        else:
            await reopened.collaboration.drain(source['id'])
            assert not reopened.runtime.inputs
            if change == 'revision-limit':
                assert reopened.collaboration.receipt('result-request')['subscription']['status'] == 'limit_reached'
            elif change == 'stop':
                assert reopened.collaboration.receipt('result-request')['subscription']['status'] == 'suppressed'
            else:
                assert reopened.collaboration.receipt(identity)['delivery'] == 'suppressed'
        assert reopened.collaboration.receipt('result-request')['response']['qualified'] is True
    finally:
        await reopened.close()


@pytest.mark.parametrize('patch', [
    {'disposition': 'background_jobs_running'},
    {'event': 'generation.detached'},
    {'nativeTerminal': None},
    {'input_ids': ['unrelated'], 'active_job_ids': ['job']},
])
async def test_background_activity_never_substitutes_for_exact_root_terminal_evidence(app, patch):
    _, target = app.state['sessions']
    await declared_result(app)
    await subscribe(app)
    await finish(app, target, 'result-request', **patch)
    request = app.collaboration.receipt('result-request')
    assert request['response']['status'] == 'rejected' and not request['response']['qualified']
    notification = app.collaboration.receipt(request['subscription']['continuationId'])
    assert notification['dependencySatisfied'] is False


async def test_new_report_supersedes_unsubmitted_success_without_erasing_either_revision(app):
    source, target = app.state['sessions']
    await declared_result(app)
    wait = await subscribe(app)
    await finish(app, target, 'result-request')
    old_id = wait['result']['continuationId']
    await generation(app, target, 'later-correction', ['followup'])
    await revised(app, 'result-request', 1, kind='conflict', outcome='conflict')
    source['status'] = 'idle'
    await app.collaboration.drain(source['id'])
    assert app.collaboration.receipt(old_id)['delivery'] == 'suppressed'
    assert 'superseded' in app.collaboration.receipt(old_id)['detail']
    request = app.collaboration.receipt('result-request')
    newer_id = app.collaboration.continuation_identity(request['subscription'], 2)
    assert app.collaboration.receipt(newer_id)['delivery'] == 'accepted'
    assert not app.collaboration.receipt(newer_id)['dependencySatisfied']
    assert app.collaboration.receipt('declare-result-request')['result']['qualified']


@pytest.mark.parametrize('change', ['progress', 'pause-resume', 'new-objective'])
async def test_later_report_keeps_original_request_after_progress_only(app, change):
    source, target = app.state['sessions']
    target['task'] = {'id': 'objective', 'revision': 1, 'continuationEpoch': 1, 'status': 'active'}
    await declared_result(app, kind='defer', outcome='deferred')
    await finish(app, target, 'result-request')
    await generation(app, target, 'later-report', ['new-input'])
    target['task']['revision'] = 5
    if change != 'progress':
        target['task']['continuationEpoch'] = 5
    if change == 'progress':
        report = await revised(app, 'result-request', 1)
        assert report['result']['responseRevision'] == 2
    else:
        with pytest.raises(AppError, match='original request boundary'):
            await revised(app, 'result-request', 1)
        assert app.collaboration.receipt('result-request')['responseRevision'] == 1
