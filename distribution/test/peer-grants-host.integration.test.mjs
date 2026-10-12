import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {createCoordinationCapabilities} from '@amplifier/unified-coordination-capability';
const python=process.env.COORDINATION_PYTHON,hostModule=process.env.COORDINATION_HOST_MODULE,fixture=process.env.COORDINATION_ACP_FIXTURE;
const wait=async fn=>{for(let i=0;i<500;i++){if(fn())return;await new Promise(r=>setTimeout(r,10))}throw Error('Approval did not arrive')};

test('real host input and two-way Python owner bind a peer grant to one human approval',{skip:!python||!hostModule||!fixture,timeout:30000},async()=>{
 const {createHost}=await import(hostModule),{AhpClient}=await import('@microsoft/agent-host-protocol/client'),{WebSocketTransport}=await import('@microsoft/agent-host-protocol/ws');
 const dir=await mkdtemp(join(tmpdir(),'peer-grant-host-')),config=join(dir,'coordination.json');await writeFile(config,JSON.stringify({dataDir:join(dir,'authority')}));
 let host,owner,client;
 try{
  owner=createCoordinationCapabilities({owner:{command:python,args:['-m','amplifier_unified_coordination.server','--config',config],env:process.env.COORDINATION_PYTHONPATH?{PYTHONPATH:process.env.COORDINATION_PYTHONPATH}:{}},
   grants:{inspect:async session=>{const state=await host.inspectSession(session);return {sessionId:session,nativeSessionId:state.nativeSessionId,kind:'root',workspace:state.executionDirectory,locationRevision:state.nativeLocationRevision,interruptionRevision:state.interruptionRevision}},
    input:args=>args.active?host.readActiveUserMessage(args.session,args.messageId,args.actorId):host.readUserMessage(args.session,args.messageId),
    review:async args=>{await host.confirmCapability(args.session,{operation:'coordination.grant',title:'Allow these chats to collaborate?',args});return {decision:'allow'}}},
   listCoordinationSessions:async()=>({items:[]}),readCoordinationSession:(session,args)=>host.readCoordinationSession(session,args),
   readCoordinationWorkers:async()=>{throw Error('No worker read requested')},controlCoordinationWorker:async()=>{throw Error('A grant never starts work')},controlCoordinationSession:async()=>{throw Error('A grant never starts work')},observeSession:async()=>()=>{}});
  host=await createHost({stateDirectory:join(dir,'host'),allowedWorkspaceRoots:[dir],engines:[{id:'fixture',command:process.execPath,args:[fixture]}],capabilities:owner});
  client=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();await client.initialize({clientId:'grant-human',protocolVersions:['0.9.0']});
  const source='ahp-session:/'+randomUUID(),peer='ahp-session:/'+randomUUID(),chat=source.replace('ahp-session:','ahp-chat:');
  for(const channel of [source,peer])await client.request('createSession',{channel,provider:'fixture',workingDirectories:[pathToFileURL(dir).href]});
  await client.subscribe(chat,{view:{turns:3}});
  const input=randomUUID();client.dispatch(chat,{type:'chat/turnStarted',turnId:input,startedAt:new Date().toISOString(),message:{text:'approval',origin:{kind:'user'}}});
  await wait(()=>host.store.get(source)?.chat.activeTurn?.responseParts.some(p=>p.toolCall?.toolCallId==='tool-1'));
  const actor={actorId:'agent:'+host.store.get(source).nativeSessionId,origin:'agent',session:source};
  const invoke=(operation,args,commandId=randomUUID(),identity=actor)=>host.invokeCapability({channel:'ahp-root://',topic:'coordination',operation:'coordination.'+operation,version:1,commandId,args},identity);
  const proposal='grant-'+randomUUID(),pending=invoke('grant',{sessionId:source,sourceMessageId:input,participants:[peer],purpose:'Read and compare the saved plans',modes:['notify']},proposal);
  const review=()=>host.store.get(source)?.chat.activeTurn?.responseParts.find(p=>p.toolCall?.toolCallId.startsWith('capability-approval:'))?.toolCall;
  await wait(review);assert.equal(review().status,'pending-confirmation');const displayed=JSON.parse(review().toolInput);
  assert.equal(displayed.args.proposalId,proposal);assert.equal(displayed.args.sourceText,'approval');assert.deepEqual(displayed.args.scope.participants,[source,peer]);
  const before=await invoke('context',{sessionId:source});assert.equal(before.result.grants.length,0);assert.equal(before.result.proposals[0].status,'pending');
  await assert.rejects(invoke('decide',{sessionId:source,proposalId:proposal,decision:'allow'}),/Only a human/);
  client.dispatch(chat,{type:'chat/toolCallConfirmed',turnId:input,toolCallId:review().toolCallId,approved:true,confirmed:'user-action',selectedOptionId:'approve'});
  const accepted=await pending;assert.equal(accepted.result.receipt.status,'approved');assert.equal(accepted.result.receipt.executionStarted,false);
  assert.equal(host.store.get(peer).chat.activeTurn,undefined);assert.equal(host.store.turns(peer.replace('ahp-session:','ahp-chat:')).turns.length,0);
  assert.equal((await invoke('context',{sessionId:source})).result.grants.length,1);
  await assert.rejects(invoke('context',{sessionId:source},randomUUID(),{...actor,actorId:'agent:child'}),/borrow/);
  client.dispatch(chat,{type:'chat/toolCallConfirmed',turnId:input,toolCallId:'tool-1',approved:false,confirmed:'user-action',selectedOptionId:'deny'});
  await wait(()=>!host.store.get(source)?.chat.activeTurn);
  const sourceMessage=await host.readUserMessage(source,input);assert.equal(sourceMessage.sourceDigest,accepted.result.receipt.result.sourceDigest,'Terminal receipt updates do not change the frozen human source');
 }finally{await client?.shutdown();await host?.close();await owner?.close();await rm(dir,{recursive:true,force:true});}
});
