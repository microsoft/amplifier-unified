// Installed real host and HTTP/SSE; only execution/provider responses are synthetic.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import './composer-test-helpers.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(output&&process.env.AMPLIFIER_TEST_PACKAGE_ROOT,'Explicit installed package and evidence required');
await mkdir(output,{recursive:true});
const fixture=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON,args:[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],env:process.env,tag:'empty-host-ui',name:'Installed Return'});
let browser,page,originalError;
const results=[],errors=[],key='amplifier.composer.returnKey.v1';
try{
 browser=await chromium.launch({headless:true});
 const identity=await fetch(fixture.base+'/fixture',{headers:{Authorization:'Bearer fixture-browser-control-token'}}).then(r=>r.json());
 assert.equal(identity.installedPackageVerified,true);
 const scenarios=[
  {name:'desktop',width:1440,safe:false},
  {name:'narrow-desktop',width:390,safe:false},
  {name:'phone',width:390,hasTouch:true,isMobile:true,safe:true},
  {name:'wide-touch',width:1440,hasTouch:true,safe:true},
  {name:'hybrid',width:768,hybrid:true,safe:true},
  {name:'unknown',width:420,unknown:true,safe:true},
 ];
 for(const scenario of scenarios){
  const context=await browser.newContext({viewport:{width:scenario.width,height:900},hasTouch:!!scenario.hasTouch,isMobile:!!scenario.isMobile,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  try{
   if(scenario.hybrid)await context.addInitScript(()=>Object.defineProperty(navigator,'maxTouchPoints',{get:()=>5}));
   if(scenario.unknown)await context.addInitScript(()=>Object.defineProperty(navigator,'maxTouchPoints',{get:()=>undefined}));
   page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));const actions=[];
   page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
   await page.goto(fixture.base);await showFixtureConversation(page,{navPinned:false});
   const input=page.getByRole('textbox',{name:'Message Amplifier'}),send=page.getByRole('button',{name:'Send message',exact:true});
   await expect(input).toBeEditable();
   await expect(input).toHaveAccessibleDescription(scenario.safe?'Return adds a new line. Use Send to send.':'Enter to send. Shift+Enter or Ctrl+Enter adds a new line.');
   assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),null,'Separate browser context starts Automatic');
   const served={};
   for(const asset of await page.locator('script[src],link[rel=stylesheet][href]').evaluateAll(nodes=>nodes.map(n=>n.src||n.href))){
    const name=new URL(asset).pathname.slice(1),response=await page.request.get(asset);assert.equal(response.status(),200);
    served[name]=createHash('sha256').update(await response.body()).digest('hex');assert.equal(served[name],identity.staticSha256[name]);
   }
   assert.ok(Object.keys(served).some(name=>name.endsWith('.js')));
   const sends=()=>actions.filter(row=>row.action==='conversation.send');
   const start=(await page.request.get(fixture.base+'/fixture').then(r=>r.json())).sent.length;
   for(const inputType of ['insertParagraph','insertLineBreak']){
    await input.fill('First');await input.press('Control+End');
    assert.equal(await input.evaluate((node,inputType)=>!node.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType})),inputType),true);
    await page.keyboard.type('Second');await expect(input).toHaveDraft('First\n\nSecond');
   }
   await input.fill('First');await input.press('Control+End');await input.press(scenario.safe?'Enter':'Shift+Enter');
   await page.keyboard.type('Second');await expect(input).toHaveDraft('First\n\nSecond');
   await input.evaluate(node=>{node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:229,isComposing:true,bubbles:true,cancelable:true}));node.dispatchEvent(new InputEvent('beforeinput',{inputType:'insertParagraph',isComposing:true,bubbles:true,cancelable:true}))});
   await expect(input).toHaveDraft('First\n\nSecond');assert.equal(sends().length,0);
   assert.equal((await page.request.get(fixture.base+'/fixture').then(r=>r.json())).sent.length,start,'Editing did not reach runtime');
   const preferenceStart=actions.length;
   await openSettingsPage(page,'appearance');const choice=page.getByLabel('Return key',{exact:true});await choice.selectOption('newline');
   assert.equal(await page.evaluate(k=>localStorage.getItem(k),key),'newline');
   assert.equal(actions.slice(preferenceStart).filter(row=>/settings|preferences|appearance|shell\.changes/.test(row.action)).length,0,'Local Return choice writes no shared preference');
   await expect(input).toHaveDraft('First\n\nSecond');await page.reload();await input.waitFor();await openSettingsPage(page,'appearance');
   await expect(choice).toHaveValue('newline');await expect(input).toHaveDraft('First\n\nSecond');
   await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();
   await showFixtureConversation(page,{navPinned:false});
   if(await page.getByRole('button',{name:'Close navigation',exact:true}).isVisible())await page.getByRole('button',{name:'Close navigation',exact:true}).click();
   await expect(input).toHaveAccessibleDescription('Return adds a new line. Use Send to send.');
   await expect(input).toBeEditable();await input.click();
   await input.press('Control+End');await input.press('Enter');await page.keyboard.type('Third');
   console.log('Return after reload',scenario.name,await input.evaluate(node=>({html:node.innerHTML,focused:document.activeElement===node,selection:getSelection()?.toString(),help:node.getAttribute('aria-describedby')})));
   await expect(input).toHaveDraft('First\n\nSecond\n\nThird');assert.equal(sends().length,0);
   await expect(send).toBeEnabled();await send.focus();await send.press('Enter');
   await expect.poll(()=>sends().length).toBe(1);assert.equal(sends()[0].args.text,'First\n\nSecond\n\nThird');
   await expect(input).toHaveDraft('');
   await expect.poll(async()=>(await page.request.get(fixture.base+'/fixture').then(r=>r.json())).sent.length).toBe(start+1);
   if(scenario.name==='desktop'){
    await input.fill('Guarded draft');
    const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
    let release,observed;const held=new Promise(resolve=>release=resolve),arrived=new Promise(resolve=>observed=resolve);
    const holdDraft=async route=>{if(route.request().postDataJSON()?.action!=='session.draft')return route.continue();observed();await held;return route.continue()};
    await page.route('**/api/actions',holdDraft);
    await page.evaluate(()=>{window.pendingDraftNavigation=window.amplifier.dispatch('session.draft',{})});await arrived;
    try{
     await expect(input).toHaveAttribute('aria-readonly','true');await expect(input).toHaveAttribute('contenteditable','false');await expect(send).toBeDisabled();
     await input.evaluate(node=>{node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));node.dispatchEvent(new InputEvent('beforeinput',{inputType:'insertParagraph',bubbles:true,cancelable:true}));node.closest('form').requestSubmit()});
     await expect(input).toHaveDraft('Guarded draft');assert.equal(sends().length,1);
    }finally{release();await page.evaluate(()=>window.pendingDraftNavigation);await page.unroute('**/api/actions',holdDraft)}
    await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sid);
    await expect(input).toHaveDraft('Guarded draft');
    assert.equal((await page.request.post(fixture.base+'/fixture/composer-eligibility',{data:{sessionId:sid,readOnlyReason:'Synthetic saved history is read-only.'}})).status(),200);
    await expect(input).toHaveAttribute('aria-disabled','true');await expect(input).toHaveAttribute('contenteditable','false');
    await input.evaluate(node=>{node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));node.dispatchEvent(new InputEvent('beforeinput',{inputType:'insertParagraph',bubbles:true,cancelable:true}));node.closest('form').requestSubmit()});
    await expect(input).toHaveDraft('Guarded draft');assert.equal(sends().length,1);
    assert.equal((await page.request.post(fixture.base+'/fixture/composer-eligibility',{data:{sessionId:sid,readOnlyReason:null}})).status(),200);
    await expect(input).toBeEditable();await input.fill('');
   }
   await page.screenshot({path:output+'/'+scenario.name+'.png'});
   results.push({scenario:scenario.name,passed:true,servedAssets:served,hostSubmissions:1,localPreferenceIsolated:true});
  }finally{await context.close()}
 }
 // Fail only the Return preference key, not the draft/outbox storage owners.
 const context=await browser.newContext({viewport:{width:1440,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 try{
  page=await context.newPage();await page.goto(fixture.base);await showFixtureConversation(page,{navPinned:false});
  const input=page.getByRole('textbox',{name:'Message Amplifier'});
  await input.fill('Retain **format** and undo');await input.press('Control+End');await page.keyboard.type(' marker');
  const before=await input.innerHTML();
  await page.evaluate(k=>{const old=Storage.prototype.setItem;Storage.prototype.setItem=function(name,value){if(name===k)throw new DOMException('Fixture quota','QuotaExceededError');return old.call(this,name,value)}},key);
  await openSettingsPage(page,'appearance');await page.getByLabel('Return key',{exact:true}).selectOption('newline');
  await expect(page.getByText('The Return key preference applies in this tab only. This browser could not confirm saving it for next time.',{exact:true})).toBeVisible();
  assert.equal(await input.innerHTML(),before,'Mounted editor structure survives failed preference storage');
  await page.getByRole('button',{name:/^Close (settings|panel)$/}).last().click();
  await showFixtureConversation(page,{navPinned:false});
  if(await page.getByRole('button',{name:'Close navigation',exact:true}).isVisible())await page.getByRole('button',{name:'Close navigation',exact:true}).click();
  await input.press('Control+z');await expect(input).not.toHaveText(/marker/);
  results.push({scenario:'failed-local-storage',passed:true,structurePreserved:true,undoPreserved:true});
 }finally{await context.close()}
 assert.deepEqual(errors,[]);
 await writeFile(output+'/result.json',JSON.stringify({scope:'installed host with synthetic runtime',results,limits:['Not physical mobile/IME acceptance','No live provider or production data']},null,2)+'\n');
}catch(error){originalError=error;await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error}
finally{await finishFixture({name:'Installed Return',stop:fixture.stop,close:[['browser',()=>browser?.close()]],originalError})}
