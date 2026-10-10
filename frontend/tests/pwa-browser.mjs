import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'ignore'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
try{
 const context=await browser.newContext({viewport:{width:1100,height:850}}),page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto('http://127.0.0.1:8958/');await page.getByRole('heading',{name:'Welcome back'}).waitFor();
 await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
 assert.equal(await page.title(),'Sign in - Amplifier');
 assert.equal(await page.locator('.brand img').evaluate(el=>el.complete&&el.naturalWidth===128),true);
 const manifest=await page.evaluate(()=>fetch(document.querySelector('link[rel=manifest]').href).then(r=>r.json()));
 assert.equal(manifest.display,'standalone');assert.equal(manifest.id,'/');assert.deepEqual(manifest.icons.map(i=>i.sizes),['192x192','512x512']);
 const cdp=await context.newCDPSession(page);
 const metadata=await cdp.send('Page.getAppManifest');assert.equal(metadata.errors.length,0);
 const installation=await cdp.send('Page.getInstallabilityErrors');assert.deepEqual(installation.installabilityErrors,[]);
 await page.goto('http://127.0.0.1:8958/login?error=1');await page.getByRole('alert').filter({hasText:'Could not sign in'}).waitFor();
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/tmp/amplifier-pwa-login.png'});
 // Authenticate only this disposable fixture; never touch the real user's login.
 await context.setExtraHTTPHeaders({Authorization:'Bearer fixture-browser-control-token'});
 await page.goto('http://127.0.0.1:8958/');await page.waitForSelector('#amp-one');
 await page.waitForFunction(()=>document.title==='Settings test - Amplifier');
 await page.evaluate(()=>window.amplifier.dispatch('session.rename',{id:window.amplifier.getState().selectedSessionId,title:'A renamed chat'}));
 await page.waitForFunction(()=>document.title==='A renamed chat - Amplifier');
 await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Second conversation'}));
 await page.waitForFunction(()=>document.title==='Second conversation - Amplifier');
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
 await page.screenshot({path:'/tmp/amplifier-pwa-offline.png'});
 await expect(page.locator('[data-offline-origin]')).toHaveText('http://127.0.0.1:8958');
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
 assert.deepEqual(errors,[]);console.log('PWA browser passed: installability, signed-out branding, worker registration, dynamic chat titles, public-only cache, automatic offline recovery, saved draft, no replay and authentication boundary.');
}finally{await browser.close();fixture.kill()}
