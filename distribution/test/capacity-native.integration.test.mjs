import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir,writeFile,readFile,rm,realpath} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';import {once} from 'node:events';import {spawnSync} from 'node:child_process';import {randomUUID} from 'node:crypto';import {pathToFileURL} from 'node:url';import {WebSocket} from 'ws';import {DatabaseSync} from 'node:sqlite';
const {createDistribution}=await import(process.env.AMPLIFIER_CAPACITY_DISTRIBUTION??'../src/index.js');const python=process.env.AMPLIFIER_CAPACITY_PYTHON;
const setup=String.raw`
import importlib.util,json,sys
from pathlib import Path
p=Path(sys.argv[1]);home=p/'home';home.mkdir();provider=p/'provider';provider.mkdir();module=provider/'amplifier_module_provider_capacity_fixture';module.mkdir()
(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-capacity-fixture"\nversion="0.1.0"\n')
(module/'__init__.py').write_text('''from pathlib import Path
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse,TextBlock,Usage
class Provider:
    name='fixture'
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline capacity fixture',defaults={'model':'fixture','max_tokens':100},capabilities=[])
    def parse_tool_calls(self,response):return []
    async def list_models(self):return [{'id':'fixture'}]
    async def complete(self,request,**kwargs):
        with Path(__file__).with_name('calls.txt').open('a') as stream:stream.write('call\\n')
        return ChatResponse(content=[TextBlock(text='Measured offline response.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):await coordinator.mount('providers',Provider(),name='fixture')
''')
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
bundle=p/'fixture.yaml';bundle.write_text('bundle:\n  name: capacity-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-capacity-fixture\n    source: '+str(provider)+'\n')
(home/'settings.yaml').write_text('bundle:\n  app: []\n')
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'app'),'bundle':str(bundle),'adminWorkspaceRoots':[str(p/'workspace')]}))
`;
class Peer{constructor(ws){this.ws=ws;this.next=0;this.pending=new Map();ws.on('message',raw=>{const v=JSON.parse(raw),p=this.pending.get(v.id);if(p){this.pending.delete(v.id);clearTimeout(p.timer);v.error?p.reject(Object.assign(Error(v.error.message),{data:v.error.data})):p.resolve(v.result);}});}request(method,params){const id=++this.next;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Capacity fixture timeout')),30000);this.pending.set(id,{resolve,reject,timer});this.ws.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});}close(){this.ws.terminate();for(const p of this.pending.values())clearTimeout(p.timer);}}
for(const migrated of [false,true])test(`installed Native, Host and shared actions enforce ${migrated?'migrated':'new'} conversation usage before provider invocation and preserve it across restart`,{skip:!python,timeout:90000},async()=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'capacity-owned-'))),workspace=join(root,'workspace'),web=join(root,'web');await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'fixture');const made=spawnSync(python,['-I','-B','-c',setup,root],{encoding:'utf8'});assert.equal(made.status,0,made.stderr);
 const legacy=join(root,'legacy.db');if(migrated){const db=new DatabaseSync(legacy);db.exec('CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT)');db.prepare('INSERT INTO state VALUES(1,?)').run(JSON.stringify({sessions:[]}));db.close();}
 const config={...(migrated?{legacyClientState:{database:legacy,account:'owned-capacity',engineId:'native'}}:{}),account:'owned-capacity',stateDirectory:join(root,'state'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,engines:[{id:'native',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(root,'native.json')]}],nativeAdmin:{engine:'native'}};let app,peer;const session='ahp-session:/'+randomUUID();
 const connect=async()=>{app=await createDistribution(config);const ws=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(ws,'open');peer=new Peer(ws);return peer.request('initialize',{channel:'ahp-root://',clientId:'capacity-browser',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});};
 const action=(operation,args={})=>peer.request('x-amplifier/capabilityAction',{channel:session,version:1,topic:'runtime-control',operation,args:{sessionId:session,...args},commandId:randomUUID()});
 try{
  await connect();await peer.request('createSession',{channel:session,provider:'native',workingDirectories:[pathToFileURL(workspace).href]});
  if(migrated){const selected=await app.host.inspectSession(session),db=new DatabaseSync(legacy);db.prepare('UPDATE state SET value=? WHERE id=1').run(JSON.stringify({sessions:[{id:'legacy-ui-id',runtimeSessionId:selected.nativeSessionId,workspace,execution:{nodes:[{kind:'llm',id:'old-call',rootSessionId:'legacy-ui-id',sessionId:'legacy-ui-id',phase:'completed',revision:2,producerId:'old-process',usage:{inputTokens:15,outputTokens:8}}]}}]}));db.close();}
  const original=migrated?await readFile(legacy):undefined;
  const before=app.host.diagnostics().activeAgents;const initial=await action('capacity.read');assert.equal(initial.result.usage.calls,migrated?1:0);assert.equal(initial.result.budget.enabled,false);assert.equal(app.host.diagnostics().activeAgents,before);
  const saved=await action('capacity.set',{expectedRevision:0,enabled:true,maxTotalTokens:10});assert.equal(saved.result.budget.revision,1);
  const run=async text=>{const commandId=randomUUID();await app.host.submitTurn(session,{commandId,clientId:'capacity-browser',text});return app.host.waitForTurn(session,commandId,30000);};
  if(!migrated)assert.equal((await run('Make one measured fixture response.')).status,'completed');const measured=(await action('capacity.read')).result;assert.equal(measured.usage.calls,1);assert.equal(measured.usage.metrics.grossTotalTokens.value,migrated?23:13);assert.equal(measured.admission.allowed,false);
  assert.notEqual((await run('Budget must refuse this provider call.')).status,'completed');if(migrated)await assert.rejects(readFile(join(root,'provider/amplifier_module_provider_capacity_fixture/calls.txt')),{code:'ENOENT'});else assert.equal((await readFile(join(root,'provider/amplifier_module_provider_capacity_fixture/calls.txt'),'utf8')).trim().split('\n').length,1);
  peer.close();peer=undefined;await app.close();app=undefined;
  const native=JSON.parse(await readFile(join(root,'native.json'),'utf8'));native.workerCommand=['/must/not/start'];await writeFile(join(root,'native.json'),JSON.stringify(native));
  await connect();assert.equal(app.host.diagnostics().activeAgents,0);const cold=(await action('capacity.read')).result;assert.equal(cold.budget.revision,1);assert.equal(cold.budget.enabled,true);assert.equal(cold.usage.calls,1);assert.equal(cold.usage.metrics.grossTotalTokens.value,migrated?23:13);assert.equal(cold.admission.allowed,false);assert.equal(app.host.diagnostics().activeAgents,0);
  if(original)assert.deepEqual(await readFile(legacy),original);
  if(process.env.AMPLIFIER_CAPACITY_RECEIPT)await writeFile(process.env.AMPLIFIER_CAPACITY_RECEIPT+(migrated?'.migrated.json':''),JSON.stringify({ownedFixture:true,legacySnapshot:migrated,sourceUnchanged:!!original,providerInvocations:migrated?0:1,recordedCalls:cold.usage.calls,grossTotalTokens:migrated?23:13,coldWorkerCount:0,budgetRevision:cold.budget.revision,budgetPaused:true,oldMessagesReplayed:false},null,2));
 }finally{peer?.close();await app?.close();await rm(root,{recursive:true,force:true});}
});
