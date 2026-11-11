import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/theme-surfaces';
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/main_refresh_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 await mkdir(out,{recursive:true});
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1440,height:960},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const select=async appearanceId=>{
  const current=(await action('appearance.preferences.read')).result;
  const scope=current.activeScope,profile=scope==='client'?current.local:current.shared;
  const receipt=await action('appearance.preferences.update',{scope,generation:current.generation,expectedRevision:profile.revision,
   ...(scope==='client'?{expectedStorageVersion:profile.storageVersion}:{}),patch:{appearanceId}});
  assert.equal(receipt.accepted,true,JSON.stringify(receipt));
  await page.waitForFunction(id=>{const a=window.amplifier.getState().appearance;return a.effective.appearanceId===id&&a.theme.id===id},appearanceId);
 };
 const useDelta=async(name,css)=>{
  const saved=await action('theme.save',{name,css,description:'Synthetic surface geometry regression'});
  assert.equal(saved.accepted,true,JSON.stringify(saved));await select(saved.result.id);
 };
 const presentation=async patch=>{const change=await page.evaluate(async patch=>{const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}});if(!prepared.accepted)throw Error(JSON.stringify(prepared));const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id});if(!applied.accepted)throw Error(JSON.stringify(applied));return {revision:state.revision+1,commandId:applied.result.commandId}},patch);
  await page.waitForFunction(({patch,change})=>{const a=window.amplifier.getState().appearance;return window.amplifier.getShellState()?.revision===change.revision&&Object.entries(patch).every(([key,value])=>a.effective[key]===value&&a.renderedPreferences[key]===value)&&(!change.commandId||a.command?.id===change.commandId&&a.command.status==='stored')},{patch,change});
  if(patch.scheme)await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',patch.scheme);
  if(patch.layout)await expect(page.locator('#amp-one')).toHaveAttribute('data-layout',patch.layout);
  if('decorations' in patch)await expect(page.locator('#amp-one')).toHaveAttribute('data-decorations',patch.decorations?'on':'off');
 };
 const style=selector=>page.locator(selector).evaluate(el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();return {left:r.left,right:r.right,bottom:r.bottom,width:r.width,gap:s.columnGap,tl:s.borderTopLeftRadius,tr:s.borderTopRightRadius,bl:s.borderBottomLeftRadius,br:s.borderBottomRightRadius,art:s.backgroundImage}});
 const chat='.a-conversation',canvas='#workspace-canvas',nav='.a-nav-rail';
 const assertFits=async()=>assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal page overflow');
 await page.goto(url);await showFixtureConversation(page);await expect(page.getByText('Lead with what someone can do here.',{exact:true})).toBeVisible();
 const draft='Keep this unfinished thought.';await page.getByRole('textbox',{name:'Message Amplifier'}).fill(draft);
 await page.getByRole('button',{name:'Open Website refresh brief in canvas',exact:true}).click();
 for(const [id,gap,radius] of [['aurora',10,22],['atelier',7,10],['graphite',6,7],['default',8,22]]){
  const theme=(await action('theme.read',{id:'builtin:'+id})).result;
  await action('appearance.preview',{name:theme.name,css:theme.css});
  await expect.poll(async()=>(await style('.a-layout')).gap).toBe(gap+'px');
  await action('appearance.preview.cancel',{});await select(theme.id);
  for(const scheme of ['light','dark']){
   await presentation({scheme,layout:'balanced',decorations:true});
   await expect.poll(async()=>(await style(chat)).tr).toBe(radius+'px');
   const c=await style(chat),v=await style(canvas),n=await style(nav);
   assert.equal(n.tl,'0px');assert.equal(n.tr,radius+'px');assert.equal(c.tl,radius+'px');assert.equal(v.tl,radius+'px');assert.equal(v.tr,'0px');
   assert.equal(c.bl,'0px');assert.equal(c.br,'0px');assert.ok(Math.abs(v.left-c.right-gap)<1);assert.ok(Math.abs(c.left-n.right-gap)<1);assert.ok(c.width>=360);assert.ok(v.right<=1441);assert.ok(Math.abs(c.bottom-960)<1,'conversation remains anchored to bottom');await assertFits();
   await page.mouse.move(600,20);await page.screenshot({path:out+'/'+id+'-'+scheme+'.png'});
  }
 }
 const aurora=(await action('theme.read',{id:'builtin:aurora'})).result;
 await select(aurora.id);
 await presentation({scheme:'light',layout:'work'});
 await expect.poll(async()=>(await style(canvas)).tl).toBe('22px');assert.equal((await style(canvas)).tr,'22px');assert.equal((await style(chat)).tr,'0px');
 await page.getByRole('button',{name:'Focus canvas',exact:true}).click();await expect(page.locator(canvas)).toHaveAttribute('data-focused','true');
 assert.equal((await style(canvas)).tl,'0px');assert.equal((await style(canvas)).tr,'0px');
 await page.keyboard.press('Escape');
 await presentation({decorations:false});
 for(const selector of ['#amp-one',chat,nav,'.a-canvas-chrome'])assert.equal((await style(selector)).art,'none');
 assert.equal((await style('.a-layout')).gap,'10px');
 await presentation({decorations:true,layout:'balanced'});assert.notEqual((await style(nav)).art,'none');
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();assert.equal((await style(chat)).tr,'0px');
 await action('view.update',{patch:{navPinned:false,navExpanded:false}});await expect(page.locator(nav)).toBeHidden();assert.equal((await style(chat)).tl,'0px');
 // At this boundary, a 16px gap must remeasure fitPanels without a viewport resize.
 await page.setViewportSize({width:904,height:960});await action('view.update',{patch:{navPinned:true,navWidth:216}});
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();
 await useDelta('Boundary',aurora.css+'\n#amp-one{--a-panel-gap:16px}#amp-one[data-theme-scheme=dark]{--a-panel-gap:8px}');
 await expect.poll(async()=>(await style('.a-layout')).gap).toBe('16px');await expect(page.locator('.a-nav-slot')).toHaveAttribute('data-docked','false');assert.ok((await style(chat)).width>=360);assert.ok((await style(canvas)).right<=905);await assertFits();
 await presentation({scheme:'dark'});await expect(page.locator('.a-nav-slot')).toHaveAttribute('data-docked','true');assert.ok((await style(canvas)).right<=905);
 await page.setViewportSize({width:1440,height:960});await presentation({scheme:'light'});await select(aurora.id);
 await page.getByRole('separator',{name:'Resize canvas'}).focus();await page.keyboard.press('ArrowLeft');await assertFits();
 // Exercise user-facing save -> apply -> download -> import -> reload.
 await openSettingsPage(page,'custom-appearance');
 const customPage=page.locator('.a-settings-experience[data-settings-page="custom-appearance"] .a-settings-page-content:not([hidden])');
 await expect(customPage.locator('.a-appearance-settings')).toHaveAttribute('data-appearance-advanced','true');
 const editor=customPage.locator('.a-appearance-advanced');
 await editor.locator('summary').click();await editor.locator('#theme-name').fill('My panel theme');
 const custom=aurora.css+'\n#amp-one{--a-panel-gap:13px;--a-panel-radius:17px}';await editor.locator('#theme-css').fill(custom);
 await editor.getByRole('button',{name:'Preview draft',exact:true}).click();await expect.poll(async()=>(await style('.a-layout')).gap).toBe('13px');
 await editor.getByRole('button',{name:'Cancel preview',exact:true}).click();await expect.poll(async()=>(await style('.a-layout')).gap).toBe('10px');
 const savedResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().method()==='POST'&&response.request().postDataJSON()?.action==='theme.save');
 await editor.getByRole('button',{name:'Save as copy',exact:true}).click();
 const saveReceipt=await (await savedResponse).json();assert.equal(saveReceipt.accepted,true);
 const saved=saveReceipt.result;assert.ok(saved.id);assert.equal(saved.name,'My panel theme');
 await expect(customPage.locator('.a-appearance-notice')).toContainText('Saved to your appearance library.');
 await expect(editor.locator('p').filter({hasText:'Editing:'})).toContainText(saved.name);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().appearance.effective.appearanceId),aurora.id,'Saving does not select');
 // Editing proves draft adoption, not library reachability. Keep this UI
 // assertion: advanced-page CSS must not hide saved entries with preferences.
 const savedRow=customPage.locator('.a-appearance-saved > .a-appearance-saved-row').filter({hasText:saved.name});
 await expect(savedRow).toHaveCount(1);
 await expect(savedRow.getByRole('button',{name:'Use My panel theme',exact:true}),
  'Saved appearance Use controls must remain visible in Custom appearance.').toBeVisible();
 await savedRow.getByRole('button',{name:'Use My panel theme',exact:true}).click();await expect.poll(async()=>(await style('.a-layout')).gap).toBe('13px');
 const downloaded=page.waitForEvent('download');await editor.getByRole('button',{name:'Export ZIP',exact:true}).click();
 const download=await downloaded;assert.match(download.suggestedFilename(),/\.zip$/);
 const exported=await readFile(await download.path());assert.equal(exported.readUInt32LE(0),0x04034b50,'Real ZIP bytes, not standalone CSS');
 const exportedReceipt=(await action('theme.export',{id:saved.id})).result;
 assert.equal(exportedReceipt.mimeType,'application/zip');assert.deepEqual(exported,Buffer.from(exportedReceipt.base64,'base64'));
 await expect(customPage.locator('#theme-import')).toHaveAttribute('accept','.zip,application/zip');
 await select(aurora.id);
 const beforeImport=await page.evaluate(()=>window.amplifier.getState().appearance.effective.appearanceId);
 const importedResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().method()==='POST'&&response.request().postDataJSON()?.action==='theme.import');
 await customPage.locator('#theme-import').setInputFiles({name:'Imported panels.zip',mimeType:'application/zip',buffer:exported});
 const importReceipt=await (await importedResponse).json();assert.equal(importReceipt.accepted,true);
 const imported=importReceipt.result;assert.ok(imported.id);assert.equal(imported.id,saved.id);
 await expect(customPage.locator('.a-appearance-notice')).toContainText('added to your library.');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().appearance.effective.appearanceId),beforeImport,'Import does not select');
 const importedEntry=(await action('theme.read',{id:imported.id})).result;assert.equal(importedEntry.css,custom);
 // ZIP manifest identity supplies the name; the uploaded filename does not rename it.
 const savedEntries=(await action('theme.list')).result.items.filter(row=>row.source==='saved');
 const importedIndex=savedEntries.findIndex(row=>row.id===imported.id);assert.ok(importedIndex>=0);
 const savedRows=customPage.locator('.a-appearance-saved > .a-appearance-saved-row');await expect(savedRows).toHaveCount(savedEntries.length);
 const importedRow=savedRows.nth(importedIndex);await expect(importedRow.getByRole('button',{name:'Use '+imported.name,exact:true})).toBeVisible();
 const applied=page.waitForResponse(response=>{if(!response.url().endsWith('/api/actions')||response.request().method()!=='POST')return false;const body=response.request().postDataJSON();return body?.action==='appearance.preferences.update'&&body.args?.patch?.appearanceId===imported.id});
 await importedRow.getByRole('button',{name:'Use '+imported.name,exact:true}).click();await applied;
 await page.waitForFunction(id=>window.amplifier.getState().appearance.theme.id===id,imported.id);
 const closed=page.waitForResponse(response=>{if(!response.url().endsWith('/api/actions')||response.request().method()!=='POST')return false;const body=response.request().postDataJSON();return body?.action==='view.update'&&body.args?.patch?.panel===null});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await closed;await expect(page.locator('.a-settings-overlay')).toBeHidden();
 await page.reload();await expect.poll(async()=>(await style('.a-layout')).gap).toBe('13px');await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().appearance.theme.id),imported.id);
 for(const width of [760,390,320]){
  await page.setViewportSize({width,height:900});await expect.poll(async()=>(await style('.a-layout')).gap).toBe('0px');await assertFits();
  if(await page.locator(canvas).isVisible())await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
  assert.equal((await style(chat)).tl,'0px');assert.equal((await style(chat)).tr,'0px');
  if(width<520){await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();await expect(page.getByRole('dialog',{name:'Model for this chat',exact:true})).toBeVisible();await page.getByRole('button',{name:'Close chat options',exact:true}).click();}
  await page.screenshot({path:out+'/narrow-'+width+'.png'});await page.getByRole('button',{name:'Open canvas',exact:true}).click();
  await expect(page.locator(canvas)).toBeVisible();assert.equal((await style(canvas)).tl,'0px');assert.equal((await style(canvas)).tr,'0px');await assertFits();
 }
 assert.deepEqual(errors,[]);console.log('Theme surfaces passed: four themes in light/dark, interior corners, gaps, resize boundary, canvas sides/focus, decoration toggle, save/export/import/reload, drafts and narrow controls.');
}finally{await browser?.close();fixture.kill()}
