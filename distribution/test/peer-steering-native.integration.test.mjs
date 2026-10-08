import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,mkdir,writeFile,readFile,access} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
const {createCoordinationCapabilities}=await import(process.env.COORDINATION_MODULE??'@amplifier/unified-coordination-capability');
const python=process.env.COORDINATION_PYTHON,hostModule=process.env.COORDINATION_HOST_MODULE;
const wait=async predicate=>{for(let i=0;i<1500;i++){if(await predicate())return;await new Promise(resolve=>setTimeout(resolve,20));}throw Error('Fixture boundary timed out');};
const setup=String.raw`
import importlib.util,json,sys
from pathlib import Path
p=Path(sys.argv[1]);home=p/'native';home.mkdir();provider=p/'provider';provider.mkdir()
(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-peer-fixture"\nversion="0.1.0"\n')
module=provider/'amplifier_module_provider_peer_fixture';module.mkdir()
(module/'__init__.py').write_text('''import asyncio,json
from pathlib import Path
from amplifier_core.models import ProviderInfo,ToolResult
from amplifier_core.message_models import ChatResponse,TextBlock,Usage,ToolCall
class Provider:
    name='fixture'
    def __init__(self,config=None):self.path=Path((config or {}).get('directory','.'))
    def parse_tool_calls(self,response):return response.tool_calls or []
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline peer fixture',defaults={'model':'fixture','max_tokens':4096},capabilities=['tools'])
    async def list_models(self):return [{'id':'fixture'}]
    async def complete(self,request,**kwargs):
        with (self.path/'provider-requests.jsonl').open('a') as output:output.write(json.dumps([m.model_dump(mode='json') for m in request.messages])+'\\n')
        text=str(request.messages)
        if 'block-until-steered' in text and 'Gate released' not in text:
            return ChatResponse(content=[],tool_calls=[ToolCall(id='gate-call',name='fixture_gate',arguments={})],finish_reason='tool_calls')
        original='STEER-ORBIT-572' in text and 'agent-origin' in text
        return ChatResponse(content=[TextBlock(text='Verified peer correction in original turn.' if original else 'Ordinary user reply.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):
    p=Path(config['directory'])
    class Gate:
        name='fixture_gate';description='Offline deterministic gate';input_schema={'type':'object','properties':{}}
        async def execute(self,args):
            (p/'entered').touch()
            async with asyncio.timeout(40):
                while not (p/'release').exists():await asyncio.sleep(.02)
            return ToolResult(success=True,output='Gate released')
    await coordinator.mount('providers',Provider(config),name='fixture')
    await coordinator.mount('tools',Gate(),name='fixture_gate')
''')
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
bundle=p/'fixture.yaml';bundle.write_text('bundle:\n  name: peer-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-peer-fixture\n    source: '+str(provider)+'\n    config:\n      directory: '+str(p)+'\n')
(home/'settings.yaml').write_text('bundle:\n  app: []\n')
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'app'),'bundle':str(bundle),'startupTimeout':90}))
`;

