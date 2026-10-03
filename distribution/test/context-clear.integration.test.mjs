import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath,chmod,readdir} from 'node:fs/promises';
import {join} from 'node:path';import {tmpdir} from 'node:os';import {pathToFileURL} from 'node:url';
import {randomUUID,createHash} from 'node:crypto';import {execFileSync} from 'node:child_process';import {createServer} from 'node:http';import {once} from 'node:events';
import {WebSocket,WebSocketServer} from 'ws';
const {createDistribution}=await import(process.env.UNIFIED_DISTRIBUTION_ENTRY??'../src/index.js');
const ACCOUNT='owned-context-composition',AUTHORITY=randomUUID();
const fixturePeer=new URL('./fixtures/context-clear-acp.mjs',import.meta.url).pathname;
async function peer(url,origin=url){
 const socket=new WebSocket(url.replace(/^http/,'ws')+'/ahp',{origin,headers:{'x-bundle-fixture-authority':AUTHORITY}});await once(socket,'open');
 let sequence=0;const pending=new Map();
 socket.on('message',raw=>{const row=JSON.parse(raw),waiter=pending.get(row.id);if(!waiter)return;pending.delete(row.id);clearTimeout(waiter.timer);row.error?waiter.reject(Object.assign(Error(row.error.message),{code:row.error.code,data:row.error.data})):waiter.resolve(row.result);});
 socket.on('close',()=>{for(const waiter of pending.values()){clearTimeout(waiter.timer);waiter.reject(Error('Reply lost: connection closed'));}pending.clear();});
 const request=(method,params)=>new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error('Timed out: '+method));},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
 const initialized=await request('initialize',{channel:'ahp-root://',clientId:'bundle-receipt-owner',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
 return {request,initialized,action:(channel,operation,args={},commandId=randomUUID())=>request('x-amplifier/capabilityAction',{channel,topic:'runtime-control',operation,version:1,args,commandId}),close:()=>socket.terminate()};
}

// A real WebSocket hop drops the completed outer response before the client
// receives it. The fixture keeps a separate oracle, never resends the request.
async function losingHop(appUrl,commandId){
 const server=createServer(),wss=new WebSocketServer({noServer:true}),pairs=new Set();let resolveDropped;
 const dropped=new Promise(resolve=>resolveDropped=resolve);
 server.on('upgrade',(req,socket,head)=>{
  wss.handleUpgrade(req,socket,head,frontend=>{
   const backend=new WebSocket(appUrl.replace(/^http/,'ws')+'/ahp',{origin:appUrl,headers:{'x-bundle-fixture-authority':AUTHORITY}}),pair={frontend,backend};pairs.add(pair);let ready=false,queued=[],dropId;
   const stop=()=>{pairs.delete(pair);frontend.terminate();backend.terminate();};
   frontend.on('error',stop);backend.on('error',stop);frontend.on('close',stop);backend.on('close',stop);
   backend.on('open',()=>{ready=true;for(const row of queued)backend.send(row,{binary:false});queued=[];});
   frontend.on('message',raw=>{const row=JSON.parse(raw);if(row.params?.commandId===commandId)dropId=row.id;ready?backend.send(raw,{binary:false}):queued.push(raw);});
   backend.on('message',raw=>{const row=JSON.parse(raw);if(dropId!==undefined&&row.id===dropId){resolveDropped(row);stop();return;}frontend.send(raw,{binary:false});});
  });
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 return {url:'http://127.0.0.1:'+server.address().port,dropped,async close(){for(const p of pairs){p.frontend.terminate();p.backend.terminate();}await new Promise(resolve=>wss.close(resolve));await new Promise(resolve=>server.close(resolve));}};
}

async function fixture({older=false,missing=false}={}){
 const root=await realpath(await mkdtemp(join(tmpdir(),'unified-context-composition-'))),workspace=join(root,'history'),execution=join(root,'execution'),web=join(root,'web');
 for(const path of [workspace,execution,web])await mkdir(path);await writeFile(join(web,'index.html'),'<title>Context composition</title>');
 const engine=id=>({id,command:missing&&id==='native'?'/impossible/context-admin':process.execPath,args:[fixturePeer],env:{...process.env,CONTEXT_FIXTURE_ROOT:root,...(older?{CONTEXT_FIXTURE_OLDER:'1'}:{})}});
 const config={account:ACCOUNT,stateDirectory:join(root,'host'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace,execution],engines:[engine('native'),engine('foreign')],nativeAdmin:{engine:'native',timeoutMs:1000}};
 const open=(defaultWorkspace=workspace)=>createDistribution({...config,defaultWorkspace},{authorize:async req=>{assert.equal(req.headers['x-bundle-fixture-authority'],AUTHORITY);return {account:ACCOUNT};}});
 const logs=async()=>{try{return (await readFile(join(root,'rpc.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);}catch(e){if(e.code==='ENOENT')return [];throw e;}};
 return {root,workspace,execution,open,logs};
}
for(const mode of ['older','missing'])test('optional context clear '+mode+' peer leaves foreign session/history usable',{timeout:15000},async()=>{
 const f=await fixture({[mode]:true});let app,client;
 try{
  app=await f.open();assert.equal(app.capabilities.manifest.actions['context.clear'],undefined);client=await peer(app.url);
  const session='ahp-session:/'+randomUUID();await client.request('createSession',{channel:session,provider:'foreign',workingDirectories:[pathToFileURL(f.workspace).href]});
  const original=await app.host.inspectSession(session);assert.ok(original.nativeSessionId);
  await client.request('subscribe',{channel:session.replace('ahp-session:','ahp-chat:'),view:{turns:1}});
  client.close();client=null;await app.close();app=null;app=await f.open();client=await peer(app.url);
  assert.equal((await app.host.inspectSession(session)).nativeSessionId,original.nativeSessionId);
  await client.request('subscribe',{channel:session.replace('ahp-session:','ahp-chat:'),view:{turns:1}});assert.equal(app.host.diagnostics().activeAgents,0);
 }finally{client?.close();await app?.close();await rm(f.root,{recursive:true,force:true});}
});
test('context receipt retains original lost-reply native identity after relocation and cold restart without worker admission',{timeout:30000},async()=>{
 const f=await fixture();let app,client,hop;const session='ahp-session:/'+randomUUID(),foreign='ahp-session:/'+randomUUID(),outer='original-context-clear';
 try{
  app=await f.open();assert.ok(app.capabilities.manifest.actions['context.clear.receipt']);client=await peer(app.url);
  assert.ok(client.initialized._meta['amplifier.dev/capabilities'].actions['context.clear']);
  await client.request('createSession',{channel:session,provider:'native',workingDirectories:[pathToFileURL(f.workspace).href]});
  await client.request('createSession',{channel:foreign,provider:'foreign',workingDirectories:[pathToFileURL(f.workspace).href]});
  await assert.rejects(client.action(foreign,'context.clear.review'),/admitted native engine/);
  await assert.rejects(client.action(foreign,'context.clear',{expectedHistoryRevision:'a'.repeat(64),expectedControlRevision:'a'.repeat(64)}),/admitted native engine/);
  const original=await app.host.inspectSession(session),review=(await client.action(session,'context.clear.review')).result;
  client.close();client=null;hop=await losingHop(app.url,outer);client=await peer(hop.url,app.url);
  await assert.rejects(client.action(session,'context.clear',{expectedHistoryRevision:review.historyRevision,expectedControlRevision:review.controlRevision},outer),/Reply lost/);
  const dropped=await hop.dropped;assert.equal(dropped.error,undefined,JSON.stringify(dropped.error));
  client.close();client=null;await hop.close();hop=null;client=await peer(app.url);
  const inspect=async()=>(await client.action(session,'context.clear.receipt',{commandId:outer})).result;
  const receipt=await inspect();assert.equal(receipt.receipt.status,'succeeded');assert.deepEqual(receipt.receipt.result,dropped.result.result);
  const nativeId='unified-context-v1:'+createHash('sha256').update(JSON.stringify(['unified-native-capabilities-context',1,session,'context.clear',outer])).digest('hex');assert.equal(receipt.receipt.commandId,nativeId);
  const moved=await app.host.relocateSession(session,{commandId:'context-relocate',target:f.execution,expectedExecutionRevision:original.executionRevision});assert.equal(moved.applied,true,JSON.stringify(moved));
  assert.equal((await app.host.inspectSession(session)).workingDirectory,f.workspace);
  await writeFile(join(f.root,'cold'),'worker admission prohibited');client.close();client=null;await app.close();app=null;
  app=await f.open(f.execution);client=await peer(app.url);assert.equal(app.host.diagnostics().activeAgents,0);
  const before=(await f.logs()).length;assert.deepEqual(await inspect(),receipt);
  await assert.rejects(client.action(foreign,'context.clear.receipt',{commandId:outer}),/admitted native engine/);
  const reads=(await f.logs()).slice(before);assert.deepEqual(reads.map(x=>x.method),['_amplifier/context/receipt']);assert.deepEqual(reads[0].params,{sessionId:original.nativeSessionId,cwd:f.workspace,commandId:nativeId});assert.equal(app.host.diagnostics().activeAgents,0);
  // Unknown and absent receipts stay observations; neither authorizes replay.
  const unknownOuter='original-unknown-context',unknownId='unified-context-v1:'+createHash('sha256').update(JSON.stringify(['unified-native-capabilities-context',1,session,'context.clear',unknownOuter])).digest('hex');
  const state=JSON.parse(await readFile(join(f.root,'native.json'),'utf8'));
  const unknown={version:1,operation:'context.clear',commandId:unknownId,status:'unknown',createdAt:3};
  state.receipts[unknownId]={sessionId:original.nativeSessionId,cwd:f.workspace,receipt:unknown};await writeFile(join(f.root,'native.json'),JSON.stringify(state));
  assert.deepEqual((await client.action(session,'context.clear.receipt',{commandId:unknownOuter})).result.receipt,unknown);
  assert.equal((await client.action(session,'context.clear.receipt',{commandId:'original-absent-context'})).result.receipt,null);
  assert.equal(app.host.diagnostics().activeAgents,0);
  assert.equal((await f.logs()).filter(x=>x.method==='_amplifier/native'&&x.params.operation==='context.clear').length,1);
  await assert.rejects(client.action(session,'runtime.control',{operation:'context.clear'}),/admitted capability/);
 }finally{client?.close();await hop?.close();await app?.close();await rm(f.root,{recursive:true,force:true});}
});

const nativePython=process.env.UNIFIED_CONTEXT_CLEAR_PYTHON;
test('actual native root factory recovers refused and lost-success receipts after relocation and cold restart',{skip:!nativePython,timeout:90000},async()=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),'unified-context-native-'))),workspace=join(root,'history'),execution=join(root,'execution'),home=join(root,'home'),appHome=join(root,'native-app'),web=join(root,'web'),nativeConfig=join(root,'native.json'),bundle=join(root,'bundle.yaml');
 for(const p of [workspace,home,web])await mkdir(p);
 const gitEnv={...process.env,GIT_CONFIG_GLOBAL:'/dev/null',GIT_CONFIG_NOSYSTEM:'1'};
 execFileSync('git',['-c','core.hooksPath=/dev/null','init','--quiet',workspace],{env:gitEnv});
 execFileSync('git',['-C',workspace,'-c','core.hooksPath=/dev/null','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','--allow-empty','-m','Owned context fixture'],{env:gitEnv});
 execFileSync('git',['-C',workspace,'-c','core.hooksPath=/dev/null','worktree','add','--quiet','--detach',execution,'HEAD'],{env:gitEnv});
 await writeFile(join(web,'index.html'),'<title>Native context fixture</title>');await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');
 const audit=join(root,'provider-calls.log');await writeFile(bundle,JSON.stringify({bundle:{name:'context-clear-root-fixture',version:'1.0.0+amplifier-unified.snapshot.1'},session:{orchestrator:{module:'loop-live'},context:{module:'context-simple'}},providers:[{module:'provider-fixture',config:{fixtureModels:[{id:'fixture-model'}],selectedModelAudit:audit}}]}));
 const settings={home,appHome,bundle,adminWorkspaceRoots:[workspace,execution],startupTimeout:60};await writeFile(nativeConfig,JSON.stringify(settings));
 const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_RUNTIME_IMMUTABLE:'1',AMPLIFIER_SOURCE_STORE:join(root,'source-store'),AMPLIFIER_SESSION_STATE_HOME:join(root,'writer-state'),UV_CACHE_DIR:join(root,'uv-cache')};
 const config={account:ACCOUNT,stateDirectory:join(root,'host'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace,execution],engines:[{id:'native',command:nativePython,args:['-I','-B','-m','amplifier_acp','--config',nativeConfig],env}],nativeAdmin:{engine:'native'}};
 const open=defaultWorkspace=>createDistribution({...config,defaultWorkspace},{authorize:async req=>{assert.equal(req.headers['x-bundle-fixture-authority'],AUTHORITY);return {account:ACCOUNT};}});
 let app,client,hop,completed=false;const session='ahp-session:/'+randomUUID(),outer='actual-native-original-clear';
 try{
  app=await open(workspace);assert.ok(app.capabilities.manifest.actions['context.clear']);client=await peer(app.url);
  await client.request('createSession',{channel:session,provider:'native',workingDirectories:[pathToFileURL(workspace).href]});
  const original=await app.host.inspectSession(session);
  await app.host.nativeControl(session,'goals.set',{condition:'Original explicit fixture goal',maxTurns:2});
  const stale=(await client.action(session,'context.clear.review')).result;
  await app.host.nativeControl(session,'goals.set',{condition:'Changed explicit fixture goal',maxTurns:2});
  await assert.rejects(client.action(session,'context.clear',{expectedHistoryRevision:stale.historyRevision,expectedControlRevision:stale.controlRevision},'actual-stale-clear'),e=>e.data?.reason==='context-clear-stale-review'&&e.data.executed===false);
  const refused=(await client.action(session,'context.clear.receipt',{commandId:'actual-stale-clear'})).result;assert.equal(refused.receipt.status,'refused');
  const review=(await client.action(session,'context.clear.review')).result;assert.equal(review.canClear,true);assert.equal(review.goalPresent,true);
  client.close();client=null;hop=await losingHop(app.url,outer);client=await peer(hop.url,app.url);
  await assert.rejects(client.action(session,'context.clear',{expectedHistoryRevision:review.historyRevision,expectedControlRevision:review.controlRevision},outer),/Reply lost/);
  const dropped=await hop.dropped;assert.equal(dropped.error,undefined,JSON.stringify(dropped.error));assert.equal(dropped.result.result.goalCleared,true);
  client.close();client=null;await hop.close();hop=null;client=await peer(app.url);
  const inspect=async()=>(await client.action(session,'context.clear.receipt',{commandId:outer})).result;
  const succeeded=await inspect();assert.equal(succeeded.receipt.status,'succeeded');assert.deepEqual(succeeded.receipt.result,dropped.result.result);
  const archive=join(appHome,'sessions',original.nativeSessionId,'history-revisions','context-'+succeeded.receipt.result.archiveId,'transcript.jsonl'),beforeImage=await readFile(archive);
  const moved=await app.host.relocateSession(session,{commandId:'actual-context-relocate',target:execution,expectedExecutionRevision:original.executionRevision});assert.equal(moved.applied,true,JSON.stringify(moved));
  const relocated=await app.host.inspectSession(session);assert.equal(relocated.workingDirectory,workspace);assert.equal(relocated.nativeSessionId,original.nativeSessionId);assert.equal(relocated.executionDirectory,execution);
  client.close();client=null;await app.close();app=null;
  await writeFile(nativeConfig,JSON.stringify({...settings,workerCommand:['/impossible/passive-context-worker']}));
  app=await open(execution);client=await peer(app.url);assert.equal(app.host.diagnostics().activeAgents,0);
  assert.deepEqual(await inspect(),succeeded);assert.deepEqual((await client.action(session,'context.clear.receipt',{commandId:'actual-stale-clear'})).result,refused);assert.equal(app.host.diagnostics().activeAgents,0);
  assert.deepEqual(await readFile(archive),beforeImage);await assert.rejects(readFile(audit));
  if(process.env.UNIFIED_CONTEXT_CLEAR_ACCEPTANCE_RECEIPT)await writeFile(process.env.UNIFIED_CONTEXT_CLEAR_ACCEPTANCE_RECEIPT,JSON.stringify({composition:'createDistribution',nativePython,session,originalNativeSessionId:original.nativeSessionId,canonicalHistoryWorkspace:workspace,executionDirectory:execution,changedDefaultWorkspace:execution,actualOuterWebSocketReplyDropped:true,exactSuccessAndRefusalReceipts:true,workerCommandProhibitedOnColdRecovery:true,coldActiveAgents:0,providerCalls:0,replayed:false,archiveBeforeImageUnchanged:true},null,2)+'\n');
  completed=true;
 }finally{client?.close();await hop?.close();await app?.close();if(completed){const writable=async p=>{await chmod(p,0o700);for(const e of await readdir(p,{withFileTypes:true}))if(e.isDirectory())await writable(join(p,e.name));};await writable(root);await rm(root,{recursive:true,force:true});}else console.error('Owned actual-native context fixture retained without replay:',root);}
});
