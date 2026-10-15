"""Disposable managed deletion and active-owner cleanup controls; no runtime work."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.session_files import sessions_dir


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    records = {}
    old = time.time() - 90 * 86400
    for key, title in [('selected', 'Keep selected chat'), ('delete', 'Delete only this managed chat'),
                       ('active', 'Protected active managed chat'), ('main', 'Browser delete managed chat'),
                       ('old', 'Old cleanup candidate')]:
        await service.dispatch('session.create', {'title': title, 'location': {'kind': 'managed'}})
        row = service._session()
        row.update(createdAt=old, updatedAt=old)
        directory = Path(row['workspace'])
        (directory / 'result.txt').write_text('Owned synthetic file: ' + key)
        native = sessions_dir(directory) / row['id']
        native.mkdir(parents=True, exist_ok=True)
        (native / 'metadata.json').write_text(json.dumps({'session_id': row['id'], 'working_dir': str(directory), 'title': title}))
        (native / 'transcript.jsonl').write_text(json.dumps({'role': 'user', 'content': 'Synthetic saved history: ' + key}) + '\n')
        records[key] = {'id': row['id'], 'title': title, 'file': directory / 'result.txt', 'history': native / 'transcript.jsonl'}
        await service.dispatch('session.pin', {'id': row['id'], 'pinned': True})
    workspace = home / 'workspaces' / 'protected-cleanup'
    workspace.mkdir(parents=True)
    (workspace / 'keep.txt').write_text('Do not remove while active')
    await service.dispatch('workspace.add', {'path': str(workspace), 'name': 'Protected cleanup workspace'})
    wid = next(row['id'] for row in service.state['workspaces'] if row.get('path') == str(workspace))
    await service.dispatch('session.create', {'title': 'Workspace active owner', 'workspace': str(workspace)})
    workspace_owner = service._session()['id']
    service._session(records['active']['id'])['status'] = 'working'
    service._session(workspace_owner)['status'] = 'working'
    await service.dispatch('session.select', {'id': records['selected']['id']})
    service.state['view'].update(panel=None, workSurface='chat')
    service._publish_full(reason='Synthetic managed cleanup fixture')

    def checksum(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    async def info(request):
        return web.json_response({'records': {key: {**{k: v for k, v in value.items() if k not in {'file', 'history'}},
            'fileSha': checksum(value['file']), 'historySha': checksum(value['history']),
            'present': any(row['id'] == value['id'] for row in service.state['sessions'])} for key, value in records.items()},
            'workspace': {'id': wid, 'path': str(workspace), 'owner': workspace_owner, 'fileSha': checksum(workspace / 'keep.txt')},
            'cleanupPreview': service.state.get('cleanupPreview'),
            'deletions': [{'id': row[0], 'phase': row[1]} for row in service.db.execute('SELECT id,phase FROM managed_deletions')]})

    async def state(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()
        args = await request.json()
        if args.get('target') not in {'active', 'workspace'} or args.get('status') not in {'idle', 'working'}:
            raise web.HTTPBadRequest()
        sid = workspace_owner if args['target'] == 'workspace' else records['active']['id']
        async with service.lock:
            service._session(sid)['status'] = args['status']
            service._publish_changes(sessions={sid})
        return web.json_response({'ok': True})

    app.router.add_get('/api/fixture/managed-cleanup', info)
    app.router.add_post('/api/fixture/managed-cleanup/state', state)
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
    with tempfile.TemporaryDirectory(prefix='unified-managed-cleanup-') as temp:
        asyncio.run(main(Path(temp)))
