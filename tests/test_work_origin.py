"""Worker scope/bridge observations use actual runtime state, not model fields."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.runtime_worker import Worker
from test_collaborative_workspaces import app
from test_coordination_delivery import native_outbound_probe, programmatic_origin_probe


@pytest.mark.parametrize('fault', ['none', 'opaque_only', 'changed_plaintext', 'oversized', 'new_input', 'wrong_response'])
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
