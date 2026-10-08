import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,mkdir,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {AhpClient} from '@microsoft/agent-host-protocol/client';
import {WebSocketTransport} from '@microsoft/agent-host-protocol/ws';
const {createCoordinationCapabilities}=await import(process.env.COORDINATION_MODULE??'@amplifier/unified-coordination-capability');
const python=process.env.COORDINATION_PYTHON,hostModule=process.env.COORDINATION_HOST_MODULE;
const setup=String.raw`
import importlib.util,json,sys
from pathlib import Path
p=Path(sys.argv[1]);home=p/'native';home.mkdir();provider=p/'provider';provider.mkdir()
(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-peer-fixture"\nversion="0.1.0"\n')
module=provider/'amplifier_module_provider_peer_fixture';module.mkdir()
(module/'__init__.py').write_text('''from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse,TextBlock,Usage
class Provider:
    name='fixture'
    def parse_tool_calls(self,response):return []
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline peer fixture',defaults={'model':'fixture','max_tokens':4096},capabilities=['tools'])
    async def list_models(self):return [{'id':'fixture'}]
    async def complete(self,request,**kwargs):
        original='ORBIT-572' in str(request.messages) and 'agent-origin' in str(request.messages)
        return ChatResponse(content=[TextBlock(text='Verified saved peer request.' if original else 'Ordinary user reply.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):await coordinator.mount('providers',Provider(),name='fixture')
''')
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
bundle=p/'fixture.yaml';bundle.write_text('bundle:\n  name: peer-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-peer-fixture\n    source: '+str(provider)+'\n')
(home/'settings.yaml').write_text('bundle:\n  app: []\n')
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'app'),'bundle':str(bundle),'startupTimeout':90}))
`;

