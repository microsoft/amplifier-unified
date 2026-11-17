import './composer-test-helpers.mjs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdirSync,mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startChatFixture} from './chat-fixture.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
const evidence=process.env.PWA_EVIDENCE_DIR||mkdtempSync(join(tmpdir(),'amplifier-pwa-evidence-'));mkdirSync(evidence,{recursive:true});
const {base,stop:stopFixture}=await startChatFixture();
let browser,originalError;
try{
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const context=await browser.newContext({viewport:{width:1100,height:850}}),page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/');await page.getByRole('heading',{name:'Welcome back'}).waitFor();
 await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
 assert.equal(await page.locator('.brand img').evaluate(el=>el.complete&&el.naturalWidth===128),true);
 const manifest=await page.evaluate(()=>fetch(document.querySelector('link[rel=manifest]').href).then(r=>r.json()));
 assert.equal(await page.title(),'Sign in · '+manifest.name);
 assert.match(manifest.name,/^Amplifier · .+:\d+$/);
 assert.equal(manifest.display,'standalone');assert.equal(manifest.id,'/');assert.deepEqual(manifest.icons.map(i=>i.sizes),['192x192','512x512']);
 const cdp=await context.newCDPSession(page);
 const metadata=await cdp.send('Page.getAppManifest');assert.equal(metadata.errors.length,0);
 const installation=await cdp.send('Page.getInstallabilityErrors');assert.deepEqual(installation.installabilityErrors,[]);
 await page.goto(base+'/login?error=1');await page.getByRole('alert').filter({hasText:'Could not sign in'}).waitFor();
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:join(evidence,'amplifier-pwa-login.png')});
 // Authenticate only this disposable fixture; never touch the real user's login.
 await context.setExtraHTTPHeaders({Authorization:'Bearer fixture-browser-control-token'});
 await page.goto(base+'/');await page.waitForSelector('#amp-one');
 await expect(page.locator('.a-adaptive-home')).toBeVisible();
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.getByRole('button',{name:'Settings test',exact:true}).click();
 await page.waitForFunction(()=>document.title==='Settings test - '+window.amplifier.getState().instanceBranding.name);
 const initialBranding=await page.evaluate(()=>window.amplifier.getState().instanceBranding);
 assert.equal(initialBranding.colorMode,'appearance');
 // A per-browser CSS accent must reach the public manifest and its icon URLs
 // without being stored as the listener's explicit icon color.
 const waitForAccent=async target=>{
  await target.waitForFunction(()=>{
   const root=document.querySelector('#amp-one'),manifest=document.querySelector('link[rel="manifest"]');
   if(!root||!manifest)return false;
   const probe=document.createElement('span');probe.style.color='var(--a-accent)';root.append(probe);
   const color=getComputedStyle(probe).color;probe.remove();
   const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');
   canvas.width=canvas.height=1;ctx.fillStyle=color;ctx.fillRect(0,0,1,1);
   const expected=[...ctx.getImageData(0,0,1,1).data].slice(0,3).map(n=>n.toString(16).padStart(2,'0')).join('');
   return new URL(manifest.href).searchParams.get('accent')?.toLowerCase()===expected;
  });
  return target.evaluate(async()=>{
   const url=new URL(document.querySelector('link[rel="manifest"]').href);
   const body=await fetch(url).then(response=>response.json());
   const headIcons=['icon','apple-touch-icon'].map(rel=>new URL(document.querySelector(`link[rel="${rel}"]`).href).searchParams.get('accent'));
   return {accent:url.searchParams.get('accent'),body,headIcons};
  });
 };
 const checkIconPixels=async(target,branding)=>{
  const pixels=await target.evaluate(async iconUrl=>{
   const response=await fetch(iconUrl);
   if(!response.ok)throw new Error(`Icon returned HTTP ${response.status}`);
   const image=await createImageBitmap(await response.blob());
   const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
   const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
   return {corner:[...ctx.getImageData(0,0,1,1).data],tile:[...ctx.getImageData(Math.floor(image.width/2),Math.floor(image.height/16),1,1).data]};
  },branding.body.icons[0].src);
  const expected=branding.accent.match(/../g).map(value=>parseInt(value,16));
  assert.equal(pixels.corner[3],0,'Built-in icon corners are transparent');
  assert.deepEqual(pixels.tile,[...expected,255],'Icon pixels match this browser’s rendered accent');
  assert.deepEqual(branding.headIcons,[branding.accent,branding.accent],'Favicon and touch icon use this browser’s accent');
 };
 await page.emulateMedia({colorScheme:'light'});
 const light=await waitForAccent(page);
 assert.equal(light.body.id,'/');
 assert.ok(light.body.icons.every(icon=>new URL(icon.src,base).searchParams.get('accent')===light.accent));
 await checkIconPixels(page,light);
 const other=await browser.newContext({viewport:{width:1100,height:850},colorScheme:'dark',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const otherPage=await other.newPage();
 await otherPage.goto(base+'/');await otherPage.waitForSelector('#amp-one');
 await otherPage.waitForFunction(()=>window.amplifier?.getState()?.instanceBranding?.colorMode==='appearance');
 const dark=await waitForAccent(otherPage);
 assert.notEqual(dark.accent,light.accent,'Different browser color schemes can use different appearance accents');
 assert.ok(dark.body.icons.every(icon=>new URL(icon.src,base).searchParams.get('accent')===dark.accent));
 await checkIconPixels(otherPage,dark);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().instanceBranding.colorMode),'appearance');
 assert.equal((await waitForAccent(page)).accent,light.accent,'A second browser must not overwrite the first browser icon');
 await other.close();
 await page.setViewportSize({width:1100,height:850});
 await page.evaluate(()=>window.amplifier.dispatch('settings.open',{page:'instance-branding',surface:'dialog'}));
 const appName=page.getByRole('textbox',{name:'App name',exact:true});
 await expect(appName).toHaveValue(initialBranding.name);
 const teal=page.getByRole('button',{name:'Teal icon',exact:true});
 for(const colorScheme of ['light','dark']){
  await page.emulateMedia({colorScheme});
  assert.equal(await teal.evaluate(node=>getComputedStyle(node).backgroundColor),'rgb(15, 118, 110)');
 }
 await page.emulateMedia({colorScheme:'light'});
 // Moving directly from a name edit to a color must apply BOTH, not lose the click to blur.
 await appName.fill('Design demo');await page.getByRole('button',{name:'Teal icon',exact:true}).click();
 await page.waitForFunction(()=>{const b=window.amplifier.getState().instanceBranding;return b.name==='Design demo'&&b.color==='#0f766e'});
 assert.equal(await page.evaluate(()=>window.amplifier.getState().instanceBranding.colorMode),'custom');
 await expect(page.locator('.a-work-brand')).toHaveText('Design demo');
 await page.waitForFunction(()=>document.title==='Settings test - Design demo');
 const freshManifest=()=>page.evaluate(()=>fetch(document.querySelector('link[rel=manifest]').href).then(async response=>({headers:Object.fromEntries(response.headers),body:await response.json()})));
 let current=await freshManifest();
 assert.equal(current.body.name,'Design demo');assert.equal(current.body.short_name,'Design demo');
 assert.equal(current.body.id,'/');assert.equal(current.body.start_url,'/');assert.equal(current.headers['cache-control'],'no-store');
 assert.notEqual(current.body.icons[0].src,manifest.icons[0].src,'Icon URL changes with branding');
 // Installers may retain an old URL without running any browser-side fallback.
 const oldIcon=await page.evaluate(async iconUrl=>{
  const response=await fetch(iconUrl),bytes=await response.blob();
  if(!response.ok)return {status:response.status};
  const image=await createImageBitmap(bytes),canvas=document.createElement('canvas');
  canvas.width=image.width;canvas.height=image.height;
  const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
  return {status:response.status,cache:response.headers.get('cache-control'),
   tile:[...ctx.getImageData(Math.floor(image.width/2),Math.floor(image.height/16),1,1).data]};
 },light.body.icons[0].src);
 assert.deepEqual(oldIcon,{status:200,cache:'no-store',tile:[15,118,110,255]},
  'Previously issued icon URLs serve current branding without immutable caching');
 assert.ok(await page.locator('link[rel="icon"]').evaluate(link=>link.href.includes('/instance-icon/32.png?v=')));
 assert.ok(await page.locator('link[rel="apple-touch-icon"]').evaluate(link=>link.href.includes('/instance-icon/180.png?v=')));
 const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=c.height=64;const ctx=c.getContext('2d');ctx.fillStyle='#f59e0b';ctx.fillRect(0,0,64,64);return c.toDataURL('image/png').split(',')[1]});
 // A direct name -> upload-picker transition must preserve the name until apply.
 await appName.fill('Upload demo');
 await page.locator('#instance-image').focus();
 await page.locator('#instance-image').setInputFiles({name:'test-icon.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
 await expect(page.getByRole('button',{name:'Use this image',exact:true})).toBeVisible();
 await expect(page.locator('.a-instance-preview img')).toHaveJSProperty('naturalWidth',64);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().instanceBranding.customIcon),false,'Preview alone does not change the server');
 await page.getByRole('button',{name:'Use this image',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().instanceBranding.customIcon);
 await page.waitForFunction(()=>window.amplifier.getState().instanceBranding.name==='Upload demo');
 await page.screenshot({path:join(evidence,'pwa-branding-desktop.png')});
 const uploaded=await page.evaluate(()=>window.amplifier.getState().instanceBranding);
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getState()?.instanceBranding?.customIcon);
 assert.equal((await freshManifest()).body.name,'Upload demo','Name persists after reload');
 await page.evaluate(()=>window.amplifier.dispatch('settings.open',{page:'instance-branding',surface:'dialog'}));
 await expect(appName).toHaveValue('Upload demo');
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:join(evidence,'pwa-branding-mobile.png')});
 await page.setViewportSize({width:1100,height:850});
 await page.getByRole('button',{name:'Rose icon',exact:true}).click();
 await page.waitForFunction(()=>{const b=window.amplifier.getState().instanceBranding;return !b.customIcon&&b.color==='#be123c'});
 assert.notEqual((await freshManifest()).body.icons[0].src,uploaded.iconUrl);
 // Returning to the appearance default must leave the app name intact.
 await page.getByRole('button',{name:'Use appearance accent',exact:true}).click();
 await page.waitForFunction(()=>{const b=window.amplifier.getState().instanceBranding;return b.colorMode==='appearance'&&!b.customIcon&&b.name==='Upload demo'});
 await expect(appName).toHaveValue('Upload demo');
 assert.equal((await waitForAccent(page)).body.name,'Upload demo');
 await appName.fill('Must not save this edit');await page.getByRole('button',{name:'Reset name & icon',exact:true}).click();
 await page.waitForFunction(name=>window.amplifier.getState().instanceBranding.name===name,initialBranding.defaultName);
 await expect(appName).toHaveValue(initialBranding.defaultName);
 await page.evaluate(()=>window.amplifier.dispatch('settings.close',{}));
 await page.evaluate(()=>window.amplifier.dispatch('session.rename',{id:window.amplifier.getState().selectedSessionId,title:'A renamed chat'}));
 await page.waitForFunction(()=>document.title==='A renamed chat - '+window.amplifier.getState().instanceBranding.name);
 await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Second conversation'}));
 await page.waitForFunction(()=>document.title==='Second conversation - '+window.amplifier.getState().instanceBranding.name);
 const keys=await page.evaluate(async()=>{const result=[];for(const name of await caches.keys())for(const r of await (await caches.open(name)).keys())result.push(new URL(r.url).pathname);return result});
 assert.ok(keys.includes('/offline.html'));assert.ok(keys.includes('/branding/pwa/pwa-512.png'));
 assert.ok(keys.every(path=>!path.startsWith('/api/')&&!['/','/login','/index.html'].includes(path)));
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const draft='Keep this draft through offline recovery';
 const saved=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().includes('/api/actions')&&response.request().postDataJSON()?.args?.patch?.draft===draft&&response.ok());
 await composer.fill(draft);await saved;
 const sends=[];page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/api/actions')){const body=request.postDataJSON();if(['conversation.send','message.edit','session.create'].includes(body?.action))sends.push(body)}});
 await context.setOffline(true);await page.reload();await page.getByRole('heading',{name:'Let’s get connected'}).waitFor();
 assert.equal(await page.locator('.brand img').evaluate(el=>el.complete&&el.naturalWidth===128),true);
 await page.screenshot({path:join(evidence,'amplifier-pwa-offline.png')});
 await expect(page.locator('[data-offline-origin]')).toHaveText(base);
 // No click: the cached fallback must notice restored connectivity on its own.
 await context.setOffline(false);await page.waitForSelector('#amp-one',{timeout:15000});
 await expect(composer).toHaveDraft(draft);assert.deepEqual(sends,[],'Recovery must not send or replay a user action');
 // Public offline support cannot reopen an authenticated chat after sign-out.
 await context.setExtraHTTPHeaders({});await page.reload();await page.getByRole('heading',{name:'Welcome back'}).waitFor();
 assert.equal(await page.evaluate(()=>fetch('/api/state').then(r=>r.status)),401);
 // Recovery from the signed-out fallback also returns through authentication.
 await context.setOffline(true);await page.reload();await page.getByRole('heading',{name:'Let’s get connected'}).waitFor();
 await context.setOffline(false);await page.getByRole('heading',{name:'Welcome back'}).waitFor({timeout:15000});
 assert.equal(await page.evaluate(()=>fetch('/api/state').then(r=>r.status)),401);
 assert.deepEqual(sends,[]);
 assert.deepEqual(errors,[]);console.log('PWA browser passed: installability, signed-out metadata, rendered light/dark accents in manifest/head/icon pixels with browser isolation, rounded pixels, old icon URL compatibility, reactive name/header/title/icons, single-click edit+color/reset, raster preview/upload/persistence, responsive Settings, dynamic manifest cache, public-only cache, automatic offline recovery, saved draft, no replay and authentication boundary.');
}catch(error){originalError=error;throw error}
finally{await finishFixture({label:'PWA fixture',close:browser?[['browser',()=>browser.close()]]:[],stop:stopFixture,originalError})}
