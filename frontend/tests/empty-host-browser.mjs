import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  const timeout=setTimeout(()=>reject(Error('Empty host startup timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Empty host exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timeout);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const bootError=new Promise((_,reject)=>page.once('pageerror',reject));
 await page.goto(url);
 await Promise.race([page.getByRole('textbox',{name:'Message Amplifier'}).waitFor(),bootError]);
 const initial=await page.evaluate(()=>window.amplifier.getState());
 assert.equal(initial.sessions.length,0);
 assert.ok(!initial.selectedSessionId);
 await expect(page.getByRole('heading',{name:'What shall we work on?'})).toBeVisible();
 assert.equal(await page.locator('.a-message').count(),0);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await expect(composer).toBeEditable();
 await expect(page.getByText('Sending message…',{exact:true})).toHaveCount(0);
 // Pause before sending so the empty composer's debounced autosave really runs.
 const autosaved=page.waitForResponse(response=>{
  const request=response.request();
  return new URL(response.url()).pathname==='/api/actions'&&request.method()==='POST'&&
   request.postDataJSON()?.action==='view.update'&&Object.hasOwn(request.postDataJSON()?.args?.patch||{},'draft');
 });
 await composer.fill('Unsent before any conversation');
 const saved=await autosaved;
 assert.equal(saved.status(),200,await saved.text());
 await expect(page.getByRole('alert')).toHaveCount(0);
 assert.equal((await page.evaluate(()=>window.amplifier.getState())).sessions.length,0);
 await page.reload();
 await expect(composer).toHaveValue('Unsent before any conversation');
 assert.ok(!(await page.evaluate(()=>window.amplifier.getState())).selectedSessionId);
 // The first send creates its conversation. Hold its HTTP acknowledgement to
 // exercise the genuine pending-send state as well as the empty idle state.
 let release;
 const held=new Promise(resolve=>{release=resolve});
 await page.route('**/api/actions',async route=>{
  if(route.request().method()==='POST'&&route.request().postDataJSON()?.action==='conversation.send')await held;
  await route.continue();
 });
 await composer.fill('First input on an empty host');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Sending message…',{exact:true})).toBeVisible();
 await expect(composer).toBeEditable();
 await expect(composer).toHaveValue('');
 await composer.fill('Next draft while the first delivery is pending');
 release();
 await page.getByText('Synthetic first response',{exact:true}).waitFor();
 await expect(composer).toHaveValue('Next draft while the first delivery is pending');
 await expect(composer).toBeEditable();
 await expect(page.getByText('Sending message…',{exact:true})).toHaveCount(0);
 assert.equal(await page.locator('.a-message.a-user').count(),1);
 const sent=await (await page.request.get(url+'/fixture')).json();
 assert.equal(sent.sent.length,1);
 assert.equal(sent.sent[0].text,'First input on an empty host');
 await page.waitForFunction(()=>window.amplifier.getState().sessions.length===1&&window.amplifier.getState().selectedSessionId);
 const selected=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('An unsent first-chat draft');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='An unsent first-chat draft');
 await page.reload();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('An unsent first-chat draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
 assert.equal(await page.locator('.a-message.a-user').count(),1);
 assert.deepEqual(errors,[]);
 console.log('Empty host passed: debounced draft before any conversation, no validation errors, draft survives reload, first send creates one chat, pending send feedback, draft and selection after reload; synthetic runtime only.');
}finally{await browser?.close();fixture.kill();}
