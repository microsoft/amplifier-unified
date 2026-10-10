// Real file-backed native history and production UI; no model work permitted.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/history_loading_server.py','--retained-history'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const {url}=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),60000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{out+=chunk;for(const line of out.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1440,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-history-loading'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url+'/login');await page.getByLabel('Username').fill('history-fixture');await page.getByLabel('Password').fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');
 const metrics=async()=>(await page.request.get(url+'/api/fixture/history-metrics')).json();
 const initial=await metrics(),id=initial.cases[0].id;
 const select=id=>page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),id);
 const rows=page.locator('[data-message-id]');
 const firstNumber=async()=>Number((await rows.first().innerText()).match(/Saved message (\d+)/)?.[1]);
 await select(id);await expect(rows).toHaveCount(100);await expect.poll(firstNumber).toBe(9900);
 // Navigation includes unloaded history; jumping does not load the whole file.
 const index=await(await page.request.get(url+'/api/conversation/navigation?'+new URLSearchParams({sessionId:id}))).json();
 assert.equal(index.turns.length,5000);
 await expect(page.locator('.a-rail-mark').first()).toHaveAttribute('data-turn-id',index.turns[0].id);
 await page.locator('.a-rail-mark').first().click();
 await expect(page.locator(`[data-message-id="${index.turns[0].id}"]`)).toHaveCount(1);
 assert.ok(await rows.count()<=100);
 await page.getByRole('button',{name:'Return to latest',exact:true}).click();
 await expect(rows).toHaveCount(100);await expect.poll(firstNumber).toBe(9900);
 // Cross nine retained pages and then the native disk-page boundary.
 for(let first=9800;first>=8900;first-=100){
  await page.getByRole('button',{name:'Load earlier conversation',exact:true}).evaluate(el=>el.click());
  await expect.poll(firstNumber).toBe(first);
 }
 assert.equal(await rows.count(),1100);
 const ids=await rows.evaluateAll(elements=>elements.map(el=>el.dataset.messageId));
 assert.equal(new Set(ids).size,ids.length);
 const reading=rows.nth(150),readingId=await reading.getAttribute('data-message-id');
 await reading.evaluate(el=>el.scrollIntoView({block:'start'}));
 await page.locator('.a-messages').evaluate(el=>{el.dispatchEvent(new WheelEvent('wheel',{deltaY:-1}));el.dispatchEvent(new Event('scroll'))});
 const offset=()=>page.locator(`[data-message-id="${readingId}"]`).evaluate(el=>el.getBoundingClientRect().top-el.closest('.a-messages').getBoundingClientRect().top);
 const before=await offset();
 // Another reader expanding the shared native window must not reset ours.
 const expansion=await(await page.request.post(url+'/api/fixture/other-reader')).json();
 assert.deepEqual(expansion,{before:8900,after:8800});
 await page.waitForFunction(id=>window.amplifier.getState().sessions.find(row=>row.id===id)?.sharedHistoryOffset===8800,id);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(await firstNumber(),8900);await expect.poll(offset).toBeCloseTo(before,0);
 const diskPages=(await metrics()).loads.filter(row=>row.before!=null).length;
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).evaluate(el=>el.click());
 await expect.poll(firstNumber).toBe(8800);await expect.poll(offset).toBeCloseTo(before,0);
 assert.equal((await metrics()).loads.filter(row=>row.before!=null).length,diskPages,'Read retained rows before requesting another disk page');
 await select(initial.home);await expect(rows).toHaveCount(0);await select(id);
 await expect(page.locator(`[data-message-id="${readingId}"]`)).toHaveCount(1);
 await expect.poll(offset).toBeCloseTo(before,0);
 assert.ok(await rows.count()<=100,'Return restores a bounded reading window');
 await page.reload();await expect(page.locator(`[data-message-id="${readingId}"]`)).toHaveCount(1);
 await expect.poll(offset).toBeCloseTo(before,0);
 assert.ok(await rows.count()<=100,'Reload restores a bounded reading window');
 const final=await metrics();assert.equal(final.unchanged,true);assert.deepEqual(final.runtimeCalls,[]);assert.deepEqual(errors,[]);
 console.log('Retained history passed: bounded reopen, full-history navigation, retained and disk paging, unique rows, reading position after switch/reload, unchanged source and no model work.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
