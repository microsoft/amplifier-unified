"""Real Settings HTTP/SSE with captured attempt history and synthetic current graph."""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import subprocess
from aiohttp import web


async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'),AMPLIFIER_WEB_HOME=str(home),
                      AMPLIFIER_UNIFIED_IMPORT_HOME=str(home/'legacy'))
    from settings_ui_server import main as settings_app
    import settings_ui_server
    original_create=settings_ui_server.create_app
    async def isolated_app(*args,**kwargs):
        kwargs.update(background_work=False,preload_providers=False)
        return await original_create(*args,**kwargs)
    settings_ui_server.create_app=isolated_app
    app=await settings_app(home);service=app['service'];manager=service.update_manager
    from amplifier_web import update_health, runtime_qualification
    from amplifier_web.runtime_profiles import configuration_key
    from amplifier_web.host.config import read_config
    from amplifier_web.update_diagnostics import UpdateDiagnostics
    saved=json.loads((Path(__file__).parent/'update-current-health-captured060.json').read_text())
    get=lambda key:saved['observedState'][key]['value']
    generation=saved['active']['current'];stage=manager.directory/'releases'/generation;stage.mkdir(parents=True)
    source=saved['generations'][generation]
    (manager.directory/'active.json').write_text(json.dumps(saved['active']))
    project=home/'runtime'/('q-'+'a'*32);project.mkdir(parents=True)
    (project/'.venv').symlink_to(Path(sys.prefix),target_is_directory=True)
    graph=runtime_qualification.installed_graph(project)
    assert any(row['name']=='amplifier-foundation' for row in graph)
    config=read_config(home,home=home,global_only=True)
    records={**{name:source[name] for name in ['plan.json','validated.json']},
        'runtime-project.json':{'project':project.name},'runtime-installed.json':graph,
        'profiles-qualified.json':{'profiles':['work'],'configuration':configuration_key(config.settings,home,stage)}}
    for name,value in records.items():(stage/name).write_text(json.dumps(value))
    (stage/'runtime.lock').write_text('fixture-immutable-lock');(project/'uv.lock').write_text('fixture-immutable-lock')
    state=service.state['updates'];state.update(phase='checked',error=None,detail='Component check complete.',available=0,
        release=generation,pendingRelease=None,pendingApp=None,pendingRestart=None,pendingReplacement=None,
        sequence=get('/updates/sequence'),applicationResult=get('/updates/applicationResult'),
        application={'current':'0.60.0','runningRevision':get('/updates/applicationResult')['revision'],'status':'current','releaseNotes':[]},items=[],
        diagnostics={'events':[],'activeFailures':get('/updates/diagnostics/activeFailures'),
            'completedBatches':list(get('/updates/diagnostics/completedBatches').values())})
    manager.diagnostics=UpdateDiagnostics(manager)
    await update_health.refresh(manager);assert manager._active_update_evidence
    original=copy.deepcopy(state['diagnostics']['activeFailures'])
    await manager.publish()
    retry = {'enabled':False,'mode':'known','fetches':0,'prepareCalls':0,'checks':0,'installActions':0}
    from amplifier_web import updates, app_updates, update_plan
    from amplifier_web.update_sequence import app_selection
    from amplifier_web.update_partial import RECEIPT, public_results
    remote=home/'fixture-remote';remote.mkdir()
    def git(path,*args):
        return subprocess.check_output(['git',*args],cwd=path,text=True,stderr=subprocess.DEVNULL).strip()
    git(remote,'init','-b','main');git(remote,'config','user.name','Fixture');git(remote,'config','user.email','fixture@example.invalid')
    (remote/'pyproject.toml').write_text('[project]\nname="fixture-retained"\nversion="1.0"\n')
    git(remote,'add','.');git(remote,'commit','-m','old');old=git(remote,'rev-parse','HEAD')
    (remote/'revision.txt').write_text('candidate');git(remote,'add','.');git(remote,'commit','-m','new');new=git(remote,'rev-parse','HEAD')
    cache=stage/'foundation/cache/retained';cache.parent.mkdir(parents=True)
    git(remote,'clone',str(remote),str(cache));git(cache,'checkout','--detach',old)
    url=remote.as_uri();git(cache,'remote','set-url','origin',url)
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':url,'ref':'main','commit':old}))
    witness=app_selection(home)['witness']
    row={'id':'retained','kind':'bundle / module','label':'Fixture retained source','path':'cache/retained',
         'url':url,'ref':'main','current':old,'latest':new,'eligible':True,'status':'not_checked',
         'usage':'configured','updateTier':'included','appSelection':witness}
    partial={'version':1,'candidate':generation,'attemptId':'e'*32,'baseline':'a'*32,
             'selection':[witness],'retained':{'fixture-retained':{'url':url,'ref':'main','revision':old,'subdirectory':''}},
             'results':{'retained':{'status':'failed','revision':old,'desired':new,'source':{'url':url,'ref':'main'}}}}
    pointer=(manager.directory/'active.json').read_bytes()
    original_inventory=manager.inventory_sources;original_process=updates.process
    original_check=app_updates.check;original_build=update_plan.build
    async def inventory(**kwargs):
        if not retry['enabled']:return await original_inventory(**kwargs)
        retry['checks']+=1
        return [copy.deepcopy(row)]
    async def process(*args,**kwargs):
        if retry['enabled'] and args[:2]==('git','ls-remote'):
            if retry['mode']=='unknown':
                from amplifier_web.update_diagnostics import CommandTimeout
                raise CommandTimeout(1)
            return new+' refs/heads/main'
        if retry['enabled'] and 'fetch' in args:retry['fetches']+=1
        return await original_process(*args,**kwargs)
    async def application():
        if retry['enabled']:return {**state['application'],'status':'current'}
        return await original_check()
    async def build(*args,**kwargs):
        if not retry['enabled']:return await original_build(*args,**kwargs)
        retry['prepareCalls']+=1
        raise RuntimeError('Synthetic qualification stop after guarded acquisition; no runtime install')
    manager.inventory_sources=inventory;updates.process=process;app_updates.check=application;update_plan.build=build
    original_command=manager.command
    async def command(action,*args,**kwargs):
        if retry['enabled'] and action=='install':retry['installActions']+=1
        return await original_command(action,*args,**kwargs)
    manager.command=command
    def retry_observation():
        return {**retry,'candidateCount':len(list((manager.directory/'releases').iterdir()))-1,
                'activePointerUnchanged':(manager.directory/'active.json').read_bytes()==pointer,
                'servingSourceUnchanged':git(cache,'rev-parse','HEAD')==old,
                'available':state.get('available'),'status':state['currentStatus']}
    async def transition(request):
        if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':raise web.HTTPForbidden()
        mode=(await request.json())['mode']
        if mode=='failure':
            manager.diagnostics.begin('ecosystem','b'*32,tier='other',components=['amplifier-foundation'])
            manager.diagnostics.record('ecosystem-source-preflight','failed',package='amplifier-foundation',reason='runtime-source-constraint')
            await manager.publish(phase='error',error='Current source conflict; installed components kept.')
        elif mode=='uncertain':await manager.publish(pendingReplacement={'unqualified':True},phase='activating')
        elif mode=='current':
            state['diagnostics']['activeFailures']=copy.deepcopy(original);state['diagnostics'].pop('lastFailure',None)
            await manager.publish(phase='checked',error=None,pendingReplacement=None)
        elif mode == 'partial':
            from amplifier_web.update_partial import adopted_items, public_results
            rows = [{'id': name, 'label': 'Fixture ' + name.upper(), 'kind': 'bundle / module',
                     'status': 'update', 'current': 'a'*40, 'latest': 'b'*40, 'ref': 'main',
                     'usage': 'configured', 'updateTier': 'included'} for name in 'abc']
            receipt = {'results': {name: {'status': status, 'revision': ('b' if name == 'b' else 'a')*40}
                       for name, status in {'a': 'failed', 'b': 'awaiting-adoption', 'c': 'dependency-held'}.items()}}
            await manager.publish(phase='installed', error=None, available=0,
                sequence={'stage': 'complete', 'install': False, 'included': {'status': 'attention', 'issues': 2}},
                items=adopted_items(rows, receipt), componentResults=public_results(receipt, adopted=True))
        elif mode.startswith('retry-'):
            retry.update(enabled=True,mode=mode.removeprefix('retry-'))
            (stage/RECEIPT).write_text(json.dumps(partial))
            state['diagnostics'].pop('componentPreparation',None)
            state['diagnostics']['batch']={}
            state['diagnostics']['activeFailures']=copy.deepcopy(original)
            state['diagnostics'].pop('lastFailure',None)
            manager.inventory=[copy.deepcopy(row)]
            await manager.publish(phase='checked',error=None,available=0,pendingReplacement=None,
                sequence={'stage':'complete','install':False},componentResults=public_results(partial,adopted=True))
            if retry['mode'] in {'missing','stale'}:
                manager.inventory[0].update(status='check_failed',availabilityFailure=True,sourceUpdate='failed')
                if retry['mode']=='missing':manager.inventory[0].pop('latest')
                else:manager.inventory[0]['appSelection']='stale'
            else:
                await manager.check()
            await update_health.refresh(manager);await manager.publish()
        elif mode.startswith('notes-'):
            application = dict(state['application'])
            application.pop('releaseNotesWarning', None)
            application.pop('releaseNotes', None)
            if mode=='notes-empty': application['releaseNotes']=[]
            elif mode=='notes-error': application['releaseNotesWarning']='Release notes could not be refreshed. Showing saved release history.'
            elif mode=='notes-next': application['current']='0.60.1'
            elif mode=='notes-ready':
                application['releaseNotes']=[{'version':application['current'],'title':'Fixture release notes',
                    'changes':['Exact installed-version notes arrived.'],'notices':[]}]
            elif mode!='notes-missing': raise web.HTTPBadRequest()
            await manager.publish(application=application)
        else:raise web.HTTPBadRequest()
        return web.json_response({'status':state['currentStatus'],'historyPreserved':all(k in state['diagnostics']['activeFailures'] for k in original),
                                  'retry':retry_observation()})
    async def observations(request):
        return web.json_response({'status':state['currentStatus'],'history':state['diagnostics']['activeFailures'],
            'originalHistoryUnchanged':state['diagnostics']['activeFailures']==original,'paidCalls':0,'liveInstallCalls':0,
            'retry':retry_observation()})
    app.router.add_post('/fixture/current-update',transition);app.router.add_get('/fixture/current-update',observations)
    return app


