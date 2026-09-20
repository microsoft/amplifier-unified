import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/computation_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exit '+code)))});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(ready.url);
 const panel=page.getByRole('region',{name:'Computation'});
 await panel.getByRole('button',{name:'Computation',exact:true}).click();
 for(const language of ['python','node']){
  await panel.getByLabel('Computation language').selectOption(language);
  await panel.getByRole('button',{name:'Create runtime'}).click();
  await panel.getByLabel('Cell code').fill(language==='python'?'answer = 40':'var answer = 40');
  await expect(panel.getByRole('button',{name:'Run cell'})).toBeEnabled({timeout:15000});
  await panel.getByRole('button',{name:'Run cell'}).click();
  await expect(panel.locator('[aria-live] strong')).toHaveText('completed');
  await expect(panel.getByRole('button',{name:'Run cell'})).toBeEnabled();
  await panel.getByLabel('Cell code').fill(language==='python'?"print('persistent result'); answer + 2":"console.log('persistent result'); answer + 2");
  await panel.getByRole('button',{name:'Run cell'}).click();
  await expect(panel.locator('pre')).toContainText('42');
  await expect(panel.locator('pre')).toContainText('persistent result');
  if(language==='node'){await page.screenshot({animations:'disabled',path:'/tmp/amplifier-computation-controls.png'})}
  await panel.getByLabel('Cell code').fill(language==='python'?"import time; time.sleep(30)":"new Promise(r=>setTimeout(r,30000))");
  await panel.getByRole('button',{name:'Run cell'}).click();
  await expect(panel.getByRole('button',{name:'Interrupt',exact:true})).toBeEnabled();
  await panel.getByRole('button',{name:'Interrupt',exact:true}).click();
  await expect(panel.locator('[aria-live] strong')).toHaveText('cancelled');
  await panel.getByRole('button',{name:'Reset variables'}).click();
  await panel.getByLabel('Cell code').fill('answer');
  await expect(panel.getByRole('button',{name:'Run cell'})).toBeEnabled({timeout:15000});
  await panel.getByRole('button',{name:'Run cell'}).click();
  await expect(panel.locator('[aria-live] strong')).toHaveText('failed');
  await panel.getByRole('button',{name:'Close runtime'}).click();
  await expect(panel.getByLabel('Cell code')).toHaveCount(0);
 }
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),ready.sessionId);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep computation draft');
 await page.reload();
 await page.getByRole('button',{name:'Operations',exact:true}).click();
 const operations=page.getByRole('region',{name:'Operations'});
 await operations.getByRole('button',{name:'Computation cell · Completed'}).first().click();
 await expect(operations.locator('pre')).toContainText('42');
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-computation-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await operations.evaluate(node=>node.scrollWidth<=node.clientWidth));
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-computation-mobile.png'});
 assert.deepEqual(errors,[]);
 console.log('Computation browser passed: Python+Node state, exact cells, interrupt, reset, durable reload, draft/selection, mobile bounds.');
}finally{await browser?.close();fixture.kill();}
