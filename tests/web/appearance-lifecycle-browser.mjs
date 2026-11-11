// Real host plus production assets, two same-profile tabs and an isolated context.
// Run after the parent's normal client synchronization/build; no provider calls.
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {startSettingsFixture} from './settings-fixture.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';

const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-appearance-lifecycle';
const report={status:'running',checks:[],geometry:[],screenshots:[]};
let browser,page,failure,stop;
const state=page=>page.evaluate(()=>window.amplifier.getState());
const action=(page,name,args={})=>page.evaluate(async([name,args])=>{
 const receipt=await window.amplifier.dispatch(name,args);
 if(receipt.accepted!==true)throw Error(receipt.error||name+' rejected');
 return receipt;
},[name,args]);
const appearance=async page=>(await action(page,'appearance.preferences.read')).result;
const check=name=>report.checks.push(name);
async function stored(page,receipt){
 assert.equal(receipt.result.status,'awaiting-storage');
 assert.equal(receipt.result.stored,false);
 const id=receipt.result.commandId;
 assert.ok(id,'The admitted command must identify its storage acknowledgement');
 await expect.poll(async()=>{
  const command=(await appearance(page)).command;
  return command?.id===id?command.status:null;
 }).toBe('stored');
 return (await appearance(page)).command;
}
async function updateGlobal(page,patch){
 const current=await appearance(page);
 return (await action(page,'appearance.preferences.update',{
  scope:'shared',generation:current.generation,expectedRevision:current.shared.revision,patch,
 })).result;
}
async function select(page,activeScope){
 const current=await appearance(page);
 return stored(page,await action(page,'appearance.preferences.select',{
  generation:current.generation,activeScope,expectedRevision:current.local.revision,
  expectedStorageVersion:current.local.storageVersion,
  ...(activeScope==='client'&&current.local.values===null?{expectedSharedRevision:current.shared.revision}:{}),
 }));
}
async function updateLocal(page,patch){
 const current=await appearance(page);
 return stored(page,await action(page,'appearance.preferences.update',{
  scope:'client',generation:current.generation,expectedRevision:current.local.revision,
  expectedStorageVersion:current.local.storageVersion,patch,
 }));
}
async function showRow(row){
 for(let depth=0;!await row.isVisible();depth++){
  assert.ok(depth<4,'A settings field must have a reachable labeled disclosure');
  // Open the outermost closed ancestor first; clicking a hidden inner summary
  // cannot reveal a field nested inside another closed disclosure.
  const closed=row.locator('xpath=ancestor::details[not(@open) and not(ancestor::details[not(@open)])]');
  assert.equal(await closed.count(),1,'A hidden field must have an expandable ancestor');
  await closed.locator(':scope > summary').click();
 }
}
const preferenceFields=['appearanceId','scheme','layout','decorations','density','accent',
 'interfaceDetail','conversationStatus','collapseInterimReplies','workStartView','planPlacement',
 'executionDetail','executionVisibility','executionMetrics'];

