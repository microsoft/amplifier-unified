import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn,spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {updateAppearance,previewAppearance,cancelAppearancePreview,settledAppearance} from './settings-fixture.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/theme-surfaces';
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/main_refresh_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Theme surfaces fixture'});
let browser,failure;
try{
 await mkdir(out,{recursive:true});
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1440,height:960},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const action=async(name,args={})=>{const receipt=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);assert.equal(receipt.accepted,true,name);return receipt};
 const presentation=async patch=>{const after=await updateAppearance(page,patch);
  for(const [key,value] of Object.entries(patch))assert.deepEqual(after.effective[key],value);
  if(patch.scheme)await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',patch.scheme);
  if(patch.layout)await expect(page.locator('#amp-one')).toHaveAttribute('data-layout',patch.layout);
  if('decorations' in patch)await expect(page.locator('#amp-one')).toHaveAttribute('data-decorations',patch.decorations?'on':'off');
  return after;
 };
 const style=selector=>page.locator(selector).evaluate(el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();return {left:r.left,right:r.right,bottom:r.bottom,width:r.width,gap:s.columnGap,tl:s.borderTopLeftRadius,tr:s.borderTopRightRadius,bl:s.borderBottomLeftRadius,br:s.borderBottomRightRadius,art:s.backgroundImage}});
 const chat='.a-conversation',canvas='#workspace-canvas',nav='.a-nav-rail';
 const assertFits=async()=>assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal page overflow');
 await page.goto(url);await showFixtureConversation(page);await expect(page.getByText('Lead with what someone can do here.',{exact:true})).toBeVisible();
 await presentation({scheme:'light'});
 const draft='Keep this unfinished thought.';await page.getByRole('textbox',{name:'Message Amplifier'}).fill(draft);
 await page.getByRole('button',{name:'Open Website refresh brief in canvas',exact:true}).click();
 // Literal expectations come from the four included Reference deltas' authored
 // --a-panel-gap/radius values. Reference is a separate neutral recovery control.
 for(const [id,gap,radius] of [['aurora',10,22],['atelier',7,10],['graphite',6,7],['default',8,22],['reference',0,0]]){
  const identity=id==='reference'?'reference':'builtin:'+id;
  if(id!=='reference'){
   const theme=(await action('theme.read',{id:identity})).result;
   const before=await settledAppearance(page);
   await previewAppearance(page,{name:theme.name,css:theme.css});
   await expect.poll(async()=>(await style('.a-layout')).gap).toBe(gap+'px');
   const cancelled=await cancelAppearancePreview(page);
   assert.deepEqual(cancelled.effective,before.effective);assert.equal(cancelled.preview,null);
  }
  const selected=await presentation({appearanceId:identity});assert.equal(selected.preview,null);
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
 await presentation({appearanceId:aurora.id,scheme:'light',layout:'work'});
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
 const boundary=(await action('theme.save',{name:'Boundary',css:aurora.css+'\n#amp-one{--a-panel-gap:16px}#amp-one[data-theme-scheme=dark]{--a-panel-gap:8px}'})).result;
 await presentation({appearanceId:boundary.id});
 await expect.poll(async()=>(await style('.a-layout')).gap).toBe('16px');await expect(page.locator('.a-nav-slot')).toHaveAttribute('data-docked','false');assert.ok((await style(chat)).width>=360);assert.ok((await style(canvas)).right<=905);await assertFits();
 await presentation({scheme:'dark'});await expect.poll(async()=>(await style('.a-layout')).gap).toBe('8px');
 assert.equal(page.viewportSize().width,904);await expect(page.locator('.a-nav-slot')).toHaveAttribute('data-docked','true');assert.ok((await style(canvas)).right<=905);
 await page.setViewportSize({width:1440,height:960});await presentation({scheme:'light',appearanceId:aurora.id});
 await page.getByRole('separator',{name:'Resize canvas'}).focus();await page.keyboard.press('ArrowLeft');await assertFits();
 // Exercise user-facing save -> apply -> download -> import -> reload.
 await openSettingsPage(page,'appearance',{activate:(_label,control,options)=>control.click(options)});
 for(const selector of ['.a-appearance-more','.a-appearance-advanced']){
  const disclosure=page.locator(selector);if(!await disclosure.evaluate(node=>node.open))await disclosure.locator(':scope > summary').click();
 }
 const profiles=()=>page.evaluate(()=>{
  const a=window.amplifier.getState().appearance;
  return {scope:a.activeScope,shared:a.shared,local:{revision:a.local.revision,values:a.local.values,storageVersion:a.local.storageVersion},effective:a.effective};
 });
 const beforeSave=await profiles();
 await page.locator('#theme-name').fill('My panel theme');
 const custom=aurora.css+'\n#amp-one{--a-panel-gap:13px;--a-panel-radius:17px}';await page.locator('#theme-css').fill(custom);
 const savedResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.save');
 await page.getByRole('button',{name:'Save as copy',exact:true}).click();
 const saved=await(await savedResponse).json();assert.equal(saved.accepted,true);
 assert.equal(saved.result.format,'zip');assert.equal(saved.result.packageVersion,2);assert.equal(saved.result.base,'reference-v1');
 const savedId=saved.result.id;
 await expect(page.getByRole('button',{name:'Use My panel theme',exact:true})).toBeVisible();
 assert.deepEqual(await profiles(),beforeSave,'Saving the copy does not activate it');
 assert.equal((await action('theme.read',{id:savedId})).result.css,custom);
 const selectSaved=async()=>{
  const before=await settledAppearance(page),profile=before.activeScope==='client'?'local':'shared';
  await page.getByRole('button',{name:'Use My panel theme',exact:true}).click();
  await page.waitForFunction(({id,profile,revision})=>{
   const a=window.amplifier.getState().appearance;return a.effective.appearanceId===id&&a[profile].revision===revision+1;
  },{id:savedId,profile,revision:before[profile].revision});
  const after=await settledAppearance(page);assert.equal(after.effective.appearanceId,savedId);
  assert.equal(after.activeScope,before.activeScope);assert.equal(after.generation,before.generation);assert.equal(after.preview,null);
  await expect.poll(async()=>(await style('.a-layout')).gap).toBe('13px');
  await expect.poll(async()=>(await style(chat)).tr).toBe('17px');
  assert.equal((await style(canvas)).tl,'17px');
 };
 await selectSaved();
 const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Export ZIP for My panel theme',exact:true}).click();
 const stream=await(await downloaded).createReadStream();assert.ok(stream);
 const chunks=[];let size=0;
 for await(const chunk of stream){size+=chunk.length;assert.ok(size<=4_000_000);chunks.push(chunk)}
 const exported=Buffer.concat(chunks),exportedPath=join(out,'panels-export.zip');
 await writeFile(exportedPath,exported);
 assert.equal(exported.subarray(0,4).toString('hex'),'504b0304');
 assert.equal(createHash('sha256').update(exported).digest('hex'),saved.result.fingerprint);
 const checked=spawnSync(fixturePython(root+'.venv/bin/python'),['-c',`
import json,sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
 members=archive.infolist()
 assert len(members)==2 and len({m.filename for m in members})==2
 assert all(m.file_size<=2_000_000 for m in members) and sum(m.file_size for m in members)<=4_000_000
 assert archive.testzip() is None
 manifest=json.loads(archive.read('appearance.json'))
 assert manifest['type']=='appearance' and manifest['formatVersion']==2 and manifest['base']=='reference-v1'
 assert manifest['name']=='My panel theme'
 assert set(archive.namelist())=={'appearance.json',manifest['stylesheet']}
 assert archive.read(manifest['stylesheet']).decode('utf-8')==sys.stdin.read()
 print(json.dumps(manifest))
`,exportedPath],{input:custom,encoding:'utf8',timeout:10000,maxBuffer:10000});
 assert.equal(checked.status,0,checked.stderr);
 // Importing the same package reuses its identity and original manifest name.
 // Select another look first so nonactivation is a meaningful assertion.
 await presentation({appearanceId:aurora.id});const beforeImport=await profiles();
 const importedResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.import');
 await page.locator('#theme-import').setInputFiles({name:'Imported panels.zip',mimeType:'application/zip',buffer:exported});
 const imported=await(await importedResponse).json();assert.equal(imported.accepted,true);
 assert.equal(imported.result.id,savedId);assert.equal(imported.result.fingerprint,saved.result.fingerprint);
 await expect(page.getByText('My panel theme added to your library.',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Edit My panel theme',exact:true})).toHaveCount(1);
 assert.deepEqual(await profiles(),beforeImport);
 await selectSaved();
 const closed=page.waitForResponse(response=>{if(!response.url().endsWith('/api/actions'))return false;const body=response.request().postDataJSON();return body?.action==='view.update'&&body.args?.patch?.panel===null});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await closed;await expect(page.locator('.a-settings-overlay')).toBeHidden();
 await page.reload();assert.equal((await settledAppearance(page)).effective.appearanceId,savedId);
 assert.equal((await action('theme.read',{id:savedId})).result.css,custom);
 await expect.poll(async()=>(await style('.a-layout')).gap).toBe('13px');await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 await expect.poll(async()=>(await style(chat)).tr).toBe('17px');assert.equal((await style(canvas)).tl,'17px');
 const narrowGeometry=[];
 for(const width of [760,390,320]){
  await page.setViewportSize({width,height:900});await expect.poll(async()=>(await style('.a-layout')).gap).toBe('0px');await assertFits();
  if(await page.locator(canvas).isVisible())await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
  const mobileNavigation=width<=640;
  await expect(page.locator('.a-layout')).toHaveAttribute('data-navigation-narrow',String(mobileNavigation));
  await expect(page.locator('.a-nav-slot')).toHaveAttribute('data-docked',String(!mobileNavigation));
  await expect.poll(()=>page.locator('.a-nav-slot').evaluate(node=>node.hidden)).toBe(mobileNavigation);
  // At 760px the retained pinned navigation still docks: the chat's left edge
  // is an interior seam, not the full-screen outer edge used below 640px.
  const measured=await page.evaluate(()=>{
   const get=selector=>document.querySelector(selector);
   const rect=node=>node.getBoundingClientRect().toJSON();
   const layout=get('.a-layout'),nav=get('.a-nav-slot'),chat=get('.a-conversation'),canvas=get('#workspace-canvas');
   const css=getComputedStyle(chat);
   return {width:innerWidth,height:innerHeight,navigationNarrow:layout.dataset.navigationNarrow,
    docked:nav.dataset.docked,hidden:nav.hidden,layout:rect(layout),nav:rect(nav),chat:rect(chat),
    canvas:rect(canvas),canvasOpen:layout.dataset.canvasOpen,canvasOverlay:layout.dataset.canvasOverlay,
    composer:rect(get('.a-composer')),gap:getComputedStyle(layout).columnGap,
    corners:{tl:css.borderTopLeftRadius,tr:css.borderTopRightRadius,bl:css.borderBottomLeftRadius,br:css.borderBottomRightRadius}};
  });
  narrowGeometry.push(measured);
  await writeFile(join(out,'narrow-surface-topology.json'),JSON.stringify(narrowGeometry,null,2));
  await page.screenshot({path:join(out,`narrow-topology-${width}.png`)});
  assert.equal(measured.canvasOpen,'false');assert.equal(measured.gap,'0px');
  assert.ok(Math.abs(measured.layout.left)<1&&Math.abs(measured.chat.right-measured.layout.right)<1);
  assert.ok(Math.abs(measured.chat.bottom-measured.layout.bottom)<1);
  assert.ok(measured.composer.left>=measured.chat.left&&measured.composer.right<=measured.chat.right
   &&measured.composer.bottom<=measured.chat.bottom,'Composer stays in its conversation');
  if(width===760){
   assert.ok(measured.nav.width>0&&Math.abs(measured.nav.left-measured.layout.left)<1);
   assert.ok(Math.abs(measured.chat.left-measured.nav.right)<1&&measured.chat.width>=360);
   assert.deepEqual(measured.corners,{tl:'17px',tr:'0px',bl:'0px',br:'0px'},'Only the proven interior seam retains the custom radius');
  }else{
   assert.ok(Math.abs(measured.chat.left-measured.layout.left)<1&&Math.abs(measured.chat.width-width)<1);
   assert.deepEqual(measured.corners,{tl:'0px',tr:'0px',bl:'0px',br:'0px'},'Full-screen outer edges stay square');
  }
  if(width<520){await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();await expect(page.getByRole('dialog',{name:'Model for this chat',exact:true})).toBeVisible();await page.getByRole('button',{name:'Close chat options',exact:true}).click();}
  await page.screenshot({path:out+'/narrow-'+width+'.png'});await page.getByRole('button',{name:'Open canvas',exact:true}).click();
  await expect(page.locator(canvas)).toBeVisible();assert.equal((await style(canvas)).tl,'0px');assert.equal((await style(canvas)).tr,'0px');await assertFits();
 }
 assert.deepEqual(errors,[]);console.log('Theme surfaces passed: four themes in light/dark, interior corners, gaps, resize boundary, canvas sides/focus, decoration toggle, save/export/import/reload, drafts and narrow controls.');
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Theme surfaces fixture',close:[['browser',()=>browser?.close()]],stop:stopFixture,originalError:failure})}
