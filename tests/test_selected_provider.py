from dataclasses import dataclass, replace
import asyncio

import pytest

from amplifier_web.host.session import SelectedProvider


@dataclass
class Request:
    model: str | None = None
    reasoning_effort: str | None = None
    max_output_tokens: int | None = None

    def model_copy(self, update):
        return replace(self, **update)


class Provider:
    default_model = 'bundle-model'

    async def complete(self, request, **kwargs):
        self.request, self.kwargs = request, kwargs
        return 'done'


class StreamingProvider(Provider):
    async def stream(self, request, **kwargs):
        self.request, self.kwargs = request, kwargs
        yield 'first'
        yield 'last'


async def test_pin_applies_to_complete_keyword_and_request_without_mutating_provider():
    provider = Provider()
    selected = SelectedProvider(provider, {'model': 'pinned-model', 'effort': 'high', 'max_output_tokens': 321})
    request = Request()
    assert not hasattr(selected, 'stream')
    assert await selected.complete(request, model='stale', reasoning_effort='low', tools=['echo']) == 'done'
    assert provider.request == Request('pinned-model', 'high', 321)
    assert provider.kwargs == {'model': 'pinned-model', 'reasoning_effort': 'high', 'tools': ['echo']}
    assert request == Request() and provider.default_model == 'bundle-model'
    # Another session using the original provider retains its own selection.
    await provider.complete(request)
    assert provider.request.model is None and not provider.kwargs


async def test_stream_pin_applies_identical_overrides_and_preserves_iterator():
    provider = StreamingProvider()
    selected = SelectedProvider(provider, {'model': 'pinned-model', 'effort': 'high', 'max_output_tokens': 321})
    request = Request()
    assert hasattr(selected, 'stream')
    assert [chunk async for chunk in selected.stream(request, tools=['echo'])] == ['first', 'last']
    assert provider.request == Request('pinned-model', 'high', 321)
    assert provider.kwargs == {'model': 'pinned-model', 'reasoning_effort': 'high', 'tools': ['echo']}
    assert request == Request() and provider.default_model == 'bundle-model'


async def test_budget_only_wrapper_preserves_automatic_model_and_effort():
    provider = StreamingProvider()
    selected = SelectedProvider(provider, {'max_output_tokens': 321})
    request = Request('routed-model', 'low')
    assert [chunk async for chunk in selected.stream(request)] == ['first', 'last']
    assert provider.request == Request('routed-model', 'low', 321)
    assert provider.kwargs == {}


async def test_surface_budget_preserves_synchronous_optional_protocol():
    from amplifier_web.surface_delivery import SurfaceProvider
    seen=[]
    class BudgetProvider(Provider):
        def request_budget(self,request,**kwargs):
            seen.append((request,kwargs))
            return {'context_token_budget':12} if len(seen)==1 else None
    class Delivery:
        async def prepare(self,request,provider):return request
    original=BudgetProvider()
    selected=SelectedProvider(original,{'model':'pinned-model','effort':'high'},
        lambda provider:SurfaceProvider(provider,Delivery()))
    assert await selected.request_budget(Request(),context_estimate=20)=={'context_token_budget':12}
    assert await selected.request_budget(Request(),context_estimate=20) is None
    assert all(request.model=='pinned-model' and kwargs['context_estimate']==20 for request,kwargs in seen)


def surface_selection():
    from amplifier_core.message_models import ChatRequest, Message, ToolSpec
    from amplifier_web.surface_delivery import SurfaceDelivery, SurfaceProvider

    lookups, budgets, dispatched, commits, revalidations = [], [], [], [], []

    async def bridge(operation, args):
        assert operation == 'context.manifest'
        lookups.append(operation)
        return {'surfaces': [], 'inputIds': ['input-one'], 'unavailable': True}

    class Delivery(SurfaceDelivery):
        async def revalidate(self, request):
            revalidations.append(request)
            return request

        def commit(self, request):
            commits.append(request)
            super().commit(request)

    class Provider:
        def request_budget(self, request, **kwargs):
            budgets.append((request, kwargs))
            return {'fits': True}

        async def complete(self, request, **kwargs):
            dispatched.append((request, kwargs))
            return 'done'

        async def stream(self, request, **kwargs):
            dispatched.append((request, kwargs))
            yield 'done'

    original = Provider()
    wrapped = SurfaceProvider(original, Delivery(bridge))
    selected = SelectedProvider(original, {'model': 'pinned-model', 'effort': 'high', 'max_output_tokens': 321},
                                lambda provider: wrapped)
    request = ChatRequest(messages=[Message(role='user', content='Current input')],
                          tools=[ToolSpec(name='app_control', parameters={'type': 'object'})])
    return selected, request, lookups, budgets, dispatched, commits, revalidations