try{
 await mkdir(out,{recursive:true});
 const fixture=await startSettingsFixture();stop=fixture.stop;const url=fixture.base;
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const options={viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}};
 const profile=await browser.newContext(options),peer=await profile.newPage();
 page=await profile.newPage();
 const isolatedProfile=await browser.newContext(options),isolated=await isolatedProfile.newPage();
 const errors=[];
 for(const client of [page,peer,isolated]){
  client.on('pageerror',error=>errors.push(error.message));
  await client.goto(url);await showFixtureConversation(client);
  await expect.poll(async()=>(await appearance(client)).local.reported).toBe(true);
 }
 await action(page,'session.create');
 const initial=await appearance(page),namespace=initial.namespace;
 assert.equal(initial.activeScope,'shared');assert.equal(initial.local.values,null);
 assert.equal((await appearance(peer)).namespace,namespace);
 assert.equal((await appearance(isolated)).namespace,namespace);
 assert.notEqual((await state(page)).client.id,(await state(peer)).client.id);
 assert.notEqual((await state(page)).client.id,(await state(isolated)).client.id);
 await updateGlobal(page,{appearanceId:'builtin:graphite',scheme:'light',executionVisibility:{step:false},executionMetrics:[]});
 await expect.poll(async()=>(await appearance(isolated)).effective.scheme).toBe('light');
 const admittedGlobal=(await appearance(page)).shared.values;
 // An absent accent means the selected appearance owns its color, not that
 // this profile inherits a color from the other profile.
 assert.deepEqual(Object.keys(admittedGlobal).sort(),preferenceFields.filter(field=>field!=='accent').sort(),'Global contains every defaulted preference; accent remains appearance-owned');
 assert.equal(Object.hasOwn(admittedGlobal,'accent'),false);
 const firstSelect=await select(page,'client');
 assert.deepEqual((await appearance(page)).local.values,admittedGlobal,'First use copies the complete admitted Global set');
 await expect.poll(async()=>(await appearance(peer)).activeScope).toBe('client');
 assert.equal((await appearance(isolated)).activeScope,'shared');
 // Undo is the entire browser state, including the uninitialized profile.
 await stored(page,await action(page,'appearance.preferences.undo',{undoId:firstSelect.undoId}));
 assert.equal((await appearance(page)).activeScope,'shared');assert.equal((await appearance(page)).local.values,null);
 await expect.poll(async()=>(await appearance(peer)).local.values).toBe(null);
 await select(page,'client');
 await updateLocal(page,{appearanceId:'builtin:atelier'});
 const retained=(await appearance(page)).local.values,composition=(await appearance(page)).composition.id;
 await expect.poll(async()=>(await appearance(peer)).effective.appearanceId).toBe('builtin:atelier');
 await updateGlobal(isolated,{appearanceId:'builtin:aurora',scheme:'dark',executionVisibility:{step:true,turn:false,conversation:false},executionMetrics:['calls'],decorations:false});
 await expect.poll(async()=>(await appearance(isolated)).effective.appearanceId).toBe('builtin:aurora');
 for(const client of [page,peer]){
  await expect.poll(async()=>(await appearance(client)).shared.values.scheme).toBe('dark');
  assert.deepEqual((await appearance(client)).effective,retained,'No Global field or nested choice leaks into a saved browser profile');
 }
 assert.equal((await appearance(page)).composition.id,composition,'Inactive Global changes do not change local composition identity');
 const saved=await page.evaluate(namespace=>JSON.parse(localStorage.getItem(namespace)),namespace);
 assert.deepEqual(Object.keys(saved).sort(),['activeScope','values','version']);
 assert.equal(saved.activeScope,'client');assert.deepEqual(saved.values,retained);assert.equal(typeof saved.version,'string');
 assert.equal(await isolated.evaluate(namespace=>localStorage.getItem(namespace),namespace),null);
 await select(page,'shared');
 await expect.poll(async()=>(await appearance(peer)).activeScope).toBe('shared');
 assert.deepEqual((await appearance(page)).local.values,retained);
 assert.equal((await appearance(page)).effective.scheme,'dark');
 // Editing an initialized inactive profile must not implicitly select it.
 const globalComposition=(await appearance(page)).composition.id;
 await updateLocal(page,{density:'compact'});
 assert.equal((await appearance(page)).activeScope,'shared');
 assert.equal((await appearance(page)).composition.id,globalComposition);
 await select(peer,'client');
 await expect.poll(async()=>(await appearance(page)).activeScope).toBe('client');
 assert.deepEqual((await appearance(page)).effective,{...retained,density:'compact'});
 const stale=await appearance(page);
 await updateLocal(peer,{density:'comfortable'});
 await expect.poll(async()=>(await appearance(page)).local.values.density).toBe('comfortable');
 const newer=await appearance(page);
 const conflict=await page.evaluate(async args=>{
  try{return await window.amplifier.dispatch('appearance.preferences.update',args)}
  catch(error){return {accepted:false,error:error.message}}
 },{scope:'client',generation:stale.generation,expectedRevision:stale.local.revision,
  expectedStorageVersion:stale.local.storageVersion,patch:{density:'compact'}});
 assert.equal(conflict.accepted,false,'A stale same-profile tab cannot overwrite the newer complete profile');
 assert.deepEqual((await appearance(page)).local.values,newer.local.values);
 assert.deepEqual((await appearance(page)).shared,newer.shared);
 await updateLocal(page,{density:'compact'});
 check('Complete first-use copy and whole-state Undo; independent false/empty/default choices; retained source switches; inactive edits; same-profile tabs and isolated context; stable composition identity.');

 await openSettingsPage(page,'appearance');
 await page.getByText('Advanced customization',{exact:true}).click();
 const draft={name:'Private whole-profile draft',css:'#amp-one { --a-accent: #ad3479; }'};
 await page.locator('#theme-name').fill(draft.name);await page.locator('#theme-css').fill(draft.css);
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();
 await expect.poll(async()=>(await appearance(page)).preview?.theme?.name).toBe(draft.name);
 await select(peer,'shared');
 await expect.poll(async()=>(await appearance(page)).activeScope).toBe('shared');
 await updateGlobal(isolated,{density:'compact'});
 await expect(page.locator('#theme-name')).toHaveValue(draft.name);await expect(page.locator('#theme-css')).toHaveValue(draft.css);
 assert.equal(JSON.stringify((await state(page)).view).includes(draft.css),false,'Private editor CSS is not a shared view field');
 for(const client of [peer,isolated]){
  assert.equal((await appearance(client)).preview,null);
  assert.equal(await client.evaluate(namespace=>sessionStorage.getItem('amplifier.appearance-editor.v2:'+namespace)?.includes('Private whole-profile draft')||false,namespace),false);
 }
 await expect.poll(()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).getPropertyValue('--a-accent').trim())).toBe('#ad3479');
 await page.locator('.a-appearance-preview').getByRole('button',{name:'Cancel preview',exact:true}).click();
 await expect.poll(async()=>(await appearance(page)).preview).toBe(null);
 await expect(page.locator('#theme-name')).toHaveValue(draft.name);await expect(page.locator('#theme-css')).toHaveValue(draft.css);
 check('Cross-tab source selection and Global updates preserve this tab private preview/editor draft without broadcasting either.');

 await openSettingsPage(page,'appearance');
 const surface=page.locator('.a-settings-page-content:not([hidden]) .a-appearance-settings');
 const experience=page.locator('.a-settings-experience');
 const row=field=>surface.locator(`[data-appearance-preference="${field}"]`);
 const source=surface.getByRole('group',{name:'Appearance profile',exact:true});
 const sourceControl=label=>source.getByRole('button',{name:label,exact:true});
 const selectUI=async(scope,label)=>{
  await sourceControl(label).click();
  await expect.poll(async()=>(await appearance(page)).activeScope).toBe(scope);
  await expect(sourceControl(label)).toHaveAttribute('aria-pressed','true');
 };
 const formShape=()=>surface.evaluate(el=>Array.from(el.querySelectorAll('[data-appearance-preference]')).map(row=>({
  field:row.dataset.appearancePreference,
  disclosures:Array.from(el.querySelectorAll('details')).filter(details=>details.contains(row)).map(details=>details.querySelector('summary')?.textContent.trim()),
  controls:Array.from(row.querySelectorAll('input,select')).map(input=>({tag:input.tagName,type:input.type,name:input.getAttribute('aria-label')||input.closest('label')?.textContent.trim(),options:input.tagName==='SELECT'?Array.from(input.options).map(option=>option.value):null})),
 })));
 await expect(source.getByRole('button')).toHaveCount(2);
 await expect(sourceControl('Global')).toHaveAttribute('aria-pressed','true');
 const globalShape=await formShape();assert.deepEqual(globalShape.map(row=>row.field),preferenceFields);
 await expect(surface.locator('.a-appearance-current,.a-appearance-local-indicator')).toHaveCount(0);
 await expect(surface.getByRole('button',{name:'Appearance in this browser',exact:true})).toHaveCount(0);
 await expect(surface.locator('[data-action="appearance.preferences.clear"],[data-action="appearance.preferences.publish"]')).toHaveCount(0);
 await expect(surface.getByRole('combobox',{name:/saved in|scope|setting$/i})).toHaveCount(0);
 const beforeEntry=await appearance(page);
 await openSettingsPage(page,'notifications');await openSettingsPage(page,'appearance');
 assert.equal((await appearance(page)).local.revision,beforeEntry.local.revision);assert.equal((await appearance(page)).shared.revision,beforeEntry.shared.revision);
 await sourceControl('This browser').focus();await page.keyboard.press('Enter');
 await expect.poll(async()=>(await appearance(page)).activeScope).toBe('client');
 await expect(sourceControl('This browser')).toHaveAttribute('aria-pressed','true');
 assert.deepEqual(await formShape(),globalShape,'Both sources have the identical controls, order, labels and disclosure layout');
 await expect(experience).toHaveAttribute('data-settings-page','appearance');
 const sharedBefore=(await appearance(page)).shared;
 await showRow(row('scheme'));await row('scheme').getByRole('radio',{name:'Dark',exact:true}).check();
 await expect.poll(async()=>(await appearance(page)).local.values.scheme).toBe('dark');
 await showRow(row('density'));await row('density').getByRole('combobox',{name:'Density',exact:true}).selectOption('comfortable');
 await expect.poll(async()=>(await appearance(page)).local.values.density).toBe('comfortable');
 assert.deepEqual((await appearance(page)).shared,sharedBefore,'Browser UI changes do not publish Global');
 await expect.poll(async()=>(await appearance(peer)).effective.scheme).toBe('dark');
 assert.equal((await appearance(isolated)).effective.density,'compact');

 // Hold before execution, not after an unknown mutation. Bound the observation
 // and always release the request on assertion failure so cleanup cannot hang.
 async function delayedUndo(field,{moveFocus=false,navigate=false}={}){
  let release;
  const held=new Promise(resolve=>{release=resolve});
  let continuation;
  const handler=async route=>{
   const request=route.request(),body=request.method()==='POST'?request.postDataJSON():null;
   if(body?.action!=='appearance.preferences.undo')return route.continue();
   continuation=(async()=>{await held;await route.continue()})();
   return continuation;
  };
  await page.route('**/api/actions',handler);
  try{
   const undo=row(field).getByRole('button',{name:/^Undo .+ change$/});
   await expect(undo).toBeVisible();await undo.focus();await page.keyboard.press('Enter');
   await expect(undo).toHaveAttribute('aria-disabled','true');await expect(undo).toBeFocused();
   await expect(row('scheme').getByRole('radio',{name:'Light',exact:true})).toBeEnabled();
   if(navigate)await openSettingsPage(page,'notifications');
   const successor=navigate?experience.locator('.a-settings-page-content:not([hidden])').getByRole('heading').first():row('scheme').getByRole('radio',{name:'Light',exact:true});
   if(moveFocus&&!navigate)await successor.focus();
   release();
   await expect.poll(async()=>(await appearance(page)).local.values.density).toBe('compact');
   if(navigate){await expect(experience).toHaveAttribute('data-settings-page','notifications');await openSettingsPage(page,'appearance')}
   else{
    await expect(row(field).getByRole('status')).toContainText('Preference change undone.');
    if(moveFocus)await expect(successor).toBeFocused();
    else await expect(row(field).getByRole('status')).toBeFocused();
   }
  }finally{
   release();
   // A removed handler is automatically continued by Playwright. Let our held
   // continuation finish first so cleanup cannot handle the same route twice.
   if(continuation)await continuation;
   await page.unroute('**/api/actions',handler);
  }
 }
 await delayedUndo('density');
 await row('density').getByRole('combobox',{name:'Density',exact:true}).selectOption('comfortable');
 await expect.poll(async()=>(await appearance(page)).local.values.density).toBe('comfortable');
 await delayedUndo('density',{moveFocus:true});
 await row('density').getByRole('combobox',{name:'Density',exact:true}).selectOption('comfortable');
 await expect.poll(async()=>(await appearance(page)).local.values.density).toBe('comfortable');
 await delayedUndo('density',{navigate:true});
 await selectUI('shared','Global');assert.equal((await appearance(page)).effective.appearanceId,'builtin:aurora');
 const globalBeforeSourceUndo=(await appearance(page)).shared;
 await surface.locator('.a-appearance-profile-feedback').getByRole('button',{name:'Undo',exact:true}).click();
 await expect.poll(async()=>(await appearance(page)).activeScope).toBe('client');
 assert.equal((await appearance(page)).effective.appearanceId,'builtin:atelier');
 assert.deepEqual((await appearance(page)).shared,globalBeforeSourceUndo,'Source Undo never publishes the browser profile');
 await selectUI('shared','Global');
 await selectUI('client','This browser');assert.equal((await appearance(page)).effective.appearanceId,'builtin:atelier');
 await page.reload();await openSettingsPage(page,'appearance');
 await expect.poll(async()=>(await appearance(page)).activeScope).toBe('client');
 await expect(row('scheme').getByRole('radio',{name:'Dark',exact:true})).toBeChecked();
 check('One keyboard-accessible two-source selector; identical 14-row form; entry writes nothing; direct profile-local autosave, reload and conditional Undo; pending focus anchor, moved focus and inactive-page completion.');

 const rejectSelect=async route=>{
  const body=route.request().method()==='POST'?route.request().postDataJSON():null;
  if(body?.action==='appearance.preferences.select')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Fixture rejected source switch before execution.'})});
  return route.continue();
 };
 await page.route('**/api/actions',rejectSelect);
 try{
  await sourceControl('Global').click();
  await expect(surface.getByRole('alert').filter({hasText:'Fixture rejected source switch before execution.'})).toBeVisible();
  assert.equal((await appearance(page)).activeScope,'client');assert.equal((await appearance(page)).effective.appearanceId,'builtin:atelier');
 }finally{await page.unroute('**/api/actions',rejectSelect)}
 check('Definitively rejected source selection retains the last confirmed source and values without a Global write.');

 await updateLocal(page,{executionMetrics:[],executionVisibility:{step:false,turn:true,conversation:true}});
 const localBeforeGlobal=(await appearance(page)).local.values;
 await updateGlobal(isolated,{executionMetrics:['calls'],executionVisibility:{step:true,turn:false,conversation:false}});
 await expect.poll(async()=>(await appearance(page)).shared.values.executionVisibility.turn).toBe(false);
 assert.deepEqual((await appearance(page)).local.values,localBeforeGlobal);
 await showRow(row('executionMetrics'));await expect(row('executionMetrics').locator('input[type=checkbox]:checked')).toHaveCount(0);
 await row('executionMetrics').getByRole('checkbox',{name:'Calls',exact:true}).check();
 await expect.poll(async()=>(await appearance(page)).local.values.executionMetrics).toEqual(['calls']);
 await row('executionMetrics').getByRole('checkbox',{name:'Calls',exact:true}).uncheck();
 await expect.poll(async()=>(await appearance(page)).local.values.executionMetrics).toEqual([]);
 await showRow(row('executionVisibility'));
 const rejectVisibility=async route=>{
  const request=route.request(),body=request.method()==='POST'?request.postDataJSON():null;
  if(body?.action==='appearance.preferences.update'&&body.args?.patch?.executionVisibility)
   return route.fulfill({status:200,json:{accepted:false,error:'Fixture rejected visibility before execution.'}});
  return route.continue();
 };
 await page.route('**/api/actions',rejectVisibility);
 try{
  await row('executionVisibility').getByRole('checkbox',{name:'Each step',exact:true}).check();
  await expect(row('executionVisibility').getByRole('status')).toContainText('Fixture rejected visibility before execution.');
  assert.deepEqual((await appearance(page)).local.values.executionVisibility,{step:false,turn:true,conversation:true});
 }finally{await page.unroute('**/api/actions',rejectVisibility)}
 await row('executionVisibility').getByRole('button',{name:'Retry',exact:true}).click();
 await expect.poll(async()=>(await appearance(page)).local.values.executionVisibility).toEqual({step:true,turn:true,conversation:true});
 await expect(row('executionVisibility').getByRole('checkbox',{name:'After each turn',exact:true})).toBeChecked();
 check('Empty metrics and nested false choices remain independent; explicit retry of a definitively rejected nested edit preserves untouched local choices.');

 for(const width of [1440,760,390]){
  await page.setViewportSize({width,height:1050});await openSettingsPage(page,'appearance');
  for(const [scope,label] of [['shared','Global'],['client','This browser']]){
   await selectUI(scope,label);assert.deepEqual(await formShape(),globalShape);
   await surface.evaluate(el=>{for(let parent=el;parent;parent=parent.parentElement)if(parent.scrollHeight>parent.clientHeight)parent.scrollTop=0});
   await expect(surface.getByRole('radio',{name:'Amplifier',exact:true})).toBeAttached();
   const geometry=await surface.evaluate(el=>{
    const gallery=el.querySelector('.a-appearance-gallery'),rect=el.getBoundingClientRect(),g=gallery.getBoundingClientRect();
    const selected=gallery.querySelectorAll('input[type=radio]:checked');
    return {viewport:innerWidth,surfaceWidth:rect.width,galleryWidth:g.width,columns:getComputedStyle(gallery).gridTemplateColumns.split(' ').length,selected:selected.length,
     selectedCards:gallery.querySelectorAll('[data-appearance-card][data-selected=true]').length,
     selectedOutline:!!gallery.querySelector('[data-appearance-card][data-selected=true]')&&
      getComputedStyle(gallery.querySelector('[data-appearance-card][data-selected=true]')).outlineStyle!=='none',
     visibleRadios:Array.from(gallery.querySelectorAll('input[type=radio]')).filter(input=>{const s=getComputedStyle(input);return s.opacity!=='0'&&s.clipPath==='none'&&input.getBoundingClientRect().width>2}).length,
     captions:Array.from(gallery.querySelectorAll('.a-appearance-card-caption')).map(node=>({name:node.querySelector('strong')?.textContent.trim(),overflow:node.scrollWidth>node.clientWidth+1})),
     overflow:document.documentElement.scrollWidth>innerWidth+1};
   });
   assert.equal(geometry.overflow,false);assert.equal(geometry.selected,1);assert.equal(geometry.selectedCards,1);assert.equal(geometry.visibleRadios,0);
   assert.equal(geometry.selectedOutline,true,'The card outline is the visible selection indicator');
   assert.ok(geometry.galleryWidth>=geometry.surfaceWidth-6,'Gallery occupies the available content width');
   assert.equal(geometry.columns,width===1440?4:width===760?3:2);
   assert.ok(geometry.captions.every(caption=>caption.name&&!caption.overflow),'Gallery names remain readable');
   await expect(surface.locator('.a-appearance-card-caption small,.a-appearance-current,.a-appearance-card svg')).toHaveCount(0);
   report.geometry.push({...geometry,scope});
   const path=`${out}/appearance-${scope}-${width}.png`;await page.screenshot({path});report.screenshots.push(path);
  }
 }
 await page.emulateMedia({reducedMotion:'reduce',forcedColors:'active'});
 await expect(sourceControl('Global')).toBeVisible();await expect(sourceControl('This browser')).toBeVisible();
 await selectUI('shared','Global');await selectUI('client','This browser');
 const selectedCard=surface.locator('[data-appearance-card][data-selected=true]');
 assert.notEqual(await selectedCard.evaluate(el=>getComputedStyle(el).outlineStyle),'none','Forced colors retain a selected outline');
 const globalBeforeLoss=(await appearance(page)).shared;
 await page.evaluate(namespace=>localStorage.removeItem(namespace),namespace);
 await expect.poll(async()=>(await appearance(peer)).activeScope).toBe('shared');
 await page.reload();await openSettingsPage(page,'appearance');
 await expect.poll(async()=>(await appearance(page)).local.reported).toBe(true);
 assert.equal((await appearance(page)).activeScope,'shared');assert.equal((await appearance(page)).local.values,null);
 assert.deepEqual((await appearance(page)).shared,globalBeforeLoss,'Storage loss never changes Global');
 await expect(sourceControl('Global')).toHaveAttribute('aria-pressed','true');
 assert.deepEqual(errors,[]);
 check('Browser storage loss returns same-profile tabs and reload to Global without changing its saved profile.');
 check('Six 1440/760/390 Global/browser screenshots; full-width name-only 4/3/2-column gallery, one selected outline and hidden native radio, no headline pill/check; source controls work with forced colors and reduced motion.');
 report.status='passed';
}catch(error){
 failure=error;report.status='failed';report.failure=String(error.stack||error);
 if(page)await page.screenshot({path:out+'/appearance-settings-failure.png',timeout:2000}).catch(()=>{});
}finally{
 try{await finishFixture({label:'Appearance lifecycle fixture',close:[['Appearance lifecycle browser',async()=>{await browser?.close()}]],stop:async()=>{await stop?.()},originalError:failure})}
 catch(error){failure??=error;report.cleanupFailure=String(error.stack||error)}
 report.status=failure?'failed':'passed';
 await writeFile(out+'/appearance-lifecycle.json',JSON.stringify(report,null,2)+'\n');
}
if(failure)throw failure;
console.log('Appearance lifecycle passed: complete independent Global/browser profiles, identical controls, autosave/Undo, private drafts/previews and responsive 1440/760/390.');