"""Held then failed bundle discovery, preserving an existing unlisted choice."""
import asyncio
import copy
import json
from pathlib import Path
import signal
import sys
import tempfile
from aiohttp import web
import settings_ui_server as base


async def main(home):
    app=await base.main(home);service=app['service'];session=service._session()
    session.update(bundle='saved-unlisted-profile',historyManaged=False,historyLoaded=True,
                   messages=[{'id':'catalog-saved','role':'user','text':'Saved history during bundle discovery'}])
    gate=asyncio.Event();calls=[];original=base.BundleManager.perform
    async def perform(manager,action,args,**kwargs):
        if action=='bundles.list':
            calls.append(action)
            if len(calls)==1:
                await gate.wait()
                raise ValueError('Fixture bundle catalog unavailable. Retry explicitly.')
        return await original(manager,action,args,**kwargs)
    base.BundleManager.perform=perform
    runtime=[]
    for name in ('start','stop','control'):
        old=getattr(service.runtime,name)
        async def observed(*args,_name=name,_old=old,**kwargs):
            runtime.append(_name);return await _old(*args,**kwargs)
        setattr(service.runtime,name,observed)
    service.state['registeredBundles']=[]
    await service.dispatch('view.update',{'patch':{'setupJourney':'done','navPinned':True,'panel':None}})
    service._publish()
    async def inspect(request):
        if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':raise web.HTTPForbidden()
        if request.method=='POST':gate.set()
        async with service.lock:
            row=service._session(session['id'])
            return web.json_response({'bundle':row['bundle'],'messages':copy.deepcopy(row['messages']),
                'calls':len(calls),'catalogStatus':copy.deepcopy(service.state.get('actionStatus',{}).get('bundles.list',{})),
                'runtimeCalls':list(runtime)})
    app.router.add_get('/fixture/bundle-catalog',inspect);app.router.add_post('/fixture/bundle-catalog',inspect)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']|={url}
    stop=asyncio.Event()
    if sys.platform!='win32':
        for sig in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(sig,stop.set)
    print(json.dumps({'url':url}),flush=True)
    try:await stop.wait()
    finally:gate.set();await runner.cleanup()


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-bundle-catalog-')as tmp:asyncio.run(main(Path(tmp)))
