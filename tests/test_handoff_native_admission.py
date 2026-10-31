"""Actual native loop, host admission and saved checkout boundary; no model service."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip('amplifier_module_loop_live')
from amplifier_core import AmplifierSession, ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.handoff_boundary import inspect
from amplifier_web.runtime import normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError
from test_collaborative_workspaces import agent_action
from test_handoff_model_admission import app
from test_handoff_refusal import checkout
from test_task_continuity import controls
from test_worktrees import settle


async def test_actual_native_turn_completes_handoff_without_self_wait(app, tmp_path):
    saved = app._session()
    sid = saved['id']
    target = await checkout(app, sid)
    checkpoint = tmp_path / 'confirmed-context.json'
    completed = asyncio.Event()
    events = []
    projection = None
    native_worker = Worker()
    control = controls(sid)
    telemetry = ExecutionEvents(sid, lambda event: observe(event))

    async def project(previous, event):
        if previous:
            await previous
        normalized = normalize_event(event, sid)
        if normalized:
            await app.on_runtime_event(*normalized)
        if event['type'] == 'generation.finished':
            completed.set()

    def observe(event):
        nonlocal projection
        events.append(event)
        telemetry.lifecycle(event)
        projection = asyncio.create_task(project(projection, event))

    live = Runtime(session_id=sid, observer=observe)
    native = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {
            'configured_bundle': True, 'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=sid)
    await native.initialize()
    native.coordinator.register_capability('live.runtime', live)
    native_worker.session, native_worker.runtime = native, live
    native_worker.controls = SimpleNamespace(persist=Mock())
    native_worker.parked = False

    async def boundary(_sid, identity, **kwargs):
        from amplifier_web.handoff_drain import finish
        await finish(native_worker, kwargs.pop('receipt', None))
        return await inspect(native_worker, identity, **kwargs)
    app.runtime.handoff_boundary = boundary

    async def admitted(row):
        if projection:
            await projection
        return await app.app_bridge('capacity.admit', {
            'call': row, '_generationId': live.generation['id'],
            '_runtimeSessionId': sid, 'waitForCapacity': True}, sid)
    control.capacity.admit = admitted
    telemetry.admission_guard = control.capacity.guard

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
                return ChatResponse(content=[], tool_calls=[ToolCall(id='move-once', name='move', arguments={})])
            return ChatResponse(content=[TextBlock(text='Saved the checkout handoff. This turn is complete.')])

    class Move:
        name = 'move'
        description = 'Move into this task checkout after the current turn settles.'
        input_schema = {'type': 'object', 'properties': {}}
        calls = 0
        receipt = None
        async def execute(self, _input):
            self.calls += 1
            if projection:
                await projection
            self.receipt = (await agent_action(app, saved, 'worktree.handoff', {
                'sessionId': sid, 'id': target['id'], 'expectedExecutionRevision': 0}, 'native-move'))['result']
            return ToolResult(success=True, output={'handoffId': self.receipt['id'], 'phase': 'pending'})

    provider, move = Provider(), Move()
    telemetry.instrument_provider(sid, provider)
    await native.coordinator.mount('providers', provider, name='fixture')
    await native.coordinator.mount('tools', move, name='move')
    task = asyncio.create_task(native.execute(''))
    try:
        await live.wait_for(lambda event: event['type'] == 'session.ready', 2)
        await live.submit(Input('user', 'Move to the dedicated checkout and finish this turn.', id='native-original'))
        # On the unfixed host the completed move tool is followed by a cancelled
        # provider admission and this actual native generation never finishes.
        try:
            await asyncio.wait_for(completed.wait(), 3)
        except TimeoutError:
            pytest.fail(str(events))
        assert provider.calls == 2 and move.calls == 1
        assert not any(event['type'] in {'generation.failed', 'generation.detached'} for event in events)
        messages = await native.coordinator.get('context').get_messages()
        assert any(message.get('role') == 'tool' and move.receipt['id'] in str(message.get('content')) for message in messages)
        assert app.worktrees.read(sid, move.receipt['id'])['phase'] == 'pending'
        assert saved['configurationBusy']
        app.runtime.quiesce_for_handoff.assert_not_awaited()
        with pytest.raises(AppError, match='new input must wait'):
            await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Do not admit before checkout settlement'}, command_id='later-input')
        assert list(live.accepted) == ['native-original']
        checkpoint.write_text(json.dumps(messages))
        native_worker.parked = True
        await settle(app)
        await asyncio.gather(*list(app.tasks))
        receipt = app.worktrees.read(sid, move.receipt['id'])
        assert receipt['phase'] == 'applied' and receipt['inputsReplayed'] is False
        assert saved['workingDirectory'] == target['path']
        assert saved['executionRevision'] == 1 and not saved['configurationBusy']
        app.runtime.quiesce_for_handoff.assert_awaited_once()
        app.runtime.collaboration_input.assert_awaited_once()
        assert move.receipt['id'] in checkpoint.read_text()
        assert provider.calls == 2 and move.calls == 1
    finally:
        if not live.closed:
            await live.submit(Input('stop', target='cancel'))
        await asyncio.gather(asyncio.wait_for(task, 2), return_exceptions=True)
        if projection:
            await projection
        await control.close()
        await native.cleanup()
