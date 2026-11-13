"""A live worker keeps one writer, including idle callbacks and naming."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.runtime_worker import Worker
from amplifier_foundation.session.activation import ActivationGate


@pytest.fixture
async def worker(tmp_path, monkeypatch):
    from amplifier_foundation.session import shared_state
    owner = Worker()
    owner.workspace, owner.home = tmp_path/'workspace', tmp_path/'home'
    owner.workspace.mkdir(); owner.home.mkdir()
    owner.runtime = SimpleNamespace(session_id='naming', queued_inputs=0,
        inbox=asyncio.Queue(), generation=None)
    owner.shared_store = shared_state.SharedSessionStore(owner.workspace, 'naming', root=tmp_path/'shared')
    owner.shared_handle = owner.shared_store.acquire(app='amplifier-unified', fixture=True)
    owner.activation_gate = ActivationGate()
    owner.activation = owner.activation_gate.activate()
    owner.checkpoint = AsyncMock()
    owner.session = SimpleNamespace(coordinator=SimpleNamespace(get=lambda _: None,
        get_capability=lambda name: owner.checkpoint if name == 'live.checkpoint' else None))
    owner.finish_name = asyncio.Event()
    async def name():
        await owner.finish_name.wait()
        owner.activation_gate.check_current()
        assert owner.shared_handle.active
    owner.naming = SimpleNamespace(pending=asyncio.create_task(name()))
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', lambda _: None)
    try:
        yield owner
    finally:
        owner.shutdown.set()
        pending = [owner.naming.pending] if owner.naming else []
        for task in pending: task.cancel()
        await asyncio.gather(*pending, *owner.tasks, return_exceptions=True)
        if owner.shared_handle: owner.shared_handle.release()


async def test_pending_name_refuses_retirement_without_blocking_inbox(worker):
    await asyncio.wait_for(worker.command({'op': 'retire', 'id': 'r'}), .5)
    assert not worker.shutdown.is_set() and worker.shared_handle.active
    assert not worker.tasks  # No deferred park task or settlement observer.
    worker.checkpoint.assert_not_awaited()
    worker.runtime.inbox.put_nowait('next user input')
    assert await worker.runtime.inbox.get() == 'next user input'


async def test_name_completion_retains_same_writer_until_explicit_retirement(worker):
    activation, handle = worker.activation, worker.shared_handle
    worker.finish_name.set()
    await worker.naming.pending
    assert worker.idle() and worker.shared_handle is handle and handle.active
    assert worker.activation is activation and not worker.shutdown.is_set()
    worker.checkpoint.assert_not_awaited()
    await worker.command({'op': 'retire', 'id': 'r'})
    assert worker.shutdown.is_set() and handle.active  # Cleanup owns release.
    worker.checkpoint.assert_awaited_once_with('completed')


@pytest.mark.parametrize('change', ['inbox', 'generation', 'stop', 'shutdown'])
async def test_retirement_rechecks_late_work_after_checkpoint(worker, change):
    worker.finish_name.set()
    await worker.naming.pending
    async def checkpoint(*_):
        if change == 'inbox': worker.runtime.inbox.put_nowait('late observation')
        elif change == 'generation': worker.runtime.generation = {'id': 'new'}
        elif change == 'stop': worker.stop_revision += 1
        else: worker.shutdown.set()
    worker.checkpoint.side_effect = checkpoint
    assert not await worker.checkpoint_idle()
    assert worker.shared_handle.active
    worker.activation_gate.check(worker.activation)


async def test_worker_configuration_baseline_ignores_own_controls_but_observes_external_files(worker):
    settings = worker.workspace / '.amplifier' / 'settings.yaml'
    settings.parent.mkdir()
    settings.write_text('bundle:\n  active: before\n')
    baseline = worker.configuration_stamp()
    own = worker.home / 'sessions' / 'naming'
    own.mkdir(parents=True)
    (own / 'control-state.json').write_text('{"task": {"revision": 2}}')
    assert worker.configuration_stamp() == baseline
    settings.write_text('bundle:\n  active: after-external-change\n')
    assert worker.configuration_stamp() != baseline


async def test_retirement_save_failure_has_explicit_persistence_provenance(worker, monkeypatch):
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker.finish_name.set()
    await worker.naming.pending
    worker.checkpoint.side_effect = OSError('private synthetic disk error')
    await worker.command({'op': 'retire', 'id': 'save'})
    assert worker.shared_handle.active and not worker.shutdown.is_set()
    error = next(event for event in events if event.get('event') == 'persistence.failed')
    assert error['executionFailure']['exceptionChain'][0]['errorType'] == 'OSError'
    assert events[-1]['op'] == 'reply' and events[-1]['id'] == 'save'
    assert 'private synthetic disk error' not in str(events)


async def test_original_loop_exception_is_captured_before_flattening_with_no_prompt(worker, monkeypatch):
    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker.runtime.stop_requested = False
    class Loop:
        async def execute(self):
            raise TypeError('private prompt and key')
    loop = Loop()
    worker.session.coordinator.get = lambda _: loop
    worker.capture_manager_failures()
    with pytest.raises(TypeError):
        await loop.execute()
    original = worker.execution_failure[2]
    assert original['runtimeSessionId'] == 'naming'
    assert original['stage'] == 'execution'
    assert original['exceptionChain'][0]['errorType'] == 'TypeError'
    assert 'private prompt and key' not in str(original)
    assert not events  # Publication waits for the real execution done callback.


@pytest.mark.parametrize('boundary', ['current', 'stop', 'runtime_replaced', 'mismatched_input'])
async def test_worker_produces_certified_truncation_supplement_for_bound_terminal(worker, monkeypatch, boundary):
    from amplifier_module_loop_live.failures import ManagerTurnError, turn_failure
    from amplifier_module_provider_openai._response_handling import FunctionCallTruncationError
    from amplifier_web.runtime import normalize_event
    from amplifier_web.session_health import generation_failure, terminal_exception_failure

    events = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', events.append)
    worker.finish_name.set()
    await worker.naming.pending
    worker.naming = None
    runtime = worker.runtime
    runtime.stop_requested = False
    generation = {'id': 'truncated-generation', 'input_ids': ['truncated-input']}
    error = FunctionCallTruncationError('PRIVATE arguments /private/path', provider='fixture')
    native = turn_failure(error)
    assert (native['error_type'], native['error_category'], native['error_stage']) == (
        'LLMError', 'unknown', 'provider_request')
    original = ManagerTurnError(native)

    class Loop:
        async def execute(self):
            runtime.generation = generation
            try:
                raise error
            except FunctionCallTruncationError as cause:
                # Use the real observer while the loop still owns this generation,
                # then clear it as loop-live does before the wrapper escapes.
                worker.observe({'type': 'generation.failed',
                    'generation_id': generation['id'],
                    'input_ids': ['other-input'] if boundary == 'mismatched_input' else list(generation['input_ids']),
                    **native})
                runtime.generation = None
                if boundary == 'stop':
                    worker.stop_revision += 1
                    runtime.stop_requested = True
                elif boundary == 'runtime_replaced':
                    worker.runtime = SimpleNamespace(**vars(runtime))
                raise original from cause

    loop = Loop()
    worker.session.coordinator.get = lambda _: loop
    worker.capture_manager_failures()
    with pytest.raises(ManagerTurnError) as raised:
        await loop.execute()
    assert raised.value is original and original.__cause__ is error
    assert runtime.generation is None
    terminals = [event for event in events if event.get('type') == 'generation.failed']
    assert len(terminals) == 1 and terminals[0]['sessionId'] == runtime.session_id
    supplements = [event for event in events if 'terminalFailure' in event]
    if boundary != 'current':
        assert not supplements
        return

    assert len(supplements) == 1
    capture = supplements[0]['terminalFailure']
    assert capture['generationId'] == generation['id'] and capture['inputIds'] == generation['input_ids']
    assert capture['errorType'] == 'LLMError'
    assert [row['errorType'] for row in capture['exceptionChain']][:2] == [
        'ManagerTurnError', 'FunctionCallTruncationError']
    assert capture['exceptionChain'][1]['providerFailure'] == {'code': 'function_call_truncated'}
    kind, terminal = normalize_event(terminals[0], runtime.session_id)
    assert kind == 'runtime.generation'
    session = {'id': runtime.session_id, 'status': 'error', 'generations': [terminal],
        'failure': {**generation_failure(terminal), 'generationId': generation['id'],
                    'inputIds': list(generation['input_ids']), 'recordedAt': 2}}
    kind, payload = normalize_event(supplements[0], runtime.session_id)
    assert kind == 'runtime.error'
    failure = terminal_exception_failure(session, payload['terminalFailure'])
    assert (failure['category'], failure['code'], failure['errorType']) == (
        'invalid_request', 'function_call_truncated', 'LLMError')
    assert failure['nativeFailure'] == {'category': 'unknown', 'errorType': 'LLMError',
        'stage': 'provider_request', 'retryable': False}
    assert failure['retryable'] is False and failure['recordedAt'] == 2
    assert 'PRIVATE' not in str([events, failure]) and '/private/path' not in str([events, failure])


async def test_cleanup_invalidates_writer_even_from_a_different_task_context(worker, monkeypatch):
    from contextvars import Context
    token = worker.activation
    worker.finish_name.set()
    await worker.naming.pending
    worker.naming = None
    worker.controls = SimpleNamespace(close=AsyncMock())
    worker.session.cleanup = AsyncMock()
    worker.read = AsyncMock()
    worker.shutdown.set()
    await asyncio.create_task(worker._run(), context=Context())
    assert worker.shared_handle is None
    with pytest.raises(RuntimeError, match='released or superseded'):
        worker.activation_gate.check(token)


async def test_real_live_loop_admits_second_turn_while_name_is_pending(worker):
    from amplifier_core import AmplifierSession
    from amplifier_core.message_models import ChatResponse, TextBlock
    from amplifier_module_loop_live.runtime import Runtime, Input

    class Provider:
        name, config = 'fixture', {}
        def __init__(self):
            self.requests, self.responses = asyncio.Queue(), asyncio.Queue()
        def get_info(self): return SimpleNamespace(default_model='fixture')
        def parse_tool_calls(self, response): return []
        async def complete(self, request, **kwargs):
            await self.requests.put(request)
            return await self.responses.get()

    provider = Provider()
    worker.runtime = Runtime(session_id='naming')
    worker.runtime.capture_activation = worker.activation_gate.current
    session = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {
            'configured_bundle': True, 'background_delegate': True,
            'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id='naming')
    await session.initialize()
    worker.session = session
    await session.coordinator.mount('providers', provider, name='fixture')
    for name, capability in {'live.runtime': worker.runtime,
            'live.activation': worker.activation_gate,
            'live.checkpoint': worker.checkpoint}.items():
        session.coordinator.register_capability(name, capability)
    assert session.coordinator.get_capability('live.park') is None
    token = worker.activation_gate.bind(worker.activation)
    await worker.runtime.submit(Input('user', 'First turn', id='first', activation=worker.activation))
    execution = asyncio.create_task(session.execute(''))
    try:
        await worker.runtime.wait_for(lambda e: e['type'] == 'session.ready', 3)
        await asyncio.wait_for(provider.requests.get(), 3)
        await provider.responses.put(ChatResponse(content=[TextBlock(text='First answer')]))
        await worker.runtime.wait_for(lambda e: e['type'] == 'generation.finished', 3)
        await worker.runtime.wait_for(lambda e: e['type'] == 'session.idle', 3)
        await worker.runtime.submit(Input('user', 'Second turn', id='second', activation=worker.activation))
        request = await asyncio.wait_for(provider.requests.get(), 1)
        assert 'Second turn' in str(request.messages)
        assert not worker.naming.pending.done()
        assert worker.shared_handle.active and worker.activation_gate.current_valid
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await session.cleanup()
        worker.activation_gate.reset(token)
