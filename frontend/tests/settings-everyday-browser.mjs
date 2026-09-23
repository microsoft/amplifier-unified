import {openSettingsPage,openSettingsDialog} from './browser-settings.mjs';
import {settingsSections} from '../src/settings-navigation.js';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/settings_collections_server.py',import.meta.url))]);
let browser,page,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),60000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});fixture.on('exit',code=>reject(Error('Fixture exited '+code+stderr)))});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:920},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(base);await page.waitForSelector('#amp-one');await openSettingsPage(page,'ai-connections');
 assert.equal(await page.locator('.a-settings-sidebar>button').count(),8);
 assert.equal(await page.locator('.a-settings-sidebar').getByText('Bundles & modules',{exact:true}).count(),0);
 await page.getByRole('button',{name:'Connect another service',exact:true}).click();
 await page.getByRole('button',{name:/Anthropic API Connect with/}).click();
 await page.getByLabel('API key',{exact:true}).fill('everyday-private-fixture-key');
 await openSettingsPage(page,'appearance');await openSettingsPage(page,'ai-connections');
 await expect(page.getByRole('button',{name:'Connect another service',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Connect another service',exact:true}).click();await page.getByRole('button',{name:/Anthropic API Connect with/}).click();await page.getByLabel('API key',{exact:true}).fill('everyday-private-fixture-key');
 assert.ok(!JSON.stringify(await state()).includes('everyday-private-fixture-key'));
 await page.getByRole('button',{name:'Save and check connection',exact:true}).click();
 await page.getByRole('button',{name:'Finish setup',exact:true}).waitFor();
 await page.getByLabel('Model',{exact:true}).selectOption('fixture-alternative');
 const oldMatrix=(await state()).setup.active;
 await page.getByRole('button',{name:'Finish setup',exact:true}).click();
 await expect(page.getByText('Model saved. Existing model rules are unchanged.',{exact:true})).toBeVisible();
 assert.equal((await state()).setup.active,oldMatrix);
 assert.ok((await state()).setup.providers.some(p=>p.module==='provider-anthropic'&&p.config.default_model==='fixture-alternative'));
 assert.ok(!JSON.stringify(await state()).includes('everyday-private-fixture-key'));
 await page.screenshot({path:'/tmp/settings-everyday-ai-desktop.png'});
 await openSettingsPage(page,'smart-tools');await page.getByRole('button',{name:'Browse',exact:true}).click();
 await page.getByRole('checkbox',{name:'Select Tool 00',exact:true}).check();await page.getByRole('checkbox',{name:'Select Tool 02',exact:true}).check();
 await page.getByRole('button',{name:'Review & install',exact:true}).click();
 await expect(page.getByRole('region',{name:'Review installation',exact:true})).toBeVisible();
 assert.equal((await state()).smartTools.operations.filter(op=>op.action==='smartTools.installBatch').length,0);
 await page.getByRole('button',{name:'Install selected (2)',exact:true}).click();
 await page.getByRole('button',{name:'Retry unfinished installations',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().smartTools.operations.filter(op=>op.action==='smartTools.installBatch').at(-1)?.status==='completed');
 await page.getByRole('button',{name:'Installed',exact:true}).click();
 await expect(page.getByText('Installed · finish setup',{exact:true}).first()).toBeVisible();
 await page.getByRole('button',{name:/tool-0 Installed · finish setup/}).click();
 await expect(page.getByText('Advanced setup required',{exact:true})).toBeVisible();
 await openSettingsPage(page,'notifications');
 const notification=page.getByText('Set up phone notifications',{exact:true});
 assert.equal(await notification.locator('..').getAttribute('open'),null);await notification.click();
 await page.getByText('Server & account',{exact:true}).click();await page.getByLabel(/^Access token/).fill('notification-private-fixture');await openSettingsPage(page,'privacy');await openSettingsPage(page,'notifications');
 await page.getByText('Set up phone notifications',{exact:true}).click();await page.getByText('Server & account',{exact:true}).click();assert.equal(await page.getByLabel(/^Access token/).inputValue(),'notification-private-fixture');assert.ok(!JSON.stringify(await state()).includes('notification-private-fixture'));
 let layouts=0;
 for(const width of [320,390,736,1280]){
  await page.setViewportSize({width,height:920});
  for(const section of settingsSections)for(const [id] of section.pages){
   await openSettingsPage(page,id);
   await expect(page.locator('.a-settings-page-content:not([hidden])')).toBeVisible();
   assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${id} overflow at ${width}`);
   assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${id} dialog overflow at ${width}`);layouts++;
  }
 }
 await openSettingsPage(page,'updates');await page.screenshot({path:'/tmp/settings-everyday-updates-desktop.png'});
 await page.setViewportSize({width:390,height:844});await openSettingsPage(page,'ai-connections');
 await page.locator('.a-settings-mobile-head button').first().click();await page.screenshot({path:'/tmp/settings-everyday-mobile-index.png'});
 await page.locator('[data-settings-section=privacy]').click();await page.screenshot({path:'/tmp/settings-everyday-mobile-privacy.png'});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({guidedProvider:true,privateDrafts:true,routingPreserved:true,catalogReviewAndRetry:true,installedVsReady:true,layouts,browserErrors:0}));
}catch(error){if(page)await page.screenshot({path:'/tmp/settings-everyday-failure.png'});throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
