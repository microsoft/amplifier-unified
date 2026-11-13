import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openChatAdvancedPage} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import './composer-test-helpers.mjs';
const moduleFailure=process.argv.includes('--module-failure');
const contextLimit=process.argv.includes('--context-limit');
const activeWorker=process.argv.includes('--active-worker');
const lifecycleMode=process.argv.includes('--request-failure')?'request-failure':process.argv.includes('--completed-runtime-fault')?'completed-runtime-fault':null;
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/session_health_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit'],env:{...process.env,...(moduleFailure?{MODULE_FAILURE_FIXTURE:'1'}:{}),...(contextLimit?{CONTEXT_LIMIT_FIXTURE:'1'}:{}),...(activeWorker?{ACTIVE_WORKER_FIXTURE:'1'}:{}),...(lifecycleMode?{LIFECYCLE_FIXTURE:lifecycleMode}:{})}});
const stop=ownFixture(fixture,{label:'Session health fixture'});
let browser,originalError;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exit '+code)))});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));await page.goto(ready.url);await showFixtureConversation(page);
 const output=fileURLToPath(new URL('../../output/conversation-details-proof/',import.meta.url));await mkdir(output,{recursive:true});
 if(lifecycleMode){
  await lifecycleCheck(page,ready,output);
 }else{
 if(contextLimit){
  await expect(page.getByText('Context limit reached.',{exact:true})).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('The conversation exceeded the model context limit.');
  await expect(page.getByRole('alert')).toContainText('Choose a model with more context or start a new conversation with a summary.');
  await expect(page.getByRole('alert')).not.toContainText('provider payload');
  await expect(page.getByRole('button',{name:'Create recovery copy',exact:true})).toBeVisible();
  if(activeWorker) await expect(page.getByRole('alert').getByRole('button',{name:'Create recovery copy',exact:true})).toBeDisabled();
 }else{
  await expect(page.getByText(moduleFailure?'Chat could not start.':'A turn stopped.',{exact:true})).toBeVisible();
  assert.ok((await page.getByRole('alert').filter({hasText:moduleFailure?'Chat could not start.':'A turn stopped.'}).boundingBox()).height<120);
 }
 await expect(page.getByRole('button',{name:'Continue conversation',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Conversation details',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Copy session ID',exact:true})).toHaveCount(0);
 await page.screenshot({path:output+'compact-error.png'});
 // The explanation must not collapse into a few characters beside the actions.
 // Exercise the actual interruption, startup and context-limit banner variants.
 const banner=page.locator('#amp-one>.a-alert').filter({hasText:contextLimit?'Context limit reached.':moduleFailure?'Chat could not start.':'A turn stopped.'});
 const originalTheme=await page.evaluate(()=>window.amplifier.getState().theme);
 const savedTheme={name:'Saved custom appearance',css:await readFile(new URL('../../tests/fixtures/legacy_theme.css',import.meta.url),'utf8')};
 for(const theme of [originalTheme,savedTheme]){
 await page.evaluate(theme=>window.amplifier.dispatch('theme.apply',{name:theme.name,css:theme.css}),theme);
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  await expect(banner).toBeVisible();
  const bounds=await banner.evaluate(element=>{
   const box=element.getBoundingClientRect(),text=element.querySelector(':scope>span').getBoundingClientRect();
   return {width:box.width,textWidth:text.width,textBottom:text.bottom,left:box.left,right:box.right,bottom:box.bottom,
    scrollWidth:element.scrollWidth,clientWidth:element.clientWidth,scrollHeight:element.scrollHeight,clientHeight:element.clientHeight,
    actions:[...element.querySelectorAll(':scope>button')].map(button=>{const rect=button.getBoundingClientRect();return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom}})};
  });
  assert.ok(bounds.textWidth>=bounds.width-40,'explanation keeps the full content width');
  assert.ok(bounds.scrollWidth<=bounds.clientWidth+1,'banner has no horizontal overflow');
  assert.ok(bounds.scrollHeight<=bounds.clientHeight+1,'normal failure explanation and actions are not clipped');
  for(const action of bounds.actions){
   assert.ok(action.top>=bounds.textBottom,'actions follow the explanation');
   assert.ok(action.left>=bounds.left&&action.right<=bounds.right+1&&action.bottom<=bounds.bottom+1,'actions remain within the banner');
  }
  await page.screenshot({path:output+`error-${theme===savedTheme?'saved':'current'}-${width}.png`});
 }
 assert.equal(await page.evaluate(()=>window.amplifier.getState().theme.css),theme.css,'responsive layout leaves saved theme bytes unchanged');
 }
 await page.evaluate(theme=>window.amplifier.dispatch('theme.apply',{name:theme.name,css:theme.css}),originalTheme);
 await page.setViewportSize({width:1280,height:900});
 async function openRecovery(){
  await openChatAdvancedPage(page,'Context & recovery');
  const recovery=page.getByRole('dialog',{name:'Context & recovery',exact:true});
  const disclosure=recovery.locator('details').filter({has:page.locator('summary',{hasText:'Recovery & recorded errors'})});
  if(!await disclosure.evaluate(el=>el.open))await disclosure.locator(':scope > summary').click();
  return recovery;
 }
 let dialog;
 if(contextLimit)dialog=await openRecovery();
 else{await page.getByRole('button',{name:'View error details',exact:true}).click();dialog=page.getByRole('dialog',{name:'Error details',exact:true});}
 await expect(dialog.getByRole('button',{name:'Hide details',exact:true})).toHaveCount(0);
 if(moduleFailure){
  await expect(dialog.getByText('Some parts of this chat could not load',{exact:true})).toBeVisible();
  await expect(dialog.getByText('hook-fixture',{exact:true})).toBeVisible();
  await expect(dialog.getByRole('listitem').filter({hasText:'Check the declared module type and metadata'})).toBeVisible();
  await expect(dialog.locator('summary').filter({hasText:'Runtime message'})).toHaveCount(0);
 }else if(contextLimit) await expect(dialog.getByText('The conversation exceeded the model context limit.',{exact:true})).toBeVisible();
 else await expect(dialog.getByText('Something interrupted this chat.',{exact:true})).toBeVisible();
 const copy=dialog.getByRole('button',{name:'Copy session ID',exact:true});
 assert.equal(await copy.textContent(),'');await copy.click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),ready.sessionId);
 await dialog.getByRole('button',{name:'Copy diagnostics',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toContain('"capturedAt"');
 if(!moduleFailure&&!contextLimit)await expect(dialog.getByText('The provider rejected an image or computer-tool result in the conversation context.',{exact:true})).toBeVisible();
 const diagnostics=JSON.parse(await page.evaluate(()=>navigator.clipboard.readText()));if(moduleFailure)assert.equal(diagnostics.moduleFailures[0].reason_code,'invalid_module_metadata');else assert.equal(diagnostics.failure.category,contextLimit?'context_limit':'invalid_image');
 // Naming is its own everyday chat page; it no longer lives in diagnostics.
 await dialog.getByRole('button',{name:'Close panel',exact:true}).click();await expect(dialog).toBeHidden();
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Rename',exact:true}).click();
 dialog=page.getByRole('dialog',{name:'Rename',exact:true});
 const automatic=dialog.getByRole('checkbox',{name:'Automatic chat naming'});
 const refresh=dialog.getByRole('button',{name:'Regenerate chat name'});
 await automatic.check();await expect(automatic).toBeChecked();
 await refresh.click();await expect(refresh).toBeDisabled();
 await expect(dialog.getByLabel('Chat name',{exact:true})).toHaveValue('Browser investigation');
 await expect(automatic).toBeChecked();
 await dialog.getByLabel('Chat name',{exact:true}).fill('My investigation');
 await dialog.getByRole('button',{name:'Save name',exact:true}).click();
 await expect(automatic).not.toBeChecked();
 assert.equal((await (await page.request.get(ready.url+'/fixture/check')).json()).namingCalls,1);
 await dialog.getByRole('button',{name:'Close panel',exact:true}).click();await expect(dialog).toBeHidden();
 dialog=await openRecovery();
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:output+'mobile-details.png'});
 await dialog.getByRole('button',{name:'Create recovery copy',exact:true}).scrollIntoViewIfNeeded();
 assert.ok(await page.locator('.a-conversation-details').evaluate(element=>element.scrollWidth<=element.clientWidth));
 if(activeWorker){
  await expect(dialog.getByRole('button',{name:'Create recovery copy',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Close panel',exact:true}).click();
  assert.equal((await (await page.request.get(ready.url+'/fixture/check')).json()).sessions,1);
 }else if(contextLimit){
  await page.getByRole('button',{name:'Close panel',exact:true}).click();
  await page.getByRole('alert').getByRole('button',{name:'Create recovery copy',exact:true}).click();
 }else{
  await page.getByRole('button',{name:'Create recovery copy',exact:true}).click();
  await page.getByRole('button',{name:'Close panel',exact:true}).click();
 }
 if(!activeWorker){
  await expect(page.getByText(/Recovery copy · Readable history retained/)).toBeVisible();
  const state=await page.evaluate(()=>window.amplifier.getState());assert.notEqual(state.selectedSessionId,ready.sessionId);
  const check=await (await page.request.get(ready.url+'/fixture/check')).json();assert.equal(check.originalUnchanged,true);assert.equal(check.sessions,2);
  await page.reload();await expect(page.getByText(/Recovery copy · Readable history retained/)).toBeVisible();
 }
 assert.deepEqual(errors,[]);
 console.log('Session health browser passed: compact error, inline copy, Auto naming, one regeneration, manual name precedence, mobile bounds, recovery and original transcript preservation.');
 }
 assert.deepEqual(errors,[]);
}catch(error){originalError=error;throw error;}
finally{await finishFixture({label:'Session health browser',close:[['browser',()=>browser?.close()]],stop,originalError});}

async function lifecycleCheck(page,ready,output){
 const actions=[];
 page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST')actions.push(request.postDataJSON())});
 const facts=()=>page.request.get(ready.url+'/fixture/check').then(response=>response.json());
 const input=(state,id)=>state.messages.find(row=>row.inputId===id&&row.role==='user');
 const before=await facts(),completed=input(before,'completed-input');
 assert.equal(completed.inputStatus.phase,'done');
 assert.equal(before.messages.find(row=>row.role==='assistant').response.status,'completed');
 const completedRow=page.locator(`[data-message-id="${completed.id}"]`);
 await expect(completedRow).toBeVisible();
 await expect(page.getByText('The saved answer is complete.',{exact:true})).toBeVisible();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toBeEditable();
 await composer.fill('Private unsent lifecycle draft');
 if(lifecycleMode==='completed-runtime-fault'){
  const response=await page.request.post(ready.url+'/fixture/late-runtime-fault');
  assert.equal(response.status(),200);
  await expect.poll(async()=>(await facts()).runtimeFault).toBeTruthy();
  await expect(page.locator('[data-part=conversation-trouble]')).toHaveCount(0);
  await expect(page.locator('[data-input-phase=failed]')).toHaveCount(0);
 }else{
  const failed=input(before,'failed-input');
  assert.equal(failed.inputStatus.phase,'failed');
  assert.equal(failed.inputStatus.failure.category,'provider_unavailable');
  const failedRow=page.locator(`[data-message-id="${failed.id}"]`);
  await expect(failedRow.locator('[data-input-phase=failed]')).toBeVisible();
  await expect(page.locator('[data-part=conversation-trouble]')).toBeVisible();
  await expect(completedRow.locator('[data-input-phase=failed]')).toHaveCount(0);
 }
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'failure UX fits narrow viewport');
  await page.screenshot({path:output+`lifecycle-${lifecycleMode}-${width}.png`});
 }
 await expect(composer).toHaveDraft('Private unsent lifecycle draft');
 await expect(page.getByRole('button',{name:'Continue conversation',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Check readiness',exact:true})).toHaveCount(0);
 let check=await facts();
 assert.equal(check.completedUnchanged,true);assert.equal(check.originalUnchanged,true);
 assert.equal(check.sessions,1);assert.equal(check.selected,ready.sessionId);assert.deepEqual(check.calls,[]);
 assert.equal(input(check,'completed-input').inputStatus.phase,'done');
 assert.deepEqual(actions.filter(row=>['conversation.send','session.recover','runtime.restart','runtime.stop'].includes(row.action)),[]);
 await page.reload();await showFixtureConversation(page);
 await expect(page.getByText('The saved answer is complete.',{exact:true})).toBeVisible();
 await expect(composer).toHaveDraft('Private unsent lifecycle draft');
 if(lifecycleMode==='completed-runtime-fault')await expect(page.locator('[data-part=conversation-trouble]')).toHaveCount(0);
 // One new explicit message is the normal continuation; diagnostics/reload sent nothing.
 await composer.fill('A new explicit request');
 await composer.press('Enter');
 await expect(page.getByText('A fresh explicit message worked.',{exact:true})).toBeVisible();
 await expect(page.locator('[data-part=conversation-trouble]')).toHaveCount(0);
 check=await facts();
 assert.equal(check.calls.filter(row=>row.operation==='send').length,1);
 assert.equal(check.completedUnchanged,true);
 assert.equal(input(check,'completed-input').inputStatus.phase,'done');
 if(lifecycleMode==='request-failure')assert.equal(input(check,'failed-input').inputStatus.phase,'failed','older request outcome remains local history');
 assert.equal(check.sessions,1);assert.equal(check.selected,ready.sessionId);
 console.log(`Session health ${lifecycleMode} passed: truthful exact-input outcome, saved answer/draft retained, narrow/reload safe, no automatic work and one ordinary explicit send.`);
}
