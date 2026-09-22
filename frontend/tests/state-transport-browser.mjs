// Production assets, real HTTP/SSE and a disposable Spark-sized catalog.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/active_client_performance_server.py'],{stdio:['ignore','pipe','inherit'],env:{...process.env,AMPLIFIER_TRANSPORT_FIXTURE:'1'}});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),45000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-active-client-token'},viewport:{width:1440,height:1000}}),page=await context.newPage();
 const errors=[],responses=[],frames=[],paint=[];let measured=false;
 page.on('pageerror',error=>errors.push(error.message));
 page.on('response',response=>{if(measured&&response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/actions')responses.push(response.body().then(body=>({action:response.request().postDataJSON().action,bytes:body.length})))});
 const cdp=await context.newCDPSession(page);await cdp.send('Network.enable');
 cdp.on('Network.eventSourceMessageReceived',event=>{if(measured)frames.push({kind:event.eventName,bytes:Buffer.byteLength(event.data)})});
 await page.goto(url);await page.getByRole('button',{name:'Settings',exact:true}).waitFor();
 const initialBytes=await page.evaluate(()=>JSON.stringify(window.amplifier.getState()).length);
 assert.ok(initialBytes>2_000_000);
 assert.equal(await page.evaluate(()=>!!window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId).execution.retiredUsageNodes),false);
 measured=true;
 for(let i=0;i<12;i++)await page.evaluate(value=>window.amplifier.dispatch('view.update',{patch:{navWidth:300+value}}),i);
 // Hold real writes in transit and prove painting does not wait for them.
 await page.route('**/api/actions',async route=>{const action=route.request().postDataJSON()?.action;if(['view.update','shell.changes.prepare','shell.changes.apply'].includes(action))await new Promise(resolve=>setTimeout(resolve,500));await route.continue()});
 paint.push(await page.evaluate(async()=>{const start=performance.now();window.pendingSettings=window.amplifier.dispatch('view.update',{patch:{panel:'settings'}});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {kind:'settings',ms:performance.now()-start,visible:!!document.querySelector('[role="dialog"]')}}));
 await page.evaluate(()=>window.pendingSettings);
 await page.locator('[data-settings-section="appearance"]').click();
 const dark=page.getByRole('button',{name:'Dark',exact:true});await dark.waitFor();
 const mode=await page.evaluate(async()=>{const button=[...document.querySelectorAll('button')].find(node=>node.textContent.trim()==='Dark'),start=performance.now();button.click();await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {kind:'appearance',ms:performance.now()-start,visible:document.getElementById('amp-one').style.colorScheme==='dark'}});paint.push(mode);
 await expect(dark).toBeEnabled();
 assert.equal(await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation.scheme),'dark');
 // Failed persistence rolls back the preview and retains the saved appearance.
 await page.unroute('**/api/actions');
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='shell.changes.apply'){await new Promise(resolve=>setTimeout(resolve,300));return route.fulfill({status:409,json:{accepted:false,error:'Fixture rejected appearance'}})}await route.continue()});
 await page.getByRole('button',{name:'Light',exact:true}).click();
 await expect(page.getByText('Fixture rejected appearance',{exact:true})).toBeVisible();
 assert.equal(await page.locator('#amp-one').evaluate(node=>node.style.colorScheme),'dark');
 await page.unroute('**/api/actions');
 measured=false;
 const receipts=await Promise.all(responses),deltas=frames.filter(row=>row.kind==='state-delta');
 assert.ok(deltas.length>=12);assert.ok(receipts.every(row=>row.bytes<16_000),JSON.stringify(receipts));
 assert.ok(deltas.every(row=>row.bytes<30_000),JSON.stringify(deltas));
 assert.ok(paint.every(row=>row.visible&&row.ms<250),JSON.stringify(paint));
 // A fresh page gets an authoritative baseline after reconnect.
 await page.reload();await page.getByRole('button',{name:'Settings',exact:true}).waitFor();
 assert.equal(await page.locator('#amp-one').evaluate(node=>node.style.colorScheme),'dark');
 assert.deepEqual(errors,[]);
 const result={initialBytes,actionCount:receipts.length,maxActionBytes:Math.max(...receipts.map(row=>row.bytes)),deltaCount:deltas.length,maxDeltaBytes:Math.max(...deltas.map(row=>row.bytes)),paint,reconnect:true,rejectionRollback:true,modelCalls:0};
 if(process.env.AMPLIFIER_PERF_EVIDENCE)await writeFile(process.env.AMPLIFIER_PERF_EVIDENCE,JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result,null,2));
}finally{await browser?.close();fixture.kill()}
