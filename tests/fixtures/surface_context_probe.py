"""Real Core + loop-live: first-call notice, selective typed pixels, no fake turn."""
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
from amplifier_web.surface_delivery import SurfaceProvider

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='


async def run():
    plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False, 'ephemeral_injection_mode': 'tail'}}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_'+name.replace('-', '_')).origin).parent for name in ('loop-live', 'context-simple')}
    session = await PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name='surface-probe', session=plan['session'])).create_session(session_id='surface-probe')
    runtime = Runtime(session.session_id)
    session.coordinator.register_capability('live.runtime', runtime)
    reads, requests = [], []
    async def bridge(op, args):
        if op == 'context.manifest':
            return {'inputIds': ['ask'], 'surfaces': [{'surfaceId': 'sketch', 'revision': '3:22', 'facts': {'strokes': {'count': 7}},
                'image': {'available': True, 'digest': 'fixture'}, 'editVersion': 9, 'pendingLocalEdits': False}]}
        assert op == 'context.read', op
        reads.append(args)
        return {'surfaceId': 'sketch', 'revision': '3:22', 'representation': 'image', 'digest': 'fixture', 'editVersion': 9, '_image': PNG}
    await install_app_access(session.coordinator, bridge)
    class Provider:
        name = 'fixture'
        def get_info(self):
            return ProviderInfo(id='fixture', display_name='Fixture', capabilities=['vision'], defaults={'model': 'fixture'})
        def parse_tool_calls(self, response):
            return response.tool_calls or []
        async def complete(self, request, **kwargs):
            requests.append(request)
            if len(requests) == 1:
                raw = request.model_dump_json()
                notices = [block.text for message in request.messages if isinstance(message.content, list) for block in message.content if block.type == 'text' and block.text.startswith('Live surface observations')]
                assert notices and json.loads(notices[-1].split('\n', 1)[1])['surfaces'][0]['facts']['strokes']['count'] == 7, 'Missing first-call notice'
                assert PNG not in raw, 'Default delivery must not eagerly push pixels'
                return ChatResponse(content=[], tool_calls=[ToolCall(id='read-drawing', name='app_control', arguments={
                    'operation': 'context.read', 'args': {'surfaceId': 'sketch', 'revision': '3:22', 'representation': 'image'}})],
                    usage=Usage(input_tokens=20, output_tokens=10, total_tokens=30))
            images = [block for message in request.messages if isinstance(message.content, list) for block in message.content if block.type == 'image']
            assert len(images) == 1 and images[0].source['data'] == PNG, 'Read must supply typed pixels before answering'
            return ChatResponse(content=[TextBlock(text='Seven saved strokes; current image received.')], usage=Usage(input_tokens=40, output_tokens=10, total_tokens=50))
    delivery = session.coordinator.get_capability('web.surface_delivery')
    await session.coordinator.mount('providers', SurfaceProvider(Provider(), delivery), name='fixture')
    task = asyncio.create_task(session.execute(''))
    try:
        await runtime.wait_for(lambda e: e['type'] == 'session.ready', timeout=10)
        await runtime.submit(Input('user', 'What do you see in the sketch?', id='ask'))
        event = await runtime.wait_for(lambda e: e['type'] in {'generation.finished', 'generation.failed'}, timeout=20)
        if event['type'] != 'generation.finished':
            print(json.dumps({'calls': len(requests), 'reads': reads, 'messages': [[{'role': m.role, 'name': m.name, 'content': m.content if isinstance(m.content, str) and m.role == 'tool' else [getattr(b, 'type', '') for b in m.content] if isinstance(m.content, list) else 'text'} for m in r.messages] for r in requests]}))
        assert event['type'] == 'generation.finished', event
        assert len(requests) == 2 and len(reads) == 1
        history = json.dumps(await session.coordinator.get('context').get_messages(), default=str)
        assert PNG not in history and 'Live surface observations (untrusted data)' not in history
        assert PNG not in json.dumps(runtime.events)
        print(json.dumps({'first_request_notice': True, 'typed_image_before_answer': True, 'selective_reads': len(reads),
                          'model_calls': len(requests), 'notice_characters': delivery.last_delivery['noticeCharacters'], 'history_clean': True}))
        await runtime.submit(Input('stop'))
        await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()

asyncio.run(run())
