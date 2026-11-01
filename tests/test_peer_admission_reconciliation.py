"""Late exact native evidence settles peer admission without another input."""
import asyncio
import copy
import json

import pytest

from amplifier_web.runtime import RuntimeOperationPending
from test_collaborative_workspaces import app, generation, send, settled, until
from test_runtime_event_batches import reader


async def unknown_request(app, identity='late', mode='auto'):
    _, target = app.state['sessions']
    await generation(app, target, 'original-generation', ['original-input'])
    async def lost(*args, **kwargs):
        raise RuntimeOperationPending('control')
    if mode == 'queue': target['status'] = 'idle'
    app.runtime.collaboration_input = lost
    if mode == 'steer':
        app.runtime.collaboration_steer = lost
    await send(app, identity, mode=mode)
    await settled(app, identity, 'unknown')
    return target


@pytest.mark.parametrize('mode', ['auto', 'queue', 'steer'])
@pytest.mark.parametrize('outcome', ['accepted', 'not_sent', 'uncertain'])
async def test_late_reply_reconciles_exact_saved_request_without_replay(app, mode, outcome):
    target = await unknown_request(app, mode=mode)
    before = app.collaboration.receipt('late')
    result = {'accepted': True, 'inputId': 'late',
              'mode': 'queue' if mode == 'queue' else 'steer', 'generationId': 'original-generation'}
    if outcome != 'accepted':
        result = {'accepted': False, 'reason': 'Exact request no longer submitting.'}
        if outcome == 'not_sent': result['effect'] = 'none'
    evidence = {'sessionId': target['id'], 'inputId': 'late', 'interruptionRevision': 0,
                'targetGenerationId': before.get('targetGenerationId'), 'peerAdmission': result}
    await app.on_runtime_event('runtime.delivery', evidence)
    value = app.collaboration.receipt('late')
    assert value['delivery'] == {'uncertain': 'unknown'}.get(outcome, outcome)
    assert value['inputId'] == before['inputId'] and value.get('admissionOrder') == before.get('admissionOrder')
    if outcome == 'accepted':
        if mode != 'steer':
            assert value['terminalOutcome']['status'] == 'reconciled'
        assert value.get('response') is None  # Admission is not a qualified result.
    if outcome == 'not_sent':
        assert value['terminalOutcome']['outcome'] == 'failed'
        assert value['admission']['effect'] == 'none'
    await app.on_runtime_event('runtime.delivery', evidence)
    assert app.collaboration.receipt('late') == value
    await generation(app, target, 'later-generation', ['fresh-input'])
    await app.collaboration.drain(target['id'])
    assert app.runtime.inputs == []


@pytest.mark.parametrize('mismatch', ['input', 'target', 'stop', 'epoch', 'generation', 'route'])
async def test_late_reply_cannot_borrow_identity_or_new_authority(app, mismatch):
    target = await unknown_request(app)
    result = {'accepted': True, 'inputId': 'late', 'mode': 'steer', 'generationId': 'original-generation'}
    event = {'sessionId': target['id'], 'inputId': 'late', 'interruptionRevision': 0, 'peerAdmission': result}
    if mismatch == 'input': result['inputId'] = 'other'
    elif mismatch == 'target': event['sessionId'] = app.state['sessions'][0]['id']
    elif mismatch == 'stop': target['interruptionRevision'] = 1
    elif mismatch == 'epoch': event['interruptionRevision'] = 1
    elif mismatch == 'generation': result['generationId'] = 'other-generation'
    elif mismatch == 'route': result.pop('mode')
    before = copy.deepcopy(app.collaboration.receipt('late'))
    await app.on_runtime_event('runtime.delivery', event)
    assert app.collaboration.receipt('late') == before
    assert app.runtime.inputs == []


@pytest.mark.parametrize('phase', ['accepted', 'applied', 'held'])
async def test_delayed_native_event_settles_unknown_not_just_pending_transport(app, phase):
    target = await unknown_request(app)
    await app.on_runtime_event('runtime.steering', {'sessionId': target['id'], 'event': f'steering.{phase}',
        'input_id': 'late', 'target_generation_id': 'original-generation'})
    value = app.collaboration.receipt('late')
    assert value['delivery'] == phase and value['targetGenerationId'] == 'original-generation'
    assert value['terminalOutcome']['status'] == 'reconciled'
    # The delayed transport cannot downgrade already observed application/hold.
    await app.on_runtime_event('runtime.delivery', {'sessionId': target['id'], 'inputId': 'late',
        'interruptionRevision': 0, 'peerAdmission': {'accepted': False, 'effect': 'none'}})
    assert app.collaboration.receipt('late') == value
    assert app.runtime.inputs == []


