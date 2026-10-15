"""Owned four-chat navigation fixture; no provider calls or external histories."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from aiohttp import web
from configured_browser import seed_connection
from empty_host_ui_server import Runtime
from amplifier_web.server import create_app
from amplifier_web.host.storage import SessionStore


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'native'), AMPLIFIER_WEB_HOME=str(home/'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(home/'legacy'))
    seed_connection(home/'native')
    workspace=home/'workspace';workspace.mkdir()
    (workspace/'keep.txt').write_text('Keep navigation fixture file.')
    runtime=Runtime()
    app=await create_app(home/'app', workspace=str(workspace), runtime=runtime,
                         voice=False, background_updates=False, preload_providers=False)
    app['control_token']='fixture-browser-control-token'
    service=app['service'];runtime.service=service;rows=[]
    for title in ['Alpha ordinary','Bravo working','Charlie attention','Delta archived']:
        response=await service.dispatch('session.create',{'workspace':str(workspace),'title':title,'bundle':'anchors'})
        row=service._session(response['result']['sessionId'])
        row.update(historyManaged=False,historyLoaded=True,messages=[{'id':row['id']+'-question','role':'user','text':'Saved '+title}])
        rows.append(row)
    await service.on_runtime_event('runtime.status',{'sessionId':rows[1]['id'],'status':'working'})
    await service.on_runtime_event('runtime.error',{'sessionId':rows[2]['id'],'error':'Synthetic fixture attention'})
    await service.dispatch('session.archive',{'id':rows[3]['id']})
    await service.dispatch('session.select',{'id':rows[0]['id']})
    await service.dispatch('view.update',{'patch':{'setupJourney':'done','navPinned':True,'navChatScope':'all','panel':None}})
    owned_workspaces=[]
    if '--workspace-actions' in sys.argv:
        for name in ['Archive this workspace','Remove this registration']:
            folder=home/name.lower().replace(' ','-');folder.mkdir()
            (folder/'keep.txt').write_text('Preserve '+name)
            await service.dispatch('workspace.add',{'path':str(folder),'name':name})
            owned_workspaces.append(next(row for row in service.state['workspaces'] if row['path']==str(folder)))
        native_store=SessionStore.for_app(home/'app',owned_workspaces[0]['path'])
        native_store.save('workspace-preserved-native',[{'role':'user','content':'Keep saved workspace history'}],
                          {'session_id':'workspace-preserved-native','working_dir':owned_workspaces[0]['path'],'name':'Preserved native work'})
        # workspace.add deliberately enters the newly registered workspace.
        # Restore the stated current-chat prerequisite before opening a browser.
        await service.dispatch('session.select',{'id':rows[0]['id']})
        await service.dispatch('view.update',{'patch':{'navPinned':True,'navChatScope':'all','panel':None}})
    service._publish()
    store=SessionStore.for_app(home/'app',str(workspace));new_id='navigation-native-new'
    def auth(request):
        if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':raise web.HTTPForbidden()
    async def inspect(request):
        auth(request)
        async with service.lock:
            return web.json_response({'ids':[row['id']for row in rows],
                'titles':{row['id']:service._session(row['id'],hydrate=False)['title']for row in rows},
                'messages':{row['id']:hashlib.sha256(json.dumps(service._session(row['id'],hydrate=False)['messages'],sort_keys=True).encode()).hexdigest()for row in rows},
                'file':(workspace/'keep.txt').read_text(),'runtime':{'started':runtime.started,'sent':runtime.sent,'stopped':runtime.stopped},
                'nativePresent':(store.directory(new_id)/'transcript.jsonl').exists(),
                'registeredWorkspaces':[{'id':row['id'],'path':row['path'],'name':row['name'],'archived':row.get('archived',False)}for row in service.state['workspaces']],
                'ownedFiles':{row['id']:(Path(row['path'])/'keep.txt').read_text()for row in owned_workspaces},
                'preservedNativeSha256':hashlib.sha256((native_store.directory('workspace-preserved-native')/'transcript.jsonl').read_bytes()).hexdigest() if owned_workspaces else None})
    async def seed_native(request):
        auth(request)
        store.save(new_id,[{'role':'user','content':'New saved native question'}],
                   {'session_id':new_id,'name':'New saved native chat','working_dir':str(workspace),'bundle':'anchors'})
        return web.json_response({'nativeId':new_id})
    app.router.add_get('/fixture/navigation',inspect)
    app.router.add_post('/fixture/seed-native',seed_native)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']|={url}
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
    print(json.dumps({'url':url,'ids':[row['id']for row in rows],'workspaces':owned_workspaces}),flush=True)
    try:await stop.wait()
    finally:await runner.cleanup()


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-navigation-controls-') as home:
        asyncio.run(main(Path(home)))
