// Production frontend/host, synthetic credentials and files; no provider calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {settledAppearance} from './settings-fixture.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py','--voice-settings'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Settings save feedback fixture'});
let browser,release,failure;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 let action='voice.configure',held=null,count=0,rejectSave=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action!==action)return route.continue();
  count++;held=body;const rejected=rejectSave;
  await new Promise(resolve=>release=resolve);
  if(rejected)return route.fulfill({status:409,json:{accepted:false,error:'Fixture save rejected. Draft retained.'}});
  return route.continue();
 });
 await page.goto(url);await openSettingsPage(page,'voice');
 const key=page.locator('#voice-api-key');
 await key.fill('synthetic-voice-key');
 const save=page.getByRole('button',{name:'Save voice connection',exact:true});
 const width=(await save.boundingBox()).width;
 await save.focus();await page.keyboard.press('Enter');
 await expect.poll(()=>held?.action).toBe(action);
 await expect(key).toBeDisabled();
 await expect(page.locator('input[name="voice-key-source"]').first()).toBeDisabled();
 const saving=page.getByRole('button',{name:'Saving…',exact:true});
 await expect(saving).toBeDisabled();
 assert.equal((await saving.boundingBox()).width,width);
 await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeEnabled();
 // Lock only this write scope: choosing a model is still available.
 await expect(page.locator('#preferred-voice')).toBeEnabled();
 await page.keyboard.press('Enter');assert.equal(count,1);
 for(const scheme of ['light','dark'])for(const motion of ['reduce','no-preference'])for(const detail of ['minimal','standard','detailed']){
  await page.locator('#amp-one').evaluate((el,value)=>el.dataset.interfaceDetail=value,detail);
  await page.emulateMedia({colorScheme:scheme,reducedMotion:motion});
  await expect(saving).toBeVisible();await expect(key).toBeDisabled();
 }
 await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});
 await expect(saving).toBeVisible();
 release();release=null;
 await expect(page.getByRole('dialog',{name:'Settings',exact:true}).getByRole('alert').filter({hasText:'Fixture save rejected'})).toBeVisible();
 await expect(key).toBeEnabled();await expect(key).toHaveValue('synthetic-voice-key');
 await page.emulateMedia({forcedColors:'none'});
 rejectSave=false;held=null;await save.click();await expect.poll(()=>held?.action).toBe(action);
 release();release=null;
 await expect(page.getByText('Voice key configured',{exact:true})).toBeVisible();
 await page.getByText('Change voice connection',{exact:true}).click();
 await expect(key).toHaveValue('');
 // Shared file picker keeps an explicit working label for async import.
 await openSettingsPage(page,'appearance',{activate:(_label,control,options)=>control.click(options)});
 for(const selector of ['.a-appearance-more','.a-appearance-advanced']){
  const disclosure=page.locator(selector);
  if(!await disclosure.evaluate(node=>node.open))await disclosure.locator(':scope > summary').click();
 }
 const initial=await settledAppearance(page);
 const unchangedProfile=()=>page.evaluate(()=>{
  const a=window.amplifier.getState().appearance;
  return {scope:a.activeScope,shared:a.shared,local:{revision:a.local.revision,values:a.local.values,storageVersion:a.local.storageVersion},
   effective:a.effective,storage:localStorage.getItem(a.namespace)};
 });
 const beforeProfile=await unchangedProfile();
 // Produce a genuine current format-2 ZIP through ordinary save and export.
 // Re-importing identical bytes must deduplicate, not activate the library item.
 const css='#amp-one { --a-accent: #336699; }';
 await page.locator('#theme-name').fill('fixture');await page.locator('#theme-css').fill(css);
 const savedResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.save');
 await page.getByRole('button',{name:'Save as copy',exact:true}).click();
 const saved=await(await savedResponse).json();assert.equal(saved.accepted,true);
 await expect(page.getByRole('button',{name:'Export ZIP for fixture',exact:true})).toBeVisible();
 const downloaded=page.waitForEvent('download');
 await page.getByRole('button',{name:'Export ZIP for fixture',exact:true}).click();
 const stream=await(await downloaded).createReadStream(),chunks=[];assert.ok(stream);let bytes=0;
 for await(const chunk of stream){bytes+=chunk.length;assert.ok(bytes<=4_000_000);chunks.push(chunk)}
 const archive=Buffer.concat(chunks);
 assert.equal(archive.subarray(0,4).toString('hex'),'504b0304');
 assert.equal(createHash('sha256').update(archive).digest('hex'),saved.result.fingerprint);
 assert.deepEqual(await unchangedProfile(),beforeProfile);
 action='theme.import';held=null;count=0;
 // Chromium exposes the file input with button semantics too. Measure the
 // native chooser button, while checking the input's lock independently.
 const upload=page.locator('.a-appearance-import button[data-action="theme.import"]');
 await expect(upload).toHaveAccessibleName('Upload ZIP package');
 const uploadWidth=(await upload.boundingBox()).width;
 await page.locator('#theme-import').setInputFiles({name:'fixture.zip',mimeType:'application/zip',buffer:archive});
 await expect.poll(()=>held?.action).toBe(action);
 const uploading=upload;
 await expect(uploading).toHaveAccessibleName('Importing appearance…');
 await expect(uploading).toBeDisabled();await expect(page.locator('#theme-import')).toBeDisabled();
 assert.equal((await uploading.boundingBox()).width,uploadWidth);
 await page.locator('.a-appearance-import .a-file-drop').evaluate((el,base64)=>{
  const data=new DataTransfer(),bytes=Uint8Array.from(atob(base64),c=>c.charCodeAt(0));
  data.items.add(new File([bytes],'second.zip',{type:'application/zip'}));
  el.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:data}));
 },archive.toString('base64'));
 await page.setViewportSize({width:390,height:844});
 await expect(uploading).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 const importedResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.import');
 release();release=null;
 const imported=await(await importedResponse).json();assert.equal(imported.accepted,true);
 assert.equal(imported.result.id,saved.result.id);assert.equal(imported.result.fingerprint,saved.result.fingerprint);
 await expect(upload).toBeEnabled();
 await expect(upload).toHaveAccessibleName('Upload ZIP package');
 await expect(page.getByText('fixture added to your library.',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Edit fixture',exact:true})).toHaveCount(1);
 assert.deepEqual(await unchangedProfile(),beforeProfile);
 assert.equal((await settledAppearance(page)).namespace,initial.namespace);
 assert.equal(count,1);assert.deepEqual(errors,[]);
 assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 console.log('Settings save feedback passed: scoped credential locking, duplicate suppression, failure/draft/retry, stable labels, shared file progress, motion/contrast and mobile.');
}catch(error){failure=error;throw error}
finally{release?.();await finishFixture({label:'Settings save feedback fixture',close:[['browser',()=>browser?.close()]],stop:stopFixture,originalError:failure})}
