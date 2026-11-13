import {showFixtureConversation} from './browser-chat-pages.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
// Packaged UI, real HTTP/state stream and synthetic discovery; no disabled effects.
import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),measureOnly=process.argv.includes('--measure-only');
const expectedTransport=process.argv.includes('--transport=sse')?'sse':'websocket';
// CS4 forbids replaying work, not refreshing appearance preferences. Keep the
// complete ledger prefix, then allow only this fixture's exact passive refresh.
// A ready report can mutate a mirror: its entire payload must match the confirmed
// pre-disconnect profile, with no storage command or changed composition.
function assertReconnectTraffic(before,after,appearance,client){
 assert.deepEqual(after.slice(0,before.length),before,'Reconnect changed the original command ledger');
 const added=after.slice(before.length);
 assert.deepEqual(added.map(row=>row.action),['appearance.preferences.read','appearance.preferences.read','appearance.client.report'],'Reconnect must not replay actions or add unknown traffic');
 assert.equal(appearance.clientId,client);assert.equal(appearance.availability,'connected');
 assert.equal(appearance.local.reported,true);assert.equal(appearance.local.error,null);
 assert.equal(appearance.command,undefined);assert.equal(appearance.preview,null);
 const ids=new Set(before.map(row=>row.request.id));
 for(const row of added){
  const request=row.request;
  assert.deepEqual(Object.keys(request).sort(),['action','args','id']);
  assert.equal(request.action,row.action);assert.equal(row.clientId,client);
  assert.equal(typeof request.id,'string');assert.ok(request.id&&!ids.has(request.id),'Reconnect reused a command identity');ids.add(request.id);
  assert.equal(row.status,200);assert.equal(row.accepted,true);assert.deepEqual(row.effects,[]);
  if(row.action==='appearance.preferences.read'){
   assert.deepEqual(request.args,{});
   for(const key of ['namespace','generation','shared','activeScope','local','effective','composition','command','preview'])
    assert.deepEqual(row.result[key],appearance[key],`Preference refresh changed ${key}`);
  }else{
   assert.deepEqual(request.args,{generation:appearance.generation,revision:appearance.local.revision,
    storageVersion:appearance.local.storageVersion,activeScope:appearance.activeScope,values:appearance.local.values,status:'ready'});
   assert.deepEqual(row.result,{status:'reported',stored:false,activated:false});
  }
 }
 return added;
}
function reconnectNegativeControls(before,after,appearance,client){
 const rejects=mutate=>{const changed=structuredClone(after);mutate(changed);assert.throws(()=>assertReconnectTraffic(before,changed,appearance,client),{name:'AssertionError'})};
 // Replays with original OR fresh identities still fail, even if they would be
 // accepted no-ops. These are oracle-only mutations, never real device commands.
 for(const action of ['shell.command','conversation.send','device.command','view.update','appearance.preferences.update','unknown.mutation'])
  for(const id of [before[0].request.id,'fresh-effect-command'])
   rejects(rows=>{rows.at(-1).action=action;rows.at(-1).request={action,args:{},id}});
 for(const patch of [{status:'stored'},{commandId:'storage-write'},{error:'failed'},
  {revision:appearance.local.revision+1},{generation:'other-generation'},
  {storageVersion:'other-version'},{activeScope:'client'},{values:{}},{renderedCompositionId:'other-composition'},{unknown:true}])
  rejects(rows=>Object.assign(rows.at(-1).request.args,patch));
 for(const mutate of [
  rows=>{rows[0].request.id='rewritten-original'},
  rows=>{rows.at(-1).request.id=before[0].request.id},
  rows=>{rows.at(-1).clientId='other-client'},
  rows=>{rows.at(-1).status=500},
  rows=>{rows.at(-1).accepted=false},
  rows=>{rows.at(-1).effects=[{type:'clipboard.write',content:'synthetic'}]},
  rows=>{rows.at(-1).result.stored=true},
  rows=>{rows[before.length].request.args={scope:'shared'}},
  rows=>{rows[before.length].result.shared.revision++},
  rows=>{rows.pop()},
  rows=>{rows.push(structuredClone(rows.at(-1)))},
 ])rejects(mutate);
 return 33;
}
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/chat_demand_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,peerBrowser,page,primaryError,log='';const transportFailures=[];
fixture.stderr.on('data',x=>{log=(log+x).slice(-16384)});
const noteFailure=value=>{transportFailures.push(value);if(transportFailures.length>20)transportFailures.shift()};
const evidence={measureOnly,expectedTransport};
try{
 const meta=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),60000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 const headers={Authorization:'Bearer fixture-demand-token','Content-Type':'application/json'};
 const api=async(path,body)=>{const r=await fetch(meta.url+path,{headers,...body===undefined?{}:{method:'POST',body:JSON.stringify(body)}});assert.equal(r.status,200,await r.clone().text());return r.json()};
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const context=await browser.newContext({viewport:{width:1400,height:1000},extraHTTPHeaders:{Authorization:headers.Authorization}});
 await context.addInitScript(()=>{
  window.demandStreams=[];window.demandFrames=[];
  const record=(stream,transport,url)=>{stream.demandIdentity={streamId:demandStreams.length,transport,path:new URL(url,location.href).pathname};demandStreams.push(stream)};
  const ES=EventSource;window.EventSource=class extends ES{constructor(...args){super(...args);if(String(args[0]).includes('/api/events')){record(this,'sse',args[0]);for(const name of ['state','state-delta'])this.addEventListener(name,e=>demandFrames.push({...this.demandIdentity,name,revision:JSON.parse(e.data).revision,bytes:new TextEncoder().encode(e.data).length}))}}};
  const WS=WebSocket;window.WebSocket=class extends WS{constructor(...args){super(...args);if(String(args[0]).includes('/api/events')){record(this,'websocket',args[0]);this.addEventListener('message',e=>{const frame=JSON.parse(e.data);if(['state','state-delta'].includes(frame.event))demandFrames.push({...this.demandIdentity,name:frame.event,revision:frame.data?.revision,bytes:new TextEncoder().encode(e.data).length})})}}};
 });
 page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('requestfailed',r=>noteFailure({path:new URL(r.url()).pathname,error:r.failure()?.errorText}));
 page.on('response',r=>{if(r.status()>=400)noteFailure({path:new URL(r.url()).pathname,status:r.status()})});
 const pending=new Set();let lastRequest=performance.now();
 page.on('request',r=>{if(r.url().includes('/api/')&&!r.url().includes('/api/events')){pending.add(r);lastRequest=performance.now()}});
 const finished=r=>{if(pending.delete(r))lastRequest=performance.now()};
 page.on('requestfinished',finished);page.on('requestfailed',finished);
 await page.goto(meta.url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const action=(name,args={})=>page.evaluate(([n,a])=>window.amplifier.dispatch(n,a),[name,args]);
 const settle=async()=>{await expect.poll(async()=>{const m=await api('/fixture/demand');return m.pending+m.catalogPending+pending.size+(performance.now()-lastRequest<500?1:0)},{timeout:30000}).toBe(0)};
 await settle();
 await page.waitForFunction(transport=>demandFrames.some(f=>f.name==='state'&&f.transport===transport),expectedTransport);
 evidence.initialTransport=await page.evaluate(()=>demandStreams.at(-1).demandIdentity);
 assert.equal(evidence.initialTransport.path,expectedTransport==='websocket'?'/api/events/ws':'/api/events');
 await action('view.update',{patch:{draft:'Source private draft'}});await settle();
 const client=await page.evaluate(()=>window.amplifier.shellClientId);
 const snapshot=()=>api('/api/state?clientId='+client);
 evidence.initialBytes=Buffer.byteLength(JSON.stringify(await snapshot()));
 await api('/fixture/reset',{});await page.evaluate(()=>demandFrames.length=0);
 const start=performance.now();await page.getByRole('button',{name:'Demand target',exact:true}).click();
 await expect(page.locator('.a-messages')).toHaveAttribute('aria-busy','false');
 await expect(page.locator('[data-message-id="1-m-118"]')).toBeAttached();
 evidence.switchReadyMs=performance.now()-start;
 await settle();evidence.navigation=await api('/fixture/demand');evidence.frames=await page.evaluate(()=>demandFrames);
 const state=await snapshot(),target=state.sessions.find(s=>s.id===meta.ids[1]);
 evidence.closedBytes=Buffer.byteLength(JSON.stringify(state));evidence.closedCatalogBytes=Buffer.byteLength(JSON.stringify(state.modelCatalogs));evidence.workerBytes=Buffer.byteLength(JSON.stringify(target.workers));evidence.workerRows=target.workers.length;
 if(!measureOnly){
  assert.equal(evidence.navigation.actions.filter(a=>a.action==='providers.list'||a.operation==='configuration.catalog').length,0);
  assert.ok(target.workerSummary.bounded);assert.equal(target.workerSummary.total,1000);assert.equal(target.workers.length,0);
  assert.ok(!JSON.stringify(state.modelCatalogs).includes('historical-'));
  assert.ok(Object.values(state.modelCatalogs).every(row=>row.modelsDeferred&&!row.models));
 }
 await expect(composer).toHaveDraft('');
 // Historical recovery notices stay out of the transcript. The retained
 // result is still available on demand from the ordinary Work details.
 await expect(page.locator('.a-recovery-group')).toHaveCount(0);
 await expect(page.locator('[data-message-id="1-m-119"]')).toHaveCount(0);
 const work=page.locator('[data-group-id="turn@1-m-118"]');
 await work.locator('button.a-execution-turn-line').click();
 const result=work.locator('[data-node-id="node"]');
 const savedResult=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/api/conversation/detail'&&url.searchParams.get('field')==='output'&&response.ok()});
 await result.locator('button.a-execution-action-line').click();await savedResult;
 await result.getByRole('button',{name:'Show all 21,023 characters',exact:true}).click();
 await expect(result).toContainText('End of retained result.');
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect(page.locator('[data-message-id="1-m-1"]')).toBeAttached();
 await expect(page.locator('[data-message-id="1-m-0"]')).toHaveCount(0);
 await expect(page.locator('.a-recovery-group')).toHaveCount(0);
 const focused=await api('/api/conversation/navigation?sessionId='+meta.ids[1]+'&messageId=1-m-0&window=true');
 if(!measureOnly)assert.equal(focused.messages.find(m=>m.id==='1-m-0').recoveryWorker.id,'old-0');
 await page.getByRole('button',{name:'Return to latest',exact:true}).click();
 // Terminal jobs win over stale session progress in either observation order.
 for(const terminal of ['completed','error','cancelled'])for(const reverse of [false,true]){
  const changed=await api('/fixture/activity',{terminal,reverse});
  await page.waitForFunction(revision=>window.amplifier.getState().revision>=revision,changed.revision);
  await expect(page.getByText('1 worker lane is active',{exact:true})).toBeVisible();
  const received=await page.evaluate(id=>window.amplifier.getState().sessions.find(s=>s.id===id).workers,meta.ids[1]);
  assert.equal(received.find(w=>w.id==='control-job').status,terminal);
  const current=(await snapshot()).sessions.find(s=>s.id===meta.ids[1]);
  assert.ok(current.workers.some(w=>w.id==='control-child'&&w.sessionId==='controlled-child'));
  assert.ok(current.workers.some(w=>w.id==='control-job'&&w.status===terminal));
 }
 await api('/fixture/activity',{});
 // Use actual desktop picker controls, preserving distinct connections.
 await page.getByRole('button',{name:'Model and reasoning settings'}).click();
 await showComposerOption(page,'model');
 const model=page.locator('#chat-model');await expect(model.locator('option[value="second"]')).toBeAttached();
 evidence.openBytes=Buffer.byteLength(JSON.stringify(await snapshot()));
 await model.selectOption('second');await settle();
 await expect(model).toHaveValue('second');
 const configurationMetrics=await api('/fixture/demand'),configured=configurationMetrics.selections[meta.ids[1]];
 assert.equal(configured.selection.instance,'one');assert.equal(configured.selection.model,'second');
 assert.equal(configured.modelConnectionKey,configurationMetrics.connectionKeys[meta.ids[1]].one);
 assert.ok(configured.modelConnectionKey);assert.equal(configured.nextMessageStatus,'saved');
 assert.deepEqual(configured.pendingModelSelection,configured.selection);
 evidence.savedSelection=configured;
 await showComposerOption(page,'provider');
 await page.locator('#chat-provider').selectOption('two');await settle();
 await expect(model.locator('option[value="other-account"]')).toBeAttached();
 assert.equal(await model.locator('option[value="second"]').count(),0);
 await page.getByRole('button',{name:'Close chat options'}).click();
 await expect(page.getByRole('dialog',{name:'Model for this chat',exact:true})).toHaveCount(0);
 // A different private client does not inherit this draft or picker demand.
 await action('view.update',{patch:{draft:'Target private draft'}});
 // Separate process also works on ARM containers requiring --single-process,
 // where creating a second incognito context can terminate Chromium.
 peerBrowser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const peer=await peerBrowser.newPage({extraHTTPHeaders:{Authorization:headers.Authorization}});
 await peer.goto(meta.url);await showFixtureConversation(peer);await peer.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await expect(peer.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('');await peerBrowser.close();peerBrowser=null;
 // Reconnect uses a fresh full baseline without replay or draft loss.
 await settle();const beforeReconnect=await api('/fixture/demand');
 const reconnectState=await snapshot(),appearance=reconnectState.appearance;
 const appearanceStorage=()=>page.evaluate(namespace=>localStorage.getItem(namespace),appearance.namespace);
 const beforeStorage=await appearanceStorage();
 const reconnectStart=await page.evaluate(()=>({streams:demandStreams.length,frames:demandFrames.length,streamId:demandStreams.at(-1).demandIdentity.streamId}));
 await page.evaluate(()=>{const stream=demandStreams.at(-1);stream.close();if(stream instanceof EventSource)stream.dispatchEvent(new Event('error'))});
 await expect.poll(()=>page.evaluate(()=>demandStreams.length),{timeout:10000}).toBeGreaterThan(reconnectStart.streams);
 await page.waitForFunction(()=>demandStreams.at(-1).readyState===1);
 await page.waitForFunction(({start,transport})=>demandFrames.slice(start.frames).some(f=>f.name==='state'&&f.streamId>start.streamId&&f.transport===transport),{start:reconnectStart,transport:expectedTransport});
 const fresh=await page.evaluate(start=>demandFrames.slice(start.frames).find(f=>f.name==='state'&&f.streamId>start.streamId),reconnectStart);
 await page.waitForFunction(revision=>window.amplifier.getState().revision>=revision,fresh.revision);
 evidence.reconnectFrameBytes=fresh.bytes;evidence.reconnectBaseline=fresh;
 await settle();const afterReconnect=await api('/fixture/demand');
 evidence.reconnectTraffic=assertReconnectTraffic(beforeReconnect.actions,afterReconnect.actions,appearance,client);
 evidence.reconnectNegativeControls=reconnectNegativeControls(beforeReconnect.actions,afterReconnect.actions,appearance,client);
 const afterState=await snapshot();
 assert.equal(afterState.selectedSessionId,reconnectState.selectedSessionId);
 assert.deepEqual(afterState.appearance,appearance,'Reconnect changed confirmed appearance state');
 assert.equal(await appearanceStorage(),beforeStorage,'Reconnect wrote browser preferences');
 assert.deepEqual(afterReconnect.selections,beforeReconnect.selections,'Reconnect changed saved model settings');
 assert.deepEqual(transportFailures,[],'HTTP/transport errors must not hide behind reconnect success');
 await expect(composer).toHaveDraft('Target private draft');
 await action('session.select',{id:meta.ids[0]});await expect(composer).toHaveDraft('Source private draft');
 await action('session.select',{id:meta.ids[1]});await expect(composer).toHaveDraft('Target private draft');
 // Existing-conversation mobile embedded picker must also declare/retire demand.
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Model for this chat',exact:true})).toBeVisible();
 await showComposerOption(page,'provider');
 await page.locator('#chat-provider').selectOption('one');await settle();
 await expect(page.locator('#chat-model option[value="second"]')).toBeAttached();
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Model for this chat',exact:true})).toHaveCount(0);
 if(!measureOnly){await expect.poll(async()=>(await snapshot()).view.composerModel?.open).toBe(false)}
 await settle();const final=await api('/fixture/demand');assert.deepEqual(final.runtimeCalls,[]);assert.equal(final.workersUnchanged,true);assert.deepEqual(errors,[]);assert.deepEqual(transportFailures,[]);
 assert.ok(final.actions.every(row=>row.status===200&&row.accepted===true),'Every action must have a successful admitted receipt');
 evidence.finalActions=final.actions;evidence.runtimeCalls=final.runtimeCalls;evidence.transportFailures=transportFailures;
 if(process.env.AMPLIFIER_PERF_EVIDENCE)await page.screenshot({path:process.env.AMPLIFIER_PERF_EVIDENCE+'.png'});
 evidence.controls={historicalNoticesHidden:true,focusedRecoveryEvidence:true,savedWorkResult:true,terminalPrecedenceBothOrders:true,desktopPicker:true,accountIsolation:true,privateDrafts:true,reconnect:true,mobilePicker:true,workerEvidenceUnchanged:true};evidence.pageErrors=errors;
 console.log(JSON.stringify(evidence,null,2));
}catch(error){
 primaryError=error;
 evidence.failure={name:error.name,message:String(error.message).slice(0,4000),stack:String(error.stack).slice(0,6000),transportFailures};
 if(page){
  if(process.env.AMPLIFIER_PERF_EVIDENCE)try{await page.screenshot({path:process.env.AMPLIFIER_PERF_EVIDENCE+'.png',timeout:2000})}catch{}
  let timer;
  try{evidence.failure.browser=await Promise.race([page.evaluate(()=>{
   const state=window.amplifier?.getState?.();
   return {url:location.pathname,readyState:document.readyState,clientId:state?.client?.id,
    revision:state?.revision,selectedSessionId:state?.selectedSessionId,
    visibleSessions:state?.sessions?.length,body:document.body?.innerText.slice(0,3000)};
  }),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Browser diagnostic timeout')),2000)})])}
  catch(diagnosticError){evidence.failure.browserError=String(diagnosticError.message).slice(0,1000)}
  finally{clearTimeout(timer)}
 }
 throw error;
}finally{
 const cleanupErrors=[];
 for(const owned of [peerBrowser,browser])try{await owned?.close()}catch(error){cleanupErrors.push(String(error.message).slice(0,1000))}
 if(fixture.exitCode===null){
  let timer;
  try{fixture.kill('SIGTERM');await Promise.race([once(fixture,'exit'),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture teardown timeout')),10000)})])}
  catch(error){
   cleanupErrors.push(String(error.message).slice(0,1000));
   // The fixture is this test's child. Reap it even when graceful teardown fails.
   if(fixture.exitCode===null&&fixture.signalCode===null){
    clearTimeout(timer);
    try{const stopped=once(fixture,'exit');fixture.kill('SIGKILL');await Promise.race([stopped,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture forced teardown timeout')),2000)})])}
    catch(forcedError){cleanupErrors.push(String(forcedError.message).slice(0,1000))}
   }
  }
  finally{clearTimeout(timer)}
 }
 evidence.fixtureExit=fixture.exitCode;
 if(primaryError){evidence.failure.fixtureStderr=log;evidence.failure.cleanupErrors=cleanupErrors}
 if(process.env.AMPLIFIER_PERF_EVIDENCE)try{await writeFile(process.env.AMPLIFIER_PERF_EVIDENCE,JSON.stringify(evidence,null,2)+'\n')}catch(error){cleanupErrors.push(String(error.message).slice(0,1000))}
 if(primaryError)console.error(JSON.stringify({chatDemandFailure:evidence.failure}));
 if(cleanupErrors.length&&!primaryError)throw Error('Chat demand cleanup failed: '+cleanupErrors.join('; '));
}
