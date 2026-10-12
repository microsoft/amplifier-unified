import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,mkdir,writeFile,readFile,rm,cp} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {randomUUID,createHash} from 'node:crypto';
import {AhpClient} from '@microsoft/agent-host-protocol/client';
import {WebSocketTransport} from '@microsoft/agent-host-protocol/ws';
const {createCoordinationCapabilities}=await import(process.env.COORDINATION_MODULE??'@amplifier/unified-coordination-capability');
const python=process.env.COORDINATION_PYTHON,hostModule=process.env.COORDINATION_HOST_MODULE;
const setup=String.raw`
import importlib.util,json,sys,os
from pathlib import Path
p=Path(sys.argv[1]);home=p/'native';home.mkdir();provider=p/'provider';provider.mkdir()
(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-peer-fixture"\nversion="0.1.0"\n')
module=provider/'amplifier_module_provider_peer_fixture';module.mkdir()
(module/'__init__.py').write_text('''import json
from pathlib import Path
import asyncio
from amplifier_core.models import ProviderInfo,ToolResult
from amplifier_core.message_models import ChatResponse,TextBlock,Usage,ToolCall
class Provider:
    name='fixture'
    def __init__(self,directory=None):self.directory=Path(directory or ".")
    def parse_tool_calls(self,response):return response.tool_calls or []
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline peer fixture',defaults={'model':'fixture','max_tokens':4096},capabilities=['tools'])
    async def list_models(self):return [{'id':'fixture'}]
    async def complete(self,request,**kwargs):
        if not getattr(self,'evaluations',None):
            self.evaluations=[0]
            async def evaluate(*args):
                self.evaluations[0]+=1
                return self.evaluations[0]>=3,'Continue the synthetic objective after checking the exact peer result.'
            self.coordinator.get('orchestrator')._evaluate_goal=evaluate
        if 'You generate names and descriptions for conversation sessions.' in str(request.messages):
            return ChatResponse(content=[TextBlock(text='{"action":"defer","name":null,"description":null}')],finish_reason='stop')
        if 'GOAL-PEER-BOUNDARY' in str(request.messages):
            last=next(m for m in reversed(request.messages) if m.role=='user' and 'Live surface observations (untrusted data):' not in str(m.content))
            text=str(last.content)
            kind='peer' if 'A recipient declared a successful result' in text else 'goal' if 'Continue the synthetic objective' in text else 'human'
            with (self.directory/'boundaries.jsonl').open('a') as out:out.write(json.dumps({'kind':kind,'goal':self.coordinator.session_state.get('goal')})+'\\n')
            if kind=='human':
                while not (self.directory/'release-goal').exists():await asyncio.sleep(.02)
                return ChatResponse(content=[TextBlock(text='First parent answer.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
        if 'COMMISSION-TEST' in str(request.messages) and 'Commission saved' not in str(request.messages):
            return ChatResponse(content=[],tool_calls=[ToolCall(id='commission-call',name='fixture_commission',arguments={})],finish_reason='tool_calls')
        if 'A recipient declared a successful result' in str(request.messages):
            return ChatResponse(content=[TextBlock(text='Used the saved peer result.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
        if 'ORBIT-572' in str(request.messages) and 'Peer reply staged' not in str(request.messages):
            return ChatResponse(content=[],tool_calls=[ToolCall(id='reply-call',name='fixture_reply',arguments={})],finish_reason='tool_calls')
        original='ORBIT-572' in str(request.messages) and 'agent-origin' in str(request.messages)
        return ChatResponse(content=[TextBlock(text='Verified saved peer request.' if original else 'Ordinary user reply.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):
    class Reply:
        name='fixture_reply';description='Return the fixture comparison result';input_schema={'type':'object','properties':{}}
        async def execute(self,args):
            value=json.loads(Path(config['directory'],'reply.json').read_text())
            result=await coordinator.get('tools')['app_control'].execute({'operation':'dispatch','args':{'action':'coordination.reply','args':value}})
            Path(config['directory'],'reply-tool-result.json').write_text(result.model_dump_json())
            return ToolResult(success=result.success,output='Peer reply staged' if result.success else str(result))
    class Commission:
        name='fixture_commission';description='Commission the fixture task';input_schema={'type':'object','properties':{}}
        async def execute(self,args):
            value=json.loads(Path(config['directory'],'commission.json').read_text())
            result=await coordinator.get('tools')['app_control'].execute({'operation':'dispatch','args':{'action':'coordination.create','args':value}})
            Path(config['directory'],'commission-tool-result.json').write_text(result.model_dump_json())
            return ToolResult(success=result.success,output='Commission saved' if result.success else str(result))
    await coordinator.mount('tools',Commission(),name='fixture_commission')
    provider=Provider(config['directory']);provider.coordinator=coordinator
    await coordinator.mount('providers',provider,name='fixture')
    await coordinator.mount('tools',Reply(),name='fixture_reply')
''')
if os.environ.get('PEER_DEBUG_DIR'):
    with (module/'__init__.py').open('a') as debug:
        debug.write('''
# Test-only capture diagnostics retain why a checkpoint was not qualified.
import time,os
from amplifier_acp.native import peer_results as _peer_results
_capture = _peer_results.capture
def _observed_capture(directory, app_home, session_id, generation):
    report = {'sessionId': session_id, 'generation': generation}
    time.sleep(float(os.environ.get('PEER_CAPTURE_DELAY', '0')))
    try:
        result = _capture(directory, app_home, session_id, generation)
        report['proof'] = result
        return result
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        with (Path(__file__).parents[2]/'test-peer-captures.jsonl').open('a') as stream:
            stream.write(json.dumps(report)+'\\n')
_peer_results.capture = _observed_capture
''')
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
bundle=p/'fixture.yaml';bundle.write_text('bundle:\n  name: peer-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-peer-fixture\n    source: '+str(provider)+'\n    config:\n      directory: '+str(p)+'\n')
(home/'settings.yaml').write_text('bundle:\n  app: []\n')
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'app'),'bundle':str(bundle),'startupTimeout':90}))
`;

