"""Same-receipt boundary delivery; explicit queue intent remains idle-only."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from amplifier_web.collaboration_input import deliver
from test_collaborative_workspaces import app, agent_action, generation, send, settled, until
from test_collaboration_input import boundary, input_type


async def test_default_auto_skips_idle_only_rows_while_manager_busy(app):
    source, target = app.state['sessions']
    await generation(app, target, 'busy-manager', ['manager-input'])
    for index in range(70):
        await send(app, f'idle-only-{index}')
    await send(app, 'note', mode='notify')
    automatic = await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'A timely dependency update'}, 'automatic')
    assert automatic['mode'] == 'auto'
    await settled(app, 'automatic', 'accepted')
    assert [row[1] for row in app.runtime.inputs] == ['automatic']
    assert app.collaboration.receipt('idle-only-0')['delivery'] == 'queued'
    assert app.collaboration.receipt('note')['delivery'] == 'notified'
    assert app.collaboration.receipt('automatic')['inputId'] == 'automatic'


async def test_terminal_event_before_no_effect_ack_reconsiders_same_receipt(app):
    _, target = app.state['sessions']
    await generation(app, target, 'finishing', ['original'])
    original = app.runtime.collaboration_input
    attempts = []
    async def admit(snapshot, args, guard, emit):
        attempts.append(args['inputId'])
        if len(attempts) == 1:
            # The only lifecycle event arrives while the durable receipt is
            # submitting. A later positively-unadmitted ack must not lose it.
            await emit('runtime.generation', {'sessionId': target['id'], 'rootSessionId': target['id'],
                'event': 'generation.finished', 'generation_id': 'finishing',
                'input_ids': ['original'], 'disposition': 'manager_turn_finished'})
            await asyncio.sleep(0)
            return {'accepted': False, 'effect': 'none', 'waitingForBoundary': True}
        return await original(snapshot, args, guard, emit)
    app.runtime.collaboration_input = admit
    await send(app, 'racing', mode='auto')
    await settled(app, 'racing', 'accepted')
    assert attempts == ['racing', 'racing']
    assert [row[1] for row in app.runtime.inputs] == ['racing']


@pytest.mark.parametrize('phase', ['accepted', 'applied', 'held', 'unknown'])
async def test_native_disposition_beats_lost_ack_and_never_retargets(app, phase):
    _, target = app.state['sessions']
    await generation(app, target, 'exact-native', ['original'])
    calls = []
    async def admit(snapshot, args, guard, emit):
        calls.append(args['inputId'])
        await emit('runtime.steering', {'sessionId': target['id'], 'event': f'steering.{phase}',
            'input_id': args['inputId'], 'target_generation_id': 'exact-native'})
        raise asyncio.CancelledError()
    app.runtime.collaboration_input = admit
    await send(app, 'lost-ack', mode='auto')
    value = await settled(app, 'lost-ack', phase)
    assert value['targetGenerationId'] == 'exact-native'
    await generation(app, target, 'later-generation', ['later-human'])
    await app.collaboration.drain(target['id'])
    assert calls == ['lost-ack']


@pytest.mark.parametrize('change', ['none', 'closed-boundary', 'unsupported', 'host-lag', 'stop', 'task', 'completed', 'blocked'])
async def test_native_auto_selects_supported_exact_boundary(boundary, input_type, change):
    b = boundary
    b.runtime.stop_requested = b.runtime.steering_closed = False
    b.permission['currentGenerationId'] = 'generation-1'
    if change == 'closed-boundary': b.runtime.steering_closed = True
    elif change == 'unsupported': b.capabilities.clear()
    elif change == 'host-lag': b.permission['currentGenerationId'] = 'old-generation'
    elif change == 'stop': b.runtime.stop_requested = True
    elif change == 'task': b.task['revision'] += 1
    elif change == 'completed': b.permission['completedTaskInput'] = True
    elif change == 'blocked':
        b.permission['blockedTaskInput'] = True
        b.controls.tasks.peer_input_allowed = AsyncMock(return_value=True)
    result = await deliver(b.controls, b.runtime, b.args, b.activation, b.authorize)
    assert result['accepted'] is (change == 'none')
    assert b.capability['submit'].await_count == (change == 'none')
    b.runtime.submit.assert_not_awaited()
    if change != 'none':
        assert result['effect'] == 'none'
        assert result['waitingForBoundary'] is (change not in {'stop', 'task'})


async def test_idle_auto_uses_same_id_and_saved_envelope(boundary, input_type):
    b = boundary
    b.runtime.generation = None
    b.runtime.stop_requested = b.runtime.steering_closed = False
    b.controls.require_idle.side_effect = None
    result = await deliver(b.controls, b.runtime, b.args, b.activation, b.authorize)
    assert result['accepted'] and result['mode'] == 'queue'
    native = b.runtime.submit.call_args.args[0]
    assert native.id == 'peer-input' and 'Host-saved correction' in native.text
    b.capability['submit'].assert_not_awaited()


async def test_only_unadmitted_canonical_host_returns_upgrade(app):
    source, target = app.state['sessions']
    target['status'] = 'working'
    await send(app, 'request', returnNotification=True)
    request = app.collaboration.receipt('request')
    request.update(outcomeRevision=1, response={'status': 'sealed', 'kind': 'result', 'outcome': 'failed',
        'text': 'Retained outcome', 'qualified': False, 'outcomeRevision': 1})
    app.collaboration.save('request', request)
    app.collaboration.enqueue_continuation(request)
    identity = request['subscription']['continuationId']
    value = app.collaboration.receipt(identity)
    assert value['mode'] == 'auto'
    from amplifier_web.peer_attribution import SYSTEM_CAPTION
    assert app.peer_attribution.resolve(source, source['messages'])[-1]['attribution']['caption'] == SYSTEM_CAPTION
    # Reconstruct only the previous release's host queue policy; all canonical
    # claim/provenance/participant fields come from the real host producer.
    value['mode'] = 'queue'
    app.collaboration.save(identity, value)
    app.collaboration.upgrade_queued_returns()
    upgraded = app.collaboration.receipt(identity)
    assert upgraded['automaticDelivery'] and upgraded['inputId'] == identity
    assert app.collaboration.receipt('request')['mode'] == 'queue'
    assert 'automaticDelivery' not in app.collaboration.receipt('request')
    for field in ('admissionOrder', 'admittedAt', 'admission', 'steering'):
        value.pop('automaticDelivery', None)
        value[field] = 1
        app.collaboration.save(identity, value)
        app.collaboration.upgrade_queued_returns()
        assert 'automaticDelivery' not in app.collaboration.receipt(identity)
        value.pop(field)


async def test_no_effect_wait_is_not_a_polling_loop(app):
    _, target = app.state['sessions']
    await generation(app, target, 'busy', ['original'])
    call = app.runtime.collaboration_input = AsyncMock(return_value={
        'accepted': False, 'effect': 'none', 'waitingForBoundary': True})
    await send(app, 'unsupported', mode='auto')
    await until(lambda: call.await_count >= 1)
    await until(lambda: target['id'] not in app.collaboration.draining)
    before = call.await_count
    for _ in range(20):
        await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'working',
            'activityOnly': True, 'event': 'provider.progress'})
    await asyncio.sleep(0)
    assert call.await_count == before
    assert app.collaboration.receipt('unsupported')['delivery'] == 'queued'


async def test_busy_selection_work_does_not_grow_with_receipt_history(app):
    _, target = app.state['sessions']
    target['configurationBusy'] = True  # Retain the real saved inputs for selection.
    await send(app, 'idle-intent')
    await send(app, 'eligible', mode='auto')
    counts = []
    for start, end in ((0, 1000), (1000, 5000)):
        for index in range(start, end):
            app.collaboration.insert(f'history-{index}', str(index), {
                'commandAction': 'coordination.send', 'target': {'sessionId': target['id']},
                'senderSessionId': 'old-peer', 'delivery': 'accepted', 'mode': 'queue',
                'admissionOrder': index + 1, 'body': 'retained output ' * 128})
        steps = [0]
        def count():
            steps[0] += 1
            return 0
        app.db.set_progress_handler(count, 1)
        try:
            rows = app.collaboration.fair_rows(target['id'], active=True)
        finally:
            app.db.set_progress_handler(None, 0)
        assert [identity for identity, _ in rows] == ['eligible']
        assert len(rows) <= 64
        counts.append(steps[0])
    # Historical bodies and deliberate idle-only rows never enter this payload
    # page. B-tree depth may change slightly; history-linear work may not.
    assert counts[1] <= counts[0] + 100, counts
