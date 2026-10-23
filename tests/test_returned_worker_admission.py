"""Returned native jobs retain display ancestry but admit their current input."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from test_assignments import app, start
from test_task_continuity import controls
from test_model_admission_wait import providers


async def test_native_delegate_return_can_start_its_observation_turn(app, tmp_path, monkeypatch):
    from amplifier_core import AmplifierSession, ToolResult
    from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
    from amplifier_module_loop_live.runtime import Input, Runtime

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    await start(app)
    source = app.state['sessions'][0]
    sid = source['id']
    control = controls(sid)
    pending = None
    emitted, admissions = [], []
    telemetry = None

    async def project(previous, event):
        if previous:
            await previous
        normalized = normalize_event(event, sid)
        if normalized:
            await app.on_runtime_event(*normalized)

    def observe(event):
        nonlocal pending
        emitted.append(copy.deepcopy(event))
        if telemetry:
            telemetry.lifecycle(event)
        pending = asyncio.create_task(project(pending, event))

    telemetry = ExecutionEvents(sid, observe)
    live = Runtime(session_id=sid, observer=observe)
    native = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {
            'configured_bundle': True, 'background_delegate': True,
            'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=sid)
    await native.initialize()
    native.coordinator.register_capability('live.runtime', live)

    async def admitted(row):
        if pending:
            await pending
        result = await app.app_bridge('capacity.admit', {
            'call': row, '_generationId': live.generation['id'],
            '_runtimeSessionId': sid, 'waitForCapacity': True}, sid)
        admissions.append({'call': copy.deepcopy(row), 'generation': copy.deepcopy(live.generation),
                           'result': copy.deepcopy(result)})
        return result

    control.capacity.admit = admitted
    telemetry.admission_guard = control.capacity.guard
    release = asyncio.Event()

    class Delegate:
        name = 'delegate'
        description = 'Retain one synthetic result before returning'
        input_schema = {'type': 'object', 'properties': {}}
        calls = 0
        async def execute(self, arguments):
            self.calls += 1
            (tmp_path / 'retained-effect.txt').write_text('saved once')
            await release.wait()
            return ToolResult(success=True, output={'report': 'Completed synthetic task'})

    class Provider:
        name = 'fixture'
        config = {}
        calls = 0
        def get_info(self):
            return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        async def complete(self, request, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(content=[], tool_calls=[
                    ToolCall(id='owned-delegate', name='delegate', arguments={'async': True})])
            return ChatResponse(content=[TextBlock(text='Waiting for the worker.' if self.calls == 2
                                                   else 'Worker result inspected.')])

    provider, delegate = Provider(), Delegate()
    telemetry.instrument_provider(sid, provider)
    await native.coordinator.mount('providers', provider, name=provider.name)
    await native.coordinator.mount('tools', delegate, name=delegate.name)
    task = asyncio.create_task(native.execute(''))
    try:
        await live.wait_for(lambda event: event['type'] == 'session.ready', 2)
        await live.submit(Input('user', 'Complete one delegated task.', id='original-input'))
        await live.wait_for(lambda event: event['type'] == 'generation.finished', 3)
        assert provider.calls == 2 and delegate.calls == 1
        release.set()
        terminal = await live.wait_for(lambda event: event['type'] in {
            'generation.finished', 'generation.detached', 'generation.failed'}
            and event.get('input_ids') != ['original-input'], 3)
        if pending:
            await pending
        assert terminal['type'] == 'generation.finished', admissions[-1]
        assert provider.calls == 3 and delegate.calls == 1
        assert (tmp_path / 'retained-effect.txt').read_text() == 'saved once'
        returned_input = next(event for event in emitted if event['type'] == 'input.delivered'
                              and event.get('source') == 'amplifier-delegate')
        assert admissions[-1]['call']['admissionInputId'] == returned_input['input_id']
        assert admissions[-1]['call']['turnId'] == 'original-input'
        assert admissions[-1]['result']['allowed'] is True
        assert not source.get('error') and not source.get('modelAdmission')
    finally:
        release.set()
        if not task.done() and not live.closed:
            await live.submit(Input('stop', target='cancel'))
        await asyncio.gather(asyncio.wait_for(task, 3), return_exceptions=True)
        if pending:
            await pending
        await native.cleanup()
        await control.close()


@pytest.mark.parametrize('case', ['current', 'new-child', 'old-child', 'old-observer',
                                  'stale-input', 'stop', 'configuration', 'budget'])
async def test_returned_input_preserves_authority_and_budget_fences(app, providers, case):
    from test_assignments import generation, limits

    await start(app)
    source = app.state['sessions'][0]
    await generation(app, source, 'returned-generation', ['service-input'])
    item = providers(source, child_id='child' if case.endswith('child') else None)
    events = item.telemetry
    events.lifecycle({'type': 'input.delivered', 'input_id': 'original-input', 'source': 'user'})
    if case == 'old-child':
        events.lifecycle({'type': 'child.updated', 'sessionId': 'child', 'runId': 'old-run'})
    if case != 'old-observer':
        events.lifecycle({'type': 'generation.started', 'generation_id': 'returned-generation',
                          'initial_input_id': 'service-input'})
        events.lifecycle({'type': 'input.delivered', 'input_id': 'service-input',
                          'source': 'amplifier-delegate'})
    if case == 'new-child':
        events.hook(source['id'], 'tool:pre', {'tool_call_id': 'spawn', 'tool_name': 'delegate'})
        events.lifecycle({'type': 'child.updated', 'sessionId': 'child', 'callId': 'spawn',
                          'runId': 'current-run'})
    if case == 'stale-input':
        events.turn_id = 'service-input'  # Valid display ancestry cannot rescue a stale admission input.
        events.admission_inputs[source['id']] = 'obsolete-input'
    elif case == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': source['id']}, command_id='stop-return')
    elif case == 'configuration':
        source['configurationBusy'] = True
    elif case == 'budget':
        await limits(app, source, maxTotalTokens=1, reserveTokens=2)
    item.release.set()
    if case in {'current', 'new-child'}:
        await item.start()
        assert item.called == ['fixture']
        row = next(row for row in events.nodes.values() if row['kind'] == 'llm')
        assert row['admissionInputId'] == 'service-input'
        assert row['turnId'] == 'original-input'
    elif case == 'budget':
        with pytest.raises(ValueError, match='insufficient unreserved'):
            await item.start()
        assert not item.called
        assert source['modelAdmission']['generationId'] == 'returned-generation'
        assert source['modelAdmission']['inputIds'] == ['service-input']
        assert source['modelAdmission']['providerRequestSent'] is False
    else:
        with pytest.raises(asyncio.CancelledError):
            await item.start()
        assert not item.called and not source.get('modelAdmission')
        assert item.control.capacity.last_denial is None


async def test_spawning_input_is_frozen_across_root_observation_and_child_events():
    events = ExecutionEvents('root', lambda event: None)
    events.lifecycle({'type': 'input.delivered', 'input_id': 'user-input', 'source': 'user'})
    events.hook('root', 'tool:pre', {'tool_call_id': 'old-spawn', 'tool_name': 'delegate'})
    events.lifecycle({'type': 'generation.started', 'initial_input_id': 'service-input'})
    events.lifecycle({'type': 'input.delivered', 'input_id': 'service-input', 'source': 'amplifier-delegate'})
    # Even delayed child registration must use its spawning call's identity.
    events.lifecycle({'type': 'child.updated', 'sessionId': 'child', 'callId': 'old-spawn', 'runId': 'old'})
    assert events.admission_input('child') == 'user-input'
    events.lifecycle({'type': 'generation.started', 'sessionId': 'child', 'initial_input_id': 'child-input'})
    events.lifecycle({'type': 'input.delivered', 'sessionId': 'child', 'input_id': 'child-input'})
    events.lifecycle({'type': 'child.updated', 'sessionId': 'child', 'callId': 'old-spawn', 'runId': 'old'})
    assert events.admission_input('child') == 'user-input'
    assert events.admission_input('root') == 'service-input'
    assert events.turn_id == 'user-input'
    # A new run of the same child is bound to its new spawning call.
    events.hook('root', 'tool:pre', {'tool_call_id': 'new-spawn', 'tool_name': 'delegate'})
    events.lifecycle({'type': 'child.updated', 'sessionId': 'child', 'callId': 'new-spawn', 'runId': 'new'})
    assert events.admission_input('child') == 'service-input'


async def test_provider_identity_is_captured_before_async_info_boundary():
    events = ExecutionEvents('root', lambda event: None)
    events.lifecycle({'type': 'input.delivered', 'input_id': 'earlier'})
    async def info():
        events.lifecycle({'type': 'generation.started', 'initial_input_id': 'later'})
        return {'id': 'fixture'}
    row = await events._begin_provider_call('root', SimpleNamespace(get_info=info), SimpleNamespace())
    assert row['admissionInputId'] == 'earlier'
    assert events.admission_input('root') == 'later'


def test_explicit_admission_identity_cannot_fall_back_to_display_ancestry():
    from amplifier_web.model_admission_identity import input_id
    assert input_id({'turnId': 'legacy'}) == 'legacy'
    assert input_id({'turnId': 'display', 'admissionInputId': 'current'}) == 'current'
    for invalid in (None, '', 42, [], 'x' * 201):
        assert input_id({'turnId': 'display', 'admissionInputId': invalid}) is None


async def test_service_observation_cannot_borrow_fresh_human_resume(app):
    from amplifier_web.capacity import fresh_human_input
    source = app.state['sessions'][0]
    source['collaborationGeneration']['inputIds'] = ['human', 'service']
    source['messages'].append({'role': 'user', 'inputId': 'human', 'inputOrigin': 'ui', 'createdAt': 20})
    call = {'sessionId': source['id'], 'turnId': 'human', 'admissionInputId': 'service'}
    assert not fresh_human_input(app, source, call, {'at': 10})
    assert fresh_human_input(app, source, {**call, 'admissionInputId': 'human'}, {'at': 10})
