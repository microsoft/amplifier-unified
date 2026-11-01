"""Qualified update holding uses original input IDs and never replays admission."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.updates import UpdateManager
from amplifier_web import update_drain as drain
from test_service import Runtime


@pytest.fixture
async def app(tmp_path):
    runtime = Runtime()
    service = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    await service.dispatch('session.create', {'title': 'Continuous work'})
    service.update_manager = UpdateManager(service)
    service.state['updates']['drain'] = {
        'attemptId': 'a' * 32, 'target': {'attemptId': 'a' * 32, 'revision': 'b' * 40, 'generation': None},
        'phase': 'draining', 'hostInstanceId': service.instance_id, 'workers': {}, 'messages': []}
    yield service
    await service.close()


def healthy(app):
    app.state['updates']['applicationResult'] = {**app.state['updates']['drain']['target']}


async def send(app, identity, **kwargs):
    return await app.dispatch('conversation.send', {'sessionId': app._session()['id'],
        'text': identity, 'allowNextTurn': True, **kwargs}, command_id=identity, include_state=False)


async def test_many_saved_messages_are_acknowledged_and_released_once_in_order(app):
    for identity in ('first', 'second', 'third'):
        result = await send(app, identity)
        assert result['delivery'] == 'queued_for_update', result
    assert app.runtime.sent == []
    assert not app.update_manager.busy()
    healthy(app)
    await drain.finish(app.update_manager, 'installed')
    await drain.finish(app.update_manager, 'installed')
    assert [row[2] for row in app.runtime.sent] == ['first', 'second', 'third']
    assert all(m['delivery']['status'] == 'accepted' for m in app._session()['messages'] if m.get('role') == 'user')
    assert app.state['updates']['drain']['phase'] == 'released'


async def test_crash_leaves_only_claimed_input_uncertain_and_rest_queued(app):
    await send(app, 'claimed')
    await send(app, 'unattempted')
    healthy(app)
    entered = asyncio.Event()
    async def crash(*args, **kwargs):
        entered.set()
        raise asyncio.CancelledError()
    app._send = crash
    with pytest.raises(asyncio.CancelledError):
        await drain.finish(app.update_manager, 'installed')
    assert entered.is_set()
    assert [m['delivery']['status'] for m in app._session()['messages']] == ['sending', 'queued_for_update']
    # The ordinary restart path treats sending as uncertain, not as retryable.
    app._session()['messages'][0]['delivery']['status'] = 'unknown'
    del app._send
    await drain.finish(app.update_manager, 'installed')
    assert [row[2] for row in app.runtime.sent] == ['unattempted']


async def test_stop_after_saved_input_holds_without_resuming_or_sending(app):
    await send(app, 'before-stop')
    session = app._session()
    session['interruptionRevision'] = 1
    session['status'] = 'stopped'
    healthy(app)
    await drain.finish(app.update_manager, 'installed')
    assert not app.runtime.sent
    assert session['status'] == 'stopped'
    assert session['messages'][0]['delivery']['status'] == 'held'
    receipt = json.loads(app.db.execute("SELECT receipt FROM commands WHERE id='before-stop'").fetchone()[0])
    assert receipt['delivery'] == 'held'


async def test_no_effect_defer_race_after_release_keeps_original_input(app):
    await send(app, 'racing-input')
    app.state['updates']['drain']['phase'] = 'released'
    app._session()['messages'][0]['delivery'] = {'status': 'sending'}
    await drain.defer_send(app, app._session()['id'], 'racing-input')
    assert [row[2] for row in app.runtime.sent] == ['racing-input']
    assert app._session()['messages'][0]['delivery']['status'] == 'accepted'


async def test_failed_or_unknown_replacement_cannot_release_held_inputs(app):
    await send(app, 'waiting')
    app.state['updates']['phase'] = 'activating'
    app.state['updates']['pendingReplacement'] = {'phase': 'replacing'}
    await drain.finish(app.update_manager, 'withdrawn')
    assert not app.runtime.sent
    assert app._session()['messages'][0]['delivery']['status'] == 'queued_for_update'
    healthy(app)
    app.state['updates']['applicationResult']['revision'] = 'c' * 40
    await drain.finish(app.update_manager, 'installed')
    assert not app.runtime.sent


async def test_known_pre_effect_withdrawal_releases_saved_messages(app):
    await send(app, 'retained')
    app.state['updates']['phase'] = 'error'
    await drain.finish(app.update_manager, 'withdrawn')
    assert [row[2] for row in app.runtime.sent] == ['retained']


async def test_explicit_steering_is_not_converted_to_an_ordinary_saved_message(app):
    session = app._session()
    session['status'] = 'working'
    session['collaborationGeneration'] = {'id': 'active', 'terminal': False}
    app.runtime.steer = AsyncMock(return_value={'accepted': False, 'disposition': 'held', 'reason': 'Steering window ended'})
    result = await app.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'exact steering',
        'expectedGenerationId': 'active', 'allowNextTurn': False}, command_id='strict', include_state=False)
    assert result['steering']['disposition'] == 'held'
    assert app.state['updates']['drain']['messages'] == []
    assert not app.runtime.sent


async def test_update_gate_does_not_reclassify_a_retry_as_unattempted(app):
    session = app._session()
    app._message(session, 'user', 'old', 'chat', inputId='old', delivery={'status': 'unknown'})
    with pytest.raises(AppError, match='finishing existing work'):
        await app.dispatch('conversation.retry', {'sessionId': session['id'], 'inputId': 'old',
            'confirmUncertain': True}, command_id='retry', include_state=False)
    assert session['messages'][0]['delivery']['status'] == 'unknown'
    assert app.state['updates']['drain']['messages'] == []


async def test_old_pre_replacement_hold_is_withdrawn_without_same_tick_activation(app):
    sid = app._session()['id']
    app.state['updates']['drain']['workers'][sid] = {
        'sessionId': sid, 'owner': 'original', 'boundaryId': 'boundary',
        'binding': drain.binding(app._session()), 'ready': False}
    app.runtime.workers = {sid: {'runtimeOwnerId': 'original'}}
    app.runtime.handoff_boundary = AsyncMock(return_value={'cancelled': True})
    await send(app, 'retained-before-withdrawal')
    assert not await drain.prepare(app.update_manager, app.state['updates']['drain']['target'])
    app.runtime.handoff_boundary.assert_awaited_once_with(sid, 'boundary', cancel=True)
    assert [row[2] for row in app.runtime.sent] == ['retained-before-withdrawal']
    assert app.state['updates']['drain']['phase'] == 'released'
    assert not app.update_manager.busy()


async def test_unconfirmed_old_boundary_withdrawal_preserves_held_input(app):
    sid = app._session()['id']
    app.state['updates']['drain']['workers'][sid] = {
        'sessionId': sid, 'owner': 'original', 'boundaryId': 'boundary',
        'binding': drain.binding(app._session()), 'ready': False}
    app.runtime.workers = {sid: {'runtimeOwnerId': 'original'}}
    app.runtime.handoff_boundary = AsyncMock(side_effect=TimeoutError('not confirmed'))
    await send(app, 'still-retained')
    with pytest.raises(TimeoutError):
        await drain.prepare(app.update_manager, app.state['updates']['drain']['target'])
    assert not app.runtime.sent
    assert app.state['updates']['drain']['phase'] == 'draining'
    assert app._session()['messages'][0]['delivery']['status'] == 'queued_for_update'


async def test_new_message_during_release_cannot_overtake_saved_inputs(app):
    await send(app, 'first')
    await send(app, 'second')
    healthy(app)
    native = app.runtime.send
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def pending(session, text, identity, emit):
        if identity == 'first':
            entered.set()
            await proceed.wait()
        await native(session, text, identity, emit)
    app.runtime.send = pending
    release = asyncio.create_task(drain.finish(app.update_manager, 'installed'))
    await entered.wait()
    later = await send(app, 'third-during-release')
    assert later['delivery'] == 'queued_for_update'
    assert not app.runtime.sent
    proceed.set()
    await release
    assert [row[2] for row in app.runtime.sent] == ['first', 'second', 'third-during-release']


@pytest.mark.parametrize('changed', ['none', 'stop', 'paused', 'objective', 'exhausted', 'not-yielded'])
async def test_only_exact_yielded_active_goal_gets_one_retained_continuation(app, changed):
    session = app._session()
    sid = session['id']
    task = {'id': 'task', 'revision': 1, 'continuationEpoch': 1, 'status': 'active',
        'objective': 'Finish the existing work', 'questionIds': [], 'operationIds': []}
    session['task'] = copy.deepcopy(task)
    goal = {'task_id': 'task', 'task_revision': 1, 'condition': task['objective'],
            'cap': 5, 'turns_used': 3, 'continuations': 2}
    row = {'sessionId': sid, 'owner': 'original', 'boundaryId': 'boundary', 'ready': True,
           'binding': drain.binding(session), 'task': task, 'yieldedGoal': goal}
    app.state['updates']['drain']['workers'][sid] = row
    if changed == 'stop': session['interruptionRevision'] = 1
    if changed == 'paused': session['task']['status'] = 'paused'
    if changed == 'objective': session['task'].update(continuationEpoch=2, objective='Different work')
    if changed == 'exhausted': goal['turns_used'] = 5
    if changed == 'not-yielded': row.pop('yieldedGoal')
    async def admit(session, args, guard, emit):
        assert guard() is None
        return {'accepted': True}
    app.runtime.collaboration_input = AsyncMock(side_effect=admit)
    healthy(app)
    await drain.finish(app.update_manager, 'installed')
    for _ in range(20):
        if app.runtime.collaboration_input.await_count or changed != 'none': break
        await asyncio.sleep(.01)
    await drain.finish(app.update_manager, 'installed')
    assert app.runtime.collaboration_input.await_count == (1 if changed == 'none' else 0)
    assert goal['turns_used'] == (5 if changed == 'exhausted' else 3)
    if changed == 'none':
        receipt = app.db.execute("SELECT receipt FROM commands WHERE json_extract(receipt,'$.commandAction')='updates.app'").fetchone()
        value = json.loads(receipt[0])
        assert value['subscription']['lastClaimedRevision'] == 1
        assert value['updateYield']['taskBinding']['taskContinuationEpoch'] == 1


async def test_restart_recovers_remaining_unattempted_messages_without_replaying_claim(app):
    await send(app, 'uncertain-first')
    await send(app, 'saved-second')
    healthy(app)
    async def crash(*args, **kwargs): raise asyncio.CancelledError()
    app._send = crash
    with pytest.raises(asyncio.CancelledError): await drain.finish(app.update_manager, 'installed')
    sid, home, workspace = app._session()['id'], app.data_dir, app._session()['workspace']
    await app.close()
    restored = AppService(home, Runtime(), workspace=workspace)
    restored.update_manager = UpdateManager(restored)
    try:
        session = restored._session(sid)
        assert [m['delivery']['status'] for m in session['messages']] == ['unknown', 'queued_for_update']
        healthy(restored)
        await drain.finish(restored.update_manager, 'installed')
        assert [r[2] for r in restored.runtime.sent] == ['saved-second']
        assert session['messages'][0]['delivery']['status'] == 'unknown'
    finally:
        await restored.close()


async def test_new_native_generation_supersedes_update_only_continuation(app):
    session = app._session()
    session['task'] = {'id': 't', 'revision': 2, 'continuationEpoch': 1, 'status': 'active'}
    session['collaborationGeneration'] = {'id': 'fresh-input-generation', 'terminal': False}
    proof = {'sessionId': session['id'], 'binding': drain.binding(session),
        'generationId': 'saved-update-generation',
        'taskBinding': {'taskId': 't', 'taskRevision': 1, 'taskContinuationEpoch': 1}}
    assert 'New input' in drain.continuation_guard(app, {'updateYield': proof})


@pytest.mark.parametrize('delivery', ['sending', 'accepted', 'unknown'])
async def test_fresh_saved_input_before_native_event_suppresses_update_continuation(app, delivery):
    session = app._session()
    task = {'id': 'task', 'revision': 1, 'continuationEpoch': 1, 'status': 'active'}
    session['task'] = task
    session['collaborationGeneration'] = {'id': 'old-generation', 'terminal': True}
    app._message(session, 'user', 'original', inputId='original', delivery={'status': 'accepted'})
    row = {'sessionId': session['id'], 'owner': 'original', 'boundaryId': 'boundary', 'ready': True,
        'binding': drain.binding(session), 'task': task, 'generationId': 'old-generation',
        'lastInputId': 'original', 'yieldedGoal': {'task_id': 'task', 'cap': 5, 'turns_used': 2}}
    current = app.state['updates']['drain']; current['result'] = 'installed'
    drain.enqueue_goal(app, current, row)
    request = json.loads(app.db.execute("SELECT receipt FROM commands WHERE json_extract(receipt,'$.commandAction')='updates.app'").fetchone()[0])
    assert drain.continuation_guard(app, request) is None  # Its own peer message is not fresh input.
    app._message(session, 'user', 'fresh instruction', inputId='fresh', delivery={'status': delivery})
    assert session['collaborationGeneration']['id'] == 'old-generation'
    assert 'New input' in drain.continuation_guard(app, request)
    current['phase'] = 'released'
    await app.collaboration.drain(session['id'])
    assert not app.runtime.sent
    saved = app.db.execute("SELECT receipt FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')=?", (request['requestId'],)).fetchone()
    assert json.loads(saved[0])['delivery'] == 'suppressed'
