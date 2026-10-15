"""Actual task-storage files and receipts in one disposable browser workspace."""
import asyncio
import json
from pathlib import Path
import tempfile

from aiohttp import web
from test_environments_ui_server import main as environment_app, TOKEN
from amplifier_web import task_resources


async def main(home):
    app = await environment_app(home, 0)
    service = app['service']
    sid = service.state['selectedSessionId']
    workspace = Path(service._session(sid)['workspace'])

    def authorized(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()

    async def facts(request):
        authorized(request)
        rows = task_resources.read(home, sid, {'limit': 32})['items']
        return web.json_response({'sessionId': sid, 'workspace': str(workspace),
            'workspaceId': next(row['id'] for row in service.state['workspaces'] if row['path'] == str(workspace)),
            'items': [{**row, 'exists': Path(row['path']).exists(),
                'marker': (Path(row['directory']) / 'retained.txt').read_text()
                    if (Path(row['directory']) / 'retained.txt').exists() else None} for row in rows]})

    async def marker(request):
        authorized(request)
        data = await request.json()
        row = task_resources.read(home, sid, {'id': data['id']})['items'][0]
        root = Path(row['directory'])
        assert root.is_relative_to(workspace / '.amplifier' / 'task-storage')
        (root / 'retained.txt').write_text('Synthetic contents must survive retirement')
        return web.json_response({'ok': True})

    app.router.add_get('/fixture/storage', facts)
    app.router.add_post('/fixture/storage-marker', marker)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='readiness-task-storage-') as temporary:
        asyncio.run(main(Path(temporary)))
