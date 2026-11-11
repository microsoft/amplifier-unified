import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {startFixture} from './fixture-readiness.mjs';
// Packaged application, isolated storage, no external model or feedback calls.
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const {base:url,stop}=await startFixture({
 command:process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 args:[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url))],
 env:process.env,tag:'empty-host-ui',name:'Appearance library',timeoutMs:20000,
});
let browser,failure;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-appearance-library';
const focused=process.argv.includes('--catalog-thumbnails-only');
const builtins=[
 ['default','Amplifier'],['aurora','Aurora'],['atelier','Atelier'],['graphite','Graphite'],
 ['digital-rain','Digital Rain'],['lightline','Lightline'],['ready','Breadbin'],
];
try{
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(async([name,args])=>{
  const receipt=await window.amplifier.dispatch(name,args);
  if(receipt.accepted===false)throw Error(receipt.error||name+' rejected');
  return receipt;
 },[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const appearance=async()=>(await state()).appearance;
 const list=async()=>(await action('theme.list')).result.items;
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await action('session.create');await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep my unfinished ideas.');
 await openSettingsPage(page,'appearance');
 await expect(page.getByRole('radio',{name:'Aurora',exact:true})).toBeVisible();
 await expect(page.locator('#theme-css')).toBeHidden();
 const items=await list();
 assert.deepEqual(items.filter(item=>item.source==='built-in').map(item=>item.id).sort(),
  builtins.map(([id])=>'builtin:'+id).sort(),'The library exposes exactly the seven Reference-based built-ins.');
 await expect(page.getByRole('button',{name:'Restore neutral appearance',exact:true})).toBeVisible();
 const before=(await appearance()).theme,selected=(await state()).selectedSessionId;

 if(focused){
  // Retained metadata must remain visible when a later refresh cannot finish.
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
  await expect(page.locator('.a-settings-experience')).toBeHidden();
  await page.route('**/api/actions',async route=>{
   const body=route.request().method()==='POST'?route.request().postDataJSON():null;
   if(body?.action==='theme.list')await route.abort('failed');else await route.continue();
  });
  await openSettingsPage(page,'appearance');
  await expect(page.getByRole('radio',{name:'Aurora',exact:true})).toBeVisible();
  await expect(page.getByText('Loading appearances…',{exact:true})).toHaveCount(0);
  assert.deepEqual((await appearance()).theme,before,'Reading a thumbnail never selects an appearance.');
  await page.unrouteAll({behavior:'wait'});
  const measurements=[];
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:1050});
   for(const mode of ['light','dark']){
    await page.getByRole('radio',{name:mode==='light'?'Light':'Dark',exact:true}).check();
    const card=page.locator('[data-appearance-card="builtin:digital-rain"]');
    await card.scrollIntoViewIfNeeded();
    await expect(card.locator('[data-thumbnail-state=ready]')).toHaveAttribute('data-thumbnail-mode',mode);
    const iframe=card.locator('iframe');await expect(iframe).toHaveAttribute('sandbox','');
    await expect(iframe).toHaveAttribute('tabindex','-1');
    const frame=await (await iframe.elementHandle()).contentFrame();assert.ok(frame);
    await expect(frame.locator('#amp-one')).toHaveAttribute('data-theme-scheme',mode);
    const specimen=await frame.locator('#amp-one').evaluate(el=>({
     scheme:el.dataset.themeScheme,version:el.dataset.thumbnailScene,
     ink:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,
     art:getComputedStyle(el.querySelector('[data-part=conversation]'),'::before').backgroundImage,
     composer:!!el.querySelector('[data-part=composer-surface]'),
     paragraphs:el.querySelectorAll('.a-markdown p').length,
     code:el.querySelector('pre code')?.textContent,
     csp:document.querySelector('meta[http-equiv="Content-Security-Policy"]').content,
    }));
    assert.match(specimen.art,/data:image\/png;base64,/,'The thumbnail uses the real bundled artwork.');
    assert.ok(specimen.composer&&specimen.paragraphs>=2);assert.match(specimen.code,/Make it real/);
    assert.match(specimen.csp,/default-src 'none'/);
    const entry=(await action('theme.read',{id:'builtin:digital-rain'})).result;
    await expect(card.locator('[data-appearance-fingerprint]')).toHaveAttribute('data-appearance-fingerprint',entry.fingerprint);
    assert.equal((await appearance()).theme.name,before.name,'Thumbnail mode cannot select an appearance.');
    measurements.push({width,mode,...specimen});
    await page.screenshot({path:`${out}/catalog-digital-rain-${mode}-${width}.png`});
   }
  }
  await page.locator('[data-appearance-card="builtin:digital-rain"]').click();
  await expect.poll(async()=>(await appearance()).theme.name).toBe('Digital Rain');
  assert.equal((await state()).selectedSessionId,selected);
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
  await expect(page.locator('.a-settings-experience')).toBeHidden();
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep my unfinished ideas.');
  assert.deepEqual(errors,[]);
  await (await import('node:fs/promises')).writeFile(`${out}/catalog-thumbnail-measurements.json`,JSON.stringify(measurements,null,2));
  console.log('Appearance catalog/thumbnails passed: shared Reference composition, light/dark and desktop/mobile, isolated frames, click-to-select and preserved conversation state.');
 }else{
  await page.screenshot({path:out+'/appearance-gallery.png'});
  for(const [,name] of builtins){
   const choice=page.getByRole('radio',{name,exact:true});
   await expect(choice).toBeVisible();
   await choice.locator('xpath=ancestor::label').click();
   await expect.poll(async()=>(await appearance()).theme.name).toBe(name);
   for(const mode of ['light','dark']){
    await page.getByRole('radio',{name:mode==='light'?'Light':'Dark',exact:true}).check();
    await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',mode);
    await page.screenshot({path:out+'/'+name.toLowerCase()+'-'+mode+'.png'});
   }
  }
  await page.getByRole('button',{name:'Restore neutral appearance',exact:true}).click();
  await expect.poll(async()=>(await appearance()).theme.id).toBe('reference');
  assert.equal((await appearance()).effective.appearanceId,'reference','Reference recovery uses the normal preference seam.');
  await expect(page.locator('.a-appearance-gallery').getByRole('radio')).toHaveCount(7,
   'The gallery contains the seven included appearances.');
  await expect(page.locator('.a-appearance-gallery').getByRole('button',{name:/^Use /})).toHaveCount(0,
   'Appearance selection does not require a redundant action button.');
  await openSettingsPage(page,'custom-appearance');
  const uploader=page.locator('#theme-import');
  await expect(uploader).toHaveAttribute('accept','.zip,application/zip');
  await expect(page.getByRole('button',{name:'Upload ZIP package',exact:true})).toBeVisible();
  await expect(page.locator('#theme-css')).toBeHidden();
  assert.equal((await appearance()).theme.id,'reference');
  await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep my unfinished ideas.');
  assert.deepEqual(errors,[]);
  console.log('Appearance library passed: seven built-ins, selectable Reference recovery, shared render preferences and ZIP-only import.');
 }
}catch(error){failure=error;throw error}
finally{
 await finishFixture({label:'Appearance library fixture',close:[['Appearance library browser',async()=>{await browser?.close()}]],stop,originalError:failure});
}