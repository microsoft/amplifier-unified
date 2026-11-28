"""Interrupted model calls recover without re-entering the turn or its tools."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core.llm_errors import LLMTimeoutError, ProviderUnavailableError, AuthenticationError, RateLimitError
from amplifier_web.request_recovery import RecoveringProvider, install

real_sleep = asyncio.sleep


class Hooks:
    def __init__(self): self.handlers, self.events = {}, []
    def register(self, event, handler, **kwargs): self.handlers.setdefault(event, []).append(handler)
    async def emit(self, event, data):
        self.events.append((event, data))
        for handler in self.handlers.get(event, []): await handler(event, data)


@pytest.fixture
def host(monkeypatch):
    runtime = SimpleNamespace(generation={'id': 'accepted-turn'}, closed=False)
    capabilities = {'live.runtime': runtime}
    coordinator = SimpleNamespace(hooks=Hooks(), get_capability=capabilities.get,
                                  get=lambda kind: {} if kind == 'tools' else None,
                                  register_capability=capabilities.__setitem__)
    install(coordinator)
    monkeypatch.setattr('amplifier_web.request_recovery.asyncio.sleep', AsyncMock())
    return coordinator


@pytest.fixture
def real_host(host):
    from amplifier_core import HookRegistry
    host.hooks = HookRegistry()
    host.register_capability('web.request_recovery', False)
    install(host)
    return host


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('payload', ['a response', ['a response'], None])
async def test_real_hooks_malformed_response_blocks_replay(real_host, streaming, payload):
    from amplifier_core import HookResult
    seen, calls = [], []
    async def observe(event, data):
        seen.append(data)
        return HookResult()
    real_host.hooks.register('llm:response', observe, name='response-reader')
    async def complete(request):
        calls.append(request)
        await real_host.hooks.emit('llm:response', payload)
        if len(calls) == 1: raise LLMTimeoutError('after response')
        return 'duplicate'
    async def stream(request):
        yield await complete(request)
    wrapped = RecoveringProvider(SimpleNamespace(complete=complete, stream=stream), real_host)
    with pytest.raises(LLMTimeoutError):
        if streaming:
            async for _ in wrapped.stream({}): pytest.fail('unexpected output')
        else: await wrapped.complete({})
    assert len(calls) == 1 and seen == [payload]


@pytest.mark.parametrize('payload', ['a response', ['a response'], None])
async def test_exposure_handler_accepts_unknown_payload_without_handler_failure(host, payload):
    # In addition to Core's isolated-handler behavior above, enforce that our
    # own observer handles these payloads without relying on that isolation.
    from amplifier_web.request_recovery import _ACTIVE
    scope = {'exposed': False}
    token = _ACTIVE.set(scope)
    try:
        await host.hooks.emit('llm:response', payload)
        assert scope['exposed'] is True
    finally:
        _ACTIVE.reset(token)


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('event', ['thinking:delta', 'thinking:final', 'llm:content_block',
                                  'orchestrator:goal_progress'])
async def test_real_hooks_progressive_output_then_error_blocks_replay(real_host, streaming, event):
    from amplifier_core import HookResult
    seen, calls = [], []
    async def observe(event, data):
        seen.append(data)
        return HookResult()
    real_host.hooks.register(event, observe, name='output-reader')
    async def complete(request):
        calls.append(request)
        await real_host.hooks.emit(event, {'text': 'Already shown'})
        await real_host.hooks.emit('llm:response', {'status': 'error', 'error': 'offline'})
        if len(calls) == 1: raise LLMTimeoutError('after output')
        return 'duplicate'
    async def stream(request):
        yield await complete(request)
    wrapped = RecoveringProvider(SimpleNamespace(complete=complete, stream=stream), real_host)
    with pytest.raises(LLMTimeoutError):
        if streaming:
            async for _ in wrapped.stream({}): pytest.fail('unexpected output')
        else: await wrapped.complete({})
    assert len(calls) == 1 and len(seen) == 1 and seen[0]['text'] == 'Already shown'


@pytest.mark.parametrize('kind,extra,recover', [
    ('chatgpt', {'error': 'offline', 'model': 'fixture'}, True),
    ('anthropic', {'error': 'offline', 'model': 'fixture'}, True),
    ('gemini', {'error': 'offline', 'model': 'fixture'}, True),
    ('openai', {'error': 'offline', 'model': 'fixture'}, True),
    ('azure-openai', {'error': 'offline', 'model': 'fixture'}, True),
    ('github-copilot', {'error_type': 'ProviderUnavailableError', 'error_message': 'offline'}, False),
    ('chat-completions', {'error_type': 'APIConnectionError'}, False),
    ('openai-with-usage', {'error': 'offline', 'usage': {}}, False),
    ('openai-with-cost', {'error': 'offline', 'known_cost_usd': '0.01'}, False),
    ('openai-with-details', {'error': 'offline', 'response_details': {}}, False),
])
async def test_real_hooks_connection_error_shapes_keep_conservative_policy(real_host, kind, extra, recover):
    """Source-derived payloads; not live qualification of these connections."""
    calls = []
    async def complete(request):
        calls.append(request)
        if len(calls) == 1:
            await real_host.hooks.emit('llm:response', {
                'provider': kind, 'status': 'error', 'duration_ms': 5, **extra})
            raise ProviderUnavailableError('offline')
        return 'Recovered'
    wrapped = RecoveringProvider(SimpleNamespace(complete=complete), real_host)
    if recover:
        assert await wrapped.complete({}) == 'Recovered'
        assert len(calls) == 2
    else:
        with pytest.raises(ProviderUnavailableError): await wrapped.complete({})
        assert len(calls) == 1


@pytest.fixture
def clock(host, monkeypatch):
    clock = SimpleNamespace(now=0, waits=[])
    async def wait(delay):
        clock.waits.append(delay)
        clock.now += delay
    monkeypatch.setattr('amplifier_web.request_recovery.monotonic', lambda: clock.now)
    monkeypatch.setattr('amplifier_web.request_recovery.asyncio.sleep', wait)
    return clock


async def test_inflight_call_reuses_request_and_completed_tool_result(host):
    history = [{'role': 'user', 'content': 'work'}, {'role': 'tool', 'content': 'already done', 'tool_call_id': 'one'}]
    request = {'model': 'chosen', 'messages': deepcopy(history)}
    result = object()
    original = SimpleNamespace(complete=AsyncMock(side_effect=[LLMTimeoutError('socket lost'), result]))
    wrapped = RecoveringProvider(original, host)
    assert not hasattr(wrapped, 'stream')
    assert await wrapped.complete(request, reasoning_effort='high') is result
    assert original.complete.await_count == 2
    assert all(call.args[0] is request for call in original.complete.await_args_list)
    assert request['messages'] == history
    assert [data['attempt'] for event, data in host.hooks.events if event == 'provider:retry'] == [1]
    assert all('socket lost' not in str(data) for event, data in host.hooks.events)


@pytest.mark.parametrize('error', [AuthenticationError('auth'), RateLimitError('limited'),
    LLMTimeoutError('disabled', retryable=False), LLMTimeoutError('truthy', retryable=1),
    ValueError('bad input'), TimeoutError('unknown owner'),
    *[ProviderUnavailableError('bad hint', retry_after=hint)
      for hint in (60, -1, '5', float('nan'), float('inf'))]])
async def test_nonrecoverable_errors_are_not_retried(host, error):
    original = SimpleNamespace(complete=AsyncMock(side_effect=error))
    with pytest.raises(type(error)) as caught: await RecoveringProvider(original, host).complete({})
    assert caught.value is error
    assert original.complete.await_count == 1


@pytest.mark.parametrize('streaming', [False, True])
async def test_retries_are_bounded_and_return_original_final_error(host, clock, streaming):
    error = ProviderUnavailableError('offline')
    original = SimpleNamespace(complete=AsyncMock(side_effect=error))
    calls = []
    async def stream(request):
        calls.append(request)
        raise error
        yield
    original.stream = stream
    wrapped = RecoveringProvider(original, host)
    with pytest.raises(ProviderUnavailableError) as caught:
        if streaming:
            async for _ in wrapped.stream({}): pytest.fail('unexpected output')
        else:
            await wrapped.complete({})
    assert caught.value is error
    assert (len(calls) if streaming else original.complete.await_count) == 22
    assert clock.now == 575
    assert clock.waits == [5, 10, 20] + [30] * 18
    retries = [data for event, data in host.hooks.events]
    assert [data['attempt'] for data in retries] == list(range(1, 22))
    assert all(data['max_retries'] == 22 and data['tools_replayed'] is False for data in retries)
    assert [data['delay'] for data in retries] == clock.waits


@pytest.mark.parametrize('hint', [0, 12, 30])
async def test_provider_retry_after_is_respected_within_bound(host, clock, hint):
    original = SimpleNamespace(complete=AsyncMock(side_effect=[
        ProviderUnavailableError('offline', retry_after=hint), 'done']))
    assert await RecoveringProvider(original, host).complete({}) == 'done'
    assert clock.waits == [max(5, hint)]


@pytest.mark.parametrize('spent', [480, 516, 650])
@pytest.mark.parametrize('streaming', [False, True])
async def test_first_retryable_failure_starts_recovery_budget(host, clock, spent, streaming):
    calls = []
    async def complete(request):
        calls.append(clock.now)
        if len(calls) == 1: clock.now += spent
        # Connection comes back 150 s after the first failure; healthy work
        # before that failure must not shorten the recovery opportunity.
        if clock.now < spent + 150:
            raise LLMTimeoutError('provider exhausted its own retries')
        return 'Recovered'
    async def stream(request):
        yield await complete(request)
    wrapped = RecoveringProvider(SimpleNamespace(complete=complete, stream=stream), host)
    if streaming: assert [item async for item in wrapped.stream({})] == ['Recovered']
    else: assert await wrapped.complete({}) == 'Recovered'
    assert calls == [0] + [spent + offset for offset in (5, 15, 35, 65, 95, 125, 155)]
    assert clock.now == spent + 155


@pytest.mark.parametrize('remaining', [30, 27, 0, -1])
@pytest.mark.parametrize('streaming', [False, True])
async def test_no_wait_when_another_retry_cannot_start_before_deadline(host, clock, remaining, streaming):
    calls, errors = [], []
    async def complete(request):
        calls.append(clock.now)
        if len(calls) == 2: clock.now = 600 - remaining
        error = ProviderUnavailableError('last failure', retry_after=30)
        errors.append(error)
        raise error
    async def stream(request):
        yield await complete(request)
    wrapped = RecoveringProvider(SimpleNamespace(complete=complete, stream=stream), host)
    with pytest.raises(ProviderUnavailableError) as caught:
        if streaming:
            async for _ in wrapped.stream({}): pytest.fail('unexpected output')
        else: await wrapped.complete({})
    assert calls == [0, 30] and caught.value is errors[-1]
    assert clock.waits == [30]
    assert len(host.hooks.events) == 1


@pytest.mark.parametrize('event', ['content_block:start', 'content_block:delta', 'content_block:end',
    'llm:stream_block_start', 'llm:stream_block_delta', 'llm:stream_block_end',
    'provider:response', 'llm:response', 'tool:pre'])
async def test_hook_exposed_output_or_tool_execution_blocks_replay(host, event):
    calls = []
    async def complete(request):
        calls.append(request)
        await host.hooks.emit(event, {})
        raise LLMTimeoutError('after output')
    with pytest.raises(LLMTimeoutError): await RecoveringProvider(SimpleNamespace(complete=complete), host).complete({})
    assert len(calls) == 1


@pytest.mark.parametrize('extra', [{'status': 'ok'}, {'content': 'answer'},
    {'raw': {'text': 'answer'}}, {'tool_calls': [{}]}, {'unknown': True}])
async def test_error_response_with_output_or_unknown_fields_still_blocks_retry(host, extra):
    calls = []
    async def complete(request):
        calls.append(request)
        await host.hooks.emit('llm:response', {'status': 'error', **extra})
        raise LLMTimeoutError('after response')
    with pytest.raises(LLMTimeoutError):
        await RecoveringProvider(SimpleNamespace(complete=complete), host).complete({})
    assert len(calls) == 1


@pytest.mark.parametrize('mode', ['native', 'generation', 'closed', 'request'])
async def test_native_operations_or_changed_authority_are_not_replayed(host, mode):
    calls = []
    async def complete(request):
        calls.append(request)
        if mode == 'generation': host.get_capability('live.runtime').generation = {'id': 'new'}
        if mode == 'closed': host.get_capability('live.runtime').closed = True
        if mode == 'request': request['changed'] = True
        raise LLMTimeoutError('lost')
    with pytest.raises(LLMTimeoutError):
        await RecoveringProvider(SimpleNamespace(complete=complete, native_bundle_live=mode == 'native'), host).complete({})
    assert len(calls) == 1


@pytest.mark.parametrize('streaming', [False, True])
async def test_stop_during_backoff_cancels_without_another_request(host, monkeypatch, streaming):
    started = asyncio.Event()
    async def wait(delay):
        started.set()
        await asyncio.Future()
    monkeypatch.setattr('amplifier_web.request_recovery.asyncio.sleep', wait)
    original = SimpleNamespace(complete=AsyncMock(side_effect=LLMTimeoutError('lost')))
    async def stream(request):
        yield await original.complete(request)
    original.stream = stream
    wrapped = RecoveringProvider(original, host)
    async def consume():
        if streaming:
            async for _ in wrapped.stream({}): pytest.fail('unexpected output')
        else: await wrapped.complete({})
    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert original.complete.await_count == 1


@pytest.mark.parametrize('change', ['generation', 'runtime', 'closed', 'request', 'kwargs', 'exposed'])
@pytest.mark.parametrize('streaming', [False, True])
async def test_changes_during_backoff_block_another_request(host, monkeypatch, change, streaming):
    request, options = {}, {'nested': {'effort': 'high'}}
    async def wait(delay):
        runtime = host.get_capability('live.runtime')
        if change == 'generation': runtime.generation['id'] = 'new'
        if change == 'runtime': host.register_capability('live.runtime', SimpleNamespace(generation=runtime.generation, closed=False))
        if change == 'closed': runtime.closed = True
        if change == 'request': request['changed'] = True
        if change == 'kwargs': options['nested']['effort'] = 'low'
        if change == 'exposed': await host.hooks.emit('content_block:delta', {})
    monkeypatch.setattr('amplifier_web.request_recovery.asyncio.sleep', wait)
    original = SimpleNamespace(complete=AsyncMock(side_effect=LLMTimeoutError('lost')))
    calls = []
    async def stream(request, **kwargs):
        calls.append(request)
        raise LLMTimeoutError('lost')
        yield
    original.stream = stream
    wrapped = RecoveringProvider(original, host)
    with pytest.raises(LLMTimeoutError):
        if streaming:
            async for _ in wrapped.stream(request, **options): pytest.fail('unexpected output')
        else:
            await wrapped.complete(request, **options)
    assert (len(calls) if streaming else original.complete.await_count) == 1


async def test_nested_argument_change_inside_provider_blocks_retry(host):
    async def complete(request, **kwargs):
        kwargs['options']['model'] = 'changed'
        raise LLMTimeoutError('lost')
    original = SimpleNamespace(complete=AsyncMock(side_effect=complete))
    with pytest.raises(LLMTimeoutError):
        await RecoveringProvider(original, host).complete({}, options={'model': 'chosen'})
    assert original.complete.await_count == 1


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('stop', [False, True], ids=['deadline', 'stop'])
async def test_retried_transport_is_bounded_and_cancellable(host, monkeypatch, streaming, stop):
    monkeypatch.setattr('amplifier_web.request_recovery.DELAYS', (0,) * 22)
    monkeypatch.setattr('amplifier_web.request_recovery.RECOVERY_SECONDS', .05)
    monkeypatch.setattr('amplifier_web.request_recovery.asyncio.sleep', real_sleep)
    first, error = LLMTimeoutError('first loss'), ProviderUnavailableError('last loss')
    calls, closed = [], []
    waiting = asyncio.Event()
    async def complete(request):
        calls.append(request)
        if len(calls) == 1: raise first
        if len(calls) == 2: raise error
        waiting.set()
        try: await asyncio.Future()
        finally: closed.append(True)
    async def stream(request):
        yield await complete(request)
    wrapped = RecoveringProvider(SimpleNamespace(complete=complete, stream=stream), host)
    async def consume():
        if streaming:
            async for _ in wrapped.stream({}): pytest.fail('unexpected output')
        else: await wrapped.complete({})
    task = asyncio.create_task(consume())
    await waiting.wait()
    if stop: task.cancel()
    with pytest.raises(asyncio.CancelledError if stop else ProviderUnavailableError) as caught:
        await asyncio.wait_for(task, 2)
    if not stop: assert caught.value is error
    assert len(calls) == 3 and closed == [True]


@pytest.mark.parametrize('streaming', [False, True])
async def test_recovery_deadline_does_not_cut_off_exposed_response(host, monkeypatch, streaming):
    monkeypatch.setattr('amplifier_web.request_recovery.DELAYS', (0,) * 22)
    monkeypatch.setattr('amplifier_web.request_recovery.RECOVERY_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.request_recovery.asyncio.sleep', real_sleep)
    calls = []
    async def complete(request):
        calls.append(request)
        if len(calls) == 1: raise LLMTimeoutError('offline')
        await host.hooks.emit('content_block:delta', {'text': 'Answer'})
        await real_sleep(.03)
        return 'Answer'
    async def stream(request):
        calls.append(request)
        if len(calls) == 1: raise LLMTimeoutError('offline')
        yield 'Answer'
        await real_sleep(.03)
        yield ' end'
    wrapped = RecoveringProvider(SimpleNamespace(complete=complete, stream=stream), host)
    if streaming:
        assert [event async for event in wrapped.stream({})] == ['Answer', ' end']
    else: assert await wrapped.complete({}) == 'Answer'
    assert len(calls) == 2


async def test_original_healthy_quiet_call_keeps_provider_timeout_policy(host, monkeypatch):
    monkeypatch.setattr('amplifier_web.request_recovery.RECOVERY_SECONDS', .01)
    async def complete(request):
        await real_sleep(.03)
        return 'Quiet generation completed'
    assert await RecoveringProvider(SimpleNamespace(complete=complete), host).complete({}) == 'Quiet generation completed'


async def test_unknown_error_on_retried_transport_is_not_a_host_deadline(host):
    error = TimeoutError('not owned by recovery')
    original = SimpleNamespace(complete=AsyncMock(side_effect=[LLMTimeoutError('offline'), error]))
    with pytest.raises(TimeoutError) as caught:
        await RecoveringProvider(original, host).complete({})
    assert caught.value is error and original.complete.await_count == 2


async def test_failed_retry_diagnostic_cannot_prevent_recovery(host):
    host.hooks.emit = AsyncMock(side_effect=RuntimeError('diagnostic unavailable'))
    original = SimpleNamespace(complete=AsyncMock(side_effect=[LLMTimeoutError('offline'), 'done']))
    assert await RecoveringProvider(original, host).complete({}) == 'done'
    assert original.complete.await_count == 2


@pytest.mark.parametrize('partial', [False, True])
async def test_stream_retries_only_before_any_exposed_event(host, partial):
    attempts, closed = [], []
    async def stream(request, **kwargs):
        attempts.append(request)
        try:
            if len(attempts) == 1:
                if partial: yield 'partial'
                raise LLMTimeoutError('lost')
            yield 'complete'
        finally: closed.append(True)
    provider = RecoveringProvider(SimpleNamespace(stream=stream), host)
    output = []
    async def consume():
        async for event in provider.stream({'model': 'same'}): output.append(event)
    if partial:
        with pytest.raises(LLMTimeoutError): await consume()
        assert output == ['partial'] and len(attempts) == 1
    else:
        await consume()
        assert output == ['complete'] and len(attempts) == 2
    assert len(closed) == len(attempts)


@pytest.mark.parametrize('different_task', [False, True])
async def test_stream_close_releases_provider_immediately_without_leaking_scope(host, different_task):
    from amplifier_web.request_recovery import _ACTIVE
    closed = []
    async def source(request):
        try:
            assert _ACTIVE.get() is not None
            yield 'first'
            yield 'second'
        finally:
            assert _ACTIVE.get() is not None
            closed.append(True)
    stream = RecoveringProvider(SimpleNamespace(stream=source), host).stream({})
    async def read():
        assert await anext(stream) == 'first'
        assert _ACTIVE.get() is None
    async def close():
        await stream.aclose()
        assert _ACTIVE.get() is None
    if different_task:
        await asyncio.create_task(read())
        await asyncio.create_task(close())
    else:
        await read()
        await close()
    assert closed == [True]
    assert host.hooks.events == []


async def test_root_and_child_app_access_share_recovery_policy(host):
    from amplifier_web.app_guidance import install_app_access
    host.mount = AsyncMock()
    await install_app_access(host, AsyncMock(), execution_role='root')
    transform = host.get_capability('web.provider_transform')
    original = SimpleNamespace(complete=AsyncMock(return_value='done'))
    wrapped = transform(original)
    assert transform(original) is wrapped
    assert transform(wrapped) is wrapped
    assert isinstance(wrapped.original, RecoveringProvider)


async def test_chatgpt_terminal_error_diagnostic_is_not_exposed_output(host):
    """Run the real provider's terminal hook through the installed host wrapper."""
    from amplifier_core.message_models import ChatRequest
    from amplifier_module_provider_openai_chatgpt import ChatGPTProvider
    from amplifier_web.app_guidance import install_app_access
    provider = ChatGPTProvider({'default_model': 'fixture', 'max_retries': 0}, host)
    provider._ensure_valid_tokens = AsyncMock()
    provider._resolve_default_model = AsyncMock(return_value='fixture')
    provider._build_payload = lambda *args, **kwargs: {'model': 'fixture'}
    provider._media_send_guard = lambda *args: None
    response = object()
    parsed = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_tokens=None)
    provider._complete_attempt = AsyncMock(side_effect=[
        LLMTimeoutError('offline', retryable=True), (response, parsed)])
    host.mount = AsyncMock()
    await install_app_access(host, AsyncMock(), execution_role='root')
    # Surface fitting is covered separately; exercise the actual Work-aware
    # recovery wrapper around the real subscription provider here.
    wrapped = host.get_capability('web.provider_transform')(provider).original
    request = ChatRequest(messages=[])
    assert await wrapped.complete(request) is response
    assert provider._complete_attempt.await_count == 2
    assert any(event == 'llm:response' and data['status'] == 'error'
               for event, data in host.hooks.events)
    retries = [data for event, data in host.hooks.events if event == 'provider:retry']
    assert len(retries) == 1 and retries[0]['recovery'] == 'unreturned_model_request'


