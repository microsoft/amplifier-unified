import {showFixtureConversation} from './browser-chat-pages.mjs';
// Real host direct-start/review receipts and packaged UI; recipient is synthetic.
import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';

const output=process.env.AMPLIFIER_TEST_ARTIFACTS||await mkdtemp(join(tmpdir(),'unified-handoff786-'));
await mkdir(output,{recursive:true});
const pythonArgs=process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE?['-I','-B','-u']:['-u'];
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[...pythonArgs,fileURLToPath(new URL('../../tests/fixtures/handoff786_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','pipe']});
const started=Date.now(),errors=[],actions=[],observations={},timings={};
let log='',browser,otherBrowser,page,phase='fixture-start';
fixture.stderr.on('data',chunk=>log+=chunk);
const state=page=>page.evaluate(()=>window.amplifier.getState());
const action=(page,name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
try{
 const info=await new Promise((resolve,reject)=>{
  let data='';const timer=setTimeout(()=>reject(Error('Fixture timeout: '+log)),30000);
  fixture.stdout.on('data',chunk=>{data+=chunk;for(const line of data.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value);return}}catch{}}});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))});
 });
 const launch={headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})};
 const options={viewport:{width:1300,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}};
 browser=await chromium.launch(launch);page=await browser.newPage(options);
 // Delay actual host frames, never synthesize receipt state. This distinguishes
 // an exact read result from an older stream projection of the same operation.
 let delayedFrames=null;
 await page.routeWebSocket(/\/api\/events\/ws(?:\?|$)/,socket=>{
  const server=socket.connectToServer();
  server.onMessage(message=>{
   if(delayedFrames)delayedFrames.push({frame:JSON.parse(message),deliver:()=>socket.send(message)});
   else socket.send(message);
  });
 });
 // Independent browser processes also work with the DTU ARM single-process workaround.
 otherBrowser=await chromium.launch(launch);const other=await otherBrowser.newPage(options);
 for(const current of [page,other])current.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'}),otherComposer=other.getByRole('textbox',{name:'Message Amplifier'});
 await page.goto(info.url);await showFixtureConversation(page);await composer.waitFor();await action(page,'session.select',{id:info.sourceId});
 await other.goto(info.url);await showFixtureConversation(other);await otherComposer.waitFor();await action(other,'session.select',{id:info.sourceId});
 const clientId=(await state(page)).client.id,otherClientId=(await state(other)).client.id;assert.notEqual(clientId,otherClientId);
 observations.clientBuild=(await state(page)).client.build;observations.browserVersion=browser.version();
 const inspect=async()=>{const response=await page.request.get(info.url+'/fixture/check');assert.equal(response.status(),200);return response.json()};
 const post=async(path,data)=>{const response=await page.request.post(info.url+path,{data});assert.equal(response.status(),200,await response.text());return response.json()};
 const journal=async id=>{const response=await page.request.get(info.url+'/fixture/receipt/'+encodeURIComponent(id));assert.equal(response.status(),200);return response.json()};
 const read=async(id,current=page)=>{
  const value=await action(current,'session.handoff.read',{requestId:id});
  assert.equal(value.accepted,true,JSON.stringify(value));assert.equal(value.result.requestId,id);return value.result;
 };
 const propose=async(requestId,title,text,target='target-project')=>{
  const value=await post('/fixture/propose',{requestId,title,text,target,clientId:(await state(page)).client.id});assert.equal(value.accepted,true,JSON.stringify(value));return value.result;
 };
 const card=id=>page.locator(`[data-handoff-id="${id}"]`);
 const row=id=>state(page).then(value=>value.handoffs.find(row=>row.requestId===id));
 const absent=async(ids,current=page)=>{
  for(const id of ids)await expect(current.locator(`[data-handoff-id="${id}"]`)).toHaveCount(0);
  await expect(current.locator('.a-handoffs-past,.a-handoffs-history,.a-handoff--settled')).toHaveCount(0);
  await expect(current.getByText(/^Past handoffs \(/)).toHaveCount(0);
 };
 const noRequests=async(current=page)=>{
  await expect(current.getByRole('region',{name:'New chat requests',exact:true})).toHaveCount(0);
  await expect(current.locator('.a-handoffs')).toHaveCount(0);
  assert.deepEqual((await state(current)).handoffs,[]);
 };
 const prominent=async id=>expect(page.locator(`.a-handoffs > [data-handoff-id="${id}"]`)).toBeVisible();
 const keyboardContinuation=async()=>{
  await expect.poll(()=>page.evaluate(()=>{
   const node=document.activeElement;
   return !!node?.isConnected&&node.matches('button,summary,textarea,input,select,a[href],[contenteditable="true"],[tabindex]');
  })).toBe(true);
  await page.keyboard.press('Tab');
  await expect.poll(()=>page.evaluate(()=>document.activeElement!==document.body)).toBe(true);
 };
 const draft='Keep this source-only draft. Do not send it.';
 await composer.fill(draft);await expect(composer).toHaveDraft(draft);
 observations.before=await inspect();assert.equal(observations.before.sessions.length,2);assert.equal(observations.before.sent.length,0);
 // Settle the only visible request by keyboard. It must leave neither a card
 // nor an empty section, including after reload and at a narrow viewport.
 phase='cancel';const cancelled=await propose('review-cancel','Not needed','This brief must never be sent.');
 const cancelResponse=page.waitForResponse(response=>{
  const request=response.request();
  return request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'&&request.postDataJSON().action==='session.handoff.cancel';
 });
 const cancelButton=card(cancelled.requestId).getByRole('button',{name:'Not now',exact:true});
 await cancelButton.focus();await cancelButton.press('Enter');
 const cancelledResult=await (await cancelResponse).json();
 assert.equal(cancelledResult.accepted,true);assert.equal(cancelledResult.result.status,'cancelled');
 await absent([cancelled.requestId]);await noRequests();await keyboardContinuation();
 observations.cancelledReceipt=await read(cancelled.requestId);
 assert.equal(observations.cancelledReceipt.creation,'not_started');assert.equal(observations.cancelledReceipt.delivery,'not_started');
 for(const width of [320,1300]){
  await page.setViewportSize({width,height:width===320?844:1000});await page.reload();await expect(composer).toBeVisible();
  await absent([cancelled.requestId]);await noRequests();await expect(composer).toHaveDraft(draft);
 }
 assert.deepEqual(await inspect(),observations.before,'cancel and exact receipt reads create no target and start no runtime');
 const title='Review the target project',brief='Read README.md in the target folder and report a short summary.\nKeep this exact second line, <literal> and **plain text**.\nDo not inherit the source chat history.';
 const proposalStarted=Date.now(),proposal=await propose('review-create-once',title,brief);
 observations.proposal=proposal;
 phase='initial-review';
 const review=card(proposal.requestId);await expect(review).toBeVisible();await expect(review.locator('pre')).toHaveText(brief);
 timings.proposalToVisibleMs=Date.now()-proposalStarted;
 await expect(review).toContainText(info.target);await expect(review).toContainText('fixture-model');await expect(review).toContainText('anchors');
 phase='other-client-isolation';await other.reload();await expect(otherComposer).toBeVisible();assert.equal((await state(other)).selectedSessionId,info.sourceId);
 await expect(other.getByRole('region',{name:'New chat requests',exact:true})).toHaveCount(0);assert.deepEqual((await state(other)).handoffs,[],'fresh owner projection excludes another client’s review');
 const deniedRead=await action(other,'session.handoff.read',{requestId:proposal.requestId}).then(value=>({value}),error=>({error:error.message}));
 assert.ok(deniedRead.error||deniedRead.value?.accepted===false,'another client cannot inspect the private review by exact ID');
 assert.deepEqual(await inspect(),observations.before,'refused private review read must not create or send work');
 await action(other,'session.select',{id:info.otherSourceId});await otherComposer.fill('Other browser private draft');await expect(otherComposer).toHaveDraft('Other browser private draft');
 assert.deepEqual(await inspect(),observations.before,'a proposal does not create or start work');
 phase='pending-review-reload';const reloadStarted=Date.now();await page.reload();await expect(review).toBeVisible();await expect(composer).toHaveDraft(draft);assert.equal((await row(proposal.requestId)).reviewHash,proposal.reviewHash);timings.reloadReviewMs=Date.now()-reloadStarted;
 await review.scrollIntoViewIfNeeded();await page.screenshot({path:join(output,'review-desktop.png'),animations:'disabled'});
 await action(page,'view.update',{patch:{navPinned:false,navExpanded:false}});
 phase='narrow-review';for(const width of [390,320]){
  await page.setViewportSize({width,height:844});await review.scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'review must not overflow the viewport');
  await expect(review.getByRole('button',{name:'Create and start',exact:true})).toBeVisible();
  await page.screenshot({path:join(output,`review-${width}.png`),animations:'disabled'});
  await review.screenshot({path:join(output,`review-card-${width}.png`),animations:'disabled'});
 }
 await page.setViewportSize({width:1300,height:1000});
 // Delay the real confirmation to check the local click fence. Lose the HTTP
 // reply while the synthetic recipient holds its initial-input acknowledgement.
 phase='confirm-lost-ack';let release,enteredResolve;const hold=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>enteredResolve=resolve);
 let confirmRequests=0,replyLost=false;
 const intercept=async route=>{
  const body=route.request().postDataJSON();
  if(body?.action!=='session.handoff.confirm'||body.args?.requestId!==proposal.requestId){await route.continue();return}
  confirmRequests++;enteredResolve();await hold;
  const response=await route.fetch();assert.equal(response.status(),200,await response.text());
  observations.confirmResponse=await response.json();replyLost=true;await route.abort('failed');
 };
 await page.route('**/api/actions',intercept);
 await review.getByRole('button',{name:'Create and start',exact:true}).click();await entered;
 const confirmButton=review.getByRole('button',{name:'Create and start',exact:true});
 // The local fence may replace the button immediately or leave it disabled.
 if(await confirmButton.count())await expect(confirmButton).toBeDisabled();
 await review.evaluate(element=>element.querySelector('[data-action="session.handoff.confirm"]')?.click());assert.equal(confirmRequests,1);
 const confirmReleased=Date.now();release();await expect.poll(()=>replyLost).toBe(true);
 await expect(review.getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
 await expect.poll(async()=>(await inspect()).sent.length).toBe(1);
 await expect.poll(async()=>(await row(proposal.requestId))?.status).toBe('delivering');
 await prominent(proposal.requestId);await absent([cancelled.requestId]);
 await post('/fixture/delivery',{requestId:proposal.requestId,stage:'unknown'});
 await expect.poll(async()=>(await row(proposal.requestId))?.status).toBe('unknown');
 await review.getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect(review.getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
 await expect(review.getByRole('alert')).toHaveCount(0);
 await prominent(proposal.requestId);await absent([cancelled.requestId]);
 assert.equal((await row(proposal.requestId)).delivery,'unknown');
 const unknownEffects=await inspect();
 for(const width of [320,1300]){
  await page.setViewportSize({width,height:width===320?844:1000});await page.reload();
  await prominent(proposal.requestId);await expect.poll(async()=>(await row(proposal.requestId))?.status).toBe('unknown');
  const check=review.getByRole('button',{name:'Check delivery',exact:true});
  await check.focus();await check.press('Enter');await expect(check).toBeEnabled();
  await expect(review.getByRole('button',{name:'Create and start',exact:true})).toHaveCount(0);
  await expect(composer).toHaveDraft(draft);assert.equal((await state(page)).selectedSessionId,info.sourceId);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'unknown recovery must not overflow the viewport');
  await absent([cancelled.requestId]);await page.screenshot({path:join(output,`unknown-actionable-${width}.png`),animations:'disabled'});
 }
 assert.deepEqual(await inspect(),unknownEffects,'unknown reload and explicit reads must not replay creation or input');
 const unknown=await row(proposal.requestId);
 // Keep an older real projection pending while the canonical late runtime
 // receipt arrives. The next manual read must settle the UI before that stream.
 delayedFrames=[];
 const retainedReview=await propose('review-retained','Still needs review','Keep this private review visible while delivery settles.');
 await expect.poll(()=>delayedFrames.some(({frame})=>JSON.stringify(frame).includes(retainedReview.requestId))).toBe(true);
 const olderFrames=delayedFrames.splice(0);
 await post('/fixture/delivery',{requestId:proposal.requestId,stage:'accepted'});
 await expect.poll(()=>delayedFrames.length).toBeGreaterThan(0);
 await review.getByRole('button',{name:'Check delivery',exact:true}).click();
 await absent([proposal.requestId,cancelled.requestId]);await keyboardContinuation();
 assert.equal((await row(proposal.requestId)).status,'unknown','held stream must lag the exact read');
 for(const frame of olderFrames)frame.deliver();
 await prominent(retainedReview.requestId);
 await absent([proposal.requestId,cancelled.requestId]);
 assert.equal((await row(proposal.requestId)).status,'unknown','late older projection must exercise retained read result');
 const lateFrames=delayedFrames;delayedFrames=null;for(const frame of lateFrames)frame.deliver();
 await expect.poll(async()=>(await row(proposal.requestId))===undefined).toBe(true);
 await absent([proposal.requestId,cancelled.requestId]);
 const accepted=await read(proposal.requestId);
 assert.equal(accepted.status,'accepted');assert.ok(accepted.revision>unknown.revision);
 observations.delayedStream={olderFrames:olderFrames.length,lateFrames:lateFrames.length};
 timings.confirmReleasedToReconciledMs=Date.now()-confirmReleased;
 await page.unroute('**/api/actions',intercept);
 observations.accepted=await inspect();
 assert.equal(observations.accepted.sessions.length,3);assert.equal(accepted.creation,'created');assert.equal(accepted.delivery,'accepted');
 assert.equal(observations.accepted.sent[0].sessionId,accepted.targetSessionId);assert.equal(observations.accepted.sent[0].inputId,accepted.inputId);
 assert.equal(observations.accepted.sent[0].text,brief);assert.equal(observations.accepted.sent[0].workspace,info.target);
 assert.equal((await state(page)).selectedSessionId,info.sourceId);await expect(composer).toHaveDraft(draft);
 assert.equal((await state(other)).selectedSessionId,info.otherSourceId);await expect(otherComposer).toHaveDraft('Other browser private draft');
 phase='accepted-review-reload';await page.reload();await absent([proposal.requestId,cancelled.requestId]);await prominent(retainedReview.requestId);await expect(composer).toHaveDraft(draft);
 const acceptedRead=await read(proposal.requestId);
 assert.equal(acceptedRead.targetSessionId,accepted.targetSessionId);assert.equal(acceptedRead.inputId,accepted.inputId);assert.equal(acceptedRead.text,brief);
 await absent([proposal.requestId,cancelled.requestId]);
 await page.screenshot({path:join(output,'accepted-absent-desktop.png'),animations:'disabled'});
 assert.deepEqual(await inspect(),observations.accepted,'reload and exact receipt read must not repeat creation or delivery');
 const reads=actions.filter(value=>value.action==='session.handoff.read').length;
 await page.waitForTimeout(1700);assert.equal(actions.filter(value=>value.action==='session.handoff.read').length,reads,'idle UI must not poll handoff receipts');
 phase='stale-target';const stale=await propose('review-removed-folder','Removed target','This must not run.','removed-project');
 await expect(card(stale.requestId)).toBeVisible();await post('/fixture/remove-target',{});
 await card(stale.requestId).getByRole('button',{name:'Create and start',exact:true}).click();
 await expect(card(stale.requestId).getByRole('alert')).toBeVisible();
 await expect(card(stale.requestId).getByRole('button',{name:'Check delivery',exact:true})).toBeVisible();
 await prominent(stale.requestId);await prominent(retainedReview.requestId);
 assert.deepEqual(await inspect(),observations.accepted,'stale target refusal creates and sends nothing');
 observations.afterStale=await inspect();observations.acceptedReceipt=accepted;
 assert.equal(confirmRequests,1);assert.equal(actions.filter(value=>value.action==='session.handoff.confirm'&&value.args.requestId===proposal.requestId).length,1);
 await page.screenshot({path:join(output,'accepted-and-stale.png'),animations:'disabled'});
 // An explicit requested start uses the agent bridge directly, without a second
 // human approval. Lose only the transport reply after the real host accepts it.
 phase='direct-start-lost-ack';
 const direct={requestId:'explicit-start-once',title:'Start the explicitly requested chat',
  text:'The user explicitly asked: create a chat in the target project and send this exact brief.\nNo source history is transferred.',
  target:'target-project',clientId:(await state(page)).client.id};
 observations.directBefore=await inspect();
 let startRequests=0,startReplyLost=false;
 const interceptStart=async route=>{
  startRequests++;
  const response=await route.fetch();assert.equal(response.status(),200,await response.text());
  observations.directStartResponse=await response.json();startReplyLost=true;await route.abort('failed');
 };
 await page.route('**/fixture/start',interceptStart);
 const directStarted=Date.now();
 const lostStart=await page.evaluate(async data=>{
  try{const response=await fetch('/fixture/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return {status:response.status,body:await response.json()}}
  catch(error){return {transportError:error.message}}
 },direct);
 assert.equal(startReplyLost,true);assert.ok(lostStart.transportError,'the initial start reply must actually be lost');
 assert.equal(observations.directStartResponse.accepted,true,JSON.stringify(observations.directStartResponse));
 assert.equal(observations.directStartResponse.result.mode,'direct');
 assert.notEqual(observations.directStartResponse.result.status,'pending','explicit start is not a review proposal');
 await page.unroute('**/fixture/start',interceptStart);
 await expect.poll(async()=>(await journal(direct.requestId)).status).toBe('accepted');
 await expect.poll(async()=>(await row(direct.requestId))===undefined).toBe(true);
 await absent([proposal.requestId,cancelled.requestId,direct.requestId]);
 timings.directStartToAcceptedMs=Date.now()-directStarted;
 const directReceipt=await read(direct.requestId);observations.directAcceptedReceipt=directReceipt;
 observations.directAccepted=await inspect();
 assert.equal(observations.directAccepted.sessions.length,observations.directBefore.sessions.length+1);
 assert.equal(observations.directAccepted.sent.length,observations.directBefore.sent.length+1);
 const delivered=observations.directAccepted.sent.filter(value=>value.inputId===directReceipt.inputId);
 assert.equal(delivered.length,1);assert.equal(delivered[0].sessionId,directReceipt.targetSessionId);
 assert.equal(delivered[0].text,direct.text);assert.equal(delivered[0].workspace,info.target);
 assert.deepEqual(delivered[0].selection,{instance:'one',model:'fixture-model'});assert.equal(delivered[0].bundle,'anchors');
 const directTarget=observations.directAccepted.sessions.find(value=>value.id===directReceipt.targetSessionId);
 assert.equal(directTarget.title,direct.title);assert.equal(directTarget.parentId,null);assert.equal(directTarget.sessionKind,'root');
 assert.equal((await state(page)).selectedSessionId,info.sourceId);await expect(composer).toHaveDraft(draft);
 assert.equal((await state(other)).selectedSessionId,info.otherSourceId);await expect(otherComposer).toHaveDraft('Other browser private draft');
 assert.equal(actions.filter(value=>value.action==='session.handoff.confirm'&&value.args.requestId===direct.requestId).length,0,'explicit start never dispatches confirmation');
 await page.screenshot({path:join(output,'direct-start-accepted-absent.png'),animations:'disabled'});
 // Retrying the identical agent request after its lost acknowledgement returns
 // the durable receipt; it must not create or send again.
 phase='direct-start-retry-reload';
 const retry=await post('/fixture/start',direct);assert.equal(retry.accepted,true,JSON.stringify(retry));
 assert.equal(retry.result.targetSessionId,directReceipt.targetSessionId);assert.equal(retry.result.inputId,directReceipt.inputId);
 assert.deepEqual(await inspect(),observations.directAccepted,'identical explicit start retry must not replay work');
 await page.reload();await expect(composer).toBeVisible();await absent([proposal.requestId,cancelled.requestId,direct.requestId]);
 await expect(composer).toHaveDraft(draft);assert.equal((await state(page)).selectedSessionId,info.sourceId);
 const directRead=await read(direct.requestId);
 assert.equal(directRead.status,'accepted');assert.equal(directRead.text,direct.text);
 assert.equal(directRead.targetSessionId,directReceipt.targetSessionId);assert.equal(directRead.inputId,directReceipt.inputId);
 await absent([direct.requestId]);
 assert.deepEqual(await inspect(),observations.directAccepted,'direct-start reload and status read must not replay work');
 // Deliberately visit the same source in the other client only after asserting
 // that creation preserved its original selection/draft. A conversation-owned
 // receipt remains exactly readable, but settled work has no shared UI tail.
 phase='direct-start-other-client';
 await action(other,'session.select',{id:info.sourceId});await other.reload();await expect(otherComposer).toBeVisible();
 await absent([direct.requestId],other);await noRequests(other);
 const sharedRead=await read(direct.requestId,other);
 assert.equal(sharedRead.targetSessionId,directReceipt.targetSessionId);assert.equal(sharedRead.inputId,directReceipt.inputId);
 for(const id of [proposal.requestId,cancelled.requestId,retainedReview.requestId,stale.requestId]){
  await expect(other.locator(`[data-handoff-id="${id}"]`)).toHaveCount(0);
  assert.equal((await state(other)).handoffs.some(value=>value.requestId===id),false,'private reviews must not leak with exact shared receipt reads');
 }
 await noRequests(other);
 const deniedPendingRead=await action(other,'session.handoff.read',{requestId:retainedReview.requestId}).then(value=>({value}),error=>({error:error.message}));
 assert.ok(deniedPendingRead.error||deniedPendingRead.value?.accepted===false,'the private pending review stays unreadable to the other client');
 await action(other,'session.select',{id:info.otherSourceId});await expect(otherComposer).toHaveDraft('Other browser private draft');
 observations.directFinal=await inspect();assert.deepEqual(observations.directFinal,observations.directAccepted);
 // A retained success tail must take no presentation space and must not crowd
 // out pending, failed or contradictory outcomes. Exact journal reads remain.
 phase='settled-absent-unresolved-actionable';
 const extra=[];
 for(let index=0;index<5;index++){
  const request={...direct,requestId:`settled-start-${index}`,title:`Settled chat ${index+1}`,text:`Exact retained brief ${index+1}.`,clientId:(await state(page)).client.id};
  const response=await post('/fixture/start',request);assert.equal(response.accepted,true,JSON.stringify(response));
  await expect.poll(async()=>(await journal(request.requestId)).status).toBe('accepted');
  extra.push(await journal(request.requestId));
 }
 const failedRequest={...direct,requestId:'delivery-failed',title:'Undelivered initial brief',
  text:'Fixture: reject this initial brief before recipient admission.',clientId:(await state(page)).client.id};
 const failedStart=await post('/fixture/start',failedRequest);assert.equal(failedStart.accepted,true,JSON.stringify(failedStart));
 await expect.poll(async()=>(await row(failedRequest.requestId))?.status).toBe('failed');
 const failed=await row(failedRequest.requestId);assert.equal(failed.creation,'created');assert.equal(failed.delivery,'failed');
 await prominent(failedRequest.requestId);
 const contradictory=await propose('review-contradictory','Contradictory receipt','This request has no confirmed creation or initial-input acceptance.');
 await post('/fixture/contradict',{requestId:contradictory.requestId});
 await expect.poll(async()=>(await row(contradictory.requestId))?.status).toBe('accepted');
 assert.equal((await row(contradictory.requestId)).creation,'not_started');assert.equal((await row(contradictory.requestId)).delivery,'unknown');
 await prominent(contradictory.requestId);
 observations.retainedBefore=await inspect();
 assert.equal(observations.retainedBefore.sessions.length,10);assert.equal(observations.retainedBefore.sent.length,8);
 for(const receipt of [accepted,directReceipt,...extra,failed]){
  assert.equal(observations.retainedBefore.sent.filter(value=>value.inputId===receipt.inputId).length,1,'each exact opening input has one send attempt, including the refused input');
 }
 const settledIds=[cancelled.requestId,accepted.requestId,direct.requestId,...extra.map(value=>value.requestId)];
 observations.unresolvedViewports=[];
 for(const width of [1300,320]){
  await page.setViewportSize({width,height:width===320?844:1000});
  await page.reload();await expect(composer).toBeVisible();await absent(settledIds);
  for(const id of [retainedReview.requestId,failed.requestId,contradictory.requestId])await prominent(id);
  for(const id of [failed.requestId,contradictory.requestId])await expect(card(id).getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
  await expect(card(failed.requestId).getByRole('button',{name:'Open chat',exact:true})).toBeEnabled();
  await expect(card(contradictory.requestId).getByRole('button',{name:'Open chat',exact:true})).toHaveCount(0);
  await expect(card(retainedReview.requestId).locator('pre')).toHaveText(retainedReview.text);
  await expect(card(retainedReview.requestId).getByRole('button',{name:'Create and start',exact:true})).toBeEnabled();
  await expect(composer).toHaveDraft(draft);assert.equal((await state(page)).selectedSessionId,info.sourceId);
  const checkButton=card(contradictory.requestId).getByRole('button',{name:'Check delivery',exact:true});
  await checkButton.focus();await checkButton.press('Enter');await expect(checkButton).toBeEnabled();
  await prominent(contradictory.requestId);assert.equal((await row(contradictory.requestId)).delivery,'unknown');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'unresolved requests must not overflow the viewport');
  assert.equal((await state(page)).handoffs.some(value=>settledIds.includes(value.requestId)),false,'settled receipts are absent from the host projection, not merely hidden');
  observations.unresolvedViewports.push({width,requestIds:(await state(page)).handoffs.map(value=>value.requestId)});
  await page.screenshot({path:join(output,`settled-absent-unresolved-${width}.png`),animations:'disabled'});
  await expect(composer).toHaveDraft(draft);
 }
 assert.deepEqual(await inspect(),observations.retainedBefore,'reload and unresolved reads must not create chats or resend briefs');
 // A late exact read may finish after its source is left. It must not restore
 // a card, draft or selection in the other conversation.
 phase='late-read-source-fence';let releaseRead,readEntered;
 const readGate=new Promise(resolve=>releaseRead=resolve),readReady=new Promise(resolve=>readEntered=resolve);
 const holdRead=async route=>{
  const body=route.request().postDataJSON();
  if(body?.action!=='session.handoff.read'||body.args?.requestId!==failed.requestId){await route.continue();return}
  const response=await route.fetch();readEntered();await readGate;await route.fulfill({response});
 };
 await page.route('**/api/actions',holdRead);
 await card(failed.requestId).getByRole('button',{name:'Check delivery',exact:true}).click();await readReady;
 await action(page,'session.select',{id:info.otherSourceId});await expect.poll(async()=>(await state(page)).selectedSessionId).toBe(info.otherSourceId);
 const lateReadResponse=page.waitForResponse(response=>{
  const request=response.request();
  return request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'&&request.postDataJSON().action==='session.handoff.read'&&request.postDataJSON().args?.requestId===failed.requestId;
 });
 releaseRead();assert.equal((await (await lateReadResponse).json()).accepted,true);await page.unroute('**/api/actions',holdRead);
 await noRequests();assert.equal((await state(page)).selectedSessionId,info.otherSourceId);await expect(composer).toHaveDraft('');
 await action(page,'session.select',{id:info.sourceId});await prominent(failed.requestId);await expect(composer).toHaveDraft(draft);
 // A created-but-failed request keeps explicit navigation. The real host's
 // dirty Canvas guard must refuse it without destroying its recovery card.
 phase='failed-open-guard';
 await page.setViewportSize({width:1300,height:1000});
 assert.equal((await action(page,'canvas.show',{kind:'text',content:'Unfinished source edit'})).accepted,true);
 const views=await action(page,'canvas.views.inspect');
 assert.equal(views.accepted,true);
 const primary=views.result.views.find(value=>value.viewId==='primary');
 assert.ok(primary);
 const canvasTarget=Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,primary[key]]));
 assert.equal((await action(page,'canvas.views.dirty',{...canvasTarget,dirty:true})).accepted,true);
 const failedOpen=card(failed.requestId).getByRole('button',{name:'Open chat',exact:true});
 await failedOpen.click();await expect(card(failed.requestId).getByRole('alert')).toContainText('primary viewer edit');
 await expect(failedOpen).toBeEnabled();await prominent(failed.requestId);
 assert.equal((await state(page)).selectedSessionId,info.sourceId);await expect(composer).toHaveDraft(draft);
 assert.deepEqual(await inspect(),observations.retainedBefore,'guarded Open chat does not create or resend');
 assert.equal((await action(page,'canvas.views.dirty',{...canvasTarget,dirty:false})).accepted,true);
 await failedOpen.click();await expect.poll(async()=>(await state(page)).selectedSessionId).toBe(failed.targetSessionId);
 await action(page,'session.select',{id:info.sourceId});await expect(composer).toHaveDraft(draft);await prominent(failed.requestId);
 await absent(settledIds);
 assert.deepEqual(await inspect(),observations.retainedBefore,'explicit navigation after clearing the guard does not replay the failed brief');
 await expect(otherComposer).toHaveDraft('Other browser private draft');
 assert.equal((await state(other)).selectedSessionId,info.otherSourceId);
 observations.final=await inspect();
 observations.directStartTransport={initialRequests:startRequests,replyLost:startReplyLost,identicalRequestRetry:true,confirmationRequests:0};
 assert.equal(startRequests,1);assert.deepEqual(errors,[]);
 await writeFile(join(output,'result.json'),JSON.stringify({passed:true,elapsedMs:Date.now()-started,timings,source:'real host + packaged UI; synthetic recipient',clientId,otherClientId,confirmRequests,idleReceiptObservationMs:1700,observations,errors,limits:['No model, provider, file-reading, host-restart, or native-device acceptance.','The brief is accepted once; its requested work is not claimed complete.']},null,2)+'\n');
 console.log('Cross-workspace handoff passed: settled cards and empty shells absent, exact retained receipts, unknown/failed/contradictory recovery, delayed-stream and source fences, keyboard continuation, private review/shared direct ownership, replay-safe retry, preserved drafts, guarded explicit navigation, narrow layout.');
}catch(error){
 const current=page?await state(page).catch(()=>null):null;
 await writeFile(join(output,'failure.json'),JSON.stringify({phase,message:error.message,elapsedMs:Date.now()-started,observations,actions,errors,state:current?{selectedSessionId:current.selectedSessionId,client:current.client,handoffs:current.handoffs,draft:current.view?.draft}:null},null,2)+'\n');
 if(page)await page.screenshot({path:join(output,'failure.png'),animations:'disabled'}).catch(()=>{});throw error;
}
finally{
 await writeFile(join(output,'fixture.log'),log);await otherBrowser?.close();await browser?.close();fixture.kill('SIGTERM');
 await new Promise(resolve=>{if(fixture.exitCode!==null||fixture.signalCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});
}
