import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/conversation_export_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Export fixture timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Export fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.waitForSelector('#amp-one');
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
 const stored=await page.request.get(url+snapshot.result.url);assert.equal((await stored.json()).content,copied);
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('Fixture clipboard denied')}}}));
 await page.getByRole('button',{name:'Copy Markdown',exact:true}).click();
 await expect(page.getByRole('alert').filter({hasText:'Fixture clipboard denied'})).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Download Markdown',exact:true}).scrollIntoViewIfNeeded();
 assert.ok(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth));
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-conversation-export-mobile.png'});
 assert.deepEqual(errors,[]);
 console.log('Conversation export browser passed: complete paged native history, exact code, voice and artifact refs, byte-identical copy/download, shared action snapshot, clipboard failure, mobile layout.');
}finally{await browser?.close();fixture.kill();}
