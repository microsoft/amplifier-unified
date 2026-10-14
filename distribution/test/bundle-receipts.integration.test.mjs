import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,realpath,chmod,readdir} from 'node:fs/promises';
import {join} from 'node:path';import {tmpdir} from 'node:os';import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';import {execFileSync} from 'node:child_process';import {createServer} from 'node:http';import {once} from 'node:events';
import {WebSocket,WebSocketServer} from 'ws';
const {createDistribution}=await import(process.env.UNIFIED_DISTRIBUTION_ENTRY??'../src/index.js');
const native=process.env.UNIFIED_BUNDLE_PYTHON,legacy=process.env.UNIFIED_BUNDLE_LEGACY_PYTHON;
const ACCOUNT='owned-bundle-acceptance',AUTHORITY=randomUUID();

async function peer(url,origin=url){
 const socket=new WebSocket(url.replace(/^http/,'ws')+'/ahp',{origin,headers:{'x-bundle-fixture-authority':AUTHORITY}});await once(socket,'open');
 let sequence=0;const pending=new Map();
 socket.on('message',raw=>{const row=JSON.parse(raw),waiter=pending.get(row.id);if(!waiter)return;pending.delete(row.id);clearTimeout(waiter.timer);row.error?waiter.reject(Object.assign(Error(row.error.message),{code:row.error.code,data:row.error.data})):waiter.resolve(row.result);});
 socket.on('close',()=>{for(const waiter of pending.values()){clearTimeout(waiter.timer);waiter.reject(Error('Reply lost: connection closed'));}pending.clear();});
 const request=(method,params)=>new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error('Timed out: '+method));},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
 const initialized=await request('initialize',{channel:'ahp-root://',clientId:'bundle-receipt-owner',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
 return {request,initialized,action:(channel,operation,args={},commandId=randomUUID())=>request('x-amplifier/capabilityAction',{channel,topic:'bundles',operation,version:1,args,commandId}),close:()=>socket.terminate()};
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
async function fixture(python){
 const root=await realpath(await mkdtemp(join(tmpdir(),'unified-bundle-assembled-'))),workspace=join(root,'workspace'),execution=join(root,'execution'),home=join(root,'home'),appHome=join(root,'native-app'),web=join(root,'web'),state=join(root,'state');
 for(const p of [workspace,execution,home,appHome,web])await mkdir(p);
 const gitEnv={...process.env,GIT_CONFIG_GLOBAL:'/dev/null',GIT_CONFIG_NOSYSTEM:'1'};
 execFileSync('git',['-c','core.hooksPath=/dev/null','init','--quiet',workspace],{env:gitEnv});
 execFileSync('git',['-C',workspace,'-c','core.hooksPath=/dev/null','-c','user.name=Owned fixture','-c','user.email=fixture@example.invalid','commit','--quiet','--allow-empty','-m','Owned relocation fixture'],{env:gitEnv});
 execFileSync('git',['-C',workspace,'-c','core.hooksPath=/dev/null','worktree','add','--quiet','--detach',execution,'HEAD'],{env:gitEnv});await writeFile(join(web,'index.html'),'<!doctype html><title>Owned bundle receipt test</title>');
 const bundle=join(root,'fixture.yaml'),nativeConfig=join(root,'native.json'),audit=join(root,'provider-calls.log');
 await writeFile(bundle,JSON.stringify({bundle:{name:'assembled-receipt-fixture',version:'1.0.0+amplifier-unified.snapshot.1'},session:{orchestrator:{module:'loop-live'},context:{module:'context-simple'}},providers:[{module:'provider-fixture',config:{fixtureModels:[{id:'fixture-model'},{id:'fixture-alternate'}],selectedModelAudit:audit}}]}));
 await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');
 const nativeOptions={home,appHome,bundle,adminWorkspaceRoots:[workspace,execution],startupTimeout:60};await writeFile(nativeConfig,JSON.stringify(nativeOptions));
 const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_RUNTIME_IMMUTABLE:'1',AMPLIFIER_SOURCE_STORE:join(root,'source-store'),AMPLIFIER_SESSION_STATE_HOME:join(root,'writer-state'),UV_CACHE_DIR:join(root,'uv-cache')};
 const engine=id=>({id,command:python,args:['-I','-B','-m','amplifier_acp','--config',nativeConfig],env});
 const config={account:ACCOUNT,stateDirectory:state,webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace,execution],engines:[engine('amplifier'),engine('foreign')],nativeAdmin:{engine:'amplifier'}};
 let authorizations=0;
 const open=(defaultWorkspace=workspace)=>createDistribution({...config,defaultWorkspace},{authorize:async req=>{assert.equal(req.headers['x-bundle-fixture-authority'],AUTHORITY);authorizations++;return {account:ACCOUNT};}});
 const cold=()=>writeFile(nativeConfig,JSON.stringify({...nativeOptions,workerCommand:['/impossible/passive-bundle-receipt-worker']}));
 return {root,workspace,execution,home,appHome,audit,config,open,cold,authorizations:()=>authorizations};
}
async function cleanup(root){const writable=async p=>{await chmod(p,0o700);for(const e of await readdir(p,{withFileTypes:true}))if(e.isDirectory())await writable(join(p,e.name));};await writable(root);await rm(root,{recursive:true,force:true});}

 test('actual root factory preserves original native receipts after lost outer WebSocket reply, relocation and cold host/native restart',
 {skip:!native,timeout:120000},async()=>{
  const f=await fixture(native);let app,client,hop,completed=false;const session='ahp-session:/'+randomUUID(),foreign='ahp-session:/'+randomUUID(),outer='assembled-lost-bundle-save';
  try{
   app=await f.open();assert.ok(app.capabilities.manifest.actions['bundle.receipt'],'real peer negotiation must precede synchronous manifest composition');
   client=await peer(app.url);assert.ok(client.initialized._meta['amplifier.dev/capabilities'].actions['bundle.receipt']);
   await client.request('createSession',{channel:session,provider:'amplifier',workingDirectories:[pathToFileURL(f.workspace).href]});
   await client.request('createSession',{channel:foreign,provider:'foreign',workingDirectories:[pathToFileURL(f.workspace).href]});
   client.close();client=null;
   hop=await losingHop(app.url,outer);client=await peer(hop.url,app.url);
   await assert.rejects(client.action(session,'bundle.save',{sessionId:session,name:'assembled-saved'},outer),/Reply lost/);
   const original=await hop.dropped;assert.equal(original.error,undefined,JSON.stringify(original.error));assert.equal(original.result.accepted,true);
   const {activation,...saved}=original.result.result;assert.equal(activation.applied,true);const savedUri=saved.saved.uri,bytes=await readFile(savedUri);
   client.close();client=null;await hop.close();hop=null;
   client=await peer(app.url);
   const inspect=async target=>(await client.action(target,'bundle.receipt',{operation:'bundle.save',commandId:outer})).result;
   const exact=await inspect(session);assert.deepEqual(exact.receipts.command.result,saved);assert.deepEqual(exact.receipts.activation.result,activation);assert.equal(exact.receipts.activation.replayed,false);
   const defaultOuter='original-host-workspace-default',defaultResult=(await client.action('ahp-root://','bundle.default',{scope:'workspace',bundle:'assembled-saved'},defaultOuter)).result;
   const originalDefault=(await client.action('ahp-root://','bundle.receipt',{operation:'bundle.default',commandId:defaultOuter})).result;assert.deepEqual(originalDefault.receipts.command.result,defaultResult);
   const location=await app.host.inspectSession(session);assert.equal(location.engineId,'amplifier');assert.ok(location.nativeSessionId);assert.equal(location.workingDirectory,f.workspace);
   const relocated=await app.host.relocateSession(session,{commandId:'owned-relocation',target:f.execution,expectedExecutionRevision:location.executionRevision});assert.equal(relocated.applied,true,JSON.stringify(relocated));
   const moved=await app.host.inspectSession(session);assert.equal(moved.workingDirectory,f.workspace);assert.equal(moved.executionDirectory,f.execution);assert.equal(moved.nativeSessionId,location.nativeSessionId);
   await f.cold();
   const before=app.host.diagnostics().activeAgents;const movedReceipt=await inspect(session);assert.deepEqual(movedReceipt,exact);assert.equal(app.host.diagnostics().activeAgents,before);
   client.close();client=null;await app.close();app=null;
   // Change the current default and prohibit worker creation. Host records and
   // receipts must still use the original admitted canonical history workspace.
   app=await f.open(f.execution);assert.equal(app.host.diagnostics().activeAgents,0);client=await peer(app.url);
   const restored=await inspect(session);assert.deepEqual(restored,exact);assert.equal(app.host.diagnostics().activeAgents,0);assert.deepEqual(await readFile(savedUri),bytes);
   await assert.rejects(client.action(foreign,'bundle.receipt',{operation:'bundle.save',commandId:outer}),/admitted native engine/);
   assert.equal(app.host.diagnostics().activeAgents,0);
   const absent=(await client.action('ahp-root://','bundle.receipt',{operation:'bundle.default',commandId:defaultOuter})).result;
   assert.equal(absent.receipts.command,null);assert.equal(app.host.diagnostics().activeAgents,0);
   await assert.rejects(readFile(f.audit));assert.ok(f.authorizations()>0);
   const proof={schema:'assembled-native-bundle-receipt-v1',factory:'createDistribution',entry:process.env.UNIFIED_DISTRIBUTION_ENTRY??'source',nativePython:native,outerReplyDroppedByActualWebSocketHop:true,outerCommand:outer,canonicalNativeIdentity:location.nativeSessionId,originalWorkspace:location.workingDirectory,relocatedExecutionWorkspace:moved.executionDirectory,originalCanonicalAuthorityPreserved:true,actualHostNativeAdminRestart:true,changedCurrentDefault:true,exactSaveAndActivationResults:true,foreignConfiguredEngineRefused:true,activeAgentsOnColdRecovery:app.host.diagnostics().activeAgents,workerCommandProhibitedOnColdRecovery:true,providerCalls:0,replayed:false,authenticatedGateway:true,originalHostDefaultReceiptPresentBeforeScopeChange:true,originalHostDefaultReceiptAfterScopeChange:null,hostDefaultRecoveryWithoutOriginalScope:'unqualified/null is not outcome proof'};
   if(process.env.UNIFIED_BUNDLE_ACCEPTANCE_RECEIPT)await writeFile(process.env.UNIFIED_BUNDLE_ACCEPTANCE_RECEIPT,JSON.stringify(proof,null,2)+'\n');
   completed=true;
  }finally{client?.close();await hop?.close();await app?.close();if(completed)await cleanup(f.root);else console.error('Preserved owned bundle assembly:',f.root);}
 });

 test('actual root factory with older native peer leaves bundle receipt action unadvertised and refuses before a worker',
 {skip:!legacy,timeout:30000},async()=>{
  const f=await fixture(legacy);let app,client,completed=false;
  try{
   await f.cold();app=await f.open();client=await peer(app.url);
   assert.equal(app.capabilities.manifest.actions['bundle.receipt'],undefined);assert.equal(client.initialized._meta['amplifier.dev/capabilities'].actions['bundle.receipt'],undefined);
   await assert.rejects(client.action('ahp-root://','bundle.receipt',{operation:'bundle.default',commandId:'original'}),/not advertised/);
   assert.equal(app.host.diagnostics().activeAgents,0);await assert.rejects(readFile(f.audit));completed=true;
  }finally{client?.close();await app?.close();if(completed)await cleanup(f.root);else console.error('Preserved owned legacy assembly:',f.root);}
 });

