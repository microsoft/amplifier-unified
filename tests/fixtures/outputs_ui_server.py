"""Real host/assets and private Git fixture; no external provider or publication."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'),AMPLIFIER_WEB_HOME=str(home/'app'),AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'))
    from aiohttp import web
    from amplifier_web.server import create_app
    root=home/'workspace';root.mkdir();(root/'report.txt').write_text('Original report bytes.')
    def git(*args):subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
    git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.test')
    (root/'design.txt').write_text('before\n');git('add','design.txt');git('commit','-qm','Initial');(root/'design.txt').write_text('after\n')
    runtime=Runtime();app=await create_app(home/'app',workspace=root,runtime=runtime,voice=False,background_updates=False,preload_providers=False)
    app['control_token']='fixture-browser-control-token';service=app['service']
    await service.dispatch('session.create',{'title':'Output task'});sid=service._session()['id']
    original='A reusable draft:\n:::writing{variant="document" id="12345"}\nOriginal writing copy.\n:::'
    service._message(service._session(),'assistant',original,'text');service._publish()
    await service.dispatch('view.update',{'patch':{'draft':'Keep output draft'}})
    async def inspect(request):
        return web.json_response({'selected':service.state['selectedSessionId'],'draft':service.state['view']['draft'],'sent':runtime.sent,
            'message':service._session(sid)['messages'][0]['text'],'file':(root/'report.txt').read_text(),'outputs':service.outputs.store.list(sid,include_unlinked=True)['items']})
    async def change(request):
        (root/'report.txt').write_text('Later original-file edit.');return web.json_response({'changed':True})
    app.router.add_get('/fixture',inspect);app.router.add_post('/fixture/change',change)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins']=app['allowed_origins']|{url}
    print(json.dumps({'url':url,'sessionId':sid,'original':original}),flush=True)
    try:await asyncio.Event().wait()
    finally:await runner.cleanup()


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='outputs-fixture-') as home:asyncio.run(main(Path(home)))
