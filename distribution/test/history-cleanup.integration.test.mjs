import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,utimes,realpath,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID,createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {once} from 'node:events';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
import {DistributionUpdateOwner} from '@amplifier/unified-distribution-update-owner';
const python=process.env.AMPLIFIER_ACP_PYTHON,owners=process.env.UNIFIED_OWNERS_PYTHON,provider=process.env.RECOVERY_NATIVE_PROVIDER,catalogPython=process.env.UNIFIED_CATALOG_PYTHON??owners;
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
async function hashes(root){const result={};async function visit(path,prefix=''){for(const item of await readdir(path,{withFileTypes:true})){if(item.isDirectory())await visit(join(path,item.name),prefix+item.name+'/');else if(item.isFile())result[prefix+item.name]=sha(await readFile(join(path,item.name)));}}await visit(root);return result;}
async function peer(app){const socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');let next=0;const pending=new Map();socket.on('message',raw=>{const row=JSON.parse(raw),p=pending.get(row.id);if(p){pending.delete(row.id);clearTimeout(p.timer);row.error?p.reject(Object.assign(Error(p.method+' '+(p.operation??'')+': '+row.error.message),{code:row.error.code,data:row.error.data})):p.resolve(row.result);}});const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Timed out: '+method));},30000);pending.set(id,{resolve,reject,timer,method,operation:params.operation});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});await request('initialize',{channel:'ahp-root://',clientId:'cleanup-reviewer',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});return {request,close:()=>socket.terminate(),action:async(topic,operation,args={},channel='ahp-root://',commandId=randomUUID())=>(await request('x-amplifier/capabilityAction',{channel,topic,operation,version:1,args,commandId})).result};}

