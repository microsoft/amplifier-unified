"""Real private host and loopback publisher; no provider or public deployment."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home / 'shared'), AMPLIFIER_WEB_HOME=str(home / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home / 'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app

    workspace = home / 'workspace'
    output = workspace / 'dist'
    output.mkdir(parents=True)
    (output / 'index.html').write_text('<!doctype html><title>Fixture release</title><h1>First release</h1>')
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=workspace, runtime=runtime, voice=False,
                           background_updates=False, preload_providers=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']
    runtime.service = service
    await service.dispatch('session.create', {'title': 'Publishing task'})
    sid = service._session()['id']
    await service.dispatch('view.update', {'patch': {'draft': 'Keep publishing draft'}})
    calls = []
    original_dispatch = service.dispatch

    async def dispatch(action, args=None, *positional, **kwargs):
        if action.startswith('publishing.'):
            calls.append({'action': action, 'args': dict(args or {})})
        return await original_dispatch(action, args, *positional, **kwargs)

    service.dispatch = dispatch

    async def inspect(request):
        listing = await original_dispatch('publishing.list', {'sessionId': sid})
        return web.json_response({'selected': service.state['selectedSessionId'],
                                 'draft': service.state['view']['draft'], 'sent': runtime.sent,
                                 'started': runtime.started, 'calls': calls,
                                 'publishing': listing['result']})

    async def change(request):
        (output / 'index.html').write_text('<!doctype html><title>Fixture release</title><h1>Second release</h1>')
        return web.json_response({'changed': True})

    async def fail_deploy(request):
        original_put = service.publishing.store._put
        def interrupted(kind, record):
            if kind == 'site':
                service.publishing.store._put = original_put
                raise RuntimeError('Injected fixture failure after listener mutation')
            return original_put(kind, record)
        service.publishing.store._put = interrupted
        return web.json_response({'armed': True})

    app.router.add_post('/fixture/fail-deploy', fail_deploy)
    app.router.add_get('/fixture', inspect)
    app.router.add_post('/fixture/change', change)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': sid}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='publishing-fixture-') as directory:
        asyncio.run(main(Path(directory).resolve()))
