"""Browser fixture uses the real saved-task controller and shared action path."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from aiohttp import web
import settings_ui_server as fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_task_continuity import controls


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.worker = controls('fixture-task')
    async def control(self, sid, operation, args):
        if operation.startswith('task.'): return await self.worker.perform(operation, args)
        return await super().control(sid, operation, args)
    async def close(self): await self.worker.close()


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    runtime = Runtime()
    service.runtime = runtime
    app['runtime'] = runtime
    sid = service._session()['id']
    await service.dispatch('view.update', {'patch': {'panel': 'settings', 'settingsSection': 'runtime', 'runtimeDraft': {'tab': 'direction'}}})
    async def restart(request):
        await runtime.worker.close()
        runtime.worker = controls('fixture-task')
        await runtime.worker.restore()
        return web.json_response((await service.app_bridge('dispatch', {'action': 'task.get', 'args': {}}, sid))['result'])
    async def agent(request):
        current = runtime.worker.tasks.record()
        return web.json_response((await service.app_bridge('dispatch', {'action': 'task.update', 'id': 'agent-correction', 'args': {'expectedRevision': current['revision'], 'correction': 'Retain the approved source correction', 'operationIds': ['uncertain-operation'], 'artifactRefs': ['report.md']}}, sid))['result'])
    app.router.add_post('/fixture/restart', restart)
    app.router.add_post('/fixture/agent', agent)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': sid}), flush=True)
    try: await asyncio.Event().wait()
    finally: await runner.cleanup()

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-task-continuity-') as temp: asyncio.run(main(Path(temp)))
