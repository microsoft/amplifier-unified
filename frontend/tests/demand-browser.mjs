import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.UNIFIED_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/demand_ui_server.py',import.meta.url))],{stdio:'inherit'});
let browser;
try{
 for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),reads=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/conversation/detail'))reads.push(r.url())});
 await page.goto('http://127.0.0.1:8958/');
 const third=page.locator('[data-turn-id="voice:third"]'),toggle=third.locator('button.a-execution-turn-line');await toggle.waitFor();
 assert.equal(reads.length,0,'collapsed groups do not read steps');
 const execution=await page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(x=>x.id===s.selectedSessionId).execution});
 assert.deepEqual(execution.nodes,[]);assert.equal(execution.detailsDeferred,true);
 let reject=true;
 await page.route('**/api/conversation/detail?**',async route=>{
  if(reject&&new URL(route.request().url()).searchParams.has('group')){reject=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Fixture detail unavailable'})})}else await route.continue();
 });
 await toggle.click();await third.getByRole('alert').waitFor();await third.getByRole('button',{name:'Retry loading work'}).click();
 await third.locator('[data-node-id="bulk-124"]').waitFor();assert.equal(await third.locator('.a-execution-node').count(),100);
 assert.equal(reads.filter(url=>new URL(url).searchParams.has('field')).length,0,'large bodies remain unloaded');
 await third.getByRole('button',{name:'Load earlier steps'}).click();await third.locator('[data-node-id="bulk-0"]').waitFor();
 assert.equal(await third.locator('.a-execution-node').count(),126);
 await third.locator('[data-node-id="bulk-0"] button.a-execution-action-line').click();
 await page.waitForFunction(()=>document.querySelector('[data-node-id="bulk-0"]')?.textContent.includes('large result '.repeat(30)));
 assert.ok(reads.some(url=>new URL(url).searchParams.get('field')==='output'));
 await toggle.click();assert.equal(await third.locator('.a-execution-node').count(),0);
 // A late response after closing must neither render nor retain hidden steps.
 await page.unroute('**/api/conversation/detail?**');let release,started;
 const gate=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>started=resolve);
 await page.route('**/api/conversation/detail?**',async route=>{started();await gate;try{await route.continue()}catch{}});
 await toggle.click();await entered;await toggle.click();release();await page.waitForTimeout(200);
 assert.equal(await third.locator('.a-execution-node').count(),0);await page.unroute('**/api/conversation/detail?**');
 await toggle.click();await third.locator('[data-node-id="bulk-124"]').waitFor();assert.equal(await third.locator('.a-execution-node').count(),100,'reopening reads a bounded page');
 // Live changes refresh an expanded group from its new summary validator.
 const identities=await page.evaluate(async()=> (await fetch('/api/fixture/work',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'update-step'})})).json());
 await third.getByText('Updated last step',{exact:true}).waitFor();
 // Visit a second chat, then delay returning to the cached first chat.
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),identities.second);
 await page.getByText('Second chat content',{exact:true}).waitFor();
 let releaseNavigation,enteredNavigation;
 const navigationGate=new Promise(resolve=>releaseNavigation=resolve),navigationEntered=new Promise(resolve=>enteredNavigation=resolve);
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='session.select'){enteredNavigation();await navigationGate}await route.continue()});
 await page.evaluate(id=>{window.pendingNavigation=window.amplifier.dispatch('session.select',{id})},identities.first);
 await navigationEntered;await page.getByRole('status').filter({hasText:'Loading conversation…'}).waitFor();
 assert.equal(await page.locator('[data-message-id="voice-user"]').count(),0,'cached transcript is not displayed');
 assert.equal(await page.getByText('Second chat content',{exact:true}).count(),0,'previous chat is not displayed');
 releaseNavigation();await page.evaluate(()=>window.pendingNavigation);
 await page.locator('[data-message-id="voice-user"]').waitFor();await page.unroute('**/api/actions');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,collapsedStepReads:0,initialNodes:execution.nodes.length,summaryBytes:JSON.stringify(execution).length,checks:['lazy steps','bounded paging','lazy bodies','error retry','collapse race','bounded reopen','live refresh','fresh navigation']}));
}finally{await browser?.close();fixture.kill()}
