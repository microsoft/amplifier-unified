// Real browser geometry, synthetic history, no application state or model calls.
import {readFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const source=await readFile(new URL('../src/chat-scroll.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
try {
 const page=await browser.newPage();
 await page.goto('about:blank');
 await page.setContent('<style>#pane{height:500px;overflow:auto;overflow-anchor:none}article{margin:0;height:100px}.old{height:1800px}.reply{height:1600px}</style><div id="pane"></div>');
 await page.evaluate(async source=>{
  const {createChatScroll}=await import('data:text/javascript;base64,'+btoa(source));
  // about:blank does not provide sessionStorage. Supply isolated tab storage.
  const values=new Map();Object.defineProperty(window,'sessionStorage',{value:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)}});
  window.following={current:true};window.pane=document.querySelector('#pane');
  window.render=reply=>{pane.innerHTML='<article class="old" data-message-id="old">Old messages</article><article data-message-id="user" data-input-id="input">Latest request</article>'+(reply?'<article class="reply" data-message-id="reply">New reply</article>':'');};
  render(false);window.controller=createChatScroll(pane,following);controller.select('alpha');
 },source);
 const top=()=>page.evaluate(()=>pane.scrollTop);
 const atStart=()=>page.evaluate(()=>pane.querySelector('[data-message-id="user"]').getBoundingClientRect().bottom-pane.getBoundingClientRect().top);
 await expect.poll(top).toBeGreaterThan(1000);
 await page.evaluate(()=>controller.submitted('input'));
 // Leave while the request is near the bottom, return after a long reply arrives.
 await page.evaluate(()=>{controller.select('beta',false);render(false)});
 await page.evaluate(()=>{render(true);controller.select('alpha')});
 await expect.poll(atStart).toBeCloseTo(16,0);
 assert.equal(await page.evaluate(()=>following.current),true,'Returning should preserve the submitted reply anchor');
 // Reply growth must not pull the reader down the page.
 const anchored=await top();await page.evaluate(()=>pane.querySelector('.reply').style.height='2200px');
 await page.waitForTimeout(100);assert.equal(await top(),anchored);
 // Browser-driven scrolling (including focusing an iframe control) is also
 // reading intent. It does not deliver wheel/pointer events to this document.
 await page.evaluate(()=>{
  const target=document.createElement('button');target.textContent='Preview control';
  target.style.marginTop='800px';pane.querySelector('.reply').append(target);
  target.scrollIntoView({block:'center'});
 });
 await expect.poll(()=>page.evaluate(()=>following.current)).toBe(false);
 const focused=await top();
 await page.evaluate(()=>{pane.querySelector('.reply').append(document.createTextNode('Preview ready'));controller.update()});
 await page.waitForTimeout(100);assert.equal(await top(),focused,'Preview updates must not undo browser focus scrolling');
 // Deliberate scrollback takes precedence, including after returning/reloading.
 await page.evaluate(()=>{pane.dispatchEvent(new WheelEvent('wheel',{deltaY:-400}));pane.scrollTop=500;pane.dispatchEvent(new Event('scroll'))});
 await page.evaluate(()=>{controller.select('beta',false);render(false)});
 await page.evaluate(()=>{render(true);controller.select('alpha')});
 await expect.poll(top).toBe(500);assert.equal(await page.evaluate(()=>following.current),false);
 // An earlier image/section growing must preserve the visible message offset.
 await page.evaluate(()=>{pane.dispatchEvent(new WheelEvent('wheel',{deltaY:1}));pane.scrollTop=1850;pane.dispatchEvent(new Event('scroll'))});
 const visible=await atStart();await page.evaluate(()=>pane.querySelector('.old').style.height='2000px');
 await expect.poll(atStart).toBeCloseTo(visible,0);
 await page.evaluate(()=>pane.querySelector('.old').style.height='1800px');
 await expect.poll(atStart).toBeCloseTo(visible,0);
 // No-scroll switch: flush the current intent even if no new scroll event fired.
 await page.evaluate(()=>controller.submitted('input'));await expect.poll(atStart).toBeCloseTo(16,0);
 await page.evaluate(()=>{controller.dispose();controller=null});
 await page.evaluate(async source=>{const {createChatScroll}=await import('data:text/javascript;base64,'+btoa(source));controller=createChatScroll(pane,following);controller.select('alpha')},source);
 await expect.poll(atStart).toBeCloseTo(16,0);assert.equal(await page.evaluate(()=>following.current),true);
 // Jump-to-latest is explicit and remains a one-time jump.
 await page.evaluate(()=>controller.reveal());const latest=await top();await page.evaluate(()=>pane.querySelector('.reply').style.height='2400px');
 await page.waitForTimeout(100);assert.equal(await top(),latest);assert.equal(await page.evaluate(()=>following.current),false);
 // A saved anchor may fall outside the loaded history window on a later visit.
 await page.evaluate(()=>controller.submitted('input'));await expect.poll(atStart).toBeCloseTo(16,0);
 await page.evaluate(()=>{controller.select('beta',false);pane.innerHTML=''});
 await page.evaluate(()=>{pane.innerHTML='<article class="reply" data-message-id="newer">Newer history window</article>';controller.select('alpha')});
 await expect.poll(()=>page.evaluate(()=>pane.scrollHeight-pane.scrollTop-pane.clientHeight)).toBe(0);
 await expect.poll(()=>page.evaluate(()=>following.current)).toBe(false);
 // A just-submitted input can precede its DOM insertion. Do not treat this as
 // an obsolete restored anchor and stop following before the message arrives.
 await page.evaluate(()=>controller.submitted('delayed-input'));
 await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>following.current),true);
 await page.evaluate(()=>{pane.insertAdjacentHTML('beforeend','<article data-message-id="delayed" data-input-id="delayed-input">Sent</article><article class="reply">Response</article>')});
 await expect.poll(()=>page.evaluate(()=>pane.querySelector('[data-input-id="delayed-input"]').getBoundingClientRect().bottom-pane.getBoundingClientRect().top)).toBeCloseTo(16,0);
 // Reading a message outside the tail loads one bounded window. Failed
 // restoration keeps the saved anchor for a later visit, without a retry loop.
 await page.evaluate(async source=>{
  controller.dispose();
  sessionStorage.setItem('amplifier.chat-reading.v1',JSON.stringify({gamma:{messageId:'historical',offset:-30,top:600}}));
  pane.innerHTML='<article class="reply" data-message-id="tail">Tail</article>';
  const {createChatScroll}=await import('data:text/javascript;base64,'+btoa(source));
  window.loads=0;window.finishLoad=null;window.loadCurrent=null;
  window.makeController=()=>createChatScroll(pane,following,()=>{},{loadAnchor:(sessionId,messageId,current)=>{loads++;loadCurrent=current;return new Promise(resolve=>{finishLoad=resolve})}});
  controller=makeController();controller.select('gamma');
 },source);
 await expect.poll(()=>page.evaluate(()=>loads)).toBe(1);
 // The API can finish before React commits the requested window. Keep the
 // restoration pending across frames until its message actually mounts.
 await page.evaluate(()=>finishLoad(true));
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await page.evaluate(()=>{pane.innerHTML='<article style="height:600px" data-message-id="before">Before</article><article class="reply" data-message-id="historical">History</article>'});
 await expect.poll(()=>page.evaluate(()=>pane.querySelector('[data-message-id="historical"]').getBoundingClientRect().top-pane.getBoundingClientRect().top)).toBe(-30);
 // Leave/return with only the tail, then fail the restoration request.
 await page.evaluate(()=>{controller.select('beta',false);pane.innerHTML='<article class="reply" data-message-id="tail">Tail</article>';controller.select('gamma')});
 await expect.poll(()=>page.evaluate(()=>loads)).toBe(2);
 await page.evaluate(()=>finishLoad());
 await page.waitForTimeout(250);assert.equal(await page.evaluate(()=>loads),2);
 await page.evaluate(()=>controller.dispose());
 assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.chat-reading.v1')).gamma.messageId),'historical');
 await page.evaluate(()=>{controller=makeController();controller.select('gamma')});
 await expect.poll(()=>page.evaluate(()=>loads)).toBe(3);
 // Deliberate navigation cancels restoration authority before a late result.
 await page.evaluate(()=>controller.reveal());assert.equal(await page.evaluate(()=>loadCurrent()),false);
 await page.evaluate(()=>finishLoad());
 await page.evaluate(()=>controller.dispose());console.log('PASS: return during reply, top alignment, growth, manual scrollback, controller reload, explicit latest jump, missing history anchor.');
}finally{await browser.close()}
