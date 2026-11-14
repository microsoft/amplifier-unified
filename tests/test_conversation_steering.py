"""User steering preserves a run, including ambiguous and late delivery."""
import json
import time
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
        'rootSessionId': session['id'], 'runtimeOwnerId': 'owner', 'observedAt': time.time(),
        'event': 'generation.started', 'generation_id': 'generation', 'initial_input_id': 'original'})
    yield app, runtime, session
    await app.close()


async def correction(app, **extra):
    return await app.dispatch('conversation.send', {'sessionId': app._session()['id'],
        'text': 'Please find a good pause point.', 'expectedGenerationId': 'generation', **extra}, command_id='correction')


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
    telemetry.lifecycle({'type': 'input.delivered', 'input_id': 'ordinary', 'delivery': 'request_boundary'})
    assert telemetry.turn_id == 'original'
    assert telemetry.admission_input(session['id']) == 'ordinary'


@pytest.mark.parametrize("ending,disposition", [
    ("steering.applied", "applied"), ("steering.failed", "held"),
    ("native.outcome_unknown", "unknown"),
])
async def test_native_phases_update_exact_ui_command_without_replay(active, ending, disposition):
    app, runtime, session = active
    await correction(app, expectedGenerationId="generation")
    for phase in ("steering.accepted", "steering.pending"):
        await event(app, phase)
        assert session["messages"][-1]["steering"]["disposition"] == "queued"
        assert "contextAt" not in session["messages"][-1]["steering"]
    await event(app, ending, generation="another-generation")
    assert session["messages"][-1]["steering"]["disposition"] == "queued"
    await event(app, ending)
    duplicate = await correction(app, expectedGenerationId="generation")
    assert duplicate["duplicate"] and duplicate["steering"]["disposition"] == disposition
    assert runtime.steer.await_count == 1 and not runtime.sent


async def test_native_delivery_phases_survive_late_send_ack_and_passive_check(active):
    app, runtime, session = active
    async def steer(*args):
        for phase in ('sent', 'accepted', 'pending'):
            await app.on_runtime_event('runtime.steering', {
                'sessionId': session['id'], 'event': 'steering.' + phase,
                'input_id': 'correction', 'target_generation_id': 'generation', 'delivery': 'native'})
        return {'accepted': True, 'disposition': 'queued', 'mode': 'native', 'phase': 'sent'}
    runtime.steer.side_effect = steer
    receipt = await correction(app, expectedGenerationId='generation')
    assert receipt['steering']['mode'] == 'native'
    assert receipt['steering']['phase'] == 'pending'
    assert 'contextAt' not in receipt['steering']
    checked = await app.dispatch('conversation.delivery', {'sessionId': session['id'], 'inputId': 'correction'})
    assert checked['result']['steering']['phase'] == 'pending'
    assert runtime.steer.await_count == 1


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


@pytest.mark.parametrize('ended', [False, True])
async def test_pending_file_policy_allows_human_guidance_without_next_turn(active, ended):
    app, runtime, session = active
    session.update(fileAccessPending=True, configurationPending=True)
    if ended:
        runtime.steer.return_value = {'accepted': False, 'reason': 'Run ended before admission.'}
    result = await correction(app, expectedGenerationId='generation')
    assert result['steering']['disposition'] == ('held' if ended else 'queued')
    assert 'allowNextTurn' not in result['steering']
    sent_session = runtime.steer.call_args.args[0]
    assert 'allowNextTurn' not in sent_session['messages'][-1]['steering']
    assert session['fileAccessPending'] and session['configurationPending']
    assert session['execution']['currentTurnId'] == 'original'
    assert not runtime.sent and not runtime.started
    assert session['messages'][-1]['text'] == 'Please find a good pause point.'


