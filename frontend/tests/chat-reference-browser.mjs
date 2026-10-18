// Real-host contract. Earlier failed runs remain evidence; run only in admitted isolation.
// Uses the real host/renderer. No mocked card, target or successful action.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {bounded,combinedFailure,observed,resultOf,withActionRoute} from './chat-reference-harness.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const evidence=process.env.CHAT_REFERENCE_EVIDENCE_DIR;
if(!evidence)throw Error('Set CHAT_REFERENCE_EVIDENCE_DIR inside the owned test instance');
await mkdir(evidence,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/chat_reference_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page,exit,primaryFailure;
const secondaryFailures=[],routeTrace=[];
const pendingActions=[];
const observe=promise=>{const outcome=observed(promise);pendingActions.push(outcome);return outcome};
const exited=new Promise(resolve=>fixture.once('exit',(code,signal)=>{exit={code,signal};resolve(exit)}));
const headers={Authorization:'Bearer fixture-browser-control-token'};
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{
   output+=chunk;
   for(const line of output.split('\n')){
    try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}
   }
  });
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({extraHTTPHeaders:headers,viewport:{width:1440,height:1000}});
 context.setDefaultTimeout(15000);context.setDefaultNavigationTimeout(15000);
 page=await context.newPage();
 const peer=await context.newPage(),errors=[],posts=[];
 let additionalPages=0;
 context.on('page',()=>additionalPages++);
 for(const p of [page,peer]){
  p.on('pageerror',error=>errors.push(error.message));
  p.on('request',request=>{
   if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')
    posts.push(request.postDataJSON());
  });
  // No Launch Handler dependency. A marker and timeOrigin reveal reloads.
  await p.addInitScript(()=>{
   Object.defineProperty(window,'launchQueue',{value:undefined,configurable:true});
   window.__chatReferenceDocument=crypto.randomUUID();
  });
 }
 const inspect=async()=>{const response=await page.request.get(url+'/fixture/chat-references');assert.ok(response.ok());return response.json()};
 const boot=await inspect(),{source,message,target,other}=boot;
 await Promise.all([page.goto(url),peer.goto(url)]);
 for(const p of [page,peer])await p.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 const act=async(p,name,args={})=>{
  let timer;
  try{return await Promise.race([
   p.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]),
   new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(`Action ${name} did not settle`)),20000)}),
  ])}finally{clearTimeout(timer)}
 };
 const selected=p=>p.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const composer=p=>p.getByRole('textbox',{name:'Message Amplifier'});
 const authoritative=async p=>{
  const id=await p.evaluate(()=>window.amplifier.getState().client.id);
  const response=await p.request.get(url+'/api/state',{headers:{'X-Amplifier-Client':id}});
  assert.ok(response.ok());return response.json();
 };
 const marker=p=>p.evaluate(()=>({document:window.__chatReferenceDocument,timeOrigin:performance.timeOrigin,client:window.amplifier.getState().client.id}));
 await act(page,'session.select',{id:source});await act(peer,'session.select',{id:other});
 const sourceArticle=page.locator(`[data-message-id="${message}"]`);
 await expect(sourceArticle).toContainText('Open the investigation chat');
 await composer(peer).fill('Private peer draft');
 const before=await marker(page),peerBefore=await marker(peer);
 // Proposed API spelling is a test-first implementation hypothesis.
 const attached=await act(page,'outputs.attachChatReference',{sessionId:source,messageId:message,targetSessionId:target});
 assert.equal(attached.accepted,true,'The real host must accept the typed reference; never inject a record');
 const card=()=>sourceArticle.getByRole('button',{name:/^Open (?:archived )?chat.*Investigation destination/});
 await expect(card()).toBeVisible();
 assert.equal(await selected(page),source,'Publication must not navigate');
 await page.screenshot({path:join(evidence,'card-wide.png')});

 async function assertNoExecution(){
  const after=await inspect();
  assert.deepEqual(after.effects,[]);
  assert.deepEqual(after.messages,boot.messages);
  assert.deepEqual(after.approvals,boot.approvals);
  const forbidden=/^(conversation\.(send|stop)|worker\.|approval\.respond|session\.(create|warm|restore|handoff\.(start|propose|confirm))|coordination\.(send|followup)|smartTools\.call)/;
  assert.deepEqual(posts.filter(row=>forbidden.test(row.action)),[]);
 }
 // Ordinary guarded selection and the card must retain the same draft/attachment.
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({
  name:'reference-note.txt',mimeType:'text/plain',buffer:Buffer.from('Exact fixture attachment'),
 });
 await page.getByRole('button',{name:'Remove reference-note.txt'}).waitFor();
 let releaseDraft,draftHeld=false;
 const draftGate=new Promise(resolve=>{releaseDraft=resolve});
 const holdDraft=async route=>{
  const request=route.request(),body=request.method()==='POST'?request.postDataJSON():{};
  if(body.action==='view.update'&&body.args.sessionId===source&&
     body.args.patch?.draft==='Source draft before debounced save'){
   draftHeld=true;await draftGate;
  }
  await route.continue();
 };
 await withActionRoute(page,{label:'held-draft',handle:holdDraft,release:releaseDraft,trace:routeTrace},async()=>{
 if(process.env.CHAT_REFERENCE_NEGATIVE_RELOAD==='1'){
  // Deliberately replace one genuine click with the reported bad behavior.
  // This run MUST fail the unchanged-document/client assertion, not turn green.
  await card().evaluate((node,target)=>{
   node.addEventListener('click',event=>{
    event.preventDefault();event.stopImmediatePropagation();
    location.assign('/?chat='+encodeURIComponent(target));
   },{capture:true,once:true});
  },target);
 }
  await composer(page).fill('Source draft before debounced save');
  await expect.poll(()=>draftHeld).toBe(true);
  assert.notEqual((await authoritative(page)).view.draft,'Source draft before debounced save');
  await card().click();
 });
 await expect.poll(async()=>(await authoritative(page)).selectedSessionId).toBe(target);
 // Keep the deliberately forced reload control's first discriminating failure
 // at document/client identity, not a later destination-focus assertion.
 assert.deepEqual(await marker(page),before);
 await expect(page.locator(`[data-part="messages"][data-session-id="${target}"]`)).toBeFocused();
 assert.equal(context.pages().length,2,'Click must not open a tab/window');
 assert.equal(additionalPages,0,'Even a short-lived popup must be detected');
 assert.equal(await selected(peer),other);
 assert.equal(await readComposerDraft(composer(peer)),'Private peer draft');
 assert.deepEqual(await marker(peer),peerBefore);
 await act(page,'session.select',{id:source});
 await expect(composer(page)).toHaveDraft('Source draft before debounced save');
 await expect(page.getByRole('button',{name:'Remove reference-note.txt'})).toBeVisible();
 await assertNoExecution();

 // A mounted dirty Canvas must survive both the card and ordinary selection.
 await act(page,'canvas.show',{kind:'markdown',title:'Retained editor',content:'# Original'});
 const manifest={id:'example.reference-editor',label:'Reference test editor',version:'1.0.0',
  apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',
  resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const editorSource='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("input",{"aria-label":"Reference unsaved edit","data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}})}';
 const digest=(await act(page,'shell.packages.stage',{manifest,source:editorSource})).result.digest;
 assert.equal((await act(page,'shell.packages.validate',{digest})).result.status,'passed');
 await act(page,'view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const editor=page.getByLabel('Reference unsaved edit'),text='Keep exact unsaved Canvas bytes';
 await editor.fill(text);
 const views=async()=>(await act(page,'canvas.views.inspect')).result.views;
 await expect.poll(async()=>(await views()).find(v=>v.viewId==='primary')?.dirty).toBe(true);
 const mount=await editor.getAttribute('data-mount');
 await card().click();
 await expect(page.getByText(/Finish or cancel the primary viewer edit/).first()).toBeVisible();
 assert.equal((await authoritative(page)).selectedSessionId,source);
 await expect(editor).toHaveValue(text);assert.equal(await editor.getAttribute('data-mount'),mount);
 await assert.rejects(()=>act(page,'session.select',{id:target}),/viewer edit/);
 const primary=(await views()).find(v=>v.viewId==='primary');
 await act(page,'canvas.views.recover',Object.fromEntries(
  ['viewId','resourceId','resourceRevision','generation'].map(key=>[key,primary[key]])));
 await act(page,'canvas.close');

 // Settings in Canvas keeps the card reachable. Cancel uses the existing guard.
 await act(page,'canvas.show',{kind:'settings'});
 await openSettingsPage(page,'providers');
 await page.locator('[data-part=provider-settings] [data-collection-id=one] button').click();
 const providerId=page.locator('#provider-id');
 await providerId.fill('unsaved-reference-fixture');
 await card().click();
 const guard=page.getByRole('alertdialog');
 await expect(guard).toBeVisible();
 await guard.getByRole('button',{name:'Keep settings open',exact:true}).click();
 assert.equal((await authoritative(page)).selectedSessionId,source);
 await expect(providerId).toHaveValue('unsaved-reference-fixture');
 await expect(card()).toBeFocused();
 // Compare ordinary selection: same dialog, same cancel, no discard.
 const pendingSelection=observe(act(page,'session.select',{id:target}));
 await expect(guard).toBeVisible();
 await guard.getByRole('button',{name:'Keep settings open',exact:true}).click();
 assert.equal((await resultOf(pendingSelection)).accepted,false);
 await expect(providerId).toHaveValue('unsaved-reference-fixture');
 // Selecting another artifact is also an existing guarded Settings exit.
 const exitSettings=observe(act(page,'canvas.show',{kind:'text',title:'After Settings',content:'Retained'}));
 await expect(guard).toBeVisible();
 await guard.getByRole('button',{name:'Discard changes',exact:true}).click();
 assert.equal((await resultOf(exitSettings)).accepted,true);
 await act(page,'canvas.close');

 // Keyboard and narrow layout must exercise the actual control, not its API.
 await page.setViewportSize({width:390,height:844});
 await expect(card()).toBeVisible();
 const bounds=await card().boundingBox();assert.ok(bounds&&bounds.height>=44);
 assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390);
 await card().focus();await expect(card()).toBeFocused();await card().press('Enter');
 await expect.poll(async()=>(await authoritative(page)).selectedSessionId).toBe(target);
 assert.deepEqual(await marker(page),before);
 await act(page,'session.select',{id:source});
 await card().focus();await card().press('Space');
 await expect.poll(async()=>(await authoritative(page)).selectedSessionId).toBe(target);
 await act(page,'session.select',{id:source});
 await page.screenshot({path:join(evidence,'card-mobile.png')});
 // Reload is explicit here, not part of card activation. Its saved association survives.
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 await expect(card()).toBeVisible();
 await expect(composer(page)).toHaveDraft('Source draft before debounced save');
 await expect(page.getByRole('button',{name:'Remove reference-note.txt'})).toBeVisible();
 const reloaded=await marker(page);assert.notEqual(reloaded.document,before.document);
 await card().click();await expect.poll(async()=>(await authoritative(page)).selectedSessionId).toBe(target);
 assert.deepEqual(await marker(page),reloaded);
 await act(page,'session.select',{id:source});
 await act(page,'session.rename',{id:target,title:'Renamed investigation destination'});
 const renamed=sourceArticle.getByRole('button',{name:/^Open chat.*Renamed investigation destination/});
 await expect(renamed).toBeVisible();
 await renamed.click();await expect.poll(async()=>(await authoritative(page)).selectedSessionId).toBe(target);
 await act(page,'session.select',{id:source});
 // Queue an earlier reference behind an existing dirty-report barrier, then a
 // newer ordinary selection. The older card must not overtake that intent.
 let releaseBarrier,barrierHeld=false;
 const barrier=new Promise(resolve=>{releaseBarrier=resolve});
 const holdBarrier=async route=>{
  const body=route.request().postDataJSON();
  if(body.action==='canvas.views.dirty'){barrierHeld=true;await barrier}
  await route.continue();
 };
 await act(page,'canvas.show',{kind:'markdown',title:'Navigation order',content:'# Safe'});
 const orderView=(await act(page,'canvas.views.inspect')).result.views.find(v=>v.viewId==='primary');
 await withActionRoute(page,{label:'navigation-barrier',handle:holdBarrier,release:releaseBarrier,trace:routeTrace},async()=>{
 const pendingBarrier=observe(act(page,'canvas.views.dirty',{...Object.fromEntries(
  ['viewId','resourceId','resourceRevision','generation'].map(key=>[key,orderView[key]])),dirty:false}));
 let newer,barrierFailure;
 try{
  await expect.poll(()=>barrierHeld).toBe(true);
  const beforePosts=posts.length;
  await renamed.click();
  newer=observe(bounded(page.evaluate(id=>{
   const result=window.amplifier.dispatch('session.select',{id});
   window.__referenceLaterSelectionStarted=true;
   return result;
  },other),20000,'Later navigation did not settle'));
  await page.waitForFunction(()=>window.__referenceLaterSelectionStarted===true);
  releaseBarrier();await resultOf(pendingBarrier);await resultOf(newer);
  await expect.poll(async()=>(await authoritative(page)).selectedSessionId).toBe(other);
  assert.equal(posts.slice(beforePosts).filter(row=>row.action==='session.select'&&row.args?.chatReference).length,0);
 }catch(error){barrierFailure=error}
 releaseBarrier();
 const pendingFailures=(await Promise.all([pendingBarrier,...(newer?[newer]:[])])).flatMap(row=>row.error?[row.error]:[]);
 const failure=combinedFailure(barrierFailure,pendingFailures);if(failure)throw failure;
 });
 await act(page,'session.select',{id:source});
 await act(page,'canvas.close');
 let uncertainPosts=0;
 const loseReference=async route=>{
  const body=route.request().postDataJSON();
  if(body.action==='session.select'&&body.args.chatReference){
   uncertainPosts++;await route.abort('failed');return;
  }
  await route.continue();
 };
 await withActionRoute(page,{label:'uncertain-selection',handle:loseReference,trace:routeTrace},async()=>{
  await renamed.click();
  await expect(sourceArticle.getByRole('alert')).toContainText('connection');
  await expect(renamed).not.toHaveAttribute('aria-busy','true');
  await renamed.click();
  await expect(sourceArticle.getByRole('alert')).toContainText('will not be replayed');
  assert.equal(uncertainPosts,1,'Unknown navigation is not retried by another click');
  assert.equal((await authoritative(page)).selectedSessionId,source);
 });
 await assertNoExecution();assert.deepEqual(errors,[]);assert.equal(additionalPages,0);
}catch(error){
 primaryFailure=error;
 if(page&&!page.isClosed()){
  try{await page.screenshot({path:join(evidence,'failure.png')})}
  catch(captureError){secondaryFailures.push(captureError)}
 }
}finally{
 try{await browser?.close()}catch(error){secondaryFailures.push(error)}
 try{
  const results=await bounded(Promise.all(pendingActions),10000,'Pending browser actions did not settle');
  secondaryFailures.push(...results.flatMap(result=>result.error?[result.error]:[]));
 }catch(error){secondaryFailures.push(error)}
 try{
  if(!exit)fixture.kill('SIGTERM');
  const waitExit=milliseconds=>bounded(exited,milliseconds,'Owned fixture did not terminate');
  try{
   const result=await waitExit(10000);
   assert.equal(result.code,0,'Fixture cleanup must exit successfully');
   assert.equal(result.signal,null,'Fixture must exit gracefully, not by signal');
  }catch(error){
   secondaryFailures.push(error);
   if(!exit){
    fixture.kill('SIGKILL');
    try{await waitExit(5000)}catch(killError){secondaryFailures.push(killError)}
   }
  }
 }catch(error){secondaryFailures.push(error)}
 try{await writeFile(join(evidence,'route-lifecycle.json'),JSON.stringify(routeTrace,null,2)+'\n')}
 catch(error){secondaryFailures.push(error)}
 const describe=error=>error?{name:error.name,message:error.message,stack:error.stack,
  ...(error instanceof AggregateError?{errors:error.errors.map(child=>({name:child.name,message:child.message,stack:child.stack}))}:{})}:null;
 try{await writeFile(join(evidence,'outcome.json'),JSON.stringify({
  primary:describe(primaryFailure),secondary:secondaryFailures.map(describe),fixtureExit:exit??null,
 },null,2)+'\n')}catch(error){secondaryFailures.push(error)}
}
const failure=combinedFailure(primaryFailure,secondaryFailures);
if(failure)throw failure;
console.log('Chat-reference source-browser checks passed: exact same-document selection, client isolation, draft/attachment retention, Canvas/Settings guards, keyboard/mobile and saved reload; graceful fixture cleanup.');