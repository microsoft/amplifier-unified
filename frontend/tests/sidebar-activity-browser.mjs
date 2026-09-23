// Packaged UI + real scoped HTTP/SSE; synthetic progress only, no model calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/active_client_performance_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout')),45000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});let output='';fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const d=JSON.parse(line);if(d.url){clearTimeout(timer);resolve(d.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-active-client-token'},viewport:{width:1280,height:1000}});
 const errors=[],traffic=[],reports=[];let measuring=false;
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{const path=new URL(r.url()).pathname;if(path==='/api/view')reports.push(r.postDataJSON());if(measuring&&['/api/shell','/api/view'].includes(path))traffic.push(r.method()+' '+path)});
 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats);
 const api=async(path,data)=>{const r=await page.request.post(url+path,{data});assert.equal(r.status(),200);return r.json()};
 const {sessions}=await (await page.request.get(url+'/fixture/metrics')).json();
 const dispatch=(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const shell=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const order=()=>page.locator('.a-nav-chat[data-session-id]').evaluateAll(rows=>rows.map(row=>row.dataset.sessionId));
 const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 const before=await order();
 // Progress in a different selected conversation must not reshuffle the sidebar.
 await dispatch('session.select',{id:sessions[1]});await api('/fixture/progress',{running:true});await wait(1500);
 measuring=true;await wait(4000);measuring=false;
 assert.deepEqual(await order(),before);assert.deepEqual(traffic,[],'steady streaming must not refetch shell or echo conversation text');
 await api('/fixture/progress',{running:false});await page.waitForFunction(id=>window.amplifier.getShellState().snapshots.chats.chatNavigation.items[0].id===id,sessions[0]);
 assert.equal((await order())[0],sessions[0]);
 // Explicit user preferences run through the same shared actions as agents.
 await page.getByRole('combobox',{name:'Sort conversations'}).selectOption('name');
 await page.waitForFunction(()=>window.amplifier.getShellState().snapshots.chats.chatNavigation.scope.sort==='name');
 const names=(await shell()).chatNavigation.items.map(r=>r.title.toLowerCase());assert.deepEqual(names,[...names].sort());
 await dispatch('session.pin',{id:sessions[1],pinned:true});await dispatch('session.pin',{id:sessions[0],pinned:true});
 await page.waitForFunction(ids=>JSON.stringify(window.amplifier.getShellState().snapshots.chats.pinnedSessionIds)===JSON.stringify(ids),[sessions[1],sessions[0]]);
 await expect(page.locator('.a-pin-handle')).toHaveCount(2);
 await page.locator(`[data-session-id="${sessions[0]}"] .a-pin-handle`).dragTo(page.locator(`[data-session-id="${sessions[1]}"]`));
 await page.waitForFunction(id=>window.amplifier.getShellState().snapshots.chats.pinnedSessionIds[0]===id,sessions[0]);
 await page.locator(`[data-session-id="${sessions[0]}"] .a-pin-handle`).focus();await page.keyboard.press('Alt+ArrowDown');
 await page.waitForFunction(id=>window.amplifier.getShellState().snapshots.chats.pinnedSessionIds[0]===id,sessions[1]);
 // Browser-only draft/focus and custom content remain observable; full explicit
 // readback still includes messages. display:contents must not lose slot text.
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this private draft');
 await page.evaluate(()=>{const div=document.createElement('div');div.dataset.shellComponent='fixture.custom';div.style.display='contents';div.textContent='Custom component observation';document.querySelector('#amp-one').append(div);document.dispatchEvent(new Event('selectionchange'))});
 await page.waitForTimeout(700);
 const latest=reports.at(-1);assert.equal(latest.visibleTextScope,'interface');assert.ok(latest.visibleText.includes('Custom component observation'));assert.ok(!latest.visibleText.includes('Synthetic saved reply'));
 assert.ok(latest.controls.some(c=>c.label==='Message Amplifier'&&c.value==='Keep this private draft'&&c.focused));
 assert.ok(await page.evaluate(()=>window.amplifier.getState().renderedView.visibleText.includes('Synthetic saved reply')));
 // The stream reconnect signal refreshes shell data even with no UI edit.
 const refreshed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/shell'&&r.status()===200);
 await page.evaluate(()=>window.dispatchEvent(new Event('amplifier-reconnected')));await refreshed;
 // Hidden clients defer shell reads/reports, then catch up on visibility restore.
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))});
 await wait(500);traffic.length=0;measuring=true;
 await api('/fixture/progress',{running:true});await wait(1000);await api('/fixture/progress',{running:false});await wait(500);measuring=false;
 assert.deepEqual(traffic,[]);
 await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))});await wait(700);
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.view?.navSort==='name');
 assert.deepEqual((await shell()).pinnedSessionIds,[sessions[1],sessions[0]]);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Keep this private draft');
 assert.deepEqual(errors,[]);
 const result={steadyStreamRequests:0,activityOrderStable:true,sortChoices:true,dragAndKeyboardPins:true,reloadPersistence:true,privateDraftRetained:true,customContentObserved:true,hiddenReportingDeferred:true,modelCalls:0};
 if(process.env.AMPLIFIER_SIDEBAR_EVIDENCE){await writeFile(process.env.AMPLIFIER_SIDEBAR_EVIDENCE,JSON.stringify(result,null,2)+'\n');await page.screenshot({path:process.env.AMPLIFIER_SIDEBAR_EVIDENCE+'.png'})}
 console.log(JSON.stringify(result,null,2));
}finally{await browser?.close();fixture.kill('SIGTERM')}
