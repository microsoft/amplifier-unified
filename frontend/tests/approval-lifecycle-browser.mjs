import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
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
 await page.getByText('Permission history (1)',{exact:true}).click();
 await expect(page.locator('[data-permission-history-id="capability:old"]')).toContainText('Dismissed old request');
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
 await page.getByText('Permission history (2)',{exact:true}).click();
 await expect(page.locator('[data-permission-history-id="capability:current"]')).toContainText('Keep this note in history');
 await page.reload();
 await expect(composer).toHaveDraft('Keep this private draft');
 const final=await inspect();
 assert.deepEqual(final.starts,[]);assert.deepEqual(final.inputs,[]);assert.deepEqual(final.controls,[]);assert.deepEqual(final.decisions,[]);
 assert.equal(final.approvals.filter(r=>r.status==='pending').length,0);
 assert.equal(final.attention.length,0);assert.notEqual(final.navigation.label,'Approval requested');
 assert.deepEqual(errors,[]);
 const report={passed:true,source:final.source,authoritySha256:final.authoritySha256,coldState:true,
  passiveReview:true,twoTabs:true,scopeRace:true,expiryRace:true,exactDismiss:true,adjacentCycle:true,
  keyboard:true,narrow:true,reload:true,privateDraft:true,scriptedRuntime:true,realProvider:false};
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({output,...report}));
}catch(error){
 if(page){await page.screenshot({path:join(output,'failure.png'),fullPage:true}).catch(()=>{});await writeFile(join(output,'failure.txt'),(await page.locator('body').innerText().catch(()=>''))+'\n'+error.stack)}
 console.error(JSON.stringify({output,passed:false,error:error.message}));throw error;
}finally{await browser?.close();fixture.kill('SIGTERM')}