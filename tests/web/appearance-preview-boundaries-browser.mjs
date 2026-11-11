// A Reference delta preview is a private composition overlay, not Apply.
// Saving an editor draft adds a library ID; selection remains a separate intent.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Appearance preview boundaries fixture'});
let browser,failure;
const state=page=>page.evaluate(()=>window.amplifier.getState());
const action=(page,name,args={})=>page.evaluate(async([name,args])=>{
 const receipt=await window.amplifier.dispatch(name,args);
 if(receipt.accepted===false)throw Error(receipt.error||name+' rejected');
 return receipt;
},[name,args]);
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);await action(page,'session.create');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Retain this conversation draft.');
 const selected=(await state(page)).selectedSessionId;
 const initial=(await state(page)).appearance.theme;
 await openSettingsPage(page,'custom-appearance');
 await page.getByText('Advanced customization',{exact:true}).click();
 const fields={name:page.locator('#theme-name'),css:page.locator('#theme-css'),description:page.locator('#theme-description')};
 const draft={name:'Private editor A',css:'#amp-one { --a-accent: #8855aa; }',description:'Private browser draft'};
 for(const [key,field] of Object.entries(fields))await field.fill(draft[key]);
 const namespace=(await state(page)).appearance.namespace;
 const storageKey='amplifier.appearance-editor.v2:'+namespace;
 const retained=await page.evaluate(namespace=>JSON.parse(sessionStorage.getItem(namespace)),storageKey);
 assert.equal(retained.version,2);assert.equal(retained.draft.name,draft.name);
 assert.equal(retained.draft.css,draft.css);
 await page.getByRole('button',{name:'Preview draft',exact:true}).click();
 await expect.poll(async()=>(await state(page)).appearance.preview?.theme?.name).toBe(draft.name);
 await expect.poll(()=>page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim())).toBe('#8855aa');
 assert.deepEqual((await state(page)).appearance.theme,initial,'Preview does not apply or select a library item.');
 assert.equal(JSON.stringify((await state(page)).view).includes(draft.css),false,'Draft CSS is not projected into view state.');
 await page.locator('.a-appearance-preview').getByRole('button',{name:'Cancel preview',exact:true}).click();
 await expect.poll(async()=>(await state(page)).appearance.preview).toBeFalsy();
 for(const [key,field] of Object.entries(fields))await expect(field).toHaveValue(draft[key]);
 assert.deepEqual(await page.evaluate(namespace=>JSON.parse(sessionStorage.getItem(namespace)),storageKey),retained,
  'Canceling preview cannot clear or rewrite the dirty draft.');

 const previousClient=(await state(page)).client.id;
 await page.reload();await showFixtureConversation(page);
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 assert.notEqual((await state(page)).client.id,previousClient);
 await expect.poll(async()=>(await state(page)).appearance.preview).toBeFalsy();
 assert.deepEqual((await state(page)).appearance.theme,initial,'Reload returns to the shared selected appearance.');
 await openSettingsPage(page,'custom-appearance');
 if(!await fields.css.isVisible())await page.getByText('Advanced customization',{exact:true}).click();
 for(const [key,field] of Object.entries(fields))await expect(field).toHaveValue(draft[key]);
 assert.deepEqual(await page.evaluate(namespace=>JSON.parse(sessionStorage.getItem(namespace)),storageKey),retained,
  'A new shell client restores the browser-tab draft without publishing it.');

 await page.getByRole('button',{name:'Save as copy',exact:true}).click();
 await expect(page.locator('.a-appearance-notice')).toContainText('Select it to use the saved version.');
 await expect.poll(async()=>(await state(page)).appearance.theme).toEqual(initial);
 const saved=(await action(page,'theme.list')).result.items.find(row=>row.source==='saved'&&row.name===draft.name);
 assert.ok(saved);
 await page.getByRole('button',{name:'Use '+draft.name,exact:true}).click();
 await expect.poll(async()=>(await state(page)).appearance.theme.id).toBe(saved.id);
 await expect.poll(async()=>(await state(page)).appearance.theme.name).toBe(draft.name);
 await expect.poll(()=>page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim())).toBe('#8855aa');
 assert.equal((await state(page)).selectedSessionId,selected);
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 await showFixtureConversation(page);
 await expect(composer).toHaveDraft('Retain this conversation draft.');
 assert.deepEqual(errors,[]);
 console.log('Appearance preview boundaries passed: preview cancellation/reload preserve the private draft, and only a saved library ID plus explicit selection changes the shared appearance.');
}catch(error){failure=error;throw error}
finally{
 await finishFixture({label:'Appearance preview boundaries fixture',
  close:[['Appearance preview boundaries browser',async()=>{await browser?.close()}]],stop,originalError:failure});
}