test('actual Host, Python scope owner and native loop deliver one attributed peer request',{skip:!python||!hostModule,timeout:60000},async()=>{
 const {createHost}=await import(hostModule),directory=await realpath(await mkdtemp(join(tmpdir(),'peer-native-delivery-'))),workspace=join(directory,'workspace');await mkdir(workspace);
 const seeded=spawnSync(python,['-I','-B','-c',setup,directory],{encoding:'utf8'});assert.equal(seeded.status,0,seeded.stderr);
 const ownerConfig=join(directory,'owner.json');await writeFile(ownerConfig,JSON.stringify({dataDir:join(directory,'authority')}));
 let host,owner,client;const callbackEvents=[];
 try{
  owner=createCoordinationCapabilities({owner:{command:python,args:['-m','amplifier_unified_coordination.server','--config',ownerConfig],env:{PYTHONPATH:process.env.COORDINATION_PYTHONPATH}},
   grants:{inspect:async session=>{const state=await host.inspectSession(session);return {sessionId:session,nativeSessionId:state.nativeSessionId,kind:'root',workspace:state.executionDirectory,locationRevision:state.nativeLocationRevision,interruptionRevision:state.interruptionRevision};},input:args=>host.readUserMessage(args.session,args.messageId),review:async()=>{throw Error('The test uses an explicit human grant');}},
   delivery:{inspect:async session=>{const task=await host.readTaskState(session);return {...await host.inspectSession(session),available:task.available,task:task.task};},submit:(session,input)=>host.submitPeer(session,input)},
   listCoordinationSessions:async()=>({items:[]}),readCoordinationSession:(session,args)=>host.readCoordinationSession(session,args),readCoordinationWorkers:async()=>{throw Error('Unexpected child read');},controlCoordinationWorker:async()=>{throw Error('Unexpected child execution');},controlCoordinationSession:async()=>{throw Error('Peer work must use guarded intake');},observeSession:(session,listener)=>host.observeSession(session,event=>{if(['chat/turnComplete','chat/turnCancelled'].includes(event.action.type))return listener();})});
  host=await createHost({stateDirectory:join(directory,'host'),allowedWorkspaceRoots:[workspace],engines:[{id:'native',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(directory,'native.json')],env:{AMPLIFIER_SESSION_STATE_HOME:join(directory,'writers')}}],capabilities:owner,nativeHostCapabilities:{version:1,features:{peerNotifications:true}},nativeHostRequest:async(context,params)=>{if(params.operation==='coordination.notifications'){try{const result=await owner.passiveNotifications(context.session,params.args);callbackEvents.push({session:context.session,args:params.args,result});return result;}catch(error){callbackEvents.push({error:String(error)});throw error;}}assert.equal(params.operation,'coordination.delivery.admit');return owner.authorizePeerDelivery(context.session,params.args);},turnSettled:event=>owner.turnSettled(event)});
  client=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();await client.initialize({clientId:'peer-human',protocolVersions:['0.9.0']});
  const source='ahp-session:/'+randomUUID(),target='ahp-session:/'+randomUUID();
  for(const channel of [source,target])await client.request('createSession',{channel,provider:'native',workingDirectories:[pathToFileURL(workspace).href]});
  const invoke=(operation,args,commandId=randomUUID(),actor={origin:'ui',clientId:'peer-human',actorId:'peer-human'})=>host.invokeCapability({channel:'ahp-root://',topic:'coordination',operation:'coordination.'+operation,version:1,commandId,args},actor);
  const grant='grant-'+randomUUID();await invoke('grant',{sessionId:source,participants:[target],purpose:'Compare the existing plans',modes:['notify','queue'],idleStart:true},grant);
  const actor={origin:'agent',session:source,actorId:'agent:'+(await host.inspectSession(source)).nativeSessionId},request='request-'+randomUUID(),args={sessionId:source,recipientSessionId:target,grantId:grant,mode:'queue',text:'Please compare ORBIT-572.'};
  const result=await invoke('send',args,request,actor),inputId=result.result.receipt.inputId;
  assert.ok(['accepted','completed'].includes(result.result.receipt.status),JSON.stringify(result));
  const finished=await host.waitForTurn(target,inputId,30000);assert.equal(finished.status,'completed',finished.detail);assert.match(finished.text,/Verified saved peer request/);
  for(let i=0;i<100&&host.diagnostics().turnSettledPending;i++)await new Promise(resolve=>setTimeout(resolve,20));
  const receipt=await invoke('result',{sessionId:source,requestId:request},randomUUID(),actor);assert.equal(receipt.result.receipt.status,'completed');assert.equal(receipt.result.qualified,false);
  await assert.rejects(invoke('send',args,request,actor),/already admitted/);assert.equal(host.store.turns(target.replace('ahp-session:','ahp-chat:')).turns.length,1);
  assert.equal(host.store.turns(source.replace('ahp-session:','ahp-chat:')).turns.length,0);
  await assert.rejects(host.readUserMessage(target,inputId),error=>error.reason==='not-user');
  await host.refreshSessionHistory(target);
  const reloaded=await host.readSessionContext(target,10);
  const savedInput=reloaded.messages.find(row=>row.inputOrigin==='peer');
  assert.equal(savedInput?.text,'Please compare ORBIT-572.');
  await assert.rejects(host.readUserMessage(target,inputId),error=>error.reason==='not-user');
  const native=spawnSync(python,['-I','-B','-c',String.raw`import json,sys
from pathlib import Path
rows=[json.loads(line) for path in Path(sys.argv[1]).rglob('transcript.jsonl') for line in path.read_text().splitlines()]
peer=[row for row in rows if row.get('metadata',{}).get('inputOrigin')=='peer']
assert len(peer)==1
assert peer[0]['metadata']['peerEnvelope']['requestId']==sys.argv[2]
assert 'ORBIT-572' in peer[0]['content']
assert peer[0]['metadata']['amplifier_public_message']['blocks']==[{'type':'text','text':'Please compare ORBIT-572.'}]
print('Canonical peer origin retained; one input')`,join(directory,'native'),inputId],{encoding:'utf8'});assert.equal(native.status,0,native.stderr);
  const passiveId='notice-'+randomUUID(),notice=await invoke('send',{...args,mode:'notify',text:'Passive ORBIT-572 notification.'},passiveId,actor);
  assert.equal(notice.result.receipt.status,'notified');assert.equal(notice.result.executionStarted,false);
  assert.equal(host.store.turns(target.replace('ahp-session:','ahp-chat:')).turns.length,1);
  const feed=await owner.read({uri:owner.manifest.topics['peer-messages'].uri,topic:'peer-messages',scope:target,clientId:'peer-human'});
  assert.equal(feed.data.peerMessages.notifications[0].text,'Passive ORBIT-572 notification.');
  const followup=randomUUID();await host.submitTurn(target,{commandId:followup,text:'Continue the original task.',clientId:'peer-human',origin:'ui'});
  assert.equal((await host.waitForTurn(target,followup,30000)).status,'completed');
  await host.refreshSessionHistory(target);
  const after=await host.readSessionContext(target,10),passiveRows=after.messages.filter(row=>row.text==='Passive ORBIT-572 notification.');
  assert.equal(passiveRows.length,1,JSON.stringify({after,callbackEvents}));assert.equal(passiveRows[0].inputOrigin,'peer');
  const savedTurn=host.store.turns(target.replace('ahp-session:','ahp-chat:')).turns.find(turn=>turn.id===followup);
  assert.equal(savedTurn.message._meta?.['amplifier.dev/history']?.recordedOnly,undefined);
  assert.ok(savedTurn.responseParts.some(part=>part._meta?.['amplifier.dev/modelCall']));
  // loop-live drains the pending input after provider:request hooks, so an
  // idle notification precedes that next human input in canonical history.
  const savedTurns=host.store.turns(target.replace('ahp-session:','ahp-chat:')).turns;
  assert.equal(savedTurns.flatMap(turn=>turn.responseParts).filter(part=>part._meta?.['amplifier.dev/history']?.peerEnvelope?.mode==='notify').length,1);
  assert.ok(after.messages.findIndex(row=>row.text==='Passive ORBIT-572 notification.')<after.messages.findIndex(row=>row.id===followup));
  await host.refreshSessionHistory(target);
  assert.equal((await host.readSessionContext(target,10)).messages.filter(row=>row.text==='Passive ORBIT-572 notification.').length,1);
  const received=await invoke('result',{sessionId:source,requestId:passiveId},randomUUID(),actor);
  assert.equal(received.result.receipt.contextDelivered,true);
  assert.equal(host.store.turns(target.replace('ahp-session:','ahp-chat:')).turns.length,2);

 }finally{await client?.shutdown();await host?.close();await owner?.close();await rm(directory,{recursive:true,force:true});}
});