@pytest.mark.parametrize('state', ['idle', 'terminal', 'remounting', 'wrong_generation'])
async def test_pending_file_policy_does_not_admit_new_or_stale_work(active, state):
    app, runtime, session = active
    session.update(fileAccessPending=True, configurationPending=True)
    if state == 'idle':
        session['status'] = 'idle'
        session['workers'] = [{'id':'child', 'persistent':True, 'status':'idle'}]
    elif state == 'terminal':
        session['collaborationGeneration'].update(terminal=True, completed=True)
    elif state == 'remounting':
        session['configurationBusy'] = True
    with pytest.raises(AppError):
        await correction(app, expectedGenerationId='other' if state == 'wrong_generation' else 'generation')
    runtime.steer.assert_not_awaited()
    assert not runtime.sent and not runtime.started and not session['messages']


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
    import asyncio
    from amplifier_foundation.session.activation import ActivationGate

    worker = Worker()
    coordinator = SimpleNamespace(
        get=Mock(return_value=SimpleNamespace(pending=None, _active_jobs=Mock(return_value=[]))),
        get_capability=Mock(side_effect=AssertionError('Active generation must not checkpoint for parking')))
    worker.session, worker.execution = SimpleNamespace(coordinator=coordinator), object()
    handle = worker.shared_handle = SimpleNamespace(release=Mock())
    worker.controls = SimpleNamespace(preserve_configuration=True)
    generation = {'id': 'generation', 'input_ids': ['original']}
    worker.runtime = SimpleNamespace(accepted={}, queued_inputs=0, inbox=asyncio.Queue(), generation=generation)
    worker.activation_gate = ActivationGate()
    activation = worker.activation = worker.activation_gate.activate()
    worker.activation_gate.reset = Mock(wraps=worker.activation_gate.reset)
    worker.activation_gate.release = Mock(wraps=worker.activation_gate.release)
    worker.shared_store = SimpleNamespace(acquire=Mock(side_effect=AssertionError('Do not reacquire the active owner')))
    bind_activation, tokens = worker.bind_activation, []
    def bind():
        token = bind_activation()
        tokens.append(token)
        return token
    worker.bind_activation = Mock(side_effect=bind)
    assert not worker.ownership.yielding
    async def adapter(controls, runtime, args, activation, stop_epoch):
        assert controls is worker.controls
        assert runtime is worker.runtime
        assert controls.preserve_configuration is False
        assert worker.command_lock.locked()
        assert activation is worker.activation
        assert worker.activation_gate.current() is activation and worker.activation_gate.current_valid
        assert args['targetGenerationId'] == 'generation'
        assert runtime.generation is generation and runtime.generation['id'] == args['targetGenerationId']
        assert worker.context_bindings['correction'] == {
            'clientId': 'browser', 'targets': [], 'interruptionRevision': 7}
        assert stop_epoch() == 0
        return {'accepted': True, 'disposition': 'queued'}
    monkeypatch.setattr('amplifier_web.conversation_steering.submit', adapter)
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    await worker.command({'op': 'control', 'id': 'request', 'operation': 'conversation.steer',
        'interruptionRevision': 7,
        'arguments': {'inputId': 'correction', 'targetGenerationId': 'generation',
                      'context_binding': {'clientId': 'browser', 'targets': []}}})
    assert len(replies) == 1 and 'error' not in replies[0]
    assert replies[0]['result']['disposition'] == 'queued'
    assert worker.controls.preserve_configuration is False
    worker.shared_store.acquire.assert_not_called()
    # Lifetime ownership remains held through the ordinary outer command.
    coordinator.get.assert_not_called()
    coordinator.get_capability.assert_not_called()
    handle.release.assert_not_called()
    worker.activation_gate.release.assert_not_called()
    assert worker.shared_handle is handle and worker.activation is activation
    assert not worker.ownership.yielding
    worker.activation_gate.check(activation)
    assert worker.runtime.generation is generation
    worker.bind_activation.assert_called_once_with()
    assert len(tokens) == 1
    worker.activation_gate.reset.assert_called_once_with(tokens[0])


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


