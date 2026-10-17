"""Real host and persisted handoff receipts; synthetic recipient, no model or account."""
import asyncio
import json
from pathlib import Path
import signal
import tempfile
import uuid

from aiohttp import web
import settings_ui_server as fixture


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.started = []
        self.sent = []

    async def start(self, session, emit, *, preserve_emit=False):
        self.started.append(session['id'])
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})

    async def send(self, session, text, input_id, emit):
        self.sent.append({'sessionId': session['id'], 'inputId': input_id,
                         'text': text, 'workspace': session['workspace'],
                         'bundle': session.get('bundle'), 'selection': session.get('selection')})
        await emit('assistant.message', {'sessionId': session['id'], 'inputId': input_id,
                                         'text': 'Synthetic recipient accepted the brief.'})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    runtime = Runtime()
    service.runtime = app['runtime'] = runtime
    source = service._session()
    source['collaborationGeneration'] = {'id': 'fixture-user-turn', 'inputIds': ['fixture-request'], 'terminal': False}
    await service.dispatch('session.rename', {'id': source['id'], 'title': 'Handoff source'})
    await service.dispatch('session.create', {'title': 'Other window source',
        'workspace': source['workspace'], 'bundle': 'anchors', 'select': False})
    other = next(row for row in service.state['sessions'] if row['id'] != source['id'])
    targets = {name: home / name for name in ('target-project', 'removed-project')}
    for target in targets.values():
        target.mkdir()
    (targets['target-project'] / 'README.md').write_text('Synthetic acceptance input. No provider reads it.\n')

    def authorize(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    async def handoff(request, action):
        authorize(request)
        args = await request.json()
        # Each transport attempt has a fresh outer command ID, so retry proof
        # depends on the handoff request's durable identity, not command caching.
        result = await service.app_bridge('dispatch', {'action': action,
            'id': action + '-' + uuid.uuid4().hex,
            '_runtimeSessionId': source['id'], '_generationId': 'fixture-user-turn',
            '_inputBindings': [{'inputId': 'fixture-request'}], 'args': {
                'requestId': args['requestId'], 'sessionId': source['id'],
                'clientId': args['clientId'], 'workspace': str(targets[args.get('target', 'target-project')]),
                'title': args['title'], 'text': args['text'], 'bundle': 'anchors',
                'selection': {'instance': 'one', 'model': 'fixture-model'}}}, source['id'])
        return web.json_response(result)

    async def propose(request):
        return await handoff(request, 'session.handoff.propose')

    async def start(request):
        return await handoff(request, 'session.handoff.start')

    async def check(request):
        authorize(request)
        return web.json_response({'started': runtime.started, 'sent': runtime.sent,
            'sessions': [{key: row.get(key) for key in ('id', 'title', 'workspace', 'sessionKind',
                'parentId', 'bundle', 'selection', 'creationCommandId')} for row in service.state['sessions']]})

    async def remove_target(request):
        authorize(request)
        targets['removed-project'].rmdir()
        return web.json_response({'removed': str(targets['removed-project'])})

    app.router.add_post('/fixture/propose', propose)
    app.router.add_post('/fixture/start', start)
    app.router.add_get('/fixture/check', check)
    app.router.add_post('/fixture/remove-target', remove_target)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sourceId': source['id'], 'otherSourceId': other['id'],
                      'target': str(targets['target-project']), 'removedTarget': str(targets['removed-project'])}), flush=True)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-handoff786-') as directory:
        asyncio.run(main(Path(directory)))
