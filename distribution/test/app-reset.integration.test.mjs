import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {once} from 'node:events';
import {WebSocket} from 'ws';
import {DistributionUpdateOwner,createManualIngressGate} from '@amplifier/unified-distribution-update-owner';
const {createDistribution}=await import(process.env.UNIFIED_DISTRIBUTION_ENTRY??'../src/index.js');
const python=process.env.APP_RESET_AMPLIFIER_PYTHON,owners=process.env.UNIFIED_OWNERS_PYTHON,catalog=process.env.UNIFIED_CATALOG_PYTHON;

async function peer(app){
 const socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');
 let sequence=0;const pending=new Map();
 socket.on('message',raw=>{const row=JSON.parse(raw),waiter=pending.get(row.id);if(!waiter)return;pending.delete(row.id);clearTimeout(waiter.timer);row.error?waiter.reject(Object.assign(Error(row.error.message),{data:row.error.data})):waiter.resolve(row.result);});
 socket.on('close',()=>{for(const waiter of pending.values()){clearTimeout(waiter.timer);waiter.reject(Error('Connection closed'));}pending.clear();});
 const request=(method,params)=>new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error('Timed out: '+method));},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
 await request('initialize',{channel:'ahp-root://',clientId:'reset-owner',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
 const action=async(topic,operation,args={},commandId=randomUUID())=>(await request('x-amplifier/capabilityAction',{version:1,topic,channel:'ahp-root://',operation,args,commandId})).result;
 const settled=async id=>{for(let n=0;n<1500;n++){const row=await action('recovery','recovery.job',{jobId:id});if(['prepared','succeeded','refused','unknown'].includes(row.state))return row;await new Promise(resolve=>setTimeout(resolve,10));}throw Error('App reset did not settle');};
 return {action,settled,close:()=>socket.terminate()};
}
async function contents(directory){const buffers=[];for(const entry of await readdir(directory,{withFileTypes:true})){const path=join(directory,entry.name);if(entry.isDirectory())buffers.push(...await contents(path));else if(entry.isFile())buffers.push(await readFile(path));}return buffers;}

test('assembled app-local reset uses actual registered owners, complete recovery census and private retained undo',
 {skip:!python||!owners||!catalog,timeout:180000},async()=>{
 const directory=await realpath(await mkdtemp(join(tmpdir(),'distribution-app-reset-'))),workspace=join(directory,'workspace'),web=join(directory,'web'),home=join(directory,'home'),appHome=join(directory,'native-app'),state=join(directory,'state');
 let app,client,supervisor,gate;
 try{
  for(const path of [workspace,web,home,appHome])await mkdir(path);
  await writeFile(join(web,'index.html'),'<!doctype html><title>Owned reset acceptance</title>');
  const original=Buffer.from('bundle:\n  active: private-default-bundle\n  unrelated: retained\nother: retained\n');
  await writeFile(join(appHome,'admin-settings.yaml'),original);
  await writeFile(join(home,'settings.yaml'),'shared: retained\n');await writeFile(join(home,'keys.env'),'shared-fixture-secret');
  const nativeConfig=join(directory,'native.json');await writeFile(nativeConfig,JSON.stringify({home,appHome,adminWorkspaceRoots:[workspace],adminMaintenance:true,workerCommand:['/impossible-reset-worker'],transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[workspace]}));
  const noUpdate=async()=>{throw Error('Reset cannot invoke a release or service effect');};
  supervisor=new DistributionUpdateOwner({directory:join(directory,'supervisor'),dataScope:'reset-owned',preferences:{autoCheck:false,autoInstall:false,intervalMs:1000},releases:{check:noUpdate,prepare:noUpdate,verify:noUpdate},lifecycle:{inspect:noUpdate,admitRestart:noUpdate,restart:noUpdate}});
  gate=await createManualIngressGate({directory:join(directory,'ingress'),id:'manual-preview-ingress'});
  const config={account:'reset-fixture',stateDirectory:state,webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],host:{managedSessionRoot:join(workspace,'.managed')},engines:[{id:'amplifier',command:python,args:['-I','-m','amplifier_acp','--config',nativeConfig]}],nativeAdmin:{engine:'amplifier'},maintenance:{},recovery:{},historyImport:{},historyCleanup:true,managedFiles:true,applicationUpdates:true,portability:{python:owners,engines:['amplifier'],stageDir:join(workspace,'stages'),exchangeDir:join(workspace,'exchange')},quiescence:{instanceId:'reset-original',dataScope:'reset-owned',timeoutMs:30000},...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(key=>[key,{python:owners}])),catalogProcess:{command:catalog,args:['-I','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--scan-interval','0','--workspace-check-interval','0']}};
  const ports={runtimeOwnerBindings:[{owner:gate.participant}],applicationUpdateSupervisor:{outlivesDistribution:true,owner:supervisor,subscribe:()=>()=>{}},authorizeRecovery:async caller=>{assert.equal(caller.account,'reset-fixture');return {accountId:'reset-fixture'};}};
  app=await createDistribution(config,ports);client=await peer(app);
  assert.equal(app.quiescence.requiredOwners.length,20);assert.equal(app.quiescence.participants.length,20);
  await client.action('notifications','notifications.save',{expectedRevision:0,patch:{enabled:true,preview:true,topic:'private-notification-topic',token:'private-notification-token'}});
  const parts=['native.app-bundle-default','notifications.settings','notifications.credentials'];
  const prepare=async extra=>client.settled((await client.action('recovery','recovery.appReset.prepare',{parts,privateContentReviewed:true,credentialsReviewed:true,...extra})).id);
  const reviewed=await prepare();assert.equal(reviewed.state,'prepared',JSON.stringify(reviewed));
  const manifest=await client.action('recovery','recovery.preview',{jobId:reviewed.id});assert.equal(manifest.owners.length,2);assert.equal(manifest.completeAppReset,false);
  assert.equal(app.host.diagnostics().activeAgents,0);assert.equal(app.host.inspectQuiescence().intakeClosed,false);
  const applied=await client.settled((await client.action('recovery','recovery.appReset.apply',{preparedJobId:reviewed.id,previewHash:reviewed.previewHash},'exact-app-reset')).id);
  assert.equal(applied.state,'succeeded',JSON.stringify(applied));assert.equal(applied.result.restored,false);assert.equal(app.host.inspectQuiescence().intakeClosed,false);
  assert.ok(!(await readFile(join(appHome,'admin-settings.yaml'),'utf8')).includes('active:'));
  assert.equal(await readFile(join(home,'settings.yaml'),'utf8'),'shared: retained\n');assert.equal(await readFile(join(home,'keys.env'),'utf8'),'shared-fixture-secret');
  const preparedUndo=await prepare({restoreResetJobId:applied.id});assert.equal(preparedUndo.state,'prepared',JSON.stringify(preparedUndo));
  const restored=await client.settled((await client.action('recovery','recovery.appReset.restore',{preparedJobId:preparedUndo.id,previewHash:preparedUndo.previewHash,resetJobId:applied.id,expectedPostResetRevision:applied.result.postResetRevision})).id);
  assert.equal(restored.state,'succeeded',JSON.stringify(restored));assert.equal(restored.result.restored,true);assert.deepEqual(await readFile(join(appHome,'admin-settings.yaml')),original);
  assert.equal(app.host.diagnostics().activeAgents,0);assert.equal(app.host.inspectQuiescence().intakeClosed,false);
  for(const bytes of await contents(join(state,'capabilities','recovery')))for(const secret of ['private-notification-topic','private-notification-token','private-default-bundle','shared-fixture-secret'])assert.equal(bytes.includes(Buffer.from(secret)),false,'Product receipts must not contain owner private values');
  client.close();client=null;await app.close();app=null;
  app=await createDistribution(config,ports);client=await peer(app);
  assert.deepEqual(await client.action('recovery','recovery.job',{jobId:applied.id}),applied,'Restart reads the exact original settled job');
  assert.deepEqual(await readFile(join(appHome,'admin-settings.yaml')),original,'Receipt recovery cannot reapply reset');assert.equal(app.host.diagnostics().activeAgents,0);
 }finally{client?.close();await app?.close();await supervisor?.close();gate?.close();await rm(directory,{recursive:true,force:true});}
});
