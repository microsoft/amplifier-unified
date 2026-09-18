import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'ignore'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true});
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
 await context.setOffline(true);await page.reload();await page.getByRole('heading',{name:'Let’s get connected'}).waitFor();
 assert.equal(await page.locator('.brand img').evaluate(el=>el.complete&&el.naturalWidth===128),true);
 await page.screenshot({path:'/tmp/amplifier-pwa-offline.png'});
 await context.setOffline(false);await page.getByRole('link',{name:'Try again'}).click();await page.waitForSelector('#amp-one');
 // Public offline support cannot reopen an authenticated chat after sign-out.
 await context.setExtraHTTPHeaders({});await page.reload();await page.getByRole('heading',{name:'Welcome back'}).waitFor();
 assert.equal(await page.evaluate(()=>fetch('/api/state').then(r=>r.status)),401);
 assert.deepEqual(errors,[]);console.log('PWA browser passed: installability, signed-out branding, worker registration, dynamic chat titles, public-only cache, offline fallback/recovery and authentication boundary.');
}finally{await browser.close();fixture.kill()}
