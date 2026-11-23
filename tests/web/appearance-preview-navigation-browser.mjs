// Real preview writes, held before delivery or after server acceptance; no inference.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {settledAppearance} from './settings-fixture.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Appearance preview navigation fixture'});
let browser,release,failure;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const editor=async()=>{
  await openSettingsPage(page,'appearance',{activate:(_label,control,options)=>control.click(options)});
  for(const selector of ['.a-appearance-more','.a-appearance-advanced']){
   const disclosure=page.locator(selector);
   if(!await disclosure.evaluate(node=>node.open))await disclosure.locator(':scope > summary').click();
  }
 };
 const draft=()=>page.evaluate(()=>{
  const a=window.amplifier.getState().appearance;
  return JSON.parse(sessionStorage.getItem('amplifier.appearance-editor.v3:'+a.namespace)||'null')?.draft;
 });
 const profile=()=>page.evaluate(()=>{
  const a=window.amplifier.getState().appearance;
  return {namespace:a.namespace,generation:a.generation,scope:a.activeScope,shared:a.shared,
   local:{values:a.local.values,revision:a.local.revision,storageVersion:a.local.storageVersion},
   effective:a.effective,theme:a.theme,storage:localStorage.getItem(a.namespace)};
 });
 await page.goto(url);await editor();await settledAppearance(page);
 await page.locator('#theme-name').fill('Navigation preview');
 await page.locator('#theme-css').fill('#amp-one { --a-accent: #6b4dcc; }');
 await expect.poll(async()=>(await draft()).name).toBe('Navigation preview');
 const applied=await profile();
 const responseFor=action=>page.waitForResponse(response=>response.url().endsWith('/api/actions')
  &&response.request().postDataJSON()?.action===action);
 const accepted=async response=>{
  assert.ok(response.ok());const receipt=await response.json();assert.equal(receipt.accepted,true);return receipt.result;
 };
 const retired=async cancellation=>{
  await accepted(await cancellation);
  await expect.poll(async()=>Boolean((await state()).appearance.preview)).toBe(false);
  assert.equal((await settledAppearance(page)).preview,null);
 };
 let mode='before',held=0;
 await page.route('**/api/actions',async route=>{
  if(route.request().postDataJSON()?.action!=='appearance.preview'||mode==='pass')return route.continue();
  const response=mode==='after'?await route.fetch():null;
  if(response){assert.ok(response.ok());assert.equal((await response.json()).accepted,true)}
  held++;await new Promise(resolve=>release=resolve);
  if(response)return route.fulfill({response});
  return route.continue();
 });
 for(const phase of ['before','after','aba']){
  mode=phase==='after'?'after':'before';
  const target=held+1;
  await page.getByRole('button',{name:'Preview draft',exact:true}).click();
  await expect.poll(()=>held).toBe(target);
  await expect(page.getByRole('button',{name:'Preparing…',exact:true})).toBeDisabled();
  const cancellation=responseFor('appearance.preview.cancel');
  await openSettingsPage(page,'updates');
  if(phase==='aba')await editor();
  const completed=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='appearance.preview');
  release();release=null;
  assert.ok((await accepted(await completed)).previewId,phase+' preview was accepted before retirement');
  await retired(cancellation);
  await editor();
  await expect(page.locator('.a-appearance-preview')).toHaveCount(0);
  await expect(page.locator('#theme-name')).toHaveValue('Navigation preview');
  await expect(page.locator('#theme-css')).toHaveValue('#amp-one { --a-accent: #6b4dcc; }');
  assert.deepEqual(await profile(),applied,'A preview must not apply either appearance profile');
 }
 // A closed panel has a new component instance. Its later explicit preview
 // must run after retiring the old request, not be cancelled by old cleanup.
 mode='before';const oldCount=held;
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();await expect.poll(()=>held).toBe(oldCount+1);
 const oldCompleted=responseFor('appearance.preview'),oldCancellation=responseFor('appearance.preview.cancel');
 const closed=page.waitForResponse(response=>{
  if(response.request().method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
  const body=response.request().postDataJSON();
  return body?.action==='view.update'&&body.args?.patch?.panel===null;
 });
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 // Closure is local while the old preview holds the command queue. Do not let
 // the opening helper reuse the retiring Settings component.
 await expect.poll(async()=>(await state()).view.panel).toBe(null);
 await expect(page.locator('.a-settings-experience')).toHaveCount(0);
 await editor();
 await page.locator('#theme-name').fill('Successor preview');
 await expect.poll(async()=>(await draft()).name).toBe('Successor preview');
 const successor=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='appearance.preview'&&response.request().postDataJSON().args.name==='Successor preview');
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();
 mode='pass';release();release=null;
 assert.ok((await accepted(await oldCompleted)).previewId);
 await accepted(await oldCancellation);
 const successorResponse=await successor,successorReceipt=await successorResponse.json();
 assert.ok(successorResponse.ok());assert.equal(successorReceipt.accepted,true);
 const closeReceipt=await closed;assert.ok(closeReceipt.ok());assert.equal((await closeReceipt.json()).accepted,true);
 await expect.poll(async()=>(await state()).appearance.preview?.id).toBe(successorReceipt.result.previewId);
 assert.equal((await settledAppearance(page)).preview.theme.name,'Successor preview');
 await expect(page.locator('.a-appearance-preview')).toContainText('Successor preview');
 const successorCancellation=responseFor('appearance.preview.cancel');
 await openSettingsPage(page,'updates');await retired(successorCancellation);
 await editor();
 // Saved-library previews use the same ownership rule as the CSS button.
 const saveResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.save');
 await page.getByRole('button',{name:'Save as copy',exact:true}).click();
 const savedReceipt=await (await saveResponse).json();assert.equal(savedReceipt.accepted,true);
 await expect(page.getByText('Saved to your appearance library. Select it to use the saved version.',{exact:true})).toBeVisible();
 // Loading the saved entry must replace a different editor revision, not merely
 // confirm the fingerprint that Save already adopted into this draft.
 await page.locator('#theme-name').fill('Different unsaved revision');
 await page.locator('#theme-css').fill('#amp-one { --a-accent: #cc1122; }');
 assert.equal((await draft()).dirty,true);
 const readResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.read'
  &&response.request().postDataJSON().args.id===savedReceipt.result.id);
 await page.getByRole('button',{name:'Edit Successor preview',exact:true}).click();
 const readReceipt=await(await readResponse).json();assert.equal(readReceipt.accepted,true);
 await expect.poll(async()=>{const value=await draft();return {id:value.entry?.id,fingerprint:value.entry?.fingerprint,
  name:value.name,css:value.css,dirty:value.dirty}}).toEqual({id:savedReceipt.result.id,fingerprint:readReceipt.result.fingerprint,
   name:'Successor preview',css:'#amp-one { --a-accent: #6b4dcc; }',dirty:false});
 await expect(page.locator('#theme-css')).toHaveValue('#amp-one { --a-accent: #6b4dcc; }');
 mode='before';const savedCount=held;
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();await expect.poll(()=>held).toBe(savedCount+1);
 const savedCancellation=responseFor('appearance.preview.cancel');
 await openSettingsPage(page,'updates');
 const savedResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='appearance.preview');
 release();release=null;assert.ok((await accepted(await savedResponse)).previewId);
 await retired(savedCancellation);
 await editor();
 await page.unrouteAll({behavior:'wait'});
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();
 await expect(page.locator('.a-appearance-preview')).toBeVisible();
 await expect.poll(async()=>Boolean((await state()).appearance.preview)).toBe(true);
 await settledAppearance(page);
 const settledCancellation=responseFor('appearance.preview.cancel');
 await openSettingsPage(page,'updates');
 await retired(settledCancellation);
 await editor();
 await expect(page.locator('.a-appearance-preview')).toHaveCount(0);
 assert.deepEqual(await profile(),applied);
 assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 assert.deepEqual(errors,[]);
 console.log('Appearance preview navigation passed: pending request/receipt and settled preview retire without applying or losing draft.');
}catch(error){failure=error;throw error}
finally{release?.();await finishFixture({label:'Appearance preview navigation fixture',close:[['browser',()=>browser?.close()]],stop:stopFixture,originalError:failure})}
