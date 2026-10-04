import test from 'node:test';
import {AhpClient} from '@microsoft/agent-host-protocol/client';
import {WebSocketTransport} from '@microsoft/agent-host-protocol/ws';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,stat,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const {createWorkspaceCapabilities,workspaceActions}=await import(process.env.WORKSPACE_MODULE?pathToFileURL(resolve(process.env.WORKSPACE_MODULE)).href:new URL('../dist/index.js',import.meta.url).href);
const python=process.env.WORKSPACE_OWNER_PYTHON??fileURLToPath(new URL('../python/.venv/bin/python',import.meta.url));
const catalogFixture=()=>{const rows=new Map(),checkpoints=new Map();return {
 async workspaceProjectionStatus({source}){return {revision:checkpoints.get(source)??0};},
 async projectWorkspaces({source,afterRevision,throughRevision,records}){assert.equal(checkpoints.get(source)??0,afterRevision);for(const row of records)rows.set(row.id,{...row,available:true,availability:'present',source:'registered'});checkpoints.set(source,throughRevision);return {revision:throughRevision};},
 async getWorkspace({id}){return rows.get(id)??null;},
 async listWorkspaces({includeHidden,limit}){return {items:[...rows.values()].filter(r=>includeHidden||!r.hidden).slice(0,limit)};},
 async list(){return {items:[{uri:'ahp-session:/root',engineId:'native',title:'Saved',createdAt:'2026-10-02T00:00:00.000Z',modifiedAt:'2026-10-02T00:00:00.000Z',workingDirectory:'/tmp/workspace',storagePath:'/secret/native',nativeSessionId:'private'}]};},
 };};
const action=(owner,operation,args,commandId)=>owner.action({channel:'ahp-root://',topic:'workspaces',version:1,operation:'workspace.'+operation,args,commandId},{clientId:'one',origin:'ui'});
test('installed Python owner serves bounded public actions, known refusal, receipt, and redacted session summaries',async()=>{
 const temp=await mkdtemp(join(tmpdir(),'workspace-owner-')),root=join(temp,'projects'),config=join(temp,'config.json');await mkdir(root);
 await writeFile(config,JSON.stringify({stateDirectory:join(temp,'state'),allowedRoots:[root],defaultRoot:root}));
 const catalog=catalogFixture(),invalidations=[];const owner=createWorkspaceCapabilities({owner:{command:python,args:['-I','-m','amplifier_unified_workspaces.server','--config',config]},catalog,onInvalidate:(...args)=>invalidations.push(args)});
 try{
  assert.deepEqual(Object.keys(await owner.actionSchemas()),Object.keys(workspaceActions));
  const prepared=(await action(owner,'prepare',{name:'Actual directory'},'prepare')).result;
  const created=await action(owner,'create',{planId:prepared.planId},'create');assert.equal(created.accepted,true);assert.equal((await stat(created.result.workspace.path)).isDirectory(),true);
  const stale=await action(owner,'rename',{id:created.result.workspace.id,name:'New name',expectedRevision:999},'stale');assert.equal(stale.accepted,false);assert.equal(stale.result.executed,false);assert.equal(stale.result.receipt.status,'rejected');
  const receipt=await action(owner,'receipt',{commandId:'create'},'read');assert.equal(receipt.result.status,'completed');
  const sessions=await action(owner,'sessions',{id:created.result.workspace.id},'sessions');assert.equal(sessions.result.items[0].resource,'ahp-session:/root');assert.equal(JSON.stringify(sessions).includes('/secret'),false);assert.equal(JSON.stringify(sessions).includes('nativeSessionId'),false);
  const snapshot=await owner.read({topic:'workspaces',scope:'host',uri:owner.manifest.topics.workspaces.uri,clientId:'one'});assert.equal(snapshot.data.workspaces.items.length,1);assert.equal(snapshot.data.workspaces.defaultRoot,await realpath(root));assert.equal(Number.isSafeInteger(snapshot.revision),true);
  assert.deepEqual(invalidations,[['workspaces','host']]);
  await assert.rejects(owner.action({channel:'ahp-session:/foreign',topic:'workspaces',version:1,operation:'workspace.list',args:{},commandId:'foreign'},{clientId:'one',session:'ahp-session:/mine'}),/scope/);
 }finally{await owner.close();await rm(temp,{recursive:true,force:true});}
});

