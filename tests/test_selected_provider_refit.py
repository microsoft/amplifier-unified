"""Selected-model dispatch through real Work fitting and the installed loop adapter."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import Message
from amplifier_module_loop_streaming._request_refit import RefitProvider

from amplifier_web.coordination_delivery import CoordinationDelivery
from amplifier_web.host.session import SelectedProvider
from amplifier_web.request_refit import RequestFitChanged
from amplifier_web.surface_delivery import SurfaceProvider
from test_coordination_delivery import Previous, available_requirements, empty_discovery, request


def case(mode='changed_once', *, legacy_budget=False):
    state = SimpleNamespace(measured=[], transported=[], options=[], dispatch_options=[],
                            revalidations=0, refits=0, closed=0, waiting=asyncio.Event())
    scope = dict(rootSessionId='owner-1', runtimeSessionId='owner-1',
                 generationId='generation-1', inputIds=['input-1'],
                 inputCoverage='complete', stopRevision=0)
    required = available_requirements()

    async def bridge(operation, args):
        if operation == 'coordination.current':
            return {'work': {'available': True, 'required': required, 'discovery': empty_discovery()}}
        assert operation == 'work.invocation.revalidate'
        return {'current': True, 'effectAuthorized': False, 'required': required}

    class Delivery(CoordinationDelivery):
        async def revalidate(self, value):
            state.revalidations += 1
            if mode == 'changed_once' and state.revalidations == 1 or mode == 'changed_repeatedly':
                value.messages.append(Message(role='user', content='Synthetic visual became unavailable.'))
            return await super().revalidate(value)

    class Provider:
        async def request_budget(self, value, *, context_estimate=0, request_options=None):
            state.measured.append(value)
            state.options.append(copy.deepcopy(request_options))
            return {'estimated_input_tokens': 100, 'input_limit_tokens': 1000,
                    'context_token_budget': context_estimate}

        async def complete(self, value, **kwargs):
            state.transported.append(value)
            state.dispatch_options.append(copy.deepcopy(kwargs))
            if mode in {'provider_typed', 'after_stream_event'}:
                raise RequestFitChanged('content_changed')
            if mode == 'provider_unknown':
                raise ValueError('Synthetic transport outcome unknown')
            return 'done'

        async def stream(self, value, **kwargs):
            try:
                if mode == 'suspended':
                    yield 'started'
                    state.waiting.set()
                    await asyncio.Event().wait()
                if mode == 'after_stream_event':
                    yield 'already emitted'
                yield await self.complete(value, **kwargs)
            finally:
                state.closed += 1

    raw = Provider()
    if legacy_budget:
        async def budget(value, *, context_estimate=0, **kwargs):
            state.measured.append(value)
            state.options.append(copy.deepcopy(kwargs))
            return {'estimated_input_tokens': 100, 'input_limit_tokens': 1000,
                    'context_token_budget': context_estimate}
        raw.request_budget = budget
    delivery = Delivery(Previous(), bridge, origin_scope=lambda: copy.deepcopy(scope))
    surface = SurfaceProvider(raw, delivery)
    selected = SelectedProvider(raw, {'model': 'selected-model', 'effort': 'high',
                                     'max_output_tokens': 321}, lambda _: surface)
    return state, selected, surface, request()


async def dispatch(provider, value, stream):
    if stream:
        return [item async for item in provider.stream(value, temperature=0.3)]
    return await provider.complete(value, temperature=0.3)


def loop_adapter(selected, state):
    async def refit(value, error, options, *, streaming):
        state.refits += 1
        return await selected.refit_request(value, error=error, context_estimate=20,
                                            request_options=options) is not None
    # Same capability check as StreamingOrchestrator._execute_stream.
    return RefitProvider(selected, refit) if callable(getattr(selected, 'refit_request', None)) else selected


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('legacy_budget', [False, True])
async def test_selected_dispatch_refits_same_request_once_with_pinned_options(stream, legacy_budget):
    state, selected, surface, value = case(legacy_budget=legacy_budget)
    before = value.model_copy(deep=True)
    options = {'temperature': 0.3}
    await selected.request_budget(value, context_estimate=20,
                                  **(options if legacy_budget else {'request_options': options}))
    result = await dispatch(loop_adapter(selected, state), value, stream)
    assert result == (['done'] if stream else 'done')
    assert state.refits == 1 and len(state.measured) == 2 and len(state.transported) == 1
    assert state.transported[0] is state.measured[1]
    assert state.options == state.dispatch_options * 2
    sent = state.transported[0]
    assert (sent.model, sent.reasoning_effort, sent.max_output_tokens) == ('selected-model', 'high', 321)
    assert value == before
    assert not selected._selected_requests and not selected._refused_requests
    assert state.closed == int(stream)


@pytest.mark.parametrize('stream', [False, True])
async def test_only_exact_refused_request_error_and_execution_owner_can_refit(stream):
    state, selected, surface, value = case()
    await selected.request_budget(value, context_estimate=20)
    with pytest.raises(RequestFitChanged) as refused:
        await dispatch(selected, value, stream)
    error = refused.value
    assert await selected.refit_request(value.model_copy(), error=error) is None
    assert await selected.refit_request(value, error=RequestFitChanged('content_changed')) is None
    assert len(state.measured) == 1 and not state.transported
    assert await selected.refit_request(value, error=error) is not None
    assert await selected.refit_request(value, error=error) is None
    assert await dispatch(selected, value, stream) == (['done'] if stream else 'done')
    assert len(state.measured) == 2 and len(state.transported) == 1


@pytest.mark.parametrize('changed', ['model', 'effort', 'max_output_tokens', 'messages',
                                     'adapter', 'cached_adapter'])
@pytest.mark.parametrize('stream', [False, True])
async def test_refit_rejects_changed_selected_request_or_execution_adapter(stream, changed):
    state, selected, surface, value = case()
    await selected.request_budget(value, context_estimate=20)
    with pytest.raises(RequestFitChanged) as refused:
        await dispatch(selected, value, stream)
    cached_refit = selected.refit_request
    if changed in {'model', 'effort', 'max_output_tokens'}:
        selected.selection[changed] = 123 if changed == 'max_output_tokens' else 'changed'
    elif changed == 'messages':
        value.messages.append(Message(role='user', content='A later input'))
    else:
        replacement = SurfaceProvider(selected.original, surface.delivery)
        selected.execution_adapter = lambda _: replacement
    refit = cached_refit if changed == 'cached_adapter' else selected.refit_request
    assert await refit(value, error=refused.value) is None
    assert len(state.measured) == 1 and not state.transported
    assert not selected._selected_requests and not selected._refused_requests


@pytest.mark.parametrize('mode', ['changed_repeatedly', 'provider_unknown', 'provider_typed'])
@pytest.mark.parametrize('stream', [False, True])
async def test_one_refit_never_replays_sent_or_unknown_provider_work(stream, mode):
    state, selected, surface, value = case(mode)
    await selected.request_budget(value, context_estimate=20)
    with pytest.raises(ValueError):
        await dispatch(loop_adapter(selected, state), value, stream)
    assert state.refits == 1
    assert len(state.measured) == (2 if mode == 'changed_repeatedly' else 1)
    assert len(state.transported) == (0 if mode == 'changed_repeatedly' else 1)
    assert not selected._selected_requests and not selected._refused_requests


async def test_stream_with_visible_output_cannot_refit_and_closes_once():
    state, selected, surface, value = case('after_stream_event')
    await selected.request_budget(value, context_estimate=20)
    received = []
    with pytest.raises(RequestFitChanged):
        async for item in loop_adapter(selected, state).stream(value):
            received.append(item)
    assert received == ['already emitted']
    assert state.refits == 0 and len(state.transported) == 1 and state.closed == 1
    assert not selected._selected_requests and not selected._refused_requests


def test_refit_capability_is_absent_without_an_execution_adapter_protocol():
    class Provider:
        pass
    assert not hasattr(SelectedProvider(Provider(), {'model': 'selected-model'}), 'refit_request')


@pytest.mark.parametrize('cancel_pending', [False, True])
async def test_stream_consumer_close_or_cancellation_cleans_selected_request(cancel_pending):
    state, selected, surface, value = case('suspended')
    await selected.request_budget(value, context_estimate=20)
    iterator = selected.stream(value)
    assert await anext(iterator) == 'started'
    if cancel_pending:
        pending = asyncio.create_task(anext(iterator))
        await asyncio.wait_for(state.waiting.wait(), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        await iterator.aclose()
    assert state.closed == 1 and state.refits == 0 and not state.transported
    assert not selected._selected_requests and not selected._refused_requests


async def test_refusal_retention_is_bounded_and_evicted_identity_cannot_refit():
    state, selected, surface, first = case('changed_repeatedly')
    errors = []
    for value in [first] + [request() for _ in range(5)]:
        await selected.request_budget(value, context_estimate=20)
        with pytest.raises(RequestFitChanged) as refused:
            await selected.complete(value)
        errors.append(refused.value)
        assert len(selected._selected_requests) <= 4 and len(selected._refused_requests) <= 4
    assert await selected.refit_request(first, error=errors[0]) is None
    assert len(state.measured) == 6 and not state.transported


@pytest.mark.parametrize('failure', ['unavailable_budget', 'raised_budget'])
async def test_refit_failure_clears_retained_selected_request(failure):
    state, selected, surface, value = case()
    await selected.request_budget(value, context_estimate=20)
    with pytest.raises(RequestFitChanged) as refused:
        await selected.complete(value)
    if failure == 'unavailable_budget':
        selected.original.request_budget = None
        assert await selected.refit_request(value, error=refused.value) is None
    else:
        async def failed_budget(value, **kwargs):
            raise RuntimeError('Synthetic budget failure')
        selected.original.request_budget = failed_budget
        with pytest.raises(RuntimeError, match='Synthetic budget failure'):
            await selected.refit_request(value, error=refused.value)
    assert not selected._selected_requests and not selected._refused_requests
