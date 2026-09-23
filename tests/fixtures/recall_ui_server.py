"""Real service/assets with synthetic saved text and no model invocation."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime

async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'),AMPLIFIER_WEB_HOME=str(home/'app'),AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app
    workspace=home/'workspace';workspace.mkdir()
    runtime=Runtime()
    app=await create_app(home/'app',workspace=workspace,runtime=runtime,voice=False,background_updates=False)
    app['control_token']='fixture-browser-control-token';service=app['service']
    await service.dispatch('session.create',{'title':'Original design decision'})
    source=service._session();service._message(source,'user','The approved paint decision is indigo.','text',inputOrigin='ui');service._publish()
    await service.dispatch('session.create',{'title':'Current work'})
    sid=service._session()['id']
    await service.dispatch('view.update',{'patch':{'draft':'Preserve this draft'}})
    async def inspect(request):
        return web.json_response({'selected':service.state['selectedSessionId'],'draft':service.state['view']['draft'],
            'sent':runtime.sent,'sourceText':source['messages'][0]['text'],'memories':service.recall.store.list_memories(None)['items'],
            'personalization':service.recall.personalization.status(service._session(sid))})
    app.router.add_get('/fixture',inspect)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins']=app['allowed_origins']|{url}
    print(json.dumps({'url':url,'sessionId':sid}),flush=True)
    try:await asyncio.Event().wait()
    finally:await runner.cleanup()

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='recall-fixture-') as home:asyncio.run(main(Path(home)))
