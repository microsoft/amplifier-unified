"""Worker/host protocol cancellation keeps the live worker and exact call identity."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from amplifier_web import runtime_worker
from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker
from test_assignments import app, child, limits, until
from test_model_admission_wait import providers


@pytest.fixture
async def transport(app, monkeypatch):
    prior = app.runtime
    managers = []
    messages = []
    queued_to = None
    monkeypatch.setattr(runtime_worker, 'publish', lambda data: queued_to.feed_data(json.dumps(data).encode() + b'\n'))

    def connect(session, *, hold_reply=False):
        nonlocal queued_to
        worker = Worker()
        worker.runtime = SimpleNamespace(generation={'id': session['collaborationGeneration']['id']})
        stream = asyncio.StreamReader()
        queued_to = stream
        gate = asyncio.Event()
        if not hold_reply: gate.set()
        reached = asyncio.Event()
        manager = RuntimeManager(app_bridge=app.app_bridge)
        ready = asyncio.get_running_loop().create_future(); ready.set_result({})
        row = {'process': SimpleNamespace(stdout=stream, returncode=None), 'emit': app.on_runtime_event,
               'closing': False, 'ready': ready, 'runtime_id': session['id'],
               'inputId': session['collaborationGeneration']['inputIds'][-1],
               'pending': {}, 'inflight': set(), 'bridge_tasks': set()}
        manager.workers[session['id']] = row
        app.runtime = manager
        async def write(row, data):
            messages.append(data)
            reached.set()
            await gate.wait()
            await worker._command_serial(json.loads(json.dumps(data)))
        monkeypatch.setattr(manager, '_write', write)
        reader = asyncio.create_task(manager._read(session['id'], row))
        managers.append((row, reader, gate))
        return SimpleNamespace(worker=worker, row=row, stream=stream, gate=gate,
            reached=reached, messages=messages, manager=manager)
    yield connect
    app.runtime = prior
    for row, reader, gate in managers:
        gate.set()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        tasks=list(row['bridge_tasks'])
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def via_worker(provider, link, session):
    provider.control.capacity.admit = lambda call: link.worker.bridge('capacity.admit', {
        'waitForCapacity': True, 'call': call, '_runtimeSessionId': session['id'],
        '_generationId': session['collaborationGeneration']['id']})


async def waiting(app, providers, transport, *, hold_reply=False):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    await limits(app, source, maxConcurrentCalls=1)
    first, second = providers(source), providers(target)
    a = first.start('already-running')
    await until(lambda: first.called)
    link = transport(target, hold_reply=hold_reply)
    via_worker(second, link, target)
    b = second.start('should-not-run')
    await until(lambda: bool(getattr(app, '_model_admission_waiters', [])))
    return source, target, first, second, a, b, link


async def test_individual_worker_cancellation_removes_host_wait_without_stopping_worker(app, providers, transport):
    source, target, first, second, a, b, link = await waiting(app, providers, transport)
    b.cancel()
    with pytest.raises(asyncio.CancelledError): await b
    async with asyncio.timeout(.5):
        while app._model_admission_waiters: await asyncio.sleep(.01)
    first.release.set(); await a
    await asyncio.sleep(.03)
    assert not second.called and link.row['process'].returncode is None
    assert not link.worker.bridges and not link.row['bridge_tasks']
    assert app.assignments.usage(app.assignments.current(source))['calls'] == 1


async def test_cancel_after_host_admission_retains_unknown_reservation(app, providers, transport):
    source, target, first, second, a, b, link = await waiting(app, providers, transport, hold_reply=True)
    first.release.set(); await a
    await link.reached.wait()
    assert link.messages[-1]['result']['allowed']
    assert app.assignments.usage(app.assignments.current(source))['calls'] == 2
    b.cancel()
    with pytest.raises(asyncio.CancelledError): await b
    await until(lambda: any(n.get('phase') == 'outcome_unknown' for n in target['execution']['nodes']))
    assert not second.called and not app._model_admission_waiters
    usage = app.assignments.usage(app.assignments.current(source))
    assert usage['calls'] == 2
    assert usage['metrics']['grossTotalTokens']['value'] == 10
    assert not link.worker.bridges


async def test_unknown_cancel_id_cannot_cancel_the_real_wait(app, providers, transport):
    source, target, first, second, a, b, link = await waiting(app, providers, transport)
    link.stream.feed_data(json.dumps({'op':'bridge.cancel','id':'another-worker-call'}).encode()+b'\n')
    await asyncio.sleep(.05)
    assert not b.done() and len(app._model_admission_waiters) == 1
    first.release.set(); await a
    await until(lambda: second.called)
    second.release.set(); await b
    await until(lambda: not link.row.get('capacity_bridges'))
    assert second.called == ['should-not-run']


async def test_cancel_from_another_worker_row_cannot_address_this_wait(app, providers, transport):
    source, target, first, second, a, b, link = await waiting(app, providers, transport)
    identity = next(iter(link.worker.bridges))
    stream = asyncio.StreamReader()
    foreign = {**link.row, 'runtime_id': 'foreign-worker',
        'process': SimpleNamespace(stdout=stream, returncode=None),
        'pending': {}, 'bridge_tasks': set(), 'capacity_bridges': {}}
    link.manager.workers['foreign-worker'] = foreign
    reader = asyncio.create_task(link.manager._read('foreign-worker', foreign))
    try:
        stream.feed_data(json.dumps({'op':'bridge.cancel','id':identity}).encode()+b'\n')
        await asyncio.sleep(.05)
        assert not b.done() and len(app._model_admission_waiters) == 1
        first.release.set(); await a
        await until(lambda: second.called)
        second.release.set(); await b
        assert second.called == ['should-not-run']
    finally:
        reader.cancel(); await asyncio.gather(reader, return_exceptions=True)
        link.manager.workers.pop('foreign-worker')


async def test_late_cancel_after_consumption_cannot_change_running_or_completed_call(app, providers, transport):
    source, target, first, second, a, b, link = await waiting(app, providers, transport)
    identity = next(iter(link.worker.bridges))
    first.release.set(); await a
    await until(lambda: second.called)
    await until(lambda: not link.row.get('capacity_bridges'))
    for completed in (False, True):
        if completed:
            second.release.set(); await b
            await until(lambda: any(n.get('endedAt') for n in target['execution']['nodes'] if n['kind'] == 'llm'))
        link.stream.feed_data(json.dumps({'op':'bridge.cancel','id':identity}).encode()+b'\n')
        await asyncio.sleep(.03)
        assert all(n.get('phase') != 'outcome_unknown' for n in target['execution']['nodes'])
    assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 20


async def test_unrelated_bridge_cancellation_does_not_cancel_its_host_effect(app, transport):
    target = app.state['sessions'][0]
    link = transport(target)
    entered, finish = asyncio.Event(), asyncio.Event()
    done = []
    async def dispatch(operation, args, sid):
        entered.set(); await finish.wait(); done.append(operation); return {'saved':True}
    link.manager.app_bridge = dispatch
    request = asyncio.create_task(link.worker.bridge('dispatch', {'action':'fixture.mutation'}))
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError): await request
    assert link.row['bridge_tasks'] and not link.row.get('capacity_bridges')
    finish.set()
    await until(lambda: done)
    assert done == ['dispatch']
