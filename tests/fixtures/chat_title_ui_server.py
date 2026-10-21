"""Unnamed native-style chats in synthetic, project-owned fixture state."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from aiohttp import web
import settings_ui_server as fixture
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_web.host.storage import SessionStore
from amplifier_web.naming import directory_for, read
from amplifier_web.host.naming import LiveSessionNaming
from amplifier_core import HookRegistry
from amplifier_core.message_models import ChatResponse, TextBlock, Usage


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    source = service._session()
    service._message(source, 'user', 'Plan a neighborhood garden')
    store = SessionStore.for_app(home, source['workspace'])
    store.save(source['id'], [{'role': 'user', 'content': 'Plan a neighborhood garden'}], {})
    transcript = store.directory(source['id']) / 'transcript.jsonl'
    original = transcript.read_bytes()
    # Same leaf folder, distinct parents: exercise real workspace qualifiers.
    for parent in ('design', 'research'):
        path = home / parent / 'project'
        path.mkdir(parents=True)
        await service.dispatch('workspace.add', {'path': str(path)})
    source = service._session(source['id'])
    source.update(title='Conversation deadbeef', titleSource='native', nativeNameSource='fallback', autoName=True)
    metadata = SessionMetadataStore(store.directory(source['id']))
    metadata.history._save_metadata_unlocked({**metadata.read(), 'name': 'Conversation deadbeef', 'name_source': 'fallback'})
    naming_calls = []
    release_second = asyncio.Event()
    class Context:
        async def get_messages(self):
            return [{'role': 'user', 'content': 'Plan a neighborhood garden'}]
    class Provider:
        name = 'fixture'
        priority = 1
        def get_info(self):
            return SimpleNamespace(id='fixture', defaults={'model': 'synthetic'})
        async def complete(self, request, **kwargs):
            naming_calls.append({'model': request.model, 'prompt': str(request.messages[0].content)})
            assert request.max_output_tokens == 256 and request.metadata['stream'] is False
            if len(naming_calls) == 2:
                await release_second.wait()
            return ChatResponse(content=[TextBlock(text=json.dumps({
                'action': 'set', 'name': 'Neighborhood garden plan',
                'description': 'Design a shared neighborhood garden.'}))],
                usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
    context, provider = Context(), Provider()
    coordinator = SimpleNamespace(
        session_id=source['id'], config={'hooks': [], 'project_dir': source['workspace']},
        hooks=HookRegistry(), mount_points={'context': context},
        get=lambda key: {'context': context, 'providers': {'fixture': provider}}.get(key),
        get_capability=lambda key: None, register_cleanup=lambda callback: None)
    namer = LiveSessionNaming(coordinator, home, lambda _: None)
    original_control = service.runtime.control
    async def control(sid, operation, args):
        if operation != 'session.naming':
            return await original_control(sid, operation, args)
        assert sid == source['id']
        return await namer.suggest()
    service.runtime.control = control
    async def check(request):
        return web.json_response({'unchanged': original == transcript.read_bytes(), 'namingCalls': len(naming_calls), 'title': service._session(source['id'])['title']})
    async def release(request):
        release_second.set()
        return web.json_response({'released': True})
    app.router.add_get('/fixture/check', check)
    app.router.add_post('/fixture/release', release)
    service._publish()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] |= {url}
    print(json.dumps({'url': url, 'sessionId': source['id']}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        release_second.set()
        await namer.close()
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='chat-title-ui-') as tmp:
        asyncio.run(main(Path(tmp)))