@pytest.mark.parametrize('status', ['available', 'unavailable'])
async def test_work_bound_request_retains_no_replay_guard(host, status):
    from amplifier_core.message_models import ChatRequest
    from amplifier_web.app_guidance import install_app_access
    host.mount = AsyncMock()
    await install_app_access(host, AsyncMock(), execution_role='root')
    original = SimpleNamespace(complete=AsyncMock(side_effect=[LLMTimeoutError('lost'), 'done']))
    surface = host.get_capability('web.provider_transform')(original)
    surface.delivery.work_request = lambda request: {'required': {'status': status}}
    if status == 'available':
        with pytest.raises(LLMTimeoutError): await surface.original.complete(ChatRequest(messages=[]))
        assert original.complete.await_count == 1
    else:
        assert await surface.original.complete(ChatRequest(messages=[])) == 'done'
        assert original.complete.await_count == 2


async def test_opaque_options_do_not_prevent_original_request(host):
    class Opaque:
        def __deepcopy__(self, memo): raise TypeError('not copyable')
    value = Opaque()
    original = SimpleNamespace(complete=AsyncMock(return_value='done'))
    assert await RecoveringProvider(original, host).complete({}, transport=value) == 'done'
    original.complete.assert_awaited_once_with({}, transport=value)
    error = LLMTimeoutError('uncertain')
    original.complete = AsyncMock(side_effect=error)
    with pytest.raises(LLMTimeoutError):
        await RecoveringProvider(original, host).complete({}, transport=value)
    assert original.complete.await_count == 1