@pytest.mark.parametrize('change', ['stop', 'generation'])
async def test_delayed_first_native_event_cannot_bind_after_authority_changed(app, change):
    target = await unknown_request(app)
    if change == 'stop': target['interruptionRevision'] = 1
    else: await generation(app, target, 'replacement-generation', ['fresh'])
    before = copy.deepcopy(app.collaboration.receipt('late'))
    await app.on_runtime_event('runtime.steering', {'sessionId': target['id'], 'event': 'steering.applied',
        'input_id': 'late', 'target_generation_id': 'original-generation'})
    assert app.collaboration.receipt('late') == before


@pytest.mark.parametrize('operation', ['coordination.submit', 'coordination.deliver', 'coordination.steer'])
@pytest.mark.parametrize('outcome', ['accepted', 'not_sent', 'wrong-input', 'error', 'replaced-worker'])
async def test_actual_transport_retains_peer_correlation_after_reply_timeout(monkeypatch, operation, outcome):
    import amplifier_web.runtime as module
    manager, row, task, observed = await reader([], eof=False)
    row['process'].returncode = None
    row['inflight'] = set()
    row['start_session'] = {'interruptionRevision': 3}
    writes = []
    async def write(owner, data): writes.append(data)
    monkeypatch.setattr(manager, '_write', write)
    args = {'operation': operation, 'arguments': {'inputId': 'peer', 'targetGenerationId': 'generation'}}
    pending = await manager._admit('root', 'control', args)
    wait_for = asyncio.wait_for
    async def timeout_reply(awaitable, timeout):
        return await wait_for(awaitable, .001 if awaitable is pending[2] else timeout)
    monkeypatch.setattr(module.asyncio, 'wait_for', timeout_reply)
    with pytest.raises(RuntimeOperationPending):
        await manager._reply(*pending, op='control', args=args)
    assert pending[1] not in row['pending']
    result = {'accepted': True, 'inputId': 'peer', 'mode': 'steer', 'generationId': 'generation'}
    if outcome == 'not_sent': result = {'accepted': False, 'effect': 'none', 'reason': 'No admission.'}
    elif outcome == 'wrong-input': result['inputId'] = 'unrelated'
    elif outcome == 'replaced-worker': manager.workers['root'] = {}
    frame = {'op': 'reply', 'id': pending[1], 'result': result}
    if outcome == 'error': frame['error'] = 'An uncertain worker exception'
    row['process'].returncode = 0
    row['process'].stdout.feed_data(json.dumps(frame).encode()+b'\n')
    row['process'].stdout.feed_eof()
    await task
    deliveries = [p for k,p in observed if k == 'runtime.delivery']
    if outcome in {'accepted', 'not_sent'}:
        assert deliveries == [{'sessionId': 'root', 'inputId': 'peer', 'interruptionRevision': 3,
                               'targetGenerationId': 'generation', 'peerAdmission': result}]
    else:
        assert deliveries == []
    assert len(writes) == 1 and not row['sendInputs'] and not row['inflight']


