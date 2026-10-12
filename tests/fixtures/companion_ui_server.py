"""Synthetic host for native-to-browser chat navigation acceptance."""
import asyncio
import json
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    original = service._session()['id']
    await service.dispatch('session.create', {
        'title': 'Companion destination', 'workspace': str(home / 'workspace'),
        'bundle': 'anchors',
    })
    destination = service._session()['id']
    await service.dispatch('session.select', {'id': original})
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'original': original, 'destination': destination}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-companion-ui-') as temp:
        asyncio.run(main(Path(temp)))
