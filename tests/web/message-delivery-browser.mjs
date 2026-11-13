import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {existsSync,statSync} from 'node:fs';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const {base,stop}=await startFixture({
 command:fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),
 args:[fileURLToPath(new URL('../../tests/fixtures/message_delivery_ui_server.py',import.meta.url))],
 env:process.env,tag:'message-delivery-ui',name:'Message delivery',
});
let browser,originalError;
try{
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const inputActions=[];
 page.on('request',request=>{
  if(new URL(request.url()).pathname!=='/api/actions'||request.method()!=='POST')return;
  const action=request.postDataJSON()?.action;
  if(action==='conversation.send'||action==='conversation.retry')inputActions.push(action);
 });
 const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||(existsSync('/artifacts')&&statSync('/artifacts').isDirectory()?'/artifacts':null);
 if(artifacts)await mkdir(artifacts,{recursive:true});
 const screenshot=async name=>{if(artifacts)await page.screenshot({path:`${artifacts}/${name}.png`})};
 const isActionResponse=(response,action)=>{const request=response.request();return new URL(request.url()).pathname==='/api/actions'&&request.method()==='POST'&&request.postDataJSON()?.action===action};
 // Observe the real service's automatic passive check; do not suppress it or
 // replace its response merely to preserve the former manual-check sequence.
 const automaticCheck=page.waitForResponse(response=>isActionResponse(response,'conversation.delivery'));
 await page.goto(base);await showFixtureConversation(page);
 const original=page.locator('.a-user').filter({hasText:'Recover this synthetic message with its attachment.'});
 await expect(original).toHaveCount(1);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Preserve my next unsent thought');
 assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]').length),0,'Recovery works without a local outbox');
 const inspect=async()=>{
  const response=await page.request.get(base+'/fixture');
  assert.equal(response.ok(),true);return response.json();
 };
 const checked=await automaticCheck,checkRequest=checked.request().postDataJSON(),checkReceipt=await checked.json();
 const sessionId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const savedInput=async()=>page.evaluate(sessionId=>{
  const state=window.amplifier.getState();
  if(state.selectedSessionId!==sessionId)throw Error('Original fixture selection changed');
  return state.sessions.find(s=>s.id===sessionId)?.messages.find(m=>m.role==='user'&&m.inputId==='original-fixture-input');
 },sessionId);
 const initial=await savedInput();
 assert.equal(checkRequest.args.sessionId,sessionId);assert.equal(checkRequest.args.inputId,'original-fixture-input');
 assert.equal(checkReceipt.accepted,true);assert.equal(checkReceipt.result.sessionId,sessionId);assert.equal(checkReceipt.result.inputId,'original-fixture-input');assert.equal(checkReceipt.result.delivery,'unknown');
 await expect(original.getByText(/Delivery is still uncertain/)).toBeVisible();
 const passive=await inspect();assert.ok(passive.checked.includes('original-fixture-input'));assert.deepEqual(passive.sent,[]);assert.deepEqual(passive.retried,[]);
 await original.getByRole('button',{name:'Send again',exact:true}).click();
 await expect(original.getByText(/Sending again could repeat that work/)).toBeVisible();
 await original.getByRole('button',{name:'Cancel',exact:true}).click();
 assert.deepEqual((await inspect()).sent,[]);assert.deepEqual((await inspect()).retried,[]);
 await page.setViewportSize({width:390,height:844});
 await original.getByRole('button',{name:'Send again',exact:true}).click();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const confirmation=await original.locator('.a-delivery-confirm').boundingBox();
 const resendButton=await original.getByRole('button',{name:'Send this message again',exact:true}).boundingBox();
 assert.ok(confirmation.width>=200,'Phone confirmation remains readable');
 assert.ok(resendButton.width>=150&&resendButton.height<80,'Resend label must not collapse into a vertical column');
 await screenshot('message-delivery-confirmation-mobile');
 assert.deepEqual((await inspect()).sent,[]);assert.deepEqual((await inspect()).retried,[],'Opening confirmation alone never retries');
 const retryResponse=page.waitForResponse(response=>isActionResponse(response,'conversation.retry'));
 await original.getByRole('button',{name:'Send this message again',exact:true}).click();
 const retry=await retryResponse,retryRequest=retry.request().postDataJSON();
 assert.equal((await retry.json()).accepted,true);
 assert.equal(retryRequest.args.sessionId,sessionId);assert.equal(retryRequest.args.inputId,'original-fixture-input');assert.equal(retryRequest.args.confirmUncertain,true);
 await expect(page.getByText('Recovery succeeded: one synthetic submission received.',{exact:true})).toBeVisible();
 await expect(composer).toHaveDraft('Preserve my next unsent thought');
 await expect(original).toHaveCount(1);
 const retained=async()=>{
  await expect(original).toHaveCount(1);
  await expect(page.locator('.a-user')).toHaveCount(1);
  await expect(composer).toHaveDraft('Preserve my next unsent thought');
  const saved=await savedInput(),receipt=await inspect();
  assert.equal(saved.id,initial.id);assert.equal(saved.inputId,initial.inputId);assert.equal(saved.text,initial.text);
  assert.equal(saved.delivery.status,'accepted');assert.deepEqual(saved.attachments,initial.attachments);
  assert.equal(saved.attachments.length,1);assert.equal(saved.attachments[0].name,'reference.txt');
  assert.equal(receipt.sent.length,1);assert.deepEqual(receipt.retried,['original-fixture-input']);
  assert.equal(receipt.sent[0].inputId,'original-fixture-input');
  assert.deepEqual(receipt.accepted,{sessionId,inputId:'original-fixture-input'});
  assert.deepEqual(inputActions,['conversation.retry'],'Status events and reload must never resend');
  return saved;
 };
 const status=original.locator('.a-input-status'),duration=status.locator('.a-input-duration');
 const noEvidence={version:1,inputId:'original-fixture-input',phase:'unknown',basis:'no_evidence'};
 const received=async()=>{
  await expect(status).toHaveAttribute('data-input-phase','received');
  await expect(status.locator('summary > [aria-live="polite"][aria-atomic="true"]')).toHaveText('Received');
  await expect(duration).toHaveCount(0);
  assert.deepEqual((await retained()).inputStatus,noEvidence);
 };
 await received();
 const advance=async step=>{
  const response=await page.request.post(base+'/fixture/status',{data:{step}});
  assert.equal(response.status(),200,`Fixture status step ${step}`);
  const observed=await response.json();assert.equal(observed.step,step);assert.equal(observed.payload.sessionId,sessionId);
  return observed.payload;
 };
 // A custom fixture route must authenticate independently of public app routes.
 const denied=await page.request.post(base+'/fixture/status',{headers:{Authorization:''},data:{step:'ambient'}});
 assert.equal(denied.status(),403);
 await advance('ambient');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).status)).toBe('working');
 await received();
 const unrelated=await advance('unrelated');
 assert.notEqual(unrelated.initial_input_id,'original-fixture-input');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).collaborationGeneration?.id)).toBe(unrelated.generation_id);
 await received();
 const started=await advance('started');
 assert.equal(started.event,'generation.started');assert.equal(started.rootSessionId,sessionId);
 assert.equal(started.initial_input_id,'original-fixture-input');
 assert.equal(started.generation_id,'accepted-fixture-generation');
 assert.ok(Number.isFinite(started.observedAt)&&started.observedAt>0);
 const working={version:1,inputId:'original-fixture-input',generationId:started.generation_id,
  phase:'working',basis:'generation_started',startedAt:started.observedAt,updatedAt:started.observedAt};
 const workingObserved=async()=>{
  await expect(status).toHaveAttribute('data-input-phase','working');
  await expect(status.locator('summary > [aria-live="polite"][aria-atomic="true"]')).toHaveText('Working');
  await expect(duration).toBeVisible();await expect(duration).toHaveText(/ · \d+:\d{2}/);
  await expect.poll(async()=>(await savedInput())?.inputStatus).toEqual(working);
  const epoch=await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).interruptionRevision);
  assert.equal(started.interruptionRevision,epoch);
  await retained();
 };
 await workingObserved();
 await page.reload();await showFixtureConversation(page);
 await workingObserved(); // The host's exact start survives reload; it is not reset to arrival time.
 const finished=await advance('finished');
 assert.equal(finished.event,'generation.finished');assert.equal(finished.rootSessionId,sessionId);
 assert.equal(finished.generation_id,started.generation_id);
 assert.equal(finished.interruptionRevision,started.interruptionRevision);
 assert.deepEqual(finished.input_ids,['original-fixture-input']);assert.deepEqual(finished.active_job_ids,[]);
 assert.ok(finished.observedAt>=started.observedAt);
 const seconds=Math.floor(finished.observedAt-started.observedAt);
 const elapsed=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
 const done={...working,phase:'done',basis:'generation_finished',updatedAt:finished.observedAt,endedAt:finished.observedAt};
 const finishedObserved=async()=>{
  await expect(status).toHaveAttribute('data-input-phase','done');
  await expect(status.locator('summary > [aria-live="polite"][aria-atomic="true"]')).toHaveText('Turn finished');
  await expect(duration).toHaveText(` · ${elapsed}`);
  await expect(duration).toHaveAttribute('aria-label',`Observed elapsed time ${elapsed}`);
  await expect.poll(async()=>(await savedInput())?.inputStatus).toEqual(done);
  await retained();
 };
 await finishedObserved();
 await page.reload();await showFixtureConversation(page);
 await finishedObserved(); // A terminal duration remains fixed, not a newly running timer.
 assert.deepEqual((await inspect()).statusEvents.map(row=>row.step),['ambient','unrelated','started','finished']);
 await screenshot('message-delivery-input-status-finished-mobile');
 // Hold exactly the first send POST. The second rendered Send captures A and
 // waits in the ordinary send queue while real host navigation selects B.
 await page.setViewportSize({width:1280,height:900});
 const targets=await inspect();
 let releaseFirst,firstHeld;
 const held=new Promise(resolve=>firstHeld=resolve);
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='conversation.send'&&body.args.text==='Captured target first'){
   firstHeld(body);
   await new Promise(resolve=>releaseFirst=resolve);
  }
  await route.continue();
 });
 await composer.fill('Captured target first');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const firstSend=await held;
 await composer.fill('Captured target queued');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const queued=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).find(row=>row.text==='Captured target queued'));
 assert.ok(queued);assert.equal(queued.sessionId,sessionId);
 await page.evaluate(async id=>{
  const receipt=await window.amplifier.dispatch('session.select',{id});
  if(receipt.accepted!==true)throw Error('Fixture navigation refused');
 },targets.targetB);
 const selected=await page.evaluate(()=>window.amplifier.getState());
 assert.equal(selected.selectedSessionId,targets.targetB);
 assert.equal(selected.sessions.some(row=>row.id===sessionId),false,'Real selected-only frame omits captured A');
 releaseFirst();
 // Observe either the exact queued POST or its terminal local refusal, rather
 // than timing out while waiting only for the desired successful network path.
 let settlement;
 await expect.poll(async()=>{
  const sent=(await inspect()).capturedSends;
  const local=await page.evaluate(id=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).find(row=>row.id===id),queued.id);
  settlement={sent,local};
  return sent.some(row=>row.inputId===queued.commandId)?'posted':local?.status!=='sending'?'refused':'pending';
 },{timeout:5000,message:'Finite queued-send settlement after releasing first POST'}).not.toBe('pending');
 if(artifacts)await writeFile(`${artifacts}/captured-target-settlement.json`,JSON.stringify({
  targetA:sessionId,targetB:targets.targetB,firstSend,queued,selectedSessionId:selected.selectedSessionId,
  selectedFrameSessionIds:selected.sessions.map(row=>row.id),settlement,
 },null,2));
 await screenshot('message-delivery-captured-target-settlement');
 assert.ok(settlement.sent.some(row=>row.inputId===queued.commandId),
  `CAPTURED TARGET CAUSAL REGRESSION: selected-only omission refused queued A: ${JSON.stringify(settlement)}`);
 assert.deepEqual(settlement.sent.map(row=>row.sessionId),[sessionId,sessionId]);
 assert.equal(firstSend.args.sessionId,sessionId);
 await screenshot('message-delivery-captured-target-after-navigation');
 assert.deepEqual(errors,[]);
 console.log('Delivery recovery and actual-server input status passed: exact passive receipt, cancel/confirmation negatives, one confirmed retry, Received without timer despite ambient/unrelated work, exact observed Working/start across active reload, fixed Turn finished duration across reload, original input/attachment/draft, no resend and mobile bounds.');
}catch(error){originalError=error;throw error}
finally{await finishFixture({label:'Message delivery fixture',close:[['Browser',()=>browser?.close()]],stop,originalError})}