test('public installed host and catalog compose without starting any native agent', {skip:!process.env.HOST_MODULE||!process.env.CATALOG_EXECUTABLE},async()=>{
 const {createHost,StdioCatalog}=await import(pathToFileURL(resolve(process.env.HOST_MODULE)).href);
 const temp=await mkdtemp(join(tmpdir(),'workspace-installed-')),root=join(temp,'projects'),config=join(temp,'owner.json');await mkdir(root);
 await writeFile(config,JSON.stringify({stateDirectory:join(temp,'owner'),allowedRoots:[root],defaultRoot:root}));
 const catalog=new StdioCatalog({command:process.env.CATALOG_EXECUTABLE,args:['serve','--db',join(temp,'catalog.sqlite'),'--scan-interval','0','--workspace-check-interval','0']});
 const capability=createWorkspaceCapabilities({owner:{command:python,args:['-I','-m','amplifier_unified_workspaces.server','--config',config]},catalog});
 const host=await createHost({stateDirectory:join(temp,'host'),allowedWorkspaceRoots:[root],engines:[{id:'unused',command:'/must-never-start'}],catalog,capabilities:capability,quiescence:{instanceId:'workspace-host',dataScope:'owned-fixture',requiredOwners:['workspaces'],coverage:{capabilities:{workspaces:'workspaces'}},participants:[capability.quiescenceParticipant],verifyRelease:async input=>({verified:true,fenceId:input.fenceId,commandId:input.commandId,outcome:'unchanged',instanceId:'workspace-host',dataScope:'owned-fixture',receiptId:'fixture-retained-files'})}});
 const client=new AhpClient(await WebSocketTransport.connect(host.url)),second=new AhpClient(await WebSocketTransport.connect(host.url));client.connect();second.connect();
 await client.initialize({clientId:'workspace-one',protocolVersions:['0.9.0']});await second.initialize({clientId:'workspace-two',protocolVersions:['0.9.0']});
 const invoke=(operation,args,commandId)=>client.request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'workspaces',version:1,operation:'workspace.'+operation,args,commandId});
 try{
  const plan=(await invoke('prepare',{name:'End to end'},'prepare')).result;
  const created=(await invoke('create',{planId:plan.planId},'create')).result;const path=created.workspace.path;await writeFile(join(path,'events.jsonl'),'preserved');
  await catalog.upsert({uri:'ahp-session:/one',nativeSessionId:'one',engineId:'unused',workingDirectory:path,title:'Root'});
  await catalog.upsert({uri:'ahp-session:/child',nativeSessionId:'child',engineId:'unused',workingDirectory:path,title:'Child',parentUri:'ahp-session:/one'});
  assert.equal((await invoke('sessions',{id:created.workspace.id},'list-roots')).result.items.length,1);
  assert.equal((await invoke('sessions',{id:created.workspace.id,parentUri:'ahp-session:/one'},'list-children')).result.items.length,1);
  const canonicalRoot=await realpath(root),otherPath=join(canonicalRoot,'Other'),outside=join(temp,'outside');await mkdir(otherPath);await mkdir(outside);
  for(const [id,workingDirectory] of [['other',otherPath],['outside',outside],['missing',join(canonicalRoot,'missing')]])await catalog.upsert({uri:'ahp-session:/'+id,nativeSessionId:id,engineId:'unused',workingDirectory,title:'Root '+id});
  assert.deepEqual((await invoke('list',{},'feature')).result.sessionQuery,{global:true,archive:true,search:true});
  const firstGlobal=(await invoke('sessions',{limit:1,query:'Root'},'global-first')).result;assert.equal(firstGlobal.items.length,1);assert.ok(firstGlobal.nextCursor);
  const secondGlobal=(await invoke('sessions',{limit:1,query:'Root',cursor:firstGlobal.nextCursor},'global-second')).result;
  assert.deepEqual([...firstGlobal.items,...secondGlobal.items].map(row=>row.resource).sort(),['ahp-session:/one','ahp-session:/other']);
  await assert.rejects(invoke('sessions',{limit:1,query:'Root',archive:'archived',cursor:firstGlobal.nextCursor},'global-wrong-filter'),/Cursor belongs to another connection or query/);
  const invalidSelection=await invoke('sessions',{id:''},'invalid-selection');assert.equal(invalidSelection.accepted,false);
  await host.archiveSession('ahp-session:/one',true);
  assert.equal((await invoke('sessions',{id:created.workspace.id},'active-roots')).result.items.length,0);
  const archived=(await invoke('sessions',{id:created.workspace.id,archive:'archived'},'archived-roots')).result.items;assert.equal(archived.length,1);assert.equal(archived[0].status & (1<<6),1<<6);
  assert.deepEqual((await invoke('sessions',{archive:'archived',query:'Root'},'global-archived')).result.items.map(row=>row.resource),['ahp-session:/one']);
  assert.deepEqual((await invoke('sessions',{},'global-active')).result.items.map(row=>row.resource),['ahp-session:/other']);
  assert.equal((await invoke('sessions',{id:created.workspace.id,archive:'all'},'all-roots')).result.items.length,1);
  await rm(otherPath,{recursive:true});await catalog.request('workspace',{path:otherPath});
  await host.archiveSession('ahp-session:/one',false);
  const defaults=(await invoke('defaults',{},'defaults-read')).result;
  const changed=(await invoke('defaults.set',{defaultRoot:path,expectedConfigRevision:defaults.configRevision},'defaults-write')).result;
  const otherDefaults=await second.request('resourceRead',{channel:'ahp-root://',uri:capability.manifest.topics.workspaces.uri+'?scope=host',encoding:'utf-8'});assert.ok(JSON.stringify(otherDefaults).includes(changed.configRevision));
  const location=await client.request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'workspaces',version:1,operation:'locations.create',args:{path,name:'Child Folder'},commandId:'child-folder'});assert.equal(location.result.registered,false);
  const page=await second.request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'workspaces',version:1,operation:'locations.list',args:{path,limit:2},commandId:'location-page'});assert.equal(page.result.entries[0].name,'Child Folder');
  const renamed=(await invoke('rename',{id:created.workspace.id,name:'Shared label',expectedRevision:created.workspace.revision},'rename')).result;
  assert.equal((await invoke('list',{query:'Shared'},'search')).result.items[0].name,'Shared label');
  const refused=await invoke('rename',{id:created.workspace.id,name:'stale',expectedRevision:0},'stale');assert.equal(refused.accepted,false);assert.equal((await client.request('x-commandReceipt',{channel:'ahp-root://',commandId:'stale'})).status,'failed');
  const otherView=await second.request('resourceRead',{channel:'ahp-root://',uri:capability.manifest.topics.workspaces.uri+'?scope=host',encoding:'utf-8'});assert.ok(JSON.stringify(otherView).includes('Shared label'));
  await invoke('remove',{id:created.workspace.id,expectedRevision:renamed.workspace.revision},'remove');
  assert.deepEqual((await invoke('sessions',{archive:'all'},'global-hidden')).result.items,[]);
  assert.deepEqual((await second.request('listSessions',{channel:'ahp-root://',limit:50})).items,[]);assert.equal(await readFile(join(path,'events.jsonl'),'utf8'),'preserved');
  assert.equal((await catalog.get('ahp-session:/one')).title,'Root');assert.equal(host.diagnostics().activeAgents,0);
  await invoke('add',{path},'reattach');assert.equal((await catalog.list({connectionId:'verify',limit:50,allowedWorkspaceRoots:[canonicalRoot]})).items.length,1);
  const held=await host.admitQuiescence({commandId:'fixture-recovery',purpose:'recovery'});assert.equal(held.admitted,true);assert.equal((await capability.inspectQuiescence()).intakeClosed,true);
  const passive=await invoke('receipt',{commandId:'create'},'held-read');assert.equal(passive.result.status,'completed');
  await assert.rejects(invoke('prepare',{name:'Never creates'},'blocked-during-maintenance'),/quiescen|intake|maintenance/i);
  await host.releaseQuiescence({fenceId:held.fenceId,commandId:held.commandId,outcome:'unchanged',evidence:{fixture:true}});assert.equal((await capability.inspectQuiescence()).intakeClosed,false);
  process.stdout.write(JSON.stringify({receipt:'workspace-installed',existingDirectoriesOnly:true,rootSessions:1,selectedChildPage:1,historyPreserved:true,activeAgents:host.diagnostics().activeAgents,knownStaleRefusal:refused.result.executed===false})+'\n');
 }finally{await client.shutdown();await second.shutdown();await capability.close();await host.close();await rm(temp,{recursive:true,force:true});}
});


