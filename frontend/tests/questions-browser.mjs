import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[process.env.AMPLIFIER_QUESTIONS_FIXTURE||fileURLToPath(new URL('../../tests/fixtures/questions_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
const output=process.env.AMPLIFIER_QUESTIONS_OUTPUT,layoutChecks=[];
if(output)await mkdir(output,{recursive:true});
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exit '+code)))});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const card=id=>page.locator(`[data-question-id="${id}"]`),composer=()=>page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const usage=()=>page.getByRole('region',{name:'Conversation usage',exact:true});
 const holdUsageRead=async()=>{
  let release,arrived=false;const gate=new Promise(resolve=>release=resolve);
  const route=async pending=>{const raw=pending.request().postDataJSON(),body=raw.action==='shell.command'?raw.args:raw;if(body.action==='capacity.read'){arrived=true;await gate}await pending.continue()};
  await page.route('**/api/actions',route);
  return {release,arrived:()=>arrived,remove:()=>page.unroute('**/api/actions',route)};
 };
 const completeUsageDuringClick=async(questions,held,label)=>{
  await expect.poll(held.arrived).toBe(true);
  await expect(usage().getByRole('button',{name:'Reading usage…',exact:true})).toBeDisabled();
  // Scroll only before pointerdown: a completion must not move the target away.
  await questions.scrollIntoViewIfNeeded();const target=await questions.elementHandle(),before=await questions.boundingBox(),refreshBefore=await usage().getByRole('button',{name:'Reading usage…',exact:true}).boundingBox();
  if(output)await page.screenshot({path:join(output,label.replace(/\W+/g,'-')+'-before.png')});
  await page.mouse.move(before.x+before.width/2,before.y+before.height/2);await page.mouse.down();
  try{
   held.release();await expect(usage().getByRole('button',{name:'Refresh usage',exact:true})).toBeEnabled();
   const after=await questions.boundingBox(),refreshAfter=await usage().getByRole('button',{name:'Refresh usage',exact:true}).boundingBox();layoutChecks.push({label,viewport:page.viewportSize(),before,after,refreshBefore,refreshAfter});
   for(const key of ['x','y','height'])assert.ok(Math.abs(refreshAfter[key]-refreshBefore[key])<=1,`${label}: refresh control ${key} moved from ${refreshBefore[key]} to ${refreshAfter[key]}`);
   assert.equal(await target.evaluate(element=>element.isConnected),true,'Usage completion preserves the history control');
   for(const key of ['x','y','width','height'])assert.ok(Math.abs(after[key]-before[key])<=1,`${label}: history target ${key} moved from ${before[key]} to ${after[key]}`);
  }finally{await page.mouse.up();await held.remove();await target.dispose()}
  await expect(questions.locator('..')).toHaveAttribute('open','');
  if(output)await page.screenshot({path:join(output,label.replace(/\W+/g,'-')+'-after.png')});
 };
 const history=async({stableUsage=false,usageCalls=0,label='history'}={})=>{
  const held=stableUsage?await holdUsageRead():null;
  await page.getByRole('button',{name:'Chat',exact:true}).click();
  await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Chat info',exact:true}).click();
  const questions=page.getByText('Questions & answers',{exact:true});
  if(held){
   await completeUsageDuringClick(questions,held,label+' initial');
   await questions.click();await expect(questions.locator('..')).not.toHaveAttribute('open','');
   // Real canonical receipts make the expanded refreshed snapshot taller.
   await usage().getByText('Model breakdown',{exact:true}).click();
   const seeded=await page.request.post(ready.url+'/fixture/usage',{data:{count:usageCalls}});
   assert.equal(seeded.status(),200,await seeded.text());
   const refresh=await holdUsageRead();await usage().getByRole('button',{name:'Refresh usage',exact:true}).click();
   await completeUsageDuringClick(questions,refresh,label+' expanded refresh');
   await expect(usage().getByText(`${usageCalls} recorded model calls ·`,{exact:false})).toBeVisible();
   await expect(usage().locator('h4')).toHaveCount(usageCalls);
  }else{
   await expect(usage().getByRole('button',{name:'Refresh usage',exact:true})).toBeEnabled();
   await questions.click();await expect(questions.locator('..')).toHaveAttribute('open','');
  }
   await page.locator('.a-question-history > summary').click();
   // Each historical answer stays compact until explicitly opened.
   const entries=page.locator('.a-question-history .a-question-item');
   for(const entry of await entries.all()){
    await expect(entry).not.toHaveAttribute('open','');
    await entry.locator(':scope > summary').click();
   }
 };
 const close=async()=>{
  // Closing is optimistic; do not reload until the closed panel is saved.
  const saved=page.waitForResponse(response=>{
   if(response.request().method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
   const raw=response.request().postDataJSON(),body=raw.action==='shell.command'?raw.args:raw;
   return body.action==='view.update'&&body.args?.patch?.panel===null&&response.ok();
  });
  await page.getByRole('button',{name:'Close panel',exact:true}).click();
  await saved;await expect(page.getByRole('dialog')).toHaveCount(0);
 };
 const check=async()=>await (await page.request.get(ready.url+'/fixture/check')).json();
 await page.goto(ready.url);await showFixtureConversation(page);
 await expect(card(ready.required)).toBeVisible();
 const uniqueQuestions=async()=>{for(const id of [ready.required,ready.optional,ready.skipped])await expect(card(id)).toHaveCount(1)};
 await uniqueQuestions();
 const alternative=card(ready.required).locator('.a-question-alternative');
 if(process.env.AMPLIFIER_QUESTIONS_NEGATIVE==='v2-density'){
  // Deliberately restore the old expanded editor: the approved-density oracle
  // below must fail, independently of Markdown/version/security assertions.
  await alternative.evaluate(element=>{element.open=true});
 }
 await expect(alternative).not.toHaveAttribute('open','');
 await expect(card(ready.required).getByRole('textbox')).toBeHidden();
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});
  await card(ready.required).scrollIntoViewIfNeeded();
  await expect(card(ready.required).getByRole('textbox')).toBeHidden();
  assert.ok(await card(ready.required).evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Choice question fits narrow view');
  if(output)await page.screenshot({path:join(output,`question-choice-collapsed-${width}.png`)});
 }
 await page.setViewportSize({width:1280,height:900});
 await card(ready.required).getByText('Question context',{exact:true}).click();
 await expect(card(ready.required).getByText('This step waits for your answer. Other work can continue.')).toBeVisible();
 await card(ready.required).getByText('Question context',{exact:true}).click();
 assert.equal(await card(ready.required).getByRole('button',{name:'Skip question'}).count(),0);
 // A neighboring handoff must not reuse the questions component identity.
 // Keep the strict, unscoped question selectors: duplicate cards are a defect.
 const questionDraft='Keep this question draft through neighboring updates';
 await alternative.locator(':scope > summary').click();
 await card(ready.required).getByRole('textbox').fill(questionDraft);
 await alternative.locator(':scope > summary').click();
 await expect(card(ready.required).getByRole('textbox')).toBeHidden();
 await alternative.locator(':scope > summary').click();
 await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 const draftSaved=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='view.update'&&response.request().postDataJSON()?.args?.patch?.draft==='Keep this unsent message');
 await composer().fill('Keep this unsent message');await draftSaved;
 await uniqueQuestions();await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 const response=await page.request.post(ready.url+'/fixture/handoff',{data:{clientId:await page.evaluate(()=>window.amplifier.getState().client.id)}});
 assert.equal(response.status(),200,await response.text());const proposal=await response.json();assert.equal(proposal.accepted,true);
 const handoff=page.locator(`[data-handoff-id="${proposal.result.requestId}"]`);
 await expect(handoff).toHaveCount(1);await expect(handoff).toBeVisible();await uniqueQuestions();
 await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 await page.reload();await expect(card(ready.required)).toBeVisible();await uniqueQuestions();
 await expect(card(ready.required).locator('.a-question-alternative')).toHaveAttribute('open','');
 await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 await expect(handoff).toHaveCount(1);await expect(handoff).toBeVisible();
 await expect(composer()).toHaveDraft('Keep this unsent message');
 await handoff.getByRole('button',{name:'Not now',exact:true}).click();
 await expect(handoff.getByRole('button',{name:'Not now',exact:true})).toHaveCount(0);
 await uniqueQuestions();await expect(composer()).toHaveDraft('Keep this unsent message');
 assert.deepEqual((await check()).sessionIds,[ready.sessionId],'A cancelled review creates no chat');
 assert.equal((await check()).sent.length,0,'Reconnect cannot submit an unanswered question');
 await expect(card(ready.required).getByRole('radio')).toHaveCount(0);
 await expect(card(ready.required).getByRole('button',{name:/Full report/})).toHaveCSS('justify-content','flex-start');
 await page.getByRole('button',{name:'Review questions',exact:true}).click();
 assert.ok(await page.locator('[data-part=questions]').evaluate(el=>el.scrollHeight<=el.clientHeight+1),'Questions use the chat scroll area, without a clipping scroller');
 const questionBox=await card(ready.required).boundingBox(),composerBox=await page.locator('[data-part=composer]').boundingBox();
 assert.ok(questionBox.width<=composerBox.width+2,'Question cards respect the chat column');
 await page.screenshot({path:'/tmp/amplifier-durable-questions-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await card(ready.required).getByRole('button',{name:/Full report/}).scrollIntoViewIfNeeded();
 assert.ok(await card(ready.required).evaluate(element=>element.scrollWidth<=element.clientWidth));
 await page.screenshot({path:'/tmp/amplifier-durable-questions-choice-mobile.png'});
 await page.setViewportSize({width:1280,height:900});
 // A failed request retains the custom draft; retry uses the same command ID.
 const answerRequests=[];let releaseAnswer,heldResolve;
 const held=new Promise(resolve=>{heldResolve=resolve});
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body.action!=='question.answer')return route.continue();
  answerRequests.push(body);
  if(answerRequests.length===1)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Try this answer again'})});
  await new Promise(resolve=>{releaseAnswer=resolve;heldResolve()});
  return route.continue();
 });
 await card(ready.required).getByRole('textbox').fill('Keep my alternative answer');
 await card(ready.required).getByRole('button',{name:/Full report/}).click();
 await expect(card(ready.required).getByRole('alert')).toBeVisible();
 await expect(card(ready.required).getByRole('textbox')).toHaveValue('Keep my alternative answer');
 await card(ready.required).getByRole('button',{name:/Full report/}).click();
 await held;
 await expect(card(ready.required).getByRole('button',{name:/Full report/})).toBeDisabled();
 await card(ready.required).getByRole('button',{name:/Full report/}).evaluate(button=>{button.click();button.click()});
 assert.equal(answerRequests.length,2,'Repeated clicks during save cannot send another answer');
 assert.equal(answerRequests[0].id,answerRequests[1].id,'Retry reuses the original command ID');
 const saved=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='question.answer'&&response.ok());
 releaseAnswer();await saved;await page.unroute('**/api/actions');
 await expect(page.locator('.a-question-history')).toHaveCount(0);
 await history({stableUsage:true,usageCalls:6,label:'desktop'});
 await expect(card(ready.required).getByText('Answer saved and delivered to Amplifier.')).toBeVisible();
 await close();
 await expect(composer()).toHaveDraft('Keep this unsent message');
 let state=await check();assert.equal(state.sent.length,1);assert.equal(state.sent[0].sessionId,ready.sessionId);
 const duplicate=await page.request.post(ready.url+'/api/actions',{data:{id:'second-device',action:'question.answer',args:{sessionId:ready.sessionId,id:ready.required,expectedRevision:1,optionId:'full'}}});
 assert.equal(duplicate.status(),409);assert.equal((await check()).sent.length,1);
 await card(ready.optional).getByRole('textbox',{name:'Your answer: What title would you like?'}).fill('Project findings');
 await card(ready.optional).getByRole('button',{name:'Submit answer'}).click();
 await expect(card(ready.optional)).toHaveCount(0);
 await history();await expect(card(ready.optional).getByText('Answer saved and delivered to Amplifier.')).toBeVisible();await close();
 await card(ready.skipped).getByRole('button',{name:'Skip question'}).click();
 await history();await expect(card(ready.skipped).getByText('Question cancelled without an answer.')).toBeVisible();await close();
 const voice=await (await page.request.post(ready.url+'/fixture/voice')).json();
 assert.equal(voice.answer.provenance.via,'call');assert.equal(voice.answer.provenance.voiceItemId,'fixture-answer');
 await history();await expect(card(voice.id).getByText('Answer saved and delivered to Amplifier.')).toBeVisible();await close();
 state=await check();assert.equal(state.sent.length,3);assert.equal(new Set(state.sent.map(row=>row.inputId)).size,3);
 await page.reload();await expect(composer()).toHaveDraft('Keep this unsent message');
 await page.setViewportSize({width:390,height:844});
 await history({stableUsage:true,usageCalls:12,label:'narrow'});await expect(card(voice.id).locator('.a-question-result .a-markdown')).toHaveText('Plain language');
 await expect(card(voice.id)).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await card(voice.id).scrollIntoViewIfNeeded();await expect(card(voice.id)).toBeInViewport();
 await page.screenshot({path:'/tmp/amplifier-durable-questions-mobile.png'});
 await close();
 const reviewResponse=await page.request.post(ready.url+'/fixture/reviews');
 assert.equal(reviewResponse.status(),200,await reviewResponse.text());
 const review=await reviewResponse.json(),q=card(review.question.id);
 await page.setViewportSize({width:1280,height:900});
 if(process.env.AMPLIFIER_QUESTIONS_NEGATIVE==='flatten'){
  // Deliberate rendered-boundary fault: the formatting oracle below must fail.
  await q.locator('.a-question-prompt').evaluate(element=>{element.textContent=element.textContent});
 }
 await expect(q.locator('.a-question-prompt h1')).toHaveText('Choose the peer presentation');
 await expect(q.locator('.a-question-prompt li')).toHaveCount(2);
 await expect(q.locator('.a-question-prompt pre code')).toHaveText('exact source\n');
 assert.equal(await q.locator('.a-question-prompt a[href^="javascript:"]').count(),0);
 assert.equal(await page.evaluate(()=>window.bad),undefined);
 assert.equal(await page.evaluate(()=>document.body.dataset.untrusted),undefined);
 const answerDraft='**Proceed**.\n\nKeep the `source` visible.';
 await q.getByRole('textbox').fill(answerDraft);
 await page.reload();await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 const rows=q.locator('.a-compact-artifact'),first=rows.nth(0),second=rows.nth(1);
 await expect(rows).toHaveCount(2);await expect(q.locator('[data-full-artifact-preview]')).toHaveCount(0);
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});
  await first.scrollIntoViewIfNeeded();
  const geometry=await first.locator('.a-compact-artifact-row').evaluate(row=>{
   const bounds=node=>{const b=node.getBoundingClientRect();return{x:b.x,y:b.y,width:b.width,height:b.height}};
   return {row:bounds(row),buttons:[...row.querySelectorAll('button')].map(bounds),scrollWidth:row.scrollWidth,clientWidth:row.clientWidth};
  });
  for(const b of geometry.buttons){assert.ok(b.width>=44&&b.height>=44,'44px review targets');assert.ok(b.x>=geometry.row.x&&b.x+b.width<=geometry.row.x+geometry.row.width+1,'Review actions fit row')}
  assert.ok(Math.abs(geometry.buttons[0].y-geometry.buttons[1].y)<1,'Review actions stay on one row');
  assert.ok(geometry.scrollWidth<=geometry.clientWidth+1,'No row overflow');
  layoutChecks.push({label:'compact-question-review',width,...geometry});
  if(output)await page.screenshot({path:join(output,`question-review-${width}.png`)});
 }
 await page.setViewportSize({width:1280,height:900});
 if(process.env.AMPLIFIER_QUESTIONS_NEGATIVE==='wrong-version'){
  // Deliberate request fault: bypassing the pinned version must fail the v1 oracle.
  await page.route('**/api/actions',async route=>{
   const body=route.request().postDataJSON();
   if(body?.action==='canvas.views.inline'&&body.args?.questionId===review.question.id)
    return route.continue({postData:JSON.stringify({...body,args:{...body.args,version:2}})});
   return route.continue();
  });
 }
 await first.getByRole('button',{name:/^Preview here:/}).click();
 await expect(first.getByRole('heading',{name:'Pinned document v1'})).toBeVisible();
 await expect(first.getByRole('heading',{name:'Latest document v2'})).toHaveCount(0);
 await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 await first.getByRole('button',{name:/^Open in Canvas:/}).click();
 await expect(first.locator('[data-full-artifact-preview]')).toHaveCount(0);
 await expect.poll(()=>page.evaluate(()=>({id:window.amplifier.getState().canvas.id,version:window.amplifier.getState().canvas.selectedVersion}))).toEqual({id:review.markdown.id,version:1});
 await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 await first.getByRole('button',{name:/^Preview here:/}).click();
 await expect(first.getByRole('heading',{name:'Pinned document v1'})).toBeVisible();
 await first.getByRole('button',{name:/^Hide preview:/}).click();
 await expect(first.locator('[data-full-artifact-preview]')).toHaveCount(0);
 await second.getByRole('button',{name:/^Preview here:/}).click();
 const frame=second.frameLocator('iframe');
 await expect(frame.getByRole('heading',{name:'HTML mockup v1'})).toBeVisible();
 await expect(second.locator('iframe')).toHaveAttribute('sandbox','allow-scripts');
 await frame.getByRole('button',{name:'Try mockup'}).click();
 await expect(frame.locator('#value')).toHaveText('1');
 await expect(frame.locator('body')).toHaveAttribute('data-parent-blocked','yes');
 await expect(frame.locator('body')).toHaveAttribute('data-network-blocked','yes');
 assert.equal((await check()).networkAttempts,0,'Sandbox must block a reachable fixture endpoint, not merely fail DNS');
 assert.equal(await page.evaluate(()=>document.body.dataset.untrusted),undefined);
 assert.equal((await check()).sent.length,3,'Review actions never send or answer');
 await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 if(output)await page.screenshot({path:join(output,'question-html-review.png')});
 const beforeTransfer=await second.locator('iframe').elementHandle();
 await second.getByRole('button',{name:/^Open in Canvas:/}).click();
 await expect(second.getByRole('button',{name:'Keep current preview',exact:true})).toBeVisible();
 await second.getByRole('button',{name:'Keep current preview',exact:true}).click();
 assert.equal(await beforeTransfer.evaluate(element=>element.isConnected),true,'Keep preserves the exact interactive frame');
 await expect(frame.locator('#value')).toHaveText('1');
 await second.getByRole('button',{name:/^Open in Canvas:/}).click();
 await second.getByRole('button',{name:'Reset preview and open in Canvas',exact:true}).click();
 await expect(second.locator('[data-full-artifact-preview]')).toHaveCount(0);
 await expect.poll(()=>beforeTransfer.evaluate(element=>element.isConnected)).toBe(false);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas.id)).toBe(review.html.id);
 // A closed Canvas retains the primary frame: returning it to the question
 // also needs explicit reset, not canvas.close as an implicit disposal.
 await page.evaluate(()=>{const state=window.amplifier.getState();return window.amplifier.dispatch('canvas.visibility',{open:false,sessionId:state.selectedSessionId,canvasId:state.canvas.id})});
 await second.getByRole('button',{name:/^Preview here:/}).click();
 await expect(second.getByRole('button',{name:'Keep current preview',exact:true})).toBeVisible();
 await second.getByRole('button',{name:'Reset preview and preview in chat',exact:true}).click();
 await expect(frame.getByRole('heading',{name:'HTML mockup v1'})).toBeVisible();
 await frame.getByRole('button',{name:'Try mockup'}).click();
 await expect(frame.locator('#value')).toHaveText('1');
 await beforeTransfer.dispose();
 // Synthetic renderer-declared dirty state is a supported contract probe.
 // It does not claim the built-in Markdown/HTML reader has a document editor.
 const reviewTarget=await page.evaluate(id=>{
  const view=window.amplifier.getState().canvasWorkspace.views.find(v=>v.viewId.startsWith('inline-')&&v.resourceId===id);
  return Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
 },review.html.id);
 const reviewAction=async(action,args)=>page.evaluate(async({action,args})=>{
  try{return await window.amplifier.dispatch(action,args)}
  catch(error){return {accepted:false,code:error.code,error:error.message}}
 },{action,args});
 assert.equal((await reviewAction('canvas.views.dirty',{...reviewTarget,dirty:true})).accepted,true);
 const mountedFrame=await second.locator('iframe').elementHandle();
 await second.getByRole('button',{name:/^Open in Canvas:/}).click();
 await expect(second.getByRole('status')).toContainText('Finish or cancel');
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'Canvas transfer cannot bypass a dirty question preview');
 await q.getByRole('button',{name:'Submit answer'}).click();
 await expect(q.getByRole('alert')).toContainText('Finish or cancel');
 await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 assert.equal((await check()).questions.find(row=>row.id===review.question.id).status,'pending');
 assert.equal((await check()).sent.length,3,'Rejected dirty answer must not send');
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'Dirty rejection preserves the mounted renderer');
 await expect(frame.locator('#value')).toHaveText('1');
 assert.equal((await reviewAction('session.draft',{})).code,'canvas_view_dirty');
 if(output)await page.screenshot({path:join(output,'question-dirty-answer-rejected.png')});
 assert.equal((await reviewAction('canvas.views.dirty',{...reviewTarget,dirty:false})).accepted,true);
 // New explicit click after confirmed rejection; the same draft must work.
 await q.getByRole('button',{name:'Submit answer'}).click();
 await expect(q.getByText('Answer saved and delivered to Amplifier.')).toBeVisible();
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'Status revision and delivery updates preserve the open review');
 await expect(frame.locator('#value')).toHaveText('1');
 assert.equal((await check()).sent.length,4);
 await history();await expect(q).toHaveCount(1);
 await expect(page.locator('.a-question-history').locator(`[data-question-id="${review.question.id}"]`)).toHaveCount(0);
 await close();
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'History inspection does not duplicate or replace the review');
 await second.getByRole('button',{name:/^Hide preview:/}).click();
 await expect(second.getByRole('button',{name:'Keep current preview',exact:true})).toBeVisible();
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'Hide does not silently reset interactive state');
 await expect(frame.locator('#value')).toHaveText('1');
 await second.getByRole('button',{name:'Keep current preview',exact:true}).click();
 await expect(frame.locator('#value')).toHaveText('1');
 await second.getByRole('button',{name:/^Hide preview:/}).click();
 await second.getByRole('button',{name:'Reset preview and hide',exact:true}).click();
 await expect(q).toHaveCount(0);
 await mountedFrame.dispose();
 await history();await expect(card(review.question.id).locator('.a-question-result strong').filter({hasText:'Proceed'})).toBeVisible();
 const answeredCard=card(review.question.id);
 await expect(answeredCard.locator('.a-question-original')).not.toHaveAttribute('open','');
 await expect(answeredCard.locator('.a-question-prompt')).toBeHidden();
 const answerFirst=await answeredCard.evaluate(element=>{
  const answer=element.querySelector('.a-question-result'),refs=element.querySelector('.a-question-artifacts');
  return !!(answer.compareDocumentPosition(refs)&Node.DOCUMENT_POSITION_FOLLOWING);
 });
 assert.equal(answerFirst,true,'Saved answer comes before review cards and original prose');
 await expect(answeredCard.locator('.a-compact-artifact')).toHaveCount(2);
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});
  await answeredCard.scrollIntoViewIfNeeded();
  assert.ok(await answeredCard.evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Answered card fits narrow view');
  if(output)await page.screenshot({path:join(output,`question-answer-first-${width}.png`)});
 }
 await answeredCard.getByText('Original question & provenance',{exact:true}).click();
 await expect(answeredCard.locator('.a-question-prompt h1')).toHaveText('Choose the peer presentation');
 await expect(answeredCard.getByText('Answer to question revision 1.',{exact:true})).toBeVisible();
 await answeredCard.getByText('Original question & provenance',{exact:true}).click();
 const savedQuestion=(await check()).questions.find(row=>row.id===review.question.id);
 assert.equal(savedQuestion.answer.text,answerDraft);assert.equal(savedQuestion.answer.questionRevision,1);
 assert.equal(savedQuestion.answer.artifacts[0].version,1);
 assert.equal(savedQuestion.answer.artifacts[0].id,review.markdown.id);
 if(output)await page.screenshot({path:join(output,'question-formatted-answer.png')});
 await close();
 for(const action of ['question.cancel','question.supersede']){
  const seeded=await page.request.post(ready.url+'/fixture/reviews');
  assert.equal(seeded.status(),200,await seeded.text());
  const data=await seeded.json(),item=card(data.question.id),document=item.locator('.a-compact-artifact').first();
  await document.getByRole('button',{name:/^Preview here:/}).click();
  await expect(document.getByRole('heading',{name:'Pinned document v1'})).toBeVisible();
  const body=await document.locator('.a-inline-artifact-body').elementHandle();
  const target=await page.evaluate(id=>{
   const view=window.amplifier.getState().canvasWorkspace.views.find(v=>v.viewId.startsWith('inline-')&&v.resourceId===id);
   return Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
  },data.markdown.id);
  const args={sessionId:ready.sessionId,id:data.question.id,expectedRevision:1,
   ...(action==='question.supersede'?{prompt:'Replacement review question',required:false,dependency:'Choose'}:{})};
  assert.equal((await reviewAction('canvas.views.dirty',{...target,dirty:true})).accepted,true);
  assert.equal((await reviewAction(action,args)).code,'canvas_view_dirty');
  assert.equal((await check()).questions.find(row=>row.id===data.question.id).status,'pending');
  assert.equal(await body.evaluate(element=>element.isConnected),true);
  assert.equal((await check()).sent.length,4,'Cancel/supersede cannot send an answer');
  assert.equal((await reviewAction('canvas.views.dirty',{...target,dirty:false})).accepted,true);
  const closed=await reviewAction(action,args);assert.equal(closed.accepted,true);
  await expect(item.getByText(action==='question.cancel'?'Question cancelled without an answer.':'Replaced by a newer question.')).toBeVisible();
  assert.equal(await body.evaluate(element=>element.isConnected),true,'Clean closure keeps the open review reachable');
  await document.getByRole('button',{name:/^Hide preview:/}).click();
  await expect(item).toHaveCount(0);await body.dispose();
  if(action==='question.supersede')assert.equal((await reviewAction('question.cancel',{
   sessionId:ready.sessionId,id:closed.result.id,expectedRevision:closed.result.revision,
  })).accepted,true);
 }
 assert.deepEqual(errors,[]);
 console.log('Durable questions browser passed: unique cards alongside handoff updates and reload, reconnect, explicit required/optional UI, option and free-text answers, cancellation, duplicate rejection, preserved draft, shared voice provenance, mobile layout.');
}finally{if(output){await mkdir(output,{recursive:true});await writeFile(join(output,'layout-checks.json'),JSON.stringify(layoutChecks,null,2)+'\n')}await browser?.close();fixture.kill()}