@pytest.mark.parametrize('stream', [False, True])
async def test_selected_budget_and_dispatch_share_one_surface_observation(stream):
    selected, request, lookups, budgets, dispatched, commits, revalidations = surface_selection()
    # Repeated budget inspection does not consume the observation.
    await selected.request_budget(request, context_estimate=20)
    await selected.request_budget(request, context_estimate=20)
    assert not commits and not revalidations
    if stream:
        assert [chunk async for chunk in selected.stream(request)] == ['done']
    else:
        assert await selected.complete(request) == 'done'
    assert len(lookups) == len(commits) == len(revalidations) == 1
    assert dispatched[0][0] is budgets[0][0] is budgets[1][0]
    assert dispatched[0][0].model == 'pinned-model'
    assert dispatched[0][0].reasoning_effort == 'high'
    assert dispatched[0][0].max_output_tokens == 321
    assert dispatched[0][1] == {'model': 'pinned-model', 'reasoning_effort': 'high'}
    assert len(request.messages) == 1 and request.model is None
    # Reusing the caller's object for another dispatch still refreshes context.
    await selected.complete(request)
    assert len(lookups) == len(commits) == len(revalidations) == 2


@pytest.mark.parametrize('changed', ['model', 'effort', 'max_output_tokens', 'message', 'tools', 'new_request'])
async def test_selected_preparation_invalidates_changed_inputs_or_selection(changed):
    from amplifier_core.message_models import ToolSpec
    selected, request, lookups, budgets, dispatched, commits, _ = surface_selection()
    await selected.request_budget(request, context_estimate=20)
    if changed in {'model', 'effort', 'max_output_tokens'}:
        selected.selection[changed] = 123 if changed == 'max_output_tokens' else 'changed'
    elif changed == 'message':
        request.messages[0].content = 'New input'
    elif changed == 'tools':
        request.tools.append(ToolSpec(name='new_tool', parameters={'type': 'object'}))
    else:
        request = request.model_copy()
    await selected.complete(request)
    assert len(lookups) == 2 and len(commits) == 1
    assert dispatched[0][0] is not budgets[0][0]
    if changed == 'message':
        assert dispatched[0][0].messages[0].content == 'New input'
    if changed in {'model', 'effort', 'max_output_tokens'}:
        key = 'reasoning_effort' if changed == 'effort' else changed
        assert getattr(dispatched[0][0], key) == selected.selection[changed]


async def test_cancelled_budget_does_not_reuse_its_prepared_observation():
    selected, request, lookups, _, _, commits, _ = surface_selection()
    entered = asyncio.Event()
    budget = selected.original.request_budget

    async def waiting(*args, **kwargs):
        entered.set()
        await asyncio.Future()

    selected.original.request_budget = waiting
    task = asyncio.create_task(selected.request_budget(request, context_estimate=20))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    selected.original.request_budget = budget
    await selected.request_budget(request, context_estimate=20)
    await selected.complete(request)
    assert len(lookups) == 2 and len(commits) == 1


def test_selected_budget_keeps_synchronous_provider_protocol():
    class BudgetProvider(Provider):
        def request_budget(self, request, **kwargs):
            return {'model': request.model, **kwargs}
    selected = SelectedProvider(BudgetProvider(), {'model': 'selected'})
    assert selected.request_budget(Request(), context_estimate=20) == {
        'model': 'selected', 'context_estimate': 20}