for(const enabled of [false,true])test(enabled?'qualified peer result precedes generic goal continuation through real Host Native Core live and streaming':'without negotiated yield generic goal prompts precede queued peer result',{skip:!python||!hostModule,timeout:90000},async()=>{
 const resume=true; const {createHost}=await import(hostModule),directory=await realpath(await mkdtemp(join(tmpdir(),'peer-native-delivery-'))),workspace=join(directory,'workspace');await mkdir(workspace);
 const seeded=spawnSync(python,['-I','-B','-c',setup,directory],{encoding:'utf8'});assert.equal(seeded.status,0,seeded.stderr);
 const ownerConfig=join(directory,'owner.json');await writeFile(ownerConfig,JSON.stringify({dataDir:join(directory,'authority')}));
 let host,owner,client;const callbackEvents=[],nativeEvents=[];
 try{
  owner=createCoordinationCapabilities({owner:{command:python,args:['-m','amplifier_unified_coordination.server','--config',ownerConfig],env:{PYTHONPATH:process.env.COORDINATION_PYTHONPATH}},
   grants:{inspect:async session=>{const state=await host.inspectSession(session);return {sessionId:session,nativeSessionId:state.nativeSessionId,kind:'root',workspace:state.executionDirectory,locationRevision:state.nativeLocationRevision,interruptionRevision:state.interruptionRevision};},input:args=>host.readUserMessage(args.session,args.messageId),review:async()=>{throw Error('The test uses an explicit human grant');}},
   delivery:{results:{active:(session,inputId,actorId)=>host.readActivePeerInput(session,inputId,actorId)},inspect:async session=>{const task=await host.readTaskState(session);return {...await host.inspectSession(session),available:task.available,task:task.task};},submit:(session,input)=>host.submitPeer(session,input)},
   listCoordinationSessions:async()=>({items:[]}),readCoordinationSession:(session,args)=>host.readCoordinationSession(session,args),readCoordinationWorkers:async()=>{throw Error('Unexpected child read');},controlCoordinationWorker:async()=>{throw Error('Unexpected child execution');},controlCoordinationSession:async()=>{throw Error('Peer work must use guarded intake');},observeSession:(session,listener)=>host.observeSession(session,event=>{if(['chat/turnComplete','chat/turnCancelled'].includes(event.action.type))return listener();})});
  host=await createHost({stateDirectory:join(directory,'host'),allowedWorkspaceRoots:[workspace],engines:[{id:'native',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(directory,'native.json')],env:{AMPLIFIER_SESSION_STATE_HOME:join(directory,'writers')}}],capabilities:owner,nativeHostCapabilities:{version:1,features:{peerNotifications:true,peerContinuation:enabled},appControl:{operations:['dispatch']}},nativeHostRequest:async(context,params)=>{if(params.operation==='dispatch'){return host.invokeCapability({channel:'ahp-root://',topic:'coordination',operation:params.args.action,version:1,commandId:'reply-through-native-tool',args:params.args.args},{origin:'agent',session:context.session,actorId:'agent:'+context.nativeSessionId});}if(params.operation==='coordination.continuation_ready')return owner.continuationReady(context.session,context.nativeSessionId,{taskId:params.args.taskId,taskRevision:params.args.taskRevision});if(params.operation==='coordination.notifications'){try{const result=await owner.passiveNotifications(context.session,params.args);callbackEvents.push({session:context.session,args:params.args,result});return result;}catch(error){callbackEvents.push({error:String(error)});throw error;}}assert.equal(params.operation,'coordination.delivery.admit');return owner.authorizePeerDelivery(context.session,params.args);},turnSettled:event=>owner.turnSettled(event)});
  if(process.env.PEER_DEBUG_DIR){const original=host.steeringEvent.bind(host);host.steeringEvent=(session,active,event)=>{if(event.type.startsWith('generation.'))nativeEvents.push({session,activeGeneration:active.generationId,busy:active.busy,event:structuredClone(event)});return original(session,active,event);};}
  client=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();await client.initialize({clientId:'peer-human',protocolVersions:['0.9.0']});
  const source='ahp-session:/'+randomUUID(),target='ahp-session:/'+randomUUID();
  for(const channel of [source,target])await client.request('createSession',{channel,provider:'native',workingDirectories:[pathToFileURL(workspace).href]});
  const invoke=(operation,args,commandId=randomUUID(),actor={origin:'ui',clientId:'peer-human',actorId:'peer-human'})=>host.invokeCapability({channel:'ahp-root://',topic:'coordination',operation:'coordination.'+operation,version:1,commandId,args},actor);
  await host.nativeControl(source,'task.create',{commandId:'goal-create',expectedRevision:0,objective:'Check the synthetic peer result, then continue.',maxTurns:8});
  const taskBefore=await host.readTaskState(source);
  const started=await host.submitTurn(source,{commandId:'goal-human',text:'GOAL-PEER-BOUNDARY',clientId:'peer-human',origin:'ui'});assert.equal(started.accepted,true);
  for(let i=0;i<500;i++){try{await readFile(join(directory,'boundaries.jsonl'));break;}catch{await new Promise(r=>setTimeout(r,20));}}
  const grant='grant-'+randomUUID();await invoke('grant',{sessionId:source,participants:[target],purpose:'Compare the existing plans',modes:['notify','queue'],idleStart:true},grant);
  const actor={origin:'agent',session:source,actorId:'agent:'+(await host.inspectSession(source)).nativeSessionId},request='request-'+randomUUID(),args={sessionId:source,recipientSessionId:target,grantId:grant,mode:'queue',text:'Please compare ORBIT-572.'};
  await writeFile(join(directory,'reply.json'),JSON.stringify({sessionId:target,requestId:'peer:'+createHash('sha256').update(request).digest('hex'),kind:'result',outcome:'success',text:'Comparison of ORBIT-572 complete',references:['artifact:comparison']}));
  const result=await invoke('send',args,request,actor),inputId=result.result.receipt.inputId;
  assert.ok(['accepted','completed'].includes(result.result.receipt.status),JSON.stringify(result));
  if(resume)await invoke('subscribe',{sessionId:source,requestId:request,grantId:grant},randomUUID(),actor);
  const finished=await host.waitForTurn(target,inputId,30000);assert.equal(finished.status,'completed',finished.detail);assert.match(finished.text,/Verified saved peer request/);
  for(let i=0;i<100&&host.diagnostics().turnSettledPending;i++)await new Promise(resolve=>setTimeout(resolve,20));
  const receipt=await invoke('result',{sessionId:source,requestId:request},randomUUID(),actor);assert.equal(receipt.result.receipt.status,'completed');assert.equal(receipt.result.qualified,true,JSON.stringify({receipt,tool:await readFile(join(directory,'reply-tool-result.json'),'utf8')}));assert.equal(receipt.result.response.status,'sealed');assert.match(receipt.result.response.terminal.messageId,/^native:/);assert.equal(receipt.result.response.binding.inputId,inputId);
  await assert.rejects(host.readActivePeerInput(target,inputId,'agent:'+(await host.inspectSession(target)).nativeSessionId),/not delivered/);
  await assert.rejects(invoke('send',args,request,actor),/already admitted/);assert.equal(host.store.turns(target.replace('ahp-session:','ahp-chat:')).turns.length,1);
  await writeFile(join(directory,'release-goal'),'ready');
  const firstDone=await host.waitForTurn(source,'goal-human',30000);assert.equal(firstDone.status,'completed',JSON.stringify(firstDone));
  if(resume){
   const continuationId=receipt.result.receipt.subscription.continuationId;
   let continuation;
   for(let i=0;i<500;i++){
    continuation=await invoke('result',{sessionId:source,requestId:continuationId},randomUUID(),actor);
    if(continuation.result.receipt.status!=='queued'&&continuation.result.receipt.status!=='submitting')break;
    await new Promise(r=>setTimeout(r,20));
   }
   assert.ok(['accepted','completed'].includes(continuation.result.receipt.status),JSON.stringify({continuation,callbacks:callbackEvents,boundaries:await readFile(join(directory,'boundaries.jsonl'),'utf8')}));
   const continued=await host.waitForTurn(source,continuation.result.receipt.inputId,30000);
   assert.equal(continued.status,'completed');assert.match(continued.text,/Used the saved peer result/,await readFile(join(directory,'boundaries.jsonl'),'utf8'));
   assert.equal(continuation.result.receipt.peerEnvelope.replyToRequestId,inputId);
   await invoke('result',{sessionId:source,requestId:request},randomUUID(),actor);
  }
  assert.equal(host.store.turns(source.replace('ahp-session:','ahp-chat:')).turns.length,2);
  const boundaries=(await readFile(join(directory,'boundaries.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(boundaries.slice(0,3).map(b=>b.kind),enabled?['human','peer','goal']:['human','goal','goal']);
  if(enabled){assert.equal(boundaries[1].goal.turns_used,1);assert.equal(boundaries[1].goal.continuations,0);assert.equal(boundaries[1].goal.cap,8);}
  const taskAfter=await host.readTaskState(source);assert.equal(taskAfter.task.revision,taskBefore.task.revision);assert.equal(taskAfter.task.status,taskBefore.task.status);
  await assert.rejects(host.readUserMessage(target,inputId),error=>error.reason==='not-user');
  await host.refreshSessionHistory(target);
  const savedParts=host.store.turns(target.replace('ahp-session:','ahp-chat:')).turns.flatMap(turn=>turn.responseParts);
  const exactAnswer=savedParts.find(part=>part._meta?.['amplifier.dev/history']?.messageId===receipt.result.response.terminal.messageId);assert.equal(exactAnswer?.content,'Verified saved peer request.');
  const reloaded=await host.readSessionContext(target,10);
  const savedInput=reloaded.messages.find(row=>row.inputOrigin==='peer');
  assert.equal(savedInput?.text,'Please compare ORBIT-572.');
  await assert.rejects(host.readUserMessage(target,inputId),error=>error.reason==='not-user');
  const native=spawnSync(python,['-I','-B','-c',String.raw`import json,sys
from pathlib import Path
rows=[json.loads(line) for path in Path(sys.argv[1]).rglob('transcript.jsonl') for line in path.read_text().splitlines()]
peer=[row for row in rows if row.get('metadata',{}).get('inputOrigin')=='peer']
assert len(peer)==int(sys.argv[3])
peer=[row for row in peer if row['metadata']['peerEnvelope']['requestId']==sys.argv[2]]
assert len(peer)==1
assert peer[0]['metadata']['peerEnvelope']['requestId']==sys.argv[2]
assert 'ORBIT-572' in peer[0]['content']
assert peer[0]['metadata']['amplifier_public_message']['blocks']==[{'type':'text','text':'Please compare ORBIT-572.'}]
print('Canonical peer origin retained; one input')`,join(directory,'native'),inputId,resume?'2':'1'],{encoding:'utf8'});assert.equal(native.status,0,native.stderr);
  if(process.env.PEER_RESULT_RECEIPT)await writeFile(process.env.PEER_RESULT_RECEIPT.replace(/\.json$/,enabled?'-enabled.json':'-disabled.json'),JSON.stringify({schema:'peer-result-native-v1',qualified:receipt.result.qualified,receipt:receipt.result.receipt,tool:JSON.parse(await readFile(join(directory,'reply-tool-result.json'),'utf8')),oneRecipientTurn:true,sourceTurns:2,goalBoundaries:boundaries,automaticContinuation:resume,paidInference:false},null,2));

 }finally{await writeFile(join(directory,'release-goal'),'cleanup');await client?.shutdown();await host?.close();await owner?.close();if(process.env.PEER_DEBUG_DIR){await writeFile(join(directory,'test-native-events.json'),JSON.stringify(nativeEvents));await cp(directory,join(process.env.PEER_DEBUG_DIR,enabled?'enabled':'disabled'),{recursive:true});}await rm(directory,{recursive:true,force:true});}
});