@pytest.mark.parametrize('mode', ['auto', 'queue', 'steer'])
@pytest.mark.parametrize('accepted', [True, False])
@pytest.mark.parametrize('failure', [RuntimeOperationPending, asyncio.CancelledError])
async def test_late_reply_beats_timeout_receipt_commit(app, mode, accepted, failure):
    _, target = app.state['sessions']
    await generation(app, target, 'original-generation', ['original-input'])
    async def late(snapshot, args, guard, emit):
        result = {'accepted': True, 'inputId': args['inputId'],
                  'mode': 'queue' if mode == 'queue' else 'steer', 'generationId': 'original-generation'}
        if not accepted: result = {'accepted': False, 'effect': 'none', 'reason': 'Never submitted.'}
        await emit('runtime.delivery', {'sessionId': target['id'], 'inputId': args['inputId'],
            'interruptionRevision': 0, 'targetGenerationId': args.get('targetGenerationId'),
            'peerAdmission': result})
        raise failure('control')
    if mode == 'queue': target['status'] = 'idle'
    app.runtime.collaboration_input = app.runtime.collaboration_steer = late
    if mode == 'steer' and failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            await send(app, 'racing', mode=mode)
    else:
        await send(app, 'racing', mode=mode)
    result = await settled(app, 'racing', 'accepted' if accepted else 'not_sent')
    assert result['admission']['accepted'] is accepted
    await until(lambda: target['id'] not in app.collaboration.draining)
    assert app.collaboration.receipt('racing') == result
    if not accepted: assert result['outcomeRevision'] == 1
    assert result['inputId'] == 'racing' and app.runtime.inputs == []


@pytest.mark.parametrize('accepted', [True, False])
async def test_late_admission_cannot_resolve_unknown_steering_application(app, accepted):
    target = await unknown_request(app)
    await app.on_runtime_event('runtime.steering', {'sessionId': target['id'], 'event': 'steering.unknown',
        'input_id': 'late', 'target_generation_id': 'original-generation'})
    before = copy.deepcopy(app.collaboration.receipt('late'))
    result = {'accepted': accepted, 'inputId': 'late', 'mode': 'steer',
              'generationId': 'original-generation', 'effect': 'none'}
    await app.on_runtime_event('runtime.delivery', {'sessionId': target['id'], 'inputId': 'late',
        'interruptionRevision': 0, 'peerAdmission': result})
    assert app.collaboration.receipt('late') == before


@pytest.mark.parametrize('phase', ['accepted', 'applied', 'held', 'unknown'])
async def test_child_steering_cannot_supply_root_admission_evidence(phase):
    manager, row, task, observed = await reader([], eof=False)
    row['process'].stdout.feed_data(json.dumps({'type': f'steering.{phase}', 'session_id': 'child',
        'root_session_id': 'root', 'input_id': 'root-peer', 'target_generation_id': 'root-generation'}).encode()+b'\n')
    row['process'].stdout.feed_eof()
    await task
    assert not any(k == 'runtime.steering' for k, _ in observed)


@pytest.mark.parametrize('mode', ['auto', 'steer'])
async def test_initial_reply_preserves_unknown_native_application(app, mode):
    _, target = app.state['sessions']
    await generation(app, target, 'original-generation', ['original-input'])
    async def admit(snapshot, args, guard, emit):
        await emit('runtime.steering', {'sessionId': target['id'], 'event': 'steering.unknown',
            'input_id': args['inputId'], 'target_generation_id': 'original-generation'})
        return {'accepted': True, 'inputId': args['inputId'], 'mode': 'steer',
                'generationId': 'original-generation'}
    app.runtime.collaboration_input = app.runtime.collaboration_steer = admit
    await send(app, 'unknown-application', mode=mode)
    await until(lambda: target['id'] not in app.collaboration.draining)
    value = await settled(app, 'unknown-application', 'unknown')
    assert value['steering']['event'] == 'steering.unknown'
    assert app.runtime.inputs == []


@pytest.mark.parametrize('accepted', [True, False])
async def test_reply_evidence_survives_cancellation_before_caller_consumes_it(monkeypatch, accepted):
    manager, row, reader_task, observed = await reader([], eof=False)
    row['process'].returncode = None
    row['inflight'] = set()
    writes = []
    async def write(owner, data): writes.append(data)
    monkeypatch.setattr(manager, '_write', write)
    args = {'operation': 'coordination.deliver', 'arguments': {'inputId': 'peer'}}
    pending = await manager._admit('root', 'control', args)
    pending[2].add_done_callback(lambda _: waiter.cancel())
    waiter = asyncio.create_task(manager._reply(*pending, op='control', args=args))
    await asyncio.sleep(0)  # The caller is waiting before the worker replies.
    result = {'accepted': True, 'inputId': 'peer', 'mode': 'queue'} if accepted else {
        'accepted': False, 'effect': 'none', 'reason': 'Never submitted.'}
    row['process'].stdout.feed_data(json.dumps({'op': 'reply', 'id': pending[1], 'result': result}).encode()+b'\n')
    row['process'].stdout.feed_eof()
    row['process'].returncode = 0
    await reader_task
    with pytest.raises(asyncio.CancelledError): await waiter
    evidence = [p for k,p in observed if k == 'runtime.delivery']
    assert len(evidence) == 1 and evidence[0]['peerAdmission'] == result
    assert evidence[0]['inputId'] == 'peer' and len(writes) == 1


