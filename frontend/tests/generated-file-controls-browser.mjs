// Focused #1228/#1210 proof. Authoring this file is not execution evidence.
// Run only against an already built candidate, with AMPLIFIER_TEST_ARTIFACTS
// (or FILE_CONTROLS_EVIDENCE) and AMPLIFIER_TEST_PYTHON set by the owned runner.
// Installed runs: copy the Python fixture + configured_browser.py beneath
// /artifacts. Set FILE_CONTROLS_FIXTURE, AMPLIFIER_TEST_INSTALLED=1, and exact
// AMPLIFIER_TEST_PACKAGE_ROOT. The fixture rejects /workspace import leakage.
// This browser harness may use the runner's existing locked Playwright install.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {mkdir,mkdtemp,readFile,readdir,writeFile} from 'node:fs/promises';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {expect} from '@playwright/test';
import {launchOwner,ownerPage,closeOwner,desktopOptions,touchOptions} from './file-controls-browser-topology.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.FILE_CONTROLS_EVIDENCE||process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(output,'Set an owned AMPLIFIER_TEST_ARTIFACTS or FILE_CONTROLS_EVIDENCE directory');
await mkdir(output,{recursive:true});
const evidence=await mkdtemp(join(resolve(output),'generated-file-controls-'));
const fixturePath=resolve(process.env.FILE_CONTROLS_FIXTURE||join(root,'tests/fixtures/generated_file_controls_server.py'));
const python=process.env.AMPLIFIER_TEST_PYTHON||process.env.FIXTURE_PYTHON||join(root,'.venv/bin/python');
const fixture=spawn(python,['-u',fixturePath],{cwd:dirname(fixturePath),stdio:['ignore','pipe','pipe']});
const stop=ownFixture(fixture,{label:'Generated file controls fixture'});
const records=[],checks=[],geometry=[],downloads=[],requests=[],commands=[],errors=[],screenshotErrors=[];
const metadataExchanges=[],detailsReadiness=[],preCleanupSnapshots=[],metadataPending=new Set();
const metadataByRequest=new WeakMap();
const pageLabels=new WeakMap(),routeCleanups=new Set(),browserOwners=[];
let logs='',lineBuffer='',page,ready,seed,failure,cleanup,timer,running;
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const errorEvidence=error=>error&&({name:error.name,message:error.message,stack:error.stack,
 ...(error.errors?{causes:error.errors.map(errorEvidence)}:{})});
fixture.stderr.on('data',chunk=>{logs=(logs+chunk).slice(-65536)});
const startup=new Promise((resolveReady,reject)=>{
 const timeout=setTimeout(()=>reject(Error('File controls fixture startup exceeded 15 seconds')),15000);
 fixture.once('error',error=>{clearTimeout(timeout);reject(error)});
 fixture.once('exit',(code,signal)=>{clearTimeout(timeout);reject(Error(`Fixture exited before readiness: ${code??signal}`))});
 fixture.stdout.on('data',chunk=>{
  lineBuffer+=chunk;
  let newline;
  while((newline=lineBuffer.indexOf('\n'))>=0){
   const line=lineBuffer.slice(0,newline);lineBuffer=lineBuffer.slice(newline+1);
   let value;try{value=JSON.parse(line)}catch{logs=(logs+line+'\n').slice(-65536);continue}
   records.push(value);
   if(value.url){clearTimeout(timeout);resolveReady(value)}
  }
 });
});

async function action(target,name,args={}){
 const result=await target.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 assert.equal(result.accepted,true,`${name} refused: ${JSON.stringify(result)}`);
 return result.result;
}
async function identity(target){
 return target.evaluate(()=>{const s=window.amplifier.getState();return {clientId:s.client.id,sessionId:s.selectedSessionId}});
}
async function fixturePost(target,suffix){
 const response=await target.request.post(ready.url+'/fixture/file-controls/'+suffix,{data:await identity(target),timeout:5000});
 assert.equal(response.status(),200,`Fixture ${suffix}: ${await response.text()}`);
 return response.json();
}
async function fixtureRead(target){
 const response=await target.request.get(ready.url+'/fixture/file-controls',{timeout:5000});
 assert.equal(response.status(),200);
 return response.json();
}
function card(target,row){
 return target.locator(`section[data-artifact-id="${row.id}"][data-artifact-version="${row.version}"]`);
}
function detailsFor(target,row){return card(target,row).locator('details.a-canvas-file-path')}
const button=(details,name)=>details.getByRole('button',{name,exact:true});
const names=['Copy full path','Copy relative path','Download saved file'];

async function bounded(promise,timeout,message){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(message)),timeout)})])}
 finally{clearTimeout(timer)}
}
// Observe rejection immediately, even before the driver reaches wait(). Keep the
// error as a tagged outcome and rethrow it from the awaited gate, never ignore it.
function observedGate(label){
 let resolve,reject;
 const outcome=new Promise((yes,no)=>{resolve=yes;reject=no}).then(value=>({value}),error=>({error}));
 return {resolve,reject,async wait(){
  const result=await bounded(outcome,8000,`${label} exceeded 8 seconds`);
  if(result.error)throw result.error;
  return result.value;
 }};
}
async function placement(target,restore=false){
 const response=await target.request.post(ready.url+'/fixture/file-controls/placement',{
  data:{...await identity(target),restore},timeout:5000});
 assert.equal(response.status(),200,`Fixture placement: ${await response.text()}`);
 return response.json();
}

// Runs in the page before application code. This is observation only: no app
// state or controls are substituted. Each snapshot is one synchronous DOM read.
function installDetailsProbe(){
 const ids=new WeakMap();let nextId=0;
 const id=node=>{if(!ids.has(node))ids.set(node,++nextId);return ids.get(node)};
 const visible=node=>node.checkVisibility({checkVisibilityCSS:true})&&node.getClientRects().length>0;
 const snapshot=node=>({
  at:Date.now(),nodeId:id(node),tag:node.tagName,open:node.open,visible:visible(node),
  viewport:{width:innerWidth,height:innerHeight},scroll:{x:scrollX,y:scrollY},
  loading:[...node.querySelectorAll('[role="status"]')].some(item=>item.textContent==='Loading file details…'),
  errors:[...node.querySelectorAll('[role="alert"]')].map(item=>item.textContent),
  paragraphs:[...node.querySelectorAll('p')].map(item=>({id:id(item),text:item.textContent,visible:visible(item)})),
  controls:[...node.querySelectorAll('button,[role="button"]')].map(item=>({
   tag:item.tagName,label:item.getAttribute('aria-label')||item.textContent.trim(),visible:visible(item)})),
  codes:[...node.querySelectorAll('code')].map(item=>({text:item.textContent,visible:visible(item)})),
  text:node.textContent,iframes:node.querySelectorAll('iframe').length
 });
 window.__fileDetailsSnapshot=snapshot;
 window.__fileDetailsProbe=node=>{
  const events=[];let overflow=false;
  const record=kind=>{
   if(events.length===100){overflow=true;return}
   events.push({kind,...snapshot(node)});
  };
  const observer=new MutationObserver(()=>record('mutation')),toggle=()=>record('toggle');
  record('armed');
  observer.observe(node,{childList:true,subtree:true,characterData:true,attributes:true});
  node.addEventListener('toggle',toggle);
  return {read:()=>({events:[...events],overflow,snapshot:snapshot(node)}),
   markOpening:()=>{record('opening');return events.length},
   stop:()=>{observer.disconnect();node.removeEventListener('toggle',toggle)}};
 };
}
const detailsSnapshot=details=>details.evaluate(node=>window.__fileDetailsSnapshot(node));

