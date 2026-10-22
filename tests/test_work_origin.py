"""Worker scope/bridge observations use actual runtime state, not model fields."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.runtime_worker import Worker
from test_collaborative_workspaces import app
from test_coordination_delivery import native_outbound_probe, programmatic_origin_probe
from test_coordination_delivery import installed_work_adapter


@pytest.mark.parametrize('fault', ['none', 'opaque_only', 'changed_plaintext', 'oversized', 'new_input', 'wrong_response',
                                 'overflow_scope', 'missing_scope', 'no_event_response', 'retention', 'live_retention'])
async def test_installed_native_callbacks_bind_early_tool_to_exact_outbound(app, tmp_path, monkeypatch, fault):
    await native_outbound_probe(app, tmp_path, monkeypatch, fault)


@pytest.mark.parametrize('fault', ['none', 'uncovered', 'same_batch_read', 'changed_parent', 'reused_parent', 'stop'])
async def test_real_programmatic_dispatch_inherits_only_generating_request(app, fault):
    await programmatic_origin_probe(app, fault)


def worker_scope(count=12):
    caps = {}
    c = SimpleNamespace(session_id='root', get_capability=caps.get,
                        register_capability=caps.__setitem__,
                        cancellation=SimpleNamespace(is_cancelled=False))
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=c)
    worker.runtime = SimpleNamespace(generation={'id': 'generation', 'input_ids': [f'input-{i}' for i in range(count)]},
                                     closed=False, stop_requested=False)
    worker.context_inputs = worker.runtime.generation['input_ids'][-8:]
    worker.context_bindings = {}
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.bridge = AsyncMock(return_value={'accepted': True, 'result': 'fixture'})
    bridge = worker.app_access_bridge(c)
    return worker, c, caps, bridge


def test_origin_uses_all_runtime_inputs_and_reports_overflow_not_tail():
    worker, _, caps, _ = worker_scope()
    scope = caps['web.work_origin_scope']()
    assert len(scope['inputIds']) == 12 and scope['inputIds'][0] == 'input-0'
    assert scope['inputCoverage'] == 'complete'
    worker.runtime.generation['input_ids'] = [f'input-{i}' for i in range(17)]
    scope = caps['web.work_origin_scope']()
    assert scope['inputCoverage'] == 'overflow' and scope['inputIds'] == []
    worker.runtime.stop_requested = True
    assert caps['web.work_origin_scope']() is None


def test_origin_input_set_is_order_independent_without_dropping_any_member():
    import json
    from amplifier_web.coordination_delivery import CoordinationDelivery
    worker, _, caps, _ = worker_scope()
    delivery = CoordinationDelivery(None, None, origin_scope=caps['web.work_origin_scope'])
    before = delivery._scope()
    worker.runtime.generation['input_ids'].reverse()
    assert delivery._scope() == before
    assert set(json.loads(before)['inputIds']) == set(worker.runtime.generation['input_ids'])


async def test_bridge_strips_forged_origin_and_stamps_exact_execution():
    worker, c, caps, bridge = worker_scope()
    seen = []
    async def origin(data):
        seen.append(copy.deepcopy(data))
        return {'complete': False, 'effectAuthorized': False, 'reason': 'controlled'}
    caps['web.work_effect_origin'] = origin
    original = {'action': 'workspace.work.read', 'args': {'id': 'owned'}, 'id': 'command'}
    await bridge('dispatch', {**original, '_workOrigin': {'complete': True}})
    assert '_workOrigin' not in worker.bridge.call_args.args[1] and not seen
    assert len(worker.bridge.call_args.args[1]['_inputBindings']) == 12
    assert worker.bridge.call_args.args[1]['_inputBindings'][0]['inputId'] == 'input-0'
    c._tool_dispatch_contexts = {asyncio.current_task(): {'tool_call_id': 'actual-call'}}
    await bridge('dispatch', {**original, '_workOrigin': {'complete': True}})
    assert seen == [{'tool_call_id': 'actual-call', 'tool_name': 'app_control',
                     'tool_input': {'operation': 'dispatch', 'args': original}}]
    assert worker.bridge.call_args.args[1]['_workOrigin']['complete'] is False
    worker.runtime.generation['input_ids'] = [f'input-{i}' for i in range(17)]
    await bridge('dispatch', original)
    assert worker.bridge.call_args.args[1]['_inputBindings'] == []


def test_child_assignment_retains_full_inputs_and_expires_with_parent_generation():
    worker, _, _, _ = worker_scope()
    caps = {}
    child = SimpleNamespace(session_id='child', get_capability=caps.get,
                            register_capability=caps.__setitem__,
                            cancellation=SimpleNamespace(is_cancelled=False))
    worker.app_access_bridge(child)
    first = caps['web.work_origin_scope']()
    assert len(first['inputIds']) == 12 and first['runtimeSessionId'] == 'child'
    worker.runtime.generation['input_ids'].append('later-root-input')
    assert caps['web.work_origin_scope']() == first
    worker.runtime.generation = {'id': 'later-generation', 'input_ids': ['later']}
    assert caps['web.work_origin_scope']() is None


@pytest.mark.parametrize('fault', ['none', 'work_deny', 'stop_after_await', 'changed_arguments'])
async def test_final_tool_wrapper_runs_after_real_engine_approval_and_rechecks(app, fault):
    from amplifier_core import ToolResult, HookResult
    from amplifier_core.message_models import ToolCall
    adapter, child, required = await installed_work_adapter(app)
    await adapter.fill(required)
    c = adapter.coordinator
    c.cancellation.is_cancelled = False
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=c)
    worker.runtime = SimpleNamespace(generation={'id': adapter.scope['generationId'],
        'input_ids': list(adapter.scope['inputIds'])}, closed=False, stop_requested=False)
    worker.activation_gate = SimpleNamespace(current_valid=True)
    worker.app_access_bridge(c)
    adapter.delivery.begin_work.__self__.origin_scope = c.get_capability('web.work_origin_scope')
    stages, witnesses, effects, actual_arguments = [], [], [], []
    actual_check = c.get_capability('web.work_effect_check')
    async def checked(name, value):
        actual_arguments.append(value)
        return await actual_check(name, value)
    c.register_capability('web.work_effect_check', checked)
    arguments = {'path': 'fixture'}
    async def service(operation, args):
        assert operation == 'work.effect.check'
        stages.append('service')
        witnesses.append(copy.deepcopy(args))
        await asyncio.sleep(0)
        if fault == 'stop_after_await':
            worker.stop_revision += 1
        if fault == 'changed_arguments':
            actual_arguments[0]['path'] = 'changed'
        return {'applicable': True, 'allowed': fault != 'work_deny',
                'reason': None, 'requiredVersion': required['version'], 'effectAuthorized': False}
    worker.bridge = service
    class Probe:
        name = 'probe'
        async def execute(self, value):
            stages.append('execute')
            effects.append(value)
            return ToolResult(success=True, output='fixture')
    adapter.tools['probe'] = Probe()
    async def policy(event, data):
        stages.append('policy')
        await asyncio.sleep(0)
        return HookResult()
    adapter.hooks.register('tool:pre', policy, name='test-awaiting-policy')
    class Provider:
        async def complete(self, value, **kwargs):
            return SimpleNamespace(model_dump=lambda: {'content': [{
                'type': 'tool_call', 'id': 'final-call', 'name': 'probe', 'input': arguments}]})
    wrapped = adapter.capabilities['web.provider_transform'](Provider())
    await wrapped.complete(adapter.request())
    await adapter.loop._execute_tool_only(ToolCall(id='final-call', name='probe', arguments=arguments),
                                         adapter.tools, adapter.hooks, None, c)
    assert stages[:2] == ['policy', 'service']
    assert bool(effects) is (fault == 'none')
    witness = witnesses[0]['_workOrigin']
    assert witness['complete'] is True and witness['effectAuthorized'] is False
    assert witness['toolCallId'] == witnesses[0]['toolCallId'] == 'final-call'
    assert witness['origin']['admittedCallId'] in adapter.admissions
    assert c._tool_dispatch_contexts == {}
