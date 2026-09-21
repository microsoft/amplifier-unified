"""Real HTTP/SSE delivery recovery with isolated data and a synthetic runtime."""
import asyncio
import json
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.checked = []
        self.retried = []
        self.known = set()

    async def send(self, session, text, input_id, emit):
        raise RuntimeError('Synthetic lost reply')

    async def delivery(self, session, input_id):
        self.checked.append(input_id)
        return 'accepted' if input_id in self.known else 'unknown'

    async def retry(self, session, text, input_id, emit):
        self.retried.append(input_id)
        if input_id in self.known:
            return {'accepted': True, 'duplicate': True}
        self.known.add(input_id)
        self.sent.append({'inputId': input_id, 'text': text})
        await emit('assistant.message', {'sessionId': session['id'], 'inputId': input_id, 'text': 'Recovery succeeded: one synthetic submission received.'})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
        return {'accepted': True}


fixture.Runtime = Runtime


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    service.state['updates'].update(items=[], available=0)
    await service.dispatch('session.rename', {'id': service._session()['id'], 'title': 'Delivery recovery fixture'})
    attachment = await service.dispatch('attachment.add', {'name': 'reference.txt', 'base64': 'UmV0YWluIHRoaXMgYXR0YWNobWVudC4='})
    attachment_id = attachment['state']['sessions'][0]['draftAttachments'][0]['id']
    try:
        await service.dispatch('conversation.send', {'text': 'Recover this synthetic message with its attachment.', 'attachmentIds': [attachment_id]}, command_id='original-fixture-input')
    except RuntimeError:
        pass
    async def inspect(request):
        return web.json_response({'sent': service.runtime.sent, 'checked': service.runtime.checked, 'retried': service.runtime.retried})
    app.router.add_get('/fixture', inspect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    print(json.dumps({'url': f'http://127.0.0.1:{port}'}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-delivery-ui-') as directory:
        asyncio.run(main(Path(directory)))
