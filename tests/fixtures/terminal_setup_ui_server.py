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
    from amplifier_web.terminal_release import TerminalRelease
    import hashlib
    async def release(platform):
        await asyncio.sleep(.2)
        value = b'synthetic wheel - do not install'
        return TerminalRelease('0.9.0rc1', 'amplifier_app_tui-0.9.0rc1-py3-none-macosx_26_0_arm64.whl',
                               hashlib.sha256(value).hexdigest(), value)
    app['terminal_setup'].release = release
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
