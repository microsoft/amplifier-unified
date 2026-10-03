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
 const catalog=catalogFixture(),invalidations=[];const owner=createWorkspaceCapabilities({owner:{command:python,args:['-m','amplifier_unified_workspaces.server','--config',config]},catalog,onInvalidate:(...args)=>invalidations.push(args)});
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
 const capability=createWorkspaceCapabilities({owner:{command:python,args:['-m','amplifier_unified_workspaces.server','--config',config]},catalog});
 const host=await createHost({stateDirectory:join(temp,'host'),allowedWorkspaceRoots:[root],engines:[{id:'unused',command:'/must-never-start'}],catalog,capabilities:capability});
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
  const renamed=(await invoke('rename',{id:created.workspace.id,name:'Shared label',expectedRevision:created.workspace.revision},'rename')).result;
  assert.equal((await invoke('list',{query:'Shared'},'search')).result.items[0].name,'Shared label');
  const refused=await invoke('rename',{id:created.workspace.id,name:'stale',expectedRevision:0},'stale');assert.equal(refused.accepted,false);assert.equal((await client.request('x-commandReceipt',{channel:'ahp-root://',commandId:'stale'})).status,'failed');
  const otherView=await second.request('resourceRead',{channel:'ahp-root://',uri:capability.manifest.topics.workspaces.uri,encoding:'utf-8'});assert.ok(JSON.stringify(otherView).includes('Shared label'));
  await invoke('remove',{id:created.workspace.id,expectedRevision:renamed.workspace.revision},'remove');
  assert.deepEqual((await second.request('listSessions',{channel:'ahp-root://',limit:50})).items,[]);assert.equal(await readFile(join(path,'events.jsonl'),'utf8'),'preserved');
  assert.equal((await catalog.get('ahp-session:/one')).title,'Root');assert.equal(host.diagnostics().activeAgents,0);
  await invoke('add',{path},'reattach');assert.equal((await catalog.list({connectionId:'verify',limit:50})).items.length,1);
  process.stdout.write(JSON.stringify({receipt:'workspace-installed',existingDirectoriesOnly:true,rootSessions:1,selectedChildPage:1,historyPreserved:true,activeAgents:host.diagnostics().activeAgents,knownStaleRefusal:refused.result.executed===false})+'\n');
 }finally{await client.shutdown();await second.shutdown();await capability.close();await host.close();await rm(temp,{recursive:true,force:true});}
});
