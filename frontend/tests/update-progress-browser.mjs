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
 const overview=page.locator('[data-part="application-update"]').first(),updates=page.locator('[data-part="updates"]'),advanced=page.locator('[data-part="ecosystem-update-details"]');
 await expect(overview).toContainText('Finishing your update');
 await expect(updates.locator('[data-part="update-batch"]')).not.toBeVisible();
 await expect(updates.locator('[data-part="update-support"]')).not.toBeVisible();
 await advanced.locator('summary').first().click();
 await updates.locator('[data-part="update-completed-batches"]>summary').click();
 await expect(updates).toContainText('24 of 37 completed');
 await expect(updates).toContainText('Current batch: amplifier-module-hooks-approval');
 await expect(updates).toContainText('Included components installed at');
 await expect(updates).not.toContainText('Other components installed at');
 await page.screenshot({path:output+'-desktop.png'});
 await page.setViewportSize({width:320,height:844});
 for(const button of await updates.locator('[data-part="update-support"] button').all()){
  // Resize and font loading can reflow between separate boundingBox calls.
  // Read both rectangles in one layout frame and allow that reflow to settle.
  await expect.poll(()=>button.evaluate(el=>{
   const svg=el.querySelector('svg').getBoundingClientRect(),label=el.querySelector('span').getBoundingClientRect();
   return Math.abs(svg.y+svg.height/2-label.y-label.height/2);
  }),{message:'diagnostic icon and label must stay inline'}).toBeLessThan(2);
 }

 assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'mobile dialog overflows');
 await page.screenshot({path:output+'-mobile.png'});
 const stage=async value=>{const response=await page.request.post('http://127.0.0.1:8957/fixture/update-progress',{data:{stage:value}});assert.ok(response.ok(),await response.text())};
 await stage('compatibility');await expect(updates).toContainText('12 of 37 completed');
 await stage('waiting');await expect(overview).toContainText('Waiting for your work to finish');
 await expect(updates).not.toContainText('12 of 37 completed');
 await expect(overview.locator('.a-progress-spinner')).toHaveCount(0);
 await updates.locator('[data-part="update-blockers"]>summary').click();
 await expect(updates.locator('[data-part="update-blockers"]')).toContainText('A voice call is active');
 await stage('activating');
 const check=page.locator('[data-action="updates.check"]'),install=page.locator('[data-action="updates.install"]');
 await expect(install).toHaveCount(0);
 await expect(check).toBeDisabled();await expect(check).toContainText('Finishing update');
 await expect(overview).toContainText('Finishing your update');
 await expect(overview).toContainText('continues automatically');
 const spinner=overview.locator('.a-progress-spinner');await expect(spinner).toBeVisible();
 await expect.poll(()=>spinner.evaluate(el=>getComputedStyle(el).animationName)).toBe('ampSpin');
 await page.emulateMedia({reducedMotion:'reduce'});
 await expect.poll(()=>spinner.evaluate(el=>getComputedStyle(el).animationName)).toBe('none');
 await expect(overview).toContainText('Finishing your update');
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.screenshot({path:output+'-activation-mobile.png'});
 await page.setViewportSize({width:1280,height:950});
 await page.screenshot({path:output+'-activation-desktop.png'});
 await page.reload();await page.waitForSelector('#amp-one');
 if(!await overview.isVisible())await openSettingsPage(page,'updates');
 await expect(install).toHaveCount(0);await expect(overview).toContainText('Finishing your update');
 await stage('failed');await expect(overview).toContainText('Updates need attention');
 await expect(updates).not.toContainText('Other components installed at');
 await expect(install).toBeEnabled();
 await expect(overview.locator('.a-progress-spinner')).toHaveCount(0);
 await stage('adopting');await expect(overview).toContainText('Ready for new work');await expect(overview).toContainText('No app restart');await expect(check).toBeEnabled();
 await stage('installed');await expect(updates).toContainText('Other components installed at');
 await expect(updates).toContainText('Included components installed at');
 await expect(overview).toContainText('You’re up to date');
 await expect(check).toBeEnabled();await expect(install).toHaveCount(0);
 await expect(overview.locator('.a-progress-spinner')).toHaveCount(0);
 await page.reload();await page.waitForSelector('#amp-one');
 // Settings is a persisted client view. Do not click the covered navigation
 // toggle when reload has already restored the open Updates dialog.
 if(!await overview.isVisible())await openSettingsPage(page,'updates');
 await expect(updates).toContainText('Other components installed at');
 await page.setViewportSize({width:1280,height:950});
 await openSettingsPage(page,'appearance');
 await page.getByLabel('Color mode',{exact:true}).selectOption('dark');
 await openSettingsPage(page,'updates');
 await stage('waiting');
 await expect(overview).toContainText('Waiting for your work to finish');
 await expect(updates.locator('[data-part="update-support"]')).not.toBeVisible();
 await page.screenshot({path:output+'-waiting-dark.png'});
 await updates.locator('[data-part="update-blockers"]>summary').click();
 await expect(updates.locator('[data-part="update-blockers"]')).toContainText('A voice call is active');
 await advanced.locator('summary').first().click();
 await updates.locator('[data-part="update-support"]').scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'-advanced-dark.png'});
 await stage('activating');
 await updates.getByRole('button',{name:'Return to chat',exact:true}).click();
 await expect(page.locator('.a-settings-experience')).not.toBeVisible();
 await openSettingsPage(page,'updates');await expect(overview).toContainText('Finishing your update');
 assert.deepEqual(errors,[]);result.status='passed';result.checks={progress:true,componentIdentity:true,completedMilestones:true,noPrematureActivation:true,waitingDistinct:true,activationBusy:true,duplicateInstallDisabled:true,reducedMotion:true,retryAfterFailure:true,completionRestoresControls:true,failureDistinct:true,reload:true,mobileOverflow:false};result.browserErrors=errors;result.browserVersion=browser.version();
}catch(error){result.status='failed';result.error=String(error);if(page)await page.screenshot({path:output+'-failed.png'});throw error}
finally{await browser?.close();fixture.kill('SIGTERM');await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},10000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});result.fixtureLog=log;await writeFile(output+'.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));}
