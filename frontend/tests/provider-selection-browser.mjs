// Real bundled UI -> creation -> synthetic provider dispatch through the native selector.
import {showComposerOption} from './composer-options-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
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
 const staticManifest=process.env.PROVIDER_SELECTION_STATIC_MANIFEST?JSON.parse(await readFile(process.env.PROVIDER_SELECTION_STATIC_MANIFEST,'utf8')):null;
 const consumedResponses=[];
 if(staticManifest)page.on('response',response=>{
  const address=new URL(response.url());
  if(address.origin!==new URL(url).origin)return;
  const path=address.pathname==='/'?'index.html':address.pathname.slice(1);
  const expected=staticManifest['amplifier_web/static/'+path];
  if(!expected)return;
  consumedResponses.push((async()=>{
   assert.equal(response.status(),200,path);
   const sha256=createHash('sha256').update(await response.body()).digest('hex');
   assert.equal(sha256,expected,path);
   return {path,sha256};
  })().catch(error=>({path,error:error.message})));
 });
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const requests=async()=>(await(await page.request.get(url+'/fixture/provider-resolution')).json()).requests;
 const model=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const titleFor=expected=>`Synthetic provider (${expected.instance}) · ${expected.model} (${expected.effort})`;
 const labelFor=expected=>`${expected.model} (${expected.effort})`;
 const beforeSends=[];
 async function assertDisplayed(expected,options={}){
  await expect(model).toHaveAttribute('title',titleFor(expected),options);
  await expect(model.locator(':scope > span')).toHaveText(labelFor(expected),options);
  const observed=await model.evaluate(node=>({title:node.title,label:node.querySelector('span').textContent}));
  assert.equal(observed.title,titleFor(expected));
  assert.equal(observed.label,labelFor(expected));
  return observed;
 }
 const inherited={instance:'cli',model:'cli-model',effort:'low'};
 let count=0;
 async function send(expected){
  await composer.fill('Synthetic first request '+count);
  // Inspect the rendered destination immediately before Enter, independently
  // of the provider's later complete-call recording.
  beforeSends.push({expected,display:await assertDisplayed(expected),callsBefore:(await requests()).length});
  await composer.press('Enter');
  await expect.poll(async()=>(await requests()).length).toBe(++count);
  const last=(await requests()).at(-1);assert.deepEqual(last.dispatch,expected);assert.deepEqual(last.effective,expected);
  await expect.poll(async()=>{const current=await state();return current.sessions.find(row=>row.id===last.sessionId)?.status}).toBe('idle');
  await expect(model.locator(':scope > span')).toHaveText(labelFor(expected));
  return last;
 }
 await page.goto(url);
 if(staticManifest){
  const expected=staticManifest,verified={};
  const assetUrls=await page.locator('script[src],link[rel="modulepreload"],link[rel="stylesheet"]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('src')||node.getAttribute('href')));
  for(const path of ['index.html','build.json',...assetUrls.map(path=>path.startsWith('/')?path.slice(1):path)]){
   const response=await page.request.get(url+'/'+path);
   assert.equal(response.status(),200,path);
   const sha=createHash('sha256').update(await response.body()).digest('hex');
   assert.equal(sha,expected['amplifier_web/static/'+path],path);
   verified[path]=sha;
  }
  await mkdir(artifacts+'/provider1201',{recursive:true});
  await writeFile(artifacts+'/provider1201/served-assets.json',JSON.stringify(verified,null,2));
 }
 await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 await assertDisplayed(inherited);
 // Negative control: the model/effort are identical but the connection is wrong.
 // The old /cli/ check accepted this label because cli-model also matched.
 const wrongTitle=titleFor({...inherited,instance:'unified'});
 const originalTitle=await model.getAttribute('title');
 await model.evaluate((node,title)=>{node.title=title},wrongTitle);
 try{
  assert.equal(await model.getAttribute('title'),wrongTitle);
  await assert.rejects(()=>assertDisplayed(inherited,{timeout:250}),/toHaveAttribute/);
 }finally{await model.evaluate((node,title)=>{node.title=title},originalTitle)}
 await assertDisplayed(inherited);
 await mkdir(artifacts+'/provider1201',{recursive:true});
 await writeFile(artifacts+'/provider1201/wrong-connection-negative.json',JSON.stringify({
  expected:titleFor(inherited),observed:wrongTitle,rejected:true,calls:(await requests()).length},null,2));
 // A deliberate draft choice uses exactly that connection/model/effort on first send.
 await model.click();await showComposerOption(page,'provider');await page.locator('select#chat-provider').selectOption('unified');
 await showComposerOption(page,'model');await page.locator('select#chat-model').selectOption('cli-model');
 await showComposerOption(page,'effort');
 const slider=page.getByRole('slider');await slider.focus();await slider.press('End');await slider.press('Tab');
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 const explicit={instance:'unified',model:'cli-model',effort:'xhigh'};
 await assertDisplayed(explicit);
 await expect.poll(async()=>(await state()).view.newSessionDraft.selection).toEqual(explicit);
 await page.reload();await assertDisplayed(explicit);
 assert.deepEqual((await send(explicit)).selection,explicit);
 // Existing-chat choice does not become a hidden new-chat default, including consecutive chats.
 for(let index=0;index<2;index++){
  await action('session.draft');await assertDisplayed(inherited);
  assert.ok(!(await send(inherited)).selection);
 }
 await action('session.draft');await assertDisplayed(inherited);
 const before=await state(),setup=before.view.newSessionDraft;
 await action('view.update',{patch:{newSessionDraft:{...setup,bundle:'alternate',selection:{}}}});
 // The alternate probe is deliberately held. A prior bundle must not masquerade as resolved.
 await expect(model.locator(':scope > span')).toHaveText('Loading model…');
 await expect(model).toHaveAttribute('title','Loading model… · Checking bundle settings…');
 await page.screenshot({path:artifacts+'/provider1201/desktop-loading.png'});
 assert.equal((await requests()).length,count);
 await page.request.post(url+'/fixture/provider-resolution/release');
 await assertDisplayed({instance:'unified',model:'unified-model',effort:'high'});
 await page.setViewportSize({width:390,height:844});
 await model.tap();await showComposerOption(page,'provider');
 await expect(page.locator('select#chat-provider')).toHaveValue('unified');
 await page.screenshot({path:artifacts+'/provider1201/narrow-provider.png'});
 await page.getByRole('button',{name:'Close chat options',exact:true}).tap();
 assert.ok(!(await send({instance:'unified',model:'unified-model',effort:'high'})).selection);
 await writeFile(artifacts+'/provider1201/dispatch.json',JSON.stringify(await requests(),null,2));
 await writeFile(artifacts+'/provider1201/before-sends.json',JSON.stringify(beforeSends,null,2));
 if(staticManifest){
  const consumed=await Promise.all(consumedResponses);
  assert.deepEqual(consumed.filter(row=>row.error),[]);
  assert.ok(consumed.some(row=>row.path==='index.html'));
  assert.ok(consumed.some(row=>row.path.startsWith('assets/app-')&&row.path.endsWith('.js')));
  await writeFile(artifacts+'/provider1201/consumed-assets.json',JSON.stringify(consumed,null,2));
 }
 assert.deepEqual(errors,[]);
 console.log('Provider selection passed: exact pre-send connection/model/effort; wrong-connection label rejected; explicit draft+effort+reload; consecutive inherited chats; delayed bundle truth; narrow touch; synthetic native-selector complete-call recording (not full prepare_manager).');
}finally{await browser?.close();fixture.kill();await new Promise(resolve=>{if(fixture.exitCode!==null||fixture.signalCode)resolve();else fixture.once('exit',resolve)});}
