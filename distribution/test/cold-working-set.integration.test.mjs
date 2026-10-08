/** Opt-in current installed composition, synthetic history and zero execution. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {createHash,randomUUID} from 'node:crypto';

const entry=process.env.UNIFIED_DISTRIBUTION_ENTRY,python=process.env.COLD_WORKING_SET_PYTHON;
const enabled=Boolean(entry&&python),root='ahp-root://';
const source=fileURLToPath(new URL('cold-working-set-fixture.py',import.meta.url));
const wait=async predicate=>{for(let n=0;n<400;n++){if(await predicate())return;await new Promise(r=>setTimeout(r,25));}throw Error('Bounded fixture condition did not settle');};
const expectedOwners=['portability','capability:attachments','workspaces','native-admin','native-message-metadata','application-updates','capability:voice','capability:connectors','notifications','diagnostics','capability:observations','capability:coordination','capability:worktrees','capability:publishing','capability:recall','capability:feedback','recovery','history-import','history-cleanup','managed-files','manual-preview-ingress'];

test('installed full-owner graph keeps cold selected history and unrelated viewers bounded',
 {skip:!enabled,timeout:180000},async()=>{
 const require=createRequire(pathToFileURL(entry)),{WebSocket}=require('ws');
 const {createDistribution}=await import(pathToFileURL(entry));
 const {createManualIngressGate,connectSupervisorFileLazy}=await import(pathToFileURL(require.resolve('@amplifier/unified-distribution-update-owner')));
 const directory=await realpath(await mkdtemp(join(process.env.COLD_WORKING_SET_DIRECTORY??tmpdir(),'full-owner-cold-')));
 const seed=JSON.parse(execFileSync(python,['-I','-B',source,'seed',directory],{encoding:'utf8'}));
 const state=join(directory,'state'),appHome=join(directory,'native-app'),log=join(directory,'io.jsonl');
 const audit=async()=>{try{return (await readFile(log,'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);}catch(e){if(e.code==='ENOENT')return [];throw e;}};
 const fixtureEnv={PYTHONDONTWRITEBYTECODE:'1',COLD_IO_LOG:log,AMPLIFIER_HOME:seed.home,AMPLIFIER_WEB_HOME:appHome,
  AMPLIFIER_SESSION_STATE_HOME:join(directory,'writers'),AMPLIFIER_SOURCE_STORE:join(directory,'sources'),XDG_CACHE_HOME:join(directory,'cache')};
 const config=join(directory,'native.json'),bundle=join(directory,'inert.yaml'),managed=join(seed.workspace,'.managed');
 await writeFile(bundle,'bundle:\n  name: cold-working-set\n  version: 1.0.0\nproviders: []\n');
 await writeFile(join(seed.home,'settings.yaml'),'bundle:\n  app: []\n');
 await writeFile(config,JSON.stringify({home:seed.home,appHome,bundle,workerCommand:['/impossible-cold-working-set-worker'],managedSessionRoots:[managed],
  adminWorkspaceRoots:[seed.workspace],adminMaintenance:true,transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[seed.workspace]}));
 // Owner wrappers isolate Python imports and bytecode; they do not modify donors.
 const ownerPython=join(directory,'owner-python');
 await writeFile(ownerPython,'#!/bin/sh\nexec '+"'"+python.replaceAll("'","'\\''")+"'"+' -I -B "$@"\n',{mode:0o700});
 const gate=await createManualIngressGate({directory:join(directory,'ingress'),id:'manual-preview-ingress'});
 const supervisor=connectSupervisorFileLazy(join(directory,'absent-supervisor.json'));
 let app;const peers=[];const receipt={schema:'current-full-owner-cold-working-set-v1',entry,node:process.version,fixture:directory,checks:{}};
 const peer=async()=>{
  const socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url,headers:{authorization:'Bearer cold-fixture'}});await once(socket,'open');
  const events=[],pending=new Map();let id=0;
  socket.on('message',raw=>{const row=JSON.parse(raw),p=pending.get(row.id);if(p){clearTimeout(p.timer);pending.delete(row.id);row.error?p.reject(Object.assign(Error(row.error.message),{code:row.error.code,data:row.error.data})):p.resolve(row.result);}else events.push(row);});
  const request=(method,params)=>new Promise((resolve,reject)=>{const key=++id,timer=setTimeout(()=>{pending.delete(key);reject(Error('RPC timeout: '+method));},20000);pending.set(key,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id:key,method,params}));});
  const p={socket,request,events};peers.push(p);
  await request('initialize',{channel:root,clientId:randomUUID(),protocolVersions:['0.9.0'],initialSubscriptions:[root]});return p;
 };
 try{
  app=await createDistribution({account:'cold-working-set',stateDirectory:state,defaultWorkspace:seed.workspace,allowedWorkspaceRoots:[seed.workspace],
   webDirectory:join(dirname(dirname(entry)),'web'),host:{managedSessionRoot:managed,maxClients:40,replayLimit:64},
   engines:[{id:'amplifier',command:python,args:['-I','-B',source,'native','amplifier-acp','--config',config],env:fixtureEnv}],
   nativeAdmin:{engine:'amplifier'},maintenance:{},recovery:{authorization:'local-account'},historyImport:{},historyCleanup:true,managedFiles:true,applicationUpdates:true,
   portability:{python:ownerPython,engines:['amplifier'],stageDir:join(seed.workspace,'stages'),exchangeDir:join(seed.workspace,'exchange')},
   quiescence:{instanceId:'cold-fixture',dataScope:'cold-owned',timeoutMs:30000},
   ...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(key=>[key,{python:ownerPython}])),
   catalogProcess:{command:python,args:['-I','-B',source,'catalog','catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',seed.home,'--app-home',appHome,'--workspace',seed.workspace,'--scan-interval','0','--workspace-check-interval','0'],env:fixtureEnv}},
   {authorize:async request=>request.headers.authorization==='Bearer cold-fixture'?{account:'cold-working-set'}:null,applicationUpdateSupervisor:supervisor,runtimeOwnerBindings:[{owner:gate.participant}],
    authorizeRecovery:async caller=>{assert.equal(caller.account,'cold-working-set');return {accountId:caller.account};}});
  assert.deepEqual([...app.quiescence.requiredOwners].sort(),[...expectedOwners].sort());
  assert.deepEqual(app.quiescence.participants.map(p=>p.id).sort(),[...expectedOwners].sort());
  receipt.owners=app.quiescence.requiredOwners;
  const started=await audit();assert.equal(started.filter(x=>x.event==='transcript-open').length,0);
  for(let n=0;n<32;n++)await peer();
  const beforePage=await audit();
  for(const p of peers){const page=await p.request('listSessions',{channel:root,limit:50});assert.equal(page.items.length,2);await p.request('subscribe',{channel:seed.sessions[0].session});}
  assert.equal((await audit()).length,beforePage.length,'Unrelated metadata must not open history or start more native peers');
  assert.equal(app.host.diagnostics().activeAgents,0);
  const chats=seed.sessions.map(s=>s.session.replace('ahp-session:','ahp-chat:'));
  const page=await peers[0].request('subscribe',{channel:chats[0]});
  assert.equal(page.snapshot.state.turns.length,50);assert.equal(page.snapshot.state.turns[0].id,'turn-1-9950');
  const other=await peers[1].request('subscribe',{channel:chats[1],view:{turns:25}});
  assert.equal(other.snapshot.state.turns.length,25);
  const selected=await audit(),readBytes=selected.filter(x=>x.event==='transcript-read').reduce((n,x)=>n+x.bytes,0);
  assert.ok(readBytes>0&&readBytes<seed.sessions[0].transcriptBytes/10);
  const seq=app.host.diagnostics().serverSeq;
  for(let n=0;n<32;n++)await peers[n].request('listSessions',{channel:root,limit:50});
  assert.equal((await audit()).length,selected.length,'Repeated metadata must not hydrate selected histories');
  assert.equal(app.host.diagnostics().serverSeq,seq,'Read-only metadata must not emit global state actions');
  for(const p of peers.slice(2))assert.equal(p.events.filter(x=>x.params?.channel?.startsWith('ahp-chat:')).length,0);
  const cursor=page.snapshot.state.turnsNextCursor;assert.ok(cursor);
  await peers[0].request('fetchTurns',{channel:chats[0],cursor});
  await wait(()=>peers[0].events.some(x=>x.params?.action?.type==='chat/turnsLoaded'));
  const loaded=peers[0].events.find(x=>x.params?.action?.type==='chat/turnsLoaded').params.action;
  assert.equal(loaded.turns.length,50);assert.equal(loaded.turns[0].id,'turn-1-9900');
  assert.equal(peers[1].events.filter(x=>x.params?.channel===chats[0]).length,0);
  receipt.checks={clients:32,selectedHistories:2,uninterestedClients:30,uninterestedChatEvents:0,rootRows:2,indexedChildren:700,
   canonicalTurns:12000,initialSelectedTurns:[50,25],initialTranscriptBytesRead:readBytes,historyPageTurns:50,metadataServerSequenceChanges:0,
   executionAgents:app.host.diagnostics().activeAgents,replay:app.host.diagnostics().retainedReplay};
  // The public authenticated gateway must return a typed refusal within its
  // frame budget, leaving both the requester and unrelated viewers connected.
  await assert.rejects(peers[0].request('subscribe',{channel:chats[0],view:{turns:10000}}),e=>e.code===-32013);
  for(const p of peers)assert.equal(await p.request('ping',{}),null);
  assert.equal(peers[1].events.filter(x=>x.params?.channel===chats[0]).length,0);
  receipt.checks.authenticatedGatewayOversizedHistory='typed-refusal-clients-preserved';
  // Detaching all viewers releases the selected shared projection, not history.
  for(const p of peers)p.socket.terminate();await wait(()=>app.host.diagnostics().clients===0);
  assert.equal(app.host.diagnostics().workingSet.selectedNativeTurnIds,0);
  if(process.env.COLD_MIXED_CLIENTS==='1'){
   for(const name of ['COLD_PLAYWRIGHT_ENTRY','COLD_PTY_PYTHON','COLD_PTY_OBSERVER','COLD_TUI_EXECUTABLE'])assert.ok(process.env[name],name+' is required');
   const {qualifyMixedClients}=await import('./cold-working-set-clients.mjs');
   receipt.mixedClients=await qualifyMixedClients({app,directory,seed});
   await wait(()=>app.host.diagnostics().clients===0);
   assert.equal(app.host.diagnostics().workingSet.selectedNativeTurnIds,0);
  }
  for(const s of seed.sessions)for(const [name,digest]of Object.entries(s.hashes))assert.equal(createHash('sha256').update(await readFile(join(s.directory,name))).digest('hex'),digest);
  assert.equal((await audit()).filter(x=>x.event.startsWith('forbidden')).length,0);
  receipt.canonicalHashesPreserved=true;receipt.diagnostics=app.host.diagnostics();
  receipt.limits=['Synthetic canonical data; actual installed owners and native passive reader.','No execution/model/device calls; no active-stream or production capacity claim. Browser/terminal coverage is present only when mixedClients is populated.','Dependency reuse must be bound by the invoking qualification receipt; not an independently resolved full runtime.'];
  if(process.env.COLD_WORKING_SET_RECEIPT)await writeFile(process.env.COLD_WORKING_SET_RECEIPT,JSON.stringify(receipt,null,2));
 }finally{for(const p of peers)p.socket.terminate();await app?.close();supervisor.close();gate.close();console.error('Retained cold working-set fixture:',directory);}
});
