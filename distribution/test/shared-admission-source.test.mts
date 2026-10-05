import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {AhpClient} from '@microsoft/agent-host-protocol/client';
import {WebSocketTransport} from '@microsoft/agent-host-protocol/ws';
import {composeNaming} from '../src/naming.js';
import {CoordinationCapabilities} from '../../components/coordination-capability/src/index.js';
const source=process.env.SHARED_HOST_SOURCE;
const wait=async(check:()=>unknown)=>{for(let n=0;n<1000;n++){if(check())return;await new Promise(r=>setTimeout(r,5));}throw Error('Fixture deadline');};
test('real admitted Native naming completion survives closed Host gate and is joined before held proof',{skip:!source},async()=>{
 const {createHost}=await import(pathToFileURL(resolve(source!,'src/host.ts')).href),dir=await mkdtemp(join(tmpdir(),'scoped-naming-drain-'));let host:any,c:AhpClient|undefined;let canonical={title:'Initial',naming:{automatic:true,nameRevision:0,policyRevision:0}};
 const identity={installationId:'fixture',dataScope:'data',ownerId:'unit',instanceId:'A',releaseDigest:'digest-A'};
 const naming=await composeNaming({admin:{namingCapabilities:async()=>({version:1,method:'_amplifier/naming',passiveMetadata:true,commandIds:true,replayUnknown:false}),performNaming:async()=>structuredClone(canonical)},engineId:'fixture',host:()=>host,inspectSession:uri=>host.inspectSession(uri),hostPortsSupported:true});
 try{host=await createHost({stateDirectory:join(dir,'host'),allowedWorkspaceRoots:[dir],engines:[{id:'fixture',command:process.execPath,args:[resolve(source!,'fixtures/acp-peer.mjs')],env:{FIXTURE_CLIENT_TOOL_NAMING:'1'},sessionMetadata:naming.sessionMetadata}],nativeEvent:(context:any,params:any)=>{naming.event(context,params);return Promise.resolve();},nativeHostRequest:(context:any)=>host.invokeClientTool(context.session,'owner','inspect',{}),quiescence:{instanceId:'A',dataScope:'data',requiredOwners:[],participants:[],coverage:{},timeoutMs:1000,verifyRelease:async()=>{throw Error('not used');},serviceLifecycle:{identity,verifyRelease:async()=>{throw Error('not used');}}}});
  c=new AhpClient(await WebSocketTransport.connect(host.url));c.connect();await c.initialize({clientId:'owner',protocolVersions:['0.9.0']});const uri='ahp-session:/named',chat='ahp-chat:/named';await c.request('createSession',{channel:uri,provider:'fixture',workingDirectories:[pathToFileURL(dir).href]});await c.subscribe(uri);
  c.dispatch(uri,{type:'session/activeClientSet',activeClient:{clientId:'owner',tools:[{name:'inspect',description:'Explicit client tool'}]}} as any);await wait(()=>host.store.get(uri).state.activeClients.length===1);
  await host.submitTurn(uri,{commandId:'accepted',text:'client-tool',clientId:'owner'});await wait(()=>host.clientTools.size===1);const [tool]=host.clientTools.keys();
  const closed=await host.closeServiceIntake({commandId:'stop',expected:identity}),pending=host.admitServiceStop({commandId:'stop',expected:identity});canonical={title:'Canonical accepted title',naming:{automatic:true,nameRevision:1,policyRevision:0}};
  c.dispatch(chat,{type:'chat/toolCallComplete',turnId:'accepted',toolCallId:tool,result:{success:true,pastTenseMessage:'Finished'}} as any);
  const held=await pending;assert.equal(held.fenceId,closed.fenceId);assert.equal(held.evidence.activeWork,0);assert.equal(naming.diagnostics().pending,0);assert.equal(naming.diagnostics().failures,0);assert.equal(host.store.get(uri).state.title,'Canonical accepted title');
 }finally{await c?.shutdown();await naming.close();await host?.close();await rm(dir,{recursive:true,force:true});}
});
test('coordination close releases watches, joins existing derived refresh and closes its owner once',async()=>{
 let release!:()=>void,entered!:()=>void,ownerClosed=0,unwatched=0;const pending=new Promise<void>(r=>release=r),started=new Promise<void>(r=>entered=r);
 const cap:any=new CoordinationCapabilities({owner:{command:'/must-not-start'},listCoordinationSessions:async()=>({}),readCoordinationSession:async()=>({}),readCoordinationWorkers:async()=>({}),controlCoordinationWorker:async()=>({}),controlCoordinationSession:async()=>({}),observeSession:async()=>()=>{unwatched++;}});
 cap.owner={request:async(method:string)=>{assert.equal(method,'changed');entered();await pending;return {};},close:async()=>{ownerClosed++;}};
 await cap.callback('watch',{token:'watch',sessions:['ahp-session:/owned']});cap.changed('ahp-session:/owned');await started;let done=false;const closing=cap.close().then(()=>done=true);await new Promise(r=>setImmediate(r));assert.equal(unwatched,1);assert.equal(done,false);assert.equal(ownerClosed,0);
 cap.changed('ahp-session:/owned');release();await closing;await cap.close();assert.equal(ownerClosed,1);
});
