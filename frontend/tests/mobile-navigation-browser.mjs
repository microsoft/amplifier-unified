import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
let base=process.env.AMPLIFIER_TEST_URL,fixture;
if(!base){
 fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/mobile_navigation_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
 base=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>{fixture.kill();reject(Error('Fixture startup timed out'))},15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;const url=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];if(url){clearTimeout(timer);resolve(url)}})});
}
const out=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-mobile-navigation';await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
const patch=patch=>action('view.update',{patch});
const results=[];
try{
 await page.goto(base);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await action('theme.reset');
 await action('session.create',{title:'Improve mobile navigation and Canvas'});
 await action('conversation.send',{text:'How can we make navigation work better on a phone?'});
 await patch({navPinned:true,navExpanded:false});
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this unsent draft');
 const drawer=page.getByRole('dialog',{name:'Workspaces and conversations'});
 for(const width of [320,360,390,412,600,760,1024,1440]){
  await page.setViewportSize({width,height:844});await page.waitForTimeout(150);
  const metrics=await page.evaluate(()=>{
   const box=s=>{const el=document.querySelector(s),b=el.getBoundingClientRect();return {x:b.x,width:b.width,height:b.height,display:getComputedStyle(el).display}};
   return {viewport:innerWidth,body:document.documentElement.scrollWidth,header:box('.a-top'),title:box('.a-mobile-chat-title'),nav:box('.a-nav-slot'),chat:box('.a-conversation'),font:getComputedStyle(document.querySelector('#amp-one')).fontSize,actions:[...document.querySelectorAll('.a-top button')].filter(b=>b.getClientRects().length).map(b=>({label:b.getAttribute('aria-label'),width:b.getBoundingClientRect().width}))};
  });
  results.push(metrics);assert.equal(metrics.body,width);assert.equal(metrics.font,'14px');
  if(width<=760){assert.equal(metrics.chat.width,width);assert.equal(metrics.nav.display,'none');assert.ok(metrics.title.width>100);assert.equal(metrics.actions.length,3);assert.ok(metrics.actions.every(row=>row.width>=44))}
  await page.screenshot({path:`${out}/chat-${width}.png`});
  if(width<=760){
   await page.getByRole('button',{name:'Open navigation',exact:true}).tap();await drawer.waitFor();
   const navigation=await drawer.evaluate(el=>{
    const rect=el.getBoundingClientRect(),rail=el.querySelector('.a-nav-rail').getBoundingClientRect();
    return {x:rect.x,y:rect.y,width:rect.width,height:rect.height,railWidth:rail.width,viewportWidth:innerWidth,viewportHeight:innerHeight,bodyWidth:document.documentElement.scrollWidth};
   });
   results.push({navigation});
   assert.equal(navigation.x,0);assert.equal(navigation.y,0);
   assert.equal(navigation.width,navigation.viewportWidth,'Phone navigation must fill the viewport');
   assert.equal(navigation.height,navigation.viewportHeight);
   assert.equal(navigation.railWidth,navigation.viewportWidth);assert.equal(navigation.bodyWidth,width);
   await page.screenshot({path:`${out}/drawer-${width}.png`});
   await page.getByRole('button',{name:'Close navigation',exact:true}).tap();
   assert.equal(await drawer.isVisible(),false);
   assert.equal(await page.getByRole('textbox',{name:'Message Amplifier'}).inputValue(),'Keep this unsent draft');
  }
 }
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await drawer.waitFor();assert.equal((await drawer.boundingBox()).width,390);
 assert.ok(await page.locator('.a-conversation').evaluate(el=>el.inert));
 await page.screenshot({path:`${out}/drawer.png`});assert.ok(await drawer.locator('input').evaluateAll(nodes=>nodes.filter(node=>node.getClientRects().length).every(node=>parseFloat(getComputedStyle(node).fontSize)>=16)));
 await page.keyboard.press('Shift+Tab');assert.ok(await drawer.evaluate(el=>el.contains(document.activeElement)));
 const details=drawer.locator('.a-navigation-more').first();await details.click();await page.getByRole('button',{name:'Close details',exact:true}).waitFor();
 await page.screenshot({path:`${out}/details.png`});
 await page.keyboard.press('Escape');assert.equal(await drawer.isVisible(),true);
 await page.keyboard.press('Escape');await page.waitForFunction(()=>document.querySelector('.a-nav-slot').hidden);
 assert.equal(await page.getByRole('textbox',{name:'Message Amplifier'}).inputValue(),'Keep this unsent draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.navPinned),true);
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.getByRole('button',{name:'Close navigation',exact:true}).tap();
 assert.equal(await drawer.isVisible(),false);
 await page.getByRole('button',{name:'More app options',exact:true}).click();
 await page.getByRole('button',{name:'Chat details and export',exact:true}).waitFor();
 await page.screenshot({path:`${out}/more.png`});
 assert.ok(await page.getByRole('dialog',{name:'More app options',exact:true}).evaluate(el=>el.contains(document.activeElement)));
 await page.keyboard.press('Escape');
 for(const scheme of ['dark','light']){
  await patch({scheme});await page.setViewportSize({width:320,height:740});
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  assert.equal(Math.round((await drawer.boundingBox()).width),320);assert.equal(Math.round((await drawer.locator('.a-nav-rail').boundingBox()).width),320);
  await page.screenshot({path:`${out}/drawer-${scheme}-320.png`});
  await page.keyboard.press('Escape');
 }
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);
 await page.locator('.a-messages').evaluate(el=>{el.dispatchEvent(new WheelEvent('wheel',{deltaY:-500}));el.scrollTop=150});
 await page.waitForTimeout(100);
 const scrollBefore=await page.locator('.a-messages').evaluate(el=>el.scrollTop);assert.equal(scrollBefore,150);
 await action('canvas.show',{kind:'html',title:'Persistent viewer',content:'<!doctype html><html><body><button onclick="this.textContent=String(Number(this.textContent)+1)">0</button><input aria-label="Retained canvas input"></body></html>'});
 const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('button',{name:'0',exact:true}).click();await frame.getByRole('textbox').fill('Keep my viewer state');
 const iframe=await page.locator('.a-canvas-html').elementHandle();
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='canvas.visibility')await new Promise(r=>setTimeout(r,1200));await route.continue()});
 for(const name of ['Close canvas','Open canvas','Close canvas','Open canvas']){
  await page.getByRole('button',{name,exact:true}).click();
  const expected=name==='Open canvas';
  assert.equal(await page.locator('#workspace-canvas').isVisible(),expected);
  assert.equal(await page.locator('.a-canvas-toggle').getAttribute('aria-busy'),null);
  assert.equal(await page.locator('.a-canvas-toggle').getAttribute('data-action-pending'),null);
 }
 await page.waitForTimeout(5500);
 assert.equal(await page.locator('#workspace-canvas').isVisible(),true);
 assert.equal(await iframe.evaluate(el=>el===document.querySelector('.a-canvas-html')),true);
 assert.equal(await frame.getByRole('button',{name:'1',exact:true}).count(),1);
 assert.equal(await frame.getByRole('textbox').inputValue(),'Keep my viewer state');
 await page.screenshot({path:`${out}/canvas.png`});
 assert.equal(await page.evaluate(()=>window.amplifier.getState().renderedView.panes.conversation.interactive),false);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().renderedView.controls.some(control=>control.label==='Message Amplifier')),false);
 await page.getByRole('button',{name:'Back to chat',exact:true}).click();
 assert.equal(await page.getByRole('textbox',{name:'Message Amplifier'}).inputValue(),'Keep this unsent draft');
 assert.equal(await page.locator('.a-messages').evaluate(el=>el.scrollTop),scrollBefore);
 await page.waitForTimeout(1500);
 await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();assert.equal(await page.locator('#workspace-canvas').isVisible(),false);
 await page.unroute('**/api/actions');
 let releaseCommand,held=false;
 const gate=new Promise(resolve=>{releaseCommand=resolve});
 const hold=async route=>{const body=route.request().postDataJSON();if(body?.action==='view.update'&&body.args?.patch?.workerDraft==='Held fixture preference'){held=true;await gate}await route.continue()};
 await page.route('**/api/actions',hold);
 await page.evaluate(()=>{window.heldPreference=window.amplifier.dispatch('view.update',{patch:{workerDraft:'Held fixture preference'}})});
 for(let i=0;i<30&&!held;i++)await page.waitForTimeout(20);assert.equal(held,true);
 const acknowledged=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='canvas.visibility',{timeout:1500});
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();await acknowledged;
 assert.equal(await page.locator('#workspace-canvas').isVisible(),true);
 releaseCommand();await page.evaluate(()=>window.heldPreference);await page.unroute('**/api/actions',hold);
 let rejected=false;
 const reject=async route=>{if(!rejected&&route.request().postDataJSON()?.action==='canvas.visibility'){rejected=true;await new Promise(resolve=>setTimeout(resolve,300));await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Fixture visibility save failed'})})}else await route.continue()};
 await page.route('**/api/actions',reject);
 await page.getByRole('button',{name:'Close canvas',exact:true}).click();assert.equal(await page.locator('#workspace-canvas').isVisible(),false);
 await page.getByText('Fixture visibility save failed',{exact:true}).waitFor();assert.equal(await page.locator('#workspace-canvas').isVisible(),true);
 await page.getByRole('button',{name:'Dismiss error',exact:true}).click();await page.unroute('**/api/actions',reject);
 assert.deepEqual(errors,[]);await writeFile(`${out}/results.json`,JSON.stringify({results,errors},null,2));console.log('Responsive layouts, drawer focus, sheets, typography, delayed rapid toggles, retained viewer, draft and reload passed');
}finally{await browser.close();fixture?.kill()}
