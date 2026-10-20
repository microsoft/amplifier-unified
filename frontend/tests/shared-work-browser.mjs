// Test-first real-host journey. Run only in the owned isolated guest after build.
// The baseline has no Work tab; no result is claimed until this script executes.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile,mkdtemp} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect as baseExpect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {assertPassiveActions,bounded,createWorkObserver,passiveWorkReads,unknownCleanupValue} from './shared-work-test-helpers.mjs';

const actionTimeoutMs=7000,navigationTimeoutMs=15000;
const expect=baseExpect.configure({timeout:actionTimeoutMs});
const observer=createWorkObserver();
const observePage=(page,client)=>{
 page.setDefaultTimeout(actionTimeoutMs);
 page.setDefaultNavigationTimeout(navigationTimeoutMs);
 page.on('pageerror',error=>observer.pageError(client,error));
 page.on('request',request=>observer.request(client,request));
};
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-shared-work';
await mkdir(output,{recursive:true});
const installed=process.env.AMPLIFIER_TEST_INSTALLED==='1';
const fixturePath=process.env.AMPLIFIER_TEST_FIXTURE||root+'tests/fixtures/empty_host_ui_server.py';
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python';
if(installed)assert.ok(process.env.AMPLIFIER_TEST_PYTHON&&process.env.AMPLIFIER_TEST_FIXTURE&&!fixturePath.startsWith(root),'Installed qualification requires an external fixture and explicit interpreter');
const home=await mkdtemp(path.join(output,'cold-home-'));
const fixtureEnv={...process.env,AMPLIFIER_TEST_SHARED_WORK_HOME:home};
if(installed){delete fixtureEnv.PYTHONPATH;delete fixtureEnv.PYTHONHOME}
const fixtureArgs=[...(installed?['-I','-B']:[]),fixturePath,'--chat-controls','--shared-work'];
const fixtureOptions={stdio:['ignore','pipe','inherit'],env:fixtureEnv,cwd:path.dirname(fixturePath)};
// Seed in a different process, close the real service and reopen its persisted
// state. The second process must never replay capture or initialization actions.
const seed=spawn(python,[...fixtureArgs,'--shared-work-seed-only'],fixtureOptions);
let seedOutput='';seed.stdout.on('data',chunk=>{if(seedOutput.length<65536)seedOutput+=String(chunk)});
const seedClosed=new Promise((resolve,reject)=>{seed.once('error',reject);seed.once('close',(code,signal)=>resolve({code,signal,pid:seed.pid}))});
let seedReceipt;
try{seedReceipt=await bounded(seedClosed,30000,'Cold seed process')}
catch(error){if(seed.exitCode===null)seed.kill('SIGKILL');await bounded(seedClosed,2000,'Cold seed reap');throw error}
assert.equal(seedReceipt.code,0);assert.equal(seedReceipt.signal,null);assert.match(seedOutput,/"sharedWorkSeed"/);
await writeFile(path.join(output,'cold-seed.json'),JSON.stringify({receipt:seedReceipt,output:seedOutput})+'\n');
const fixture=spawn(python,fixtureArgs,fixtureOptions);
// An admitted HTTP Work wait is shielded from transport cancellation and can
// naturally take 30s. Keep escalation a failure; allow its actual drain first.
const stop=ownFixture(fixture,{label:'Shared Work fixture',timeoutMs:40000});
const headers={Authorization:'Bearer fixture-browser-control-token'};
const launch={headless:true,timeout:15000,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]};
let browser,originalError,result,page,fixtureUrl;
const termination={fixturePid:fixture.pid,browserDisconnectedAt:null,closeRequestedAt:null,fixtureClose:null,workWaits:[],cleanupComplete:null,observationErrors:[]};
let observationBuffer='';
fixture.stdout.on('data',chunk=>{
 observationBuffer+=String(chunk);
 if(observationBuffer.length>65536){termination.observationErrors.push('Fixture observation exceeded 64KiB');observationBuffer='';return}
 let end;
 while((end=observationBuffer.indexOf('\n'))>=0){
  const line=observationBuffer.slice(0,end);observationBuffer=observationBuffer.slice(end+1);
  if(!line.trim())continue;
  try{
   const row=JSON.parse(line);
   if(row.workWait){if(termination.workWaits.length<200)termination.workWaits.push(row.workWait);else if(!termination.observationErrors.length)termination.observationErrors.push('Wait observation overflow')}
   if(row.sharedWorkCleanup)termination.cleanupComplete=row.sharedWorkCleanup;
  }catch{if(termination.observationErrors.length<10)termination.observationErrors.push('Malformed fixture observation')}
 }
});
fixture.once('close',(code,signal)=>{termination.fixtureClose={code,signal,at:Date.now()}});
try {
 const url=await new Promise((resolve,reject)=>{
  let body='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),30000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{body+=chunk;for(const line of body.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
 });
 fixtureUrl=url;
 await mkdir(output,{recursive:true});
 browser=await chromium.launch(launch);
 browser.on('disconnected',()=>{termination.browserDisconnectedAt=Date.now()});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 observePage(page,'primary');
 const calls=observer.calls;
 const manifest=await (await page.request.get(url+'/fixture/shared-work',{timeout:actionTimeoutMs})).json();
 assert.equal(manifest.coldReopen,true);assert.deepEqual(manifest.coldReadCalls,[]);
 assert.notEqual(seed.pid,fixture.pid);
 let installedIdentity=null;
 if(installed){
  installedIdentity=(await (await page.request.get(url+'/fixture',{timeout:actionTimeoutMs})).json()).installedIdentity;
  assert.equal(installedIdentity?.verified,true);assert.ok(installedIdentity.fileCount>0);
  for(const name of ['index.html','build.json',...Object.keys(installedIdentity.static).filter(name=>/^assets\/app-.*\.(js|css)$/.test(name))]){
   const response=await page.request.get(url+'/'+(name==='index.html'?'':name),{timeout:actionTimeoutMs});
   assert.equal(response.status(),200);
   assert.equal(createHash('sha256').update(await response.body()).digest('hex'),installedIdentity.static[name],name+' served from installed wheel');
  }
 }
 assert.equal(manifest.target.delivery,'reported_published');
 assert.equal(manifest.target.latestOutcome,'unknown');
 assert.equal(manifest.target.verification,'not_verified');
 assert.equal(manifest.target.humanDecision,'not_recorded');
 assert.equal(manifest.target.deliveryReport.hostObserved,false);
 assert.equal(manifest.target.deliveryReport.independentlyVerified,false);
 assert.equal(manifest.coverage.historicalCoverage,'incomplete');
 assert.ok(manifest.coverage.unreviewedInputs>=1);
 assert.deepEqual(manifest.runtimeCallbackCounts,{start:0,send:0,prewarm:0});
 assert.equal(manifest.pendingWarmups,0);
 const action=(name,args={})=>bounded(page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]),actionTimeoutMs,'Primary '+name);
 const state=()=>bounded(page.evaluate(()=>window.amplifier.getState()),actionTimeoutMs,'Primary state read');
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 // The selected draft belongs to a different chat; reading Work must not touch it.
 const created=await action('session.create',{workspace:manifest.workspace.path,title:'Draft owner'});
 const draftOwner=created.result.sessionId;
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Private draft remains unchanged.');
 await action('attachment.add',{sessionId:draftOwner,name:'private-draft.txt',base64:'cHJpdmF0ZQ=='});
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:manifest.workspace.id,workWorkspaceTab:'chats',navExpanded:false}});
 const before=calls.length;
 await page.getByRole('group',{name:'Workspace view'}).getByRole('button',{name:'Work',exact:true}).click();
 const work=page.getByRole('region',{name:'Shared work',exact:true});
 await expect(work).toBeVisible();
 const filter=work.getByRole('combobox',{name:'Show work'});
 await expect(filter).toHaveValue('accepted');
 await expect(work.getByText(/historical reconciliation is incomplete/i)).toBeVisible();
 const found=new Set();
 for(let pageNumber=0;pageNumber<5;pageNumber++) {
  await expect(work.locator('[data-work-id]').first()).toBeVisible();
  for(const id of await work.locator('[data-work-id]').evaluateAll(rows=>rows.map(row=>row.dataset.workId)))found.add(id);
  const next=work.getByRole('button',{name:'Next work',exact:true});
  if(await next.isDisabled())break;
  const previous=await work.locator('[data-work-id]').first().getAttribute('data-work-id');
  await next.click();
  await expect(work.locator('[data-work-id]').first()).not.toHaveAttribute('data-work-id',previous);
 }
 assert.deepEqual([...found].sort(),[...manifest.acceptedIds].sort(),'Every accepted-open record reachable through bounded pages');
 assert.ok(!found.has(manifest.proposal.id));assert.ok(!found.has(manifest.closed.id));
 await work.getByRole('searchbox',{name:'Find work'}).fill('Retain the original promise');
 const row=work.locator(`[data-work-id="${manifest.target.id}"]`);
 await row.focus();await page.keyboard.press('Enter');
 const detail=work.getByRole('article',{name:'Work details'});
 await expect(detail).toBeVisible();
 for(const fact of ['Commitment','Execution','Verification','Human decision','Delivery','Cleanup'])await expect(detail.getByText(fact,{exact:true})).toBeVisible();
 const cleanupValue=detail.locator('dt').filter({hasText:/^Cleanup$/}).locator('xpath=following-sibling::*[1][self::dd]');
 await expect(cleanupValue).toHaveCount(1);
 await expect(cleanupValue).toHaveText(unknownCleanupValue);
 await expect(detail.getByText(/Returned.*unverified/i)).toBeVisible();
 await expect(detail.getByText('Reported published',{exact:true})).toBeVisible();
 await expect(detail.getByText('Participant reports publication; no host-observed delivery or independent verification.',{exact:true})).toBeVisible();
 await expect(detail.getByText(/Independent checks passed|Cleanup complete|Shipped/)).toHaveCount(0);
 const source=detail.getByRole('region',{name:'Original source'});
 for(let index=0;index<20&&!((await source.innerText()).includes('ORIGINAL-END'));index++) {
  const previous=await source.locator('blockquote').textContent();
  await detail.getByRole('button',{name:'Read more source',exact:true}).click();
  // A DOM click completes before the passive read does. Wait for the exact
  // displayed page to advance before deciding whether another page exists.
  await expect.poll(()=>source.locator('blockquote').textContent()).not.toBe(previous);
 }
 await expect.poll(()=>source.locator('blockquote').textContent()).toBe(manifest.originalText);
 await expect(source.getByText(manifest.originalSha256,{exact:true})).toBeVisible();
 assert.equal(await source.locator('script').count(),0,'Source rendered as text, never executable HTML');
 await detail.getByRole('button',{name:'Read history',exact:true}).click();
 await expect(detail.getByText('Participant reports success. This is not a retained check receipt.',{exact:true})).toBeVisible();
 await detail.getByRole('button',{name:'Read correction source',exact:true}).click();
 await expect(detail.getByText(manifest.correctionText,{exact:true})).toBeVisible();
 await detail.getByRole('button',{name:'Next history',exact:true}).click();
 await expect(detail.getByText('The current cold-reopen outcome is unknown; the old success is retained.',{exact:true})).toBeVisible();
 assert.equal((await state()).selectedSessionId,draftOwner);
 const readCalls=calls.slice(before);
 assert.ok(readCalls.filter(row=>row.action.startsWith('workspace.work.')).every(row=>passiveWorkReads.has(row.action)));
 assertPassiveActions(readCalls);
 observer.assertHealthy();
 assert.ok(readCalls.filter(row=>row.action==='workspace.work.list').every(row=>row.args.limit<=50));
 const waitCalls=readCalls.filter(row=>row.action==='workspace.work.wait');
 assert.ok(waitCalls.length>0,'Visible Work subscribes without a model or worker');
 assert.ok(waitCalls.every(row=>typeof row.args.afterVersion==='string'&&row.args.afterVersion.length===64
  &&row.args.workspaceId===manifest.workspace.id&&(row.args.waitMs??30000)<=30000));
 // New incoming state cannot steal focus; Back restores the explicit item.
 for(const width of [390,320,1440]) {
  await page.setViewportSize({width,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow at '+width);
  if(width<720) {
   const back=detail.getByRole('button',{name:'Back to Work',exact:true});await expect(back).toBeVisible();
   const box=await back.boundingBox();assert.ok(box.height>=44&&box.width>=44,'44px Back target');
   await back.click();await expect(row).toBeFocused();
   const rowBox=await row.boundingBox();assert.ok(rowBox.height>=44&&rowBox.width>=44,'44px item target');
   await page.keyboard.press('Enter');
   await expect(detail.getByRole('heading',{name:manifest.target.title,exact:true})).toBeFocused();
  }
  await page.screenshot({path:path.join(output,`shared-work-${width}.png`)});
 }
 // A second browser client has independent presentation choices.
 const peer=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 observePage(peer,'peer');
 await peer.goto(url);await peer.waitForFunction(()=>window.amplifier);
 await bounded(peer.evaluate(id=>window.amplifier.dispatch('view.update',{patch:{workSurface:'workspace',workWorkspaceId:id,workWorkspaceTab:'work'}}),manifest.workspace.id),actionTimeoutMs,'Peer Work navigation');
 const peerWork=peer.getByRole('region',{name:'Shared work',exact:true});
 await peerWork.getByRole('combobox',{name:'Show work'}).selectOption('closed');
 await expect(peerWork.locator(`[data-work-id="${manifest.closed.id}"]`)).toBeVisible();
 await expect(filter).toHaveValue('accepted');
 await expect(work.getByRole('searchbox',{name:'Find work'})).toHaveValue('Retain the original promise');
 assertPassiveActions(calls.slice(before)); // Includes both clients, before explicit Open chat.
 observer.assertHealthy(); // Rejects session.warm across the whole observation.
 // Only explicit Open chat traverses the existing guarded navigation path.
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(manifest.ownerSessionId);
 await action('session.select',{id:draftOwner});
 await expect(composer).toHaveDraft('Private draft remains unchanged.',{timeout:actionTimeoutMs});
 await expect(page.getByRole('button',{name:'Remove private-draft.txt',exact:true})).toBeVisible();
 // Actual dirty Canvas memory, not a fabricated accepted navigation receipt.
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'markdown',title:'Work guard source',content:'# Original guard source'});
 const rendererManifest={id:'example.work-dirty-editor',label:'Work guard editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const rendererSource='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("label",null,"Work unsaved edit",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const renderer=(await action('shell.packages.stage',{manifest:rendererManifest,source:rendererSource})).result.digest;
 assert.equal((await action('shell.packages.validate',{digest:renderer})).result.status,'passed');
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(renderer);
 const editor=page.locator('[data-canvas-view="primary"]').getByLabel('Work unsaved edit');
 await editor.fill('DIRTY WORK CANVAS');
 const canvasView=async()=>(await action('canvas.views.inspect')).result.views.find(view=>view.viewId==='primary');
 await expect.poll(async()=>(await canvasView()).dirty).toBe(true);
 const originalView=await canvasView(),mount=await editor.getAttribute('data-mount');
 const target=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
 const browseWork=()=>action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:manifest.workspace.id,workWorkspaceTab:'work'}});
 await browseWork();await expect(editor).toBeHidden();await expect(detail).toBeVisible();
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 await expect(detail.getByRole('alert')).toContainText(/viewer edit/);
 assert.equal((await state()).selectedSessionId,draftOwner);
 assert.deepEqual(target(await canvasView()),target(originalView));
 await action('view.update',{patch:{workSurface:'chat'}});
 await expect(editor).toHaveValue('DIRTY WORK CANVAS');assert.equal(await editor.getAttribute('data-mount'),mount);
 await expect(composer).toHaveDraft('Private draft remains unchanged.');
 await expect(page.getByRole('button',{name:'Remove private-draft.txt',exact:true})).toBeVisible();
 await action('canvas.views.recover',target(await canvasView()));
 assert.equal((await state()).canvas.content,'# Original guard source');
 // A real Settings private draft stays mounted while Work is inspected. The
 // same Open chat path must ask, refuse on Keep, and retain the exact input.
 await action('canvas.show',{kind:'settings',title:'Guarded Settings'});
 await action('view.update',{patch:{settingsExpanded:['ai-connections']}});
 const ai=page.locator('.a-settings-experience [data-part="ai-connections"]');
 await ai.getByRole('button',{name:'Connect another service',exact:true}).click();
 await ai.getByRole('button',{name:/Anthropic API Connect with/}).click();
 const secret=ai.getByRole('textbox',{name:'API key',exact:true}),secretText='synthetic-unsaved-work-secret';
 await secret.fill(secretText);assert.ok(!JSON.stringify(await state()).includes(secretText));
 const providersBefore=(await state()).setup.providers.map(row=>row.id);
 await browseWork();await expect(secret).toBeHidden();
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 const guard=page.getByRole('alertdialog',{name:'Unsaved settings changes',exact:true});
 await expect(guard).toBeVisible();await guard.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(detail.getByRole('alert')).toContainText('Settings changes were kept');
 assert.equal((await state()).selectedSessionId,draftOwner);
 await action('view.update',{patch:{workSurface:'chat'}});
 await expect(secret).toHaveValue(secretText);
 assert.deepEqual((await state()).setup.providers.map(row=>row.id),providersBefore);
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await guard.getByRole('button',{name:'Discard changes',exact:true}).click();
 await expect(page.locator('.a-settings-experience')).toHaveCount(0);
 await expect(composer).toHaveDraft('Private draft remains unchanged.');
 await expect(page.getByRole('button',{name:'Remove private-draft.txt',exact:true})).toBeVisible();
 await browseWork();
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(manifest.ownerSessionId);
 await action('session.select',{id:draftOwner});
 const observation=await (await page.request.get(url+'/fixture/shared-work',{timeout:actionTimeoutMs})).json();
 assert.deepEqual(observation.forbiddenRuntimeCalls,[]);
 assert.deepEqual(observation.runtimeCallbackCounts,{start:0,send:0,prewarm:0});
 assert.equal(observation.pendingWarmups,0,'No scheduled warm callback may remain unobserved');
 assert.equal(observation.coldReopen,true);assert.deepEqual(observation.coldReadCalls,[]);
 const runtime=await (await page.request.get(url+'/fixture',{timeout:actionTimeoutMs})).json();
 assert.deepEqual(runtime.started,[]);assert.deepEqual(runtime.sent,[]);assert.equal(runtime.workerCount,0);
 // Close observed pages before the final awaited assertion: detached listener
 // failures from either client must not arrive after the PASS decision.
 await bounded(peer.close(),actionTimeoutMs,'Peer page close');
 await bounded(page.close(),actionTimeoutMs,'Primary page close');
 observer.assertHealthy();
 result={status:'passed',sourceFixture:!installed,installedWheel:!!installedIdentity,installedIdentity,launchOptions:launch,
  coldServiceReopen:{seedPid:seed.pid,reopenedPid:fixture.pid,readHydrationCalls:observation.coldReadCalls},
  dirtyCanvas:{refusal:true,mountPreserved:true,draftAndAttachmentPreserved:true},
  dirtySettings:{keepRefusedNavigation:true,privateDraftPreserved:true,discardWithoutSave:true},
  acceptedItems:found.size,sourcePagesComplete:true,priorReturnRetained:true,currentUnknown:true,
  widths:[1440,390,320],clients:2,forbiddenRuntimeCalls:observation.forbiddenRuntimeCalls,
  runtimeCallbackCounts:observation.runtimeCallbackCounts,observationErrors:observer.errors,
  browserTimeouts:{actionTimeoutMs,navigationTimeoutMs},
  limits:['No live provider or private history','Synthetic runtime; installed identity requires explicit independently prepared wheel']};
} catch(error) {
 originalError=error;
 // An expected missing-UI baseline still needs observed fixture/runtime facts.
 // Evidence collection never replaces the original assertion or bypasses finally.
 const failure={status:'failed',error:error.message,observationErrors:observer.errors,fixture:null};
 try {
  if(page&&fixtureUrl)failure.fixture=await (await page.request.get(fixtureUrl+'/fixture/shared-work',{timeout:actionTimeoutMs})).json();
  await writeFile(path.join(output,'failure.json'),JSON.stringify(failure,null,2)+'\n');
  if(page&&!page.isClosed())await page.screenshot({path:path.join(output,'failure.png'),timeout:actionTimeoutMs});
 } catch(evidenceError){console.error('Failure evidence unavailable:',evidenceError.message)}
 throw error;
}
finally {
 termination.closeRequestedAt=Date.now();
 try{
  await finishFixture({label:'Shared Work fixture',close:[['browser',()=>browser?.close()]],stop,originalError});
  try{
   assert.deepEqual(termination.observationErrors,[]);
   assert.ok(termination.cleanupComplete,'Real runner.cleanup must return');
   const entered=termination.workWaits.filter(row=>row.phase==='entered');
   assert.ok(entered.length>0,'Real Work wait entered before teardown');
   for(const row of entered)assert.ok(termination.workWaits.some(end=>end.id===row.id&&end.phase==='returned'),'Wait must drain, not be cancelled: '+row.id);
   assert.ok(termination.workWaits.some(row=>row.phase==='returned'&&row.timedOut===true),'Observe natural long-poll deadline');
   assert.ok(!termination.workWaits.some(row=>['cancelled','failed'].includes(row.phase)),'No hidden wait cancellation');
  }catch(error){if(originalError)console.error('Work wait drain verification failed:',error);else throw error}
 }
 finally{
  try{await writeFile(path.join(output,'termination.json'),JSON.stringify(termination,null,2)+'\n')}
  catch(error){if(originalError)console.error('Termination evidence unavailable:',error.message);else throw error}
 }
}
await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));