function observeMetadata(request,payload,client,requestId){
 const args=payload.args||{},gate=observedGate('Exact metadata response completion');
 const record={client,requestedAt:Date.now(),requestId,
  target:{id:args.id,version:args.version,sessionId:args.sessionId}};
 metadataExchanges.push(record);
 const observed={record,wait:()=>gate.wait()};metadataByRequest.set(request,observed);
 const pending=(async()=>{
  try{
   const response=await request.response();assert.ok(response,'Metadata request has no response');
   record.responseAt=Date.now();record.httpStatus=response.status();
   const body=await response.json(),value=body.result;
   const finished=await response.finished();assert.equal(finished,null,'Metadata response did not complete');
   record.completedAt=Date.now();
   record.response={accepted:body.accepted,id:value?.id,version:value?.version,
    filePaths:value?.filePaths,download:value?.download,error:body.error};
   gate.resolve(record);
  }catch(error){record.failedAt=Date.now();record.error=errorEvidence(error);gate.reject(error)}
 })();
 metadataPending.add(pending);pending.finally(()=>metadataPending.delete(pending));
 return observed;
}
function metadataOracle(record,row){
 assert.equal(record.httpStatus,200,'Fresh metadata HTTP response refused');
 assert.ok(record.completedAt,'Fresh metadata response must finish');
 assert.equal(record.response.accepted,true,'Fresh metadata action refused');
 assert.equal(record.response.id,row.id,'Fresh metadata artifact changed');
 assert.equal(record.response.version,row.version,'Fresh metadata version changed');
 assert.deepEqual(record.response.filePaths,row.filePaths,'Fresh metadata paths differ from fixture provenance');
}
function freshRenderReady(state,openingIndex,version){
 const current=state.snapshot;
 const loadedThisOpening=state.events.slice(openingIndex).some(event=>event.open&&event.loading);
 return loadedThisOpening&&current.open&&!current.loading&&(current.errors.length>0||
  current.paragraphs.some(item=>item.text===`Saved version ${version}`&&item.visible));
}
// Poll only lifecycle readiness, NEVER the expected button/path counts. Once a
// fresh result is rendered, the exact oracle below runs once and fails on a
// settled missing control. An unchanged old version label is not fresh evidence.
async function freshDetails(target,details,row,trigger){
 const trace={client:pageLabels.get(target),expected:{id:row.id,version:row.version,sessionId:seed.sessionId},
  armedAt:Date.now(),matches:[]};detailsReadiness.push(trace);
 const probe=await bounded(details.evaluateHandle(node=>window.__fileDetailsProbe(node)),2000,'Details probe setup timed out');
 const readProbe=()=>bounded(probe.evaluate(value=>value.read()),1000,'Details snapshot timed out');
 const failures=[];
 let matched;
 const requestGate=observedGate('Fresh opening metadata request');
 const onRequest=request=>{
  const observed=metadataByRequest.get(request);
  if(!observed||JSON.stringify(observed.record.target)!==JSON.stringify(trace.expected))return;
  trace.matches.push(observed.record);if(!matched){matched=observed;requestGate.resolve(observed)}
 };
 try{
  let before=await readProbe();trace.before=before;
  // The rail can retain an open disclosure when its panel closes. Exercise a
  // real close/reopen rather than accepting that prior result as a fresh load.
  if(before.snapshot.open){
   await trigger();
   await expect.poll(async()=>{
    before=await readProbe();
    return !before.snapshot.open&&before.events.some(event=>event.kind==='toggle'&&!event.open);
   },{timeout:4000}).toBe(true);
  }
  trace.closed=before.snapshot;
  assert.equal(metadataExchanges.filter(record=>record.client===trace.client&&
   JSON.stringify(record.target)===JSON.stringify(trace.expected)&&!record.completedAt&&!record.failedAt).length,0,
  'An earlier matching metadata request is still pending');
  target.on('request',onRequest); // Armed BEFORE the native opening gesture.
  const openingIndex=await bounded(probe.evaluate(value=>value.markOpening()),1000,'Opening marker timed out');
  // Synthetic predicate controls only, never evidence of application rendering.
  // Both use the SAME open, visible result; only post-opening loading differs.
  const staleVisibleSnapshot={open:true,visible:true,loading:false,errors:[],
   paragraphs:[{text:`Saved version ${row.version}`,visible:true}]};
  assert.equal(freshRenderReady({events:[],snapshot:staleVisibleSnapshot},0,row.version),false);
  assert.equal(freshRenderReady({events:[{open:true,loading:true}],snapshot:staleVisibleSnapshot},0,row.version),true);
  trace.openingAt=Date.now();await trigger();
  const observed=await requestGate.wait();await observed.wait();
  metadataOracle(observed.record,row);
  let state;
  await expect.poll(async()=>{
   state=await readProbe();trace.latest=state;
   // Require this opening's observed loading -> result transition, even when
   // React reuses its paragraph. If no transition was observed, fail readiness;
   // do not infer a new render from receipt completion or a retained label.
   return freshRenderReady(state,openingIndex,row.version);
  },{timeout:4000}).toBe(true);
  assert.equal(trace.matches.length,1,'More than one matching metadata request during this opening');
  assert.equal(state.overflow,false,'Details transition evidence exceeded its bound');
  controlsSnapshotOracle(state.snapshot,row);
  trace.settledAt=Date.now();trace.passed=true;
 }catch(error){trace.error=errorEvidence(error);failures.push(error)}
 finally{
  target.off('request',onRequest);
  try{trace.final=await bounded(probe.evaluate(value=>{const result=value.read();value.stop();return result}),
   1500,'Final details capture timed out')}
  catch(error){trace.captureError=errorEvidence(error);failures.push(error)}
  try{await bounded(probe.dispose(),1000,'Details probe disposal timed out')}
  catch(error){trace.disposeError=errorEvidence(error);failures.push(error)}
 }
 if(failures.length===1)throw failures[0];
 if(failures.length)throw new AggregateError(failures,'Details readiness and diagnostic capture failed');
}
async function openDetails(target,row,mode='click'){
 const details=detailsFor(target,row),summary=details.locator('summary');
 await expect(summary).toHaveText('File details');
 if(mode==='keyboard'){
  await card(target,row).getByRole('button',{name:'Preview in chat',exact:true}).focus();
  await target.keyboard.press('Tab');await expect(summary).toBeFocused();
 }
 await freshDetails(target,details,row,()=>mode==='keyboard'?target.keyboard.press('Enter'):
  mode==='touch'?summary.tap():summary.click());
 return details;
}

