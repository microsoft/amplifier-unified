/** Opt-in existing native history copies, without execution or credential import. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,realpath} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {tmpdir} from 'node:os';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {once} from 'node:events';

const entry=process.env.UNIFIED_DISTRIBUTION_ENTRY,python=process.env.RETAINED_HISTORY_PYTHON,donor=process.env.RETAINED_HISTORY_COPIES;
const enabled=Boolean(entry&&python&&donor),source=fileURLToPath(new URL('retained-user-history-fixture.py',import.meta.url));
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
// Response chunk IDs are per-read view identities. Canonical turn IDs, message
// locators, tool IDs, text and metadata must still survive exactly.
const historyDigest=turns=>sha(JSON.stringify(turns.map(turn=>({...turn,responseParts:turn.responseParts.map(part=>{if(!['markdown','reasoning'].includes(part.kind))return part;const {id,...body}=part;return body;})}))));
test('installed catalog and passive host browse unchanged real native history copies across restart and archive',
 {skip:!enabled,timeout:180000},async()=>{
 const require=createRequire(pathToFileURL(entry)),{WebSocket}=require('ws');
 const {createDistribution}=await import(pathToFileURL(entry));
 const directory=await realpath(await mkdtemp(join(process.env.RETAINED_HISTORY_DIRECTORY??tmpdir(),'retained-history-')));
 const seed=JSON.parse(execFileSync(python,['-I','-B',source,'seed',directory,donor],{encoding:'utf8'}));
 const workspace=join(directory,'workspace'),appHome=join(directory,'native-app'),state=join(directory,'host-state');
 await mkdir(workspace);const bundle=join(directory,'inert.yaml'),config=join(directory,'native.json');
 await writeFile(bundle,'bundle:\n  name: retained-history-rehearsal\n  version: 1.0.0\nproviders: []\n');
 await writeFile(join(seed.home,'settings.yaml'),'bundle:\n  app: []\n');
 const roots=[workspace,...new Set(seed.sessions.map(s=>s.grant))];
 await writeFile(config,JSON.stringify({home:seed.home,appHome,bundle,workerCommand:['/impossible-retained-history-worker'],adminWorkspaceRoots:roots}));
 const env={TMPDIR:directory,PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_HOME:seed.home,AMPLIFIER_WEB_HOME:appHome,AMPLIFIER_SESSION_STATE_HOME:join(directory,'writers'),AMPLIFIER_SOURCE_STORE:join(directory,'sources'),XDG_CACHE_HOME:join(directory,'cache')};
 const options={account:'retained-rehearsal',stateDirectory:state,defaultWorkspace:workspace,allowedWorkspaceRoots:roots,
  webDirectory:join(dirname(dirname(entry)),'web'),engines:[{id:'amplifier',command:python,args:['-I','-B',source,'native',directory,'amplifier-acp','--config',config],env}],
  nativeAdmin:{engine:'amplifier'},catalogProcess:{command:python,args:['-I','-B',source,'catalog',directory,'catalog','serve','--db',join(directory,'catalog.sqlite'),'--home',seed.home,'--app-home',appHome,'--scan-interval','0','--workspace-check-interval','0'],env}};
 let app,socket;const receipt={kind:'retained-history-installed-passive-rehearsal',fixture:directory,samples:[],workersStarted:0,workReplayed:false};
 const connect=async()=>{
  socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');let id=0;const pending=new Map();
  socket.on('message',raw=>{const row=JSON.parse(raw),p=pending.get(row.id);if(p){clearTimeout(p.timer);pending.delete(row.id);row.error?p.reject(Object.assign(Error(row.error.message),{code:row.error.code})):p.resolve(row.result);}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const key=++id,timer=setTimeout(()=>{pending.delete(key);reject(Error('RPC timeout: '+method));},20000);pending.set(key,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id:key,method,params}));});
  await request('initialize',{channel:'ahp-root://',clientId:randomUUID(),protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});return request;
 };
 const unchanged=async()=>{for(const row of seed.sessions)for(const [name,digest]of Object.entries(row.hashes)){
  assert.equal(sha(await readFile(join(row.directory,name))),digest,'Rehearsal copy changed');
  assert.equal(sha(await readFile(join(donor,row.sample,name))),digest,'Read-only source copy changed');
 }};
 const query=async(extra={})=>app.host.queryLibrary({connectionId:'rehearsal-reader',allowedWorkspaceRoots:roots,limit:1,archive:'all',sort:'name',...extra});
 try{
  app=await createDistribution(options);let request=await connect();await app.host.reconcileLibrary();
  const indexed=[];let cursor;do{const page=await query(cursor?{cursor}:{});assert.ok(!page.refreshRequired&&page.available!==false);indexed.push(...page.items);cursor=page.nextCursor;assert.ok(indexed.length<=seed.sessions.length);}while(cursor);
  // Missing workspace histories may be read-only, but retain their canonical identity.
  const allRoots=seed.sessions.filter(s=>s.kind==='root');
  assert.deepEqual(indexed.map(row=>row.title.toLowerCase()),indexed.map(row=>row.title.toLowerCase()).sort(),'Name sorting must cover the complete paged result');
  receipt.indexedRoots=indexed.length;receipt.sourceRoots=allRoots.length;receipt.wholeResultNameSort=true;
  const snapshots=new Map();
  for(const row of seed.sessions){
   const sample={sample:row.sample,kind:row.kind,workspaceExists:row.workspaceExists,listed:indexed.some(x=>x.uri===row.session)};
   try{
    const response=await request('subscribe',{channel:row.session.replace('ahp-session:','ahp-chat:'),view:{turns:5}}),chat=response.snapshot.state;
    assert.ok(chat.turns.length>0);assert.ok(chat.turns.length<=5);sample.selectedTurns=chat.turns.length;sample.hasOlderPage=!!chat.turnsNextCursor;
    // Compare only canonical turns, never transport revisions or transient cursors.
    await writeFile(join(directory,'before-'+row.sample+'.json'),JSON.stringify(chat.turns));snapshots.set(row.session,historyDigest(chat.turns));sample.historyReadable=true;
   }catch(error){sample.historyReadable=false;sample.errorCode=error.code??null;sample.error=error.message;}
   receipt.samples.push(sample);
  }
  assert.ok(receipt.samples.every(x=>!x.workspaceExists||x.historyReadable),'Existing-workspace retained history was not readable');
  for(const row of allRoots.filter(s=>s.workspaceExists)){
   const found=await query({search:row.session});assert.equal(found.items.length,1,'Search must find copied root by stable identity');
   await app.host.archiveSession(row.session,true);
   assert.equal((await query({search:row.session,archive:'active'})).items.length,0);
   assert.equal((await query({search:row.session,archive:'archived'})).items.length,1);
  }
  assert.equal(app.host.diagnostics().activeAgents,0);await unchanged();socket.terminate();socket=null;await app.close();app=null;
  app=await createDistribution(options);request=await connect();await app.host.reconcileLibrary();
  for(const row of seed.sessions){
   if(snapshots.has(row.session)){
    const response=await request('subscribe',{channel:row.session.replace('ahp-session:','ahp-chat:'),view:{turns:5}});
    await writeFile(join(directory,'after-'+row.sample+'.json'),JSON.stringify(response.snapshot.state.turns));assert.equal(historyDigest(response.snapshot.state.turns),snapshots.get(row.session),'History projection changed after restart');
   }
   if(row.kind==='root'&&row.workspaceExists){
    assert.equal((await query({search:row.session,archive:'archived'})).items.length,1,'Archive state did not persist');
    await app.host.archiveSession(row.session,false);assert.equal((await query({search:row.session,archive:'active'})).items.length,1);
   }
  }
  await unchanged();assert.equal(app.host.diagnostics().activeAgents,0);
  const audit=(await readFile(join(directory,'native-audit.jsonl'),'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);
  assert.equal(audit.filter(x=>x.event.endsWith('refused')).length,0);
  Object.assign(receipt,{canonicalBytesPreserved:true,sourceCopiesPreserved:true,restartProjectionPreserved:true,archiveReopenPreserved:true,readableHistories:receipt.samples.filter(s=>s.historyReadable).length,
   limits:['Bounded pre-copied samples only; not a complete user migration.','Passive reads and organization only; no resume, fork, artifact, event-log, settings or credential migration.','No execution, model calls, browser/device or production-service changes.']});
 }finally{
  socket?.terminate();await app?.close();
  await writeFile(join(directory,'acceptance.json'),JSON.stringify(receipt,null,2));
  if(process.env.RETAINED_HISTORY_RECEIPT)await writeFile(process.env.RETAINED_HISTORY_RECEIPT,JSON.stringify(receipt,null,2));
  console.error('Retained owned history fixture:',directory);
 }
});