const fence={fenceId:'held-workspaces',commandId:'maintenance',purpose:'recovery',instanceId:'fixture-launch',dataScope:'fixture-data'};
const proof={verified:true,...fence,outcome:'unchanged',receiptId:'fixture-known-nochange'};
const wait=async fn=>{for(let n=0;n<500;n++){const value=await fn();if(value)return value;await new Promise(r=>setTimeout(r,10));}throw Error('Fixture did not reach expected boundary');};
test('queued creation and reverse catalog work prevent acquisition; held reads are passive and idle signals are delivered',async()=>{
 const temp=await mkdtemp(join(tmpdir(),'workspace-held-')),root=join(temp,'projects'),config=join(temp,'owner.json');await mkdir(root);await writeFile(config,JSON.stringify({stateDirectory:join(temp,'state'),allowedRoots:[root],defaultRoot:root}));
 const catalog=catalogFixture();let enter,unblock,entered=false,block=true,idle=0;const waiting=new Promise(resolve=>unblock=resolve),base=catalog.projectWorkspaces;
 catalog.projectWorkspaces=async args=>{if(block){entered=true;await waiting;}return base(args);};
 const options={owner:{command:python,args:['-I','-m','amplifier_unified_workspaces.server','--config',config]},catalog,onMayBeIdle:()=>idle++};let owner=createWorkspaceCapabilities(options);
 try{
  const plan=(await action(owner,'prepare',{name:'Count complete mutation'},'prepare')).result;
  const creation=action(owner,'create',{planId:plan.planId},'create');await wait(()=>entered);
  assert.equal(await owner.quiescenceParticipant.acquire(fence),null);assert.equal((await stat(plan.path)).isDirectory(),true);
  unblock();await creation;block=false;const lease=await owner.quiescenceParticipant.acquire(fence);assert.ok(lease);assert.ok(idle>0);
  const refused=await action(owner,'prepare',{name:'Not admitted'},'blocked');assert.equal(refused.accepted,false);assert.equal(refused.result.executed,false);
  assert.equal((await action(owner,'receipt',{commandId:'create'},'receipt')).result.status,'completed');
  const rows=await owner.read({topic:'workspaces',scope:'host',uri:owner.manifest.topics.workspaces.uri,clientId:'one'});assert.equal(rows.data.workspaces.coverage.projection.held,true);
  await lease.release('unknown');await owner.close();owner=createWorkspaceCapabilities(options);
  assert.equal((await owner.inspectQuiescence()).intakeClosed,true);assert.equal((await action(owner,'prepare',{name:'Still blocked'},'blocked-again')).accepted,false);
  await owner.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof});await owner.close();owner=createWorkspaceCapabilities(options);
  await owner.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof});assert.equal((await owner.inspectQuiescence()).intakeClosed,false);
  await assert.rejects(owner.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof:{...proof,receiptId:'different'}}),/receipt/);
 }finally{unblock();await owner.close();await rm(temp,{recursive:true,force:true});}
});

