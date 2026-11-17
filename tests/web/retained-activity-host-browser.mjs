// Actual Unified host/actions/SSE + exact candidate client; synthetic runtime only.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/retained-activity-host';
const source=process.env.AMPLIFIER_TEST_CLIENT_SOURCE||fileURLToPath(new URL('../../.build/web-client/',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||'python',['-u',fileURLToPath(new URL('../fixtures/retained_activity_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','pipe'],env:process.env});
let logs='',vite,browser,page,primary;
const proof={scope:'candidate source host/browser; synthetic questions and runtime, not installed/released/live acceptance',checkpoints:[]};
const errors=[];
fixture.stderr.on('data',value=>logs+=value);
const ready=new Promise((resolve,reject)=>{let output='';fixture.stdout.on('data',value=>{output+=value;const line=output.split('\n').find(row=>row.startsWith('{"url":'));if(line)resolve(JSON.parse(line))});fixture.once('error',reject);fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${logs}`)))});
try{
 await mkdir(out,{recursive:true});
 const {url:target,owner,peer}=await ready;
 vite=await createServer({configFile:false,root:source,cacheDir:'/tmp/retained-activity-vite-cache',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,req=>req.setHeader('Origin',target))}},'/branding':target,'/fixture':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),...(process.env.CHROMIUM_SINGLE_PROCESS==='1'?{args:['--single-process','--no-zygote','--no-sandbox']}: {})});
 page=await browser.newPage({viewport:{width:1440,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.stack||error.message));
 const shell=async()=>{await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats);return page.evaluate(()=>window.amplifier.getShellState().snapshots.chats)};
 const mode=mode=>page.evaluate(async mode=>{const r=await fetch('/fixture/activity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});if(!r.ok)throw Error('Fixture transition '+r.status);return r.json()},mode);
 await page.goto(vite.resolvedUrls.local[0]);
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 const opened=await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{workSurface:'chat',navPinned:true}}));
 assert.equal(opened.accepted,true);
 await page.waitForFunction(()=>window.amplifier?.getState()?.view?.workSurface==='chat');
 const pin=page.locator(`.a-pinned-chats [data-session-id="${owner}"]`);
 const recent=page.locator(`.a-recent-shortcuts [data-session-id="${owner}"]`);
 const status=locator=>locator.locator('.a-navigation-status');
 await expect(pin).toHaveCount(1);await expect(recent).toHaveCount(1);
 await expect(status(pin)).toHaveAttribute('aria-label','Answer requested');
 await pin.scrollIntoViewIfNeeded();
 const geometry=()=>pin.evaluate(node=>{const scroll=node.closest('.a-nav-content'),r=node.getBoundingClientRect();return {top:r.top,height:r.height,scrollTop:scroll.scrollTop,rows:scroll.querySelectorAll('[data-session-id]').length}});
 const before=await geometry();
 await mode('hold');
 await page.evaluate(async id=>{const r=await window.amplifier.dispatch('session.select',{id});if(!r.accepted)throw Error('Selection rejected')},peer);
 await expect.poll(async()=>(await shell()).selectedSessionId).toBe(peer);
 await expect.poll(async()=>Object.keys((await shell()).navigationResourceRetained||{})).toContain('pinnedNavigation');
 const held=await shell();
 assert.equal(held.navigationResources.includes('pinnedNavigation'),false);
 assert.equal(held.navigationResources.includes('recentNavigation'),false);
 assert.equal(held.navigationResourcePending.pinnedNavigation.status,'pending');
 await expect(status(pin)).toHaveAttribute('aria-label','Answer requested');
 const after=await geometry();assert.equal(after.rows,before.rows);assert.ok(Math.abs(after.scrollTop-before.scrollTop)<1);
 await pin.locator('.a-nav-chat-select').focus();
 await pin.getByRole('button',{name:'Details and actions for Question owner',exact:true}).click();
 const details=page.getByRole('dialog',{name:'Details for Question owner',exact:true});
 const detailStatus=details.locator('.a-navigation-detail-status strong');
 await expect(detailStatus).toHaveText('Answer requested');
 for(const [transition,label] of [['answered','Working'],['pending','Answer requested'],['unknown','Check answer delivery'],['rejected','Check answer delivery'],['multiple','Answer requested']]){
  await mode(transition);
  await expect.poll(async()=>(await shell()).navigationActivityById?.[owner]?.label).toBe(label);
  await expect(status(pin)).toHaveAttribute('aria-label',label);
  await expect(status(recent)).toHaveAttribute('aria-label',label);
  await expect(detailStatus).toHaveText(label);
  assert.equal((await shell()).navigationResources.includes('pinnedNavigation'),false);
  proof.checkpoints.push({transition,label,selected:peer,pending:true,openDetails:true});
 }
 await page.screenshot({path:out+'/retained-current.png'});
 await mode('fresh');
 await expect.poll(async()=>(await shell()).navigationResourcePending.pinnedNavigation).toBeUndefined();
 await expect(status(pin)).toHaveAttribute('aria-label','Working');
 const info=await page.evaluate(async()=>{const r=await fetch('/fixture/activity');return r.json()});
 assert.deepEqual(info.runtimeStarts,[]);assert.deepEqual(info.runtimeSends,[]);assert.equal(info.historyUnchanged,true);assert.deepEqual(errors,[]);
 proof.status='PASS';proof.owner=owner;proof.peer=peer;proof.geometry={before,after};proof.info=info;proof.errors=errors;
 console.log(JSON.stringify(proof,null,2));
}catch(error){primary=error;proof.status='FAIL';proof.failure=String(error.stack||error);if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{
 proof.errors=errors;
 if(page)await writeFile(out+'/final.html',await page.content()).catch(()=>{});
 await writeFile(out+'/result.json',JSON.stringify(proof,null,2)+'\n');
 let failure;
 for(const cleanup of [()=>browser?.close(),()=>vite?.close(),async()=>{if(fixture.exitCode!==null)return;const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{const [code,signal]=await done;if(code!==0)throw Error(`Fixture cleanup ${code}/${signal}`)}finally{clearTimeout(timer)}}])try{await cleanup()}catch(error){failure??=error}
 await writeFile(out+'/fixture.log',logs);
 if(!primary&&failure)throw failure;
}