// Positive and deliberate missing-button cases consume the SAME atomic snapshot
// predicate. No sequential locator counts, retries or synthesized replacements.
function controlsSnapshotOracle(snapshot,row){
 assert.equal(snapshot.tag,'DETAILS');assert.equal(snapshot.open,true);
 assert.equal(snapshot.visible,true);assert.equal(snapshot.loading,false);assert.deepEqual(snapshot.errors,[]);
 const expected=row.filePaths;
 for(const [name,present] of [
  ['Copy full path',!!expected.absolute],['Copy relative path',!!expected.relative],['Download saved file',true]]){
  const controls=snapshot.controls.filter(item=>item.label===name);
  assert.equal(controls.length,present?1:0,`Required control mismatch: ${name}`);
  if(present){assert.equal(controls[0].tag,'BUTTON',`${name} must be a native button`);assert.equal(controls[0].visible,true)}
 }
 const codes=snapshot.codes.filter(item=>item.visible).map(item=>item.text);
 for(const value of Object.values(expected))assert.ok(codes.includes(value),`Missing displayed path/base: ${value}`);
 if(!expected.absolute)assert.ok(snapshot.text.includes('Saved in this chat; no backing filesystem path.'));
 else if(!expected.relative)assert.ok(snapshot.text.includes('No relative path:'));
 assert.equal(snapshot.iframes,0,'File details must not mount a preview');
}
async function controlsOracle(details,row){
 const snapshot=await detailsSnapshot(details);
 detailsReadiness.push({kind:'atomic-oracle',at:Date.now(),expected:{id:row.id,version:row.version},snapshot});
 controlsSnapshotOracle(snapshot,row);
}

async function copy(target,details,name,expected,trigger){
 const sentinel=`clipboard-sentinel:${name}:${randomUUID()}`;
 await target.evaluate(value=>navigator.clipboard.writeText(value),sentinel);
 assert.equal(await target.evaluate(()=>navigator.clipboard.readText()),sentinel);
 await trigger(button(details,name));
 const status=button(details,name).locator('..').getByRole('status');
 await expect(status).toHaveText('Copied.');
 await expect.poll(()=>target.evaluate(()=>navigator.clipboard.readText()),{timeout:3000}).toBe(expected);
 checks.push({check:'real clipboard + Copied.',control:name,expected,sentinel});
}

// Save the browser's actual download before checking the independently seeded
// bytes/hash/name. This same oracle must reject the corrupted-download control.
async function saveAndVerify(target,details,row,label,trigger=control=>control.click()){
 return saveControlAndVerify(target,button(details,'Download saved file'),row,label,trigger);
}
async function saveControlAndVerify(target,control,row,label,trigger=control=>control.click()){
 assert.equal(await control.evaluate(node=>node.tagName),'BUTTON','Exercise the rendered native download control');
 const event=target.waitForEvent('download',{timeout:5000});
 // Attach both rejection handlers immediately, including when a failed gesture
 // leaves the download wait to time out during cleanup.
 const [download]=await Promise.all([event,trigger(control)]);
 assert.equal(await download.failure(),null);
 assert.equal(download.suggestedFilename(),row.filename,'Filename must describe the saved source');
 const path=join(evidence,label+'-'+row.filename);
 await download.saveAs(path);
 const bytes=await readFile(path);
 const observed={label,filename:download.suggestedFilename(),bytes:bytes.length,sha256:digest(bytes)};
 downloads.push(observed);
 assert.equal(observed.sha256,row.sha256,'Saved file SHA-256 mismatch');
 assert.equal(bytes.length,row.byteLength,'Saved file byte length mismatch');
 assert.deepEqual(bytes,Buffer.from(row.body,'utf8'),'Saved file changed original Unicode/CRLF bytes');
 await expect(control.locator('..').getByRole('status')).toHaveText('Download started. Check your browser’s downloads.');
 return observed;
}

async function preserved(target,draft){
 const value=await target.evaluate(()=>{
  const s=window.amplifier.getState(),c=s.canvas;
  return {selected:s.selectedSessionId,draft:s.view.draft,canvas:{id:c.id,version:c.selectedVersion,readOnly:c.readOnlyVersion},
   views:s.canvasWorkspace?.views||[],iframeSightings:window.__fileControlsFrames||[]};
 });
 assert.equal(value.selected,seed.sessionId);
 assert.equal(value.draft,draft,'File controls changed the private unsent draft');
 assert.deepEqual(value.canvas,{id:seed.unrelated.id,version:1,readOnly:true},'Unrelated historical Canvas selection changed');
 const ids=new Set(Object.values(seed.artifacts).map(row=>row.id));
 assert.deepEqual(value.views.filter(view=>ids.has(view.resourceId)),[],'Details/download acquired a generated-artifact renderer lease');
 assert.deepEqual(value.iframeSightings,[],'Details/download mounted an HTML iframe, even transiently');
 assert.equal(await target.locator('iframe').count(),0);
 assert.equal(await target.getByRole('textbox',{name:'Message Amplifier',exact:true}).innerText(),draft);
}

