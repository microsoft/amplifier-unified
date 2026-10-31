"""Actual Core/loop-live safe goal yield, with no external model service."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip('amplifier_module_loop_live')
from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.handoff_boundary import inspect
from amplifier_web.runtime_worker import Worker


@pytest.mark.parametrize('change', ['none', 'objective', 'cap', 'completed'])
async def test_real_native_yield_ends_only_current_turn_and_preserves_goal(change):
    sid = 'native-update-boundary'
    entered, release = asyncio.Event(), asyncio.Event()
    live = Runtime(session_id=sid)
    native = AmplifierSession({'session': {
        'orchestrator': {'module': 'loop-live', 'config': {
            'configured_bundle': True, 'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=sid)
    await native.initialize()
    coordinator = native.coordinator
    coordinator.register_capability('live.runtime', live)
    worker = Worker()
    worker.session, worker.runtime = native, live
    snapshots = []
    worker.controls = SimpleNamespace(persist=lambda **kw: snapshots.append(copy.deepcopy(coordinator.session_state)))
    worker.parked = False
    task = {'id': 'existing-task', 'revision': 1, 'continuationEpoch': 1,
            'status': 'active', 'objective': 'Finish the authorized report'}
    goal = {'condition': task['objective'], 'task_id': task['id'], 'task_revision': 1,
            'turns_used': 2, 'continuations': 1, 'cap': 5}
    coordinator.session_state.update(task=task, goal=goal)

    class Provider:
        name = 'fixture'
        config = {}
        calls = 0
        def get_info(self): return SimpleNamespace(id='fixture', default_model='fixture')
        def parse_tool_calls(self, response): return response.tool_calls or []
        async def complete(self, request, **kwargs):
            text = str(request.messages)
            if 'tool-less evaluator' in text:
                return ChatResponse(content=[TextBlock(text='NO\nContinue the authorized report.')])
            self.calls += 1
            entered.set()
            await release.wait()
            assert self.calls == 1, 'An automatic next turn crossed the update boundary'
            return ChatResponse(content=[TextBlock(text='Saved this completed step.')])
    provider = Provider()
    await coordinator.mount('providers', provider, name='fixture')
    execution = asyncio.create_task(native.execute(''))
    try:
        async with asyncio.timeout(15):
            await live.wait_for(lambda e: e['type'] == 'session.ready', 3)
            await live.submit(Input('user', 'Continue the existing authorized report.', id='original'))
            await entered.wait()
            first = await inspect(worker, 'update-attempt', update=True)
            assert not first['ready'] and 'generation' in first['waitingFor']
            release.set()
            await live.wait_for(lambda e: e['type'] == 'generation.finished', 5)
            assert provider.calls == 1
            assert coordinator.session_state['goal']['turns_used'] == 3
            assert coordinator.session_state['goal']['continuations'] == 1
            assert snapshots[-1]['goal']['turns_used'] == 3
            # Park/checkpoint ownership is qualified by the separate real
            # Worker process probe; this in-process test supplies its observation.
            worker.parked = True
            if change == 'objective':
                coordinator.session_state['task'] = {**task, 'revision': 2, 'continuationEpoch': 2, 'objective': 'Different task'}
                coordinator.session_state['goal'] = {**coordinator.session_state['goal'], 'condition': 'Different task', 'task_revision': 2}
            elif change in {'cap', 'completed'}:
                coordinator.session_state['goal'] = None
                if change == 'completed': coordinator.session_state['task']['status'] = 'completed'
            result = await inspect(worker, 'update-attempt', update=True)
            assert result['ready'], result
            assert ('yieldedGoal' in result) is (change == 'none')
            assert set(live.accepted) == {'original'}
            await inspect(worker, 'update-attempt', cancel=True)
            assert worker._handoff_boundary is None
    finally:
        release.set()
        if not live.closed: await live.submit(Input('stop', target='cancel'))
        await asyncio.gather(asyncio.wait_for(execution, 3), return_exceptions=True)
        await native.cleanup()
