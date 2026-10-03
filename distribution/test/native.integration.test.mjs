import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
const python=process.env.AMPLIFIER_ACP_PYTHON;
const ownersPython=process.env.UNIFIED_OWNERS_PYTHON;
const setup=String.raw`
import importlib.util,json,sys
from pathlib import Path
p=Path(sys.argv[1]); home=p/'native-home';home.mkdir(); provider=p/'provider';provider.mkdir()
(provider/'pyproject.toml').write_text('''[project]\nname="amplifier-module-provider-distribution-fixture"\nversion="0.1.0"\n''')
module=provider/'amplifier_module_provider_distribution_fixture';module.mkdir()
(module/'__init__.py').write_text('''from pathlib import Path
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse,TextBlock,Usage,ToolCall
class Provider:
    name='fixture'
    def parse_tool_calls(self,response):return response.tool_calls or []
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline distribution fixture',defaults={'model':'fixture','max_tokens':4096},capabilities=['tools'])
    async def list_models(self):return [{'id':'fixture'}]
    async def complete(self,request,**kwargs):
        with Path(__file__).with_name('provider-calls.txt').open('a') as stream:stream.write('call\\n')
        if 'native-runtime' in str(request.messages):Path(__file__).with_name('maintenance-observed.txt').write_text('Host scope reached from native session')
        if 'cobalt' in str(request.messages):Path(__file__).with_name('memory-observed.txt').write_text('Delivered through memory context')
        if not getattr(self,'called',False):
            self.called=True
            extras=[ToolCall(id='maintenance-call',name='app_control',arguments={'operation':'dispatch','args':{'action':'updates.runtime.inspect','args':{},'id':'fixture-maintenance'}})] if Path(__file__).with_name('maintenance-enabled').exists() else []
            return ChatResponse(content=[TextBlock(text='Creating an artifact through the advertised host.')],tool_calls=extras+[ToolCall(id='artifact-call',name='app_control',arguments={'operation':'dispatch','args':{'action':'canvas.show','args':{'kind':'text','title':'Native artifact','content':'Core to ACP to AHP'},'id':'fixture-artifact'}})],finish_reason='tool_calls')
        return ChatResponse(content=[TextBlock(text='Native distribution graph completed.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):await coordinator.mount('providers',Provider(),name='fixture')
''')
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
bundle=p/'fixture.yaml';bundle.write_text('bundle:\n  name: distribution-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-distribution-fixture\n    source: '+str(provider)+'\n')
(home/'settings.yaml').write_text('bundle:\n  app: []\n')
(p/'runtime.toml').write_text('[project]\nname="empty-maintenance-fixture"\nversion="0.1.0"\nrequires-python=">=3.13"\ndependencies=[]\n')
(p/'native.json').write_text(json.dumps({'adminGenerations':True,'runtimeManifest':str(p/'runtime.toml'),'home':str(home),'appHome':str(p/'native-app'),'bundle':str(bundle),'startupTimeout':90,'adminWorkspaceRoots':[str(p/'workspace')]}))
`;
test('installed Core/Foundation adapter composes resources, questions, opt-in Recall and host maintenance',{skip:!python||!ownersPython,timeout:120000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'unified-native-distribution-')),workspace=join(directory,'workspace'),web=join(directory,'web');await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'<html><head></head><body>Fixture</body></html>');
 let app,socket;try{
  const result=spawnSync(python,['-c',setup,directory],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);
  await writeFile(join(directory,'provider/amplifier_module_provider_distribution_fixture/maintenance-enabled'),'enabled');
  app=await createDistribution({account:'offline-native-fixture',stateDirectory:join(directory,'distribution'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,engines:[{id:'amplifier',label:'Actual Core/Foundation',command:python,args:['-m','amplifier_acp','--config',join(directory,'native.json')]}],nativeAdmin:{engine:'amplifier'},maintenance:{},operations:{python:ownersPython},recall:{python:ownersPython}});
  socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');let next=0;const pending=new Map();socket.on('message',raw=>{const message=JSON.parse(raw);const entry=pending.get(message.id);if(entry){pending.delete(message.id);clearTimeout(entry.timer);message.error?entry.reject(Error(message.error.message)):entry.resolve(message.result);}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Native request timed out'));},100000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
  await request('initialize',{channel:'ahp-root://',clientId:'native-fixture-browser',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
  const session='ahp-session:/'+randomUUID();await request('createSession',{channel:session,provider:'amplifier',workingDirectories:[pathToFileURL(workspace).href]});
  const agentsBeforeMaintenance=app.host.diagnostics().activeAgents;
  const maintenance=await request('resourceRead',{channel:'ahp-root://',uri:'amplifier-capability://maintenance/maintenance?scope=host',encoding:'utf-8'});assert.equal(JSON.parse(maintenance.data).data.updates.scope,'native-runtime');assert.equal(app.host.diagnostics().activeAgents,agentsBeforeMaintenance);
  const recallAction=async(operation,args={},id=randomUUID())=>(await request('x-amplifier/capabilityAction',{channel:session,topic:'recall',operation,version:1,args,commandId:id})).result;
  const note=await recallAction('memory.create',{scope:'workspace',text:'The test artifact uses cobalt.'});
  await recallAction('memory.configure',{expectedRevision:0,use:true});
  const commandId=randomUUID();await app.host.submitTurn(session,{commandId,clientId:'native-fixture-browser',text:'Create the test artifact.'});
  const settled=await app.host.waitForTurn(session,commandId,30000);assert.equal(settled.status,'completed',settled.detail);assert.match(settled.text,/Native distribution graph completed/);
  assert.equal(await readFile(join(directory,'provider/amplifier_module_provider_distribution_fixture/maintenance-observed.txt'),'utf8'),'Host scope reached from native session');
  assert.equal(await readFile(join(directory,'provider/amplifier_module_provider_distribution_fixture/memory-observed.txt'),'utf8'),'Delivered through memory context');
  const page=await app.resources.read({uri:'amplifier-capability://canvas',scope:session});assert.equal(page.data.canvasArtifacts.length,1);assert.equal(page.data.canvasArtifacts[0].title,'Native artifact');
  const body=await request('resourceRead',{channel:'ahp-root://',uri:page.data.canvasArtifacts[0].bodyUri,encoding:'utf-8'});assert.equal(JSON.parse(body.data).content,'Core to ACP to AHP');
  const context=await app.host.readSessionContext(session,2);assert.match(context.messages.at(-1).text,/Native distribution graph completed/);
  const spoken=await app.host.nativeControl(session,'voice.transcript.record',{callId:'fixture-call',itemId:'fixture-voice',role:'user',text:'Preserve this spoken thought.',append:false,commandId:'fixture-spoken-record'});assert.equal(spoken.recorded,true);assert.equal(spoken.executed,false);
  await app.host.invalidateNativeHistory(session);
  const history=await request('subscribe',{channel:session.replace('ahp-session:','ahp-chat:'),view:{turns:5}});const voice=history.snapshot.state.turns.find(turn=>turn.message.text==='Preserve this spoken thought.');assert.ok(voice);assert.equal(voice.message._meta['amplifier.dev/history'].recordedOnly,true);assert.equal(voice.state,'complete');assert.deepEqual(voice.responseParts,[]);
  assert.equal((await app.host.waitForTurn(session,commandId,1000)).status,'completed');
  const action=(operation,args,id=randomUUID())=>request('x-amplifier/capabilityAction',{channel:session,topic:'questions',operation,version:1,args,commandId:id});
  const question=(await action('question.create',{prompt:'Which color should be used?',dependency:'Color selection',required:true,options:[{id:'blue',label:'Blue'}]})).result;
  const answerId=randomUUID(),answerArgs={id:question.id,expectedRevision:1,optionId:'blue'};
  const answer=(await action('question.answer',answerArgs,answerId)).result;
  assert.equal(answer.status,'answered');assert.equal(answer.delivery.status,'accepted');
  assert.equal((await app.host.waitForTurn(session,answer.delivery.inputId,30000)).status,'completed');
  await assert.rejects(action('question.answer',answerArgs,answerId),/already admitted/);
  assert.equal((await action('question.read',{id:question.id})).result.delivery.inputId,answer.delivery.inputId);
  const indexed=await app.host.readUserMessage(session,answer.delivery.inputId);assert.equal(indexed.inputOrigin,'question');assert.equal(indexed.questionId,question.id);
  const stamp=await app.host.inspectRecallSource(session),saved=await app.host.readRecallSource(session,{expectedRevision:stamp.revision,limit:10});
  const persisted=saved.rows.filter(row=>row.questionId===question.id&&row.role==='user');assert.equal(persisted.length,1);assert.equal(persisted[0].authorization,'unverified-native-history');
  const executionRevision=(await app.host.inspectSession(session)).executionRevision;
  await recallAction('recall.refresh',{scope:'task'});
  let coverage=await recallAction('recall.status');
  for(let n=0;n<30&&coverage.status==='indexing';n++) {await recallAction('recall.wait',{afterRevision:coverage.revision,waitMs:1000});coverage=await recallAction('recall.status');}
  assert.equal(coverage.status,'ready');const matches=await recallAction('recall.search',{query:'artifact',scope:'task'});assert.ok(matches.items.length);
  assert.equal((await app.host.inspectSession(session)).executionRevision,executionRevision,'Passive indexing must not execute a turn');
  assert.equal((await recallAction('memory.read',{id:note.id})).text,'The test artifact uses cobalt.');
  await assert.rejects(app.host.nativeControl(session,'question.submit',{inputId:'bypass',text:'Do not admit'}),/submitQuestionAnswer/);
 }finally{socket?.terminate();await app?.close();await rm(directory,{recursive:true,force:true});}
});

