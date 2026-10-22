"""Explicit controls keep real hook/Work checks without a fabricated loop call."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_core import HookResult, ToolResult
from amplifier_web.app_guidance import install_app_access
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.runtime_worker import Worker
from test_collaborative_workspaces import app
from test_work_invocation import prepared_work, assert_available_commission


async def controlled(app, *, sid=None, name='control_probe'):
    effects, events, requests, caps, tools = [], [], [], {}, {}
    sid = sid or app.state['sessions'][1]['id']
    class Probe:
        input_schema = {'type': 'object'}
        async def execute(self, arguments):
            effects.append(copy.deepcopy(arguments))
            return ToolResult(success=True, output='controlled effect')
    tools[name] = Probe()
    async def emit(event, data):
        events.append((event, data))
        return HookResult()
    async def mount(kind, tool, name):
        tools[name] = tool
    c = SimpleNamespace(session_id=sid, get=lambda kind: tools if kind == 'tools' else None,
        get_capability=caps.get, register_capability=caps.__setitem__, mount=mount,
        cancellation=SimpleNamespace(is_cancelled=False),
        hooks=SimpleNamespace(emit=emit, register=lambda *a, **kw: None),
        process_hook_result=AsyncMock(side_effect=lambda result, *a: result))
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=c, session_id=sid)
    worker.runtime = SimpleNamespace(generation=None, closed=False, stop_requested=False, queued_inputs=0)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.shared_handle, worker.activation = object(), object()
    controls = object.__new__(RuntimeControls)
    controls.coordinator, controls.runtime, controls.session = c, worker.runtime, worker.session
    controls.lock, controls.checkpoint = asyncio.Lock(), AsyncMock()
    controls._tool_invocation = getattr(worker, 'tool_invocation', None)
    worker.controls = controls
    async def host(operation, arguments):
        requests.append(copy.deepcopy(arguments))
        return await app.app_bridge(operation, arguments, sid)
    worker.bridge = host
    await install_app_access(c, worker.app_access_bridge(c))
    return SimpleNamespace(worker=worker, controls=controls, c=c, tools=tools,
        effects=effects, events=events, requests=requests, caps=caps, host=host)


async def invoke(f, **extra):
    return await f.controls.perform('tool.invoke', {
        'name': 'control_probe', 'arguments': {'value': 'original'}, **extra})


async def test_idle_control_uses_exact_host_check_without_loop_witness_or_replay(app):
    f = await controlled(app)
    result = await invoke(f, actor='ui', toolCallId='forged', _workOrigin={'complete': True})
    assert result['result']['success'] and f.effects == [{'value': 'original'}]
    assert [event for event, _ in f.events] == ['tool:pre', 'tool:post']
    assert len(f.requests) == 1
    request = f.requests[0]
    assert request['toolCallId'] == result['callId'] != 'forged'
    assert request['toolInput'] == {'value': 'original'} and '_workOrigin' not in request
    assert f.worker.runtime.generation is None and not getattr(f.c, '_tool_dispatch_contexts', {})
    assert f.worker.control_invocations == {}
    # An actor claim and the old call ID cannot activate the retired scope.
    denied = await f.tools['control_probe'].execute({'actor': 'ui', 'toolCallId': result['callId']})
    assert denied.error['code'] == 'work_precondition_unavailable'
    assert len(f.effects) == len(f.requests) == 1


@pytest.mark.parametrize('policy', ['deny', 'modify'])
async def test_policy_runs_before_scope_binds_final_arguments(app, policy):
    f = await controlled(app)
    async def emit(event, data):
        f.events.append((event, data))
        if event == 'tool:pre':
            assert f.worker.control_invocations == {}
            await asyncio.sleep(0)
            return HookResult(action=policy, reason='Policy decision', data={'tool_input': {'value': 'approved'}})
        return HookResult()
    f.c.hooks.emit = emit
    result = await invoke(f)
    if policy == 'deny':
        assert result['success'] is False and not f.effects and not f.requests
    else:
        assert f.effects == [{'value': 'approved'}]
        assert f.requests[0]['toolInput'] == {'value': 'approved'}
    assert not f.worker.control_invocations


def mutate(f, fault):
    w = f.worker
    if fault == 'stop': w.stop_revision += 1
    elif fault == 'stop-requested': w.runtime.stop_requested = True
    elif fault == 'cancelled': f.c.cancellation.is_cancelled = True
    elif fault == 'closed': w.runtime.closed = True
    elif fault == 'controls': w.controls = object()
    elif fault == 'root': w.session = SimpleNamespace(coordinator=f.c)
    elif fault == 'root-coordinator': w.session.coordinator = object()
    elif fault == 'runtime': w.runtime = copy.copy(w.runtime)
    elif fault == 'handle': w.shared_handle = object()
    elif fault == 'activation': w.activation = object()
    elif fault == 'gate': w.activation_gate = SimpleNamespace(current_valid=True)
    elif fault == 'parked': w.parked = True
    elif fault == 'yielding': w.ownership.yielding = True
    elif fault == 'remounting': w.remounting = True
    elif fault == 'generation': w.runtime.generation = {'id': 'new', 'input_ids': []}
    elif fault == 'mount': f.tools['control_probe'] = object()


@pytest.mark.parametrize('phase', ['approval', 'host'])
@pytest.mark.parametrize('fault', ['stop', 'stop-requested', 'cancelled', 'closed', 'controls',
    'root', 'root-coordinator', 'runtime', 'handle', 'activation', 'gate', 'parked', 'yielding', 'remounting', 'generation', 'mount'])
async def test_current_owner_is_retained_across_approval_and_host_await(app, phase, fault):
    f = await controlled(app)
    if phase == 'approval':
        async def emit(event, data):
            if event == 'tool:pre':
                await asyncio.sleep(0)
                mutate(f, fault)
            return HookResult()
        f.c.hooks.emit = emit
        with pytest.raises(ValueError, match='execution owner changed'):
            await invoke(f)
        assert not f.requests
    else:
        async def host(operation, arguments):
            result = await f.host(operation, arguments)
            mutate(f, fault)
            return result
        f.worker.bridge = host
        result = await invoke(f)
        assert result['result']['error']['code'] == 'work_precondition_unavailable'
    assert not f.effects and not f.worker.control_invocations


@pytest.mark.parametrize('fault', ['arguments', 'timeout', 'unknown', 'exception', 'cancel', 'task'])
async def test_changed_arguments_unknown_and_cancelled_checks_never_execute_or_replay(app, monkeypatch, fault):
    f = await controlled(app)
    async def host(operation, arguments):
        f.requests.append(copy.deepcopy(arguments))
        if fault == 'arguments':
            f.events[0][1]['tool_input']['value'] = 'redirected'
        elif fault == 'timeout':
            await asyncio.Event().wait()
        elif fault == 'unknown': return {}
        elif fault == 'exception': raise RuntimeError('connection lost')
        elif fault == 'cancel': raise asyncio.CancelledError()
        elif fault == 'task':
            refused = await asyncio.create_task(f.tools['control_probe'].execute({'value': 'original'}))
            assert refused.error['code'] == 'work_precondition_unavailable'
            return {'allowed': False}
        return {'allowed': True}
    f.worker.bridge = host
    monkeypatch.setattr('amplifier_web.runtime_worker.WORK_EFFECT_TIMEOUT', .01)
    if fault in {'exception', 'cancel'}:
        with pytest.raises(RuntimeError if fault == 'exception' else asyncio.CancelledError):
            await invoke(f)
    else:
        result = await invoke(f)
        assert result['result']['error']['code'] == 'work_precondition_unavailable'
    assert not f.effects and len(f.requests) == 1 and not f.worker.control_invocations


async def test_control_scope_cannot_supply_missing_work_coverage(app):
    owner = app.state['sessions'][0]
    item = await prepared_work(app, owner, 'control-work')
    _, child = await assert_available_commission(app, owner, item, 'control-commission')
    f = await controlled(app, sid=child['id'])
    result = await invoke(f, actor='ui', _workOrigin={'complete': True})
    assert result['result']['error']['code'] == 'work_precondition_unavailable'
    assert not f.effects and len(f.requests) == 1 and '_workOrigin' not in f.requests[0]
    assert not f.worker.control_invocations


@pytest.mark.parametrize('operation', ['operations.write', 'operations.cancel'])
@pytest.mark.parametrize('fault', ['none', 'removed', 'replaced', 'foreign', 'cancelled'])
async def test_child_control_uses_only_current_registered_child(app, fault, operation):
    f = await controlled(app)
    child = copy.copy(f.c)
    child.session_id = 'native-child'
    child.cancellation = SimpleNamespace(is_cancelled=False)
    probe = f.tools['control_probe'].original
    owners = []
    probe.validate_process_owner = lambda process, owner: owners.append((process, owner))
    caps, tools = {}, {'bash': probe}
    child.get_capability, child.register_capability = caps.get, caps.__setitem__
    child.get = lambda kind: tools if kind == 'tools' else None
    async def mount(kind, tool, name): tools[name] = tool
    child.mount = mount
    children = SimpleNamespace(sessions={child.session_id: SimpleNamespace(coordinator=child)})
    f.caps['live.children'] = children
    await install_app_access(child, f.worker.app_access_bridge(child))
    if fault == 'foreign': children.sessions.clear()
    async def host(operation, arguments):
        result = await f.host(operation, arguments)
        if fault == 'removed': children.sessions.clear()
        elif fault == 'replaced': children.sessions[child.session_id] = SimpleNamespace(coordinator=copy.copy(child))
        elif fault == 'cancelled': child.cancellation.is_cancelled = True
        return result
    f.worker.bridge = host
    call = f.controls.perform(operation, {'runtimeSessionId': child.session_id,
        'processId': 'owned-process', 'ownerId': 'owned-call', 'stdin': 'input', 'actor': 'ui'})
    if fault == 'foreign':
        with pytest.raises(ValueError, match='owning session is no longer mounted'): await call
    else:
        result = await call
        assert result['result']['success'] is (fault == 'none')
    expected = {'action': 'write' if operation == 'operations.write' else 'terminate', 'process_id': 'owned-process'}
    if operation == 'operations.write': expected.update(stdin='input', close_stdin=False)
    assert f.effects == ([expected] if fault == 'none' else [])
    assert owners == ([] if fault == 'foreign' else [('owned-process', 'owned-call')])
    if f.requests:
        assert f.requests[0]['_runtimeSessionId'] == 'native-child'
    assert not f.worker.control_invocations


@pytest.mark.parametrize('phase', ['approval', 'host'])
async def test_active_operation_control_cannot_cross_changed_input_set(app, phase):
    f = await controlled(app)
    f.worker.runtime.generation = {'id': 'active', 'input_ids': ['original']}
    if phase == 'host':
        async def host(operation, arguments):
            result = await f.host(operation, arguments)
            f.worker.runtime.generation['input_ids'].append('later')
            return result
        f.worker.bridge = host
        result = await f.controls.invoke({'name': 'control_probe', 'arguments': {}})
        assert result['result']['error']['code'] == 'work_precondition_unavailable'
    else:
        async def emit(event, data):
            if event == 'tool:pre':
                await asyncio.sleep(0)
                f.worker.runtime.generation['input_ids'].append('later')
            return HookResult()
        f.c.hooks.emit = emit
        with pytest.raises(ValueError, match='execution owner changed'):
            await f.controls.invoke({'name': 'control_probe', 'arguments': {}})
    assert not f.effects and not f.worker.control_invocations
