// Production host/UI with synthetic local diagnostics; no outbound delivery or provider calls.
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
const output=resolve(process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/diagnostics-feedback');
await mkdir(output,{recursive:true});assert.deepEqual(await readdir(output),[],'Use an empty evidence directory');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Diagnostics feedback'});
let browser,page,release,originalError;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],actions=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 const presentation=patch=>updateAppearance(page,patch);
 let count=0,rejected=true;
 await page.route('**/api/actions',async route=>{
  const envelope=route.request().postDataJSON(),body=envelope?.action==='shell.command'?envelope.args:envelope;
  if(body?.action!=='diagnostics.configure')return route.continue();
  count++;await new Promise(resolve=>release=resolve);
  return rejected?route.fulfill({status:503,json:{accepted:false,error:'Synthetic diagnostics save failure'}}):route.continue();
 });
 await page.goto(url);await openSettingsPage(page,'diagnostics');
 const initialAppearance=await settledAppearance(page);
 const selected=await page.evaluate(a=>window.amplifier.dispatch('appearance.preferences.select',{
  generation:a.generation,activeScope:'client',expectedRevision:a.local.revision,
  expectedStorageVersion:a.local.storageVersion,expectedSharedRevision:a.shared.revision}),initialAppearance);
 assert.equal(selected.accepted,true);assert.equal((await settledAppearance(page)).activeScope,'client');
 const selectedSession=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 // A deliberately held ordered write must not be a prerequisite for its own
 // appearance ACK. Exercise the full journey under each settled profile.
 for(const scheme of ['light','dark'])for(const detail of ['minimal','standard','detailed']){
 await page.setViewportSize({width:1280,height:900});await page.emulateMedia({forcedColors:'none',reducedMotion:'no-preference'});
 await presentation({scheme,interfaceDetail:detail});
 await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
 await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail',detail);
 count=0;rejected=true;
 const days=page.getByLabel('Index days',{exact:true}),records=page.getByLabel('Maximum indexed records',{exact:true});await days.fill('21');
 await days.blur();await expect.poll(()=>count).toBe(1);
 const saving=page.getByText('Saving diagnostics settings…',{exact:true});await expect(saving).toBeVisible();
 assert.equal(await page.locator('.a-diagnostics-fields').evaluate(el=>getComputedStyle(el).borderTopWidth),'0px');
 await expect(days).toBeEnabled();await expect(records).toBeEnabled();await expect(page.getByRole('button',{name:'Add server',exact:true})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeEnabled();
 await expect(page.getByLabel('Stream filter',{exact:true})).toBeEnabled();
 await page.emulateMedia({reducedMotion:'reduce',forcedColors:'active'});
 assert.equal(await page.locator('.a-check-result.pending .a-progress-spinner').evaluate(el=>getComputedStyle(el).animationName),'none');
 assert.equal(count,1);
 release();release=null;
 await expect(page.getByRole('alert').filter({hasText:'Your entries are kept'})).toBeVisible();await expect(days).toHaveValue('21');await expect(days).toBeEnabled();
 rejected=false;await page.getByRole('button',{name:'Retry saving preference',exact:true}).click();await expect.poll(()=>count).toBe(2);
 // Read-only record filters are independent of the submitted capture settings.
 await page.getByLabel('Stream filter',{exact:true}).fill('updates');
 release();release=null;
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().diagnostics?.config?.retentionDays)).toBe(21);
 await expect(days).toBeEnabled();await expect(page.getByLabel('Stream filter',{exact:true})).toHaveValue('updates');
 // Autosave keeps the numeric inputs editable. The older accepted save must
 // not erase a newer, acknowledged document-local draft.
 await days.fill('22');await days.blur();await expect.poll(()=>count).toBe(3);
 const changedDraft=await page.evaluate(()=>{const s=window.amplifier.getState(),d=s.view.diagnosticsDraft;return window.amplifier.dispatch('view.update',{patch:{diagnosticsDraft:{...d,config:{...d.config,retentionDays:23}}}},{presentation:true});});
 assert.equal(changedDraft.accepted,true);
 await expect(days).toHaveValue('23');release();release=null;
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().diagnostics.config.retentionDays)).toBe(22);
 await expect(days).toBeEnabled();await expect(days).toHaveValue('23');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.retentionDays),22);
 await days.focus();await days.blur();await expect.poll(()=>count).toBe(4);release();release=null;await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().diagnostics.config.retentionDays)).toBe(23);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.retentionDays),23);
 await page.getByRole('button',{name:'Add server',exact:true}).click();
 const name=page.getByLabel('Name',{exact:true});await name.fill('Synthetic disabled destination');await page.getByLabel('Server URL',{exact:true}).fill('https://example.invalid/diagnostics');
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Save destination',exact:true}).click();await expect.poll(()=>count).toBe(5);
 await expect(name).toBeDisabled();await expect(page.getByRole('button',{name:'Saving…',exact:true})).toBeDisabled();
 await expect(page.getByLabel('Server URL',{exact:true})).toBeDisabled();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:resolve(output,`diagnostics-${scheme}-${detail}-mobile.png`),animations:'disabled'});
 release();release=null;await expect(name).toBeEnabled();
 const stored=await page.evaluate(()=>window.amplifier.getState().diagnostics.config.destinations);assert.equal(stored.length,1);assert.equal(stored[0].enabled,false);
 await page.getByRole('button',{name:'Remove destination',exact:true}).click();await expect.poll(()=>count).toBe(6);
 await expect(page.getByRole('button',{name:'Removing…',exact:true})).toBeDisabled();await expect(name).toBeDisabled();release();release=null;
 await expect(days).toBeVisible();
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.destinations),[]);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selectedSession);
 assert.deepEqual(actions.filter(row=>['diagnostics.test','diagnostics.retry','conversation.send','runtime.control'].includes(row.action)),[],'No outbound probe, delivery retry or model input');
 console.log(JSON.stringify({scheme,detail,passed:true,scope:'Settled browser profile; disabled destination only, no outbound probe'}));
 }
 assert.deepEqual(errors,[]);assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 console.log('Diagnostics save passed: stable working labels, scoped input locking, rejected-save retry, independent record filters, newer concurrent draft retained, disabled destination save/remove, narrow footer; no outbound delivery or model calls.');
}catch(error){originalError=error;await writeFile(resolve(output,'failure.json'),JSON.stringify({error:String(error.stack||error)},null,2));await page?.screenshot({path:resolve(output,'failure.png')}).catch(()=>{});throw error}
finally{release?.();await finishFixture({label:'Diagnostics feedback',close:[['browser',()=>browser?.close()]],stop,originalError})}
