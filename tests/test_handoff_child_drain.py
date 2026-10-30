"""Native background results must settle before a pending checkout move releases."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip('amplifier_module_loop_live')
from amplifier_core import AmplifierSession, ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.handoff_boundary import inspect
from amplifier_web.handoff_drain import observe as observe_drain, witness, native_child
from amplifier_web.host.children import Children
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime import normalize_event
from amplifier_web.runtime_worker import Worker
from test_collaborative_workspaces import agent_action
from test_handoff_model_admission import app
from test_handoff_refusal import checkout
from test_task_continuity import controls


@pytest.mark.parametrize('child_phase', ['admitted-result', 'late-admission', 'already-waiting', 'in-transit', 'root-waiter'])
async def test_native_child_and_return_observation_settle_before_move(app, child_phase, tmp_path, monkeypatch):
    saved = app._session()
    sid = saved['id']
    target = await checkout(app, sid)
    first_finished, child_started, observed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events, admissions = [], []
    projection = None
    transmitted, root_second = asyncio.Event(), asyncio.Event()
    second_delivered = asyncio.Event()
    started_children = set()
    worker = Worker()
    control = controls(sid)
    telemetry = ExecutionEvents(sid, lambda event: observe(event))

    async def project(previous, event):
        if previous:
            await previous
        normalized = normalize_event(event, sid)
        if normalized:
            await app.on_runtime_event(*normalized)
        if event['type'] == 'generation.finished':
            first_finished.set()
        if event['type'] == 'input.delivered' and event.get('call_id') == 'child-two':
            second_delivered.set()
            app.coordination.notify()

    def observe(event):
        nonlocal projection
        events.append(event)
        telemetry.lifecycle(event)
        observe_drain(worker, event)
        projection = asyncio.create_task(project(projection, event))

    live = Runtime(session_id=sid, observer=observe)
    native = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {
            'configured_bundle': True, 'background_delegate': True,
            'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=sid)
    await native.initialize()
    native.coordinator.register_capability('live.runtime', live)
    worker.session, worker.runtime = native, live
    worker.controls = SimpleNamespace(persist=Mock())
    worker.parked = False
    registry = Children(live, SessionStore(tmp_path / 'children'), None)
    registry.sessions[sid] = native
    native.coordinator.register_capability('live.children', registry)

    async def reply(message):
        try:
            if message['operation'] == 'capacity.admit' and message['args']['call']['sessionId'] != sid:
                transmitted.set()
                if child_phase == 'in-transit':
                    await first_finished.wait()
            answer = await app.app_bridge(message['operation'], message['args'], sid)
            worker.bridges[message['id']].set_result(answer)
        except BaseException as exc:
            worker.bridges[message['id']].set_exception(exc)
    replies = []
    def publish(message):
        if message.get('op') == 'bridge':
            replies.append(asyncio.create_task(reply(message)))
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', publish)

    async def boundary(_sid, identity, **kwargs):
        from amplifier_web.handoff_drain import finish
        await finish(worker, kwargs.pop('receipt', None))
        return await inspect(worker, identity, **kwargs)
    app.runtime.handoff_boundary = boundary

    async def admitted(row):
        if projection:
            await projection
        answer = await worker.bridge('capacity.admit', {
            'call': row, '_nativeChild': native_child(worker, row), '_handoffDrain': witness(worker, row), '_generationId': (live.generation or {}).get('id'),
            '_runtimeSessionId': sid, 'waitForCapacity': True})
        admissions.append({'sessionId': row['sessionId'], 'answer': answer})
        return answer
    control.capacity.admit = admitted
    telemetry.admission_guard = control.capacity.guard

    class ChildProvider:
        def __init__(self, identity):
            self.identity = identity
        def get_info(self):
            return SimpleNamespace(id='fixture-child', defaults={'model': 'fixture-child'})
        async def complete(self, request):
            started_children.add(self.identity)
            if len(started_children) == (2 if child_phase == 'root-waiter' else 1):
                child_started.set()
            await first_finished.wait()
            if child_phase == 'root-waiter' and self.identity == 'native-second':
                from test_collaborative_workspaces import until
                await until(lambda: bool(getattr(app, '_model_admission_waiters', [])))
            return SimpleNamespace(usage={'input_tokens': 2, 'output_tokens': 1})
    child_providers = {identity: telemetry.instrument_provider(identity, ChildProvider(identity))
                       for identity in ('native-child', 'native-second')}

    async def child_body(identity, instruction, parent, agent, overlay, *, admission, **kwargs):
        telemetry.lifecycle({'type': 'child.updated', **{key: admission[key] for key in
            ('sessionId', 'parentSessionId', 'callId', 'runId')}, 'status': 'running'})
        if child_phase == 'already-waiting':
            await root_second.wait()
        if child_phase == 'late-admission':
            child_started.set()
            await first_finished.wait()
        await child_providers[identity].complete(SimpleNamespace(model='fixture-child'))
        telemetry.lifecycle({'type': 'child.updated', **{key: admission[key] for key in
            ('sessionId', 'parentSessionId', 'callId', 'runId')}, 'status': 'completed'})
        return ToolResult(success=True, output={'saved': 'child-result'})
    registry._execute_admitted = child_body

    class Delegate:
        name = 'delegate'
        description = 'An already admitted background child with a delayed return.'
        input_schema = {'type': 'object', 'properties': {}}
        async def execute(self, _input):
            from amplifier_module_loop_live.scope import JOB_CALL
            identity = 'native-second' if JOB_CALL.get() == 'child-two' else 'native-child'
            return await registry._execute(identity, 'finish existing task', native, 'fixture', {})

    class Move:
        name = 'move'
        description = 'Move after native work settles.'
        input_schema = {'type': 'object', 'properties': {}}
        receipt = None
        async def execute(self, _input):
            if projection:
                await projection
            self.receipt = (await worker.bridge('dispatch', {
                'action': 'worktree.handoff', 'id': 'move-after-child-start',
                '_runtimeSessionId': sid, '_generationId': live.generation['id'],
                '_inputBindings': [{'inputId': value, 'clientId': None} for value in live.generation['input_ids']],
                'args': {'sessionId': sid, 'id': target['id'], 'expectedExecutionRevision': 0}}))['result']
            return ToolResult(success=True, output={'phase': 'pending', 'handoffId': self.receipt['id']})

    class Provider:
        name = 'fixture'
        config = {}
        calls = 0
        def get_info(self):
            return SimpleNamespace(id='fixture', default_model='fixture')
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        async def complete(self, request, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(content=[], tool_calls=[ToolCall(id=call, name='delegate', arguments={'async': True})
                    for call in (['child-once', 'child-two'] if child_phase == 'root-waiter' else ['child-once'])])
            if self.calls == 2:
                root_second.set()
                if child_phase in {'already-waiting', 'in-transit'}:
                    await transmitted.wait()
                    if child_phase == 'already-waiting':
                        from test_collaborative_workspaces import until
                        await until(lambda: bool(getattr(app, '_model_admission_waiters', [])))
                    await move.execute({})
                    return ChatResponse(content=[TextBlock(text='The current work is saved.')])
                await child_started.wait()
                return ChatResponse(content=[], tool_calls=[ToolCall(id='move-once', name='move', arguments={})])
            if self.calls >= (3 if child_phase in {'already-waiting', 'in-transit'} else 4):
                observed.set()
            return ChatResponse(content=[TextBlock(text='The current work is saved.')])

    if child_phase == 'already-waiting':
        from test_assignments import limits
        await limits(app, saved, maxConcurrentCalls=1)
    provider, move = Provider(), Move()
    if child_phase == 'root-waiter':
        # Hold the real host capacity waiter until both native results have
        # joined its generation. Only the availability timing is controlled.
        original_admission = app.assignments.admission
        def capacity_after_both(session, row):
            result = original_admission(session, row)
            if first_finished.is_set() and row.get('sessionId') == sid and not second_delivered.is_set():
                return {**result, 'allowed': False, 'waiting': True, 'reasons': ['Fixture capacity is occupied.']}
            return result
        monkeypatch.setattr(app.assignments, 'admission', capacity_after_both)
    telemetry.instrument_provider(sid, provider)
    await native.coordinator.mount('providers', provider, name='fixture')
    await native.coordinator.mount('tools', Delegate(), name='delegate')
    await native.coordinator.mount('tools', move, name='move')
    task = asyncio.create_task(native.execute(''))
    try:
        await live.wait_for(lambda event: event['type'] == 'session.ready', 2)
        await live.submit(Input('user', 'Start the child, move this checkout, then retain its result.', id='original-input'))
        try:
            await asyncio.wait_for(observed.wait(), 5)
        except TimeoutError:
            pytest.fail(str({'admissions': admissions, 'events': [event for event in events
                if event['type'] in {'generation.started', 'generation.finished', 'generation.detached', 'job.returned', 'job.cancelled'}],
                'nativeError': repr(task.exception()) if task.done() and not task.cancelled() else None}))
        assert provider.calls == (3 if child_phase in {'already-waiting', 'in-transit'} else 4)
        assert not any(event['type'] in {'generation.failed', 'generation.detached', 'job.cancelled'} for event in events), str({'admissions': admissions, 'events': [e for e in events if e['type'] in {'generation.failed', 'generation.detached', 'job.cancelled', 'job.returned'}], 'binding': move.receipt.get('finishingTurn')})
        assert sum(event['type'] == 'job.returned' for event in events) == (2 if child_phase == 'root-waiter' else 1)
        if child_phase == 'root-waiter':
            assert second_delivered.is_set() and app._model_admission_waiters == []
        assert app.worktrees.read(sid, move.receipt['id'])['phase'] == 'pending'
        app.runtime.quiesce_for_handoff.assert_not_awaited()
    finally:
        if not live.closed:
            await live.submit(Input('stop', target='cancel'))
        await asyncio.gather(asyncio.wait_for(task, 2), return_exceptions=True)
        if projection:
            await projection
        await asyncio.gather(*replies, return_exceptions=True)
        await control.close()
        await native.cleanup()
