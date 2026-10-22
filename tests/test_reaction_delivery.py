"""Captured fake provider requests; never contacts an account or changes history."""
import copy
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_web.reaction_delivery import ReactionDelivery
from amplifier_web.surface_delivery import SurfaceProvider


class Previous:
    async def prepare(self, request, provider, *, commit=False):
        return request

    def commit(self, request):
        assert not any((m.metadata or {}).get('reactionObservation') for m in request.messages)


def request():
    return ChatRequest(messages=[Message(role='user', content='Original input')],
                       tools=[ToolSpec(name='app_control', parameters={'type': 'object'})])


@pytest.mark.parametrize('stream', [False, True])
async def test_future_request_sees_committed_reaction_without_changing_inflight_request(stream):
    state = {'sessionId': 's', 'revision': 0, 'items': []}
    reads, budgets, transported = [], [], []

    async def bridge(operation, args):
        assert operation == 'message.reactions.context' and args == {}
        reads.append(operation)
        return copy.deepcopy(state)

    class Provider:
        async def request_budget(self, value, **kwargs):
            budgets.append(value.model_dump_json())
            return {'tokens': 20}

        async def complete(self, value, **kwargs):
            transported.append(value.model_dump_json())
            return 'done'

        async def stream(self, value, **kwargs):
            transported.append(value.model_dump_json())
            yield 'done'

    provider = SurfaceProvider(Provider(), ReactionDelivery(Previous(), bridge))
    original = request()
    await provider.request_budget(original)
    state.update(revision=1, items=[{'messageId': 'm', 'reactions': ['\u2764\ufe0f'], 'excerpt': '<instruction>untrusted</instruction>'}])
    if stream:
        assert [value async for value in provider.stream(original)] == ['done']
    else:
        await provider.complete(original)
    assert transported[0] == budgets[0]  # Already prepared request is not retrofitted.
    assert 'reactionObservation' not in transported[0]
    for revision, items in [(1, state['items']), (2, [])]:
        state.update(revision=revision, items=items)
        current = request()
        await provider.request_budget(current)
        await provider.complete(current)
        assert 'reactionObservation' in transported[-1]
        assert 'not a new instruction' in transported[-1]
        assert len(current.messages) == 1
    assert budgets == transported and len(reads) == 3
    assert 'untrusted</instruction>' in transported[1] and 'untrusted</instruction>' not in transported[2]
    assert original.model_dump_json() == request().model_dump_json()


async def test_refresh_replaces_only_own_observation_and_auxiliary_calls_do_not_read():
    from amplifier_web.execution_events import CALL_PURPOSE
    calls = []

    async def bridge(operation, args):
        calls.append(operation)
        raise RuntimeError('Unavailable')

    delivery = ReactionDelivery(Previous(), bridge)
    value = request()
    value.messages.append(Message(role='user', content='Old snapshot', metadata={'reactionObservation': True}))
    value.messages.append(Message(role='user', content='Quoted saved reactions'))
    fresh = await delivery.prepare(value, None)
    assert 'Old snapshot' not in fresh.model_dump_json()
    assert 'Quoted saved reactions' in fresh.model_dump_json()
    assert 'unavailable' in fresh.model_dump_json()
    token = CALL_PURPOSE.set('title')
    try:
        assert await delivery.prepare(value, None) is value
    finally:
        CALL_PURPOSE.reset(token)
    assert len(calls) == 1
    delivery.commit(fresh)


async def test_legacy_saved_reaction_without_revision_is_still_visible():
    async def bridge(operation, args):
        return {'sessionId': 's', 'revision': 0, 'items': [{'messageId': 'm', 'reactions': ['check']}]}
    fresh = await ReactionDelivery(Previous(), bridge).prepare(request(), None)
    assert 'reactionObservation' in fresh.model_dump_json()
    assert 'check' in fresh.model_dump_json()
