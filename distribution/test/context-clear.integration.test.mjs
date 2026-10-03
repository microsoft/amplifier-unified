import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath} from 'node:fs/promises';
import {join} from 'node:path';import {tmpdir} from 'node:os';import {pathToFileURL} from 'node:url';
import {randomUUID,createHash} from 'node:crypto';import {createServer} from 'node:http';import {once} from 'node:events';
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
