import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/worktrees_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exited '+code)))});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 // Select the supported SSE lane; do not replace actions or event payloads.
 await page.route('**/api/clients/attach',async route=>{
  const response=await route.fetch(),body=await response.json();body.transports=['http','sse'];
  await route.fulfill({response,json:body});
 });
 let sse=false;
 page.on('response',response=>{if(response.url().includes('/api/events?'))sse=true});
 const checks=[],actions=[];
 page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/actions')){
  const data=request.postDataJSON();actions.push(data);if(data.action==='worktree.check')checks.push(data);
 }});
 await page.goto(ready.url);await showFixtureConversation(page);await page.getByRole('region',{name:'Task worktrees'}).waitFor();
 const panel=page.getByRole('region',{name:'Task worktrees'});
 await panel.getByRole('button',{name:'Inspect repository',exact:true}).click();await expect(panel.getByText('main · Checkout clean',{exact:true})).toBeVisible();
 await expect(panel.locator('[data-part=execution-host]')).toHaveText(ready.host.label);
 await panel.getByRole('button',{name:'Create managed checkout'}).click();const card=panel.locator('[data-worktree-id]').first();await expect(card.getByRole('status')).toHaveText('Checkout ready');await expect(card.getByText('Local host: '+ready.host.label,{exact:true})).toBeVisible();
 await page.request.post(ready.url+'/fixture/wait-turn');
 await card.getByRole('button',{name:'Use this folder next'}).click();
 const initial=panel.locator('[data-handoff-id]').first();
 await expect(initial.locator('[data-part=move-status]')).toHaveText('Folder choice saved');
 const before=await (await page.request.get(ready.url+'/fixture/move-observations')).json();
 const firstId=await initial.getAttribute('data-handoff-id');
 // Keep the original saved identity, not a position that changes when the
 // canonical collection inserts the newer desired folder.
 const first=panel.locator(`[data-handoff-id="${firstId}"]`);
 await expect(first.getByRole('button',{name:'Check move',exact:true})).toHaveCount(0);
 await expect(panel.locator('[data-part=execution-directory]')).toHaveText(ready.root);
 await expect(panel.locator('[data-part=next-execution-directory]')).not.toHaveText(ready.root);
 await expect(card.getByRole('button',{name:'Archive and preserve files'})).toBeDisabled();
 await expect(card.getByRole('button',{name:'Remove clean checkout'})).toBeDisabled();
 const home=panel.getByRole('button',{name:'Use home folder next',exact:true});
 await home.focus();await page.keyboard.press('Enter');
 await expect(panel.locator('[data-part=next-execution-directory]')).toHaveText(ready.root);
 await expect(first.locator('[data-part=move-status]')).toHaveText('Not applied — a newer folder was chosen');
 await card.getByRole('button',{name:'Use this folder next'}).click();
 await expect(panel.locator('[data-part=next-execution-directory]')).not.toHaveText(ready.root);
 const after=await (await page.request.get(ready.url+'/fixture/move-observations')).json();
 assert.equal(after.moves.length,3);
 assert.equal(after.moves.find(row=>row.id===firstId).supersededBy,after.moves.find(row=>row.target===ready.root).id);
 assert.equal(after.releaseCalls,before.releaseCalls);assert.equal(after.executionRevision,before.executionRevision);
 assert.equal(after.messages,before.messages);
 assert.equal(after.moves.filter(row=>row.phase==='pending').length,1);
 assert.ok(after.moves.every(row=>row.placementMode==='deferred-retirement'&&typeof row.createdAt==='number'));
 assert.equal(typeof after.moves[0].revision,'number');
 await expect(card.getByRole('button',{name:'Use this folder next'})).toBeDisabled();
 await page.request.post(ready.url+'/fixture/finish-turn');
 await expect(panel.locator('[data-part=move-status]').filter({hasText:/^Folder changed$/})).toHaveCount(1);
 await expect(panel.locator('[data-part="execution-directory"]')).not.toHaveText(ready.root);
 await card.getByRole('button',{name:'Inspect checkout and manifest'}).click();await expect(panel.getByText('Checkout inspection',{exact:true})).toBeVisible();
 await panel.locator('[data-part="execution-directory"]').scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/unified-worktrees-desktop.png'});
 await page.reload();await page.getByRole('region',{name:'Task worktrees'}).waitFor();await expect(panel.locator('[data-part="execution-directory"]')).not.toHaveText(ready.root);
 await page.request.post(ready.url+'/fixture/fail-release');await panel.getByRole('button',{name:'Use home folder next'}).click();
 const unknown=panel.locator('[data-handoff-id]').filter({has:page.locator('[data-part=move-status]',{hasText:'Folder change needs recovery'})});
 await expect(unknown).toHaveCount(1);await expect(unknown.getByText(/Work was not replayed/i)).toBeVisible();
 const unknownId=await unknown.getAttribute('data-handoff-id');
 const failed=await (await page.request.get(ready.url+'/fixture/move-observations')).json();
 await expect(unknown.getByRole('button',{name:'Check move',exact:true})).toHaveCount(0);
 const checked=await (await page.request.get(ready.url+'/fixture/move-observations')).json();
 assert.equal(checked.moves.length,4);assert.equal(checked.releaseCalls,failed.releaseCalls);
 assert.equal(checked.executionRevision,failed.executionRevision);assert.equal(checked.messages,failed.messages);
 assert.equal(checked.moves.find(row=>row.id===unknownId).phase,'unknown');
 await expect(panel.getByLabel('What did you verify?')).toHaveCount(0);
 await expect(panel.getByRole('button',{name:/Use (source|target) folder/})).toHaveCount(0);
 await expect(panel.getByRole('button',{name:'Use home folder next'})).toBeDisabled();
 await page.setViewportSize({width:390,height:844});await panel.locator('[data-part="execution-directory"]').scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/unified-worktrees-mobile.png'});
 assert.deepEqual(errors,[]);assert.equal(checks.length,0);assert.ok(sse,'actual SSE stream was used');
 assert.equal(actions.filter(row=>row.action==='worktree.handoff').length,4);
 console.log(JSON.stringify({status:'passed',transport:'production act/HTTP/SSE',realGit:true,
   ordinaryChecks:0,explicitChoices:4,currentAuthoritativeUntilCommit:true,activeTurnNoRelease:true,
   sameProjectionSupersession:true,unknownPreserved:true,pendingTargetCleanupDisabled:true,
   keyboardChoice:true,placementNotContinuation:true,mobileNoOverflow:true,pageErrors:errors}));
}finally{await browser?.close();fixture.kill()}
