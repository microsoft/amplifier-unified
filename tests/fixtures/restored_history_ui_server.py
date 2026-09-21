"""Real restarted host and saved native history; synthetic runtime only."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from empty_host_ui_server import Runtime


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home / 'amplifier'),
                      AMPLIFIER_WEB_HOME=str(home / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home / 'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app
    from amplifier_web.service import AppService
    from test_automatic_history import native_session, files_snapshot

    workspace = home / 'workspace'
    source = native_session(workspace, 'restored-chat')
    original = files_snapshot(source)
    previous = AppService(home / 'app', Runtime(), workspace=workspace)
    await previous.history.refresh()
    sid = next(row['id'] for row in previous.state['sessions'] if row.get('nativeIdentity') == 'restored-chat')
    previous.clients.attach('previous-browser')
    with previous.clients.bind('previous-browser'):
        previous.state['selectedSessionId'] = sid
        previous.clients.draft(sid, 'Saved unsent draft')
        previous._save()
    await previous.close()

    runtime = Runtime()
    app = await create_app(home / 'app', workspace=str(workspace), runtime=runtime,
                           voice=False, preload_providers=False, background_updates=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = runtime.service = app['service']
    await service.history.close()
    await service.history.refresh()
    assert service._session(sid)['historyLoaded'] is False
    gate = asyncio.Event()
    original_refresh = service.history.refresh_session

    async def delayed(identity):
        await gate.wait()
        await original_refresh(identity)

    service.history.refresh_session = delayed

    async def release(request):
        gate.set()
        return web.json_response({'ok': True})

    async def inspect(request):
        return web.json_response({'started': runtime.started, 'sent': runtime.sent,
                                  'originalsUnchanged': files_snapshot(source) == original})

    app.router.add_post('/fixture/release', release)
    app.router.add_get('/fixture', inspect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        gate.set()
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-restored-composer-') as directory:
        asyncio.run(main(Path(directory)))
