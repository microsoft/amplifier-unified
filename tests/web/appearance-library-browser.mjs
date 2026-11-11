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
    await expect(iframe).toBeInViewport({ratio:1});
    const frame=await (await iframe.elementHandle()).contentFrame();assert.ok(frame);
    await expect(frame.locator('#amp-one')).toHaveAttribute('data-theme-scheme',mode);
    const entry=(await action('theme.read',{id:'builtin:digital-rain'})).result;
    const bundledArt=entry.css.match(/data:image\/png;base64,[A-Za-z0-9+/=]+/g);
    assert.equal(bundledArt?.length,3,'Digital Rain supplies three bundled PNG layers.');
    // "ready" means srcDoc was generated, not that its document/styles loaded.
    // Reference paints the root and native conversation directly, not ::before.
    for(const surface of [frame.locator('#amp-one'),frame.locator('#amp-one .a-conversation[data-part=conversation]')]){
     await expect(surface).toHaveCSS('background-image',/data:image\/png;base64,/);
    }
    const contentSelectors={
     messages:'[data-part=messages]',
     userMessage:'.a-message.a-user .a-markdown p',
     assistantMessage:'.a-message.a-assistant .a-markdown p',
     composer:'form[data-part=composer]',
     composerSurface:'[data-part=composer-surface]',
     composerText:'[data-part=composer-surface] [role=textbox]',
    };
    // Painted containers and present text do not prove their children are shown.
    const conversation=frame.locator('#amp-one .a-conversation[data-part=conversation]');
    for(const selector of Object.values(contentSelectors)){
     await expect(conversation.locator(selector)).toBeVisible();
    }
    await expect(conversation.locator(contentSelectors.userMessage)).toHaveText('Make room for a new idea.');
    await expect(conversation.locator(contentSelectors.assistantMessage)).toHaveText('A clear space for your next idea, with a useful reference.');
    await expect(conversation.locator(contentSelectors.composerText)).toHaveText('Ask Amplifier…');
    const specimen=await frame.locator('#amp-one').evaluate((el,selectors)=>{
     const conversation=el.querySelector('.a-conversation[data-part=conversation]');
     const rootBounds=el.getBoundingClientRect(),conversationBounds=conversation.getBoundingClientRect();
     return {
      scheme:el.dataset.themeScheme,version:el.dataset.thumbnailScene,
      ink:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,
      art:getComputedStyle(el).backgroundImage,conversationArt:getComputedStyle(conversation).backgroundImage,
      nativeBounds:{width:rootBounds.width,height:rootBounds.height,conversation:{
       x:conversationBounds.left-rootBounds.left,y:conversationBounds.top-rootBounds.top,
       width:conversationBounds.width,height:conversationBounds.height,
      },content:Object.fromEntries(Object.entries(selectors).map(([name,selector])=>{
       const bounds=conversation.querySelector(selector).getBoundingClientRect();
       return [name,{x:bounds.left-rootBounds.left,y:bounds.top-rootBounds.top,width:bounds.width,height:bounds.height}];
      }))},
      composer:!!el.querySelector('[data-part=composer-surface]'),
      paragraphs:el.querySelectorAll('.a-markdown p').length,
      code:el.querySelector('pre code')?.textContent,
      csp:document.querySelector('meta[http-equiv="Content-Security-Policy"]').content,
     };
    },contentSelectors);
    for(const art of [specimen.art,specimen.conversationArt]){
     assert.deepEqual(art.match(/data:image\/png;base64,[A-Za-z0-9+/=]+/g),bundledArt,
      'Both thumbnail surfaces paint the exact bundled PNG layers.');
    }
    const decoded=await frame.evaluate(async sources=>Promise.all(sources.map(async src=>{
     const image=new Image();image.src=src;await image.decode();
     return {width:image.naturalWidth,height:image.naturalHeight};
    })),bundledArt);
    assert.ok(decoded.every(image=>image.width>0&&image.height>0),'Every painted PNG decodes.');
    assert.equal(specimen.nativeBounds.width,800);assert.equal(specimen.nativeBounds.height,520);
    const bounds=specimen.nativeBounds.conversation;
    assert.ok(bounds.width>0&&bounds.height>0&&bounds.x>=0&&bounds.y>=0&&
     bounds.x+bounds.width<=800&&bounds.y+bounds.height<=520,'The native conversation stays inside the specimen.');
    for(const [name,content] of Object.entries(specimen.nativeBounds.content)){
     assert.ok(content.width>0&&content.height>0&&content.x>=bounds.x&&content.y>=bounds.y&&
      content.x+content.width<=bounds.x+bounds.width&&content.y+content.height<=bounds.y+bounds.height,
      `The native ${name} has positive bounds inside the conversation and its 800×520 specimen.`);
    }
    const messages=specimen.nativeBounds.content.messages;
    for(const name of ['userMessage','assistantMessage']){
     const content=specimen.nativeBounds.content[name];
     assert.ok(content.x>=messages.x&&content.y>=messages.y&&
      content.x+content.width<=messages.x+messages.width&&content.y+content.height<=messages.y+messages.height,
      `The native ${name} is not clipped by the messages surface.`);
    }
    const screenBounds=await iframe.boundingBox(),thumbnailBounds=await card.locator('.a-appearance-thumbnail').boundingBox();
    assert.ok(screenBounds&&thumbnailBounds);
    assert.ok(Math.abs(screenBounds.x-thumbnailBounds.x)<=1&&Math.abs(screenBounds.y-thumbnailBounds.y)<=1&&
     Math.abs(screenBounds.width-thumbnailBounds.width)<=1&&Math.abs(screenBounds.height-thumbnailBounds.height)<=1,
     'The on-screen iframe fits its thumbnail rather than clipping an unscaled native scene.');
    assert.ok(specimen.composer&&specimen.paragraphs>=2);assert.match(specimen.code,/Make it real/);
    assert.match(specimen.csp,/default-src 'none'/);
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
  await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page','appearance');
  await expect(page.locator('[data-settings-destination="custom-appearance"]')).toHaveCount(0);
  const uploader=page.locator('#theme-import');
  await page.getByRole('button',{name:'Upload ZIP package',exact:true}).scrollIntoViewIfNeeded();
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