@pytest.mark.parametrize('projected', ['active', 'completed', 'missing'])
async def test_ordinary_message_never_uses_projected_generation(active, projected, monkeypatch):
    app, runtime, session = active
    if projected == 'completed':
        session['collaborationGeneration'].update(terminal=True, completed=True)
    elif projected == 'missing':
        session.pop('collaborationGeneration')
    monkeypatch.setattr('amplifier_web.conversation_steering.target',
                        Mock(side_effect=AssertionError('Ordinary input must not classify a route')))
    async def send(captured, text, identity, emit):
        assert not captured['messages'][-1].get('steering')
        assert captured['execution']['currentTurnId'] == 'original'
        runtime.sent.append((captured['id'], text, identity))
        return {'accepted': True}
    runtime.send = send
    args = {'sessionId': session['id'], 'text': 'Continue the work'}
    result = await app.dispatch('conversation.send', args, command_id='ordinary')
    assert result['delivery'] == 'accepted' and not result.get('steering')
    assert len(runtime.sent) == 1 and runtime.sent[0][2] == 'ordinary'
    runtime.steer.assert_not_awaited()
    assert session['execution']['currentTurnId'] == 'original'
    duplicate = await app.dispatch('conversation.send', args, command_id='ordinary')
    assert duplicate['duplicate'] and len(runtime.sent) == 1
    with pytest.raises(AppError, match='already used'):
        await app.dispatch('conversation.send', {**args, 'expectedGenerationId': 'generation'}, command_id='ordinary')


@pytest.mark.parametrize('state', ['completed', 'other_live', 'other_completed', 'restart', 'failed', 'detached', 'children'])
async def test_explicit_anchor_never_falls_forward_after_completion(active, state):
    app, runtime, session = active
    if state in {'completed', 'failed', 'detached', 'children'}:
        await app.on_runtime_event('runtime.generation', {'sessionId': session['id'],
            'event': 'generation.finished' if state in {'completed', 'children'} else 'generation.' + state,
            'generation_id': 'generation', 'disposition': 'manager_turn_finished',
            'active_job_ids': ['child'] if state == 'children' else []})
    else:
        session['collaborationGeneration'] = {
            'id': 'generation' if state == 'restart' else 'another',
            'terminal': state != 'other_live', 'completed': state == 'other_completed'}
    with pytest.raises(AppError, match='Your message was not sent'):
        await correction(app, expectedGenerationId='generation')
    assert not runtime.sent and not session['messages']
    runtime.steer.assert_not_awaited()


@pytest.mark.parametrize('terminal', ['generation.finished', 'generation.failed', 'generation.detached'])
async def test_worker_explicit_anchor_never_creates_idle_turn(monkeypatch, terminal):
    from amplifier_module_loop_live.runtime import Runtime as NativeRuntime
    from amplifier_web.conversation_steering import submit
    runtime = NativeRuntime(session_id='root')
    await runtime.emit(terminal, generation_id='g', disposition='manager_turn_finished', active_job_ids=[])
    controls = SimpleNamespace(coordinator=object())
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', AsyncMock(return_value='ordinary message'))
    capability = AsyncMock(return_value={'accepted': False})
    monkeypatch.setattr('amplifier_web.collaboration_input._steering_capability', lambda controls: {'submit': capability})
    args = {'text': 'ordinary message', 'inputId': 'i', 'targetGenerationId': 'g'}
    result = await submit(controls, runtime, args, None, lambda: 0)
    assert result['accepted'] is False
    assert not runtime.accepted and runtime.queued_inputs == 0
    capability.assert_not_awaited()


