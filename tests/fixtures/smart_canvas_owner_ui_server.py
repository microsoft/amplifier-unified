"""Two local MCP Apps; hold one document after its actual SDK resource read."""
import asyncio
import json
import os
import signal
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home, documents):
    os.environ.update(AMPLIFIER_HOME=str(home / 'shared'),
                      AMPLIFIER_WEB_HOME=str(home / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home / 'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app
    from configured_browser import seed_connection
    seed_connection(home / 'shared')
    workspace = home / 'workspace'
    workspace.mkdir()
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=workspace, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    await service.dispatch('session.create', {'title': 'Tool owner test'})
    sid = service._session()['id']
    service._message(service._session(), 'assistant', 'Original saved conversation.', 'text')
    service._publish()
    for identity in ('alpha', 'bravo'):
        await service.smart_tools.execute('smartTools.configure', {
            'id': identity, 'name': identity.title() + ' counter', 'command': sys.executable,
            'args': [str(Path(__file__).with_name('canvas_mcp_server.py')),
                     str(documents / (identity + '.html'))]})
        await service.smart_tools.execute('smartTools.connect', {'id': identity})
    gate = asyncio.Event()
    held = {'identity': 'alpha', 'enabled': True, 'entered': False, 'released': False}
    original_read = service.smart_tools.read_app

    async def read_app(identity, uri):
        result = await original_read(identity, uri)
        if identity == held['identity'] and held['enabled']:
            held['enabled'] = False
            held['entered'] = True
            await gate.wait()
            held['released'] = True
        return result

    service.smart_tools.read_app = read_app

    async def inspect(request):
        return web.json_response({
            'held': held, 'started': runtime.started, 'sent': runtime.sent,
            'messages': service._session(sid)['messages'],
            'operations': service.smart_tools.state['operations'],
        })

    async def release(request):
        gate.set()
        return web.json_response({'released': True})

    async def arm(request):
        payload = await request.json()
        assert payload['identity'] in {'alpha', 'bravo'}
        gate.clear()
        held.update(identity=payload['identity'], enabled=True, entered=False, released=False)
        return web.json_response(held)

    app.router.add_get('/fixture/observations', inspect)
    app.router.add_post('/fixture/release', release)
    app.router.add_post('/fixture/hold', arm)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': sid}), flush=True)
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await stopped.wait()
    finally:
        gate.set()
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='smart-canvas-owner-') as home:
        asyncio.run(main(Path(home), Path(sys.argv[1])))
