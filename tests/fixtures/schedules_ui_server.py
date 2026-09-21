"""Actual local service, saved task controller, schedule store and fixed clock."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from aiohttp import web
import settings_ui_server as fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_schedules import Runtime, attach
from amplifier_web.schedules import Schedules


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    await service.schedules.close()  # Deterministic explicit ticks in this fixture.
    runtime, now = Runtime(), [1800000000.0]
    service.runtime = runtime; app['runtime'] = runtime
    service.schedules.clock = lambda: now[0]
    sid = service._session()['id']
    # Keep the real app Management/session ownership path; only the provider is synthetic.
    await service.dispatch('task.create', {'sessionId': sid, 'expectedRevision': 0, 'objective': 'Monitor the report and retain comparison evidence'}, command_id='task')
    await service.dispatch('view.update', {'patch': {'panel': 'settings', 'runtimeDraft': {'tab': 'direction'}}})
    async def advance(request):
        record = service.schedules.store.list(sid)[0]
        now[0] = max(now[0] + 1, record.get('nextDue') or now[0])
        await service.schedules.tick()
        return web.json_response({'runs': service.schedules.store.runs(sid), 'inputs': runtime.inputs})
    async def finish(request):
        data = await request.json()
        run = service.schedules.store.runs(sid)[0]
        args = {'runId': run['id'], 'expectedRevision': run['revision'], 'outcome': 'unchanged', 'detail': 'Compared reported count: 4', 'values': {'count': 4}}
        await service.app_bridge('dispatch', {'action': 'schedule.report', 'id': 'report-' + run['id'], 'args': args}, sid)
        await service.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished', 'generation_id': run['id'], 'input_ids': [run['inputId']], 'text': 'Report compared.', 'active_job_ids': []})
        await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
        return web.json_response({'run': service.schedules.store.run(sid, run['id']), 'notifications': service.state.get('scheduleNotifications', [])})
    async def restart(request):
        await service.schedules.close(); service.schedules.store.close()
        service.schedules = Schedules(service, clock=lambda: now[0]); now[0] += 31
        await service.schedules.tick()
        return web.json_response({'runs': service.schedules.store.runs(sid), 'inputs': runtime.inputs})
    app.router.add_post('/fixture/tick', advance)
    app.router.add_post('/fixture/finish', finish)
    app.router.add_post('/fixture/restart', restart)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app['allowed_origins'] = app['allowed_origins'] | {url}
    print(json.dumps({'url': url, 'sessionId': sid, 'now': now[0]}), flush=True)
    try: await asyncio.Event().wait()
    finally: await runner.cleanup()

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='unified-schedules-') as temp: asyncio.run(main(Path(temp)))
