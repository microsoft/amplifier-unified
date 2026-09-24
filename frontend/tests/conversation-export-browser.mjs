import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/conversation_export_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,vite;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Export fixture timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Export fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 const target=url,root=fileURLToPath(new URL('../',import.meta.url));
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,configure(proxy){proxy.on('proxyReq',request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'session-details'}}));
 await page.getByRole('button',{name:'Preview Markdown',exact:true}).click();
 await expect(page.getByLabel('Reviewed Markdown')).toBeVisible();
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect(page.getByRole('status').filter({hasText:'Copied conversation Markdown'})).toBeVisible();
 const copied=await page.evaluate(()=>navigator.clipboard.readText());
 assert.match(copied,/The earliest question/);assert.match(copied,/## User \(voice\)\n\nSpoken follow-up/);
 assert.ok(copied.includes('```python\nprint("exact α")  \n```\n'));assert.match(copied,/saved-diagram/);
 const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Download Markdown',exact:true}).click();
 const download=await pending;assert.match(download.suggestedFilename(),/\.md$/);
 assert.equal(await readFile(await download.path(),'utf8'),copied);
 await expect(page.getByRole('status').filter({hasText:'Markdown download started'})).toBeVisible();
 // The exact existing UI action is also available through the shared bridge.
 const snapshot=await page.evaluate(async()=>{const state=window.amplifier.getState();return window.amplifier.dispatch('session.export',{id:state.selectedSessionId,format:'markdown',destination:'none'})});
 const stored=await page.request.get(new URL(snapshot.result.url,page.url()).href);assert.equal((await stored.json()).content,copied);
 // A selected range is reviewed separately; changing controls revokes the old preview.
 await page.getByLabel('Export scope').selectOption('range');
 await expect(page.getByLabel('Reviewed Markdown')).toHaveCount(0);
 const boundaries=await page.getByLabel('Starting message').locator('option').evaluateAll(rows=>rows.map(row=>row.value));
 await page.getByLabel('Starting message').selectOption(boundaries.at(-2));
 await page.getByLabel('Ending message').selectOption(boundaries.at(-1));
 await page.getByRole('checkbox',{name:'Minimal context: omit conversation identifiers'}).check();
 await page.getByRole('button',{name:'Preview Markdown',exact:true}).click();
 await expect(page.getByLabel('Reviewed Markdown')).toBeVisible();
 const range=await page.getByLabel('Reviewed Markdown').inputValue();
 assert.ok(!range.includes('The earliest question'));assert.ok(range.includes('exact α'));assert.ok(range.includes('Spoken follow-up'));assert.ok(range.includes('saved-diagram'));
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(range);
 // New chat activity cannot change already-reviewed bytes.
 await page.request.post(new URL('/api/fixture/exportAppend',page.url()).href);
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(range);
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('Fixture clipboard denied')}}}));
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect(page.getByRole('alert').filter({hasText:'Fixture clipboard denied'})).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Download Markdown',exact:true}).scrollIntoViewIfNeeded();
 assert.ok(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth));
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-conversation-export-mobile.png'});
 // A late preview response must not populate another conversation's review.
 let releasePreview,startedPreview,finishedPreview;
 const held=new Promise(resolve=>{releasePreview=resolve}),started=new Promise(resolve=>{startedPreview=resolve}),finished=new Promise(resolve=>{finishedPreview=resolve});
 await page.route('**/api/conversation/exports/**',async route=>{startedPreview();await held;await route.continue();finishedPreview()});
 await page.getByRole('button',{name:'Refresh preview',exact:true}).click();await started;
 await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Different export chat'}));
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'session-details'}}));
 releasePreview();await finished;await page.unroute('**/api/conversation/exports/**');
 await expect(page.getByLabel('Reviewed Markdown')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Preview Markdown',exact:true})).toBeVisible();
 assert.deepEqual(errors,[]);
 console.log('Conversation export browser passed: complete paged native history, exact code, voice and artifact refs, byte-identical copy/download, shared action snapshot, selected ranges, immutable delivery after later activity, stale preview navigation, clipboard failure, mobile layout.');
}finally{await browser?.close();await vite?.close();fixture.kill();}
