"""Real Core/loop-live admitted upload, checkpoint and private projection.

Uses a local stub provider, not inference. Host SurfaceProvider/root-child
composition is qualified separately by its owning integration checks.
"""
import asyncio
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_foundation.session.media import bind_session_media
from amplifier_foundation.session.media_provider import MediaPreparedProvider
from amplifier_module_loop_live.runtime import Input, Runtime
from amplifier_web.attachments import save, encode
from amplifier_web.host.storage import SessionStore


async def run():
    temporary = tempfile.TemporaryDirectory()
    home = Path(temporary.name)
    os.environ['AMPLIFIER_WEB_HOME'] = str(home)
    store = SessionStore(home / 'native')
    media = store.media('attachment-fixture')
    cap = None

    async def before_initialize(session):
        nonlocal cap
        cap = bind_session_media(session.coordinator, store=media, enabled=True)

    plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False}}, 'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent for name in ('loop-live', 'context-simple')}
    prepared = PreparedBundle(plan, BundleModuleResolver(paths), Bundle(name='attachment-fixture', session=plan['session']))
    session = await prepared.create_session(session_id='attachment-fixture', before_initialize=before_initialize)
    runtime = Runtime(session.session_id)
    session.coordinator.register_capability('live.runtime', runtime)
    session.coordinator.register_capability('live.attachments.encode', encode)
    checkpoints = []

    async def checkpoint():
        rows = await session.coordinator.get('context').get_messages()
        canonical = store.save(session.session_id, rows, {})
        receipt = store.acknowledge_media(session.session_id, canonical, media)
        checkpoints.append(receipt)
        return True

    session.coordinator.register_capability('session.durable_checkpoint', checkpoint)

    class Provider:
        name = 'fixture'
        requests = []
        seen_images = []
        def get_info(self):
            return ProviderInfo(id='fixture', display_name='Fixture', defaults={'model': 'fixture-model'})
        def get_media_request_support(self, *, model=None, request_options=None):
            return {'v': 1, 'positions': ['user']}
        def parse_tool_calls(self, response):
            return []
        async def complete(self, request, **kwargs):
            assert checkpoints, 'Provider cannot precede the actual settled checkpoint'
            cap.validate(request, purpose='transport', request_options=kwargs)
            # Projection lifetime ends when complete returns; inspect/copy
            # transport facts here, never retain released private buffers.
            self.requests.append([(m.role, type(m.content).__name__) for m in request.messages])
            for message in request.messages:
                for block in message.content if isinstance(message.content, list) else []:
                    if getattr(block, 'type', None) == 'image':
                        self.seen_images.append(base64.b64decode(block.source['data']))
            return ChatResponse(content=[TextBlock(text='Image received')], usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12))

    provider = Provider()
    projected = MediaPreparedProvider(provider, cap,
        provider_identity={'provider': 'fixture', 'account': 'fixture', 'generation': 'fixture'},
        model='fixture-model')
    await session.coordinator.mount('providers', projected, name='fixture')
    task = None
    try:
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (2, 3), color='navy').save(stream, format='PNG')
        data = stream.getvalue()
        encoded = base64.b64encode(data).decode()
        attachment = save(home, 'tiny.png', encoded)
        task = asyncio.create_task(session.execute(''))
        await runtime.wait_for(lambda e: e['type'] == 'session.ready', timeout=8)
        await runtime.submit(Input('user', 'Please review the attached files.', id='image-only', attachments=(attachment,)))
        event = await runtime.wait_for(lambda e: e['type'] in {'generation.finished', 'generation.failed'}, timeout=8)
        assert event['type'] == 'generation.finished', event
        assert provider.requests
        assert provider.seen_images == [data], provider.requests
        rows = await session.coordinator.get('context').get_messages()
        assert encoded not in json.dumps(rows)
        assert encoded not in json.dumps(runtime.events)
        saved = store.load(session.session_id)[0]
        admitted = next(row for row in saved if (row.get('metadata') or {}).get('amplifier_input', {}).get('id') == 'image-only')
        ref = admitted['metadata']['experimental.media.v1']['refs'][0]
        assert ref['provenance']['id'] == attachment['id']
        assert ref['provenance']['expires_at'] is None
        assert encoded not in (store.directory(session.session_id) / 'transcript.jsonl').read_text()
        await runtime.submit(Input('stop'))
        await asyncio.wait_for(task, 5)
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await projected.aclose()
        await session.cleanup()
        media.close()
        temporary.cleanup()
    print(json.dumps({'image_received': True, 'canonical_reference_only': True,
                      'actual_checkpoint_before_provider': True, 'public_events_contain_no_image_data': True,
                      'provider_network_calls': 0}))

asyncio.run(run())
