"""Actual Core hook transport binds each host update to one saved observation."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_core import HookRegistry, HookResult
from amplifier_web.event_window import _observation
from amplifier_web.execution import ingest
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.runtime_worker import Worker
from amplifier_web.tool_observations import identity


def host(monkeypatch, sid='root', telemetry=None):
    hooks, capabilities, messages, saved = HookRegistry(), {}, [], []
    worker = Worker()
    worker.runtime = SimpleNamespace(session_id='root')
    worker.telemetry = telemetry or ExecutionEvents('root', messages.append)
    co = SimpleNamespace(session_id=sid, hooks=hooks, get_capability=capabilities.get,
        register_capability=capabilities.__setitem__, get=lambda name: {})
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', lambda row: None)
    worker.install_activity(co)
    async def capture(event, data):
        raw = json.dumps({'event': event, 'data': data}).encode()
        saved.append(_observation(json.loads(raw), raw, len(saved), Path('/fixture/events.jsonl'), sid))
        return HookResult()
    for event in ('tool:pre', 'tool:post', 'tool:error'):
        hooks.register(event, capture, priority=100, name='canonical-test-capture')
    return co, worker, messages, saved


def rows(messages):
    return [item['event'] for item in messages if item['type'] == 'execution.event']


async def test_actual_hook_wire_replaces_forgery_and_round_trips_bounded_child_hints(monkeypatch):
    co, worker, messages, saved = host(monkeypatch, 'child')
    raw = {'tool_call_id': 'same', 'tool_name': 'tool_exec', 'session_id': 'child',
           'unified_tool_observation_id': '11111111-1111-1111-1111-111111111111',
           'tool_input': {'code': 'x' * 250, 'credential': 'private-token', 'nested': {'command': 'hidden'}}}
    await co.hooks.emit('tool:pre', raw)
    post = {**raw, 'result': {'success': False, 'output': {'returncode': 3, 'stderr': 'first\nlast error\n', 'stdout': 'large secret body' * 1000}}}
    result = await co.hooks.emit('tool:post', post)
    assert result.data['result'] == post['result']
    updates = rows(messages)
    assert len(updates) == len(saved) == 2
    assert updates[0]['toolObservationId'] != updates[1]['toolObservationId']
    for update, recorded in zip(updates, saved):
        token = update['toolObservationId']
        assert identity(token) == token != raw['unified_tool_observation_id']
        assert recorded['_hostToolId'] == token
        public = normalize_event({'type': 'execution.event', 'event': update}, 'root')[1]
        session = {}; ingest(session, public)
        admitted = session['execution']['nodes'][0]
        assert admitted['toolObservationId'] == token
        assert admitted['sessionId'] == 'child'
        assert admitted['inputSummary'] == {'code': 'x' * 179 + '…'}
        assert 'private-token' not in str(admitted) and 'large secret body' not in str(admitted)
    assert updates[-1]['outcomeSummary'] == {'exitCode': 3, 'error': 'last error'}
    assert saved[-1]['outcomeSummary'] == updates[-1]['outcomeSummary']


@pytest.mark.parametrize('terminal', ['tool:post', 'tool:error'])
async def test_reused_id_late_terminal_does_not_steal_new_pre_input(monkeypatch, terminal):
    co, worker, messages, saved = host(monkeypatch)
    base = {'tool_call_id': 'reused', 'tool_name': 'app_control'}
    await co.hooks.emit('tool:pre', {**base, 'tool_input': {'action': 'first'}})
    await co.hooks.emit('tool:pre', {**base, 'tool_input': {'action': 'second'}})
    await co.hooks.emit(terminal, {**base, 'error': {'msg': 'late failure'}})
    updates = rows(messages)
    assert len({row['toolObservationId'] for row in updates}) == 3
    assert [row['_hostToolId'] for row in saved] == [row['toolObservationId'] for row in updates]
    assert updates[-1]['inputSummary'] == {} and 'input' not in saved[-1]
    assert updates[-1]['outcomeSummary'] == {'error': 'late failure'}
    assert saved[-1]['outcomeSummary'] == {'error': 'late failure'}
    session = {}
    for row in updates:
        ingest(session, normalize_event({'type': 'execution.event', 'event': row}, 'root')[1])
    assert session['execution']['nodes'][0]['inputSummary'] == {}


async def test_actual_streaming_tools_with_concurrent_reused_ids_bind_each_saved_result(monkeypatch):
    from amplifier_core.models import ToolResult
    from amplifier_core.message_models import ToolCall
    from amplifier_module_loop_streaming import StreamingOrchestrator
    co, worker, messages, saved = host(monkeypatch)
    gate = asyncio.Event()
    class Tool:
        async def execute(self, arguments):
            if arguments['action'] == 'first':
                await gate.wait()
            else:
                gate.set()
            return ToolResult(success=True, output={'action': arguments['action']})
    loop = StreamingOrchestrator({})
    values = await asyncio.gather(*(loop._execute_tool_only(
        ToolCall(id='reused', name='fixture', arguments={'action': action}),
        {'fixture': Tool()}, co.hooks, None) for action in ('first', 'second')))
    updates = rows(messages)
    assert len(values) == 2 and len(updates) == len(saved) == 4
    assert len({row['toolObservationId'] for row in updates}) == 4
    by_token = {record['_hostToolId']: record for record in saved}
    assert len(by_token) == 4
    for update in updates:
        record = by_token[update['toolObservationId']]
        assert record['_hostToolId'] == update['toolObservationId']
        assert record['inputSummary'] == update['inputSummary']
        if record['event'] == 'tool:post':
            assert json.loads(record['output'])['output']['action'] == update['inputSummary']['action']


async def test_foreign_session_is_not_stamped_or_published(monkeypatch):
    co, worker, messages, saved = host(monkeypatch)
    await co.hooks.emit('tool:pre', {'session_id': 'foreign', 'tool_call_id': 'call', 'tool_name': 'bash'})
    assert not rows(messages)
    assert '_hostToolId' not in saved[0]


def test_metadata_allowlists_refuse_non_tool_and_unbounded_values():
    event = {'id': 'tool', 'kind': 'tool', 'toolObservationId': 'not-a-uuid',
        'inputSummary': {'command': 'x' * 1000, 'token': 'private'},
        'outcomeSummary': {'error': 'y' * 1000, 'exitCode': True, 'secret': 'private'}}
    public = normalize_event({'type': 'execution.event', 'event': event}, 'root')[1]
    assert 'toolObservationId' not in public
    assert public['inputSummary'] == {'command': 'x' * 179 + '…'}
    assert public['outcomeSummary'] == {'error': 'y' * 179 + '…'}
    public = normalize_event({'type': 'execution.event', 'event': {**event, 'kind': 'llm'}}, 'root')[1]
    assert not {'toolObservationId', 'inputSummary', 'outcomeSummary'} & public.keys()


def test_fresh_observation_drops_cached_fields_from_previous_observation():
    import uuid
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    session = {}
    base = {'id': 'tool:root:call', 'kind': 'tool', 'phase': 'completed', 'revision': 1}
    ingest(session, {**base, 'toolObservationId': old, 'input': 'old input',
        'output': 'old output', 'inputSummary': {'action': 'old'}})
    ingest(session, {**base, 'revision': 2, 'toolObservationId': new,
        'inputSummary': {}, 'outcomeSummary': {'error': 'new terminal'}})
    row = session['execution']['nodes'][0]
    assert row['toolObservationId'] == new
    assert 'input' not in row and 'output' not in row
    assert row['inputSummary'] == {}
