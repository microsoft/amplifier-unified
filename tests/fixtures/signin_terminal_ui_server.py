"""Actual login lifecycle with a disposable provider child and a shortened deadline."""
import asyncio
import json
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace

from aiohttp import web
import settings_ui_server as base
import amplifier_web.setup as setup


if len(sys.argv) > 1 and sys.argv[1] == 'provider-child':
    # The child only reads the synthetic request; it never creates credentials.
    json.loads(sys.stdin.readline())
    print(json.dumps({'status': 'waiting', 'instruction':
        'Open https://auth.openai.com/codex/device and enter code: TEST-EXPIRED'}), flush=True)
    if sys.argv[2] == 'denied':
        import time
        time.sleep(.3)
        print(json.dumps({'status': 'failed', 'error': 'Synthetic authorization denied'}), flush=True)
        sys.exit(1)
    import time
    time.sleep(60)
    sys.exit(2)


# Only this module's 900-second login deadline is accelerated. The production
# task, subprocess cleanup, terminal-state publication and UI remain unchanged.
setup.asyncio = SimpleNamespace(**{key: getattr(asyncio, key) for key in dir(asyncio)})
setup.asyncio.timeout = lambda seconds: asyncio.timeout(1 if seconds == 900 else seconds)
original_login = setup.SetupManager.start_login
attempts = []
managers = []


async def login(self, provider, args, workspace, scope):
    if self not in managers:
        managers.append(self)
    mode = 'expired' if not attempts else 'denied'
    attempts.append(mode)
    self.auth_command = [sys.executable, str(Path(__file__).resolve()), 'provider-child', mode]
    return await original_login(self, provider, args, workspace, scope)


setup.SetupManager.start_login = login


async def serve(home):
    app = await base.main(home)

    async def observations(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        rows = [row for manager in managers for row in manager.logins.values()]
        return web.json_response({'attempts': attempts,
            'statuses': [row['status'] for row in rows],
            'liveChildren': sum(row.get('process') is not None and row['process'].returncode is None for row in rows),
            'credentialFiles': len(list((home/'config'/'chatgpt-sign-in').rglob('*.json')))})

    app.router.add_get('/fixture/signin-observations', observations)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(url, flush=True)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='signin-terminal-ui-') as folder:
        asyncio.run(serve(Path(folder)))