@pytest.mark.parametrize('surface', [False, True])
async def test_strict_budget_protocol_receives_selection_in_request_options(surface):
    from amplifier_web.surface_delivery import SurfaceProvider
    class AnthropicStyleProvider(Provider):
        async def request_budget(self, request, *, context_estimate, request_options=None):
            self.budget_request, self.options = request, request_options
            assert context_estimate == 20
            return {'context_token_budget': 100}
    class Delivery:
        async def prepare(self, request, provider): return request
        def commit(self, request): pass
    provider = AnthropicStyleProvider()
    selected = SelectedProvider(provider, {'model': 'fable-model', 'effort': 'high'},
        (lambda original: SurfaceProvider(original, Delivery())) if surface else None)
    options = {'model': 'stale', 'reasoning_effort': 'low', 'temperature': 0.3}
    request = Request()
    assert await selected.request_budget(request, context_estimate=20, request_options=options) == {'context_token_budget': 100}
    assert provider.options == {'model': 'fable-model', 'reasoning_effort': 'high', 'temperature': 0.3}
    assert provider.budget_request == Request('fable-model', 'high')
    assert options['model'] == 'stale' and request == Request()
    await selected.complete(request)
    assert provider.request.model == 'fable-model' and provider.kwargs['model'] == 'fable-model'


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('strict_budget', [False, True])
@pytest.mark.parametrize('purpose, requested, expected', [
    ('context-compaction', 'low', 'low'),
    ('context-compaction', 'none', 'none'),
    ('context-compaction', None, 'xhigh'),
    ('other-auxiliary', 'low', 'xhigh'),
    (None, 'low', 'xhigh'),
])
async def test_summary_effort_agrees_across_budget_and_dispatch(stream, strict_budget, purpose, requested, expected):
    from amplifier_core.message_models import ChatRequest

    class BudgetProvider(StreamingProvider):
        def request_budget(self, request, **kwargs):
            self.budget_request, self.options = request, kwargs
            return {'fits': True}

    class StrictBudgetProvider(BudgetProvider):
        def request_budget(self, request, *, context_estimate, request_options=None):
            self.budget_request, self.options = request, request_options
            return {'fits': True}

    provider = StrictBudgetProvider() if strict_budget else BudgetProvider()
    choices = {'model': 'pinned-model', 'effort': 'xhigh'}
    selected = SelectedProvider(provider, choices)
    request = ChatRequest(messages=[], reasoning_effort=requested,
                          metadata={'purpose': purpose} if purpose else {})
    before = request.model_copy(deep=True)
    options = {'model': 'stale', 'reasoning_effort': 'high'}
    budget_args = {'request_options': options} if strict_budget else dict(options)
    assert selected.request_budget(request, context_estimate=20, **budget_args) == {'fits': True}
    if stream:
        assert [chunk async for chunk in selected.stream(request, **options)] == ['first', 'last']
    else:
        assert await selected.complete(request, **options) == 'done'
    assert provider.request is provider.budget_request
    assert provider.request.reasoning_effort == provider.options['reasoning_effort'] == provider.kwargs['reasoning_effort'] == expected
    assert provider.request.model == provider.options['model'] == provider.kwargs['model'] == 'pinned-model'
    assert request == before
    assert options == {'model': 'stale', 'reasoning_effort': 'high'}
    assert choices == {'model': 'pinned-model', 'effort': 'xhigh'}


async def test_summary_effort_reaches_keyword_providers_without_a_user_effort_pin():
    from amplifier_core.message_models import ChatRequest

    provider = Provider()
    selected = SelectedProvider(provider, {'model': 'pinned-model'})
    request = ChatRequest(messages=[], reasoning_effort='low', metadata={'purpose': 'context-compaction'})
    await selected.complete(request, reasoning_effort='high')
    assert provider.request.reasoning_effort == provider.kwargs['reasoning_effort'] == 'low'


@pytest.mark.parametrize('stream', [False, True])
async def test_summary_effort_keeps_budget_and_surface_dispatch_on_one_request(stream):
    selected, request, lookups, budgets, dispatched, commits, _ = surface_selection()
    request.metadata = {'purpose': 'context-compaction'}
    request.reasoning_effort = 'low'
    await selected.request_budget(request, context_estimate=20)
    if stream:
        assert [chunk async for chunk in selected.stream(request)] == ['done']
    else:
        assert await selected.complete(request) == 'done'
    assert len(lookups) == len(commits) == 1
    assert dispatched[0][0] is budgets[0][0]
    assert dispatched[0][0].reasoning_effort == 'low'
    assert dispatched[0][1]['reasoning_effort'] == budgets[0][1]['reasoning_effort'] == 'low'
    assert selected.selection['effort'] == 'high'
