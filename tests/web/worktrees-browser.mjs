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
 await card.getByRole('button',{name:'Move execution here'}).click();
 const first=panel.locator('[data-handoff-id]').first();
 await expect(first.locator('[data-part=move-status]')).toHaveText('Waiting for this turn');
 const before=await (await page.request.get(ready.url+'/fixture/move-observations')).json();
 const firstId=await first.getAttribute('data-handoff-id');
 const check=first.getByRole('button',{name:'Check move',exact:true});
 await check.focus();await page.keyboard.press('Enter');
 await expect(first.getByText('Move checked. The saved move status updates here.',{exact:true})).toBeVisible();
 await expect(check).not.toHaveAttribute('aria-disabled','true');
 await expect(first.getByText('Checking move…',{exact:true})).toHaveCount(0);
 const after=await (await page.request.get(ready.url+'/fixture/move-observations')).json();
 assert.equal(after.moves.length,1);assert.equal(after.moves[0].id,firstId);
 assert.equal(after.releaseCalls,before.releaseCalls);assert.equal(after.executionRevision,before.executionRevision);
 assert.equal(after.messages,before.messages);
 assert.equal(after.moves[0].displayState,'waiting');assert.ok(Array.isArray(after.moves[0].waitingFor));
 assert.equal(typeof after.moves[0].revision,'number');
 await expect(card.getByRole('button',{name:'Move execution here'})).toBeDisabled();
 await page.request.post(ready.url+'/fixture/finish-turn');
 await expect(first.locator('[data-part=move-status]')).toHaveText('Moved');
 await expect(first.locator('[data-part=move-continuation]')).toHaveCount(0);
 await expect(panel.locator('[data-part="execution-directory"]')).not.toHaveText(ready.root);
 await card.getByRole('button',{name:'Inspect checkout and manifest'}).click();await expect(panel.getByText('Checkout inspection',{exact:true})).toBeVisible();
 await panel.locator('[data-part="execution-directory"]').scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/unified-worktrees-desktop.png'});
 await page.reload();await page.getByRole('region',{name:'Task worktrees'}).waitFor();await expect(panel.locator('[data-part="execution-directory"]')).not.toHaveText(ready.root);
 await page.request.post(ready.url+'/fixture/fail-release');await panel.getByRole('button',{name:'Return execution to home'}).click();
 const unknown=panel.locator('[data-handoff-id]').filter({has:page.locator('[data-part=move-status]',{hasText:'Needs recovery'})});
 await expect(unknown).toHaveCount(1);await expect(unknown.getByText(/No input was replayed/i)).toBeVisible();
 const unknownId=await unknown.getAttribute('data-handoff-id');
 const failed=await (await page.request.get(ready.url+'/fixture/move-observations')).json();
 await unknown.getByRole('button',{name:'Check move',exact:true}).click();
 await expect(unknown.getByText(/Move check: Needs recovery/)).toBeVisible();
 await expect(unknown.getByRole('button',{name:'Check move',exact:true})).not.toHaveAttribute('aria-disabled','true');
 const checked=await (await page.request.get(ready.url+'/fixture/move-observations')).json();
 assert.equal(checked.moves.length,2);assert.equal(checked.releaseCalls,failed.releaseCalls);
 assert.equal(checked.executionRevision,failed.executionRevision);assert.equal(checked.messages,failed.messages);
 assert.equal(checked.moves.find(row=>row.id===unknownId).phase,'unknown');
 await expect(panel.getByLabel('What did you verify?')).toHaveCount(0);
 await expect(panel.getByRole('button',{name:/Use (source|target) folder/})).toHaveCount(0);
 await expect(panel.getByRole('button',{name:'Return execution to home'})).toBeDisabled();
 await page.setViewportSize({width:390,height:844});await panel.locator('[data-part="execution-directory"]').scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/unified-worktrees-mobile.png'});
 assert.deepEqual(errors,[]);assert.equal(checks.length,2);assert.ok(sse,'actual SSE stream was used');
 assert.deepEqual(checks.map(row=>row.args.id),[firstId,unknownId]);
 assert.ok(checks.every(row=>typeof row.id==='string'&&row.args.sessionId===ready.sessionId));
 console.log(JSON.stringify({status:'passed',transport:'production act/HTTP/SSE',realGit:true,
   sameReceiptChecks:2,activeTurnNoRelease:true,unknownPreserved:true,noManualRecoveryEssay:true,
   keyboardCheckSettled:true,placementNotContinuation:true,mobileNoOverflow:true,pageErrors:errors}));
}finally{await browser?.close();fixture.kill()}
