import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/cache_usage_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Cache usage fixture timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Cache usage fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);
 await page.locator('#amp-one').waitFor();
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsSection:'maintenance',settingsExpanded:['updates']}}));
 await expect(page.getByRole('heading',{name:'Other cached sources',exact:true})).toBeVisible();
 await expect(page.locator('#update-issues-list li')).toHaveCount(1);
 await expect(page.locator('#update-issues-list')).toContainText('Configured bundle');
 await expect(page.locator('#unknown-source-issues-list li')).toHaveCount(2);
 await expect(page.locator('#unknown-source-issues-list')).toContainText('Historical branch');
 await expect(page.locator('#unknown-source-issues-list')).toContainText('Tracked source changes are preserved.');
 await expect(page.locator('#available-updates-list')).toContainText('Indirect dependency');
 await expect(page.getByRole('button',{name:'Install available',exact:true})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Show all 5 sources',exact:true})).toHaveAttribute('aria-expanded','false');
 await page.getByRole('heading',{name:'Other cached sources',exact:true}).locator('..').scrollIntoViewIfNeeded();
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-cache-usage-desktop.png'});
 await page.getByRole('button',{name:'Check now',exact:true}).click();
 await expect.poll(async()=>await (await page.request.get(url+'/fixture')).json()).toEqual(['check']);
 await page.getByRole('button',{name:'Install available',exact:true}).click();
 await expect.poll(async()=>await (await page.request.get(url+'/fixture')).json()).toEqual(['check','install']);
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('heading',{name:'Other cached sources',exact:true}).locator('..').scrollIntoViewIfNeeded();
 assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth));
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-cache-usage-mobile.png'});
 await page.getByRole('button',{name:'Show all 5 sources',exact:true}).click();
 await expect(page.locator('#update-sources-list li')).toHaveCount(5);
 await expect(page.locator('#update-sources-list')).toContainText('Pinned dependency');
 assert.deepEqual(errors,[]);
 console.log('Cache usage UI passed: configured/unknown grouping, visible failures with inventory collapsed, indirect updates installable, synthetic check/install controls, preserved pins, desktop and mobile layout.');
}finally{await browser?.close();fixture.kill();}
