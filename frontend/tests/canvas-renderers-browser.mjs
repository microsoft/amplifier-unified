import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1500,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];let streams=0,resourceReads=0;
 page.on('pageerror',error=>errors.push(error.message));
 page.on('websocket',socket=>{if(new URL(socket.url()).pathname==='/api/events/ws')streams++});page.on('request',request=>{if(new URL(request.url()).pathname==='/api/events')streams++;if(request.url().includes('/api/canvas/views/'))resourceReads++});
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const inspect=()=>action('canvas.views.inspect').then(result=>result.result);
 const view=async id=>(await inspect()).views.find(view=>view.viewId===id);
 const target=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
 const canvas=page.locator('#workspace-canvas'),inspector=canvas.locator('.a-canvas-inspector'),reader=canvas.locator('.a-canvas-reader');
 const openDetails=()=>canvas.getByRole('button',{name:'Canvas details',exact:true}).click();
 // At these pane widths Details covers the reader; use the UI to expose it.
 const closeDetails=async()=>{
  await inspector.getByRole('button',{name:'Close canvas details',exact:true}).click();
  await expect(inspector).toBeHidden();
  await expect(reader).toHaveJSProperty('inert',false);
  await expect(reader).not.toHaveAttribute('aria-hidden','true');
  await expect(reader.getByRole('region',{name:'Artifact view',exact:true})).toBeVisible();
 };
 await action('session.create',{});
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,canvasWidth:640}});
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this draft through viewer changes');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='Keep this draft through viewer changes');
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 const initial=await state(),workspace=initial.workspaces.find(row=>row.id===initial.selectedWorkspaceId).path;
 const filePath=workspace+'/copy path proof.md';await writeFile(filePath,'# File path proof');
 await action('canvas.show',{kind:'markdown',path:filePath});
 const fileDetails=page.locator('#workspace-canvas .a-canvas-inspector details.a-canvas-file-path');
 await fileDetails.getByText('File details',{exact:true}).click();
 await expect(fileDetails.locator('code')).toHaveText([filePath,workspace,'copy path proof.md']);
 await fileDetails.getByRole('button',{name:'Copy full path',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(filePath);
 await fileDetails.getByRole('button',{name:'Copy relative path',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('copy path proof.md');
 await fileDetails.getByText('File details',{exact:true}).click();
 await action('canvas.show',{kind:'html',title:'Live HTML view',content:'<h1>Persistent viewer</h1><input aria-label="Viewer note" value="Retained input">'});
 const html=await view('primary');
 await closeDetails();
 await expect(page.getByRole('button',{name:'Open a second view'})).toHaveCount(0);
 await page.evaluate(()=>{window.proof={root:document.getElementById('amp-one'),time:performance.timeOrigin,host:window.amplifier.getState().client.hostInstanceId,session:window.amplifier.getState().selectedSessionId}});
 await action('canvas.show',{kind:'markdown',title:'Reading notes',content:'# Reading notes\n\nA live document for the reader.\n\n## Next section\n\nKeep the source unchanged.'});
 const markdown=await view('primary');
 const sourceBefore=(await state()).canvas.content;
 const clientId=(await state()).client.id;
 const sourceURL='/api/canvas/views/primary/source?'+new URLSearchParams({...target(markdown),clientId});
 const sourceResponse=await page.request.get(url+sourceURL);
 assert.equal(sourceResponse.status(),200);assert.equal((await sourceResponse.json()).content,sourceBefore);
 const staleResponse=await page.request.get(url+'/api/canvas/views/primary/source?'+new URLSearchParams({...target(html),clientId}));
 assert.equal(staleResponse.status(),409,'HTTP source reads must reject an old artifact binding');
 // Author/build the separate package only after the production app is open.
 await import('../scripts/build-reader-example.mjs');
 const manifest=JSON.parse(await readFile(root+'examples/shell-reader/dist/manifest.json','utf8'));
 const source=await readFile(root+'examples/shell-reader/dist/module.mjs','utf8');
 const staged=(await action('shell.packages.stage',{manifest,source})).result;
 await assert.rejects(()=>action('canvas.views.renderer',{...target(markdown),renderer:staged.digest}),/validation/);
 assert.equal((await action('shell.packages.validate',{digest:staged.digest})).result.status,'passed');
 await openDetails();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(staged.digest);
 await closeDetails();
 await page.getByRole('article',{name:'Reading view'}).waitFor();
 await expect.poll(async()=>(await view('primary')).activation?.status).toBe('ready');
 await page.getByRole('combobox',{name:'Reader text size'}).selectOption('22');
 await expect.poll(async()=>(await view('primary')).view.readerScale).toBe(22);
 await action('view.update',{patch:{canvasWidth:620}});
 assert.deepEqual(await page.evaluate(()=>({root:window.proof.root===document.getElementById('amp-one'),time:window.proof.time===performance.timeOrigin,host:window.proof.host===window.amplifier.getState().client.hostInstanceId,session:window.proof.session===window.amplifier.getState().selectedSessionId})),{root:true,time:true,host:true,session:true});
 assert.equal((await state()).canvas.content,sourceBefore);
 await openDetails();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption('builtin.canvas.markdown');
 await closeDetails();
 await expect(reader.getByRole('heading',{name:'Reading notes',exact:true})).toBeVisible();
 await expect(page.getByRole('article',{name:'Reading view'})).toHaveCount(0);
 await openDetails();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(staged.digest);
 await closeDetails();
 await page.getByRole('article',{name:'Reading view'}).waitFor();
 await expect(page.getByRole('combobox',{name:'Reader text size'})).toHaveValue('22');
 const readsBefore=resourceReads;
 await page.getByRole('combobox',{name:'Reader text size'}).selectOption('26');
 await expect.poll(async()=>(await view('primary')).view.readerScale).toBe(26);
 assert.equal(resourceReads,readsBefore,'View preferences must not refetch the artifact body');
 await mkdir(root+'output/canvas-proof',{recursive:true});
 await page.screenshot({path:root+'output/canvas-proof/single-view.png'});
 const oldPrimary=await view('primary');
 await action('canvas.show',{kind:'markdown',title:'Other document',content:'# Another document'});
 await assert.rejects(()=>action('canvas.views.command',{...target(oldPrimary),action:'canvas.view',args:{patch:{readerScale:99}}}),/changed/);
 await action('canvas.select',{id:markdown.resourceId});
 await expect(page.locator('[data-canvas-view="primary"]').getByRole('combobox',{name:'Reader text size'})).toHaveValue('26');
 const beforeClose=await view('primary');
 await action('canvas.close');await action('canvas.reopen');
 await assert.rejects(()=>action('canvas.views.command',{...target(beforeClose),action:'canvas.view',args:{patch:{readerScale:99}}}),/changed/);
 const reloadState=await state(),beforeReload=reloadState.client.id;
 // The visible inspector is optimistic. Hold only this client's exact opening
 // update and exercise the receipt barrier while that panel is already visible.
 const detailsArgs={sessionId:reloadState.selectedSessionId,patch:{canvasControlsExpanded:true}};
 const actionsURL=new URL('/api/actions',url).href;
 const openingDetails=request=>{
  if(request.url()!==actionsURL||request.method()!=='POST'||request.headers()['x-amplifier-client']!==beforeReload)return false;
  let body;try{body=request.postDataJSON()}catch(error){failDetails(error);return false}
  return body?.action==='view.update'&&body.args?.sessionId===detailsArgs.sessionId&&
   body.args?.patch?.canvasControlsExpanded===true&&Object.keys(body.args.patch).length===1&&Object.keys(body.args).length===2;
 };
 let releaseDetails,markHeld,heldRequest,forwarded,requestedDetails,reloadAfterReceipt,acceptedDetails=false,reloadStarted=false,cancelReload=false;
 let rejectDetails,detailsTimer,drainTimer,detailsFailure,pageStop;
 const pendingDetails=[],detailsFailures=[],cleanupFailures=[];
 const heldDetails=new Promise(resolve=>{releaseDetails=resolve});
 const capturedDetails=new Promise(resolve=>{markHeld=resolve});
 // One lifecycle watchdog includes capture, forwarding, the complete response
 // body and reload. Reserve five seconds for cleanup: at most 30 seconds total.
 const detailsDeadline=new Promise((_,reject)=>{rejectDetails=reject});
 const failDetails=error=>{detailsFailures.push(error);cancelReload=true;releaseDetails();rejectDetails(error)};
 const retainDetails=promise=>{
  pendingDetails.push(promise);promise.catch(failDetails);return promise;
 };
 const holdDetails=route=>{
  const operation=(async()=>{
   const request=route.request();
   if(heldRequest||!openingDetails(request))return route.continue();
   heldRequest=request;markHeld(request);
   forwarded=heldDetails.then(()=>route.continue());
   return forwarded;
  })();
  // The handler must not leak a rejection; retain it and fail the lifecycle.
  return retainDetails(operation).catch(()=>{});
 };
 detailsTimer=setTimeout(()=>failDetails(Error('Details reload lifecycle exceeded 25 seconds')),25000);
 const detailsLifecycle=(async()=>{
  await page.route(actionsURL,holdDetails);
  if(cancelReload)return;
  requestedDetails=retainDetails(page.waitForRequest(openingDetails));
  await openDetails();
  const requested=await requestedDetails;
  const detailsRequest=await capturedDetails;
  if(cancelReload)return;
  assert.equal(detailsRequest,requested);
  const detailsPayload=detailsRequest.postDataJSON();
  assert.equal(detailsPayload.action,'view.update');assert.deepEqual(detailsPayload.args,detailsArgs);
  assert.equal(typeof detailsPayload.id,'string');assert.ok(detailsPayload.id);
  // Correlate by the captured Request object, not another view.update response.
  reloadAfterReceipt=retainDetails((async()=>{
   const response=await page.waitForResponse(response=>response.request()===detailsRequest);
   assert.equal(response.request(),detailsRequest);assert.equal(response.status(),200);
   assert.equal(await response.finished(),null);
   const receipt=await response.json();
   assert.equal(receipt.accepted,true);assert.equal(receipt.hostInstanceId,reloadState.client.hostInstanceId);
   assert.deepEqual(response.request().postDataJSON(),detailsPayload);
   acceptedDetails=true;
   if(cancelReload)return;
   reloadStarted=true;
   await page.reload();
  })());
  await expect(inspector).toBeVisible();await expect(reader).toHaveJSProperty('inert',true);
  assert.equal(acceptedDetails,false,'Optimistic Details is not an accepted update');
  assert.equal(reloadStarted,false,'Visible Details must not release the reload barrier');
  assert.equal((await state()).client.id,beforeReload);
  releaseDetails();await forwarded;
  await reloadAfterReceipt;
  assert.equal(acceptedDetails,true);assert.equal(reloadStarted,true);
 })();
 try{await Promise.race([detailsLifecycle,detailsDeadline])}catch(error){detailsFailure=error}
 finally{
  clearTimeout(detailsTimer);cancelReload=true;releaseDetails();markHeld(null);
  // This test owns this page. Stop its I/O before draining failed work, never
  // close another page/context or wait indefinitely for a response body.
  const stopDetailsIO=()=>pageStop??=(async()=>{
   try{await page.close({runBeforeUnload:false})}catch(error){cleanupFailures.push(error)}
  })();
  const drainDetails=(async()=>{
   if(detailsFailure||detailsFailures.length)await stopDetailsIO();
   try{await page.unroute(actionsURL,holdDetails)}catch(error){cleanupFailures.push(error);await stopDetailsIO()}
   const settled=await Promise.allSettled([detailsLifecycle,...pendingDetails]);
   for(const result of settled)if(result.status==='rejected')detailsFailures.push(result.reason);
  })();
  try{
   await Promise.race([drainDetails,new Promise((_,reject)=>{
    drainTimer=setTimeout(()=>{stopDetailsIO();reject(Error('Details reload cleanup exceeded 5 seconds; owned page stop requested'))},5000);
   })]);
  }catch(error){cleanupFailures.push(error)}finally{clearTimeout(drainTimer)}
 }
 const detailsErrors=[...new Set([detailsFailure,...detailsFailures,...cleanupFailures].filter(error=>error!==undefined))];
 if(detailsErrors.length)throw detailsErrors.length===1?detailsErrors[0]:new AggregateError(detailsErrors,'Details reload failed, including retained I/O/cleanup failures');
 await closeDetails();await page.getByRole('article',{name:'Reading view'}).first().waitFor();
 assert.notEqual((await state()).client.id,beforeReload);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this draft through viewer changes');
 // A module can pass fixtures but still fail on live data: contain and recover.
 const brokenSource='export default ({React})=>function Broken({host}){const s=React.useSyncExternalStore(host.subscribe,host.getSnapshot,host.getSnapshot);if(s.resource?.content?.includes("live document"))throw Error("Live reader failure");return React.createElement("p",null,"Fixture reader")}';
 const broken=(await action('shell.packages.stage',{manifest:{...manifest,id:'example.live-failure'},source:brokenSource})).result;
 assert.equal((await action('shell.packages.validate',{digest:broken.digest})).result.status,'passed');
 await openDetails();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(broken.digest);
 await closeDetails();
 await page.getByText(/This renderer needs attention: Live reader failure/).waitFor();
 await expect.poll(async()=>(await view('primary')).activation?.status).toBe('error');
 assert.equal((await view('primary')).resourceId,markdown.resourceId);
 await page.getByRole('button',{name:'Use standard viewer'}).click();
 await openDetails();
 await expect(page.getByRole('combobox',{name:'Open with',exact:true})).toHaveValue('builtin.canvas.markdown');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this draft through viewer changes');
 await page.setViewportSize({width:390,height:844});
 await action('view.update',{patch:{canvasFocused:true,canvasControlsPinned:true,canvasControlsExpanded:true}});
 await expect(page.getByRole('combobox',{name:'Open with',exact:true})).toBeVisible();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'The single-view shell must fit a narrow display');
 await page.screenshot({path:root+'output/canvas-proof/single-view-mobile.png'});
 // Recovery startup must skip the optional renderer, preserving its saved
 // selection until the user explicitly restores the standard viewer.
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(staged.digest);
 await page.goto(url+'/?shell=recovery');
 await page.locator('[aria-label="Message Amplifier"]').waitFor({state:'attached'});
 await closeDetails();
 await expect(page.getByText('Recovery mode uses standard viewers.')).toBeVisible();
 await expect(reader.getByRole('heading',{name:'Reading notes',exact:true})).toBeVisible();
 assert.equal(await page.getByRole('article',{name:'Reading view'}).count(),0);
 await expect.poll(async()=>(await view('primary')).activation?.status).toBe('error');
 assert.equal((await view('primary')).renderer,staged.digest);
 await page.getByRole('button',{name:'Use standard viewer'}).click();
 await expect.poll(async()=>(await view('primary')).renderer).toBe('builtin.canvas.markdown');
 await expect.poll(async()=>(await view('primary')).activation?.status).toBe('ready');
 assert.deepEqual(errors,[]);
 assert.equal(streams,3,'One event stream per page load');
 await writeFile(root+'output/canvas-proof/evidence.json',JSON.stringify({externalDigest:staged.digest,resourceId:markdown.resourceId,streams,resourceReads,checks:['production hot loading after independent build','single viewer and retained saved artifacts','draft and conversation retention','renderer choices and state after reload','stale action rejection','failed live renderer fallback and recovery']},null,2));
 console.log('Canvas renderer proof passed: external validated hot loading, single viewer and preserved draft, reload retention, stale actions, fallback/recovery.');
}finally{await browser?.close();fixture.kill();}
