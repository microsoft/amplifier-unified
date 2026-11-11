import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
// Runs the rebuilt packaged app with real appearance actions. This is browser
// paint/startup acceptance, not proof that a native OS honors theme-color.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({colorScheme:'dark',viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const select=async appearanceId=>{
  const current=(await action('appearance.preferences.read')).result;
  const scope=current.activeScope,profile=scope==='client'?current.local:current.shared;
  const receipt=await action('appearance.preferences.update',{scope,generation:current.generation,expectedRevision:profile.revision,
   ...(scope==='client'?{expectedStorageVersion:profile.storageVersion}:{}),patch:{appearanceId}});
  assert.equal(receipt.accepted,true,JSON.stringify(receipt));
  await page.waitForFunction(id=>{const a=window.amplifier.getState().appearance;return a.theme.id===id&&a.effective.appearanceId===id},appearanceId);
 };
 const saveAndSelect=async(name,css)=>{
  const saved=await action('theme.save',{name,css,description:'Synthetic browser chrome regression'});
  assert.equal(saved.accepted,true,JSON.stringify(saved));await select(saved.result.id);
 };
 const presentation=async patch=>{
  const change=await page.evaluate(async patch=>{const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}});if(!prepared.accepted)throw Error(JSON.stringify(prepared));const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id});if(!applied.accepted)throw Error(JSON.stringify(applied));return {revision:state.revision+1,commandId:applied.result.commandId}},patch);
  await page.waitForFunction(({patch,change})=>{const a=window.amplifier.getState().appearance;return window.amplifier.getShellState()?.revision===change.revision&&Object.entries(patch).every(([key,value])=>a.effective[key]===value&&a.renderedPreferences[key]===value)&&(!change.commandId||a.command?.id===change.commandId&&a.command.status==='stored')},{patch,change});
 };
 const paint=()=>page.locator('.a-work-header').evaluate(el=>({height:el.getBoundingClientRect().height,background:getComputedStyle(el).backgroundColor,meta:document.querySelector('meta[name="theme-color"]').content}));
 const assertPaint=async()=>{await expect.poll(async()=>{const p=await paint();return p.meta===p.background}).toBe(true);assert.ok((await paint()).height<=56,'desktop header is compact');};
 await page.goto(url);await showFixtureConversation(page);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();await action('session.create');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Unsent appearance regression draft');
 for(const id of ['default','atelier','aurora','graphite']){
  await select('builtin:'+id);
  for(const scheme of ['dark','light']){await presentation({scheme});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);await assertPaint();}
 }
 await presentation({scheme:'system'});await page.emulateMedia({colorScheme:'dark'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','dark');await assertPaint();
 await page.emulateMedia({colorScheme:'light'});await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','light');await assertPaint();
 await page.waitForFunction(()=>sessionStorage.getItem('amplifier.appearance-composition.v2'));
 const committed=await page.evaluate(()=>sessionStorage.getItem('amplifier.appearance-composition.v2'));
 await action('appearance.preview',{name:'Custom chrome',css:'#amp-one{--a-chrome-bg:#14365a;--a-chrome-ink:#ffffff}'});await expect.poll(async()=>(await paint()).meta).toBe('rgb(20, 54, 90)');
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('amplifier.appearance-composition.v2')),committed,'client preview must not replace committed startup cache');
 await action('appearance.preview.cancel',{});await assertPaint();
 await saveAndSelect('Custom chrome','#amp-one{--a-chrome-bg:#14365a;--a-chrome-ink:#ffffff}');await expect.poll(async()=>(await paint()).meta).toBe('rgb(20, 54, 90)');
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.appearance-composition.v2')).colors[document.getElementById('amp-one').dataset.themeScheme]?.chrome==='rgb(20, 54, 90)');
 // Delay authoritative composition: bootstrap chrome must already match the
 // last committed header while the loading screen is visible.
 let release;const gate=new Promise(resolve=>{release=resolve});
 await page.route('**/api/shell?*',async route=>{await gate;await route.continue()});await page.reload({waitUntil:'domcontentloaded'});await expect(page.locator('.boot')).toBeVisible();
 assert.equal(await page.locator('meta[name="theme-color"]').getAttribute('content'),'rgb(20, 54, 90)');release();await page.unrouteAll({behavior:'wait'});await expect(page.locator('#amp-one')).toBeVisible();await assertPaint();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Unsent appearance regression draft');
 await action('appearance.preview',{name:'Translucent header',css:'#amp-one{--a-chrome-bg:transparent;background-color:#123456}'});await expect.poll(async()=>(await paint()).meta).toBe('rgb(18, 52, 86)');await action('appearance.preview.cancel',{});
 await select('reference');await assertPaint();
 await saveAndSelect('Modern opaque chrome','#amp-one{--a-chrome-bg:oklch(40% .1 260);--a-chrome-ink:#ffffff}');
 await assertPaint();const modern=(await paint()).meta;
 assert.match(modern,/oklch/);
 await page.waitForFunction(modern=>JSON.parse(sessionStorage.getItem('amplifier.appearance-composition.v2')).colors[document.getElementById('amp-one').dataset.themeScheme]?.chrome===modern,modern);
 let releaseModern;const modernGate=new Promise(resolve=>{releaseModern=resolve});
 await page.route('**/api/shell?*',async route=>{await modernGate;await route.continue()});
 await page.reload({waitUntil:'domcontentloaded'});await expect(page.locator('.boot')).toBeVisible();
 assert.equal(await page.locator('meta[name="theme-color"]').getAttribute('content'),modern,'Modern committed chrome survives the actual synchronous startup boundary');
 releaseModern();await page.unrouteAll({behavior:'wait'});
 await expect(page.locator('#amp-one')).toBeVisible();
 await expect.poll(async()=>(await paint()).meta).toBe(modern);
 assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.appearance-composition.v2')).colors[document.getElementById('amp-one').dataset.themeScheme].chrome),modern);
 const output=process.env.AMPLIFIER_TEST_ARTIFACTS;if(output){await mkdir(output,{recursive:true});await page.screenshot({path:output+'/window-chrome-default.png'});}
 assert.deepEqual(errors,[]);console.log('Rebuilt app: four appearances × light/dark, Device changes, saved Reference-delta chrome, preview/cancel/Reference recovery, committed startup meta and unsent draft passed. Native OS color rendering not asserted.');
}finally{await browser?.close();fixture.kill()}
