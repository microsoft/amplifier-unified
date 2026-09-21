from dataclasses import dataclass, replace

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
