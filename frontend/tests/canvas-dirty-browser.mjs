import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

// Production assets/host, isolated fixture only. No fake DOM or provider calls.
const root=fileURLToPath(new URL('../../',import.meta.url));
const base=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks');
await mkdir(base,{recursive:true});
const output=await mkdtemp(join(base,'canvas-dirty-'));
const report={status:'running',checks:[],actions:[],http:[],browserErrors:[],screenshots:[],evidenceErrors:[],expectedRejections:[],
 limits:['Synthetic runtime and staged test renderer; no provider calls.','Saved screenshots/trace do not establish pixel review.']};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,context,page,failure,stdout='',stderr='',releaseDirty,phase='exercise';
const pendingReceipts=[],unwrap=body=>body?.action==='shell.command'?(body.args.command||body.args):body;
const isAction=request=>request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions';
const errorText=error=>error?.stack||String(error);
fixture.stderr.on('data',chunk=>stderr+=chunk);
async function capture(name){
 const file=name+'.png';await page.screenshot({path:join(output,file)});
 report.screenshots.push({file,visualReview:'not-performed'});
}
try {
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{stdout+=chunk;for(const line of stdout.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 context=await browser.newContext({viewport:{width:1500,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});
 page=await context.newPage();page.setDefaultTimeout(8000);
 page.on('pageerror',error=>report.browserErrors.push(error.message));
 page.on('response',response=>{
  if(!isAction(response.request()))return;
  const row={phase,request:unwrap(response.request().postDataJSON()),status:response.status()};report.http.push(row);
  pendingReceipts.push(response.json().then(body=>{row.body=body},error=>{row.readError=errorText(error)}));
 });
 page.on('requestfailed',request=>{if(isAction(request))report.http.push({phase,request:unwrap(request.postDataJSON()),transportError:request.failure()})});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=async(name,args={})=>{
  const row={name,args};report.actions.push(row);
  try{
   row.receipt=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
   assert.equal(row.receipt?.accepted,true,name+': explicit accepted receipt required');
   return row.receipt;
  }catch(error){row.error=errorText(error);throw error}
 };
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const inspect=async()=>(await action('canvas.views.inspect')).result.views;
 const view=async id=>(await inspect()).find(v=>v.viewId===id);
 const target=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(k=>[k,v[k]]));
 await action('session.create');const otherSession=(await state()).selectedSessionId;
 await action('session.create');const session=(await state()).selectedSessionId;
 await action('view.update',{patch:{canvasWidth:720,canvasOverviewWidth:720,canvasControlsPinned:false,canvasControlsExpanded:false,draft:'Keep composer target and draft'}});
 await action('canvas.show',{kind:'markdown',title:'Other artifact',content:'# Other source'});
 const otherArtifact=await view('primary');
 await action('canvas.show',{kind:'markdown',title:'Editable artifact',content:'# Original retained source'});
 const first=await view('primary');
 const panel=page.locator('#workspace-canvas'),overview=page.getByRole('region',{name:'Chat overview',exact:true});
 const overviewTab=panel.getByRole('tab',{name:'Chat overview',exact:true}),editableTab=panel.getByRole('tab',{name:'Editable artifact',exact:true});
 const toggle=page.locator('#canvas-toggle');
 const manifest={id:'example.dirty-editor',label:'Unsaved editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 // The edit deliberately lives only in React state, exercising the public dirty contract.
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("label",null,"Unsaved renderer edit",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
 assert.equal((await action('shell.packages.validate',{digest})).result.status,'passed');
 // Primary edits block all transitions that would replace the primary mount.
 await panel.getByRole('button',{name:'Canvas details',exact:true}).click();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const primary=page.locator('[data-canvas-view="primary"]').getByLabel('Unsaved renderer edit');
 // Slow the dirty declaration: the separate navigation queue must not overtake it.
 let dirtyHeld=false;
 const dirtyGate=new Promise(resolve=>{releaseDirty=resolve});
 const holdDirty=async route=>{
  const request=route.request(),body=request.method()==='POST'?unwrap(request.postDataJSON()):{};
  if(body.action==='canvas.views.dirty'&&body.args.dirty){dirtyHeld=true;await dirtyGate}
  await route.continue();
 };
 await page.route('**/api/actions',holdDirty);
 await primary.fill('PRIMARY UNSAVED EDIT');
 await expect.poll(()=>dirtyHeld).toBe(true);
 const overtaken=page.waitForRequest(request=>isAction(request)&&unwrap(request.postDataJSON()).action==='session.select',{timeout:250}).then(()=>true,()=>false);
 const navigation=action('session.select',{id:otherSession}).then(()=>({accepted:true}),error=>({error:error.message}));
 try{assert.equal(await overtaken,false,'Navigation must wait for the preceding dirty declaration')}finally{releaseDirty()}
 const blockedNavigation=await navigation;assert.match(blockedNavigation.error,/viewer edit/);
 report.expectedRejections.push({name:'delayed session.select',...blockedNavigation});
 report.checks.push({name:'delayed dirty declaration precedes navigation',status:'passed'});
 await page.unroute('**/api/actions',holdDirty);
 await expect.poll(async()=>(await view('primary')).dirty).toBe(true);
 const before=(await inspect()).map(target);
 const mount=await primary.getAttribute('data-mount');assert.ok(mount);
 const retained=async(hidden=false)=>{
  await expect(primary).toHaveCount(1);
  await expect(primary).toHaveValue('PRIMARY UNSAVED EDIT');
  assert.equal(await primary.getAttribute('data-mount'),mount,'Renderer must not remount');
  assert.deepEqual((await inspect()).map(target),before);
  assert.equal((await view('primary')).dirty,true);
  assert.equal((await state()).selectedSessionId,session);
  assert.equal((await state()).view.draft,'Keep composer target and draft');
  if(hidden){
   await expect(primary).toBeHidden();
   assert.equal(await primary.evaluate(el=>el.isConnected&&el.closest('.a-canvas-workspace').inert),true);
   await expect(overviewTab).toHaveAttribute('aria-selected','true');await expect(overview).toBeVisible();
  }else await expect(primary).toBeVisible();
 };
 // Browsing retains the dirty mount; explicitly reopening the same chat must
 // neither block nor discard its edit just because return shortcuts are gone.
 await action('view.update',{patch:{workSurface:'workspaces'}});
 await expect(primary).toBeHidden();
 assert.equal((await state()).selectedSessionId,session);
 assert.equal((await view('primary')).dirty,true);
 await action('session.select',{id:session});
 await expect(primary).toBeVisible();await expect(primary).toHaveValue('PRIMARY UNSAVED EDIT');
 assert.deepEqual((await inspect()).map(target),before);
 for(let cycle=0;cycle<2;cycle++){
  await overviewTab.click();await retained(true);
  // Overview is a selected tab, not a toggle/back button. Clicking it again
  // must remain Overview. Both the same document tab and its saved output
  // restore the retained renderer without discarding local component state.
  await overviewTab.click();await retained(true);
  if(cycle===0)await editableTab.click();
  else await overview.locator('.a-saved-artifact').filter({hasText:'Editable artifact'}).click();
  await expect(editableTab).toHaveAttribute('aria-selected','true');await retained();
 }
 await capture('dirty-restored-same-document');
 await expect(toggle).toHaveAccessibleName('Hide canvas');await toggle.click();
 await expect(primary).toBeHidden();
 await expect(primary).toHaveCount(1);assert.equal(await primary.getAttribute('data-mount'),mount);
 await expect(toggle).toHaveAccessibleName('Open canvas');await toggle.click();await retained();
 await overviewTab.click();await retained(true);
 await toggle.click();await expect(panel).toBeHidden();await toggle.click();await retained(true);
 report.checks.push({name:'Overview and header hide retain the real dirty renderer; same document restores it',status:'passed',mount});

 // Observe the actual failed host receipt, not just a swallowed UI promise.
 // No fake response is supplied and no old artifact is silently reselected.
 for(const entry of [
  {name:'other document tab',locator:panel.getByRole('tab',{name:'Other artifact',exact:true})},
  {name:'other saved output',locator:overview.locator('.a-saved-artifact').filter({hasText:'Other artifact'})},
 ]){
  const [response]=await Promise.all([page.waitForResponse(response=>isAction(response.request())&&
   unwrap(response.request().postDataJSON()).action==='canvas.select'&&
   unwrap(response.request().postDataJSON()).args.id===otherArtifact.resourceId,{timeout:8000}),entry.locator.click()]);
  const receipt=await response.json();
  assert.equal(receipt.accepted,false,'Other-artifact selection must return an explicit rejection');
  assert.match(receipt.error,/viewer edit/);
  report.expectedRejections.push({name:entry.name,status:response.status(),receipt});
  await retained(true);assert.equal((await state()).canvas.id,first.resourceId);
 }
 await panel.getByRole('button',{name:'Open documents, 2 open',exact:true}).click();
 const openDocuments=page.getByRole('dialog',{name:'Open documents',exact:true});
 const [selectionResponse]=await Promise.all([page.waitForResponse(response=>isAction(response.request())&&
  unwrap(response.request().postDataJSON()).action==='canvas.select'&&
  unwrap(response.request().postDataJSON()).args.id===otherArtifact.resourceId,{timeout:8000}),
  openDocuments.locator('li').getByRole('button',{name:'Other artifact markdown',exact:true}).click()]);
 const selectionReceipt=await selectionResponse.json();
 assert.equal(selectionReceipt.accepted,false);assert.match(selectionReceipt.error,/viewer edit/);
 report.expectedRejections.push({name:'other document from Open documents',status:selectionResponse.status(),receipt:selectionReceipt});
 await expect(openDocuments).toBeVisible();await expect(openDocuments.getByRole('alert')).toContainText('viewer edit');
 assert.equal((await state()).canvas.id,first.resourceId);await expect(primary).toHaveValue('PRIMARY UNSAVED EDIT');
 await openDocuments.getByRole('button',{name:'Close tab Editable artifact',exact:true}).click();
 await expect(openDocuments.getByRole('alert')).toContainText('viewer edit');
 await expect(openDocuments).toBeVisible();
 await expect(primary).toHaveValue('PRIMARY UNSAVED EDIT');
 assert.equal(await primary.getAttribute('data-mount'),mount);
 assert.equal((await state()).canvas.id,first.resourceId);
 await page.keyboard.press('Escape');
 await expect(panel.getByRole('button',{name:'Open documents, 2 open',exact:true})).toBeFocused();
 await retained(true);
 await capture('failed-selection-stays-overview');
 report.checks.push({name:'failed other-artifact selection preserves Overview, source, draft and dirty guard',status:'passed'});
 for(const [name,args] of [
  ['canvas.close',{}],['canvas.select',{id:otherArtifact.resourceId}],
  ['canvas.tabClose',{id:first.resourceId}],['canvas.show',{kind:'text',content:'Do not replace'}],
  ['session.select',{id:otherSession}],['session.create',{}],['session.fork',{id:session}],
  ['workspace.create',{path:root+'output/dirty-guard-must-not-create'}],
 ]){
  let rejection;
  await assert.rejects(async()=>{try{await action(name,args)}catch(error){rejection=error.message;throw error}},/viewer edit/);
  report.expectedRejections.push({name,args,error:rejection});
  assert.equal((await state()).selectedSessionId,session);
  assert.equal((await state()).view.draft,'Keep composer target and draft');
  assert.deepEqual((await inspect()).map(target),before);
  await expect(primary).toHaveValue('PRIMARY UNSAVED EDIT');assert.equal(await primary.getAttribute('data-mount'),mount);
  await retained(true);
 }
 await editableTab.click();await retained();await capture('guarded-renderer-restored');
 report.checks.push({name:'all replacement transitions remain guarded after failed selection',status:'passed'});
 // Explicit recovery discards just the targeted local edit and retains sources.
 await action('canvas.views.recover',target(await view('primary')));
 await expect(primary).toHaveCount(0);
 assert.equal((await state()).canvas.content,'# Original retained source');
 await action('canvas.close');assert.equal((await state()).canvas.open,false);
 await action('canvas.reopen');
 assert.equal((await state()).view.draft,'Keep composer target and draft');
 assert.equal((await state()).canvasArtifacts.length,2);
 await expect(panel.getByRole('heading',{name:'Original retained source',exact:true})).toBeVisible();
 await capture('explicit-recovery');report.checks.push({name:'explicit recovery keeps both saved sources and composer draft',status:'passed'});
 await action('canvas.views.inspect');await Promise.all(pendingReceipts);
 const guarded=new Set(['session.select','canvas.close','canvas.select','canvas.tabClose','canvas.show','session.create','session.fork','workspace.create']);
 const failed=report.http.filter(row=>row.transportError||row.readError||row.status>=400||row.body?.accepted===false);
 assert.deepEqual(failed.filter(row=>row.transportError||row.readError||!guarded.has(row.request?.action)||row.body?.accepted!==false||!/viewer edit/.test(row.body?.error||'')),[],'Unexpected action/transport failure');
 assert.deepEqual(report.browserErrors,[]);report.status='passed';
}catch(error){
 failure=error;report.status='failed';report.error=errorText(error);
 releaseDirty?.();
 if(page&&!page.isClosed())try{await capture('failure')}catch(error){report.evidenceErrors.push(errorText(error))}
}finally{
 phase='teardown';
 releaseDirty?.();
 if(context)try{await context.tracing.stop({path:join(output,'trace.zip')})}catch(error){report.evidenceErrors.push(errorText(error));failure||=error}
 try{await browser?.close()}catch(error){report.evidenceErrors.push(errorText(error));failure||=error}
 if(fixture.pid&&fixture.exitCode===null&&fixture.signalCode===null){
  const exited=once(fixture,'exit'),timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);
  fixture.kill('SIGTERM');try{await exited}finally{clearTimeout(timer)}
 }
 await Promise.all(pendingReceipts);
 if(failure)report.status='failed';
 await writeFile(join(output,'fixture.stdout.log'),stdout);await writeFile(join(output,'fixture.stderr.log'),stderr);
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({test:'canvas-dirty',status:report.status,output,checks:report.checks,visualReview:'not-performed'}));
}
if(failure)throw failure;
