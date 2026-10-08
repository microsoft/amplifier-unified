import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
const python=process.env.AMPLIFIER_ACP_PYTHON;
const setup=String.raw`
import importlib.util,json,sys
from pathlib import Path
from amplifier_acp.native.session_files import project_slug
from amplifier_session_catalog import Catalog
p=Path(sys.argv[1]);target=sys.argv[2];workspace=p/'workspace';home=p/'native';home.mkdir()
saved=home/'projects'/project_slug(str(workspace))/'sessions'/target;saved.mkdir(parents=True)
(saved/'metadata.json').write_text(json.dumps({'session_id':target,'working_dir':str(workspace),'name':'Saved peer'}))
(saved/'transcript.jsonl').write_text(json.dumps({'role':'user','content':'What is the marker?'})+'\n'+json.dumps({'role':'assistant','content':'Peer result: ORBIT-572'})+'\n')
catalog=Catalog(str(p/'catalog.sqlite'));catalog.upsert(dict(uri='ahp-session:/'+target,engineId='native',nativeSessionId=target,workingDirectory=str(workspace),title='Saved peer',createdAt=1000,modifiedAt=1000));catalog.workspace(str(workspace),availability='present')
provider=p/'provider';provider.mkdir();(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-peer-fixture"\nversion="0.1.0"\n')
module=provider/'amplifier_module_provider_peer_fixture';module.mkdir()
code='''from pathlib import Path
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse,TextBlock,Usage,ToolCall
class Provider:
    name='fixture'
    def parse_tool_calls(self,response):return response.tool_calls or []
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline peer fixture',defaults={'model':'fixture','max_tokens':4096},capabilities=['tools'])
    async def list_models(self):return [{'id':'fixture'}]
    async def complete(self,request,**kwargs):
        if 'You generate names and descriptions' in str(request.messages):
            return ChatResponse(content=[TextBlock(text='{"action":"defer","name":null,"description":null}')],finish_reason='stop')
        if not getattr(self,'called',False):
            self.called=True
            return ChatResponse(content=[],tool_calls=[ToolCall(id='read-peer',name='app_control',arguments={'operation':'dispatch','args':{'action':'coordination.read','args':{'sessionId':TARGET,'limit':10,'textLimit':1000},'id':'read-peer-once'}})],finish_reason='tool_calls')
        found='ORBIT-572' in str(request.messages)
        Path(__file__).with_name('observed.txt').write_text('seen' if found else str(request.messages))
        return ChatResponse(content=[TextBlock(text='Saved peer result received.' if found else 'Missing peer result.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):await coordinator.mount('providers',Provider(),name='fixture')
'''.replace('TARGET',repr('ahp-session:/'+target))
(module/'__init__.py').write_text(code)
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
bundle=p/'fixture.yaml';bundle.write_text('bundle:\n  name: peer-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-peer-fixture\n    source: '+str(provider)+'\n')
(home/'settings.yaml').write_text('bundle:\n  app: []\n')
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'app'),'bundle':str(bundle),'startupTimeout':90}))
print(str(saved))
`;
test('actual native app_control reads an explicit peer without selecting it or starting its worker',{skip:!python,timeout:120000},async()=>{
 const directory=await realpath(await mkdtemp(join(tmpdir(),'peer-native-action-'))),workspace=join(directory,'workspace'),web=join(directory,'web'),target=randomUUID();
 await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'<html><body>Fixture</body></html>');
 let app,socket;
 try{
  const seeded=spawnSync(python,['-I','-B','-c',setup,directory,target],{encoding:'utf8'});assert.equal(seeded.status,0,seeded.stderr);
  const saved=seeded.stdout.trim(),before=await readFile(join(saved,'transcript.jsonl')),metadata=await readFile(join(saved,'metadata.json'));
  app=await createDistribution({account:'peer-native-fixture',stateDirectory:join(directory,'distribution'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,coordination:{python},engines:[{id:'native',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(directory,'native.json')],env:{AMPLIFIER_SESSION_STATE_HOME:join(directory,'writers')}}],catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',join(directory,'native'),'--app-home',join(directory,'app'),'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0']}});
  socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');let next=0;const pending=new Map();
  socket.on('message',raw=>{const row=JSON.parse(raw),p=pending.get(row.id);if(p){pending.delete(row.id);clearTimeout(p.timer);row.error?p.reject(Error(row.error.message)):p.resolve(row.result)}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Fixture request timed out'))},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}))});
  await request('initialize',{channel:'ahp-root://',clientId:'peer-fixture-reader',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
  const session='ahp-session:/'+randomUUID();await request('createSession',{channel:session,provider:'native',workingDirectories:[pathToFileURL(workspace).href]});
  const invoke=app.host.invokeCapability.bind(app.host);let nativeReadCount=0;
  app.host.invokeCapability=(args,actor)=>{if(args.operation==='coordination.read'){assert.equal(actor.origin,'agent');assert.equal(actor.session,session);assert.equal(args.args.sessionId,'ahp-session:/'+target);nativeReadCount++;}return invoke(args,actor)};
  const commandId=randomUUID();await app.host.submitTurn(session,{commandId,clientId:'peer-fixture-reader',text:'Read the saved peer using the provided action.'});
  const result=await app.host.waitForTurn(session,commandId,60000);assert.equal(result.status,'completed',result.detail);assert.match(result.text,/Saved peer result received/);
  assert.equal(nativeReadCount,1);assert.equal(await readFile(join(directory,'provider/amplifier_module_provider_peer_fixture/observed.txt'),'utf8'),'seen');
  assert.deepEqual(await readFile(join(saved,'transcript.jsonl')),before);assert.deepEqual(await readFile(join(saved,'metadata.json')),metadata);
  assert.equal(app.host.diagnostics().activeAgents,1,'Only the reader has a native execution worker');
 }finally{socket?.terminate();await app?.close();await rm(directory,{recursive:true,force:true});}
});
