import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
const python=process.env.UNIFIED_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url));
const fixture=spawn(python,['-u',fileURLToPath(new URL('../../tests/fixtures/update_progress_server.py',import.meta.url))]);
const output=process.env.UNIFIED_BROWSER_EVIDENCE||'/tmp/update-progress';
let browser,page,log='';const result={status:'pending',syntheticTransitions:true,realHTTPAndSSE:true,paidCalls:0};
fixture.stderr.on('data',data=>log+=data);
try{
 const headers={Authorization:'Bearer fixture-browser-control-token'};
 await expect.poll(async()=>{if(fixture.exitCode!==null)throw Error(log);try{return (await fetch('http://127.0.0.1:8957/api/health',{headers})).status}catch{return 0}},{timeout:60000}).toBe(200);
 browser=await chromium.launch({channel:'chromium',headless:true,args:process.env.UNIFIED_BROWSER_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:headers});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('http://127.0.0.1:8957');await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 const overview=page.locator('[data-part="application-update"]').first();
 await expect(overview).toContainText('24 of 37 completed');
 await expect(overview).toContainText('Current batch: amplifier-module-hooks-approval');
 await expect(overview).toContainText('Included components installed at');
 await expect(overview).not.toContainText('Other components installed at');
 await page.screenshot({path:output+'-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'mobile dialog overflows');
 await page.screenshot({path:output+'-mobile.png'});
 const stage=async value=>{const response=await page.request.post('http://127.0.0.1:8957/fixture/update-progress',{data:{stage:value}});assert.ok(response.ok(),await response.text())};
 await stage('compatibility');await expect(overview).toContainText('12 of 37 completed');
 await stage('waiting');await expect(overview).toContainText('Waiting for your work to finish');
 await expect(overview).not.toContainText('12 of 37 completed');
 await stage('failed');await expect(overview).toContainText('Updates need attention');
 await expect(overview).not.toContainText('Other components installed at');
 await stage('installed');await expect(overview).toContainText('Other components installed at');
 await expect(overview).toContainText('Included components installed at');
 await page.reload();await openSettingsPage(page,'updates');await expect(overview).toContainText('Other components installed at');
 assert.deepEqual(errors,[]);result.status='passed';result.checks={progress:true,componentIdentity:true,completedMilestones:true,noPrematureActivation:true,waitingDistinct:true,failureDistinct:true,reload:true,mobileOverflow:false};result.browserErrors=errors;result.browserVersion=browser.version();
}catch(error){result.status='failed';result.error=String(error);if(page)await page.screenshot({path:output+'-failed.png'});throw error}
finally{await browser?.close();fixture.kill('SIGTERM');await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},10000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});result.fixtureLog=log;await writeFile(output+'.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));}
