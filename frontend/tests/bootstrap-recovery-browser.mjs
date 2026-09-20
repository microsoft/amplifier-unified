// Production assets and disposable retained history; no provider calls/user data.
import {chromium} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+fixtureLog)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+fixtureLog))})});
const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
const control=async body=>(await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)})).json();
const seeded=await control({op:'heavy',active:true,messages:520,otherMessages:549,chars:5000,nodes:400,nodeBytes:10000,extraMessageCounts:[12,12,11,11,11,11,11,11]});
assert.equal(seeded.retainedSessions,10);assert.equal(seeded.retainedMessages,1159);assert.ok(seeded.canonicalBytes>5842698);
const compact=await (await fetch(base+'/api/state',{headers})).text();assert.ok(Buffer.byteLength(compact)<1000000);
let browser;
try{
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const start=Date.now();await page.goto(base);await page.getByRole('button',{name:'Settings',exact:true}).waitFor();const shellMs=Date.now()-start;assert.equal(await page.locator('[data-message-id]').count(),60);
 let blockedDetail;await page.route('**/api/conversation/detail?**',route=>{blockedDetail=route});
 await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();
 await page.getByText('Loading earlier messages…',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Settings',exact:true}).click();await page.locator('[data-settings-section=history]').click();await page.getByRole('heading',{name:'History & recovery',exact:true}).waitFor();
 assert.ok(blockedDetail,'Slow history is independent of Settings/Maintenance');
 await blockedDetail.fulfill({status:503,json:{error:'Fixture history temporarily unavailable'}});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await page.getByRole('alert').filter({hasText:'Fixture history temporarily unavailable'}).waitFor();
 await page.unroute('**/api/conversation/detail?**');await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-message-id]').length===120);
 const messageIds=await page.locator('[data-message-id]').evaluateAll(els=>els.map(el=>el.dataset.messageId));assert.equal(new Set(messageIds).size,120);
 const alpha=await (await fetch(base+'/api/state?sessionId='+seeded.alpha,{headers})).json(),beta=await (await fetch(base+'/api/state?sessionId='+seeded.beta,{headers})).json();
 assert.equal(alpha.sessions.find(s=>s.id===seeded.alpha).messages.length,520);assert.equal(beta.sessions.find(s=>s.id===seeded.beta).messages.length,549);
 assert.ok((await page.getByRole('textbox',{name:'Message Amplifier'}).isEnabled()),'Retained chat stays usable');

 // The event stream can bootstrap the UI even when the initial state read stalls.
 const streamed=await context.newPage();await streamed.route('**/api/state',()=>{});await streamed.goto(base);await streamed.getByRole('button',{name:'Settings',exact:true}).waitFor({timeout:5000});await streamed.close();
 // With neither state source responding, show a finite retry and recover via HTTP.
 const retry=await context.newPage();let first=true;
 await retry.route('**/api/state',route=>{if(first){first=false;return}return route.continue()});
 await retry.route('**/api/events*',route=>route.abort());
 await retry.goto(base);await retry.getByRole('button',{name:'Retry connection',exact:true}).waitFor({timeout:13000});await retry.getByRole('button',{name:'Retry connection',exact:true}).click();await retry.getByRole('button',{name:'Settings',exact:true}).waitFor();await retry.close();
 const retained=await control({op:'patch'});assert.equal(retained.retainedMessages,1159);assert.equal(retained.retainedSessions,10);assert.deepEqual(errors,[]);
 const result={sessions:10,messages:1159,canonicalBytes:seeded.canonicalBytes,browserBytes:Buffer.byteLength(compact),initialMessages:60,loadedMessages:120,shellMs,maintenanceDuringSlowDetail:true,recoveredDetailFailure:true,stateStreamFallback:true,startupRetry:true,historyRetained:true};
 await writeFile('/tmp/amplifier-bootstrap-recovery-results.json',JSON.stringify(result,null,2));console.log(result);
}finally{await browser?.close();fixture.kill()}
