"""Real host + worker/loop + deterministic local provider, with a controllable clock."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def main(home):
    from aiohttp import web
    import yaml
    from amplifier_web.server import create_app
    from amplifier_web.schedules import Schedules
    import amplifier_web.runtime_worker as worker
    import amplifier_module_context_simple as context
    import amplifier_module_loop_live as loop

    workspace = home/'workspace'; workspace.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'), AMPLIFIER_WEB_HOME=str(home/'app'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(home/'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(home/'ownership'))
    module = home/'provider'/'amplifier_module_provider_fixture'; module.mkdir(parents=True)
    module.joinpath('__init__.py').write_text('''from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
class Provider:
    name='fixture'
    def get_info(self): return ProviderInfo(id='fixture',display_name='Local fixture',defaults={'model':'schedule-fixture'})
    async def list_models(self): return []
    async def complete(self,request,**kw):
        return ChatResponse(content=[TextBlock(text='SCHEDULED_NEW_TASK_RESULT')],usage=Usage(input_tokens=1,output_tokens=1,total_tokens=2))
    def parse_tool_calls(self,response): return []
async def mount(coordinator,config=None): await coordinator.mount('providers',Provider(),name='fixture')
''')
    bundle = home/'bundle.md'
    plan = {'bundle': {'name': 'new-task-fixture'}, 'session': {
        'orchestrator': {'module':'loop-live','source':str(Path(loop.__file__).parent.parent), 'config':{'max_iterations':1}},
        'context': {'module':'context-simple','source':str(Path(context.__file__).parent.parent)}},
        'providers':[{'module':'provider-fixture','source':str(module.parent)}]}
    bundle.write_text('---\n'+yaml.safe_dump(plan)+'---\nSynthetic scheduler fixture.\n')
    app = await create_app(home/'app', workspace=workspace, voice=False, background_updates=False, preload_providers=False)
    app['control_token'] = 'fixture-browser-control-token'
    service = app['service']; service.runtime.command = [sys.executable,str(Path(worker.__file__).resolve())]
    try:
        await service.schedules.close(); service.schedules.runner = None
        now = [time.time()]; service.schedules.clock = lambda: now[0]
        await service.dispatch('session.create', {'title':'Schedule owner','bundle':str(bundle),'workspace':str(workspace)})
        sid = service._session()['id']
        # Prepare the actual source configuration without sending model input.
        await service.dispatch('task.get', {'sessionId':sid})
        await service.dispatch('runtime.control', {'sessionId':sid,'operation':'provider.select','args':{'instance':'fixture','model':'schedule-fixture','effort':'high'}})
        events = []
        original = service.on_runtime_event
        async def observed(kind, data):
            if kind in {'assistant.message','runtime.generation'}: events.append({'kind':kind,**data})
            await original(kind,data)
        service.on_runtime_event = observed
        await service.dispatch('view.update', {'patch':{'panel':'settings','runtimeDraft':{'tab':'direction'}}})

        async def tick(request):
            record = service.schedules.store.list(sid)[0]
            now[0] = max(now[0]+1,record.get('nextDue') or now[0])
            await service.schedules.tick()
            return web.json_response({'runs':service.schedules.store.runs(sid)})
        async def inspect(request):
            return web.json_response({'runs':service.schedules.store.runs(sid), 'events':events,
                'sessions':[{'id':s['id'],'title':s['title'],'status':s['status'],'selection':s.get('selection'),'task':s.get('task')} for s in service.state['sessions']]})
        async def restart(request):
            service.schedules.store.close()
            service.schedules = Schedules(service, clock=lambda:now[0]); now[0]+=31
            await service.schedules.tick()
            return web.json_response({'runs':service.schedules.store.runs(sid)})
        app.router.add_post('/fixture/tick',tick); app.router.add_get('/fixture/inspect',inspect); app.router.add_post('/fixture/restart',restart)
        runner=web.AppRunner(app); await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
        url=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        app['allowed_origins']=app['allowed_origins']|{url}
        print(json.dumps({'url':url,'sessionId':sid,'now':now[0]}),flush=True)
        try: await asyncio.Event().wait()
        finally: await runner.cleanup()
    finally:
        if not service.closed: await service.close()


if __name__=='__main__':
    os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix='unified-new-tasks-') as folder: asyncio.run(main(Path(folder)))
    except KeyboardInterrupt: pass
