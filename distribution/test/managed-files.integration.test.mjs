import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,realpath,rm,stat} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID,createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {once} from 'node:events';
import {WebSocket} from 'ws';
const {createDistribution}=await import(process.env.UNIFIED_DISTRIBUTION_ENTRY??'../src/index.js');
import {DistributionUpdateOwner,createManualIngressGate} from '@amplifier/unified-distribution-update-owner';
import {createCountedIngress} from './counted-ingress-fixture.mjs';

const python=process.env.AMPLIFIER_ACP_PYTHON,owners=process.env.UNIFIED_OWNERS_PYTHON,
 provider=process.env.RECOVERY_NATIVE_PROVIDER,catalogPython=process.env.UNIFIED_CATALOG_PYTHON;
const withIngress=process.env.MANAGED_FILES_INGRESS==='1';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function hashes(root){const result={};async function visit(path,prefix=''){for(const item of await readdir(path,{withFileTypes:true})){if(item.isDirectory())await visit(join(path,item.name),prefix+item.name+'/');else if(item.isFile())result[prefix+item.name]=sha(await readFile(join(path,item.name)));}}await visit(root);return result;}
async function peer(app,clientId='disposal-reviewer'){
 const socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');let next=0;const pending=new Map();
 socket.on('message',raw=>{const row=JSON.parse(raw),p=pending.get(row.id);if(!p)return;pending.delete(row.id);clearTimeout(p.timer);
  // Lose only the transport acknowledgement AFTER the unmodified server has
  // replied. No owner, native operation or receipt function is substituted.
  if(p.lose){socket.terminate();p.reject(Object.assign(Error('Lost disposal acknowledgement'),{outcome:'unknown'}));return;}
  row.error?p.reject(Object.assign(Error(p.method+' '+(p.operation??'')+': '+row.error.message),{code:row.error.code,data:row.error.data})):p.resolve(row.result);
 });
 socket.on('close',()=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('Connection closed; inspect the original receipt'));}pending.clear();});
 const request=(method,params,lose=false)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Timed out: '+method));},45000);pending.set(id,{resolve,reject,timer,method,operation:params.operation,lose});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
 await request('initialize',{channel:'ahp-root://',clientId,protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
 return {request,close:()=>socket.terminate(),action:async(topic,operation,args={},channel='ahp-root://',commandId=randomUUID(),lose=false)=>(await request('x-amplifier/capabilityAction',{channel,topic,operation,version:1,args,commandId},lose)).result};
}

test('installed '+(withIngress?'twenty':'nineteen')+'-owner public disposal protects retained references/future work/viewers and preserves canonical history',
 {skip:!python||!owners||!provider||!catalogPython,timeout:180000},async()=>{
 const directory=await realpath(await mkdtemp(join(tmpdir(),'nineteen-owner-disposal-'))),workspace=join(directory,'workspace'),managed=join(workspace,'.managed'),web=join(directory,'web'),home=join(directory,'home'),appHome=join(directory,'native-app'),state=join(directory,'state');
 let app,client,viewer,updateOwner,ingress,access;let idleSignals=0;
 try{
  for(const path of [workspace,web,home])await mkdir(path);await writeFile(join(web,'index.html'),'<!doctype html><title>Owned disposal acceptance</title>');await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');
  const context=execFileSync(python,['-I','-c','import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec("amplifier_module_context_simple").origin).parent)'],{encoding:'utf8'}).trim();
  const bundle=join(directory,'bundle.yaml');await writeFile(bundle,`bundle:\n  name: nineteen-owner-disposal\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: ${context}\nproviders:\n  - module: provider-fixture\n    source: ${provider}\n`);
  const config=join(directory,'native.json'),native={home,appHome,bundle,managedSessionRoots:[managed],adminWorkspaceRoots:[workspace],adminMaintenance:true,transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[workspace],maintenanceExternalWriters:'foundation-cooperative'};await writeFile(config,JSON.stringify(native));
  const common={account:'disposal-fixture',webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],stateDirectory:state,host:{managedSessionRoot:managed},engines:[{id:'amplifier',command:python,args:['-I','-m','amplifier_acp','--config',config],env:{AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,AMPLIFIER_SESSION_STATE_HOME:join(directory,'writers')}}]};
  // Prepare genuine canonical history before loading the complete owner graph.
  app=await createDistribution(common);client=await peer(app);const records=[];
  for(const name of ['retained-file','future-work','disposable']){
   const session='ahp-session:/'+randomUUID();await client.request('x-amplifier/managedSession',{channel:'ahp-root://',operation:'create',args:{session,commandId:'create-'+name,title:name}});
   await app.host.submitTurn(session,{commandId:'input-'+name,text:'Preserve canonical '+name,clientId:'disposal-reviewer'});assert.equal((await app.host.waitForTurn(session,'input-'+name,30000)).status,'completed');
   const info=await app.host.inspectSession(session),cwd=info.executionDirectory,nativeDirectory=join(home,'projects',cwd.replaceAll('/','-'),'sessions',info.nativeSessionId);
   await writeFile(join(cwd,'result.txt'),'Private generated '+name);await writeFile(join(nativeDirectory,'events.jsonl'),'Retained canonical event '+name+'\n');records.push({session,cwd,nativeDirectory,nativeId:info.nativeSessionId});
  }
  const other='ahp-session:/'+randomUUID();await client.request('createSession',{channel:other,provider:'amplifier',workingDirectories:[pathToFileURL(workspace).href]});
  client.close();client=null;await app.close();app=null;
  const [referenced,scheduled,disposable]=records;
  // Two actual passive Foundation histories make family order observable. The
  // catalog, not this test or the host, derives their AHP identities.
  const children=[randomUUID(),randomUUID()];for(const id of children)execFileSync(python,['-I','-c',"import sys;from pathlib import Path;from amplifier_foundation.session.history import SessionHistoryStore;SessionHistoryStore(Path(sys.argv[1]),session_id=sys.argv[2]).save([{'role':'user','content':'Retained child canonical history'}],{'session_id':sys.argv[2],'parent_id':sys.argv[3],'status':'idle','working_dir':sys.argv[4]})",join(dirname(disposable.nativeDirectory),id),id,disposable.nativeId,disposable.cwd]);
  const expectedChildren=JSON.parse(execFileSync(catalogPython,['-I','-c',"import json,sys;from amplifier_session_catalog.discovery import session_uri;print(json.dumps([session_uri(sys.argv[1],sys.argv[2],identity) for identity in sys.argv[3:]]))",home,disposable.cwd.replaceAll('/','-'),...children],{encoding:'utf8'}));
  const before=await hashes(join(home,'projects'));
  // Every later native peer can initialize and inspect but cannot start a worker.
  await writeFile(config,JSON.stringify({...native,workerCommand:['/impossible-disposal-worker']}));
  const noUpdate=async()=>{throw Error('Disposal must not invoke update release/lifecycle ports')};
  updateOwner=new DistributionUpdateOwner({directory:join(directory,'supervisor'),dataScope:'disposal-owned',preferences:{autoCheck:false,autoInstall:false,intervalMs:1000},releases:{check:noUpdate,prepare:noUpdate,verify:noUpdate},lifecycle:{inspect:noUpdate,admitRestart:noUpdate,restart:noUpdate}});
  const completeConfig={...common,nativeAdmin:{engine:'amplifier'},maintenance:{},recovery:{authorization:'local-account'},historyImport:{},historyCleanup:true,managedFiles:true,applicationUpdates:true,portability:{python:owners,engines:['amplifier'],stageDir:join(workspace,'stages'),exchangeDir:join(workspace,'exchange')},quiescence:{instanceId:'disposal-original',dataScope:'disposal-owned',timeoutMs:30000},...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(key=>[key,{python:owners}])),catalogProcess:{command:catalogPython,args:['-I','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--workspace',workspace,'--scan-on-start','--scan-interval','0','--workspace-check-interval','0']}};
  const completePorts={applicationUpdateSupervisor:{outlivesDistribution:true,owner:updateOwner,subscribe:()=>()=>{}},authorizeRecovery:async trusted=>{assert.equal(trusted.account,'disposal-fixture');return {accountId:'disposal-fixture'}}};
  if(withIngress){
   completeConfig.manualIngress={stateDirectory:join(directory,'manual-ingress')};
   ingress=await createManualIngressGate({directory:completeConfig.manualIngress.stateDirectory,id:'manual-preview-ingress',onMayBeIdle:()=>idleSignals++});
   access=await createCountedIngress(ingress);
   const entry=process.env.UNIFIED_DISTRIBUTION_ENTRY?pathToFileURL(process.env.UNIFIED_DISTRIBUTION_ENTRY):new URL('../src/index.js',import.meta.url);
   const components=JSON.parse(await readFile(new URL('../components.json',entry),'utf8')).components;
   const component=components['@amplifier/unified-distribution-update-owner'];assert.equal(component.version,'0.16.1');
   completePorts.runtimeOwnerBindings=[{owner:ingress.participant,storage:{packageName:'@amplifier/unified-distribution-update-owner',packageVersion:component.version,revision:component.revision,configKey:'manualIngress',rootRole:'service-ingress',stateDirectory:completeConfig.manualIngress.stateDirectory}}];
  }
  app=await createDistribution(completeConfig,completePorts);
  const connect=async(id)=>{if(access){access.forward(app.url);return peer(access,id);}return peer(app,id);};
  client=await connect();assert.equal(app.quiescence.requiredOwners.length,withIngress?20:19);
  if(ingress){
   assert.equal(ingress.inspect().active,1);
   assert.ok(app.quiescence.requiredOwners.includes(ingress.participant.id));
   const inventory=await app.storageInventory({externalCoverage:{[ingress.participant.id]:'declared'},externalRoots:[{id:'ingress',ownerIds:[ingress.participant.id],path:completeConfig.manualIngress.stateDirectory,coverage:'authoritative',capture:'tree'}]});
   assert.equal(inventory.omissions.some(row=>row.id.includes(ingress.participant.id)),false);
   assert.equal((await fetch(access.url+'/health')).status,200);
   const busy=await app.host.admitQuiescence({commandId:'network-busy-update',purpose:'distribution-update'});
   assert.equal(busy.admitted,false,JSON.stringify(busy));assert.equal(busy.executed,false);assert.equal(busy.intakeClosed,false);
   assert.deepEqual(ingress.inspect().held,null);assert.equal((await fetch(access.url+'/health')).status,200);
  }
  // Explicit metadata-only preview waits for startup index coverage, never scans
  // transcripts or warms agents. Both product facades are genuinely composed.
  let cleanup;for(let attempt=0;attempt<150;attempt++){cleanup=await client.action('history-cleanup','cleanup.preview',{modifiedBefore:Math.floor(Date.now()/1000)-1,limit:50});if(cleanup.candidateCount>=3)break;await pause(20);}
  assert.ok(cleanup.candidateCount>=3,JSON.stringify(cleanup));assert.equal(cleanup.nativeFilesRead,false);assert.equal(app.host.diagnostics().activeAgents,0);
  // This ordinary conversation is explicitly authorized to its parent workspace.
  // Its retained artifact points into the managed allocation through the public
  // Canvas API, not a private resource database or substituted authority callback.
  const artifact=await client.action('canvas','canvas.openFile',{path:join(referenced.cwd,'result.txt')},other);assert.ok(artifact.artifact.id);
  const referencedReview=await client.action('managed-files','managedFiles.preview',{sessionId:referenced.session});
  const input=review=>({sessionId:review.session,reviewId:review.reviewId,reviewHash:review.reviewHash,expectedHistoryRevision:review.historyRevision});
  await assert.rejects(client.action('managed-files','managedFiles.dispose',input(referencedReview),'ahp-root://','refused-reference'),error=>error.data?.executed===false);
  const refused=await client.action('managed-files','managedFiles.receipt',{sessionId:referenced.session,commandId:'refused-reference'});assert.equal(refused.receipt.status,'refused');assert.equal(refused.receipt.executed,false);assert.ok(refused.protection.owners.some(owner=>owner.references?.protected?.some(row=>row.reasons.includes('artifact-file-source'))),JSON.stringify(refused));assert.equal(await readFile(join(referenced.cwd,'result.txt'),'utf8'),'Private generated retained-file');
  await assert.rejects(client.action('managed-files','managedFiles.dispose',input(referencedReview),'ahp-root://','refused-reference'),/already admitted/);
  assert.deepEqual((await client.action('managed-files','managedFiles.receipt',{sessionId:referenced.session,commandId:'refused-reference'})).receipt,refused.receipt);
  const scheduleArgs={prompt:'Retain future work',kind:'monitor',destination:'new_task',spec:{kind:'interval',timezone:'UTC',startAt:new Date(Date.now()+86400000).toISOString(),intervalSeconds:86400},notificationPolicy:'changes',missedRunPolicy:'latest'};
  const scheduleReview=await client.action('schedules','schedule.preview',scheduleArgs,scheduled.session);await client.action('schedules','schedule.create',{...scheduleArgs,expectedRevision:0,previewHash:scheduleReview.previewHash},scheduled.session);
  const futureReview=await client.action('managed-files','managedFiles.preview',{sessionId:scheduled.session});await assert.rejects(client.action('managed-files','managedFiles.dispose',input(futureReview),'ahp-root://','refused-future'),error=>error.data?.executed===false);
  const future=await client.action('managed-files','managedFiles.receipt',{sessionId:scheduled.session,commandId:'refused-future'});assert.equal(future.receipt.status,'refused');assert.ok((await stat(scheduled.cwd)).isDirectory());
  viewer=await connect('another-selected-client');await viewer.request('subscribe',{channel:disposable.session});
  await assert.rejects(client.action('managed-files','managedFiles.preview',{sessionId:disposable.session}),/selected client/);await viewer.request('unsubscribe',{channel:disposable.session});viewer.close();viewer=null;
  const review=await client.action('managed-files','managedFiles.preview',{sessionId:disposable.session});assert.equal(review.preservesCanonical,true);assert.equal(review.manifestComplete,true);
  assert.equal(app.host.diagnostics().activeAgents,0);
  await assert.rejects(client.action('managed-files','managedFiles.dispose',input(review),'ahp-root://','lost-dispose-ack',true),error=>error.outcome==='unknown');client=null;
  client=await connect();const recovered=await client.action('managed-files','managedFiles.receipt',{sessionId:disposable.session,commandId:'lost-dispose-ack'});
  assert.equal(recovered.receipt.status,'completed',JSON.stringify(recovered));assert.equal(recovered.receipt.protectionRelease,'confirmed');assert.equal(recovered.protection.state,'released');assert.equal(recovered.receipt.native.directoryRemoved,true);assert.equal(recovered.receipt.native.canonicalFilesDeleted,0);assert.equal(recovered.receipt.native.eventsDeleted,0);
  assert.equal(recovered.receipt.familyCount,2);assert.deepEqual(recovered.receipt.descendants,expectedChildren.sort((a,b)=>a.localeCompare(b)));assert.ok(recovered.protection.owners.every(owner=>owner.state==='released'));assert.ok(recovered.protection.owners.length>=16);
  if(ingress){assert.ok(recovered.protection.owners.some(owner=>owner.id===ingress.participant.id||owner.ownerId===ingress.participant.id),JSON.stringify(recovered.protection));assert.equal(ingress.inspect().held,null);assert.ok(ingress.inspect().active>=1);}
  await assert.rejects(stat(dirname(disposable.cwd)),error=>error.code==='ENOENT');assert.deepEqual(await hashes(join(home,'projects')),before);
  const exact=await client.action('managed-files','managedFiles.reconcile',{sessionId:disposable.session,commandId:'lost-dispose-ack'});assert.deepEqual(exact.receipt,recovered.receipt);await assert.rejects(client.action('managed-files','managedFiles.dispose',input(review),'ahp-root://','lost-dispose-ack'),/already admitted/);
  client.close();client=null;await app.close();app=null;
  if(access){await access.close();access=null;ingress.close();ingress=await createManualIngressGate({directory:completeConfig.manualIngress.stateDirectory,id:'manual-preview-ingress',onMayBeIdle:()=>idleSignals++});completePorts.runtimeOwnerBindings[0].owner=ingress.participant;access=await createCountedIngress(ingress);}
  app=await createDistribution({...completeConfig,quiescence:{...completeConfig.quiescence,instanceId:'disposal-reopened'}},completePorts);client=await connect();
  const restarted=await client.action('managed-files','managedFiles.receipt',{sessionId:disposable.session,commandId:'lost-dispose-ack'});
  assert.deepEqual(restarted.receipt,recovered.receipt);assert.equal(restarted.protection.state,'released');assert.equal(app.host.diagnostics().activeAgents,0);
  await assert.rejects(app.host.submitTurn(disposable.session,{commandId:'no-reactivation',text:'Do not execute',clientId:'disposal-reviewer'}),/disposal|removed|execution/i);
  const history=await client.request('x-amplifier/sessionLifecycle',{channel:disposable.session,operation:'inspect',args:{}});assert.equal(history.historyRevision,review.historyRevision);
  await client.request('x-amplifier/sessionLifecycle',{channel:disposable.session,operation:'history.refresh',args:{limit:5,expectedHistoryRevision:review.historyRevision}});
  const chat=await client.request('subscribe',{channel:disposable.session.replace('ahp-session:','ahp-chat:'),view:{turns:5}});assert.ok(chat.snapshot.state.turns.some(turn=>turn.message.text==='Preserve canonical disposable'));
  const exported=await client.request('x-amplifier/sessionLifecycle',{channel:disposable.session,operation:'export',args:{commandId:'historical-export',expectedHistoryRevision:review.historyRevision,format:'markdown'}});
  const body=await client.request('resourceRead',{channel:disposable.session,uri:exported.resource,encoding:'base64'});assert.match(Buffer.from(body.data,'base64').toString(),/Preserve canonical disposable/);
  assert.deepEqual(await hashes(join(home,'projects')),before);assert.equal(app.host.diagnostics().activeAgents,0);
  // Hiding the retained-file conversation is deliberately a different authority:
  // it keeps the allocation and cross-chat immutable resource reference intact.
  const hideReview=await client.action('history-cleanup','cleanup.preview',{modifiedBefore:Math.floor(Date.now()/1000)-1,limit:50});
  assert.ok(hideReview.items.some(row=>row.session===referenced.session));
  const hidden=await client.action('history-cleanup','cleanup.apply',{reviewId:hideReview.reviewId,reviewHash:hideReview.reviewHash,sessionIds:[referenced.session]},'ahp-root://','hide-preserves-files');
  assert.equal(hidden.items[0].status,'hidden',JSON.stringify(hidden));assert.ok((await stat(referenced.cwd)).isDirectory());
  const retained=await client.action('canvas','canvas.versions.inspect',{id:artifact.artifact.id,includeSource:true},other);assert.equal(retained.body.content,'Private generated retained-file');
  assert.deepEqual(await hashes(join(home,'projects')),before);assert.equal(app.host.diagnostics().activeAgents,0);
  if(ingress){
   client.close();client=null;await access.drain();assert.ok(idleSignals>0);
   const held=await app.host.admitQuiescence({commandId:'network-busy-update',purpose:'distribution-update',retryRefused:true});
   assert.equal(held.admitted,true,JSON.stringify(held));assert.equal(held.evidence.intakeClosed,true);assert.equal(ingress.inspect().held.commandId,'network-busy-update');
   assert.equal((await fetch(access.url+'/health')).status,503);
   // No invented release proof: an admitted process-replacement fence remains
   // held across host/gate closure until the real supervisor settles it.
   await app.close();app=null;await access.close();access=null;ingress.close();
   ingress=await createManualIngressGate({directory:completeConfig.manualIngress.stateDirectory,id:'manual-preview-ingress'});
   assert.equal(ingress.inspect().held.commandId,'network-busy-update');assert.equal(ingress.enter(),null);
  }
 }finally{client?.close();viewer?.close();await app?.close();await access?.close();ingress?.close();await updateOwner?.close();if(process.env.KEEP_MANAGED_FILES_FIXTURE)console.log('Retained owned fixture:',directory);else await rm(directory,{recursive:true,force:true});}
});
