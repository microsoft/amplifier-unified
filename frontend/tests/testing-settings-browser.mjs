import {finishFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {startSettingsFixture} from './settings-fixture.mjs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const {base,stop}=await startSettingsFixture();
let browser,page,failure;
const errors=[];
try{
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/');await page.waitForSelector('#amp-one');
 await openSettingsPage(page,'testing');
 const section=page.getByRole('region',{name:'Testing and resources'});
 await expect(section.getByRole('heading',{name:'Test runner not ready'})).toBeVisible();
 await section.getByText('Advanced limits',{exact:true}).click();
 await section.getByLabel('Running test environments',{exact:true}).fill('7');
 await section.getByLabel('Minimum free disk space (GiB)',{exact:true}).fill('0');
 await expect(page.getByText('You have unsaved settings changes. They are kept while Settings stays open.',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Save testing limits'}).click();
 await expect(section.getByRole('status')).toContainText('Testing limits saved');
 let state=await page.evaluate(()=>window.amplifier.dispatch('testing.capacity.read',{}));
 assert.equal(state.result.defaults.maxRunning,7);
 assert.equal(state.result.defaults.minFreeBytes,0);
 assert.equal(state.result.runnerId,null);

 // The server saves successfully but its acknowledgement is lost. The UI
 // recognizes the exact saved values with one read, without another mutation.
 let saves=0;
 const lostReply=async route=>{
  if(route.request().postDataJSON()?.action!=='testing.capacity.save'){await route.continue();return}
  saves++;await route.fetch();
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Save acknowledgement unavailable'})});
 };
 await page.route('**/api/actions',lostReply);
 await section.getByLabel('Running test environments',{exact:true}).fill('6');
 await page.getByRole('button',{name:'Save testing limits'}).click();
 await expect(section.getByRole('status')).toContainText('Current testing settings match your changes');
 assert.equal(saves,1);
 await page.unroute('**/api/actions',lostReply);

 // Another settings writer wins; stale edits must not overwrite it.
 await section.getByLabel('Running test environments',{exact:true}).fill('8');
 await page.evaluate(async()=>{const current=(await window.amplifier.dispatch('testing.capacity.read',{})).result;await window.amplifier.dispatch('testing.capacity.save',{expectedRevision:current.revision,defaults:{maxRunning:9}})});
 await page.getByRole('button',{name:'Save testing limits'}).click();
 await expect(section.getByRole('alert')).toContainText('changed');
 await expect(section.getByLabel('Running test environments',{exact:true})).toHaveValue('8');
 await section.getByRole('button',{name:'Discard changes and reload'}).click();
 await expect(section.getByLabel('Running test environments',{exact:true})).toHaveValue('9');

 // Saved global settings remain reachable on narrow layouts and cold reopen.
 await page.setViewportSize({width:390,height:844});
 await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-compact','true');
 await section.getByLabel('Running test environments',{exact:true}).fill('10');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(section.getByLabel('Running test environments',{exact:true})).toHaveValue('10');
 await page.getByRole('button',{name:'Save testing limits'}).click();
 await expect(section.getByRole('status')).toContainText('Testing limits saved');
 await page.reload();await page.waitForSelector('#amp-one');
 await openSettingsPage(page,'testing');
 await section.getByText('Advanced limits',{exact:true}).click();
 await expect(section.getByLabel('Running test environments',{exact:true})).toHaveValue('10');
 assert.deepEqual(errors,[]);
 console.log('PASS: global settings without a runner, zero override, lost-acknowledgement readback without replay, stale writer protection, preserved draft, narrow layout and cold reopen');
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Testing settings fixture',close:[['Testing settings browser',()=>browser?.close()]],stop,originalError:failure})}
