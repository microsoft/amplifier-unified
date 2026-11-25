import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {startFixture} from './fixture-readiness.mjs';
import {readAppearance,settledAppearance} from './settings-fixture.mjs';
// Packaged application, isolated storage, no external model or feedback calls.
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const python=process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url));
const {base:url,stop}=await startFixture({command:python,
 args:[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url))],
 env:process.env,tag:'empty-host-ui',name:'Appearance library',timeoutMs:20000});
let browser,failure;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-appearance-library';
const focused=process.argv.includes('--catalog-thumbnails-only');
// A real strict format-2 Reference delta ZIP, constructed independently with
// stdlib. Raw CSS/full-skin import is deliberately not a compatibility path.
const packageBytes=(name,css)=>execFileSync(python,['-c',String.raw`
import io,json,sys,zipfile
name,css=sys.argv[1:]
b=io.BytesIO()
with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:
 z.writestr('appearance.json',json.dumps({'type':'appearance','formatVersion':2,'base':'reference-v1','name':name,'stylesheet':'delta.css','assets':[],'licenses':[]}))
 z.writestr('delta.css',css)
sys.stdout.buffer.write(b.getvalue())
`,name,css]);
try{
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 if(focused)await page.clock.install();
 page.on('pageerror',e=>errors.push(e.message));
 const action=async(name,args={})=>{
  const receipt=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  assert.equal(receipt.accepted,true,name+' accepted');return receipt;
 };
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const gallery=page.getByRole('radiogroup',{name:'Available appearances',exact:true});
 const radio=name=>gallery.getByRole('radio',{name,exact:true});
 const card=id=>page.locator(`[data-appearance-card="${id}"]`);
 const select=async(name,id)=>{
  // The native radio is visually hidden inside its full-card label.
  await card(id).click();await expect(radio(name)).toBeChecked();
  await expect.poll(async()=>(await readAppearance(page)).effective.appearanceId).toBe(id);
  await settledAppearance(page);
 };
 const scheme=async mode=>{
  await page.getByRole('radiogroup',{name:'Color mode',exact:true}).getByRole('radio',{name:mode==='light'?'Light':'Dark',exact:true}).check();
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',mode);await settledAppearance(page);
 };
 const advanced=async()=>{const details=page.locator('.a-appearance-more');if(!await details.evaluate(el=>el.open))await details.locator(':scope > summary').click()};
 const row=name=>page.locator('.a-appearance-saved-row').filter({has:page.getByText(name,{exact:true})});
 const edit=async name=>{await row(name).getByRole('button',{name:'Edit '+name,exact:true}).click();await expect(page.locator('#theme-css')).toBeVisible()};
 const preview=async name=>{
  await edit(name);await page.getByRole('button',{name:'Preview draft',exact:true}).click();
  await expect(page.locator('.a-appearance-preview')).toContainText('Previewing '+name);await settledAppearance(page);
 };
 const cancel=async()=>{await page.locator('.a-appearance-preview').getByRole('button',{name:'Cancel preview',exact:true}).click();await expect(page.locator('.a-appearance-preview')).toHaveCount(0);await settledAppearance(page)};
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await action('session.create');await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep my unfinished ideas.');
 await openSettingsPage(page,'appearance');await settledAppearance(page);
 await expect(radio('Aurora')).toBeVisible();await expect(page.locator('#theme-css')).toBeHidden();
 if(focused){
  // Retained metadata must remain visible when a later refresh cannot finish.
  const original=await readAppearance(page),selected=(await state()).selectedSessionId;
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
  await expect(page.locator('.a-settings-experience')).toBeHidden();
  let failedRefreshes=0;
  await page.route('**/api/actions',async route=>{
   const request=route.request(),body=request.method()==='POST'?request.postDataJSON():null;
   if(body?.action==='theme.list'){failedRefreshes++;await route.abort('failed')}else await route.continue();
  });
  // Expire the documented 30s catalog freshness window, not a blind sleep.
  await page.clock.fastForward(31000);
  await openSettingsPage(page,'appearance');await expect(radio('Aurora')).toBeVisible();
  await expect.poll(()=>failedRefreshes).toBeGreaterThan(0);
  await expect(page.getByText('Loading appearances…',{exact:true})).toHaveCount(0);
  assert.deepEqual((await readAppearance(page)).theme,original.theme,'Reading a thumbnail never applies an appearance');
  assert.deepEqual((await readAppearance(page)).effective,original.effective);
  await page.unrouteAll({behavior:'wait'});
  const measurements=[];
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:1050});
   for(const mode of ['light','dark']){
    await scheme(mode);
    const rain=card('builtin:digital-rain');await rain.scrollIntoViewIfNeeded();
    await expect(rain.locator('[data-thumbnail-state=ready]')).toHaveAttribute('data-thumbnail-mode',mode);
    const iframe=rain.locator('iframe');await expect(iframe).toHaveAttribute('sandbox','');await expect(iframe).toHaveAttribute('tabindex','-1');
    const frame=await (await iframe.elementHandle()).contentFrame();assert.ok(frame);
    await expect(frame.locator('#amp-one')).toHaveAttribute('data-theme-scheme',mode);
    const specimen=await frame.locator('#amp-one').evaluate(el=>({scheme:el.dataset.themeScheme,version:el.dataset.thumbnailScene,
     ink:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,
     art:getComputedStyle(el.querySelector('[data-part=conversation]')).backgroundImage,
     composer:!!el.querySelector('[data-part=composer-surface]'),paragraphs:el.querySelectorAll('.a-markdown p').length,
     code:el.querySelector('pre code')?.textContent,csp:document.querySelector('meta[http-equiv="Content-Security-Policy"]').content}));
    assert.match(specimen.art,/data:image\/png;base64,/,'Actual embedded Rain artwork, not a palette fallback');
    assert.ok(specimen.composer&&specimen.paragraphs>=2);assert.match(specimen.code,/Make it real/);assert.match(specimen.csp,/default-src 'none'/);
    const entry=(await action('theme.read',{id:'builtin:digital-rain'})).result;
    await expect(rain.locator('[data-appearance-fingerprint]')).toHaveAttribute('data-appearance-fingerprint',entry.fingerprint);
    assert.equal((await readAppearance(page)).effective.appearanceId,original.effective.appearanceId,'Thumbnail mode does not select a different appearance');
    measurements.push({width,mode,...specimen});await page.screenshot({path:`${out}/catalog-digital-rain-${mode}-${width}.png`});
   }
  }
  // A native single-selection gesture applies immediately without a preview ritual.
  await select('Digital Rain','builtin:digital-rain');
  await expect(page.getByRole('button',{name:'Cancel preview',exact:true})).toHaveCount(0);
  assert.equal((await state()).selectedSessionId,selected);
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();await expect(page.locator('.a-settings-experience')).toBeHidden();
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep my unfinished ideas.');
  await writeFile(`${out}/catalog-thumbnail-measurements.json`,JSON.stringify(measurements,null,2));
  console.log('Appearance catalog/thumbnails passed: warm radios, Reference+delta specimen, both modes/widths, isolated frames, direct selection and preserved draft/selection.');
 }else{
  const selected=(await state()).selectedSessionId;
  await page.screenshot({path:out+'/appearance-gallery.png'});
  for(const [name,id] of [['Aurora','aurora'],['Atelier','atelier'],['Graphite','graphite'],['Digital Rain','digital-rain']]){
   await select(name,'builtin:'+id);await expect(page.getByRole('button',{name:'Cancel preview',exact:true})).toHaveCount(0);
   for(const mode of ['light','dark']){await scheme(mode);await page.screenshot({path:out+'/'+id+'-'+mode+'.png'})}
  }
  await select('Aurora','builtin:aurora');
  const background=()=>page.locator('#amp-one').evaluate(el=>getComputedStyle(el).backgroundImage);
  await expect.poll(background).not.toBe('none');await advanced();
  const decoration=page.getByRole('checkbox',{name:'Show background decorations',exact:true});
  await decoration.uncheck();await expect.poll(background).toBe('none');await decoration.check();await expect.poll(background).not.toBe('none');
  for(const width of [760,390,320]){
   await page.setViewportSize({width,height:900});await radio('Aurora').scrollIntoViewIfNeeded();await expect(radio('Aurora')).toBeVisible();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.locator('.a-settings-content').evaluate(el=>el.scrollTop=0);await page.screenshot({path:out+'/appearance-'+width+'.png'});
  }
  await page.setViewportSize({width:1440,height:1050});await advanced();
  const oceanCss='#amp-one{--a-accent:#137c8b;background-image:none}';
  await page.locator('#theme-import').setInputFiles({name:'Ocean.zip',mimeType:'application/zip',buffer:packageBytes('Ocean',oceanCss)});
  await expect(row('Ocean')).toBeVisible();assert.equal((await readAppearance(page)).effective.appearanceId,'builtin:aurora','Import does not select');
  await page.reload();await openSettingsPage(page,'appearance');await advanced();await expect(row('Ocean')).toBeVisible();
  assert.equal((await readAppearance(page)).effective.appearanceId,'builtin:aurora');
  const ocean=(await action('theme.list')).result.items.find(item=>item.name==='Ocean');
  const oceanRead=(await action('theme.read',{id:ocean.id})).result;
  assert.equal(oceanRead.css,oceanCss);assert.equal(oceanRead.packageVersion,2);assert.equal(oceanRead.base,'reference-v1');
  const paint=()=>page.locator('#amp-one').evaluate(el=>{
    const css=getComputedStyle(el);return {accent:css.getPropertyValue('--a-accent').trim(),background:css.backgroundImage};
  });
  const before=await readAppearance(page),beforePaint=await paint();
  await preview('Ocean');assert.deepEqual((await readAppearance(page)).effective,before.effective);
  await expect.poll(paint).toEqual({accent:'#137c8b',background:'none'});
  await cancel();assert.deepEqual((await readAppearance(page)).theme,before.theme);await expect.poll(paint).toEqual(beforePaint);
  const beforeReject={items:(await action('theme.list')).result.items,appearance:await readAppearance(page),
    editor:await page.locator('#theme-name,#theme-description,#theme-css').evaluateAll(nodes=>nodes.map(node=>node.value))};
  const preserved=async()=>{
    assert.deepEqual((await action('theme.list')).result.items,beforeReject.items,'Rejected import preserves exact existing library');
    const current=await readAppearance(page);
    assert.deepEqual(current.shared,beforeReject.appearance.shared);assert.deepEqual(current.local,beforeReject.appearance.local);
    assert.deepEqual(current.effective,beforeReject.appearance.effective);
    assert.deepEqual(await page.locator('#theme-name,#theme-description,#theme-css').evaluateAll(nodes=>nodes.map(node=>node.value)),beforeReject.editor);
  };
  // Remote CSS remains rejected inside the only supported package format.
  await page.locator('#theme-import').setInputFiles({name:'Bad.zip',mimeType:'application/zip',buffer:packageBytes('Bad','@import "https://example.com/style.css";')});
  await expect(page.getByRole('alert').filter({hasText:/external|remote|import|not allowed/i})).toBeVisible();
  await expect(row('Bad')).toHaveCount(0);assert.equal((await readAppearance(page)).effective.appearanceId,'builtin:aurora');
  await preserved();
  await page.locator('#theme-import').setInputFiles({name:'Raw.css',mimeType:'text/css',buffer:Buffer.from(oceanCss)});
  await expect(page.getByRole('alert').filter({hasText:/ZIP|package/i})).toBeVisible();
  await expect(row('Raw')).toHaveCount(0);
  await preserved();
  await page.locator('#theme-name').fill('My Aurora');await page.locator('#theme-css').fill(before.theme.css);
  await page.getByRole('button',{name:'Save as copy',exact:true}).click();await expect(row('My Aurora')).toBeVisible();
  await preview('Ocean');await row('Ocean').getByRole('button',{name:'Use Ocean',exact:true}).click();
  await expect.poll(async()=>(await readAppearance(page)).theme.name).toBe('Ocean');
  assert.equal((await readAppearance(page)).theme.css,oceanRead.css);
  assert.equal((await readAppearance(page)).theme.fingerprint,oceanRead.fingerprint);
  await cancel();await preview('My Aurora');
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
  await expect.poll(async()=>(await readAppearance(page)).preview).toBe(null);
  assert.equal((await readAppearance(page)).theme.name,'Ocean');assert.equal((await state()).selectedSessionId,selected);
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep my unfinished ideas.');
  console.log('Appearance library passed: dual-mode direct selection, strict ZIP import, document preview/cancel, background toggle, persistent library, rejected remote CSS, named save, responsive gallery and preserved draft/selection.');
 }
 assert.deepEqual(errors,[]);
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Appearance library fixture',close:[['Appearance library browser',()=>browser?.close()]],stop,originalError:failure})}
