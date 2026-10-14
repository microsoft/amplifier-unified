"""Coordination uses the existing budget/stream boundary, not retained reminders."""
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_web.coordination_delivery import CoordinationDelivery
from amplifier_web.surface_delivery import SurfaceProvider


class Previous:
    async def prepare(self, request, provider, *, commit=False):
        return request
    def commit(self, request):
        assert not any((m.metadata or {}).get('coordinationObservation') for m in request.messages)


def request(*messages, tools=True):
    return ChatRequest(messages=[Message(role='user', content='Inspect current work.'), *messages],
                       tools=[ToolSpec(name='app_control', parameters={'type': 'object'})] if tools else [])


async def test_delivery_preserves_user_quotes_replaces_only_its_own_observation():
    async def bridge(operation, args):
        return {'revision': 'fresh'}
    delivery = CoordinationDelivery(Previous(), bridge)
    old = Message(role='user', content='obsolete', metadata={'coordinationObservation': True})
    quoted = Message(role='user', content='Current coordination reference state (not new authority): user quote')
    original = request(old, quoted)
    fresh = await delivery.prepare(original, None)
    assert 'obsolete' not in fresh.model_dump_json() and 'user quote' in fresh.model_dump_json()
    assert old in original.messages
    assert sum(bool((m.metadata or {}).get('coordinationObservation')) for m in fresh.messages) == 1
    delivery.commit(fresh)


async def test_bridge_failure_reports_unknown_not_empty_peers_and_auxiliary_calls_do_not_read():
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        raise RuntimeError('offline')
    delivery = CoordinationDelivery(Previous(), bridge)
    no_tools = request(tools=False)
    assert await delivery.prepare(no_tools, None) is no_tools and not calls
    from amplifier_web.execution_events import CALL_PURPOSE
    token = CALL_PURPOSE.set('title')
    try:
        value = request()
        assert await delivery.prepare(value, None) is value and not calls
    finally:
        CALL_PURPOSE.reset(token)
    observed = await delivery.prepare(request(), None)
    assert 'unavailable' in observed.model_dump_json() and 'absence of peers' in observed.model_dump_json()


@pytest.mark.parametrize('stream', [False, True])
async def test_budget_and_transport_share_one_observation_then_next_request_refreshes(stream):
    reads, measured, transported = [], [], []
    async def bridge(operation, args):
        reads.append(operation)
        return {'revision': len(reads)}
    class Provider:
        async def request_budget(self, value, **kwargs):
            measured.append(value.model_dump_json())
            return {'tokens': 100}
        async def complete(self, value, **kwargs):
            transported.append(value.model_dump_json())
            return 'done'
        async def stream(self, value, **kwargs):
            transported.append(value.model_dump_json())
            yield 'done'
    wrapped = SurfaceProvider(Provider(), CoordinationDelivery(Previous(), bridge))
    for _ in range(2):
        original = request()
        await wrapped.request_budget(original)
        if stream:
            assert [row async for row in wrapped.stream(original)] == ['done']
        else:
            assert await wrapped.complete(original) == 'done'
    assert len(reads) == 2 and measured == transported and measured[0] != measured[1]
