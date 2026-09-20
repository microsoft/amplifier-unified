// Real HTTP/SSE and saved JSONL, with a synthetic runtime and temporary home.
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(root+'/.venv/bin/python',['-u',root+'/tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','pipe']});
let browser,log='';fixture.stderr.on('data',chunk=>log+=chunk);
try{
 const port=await new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);
  fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timeout);resolve(JSON.parse(line).port)}});
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code+': '+log))});
 });
 const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 const prepared=await control({op:'native-history'});
 const native=prepared.state.sessions.find(row=>row.nativeIdentity==='paging-fixture');assert.ok(native);
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers}),errors=[],pages=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST'){const body=request.postDataJSON();if(body?.action==='session.history')pages.push(body.args)}});
 await page.goto(base);
 await expect(page.locator('[data-message-id]')).toHaveCount(100);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this draft');
 const pane=page.locator('.a-messages');
 await page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight<3});
 // Keep the manual click from first causing a scroll-triggered request.
 await page.getByRole('button',{name:'Load earlier messages',exact:true}).evaluate(button=>button.click());
 await expect(page.locator('[data-message-id]')).toHaveCount(200);
 assert.equal(pages.length,1);assert.equal(pages[0].before,205);
 await control({op:'live-response',id:native.id});
 await expect(page.getByText('A new live response.',{exact:true})).toBeVisible();
 await expect(page.getByText('Still writing...',{exact:true})).toBeVisible();
 await page.waitForFunction(()=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId)?.status==='working');
 // Let the live-status/composer layout settle before measuring the reader's
 // position. Those updates are separate SSE messages from the page response.
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const anchor=await pane.evaluate(element=>{element.scrollTop=0;const first=element.querySelector('[data-message-id]');return {id:first.dataset.messageId,y:first.getBoundingClientRect().y}});
 await expect(page.locator('[data-message-id]')).toHaveCount(301);
 await page.waitForTimeout(100);
 const after=await page.locator(`[data-message-id="${anchor.id}"]`).boundingBox();
 assert.ok(Math.abs(after.y-anchor.y)<3,`Scroll anchor moved ${after.y-anchor.y}px`);
 assert.equal(pages.length,2);assert.equal(pages[1].before,105);
 const current=await control({op:'inspect'}),saved=current.state.sessions.find(row=>row.id===native.id);
 assert.equal(saved.sharedHistoryOffset,5);
 assert.equal(saved.status,'working');assert.equal(saved.streaming,'Still writing...');
 assert.equal(saved.messages.at(-1).text,'A new live response.');
 assert.equal(current.runtimeSends,prepared.runtimeSends,'Paging must not submit work');
 assert.equal(current.runtimeStarts,prepared.runtimeStarts,'Paging must not start a runtime');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Keep this draft');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,manualPageWhileReady:true,scrollPageWhileWorking:true,liveResponsePreserved:true,scrollAnchorDelta:after.y-anchor.y}));
}finally{await browser?.close();fixture.kill()}
