import {openSettingsPage} from './browser-settings.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 ['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],
 {stdio:['ignore','pipe','pipe']});
let browser,log='';fixture.stderr.on('data',chunk=>log+=chunk);
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-detail-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(`http://127.0.0.1:${port}`);
 await page.waitForSelector('#amp-one');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsExpanded:['diagnostics']}}));
 await openSettingsPage(page,'diagnostics');
 const recording=page.getByLabel('Record provider requests for troubleshooting',{exact:true});
 await expect(recording).not.toBeChecked();
 await expect(page.getByText(/Earlier calls cannot be recovered/)).toBeVisible();
 await recording.check();
 await page.getByRole('button',{name:'Save capture settings',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().diagnostics.config.providerRequests===true);
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.destinations),[]);
 await page.reload();await openSettingsPage(page,'diagnostics');
 await expect(recording).toBeChecked();
 await page.screenshot({path:'/tmp/unified-provider-recording-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await openSettingsPage(page,'diagnostics');
 await expect(recording).toBeChecked();
 await recording.uncheck();
 await page.getByRole('button',{name:'Save capture settings',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().diagnostics.config.providerRequests===false);
 await page.reload();await openSettingsPage(page,'diagnostics');
 await expect(recording).not.toBeChecked();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await recording.scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/unified-provider-recording-mobile.png'});
 assert.deepEqual(errors,[]);
 console.log('Provider recording: desktop/mobile opt-in, saved reload, opt-out, no forwarding destination, and no horizontal overflow passed.');
}finally{
 await browser?.close();fixture.kill();
}