@pytest.mark.parametrize('mode,events', [
    ('native', ['steering.sent', 'steering.accepted', 'steering.pending', 'steering.applied']),
    ('request_boundary', ['input.queued', 'input.delivered']),
])
async def test_ordinary_receipt_uses_only_native_route_evidence(active, mode, events):
    app, runtime, session = active
    async def send(captured, text, identity, emit):
        assert 'steering' not in captured['messages'][-1]
        for kind in events:
            await emit('runtime.steering', {'sessionId': session['id'], 'rootSessionId': session['id'],
                'event': kind, 'input_id': identity, 'generation_id': 'generation', 'delivery': mode})
            current = session['messages'][-1]['steering']
            assert current['disposition'] == ('applied' if kind in {'input.delivered', 'steering.applied'} else 'queued')
            assert ('contextAt' in current) == (current['disposition'] == 'applied')
        return {'accepted': True}
    runtime.send = send
    result = await app.dispatch('conversation.send', {'text': 'ordinary'}, command_id='ordinary')
    assert result['steering']['mode'] == mode
    assert result['steering']['disposition'] == 'applied'
    assert session['execution']['currentTurnId'] == 'original'
    checked = await app.dispatch('conversation.delivery', {'sessionId': session['id'], 'inputId': 'ordinary'})
    assert checked['result']['steering']['disposition'] == 'applied'
    runtime.steer.assert_not_awaited()


@pytest.mark.parametrize('ending,disposition', [('input.held', 'held'), ('input.unknown', 'unknown')])
async def test_delayed_owner_route_receipt_survives_projection_and_stop(active, ending, disposition):
    app, runtime, session = active
    runtime.send = AsyncMock(return_value={'accepted': True})
    await app.dispatch('conversation.send', {'text': 'ordinary'}, command_id='ordinary')
    # Receipt follows its captured input authority, not whichever generation the
    # host is displaying by the time this source-ordered event arrives.
    session['collaborationGeneration'] = {'id': 'later', 'terminal': False, 'interruptionRevision': 1}
    session['interruptionRevision'] = 1
    await app.on_runtime_event('runtime.steering', {'sessionId': session['id'], 'rootSessionId': session['id'],
        'event': ending, 'input_id': 'ordinary', 'generation_id': 'actual-owner-generation',
        'delivery': 'request_boundary', 'interruptionRevision': 0})
    assert session['messages'][-1]['steering']['disposition'] == disposition
    assert session['messages'][-1]['steering']['generationId'] == 'actual-owner-generation'
    await app.on_runtime_event('runtime.steering', {'sessionId': session['id'],
        'event': 'input.delivered', 'input_id': 'ordinary', 'generation_id': 'wrong-generation',
        'delivery': 'request_boundary', 'interruptionRevision': 0})
    assert session['messages'][-1]['steering']['disposition'] == disposition
    assert session['collaborationGeneration']['id'] == 'later'


async def test_ordinary_lost_ack_does_not_use_strict_or_retry_alternate(active):
    app, runtime, session = active
    runtime.send = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        await app.dispatch('conversation.send', {'text': 'ordinary'}, command_id='ordinary')
    assert session['messages'][-1]['delivery']['status'] == 'unknown'
    runtime.steer.assert_not_awaited()
    duplicate = await app.dispatch('conversation.send', {'text': 'ordinary'}, command_id='ordinary')
    assert duplicate['duplicate'] and duplicate['delivery'] == 'unknown'
    assert runtime.send.await_count == 1


async def test_stop_epoch_fences_network_delayed_intent_but_not_duplicate_or_new_input(active):
    app, runtime, session = active
    runtime.send = AsyncMock(return_value={'accepted': True})
    args = {'text': 'ordinary', 'expectedInterruptionRevision': 0}
    await app.dispatch('conversation.send', args, command_id='admitted')
    await app.dispatch('conversation.stop', {}, command_id='stop')
    with pytest.raises(AppError) as stopped:
        await app.dispatch('conversation.send', args, command_id='network-delayed')
    assert stopped.value.code == 'input_stopped'
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='network-delayed'").fetchone()
    duplicate = await app.dispatch('conversation.send', args, command_id='admitted')
    assert duplicate['duplicate'] and runtime.send.await_count == 1
    result = await app.dispatch('conversation.send',
        {'text': 'fresh deliberate input', 'expectedInterruptionRevision': 1}, command_id='fresh')
    assert result['delivery'] == 'accepted' and runtime.send.await_count == 2


