"""Exercise old-app execution after candidate writes in a private fixture copy."""
from pathlib import Path
import asyncio, hashlib, json, os, shutil, sys, uuid

legacy, previous = map(lambda p: Path(p).resolve(), sys.argv[1:])
assert previous.name.startswith('legacy-continuation-') and (previous/'acceptance.json').is_file()
root=previous/('old-runtime-'+str(uuid.uuid4()));root.mkdir(mode=0o700)
home=root/'native';shutil.copytree(previous/'rollback-native',home)
workspace=previous/'workspace'
original_hashes={str(p.relative_to(previous)):hashlib.sha256(p.read_bytes()).hexdigest()
 for base in ['original-native','rollback-native','workspace'] for p in (previous/base).rglob('*') if p.is_file()}
transcript=next(home.glob('projects/*/sessions/d171ccd8-a11a-4c92-94f6-65054bda043b/transcript.jsonl'))
original_rows=[json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
assert (workspace/'after-switch.txt').read_text()=='violet compass\n'
for key in list(os.environ):
    if key.startswith(('AMPLIFIER_','OPENAI_','ANTHROPIC_','AZURE_','GOOGLE_','PYTHON','UV_')):os.environ.pop(key)
app_home=root/'app'
os.environ.update(AMPLIFIER_HOME=str(home),AMPLIFIER_WEB_HOME=str(app_home),
 AMPLIFIER_SESSION_STATE_HOME=str(root/'writers'),AMPLIFIER_SOURCE_STORE=str(root/'sources'),
 XDG_CACHE_HOME=str(root/'cache'),PYTHONDONTWRITEBYTECODE='1',AMPLIFIER_RUNTIME_IMMUTABLE='1')
sys.path.insert(0,str(legacy))
external=[];children=[]
def audit(event,args):
    if event=='socket.connect' and isinstance(args[1],tuple) and args[1][0] not in {'127.0.0.1','::1','localhost'}:
        external.append(str(args[1][0]));raise RuntimeError('Offline rollback fixture forbids external network')
    if event=='subprocess.Popen':
        arguments=args[1]
        if arguments != [sys.executable,'-I','-B',str(root/'worker.py')]:raise RuntimeError('Only the original legacy worker is allowed: '+str(arguments[:3]))
        children.append('legacy-worker')
sys.addaudithook(audit)
# Child guard is independent of the parent audit hook. No credential inheritance.
bootstrap="""import asyncio,runpy,sys
from pathlib import Path
sys.path.insert(0,LEGACY)
def audit(event,args):
 if event=='socket.connect' and isinstance(args[1],tuple) and args[1][0] not in {'127.0.0.1','::1','localhost'}:raise RuntimeError('Offline rollback worker forbids external network')
 if event=='subprocess.Popen':raise RuntimeError('Rollback worker cannot install or launch another process')
sys.addaudithook(audit)
runpy.run_module('amplifier_web.runtime_worker',run_name='__main__')
""".replace('LEGACY',repr(str(legacy)))
(root/'worker.py').write_text(bootstrap)
provider=root/'provider';module=provider/'amplifier_module_provider_rollback_fixture';module.mkdir(parents=True)
(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-rollback-fixture"\nversion="0.1.0"\n')
(module/'__init__.py').write_text("""import json
from pathlib import Path
from amplifier_core.models import ProviderInfo,ToolResult
from amplifier_core.message_models import ChatResponse,TextBlock,Usage,ToolCall
class Provider:
 name='fixture'
 def __init__(self,config=None):self.root=Path((config or {}).get('directory','.'))
 def get_info(self):return ProviderInfo(id='fixture',display_name='Offline rollback',defaults={'model':'fixture','max_tokens':4096},capabilities=['tools'])
 def parse_tool_calls(self,response):return response.tool_calls or []
 async def list_models(self):return [{'id':'fixture'}]
 async def complete(self,request,**kwargs):
  text=str(request.messages)
  assert 'violet compass' in text and 'Continued with violet compass and saved the new artifact.' in text,'Candidate conversation lost'
  assert 'CONTINUE-OLD-42' in text,'Only explicit rollback continuation may execute'
  with (self.root/'requests.jsonl').open('a') as f:f.write(json.dumps([m.model_dump(mode='json') for m in request.messages])+'\\n')
  if 'Old runtime artifact saved' not in text:return ChatResponse(content=[],tool_calls=[ToolCall(id='write-rollback',name='fixture_rollback_save',arguments={})],finish_reason='tool_calls')
  return ChatResponse(content=[TextBlock(text='The old runtime retained violet compass and the candidate artifact.')],finish_reason='stop',usage=Usage(input_tokens=12,output_tokens=10,total_tokens=22))
async def mount(coordinator,config=None):
 root=Path(config['directory'])
 class Save:
  name='fixture_rollback_save';description='Save isolated rollback artifact';input_schema={'type':'object','properties':{}}
  async def execute(self,args):
   with (root/'after-rollback.txt').open('x') as f:f.write('violet compass\\n')
   with (root/'effects.jsonl').open('a') as f:f.write('write\\n')
   return ToolResult(success=True,output='Old runtime artifact saved')
 await coordinator.mount('providers',Provider(config),name='fixture')
 await coordinator.mount('tools',Save(),name='fixture_rollback_save')
""")
import amplifier_module_context_simple as context
bundle=root/'rollback.yaml'
bundle.write_text('bundle:\n  name: rollback-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(Path(context.__file__).parent)+'\nproviders:\n  - module: provider-rollback-fixture\n    source: '+str(provider)+'\n    config:\n      directory: '+str(root)+'\n')
# The copied conversation's test provider and dependency paths belonged to the
# retired candidate qualification runtime. Redirect only its fixture bundle;
# leave every transcript row unchanged. This does not qualify real bundle adoption.
import amplifier_module_hook_context_intelligence as ci_hook
import yaml
settings=yaml.safe_load((home/'settings.yaml').read_text()) or {}
settings.setdefault('sources',{}).setdefault('modules',{})['hook-context-intelligence']=str(Path(ci_hook.__file__).parent)
(home/'settings.yaml').write_text(yaml.safe_dump(settings))
for metadata in home.glob('projects/*/sessions/d171ccd8-a11a-4c92-94f6-65054bda043b/metadata.json'):
 data=json.loads(metadata.read_text());data['bundle']=data['bundle_name']=str(bundle)
 metadata.write_text(json.dumps(data))
from aiohttp import web,ClientSession
from amplifier_web.server import create_app
from amplifier_web.auth import control_token
from amplifier_web.deployment import load_server_config
from amplifier_web.runtime import RuntimeManager
async def main():
    config=load_server_config(app_home);config['runtime']['prewarm_on_select']=False
    runtime=RuntimeManager(command=[sys.executable,'-I','-B',str(root/'worker.py')],startup_timeout=40)
    app=await create_app(app_home,workspace=str(workspace),runtime=runtime,voice=False,background_updates=False,preload_providers=False,server_config=config)
    # The real legacy service supplies the same bridge used by its default manager.
    runtime.app_bridge=app['service'].app_bridge
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    origin='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
    try:
      async with ClientSession(headers={'Authorization':'Bearer '+control_token(app_home)}) as client:
        async def get(path,**params):
          async with client.get(origin+path,params=params) as response:
            value=await response.json();assert response.status==200,(response.status,value);return value
        async def action(name,args,id):
          async with client.post(origin+'/api/actions',json={'action':name,'args':args,'id':id}) as response:
            value=await response.json();assert response.status==200,(response.status,value);return value
        client_id=str(uuid.uuid4())
        async with client.post(origin+'/api/clients/attach',json={'clientId':client_id,'kind':'web','protocolVersion':1}) as response:assert response.status==200,await response.text()
        client.headers['X-Amplifier-Client']=client_id
        for _ in range(100):
          state=await get('/api/state');saved=next((s for s in state['sessions'] if (s.get('nativeIdentity') or s['id'])=='d171ccd8-a11a-4c92-94f6-65054bda043b'),None)
          if saved:break
          await asyncio.sleep(.1)
        assert saved,'Old app did not discover candidate history'
        # Configure the offline bundle via the app's public default action before selection.
        await action('bundle.default',{'scope':'app','bundle':str(bundle)},'rollback-default')
        await action('session.select',{'id':saved['id']},'rollback-select')
        # Retained chat inherited a former fixture bundle; choose the new fixture
        # using the existing saved session field before any worker can start.
        internal=app['service']._session(saved['id']);internal['bundle']=str(bundle)
        internal['selection']={}
        await action('conversation.send',{'sessionId':saved['id'],'text':'CONTINUE-OLD-42. Keep the phrase and verify the candidate artifact.'},'rollback-explicit-input')
        for _ in range(600):
          selected=(await get('/api/sessions/'+saved['id']))['session']
          if selected.get('status') not in {'working','starting','queued'}:break
          await asyncio.sleep(.1)
        (root/'outcome.json').write_text(json.dumps(selected,indent=2))
        assert selected.get('status')=='idle',json.dumps(selected.get('error') or selected.get('runtime') or selected)[:4000]
        rows=[json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
        assert rows[:len(original_rows)]==original_rows,'Old runtime changed existing candidate transcript rows'
        assert sum(row.get('role')=='user' and 'CONTINUE-OLD-42' in str(row.get('content')) for row in rows)==1
        assert any('The old runtime retained violet compass and the candidate artifact.' in str(row.get('content')) for row in rows)
        assert (root/'after-rollback.txt').read_text()=='violet compass\n'
        assert (root/'effects.jsonl').read_text()=='write\n'
        requests=(root/'requests.jsonl').read_text().splitlines();assert len(requests)==2,len(requests)
        assert (workspace/'after-switch.txt').read_text()=='violet compass\n'
        assert (workspace/'before-switch.txt').read_text()=='Original workspace artifact\n'
        result={'result':'passed','root':str(root),'legacyPython':sys.executable,'legacySource':str(legacy),'providerCalls':2,'toolEffects':1,'oldAndCandidateArtifactsUnchanged':True,'newExplicitInputOnly':True,'externalConnections':external,'childWorkers':len(children),'existingRowsPreserved':len(original_rows),'fixtureBundleRedirected':True,'boundary':'Real old application HTTP and worker against a private post-candidate native copy. Fixture bundle redirects an offline provider and retired dependency paths; not real bundle adoption or complete all-owner activation.'}
    finally:await runner.cleanup()
    for name,digest in original_hashes.items():assert hashlib.sha256((previous/name).read_bytes()).hexdigest()==digest,name
    result['originalFilesUnchanged']=len(original_hashes)
    (root/'receipt.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
asyncio.run(main())

