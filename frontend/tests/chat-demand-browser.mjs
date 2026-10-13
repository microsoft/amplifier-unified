// Packaged UI, real HTTP/SSE and synthetic discovery; no disabled production effects.
import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),measureOnly=process.argv.includes('--measure-only');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/chat_demand_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,peerBrowser,log='';fixture.stderr.on('data',x=>log+=x);
const evidence={measureOnly};
try{
 const meta=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),60000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 const headers={Authorization:'Bearer fixture-demand-token','Content-Type':'application/json'};
 const api=async(path,body)=>{const r=await fetch(meta.url+path,{headers,...body===undefined?{}:{method:'POST',body:JSON.stringify(body)}});assert.equal(r.status,200,await r.clone().text());return r.json()};
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const context=await browser.newContext({viewport:{width:1400,height:1000},extraHTTPHeaders:{Authorization:headers.Authorization}});
 await context.addInitScript(()=>{const ES=EventSource;window.demandStreams=[];window.demandFrames=[];window.EventSource=class extends ES{constructor(...args){super(...args);if(String(args[0]).includes('/api/events')){demandStreams.push(this);for(const name of ['state','state-delta'])this.addEventListener(name,e=>demandFrames.push({name,bytes:new TextEncoder().encode(e.data).length}))}}}});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const pending=new Set();let lastRequest=performance.now();
 page.on('request',r=>{if(r.url().includes('/api/')&&!r.url().includes('/api/events')){pending.add(r);lastRequest=performance.now()}});
 const finished=r=>{if(pending.delete(r))lastRequest=performance.now()};
 page.on('requestfinished',finished);page.on('requestfailed',finished);
 await page.goto(meta.url);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const action=(name,args={})=>page.evaluate(([n,a])=>window.amplifier.dispatch(n,a),[name,args]);
 const settle=async()=>{await expect.poll(async()=>{const m=await api('/fixture/demand');return m.pending+m.catalogPending+pending.size+(performance.now()-lastRequest<500?1:0)},{timeout:30000}).toBe(0)};
 await settle();await action('view.update',{patch:{draft:'Source private draft'}});await settle();
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
 // Both the initial and earlier recovery windows retain identity despite deferral.
 const expandRecovery=async()=>{for(const summary of await page.locator('details.a-recovery-group:not([open]) > summary').all())await summary.click()};
 await expandRecovery();
 await expect(page.locator('[data-message-id="1-m-119"]')).toContainText('Historical worker 0');
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect(page.locator('[data-message-id="1-m-1"]')).toBeAttached();
 await expandRecovery();
 await expect(page.locator('[data-message-id="1-m-0"]')).toContainText('Historical worker 0');
 const focused=await api('/api/conversation/navigation?sessionId='+meta.ids[1]+'&messageId=1-m-0&window=true');
 if(!measureOnly)assert.equal(focused.messages.find(m=>m.id==='1-m-0').recoveryWorker.id,'old-0');
 // Terminal jobs win over stale session progress in either observation order.
 for(const terminal of ['completed','error','cancelled'])for(const reverse of [false,true]){
  await api('/fixture/activity',{terminal,reverse});
  await expect(page.getByText('1 worker lane is active',{exact:true})).toBeVisible();
  const current=(await snapshot()).sessions.find(s=>s.id===meta.ids[1]);
  assert.ok(current.workers.some(w=>w.id==='control-child'&&w.sessionId==='controlled-child'));
  assert.ok(current.workers.some(w=>w.id==='control-job'&&w.status===terminal));
 }
 await api('/fixture/activity',{});
 // Use actual desktop picker controls, preserving distinct connections.
 await page.getByRole('button',{name:'Model and reasoning settings'}).click();
 const model=page.locator('#chat-model');await expect(model.locator('option[value="second"]')).toBeAttached();
 evidence.openBytes=Buffer.byteLength(JSON.stringify(await snapshot()));
 await model.selectOption('second');await settle();
 await expect(model).toHaveValue('second');
 await page.locator('#chat-provider').selectOption('two');await settle();
 await expect(model.locator('option[value="other-account"]')).toBeAttached();
 assert.equal(await model.locator('option[value="second"]').count(),0);
 await page.getByRole('button',{name:'Close model settings'}).click();
 await expect(page.locator('.a-model-popover')).toHaveCount(0);
 // A different private client does not inherit this draft or picker demand.
 await action('view.update',{patch:{draft:'Target private draft'}});
 // Separate process also works on ARM containers requiring --single-process,
 // where creating a second incognito context can terminate Chromium.
 peerBrowser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const peer=await peerBrowser.newPage({extraHTTPHeaders:{Authorization:headers.Authorization}});
 await peer.goto(meta.url);await peer.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await expect(peer.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('');await peerBrowser.close();peerBrowser=null;
 // Reconnect uses a fresh full baseline without replay or draft loss.
 const streams=await page.evaluate(()=>demandStreams.length);
 await page.evaluate(()=>{const stream=demandStreams.at(-1);stream.close();stream.dispatchEvent(new Event('error'))});
 await expect.poll(()=>page.evaluate(()=>demandStreams.length),{timeout:10000}).toBeGreaterThan(streams);
 await page.waitForFunction(()=>demandStreams.at(-1).readyState===EventSource.OPEN);
 await expect(composer).toHaveDraft('Target private draft');
 await action('session.select',{id:meta.ids[0]});await expect(composer).toHaveDraft('Source private draft');
 await action('session.select',{id:meta.ids[1]});await expect(composer).toHaveDraft('Target private draft');
 // Existing-conversation mobile embedded picker must also declare/retire demand.
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Chat options',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Chat options'})).toBeVisible();
 await page.locator('#chat-provider').selectOption('one');await settle();
 await expect(page.locator('#chat-model option[value="second"]')).toBeAttached();
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Chat options'})).toHaveCount(0);
 await settle();const final=await api('/fixture/demand');assert.deepEqual(final.runtimeCalls,[]);assert.equal(final.workersUnchanged,true);assert.deepEqual(errors,[]);
 evidence.controls={olderRecovery:true,focusedRecovery:true,terminalPrecedenceBothOrders:true,desktopPicker:true,accountIsolation:true,privateDrafts:true,reconnect:true,mobilePicker:true,workerEvidenceUnchanged:true};evidence.pageErrors=errors;
 console.log(JSON.stringify(evidence,null,2));
}finally{
 await peerBrowser?.close();await browser?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await Promise.race([once(fixture,'exit'),new Promise((_,reject)=>setTimeout(()=>reject(Error('Fixture teardown timeout')),10000))])}
 evidence.fixtureExit=fixture.exitCode;if(process.env.AMPLIFIER_PERF_EVIDENCE)await writeFile(process.env.AMPLIFIER_PERF_EVIDENCE,JSON.stringify(evidence,null,2)+'\n');
}
