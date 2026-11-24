"""Ordinary follow-ups borrow one observation and never commit an abandoned one."""
import asyncio

import pytest
from amplifier_core.message_models import Message

from amplifier_web.host.session import SelectedProvider
from amplifier_web.surface_delivery import SurfaceProvider
from amplifier_web.coordination_delivery import CoordinationDelivery
from test_coordination_delivery import Previous, request


def hosted_selection():
    observations, sent = [], []

    async def bridge(operation, args):
        observations.append(operation)
        return {}

    class Provider:
        async def complete(self, actual, **options):
            sent.append(actual)
            return 'done'

    provider = Provider()
    work = CoordinationDelivery(Previous(), bridge)
    surface = SurfaceProvider(provider, work)
    selected = SelectedProvider(provider, {'model': 'selected', 'max_output_tokens': 24000}, lambda _: surface)
    return selected, surface, work, sent, observations


async def test_borrowed_projection_is_consumed_once_with_selected_options_and_work():
    selected, surface, work, sent, observations = hosted_selection()
    original = request()
    assert not hasattr(selected, 'request_budget')
    async with selected.prepared_foreground(original) as projected:
        assert projected.max_output_tokens == 24000 and projected.model == 'selected'
        binding = work.work_request(projected)
        assert binding and not work.requests
        before = list(observations)
        assert await selected.complete(original) == 'done'
        assert observations == before and sent == [projected]
        assert list(work.requests.values()) == [binding]
        with pytest.raises(ValueError, match='changed|expired'):
            await selected.complete(original)
    assert not selected._selected_requests and not surface.prepared


@pytest.mark.parametrize('reason', ['decline', 'cancel', 'option', 'source', 'eviction'])
async def test_unsent_projection_leaves_peer_work_intact(reason):
    selected, surface, work, sent, _ = hosted_selection()
    # The Work owner has a bounded table. Even a full table must survive an
    # abandoned preview, including a preview that had generated its own receipt.
    for i in range(4):
        await work.prepare(request(Message(role='user', content=str(i))), surface.original)
    peers = list(work.requests.items())
    original = request()
    try:
        async with selected.prepared_foreground(original) as projected:
            assert work.work_request(projected) and list(work.requests.items()) == peers
            if reason == 'cancel':
                raise asyncio.CancelledError()
            if reason == 'option':
                selected.selection['max_output_tokens'] = 99999
            elif reason == 'source':
                original.messages.append(Message(role='user', content='superseded'))
            elif reason == 'eviction':
                surface.prepared.clear()
            if reason not in {'decline', 'cancel'}:
                with pytest.raises(ValueError, match='changed|expired'):
                    await selected.complete(original)
    except asyncio.CancelledError:
        assert reason == 'cancel'
    assert not sent and not selected._selected_requests and not surface.prepared
    assert list(work.requests.items()) == peers and work.work_request(projected) is None


async def test_cancellation_between_work_creation_and_outer_overlay_leaves_no_work():
    selected, surface, work, sent, _ = hosted_selection()
    normal = surface.delivery

    class Interrupted:
        def __getattr__(self, key):
            return getattr(normal, key)

        async def prepare(self, req, provider):
            self.observed = await normal.prepare(req, provider)
            raise asyncio.CancelledError()

    interrupted = Interrupted()
    surface.delivery = interrupted
    with pytest.raises(asyncio.CancelledError):
        async with selected.prepared_foreground(request()):
            pytest.fail('Cancelled during preparation')
    assert not work.requests and work.work_request(interrupted.observed) is None
    assert not selected._selected_requests and not surface.prepared and not sent


async def test_changed_final_observation_cannot_send_or_publish():
    selected, surface, work, sent, _ = hosted_selection()

    async def changed(prepared):
        return prepared.model_copy(update={'messages': [*prepared.messages, Message(role='user', content='new')]})

    work.revalidate = changed
    original = request()
    async with selected.prepared_foreground(original) as preview:
        with pytest.raises(ValueError, match='contents changed'):
            await selected.complete(original)
    assert not sent and not surface.prepared and work.work_request(preview) is None


@pytest.mark.parametrize('route', ['selected', 'surface'])
async def test_foreign_task_cannot_consume_or_discard_another_tasks_projection(route):
    selected, surface, _, sent, _ = hosted_selection()
    owner = selected if route == 'selected' else surface
    original = request()
    async with owner.prepared_foreground(original):
        with pytest.raises(ValueError, match='another preparation task'):
            await asyncio.create_task(owner.complete(original))
        assert not sent
        assert await owner.complete(original) == 'done'
    assert len(sent) == 1


async def test_change_in_selection_during_final_work_check_refuses_before_send():
    selected, surface, work, sent, _ = hosted_selection()
    async def revalidated(prepared):
        selected.selection['model'] = 'another'
        return prepared
    work.revalidate = revalidated
    original = request()
    async with selected.prepared_foreground(original):
        with pytest.raises(ValueError, match='Selected provider or options changed'):
            await selected.complete(original)
    assert not sent and not work.requests and not surface.prepared
