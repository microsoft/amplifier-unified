// Real bundled UI -> creation -> synthetic provider dispatch through the native selector.
import {showComposerOption} from './composer-options-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';
const fixturePath=process.env.PROVIDER_SELECTION_FIXTURE||fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fixturePath,'--chat-controls','--provider-resolution'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const context=await browser.newContext({viewport:{width:1280,height:900},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const requests=async()=>(await(await page.request.get(url+'/fixture/provider-resolution')).json()).requests;
 const model=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 let count=0;
 async function send(expected){
  await composer.fill('Synthetic first request '+count);await composer.press('Enter');
  await expect.poll(async()=>(await requests()).length).toBe(++count);
  const last=(await requests()).at(-1);assert.deepEqual(last.dispatch,expected);assert.deepEqual(last.effective,expected);
  await expect.poll(async()=>{const current=await state();return current.sessions.find(row=>row.id===last.sessionId)?.status}).toBe('idle');
  await expect(model).toContainText(`${expected.model} (${expected.effort})`);
  await expect(model).toHaveAttribute('title',new RegExp(expected.instance));
  return last;
 }
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 await expect(model).toContainText('cli-model');await expect(model).toHaveAttribute('title',/cli/);
 // A deliberate draft choice uses exactly that connection/model/effort on first send.
 await model.click();await showComposerOption(page,'provider');await page.locator('select#chat-provider').selectOption('unified');
 await showComposerOption(page,'model');await page.locator('select#chat-model').selectOption('cli-model');
 await showComposerOption(page,'effort');
 const slider=page.getByRole('slider');await slider.focus();await slider.press('End');await slider.press('Tab');
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await expect(model).toContainText('cli-model (xhigh)');await expect(model).toHaveAttribute('title',/unified/);
 const explicit={instance:'unified',model:'cli-model',effort:'xhigh'};
 await expect.poll(async()=>(await state()).view.newSessionDraft.selection).toEqual(explicit);
 await page.reload();await expect(model).toContainText('cli-model (xhigh)');
 assert.deepEqual((await send(explicit)).selection,explicit);
 // Existing-chat choice does not become a hidden new-chat default, including consecutive chats.
 for(let index=0;index<2;index++){
  await action('session.draft');await expect(model).toContainText('cli-model');await expect(model).toHaveAttribute('title',/cli/);
  assert.ok(!(await send({instance:'cli',model:'cli-model',effort:'low'})).selection);
 }
 await action('session.draft');await expect(model).toContainText('cli-model');
 const before=await state(),setup=before.view.newSessionDraft;
 await action('view.update',{patch:{newSessionDraft:{...setup,bundle:'alternate',selection:{}}}});
 // The alternate probe is deliberately held. A prior bundle must not masquerade as resolved.
 await expect(model).toContainText('Loading model');
 await mkdir(artifacts+'/provider1201',{recursive:true});
 await page.screenshot({path:artifacts+'/provider1201/desktop-loading.png'});
 assert.equal((await requests()).length,count);
 await page.request.post(url+'/fixture/provider-resolution/release');
 await expect(model).toContainText('unified-model');
 await page.setViewportSize({width:390,height:844});
 await model.tap();await showComposerOption(page,'provider');
 await expect(page.locator('select#chat-provider')).toHaveValue('unified');
 await page.screenshot({path:artifacts+'/provider1201/narrow-provider.png'});
 await page.getByRole('button',{name:'Close chat options',exact:true}).tap();
 assert.ok(!(await send({instance:'unified',model:'unified-model',effort:'high'})).selection);
 await writeFile(artifacts+'/provider1201/dispatch.json',JSON.stringify(await requests(),null,2));
 assert.deepEqual(errors,[]);
 console.log('Provider selection passed: explicit draft+effort+reload; consecutive inherited chats; delayed bundle truth; narrow touch; native selector recording.');
}finally{await browser?.close();fixture.kill();await new Promise(resolve=>{if(fixture.exitCode!==null||fixture.signalCode)resolve();else fixture.once('exit',resolve)});}
