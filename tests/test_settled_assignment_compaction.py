"""After-turn context work crosses the worker bridge and real assignment gate."""
import asyncio
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_worker
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.settled_compaction import bind_scheduler
from test_assignments import app, start
from test_collaborative_workspaces import generation
from test_task_continuity import controls


@pytest.fixture
async def assigned_worker(app, monkeypatch):
    session = app.state['sessions'][0]
    sid = session['id']
    await start(app)
    await generation(app, session, 'maintenance-origin', ['input'])
    worker = runtime_worker.Worker()
    worker.session = SimpleNamespace(session_id=sid, coordinator=SimpleNamespace(get_capability=lambda key: None))
    worker.runtime = SimpleNamespace(session_id=sid, generation={'id': 'maintenance-origin'})
    control = controls(sid)
    telemetry = ExecutionEvents(sid, lambda event: None)
    telemetry.lifecycle({'type': 'generation.started', 'generation_id': 'maintenance-origin', 'initial_input_id': 'input'})
    telemetry.admission_guard = control.capacity.guard
    control.capacity.admit = worker.admit_model

    async def deliver(message):
        future = worker.bridges[message['id']]
        try:
            result = await app.app_bridge(message['operation'], message['args'], sid)
        except Exception as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)

    def publish(message):
        if message.get('op') == 'bridge':
            asyncio.create_task(deliver(message))

    monkeypatch.setattr(runtime_worker, 'publish', publish)

    async def finish():
        await app.on_runtime_event('runtime.generation', {'sessionId': sid, 'rootSessionId': sid,
            'generation_id': 'maintenance-origin', 'event': 'generation.finished', 'input_ids': ['input'],
            'disposition': 'manager_turn_finished', 'active_job_ids': []})
        worker.runtime.generation = None
        session['status'] = 'idle'

    try:
        yield session, telemetry, finish
    finally:
        await control.close()


@pytest.mark.parametrize('change', ['none', 'stop', 'configuration', 'new generation', 'ordinary', 'failed',
                                   'detached', 'replacement worker input'])
async def test_settled_compaction_owns_its_completed_turn(assigned_worker, change):
    session, telemetry, finish = assigned_worker
    entered = []
    ready = asyncio.Event()

    class Provider:
        def get_info(self):
            return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})

        async def complete(self, request):
            raise AssertionError('Unexpected ordinary call')

        async def compact_context(self, request):
            entered.append(True)
            return {'usage': {}}

    provider = telemetry.instrument_provider(session['id'], Provider())
    async def invoke():
        await ready.wait()
        await provider.compact_context(SimpleNamespace(model='fixture', metadata={'purpose': 'context-compaction'}))

    context = SimpleNamespace(schedule_settled_compaction=lambda: asyncio.create_task(invoke()))
    bind_scheduler(context, telemetry)
    task = asyncio.create_task(invoke()) if change == 'ordinary' else context.schedule_settled_compaction()
    await finish()
    if change == 'stop':
        session['interruptionRevision'] = 1
    if change == 'configuration':
        session['configurationPendingRevision'] = 1
    if change == 'new generation':
        session['collaborationGeneration']['id'] = 'new'
    if change == 'detached':
        session.pop('assignmentId', None)
        session.get('collaboration', {}).pop('assignmentId', None)
    if change == 'replacement worker input':
        replacement = 'replacement'
        telemetry.lifecycle({'type': 'generation.started', 'generation_id': replacement,
                             'initial_input_id': replacement})
        session['collaborationGeneration'].update(id=replacement, inputIds=[replacement], terminal=False)
    if change == 'failed':
        session['collaborationGeneration']['completed'] = False
    ready.set()
    if change == 'none':
        await task
        assert entered == [True]
    else:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not entered


async def test_settled_request_rechecks_ownership_while_waiting(app, assigned_worker, monkeypatch):
    session, telemetry, finish = assigned_worker
    entered = []
    ready = asyncio.Event()
    queued = asyncio.Event()

    def at_capacity(current, row):
        queued.set()
        return {'allowed': False, 'waiting': True, 'reasons': ['At assignment capacity']}

    monkeypatch.setattr(app.assignments, 'admission', at_capacity)

    async def invoke():
        await ready.wait()
        request = SimpleNamespace(metadata={'purpose': 'context-compaction'}, model='fixture')
        provider = SimpleNamespace(get_info=lambda: {'id': 'fixture', 'defaults': {'model': 'fixture'}})
        await telemetry.provider_call(session['id'], provider, request, lambda: entered.append(True))

    context = SimpleNamespace(schedule_settled_compaction=lambda: asyncio.create_task(invoke()))
    bind_scheduler(context, telemetry)
    task = context.schedule_settled_compaction()
    await finish()
    ready.set()
    await asyncio.wait_for(queued.wait(), 5)
    assert app._model_admission_waiters and session['status'] == 'idle'
    assert (session.get('activity') or {}).get('phase') != 'capacity'
    session['interruptionRevision'] = 1
    app.coordination.signal.event.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert not entered and not app._model_admission_waiters
