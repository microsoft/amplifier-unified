"""Steering contracts run with worker dependencies, without the host service."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.conversation_steering import submit


async def test_worker_rechecks_target_after_input_preparation(monkeypatch):
    pytest.importorskip('amplifier_module_loop_live.runtime')
    runtime = SimpleNamespace(closed=False, generation={'id': 'old'}, max_input_chars=10000)
    capability = {'version': 1, 'mode': 'request_boundary', 'submit': AsyncMock()}
    controls = SimpleNamespace(coordinator=SimpleNamespace(get_capability=lambda name: capability))
    async def prepare(*args, **kwargs):
        runtime.generation = {'id': 'new'}
        return 'pause'
    monkeypatch.setattr('amplifier_web.message_interactions.prepare_input', prepare)
    result = await submit(controls, runtime, {'text': 'pause', 'inputId': 'correction',
        'targetGenerationId': 'old'}, None, lambda: 0)
    assert result['accepted'] is False
    capability['submit'].assert_not_awaited()


@pytest.mark.parametrize('late', [False, True])
async def test_real_loop_delivers_at_boundary_or_holds_without_new_generation(monkeypatch, tmp_path, late):
    """Real core/context/loop, scripted provider/tool; no model account required."""
    live = pytest.importorskip('amplifier_module_loop_live.runtime')
    pytest.importorskip('amplifier_module_context_simple')
    from amplifier_core import AmplifierSession, ToolResult
    from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path))
    requests, responses = asyncio.Queue(), asyncio.Queue()
    class Provider:
        async def complete(self, request, **kwargs):
            await requests.put(request)
            return await responses.get()
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        def get_info(self):
            return SimpleNamespace(default_model='fixture')
    class Step:
        name, description, input_schema = 'step', 'One step', {'type': 'object', 'properties': {}}
        async def execute(self, args):
            return ToolResult(success=True, output={'done': True})
    runtime = live.Runtime()
    session = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live',
        'config': {'configured_bundle': True, 'min_delay_between_calls_ms': 0}},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id=runtime.session_id)
    task = None
    try:
        await session.initialize()
        await session.coordinator.mount('providers', Provider(), name='fixture')
        await session.coordinator.mount('tools', Step(), name='step')
        session.coordinator.register_capability('live.runtime', runtime)
        task = asyncio.create_task(session.execute(''))
        await runtime.wait_for(lambda e: e['type'] == 'session.ready', 5)
        await runtime.submit(live.Input('user', 'Do several steps', id='original'))
        await asyncio.wait_for(requests.get(), 5)
        generation = runtime.generation['id']
        entered, release = asyncio.Event(), asyncio.Event()
        if late:
            async def checkpoint(*args):
                entered.set()
                await release.wait()
            session.coordinator.register_capability('live.checkpoint', checkpoint)
            await responses.put(ChatResponse(content=[TextBlock(text='Already finished')]))
            await asyncio.wait_for(entered.wait(), 5)
        result = await submit(SimpleNamespace(coordinator=session.coordinator), runtime,
            {'text': 'Please find a good pause point.', 'inputId': 'correction',
             'targetGenerationId': generation}, None, lambda: 0)
        assert result['accepted'] and result['disposition'] == 'queued'
        if late:
            release.set()
            await runtime.wait_for(lambda e: e['type'] == 'steering.held', 5)
        else:
            await runtime.wait_for(lambda e: e['type'] == 'input.queued' and e['input_id'] == 'correction', 5)
            await responses.put(ChatResponse(content=[], tool_calls=[ToolCall(id='step-1', name='step', arguments={})]))
            request = await asyncio.wait_for(requests.get(), 5)
            assert 'Please find a good pause point.' in str(request.messages)
            await runtime.wait_for(lambda e: e['type'] == 'steering.applied', 5)
            await responses.put(ChatResponse(content=[TextBlock(text='Paused at the step boundary')]))
        finished = await runtime.wait_for(lambda e: e['type'] == 'generation.finished'
                                           and e['generation_id'] == generation, 5)
        await runtime.wait_for(lambda e: e['type'] == 'session.idle' and e['sequence'] > finished['sequence'], 5)
        assert len([e for e in runtime.events if e['type'] == 'generation.started']) == 1
        assert requests.empty()
        assert finished['generation_id'] == generation
        assert ('correction' in finished['input_ids']) is (not late)
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()
