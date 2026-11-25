// Production host/UI with synthetic directories; delayed reads, no model work.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {settledAppearance,updateAppearance} from './settings-fixture.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=resolve(process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/location-feedback');
await mkdir(output,{recursive:true});assert.deepEqual(await readdir(output),[],'Use an empty evidence directory');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Location feedback'});
let browser,page,release,originalError;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],actions=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 // Profile CAS, browser storage acknowledgement and rendered composition are
 // separate from shell layout revision. Use their existing production owner.
 const presentation=patch=>updateAppearance(page,patch);
 let hold=false,count=0,rejected=true;
 await page.route('**/api/actions',async route=>{
  const envelope=route.request().postDataJSON(),body=envelope?.action==='shell.command'?envelope.args:envelope;
  if(!hold||body?.action!=='locations.list')return route.continue();
  count++;await new Promise(resolve=>release=resolve);
  if(rejected)return route.fulfill({status:503,json:{accepted:false,error:'Fixture folder lookup failed'}});
  return route.continue();
 });
 await page.goto(url);await openSettingsPage(page,'permissions');
 const initialAppearance=await settledAppearance(page);
 const selected=await page.evaluate(a=>window.amplifier.dispatch('appearance.preferences.select',{
  generation:a.generation,activeScope:'client',expectedRevision:a.local.revision,
  expectedStorageVersion:a.local.storageVersion,expectedSharedRevision:a.shared.revision}),initialAppearance);
 assert.equal(selected.accepted,true);assert.equal((await settledAppearance(page)).activeScope,'client');
 // Settle each real profile before holding the read. Appearance reads observe
 // preceding commands; waiting for them while withholding that command cycles.
 for(const scheme of ['light','dark'])for(const detail of ['minimal','standard','detailed']){
 await page.setViewportSize({width:1280,height:900});await page.emulateMedia({forcedColors:'none',reducedMotion:'no-preference',colorScheme:scheme});
 await presentation({scheme,interfaceDetail:detail});
 await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
 await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail',detail);
 hold=false;count=0;rejected=true;
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().permissions?.scope)).toBe('global');
 const original=await page.evaluate(()=>({permissions:window.amplifier.getState().permissions,selected:window.amplifier.getState().selectedSessionId}));
 const field=page.locator('.a-path-field:has(#allowed-folders)');
 await field.getByRole('button',{name:'Browse',exact:true}).click();
 const picker=field.locator('.a-location-picker'),path=picker.getByRole('textbox',{name:'Folder path',exact:true});
 await expect(picker.locator('.a-location-list')).toBeVisible();
 const originalPath=await path.inputValue(),originalListing=await picker.locator('.a-location-list').innerText();
 const go=picker.getByRole('button',{name:'Go',exact:true}),width=(await go.boundingBox()).width;
 const surface=()=>picker.evaluate(el=>{const s=getComputedStyle(el);return {background:s.background,border:s.border,shadow:s.boxShadow}}),beforeSurface=await surface();
 hold=true;await go.click();await expect.poll(()=>count).toBe(1);
 const loading=picker.getByRole('button',{name:'Loading…',exact:true});
 await expect(loading).toBeVisible();await expect(loading).toBeDisabled();await expect(path).toBeDisabled();
 assert.equal((await loading.boundingBox()).width,width);assert.deepEqual(await surface(),beforeSurface);
 await expect(picker.getByRole('status').filter({hasText:'Loading locations…'})).toBeVisible();
 assert.equal(await picker.locator('.a-location-list').innerText(),originalListing);
 await path.evaluate(el=>el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})));assert.equal(count,1);
 await expect(picker.getByRole('button',{name:'Close location picker',exact:true})).toBeEnabled();
 await expect(page.locator('#denied-folders')).toBeEnabled();
 for(const motion of ['reduce','no-preference']){
  await page.emulateMedia({colorScheme:scheme,reducedMotion:motion});await expect(loading).toBeVisible();
  await expect(path).toBeDisabled();assert.equal(count,1,'Presentation does not repeat the held folder read');
  if(motion==='reduce')assert.equal(await picker.locator('.a-progress-spinner').evaluate(el=>getComputedStyle(el).animationName),'none');
 }
 await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});await expect(loading).toBeVisible();
 release();release=null;
 await expect(field.getByRole('alert').filter({hasText:'Could not load locations'})).toBeVisible();
 await expect(path).toBeEnabled();await expect(path).toHaveValue(originalPath);
 rejected=false;await go.click();await expect.poll(()=>count).toBe(2);
 await picker.getByRole('button',{name:'Close location picker',exact:true}).click();
 release();release=null;await expect(picker).toHaveCount(0);
 hold=false;await field.getByRole('button',{name:'Browse',exact:true}).click();await expect(go).toBeEnabled();
 await expect(picker.locator('.a-location-list')).toBeVisible();assert.equal(await field.getByRole('alert').count(),0);
 await page.setViewportSize({width:390,height:844});await expect(picker).toBeVisible();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:resolve(output,`location-${scheme}-${detail}.png`)});
 assert.deepEqual(await page.evaluate(()=>({permissions:window.amplifier.getState().permissions,selected:window.amplifier.getState().selectedSessionId})),original,'Browsing, rejection and late lookup do not save permissions or change target');
 assert.deepEqual(actions.filter(row=>['permissions.save','conversation.send','conversation.stop','runtime.control'].includes(row.action)),[]);
 await picker.getByRole('button',{name:'Close location picker',exact:true}).click();
 console.log(JSON.stringify({scheme,detail,passed:true,scope:'Settled browser profile; delayed lookup does not save permissions'}));
 }
 assert.deepEqual(errors,[]);assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 console.log('Location feedback passed: retained listing, immediate working label, scoped input lock, duplicate guard, failure/retry, close during lookup, appearance preferences and narrow layout.');
}catch(error){originalError=error;await writeFile(resolve(output,'failure.json'),JSON.stringify({error:String(error.stack||error)},null,2));await page?.screenshot({path:resolve(output,'failure.png')}).catch(()=>{});throw error}
finally{release?.();await finishFixture({label:'Location feedback',close:[['browser',()=>browser?.close()]],stop,originalError})}
