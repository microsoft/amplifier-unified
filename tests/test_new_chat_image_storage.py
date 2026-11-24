import asyncio
import base64
import copy

import pytest

from amplifier_web import attachments
from amplifier_web.host import media
from amplifier_web.service import AppService
from test_service import Runtime as ServiceRuntime


async def test_only_new_root_chats_in_the_opted_in_private_home_keep_the_choice(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_HOME', str(home / 'native'))
    monkeypatch.delenv('AMPLIFIER_UNIFIED_COMPACT_CHAT_IMAGES', raising=False)
    app = AppService(home, ServiceRuntime(), workspace=tmp_path)
    async def create(**args):
        return (await app.dispatch('session.create', args, include_state=False))['sessionId']
    try:
        old = await create()
        monkeypatch.setenv('AMPLIFIER_UNIFIED_COMPACT_CHAT_IMAGES', '1')
        new = await create()
        internal = await create(purpose='terminal-tool')
        monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
        shared = await create()
        monkeypatch.delenv('AMPLIFIER_UNIFIED_COMPACT_CHAT_IMAGES')
    finally:
        await app.close()
    restored = AppService(home, ServiceRuntime(), workspace=tmp_path)
    try:
        marked = restored._session(new)
        assert marked['composerImageReferences'] is True
        assert attachments.encoder_for_session(marked) is attachments.encode
        monkeypatch.setenv('AMPLIFIER_HOME', str(home / 'native'))
        assert attachments.encoder_for_session(marked) is not attachments.encode
        for sid in (old, internal, shared):
            session = restored._session(sid)
            assert 'composerImageReferences' not in session
            assert attachments.encoder_for_session(session) is attachments.encode
    finally:
        await restored.close()


async def test_ordinary_inline_provider_sees_only_the_same_admitted_composer_upload(tmp_path, monkeypatch):
    from amplifier_core.message_models import ChatRequest, Message
    from amplifier_module_loop_live.runtime import Input, Runtime
    from amplifier_web.surface_delivery import SurfaceProvider
    from fixtures.narrow_tool_image_fixture import MediaOnlyDelivery
    from test_narrow_tool_images import picture
    from test_tool_image_consumer_join import ScriptedReadProvider, actual_root

    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_HOME', str(home / 'native'))
    path, pixels = picture(tmp_path)
    upload = attachments.save(home, 'upload.png', base64.b64encode(pixels).decode())
    provider = ScriptedReadProvider(path, read=False)
    session, store, checkpoint, *_ = await actual_root(tmp_path, provider)
    coordinator = session.coordinator
    runtime = Runtime(session.session_id)
    coordinator.register_capability('live.runtime', runtime)
    coordinator.register_capability('live.attachments.encode',
                                    attachments.encoder_for_session({'composerImageReferences': True}))
    command = Input('user', 'Look at this', attachments=({'id': upload['id']},))
    execution = asyncio.create_task(session.execute(''))
    try:
        await runtime.submit(command)
        event = await runtime.wait_for(lambda e: e['type'] in ('generation.finished', 'generation.failed'), timeout=10)
        assert event['type'] == 'generation.finished'
        assert provider.observed == [[('user', None, pixels)]]
        rows = await checkpoint()
        issued = next(row for row in rows if media.NAMESPACE in (row.get('metadata') or {}))
        canonical = copy.deepcopy(issued)
        assert base64.b64encode(pixels).decode() not in str(issued)

        class InlineProvider:
            name = 'ordinary-inline'
            default_model = 'offline'
            async def request_budget(self, request, **kwargs):
                self.fitted = request
                return {'input_tokens': 7}

            async def complete(self, request, **kwargs):
                assert self.fitted is request
                return [base64.b64decode(block.source['data']) for block in request.messages[0].content
                        if block.type == 'image']

        legacy = InlineProvider()
        selected = SurfaceProvider(legacy, MediaOnlyDelivery(), coordinator=coordinator)
        def request(row):
            return ChatRequest(messages=[Message(**row)])
        original = request(issued)
        await selected.request_budget(original)
        assert media.NAMESPACE not in legacy.fitted.messages[0].metadata
        assert await selected.complete(original) == [pixels]
        saved, _ = store.load(session.session_id)
        assert next(row for row in saved if media.NAMESPACE in (row.get('metadata') or {})) == canonical

        forged = copy.deepcopy(issued)
        forged['metadata']['amplifier_input']['id'] = 'unrelated'
        refused = request(forged)
        await selected.request_budget(refused)
        assert 'Image unavailable' in legacy.fitted.model_dump_json()
        assert await selected.complete(refused) == []

        legacy.get_media_request_support = lambda **kwargs: {'v': 1, 'positions': []}
        refused = request(issued)
        await selected.request_budget(refused)
        assert await selected.complete(refused) == []
        del legacy.get_media_request_support
        guarded = request(issued)
        await selected.request_budget(guarded)
        native_store = coordinator.get_capability('web.media.store')
        original_policy = native_store.policy_verifier
        native_store.policy_verifier = lambda ref, **kw: False
        with pytest.raises(ValueError):
            await selected.complete(guarded)
        native_store.policy_verifier = original_policy
        await selected.request_budget(guarded)
        retained = next(block for block in legacy.fitted.messages[0].content if block.type == 'image')
        body = native_store.root / 'assets' / issued['metadata'][media.NAMESPACE]['refs'][0]['sha256']
        body.write_bytes(bytes(len(pixels)))
        with pytest.raises(ValueError):
            await selected.complete(guarded)
        assert retained.source == {}
    finally:
        if not execution.done():
            await runtime.submit(Input('stop'))
        await asyncio.wait_for(execution, 5)
        await session.cleanup()
