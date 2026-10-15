"""Real workspace configuration owners with deterministic, credential-free validation."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import signal
import sys
import tempfile

from aiohttp import web
import yaml
import settings_collections_server as base
from amplifier_web.host.config import read_config, write_private
from amplifier_web.registry import RegistryManager


async def main(home):
    helper=home/'validator.py'; validations=home/'validation.jsonl'
    helper.write_text('import json,sys\nfrom pathlib import Path\na=json.loads(sys.stdin.readline())\n'
        f'with Path({str(validations)!r}).open("a") as f:f.write(json.dumps(a)+"\\n")\n'
        'print(json.dumps({"type":"module.validation","id":a["id"],"passed":"valid-candidate" in a.get("source",""),"checks":[{"name":"Synthetic contract","passed":"valid-candidate" in a.get("source","")}]}))\n')
    original=RegistryManager.__init__
    def init(self,*args,**kwargs):
        original(self,*args,**kwargs);self.validation_command=[sys.executable,str(helper)]
    RegistryManager.__init__=init
    app=await base.main(home); service=app['service'];session=service._session()
    session.update(historyManaged=False,historyLoaded=True,messages=[{'id':'configuration-saved-question','role':'user','text':'Keep this saved configuration question'}])
    first=Path(session['workspace']);second=home/'other-workspace';second.mkdir()
    for folder in (first,second):
        (folder/'keep.txt').write_text('Unchanged workspace file '+folder.name)
        for name in ('valid-candidate','failed-candidate'):(folder/name).mkdir()
    shared=home/'native/settings.yaml';settings=yaml.safe_load(shared.read_text())
    settings['sources']={'modules':{'tool-example':str(first/'valid-candidate')},'bundles':{'fixture-base':'https://example.com/inherited'}}
    write_private(shared,yaml.safe_dump(settings,sort_keys=False))
    await service.dispatch('workspace.add',{'path':str(second),'name':'Other workspace'})
    await service.dispatch('session.select',{'id':session['id']})
    await service.dispatch('view.update',{'patch':{'setupJourney':'done','navPinned':True,'panel':None}})
    runtime_calls=[]
    for name in ('start','stop','control'):
        old=getattr(service.runtime,name)
        async def observed(*args,_name=name,_old=old,**kwargs):
            runtime_calls.append(_name);return await _old(*args,**kwargs)
        setattr(service.runtime,name,observed)
    service._publish()
    def read(path):return yaml.safe_load(path.read_text()) if path.exists() else None
    def workspace(folder):
        config=read_config(folder,home=home,shared_home=home/'native')
        return {'local':read(folder/'.amplifier/settings.local.yaml'),'project':read(folder/'.amplifier/settings.yaml'),
            'effectiveSources':copy.deepcopy(config.settings.get('sources',{})),
            'routingLocal':read(folder/'.amplifier/routing.local/workspace-proof.yaml'),
            'routingProject':read(folder/'.amplifier/routing/workspace-shared.yaml'),
            'file':(folder/'keep.txt').read_text()}
    async def inspect(request):
        if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':raise web.HTTPForbidden()
        async with service.lock:
            row=service._session(session['id'])
            return web.json_response({'sessionId':row['id'],'session':{k:copy.deepcopy(row.get(k))for k in ('messages','model','bundle','providerId','configuration')},
                'workspaces':[{'id':w['id'],'path':w['path'],'name':w['name']}for w in service.state['workspaces']],
                'first':workspace(first),'second':workspace(second),
                'globalSha256':hashlib.sha256(shared.read_bytes()).hexdigest(),
                'globalRoutingSha256':hashlib.sha256((home/'native/routing/balanced.yaml').read_bytes()).hexdigest(),
                'validationCalls':[json.loads(line)for line in validations.read_text().splitlines()]if validations.exists()else [],
                'runtimeCalls':list(runtime_calls)})
    app.router.add_get('/fixture/workspace-configuration',inspect)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]);app['allowed_origins']|={url}
    stop=asyncio.Event()
    if sys.platform!='win32':
        for sig in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(sig,stop.set)
    print(json.dumps({'url':url,'workspace':str(first)}),flush=True)
    try:await stop.wait()
    finally:await runner.cleanup()


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='unified-workspace-configuration-')as tmp:asyncio.run(main(Path(tmp)))
