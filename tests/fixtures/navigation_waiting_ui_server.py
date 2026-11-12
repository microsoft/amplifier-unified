"""Disposable saved-request navigation fixture. Never starts a provider."""
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile

async def main(home, identity, verify):
    from aiohttp import web
    from configured_browser import seed_connection
    from empty_host_ui_server import Runtime
    from amplifier_web.server import create_app
    seed_connection(home/'native')
    folder=home/'workspace';folder.mkdir()
    runtime=Runtime()
    app=await create_app(home/'app',workspace=str(folder),runtime=runtime,
        voice=False,background_updates=False,background_work=False,preload_providers=False)
    app['control_token']='fixture-browser-control-token'
    service=app['service'];runtime.service=service;rows=[]
    titles=('Release manager','Unified: Readiness','Second worker with a long descriptive title')
    long_title='Unified: Readiness — '+('exact saved dependency with a long descriptive title ' * 3).strip()
    for title in titles:
        result=await service.dispatch('session.create',{'workspace':str(folder),'title':title,'bundle':'anchors'})
        row=service._session(result['result']['sessionId'])
        row.update(historyManaged=False,historyLoaded=True,messages=[
            {'id':row['id']+'-question','role':'user','text':'Synthetic saved work'}])
        rows.append(row)
    owner=rows[0]
    owner.update(collaborationGeneration={'id':'fixture-generation','inputIds':['fixture-input'],'terminal':True},
                 executionRevision=1)
    workspace_id=next(row['id'] for row in service.state['workspaces'] if row['path']==str(folder))
    await service.dispatch('workspace.rename',{'id':workspace_id,'name':'Unified: Launch Readiness'})
    await service.dispatch('workspace.pin',{'id':workspace_id,'pinned':True})
    for row in rows[:2]:
        await service.dispatch('session.pin',{'id':row['id'],'pinned':True})
    await service.dispatch('session.select',{'id':owner['id']})
    await service.dispatch('view.update',{'patch':{'setupJourney':'done','navPinned':True,'navChatScope':'all','panel':None}})
    histories={row['id']:hashlib.sha256(json.dumps(row['messages'],sort_keys=True).encode()).hexdigest() for row in rows}
    current_mode='one'

    def configure(mode):
        nonlocal current_mode
        current_mode=mode
        many=mode.endswith('-many') or mode in {'many','mixed'}
        outcome=mode.removesuffix('-many')
        if mode in {'long','long-deferred'}:outcome='deferred' if mode=='long-deferred' else 'one'
        for row,title in zip(rows,titles):
            row['title']=long_title if row is rows[1] and mode in {'long','long-deferred'} else title
        owner.update(status='idle',questions=[],approvals=[],error=None)
        owner['collaborationGeneration']['id']='fixture-generation'
        owner['collaborationGeneration']['inputIds']=['fixture-input']
        owner['collaborationGeneration']['terminal']=True
        task={'id':'fixture-task','revision':2,'status':'blocked','continuationEpoch':2,
              'dependencyWaitEpoch':1,'objective':'Wait for the exact reports',
              'blockProvenance':{'version':1,'origin':'agent','revision':2,'dependency':True}}
        owner['task']=task
        service.state.setdefault('runtimeControl',{})[owner['id']]={'task.get':{'task':task}}
        service.db.execute("DELETE FROM commands WHERE id LIKE 'fixture-wait-%'")
        for index, row in enumerate(rows[1:]):
            row['status']='working'
            if mode=='thinking' or index and not many:
                continue
            receipt={'commandAction':'coordination.send','requestId':f'fixture-wait-{index}',
                'sourceGenerationId':owner['collaborationGeneration']['id'],
                'sourceInputIds':list(owner['collaborationGeneration']['inputIds']),
                'protocol':2,'workspace':owner['workspace'],'senderSessionId':owner['id'],'target':{'sessionId':row['id'],'title':row['title']},
                'delivery':'applied','subscription':{'taskId':task['id'],'taskContinuationEpoch':1,
                    'interruptionRevision':owner.get('interruptionRevision',0),'notificationVersion':2,
                    'supported':True,'open':True,'status':'waiting'}}
            if outcome=='ready' or mode=='mixed' and not index:
                receipt['response']={'status':'staged','outcome':'success'}
            elif outcome in {'returned','explicit-return'}:
                receipt['response']={'status':'sealed','kind':'result','outcome':'success','qualified':True}
                receipt['subscription'].update(status='suppressed',open=False)
            elif outcome in {'failed','unknown','declined','deferred','decision'}:
                receipt['response']={'status':'sealed','kind':'result',
                    'outcome':'question' if outcome=='decision' else outcome,'qualified':True}
            elif outcome=='backpressured':
                receipt['subscription'].update(status='backpressured',open=True)
            service.db.execute('INSERT INTO commands VALUES(?,?,?)',
                (receipt['requestId'],'fixture',json.dumps(receipt)))
        if mode=='paused':
            task['status']='paused'
        elif mode=='stopped':
            owner['status']='stopped'
        elif mode=='question':
            owner['questions']=[{'id':'fixture-question','status':'pending','required':True,'revision':1,
                                 'question':'Choose the destination','prompt':'Choose the destination','options':[],
                                 'dependency':'Choose a destination before continuing','allowFreeText':True,
                                 'questionIds':[],'artifacts':[],'createdAt':1,'updatedAt':1}]
        elif mode=='recipient-error':
            rows[1].update(status='error',error='Synthetic worker failure')
        elif mode=='new-input':
            owner['collaborationGeneration']['inputIds'].append('new-human-input')
        elif mode=='explicit-return':
            task['operationIds']=['fixture-wait-0']
            owner['collaborationGeneration']={'id':'new-generation','inputIds':['fresh-input'],'terminal':True}
        elif mode=='unclassified':
            task['blockProvenance']=None
        elif mode=='thinking':
            task['status']='running'
            owner['collaborationGeneration']['terminal']=False
        service._publish_changes(sessions={row['id'] for row in rows})

    def auth(request):
        if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':
            raise web.HTTPForbidden()

    async def mode(request):
        auth(request)
        payload=await request.json()
        if not isinstance(payload,dict) or set(payload)!={'mode'} or not isinstance(payload['mode'],str):
            raise web.HTTPBadRequest()
        value=payload['mode']
        if value not in {'one','many','mixed','paused','stopped','ready','returned','explicit-return',
                         'failed','unknown','declined','deferred','decision','backpressured',
                         'unclassified','question','recipient-error','new-input','thinking','long','long-deferred',
                         'failed-many','unknown-many','decision-many','returned-many'}:
            raise web.HTTPBadRequest()
        async with service.lock:
            configure(value)
        if value=='thinking':
            # A legitimate synthetic runtime observation, not client state or a
            # substituted navigation projection. No input/provider is started.
            await service.on_runtime_event('runtime.status',{'sessionId':owner['id'],
                'status':'working','phase':'thinking','detail':'Thinking'})
        return web.json_response({'mode':value})

    async def observations(request):
        auth(request)
        commands=service.db.execute("SELECT id,receipt FROM commands WHERE id LIKE 'fixture-wait-%' ORDER BY id").fetchall()
        return web.json_response({'syntheticDurableReceipts':True,'mode':current_mode,'ids':[row['id'] for row in rows],
            'longTitle':long_title,'path':str(folder),'identity':identity,
            'runtimeStarts':len(runtime.started),'runtimeSends':len(runtime.sent),
            'historyUnchanged':all(hashlib.sha256(json.dumps(row['messages'],sort_keys=True).encode()).hexdigest()
                ==histories[row['id']] for row in rows),
            'receiptSha256':hashlib.sha256(json.dumps([list(row) for row in commands]).encode()).hexdigest()})

    configure('one')
    app.router.add_post('/fixture/wait',mode)
    app.router.add_get('/fixture/wait',observations)
    verify()
    runner=web.AppRunner(app);await runner.setup()
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    signals=[]
    try:
        for sig in (signal.SIGTERM,signal.SIGINT):
            loop.add_signal_handler(sig,stop.set);signals.append(sig)
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']|={url}
        print(json.dumps({'url':url,'fixture':'navigation-waiting','pid':os.getpid(),
                          'ids':[row['id'] for row in rows],'path':str(folder)}),flush=True)
        await stop.wait()
    finally:
        try:await runner.cleanup()
        finally:
            for sig in signals:loop.remove_signal_handler(sig)
            verify()


