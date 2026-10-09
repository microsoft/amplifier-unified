"""Unnamed native-style chats in synthetic, project-owned fixture state."""
import asyncio
import json
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as fixture
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_web.host.storage import SessionStore
from amplifier_web.naming import directory_for, read


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
    original_control = service.runtime.control
    async def control(sid, operation, args):
        if operation != 'session.naming':
            return await original_control(sid, operation, args)
        naming_calls.append(sid)
        before = read(directory_for(home, service._session(sid)))
        await asyncio.sleep(.2)
        return {**before, 'name': 'Neighborhood garden plan'}
    service.runtime.control = control
    async def check(request):
        return web.json_response({'unchanged': original == transcript.read_bytes(), 'namingCalls': len(naming_calls), 'title': service._session(source['id'])['title']})
    app.router.add_get('/fixture/check', check)
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
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='chat-title-ui-') as tmp:
        asyncio.run(main(Path(tmp)))
