"""User steering preserves a run, including ambiguous and late delivery."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from amplifier_web.conversation_steering import submit
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
    assert result['steering'] == {'generationId': 'generation', 'disposition': 'queued'}
    assert session['execution']['currentTurnId'] == 'original'
    assert [t['id'] for t in session['execution']['turns']] == ['original']
    assert session['status'] == 'working'
    assert runtime.sent == []
    runtime.steer.assert_awaited_once()
    await event(app, 'steering.applied')
    duplicate = await correction(app, expectedGenerationId='generation')
    assert duplicate['duplicate'] and duplicate['steering']['disposition'] == 'applied'
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


async def test_worker_rechecks_target_after_input_preparation(monkeypatch):
    pytest.importorskip('amplifier_module_loop_live.runtime')
    runtime = SimpleNamespace(closed=False, generation={'id': 'old'}, max_input_chars=10000)
    capability = {'version': 1, 'mode': 'request_boundary', 'submit': AsyncMock()}
    controls = SimpleNamespace(coordinator=SimpleNamespace(get_capability=lambda name: capability))
    async def prepare(*args, **kwargs):
        runtime.generation = {'id': 'new'}
        return 'pause'
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    result = await submit(controls, runtime, {'text': 'pause', 'inputId': 'correction',
        'targetGenerationId': 'old'}, None, lambda: 0)
    assert result['accepted'] is False
    capability['submit'].assert_not_awaited()


@pytest.mark.parametrize('late', [False, True])
async def test_real_loop_delivers_at_boundary_or_holds_without_new_generation(monkeypatch, tmp_path, late):
    """Real core/context/loop, scripted provider/tool; no model account required."""
    live = pytest.importorskip('amplifier_module_loop_live.runtime')
    pytest.importorskip('amplifier_module_context_simple')
    from amplifier_core import AmplifierSession, ToolResult
    from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    requests, responses = asyncio.Queue(), asyncio.Queue()
    class Provider:
        async def complete(self, request, **kwargs):
            await requests.put(request)
            return await responses.get()
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        def get_info(self):
            return SimpleNamespace(default_model='fixture')
    class Step:
        name, description, input_schema = 'step', 'One step', {'type': 'object', 'properties': {}}
        async def execute(self, args):
            return ToolResult(success=True, output={'done': True})
    runtime = live.Runtime()
    session = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live',
        'config': {'configured_bundle': True, 'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=runtime.session_id)
    task = None
    try:
        await session.initialize()
        await session.coordinator.mount('providers', Provider(), name='fixture')
        await session.coordinator.mount('tools', Step(), name='step')
        session.coordinator.register_capability('live.runtime', runtime)
        task = asyncio.create_task(session.execute(''))
        await runtime.wait_for(lambda e: e['type'] == 'session.ready', 5)
        await runtime.submit(live.Input('user', 'Do several steps', id='original'))
        await asyncio.wait_for(requests.get(), 5)
        generation = runtime.generation['id']
        entered, release = asyncio.Event(), asyncio.Event()
        if late:
            async def checkpoint(*args):
                entered.set()
                await release.wait()
            session.coordinator.register_capability('live.checkpoint', checkpoint)
            await responses.put(ChatResponse(content=[TextBlock(text='Already finished')]))
            await asyncio.wait_for(entered.wait(), 5)
        result = await submit(SimpleNamespace(coordinator=session.coordinator), runtime,
            {'text': 'Please find a good pause point.', 'inputId': 'correction',
             'targetGenerationId': generation}, None, lambda: 0)
        assert result['accepted'] and result['disposition'] == 'queued'
        if late:
            release.set()
            await runtime.wait_for(lambda e: e['type'] == 'steering.held', 5)
        else:
            await runtime.wait_for(lambda e: e['type'] == 'input.queued' and e['input_id'] == 'correction', 5)
            await responses.put(ChatResponse(content=[], tool_calls=[ToolCall(id='step-1', name='step', arguments={})]))
            request = await asyncio.wait_for(requests.get(), 5)
            assert 'Please find a good pause point.' in str(request.messages)
            await runtime.wait_for(lambda e: e['type'] == 'steering.applied', 5)
            await responses.put(ChatResponse(content=[TextBlock(text='Paused at the step boundary')]))
        finished = await runtime.wait_for(lambda e: e['type'] == 'generation.finished'
                                           and e['generation_id'] == generation, 5)
        await runtime.wait_for(lambda e: e['type'] == 'session.idle' and e['sequence'] > finished['sequence'], 5)
        assert len([e for e in runtime.events if e['type'] == 'generation.started']) == 1
        assert requests.empty()
        assert finished['generation_id'] == generation
        assert ('correction' in finished['input_ids']) is (not late)
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()
