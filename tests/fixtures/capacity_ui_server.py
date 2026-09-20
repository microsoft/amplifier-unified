"""Real local service/controllers and provider instrumentation; synthetic usage."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from aiohttp import web
import settings_ui_server as fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_task_continuity import controls
from amplifier_web.execution_events import ExecutionEvents


class Runtime(fixture.Runtime):
    def __init__(self, sid):
        super().__init__()
        self.worker = controls(sid)
    async def control(self, sid, operation, args):
        if operation.startswith('capacity.'): return await self.worker.perform(operation, args)
        return await super().control(sid, operation, args)
    async def close(self): await self.worker.close()


async def main(home):
    app = await fixture.main(home)
    service = app['service']; sid = service._session()['id']
    runtime = Runtime(sid); service.runtime = runtime; app['runtime'] = runtime
    outputs = []
    class Provider:
        def __init__(self): self.calls = 0
        def get_info(self): return SimpleNamespace(id='Fixture provider', defaults={'model': 'fixture-model'})
        async def complete(self, request):
            self.calls += 1
            return SimpleNamespace(usage={'input_tokens': 8, 'output_tokens': 2})
    provider = Provider()
    async def configure():
        runtime.worker.capacity.admit = lambda row: service.app_bridge('capacity.admit', {'call': row}, sid)
        telemetry = ExecutionEvents(sid, outputs.append); telemetry.admission_guard = runtime.worker.capacity.guard
        telemetry.instrument_provider(sid, provider)
    await configure()
    await service.dispatch('view.update', {'patch': {'panel': 'settings', 'settingsSection': 'runtime', 'runtimeDraft': {'tab': 'limits'}, 'draft': 'Keep this draft'}})
    async def model(request):
        error = None
        try: await provider.complete(SimpleNamespace(model='fixture-model'))
        except ValueError as exc: error = str(exc)
        while outputs:
            await service.on_runtime_event('execution.event', outputs.pop(0)['event'])
        return web.json_response({'calls': provider.calls, 'error': error})
    async def restart(request):
        await runtime.worker.close(); runtime.worker = controls(sid); await runtime.worker.restore()
        return web.json_response((await service.app_bridge('dispatch', {'action': 'capacity.read', 'args': {}}, sid))['result'])
    async def agent(request):
        result = await service.app_bridge('dispatch', {'action': 'capacity.set', 'id': 'agent-budget', 'args': {'expectedRevision': runtime.worker.capacity.policy['revision'], 'maxTotalTokens': 100}}, sid)
        return web.json_response({'result': result['result'], 'draft': service.state['view']['draft']})
    app.router.add_post('/fixture/model', model)
    app.router.add_post('/fixture/restart', restart)
    app.router.add_post('/fixture/agent', agent)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': sid}), flush=True)
    try: await asyncio.Event().wait()
    finally: await runner.cleanup()

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-capacity-') as temp: asyncio.run(main(Path(temp)))
