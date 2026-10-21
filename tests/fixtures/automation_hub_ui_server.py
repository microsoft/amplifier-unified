"""Real schedule/host/browser, fixed clock, synthetic runtime and private state."""
import asyncio
import json
from pathlib import Path
import signal
import sys
import tempfile
import uuid
from aiohttp import web
import settings_ui_server as base
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_schedule_new_tasks import ConfiguredRuntime
from test_schedules import config
from test_task_continuity import controls


async def main(home):
    app = await base.main(home)
    service = app['service']
    await service.schedules.close()
    runtime, now = ConfiguredRuntime(), [1800000000.0]
    service.runtime = app['runtime'] = runtime
    service.schedules.clock = lambda: now[0]
    owner = service._session()
    await service.dispatch('session.rename', {'id': owner['id'], 'title': 'Exited report owner'})
    control = controls(owner['id'])
    control.coordinator.config = {'session': {'context': {'module': 'context-simple'}}, 'providers': [{'module': 'synthetic', 'config': {'default_model': 'test-model'}}]}
    control.selection = {'instance': 'synthetic', 'model': 'test-model'}
    control.persist(); await control.close()
    owner['selection'] = dict(control.selection)
    args = {'sessionId': owner['id'], **config(now[0]), 'destination': 'new_task', 'newTaskTitle': 'Scheduled report', 'newTaskMaxTurns': 1}
    preview = (await service.dispatch('schedule.preview', args))['result']
    record = (await service.dispatch('schedule.create', {**args, 'expectedRevision': 0, 'previewHash': preview['previewHash']}))['result']['schedule']
    owner['status'] = 'error'
    service.schedules.store.review(owner['id'], record['id'], 'The configuration needs human review.', now[0])
    for index in range(31):
        service.schedules.store.put({**record, 'id': str(uuid.uuid4()), 'status': 'paused', 'prompt': f'Paused synthetic automation {index+1}'})
    service.schedules.store.put({**record, 'id': str(uuid.uuid4()), 'status': 'cancelled', 'prompt': 'Cancelled synthetic automation'})
    service.schedules.changed()
    await service.dispatch('session.create', {'title': 'My current chat', 'workspace': owner['workspace'], 'bundle': 'anchors'})
    current = service._session()['id']
    warmed = []
    ensure = service.management.ensure_runtime
    async def track(session, *args, **kwargs):
        warmed.append(session['id'])
        return await ensure(session, *args, **kwargs)
    service.management.ensure_runtime = track
    def authorize(request):
        if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token': raise web.HTTPForbidden()
    async def inspect(request):
        authorize(request)
        return web.json_response({'inputs': runtime.inputs, 'warmed': warmed, 'ownerStatus': owner['status'], 'sessions': len(service.state['sessions']), 'schedule': service.schedules.store.get(owner['id'], record['id']), 'runs': service.schedules.store.runs(owner['id'], record['id'])})
    async def tick(request):
        authorize(request)
        now[0] = service.schedules.store.get(owner['id'], record['id'])['nextDue'] + 1
        await service.schedules.tick(); await service.schedules.tick()
        return web.json_response({'inputs': runtime.inputs, 'runs': service.schedules.store.runs(owner['id'], record['id'])})
    async def stop(request):
        authorize(request)
        owner.update(status='stopped', interruptionRevision=owner.get('interruptionRevision', 0)+1)
        service.schedules.store.review(owner['id'], record['id'], 'The user stopped this chat.', now[0])
        service.schedules.changed()
        return web.json_response({'stopped': True})
    async def empty(request):
        authorize(request)
        # Reset only this disposable fixture, never the user's scheduling ledger.
        with service.schedules.store.transaction():
            service.schedules.store.db.execute('DELETE FROM schedule_runs')
            service.schedules.store.db.execute('DELETE FROM schedules')
        control = controls(current)
        control.coordinator.config = {'session': {'context': {'module': 'context-simple'}}, 'providers': [{'module': 'synthetic', 'config': {'default_model': 'test-model'}}]}
        control.selection = {'instance': 'synthetic', 'model': 'test-model'}
        control.persist(); await control.close()
        service._session(current)['selection'] = dict(control.selection)
        service.schedules.changed()
        return web.json_response({'now': now[0], 'sessionId': current})
    async def journey(request):
        authorize(request)
        return web.json_response({'schedules': service.schedules.store.list(current), 'runs': service.schedules.store.runs(current), 'inputs': runtime.inputs})
    async def uncertain(request):
        authorize(request)
        saved = service.schedules.store.get(current, request.query['id']) if 'id' in request.query else service.schedules.store.list(current)[0]
        service.schedules.store.put_run({'id': 'uncertain-demo-run', 'sessionId': current, 'scheduleId': saved['id'], 'dueAt': now[0], 'phase': 'unknown', 'revision': 1, 'detail': 'Unconfirmed result; inspect effects, never replay.'})
        service.schedules.store.review(current, saved['id'], 'A prior run has an uncertain outcome.', now[0])
        service.schedules.changed()
        return web.json_response({'unknown': True})
    app.router.add_get('/fixture/inspect', inspect);app.router.add_post('/fixture/tick', tick);app.router.add_post('/fixture/stop', stop)
    app.router.add_post('/fixture/empty', empty)
    app.router.add_get('/fixture/journey', journey)
    app.router.add_post('/fixture/uncertain', uncertain)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins']=app['allowed_origins']|{url}
    print(json.dumps({'url':url,'ownerId':owner['id'],'sessionId':current,'scheduleId':record['id']}),flush=True)
    stopped=asyncio.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):asyncio.get_running_loop().add_signal_handler(sig,stopped.set)
    try:await stopped.wait()
    finally:await runner.cleanup()

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-automation-hub-') as temp:asyncio.run(main(Path(temp)))
