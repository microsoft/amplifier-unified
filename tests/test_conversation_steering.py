"""User steering preserves a run, including ambiguous and late delivery."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.execution import ensure_turn
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppService, AppError
from test_service import Runtime


@pytest.fixture
async def active(tmp_path):
    runtime = Runtime()
    runtime.steer = AsyncMock(return_value={'accepted': True, 'disposition': 'queued'})
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    session['status'] = 'working'
    ensure_turn(session, 'original', 'Original task')
    await app.on_runtime_event('runtime.generation', {'sessionId': session['id'],
        'event': 'generation.started', 'generation_id': 'generation', 'initial_input_id': 'original'})
    yield app, runtime, session
    await app.close()


async def correction(app, **extra):
    return await app.dispatch('conversation.send', {'sessionId': app._session()['id'],
        'text': 'Please find a good pause point.', **extra}, command_id='correction')


async def event(app, kind, generation='generation'):
    await app.on_runtime_event('runtime.steering', {'sessionId': app._session()['id'],
        'event': kind, 'input_id': 'correction', 'target_generation_id': generation})


async def test_correction_keeps_turn_and_usage_and_receipt(active):
    app, runtime, session = active
    result = await correction(app, expectedGenerationId='generation')
    assert result['steering']['generationId'] == 'generation'
    assert result['steering']['disposition'] == 'queued'
    assert result['steering']['queuedAt'] >= session['messages'][-1]['createdAt']
    assert session['execution']['currentTurnId'] == 'original'
    assert [t['id'] for t in session['execution']['turns']] == ['original']
    assert session['status'] == 'working'
    assert runtime.sent == []
    runtime.steer.assert_awaited_once()
    await event(app, 'steering.applied')
    duplicate = await correction(app, expectedGenerationId='generation')
    assert duplicate['duplicate'] and duplicate['steering']['disposition'] == 'applied'
    assert duplicate['steering']['contextAt'] >= result['steering']['queuedAt']
    assert runtime.steer.await_count == 1
    assert app.browser_state()['sessions'][0]['messages'][-1]['steering']['disposition'] == 'applied'
    telemetry = ExecutionEvents(session['id'], lambda e: None)
    telemetry.lifecycle({'type': 'input.delivered', 'input_id': 'original'})
    telemetry.lifecycle({'type': 'input.delivered', 'input_id': 'correction', 'target_generation_id': 'generation'})
    assert telemetry.turn_id == 'original'


@pytest.mark.parametrize('disposition', ['applied', 'held', 'unknown'])
async def test_late_ack_cannot_erase_runtime_disposition(active, disposition):
    app, runtime, session = active
    async def steer(*args):
        await event(app, 'steering.' + disposition)
        return {'accepted': True, 'disposition': 'queued'}
    runtime.steer.side_effect = steer
    assert (await correction(app))['steering']['disposition'] == disposition
    await event(app, 'steering.applied', generation='other')
    assert session['messages'][-1]['steering']['disposition'] == disposition


async def test_stale_browser_target_rejects_before_any_input(active):
    app, runtime, session = active
    session['collaborationGeneration']['terminal'] = True
    session['status'] = 'idle'
    with pytest.raises(AppError, match='ended or changed'):
        await correction(app, expectedGenerationId='generation')
    runtime.steer.assert_not_awaited()
    assert not runtime.sent and not session['messages']


@pytest.mark.parametrize('failure', ['ended', 'unsupported', 'lost'])
async def test_failed_steering_never_starts_or_fails_current_work(active, failure):
    app, runtime, session = active
    if failure == 'lost':
        runtime.steer.side_effect = TimeoutError()
    elif failure == 'unsupported':
        runtime.steer = None
    else:
        runtime.steer.return_value = {'accepted': False, 'reason': 'Run ended before admission.'}
    result = await correction(app)
    assert result['steering']['disposition'] == ('unknown' if failure == 'lost' else 'held')
    assert session['status'] == 'working' and not session.get('error')
    assert not runtime.sent and not runtime.started
    check = await app.dispatch('conversation.delivery', {'sessionId': session['id'], 'inputId': 'correction'})
    assert check['result']['steering'] == result['steering']
    with pytest.raises(AppError, match='original run'):
        await app.dispatch('conversation.retry', {'sessionId': session['id'], 'inputId': 'correction', 'confirmUncertain': True})


async def test_restart_retains_uncertainty_without_replay(active, tmp_path):
    app, runtime, session = active
    await correction(app)
    await app.close()
    restored = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    try:
        saved = restored._session(session['id'])['messages'][-1]
        assert saved['steering']['disposition'] == 'unknown'
        assert not restored.runtime.sent
        receipt = json.loads(restored.db.execute('SELECT receipt FROM commands WHERE id=?', ('correction',)).fetchone()[0])
        assert receipt['steering']['disposition'] == 'unknown'
    finally:
        await restored.close()


@pytest.mark.parametrize('delivered', [True, False])
async def test_terminal_evidence_settles_queued_correction(active, delivered):
    app, runtime, session = active
    await correction(app)
    await app.on_runtime_event('runtime.generation', {'sessionId': session['id'],
        'event': 'generation.finished', 'generation_id': 'generation',
        'input_ids': ['original', 'correction'] if delivered else ['original'],
        'active_job_ids': [], 'disposition': 'manager_turn_finished'})
    assert session['messages'][-1]['steering']['disposition'] == ('applied' if delivered else 'unknown')
    assert not runtime.sent


async def test_parked_worker_refuses_without_reacquiring(monkeypatch):
    worker = Worker()
    worker.parked = True
    worker.acquire_for_mutation = AsyncMock(side_effect=AssertionError('Do not wake'))
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    await worker.command({'op': 'control', 'id': 'request', 'operation': 'conversation.steer',
                          'arguments': {'targetGenerationId': 'old'}})
    assert replies[0]['result']['accepted'] is False
    worker.acquire_for_mutation.assert_not_awaited()


async def test_worker_routes_correction_under_existing_ownership(monkeypatch):
    worker = Worker()
    worker.session, worker.execution, worker.shared_handle = object(), object(), object()
    worker.controls, worker.runtime, worker.activation = object(), object(), object()
    worker.acquire_for_mutation = AsyncMock()
    worker.bind_activation = Mock(return_value='token')
    worker.activation_gate = SimpleNamespace(reset=Mock())
    worker.park = AsyncMock()
    async def adapter(controls, runtime, args, activation, stop_epoch):
        assert worker.command_lock.locked()
        assert activation is worker.activation
        assert args['targetGenerationId'] == 'generation'
        assert worker.context_bindings['correction'] == {'clientId': 'browser', 'targets': []}
        assert stop_epoch() == 0
        return {'accepted': True, 'disposition': 'queued'}
    monkeypatch.setattr('amplifier_web.conversation_steering.submit', adapter)
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    await worker.command({'op': 'control', 'id': 'request', 'operation': 'conversation.steer',
        'arguments': {'inputId': 'correction', 'targetGenerationId': 'generation',
                      'context_binding': {'clientId': 'browser', 'targets': []}}})
    assert replies[0]['result']['disposition'] == 'queued'
    worker.activation_gate.reset.assert_called_once_with('token')


async def test_manager_never_starts_worker_for_stale_correction():
    manager = RuntimeManager()
    manager.start = AsyncMock(side_effect=AssertionError('Must not start'))
    session = {'id': 'chat', 'messages': [{'role': 'user', 'inputId': 'correction',
               'steering': {'generationId': 'old'}}]}
    result = await manager.steer(session, 'pause', 'correction', AsyncMock())
    assert result['accepted'] is False
    manager.start.assert_not_awaited()


async def test_manager_preserves_quote_attachments_and_target():
    manager = RuntimeManager()
    manager.workers['chat'] = {'process': SimpleNamespace(returncode=None)}
    manager._admit = AsyncMock(return_value=('pending', 'reply'))
    manager._reply = AsyncMock(return_value={'accepted': True, 'disposition': 'queued'})
    session = {'id': 'chat', 'messages': [{'role': 'user', 'inputId': 'correction',
        'steering': {'generationId': 'generation'}, 'replyTo': {'id': 'quoted'},
        'attachments': [{'id': 'image'}]}]}
    await manager.steer(session, 'Pause', 'correction', AsyncMock())
    sid, op, envelope = manager._admit.call_args.args
    assert (sid, op, envelope['operation']) == ('chat', 'control', 'conversation.steer')
    assert envelope['arguments']['attachments'] == [{'id': 'image'}]
    assert envelope['arguments']['reply_context'] == {'id': 'quoted'}
    assert envelope['arguments']['targetGenerationId'] == 'generation'
