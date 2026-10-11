"""Synthetic, disposable workspace/storage/attention acceptance fixture."""
import asyncio
import json
from pathlib import Path
import tempfile

from aiohttp import web
import settings_ui_server as fixture
from amplifier_web.event_log_view import event_path


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    workspace = home / 'workspaces' / 'cleanup-demo'
    workspace.mkdir(parents=True)
    (workspace / 'notes.txt').write_text('Synthetic cleanup fixture\n')
    (workspace / 'nested').mkdir()
    (workspace / 'nested' / 'data.txt').write_text('Drill in and back\n')
    await service.dispatch('workspace.add', {'path': str(workspace), 'name': 'Cleanup demo'})
    wid = next(row['id'] for row in service.state['workspaces'] if row.get('path') == str(workspace))
    await service.dispatch('session.create', {'title': 'Plan demo', 'workspace': str(workspace)})
    source = service._session()
    sid = source['id']
    source['messages'] = [{'id': 'fixture-message', 'role': 'assistant', 'text': 'This conversation will be retained.'}]
    path = event_path(source, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for index, status in enumerate(['pending', 'completed']):
        records += [{'event': event, 'timestamp': 10+index*2+offset, 'data': {
            'session_id': sid, 'tool_name': 'todo', 'tool_call_id': f'plan-{index}', **data}}
            for offset, (event, data) in enumerate([
                ('tool:pre', {'tool_input': {'action': 'update', 'todos': [{'content': 'Review the demo', 'status': status}]}}),
                ('tool:post', {'result': {'success': True, 'output': {'status': 'updated', 'count': 1}}}),
            ])]
    path.write_text(''.join(json.dumps(row)+'\n' for row in records))
    service.state['view'].update(panel=None, workSurface='chat')
    service._publish()

    async def info(request):
        return web.json_response({'sessionId': sid, 'workspaceId': wid, 'workspace': str(workspace),
                                  'filesExist': workspace.exists()})
    app.router.add_get('/api/fixture/readiness', info)
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
    with tempfile.TemporaryDirectory(prefix='unified-readiness102-') as temp:
        asyncio.run(main(Path(temp)))
