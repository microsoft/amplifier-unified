import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import './composer-test-helpers.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:390,height:844},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);const composer=page.getByRole('textbox',{name:'Message Amplifier'}),options=page.getByRole('button',{name:'Chat options',exact:true}),menu=page.getByRole('dialog',{name:'Chat options',exact:true});
 await expect(options).toBeVisible();await composer.fill('Use my selected model');await options.click();await expect(menu).toBeVisible();
 await expect(menu.locator('#chat-provider')).toBeFocused();
 const bundle=menu.getByRole('combobox',{name:'Registered root bundles',exact:true});
 await bundle.selectOption('work');await menu.locator('#chat-model').selectOption('chosen-model');
 await menu.getByText('Advanced model options',{exact:true}).click();await menu.locator('#chat-effort').press('End');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.selection.effort)).toBe('high');
 await menu.getByRole('button',{name:'Done',exact:true}).click();await expect(composer).toHaveDraft('Use my selected model');
 await page.reload();await expect(options).toBeVisible();await options.click();await expect(menu.locator('#chat-model')).toHaveValue('chosen-model');await expect(bundle).toHaveValue('work');
 await expect(menu.getByRole('button',{name:'Refresh models',exact:true})).toBeHidden();
 await menu.getByRole('button',{name:'Done',exact:true}).click();
 // Resizing closes the narrow menu and restores independent controls.
 await options.click();await page.setViewportSize({width:1280,height:900});await expect(menu).toHaveCount(0);await expect(options).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Model and reasoning settings',exact:true})).toContainText('chosen-model');
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();await expect(page.getByRole('region',{name:'Conversation model',exact:true})).toBeVisible();
 await page.setViewportSize({width:320,height:700});await expect(page.getByRole('region',{name:'Conversation model',exact:true})).toHaveCount(0);
 await options.click();await expect(menu).toBeVisible();await page.screenshot({path:'/tmp/readiness107-options-mobile.png'});await menu.getByRole('button',{name:'Done',exact:true}).click();
 await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic first response',{exact:true}).waitFor();
 const data=await(await page.request.get(url+'/fixture')).json();assert.equal(data.sent.length,1);assert.equal(data.sent[0].selection.model,'chosen-model');assert.equal(data.sent[0].selection.effort,'high');
 for(const scheme of ['light','dark']){await page.evaluate(scheme=>window.amplifier.dispatch('view.update',{patch:{scheme}}),scheme);await expect(options).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:`/tmp/readiness107-composer-${scheme}.png`});}
 assert.deepEqual(errors,[]);console.log('PASS: one narrow Options menu; real model/bundle/effort selection, preserved draft and reload, viewport resize, exact first submission, light/dark 320px.');
}finally{await browser?.close();fixture.kill()}
