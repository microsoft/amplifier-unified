import asyncio
from types import SimpleNamespace

import pytest

from amplifier_web.preparation_context import OPERATIONS, PreparationContext
from test_automatic_history import app_factory


def request():
    return SimpleNamespace(tools=[SimpleNamespace(name='app_control')])


async def test_one_snapshot_per_preparation_and_fresh_reads_afterward():
    calls = []
    generation = 0
    async def bridge(operation, args):
        nonlocal generation
        calls.append((operation, args))
        if operation == 'context.prepare':
            generation += 1
            return {name: {'available': True, 'value': {'generation': generation, 'name': name}}
                    for name in OPERATIONS}
        return {'fresh': True}
    context = PreparationContext(bridge)
    class Delivery:
        async def prepare(self, request, provider, *, commit=False):
            # Existing delivery consumers use their own timeout tasks. The
            # request-scoped snapshot must work through those task boundaries.
            return [await asyncio.wait_for(context.read(name, {}), .1) for name in OPERATIONS]
    wrapped = context.wrap(Delivery())
    assert {row['generation'] for row in await wrapped.prepare(request(), None)} == {1}
    assert {row['generation'] for row in await wrapped.prepare(request(), None)} == {2}
    assert [operation for operation, _ in calls] == ['context.prepare', 'context.prepare']
    assert await context.read('memory.context', {'expected': {'old': True}}) == {'fresh': True}
    assert await context.read('context.manifest', {}) == {'fresh': True}


async def test_partial_failure_is_not_empty_or_a_second_serial_roundtrip():
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        return {'memory.context': {'available': False},
                'context.manifest': {'available': True, 'value': {'surfaces': []}}}
    context = PreparationContext(bridge)
    class Delivery:
        async def prepare(self, request, provider, *, commit=False):
            assert await context.read('context.manifest', {}) == {'surfaces': []}
            for name in OPERATIONS[1:]:
                with pytest.raises(ValueError, match='unavailable'):
                    await context.read(name, {})
    await context.wrap(Delivery()).prepare(request(), None)
    assert calls == ['context.prepare']


async def test_concurrent_preparations_do_not_borrow_each_others_snapshot():
    count = 0
    entered, release = asyncio.Event(), asyncio.Event()
    async def bridge(operation, args):
        nonlocal count
        count += 1
        return {name: {'available': True, 'value': count} for name in OPERATIONS}
    context = PreparationContext(bridge)
    class Delivery:
        async def prepare(self, request, provider, *, commit=False):
            first = await context.read('context.manifest', {})
            if first == 1:
                entered.set()
                await release.wait()
            else:
                release.set()
            return first, await context.read('memory.context', {})
    delivery = context.wrap(Delivery())
    first = asyncio.create_task(delivery.prepare(request(), None))
    await entered.wait()
    second = await delivery.prepare(request(), None)
    assert await first == (1, 1) and second == (2, 2)


async def test_cancelled_preparation_unwinds_without_replay_or_context_reuse():
    entered = asyncio.Event()
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        if operation == 'context.prepare':
            entered.set()
            await asyncio.Event().wait()
        return {'fresh': True}
    context = PreparationContext(bridge)
    class Delivery:
        async def prepare(self, *args, **kwargs):
            pytest.fail('Cancelled preparation must not continue into provider fitting')
    task = asyncio.create_task(context.wrap(Delivery()).prepare(request(), None))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.current.get() is None
    assert await context.read('context.manifest', {}) == {'fresh': True}
    assert calls == ['context.prepare', 'context.manifest']


async def test_host_snapshot_uses_existing_owners_and_preserves_partial_coverage(app_factory, monkeypatch):
    app = app_factory()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    original = app._app_bridge
    calls = []
    async def observe(operation, args, session_id, *, input_origin=None):
        calls.append((operation, args, input_origin))
        if operation == 'memory.context':
            raise OSError('fixture memory unavailable')
        return await original(operation, args, session_id, input_origin=input_origin)
    monkeypatch.setattr(app, '_app_bridge', observe)
    result = await app.app_bridge('context.prepare', {'_contextInputs': ['input'],
        '_contextBindings': [], '_inputClients': ['client']}, sid)
    assert set(result) == set(OPERATIONS)
    assert result['context.manifest']['value']['inputIds'] == ['input']
    assert result['message.reactions.context']['available'] is True
    assert result['coordination.current']['available'] is True
    assert result['memory.context'] == {'available': False}
    assert [name for name, _, _ in calls] == ['context.prepare', *OPERATIONS]
    assert all(origin == {'clientId': 'client', 'status': 'client'} for _, _, origin in calls)


@pytest.mark.parametrize('cancel', [False, True])
async def test_held_host_section_preserves_completed_context_and_unwinds(app_factory, monkeypatch, cancel):
    app = app_factory()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    original = app._app_bridge
    held, unwound = asyncio.Event(), asyncio.Event()
    async def observe(operation, args, session_id, *, input_origin=None):
        if operation == 'memory.context':
            held.set()
            try:
                await asyncio.Event().wait()
            finally:
                unwound.set()
        return await original(operation, args, session_id, input_origin=input_origin)
    monkeypatch.setattr(app, '_app_bridge', observe)
    calls = []
    async def bridge(operation, args):
        calls.append(operation)
        return await app.app_bridge(operation, args, sid)
    context = PreparationContext(bridge)
    class Delivery:
        async def prepare(self, *args, **kwargs):
            values = [await context.read(name, {}) for name in OPERATIONS[:-1]]
            assert all(isinstance(value, dict) for value in values)
            with pytest.raises(ValueError, match='unavailable'):
                await context.read('memory.context', {})
            return values
    task = asyncio.create_task(context.wrap(Delivery()).prepare(request(), None))
    await held.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        values = await task
        assert 'surfaces' in values[0] and 'work' in values[2]
    assert unwound.is_set() and calls == ['context.prepare']
