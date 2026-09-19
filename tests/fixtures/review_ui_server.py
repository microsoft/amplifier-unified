"""Issue regression fixture; isolated state, synthetic completion, no providers."""
import asyncio
import json
import tempfile
from pathlib import Path
from aiohttp import web
import settings_ui_server as fixture

async def main():
    with tempfile.TemporaryDirectory(prefix='amplifier-review-ui-') as directory:
        app=await fixture.main(Path(directory));service=app['service']
        work=Path(service.state['settings']['workspace'])
        (work/'large.html').write_text('<h1>Large saved preview</h1><button onclick="this.textContent=\'Interactive\'">Run</button><!--'+'x'*3_800_000+'-->')
        async def complete(request):
            data=await request.json();sid=data['sessionId'];gid=data['generationId']
            await service.on_runtime_event('assistant.message',{'sessionId':sid,'text':'Completed '+gid+'\n\n'+'A useful result.\n\n'*70,'inputId':gid})
            await service.on_runtime_event('runtime.generation',{'sessionId':sid,'event':'generation.finished','generation_id':gid,'input_ids':[gid],'active_job_ids':[],'text':'Completed '+gid})
            return web.json_response({'ok':True})
        app.router.add_post('/api/fixture/complete',complete)
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
        print(json.dumps({'port':port}),flush=True)
        try:await asyncio.Event().wait()
        finally:await runner.cleanup()

if __name__=='__main__':asyncio.run(main())
