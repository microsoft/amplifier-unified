// One synthetic, private lifecycle, not provider/runtime or release acceptance.
// Run only in a prepared test environment with the paired host's built assets.
// Required: AMPLIFIER_TEST_PYTHON and either AMPLIFIER_TEST_SERVER (the maintained
// empty_host_ui_server.py path) or AMPLIFIER_UNIFIED_ROOT. The fixture's adjacent
// tests/web readiness/lifecycle helpers remain their single implementation.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {join,resolve,basename} from 'node:path';
import {pathToFileURL} from 'node:url';
import {isDeepStrictEqual} from 'node:util';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';

const python=process.env.AMPLIFIER_TEST_PYTHON;
const fixturePath=process.env.AMPLIFIER_TEST_SERVER||
 (process.env.AMPLIFIER_UNIFIED_ROOT&&join(process.env.AMPLIFIER_UNIFIED_ROOT,'tests/fixtures/empty_host_ui_server.py'));
assert.ok(python&&fixturePath,'Select AMPLIFIER_TEST_PYTHON and AMPLIFIER_TEST_SERVER or AMPLIFIER_UNIFIED_ROOT; no live-server fallback.');
assert.equal(basename(fixturePath),'empty_host_ui_server.py');
assert.ok(!process.env.AMPLIFIER_TEST_SHARED_WORK_HOME,'This journey requires fresh disposable fixture storage.');
const fixtureUrl=pathToFileURL(resolve(fixturePath));
const {startFixture}=await import(new URL('../web/fixture-readiness.mjs',fixtureUrl));
const {finishFixture}=await import(new URL('../web/fixture-lifecycle.mjs',fixtureUrl));
const headers={Authorization:'Bearer fixture-browser-control-token'};
const cacheKey='amplifier.appearance-composition.v4';
const namespacePattern=/^amplifier\.appearance\.v2:[a-f0-9]{32}$/;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS;
const evidence={scope:'synthetic appearance lifecycle; no provider/runtime acceptance',checks:[],boot:[],pageErrors:[]};
const retained={
 local:{
  'amplifier.appearance.v1:retained-canary':JSON.stringify({version:'retained',activeScope:'client',values:{scheme:'dark',accent:'#ff00ff'}}),
  'amplifier.appearance-experiment-0801.v1:retained-canary':JSON.stringify({version:'trial',activeScope:'client',values:{scheme:'dark',accent:'#ff00ff'}}),
 },
 session:{
  'amplifier.appearance':JSON.stringify({version:1,scheme:'dark',colors:{dark:{background:'#010203',foreground:'#ff00ff'}}}),
  'amplifier.appearance-composition.v3':JSON.stringify({version:3,themeKey:'retained',scheme:'dark'}),
  'amplifier.appearance-experiment-0801-composition.v3':JSON.stringify({version:3,themeKey:'trial',activeScope:'client',storageVersion:'trial',compositionId:'trial',scheme:'dark',colors:{dark:{background:'#010203',foreground:'#ff00ff',chrome:'#010203'}}}),
  'amplifier.appearance-editor-experiment-0801.v2':JSON.stringify({version:2,draft:{entry:null,name:'Retained editor canary',css:'#amp-one{--a-accent:#ff00ff}',description:'Retained',dirty:true}}),
 },
};
let browser;
const state=page=>page.evaluate(()=>window.amplifier.getState());
const appearance=async page=>(await state(page)).appearance;
const storageRecord=(page,key,area='sessionStorage')=>page.evaluate(([key,area])=>JSON.parse(window[area].getItem(key)||'null'),[key,area]);
async function readAction(page,action,args={}){
 const receipt=await page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 assert.equal(receipt.accepted,true,action+' read accepted');
 return receipt.result;
}
async function gestureReceipt(page,action,gesture){
 const [response]=await Promise.all([
  page.waitForResponse(response=>response.request().method()==='POST'&&
   new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.action===action),
  gesture(),
 ]);
 const receipt=await response.json();
 assert.equal(receipt.accepted,true,action+' accepted');
 return receipt.result;
}
async function ready(page){
 await page.waitForFunction(()=>{const a=window.amplifier?.getState()?.appearance;return a?.availability==='connected'&&a.local?.reported===true&&!a.local.error});
 await expect(page.locator('#amp-one')).toBeVisible();
}
async function openAppearanceEditor(page){
 // The editor is nested under ordinary Advanced choices. A look switch or
 // reload can remount either disclosure; never toggle an already-open parent.
 for(const selector of ['.a-appearance-more','.a-appearance-advanced']){
  const disclosure=page.locator('[data-settings-page="appearance"] '+selector);
  await expect(disclosure).toHaveCount(1);
  if(await disclosure.getAttribute('open')===null)await disclosure.locator(':scope > summary').click();
  await expect(disclosure).toHaveJSProperty('open',true);
 }
 await expect(page.getByLabel('Reference CSS delta',{exact:true})).toBeVisible();
}
async function records(context,base){
 const response=await context.request.get(base+'/fixture/appearance-records');
 assert.ok(response.ok(),'Authenticated fixture evidence is readable');
 return response.json();
}
function inert(value){
 assert.match(value.legacy.beforeSha256,/^[a-f0-9]{64}$/);
 assert.equal(value.legacy.currentSha256,value.legacy.beforeSha256);
 assert.equal(value.legacy.unchanged,true,'Retained host records stay byte-for-byte inert');
 assert.deepEqual(value.sent,[]);assert.deepEqual(value.started,[]);
 assert.ok(value.library.legacyNames.length>0,'Fixture actually contains legacy library canaries');
}
async function canaries(page){
 const actual=await page.evaluate(value=>Object.fromEntries(Object.entries(value).map(([area,entries])=>[
  area,Object.fromEntries(Object.keys(entries).map(key=>[key,(area==='local'?localStorage:sessionStorage).getItem(key)])),
 ])),retained);
 assert.deepEqual(actual,retained,'Old browser records remain stored and unchanged');
}
async function quiet(page){
 const settings=page.locator('.a-appearance-settings');
 await expect(settings.locator('.a-appearance-feedback,.a-appearance-profile-feedback')).toHaveCount(0);
 await expect(settings.getByRole('button',{name:/^Undo\b/})).toHaveCount(0);
 assert.deepEqual(await page.evaluate(()=>window.appearanceSuccessNoise||[]),[],'No transient Saved/Undo preference UI');
}
async function observeQuiet(page){
 await page.locator('.a-appearance-settings').evaluate(node=>{
  window.appearanceSuccessNoise=[];
  const sample=()=>{
   const fields=[...node.querySelectorAll('[data-feedback-for],.a-appearance-profile-feedback')].map(n=>n.textContent.trim());
   const undo=[...node.querySelectorAll('button')].filter(n=>/^Undo\b/.test(n.textContent.trim()));
   for(const text of fields)if(/\bSaved\b/.test(text)&&window.appearanceSuccessNoise.length<20)window.appearanceSuccessNoise.push(text);
   if(undo.length&&window.appearanceSuccessNoise.length<20)window.appearanceSuccessNoise.push('Undo');
  };
  new MutationObserver(sample).observe(node,{subtree:true,childList:true,characterData:true});sample();
 });
}
async function seedAndObserve(page){
 await page.addInitScript(({retained,cacheKey})=>{
  // Playwright also invokes init scripts in opaque sandboxed thumbnail frames.
  // Only the top-level test document owns these storage canaries and probes.
  if(window.top!==window)return;
  // Seed once, not on reload: deletion or mutation of a canary must be observable.
  if(!sessionStorage.getItem('appearance-lifecycle-seeded')){
   for(const [key,value] of Object.entries(retained.local))localStorage.setItem(key,value);
   for(const [key,value] of Object.entries(retained.session))sessionStorage.setItem(key,value);
   sessionStorage.setItem('appearance-lifecycle-seeded','1');
  }
  window.appearanceBootFrames=[];window.appearanceBootOverflow=false;
  let stopped=false,frame;
  const sample=()=>{
   const root=document.documentElement;if(!root)return;
   const boot=document.querySelector('.boot');
   if(!boot||getComputedStyle(boot).display==='none')return;
   if(window.appearanceBootFrames.length>=120){window.appearanceBootOverflow=true;return}
   window.appearanceBootFrames.push({hydrated:!!document.getElementById('amp-one'),scheme:root.dataset.bootScheme||null,
    background:root.style.getPropertyValue('--boot-bg'),ink:root.style.getPropertyValue('--boot-ink'),
    bodyBackground:document.body?getComputedStyle(document.body).backgroundColor:null,
    bootInk:boot?getComputedStyle(boot).color:null,cache:sessionStorage.getItem(cacheKey)});
  };
  const paint=()=>{if(stopped)return;sample();frame=requestAnimationFrame(paint)};
  frame=requestAnimationFrame(paint);
  window.stopAppearanceBootProbe=()=>{sample();stopped=true;cancelAnimationFrame(frame);return {frames:window.appearanceBootFrames,overflow:window.appearanceBootOverflow}};
 },{retained,cacheKey});
}
async function prehydration(page,navigate,expected){
 let release;
 const gate=new Promise(resolve=>{release=resolve});
 const hold=async route=>{await gate;await route.continue().catch(()=>{})};
 await page.route('**/api/**',hold);
 try{
  await navigate();
  await expect(page.locator('.boot')).toBeVisible();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const {frames,overflow}=await page.evaluate(()=>window.stopAppearanceBootProbe());
  assert.equal(overflow,false,'First-paint evidence is bounded without silently dropping earlier frames');
  assert.ok(frames.length,'Observe document before API hydration, not only final getState');
  assert.ok(frames.every(frame=>!frame.hydrated),'API gate actually holds hydration');
  for(const current of frames){
  if(expected){
   assert.equal(current.scheme,expected.scheme);
   assert.equal(current.background,expected.colors[expected.scheme].background);
   assert.equal(current.ink,expected.colors[expected.scheme].foreground);
   assert.equal(current.bodyBackground,expected.colors[expected.scheme].background);
   assert.equal(current.bootInk,expected.colors[expected.scheme].foreground);
   assert.deepEqual(JSON.parse(current.cache),expected,'Synchronous bootstrap consumes the confirmed v4 cache');
  }else{
   assert.equal(current.scheme,null);assert.equal(current.background,'');
   assert.equal(current.bodyBackground,'rgb(232, 238, 255)','Loading surface uses the neutral cold-boot paint, not retained magenta/dark data');
   assert.equal(current.cache,null,'Cold boot must not promote a retained/trial cache');
  }
  }
  evidence.boot.push({phase:expected?'confirmed-cache-reload':'cold-legacy-only',frames});
 }finally{release();await page.unroute('**/api/**',hold)}
 await ready(page);
}
async function runFixture(malformed,journey){
 const owned=await startFixture({command:python,args:['-u',resolve(fixturePath),'--synthetic-discovery','--appearance-records',
  ...(malformed?['--appearance-malformed']:[])],env:process.env,tag:'empty-host-ui',name:'Appearance lifecycle',timeoutMs:20000});
 let context,page,failure;
 try{
  context=await browser.newContext({viewport:{width:1440,height:1050},colorScheme:'light',extraHTTPHeaders:headers});
  context.setDefaultTimeout(10000);
  context.on('page',p=>p.on('pageerror',error=>{if(evidence.pageErrors.length<20)evidence.pageErrors.push(error.message)}));
  page=await context.newPage();
  await journey({context,page,base:owned.base});
 }catch(error){failure=error;throw error}
 finally{
  try{if(out&&page)await page.screenshot({path:join(out,malformed?'malformed.png':'lifecycle.png')})}
  catch(error){if(!failure)failure=error;else console.error('Appearance evidence screenshot failed:',error.message)}
  await finishFixture({label:'Appearance lifecycle',close:[['Appearance context',()=>context?.close()]],stop:owned.stop,originalError:failure});
  if(failure)throw failure;
 }
}

