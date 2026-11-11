// Appearance rendering and settings are shell preferences, not a CSS compiler.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Appearance rendering fixture'});
let browser,release,failure;
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({
  colorScheme:'dark',viewport:{width:1280,height:900},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},
 });
 const errors=[],actions=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if(request.method()==='POST'&&request.url().endsWith('/api/actions'))
   try{actions.push(request.postDataJSON())}catch{}
 });
 const action=(name,args={})=>page.evaluate(async([name,args])=>{
  const receipt=await window.amplifier.dispatch(name,args);
  if(receipt.accepted===false)throw Error(receipt.error||name+' rejected');
  return receipt;
 },[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const appearance=async()=>(await state()).appearance;
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await expect(composer).toBeVisible();await action('session.create');
 const draft='Preserve this unsent draft\nwith eight\nsaved\nlines\nthat need\nroom\nto stay\nreadable';
 await composer.fill(draft);
 const savedDraft=await readComposerDraft(composer);
 assert.deepEqual(savedDraft.split(/\n+/),draft.split('\n'));
 assert.equal(savedDraft,draft.replaceAll('\n','\n\n'));
 const selected=(await state()).selectedSessionId;
 await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','dark');

 await openSettingsPage(page,'appearance');
 await expect(page.getByRole('radio',{name:'Aurora',exact:true})).toBeVisible();
 await page.getByRole('radio',{name:'Light',exact:true}).check();
 await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','light');
 await expect.poll(async()=>(await appearance()).renderedPreferences.scheme).toBe('light');
 const decorations=page.getByRole('checkbox',{name:'Show background decorations',exact:true});
 await decorations.uncheck();
 await expect(page.locator('#amp-one')).toHaveAttribute('data-decorations','off');
 await expect.poll(async()=>(await appearance()).renderedPreferences.decorations).toBe(false);
 const preferenceWrites=actions.filter(row=>row?.action==='appearance.preferences.update');
 assert.ok(preferenceWrites.some(row=>row.args?.patch?.scheme==='light'));
 assert.ok(preferenceWrites.some(row=>row.args?.patch?.decorations===false));
 assert.equal(actions.some(row=>row?.action==='shell.changes.prepare'||row?.action==='shell.changes.apply'),false,
  'Appearance scheme and decorations go through the preferences owner, not optimistic shell mutations.');
 assert.equal((await appearance()).effective.scheme,'light');
 assert.equal((await appearance()).effective.decorations,false);

 // Keep Settings open and delay shell restoration: the cached committed
 // presentation must paint light even though the device is still dark.
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.appearance-composition.v2'))?.scheme==='light');
 const gate=new Promise(resolve=>{release=resolve});
 await page.route('**/api/shell?*',async route=>{await gate;await route.continue()});
 await page.reload({waitUntil:'domcontentloaded'});
 await expect(page.locator('.boot')).toBeVisible();await expect(page.locator('#amp-one')).toHaveCount(0);
 assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'light');
 release();await page.unrouteAll({behavior:'wait'});
 await expect(page.getByRole('dialog')).toBeVisible();
 await expect.poll(()=>page.getByRole('dialog').evaluate(el=>el.contains(document.activeElement))).toBe(true);
 await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);
 await showFixtureConversation(page,{navPinned:false});
 await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','light');
 await expect.poll(async()=>(await appearance()).renderedPreferences.decorations).toBe(false);
 await expect(composer).toHaveDraft(savedDraft);
 await expect.poll(()=>composer.evaluate(el=>el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(170);
 assert.equal((await state()).selectedSessionId,selected);
 await expect.poll(()=>page.locator('#amp-one').evaluate(element=>getComputedStyle(element).colorScheme)).toBe('light');
 assert.deepEqual(errors,[]);
 console.log('Reference appearance preference rendering passed: UI actions persist scheme/decorations through the preference seam, reload restores the authoritative first paint, and the unfinished conversation draft survives.');
}catch(error){failure=error;throw error}
finally{
 release?.();
 await finishFixture({label:'Appearance rendering fixture',close:[['Appearance rendering browser',()=>browser?.close()]],stop,originalError:failure});
}