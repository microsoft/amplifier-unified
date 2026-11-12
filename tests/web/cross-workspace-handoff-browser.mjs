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
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/handoff786_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','pipe']});
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
 const propose=async(requestId,title,text,target='target-project')=>{
  const value=await post('/fixture/propose',{requestId,title,text,target,clientId:(await state(page)).client.id});assert.equal(value.accepted,true,JSON.stringify(value));return value.result;
 };
 const card=id=>page.locator(`[data-handoff-id="${id}"]`);
 const row=id=>state(page).then(value=>value.handoffs.find(row=>row.requestId===id));
 const history=(current=page)=>current.locator('details.a-handoffs-past');
 const summary=(current=page)=>history(current).locator(':scope > summary');
 const collapsed=async(count,current=page)=>{
  await expect(history(current)).toHaveCount(1);
  await expect(summary(current)).toHaveText(`Past handoffs (${count})`);
  await expect(history(current)).not.toHaveAttribute('open','');
  await expect(history(current).locator('[data-handoff-id]')).toHaveCount(count);
  await expect(history(current).locator('[data-handoff-id]:visible')).toHaveCount(0);
 };
 const expand=async(current=page)=>{
  await summary(current).focus();await summary(current).press('Enter');
  await expect(history(current)).toHaveAttribute('open','');
 };
 const details=async(receiptCard)=>{
  await receiptCard.locator(':scope > details > summary').focus();
  await receiptCard.locator(':scope > details > summary').press('Enter');
  await expect(receiptCard.locator('pre')).toBeVisible();
 };
 const prominent=async(id)=>{
  await expect(page.locator(`.a-handoffs > [data-handoff-id="${id}"]`)).toBeVisible();
  await expect(history().locator(`[data-handoff-id="${id}"]`)).toHaveCount(0);
 };
 const draft='Keep this source-only draft. Do not send it.';
 await composer.fill(draft);await expect(composer).toHaveDraft(draft);
 observations.before=await inspect();assert.equal(observations.before.sessions.length,2);assert.equal(observations.before.sent.length,0);
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
 phase='cancel';const cancelled=await propose('review-cancel','Not needed','This brief must never be sent.');
 await card(cancelled.requestId).getByRole('button',{name:'Not now',exact:true}).click();
 await expect.poll(async()=>(await row(cancelled.requestId))?.status).toBe('cancelled');
 await collapsed(1);await prominent(proposal.requestId);
 assert.deepEqual(await inspect(),observations.before,'cancel creates no target and starts no runtime');
 // Delay the real confirmation to check the local click fence. Lose the HTTP
 // reply while the synthetic recipient holds its initial-input acknowledgement.
 phase='confirm-lost-ack';let release,enteredResolve;const hold=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>enteredResolve=resolve);
 let confirmRequests=0,replyLost=false;
 const intercept=async route=>{
  const body=route.request().postDataJSON();
  if(body.action!=='session.handoff.confirm'||body.args?.requestId!==proposal.requestId){await route.continue();return}
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
 await prominent(proposal.requestId);await collapsed(1);
 await post('/fixture/delivery',{requestId:proposal.requestId,stage:'unknown'});
 await expect.poll(async()=>(await row(proposal.requestId))?.status).toBe('unknown');
 await review.getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect(review.getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
 await expect(review.getByRole('alert')).toHaveCount(0);
 await prominent(proposal.requestId);await collapsed(1);
 assert.equal((await row(proposal.requestId)).delivery,'unknown');
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
 await collapsed(2);await expect(summary()).toBeFocused();
 assert.equal((await row(proposal.requestId)).status,'unknown','held stream must lag the exact read');
 for(const frame of olderFrames)frame.deliver();
 await prominent(retainedReview.requestId);
 await collapsed(2);
 assert.equal((await row(proposal.requestId)).status,'unknown','late older projection must exercise retained read result');
 const lateFrames=delayedFrames;delayedFrames=null;for(const frame of lateFrames)frame.deliver();
 await expect.poll(async()=>(await row(proposal.requestId))?.status).toBe('accepted');
 await collapsed(2);
 assert.ok((await row(proposal.requestId)).revision>unknown.revision);
 observations.delayedStream={olderFrames:olderFrames.length,lateFrames:lateFrames.length};
 timings.confirmReleasedToReconciledMs=Date.now()-confirmReleased;
 await page.unroute('**/api/actions',intercept);
 observations.accepted=await inspect();const accepted=await row(proposal.requestId);
 assert.equal(observations.accepted.sessions.length,3);assert.equal(accepted.creation,'created');assert.equal(accepted.delivery,'accepted');
 assert.equal(observations.accepted.sent[0].sessionId,accepted.targetSessionId);assert.equal(observations.accepted.sent[0].inputId,accepted.inputId);
 assert.equal(observations.accepted.sent[0].text,brief);assert.equal(observations.accepted.sent[0].workspace,info.target);
 assert.equal((await state(page)).selectedSessionId,info.sourceId);await expect(composer).toHaveDraft(draft);
 assert.equal((await state(other)).selectedSessionId,info.otherSourceId);await expect(otherComposer).toHaveDraft('Other browser private draft');
 phase='accepted-review-reload';await page.reload();await collapsed(2);await prominent(retainedReview.requestId);await expect(composer).toHaveDraft(draft);
 const beforeHistory=actions.length;
 await expand();await expect(review.getByRole('button',{name:'Open chat',exact:true})).toBeVisible();
 await details(review);await expect(review.locator('pre')).toHaveText(brief);
 await expect(review).toContainText('does not mean its requested work has completed');
 assert.equal(actions.length,beforeHistory,'keyboard disclosure must not dispatch an app action');
 await review.scrollIntoViewIfNeeded();await page.screenshot({path:join(output,'accepted-desktop.png'),animations:'disabled'});
 await review.getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect(review.getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
 await expect.poll(async()=>(await row(proposal.requestId))?.status).toBe('accepted');assert.deepEqual(await inspect(),observations.accepted,'reload and status read must not repeat creation or delivery');
 const reads=actions.filter(value=>value.action==='session.handoff.read').length;
 await page.waitForTimeout(1700);assert.equal(actions.filter(value=>value.action==='session.handoff.read').length,reads,'idle UI must not poll handoff receipts');
 phase='explicit-open';await review.getByRole('button',{name:'Open chat',exact:true}).click();await expect.poll(async()=>(await state(page)).selectedSessionId).toBe(accepted.targetSessionId);
 await expect(page.getByText('Synthetic recipient accepted the brief.',{exact:true})).toBeVisible();
 assert.equal((await state(other)).selectedSessionId,info.otherSourceId);await expect(otherComposer).toHaveDraft('Other browser private draft');
 await action(page,'session.select',{id:info.sourceId});await expect(composer).toHaveDraft(draft);
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
 const directCard=card(direct.requestId);
 await expect.poll(async()=>(await row(direct.requestId))?.status).toBe('accepted');
 await collapsed(3);await expand();await expect(directCard).toBeVisible();
 await expect(directCard.getByRole('button',{name:'Create and start',exact:true})).toHaveCount(0);
 timings.directStartToAcceptedMs=Date.now()-directStarted;
 const directReceipt=await row(direct.requestId);observations.directAcceptedReceipt=directReceipt;
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
 await directCard.scrollIntoViewIfNeeded();await page.screenshot({path:join(output,'direct-start-accepted.png'),animations:'disabled'});
 // Retrying the identical agent request after its lost acknowledgement returns
 // the durable receipt; it must not create or send again.
 phase='direct-start-retry-reload';
 const retry=await post('/fixture/start',direct);assert.equal(retry.accepted,true,JSON.stringify(retry));
 assert.equal(retry.result.targetSessionId,directReceipt.targetSessionId);assert.equal(retry.result.inputId,directReceipt.inputId);
 assert.deepEqual(await inspect(),observations.directAccepted,'identical explicit start retry must not replay work');
 await page.reload();await collapsed(3);await expand();await expect(directCard.getByRole('button',{name:'Open chat',exact:true})).toBeVisible();
 await expect(composer).toHaveDraft(draft);assert.equal((await state(page)).selectedSessionId,info.sourceId);
 await details(directCard);await expect(directCard.locator('pre')).toHaveText(direct.text);
 await directCard.getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect(directCard.getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
 await expect.poll(async()=>(await row(direct.requestId))?.status).toBe('accepted');
 assert.equal((await row(direct.requestId)).targetSessionId,directReceipt.targetSessionId);
 assert.equal((await row(direct.requestId)).inputId,directReceipt.inputId);
 assert.deepEqual(await inspect(),observations.directAccepted,'direct-start reload and status read must not replay work');
 // Deliberately visit the same source in the other client only after asserting
 // that creation preserved its original selection/draft. NewChatHandoffs.project
 // includes conversation-owned starts for that source; private reviews are not
 // shared (authorize still requires the original/restored client for reviews).
 phase='direct-start-other-client';
 await action(other,'session.select',{id:info.sourceId});await other.reload();await expect(otherComposer).toBeVisible();
 await collapsed(1,other);await expand(other);
 await expect(other.locator(`[data-handoff-id="${direct.requestId}"]`)).toBeVisible();
 assert.equal((await state(other)).handoffs.find(value=>value.requestId===direct.requestId).inputId,directReceipt.inputId);
 for(const id of [proposal.requestId,cancelled.requestId,retainedReview.requestId,stale.requestId]){
  await expect(other.locator(`[data-handoff-id="${id}"]`)).toHaveCount(0);
  assert.equal((await state(other)).handoffs.some(value=>value.requestId===id),false,'private reviews must not leak with shared direct receipts');
 }
 await other.locator(`[data-handoff-id="${direct.requestId}"] > details > summary`).press('Enter');
 await other.locator(`[data-handoff-id="${direct.requestId}"]`).getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect(other.locator(`[data-handoff-id="${direct.requestId}"]`).getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
 await action(other,'session.select',{id:info.otherSourceId});await expect(otherComposer).toHaveDraft('Other browser private draft');
 observations.directFinal=await inspect();assert.deepEqual(observations.directFinal,observations.directAccepted);
 // Eight genuine settled receipts share one small default disclosure. Keep an
 // unfinished private review alongside them; do not lower counts to fit history.
 phase='eight-settled-history';
 const extra=[];
 for(let index=0;index<5;index++){
  const request={...direct,requestId:`history-start-${index}`,title:`History chat ${index+1}`,text:`Exact history brief ${index+1}.`,clientId:(await state(page)).client.id};
  const response=await post('/fixture/start',request);assert.equal(response.accepted,true,JSON.stringify(response));
  await expect.poll(async()=>(await row(request.requestId))?.status).toBe('accepted');
  extra.push(await row(request.requestId));
 }
 const failedRequest={...direct,requestId:'history-failed',title:'Undelivered initial brief',
  text:'Fixture: reject this initial brief before recipient admission.',clientId:(await state(page)).client.id};
 const failedStart=await post('/fixture/start',failedRequest);assert.equal(failedStart.accepted,true,JSON.stringify(failedStart));
 await expect.poll(async()=>(await row(failedRequest.requestId))?.status).toBe('failed');
 const failed=await row(failedRequest.requestId);assert.equal(failed.creation,'created');assert.equal(failed.delivery,'failed');
 await prominent(failedRequest.requestId);
 observations.historyBefore=await inspect();
 assert.equal(observations.historyBefore.sessions.length,10);assert.equal(observations.historyBefore.sent.length,8);
 for(const receipt of [accepted,directReceipt,...extra,failed]){
  assert.equal(observations.historyBefore.sent.filter(value=>value.inputId===receipt.inputId).length,1,'each exact opening input has one send attempt, including the refused input');
 }
 observations.historyFootprints=[];
 for(const width of [1300,320]){
  await page.setViewportSize({width,height:width===320?844:1000});
  await page.reload();await collapsed(8);await prominent(retainedReview.requestId);await prominent(failed.requestId);
  await expect(card(failed.requestId).getByRole('button',{name:'Check delivery',exact:true})).toBeVisible();
  await expect(card(retainedReview.requestId).locator('pre')).toHaveText(retainedReview.text);
  await expect(card(retainedReview.requestId).getByRole('button',{name:'Create and start',exact:true})).toBeEnabled();
  await expect(composer).toHaveDraft(draft);assert.equal((await state(page)).selectedSessionId,info.sourceId);
  await summary().scrollIntoViewIfNeeded();
  const box=await history().boundingBox();assert.ok(box&&box.height<=64,`collapsed history at ${width}px must fit one compact disclosure (<=64px), got ${JSON.stringify(box)}`);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'history must not overflow the viewport');
  observations.historyFootprints.push({width,height:box.height});
  await page.screenshot({path:join(output,`history-collapsed-${width}.png`),animations:'disabled'});
  const before=actions.length;await expand();
  await expect(history().locator('[data-handoff-id]:visible')).toHaveCount(8);
  await expect(history().locator('pre:visible')).toHaveCount(0);
  await expect(history().getByText('Opening brief acceptance is not work completion.',{exact:true})).toBeVisible();
  await details(directCard);await expect(directCard.locator('pre')).toHaveText(direct.text);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'expanded history details must not overflow the viewport');
  await expect(directCard.getByRole('button',{name:'Open chat',exact:true})).toBeVisible();
  await summary().focus();await summary().press('Space');await collapsed(8);
  assert.equal(actions.length,before,'history expansion/details/collapse must not change app state or send input');
  await expect(composer).toHaveDraft(draft);
 }
 const historyReads=actions.filter(value=>value.action==='session.handoff.read').length;
 await page.waitForTimeout(1700);
 assert.equal(actions.filter(value=>value.action==='session.handoff.read').length,historyReads,'collapsed history must not poll delivery');
 assert.deepEqual(await inspect(),observations.historyBefore,'history interaction/reload must not create chats or resend briefs');
 await expect(otherComposer).toHaveDraft('Other browser private draft');
 assert.equal((await state(other)).selectedSessionId,info.otherSourceId);
 observations.final=await inspect();
 observations.directStartTransport={initialRequests:startRequests,replyLost:startReplyLost,identicalRequestRetry:true,confirmationRequests:0};
 assert.equal(startRequests,1);assert.deepEqual(errors,[]);
 await writeFile(join(output,'result.json'),JSON.stringify({passed:true,elapsedMs:Date.now()-started,timings,source:'real host + packaged UI; synthetic recipient',clientId,otherClientId,confirmRequests,idleReceiptObservationMs:1700,observations,errors,limits:['No model, provider, file-reading, host-restart, or native-device acceptance.','The brief is accepted once; its requested work is not claimed complete.']},null,2)+'\n');
 console.log('Cross-workspace handoff passed: exact host confirmation/read, unknown and late acceptance, retained read result, eight collapsed receipts, keyboard details, private review/shared direct ownership, replay-safe retry, preserved drafts, explicit navigation, narrow layout.');
}catch(error){
 const current=page?await state(page).catch(()=>null):null;
 await writeFile(join(output,'failure.json'),JSON.stringify({phase,message:error.message,elapsedMs:Date.now()-started,observations,actions,errors,state:current?{selectedSessionId:current.selectedSessionId,client:current.client,handoffs:current.handoffs,draft:current.view?.draft}:null},null,2)+'\n');
 if(page)await page.screenshot({path:join(output,'failure.png'),animations:'disabled'}).catch(()=>{});throw error;
}
finally{
 await writeFile(join(output,'fixture.log'),log);await otherBrowser?.close();await browser?.close();fixture.kill('SIGTERM');
 await new Promise(resolve=>{if(fixture.exitCode!==null||fixture.signalCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});
}
