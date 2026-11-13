"""Real Unified host and independent ACP peers; synthetic model-free history."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from amplifier_web.deployment import save_server_config
from amplifier_web.server import create_app
from test_acp_participant import operations, peer
from test_service import Runtime


async def serve(home):
    for key, folder in [('AMPLIFIER_HOME', 'native'), ('AMPLIFIER_WEB_HOME', 'app'),
                        ('AMPLIFIER_SESSION_STATE_HOME', 'sessions'),
                        ('AMPLIFIER_TERMINAL_HOME', 'terminals')]:
        os.environ[key] = str(home / folder)
    workspace = home / 'workspace'
    workspace.mkdir()
    agents = [{**peer(home / identity), 'id': identity, 'label': identity.title()}
              for identity in ('alpha', 'beta')]
    save_server_config(home / 'app', {'externalAgents': agents})
    runtime = Runtime()
    app = await create_app(home / 'app', workspace=workspace, runtime=runtime,
                          voice=False, background_updates=False,
                          preload_providers=False, background_work=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']

    async def observe(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        async with service.lock:
            return web.json_response({'nativeStarts': runtime.started, 'nativeSends': runtime.sent,
                'sessions': [{'id': row['id'], 'messages': row['messages'],
                              'externalAgent': row.get('externalAgent')}
                             for row in service._state['sessions']],
                'peers': {identity: operations(home / identity)
                          if (home / identity / 'peer/operations.jsonl').exists() else []
                          for identity in ('alpha', 'beta')}})

    app.router.add_get('/fixture/participants', observe)
    runner = web.AppRunner(app)
    await runner.setup()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        app['allowed_origins'] = app['allowed_origins'] | {url}
        print(json.dumps({'url': url, 'workspace': str(workspace)}), flush=True)
        await stopped.wait()
    finally:
        await runner.cleanup()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-multiparty-ui-') as directory:
        asyncio.run(serve(Path(directory)))