test('installed all-owner cleanup protects future work, hides only reviewed roots and preserves native history bytes',
 {skip:!python||!owners||!provider,timeout:120000},async()=>{
 const directory=await realpath(await mkdtemp(join(tmpdir(),'all-owner-cleanup-'))),workspace=join(directory,'workspace'),web=join(directory,'web'),home=join(directory,'home'),appHome=join(directory,'native-app'),state=join(directory,'state');
 let app,client,updateOwner;
 try{
  for(const p of [workspace,web,home])await mkdir(p);await writeFile(join(web,'index.html'),'<!doctype html><title>Owned cleanup fixture</title>');await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');
  const context=execFileSync(python,['-I','-c','import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec("amplifier_module_context_simple").origin).parent)'],{encoding:'utf8'}).trim();
  const bundle=join(directory,'bundle.yaml');await writeFile(bundle,`bundle:\n  name: all-owner-cleanup\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: ${context}\nproviders:\n  - module: provider-fixture\n    source: ${provider}\n`);
  const config=join(directory,'native.json');await writeFile(config,JSON.stringify({home,appHome,bundle,adminWorkspaceRoots:[workspace],adminMaintenance:true,transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[workspace],maintenanceExternalWriters:'foundation-cooperative'}));
  const common={account:'cleanup-fixture',webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],engines:[{id:'amplifier',command:python,args:['-I','-m','amplifier_acp','--config',config],env:{AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,AMPLIFIER_SESSION_STATE_HOME:join(directory,'writer-state')}}]};
  app=await createDistribution({...common,stateDirectory:join(directory,'creator')});client=await peer(app);const nativeDirectories=[];
  for(let i=0;i<2;i++){const session='ahp-session:/'+randomUUID();await client.request('createSession',{channel:session,provider:'amplifier',workingDirectories:[pathToFileURL(workspace).href]});const commandId=randomUUID();await app.host.submitTurn(session,{commandId,text:'Preserve owned historical fixture '+i,clientId:'cleanup-reviewer'});assert.equal((await app.host.waitForTurn(session,commandId,30000)).status,'completed');const row=await app.host.inspectSession(session);nativeDirectories.push(join(home,'projects',workspace.replaceAll('/','-'),'sessions',row.nativeSessionId));}
  client.close();client=null;await app.close();app=null;
  const cutoff=Math.floor(Date.now()/1000)-86400,aged=cutoff-86400;
  for(const native of nativeDirectories){const path=join(native,'metadata.json'),metadata=JSON.parse(await readFile(path,'utf8'));for(const key of ['created','created_at','updated_at','modified_at'])if(key in metadata)metadata[key]=aged;await writeFile(path,JSON.stringify(metadata));for(const name of ['metadata.json','transcript.jsonl','events.jsonl'])try{await utimes(join(native,name),aged,aged);}catch(error){if(error.code!=='ENOENT')throw error;}}
  const before=await hashes(join(home,'projects'));
  const noUpdate=async()=>{throw Error('Cleanup must not invoke distribution lifecycle or release ports')};
  updateOwner=new DistributionUpdateOwner({directory:join(directory,'supervisor'),dataScope:'cleanup-owned',preferences:{autoCheck:false,autoInstall:false,intervalMs:1000},releases:{check:noUpdate,prepare:noUpdate,verify:noUpdate},lifecycle:{inspect:noUpdate,admitRestart:noUpdate,restart:noUpdate}});
  app=await createDistribution({...common,stateDirectory:state,nativeAdmin:{engine:'amplifier'},maintenance:{},recovery:{authorization:'local-account'},historyImport:{},historyCleanup:true,applicationUpdates:true,portability:{python:owners,engines:['amplifier'],stageDir:join(workspace,'stages'),exchangeDir:join(workspace,'exchange')},quiescence:{instanceId:'cleanup-original',dataScope:'cleanup-owned',timeoutMs:30000},...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(key=>[key,{python:owners}])),catalogProcess:{command:catalogPython,args:['-I','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--workspace',workspace,'--scan-on-start','--scan-interval','0','--workspace-check-interval','0']}},{applicationUpdateSupervisor:{outlivesDistribution:true,owner:updateOwner,subscribe:()=>()=>{}},authorizeRecovery:async context=>{assert.equal(context.account,'cleanup-fixture');return {accountId:'cleanup-fixture'}}});
  client=await peer(app);let review;
  for(let attempt=0;attempt<150;attempt++){review=await client.action('history-cleanup','cleanup.preview',{modifiedBefore:cutoff,limit:50});if(review.candidateCount===2)break;await new Promise(resolve=>setTimeout(resolve,20));}
  assert.equal(review.candidateCount,2,JSON.stringify(review));assert.equal(review.nativeFilesRead,false);assert.equal(app.host.diagnostics().activeAgents,0);assert.equal(app.host.store.count,0);
  assert.equal(app.quiescence.requiredOwners.length,18);const [free,protectedSession]=review.items.map(item=>item.session);
  const scheduleArgs={prompt:'Retain this future work',kind:'monitor',destination:'new_task',spec:{kind:'interval',timezone:'UTC',startAt:new Date(Date.now()+86400000).toISOString(),intervalSeconds:86400},notificationPolicy:'changes',missedRunPolicy:'latest'};
  const scheduleReview=await client.action('schedules','schedule.preview',scheduleArgs,protectedSession),scheduled=await client.action('schedules','schedule.create',{...scheduleArgs,expectedRevision:0,previewHash:scheduleReview.previewHash},protectedSession);
  const fresh=await client.action('history-cleanup','cleanup.preview',{modifiedBefore:cutoff,limit:50});assert.equal(fresh.candidateCount,2);assert.equal(app.host.diagnostics().activeAgents,0);
  const input={reviewId:fresh.reviewId,reviewHash:fresh.reviewHash,sessionIds:[free,protectedSession]},receipt=await client.action('history-cleanup','cleanup.apply',input,'ahp-root://','reviewed-batch');
  assert.equal(receipt.status,'completed',JSON.stringify(receipt));assert.deepEqual(receipt.items.map(row=>row.status),['hidden','refused'],JSON.stringify(receipt));assert.ok(receipt.items[1].reason.endsWith(app.quiescence.coverage.capabilities.schedules));assert.equal(receipt.items[1].executed,false);
  assert.deepEqual((await client.action('history-cleanup','cleanup.receipt',{commandId:'reviewed-batch'})).receipt,receipt);await assert.rejects(client.action('history-cleanup','cleanup.apply',input,'ahp-root://','reviewed-batch'),/already admitted/);assert.deepEqual((await client.action('history-cleanup','cleanup.receipt',{commandId:'reviewed-batch'})).receipt,receipt);
  assert.deepEqual(await hashes(join(home,'projects')),before);assert.equal(app.host.diagnostics().activeAgents,0);
  await client.action('schedules','schedule.cancel',{id:scheduled.schedule.id,expectedRevision:scheduled.schedule.revision},protectedSession);
  const remaining=await client.action('history-cleanup','cleanup.preview',{modifiedBefore:cutoff,limit:50});assert.equal(remaining.candidateCount,1);assert.equal(remaining.items[0].session,protectedSession);
  const last=await client.action('history-cleanup','cleanup.apply',{reviewId:remaining.reviewId,reviewHash:remaining.reviewHash,sessionIds:[protectedSession]},'ahp-root://','after-explicit-cancel');assert.equal(last.items[0].status,'hidden',JSON.stringify(last));assert.deepEqual(await hashes(join(home,'projects')),before);assert.equal(app.host.diagnostics().activeAgents,0);
  const final=await client.action('history-cleanup','cleanup.preview',{modifiedBefore:cutoff,limit:50});assert.equal(final.candidateCount,0);
 }finally{client?.close();await app?.close();await updateOwner?.close();await rm(directory,{recursive:true,force:true});}
});