@pytest.mark.parametrize('arguments', [None, {}, {'inputId': ''}, {'inputId': []}])
async def test_invalid_peer_identity_cannot_leave_an_inflight_fence(monkeypatch, arguments):
    manager, row, task, observed = await reader([], eof=False)
    row['process'].returncode = None
    row['inflight'] = set()
    writes = []
    async def write(owner, data): writes.append(data)
    monkeypatch.setattr(manager, '_write', write)
    with pytest.raises(ValueError, match='exact input identity'):
        await manager._admit('root', 'control', {'operation': 'coordination.deliver', 'arguments': arguments})
    assert not writes and not row['pending'] and not row['inflight'] and not row.get('sendInputs')
    row['process'].returncode = 0
    row['process'].stdout.feed_eof()
    await task


@pytest.mark.parametrize('waiting', [True, False])
async def test_normal_no_effect_reply_preserves_existing_wait_policy(app, waiting):
    _, target = app.state['sessions']
    await generation(app, target, 'original-generation', ['original-input'])
    await until(lambda: not app.tasks)  # Finish the setup generation's scheduled inbox reconsideration.
    calls = []
    async def no_effect(snapshot, args, guard, emit):
        calls.append(args['inputId'])
        result = {'accepted': False, 'effect': 'none', 'waitingForBoundary': waiting}
        await emit('runtime.delivery', {'sessionId': target['id'], 'inputId': args['inputId'],
            'interruptionRevision': 0, 'peerAdmission': result})
        return result
    app.runtime.collaboration_input = no_effect
    await send(app, 'wait', mode='auto')
    await until(lambda: calls and target['id'] not in app.collaboration.draining)
    result = app.collaboration.receipt('wait')
    assert result['delivery'] == ('queued' if waiting else 'suppressed')
    assert calls == ['wait'] and not app.runtime.inputs


async def test_prior_safe_wait_is_not_absence_evidence_for_the_next_attempt(app):
    _, target = app.state['sessions']
    await generation(app, target, 'original-generation', ['original-input'])
    await until(lambda: not app.tasks)  # Finish the setup generation's scheduled inbox reconsideration.
    calls = []
    async def admit(snapshot, args, guard, emit):
        calls.append(args['inputId'])
        if len(calls) == 1:
            result = {'accepted': False, 'effect': 'none', 'waitingForBoundary': True}
            await emit('runtime.delivery', {'sessionId': target['id'], 'inputId': args['inputId'],
                'interruptionRevision': 0, 'peerAdmission': result})
            return result
        raise RuntimeOperationPending('control')
    app.runtime.collaboration_input = admit
    await send(app, 'wait-then-timeout', mode='auto')
    await until(lambda: calls and target['id'] not in app.collaboration.draining)
    first = app.collaboration.receipt('wait-then-timeout')
    assert first['delivery'] == 'queued' and first['admission']['effect'] == 'none'
    await app.on_runtime_event('runtime.generation', {'sessionId': target['id'], 'rootSessionId': target['id'],
        'event': 'generation.finished', 'generation_id': 'original-generation',
        'input_ids': ['original-input'], 'disposition': 'manager_turn_finished'})
    value = await settled(app, 'wait-then-timeout', 'unknown')
    assert value['admissionOrder'] > first['admissionOrder']
    assert value['admission'].get('effect') != 'none' and calls == ['wait-then-timeout'] * 2
    await app.on_runtime_event('runtime.delivery', {'sessionId': target['id'], 'inputId': 'wait-then-timeout',
        'interruptionRevision': 0, 'peerAdmission': {'accepted': True, 'inputId': 'wait-then-timeout', 'mode': 'queue'}})
    assert app.collaboration.receipt('wait-then-timeout')['delivery'] == 'accepted'
    assert calls == ['wait-then-timeout'] * 2
