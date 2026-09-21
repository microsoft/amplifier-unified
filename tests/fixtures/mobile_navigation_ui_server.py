"""Isolated responsive navigation fixture, provider-free and dynamic-port safe."""
import asyncio
import os
from pathlib import Path
import tempfile
from aiohttp import web
import chat_ui_server


async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-mobile-navigation-') as tmp:
        app = await chat_ui_server.fixture.main(Path(tmp))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', int(os.environ.get('AMPLIFIER_TEST_PORT', '0')))
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
        print(f'http://127.0.0.1:{port}', flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