async def serve():
    with tempfile.TemporaryDirectory(prefix='update-health-ui-') as tmp:
        os.environ.update(AMPLIFIER_HOME=str(Path(tmp)/'shared'),AMPLIFIER_WEB_HOME=tmp,
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(Path(tmp)/'legacy'))
        installed=os.environ.get('AMPLIFIER_TEST_INSTALLED_PACKAGE')
        if installed:
            from client_paired_server import verify_installed, verify_imports
            package=Path(installed).resolve()
            record=json.loads(Path(os.environ['AMPLIFIER_TEST_ASSEMBLY_RECORD']).read_text())
            verify_installed(package,record);verify_imports(package)
        app=await main(Path(tmp));runner=web.AppRunner(app);await runner.setup();stopped=asyncio.Event();loop=asyncio.get_running_loop()
        for sig in [signal.SIGTERM,signal.SIGINT]:loop.add_signal_handler(sig,stopped.set)
        try:
            site=web.TCPSite(runner,'127.0.0.1',0);await site.start();url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']|={url}
            import amplifier_web
            print(json.dumps({'url':url,'package':amplifier_web.__file__}),flush=True);await stopped.wait()
        finally:
            await runner.cleanup()
            if installed:verify_installed(package,record);verify_imports(package)

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    asyncio.run(serve())
