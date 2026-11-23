// Production host/UI with synthetic permission settings; no provider calls.
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
const output=resolve(process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/permissions-feedback');
await mkdir(output,{recursive:true});assert.deepEqual(await readdir(output),[],'Use an empty evidence directory');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Permissions feedback'});
let browser,page,release,originalError;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const presentation=patch=>updateAppearance(page,patch);
 let hold=true,rejected=true,count=0;const saves=[];
 await page.route('**/api/actions',async route=>{
  const envelope=route.request().postDataJSON(),body=envelope?.action==='shell.command'?envelope.args:envelope;
  if(!hold||body?.action!=='permissions.save')return route.continue();
  count++;saves.push(body.args);await new Promise(resolve=>release=resolve);
  if(rejected)return route.fulfill({status:503,json:{accepted:false,error:'Synthetic permission save failure'}});
  return route.continue();
 });
 await page.goto(url);
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Synthetic file access check'}));
 const target=await page.evaluate(()=>{const state=window.amplifier.getState(),session=state.sessions.find(row=>row.id===state.selectedSessionId);return {id:session.id,workspace:session.workspace}});
 await openSettingsPage(page,'permissions');
 // Other visited settings editors stay mounted but hidden to retain drafts.
 await expect(page.locator('[data-settings-page="permissions"] .a-settings-page-content:not([hidden]) select')).toHaveCount(0);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const initialAppearance=await settledAppearance(page);
 const selected=await page.evaluate(a=>window.amplifier.dispatch('appearance.preferences.select',{
  generation:a.generation,activeScope:'client',expectedRevision:a.local.revision,
  expectedStorageVersion:a.local.storageVersion,expectedSharedRevision:a.shared.revision}),initialAppearance);
 assert.equal(selected.accepted,true);assert.equal((await settledAppearance(page)).activeScope,'client');
 for(const scheme of ['light','dark'])for(const detail of ['minimal','standard','detailed']){
 await page.setViewportSize({width:1280,height:900});await page.emulateMedia({forcedColors:'none',reducedMotion:'no-preference'});
 await presentation({scheme,interfaceDetail:detail});
 await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
 await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail',detail);
 count=0;rejected=true;saves.length=0;
 const allowedPath=`/tmp/synthetic-allowed-${scheme}-${detail}`;
 const deniedPath=`/tmp/synthetic-denied-${scheme}-${detail}`,revisedPath=deniedPath+'-revised';
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-files'}}));
 const section=page.locator('.a-chat-pages'),allowed=section.locator('#allowed-folders'),denied=section.locator('#denied-folders'),scope=section.locator('select').first();
 await scope.selectOption('project');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().permissions?.scope)).toBe('project');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().permissions?.workspace)).toBe(target.workspace);
 await allowed.fill(allowedPath);await denied.fill(deniedPath);
 const save=page.getByRole('button',{name:'Save permissions',exact:true}),width=(await save.boundingBox()).width;
 const beforeSave=await page.evaluate(()=>window.amplifier.getState().permissions);
 assert.ok(!beforeSave.allowed.includes(allowedPath)&&!beforeSave.denied.includes(revisedPath),'Each profile must persist a new policy, not read a previous cell');
 await save.click();await expect.poll(()=>count).toBe(1);
 assert.deepEqual(saves[0],{sessionId:target.id,scope:'project',allowed:[allowedPath],denied:[deniedPath]});
 const saving=page.getByRole('button',{name:'Saving…',exact:true});
 await expect(saving).toBeVisible();await expect(saving).toBeDisabled();
 assert.ok(Math.abs((await saving.boundingBox()).width-width)<0.01,'Saving label must retain button width within browser geometry precision');
 for(const control of [allowed,denied,scope,section.getByRole('button',{name:'Load permissions',exact:true}),...await section.getByRole('button',{name:'Browse',exact:true}).all()])await expect(control).toBeDisabled();
 await expect(section.getByRole('status')).toContainText('Saving file access settings…');
 await page.screenshot({path:resolve(output,`permissions-${scheme}-${detail}-desktop.png`),animations:'disabled'});
 await saving.evaluate(el=>el.click());assert.equal(count,1);
 await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeEnabled();
 await page.emulateMedia({reducedMotion:'reduce',forcedColors:'active'});
 assert.equal(await section.locator('.a-progress-spinner').evaluate(el=>getComputedStyle(el).animationName),'none');
 release();release=null;
 await expect(section.getByRole('alert')).toContainText('Your entries are kept');
 await expect(allowed).toBeEnabled();await expect(scope).toHaveValue('project');
 await expect(allowed).toHaveValue(allowedPath);await expect(denied).toHaveValue(deniedPath);
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().permissions),beforeSave,'A rejected save changes no saved policy');
 await denied.fill(revisedPath);rejected=false;
 await save.click();await expect.poll(()=>count).toBe(2);release();release=null;
 await expect(section.getByText('Saved. Idle conversations use these settings on their next message.',{exact:true})).toBeVisible();
 await expect(section.getByRole('alert')).toHaveCount(0);await expect(allowed).toBeEnabled();
 await page.evaluate(()=>window.amplifier.dispatch('permissions.get',{sessionId:window.amplifier.getState().selectedSessionId,scope:'project'}));
 const prefs=await page.evaluate(()=>window.amplifier.getState().permissions);
 assert.deepEqual(prefs.allowed,[allowedPath]);assert.deepEqual(prefs.denied,[revisedPath]);assert.equal(prefs.scope,'project');assert.equal(prefs.workspace,target.workspace);
 // The chat-owned form must remain usable and locked on a small viewport.
 await page.setViewportSize({width:390,height:844});await save.click();await expect.poll(()=>count).toBe(3);
 await expect(saving).toBeDisabled();await expect(scope).toBeDisabled();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:resolve(output,`permissions-${scheme}-${detail}-mobile.png`),animations:'disabled'});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();release();release=null;
 await expect(section).toHaveCount(0);
 await page.evaluate(id=>window.amplifier.dispatch('permissions.get',{sessionId:id,scope:'project'}),target.id);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),target.id);
 assert.ok(saves.every(args=>args.sessionId===target.id&&args.scope==='project'));
 console.log(JSON.stringify({scheme,detail,passed:true,scope:'Settled browser profile; exact project permission target'}));
 }
 assert.deepEqual(errors,[]);assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 console.log('Permission save passed: immediate stable working label, scoped fields disabled, duplicate prevention, rejected draft retained, corrected retry persisted to selected scope, actual appearance choices, reduced motion, forced colors, narrow footer and close while saving.');
}catch(error){originalError=error;await writeFile(resolve(output,'failure.json'),JSON.stringify({error:String(error.stack||error)},null,2));await page?.screenshot({path:resolve(output,'failure.png')}).catch(()=>{});throw error}
finally{release?.();await finishFixture({label:'Permissions feedback',close:[['browser',()=>browser?.close()]],stop,originalError})}