try{
 if(out)await mkdir(out,{recursive:true});
 browser=await chromium.launch({headless:true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
  args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote','--no-sandbox']:['--no-sandbox']});
 await runFixture(false,async({context,page,base})=>{
  const cold=await records(context,base);inert(cold);
  assert.equal(cold.production.present,false);assert.equal(cold.production.revision,0);
  assert.deepEqual(cold.library.newNames,[]);
  await seedAndObserve(page);
  await prehydration(page,()=>page.goto(base,{waitUntil:'domcontentloaded'}));
  const initial=await appearance(page),namespace=initial.namespace;
  assert.match(namespace,namespacePattern);
  assert.equal(initial.activeScope,'shared');
  assert.equal(initial.effective.appearanceId,'builtin:default');
  assert.equal(initial.effective.scheme,'system');assert.equal(initial.effective.density,'comfortable');
  assert.equal(initial.effective.layout,'balanced');assert.equal(initial.effective.decorations,true);
  assert.ok(!Object.hasOwn(initial.effective,'accent'));
  assert.equal(initial.local.values,null);
  await openSettingsPage(page,'appearance');await observeQuiet(page);
  const thumbnail=page.locator('.a-appearance-thumbnail iframe').first();
  await expect(thumbnail).toBeVisible();await expect(thumbnail).toHaveAttribute('sandbox','');
  const thumbnailFrame=await (await thumbnail.elementHandle()).contentFrame();
  await thumbnailFrame.waitForLoadState('domcontentloaded');
  assert.deepEqual(await thumbnailFrame.evaluate(()=>({
   child:window.top!==window,probe:typeof window.stopAppearanceBootProbe,
   frames:typeof window.appearanceBootFrames,
  })),{child:true,probe:'undefined',frames:'undefined'},
  'An actual opaque thumbnail frame is mounted, but the top-document storage/paint probe never runs there');
  evidence.checks.push('opaque thumbnail mounted with no top-document init probe; all page errors remain fatal');
  await expect(page.locator('[data-appearance-card="builtin:default"] input')).toBeChecked();
  await expect(page.locator('.a-appearance-saved-row')).toHaveCount(0);
  await canaries(page);
  const untouched=await records(context,base);inert(untouched);
  assert.equal(untouched.production.present,false,'Opening/reading settings does not persist virtual Global defaults');
  assert.deepEqual(untouched.library,cold.library);
  evidence.checks.push('cold prehydration ignores retained caches; host/browser legacy data inert; Global defaults virtual');

  // Only this explicit UI gesture creates the private fixture's production Global.
  await page.getByRole('radio',{name:'Dark',exact:true}).check();
  await expect.poll(async()=>(await appearance(page)).shared.values.scheme).toBe('dark');
  const global=(await appearance(page)).shared;
  const persisted=await records(context,base);inert(persisted);
  assert.equal(persisted.production.present,true);
  assert.equal(persisted.production.namespace,namespace);
  assert.equal(persisted.production.revision,global.revision);
  assert.deepEqual(persisted.production.values,global.values);
  await quiet(page);

  // Hold one real storage ACK before delivery. A disk write alone must not
  // select the profile; the host read and rendered source must agree afterward.
  let releaseAck,held;
  const ackGate=new Promise(resolve=>{releaseAck=resolve});
  const holdAck=async route=>{
   const body=route.request().postDataJSON();
   if(!held&&body?.action==='appearance.client.report'&&body.args?.status==='stored'&&body.args.commandId){
    held=body.args;await ackGate;
   }
   await route.continue();
  };
  await page.route('**/api/actions',holdAck);
  try{
   await page.getByRole('button',{name:'This browser',exact:true}).click();
   await expect.poll(()=>!!held).toBe(true);
   assert.equal(held.generation,initial.generation);
   assert.deepEqual(held.values,global.values);
   await expect(page.locator('.a-appearance-profile-feedback')).toContainText('Saving…');
   assert.equal((await appearance(page)).activeScope,'shared','No activation before exact storage ACK');
   await expect(page.getByRole('button',{name:'This browser',exact:true})).toHaveAttribute('aria-pressed','false');
   releaseAck();
   await expect.poll(async()=>(await appearance(page)).activeScope).toBe('client');
  }finally{releaseAck();await page.unroute('**/api/actions',holdAck)}
  let current=await readAction(page,'appearance.preferences.read');
  assert.equal(current.local.reported,true);assert.equal(current.activeScope,'client');
  assert.deepEqual(current.local.values,global.values,'First browser selection copies the complete Global profile');
  let local=await storageRecord(page,namespace,'localStorage');
  assert.equal(local.activeScope,'client');assert.equal(local.version,current.local.storageVersion);
  assert.deepEqual(local.values,current.local.values);
  await quiet(page);
  evidence.checks.push('explicit Global write/readback; complete browser copy activates only after storage ACK');

  await page.getByRole('combobox',{name:'Density',exact:true}).selectOption('compact');
  await expect.poll(async()=>(await appearance(page)).effective.density).toBe('compact');
  await page.getByLabel('Accent color',{exact:true}).fill('#b84477');
  await expect.poll(async()=>(await appearance(page)).effective.accent).toBe('#b84477');
  await page.getByRole('button',{name:'Use appearance accent',exact:true}).click();
  await expect.poll(async()=>Object.hasOwn((await appearance(page)).effective,'accent')).toBe(false);
  assert.equal((await appearance(page)).effective.appearanceId,'builtin:default');
  await page.getByLabel('Accent color',{exact:true}).fill('#4477bb');
  await expect.poll(async()=>(await appearance(page)).effective.accent).toBe('#4477bb');
  await page.getByRole('button',{name:'Restore neutral appearance',exact:true}).click();
  await expect.poll(async()=>(await appearance(page)).effective.appearanceId).toBe('reference');
  current=await readAction(page,'appearance.preferences.read');
  assert.ok(!Object.hasOwn(current.effective,'accent'),'Changing look drops the prior accent override');
  assert.equal(current.effective.density,'compact');assert.equal(current.effective.scheme,'dark');
  assert.deepEqual(current.shared,global,'Browser edits never mix into Global');
  await quiet(page);
  evidence.checks.push('quiet preference success; accent-only reset and look switch preserve other preferences');

  const name='Lifecycle reference',css='#amp-one[data-theme-scheme="light"]{--a-accent:#338855}\n#amp-one[data-theme-scheme="dark"]{--a-accent:#55bb88}';
  await openAppearanceEditor(page);
  await page.getByLabel('Appearance name',{exact:true}).fill(name);
  await page.getByLabel('Reference CSS delta',{exact:true}).fill(css);
  const editorKey='amplifier.appearance-editor.v3:'+namespace;
  assert.equal((await storageRecord(page,editorKey)).version,3);
  const beforePreview=await readAction(page,'appearance.preferences.read');
  await expect.poll(async()=>!!(await storageRecord(page,cacheKey))).toBe(true);
  const cacheBeforePreview=await storageRecord(page,cacheKey);
  await page.getByRole('button',{name:'Preview draft',exact:true}).click();
  await expect(page.locator('.a-appearance-preview')).toContainText(name);
  await expect.poll(async()=>page.locator('#amp-one').evaluate(n=>getComputedStyle(n).getPropertyValue('--a-accent').trim())).toBe('#55bb88');
  current=await readAction(page,'appearance.preferences.read');
  assert.deepEqual(current.effective,beforePreview.effective);assert.deepEqual(current.shared,global);
  assert.deepEqual(await storageRecord(page,cacheKey),cacheBeforePreview,'Preview never replaces confirmed first-paint cache');
  await page.locator('.a-appearance-preview').getByRole('button',{name:'Cancel preview',exact:true}).click();
  await expect(page.locator('.a-appearance-preview')).toHaveCount(0);
  await expect.poll(async()=>!!(await appearance(page)).preview).toBe(false);
  assert.deepEqual((await appearance(page)).theme,beforePreview.theme);
  await expect(page.getByLabel('Reference CSS delta',{exact:true})).toHaveValue(css);

  const saved=await gestureReceipt(page,'theme.save',()=>page.getByRole('button',{name:'Save as copy',exact:true}).click());
  assert.equal(saved.packageVersion,2);assert.equal(saved.base,'reference-v1');
  const readSaved=await readAction(page,'theme.read',{id:saved.id});
  assert.equal(readSaved.css,css);assert.equal(readSaved.name,name);
  const row=page.locator('.a-appearance-saved-row').filter({has:page.getByText(name,{exact:true})});
  const [download]=await Promise.all([page.waitForEvent('download'),row.getByRole('button',{name:'Export ZIP',exact:true}).click()]);
  const stream=await download.createReadStream(),chunks=[];
  let bytes=0;
  for await(const chunk of stream){bytes+=chunk.length;assert.ok(bytes<=4_000_000,'Finite export');chunks.push(chunk)}
  const zip=Buffer.concat(chunks),digest=createHash('sha256').update(zip).digest('hex');
  assert.equal(zip.readUInt32LE(0),0x04034b50);assert.equal(digest,readSaved.fingerprint,'Downloaded bytes match the validated saved ZIP');
  await row.getByRole('button',{name:'Delete '+name,exact:true}).click();
  await page.getByRole('button',{name:'Confirm delete',exact:true}).click();
  await expect(row).toHaveCount(0);
  const imported=await gestureReceipt(page,'theme.import',()=>page.locator('#theme-import').setInputFiles({name:'lifecycle.zip',mimeType:'application/zip',buffer:zip}));
  const readImported=await readAction(page,'theme.read',{id:imported.id});
  assert.equal(readImported.packageVersion,2);assert.equal(readImported.base,'reference-v1');
  assert.equal(readImported.css,css);assert.equal(readImported.name,name);assert.equal(readImported.fingerprint,digest);
  assert.deepEqual((await appearance(page)).effective,beforePreview.effective,'Library operations do not apply a look');
  evidence.package={bytes,sha256:digest,packageVersion:readImported.packageVersion};
  evidence.checks.push('private v3 editor; preview/cancel preserves profile/cache; format-2 export/import exact-byte readback');

  await expect.poll(async()=>(await storageRecord(page,cacheKey))?.compositionId).toBe((await appearance(page)).composition.id);
  const confirmed=await storageRecord(page,cacheKey);
  assert.equal(confirmed.version,4);assert.equal(confirmed.namespace,namespace);assert.equal(confirmed.activeScope,'client');
  assert.equal(confirmed.scheme,'dark');
  local=await storageRecord(page,namespace,'localStorage');
  await prehydration(page,()=>page.reload({waitUntil:'domcontentloaded'}),confirmed);
  current=await readAction(page,'appearance.preferences.read');
  assert.equal(current.namespace,namespace);assert.equal(current.activeScope,'client');
  assert.deepEqual(current.local.values,local.values);assert.equal(current.local.storageVersion,local.version);
  await openSettingsPage(page,'appearance');await observeQuiet(page);
  await openAppearanceEditor(page);
  await expect(page.getByLabel('Reference CSS delta',{exact:true})).toHaveValue(css);
  await canaries(page);
  const tab=await context.newPage();await tab.goto(base);await ready(tab);await openSettingsPage(tab,'appearance');
  assert.equal((await appearance(tab)).namespace,namespace);
  assert.deepEqual((await appearance(tab)).local.values,local.values);
  await tab.getByRole('combobox',{name:'Density',exact:true}).selectOption('comfortable');
  await expect.poll(async()=>(await appearance(page)).local.values.density).toBe('comfortable');
  await expect.poll(async()=>(await appearance(tab)).local.storageVersion).toBe((await storageRecord(page,namespace,'localStorage')).version);
  await tab.getByRole('button',{name:'Global',exact:true}).click();
  await expect.poll(async()=>(await appearance(page)).activeScope).toBe('shared');
  assert.deepEqual((await appearance(page)).effective,global.values);
  const retainedLocal=(await appearance(page)).local.values;
  await page.getByRole('button',{name:'This browser',exact:true}).click();
  await expect.poll(async()=>(await appearance(tab)).activeScope).toBe('client');
  assert.deepEqual((await appearance(page)).effective,retainedLocal,'Source switches retain, not recopy, browser values');
  assert.deepEqual((await appearance(page)).shared,global);
  await tab.close();await quiet(page);
  evidence.checks.push('confirmed v4 cache paints before reload hydration; stable namespace and editor; real cross-tab storage/source reconciliation');

  const lastConfirmed=await readAction(page,'appearance.preferences.read'),lastStored=await storageRecord(page,namespace,'localStorage');
  await expect.poll(async()=>{
   const cache=await storageRecord(page,cacheKey);
   return cache?.namespace===lastConfirmed.namespace&&cache.activeScope==='client'&&
    cache.storageVersion===lastStored.version&&cache.compositionId===lastConfirmed.composition.id;
  }).toBe(true);
  const quotaCache=await storageRecord(page,cacheKey);
  assert.equal(quotaCache.scheme,'dark');
  const quotaReports=[],quotaTasks=[];let quotaReportOverflow=false;
  const traceReport=response=>{
   const request=response.request();
   if(request.method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return;
   const body=request.postDataJSON();
   if(body?.action!=='appearance.client.report')return;
   if(quotaReports.length>=64){quotaReportOverflow=true;return}
   const entry={sequence:quotaReports.length,args:body.args};quotaReports.push(entry);
   quotaTasks.push(response.json().then(receipt=>{
    entry.accepted=receipt.accepted;entry.result=receipt.result;
   },error=>{entry.decodeError=error.message}));
  };
  page.on('response',traceReport);
  await page.evaluate(({namespace,cacheKey})=>{
   const descriptor=Object.getOwnPropertyDescriptor(Storage.prototype,'setItem'),original=descriptor.value;
   window.appearanceQuotaPaints=[];window.appearanceQuotaPaintOverflow=false;
   Object.defineProperty(Storage.prototype,'setItem',{...descriptor,value:function(key,value){
    if(this===localStorage&&key===namespace)throw new DOMException('Synthetic appearance quota failure','QuotaExceededError');
    if(this===sessionStorage&&key===cacheKey){
     if(window.appearanceQuotaPaints.length>=64)window.appearanceQuotaPaintOverflow=true;
     else window.appearanceQuotaPaints.push(JSON.parse(value));
    }
    return original.call(this,key,value);
   }});
   window.restoreAppearanceStorage=()=>Object.defineProperty(Storage.prototype,'setItem',descriptor);
  },{namespace,cacheKey});
  try{
   await page.getByRole('button',{name:'Global',exact:true}).click();
   await expect(page.locator('.a-appearance-profile-feedback')).toContainText(/fail|storage|quota|could not/i);
   await expect(page.locator('.a-appearance-profile-feedback').getByRole('button',{name:'Retry',exact:true})).toBeVisible();
   current=await readAction(page,'appearance.preferences.read');
   assert.equal(current.activeScope,'client','Storage failure must not silently select Global');
   assert.deepEqual(current.effective,lastConfirmed.effective);assert.deepEqual(current.shared,global);
   assert.deepEqual(await storageRecord(page,namespace,'localStorage'),lastStored);
   await expect.poll(async()=>{
    current=await readAction(page,'appearance.preferences.read');
    return current.command?.status==='failed'&&current.local.reported===true&&!current.local.error;
   }).toBe(true);
   assert.equal(current.namespace,lastConfirmed.namespace);assert.equal(current.generation,lastConfirmed.generation);
   assert.equal(current.activeScope,'client');assert.deepEqual(current.effective,lastConfirmed.effective);
   assert.deepEqual(current.local.values,lastStored.values);assert.equal(current.local.storageVersion,lastStored.version);
   assert.equal(current.local.revision,lastConfirmed.local.revision);assert.equal(current.composition.id,lastConfirmed.composition.id);
   // A failed intent is not authority. Exact re-confirmation of the old record
   // may restore its valid cache after transient error invalidation.
   await expect.poll(()=>storageRecord(page,cacheKey)).toEqual(quotaCache);
  }finally{
   page.off('response',traceReport);
   try{
    await Promise.all(quotaTasks);
    evidence.quota={reports:quotaReports,reportOverflow:quotaReportOverflow,
     ...await page.evaluate(()=>({cacheWrites:window.appearanceQuotaPaints,cacheOverflow:window.appearanceQuotaPaintOverflow})),lastConfirmed:{
     namespace:lastConfirmed.namespace,generation:lastConfirmed.generation,revision:lastConfirmed.local.revision,
     storageVersion:lastStored.version,activeScope:lastConfirmed.activeScope,compositionId:lastConfirmed.composition.id,
    },settled:current};
   }finally{await page.evaluate(()=>window.restoreAppearanceStorage())}
  }
  assert.equal(evidence.quota.reportOverflow,false,'Quota report trace did not overflow');
  assert.equal(evidence.quota.cacheOverflow,false,'Quota cache trace did not overflow');
  assert.ok(quotaReports.every(report=>!report.decodeError),'Every observed report receipt was decoded');
  const failedReportIndex=quotaReports.findIndex(report=>report.args.commandId&&report.args.status==='failed'&&report.accepted===true&&report.result?.status==='failed');
  assert.ok(failedReportIndex>=0,
   'The rejected source switch has a failed command receipt, not a stored/activated outcome');
  assert.ok(quotaReports.slice(failedReportIndex+1).some(report=>report.args.status==='ready'&&!report.args.commandId&&report.accepted===true&&
   report.args.generation===lastConfirmed.generation&&report.args.activeScope==='client'&&
   report.args.storageVersion===lastStored.version&&report.args.revision===lastConfirmed.local.revision&&
   isDeepStrictEqual(report.args.values,lastStored.values)),
   'A fresh exact-record ready report re-confirms only the old client profile');
  assert.ok(!quotaReports.some(report=>report.args.commandId===quotaReports[failedReportIndex].args.commandId&&
   (report.result?.status==='stored'||report.result?.activated===true)),'Failed command must never report stored or activated');
  for(const cached of evidence.quota.cacheWrites)assert.deepEqual(cached,quotaCache,'Failed Global intent cannot seed first-paint colors');
  await prehydration(page,()=>page.reload({waitUntil:'domcontentloaded'}),quotaCache);
  current=await readAction(page,'appearance.preferences.read');
  assert.equal(current.activeScope,'client');assert.equal(current.namespace,namespace);
  assert.deepEqual(current.effective,lastConfirmed.effective);assert.deepEqual(current.shared,global);
  assert.deepEqual(await storageRecord(page,namespace,'localStorage'),lastStored);
  const final=await records(context,base);inert(final);await canaries(page);
  assert.deepEqual(final.production,persisted.production);
  assert.deepEqual(final.library.legacyNames,cold.library.legacyNames);
  assert.equal(final.library.newNames.length,1,'Only the explicitly imported production package exists');
  evidence.checks.push('failed source command re-confirms only the old client record/cache; reload paints that confirmed profile, never attempted Global');
 });
 await runFixture(true,async({context,page,base})=>{
  const before=await records(context,base);inert(before);assert.equal(before.production.present,true);
  await page.goto(base);
  await expect(page.getByRole('alert')).toContainText('Appearance unavailable');
  await expect(page.getByRole('button',{name:'Retry connection',exact:true})).toBeVisible();
  const failed=await appearance(page);
  assert.equal(failed.availability,'failed');assert.ok(failed.error);
  await expect(page.locator('#amp-one')).toHaveCount(0);
  assert.equal(await storageRecord(page,cacheKey),null,'Invalid production data cannot seed a usable cache');
  const after=await records(context,base);inert(after);
  assert.deepEqual(after.production,before.production,'Invalid production data is not overwritten with defaults');
  assert.deepEqual(after.library,before.library);
  evidence.checks.push('malformed new production record fails explicitly, without migration, fallback or runtime start');
 });
 assert.deepEqual(evidence.pageErrors,[]);
 evidence.status='passed';
}catch(error){evidence.status='failed';evidence.error=error.message;throw error}
finally{
 await browser?.close();
 if(out)await writeFile(join(out,'appearance-lifecycle.json'),JSON.stringify(evidence,null,2)+'\n');
}
console.log(JSON.stringify({status:evidence.status,checks:evidence.checks,scope:evidence.scope}));