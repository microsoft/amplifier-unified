"""Real Core/Foundation/loop-live multimodal turn against a local stub provider."""
import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.attachments import MediaAttachmentContext, save, encode

async def run():
    plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False}}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent for name in ('loop-live', 'context-simple')}
    prepared = PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name='attachment-fixture', session=plan['session']))
    session = await prepared.create_session(session_id='attachment-fixture')
    runtime = Runtime(session.session_id)
    session.coordinator.register_capability('live.runtime', runtime)
    session.coordinator.register_capability('live.attachments.encode', encode)

    class Provider:
        name = 'fixture'
        requests = []
        def get_info(self):
            return ProviderInfo(id='fixture', display_name='Fixture', defaults={'model': 'fixture-model'})
        def parse_tool_calls(self, response):
            return []
        async def complete(self, request, **kwargs):
            self.requests.append(request)
            return ChatResponse(content=[TextBlock(text='Image received')], usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12))
    provider = Provider()
    await session.coordinator.mount('providers', provider, name='fixture')
    task = None
    try:
        with tempfile.TemporaryDirectory() as home:
            home = Path(home).resolve()
            os.environ['AMPLIFIER_WEB_HOME'] = str(home)
            from io import BytesIO
            from PIL import Image
            from amplifier_foundation.session.checkpoint import acknowledge_legacy_checkpoint, verified_checkpoint_refs
            from amplifier_foundation.session.media import SessionMediaStore
            from amplifier_web.host.media import NAMESPACE
            bitmap = BytesIO()
            Image.new('RGB', (2, 2), 'blue').save(bitmap, format='PNG')
            original = bitmap.getvalue()
            encoded = base64.b64encode(original).decode()
            coordinator = session.coordinator
            media = SessionMediaStore(Path(home).resolve() / 'media', session.session_id,
                                      checkpoint_verifier=verified_checkpoint_refs)
            context = MediaAttachmentContext(coordinator.get('context'), coordinator)
            await coordinator.mount('context', context)
            coordinator.register_capability('web.media.composer', media)
            async def checkpoint():
                rows = await context.get_messages()
                (Path(home) / 'transcript.jsonl').write_text(
                    ''.join(json.dumps(row) + '\n' for row in rows))
                media.acknowledge_checkpoint(acknowledge_legacy_checkpoint(
                    Path(home), session_id=session.session_id, expected_messages=rows))
            coordinator.register_capability('session.durable_checkpoint', checkpoint)
            attachment = save(home, 'tiny.png', encoded)
            task = asyncio.create_task(session.execute(''))
            await runtime.wait_for(lambda e: e['type'] == 'session.ready', timeout=8)
            await runtime.submit(Input('user', 'Please review the attached files.', id='image-only', attachments=(attachment,)))
            event = await runtime.wait_for(lambda e: e['type'] in {'generation.finished', 'generation.failed'}, timeout=8)
            assert event['type'] == 'generation.finished', event
            assert provider.requests
            request = provider.requests[0]
            user = next(message for message in request.messages if message.role == 'user' and isinstance(message.content, list))
            canonical = [json.loads(line) for line in (home / 'transcript.jsonl').read_text().splitlines()]
            uploaded = next(message for message in canonical if NAMESPACE in (message.get('metadata') or {}))
            [ref] = uploaded['metadata'][NAMESPACE]['refs']
            media.verify_checkpoint(ref)
            with media.resolve_ref(ref) as resolved:
                assert resolved.read() == original
            assert not any(block.type == 'image' for block in user.content)
            assert encoded not in json.dumps(runtime.events)
            assert encoded not in (Path(home) / 'transcript.jsonl').read_text()
            await runtime.submit(Input('stop'))
            await asyncio.wait_for(task, 5)
            media.close()
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()
    print(json.dumps({'image_reference_received': True, 'public_events_contain_no_image_data': True, 'provider_network_calls': 0}))

asyncio.run(run())
