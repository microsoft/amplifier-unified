"""Private server feedback must survive host request projection, not bypass it."""
from types import SimpleNamespace

import pytest
from amplifier_core.llm_errors import ContextLengthError
from amplifier_core.message_models import ChatRequest, Message

from amplifier_web.host.session import SelectedProvider
from amplifier_web.surface_delivery import SurfaceProvider


class Delivery:
    async def prepare(self, request, provider):
        return request.model_copy(update={'messages': [
            *request.messages, Message(role='user', content='Current host observation.')]})

    def commit(self, request):
        pass


class Provider:
    def __init__(self, *, emitted=False):
        self.emitted = emitted
        self.sent = []
        self.error = ContextLengthError('Synthetic input overflow', provider='fixture')
        self.consumed = False

    async def request_budget(self, request, *, context_estimate, request_options=None):
        return None

    async def complete(self, request, **options):
        self.sent.append((request, options))
        raise self.error

    async def stream(self, request, **options):
        if self.emitted:
            yield 'already visible'
        await self.complete(request, **options)

    def recover_context_overflow(self, request, error, *, context_estimate, request_options=None):
        # Same ownership contract as Anthropic's private server feedback.
        if (self.consumed or error is not self.error or request is not self.sent[-1][0]
                or dict(request_options or {}) != self.sent[-1][1]):
            return None
        self.consumed = True
        return {'estimated_input_tokens': 1_005_207, 'input_limit_tokens': 1_000_000,
                'context_token_budget': context_estimate // 2, 'max_output_tokens': 128_000}


def case(wrapping, **kwargs):
    raw = Provider(**kwargs)
    surface = SurfaceProvider(raw, Delivery())
    selection = {'model': 'claude-fable-5-2', 'effort': 'high', 'max_output_tokens': 128_000}
    if wrapping == 'surface':
        wrapped = surface
    else:
        wrapped = SelectedProvider(raw, selection,
            (lambda _: surface) if wrapping == 'selected_surface' else None)
    request = ChatRequest(messages=[Message(role='user', content='Continue the accepted task.')])
    return SimpleNamespace(raw=raw, surface=surface, wrapped=wrapped, request=request)


async def fail(value, stream):
    with pytest.raises(ContextLengthError) as caught:
        if stream:
            async for _ in value.wrapped.stream(value.request, temperature=0.3):
                pass
        else:
            await value.wrapped.complete(value.request, temperature=0.3)
    return caught.value


@pytest.mark.parametrize('wrapping', ['selected', 'surface', 'selected_surface'])
@pytest.mark.parametrize('stream', [False, True])
async def test_exact_overflow_feedback_reaches_original_inner_request_and_options_once(wrapping, stream):
    value = case(wrapping)
    before = value.request.model_copy(deep=True)
    error = await fail(value, stream)
    assert value.raw.sent[0][0] is not value.request
    result = value.wrapped.recover_context_overflow(value.request, error,
        context_estimate=600_000, request_options={'temperature': 0.3})
    assert result is not None
    assert result['context_token_budget'] == 300_000
    assert value.request == before
    assert len(value.raw.sent) == 1  # Feedback does not perform inference or replay tools.
    assert value.wrapped.recover_context_overflow(value.request, error,
        context_estimate=600_000, request_options={'temperature': 0.3}) is None


@pytest.mark.parametrize('changed', ['copy', 'error', 'messages', 'options', 'selection', 'adapter'])
async def test_changed_or_unowned_feedback_cannot_be_forwarded(changed):
    value = case('selected_surface')
    error = await fail(value, False)
    request, options = value.request, {'temperature': 0.3}
    if changed == 'copy':
        request = request.model_copy()
    elif changed == 'error':
        error = ContextLengthError('Another error', provider='fixture')
    elif changed == 'messages':
        request.messages.append(Message(role='user', content='Later input.'))
    elif changed == 'options':
        options['temperature'] = 0.9
    elif changed == 'selection':
        value.wrapped.selection['model'] = 'another-model'
    else:
        replacement = SurfaceProvider(value.raw, Delivery())
        value.wrapped.execution_adapter = lambda _: replacement
    assert value.wrapped.recover_context_overflow(request, error,
        context_estimate=600_000, request_options=options) is None
    assert not value.raw.consumed
    assert len(value.raw.sent) == 1


@pytest.mark.parametrize('wrapping', ['selected', 'surface', 'selected_surface'])
async def test_visible_stream_output_prevents_feedback_forwarding(wrapping):
    value = case(wrapping, emitted=True)
    error = await fail(value, True)
    assert value.wrapped.recover_context_overflow(value.request, error,
        context_estimate=600_000, request_options={'temperature': 0.3}) is None
    assert not value.raw.consumed


@pytest.mark.parametrize('stream', [False, True])
async def test_native_feedback_crosses_existing_transport_wrapper_without_generic_retry(stream):
    from amplifier_web.request_recovery import RecoveringProvider
    value = case('selected_surface')
    coordinator = SimpleNamespace(get_capability=lambda _: None)
    value.surface.original = RecoveringProvider(value.raw, coordinator)
    error = await fail(value, stream)
    assert value.wrapped.recover_context_overflow(value.request, error,
        context_estimate=600_000, request_options={'temperature': 0.3}) is not None
    assert len(value.raw.sent) == 1


async def test_abandoned_feedback_is_bounded_and_eviction_releases_originals():
    import gc
    import weakref
    value = case('selected_surface')
    first, old_error = None, None
    for index in range(8):
        value.request = ChatRequest(messages=[Message(role='user', content=f'Input {index}')])
        value.raw.error = ContextLengthError(f'Overflow {index}', provider='fixture')
        error = await fail(value, False)
        if index == 0:
            first, old_error = weakref.ref(value.request), error
        # Provider fixture must not itself keep the evicted input alive.
        value.raw.sent.clear()
        assert len(value.wrapped._overflow_feedback.entries) <= 4
        assert len(value.surface._overflow_feedback.entries) <= 4
    # Exceptions carry traceback references; external error ownership is separate.
    old_error.__traceback__ = None
    gc.collect()
    assert first() is None


def test_optional_feedback_protocol_is_absent_when_provider_does_not_support_it():
    raw = SimpleNamespace()
    assert not hasattr(SurfaceProvider(raw, Delivery()), 'recover_context_overflow')
    assert not hasattr(SelectedProvider(raw, {'model': 'selected'}), 'recover_context_overflow')


async def test_explicit_new_budget_invalidates_previous_error_binding():
    value = case('selected_surface')
    error = await fail(value, False)
    await value.wrapped.request_budget(value.request, context_estimate=600_000)
    assert value.wrapped.recover_context_overflow(value.request, error,
        context_estimate=600_000, request_options={'temperature': 0.3}) is None
    assert not value.raw.consumed


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('changed', ['original', 'prepared', 'selection', 'options'])
async def test_dispatch_inflight_changes_cannot_become_the_feedback_snapshot(stream, changed):
    import asyncio
    value = case('selected_surface')
    entered, release = asyncio.Event(), asyncio.Event()
    options = {'custom': {'value': 1}}

    async def suspended(request, **kwargs):
        value.raw.sent.append((request, kwargs))
        entered.set()
        await release.wait()
        raise value.raw.error

    value.raw.complete = suspended

    async def invoke():
        if stream:
            async for _ in value.wrapped.stream(value.request, **options):
                pass
        else:
            await value.wrapped.complete(value.request, **options)

    pending = asyncio.create_task(invoke())
    await asyncio.wait_for(entered.wait(), timeout=1)
    if changed == 'original':
        value.request.max_output_tokens = 7
    elif changed == 'prepared':
        value.raw.sent[-1][0].max_output_tokens = 7
    elif changed == 'selection':
        value.wrapped.selection['model'] = 'changed-selection'
    else:
        options['custom']['value'] = 2
    release.set()
    with pytest.raises(ContextLengthError) as caught:
        await pending
    assert value.wrapped.recover_context_overflow(value.request, caught.value,
        context_estimate=600_000, request_options=options) is None
    assert not value.raw.consumed and len(value.raw.sent) == 1


async def test_equal_python_values_do_not_hide_option_type_mutation():
    value = case('selected_surface')
    with pytest.raises(ContextLengthError) as caught:
        await value.wrapped.complete(value.request, custom={'value': 1})
    assert value.wrapped.recover_context_overflow(value.request, caught.value,
        context_estimate=600_000, request_options={'custom': {'value': True}}) is None
    assert not value.raw.consumed


@pytest.mark.parametrize('size', [100, 1_000_000])
def test_feedback_witness_keeps_only_fixed_hashes_not_request_body_copies(size, monkeypatch):
    from amplifier_web import overflow_feedback
    raw = Provider()
    request = ChatRequest(messages=[Message(role='user', content='X' * size)])
    original = overflow_feedback.copy.deepcopy

    def copy_small(value):
        assert not isinstance(value, ChatRequest)
        return original(value)

    monkeypatch.setattr(overflow_feedback.copy, 'deepcopy', copy_small)
    feedback = overflow_feedback.OverflowFeedback()
    row = feedback.capture(request, request, {}, {}, raw)
    assert len(row.original_snapshot) == len(row.prepared_snapshot) == 32
    assert row.original is request and row.prepared is request
    assert feedback.entries == []  # Success does not retain a historical binding.


async def test_real_anthropic_private_server_feedback_through_both_host_projections():
    import json
    from anthropic import _base_client
    module = pytest.importorskip('amplifier_module_provider_anthropic')
    sdk_httpx = getattr(_base_client, 'httpx2', None) or _base_client.httpx
    requests = []

    def transport(request):
        requests.append(request)
        if request.method == 'GET':
            return sdk_httpx.Response(200, json={
                'id': 'claude-fable-5-2', 'type': 'model', 'display_name': 'Fixture',
                'created_at': '2026-11-09T00:00:00Z'})
        body = json.loads(request.content)
        assert body['model'] == 'claude-fable-5-2'
        return sdk_httpx.Response(400, headers={'request-id': 'synthetic-overflow-1'}, json={
            'type': 'error', 'error': {'type': 'invalid_request_error',
                'message': 'prompt is too long: 1005207 tokens > 1000000 maximum'}})

    sdk = module.AsyncAnthropic(api_key='synthetic-not-live', max_retries=0,
        http_client=sdk_httpx.AsyncClient(transport=sdk_httpx.MockTransport(transport)))
    raw = module.AnthropicProvider(api_key='synthetic-not-live', config={
        'default_model': 'claude-sonnet-5', 'use_streaming': False,
        'max_retries': 0, 'enable_prompt_caching': False, 'enable_1m_context': True})
    raw._client = sdk
    raw.coordinator = SimpleNamespace(hooks=SimpleNamespace(emit=_ignore))
    surface = SurfaceProvider(raw, Delivery())
    wrapped = SelectedProvider(raw, {
        'model': 'claude-fable-5-2', 'effort': 'high', 'max_output_tokens': 128_000},
        lambda _: surface)
    request = ChatRequest(messages=[Message(role='user', content='Synthetic task.')])
    try:
        with pytest.raises(ContextLengthError) as caught:
            await wrapped.complete(request)
        decision = wrapped.recover_context_overflow(request, caught.value,
            context_estimate=600_000, request_options={})
        assert decision is not None
        assert decision['input_limit_tokens'] == 1_000_000
        assert 0 < decision['context_token_budget'] < 600_000
        assert wrapped.recover_context_overflow(request, caught.value,
            context_estimate=600_000, request_options={}) is None
        assert sum(item.method == 'POST' for item in requests) == 1
    finally:
        await sdk.close()


@pytest.mark.parametrize('size', [100, 4_000_000])
async def test_successful_dispatch_witness_work_against_disabled_baseline(size, monkeypatch):
    import time
    import tracemalloc
    from amplifier_web.overflow_feedback import OverflowFeedback
    from test_context_inspection import evidence

    class Success(Provider):
        async def complete(self, request, **options):
            return 'done'

    records = []
    encode = ChatRequest.model_dump_json
    capture = OverflowFeedback.capture
    for enabled in (False, True):
        raw = Success()
        surface = SurfaceProvider(raw, Delivery())
        selected = SelectedProvider(raw, {'model': 'selected'}, lambda _: surface)
        request = ChatRequest(messages=[Message(role='user', content='X' * size)])
        work = {'encodings': 0, 'characters': 0}

        def counted(value, *args, **kwargs):
            text = encode(value, *args, **kwargs)
            work['encodings'] += 1
            work['characters'] += len(text)
            return text

        with monkeypatch.context() as patch:
            patch.setattr(ChatRequest, 'model_dump_json', counted)
            if not enabled:
                patch.setattr(OverflowFeedback, 'capture', lambda *a, **kw: None)
            else:
                patch.setattr(OverflowFeedback, 'capture', capture)
            tracemalloc.start()
            started = time.perf_counter()
            assert await selected.complete(request) == 'done'
            elapsed = time.perf_counter() - started
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        assert not selected._overflow_feedback.entries and not surface._overflow_feedback.entries
        records.append({'enabled': enabled, **work, 'elapsedSeconds': elapsed,
                        'retainedBytes': current, 'peakBytes': peak})
    assert records[0]['encodings'] == 0
    assert records[1]['encodings'] == 4
    assert records[1]['characters'] <= (size + 1000) * 4
    evidence(f'overflow-witness-work-{size}', {'bodyCharacters': size, 'measurements': records})


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('cancelled', [False, True])
async def test_optional_feedback_lookup_failure_cannot_replace_success_or_cancellation(stream, cancelled):
    import asyncio

    class DynamicProvider:
        def __getattr__(self, name):
            if name == 'recover_context_overflow':
                raise RuntimeError('Synthetic optional capability lookup failure')
            raise AttributeError(name)

        async def complete(self, request, **options):
            if cancelled:
                raise cancellation
            return 'done'

        async def stream(self, request, **options):
            yield await self.complete(request, **options)

    cancellation = asyncio.CancelledError('Original cancellation')
    raw = DynamicProvider()
    surface = SurfaceProvider(raw, Delivery())
    selected = SelectedProvider(raw, {'model': 'selected'}, lambda _: surface)
    request = ChatRequest(messages=[Message(role='user', content='Synthetic task.')])

    async def invoke():
        if stream:
            return [item async for item in selected.stream(request)]
        return await selected.complete(request)

    if cancelled:
        with pytest.raises(asyncio.CancelledError) as caught:
            await invoke()
        assert caught.value is cancellation
    else:
        assert await invoke() == (['done'] if stream else 'done')
    assert not selected._selected_requests and not selected._overflow_feedback.entries
    assert not surface.prepared and not surface._overflow_feedback.entries


async def _ignore(*args, **kwargs):
    pass