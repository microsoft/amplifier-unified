import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/session_health_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Multiple-window stream fixture'});
let browser,primaryFailure;
const image='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9mQAAAAASUVORK5CYII=';
try{
 const {url,sessionId}=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const pages=[],errors=[],mutations=[];
 const observe=page=>{
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
   const raw=request.postDataJSON(),body=raw.action==='shell.command'?raw.args:raw;
   if(['conversation.send','conversation.retry','message.edit','session.recover','session.create','session.select'].includes(body.action))mutations.push(body.action);
  });
 };
 const failureState=page=>page.evaluate(id=>{
  const session=window.amplifier.getState().sessions.find(row=>row.id===id);
  return {id:session?.id,status:session?.status,error:session?.error,failure:session?.failure,
   messages:session?.messages?.map(({id,role,text,inputId,partial})=>({id,role,text,inputId,partial}))};
 },sessionId);
 const errorDetails=async page=>{
  await page.waitForFunction(id=>{const state=window.amplifier?.getState();return state?.selectedSessionId===id&&state.sessions.find(row=>row.id===id)?.status==='error'},sessionId);
  const saved=await failureState(page);
  assert.equal(saved.error,'RuntimeError: Manager turn failed; no automatic replay');
  assert.equal(saved.failure.generationId,'image-generation');
  assert.equal(saved.failure.errorType,'RuntimeError');
  assert.equal(saved.failure.recordedAt,1789926000);
  const disclosure=page.locator('[data-part="conversation-trouble"] details').filter({hasText:'Details & reporting'});
  await expect(disclosure).toBeVisible();
  if(!await disclosure.evaluate(node=>node.open))await disclosure.locator('summary').click();
  await disclosure.getByRole('button',{name:'Technical details',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'Error details',exact:true});
  await expect(panel).toBeVisible();
  await panel.getByText('Technical details',{exact:true}).click();
  await expect(panel).toContainText(saved.error);
  await expect(panel).toContainText('image-generation');
  await panel.getByRole('button',{name:'Close panel',exact:true}).click();
  await expect(panel).toHaveCount(0);
  return saved;
 };
 let original;
 for(let i=0;i<8;i++){
  const page=await context.newPage();pages.push(page);observe(page);
  await page.goto(url,{waitUntil:'domcontentloaded'});await showFixtureConversation(page);
  const saved=await errorDetails(page);
  if(i===0)original=saved;else assert.deepEqual(saved,original);
  await expect(page.getByText('Connected',{exact:true})).toHaveCount(1);
 }
 const identities=await Promise.all(pages.map(p=>p.evaluate(()=>window.amplifier.shellClientId)));
 assert.equal(new Set(identities).size,8);
 const results=[];
 for(const page of pages){
  results.push(await page.evaluate(async()=>{const started=performance.now();const result=await fetch('/api/health',{signal:AbortSignal.timeout(3000)});await window.amplifier.dispatch('view.update',{patch:{navWidth:290}});return {status:result.status,ms:performance.now()-started}}));
 }
 await pages[0].getByRole('textbox',{name:'Message Amplifier'}).fill('Private first-window draft');
 assert.equal(await readComposerDraft(pages[7].getByRole('textbox',{name:'Message Amplifier'})),'');
 const loaded=await pages[7].evaluate(async image=>{
  await window.amplifier.dispatch('canvas.show',{kind:'image',title:'Saved test image',content:image});
  const views=(await window.amplifier.dispatch('canvas.views.inspect')).result.views;
  const target=views.find(v=>v.viewId==='primary');
  const args=Object.fromEntries(['resourceId','resourceRevision','generation'].map(k=>[k,target[k]]));args.clientId=window.amplifier.shellClientId;
  const response=await fetch('/api/canvas/views/primary/resource?'+new URLSearchParams(args),{signal:AbortSignal.timeout(3000)});
  return {status:response.status,canvas:await response.json()};
 },image);
 assert.equal(loaded.status,200);assert.equal(loaded.canvas.content,image);
 assert.equal(await readComposerDraft(pages[0].getByRole('textbox',{name:'Message Amplifier'})),'Private first-window draft');
 await pages[0].close();await pages[7].reload();await showFixtureConversation(pages[7]);
 assert.deepEqual(await errorDetails(pages[7]),original);
 const retry=await context.newPage();observe(retry);let first=true;
 await retry.routeWebSocket('**/api/events/ws?**',socket=>{if(first){first=false;socket.close()}else socket.connectToServer()});
 await retry.goto(url);await retry.getByRole('button',{name:'Retry connection',exact:true}).waitFor({timeout:15000});
 await retry.getByRole('button',{name:'Retry connection',exact:true}).click();
 // This fresh client arrives at Home, just like the initial eight windows.
 await showFixtureConversation(retry);
 await expect(retry.getByText('Connected',{exact:true})).toHaveCount(1);
 assert.deepEqual(await errorDetails(retry),original);
 assert.equal(await readComposerDraft(retry.getByRole('textbox',{name:'Message Amplifier'})),'');
 for(const page of pages.slice(1))assert.deepEqual(await failureState(page),original);
 const retained=await pages[7].request.get(url+'/fixture/check').then(response=>response.json());
 assert.equal(retained.originalUnchanged,true);assert.equal(retained.namingCalls,0);
 assert.deepEqual(mutations,[]);
 assert.deepEqual(errors,[]);console.log(JSON.stringify({windows:8,privateClients:true,imageLoaded:true,reconnected:true,startupRetry:true,requests:results}));
}catch(error){primaryFailure=error;throw error}
finally{await finishFixture({label:'Multiple-window stream fixture',close:[['browser',()=>browser?.close()]],stop:stopFixture,originalError:primaryFailure})}
