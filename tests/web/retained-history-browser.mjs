import {showFixtureConversation} from './browser-chat-pages.mjs';
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
 const errors=[],navigationReads=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/conversation/navigation')navigationReads.push(new URL(request.url()))});
 const login=async reader=>{await reader.goto(url+'/login');await reader.getByLabel('Username').fill('history-fixture');await reader.getByLabel('Password').fill('fixture-password');await reader.getByRole('button',{name:'Sign in',exact:true}).click();await reader.waitForSelector('#amp-one');await showFixtureConversation(reader);};
 await login(page);
 const metrics=async()=>(await page.request.get(url+'/api/fixture/history-metrics')).json();
 const initial=await metrics(),id=initial.cases[0].id;
 const select=id=>page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),id);
 const rows=page.locator('[data-message-id]');
 const firstNumber=async()=>Number((await rows.first().innerText()).match(/Saved message (\d+)/)?.[1]);
 const expectRange=async(elements,first,count)=>{
  await expect(elements).toHaveCount(count);
  assert.deepEqual(await elements.evaluateAll(nodes=>nodes.map(node=>Number(node.innerText.match(/Saved message (\d+)/)?.[1]))),
   Array.from({length:count},(_,i)=>first+i),'Reading window must remain contiguous and in source order');
 };
 // Native startup intentionally reads only20; explicitly requested older
 // pages retain their independent100-message size (TranscriptWindows).
 await select(id);await expect(rows).toHaveCount(20);await expect.poll(firstNumber).toBe(9980);
 await expectRange(rows,9980,20);
 // The navigator describes only this requested reading window. Opening and
 // using it must not enumerate all 5,000 turns in the saved transcript.
 await expect(page.locator('.a-rail-mark')).toHaveCount(10);
 await expect(page.locator('.a-rail-mark').first()).toHaveAttribute('data-turn-id',await rows.first().getAttribute('data-message-id'));
 await page.locator('.a-rail-mark').first().click();
 await expect.poll(()=>rows.first().evaluate(el=>Math.abs(el.getBoundingClientRect().top-el.closest('.a-messages').getBoundingClientRect().top))).toBeLessThan(100);
 await page.locator('.a-rail-mark').last().click();
 assert.equal(navigationReads.length,0,'Visible navigator jumps need no full-history index');
 await expectRange(rows,9980,20);
 // Read ten prior pages explicitly, preserving contiguous, unique rows.
 for(let first=9880;first>=8980;first-=100){
  await page.getByRole('button',{name:'Load earlier conversation',exact:true}).evaluate(el=>el.click());
  await expect.poll(firstNumber).toBe(first);
  await expectRange(rows,first,10000-first);
 }
 assert.equal(await rows.count(),1020);
 const ids=await rows.evaluateAll(elements=>elements.map(el=>el.dataset.messageId));
 assert.equal(new Set(ids).size,ids.length);
 const reading=rows.nth(150),readingId=await reading.getAttribute('data-message-id');
 await reading.evaluate(el=>el.scrollIntoView({block:'start'}));
 await page.locator('.a-messages').evaluate(el=>{el.dispatchEvent(new WheelEvent('wheel',{deltaY:-1}));el.dispatchEvent(new Event('scroll'))});
 // Let the scroll controller finish the pending paging/layout frame before
 // recording the position that the independent reader must preserve.
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const offset=()=>page.locator(`[data-message-id="${readingId}"]`).evaluate(el=>el.getBoundingClientRect().top-el.closest('.a-messages').getBoundingClientRect().top);
 const before=await offset();
 // An independent browser starts with its own latest page and can expand it
 // without moving this reader or inheriting its initial and ten older pages.
 const other=await browser.newPage({viewport:{width:1440,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-history-loading'}});
 other.on('pageerror',e=>errors.push(e.message));await login(other);
 await other.evaluate(id=>window.amplifier.dispatch('session.select',{id}),id);
 await expectRange(other.locator('[data-message-id]'),9980,20);
 await other.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expectRange(other.locator('[data-message-id]'),9880,120);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(await firstNumber(),8980);await expectRange(rows,8980,1020);await expect.poll(offset).toBeCloseTo(before,0);
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).evaluate(el=>el.click());
 await expect.poll(firstNumber).toBe(8880);await expect.poll(offset).toBeCloseTo(before,0);
 await expectRange(rows,8880,1120);
 await expectRange(other.locator('[data-message-id]'),9880,120);
 await other.close();
 await select(initial.home);await expect(rows).toHaveCount(0);await select(id);
 await expect(page.locator(`[data-message-id="${readingId}"]`)).toHaveCount(1);
 await expect.poll(offset).toBeCloseTo(before,0);
 assert.ok(await rows.count()<=100,'Return restores a bounded reading window');
 await page.reload();await expect(page.locator(`[data-message-id="${readingId}"]`)).toHaveCount(1);
 await expect.poll(offset).toBeCloseTo(before,0);
 assert.ok(await rows.count()<=100,'Reload restores a bounded reading window');
 assert.equal(navigationReads.length,0,'Native reading restoration never requests the full-history index');
 const final=await metrics();assert.equal(final.unchanged,true);assert.deepEqual(final.runtimeCalls,[]);assert.deepEqual(errors,[]);
 console.log('Retained history passed: cold bounded open, requested-window navigator, sequential paging, independent readers, unique rows, bounded reading position after switch/reload, unchanged source and no model work.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