async def serve():
    with tempfile.TemporaryDirectory(prefix='unified-waiting-ui-') as home:
        home=Path(home)
        os.environ.update(AMPLIFIER_HOME=str(home/'native'), AMPLIFIER_WEB_HOME=str(home/'app'),
                          AMPLIFIER_SESSION_STATE_HOME=str(home/'sessions'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(home/'legacy'))
        installed=os.environ.get('AMPLIFIER_TEST_INSTALLED_PACKAGE')
        identity={'installed':False}
        def verify():pass
        if installed:
            assert Path(installed).is_absolute()
            from client_paired_server import verify_installed, verify_imports
            package=Path(installed).resolve()
            record=json.loads(Path(os.environ['AMPLIFIER_TEST_ASSEMBLY_RECORD']).read_text())
            def verify():
                verify_installed(package,record);verify_imports(package)
            verify()
            assert shutil.which('git'), 'Installed host PATH must contain Git, not Node tooling'
            # Metadata visibility of prepared dependencies is not a claim that
            # they were installed into the separate wheel-only host venv.
            dependency=Path(os.environ['AMPLIFIER_TEST_DEPENDENCY_VENV'])
            assert dependency.is_absolute() and dependency.is_dir()
            paths=[*dependency.glob('lib/python*/site-packages'),dependency/'Lib/site-packages']
            graph={dist.metadata['Name'].lower().replace('_','-'):dist.version
                   for dist in importlib.metadata.distributions(path=[str(path) for path in paths if path.is_dir()])}
            assert graph.get('amplifier-foundation')==importlib.metadata.version('amplifier-foundation')
            identity={'installed':True,'package':str(package),'serverRevision':record['server']['revision'],
                      'client':record['client']['manifest']['source'],'nodeUnavailable':True,
                      'preparedFoundationVersion':graph['amplifier-foundation']}
            os.environ['AMPLIFIER_TEST_INSTALLED']='1'  # empty-host helper exposes siblings only.
        else:
            sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
        from configured_browser import synthetic_discovery
        try:
            with synthetic_discovery():
                await main(home,identity,verify)
        finally:verify()


if __name__=='__main__':
    # -I excludes CWD; expose helpers, never a checkout root in installed mode.
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    asyncio.run(serve())