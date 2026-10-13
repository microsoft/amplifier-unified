"""Interrupted model calls recover without re-entering the turn or its tools."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core.llm_errors import LLMTimeoutError, ProviderUnavailableError, AuthenticationError, RateLimitError
from amplifier_web.request_recovery import RecoveringProvider, install


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
                                  register_capability=capabilities.__setitem__)
    install(coordinator)
    monkeypatch.setattr('amplifier_web.request_recovery.asyncio.sleep', AsyncMock())
    return coordinator


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
    LLMTimeoutError('disabled', retryable=False), ValueError('bad input'), TimeoutError('unknown owner'),
    ProviderUnavailableError('long wait', retry_after=60), ProviderUnavailableError('nan', retry_after=float('nan'))])
async def test_nonrecoverable_errors_are_not_retried(host, error):
    original = SimpleNamespace(complete=AsyncMock(side_effect=error))
    with pytest.raises(type(error)) as caught: await RecoveringProvider(original, host).complete({})
    assert caught.value is error
    assert original.complete.await_count == 1


async def test_retries_are_bounded_and_return_original_final_error(host):
    error = ProviderUnavailableError('offline')
    original = SimpleNamespace(complete=AsyncMock(side_effect=error))
    with pytest.raises(ProviderUnavailableError) as caught: await RecoveringProvider(original, host).complete({})
    assert caught.value is error and original.complete.await_count == 3
    assert [data['attempt'] for event, data in host.hooks.events] == [1, 2]


@pytest.mark.parametrize('event', ['content_block:start', 'content_block:delta', 'content_block:end', 'provider:response', 'llm:response', 'tool:pre'])
async def test_hook_exposed_output_or_tool_execution_blocks_replay(host, event):
    calls = []
    async def complete(request):
        calls.append(request)
        await host.hooks.emit(event, {})
        raise LLMTimeoutError('after output')
    with pytest.raises(LLMTimeoutError): await RecoveringProvider(SimpleNamespace(complete=complete), host).complete({})
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


async def test_stop_during_backoff_cancels_without_another_request(host, monkeypatch):
    started = asyncio.Event()
    async def wait(delay):
        started.set()
        await asyncio.Future()
    monkeypatch.setattr('amplifier_web.request_recovery.asyncio.sleep', wait)
    original = SimpleNamespace(complete=AsyncMock(side_effect=LLMTimeoutError('lost')))
    task = asyncio.create_task(RecoveringProvider(original, host).complete({}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert original.complete.await_count == 1


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


async def test_root_and_child_app_access_share_recovery_policy(host):
    from amplifier_web.app_guidance import install_app_access
    host.mount = AsyncMock()
    await install_app_access(host, AsyncMock())
    transform = host.get_capability('web.provider_transform')
    original = SimpleNamespace(complete=AsyncMock(return_value='done'))
    wrapped = transform(original)
    assert transform(original) is wrapped
    assert transform(wrapped) is wrapped
    assert isinstance(wrapped.original, RecoveringProvider)


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


async def test_real_loop_recovers_model_request_without_repeating_tool(monkeypatch):
    pytest.importorskip('amplifier_module_loop_live', reason='Optional installed runtime integration')
    pytest.importorskip('amplifier_module_context_simple', reason='Optional installed runtime integration')
    from amplifier_core import AmplifierSession, ToolResult
    from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
    monkeypatch.setattr('amplifier_web.request_recovery.DELAYS', (0, 0))
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
            if self.calls == 2: raise LLMTimeoutError('connection interrupted')
            assert 'one-effect' in str(request.messages)
            return ChatResponse(content=[TextBlock(text='Recovered')])
    session = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []})
    await session.initialize()
    try:
        coordinator = session.coordinator
        install(coordinator)
        tool, provider = Tool(), Provider()
        await coordinator.mount('tools', tool, name='effect')
        await coordinator.mount('providers', RecoveringProvider(provider, coordinator), name='fixture')
        assert await session.execute('Do one effect then answer') == 'Recovered'
        assert tool.calls == 1 and provider.calls == 3
        assert provider.requests[1] is provider.requests[2]
    finally:
        await session.cleanup()
