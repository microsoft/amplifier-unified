"""Isolated real setup/enrollment routes, synthetic wheel, no provider or installs."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home / 'amplifier'), AMPLIFIER_WEB_HOME=str(home / 'app'))
    from aiohttp import web
    from amplifier_web.server import create_app
    app = await create_app(home / 'app', workspace=home, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False)
    app['control_token'] = 'fixture-browser-control-token'
    async def wheel(platform):
        await asyncio.sleep(.2)
        return b'synthetic wheel - do not install'
    app['terminal_setup'].wheel = wheel
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
    app['allowed_origins'] = app['allowed_origins'] | {url}
    app['terminal_setup'].allowed_origins = app['allowed_origins']
    print(json.dumps({'url': url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-terminal-feedback-') as temporary:
        asyncio.run(main(Path(temporary)))