test('unavailable optional native administration cannot prevent healthy engine history and explicit host reconnect',
 {skip:!native,timeout:30000},async()=>{
  const f=await fixture(native);let app,client,completed=false;const session='ahp-session:/'+randomUUID();
  const boundedOpen=async()=>{let timer;try{return await Promise.race([f.open(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Optional admin negotiation blocked the unrelated host')),4000);})]);}finally{if(timer)clearTimeout(timer);}};
  try{
   f.config.engines[0].command='/impossible/unavailable-native-admin';
   app=await boundedOpen();client=await peer(app.url);assert.equal(app.capabilities.manifest.actions['bundle.receipt'],undefined);await app.capabilities.getActionSchemas();
   await client.request('createSession',{channel:session,provider:'foreign',workingDirectories:[pathToFileURL(f.workspace).href]});
   const before=await app.host.inspectSession(session);assert.equal(before.engineId,'foreign');assert.ok(before.nativeSessionId);
   const healthy=await client.request('x-amplifier/capabilityAction',{channel:session,topic:'runtime-control',operation:'runtime.control',version:1,args:{operation:'goals.get'},commandId:'healthy-other-engine-goals'});assert.equal(healthy.accepted,true);
   const availability=await client.request('resourceRead',{channel:'ahp-root://',uri:'amplifier-capability://native/bundles?scope=host',encoding:'utf-8'});assert.deepEqual(JSON.parse(availability.data).data.nativeAdministration,{state:'unavailable',reason:'native-admin-negotiation-unavailable'});
   await client.request('subscribe',{channel:session.replace('ahp-session:','ahp-chat:'),view:{turns:1}});
   client.close();client=null;await app.close();app=null;await f.cold();
   app=await boundedOpen();client=await peer(app.url);assert.equal((await app.host.inspectSession(session)).nativeSessionId,before.nativeSessionId);
   await client.request('subscribe',{channel:session.replace('ahp-session:','ahp-chat:'),view:{turns:1}});assert.equal(app.host.diagnostics().activeAgents,0);
   client.close();client=null;await app.close();app=null;
   // Explicitly recreate the host after restoring the configured peer. This
   // renegotiates passive metadata, not an uncertain effect or dynamic catalog.
   f.config.engines[0].command=native;app=await boundedOpen();client=await peer(app.url);assert.ok(app.capabilities.manifest.actions['bundle.receipt']);
   assert.equal((await app.host.inspectSession(session)).nativeSessionId,before.nativeSessionId);assert.equal(app.host.diagnostics().activeAgents,0);await assert.rejects(readFile(f.audit));
   if(process.env.UNIFIED_BUNDLE_AVAILABILITY_RECEIPT)await writeFile(process.env.UNIFIED_BUNDLE_AVAILABILITY_RECEIPT,JSON.stringify({schema:'optional-native-admin-availability-v1',entry:process.env.UNIFIED_DISTRIBUTION_ENTRY??'source',nativePython:native,unavailableAdminDoesNotBlockHost:true,healthyOtherEngineControls:true,actionSchemasReadable:true,originalNativeHistoryIdentityRetained:true,unavailableProjectionExplicit:true,catalogFabricated:false,receiptAdvertisementStableUntilExplicitFactoryReconnect:true,restoredPeerRenegotiated:true,activeAgentsOnColdHistoryRead:0,providerCalls:0,effectsRetried:false},null,2)+'\n');completed=true;
  }finally{client?.close();await app?.close();if(completed)await cleanup(f.root);else console.error('Preserved optional-admin fixture:',f.root);}
});
