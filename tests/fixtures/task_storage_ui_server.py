"""Actual task-storage files and receipts in one disposable browser workspace."""
import asyncio
import hashlib
import json
import os
import signal
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
    # Optional real OS denial at the first managed-path write. This is separate
    # from agent file-policy admission, which human UI intent does not inherit.
    deny = os.environ.get('TASK_STORAGE_DENY_WORKSPACE') == '1'
    modes = {path: path.stat().st_mode & 0o777 for path in
             [workspace, workspace / '.amplifier'] if path.is_dir()}
    if deny:
        assert not (workspace / '.amplifier' / 'task-storage').exists()
        for path in modes:
            path.chmod(0o500)

    def contents():
        return {str(path.relative_to(workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in workspace.rglob('*') if path.is_file()}

    def authorized(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()

    async def facts(request):
        authorized(request)
        rows = task_resources.read(home, sid, {'limit': 32})['items']
        return web.json_response({'sessionId': sid, 'workspace': str(workspace),
            'files': contents(), 'writeDenied': deny,
            'managedPathExists': (workspace / '.amplifier' / 'task-storage').exists(),
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
    stopped = asyncio.Event()
    for event in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(event, stopped.set)
    try:
        await stopped.wait()
    finally:
        for path, mode in modes.items():
            path.chmod(mode)
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='readiness-task-storage-') as temporary:
        asyncio.run(main(Path(temporary)))
