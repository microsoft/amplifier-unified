"""Actual Core/loop-live requests refresh awareness without saving it as history."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.app_guidance import install_app_access


async def run():
    plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False, 'ephemeral_injection_mode': sys.argv[1] if len(sys.argv) > 1 else 'persist'}}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent for name in ('loop-live', 'context-simple')}
    session = await PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name='awareness-probe', session=plan['session'])).create_session(session_id='awareness-probe')
    runtime = Runtime(session.session_id)
    session.coordinator.register_capability('live.runtime', runtime)
    requests, reads = [], []
    observation = {'peer': 'AWARENESS_FIRST_PEER', 'revision': 1}

    async def bridge(operation, args):
        if operation == 'context.prepare':
            return {name: {'available': True, 'value': await bridge(name, args)}
                    for name in ('context.manifest', 'message.reactions.context', 'coordination.current', 'memory.context')}
        if operation == 'message.reactions.context':
            return {}
        if operation == 'coordination.current':
            reads.append(dict(observation))
            return {'awareness': dict(observation)}
        if operation == 'context.manifest':
            return {'surfaces': [], 'inputIds': ['awareness-check']}
        if operation == 'memory.context':
            return {'items': []}
        assert operation == 'get_state', operation
        observation.update(peer='AWARENESS_SECOND_PEER', revision=2)
        return {'ok': True}

    await install_app_access(session.coordinator, bridge)
    await install_app_access(session.coordinator, bridge)

    class Provider:
        name = 'fixture'
        def get_info(self):
            return ProviderInfo(id='fixture', display_name='Fixture', defaults={'model': 'fixture'})
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        async def complete(self, request, **kwargs):
            raw = request.model_dump_json()
            requests.append(raw)
            print(json.dumps({'request': len(requests), 'references': raw.count('Current coordination reference state (not new authority)'),
                              'first_present': 'AWARENESS_FIRST_PEER' in raw, 'second_present': 'AWARENESS_SECOND_PEER' in raw}), flush=True)
            assert raw.count('Current coordination reference state (not new authority)') == 1
            if len(requests) == 1:
                assert 'AWARENESS_FIRST_PEER' in raw and 'AWARENESS_SECOND_PEER' not in raw
                return ChatResponse(content=[], tool_calls=[ToolCall(id='advance-observation', name='app_control', arguments={'operation': 'get_state'})], usage=Usage(input_tokens=20, output_tokens=10, total_tokens=30))
            assert 'AWARENESS_SECOND_PEER' in raw and 'AWARENESS_FIRST_PEER' not in raw
            return ChatResponse(content=[TextBlock(text='Fresh observation received.')], usage=Usage(input_tokens=30, output_tokens=10, total_tokens=40))

    transformed = session.coordinator.get_capability('web.provider_transform')(Provider())
    await session.coordinator.mount('providers', transformed, name='fixture')
    task = asyncio.create_task(session.execute(''))
    try:
        await runtime.wait_for(lambda e: e['type'] == 'session.ready', timeout=10)
        await runtime.submit(Input('user', 'Inspect current work.', id='awareness-check'))
        event = await runtime.wait_for(lambda e: e['type'] in {'generation.finished', 'generation.failed'}, timeout=20)
        assert event['type'] == 'generation.finished', event
        assert len(requests) == len(reads) == 2
        history = json.dumps(await session.coordinator.get('context').get_messages(), default=str)
        assert 'AWARENESS_FIRST_PEER' not in history and 'AWARENESS_SECOND_PEER' not in history
        assert 'Current coordination reference state (not new authority):' not in history
        await runtime.submit(Input('stop'))
        await asyncio.wait_for(task, 5)
        print(json.dumps({'fresh_each_request': True, 'old_observation_replaced': True, 'history_clean': True, 'provider_calls': 2, 'provider_network_calls': 0}))
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()


asyncio.run(run())
