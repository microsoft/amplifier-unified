import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const base=resolve(process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'));
await mkdir(base,{recursive:true});
const output=await mkdtemp(join(base,'approval-lifecycle-'));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||'python',[root+'/tests/fixtures/approval_lifecycle_server.py'],{
 stdio:['ignore','pipe','inherit'],env:{...process.env,AMPLIFIER_TEST_OUTPUT_DIR:output}});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{
  let text='';const timeout=setTimeout(()=>reject(Error('Fixture startup')),15000);
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}});
  fixture.once('exit',code=>reject(Error(`Fixture exited: ${code}`)));fixture.once('error',reject);
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-approval-control-token'}});
 page=await context.newPage();const other=await context.newPage();
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const inspect=()=>page.request.get(url+'/fixture').then(r=>r.json());
 const call=async(action,args,id)=>{
  const response=await page.request.post(url+'/api/actions',{data:{action,args,id}});
  assert.equal(response.status(),200,await response.text());return response.json();
 };
 await page.goto(url);
 await showFixtureConversation(page);
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null}}));
 const initial=await inspect(),sid=initial.sessionId;
 assert.deepEqual(initial.starts,[]);
 assert.equal(initial.attention.length,1);assert.equal(initial.navigation.label,'Approval requested');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Keep this private draft');
 const old=page.locator('[data-approval-id="capability:old"]'),current=page.locator('[data-approval-id="capability:current"]');
 await expect(old).toContainText('stopped');
 await expect(old.getByRole('button',{name:'Allow',exact:true})).toHaveCount(0);
 await expect(old.getByRole('button',{name:'Deny',exact:true})).toHaveCount(0);
 await expect(current.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
 await old.getByRole('button',{name:'Review current scope'}).click();
 await expect(old.getByRole('region',{name:'Current configuration scope'})).toBeVisible();
 assert.deepEqual((await inspect()).starts,[]);
 assert.deepEqual((await inspect()).controls,[]);
 assert.deepEqual((await inspect()).inputs,[]);
 await page.screenshot({path:join(output,'desktop.png'),animations:'disabled'});
 // Another tab sees the same owner and cannot revive the dismissed request.
 await other.goto(url);
 await showFixtureConversation(other);
 await other.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null}}));
 const observed=(await call('capability.read',{sessionId:sid,id:'capability:old'},'read-old')).result.request;
 await old.getByRole('button',{name:'Dismiss old request'}).focus();
 await page.keyboard.press('Enter');
 await expect(old).toHaveCount(0);
 await expect(current.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
 await expect(other.locator('[data-approval-id="capability:old"]')).toHaveCount(0);
 const duplicate=await page.request.post(url+'/api/actions',{data:{action:'capability.revoke',args:{sessionId:sid,id:'capability:old',dismissRequest:true,expectedRevision:observed.revision,expectedRequestHash:observed.requestHash},id:'stale-second-tab'}});
 assert.equal(duplicate.status(),409);
 const late=await page.request.post(url+'/api/actions',{data:{action:'approval.respond',args:{sessionId:sid,id:'capability:old',decision:'allow'},id:'late-allow'}});
 assert.equal(late.status(),409,await late.text());
 const dismissed=(await call('capability.read',{sessionId:sid,id:'capability:old'},'check-dismissed')).result.request;
 assert.equal(dismissed.status,'revoked');assert.equal(dismissed.dismissed,true);
 assert.equal((await inspect()).attention.length,1);
 const historyEntry=()=>page.getByRole('button',{name:/^Permissions history \(/});
 const panel=page.locator('#workspace-canvas'),history=panel.locator('[data-overview-section="permissions"]');
 const closeHistory=async()=>{
  await panel.getByRole('button',{name:'Close canvas panel',exact:true}).click();
  await expect(panel).toBeHidden();
  await expect(page.locator('#canvas-toggle')).toBeFocused();
 };
 const recordsBefore=(await inspect()).approvals;
 await historyEntry().focus();await page.keyboard.press('Enter');
 await expect(history).toHaveAttribute('open','');
 await expect(history.locator('summary')).toBeFocused();
 await expect(page.locator('[data-permission-history-id="capability:old"]')).toContainText('Dismissed old request');
 await expect(page.locator('[data-part="conversation"] [data-permission-history-id]')).toHaveCount(0);
 await page.screenshot({path:join(output,'history-desktop.png'),animations:'disabled'});
 await closeHistory();
 assert.deepEqual((await inspect()).approvals,recordsBefore,'Passive history keeps exact audit records');
 await page.reload();
 await expect(composer).toHaveDraft('Keep this private draft');
 await expect(current.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
 // Change actual scope without pushing a new card. Preflight must not approve it.
 await page.request.post(url+'/fixture',{data:{change:'scope'}});
 await current.getByRole('button',{name:'Allow',exact:true}).click();
 await expect(current.getByRole('alert')).toContainText('changed');
 await expect(current).toContainText('Changed scope requiring another review');
 assert.equal((await inspect()).approvals.find(r=>r.id==='capability:current').status,'pending');
 // Expire between review and click; no approval is applied or automatically retried.
 await current.getByRole('textbox',{name:'Note to Amplifier (optional)'}).fill('Keep this note in history');
 await page.request.post(url+'/fixture',{data:{change:'expire'}});
 await current.getByRole('button',{name:'Deny',exact:true}).click();
 await expect(current.getByRole('button',{name:'Dismiss old request'})).toBeVisible();
 await expect(current.getByRole('button',{name:'Allow',exact:true})).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});
 await current.scrollIntoViewIfNeeded();
 assert.equal(await current.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.locator('[data-part="approvals"]').evaluate(element=>{element.scrollTop=0});
 await page.screenshot({path:join(output,'narrow.png'),animations:'disabled'});
 const dismissButton=current.getByRole('button',{name:'Dismiss old request'});
 await dismissButton.scrollIntoViewIfNeeded();
 assert.equal(await dismissButton.evaluate(button=>{
  const bounds=button.getBoundingClientRect(),container=button.closest('[data-part="approvals"]').getBoundingClientRect();
  return bounds.top>=container.top&&bounds.bottom<=container.bottom&&button.contains(document.elementFromPoint(bounds.x+bounds.width/2,bounds.y+bounds.height/2));
 }),true,'Narrow dismissal is visible and hit-testable after scrolling');
 await page.screenshot({path:join(output,'narrow-actions.png'),animations:'disabled'});
 await current.getByRole('button',{name:'Dismiss old request'}).click();
 await expect(current).toHaveCount(0);
 await expect(page.locator('[data-part="approvals"]')).toHaveCount(0);
 await expect(historyEntry()).toBeVisible();
 const compact=await historyEntry().boundingBox();
 assert.ok(compact.height>=44&&compact.height<=60,'Compact entry retains a touch-size target');
 await page.screenshot({path:join(output,'history-only-narrow-chat.png'),animations:'disabled'});
 await historyEntry().click();
 await expect(history).toBeVisible();
 await expect(page.locator('[data-permission-history-id="capability:current"]')).toContainText('Keep this note in history');
 await page.screenshot({path:join(output,'history-narrow.png'),animations:'disabled'});
 await closeHistory();
 await page.reload();
 await expect(composer).toHaveDraft('Keep this private draft');
 await expect(page.locator('[data-part="approvals"]')).toHaveCount(0);
 // A new independent permission still arrives in chat after history-only state.
 await page.request.post(url+'/fixture',{data:{change:'later'}});
 const later=page.locator('[data-approval-id="capability:later"]');
 await expect(later.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
 await expect(later.getByRole('button',{name:'Deny',exact:true})).toBeEnabled();
 await later.getByRole('button',{name:'Deny',exact:true}).click();
 await expect(later).toHaveCount(0);
 await expect(historyEntry()).toHaveText('Permissions history (3)');
 // Touch activation in a real narrow touch context; no synthetic DOM click.
 const touchContext=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,extraHTTPHeaders:{Authorization:'Bearer fixture-approval-control-token'}});
 const touch=await touchContext.newPage();
 await touch.goto(url);await showFixtureConversation(touch);
 await touch.getByRole('button',{name:'Permissions history (3)',exact:true}).tap();
 await expect(touch.locator('#workspace-canvas [data-permission-history-id="capability:later"]')).toContainText('Permission denied');
 assert.equal(await touch.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await touch.screenshot({path:join(output,'history-touch.png'),animations:'disabled'});
 await touch.getByRole('button',{name:'Close canvas panel',exact:true}).tap();
 await expect(touch.locator('#workspace-canvas')).toBeHidden();await touchContext.close();
 // Dirty renderer-local edits survive history browsing. Replacement still refuses.
 await page.setViewportSize({width:1400,height:900});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('canvas.show',{kind:'markdown',title:'Unfinished document',content:'# Retained original'});
 const manifest={id:'example.permission-editor',label:'Permission history test editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("label",null,"Retained local edit",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const staged=await action('shell.packages.stage',{manifest,source}),digest=staged.result.digest;
 assert.equal((await action('shell.packages.validate',{digest})).result.status,'passed');
 await panel.getByRole('button',{name:'Canvas details',exact:true}).click();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const edit=page.getByLabel('Retained local edit', {exact:true});
 await edit.fill('Keep unsaved permission review edit');
 await panel.getByRole('button',{name:'Close canvas details',exact:true}).click();
 const views=async()=>(await action('canvas.views.inspect')).result.views;
 await expect.poll(async()=>(await views()).find(row=>row.viewId==='primary').dirty).toBe(true);
 const beforeView=(await views()).find(row=>row.viewId==='primary'),mount=await edit.getAttribute('data-mount');
 await historyEntry().click();await expect(history).toBeVisible();await expect(edit).toBeHidden();
 const refusal=await action('canvas.show',{kind:'text',title:'Must not replace',content:'not accepted'}).then(value=>({value}),error=>({error:error.message}));
 assert.match(refusal.error,/viewer edit/);
 await panel.getByRole('tab',{name:'Unfinished document',exact:true}).click();
 await expect(edit).toHaveValue('Keep unsaved permission review edit');
 assert.equal(await edit.getAttribute('data-mount'),mount);
 const afterView=(await views()).find(row=>row.viewId==='primary');
 for(const key of ['viewId','resourceId','resourceRevision','generation','dirty'])assert.equal(afterView[key],beforeView[key],key);
 await expect(composer).toHaveDraft('Keep this private draft');
 await page.screenshot({path:join(output,'retained-dirty-editor.png'),animations:'disabled'});
 // Newer Close wins even when the history opening request is slow.
 let releaseHistory,historyHeld=false;
 const gate=new Promise(resolve=>{releaseHistory=resolve});
 const holdHistory=async route=>{
  const raw=route.request().postDataJSON(),body=raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
  if(body?.action==='view.update'&&body.args?.patch?.canvasDraft?.delivery==='permissions'){historyHeld=true;await gate}
  await route.continue();
 };
 await page.route('**/api/actions',holdHistory);
 await historyEntry().click();
 await expect.poll(()=>historyHeld).toBe(true);
 await panel.getByRole('button',{name:'Close canvas panel',exact:true}).click();
 await expect(panel).toBeHidden();
 releaseHistory();
 await expect(historyEntry()).toBeEnabled();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas.open)).toBe(false);
 await page.unroute('**/api/actions',holdHistory);
 assert.equal((await views()).find(row=>row.viewId==='primary').dirty,true);
 const final=await inspect();
 assert.deepEqual(final.starts,[]);assert.deepEqual(final.inputs,[]);assert.deepEqual(final.controls,[]);assert.deepEqual(final.decisions,[]);
 assert.equal(final.approvals.filter(r=>r.status==='pending').length,0);
 assert.equal(final.attention.length,0);assert.notEqual(final.navigation.label,'Approval requested');
 assert.deepEqual(errors,[]);
 const report={passed:true,source:final.source,authoritySha256:final.authoritySha256,coldState:true,
  passiveReview:true,twoTabs:true,scopeRace:true,expiryRace:true,exactDismiss:true,adjacentCycle:true,
  keyboard:true,narrow:true,touch:true,reload:true,privateDraft:true,canvasHistory:true,laterPermission:true,dirtyRefusal:true,retainedEditor:true,lateClose:true,scriptedRuntime:true,realProvider:false};
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({output,...report}));
}catch(error){
 if(page){await page.screenshot({path:join(output,'failure.png'),fullPage:true}).catch(()=>{});await writeFile(join(output,'failure.txt'),(await page.locator('body').innerText().catch(()=>''))+'\n'+error.stack)}
 console.error(JSON.stringify({output,passed:false,error:error.message}));throw error;
}finally{
 await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){
  const ended=once(fixture,'exit');fixture.kill('SIGTERM');
  const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);
  try{await ended}finally{clearTimeout(timer)}
 }
 await writeFile(join(output,'cleanup.json'),JSON.stringify({exitCode:fixture.exitCode,signal:fixture.signalCode}));
}