@pytest.mark.parametrize('loop_name', ['loop-live', 'loop-streaming'])
async def test_real_loop_recovers_model_request_without_repeating_tool(monkeypatch, clock, loop_name):
    pytest.importorskip('amplifier_module_loop_live', reason='Optional installed runtime integration')
    pytest.importorskip('amplifier_module_context_simple', reason='Optional installed runtime integration')
    from amplifier_core import AmplifierSession, ToolResult
    from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
    class Tool:
        name = 'effect'
        description = 'Count one synthetic effect'
        input_schema = {'type': 'object', 'properties': {}}
        calls = 0
        async def execute(self, arguments):
            self.calls += 1
            return ToolResult(success=True, output={'completed_once': self.calls})
    class Provider:
        name = 'fixture'
        config = {}
        calls = 0
        requests = []
        def get_info(self): return SimpleNamespace(default_model='fixture')
        def parse_tool_calls(self, response): return response.tool_calls or []
        async def complete(self, request, **kwargs):
            self.calls += 1
            self.requests.append(request)
            if self.calls == 1:
                return ChatResponse(content=[], tool_calls=[ToolCall(id='one-effect', name='effect', arguments={})])
            if self.calls in (2, 3, 4):
                # Include the measured first call's 94.5 s provider retry cost.
                clock.now += 94.5
                await coordinator.hooks.emit('llm:response', {
                    'status': 'error', 'error': 'connection interrupted',
                    'provider': 'fixture', 'duration_ms': 94500})
                raise LLMTimeoutError('connection interrupted')
            assert 'one-effect' in str(request.messages)
            return ChatResponse(content=[TextBlock(text='Recovered')])
    session = AmplifierSession({'session': {
        'orchestrator': {'module': loop_name, 'config': {'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []})
    await session.initialize()
    try:
        coordinator = session.coordinator
        install(coordinator)
        loop = coordinator.get('orchestrator')
        coordinator.session_state['goal'] = {'condition': 'Finish the fixture', 'turns_used': 0,
                                             'last_reason': '', 'cap': 3}
        loop._evaluate_goal = AsyncMock(side_effect=[(False, 'One continuation needed'), (True, 'Verified')])
        loop._summarize_goal_run = AsyncMock(return_value='Goal verified')
        completions = []
        async def completed(event, data):
            from amplifier_core import HookResult
            completions.append(data)
            return HookResult()
        coordinator.hooks.register('orchestrator:complete', completed, name='fixture-complete')
        tool, provider = Tool(), Provider()
        await coordinator.mount('tools', tool, name='effect')
        await coordinator.mount('providers', RecoveringProvider(provider, coordinator), name='fixture')
        assert await session.execute('Do one effect then answer') == 'Recovered'
        assert tool.calls == 1 and provider.calls == 6
        assert all(request is provider.requests[1] for request in provider.requests[2:5])
        assert clock.now == 318.5 and clock.waits == [5, 10, 20]
        assert loop._evaluate_goal.await_count == 2
        assert coordinator.session_state['goal'] is None
        assert [row['goal_final'] for row in completions] == [False, True]
        assert all(row['status'] == 'success' and row['metadata']['resumable'] for row in completions)
    finally:
        await session.cleanup()
