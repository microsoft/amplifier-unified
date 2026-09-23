"""Production UI, synthetic voice signaling, actual bounded capture service."""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from empty_host_ui_server import Runtime


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'),AMPLIFIER_WEB_HOME=str(home/'app'),AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'))
    from aiohttp import web

    from amplifier_web.server import create_app
    runtime=Runtime();app=await create_app(home/'app',workspace=str(home),runtime=runtime,voice=False,background_updates=False,preload_providers=False)
    service=app['service'];runtime.service=service;app['control_token']='fixture-browser-control-token'
    class Manager:
        api_key=True
        call=None
        async def end(self,*args):
            if self.call:self.call.closed=True
            await service.set_voice_status({'status':'ended'})
            return {'closed':True}
    service.voice_service=Manager()
    class SyntheticNative:
        calls=[]
        async def run(self, operation):
            self.calls.append(operation)
            if operation=='status': return {'available':True,'status':'ready','permission':'granted'}
            return {'image':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC',
                    'capturedAt':time.time(),'window':{'id':'fixture-42','title':'Synthetic native window','application':'Fixture application','bounds':[0,0,1,1]}}
    service.voice_visual.native=SyntheticNative()
    original_visual = service.computer_visual.for_client
    def computer_visual(*args, **kwargs):
        visual = original_visual(*args, **kwargs)
        visual.native = service.voice_visual.native
        return visual
    service.computer_visual.for_client = computer_visual
    async def config(request):return web.json_response({'available':True})
    async def connect(request):
        data=await request.json();sid=data['sessionId'];call=SimpleNamespace(id='fixture-call',session_id=sid,closed=False,closing=False,client_id=service.clients.current.get())
        service.voice_service.call=call
        await service.set_voice_status({'id':call.id,'sessionId':sid,'status':'connecting'})
        return web.json_response({'id':call.id,'sessionId':sid,'sdp':'synthetic-answer','provider':'synthetic','model':'Synthetic voice fixture'})
    async def ready(request):await service.set_voice_status({'status':'connected'});return web.json_response({'ready':True})
    async def end(request):return web.json_response(await service.voice_service.end())
    async def inspect(request):
        return web.json_response({'grant':service.voice_visual.grant,'receipts':list(service.voice_visual.receipts.values()),'sent':runtime.sent,'stopped':runtime.stopped,'nativeCalls':service.voice_visual.native.calls,'computerGrants':[v.grant for v in service.computer_visual.clients.values() if v.grant],'computerReceipts':[r for v in service.computer_visual.clients.values() for r in v.receipts.values()]})
    async def activity(request):
        data=await request.json();session=service._session(data['sessionId'])
        session['status']=data.get('status','idle');session['workers']=data.get('workers',[])
        service._publish()
        return web.json_response({'ok':True})
    async def agent(request):
        payload=await request.json()
        return web.json_response(await service.app_bridge('dispatch',payload,payload.get('sessionId') or service.voice_service.call.session_id))
    app.router.add_get('/api/voice/config',config);app.router.add_post('/api/voice/connect',connect);app.router.add_post('/api/voice/ready',ready);app.router.add_post('/api/voice/end',end)
    app.router.add_get('/fixture',inspect);app.router.add_post('/fixture/agent',agent);app.router.add_post('/fixture/activity',activity)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    app['allowed_origins']=app['allowed_origins']|{url};print(json.dumps({'url':url}),flush=True)
    try:await asyncio.Event().wait()
    finally:await runner.cleanup()
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='voice-visual-fixture-') as home:asyncio.run(main(Path(home)))
