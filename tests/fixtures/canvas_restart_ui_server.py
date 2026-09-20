"""Disposable production host that can restart over the same test directory."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home, port):
    os.environ.update(AMPLIFIER_HOME=str(home / 'shared-settings'),
                      AMPLIFIER_WEB_HOME=str(home / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home / 'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app
    workspace = home / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    app = await create_app(home / 'app', workspace=str(workspace), runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    app['control_token'] = 'fixture-browser-control-token'
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    print(json.dumps({'url': url}), flush=True)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]), int(sys.argv[2])))
