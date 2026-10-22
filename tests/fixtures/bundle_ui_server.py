"""Bundle chooser acceptance host; synthetic runtime and private history."""
import os,sys,tempfile,uuid,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.bundle_selection import catalog_entry
from amplifier_web.bundles import BundleManager
from amplifier_web import draft_defaults
from amplifier_web.host.storage import SessionStore
from amplifier_web.host.config import PRECONFIGURED_BUNDLES, write_private
import yaml
class Runtime:
    def __init__(self):self.tokens={};self.calls=[]
    async def start(self,session,emit,*,guard=None):
        if guard is not None:guard()
        self.calls.append('start')
        if session['bundle']=='broken':raise ValueError('The candidate failed to load; original configuration is preserved.')
        await emit('runtime.status',{'sessionId':session['id'],'status':'ready'})
    async def stop(self,*args,reason="stopped"):self.calls.append('stop')
    async def close(self):pass
    async def control(self,sid,op,args):
        self.calls.append(op)
        if op=='bundle.preview':
            token=str(uuid.uuid4());self.tokens[sid]=token
            return {'bundle':args['bundle'],'previewId':token,'fingerprint':args['bundle'],'selection':None,'modelCompatible':True,'changes':{'tools':{'added':['read_transcript'],'removed':['old_tool']},'context':{'before':'context-simple','after':'context-managed'}},'appCapabilities':[]}
        if op=='bundle.prepare':
            if args.get('previewId') and self.tokens.get(sid)!=args['previewId']:raise ValueError('Preview again.')
            return {'replacement':{'bundle':args['bundle'],'fingerprint':args['bundle'],'selection':None,'modelCompatible':True,'resetModel':False}}
        if op=='history.snapshot':return {'messages':[{'role':'user','content':'Keep my original history.'},{'role':'assistant','content':'Saved response.'}]}
        if op=='configuration.inspect':return {'plan':{}}
        if op=='configuration.providers':return {'providers':[],'effective':{'model':'Fixture model'}}
        return {}
# Saved previews/catalogs use the passive resolver, not Runtime.control.
# Keep this synthetic boundary independent from immediate switch/fork effects.
async def resolve(home,workspace,bundle=None,*args,**kwargs):
    return {'bundle':bundle or 'anchors','fingerprint':bundle or 'anchors',
            'selection':None,'modelCompatible':True,'changes':{},
            'providers':[],'effective':{'model':'Fixture model'}}
draft_defaults.resolve_defaults=resolve
original=BundleManager.perform
async def perform(self,action,args,**kwargs):
    if action=='bundles.list':return {'bundles':[],'registeredBundles':[catalog_entry(name) for name in (*PRECONFIGURED_BUNDLES,'anchors-work','broken')]}
    return await original(self,action,args,**kwargs)
BundleManager.perform=perform
async def main(home):
    os.environ.update(AMPLIFIER_HOME=str(home/'shared'),AMPLIFIER_WEB_HOME=str(home))
    write_private(home/'shared/settings.yaml',yaml.safe_dump({'config':{'providers':[{'id':'fixture','module':'provider-openai','config':{'default_model':'fixture-model'}}]}}))
    workspace=home/'workspace';workspace.mkdir()
    runtime=Runtime()
    app=await create_app(home,workspace=workspace,runtime=runtime,voice=False,background_updates=False)
    async def runtime_calls(request):return web.json_response(runtime.calls)
    app.router.add_get('/fixture/runtime-calls',runtime_calls)
    app['control_token']='bundle-fixture-control';app['allowed_origins']|={'http://127.0.0.1:8963'}
    service=app['service'];await service.dispatch('session.create',{'title':'Bundle acceptance','bundle':'anchors'})
    session=service._session();session['messages']=[{'id':'saved-user','createdAt':time.time(),'role':'user','text':'Keep my original history.'},{'id':'saved-answer','createdAt':time.time(),'role':'assistant','text':'Saved response.'}]
    SessionStore.for_app(home,workspace).save(session['id'],[{'role':row['role'],'content':row['text']} for row in session['messages']],{'bundle_name':'anchors'})
    service._publish();return app
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-bundle-ui-') as folder:web.run_app(main(Path(folder)),host='127.0.0.1',port=8963,print=None)
