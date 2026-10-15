"""Disposable snapshot sharing with controlled expiry; no external publication."""
import asyncio
import json
import tempfile
import time
from pathlib import Path

from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def start(self, session, emit, **kwargs):
        self.calls.append(('start', session['id']))
        raise AssertionError('Sharing must not start a worker')

    async def send(self, session, *args, **kwargs):
        self.calls.append(('send', session['id']))
        raise AssertionError('Sharing must not send model input')


async def main(home):
    fixture.Runtime = Runtime
    app = await fixture.main(home)
    service = app['service']
    source = service._session()
    source.update(historyManaged=False, historyLoaded=True, status='idle')
    service._message(source, 'user', 'Saved sharing question')
    service._message(source, 'assistant', 'Saved sharing answer\n' + 'Readable saved detail. ' * 900 + '\nEnd of saved answer')
    service.state['view']['panel'] = None
    service._publish()

    def authorize(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    async def observations(request):
        authorize(request)
        async with service.lock:
            rows = [json.loads(row[0]) for row in service.db.execute(
                'SELECT value FROM conversation_shares WHERE session_id=?', (source['id'],))]
            return web.json_response({'sessionId': source['id'], 'messages': len(source['messages']),
                'runtimeCalls': service.runtime.calls, 'snapshots': [service.conversation_library.describe(row) for row in rows]})

    async def append(request):
        authorize(request)
        async with service.lock:
            service._message(source, 'assistant', 'New answer after snapshot review')
            service._publish_changes(sessions={source['id']})
        return web.json_response({'ok': True})

    async def expire(request):
        authorize(request)
        args = await request.json()
        async with service.lock:
            token, row = service.conversation_library.snapshot(args['id'])
            if row['sessionId'] != source['id'] or not token:
                raise web.HTTPBadRequest()
            row['expiresAt'] = time.time() - 1
            service.conversation_library.save(row, token)
            service.db.commit()
        return web.json_response({'ok': True})

    app.router.add_get('/fixture/share', observations)
    app.router.add_post('/fixture/share/append', append)
    app.router.add_post('/fixture/share/expire', expire)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='standard-ux-share-') as temp:
        asyncio.run(main(Path(temp)))
