import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/questions_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
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
  await page.locator('.a-question-history summary').click();
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
 await page.goto(ready.url);
 await expect(card(ready.required)).toBeVisible();
 const uniqueQuestions=async()=>{for(const id of [ready.required,ready.optional,ready.skipped])await expect(card(id)).toHaveCount(1)};
 await uniqueQuestions();
 await expect(card(ready.required).getByText('This step waits for your answer. Other work can continue.')).toBeVisible();
 assert.equal(await card(ready.required).getByRole('button',{name:'Skip question'}).count(),0);
 // A neighboring handoff must not reuse the questions component identity.
 // Keep the strict, unscoped question selectors: duplicate cards are a defect.
 const questionDraft='Keep this question draft through neighboring updates';
 await card(ready.required).getByRole('textbox').fill(questionDraft);
 const draftSaved=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='view.update'&&response.request().postDataJSON()?.args?.patch?.draft==='Keep this unsent message');
 await composer().fill('Keep this unsent message');await draftSaved;
 await uniqueQuestions();await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 const response=await page.request.post(ready.url+'/fixture/handoff',{data:{clientId:await page.evaluate(()=>window.amplifier.getState().client.id)}});
 assert.equal(response.status(),200,await response.text());const proposal=await response.json();assert.equal(proposal.accepted,true);
 const handoff=page.locator(`[data-handoff-id="${proposal.result.requestId}"]`);
 await expect(handoff).toHaveCount(1);await expect(handoff).toBeVisible();await uniqueQuestions();
 await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 await page.reload();await expect(card(ready.required)).toBeVisible();await uniqueQuestions();
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
 await history({stableUsage:true,usageCalls:12,label:'narrow'});await expect(card(voice.id).getByText('Your answer: Plain language',{exact:true})).toBeVisible();
 await expect(card(voice.id)).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await card(voice.id).scrollIntoViewIfNeeded();await expect(card(voice.id)).toBeInViewport();
 await page.screenshot({path:'/tmp/amplifier-durable-questions-mobile.png'});
 assert.deepEqual(errors,[]);
 console.log('Durable questions browser passed: unique cards alongside handoff updates and reload, reconnect, explicit required/optional UI, option and free-text answers, cancellation, duplicate rejection, preserved draft, shared voice provenance, mobile layout.');
}finally{if(output){await mkdir(output,{recursive:true});await writeFile(join(output,'layout-checks.json'),JSON.stringify(layoutChecks,null,2)+'\n')}await browser?.close();fixture.kill()}