const mcpPython=process.env.MCP_BROKER_PYTHON;
const seedObserver=String.raw`
import asyncio,hashlib,json,sys
from pathlib import Path
from amplifier_unified_mcp.broker import Broker
async def main():
 p=Path(sys.argv[1]);broker=Broker(p/'distribution/capabilities/mcp');manager=broker.manager
 installed=manager.root/'installs'/('a'*24);installed.mkdir(parents=True)
 code=installed/'observer.py';code.write_bytes(Path(sys.argv[2]).read_bytes());data=p/'observer-source';data.mkdir()
 (data/'record.json').write_text(json.dumps({'status':'pending','revision':'r1','summary':'Exact source pending'}))
 await manager._change(lambda state:state['installations'].append({'id':'a'*24,'status':'installed','commit':'reviewed-fixture'}))
 await manager.configure({'id':'observer','name':'Qualified fixture','command':sys.executable,'args':[str(code),str(data)],'installationId':'a'*24})
 descriptor={'installationId':'a'*24,'connectionId':'observer','toolName':'fixture_observe','implementationRevision':'reviewed-fixture','requestArgument':'observation','targetSchema':{'const':{'record':'one','owner':'fixture'}},'scopeSchema':{'const':{'read':'one'}},'artifacts':[{'path':'observer.py','sha256':hashlib.sha256(code.read_bytes()).hexdigest()}],'qualification':{'kind':'reviewed-trusted-read','providerFree':True,'concurrentReadSafe':True,'evidence':[{'uri':'fixture://review','digest':'a'*64}]}}
 (p/'observer.json').write_text(json.dumps(descriptor));await broker.close()
asyncio.run(main())
`;
test('installed MCP watch owner admits one finite explanation through resident ACP/Core runtime',{skip:!python||!ownersPython||!mcpPython,timeout:90000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'unified-watch-distribution-')),workspace=join(directory,'workspace'),web=join(directory,'web');await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'<html><head></head><body>Fixture</body></html>');
 let app,socket;try{
  for(const [executable,script,extra]of [[python,setup,[]],[mcpPython,seedObserver,[new URL('./fixtures/mcp_observer.py',import.meta.url).pathname]]]){const result=spawnSync(executable,['-I','-c',script,directory,...extra],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);}
  app=await createDistribution({account:'offline-watch-fixture',stateDirectory:join(directory,'distribution'),defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],webDirectory:web,engines:[{id:'amplifier',label:'Core/Foundation',command:python,args:['-I','-m','amplifier_acp','--config',join(directory,'native.json')]}],operations:{python:ownersPython},mcp:{python:mcpPython}});
  socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');let next=0;const pending=new Map();socket.on('message',raw=>{const message=JSON.parse(raw),entry=pending.get(message.id);if(entry){pending.delete(message.id);clearTimeout(entry.timer);message.error?entry.reject(Error(message.error.message)):entry.resolve(message.result);}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Watch request timed out'));},60000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
  const initialized=await request('initialize',{channel:'ahp-root://',clientId:'watch-fixture-browser',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});assert.ok(initialized._meta['amplifier.dev/capabilities'].topics.observations);
  const session='ahp-session:/'+randomUUID();await request('createSession',{channel:session,provider:'amplifier',workingDirectories:[pathToFileURL(workspace).href]});
  const action=async(topic,operation,args={})=>(await request('x-amplifier/capabilityAction',{channel:session,topic,operation,version:1,args,commandId:randomUUID()})).result;
  await app.host.submitTurn(session,{commandId:'warm',text:'Create a fixture artifact.',clientId:'watch-fixture-browser'});assert.equal((await app.host.waitForTurn(session,'warm',30000)).status,'completed');
  const task=await app.host.nativeControl(session,'task.create',{objective:'Observe the exact fixture record',maxTurns:3,commandId:'task',expectedRevision:0});assert.equal(task.task.status,'active');
  const active=app.host.diagnostics().activeAgents,callLog=join(directory,'provider/amplifier_module_provider_distribution_fixture/provider-calls.txt'),callsBefore=(await readFile(callLog,'utf8')).trim().split('\n').length;
  assert.equal((await action('connectors','smartTools.connect',{id:'observer'})).status,'completed');
  const qualified=await action('observations','observation.qualify',{descriptor:JSON.parse(await readFile(join(directory,'observer.json'),'utf8'))});
  const args={observerId:qualified.observer.id,target:{record:'one',owner:'fixture'},scope:{read:'one'},sourceId:'fixture://one',args:{},intervalSeconds:15,durationSeconds:120,readTimeoutSeconds:3};
  const preview=await action('observations','observation.preview',args),created=await action('observations','observation.create',{...args,previewHash:preview.previewHash,requestId:'watch'});let report;
  for(let n=0;n<60;n++){report=await action('observations','observation.read',{id:created.watch.id});if(report.runs.at(-1)?.phase==='pending')break;await new Promise(resolve=>setTimeout(resolve,100));}
  assert.equal(report.runs.at(-1).phase,'pending',JSON.stringify(report));assert.equal(report.handoffs.length,0);assert.equal(app.host.diagnostics().activeAgents,active);
  await writeFile(join(directory,'observer-source/record.json'),JSON.stringify({status:'actionable',revision:'r2',summary:'The exact fixture record completed',evidence:[{uri:'fixture://one/result',revision:'r2',digest:'a'.repeat(64)}]}));
  for(let n=0;n<80;n++){report=await action('observations','observation.read',{id:created.watch.id});if(report.handoffs[0]?.phase==='completed')break;await new Promise(resolve=>setTimeout(resolve,500));}
  assert.equal(report.handoffs.length,1);assert.equal(report.handoffs[0].phase,'completed',JSON.stringify(report));assert.equal(report.watch.terminal,'actionable');
  const inputId=report.handoffs[0].inputId;await assert.rejects(app.host.readUserMessage(session,inputId),/not-user/,'A generated watch result must not authorize future human-only work');
  const stamp=await app.host.inspectRecallSource(session),history=await app.host.readRecallSource(session,{expectedRevision:stamp.revision,limit:10});assert.ok(history.rows.every(row=>row.authorization==='unverified-native-history'));assert.equal((await readFile(callLog,'utf8')).trim().split('\n').length,callsBefore+1,'One finite model explanation, without repeating work');
  const operations=await action('operations','operations.list');assert.equal(operations.operations.find(row=>row.source==='observation').controlAvailable,false);
  assert.equal((await app.host.nativeControlExisting(session,'task.get',{})).task.revision,task.task.revision);
 }finally{socket?.terminate();await app?.close();await rm(directory,{recursive:true,force:true});}
});