async def test_manager_stop_epoch_rechecks_after_preparation_without_routing():
    from amplifier_web.runtime import RuntimeStartupError
    manager = RuntimeManager()
    epoch = 0
    manager.bind_execution_state(lambda sid: {'hostMatches': True, 'fenced': False,
        'revision': 0, 'directory': '/work', 'interruptionRevision': epoch})
    async def prepare(*args, **kwargs):
        nonlocal epoch
        epoch = 1
        return None
    manager._start_for_input = prepare
    manager._request = AsyncMock(side_effect=lambda *args, **kwargs: kwargs['guard']())
    with pytest.raises(RuntimeStartupError, match='stopped'):
        await manager.send({'id': 'chat', 'workspace': '/work', 'interruptionRevision': 0},
                           'ordinary', 'input', AsyncMock())


async def test_confirmed_worker_exit_retires_steering_authority_without_replay(active):
    app, runtime, session = active
    await correction(app)
    await app.on_runtime_event('runtime.ended', {'sessionId': session['id'], 'runtimeOwnerId': 'owner', 'status': 'stopped'})
    await app.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'stopped'})
    assert session['collaborationGeneration']['terminal'] is True
    assert session['collaborationGeneration']['completed'] is False
    saved = session['messages'][-1]
    assert saved['steering']['disposition'] == 'unknown'
    assert saved['delivery']['status'] == 'unknown'
    assert not runtime.sent
    # An explicit stale correction must remain bound and cannot become new work.
    with pytest.raises(AppError, match='ended or changed'):
        await app.dispatch('conversation.send', {'text': 'stale correction',
            'expectedGenerationId': 'generation'}, command_id='stale')
    # A newly written ordinary input starts a fresh turn after confirmed exit.
    result = await app.dispatch('conversation.send', {'text': 'Start fresh'}, command_id='fresh')
    assert result['delivery'] == 'accepted'
    assert 'steering' not in session['messages'][-1]
    assert len(runtime.sent) == 1
    assert runtime.steer.await_count == 1
    assert saved['steering']['disposition'] == 'unknown'


@pytest.mark.parametrize('payload', [
    {'sessionId': 'child', 'rootSessionId': None},
    {'backgroundOnly': True},
    {'sessionId': None},
    {'rootSessionId': 'different-root'},
])
async def test_background_exit_cannot_retire_root_steering_authority(active, payload):
    app, _, session = active
    app.collaboration.observe(session, 'runtime.ended', {'sessionId': session['id'], **payload})
    assert session['collaborationGeneration']['terminal'] is False


async def test_confirmed_exit_preserves_existing_terminal_success_and_applied_input(active):
    app, _, session = active
    await correction(app)
    await event(app, 'steering.applied')
    await app.on_runtime_event('runtime.generation', {'sessionId': session['id'],
        'event': 'generation.finished', 'generation_id': 'generation',
        'input_ids': ['original', 'correction'], 'active_job_ids': [],
        'disposition': 'manager_turn_finished'})
    await app.on_runtime_event('runtime.ended', {'sessionId': session['id'], 'status': 'stopped'})
    assert session['collaborationGeneration']['completed'] is True
    assert session['messages'][-1]['steering']['disposition'] == 'applied'


@pytest.mark.parametrize('disposition', ['applied', 'held'])
async def test_confirmed_exit_preserves_known_steering_disposition(active, disposition):
    app, _, session = active
    await correction(app)
    await event(app, 'steering.' + disposition)
    await app.on_runtime_event('runtime.ended', {'sessionId': session['id'], 'runtimeOwnerId': 'owner', 'status': 'interrupted'})
    assert session['collaborationGeneration']['terminal'] is True
    assert session['messages'][-1]['steering']['disposition'] == disposition