async function measureControls(target,controls,label){
 for(const control of controls){
  await control.scrollIntoViewIfNeeded();
  // The inspector has a sticky heading. Center within real scroll containers
  // before hit testing rather than accepting a button covered by that heading.
  await control.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
  const measured=await control.evaluate(node=>{
   const r=node.getBoundingClientRect(),inset=3;
   const points=[[r.left+inset,r.top+inset],[r.right-inset,r.top+inset],
    [r.left+inset,r.bottom-inset],[r.right-inset,r.bottom-inset]];
   return {name:node.getAttribute('aria-label')||node.textContent.trim(),rect:r.toJSON(),
    viewport:{width:innerWidth,height:innerHeight},corners:points.map(([x,y])=>({x,y,
     reachable:x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&node.contains(document.elementFromPoint(x,y))}))};
  });
  geometry.push({label,...measured});
  assert.ok(measured.rect.width>=44&&measured.rect.height>=44,`${label} ${measured.name}: target below 44px`);
  assert.ok(measured.corners.every(point=>point.reachable),`${label} ${measured.name}: obscured/offscreen corner`);
 }
 assert.ok(await target.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Document horizontal overflow');
}
async function measure(target,details,label){
 await measureControls(target,[details.locator('summary'),...names.map(name=>button(details,name))],label);
 assert.ok(await details.evaluate(node=>node.scrollWidth<=node.clientWidth),'File-details horizontal overflow');
 await target.screenshot({path:join(evidence,label+'-controls.png')});
 await details.locator('summary').scrollIntoViewIfNeeded();
 await target.screenshot({path:join(evidence,label+'-paths.png')});
}
async function cornerTap(control,corner){
 await control.scrollIntoViewIfNeeded();
 await control.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
 assert.ok(['top-left','top-right','bottom-left','bottom-right'].includes(corner));
 const measured=await control.evaluate((node,corner)=>{
  const describe=element=>element&&({tag:element.tagName,id:element.id,className:element.className,
   label:element.getAttribute('aria-label')||element.textContent.trim().slice(0,120)});
  const sample=()=>{
   const r=node.getBoundingClientRect(),style=getComputedStyle(node);
   const borders={left:parseFloat(style.borderLeftWidth),top:parseFloat(style.borderTopWidth),
    right:parseFloat(style.borderRightWidth),bottom:parseFloat(style.borderBottomWidth)};
   const oldPosition={x:corner.endsWith('left')?3:r.width-3,y:corner.startsWith('top')?3:r.height-3};
   const intended={x:r.left+oldPosition.x,y:r.top+oldPosition.y};
   // Playwright's explicit positions start at the padding box, not the border
   // box used by getBoundingClientRect and the four-corner reachability oracle.
   const position={x:oldPosition.x-borders.left,y:oldPosition.y-borders.top};
   const hit=point=>({point,reachable:node.contains(document.elementFromPoint(point.x,point.y)),
    elements:document.elementsFromPoint(point.x,point.y).map(describe)});
   const fromPosition=offset=>({x:r.left+borders.left+offset.x,y:r.top+borders.top+offset.y});
   const ancestors=[];for(let parent=node.parentElement;parent;parent=parent.parentElement){
    if(parent.scrollTop||parent.scrollLeft)ancestors.push({node:describe(parent),x:parent.scrollLeft,y:parent.scrollTop});
   }
   return {corner,node:describe(node),rect:r.toJSON(),borders,
    radii:[style.borderTopLeftRadius,style.borderTopRightRadius,style.borderBottomLeftRadius,style.borderBottomRightRadius],
    scroll:{x:scrollX,y:scrollY,ancestors},viewport:{width:innerWidth,height:innerHeight},
    intended,position,oldPosition,corrected:hit(fromPosition(position)),old:hit(fromPosition(oldPosition))};
  };
  window.__fileControlsLastTap=null;
  node.addEventListener('pointerdown',event=>{
   window.__fileControlsLastTap={...sample(),event:{x:event.clientX,y:event.clientY,
    pointerType:event.pointerType,target:describe(event.target),sameNode:node.contains(event.target)}};
  },{capture:true,once:true});
  return sample();
 },corner);
 geometry.push({label:'corner-tap-before',...measured});
 const exactCorner=sample=>{
  assert.deepEqual(sample.point,measured.intended,'Tap must preserve the measured 3px border-box corner');
  assert.ok(sample.reachable,'Exact corner must hit the native control');
 };
 exactCorner(measured.corrected);
 if(measured.borders.left||measured.borders.top){
  assert.throws(()=>exactCorner(measured.old),/Tap must preserve the measured 3px border-box corner/);
  checks.push({check:'old padding-origin conversion rejected by same exact-corner oracle',corner,
   oldPoint:measured.old.point,intended:measured.intended,oldReachable:measured.old.reachable});
 }
 await control.tap({position:measured.position});
 const delivered=await control.evaluate(()=>window.__fileControlsLastTap);
 geometry.push({label:'corner-tap-delivered',...delivered});
 assert.ok(delivered?.event?.sameNode,'Touch must reach the native control');
 assert.deepEqual(delivered.rect,measured.rect,'Control geometry changed between measurement and gesture');
 assert.deepEqual(delivered.scroll,measured.scroll,'Control scrolled between measurement and gesture');
 assert.equal(delivered.event.pointerType,'touch');
 // Chromium may quantize fractional CSS coordinates; retain actual delivery.
 assert.ok(Math.abs(delivered.event.x-measured.intended.x)<1&&
  Math.abs(delivered.event.y-measured.intended.y)<1,'Delivered touch missed the measured corner');
}

async function connect(owner,options){
 const label=owner.label,target=await ownerPage(owner,ready.url,options);pageLabels.set(target,label);
 target.on('pageerror',error=>errors.push(error.message));
 target.on('request',request=>{
  const pathname=new URL(request.url()).pathname;
  if(pathname.startsWith('/api/canvas/'))requests.push({client:label,path:pathname,query:new URL(request.url()).search});
  if(request.method()==='POST'&&pathname==='/api/actions'){
   const payload=request.postDataJSON(),command=payload?.action==='shell.command'?(payload.args.command||payload.args):payload;
   commands.push(command);
   if(command?.action==='canvas.versions.inspect')observeMetadata(request,command,label,payload.id);
  }
 });
 await target.addInitScript(installDetailsProbe);
 await target.addInitScript(()=>{
  window.__fileControlsFrames=[];
  new MutationObserver(records=>{
   for(const record of records){
    if(record.type==='attributes'&&record.target.tagName==='IFRAME')window.__fileControlsFrames.push(record.target.getAttribute('src')||'inline iframe');
    for(const node of record.addedNodes||[])if(node.nodeType===1){
     if(node.tagName==='IFRAME')window.__fileControlsFrames.push(node.getAttribute('src')||'inline iframe');
     for(const frame of node.querySelectorAll('iframe'))window.__fileControlsFrames.push(frame.getAttribute('src')||'inline iframe');
    }
   }
  }).observe(document,{childList:true,subtree:true,attributes:true,attributeFilter:['src','srcdoc']});
 });
 await target.goto(ready.url);
 await target.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 return target;
}

const documentRequests=target=>requests.filter(row=>row.path.endsWith('/document')&&row.client===pageLabels.get(target));
async function previewState(target,row){
 return target.evaluate(id=>{
  const s=window.amplifier.getState(),c=s.canvas;
  return {selected:s.selectedSessionId,draft:s.view.draft,
   canvas:{id:c.id,version:c.selectedVersion,readOnly:c.readOnlyVersion},
   views:(s.canvasWorkspace?.views||[]).filter(view=>view.resourceId===id).map(view=>({
    viewId:view.viewId,resourceId:view.resourceId,resourceRevision:view.resourceRevision,generation:view.generation,
    selectedVersion:view.selectedVersion
   })),iframeSightings:window.__fileControlsFrames||[]};
 },row.id);
}
const executionMarker=frame=>frame.evaluate(()=>({
 count:window.fileControlsExecutions,executed:document.body.dataset.fileControlsExecuted,
 token:document.body.dataset.fileControlsToken
}));

async function startPreview(target,row,draft,label){
 // A separate, deliberate preview SETUP, after the card-only no-document oracle.
 // Never classify this canvas.select or its document request as a download.
 const before=documentRequests(target).length;
 await action(target,'view.update',{patch:{canvasWidth:900,canvasControlsExpanded:false,canvasFocused:false}});
 await action(target,'canvas.select',{id:row.id,sessionId:seed.sessionId,version:row.version,presentation:'canvas'});
 const panel=target.locator('#workspace-canvas');
 await expect(panel).toBeVisible();
 const locator=panel.locator('.a-canvas-workspace iframe.a-canvas-html');
 await expect(locator).toHaveCount(1);
 const element=await locator.elementHandle();
 try{
  const frame=await element.contentFrame();assert.ok(frame,'Primary HTML frame must be attached');
  await expect(frame.getByRole('heading',{name:'Original résumé α — 雪',exact:true})).toBeVisible();
  await frame.waitForFunction(()=>window.fileControlsExecutions===1&&document.body.dataset.fileControlsToken,undefined,{timeout:4000});
  const input=frame.getByRole('textbox',{name:'Unfinished preview note',exact:true});
  const unfinished=`Unsubmitted ${label} note — ${randomUUID()}`;
  await input.fill(unfinished);
  const marker=await executionMarker(frame);
  assert.equal(marker.count,1);assert.equal(marker.executed,'1');
  assert.match(marker.token,/^\d+-\d+-\d+-\d+$/,'Fixture script must have generated a fresh execution token');
  const state=await previewState(target,row);
  assert.equal(state.selected,seed.sessionId);assert.equal(state.draft,draft);
  assert.deepEqual(state.canvas,{id:row.id,version:row.version,readOnly:true});
  assert.equal(state.views.length,1);assert.equal(state.views[0].viewId,'primary');
  const documents=documentRequests(target);
  assert.equal(documents.length,before+1,'Preview setup must load exactly one document');
  assert.equal(documents.at(-1).path,`/api/canvas/${row.id}/document`);
  const retained={target,row,panel,locator,element,frame,input,unfinished,marker,state,documents,commandStart:commands.length,label};
  checks.push({check:'deliberate saved-v1 preview setup, not a download',label,marker,state,documents});
  await previewPreserved(retained);
  return retained;
 }catch(error){await element.dispose();throw error}
}
async function previewPreserved(retained){
 const {target,row,locator,element,frame,input,unfinished,marker,state,documents}=retained;
 assert.equal(await element.evaluate(node=>node.isConnected),true,'Running preview iframe was removed');
 assert.equal(await locator.evaluate((node,original)=>node===original,element),true,'Running preview iframe was replaced');
 assert.equal(await target.locator('iframe').count(),1,'A second preview frame was mounted');
 assert.deepEqual(await executionMarker(frame),marker,'Preview execution marker changed: script reran or frame reloaded');
 assert.equal(await input.inputValue(),unfinished,'Unfinished preview input changed');
 assert.deepEqual(await previewState(target,row),state,'File controls changed selection, draft, renderer lease or frame attributes');
 assert.deepEqual(documentRequests(target),documents,'File controls issued an extra HTML document request');
 assert.deepEqual(commands.slice(retained.commandStart).filter(row=>
  ['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action)),[],
 'File controls selected/mounted a preview or sent input');
}
async function tabTo(target,control){
 // Traverse the real tab order; do not turn a missing/unreachable control into
 // a pass by clicking via evaluate or directly focusing the destination.
 for(let step=0;step<16;step++){
  if(await control.evaluate(node=>node===document.activeElement))return;
  await target.keyboard.press('Tab');
 }
 throw Error(`Native keyboard traversal did not reach ${await control.getAttribute('aria-label')||await control.innerText()}`);
}
async function usePreviewControls(retained,label,mode){
 const {target,panel,row}=retained;
 const primary=panel.locator('.a-canvas-primary-actions').getByRole('button',{name:'Download canvas source',exact:true});
 const toggle=panel.getByRole('button',{name:'Canvas details',exact:true});
 const inspector=panel.getByRole('region',{name:'Canvas details',exact:true});
 await expect(inspector).toBeHidden();
 await expect(primary).toBeVisible();
 assert.equal(await primary.evaluate(node=>node.tagName),'BUTTON');
 await measureControls(target,[primary,toggle],label+'-primary-before');
 if(mode==='keyboard'){
  // Copy precedes Download in the actual primary action row.
  await panel.locator('.a-canvas-primary-actions').getByRole('button',{name:'Copy canvas source',exact:true}).focus();
  await target.keyboard.press('Tab');await expect(primary).toBeFocused();
  await saveControlAndVerify(target,primary,row,label+'-primary-v1',()=>target.keyboard.press('Enter'));
 }else await saveControlAndVerify(target,primary,row,label+'-primary-v1',control=>cornerTap(control,'bottom-right'));
 await previewPreserved(retained);
 await measureControls(target,[primary,toggle],label+'-primary-after');
 const notice=primary.locator('..').getByRole('status');
 await expect(notice).toBeVisible();
 const feedback=await notice.evaluate(node=>{
  const r=node.getBoundingClientRect(),bar=node.closest('.a-canvas-artifact-toolbar').getBoundingClientRect();
  const range=document.createRange();range.selectNodeContents(node);
  const lines=[...range.getClientRects()].map(rect=>rect.toJSON());
  return {rect:r.toJSON(),bar:bar.toJSON(),lines,contained:r.left>=bar.left&&r.right<=bar.right&&r.bottom<=bar.bottom&&
   r.right<=innerWidth&&r.bottom<=innerHeight&&lines.every(line=>line.left>=r.left-1&&line.right<=r.right+1&&line.bottom<=r.bottom+1)};
 });
 geometry.push({label:label+'-visible-feedback',...feedback});
 assert.ok(feedback.contained&&feedback.lines.length>0,'Download completion feedback must remain visible and bounded');
 await target.screenshot({path:join(evidence,label+'-primary.png')});
 if(mode==='keyboard'){
  // Download temporarily disables its button. Re-anchor after completion so a
  // browser's native disabled-control blur is not mistaken for wrong tab order.
  await expect(primary).toBeEnabled();await primary.focus();
  await target.keyboard.press('Tab');await expect(toggle).toBeFocused();await target.keyboard.press('Enter');
 }else await toggle.tap();
 await expect(inspector).toBeVisible();
 await expect(panel.locator('.a-canvas-content')).toHaveAttribute('data-details-overlay',mode==='keyboard'?'false':'true');
 const details=inspector.locator('details.a-canvas-file-path'),summary=details.locator('summary');
 await expect(summary).toHaveText('File details');
 // This is the existing right-rail portal, not the similarly named chat card.
 assert.equal(await details.count(),1);
 if(mode==='keyboard'){
  await expect(inspector.getByRole('button',{name:'Close canvas details',exact:true})).toBeFocused();
  await tabTo(target,summary);
 }
 await freshDetails(target,details,row,()=>mode==='keyboard'?target.keyboard.press('Enter'):summary.tap());
 await controlsOracle(details,row);
 for(const [name,value] of [[names[0],row.filePaths.absolute],[names[1],row.filePaths.relative]]){
  if(mode==='keyboard'){
   await tabTo(target,button(details,name));await expect(button(details,name)).toBeFocused();
   await copy(target,details,name,value,()=>target.keyboard.press('Enter'));
  }else await copy(target,details,name,value,control=>cornerTap(control,name===names[0]?'top-left':'bottom-right'));
  await previewPreserved(retained);
 }
 if(mode==='keyboard'){
  await tabTo(target,button(details,names[2]));await expect(button(details,names[2])).toBeFocused();
  await saveAndVerify(target,details,row,label+'-rail-v1',()=>target.keyboard.press('Enter'));
 }else await saveAndVerify(target,details,row,label+'-rail-v1',control=>cornerTap(control,'bottom-right'));
 await previewPreserved(retained);
 await measure(target,details,label+'-rail');
 const close=inspector.getByRole('button',{name:'Close canvas details',exact:true});
 if(mode==='keyboard'){
  await button(details,names[2]).focus();await target.keyboard.press('Escape');
 }else await close.tap();
 await expect(inspector).toBeHidden();
 await previewPreserved(retained);
 await target.screenshot({path:join(evidence,label+'-retained-preview.png')});
 checks.push({check:'right-rail paths/download and primary Download preserve running saved-v1 HTML',label,mode,
  marker:retained.marker,unfinished:retained.unfinished,documents:documentRequests(target),state:await previewState(target,row)});
}

async function run(){
 ready=await startup;
 if(process.env.AMPLIFIER_TEST_INSTALLED==='1'||process.env.AMPLIFIER_TEST_PACKAGE_ROOT)assert.equal(ready.installedVerified,true);
 const desktopOwner=await launchOwner('desktop',browserOwners);
 page=await connect(desktopOwner,desktopOptions);
 await action(page,'session.create',{title:'Generated file controls'});
 await showFixtureConversation(page,{navPinned:false});
 seed=await fixturePost(page,'seed');
 assert.notEqual(seed.workspace,seed.workingDirectory);
 const {html,adjacent,saved,outside}=seed.artifacts;
 const draft='Keep this unsent file-inspection draft — α.';
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.fill(draft);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe(draft);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas.readOnlyVersion)).toBe(true);
 const controlsStart=commands.length;
 let details=await openDetails(page,html,'keyboard');
 await controlsOracle(details,html);
 const metadata=await action(page,'canvas.versions.inspect',{id:html.id,sessionId:seed.sessionId,version:1});
 assert.equal(metadata.version,1);assert.deepEqual(metadata.filePaths,html.filePaths);
 assert.equal(metadata.download.filename,html.filename);
 assert.equal(new URL(metadata.download.url,ready.url).searchParams.get('version'),'1');
 assert.equal(metadata.source,undefined,'Metadata inspect must not include the document body');

 // No second fixture or build: remove the real buttons, run the exact positive
 // oracle, then restore the same DOM nodes before any React state changes.
 await details.evaluate(node=>{node.__removed=[...node.querySelectorAll('button')].map(button=>({button,parent:button.parentNode,next:button.nextSibling}));for(const row of node.__removed)row.button.remove()});
 try{await assert.rejects(()=>controlsOracle(details,html),/Required control mismatch/);checks.push({check:'negative control: missing buttons rejected by the same oracle'})}
 finally{await details.evaluate(node=>{for(const row of node.__removed)row.parent.insertBefore(row.button,row.next);delete node.__removed})}
 await controlsOracle(details,html);
 await details.locator('summary').focus();
 for(const [name,value] of [[names[0],html.filePaths.absolute],[names[1],html.filePaths.relative]]){
  await page.keyboard.press('Tab');await expect(button(details,name)).toBeFocused();
  await copy(page,details,name,value,()=>page.keyboard.press('Enter'));
 }
 // Delay the saved URL itself, after the click has captured v1. Publish v2
 // through the maintained action before allowing the original GET to resolve.
 const downloadUrl=url=>url.pathname===new URL(metadata.download.url,ready.url).pathname&&url.searchParams.get('version')==='1';
 let raceObserved=false,raceError;
 await page.route(downloadUrl,async route=>{
  try{
   assert.equal(new URL(route.request().url()).searchParams.get('version'),'1');
   const revision=await fixturePost(page,'newer');assert.equal(revision.revision,2);
   raceObserved=true;await route.continue();
  }catch(error){raceError=error;await route.abort()}
 },{times:1});
 await page.keyboard.press('Tab');await expect(button(details,names[2])).toBeFocused();
 try{await saveAndVerify(page,details,html,'desktop-race-v1',()=>page.keyboard.press('Enter'))}
 catch(error){throw raceError||error}
 assert.equal(raceObserved,true,'Revision race did not run');
 checks.push({check:'v1 browser download remains original after v2 is saved before URL resolution'});
 await preserved(page,draft);
 await measure(page,details,'desktop');

 // Inject only clipboard write failure; readText remains the real browser API.
 const deniedSentinel='denied-write:'+randomUUID();
 await page.evaluate(value=>navigator.clipboard.writeText(value),deniedSentinel);
 await page.evaluate(()=>{window.__clipboardDescriptor=Object.getOwnPropertyDescriptor(navigator.clipboard,'writeText');Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:()=>Promise.reject(new DOMException('Synthetic denied write','NotAllowedError'))})});
 try{
  await button(details,names[0]).click();
  await expect(button(details,names[0]).locator('..').getByRole('status')).toHaveText('Clipboard access was denied. Select the block text to copy it.');
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),deniedSentinel);
  checks.push({check:'clipboard refusal reports failure and leaves real clipboard unchanged'});
 }finally{await page.evaluate(()=>{if(window.__clipboardDescriptor)Object.defineProperty(navigator.clipboard,'writeText',window.__clipboardDescriptor);else delete navigator.clipboard.writeText;delete window.__clipboardDescriptor})}
 await preserved(page,draft);

 let observedDownloads=0;const countDownload=()=>observedDownloads++;page.on('download',countDownload);
 await page.route(downloadUrl,route=>route.fulfill({status:403,contentType:'text/plain',body:'Synthetic guarded refusal'}),{times:1});
 await button(details,names[2]).click();
 await expect(details.locator('.a-saved-file-download [role="status"]')).toHaveText('The saved file could not be downloaded (403).');
 assert.equal(observedDownloads,0,'403 must not create an error-page download');
 await expect(button(details,names[2])).toBeEnabled();
 await saveAndVerify(page,details,html,'desktop-after-403');
 assert.equal(observedDownloads,1,'A legitimate subsequent click must download once');
 page.off('download',countDownload);
 checks.push({check:'403 has visible feedback and zero downloads, then a legitimate click succeeds'});
 await preserved(page,draft);

 // The real UI saves this deliberately normalized response. The SAME byte/hash
 // oracle must fail, rather than accepting a download event or a .html filename.
 await page.route(downloadUrl,route=>route.fulfill({status:200,headers:{'Content-Type':'text/html; charset=utf-8',
  'Content-Disposition':'attachment; filename="fixture.html"'},body:Buffer.from(html.body.replaceAll('\r\n','\n'))}),{times:1});
 await assert.rejects(()=>saveAndVerify(page,details,html,'negative-wrong-bytes'),/Saved file SHA-256 mismatch/);
 checks.push({check:'negative control: real wrong-byte download rejected by the same SHA-256 oracle'});
 await preserved(page,draft);

 const deleted=await fixturePost(page,'delete-source');assert.equal(deleted.sourceExists,false);
 await page.reload();await showFixtureConversation(page,{navPinned:false});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(seed.sessionId);
 details=await openDetails(page,html);
 await controlsOracle(details,html);
 for(const [name,value] of [[names[0],html.filePaths.absolute],[names[1],html.filePaths.relative]])await copy(page,details,name,value,control=>control.click());
 await saveAndVerify(page,details,html,'reload-deleted-source-v1');
 await preserved(page,draft);
 checks.push({check:'reload after source deletion retains paths, exact saved v1 download and unrelated read-only Canvas/draft'});

 for(const row of [adjacent,saved,outside]){
  const other=await openDetails(page,row);await controlsOracle(other,row);
  if(row.filePaths.absolute)await copy(page,other,names[0],row.filePaths.absolute,control=>control.click());
  if(row.filePaths.relative)await copy(page,other,names[1],row.filePaths.relative,control=>control.click());
  if(row!==outside)await saveAndVerify(page,other,row,row===adjacent?'adjacent-markdown':'saved-only');
  await preserved(page,draft);
  await other.locator('summary').click();
 }
 assert.notEqual(adjacent.sha256,html.sha256,'Adjacent specimen must detect cross-file confusion');
 checks.push({check:'adjacent Markdown stays distinct; direct saved-only has no paths; outside-current-base has no relative-copy control'});
 // Open disclosure survives placement changes, but never retains a stale base.
 await placement(page);
 await expect(button(details,'Copy relative path')).toHaveCount(0);
 await expect(details.getByText(html.filePaths.relative,{exact:true})).toHaveCount(0);
 await expect(details.getByText(html.filePaths.absolute,{exact:true})).toBeVisible();
 await placement(page,true);
 await expect(button(details,'Copy relative path')).toBeVisible();
 await controlsOracle(details,html);
 // Hold only the FIRST matching request. Playwright 1.63.0 auto-continues a held
 // route if unroute runs early, so fulfill and await delivery BEFORE unregistering.
 // Later metadata requests must continue while the old receipt is held.
 await details.locator('summary').click();
 let releaseMetadata,metadataHeld=false,metadataClosing=false,metadataCleanup;
 const metadataCallbacks=new Set(),metadataErrors=[];
 const metadataGate=new Promise(resolve=>{releaseMetadata=resolve});
 const acceptedGate=observedGate('Old metadata acceptance'),deliveredGate=observedGate('Old metadata delivery');
 const delayMetadata=route=>{
  const pending=(async()=>{
   try{
    const data=route.request().postDataJSON();
    if(metadataClosing||metadataHeld||data?.action!=='canvas.versions.inspect'||data.args?.id!==html.id){
     await route.continue();return;
    }
    metadataHeld=true; // Synchronous, before route.fetch yields.
    const response=await route.fetch({timeout:5000});
    assert.equal(response.status(),200,'Held metadata request failed');
    acceptedGate.resolve();await metadataGate;
    await route.fulfill({response});deliveredGate.resolve();
   }catch(error){
    acceptedGate.reject(error);
    let deliveredError=error;
    try{await route.abort()}catch(abortError){deliveredError=new AggregateError([error,abortError],'Metadata route and abort failed')}
    // Even a later continue failure, after a gate settled, is retained and
    // thrown by drainMetadata. No rejected callback is left unobserved.
    metadataErrors.push(deliveredError);deliveredGate.reject(deliveredError);
   }
  })();
  metadataCallbacks.add(pending);
  return pending;
 };
 // Idempotent: the local finally and the overall deadline cleanup share this
 // release. A callback failure is rethrown by an awaited gate, including cleanup.
 const drainMetadata=()=>metadataCleanup??=(async()=>{
  metadataClosing=true;releaseMetadata();
  const failures=[];
  try{if(metadataHeld)await deliveredGate.wait()}catch(error){failures.push(error)}
  try{await page.unroute('**/api/actions',delayMetadata)}catch(error){failures.push(error)}
  try{await bounded(Promise.all([...metadataCallbacks]),8000,'Metadata callbacks did not settle')}catch(error){failures.push(error)}
  failures.push(...metadataErrors);
  if(failures.length)throw new AggregateError([...new Set(failures)],'Metadata interceptor failed');
 })();
 await page.route('**/api/actions',delayMetadata);
 routeCleanups.add(drainMetadata);
 try{
  await details.locator('summary').click();await acceptedGate.wait();
  assert.equal(metadataHeld,true);
  await placement(page);
  await expect.poll(()=>page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(row=>row.id===s.selectedSessionId)?.workingDirectory})).toBe(seed.workspace);
  await expect(button(details,'Copy relative path')).toHaveCount(0);
  // The app serializes actions; release the old receipt before expecting the
  // replacement metadata request to finish. Assert after both deliveries.
  releaseMetadata();await deliveredGate.wait();
  await expect(details).toContainText('No relative path:');
  await expect(details.getByText(seed.workspace,{exact:true})).toBeVisible();
  await expect(button(details,'Copy relative path')).toHaveCount(0);
  await expect(details.getByText(html.filePaths.relative,{exact:true})).toHaveCount(0);
 }finally{
  try{await drainMetadata()}finally{routeCleanups.delete(drainMetadata)}
 }
 await placement(page,true);
 await expect(button(details,'Copy relative path')).toBeVisible();
 checks.push({check:'open details refreshes current working folder and rejects delayed prior-placement metadata'});
 const desktopCommands=commands.slice(controlsStart);
 assert.deepEqual(desktopCommands.filter(row=>['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action)),[],
  'File actions must not select/mount previews or send messages');

 // A real touch-enabled context, not just a resized desktop. Reuse the same
 // retained artifact and positive oracles at both narrow widths.
 const touchOwner=await launchOwner('touch',browserOwners);
 const mobile=await connect(touchOwner,touchOptions);
 await action(mobile,'session.select',{id:seed.sessionId});
 await showFixtureConversation(mobile,{navPinned:false});
 await action(mobile,'canvas.select',{id:seed.unrelated.id,sessionId:seed.sessionId,version:1,presentation:'canvas'});
 await action(mobile,'canvas.visibility',{open:false,sessionId:seed.sessionId,canvasId:seed.unrelated.id});
 const mobileDraft='Keep touch draft — unsubmitted.';
 await mobile.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill(mobileDraft);
 await expect.poll(()=>mobile.evaluate(()=>window.amplifier.getState().view.draft)).toBe(mobileDraft);
 const touchStart=commands.length;
 for(const width of [390,320]){
  await mobile.setViewportSize({width,height:width===390?844:740});
  assert.ok(await mobile.evaluate(()=>navigator.maxTouchPoints>0));
  const touchDetails=await openDetails(mobile,html,'touch');
  await controlsOracle(touchDetails,html);
  await measure(mobile,touchDetails,`touch-${width}`);
  await copy(mobile,touchDetails,names[0],html.filePaths.absolute,control=>cornerTap(control,'top-left'));
  await copy(mobile,touchDetails,names[1],html.filePaths.relative,control=>cornerTap(control,'bottom-right'));
  await saveAndVerify(mobile,touchDetails,html,`touch-${width}-deleted-v1`,control=>cornerTap(control,'bottom-right'));
  await preserved(mobile,mobileDraft);
  // Leave it open. On the next width freshDetails owns BOTH the close fence and
  // the fresh opening, instead of racing an external close's native toggle.
 }
 assert.deepEqual(commands.slice(touchStart).filter(row=>['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action)),[]);
 checks.push({check:'390px/320px emulated touch, actual corner taps, 44px targets, four-corner hit testing and saved PNGs'});
 // End the card-only phase here. The unrelated read-only Canvas and no-document
 // assertions above remain strict; the next phase intentionally opens our HTML.
 assert.deepEqual(requests.filter(row=>row.path.endsWith('/document')),[],'Card details/download must not request an HTML document');
 checks.push({check:'card-only phase completed with zero document requests and unrelated historical Canvas retained'});
 const desktopPreview=await startPreview(page,html,draft,'desktop');
 try{
  // Meaningful negatives for the same retained-preview oracle, without a second
  // renderer or a deliberate reload that would invalidate the original handle.
  await desktopPreview.input.evaluate(node=>{node.value='Synthetic lost input'});
  try{await assert.rejects(()=>previewPreserved(desktopPreview),/Unfinished preview input changed/)}
  finally{await desktopPreview.input.evaluate((node,value)=>{node.value=value},desktopPreview.unfinished)}
  await desktopPreview.frame.evaluate(()=>{document.body.dataset.fileControlsToken='Synthetic rerun token'});
  try{await assert.rejects(()=>previewPreserved(desktopPreview),/Preview execution marker changed/)}
  finally{await desktopPreview.frame.evaluate(value=>{document.body.dataset.fileControlsToken=value},desktopPreview.marker.token)}
  await previewPreserved(desktopPreview);
  checks.push({check:'negative controls: lost input and changed execution token rejected by the same retained-preview oracle'});
  await usePreviewControls(desktopPreview,'desktop-preview','keyboard');
 }finally{await desktopPreview.element.dispose()}
 await mobile.setViewportSize({width:390,height:844});
 const touchPreview=await startPreview(mobile,html,mobileDraft,'touch');
 try{
  for(const width of [390,320]){
   await mobile.setViewportSize({width,height:width===390?844:740});
   assert.ok(await mobile.evaluate(()=>navigator.maxTouchPoints>0));
   await previewPreserved(touchPreview);
   await usePreviewControls(touchPreview,`touch-${width}-preview`,'touch');
  }
 }finally{await touchPreview.element.dispose()}
 const final=await fixtureRead(page);
 assert.deepEqual(final.sent,[]);assert.equal(final.sourceDeleted,true);assert.equal(final.sourceExists,false);
 assert.equal(final.newer.revision,2);
 assert.equal(requests.filter(row=>row.path.endsWith('/document')).length,2,
  'Only the two deliberate preview setups may request HTML; all copy/download phases must retain them');
 assert.deepEqual(errors,[]);
}

try{
 running=run();
 await Promise.race([running,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Focused file-controls proof exceeded 120 seconds')),120000)})]);
}catch(error){
 failure=error;
 for(const target of browserOwners.flatMap(owner=>owner.browser?.contexts()||[]).flatMap(context=>context.pages()).filter(target=>!target.isClosed())){
  const label=pageLabels.get(target)||'unknown';
  try{preCleanupSnapshots.push({client:label,...await bounded(target.evaluate(()=>({
   at:Date.now(),viewport:{width:innerWidth,height:innerHeight},
   details:[...document.querySelectorAll('details.a-canvas-file-path')].map(window.__fileDetailsSnapshot)
  })),1500,`${label} pre-cleanup details capture timed out`)})}
  catch(error){preCleanupSnapshots.push({client:label,error:errorEvidence(error)})}
  try{await target.screenshot({path:join(evidence,`failure-${label}.png`),timeout:3000})}
  catch(error){screenshotErrors.push({client:label,error:errorEvidence(error)})}
 }
}finally{
 clearTimeout(timer);browserOwners.closing=true;
 try{
  await finishFixture({label:'Generated file controls fixture',
   close:[...Array.from(routeCleanups,release=>['held metadata response',release]),...browserOwners.map(owner=>[`${owner.label} browser`,()=>closeOwner(owner)])],
   stop:async()=>{cleanup=await stop()}});
  assert.equal(records.find(row=>row.cleanup)?.cleanup.homeRemoved,true,'Fixture-owned state/socket directory was not removed');
 }catch(error){failure=failure?new AggregateError([failure,error],'File controls proof and cleanup failed'):error}
 // A deadline is not cancellation. Closing the browser settles outstanding
 // driver calls; await the run before freezing evidence, including its finally.
 if(running)try{await bounded(running,10000,'Driver did not settle after fixture cleanup')}
 catch(error){if(error!==failure)failure=failure?new AggregateError([failure,error],'File controls proof did not settle cleanly'):error}
 try{
  await bounded(Promise.all([...metadataPending]),5000,'Metadata observations did not settle after cleanup');
  assert.deepEqual(metadataExchanges.filter(record=>record.error),[],'Metadata response capture failed');
 }
 catch(error){failure=failure?new AggregateError([failure,error],'Metadata observation cleanup failed'):error}
 const writes=await Promise.allSettled([
  writeFile(join(evidence,'fixture.log'),logs),
  writeFile(join(evidence,'observations.json'),JSON.stringify({passed:!failure,error:failure?.stack,failure:errorEvidence(failure),screenshotErrors,
  installed:ready&&{packageRoot:ready.packageRoot,verified:ready.installedVerified,staticSha256:ready.staticSha256},
  seed,checks,geometry,downloads,requests,commands,metadataExchanges,detailsReadiness,preCleanupSnapshots,
  errors,cleanup,fixtureCleanup:records.find(row=>row.cleanup)?.cleanup,
  browserCleanup:browserOwners.map(owner=>({label:owner.label,closed:owner.closed,connected:owner.browser?.isConnected()??false})),
  limits:['Synthetic runtime and fixture bearer authentication; no provider, login, remote server or physical touch-device claim.',
   'Clipboard/403/wrong-byte faults are deliberate test injections; positive clipboard reads and downloaded files are real browser results.',
   'The two later HTML previews execute deliberately as setup; copy/download must retain each original frame and execution token.',
   'Exact-byte identity covers ordinary saved UTF-8 HTML/Markdown, not Babylon engine-prepending exports.',
   'PNG capture and measured reachability do not claim independent human visual review.']},null,2)+'\n')]);
 const writeErrors=writes.filter(row=>row.status==='rejected').map(row=>row.reason);
 if(writeErrors.length)failure=new AggregateError([...(failure?[failure]:[]),...writeErrors],'File controls evidence write failed');
 const entries=await readdir(evidence,{withFileTypes:true});
 assert.ok(entries.every(entry=>entry.isFile()),'Export only ordinary evidence files, never fixture sockets/profile/state');
}
if(failure)throw failure;
console.log(`Generated file controls passed; evidence: ${evidence}`);
