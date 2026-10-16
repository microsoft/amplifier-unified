"""Local settings/feedback recovery evidence; no provider or outbound delivery."""
import asyncio
import copy
import json
import signal
import tempfile
from pathlib import Path

from aiohttp import web
from settings_ui_server import main as settings_app
from amplifier_web import attention, attachments

TOKEN = 'fixture-browser-control-token'


async def main(home):
    app = await settings_app(home)
    service = app['service']
    original = copy.deepcopy(service.state['sessions'])
    service.state['notificationError'] = 'Synthetic delivery notice one'
    service.state['updates']['error'] = 'Synthetic unrelated update failure'

    async def facts(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()
        rows = []
        for request_id, metadata in service.db.execute(
                'SELECT request_id,metadata FROM feedback_attachments'):
            item = json.loads(metadata)
            path, _ = attachments.file_path(service.data_dir, item['id'])
            rows.append({'requestId': request_id, **item,
                         'bytes': path.read_bytes().decode()})
        return web.json_response({'attention': attention.snapshot(service.state),
            'attentionRead': service.state.get('attentionRead', {}),
            'attachments': rows, 'sessionsUnchanged': service.state['sessions'] == original})

    app.router.add_get('/fixture/ordinary-recovery', facts)
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
        await runner.cleanup()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='ordinary-recovery-') as temporary:
        asyncio.run(main(Path(temporary)))
