"""Synthetic host for native-to-browser chat navigation acceptance."""
import asyncio
import json
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web import auth


async def main(home):
    app = await fixture.main(home)
    auth.authenticate_pam = lambda user, password: (user, password) == ('fixture-user', 'fixture-only')
    service = app['service']
    original = service._session()['id']
    await service.dispatch('session.create', {
        'title': 'Companion destination', 'workspace': str(home / 'workspace'),
        'bundle': 'anchors',
    })
    destination = service._session()['id']
    await service.dispatch('session.select', {'id': original})
    deleted = '00000000-0000-4000-8000-000000000001'
    await service.dispatch('session.create', {'id': deleted, 'title': 'Deleted target'})
    service.state['sessions'][:] = [row for row in service.state['sessions'] if row['id'] != deleted]
    # Eviction is not deletion: remove the fixture's durable owner as well.
    service.forget_session_records({deleted})
    service._save()
    assert service.directory.resolve(deleted) is None
    await service.dispatch('session.select', {'id': original})
    service.projections.invalidate()
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