test('installed Host and Native steer exactly one current peer generation with saved original and no idle replay',{skip:!python||!hostModule,timeout:90000},async()=>{
 const {createHost}=await import(hostModule),directory=await realpath(await mkdtemp(join(tmpdir(),'peer-native-steering-'))),workspace=join(directory,'workspace');await mkdir(workspace);
 const seeded=spawnSync(python,['-I','-B','-c',setup,directory],{encoding:'utf8'});assert.equal(seeded.status,0,seeded.stderr);
 const ownerConfig=join(directory,'owner.json');await writeFile(ownerConfig,JSON.stringify({dataDir:join(directory,'authority')}));
 let host,owner;const proofs=[];
 try{
  owner=createCoordinationCapabilities({owner:{command:python,args:['-I','-B','-m','amplifier_unified_coordination.server','--config',ownerConfig]},
   grants:{inspect:async session=>{const state=await host.inspectSession(session);return {sessionId:session,nativeSessionId:state.nativeSessionId,kind:'root',workspace:state.executionDirectory,locationRevision:state.nativeLocationRevision,interruptionRevision:state.interruptionRevision};},input:args=>host.readUserMessage(args.session,args.messageId),review:async()=>{throw Error('Explicit human fixture grant required');}},
   delivery:{inspect:async session=>{const task=await host.readTaskState(session);return {...await host.inspectSession(session),available:task.available,task:task.task};},submit:(session,input)=>host.submitPeer(session,input),steering:{submit:(session,input)=>host.submitPeerSteering(session,input),inspect:(session,id)=>host.inspectPeerSteering(session,id)}},
   listCoordinationSessions:async()=>({items:[]}),readCoordinationSession:(session,args)=>host.readCoordinationSession(session,args),readCoordinationWorkers:async()=>({items:[]}),controlCoordinationWorker:async()=>{throw Error('No child execution');},controlCoordinationSession:async()=>{throw Error('Guarded peer intake required');},observeSession:async()=>()=>{}});
  const hostConfig={stateDirectory:join(directory,'host'),allowedWorkspaceRoots:[workspace],engines:[{id:'native',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(directory,'native.json')],env:{AMPLIFIER_SESSION_STATE_HOME:join(directory,'writers')}}],capabilities:owner,nativeHostCapabilities:{version:1,features:{peerNotifications:true}},nativeHostRequest:async(context,params)=>{
   if(params.operation==='coordination.notifications')return owner.passiveNotifications(context.session,params.args);
   assert.equal(params.operation,'coordination.delivery.admit');const proof=await owner.authorizePeerDelivery(context.session,params.args);proofs.push(proof);return proof;
  },turnSettled:event=>owner.turnSettled(event)};
  host=await createHost(hostConfig);
  const source='ahp-session:/'+randomUUID(),target='ahp-session:/'+randomUUID();
  // Public creation API, as used by independent AHP clients.
  const {AhpClient}=await import('@microsoft/agent-host-protocol/client'),{WebSocketTransport}=await import('@microsoft/agent-host-protocol/ws');
  const client=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();
  try{await client.initialize({clientId:'peer-human',protocolVersions:['0.9.0']});for(const channel of [source,target])await client.request('createSession',{channel,provider:'native',workingDirectories:[pathToFileURL(workspace).href]});}finally{await client.shutdown();}
  const invoke=(operation,args,commandId=randomUUID(),actor={origin:'ui',clientId:'peer-human',actorId:'peer-human'})=>host.invokeCapability({channel:'ahp-root://',topic:'coordination',operation:'coordination.'+operation,version:1,commandId,args},actor);
  const grant='grant-'+randomUUID();await invoke('grant',{sessionId:source,participants:[target],purpose:'Correct only the current response',modes:['steer'],idleStart:false},grant);
  const original='human-'+randomUUID();await host.submitTurn(target,{commandId:original,text:'block-until-steered',clientId:'peer-human',origin:'ui'});
  await wait(async()=>access(join(directory,'entered')).then(()=>true,()=>false));
  const before=await host.inspectSession(target);assert.equal(before.activeSteering.supported,true);
  const actor={origin:'agent',session:source,actorId:'agent:'+(await host.inspectSession(source)).nativeSessionId},request='steer-'+randomUUID(),args={sessionId:source,recipientSessionId:target,grantId:grant,mode:'steer',text:'Apply STEER-ORBIT-572 to the current answer.'};
  const sent=await invoke('send',args,request,actor),inputId=sent.result.receipt.inputId;
  assert.ok(['accepted','applied'].includes(sent.result.receipt.status),JSON.stringify(sent));
  assert.equal(sent.result.executionStarted,false);assert.ok(proofs.length>=2&&proofs.every(p=>p.admitted));
  assert.equal((await host.inspectSession(target)).activeTurnId,original);
  await writeFile(join(directory,'release'),'');const finished=await host.waitForTurn(target,original,30000);
  assert.equal(finished.status,'completed',JSON.stringify(finished));assert.match(finished.text,/Verified peer correction/);
  const result=await invoke('result',{sessionId:source,requestId:request},randomUUID(),actor);assert.equal(result.result.receipt.status,'applied');assert.equal(result.result.qualified,false);
  const chat=target.replace('ahp-session:','ahp-chat:');assert.equal(host.store.turns(chat).turns.length,1);
  await assert.rejects(invoke('send',args,request,actor),/already admitted/);
  const idle=await invoke('send',args,'late-'+randomUUID(),actor);assert.equal(idle.result.receipt.status,'suppressed');assert.equal(host.store.turns(chat).turns.length,1);
  const refreshed=await host.refreshSessionHistory(target);assert.equal(refreshed.unresolvedSteeringInputs,0,JSON.stringify(refreshed));assert.equal(refreshed.refreshedTurns,1,JSON.stringify(refreshed));
  const reloaded=await host.readSessionContext(target,10),peer=reloaded.messages.filter(row=>row.inputOrigin==='peer');assert.equal(peer.length,1,JSON.stringify(reloaded));assert.equal(peer[0].text,args.text);
  await assert.rejects(host.readUserMessage(target,inputId),error=>error.reason==='not-user'||error.reason==='not-indexed');
  assert.equal(peer[0].role,'assistant');assert.equal(peer[0].delivery,'steering');assert.equal(peer[0].peerEnvelope.grantId,grant);
  assert.ok(reloaded.messages.findIndex(row=>row.id===inputId)<reloaded.messages.findIndex(row=>row.role==='assistant'&&row.text.includes('Verified peer correction')));
  assert.equal(host.store.turns(chat).turns.length,1);
  const requestsBeforeRestart=await readFile(join(directory,'provider-requests.jsonl'),'utf8');
  await host.close();host=await createHost(hostConfig);
  const restored=await host.readSessionContext(target,10),restoredPeer=restored.messages.filter(row=>row.inputOrigin==='peer');
  assert.equal(restoredPeer.length,1);assert.equal(restoredPeer[0].role,'assistant');assert.equal(restoredPeer[0].text,args.text);
  assert.equal(await readFile(join(directory,'provider-requests.jsonl'),'utf8'),requestsBeforeRestart);
  await assert.rejects(host.readUserMessage(target,inputId),error=>error.reason==='not-user'||error.reason==='not-indexed');
  const canonical=spawnSync(python,['-I','-B','-c',String.raw`import json,sys
from pathlib import Path
rows=[json.loads(line) for path in Path(sys.argv[1]).rglob('transcript.jsonl') for line in path.read_text().splitlines()]
peer=[r for r in rows if r.get('metadata',{}).get('inputOrigin')=='peer']
assert len(peer)==1
assert peer[0]['metadata']['peerEnvelope']['mode']=='steer'
assert peer[0]['metadata']['activeInputId']==sys.argv[2]
assert peer[0]['metadata']['amplifier_public_message']['blocks']==[{'type':'text','text':sys.argv[3]}]
assert 'agent-origin' in peer[0]['content']
print('One original peer correction in the same native turn')`,join(directory,'native'),original,args.text],{encoding:'utf8'});assert.equal(canonical.status,0,canonical.stderr);
  await writeFile(join(directory,'acceptance.json'),JSON.stringify({passed:true,source,target,original,inputId,proofChecks:proofs.length,oneTurn:true,peerNotHuman:true,exactOriginal:true,lateInputSuppressed:true,refresh:refreshed,restartNoExecution:true,directory},null,2));
  console.log('Peer steering receipt: '+join(directory,'acceptance.json'));
 }finally{await writeFile(join(directory,'release'),'');await host?.close();await owner?.close();}
});
