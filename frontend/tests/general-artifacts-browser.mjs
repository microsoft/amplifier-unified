// Authored consumer journeys against the existing real synthetic host fixture.
// No response mocking, generated backend, paid SDK, or claim of execution here.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/empty_host_ui_server.py','--canvas-versions'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-canvas-versions'}});
 const page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(target,name,args={})=>target.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const snapshot=target=>target.evaluate(()=>window.amplifier.getState());
 const card=(target,title,version=1)=>target.locator(`.a-general-artifact[data-artifact-version="${version}"]`).filter({has:target.locator('strong',{hasText:title})});
 await action(page,'session.create');
 await action(page,'conversation.send',{text:'Create exact saved general artifacts.'});
 await expect.poll(async()=>{const state=await snapshot(page);return state.sessions.find(row=>row.id===state.selectedSessionId)?.status}).not.toBe('working');
 const png=await page.evaluate(()=>{
  const canvas=document.createElement('canvas');canvas.width=640;canvas.height=240;
  canvas.getContext('2d').fillRect(0,0,640,240);return canvas.toDataURL('image/png');
 });
 const image=(await action(page,'canvas.show',{kind:'image',title:'Ordinary PNG',content:png})).result;
 await action(page,'canvas.close');
 const imageCard=card(page,'Ordinary PNG');
 await imageCard.scrollIntoViewIfNeeded();await expect(imageCard.locator('.a-canvas-image')).toBeVisible();
 assert.equal(await page.locator('.a-inline-gallery').count(),0,'Ordinary PNG must not enter imagegen');
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:950});await imageCard.scrollIntoViewIfNeeded();
  assert.ok(await imageCard.evaluate(node=>Math.abs(node.getBoundingClientRect().width-node.parentElement.getBoundingClientRect().width)<=2),'Full available chat width');
  assert.ok(await imageCard.evaluate(node=>node.scrollWidth<=node.clientWidth),'No artifact overflow');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No shell overflow');
 }
 await page.emulateMedia({reducedMotion:'reduce'});
 await imageCard.getByRole('button',{name:'Collapse Ordinary PNG',exact:true}).focus();await page.keyboard.press('Enter');
 await expect(imageCard).toHaveAttribute('data-chat-mode','compact');
 await expect(imageCard.getByRole('button',{name:'Open Ordinary PNG in Canvas',exact:true})).toBeVisible();
 await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await expect(card(page,'Ordinary PNG')).toHaveAttribute('data-chat-mode','compact');
 const sid=(await snapshot(page)).selectedSessionId;
 const peer=await context.newPage();await peer.goto(url);await peer.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await action(peer,'session.select',{id:sid});
 await card(peer,'Ordinary PNG').scrollIntoViewIfNeeded();
 await expect(card(peer,'Ordinary PNG')).toHaveAttribute('data-chat-mode','inline');
 await card(page,'Ordinary PNG').getByRole('button',{name:'Open Ordinary PNG in Canvas',exact:true}).click();
 await expect.poll(async()=>(await snapshot(page)).canvas.open).toBe(true);
 await expect.poll(async()=>(await snapshot(page)).canvas.selectedVersion).toBe(1);
 assert.equal((await snapshot(page)).view.canvasFocused,false,'Open uses normal Canvas split');
 assert.equal((await snapshot(peer)).canvas.open,false,'Open never targets the other client');
 await peer.close();await action(page,'canvas.close');

 const html=(await action(page,'canvas.show',{kind:'html',title:'Dirty HTML',content:'<button onclick="this.textContent=\'Private edit retained\'">Try private edit</button>'})).result;
 await action(page,'canvas.close');
 const htmlCard=card(page,'Dirty HTML');await htmlCard.scrollIntoViewIfNeeded();
 await htmlCard.frameLocator('iframe').getByRole('button',{name:'Try private edit',exact:true}).click();
 const view=(await snapshot(page)).canvasWorkspace.views.find(row=>row.resourceId===html.id&&row.viewId.startsWith('inline-'));
 const target=Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
 await action(page,'canvas.views.dirty',{...target,dirty:true});
 await htmlCard.getByRole('button',{name:'Collapse Dirty HTML',exact:true}).click();
 await expect(htmlCard.locator('.a-inline-artifact-notice[role="status"]')).toContainText('Finish or cancel');
 await expect(htmlCard.frameLocator('iframe').getByRole('button',{name:'Private edit retained',exact:true})).toBeVisible();
 await expect(htmlCard).toHaveAttribute('data-chat-mode','inline');
 await action(page,'canvas.views.dirty',{...target,dirty:false});
 await htmlCard.getByRole('button',{name:'Collapse Dirty HTML',exact:true}).click();
 await expect(htmlCard.locator('iframe')).toHaveCount(0);
 assert.ok(!(await snapshot(page)).canvasWorkspace.views.some(row=>row.viewId===target.viewId));

 // Mount eight clean native previews in order, then demand the ninth. Earlier
 // views stay allocated; refusal must not render an empty successful body.
 await action(page,'session.create');await action(page,'conversation.send',{text:'Capacity qualification'});
 await expect.poll(async()=>{const state=await snapshot(page);return state.sessions.find(row=>row.id===state.selectedSessionId)?.status}).not.toBe('working');
 for(let n=1;n<=9;n++){
  await action(page,'canvas.show',{kind:'html',title:`Capacity ${n}`,content:`<p>Saved body ${n}</p>`});await action(page,'canvas.close');
  const current=card(page,`Capacity ${n}`);await current.scrollIntoViewIfNeeded();
  if(n<=8)await expect(current.locator('iframe')).toBeVisible();
  else{
   await expect(current).toHaveAttribute('data-chat-mode','compact');
   await expect(current.getByRole('status')).toContainText('Close a preview');
   await expect(current.getByRole('button',{name:`Open Capacity ${n} in Canvas`,exact:true})).toBeVisible();
   assert.equal((await snapshot(page)).canvasWorkspace.views.filter(row=>row.viewId.startsWith('inline-')).length,8);
  }
 }
 await card(page,'Capacity 1').getByRole('button',{name:'Collapse Capacity 1',exact:true}).click();
 await card(page,'Capacity 9').getByRole('button',{name:'Expand Capacity 9 in chat',exact:true}).click();
 await expect(card(page,'Capacity 9').locator('iframe')).toBeVisible();
 await action(page,'session.create');
 await expect(page.locator('.a-general-artifact')).toHaveCount(0);
 assert.ok(!(await snapshot(page)).canvasWorkspace.views.some(row=>row.viewId.startsWith('inline-')));

 // These exact producer/consumer flows deliberately fail loud on the current
 // older fixture. ROOT must qualify them after the real backend action lands.
 const names=await page.evaluate(()=>window.amplifier.getActions().map(row=>row.name||row.action));
 assert.ok(names.includes('canvas.present'),'Backend prerequisite: canvas.present must be exposed; no fabricated service or mutation fallback');
 await action(page,'session.select',{id:sid});
 for(const chat of ['compact','inline','none']){
  await action(page,'canvas.present',{sessionId:sid,id:image.id,version:1,presentation:{chat,canvas:'unchanged'}});
  // Local compact override remains intact even when the publication becomes
  // inline/none. A new client observes only the saved default.
  await expect(card(page,'Ordinary PNG')).toHaveAttribute('data-chat-mode','compact');
  const fresh=await context.newPage();await fresh.goto(url);await fresh.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
  await action(fresh,'session.select',{id:sid});
  if(chat==='none')await expect(card(fresh,'Ordinary PNG')).toHaveCount(0);
  else await expect(card(fresh,'Ordinary PNG')).toHaveAttribute('data-chat-mode',chat);
  await action(page,'canvas.present',{sessionId:sid,id:image.id,version:1,presentation:{chat,canvas:'open'}});
  await expect.poll(async()=>(await snapshot(page)).canvas.open).toBe(true);
  await expect.poll(async()=>(await snapshot(page)).canvas.selectedVersion).toBe(1);
  assert.equal((await snapshot(page)).view.canvasFocused,false);
  assert.equal((await snapshot(fresh)).canvas.open,false);
  await fresh.close();await action(page,'canvas.close');
 }
 assert.deepEqual(errors,[]);
 console.log('General artifact browser journeys passed against the real fixture: width, keyboard, reduced motion, private reload/peer state, exact Canvas version, dirty refusal, capacity handoff and all five publication/Canvas combinations.');
 // Media outputId binding, PDF bytes, advanced lifecycles, history-window dirty
 // guards and per-surface dirty/no-client receipts still need ROOT's producer
 // fixtures and named integration checks; this script is not proof of those.
}finally{await browser?.close();fixture.kill()}