test('private release acknowledgement loss retains exact native release receipt without repeating filesystem effects',async()=>{
 const temp=await mkdtemp(join(tmpdir(),'workspace-lost-release-')),root=join(temp,'projects'),config=join(temp,'owner.json'),marker=join(temp,'dropped');await mkdir(root);await writeFile(config,JSON.stringify({stateDirectory:join(temp,'state'),allowedRoots:[root],defaultRoot:root}));
 const script=`import asyncio,json,os,sys
from pathlib import Path
from amplifier_unified_workspaces.server import Peer
class Drop(Peer):
 async def write(self,row):
  marker=Path(sys.argv[2])
  if isinstance(row.get('result'),dict) and row['result'].get('released') is True and not marker.exists():
   marker.write_text('exact release persisted');os._exit(73)
  await super().write(row)
asyncio.run(Drop(json.loads(Path(sys.argv[1]).read_text())).run())`;
 const options={owner:{command:python,args:['-I','-c',script,config,marker]},catalog:catalogFixture()};let owner=createWorkspaceCapabilities(options);
 try{
  const lease=await owner.quiescenceParticipant.acquire(fence);assert.ok(lease);await assert.rejects(lease.release('unchanged',proof),/exited|unknown/);await owner.close();
  owner=createWorkspaceCapabilities(options);await owner.quiescenceParticipant.reconcileRelease({...fence,outcome:'unchanged',proof});assert.equal((await owner.inspectQuiescence()).intakeClosed,false);assert.equal(await readFile(marker,'utf8'),'exact release persisted');
  assert.equal((await action(owner,'list',{},'read')).result.items.length,0);
 }finally{await owner.close();await rm(temp,{recursive:true,force:true});}
});
