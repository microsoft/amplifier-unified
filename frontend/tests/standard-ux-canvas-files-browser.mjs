import {showFixtureConversation} from './browser-chat-pages.mjs';
// Public local-file path, exact server location, copy and Canvas lifecycle.
import {spawn} from 'node:child_process';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {fileURLToPath} from 'node:url';
import {dirname} from 'node:path';
import {mkdir,writeFile,lstat} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory is required');
await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/outputs_ui_server.py','--canvas-reading'],{stdio:['ignore','pipe','pipe']});
const stopFixture=ownFixture(fixture,{label:'Canvas files fixture',timeoutMs:5000});
let browser,page,ready,logs='',failure,uiFailure,uiPassed=false,browserClosed=null,fixtureExit=null,fixtureCleanup=null,homeRemoved=null,fixtureStopSucceeded=false;
const errors=[],checks=[],cleanupFailures=[];
const errorRecord=error=>({name:error?.name||'Error',message:String(error?.message||error),...(error instanceof AggregateError?{errors:error.errors.map(errorRecord)}:{})});
fixture.once('close',(code,signal)=>{fixtureExit={pid:fixture.pid,code,signal,settled:true}});
let fixtureLines='';
fixture.stdout.on('data',chunk=>{
 fixtureLines+=chunk;let newline;
 while((newline=fixtureLines.indexOf('\n'))!==-1){
  const line=fixtureLines.slice(0,newline);fixtureLines=fixtureLines.slice(newline+1);
  try{const receipt=JSON.parse(line).outputsFixtureCleanup;if(receipt)fixtureCleanup=receipt}catch{}
 }
});
fixture.stderr.on('data',chunk=>logs+=chunk);
try{
 ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('File fixture timed out')),15000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('File fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:950},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(ready.url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep private file-inspection draft');
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();const canvas=page.locator('#workspace-canvas');await expect(canvas).toBeVisible();
 await canvas.getByRole('button',{name:'Open file',exact:true}).click();const inspector=canvas.locator('.a-canvas-inspector'),form=inspector.locator('.a-canvas-file-form');
 await form.getByLabel('File in this workspace',{exact:true}).fill('report.txt');await form.getByRole('combobox',{name:'Canvas file format',exact:true}).selectOption('text');await form.getByRole('button',{name:'Open file',exact:true}).click();
 await inspector.getByRole('button',{name:'Close canvas details',exact:true}).click();await expect(inspector).toBeHidden();
 await expect(canvas.getByRole('region',{name:'Artifact view',exact:true})).toContainText('Original report bytes.');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas?.path)).toBe(ready.workspace+'/report.txt');
 const resourceId=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await canvas.getByRole('button',{name:'Canvas details',exact:true}).click();await expect(inspector).toBeVisible();
 const paths=inspector.locator('details.a-canvas-file-path');await paths.getByText('File details',{exact:true}).click();
 await expect(paths.locator('code')).toHaveText([ready.workspace+'/report.txt',ready.workspace,'report.txt']);
 const owner=await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views.find(row=>row.viewId==='primary').fileReveal);
 assert.ok(owner.host.id);assert.ok(owner.host.label);await expect(paths.getByText('On '+owner.host.label,{exact:true})).toBeVisible();assert.equal(owner.available,false);
 await expect(paths.getByText('No supported desktop file manager is available on this host.',{exact:true})).toBeVisible();
 await paths.getByRole('button',{name:'Copy full path',exact:true}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(ready.workspace+'/report.txt');
 await paths.getByRole('button',{name:'Copy relative path',exact:true}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('report.txt');
 checks.push('Public file form opens exact text bytes and the named server path; both copies match fixture ownership; missing server file manager is explicit.');
 await inspector.getByRole('button',{name:'Close canvas details',exact:true}).click();await expect(inspector).toBeHidden();
 await canvas.getByRole('button',{name:'Focus canvas',exact:true}).click();await expect(canvas).toHaveAttribute('role','dialog');await expect(canvas.getByRole('button',{name:'Exit canvas focus',exact:true})).toBeFocused();
 const focusables=canvas.locator('button:visible:not([disabled]),a[href]:visible,input:visible:not([disabled]),select:visible:not([disabled]),textarea:visible:not([disabled]),[tabindex="0"]:visible');
 await focusables.last().focus();await page.keyboard.press('Tab');await expect(focusables.first()).toBeFocused();
 await page.keyboard.press('Shift+Tab');await expect(focusables.last()).toBeFocused();
 await page.keyboard.press('Escape');await expect(canvas).not.toHaveAttribute('role','dialog');await expect(canvas.getByRole('button',{name:'Focus canvas',exact:true})).toBeFocused();
 const anchor=page.locator('.a-messages [data-message-id]').filter({hasText:'Canvas reading marker 55'});await anchor.scrollIntoViewIfNeeded();
 const anchorTop=()=>anchor.evaluate(el=>el.getBoundingClientRect().top-el.closest('.a-messages').getBoundingClientRect().top);
 const readingTop=await anchorTop();const readingId=await anchor.getAttribute('data-message-id');
 await canvas.getByRole('button',{name:'Close canvas panel',exact:true}).click();await expect(canvas).toBeHidden();await expect(page.getByRole('button',{name:'Open canvas',exact:true})).toBeFocused();
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();await expect(canvas).toBeVisible();assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.id),resourceId);await expect(canvas.getByRole('region',{name:'Artifact view',exact:true})).toContainText('Original report bytes.');
 await expect.poll(async()=>Math.abs(await anchorTop()-readingTop)).toBeLessThan(3);assert.equal(await anchor.getAttribute('data-message-id'),readingId);
 checks.push('Visible saved-message anchor remains within 3px after closing/reopening Canvas; exact message identity retained.');
 checks.push('Focus/Escape and close/reopen retain resource identity and return focus to the invoking control.');
 await page.setViewportSize({width:390,height:844});await expect(canvas.getByRole('button',{name:'Back to chat',exact:true})).toBeVisible();assert.ok(await canvas.evaluate(el=>el.scrollWidth<=el.clientWidth));await canvas.getByRole('button',{name:'Back to chat',exact:true}).click();await expect(composer).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep private file-inspection draft');
 const final=await page.request.get(ready.url+'/fixture').then(response=>response.json());assert.equal(final.selected,ready.sessionId);assert.equal(final.message,ready.original);assert.equal(final.file,'Original report bytes.');assert.deepEqual(final.outputs,[]);assert.deepEqual(final.sent,[]);assert.deepEqual(errors,[]);
 checks.push('Narrow Back to chat preserves the exact draft, selection, original message and original file; no output or model input created.');
 await page.screenshot({path:out+'/canvas-files.png'});uiPassed=true;
}catch(error){uiFailure=error}
finally{
 try{
  await finishFixture({label:'Canvas files fixture',close:[
   ...(uiFailure&&page?[['Canvas files failure screenshot',()=>page.screenshot({path:out+'/failure.png'})]]:[]),
   ['Canvas files browser',async()=>{if(browser){try{await browser.close()}finally{browserClosed=!browser.isConnected()}assert.equal(browserClosed,true)}}]
  ],stop:async()=>{await stopFixture();fixtureStopSucceeded=true}});
 }catch(error){cleanupFailures.push(error)}
 const expectedHome=ready?.workspace?dirname(ready.workspace):null;
 if(expectedHome){
  try{await lstat(expectedHome);homeRemoved=false}catch(error){if(error.code==='ENOENT')homeRemoved=true;else cleanupFailures.push(error)}
 }
 try{
  assert.equal(fixtureExit?.settled,true);assert.equal(fixtureExit?.code,0);assert.equal(fixtureExit?.signal,null);
  assert.equal(fixtureStopSucceeded,true);assert.equal(browserClosed,true);
  assert.ok(expectedHome);assert.equal(fixtureCleanup?.pid,fixture.pid);assert.equal(fixtureCleanup?.home,expectedHome);
  assert.equal(fixtureCleanup?.signal,'SIGTERM');assert.equal(fixtureCleanup?.runnerCleanup,true);assert.equal(fixtureCleanup?.homeRemoved,true);
  assert.equal(homeRemoved,true);
 }catch(error){cleanupFailures.push(error)}
 try{await writeFile(out+'/fixture.log',logs)}catch(error){cleanupFailures.push(error)}
 const failures=[...(uiFailure?[uiFailure]:[]),...cleanupFailures];
 failure=failures.length>1?new AggregateError(failures,'Canvas files UI and/or cleanup failed'):failures[0];
 const observations={schemaVersion:1,passed:uiPassed&&!failure,uiPassed,checks,browserClosed,fixtureExit,fixtureStopSucceeded,fixtureCleanup,homeRemoved,uiFailure:uiFailure?errorRecord(uiFailure):null,cleanupFailures:cleanupFailures.map(errorRecord),failure:failure?errorRecord(failure):null,limits:['Synthetic local Linux host with no file manager; no actual OS reveal action or remote browser/server pair.','Native selectOption exercises form state, not native picker gesture.','Desktop ordinary path plus keyboard focus/Escape and narrow Back; not complete four-mode repetition.','No Canvas resize or physical user-scroll gesture claim; anchor positioned through locator before public close/reopen.']};
 try{await writeFile(out+'/observations.json',JSON.stringify(observations,null,2)+'\n')}
 catch(error){
  failure=failure?new AggregateError([failure,error],'Canvas files observations could not be saved'):error;
  observations.passed=false;observations.reportWriteFailure=errorRecord(error);observations.failure=errorRecord(failure);
 }finally{console.log(JSON.stringify({canvasFilesQualification:observations}))}
}
if(failure)throw failure;
console.log('Canvas file path passed: exact local bytes and owner, absolute/relative copy, unavailable reveal, focus return, narrow Back and unchanged chat/draft/file.');
