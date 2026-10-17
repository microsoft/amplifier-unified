"""Retained development app plus an owner-authenticated scripted review route.

Run only in an owned DTU. Uses a local synthetic provider, no account credentials.
"""
import asyncio
import hmac
import json
import os
from pathlib import Path
import signal
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from aiohttp import web
from voice_status_worker_probe import fixture
from voice_status_review import review


async def serve(root,port):
    workspace,bundle,gates=fixture(root)
    (gates/'release').touch()  # Ordinary preview chat uses the synthetic provider.
    from amplifier_web.server import create_app
    app=await create_app(Path(root)/'app',workspace=workspace,background_updates=False,preload_providers=False,
        server_config={'port':port,'bind':['127.0.0.1'],
                       'public_origins':['http://127.0.0.1:39473','http://localhost:39473']})
    service=app['service'];service.runtime.command=[sys.executable,str(ROOT/'amplifier_web/runtime_worker.py')]
    await service.schedules.close()  # No outbound schedules in this review fixture.
    if not service._state['sessions']:
        await service.dispatch('session.create',{'title':'Voice development review · synthetic provider','bundle':str(bundle)})
    latest={};lock=asyncio.Lock()
    async def report(request):
        if not hmac.compare_digest(request.headers.get('Authorization',''), 'Bearer '+app['control_token']):
            raise web.HTTPForbidden(text='The preview owner credential is required.')
        if request.method=='POST':
            payload=await request.json()
            if set(payload)-{'provider'} or payload.get('provider','live') not in {'live','realtime'}:raise web.HTTPBadRequest()
            if lock.locked():raise web.HTTPConflict(text='Review already running')
            async with lock:
                result=await review(payload.get('provider','live'))
                latest[result['provider']]=result
                (Path(root)/'review-results.json').write_text(json.dumps(latest,indent=2)+'\n')
                return web.json_response(result)
        return web.json_response({'kind':'scripted-protocol-review','results':latest})
    app.router.add_get('/api/voice-preview',report);app.router.add_post('/api/voice-preview',report)
    runner=web.AppRunner(app);await runner.setup();stopped=asyncio.Event()
    for signum in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(signum,stopped.set)
    try:
        await web.TCPSite(runner,'127.0.0.1',port).start()
        print(json.dumps({'url':f'http://127.0.0.1:{port}','mode':'synthetic-voice-development-preview'}),flush=True)
        await stopped.wait()
    finally:await runner.cleanup()


if __name__=='__main__':asyncio.run(serve(Path(sys.argv[1]),int(sys.argv[2])))
