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
const fileFrameCaptures=[];
const metadataExchanges=[],detailsReadiness=[],preCleanupSnapshots=[],metadataPending=new Set();
const metadataByRequest=new WeakMap();
const pageLabels=new WeakMap(),routeCleanups=new Set(),browserOwners=[];
const cornerDiagnosticMode=process.env.FILE_CONTROLS_CORNER_DIAGNOSTIC==='1';
const cornerDiagnostics=[];
const contourOracle={version:'circular-contour-v1',formula:'d=max(3,r-(r-3)/sqrt(2))',
 logicChecks:[],cornerShapeChecks:[],cornerShapeObservations:[],cornerShapeCaptureErrors:[],browserPreflight:null,
 negativeCounts:{size:0,regions:0,obstruction:0,paddingOrigin:0}};
const cornerShapeLogPrefix='FILE_CONTROLS_CORNER_SHAPES ';
let cornerDiagnosticComplete=false;
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
 window.__fileControlsNodeId=id;
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

// One deterministic circular-contour definition, also installed verbatim in the
// page. No passing-point search, CSS radius rescaling or Chromium pixel snapping.
// Reject geometry outside this deliberately small supported domain.
function circularContour({rect,radii,transforms,fragments,viewport}){
 const require=(condition,message)=>{if(!condition)throw Error(`Unsupported contour geometry: ${message}`)};
 require(['left','top','right','bottom','width','height'].every(key=>Number.isFinite(rect[key])),'nonfinite rect');
 require(rect.width>0&&rect.height>0&&rect.right-rect.left===rect.width&&rect.bottom-rect.top===rect.height,
  'inconsistent border box');
 require(fragments===1,'fragmented border box');
 require(Number.isFinite(viewport.width)&&viewport.width>0&&Number.isFinite(viewport.height)&&viewport.height>0&&
  viewport.scale===1&&viewport.offsetLeft===0&&viewport.offsetTop===0,'visual viewport mapping');
 require(Array.isArray(transforms)&&transforms.length>0&&transforms.every(style=>
  ['transform','perspective','translate','rotate','scale','offsetPath'].every(key=>style[key]==='none')&&
  style.transformStyle==='flat'&&['1','normal'].includes(style.zoom)),'transform/perspective/zoom');
 const px=value=>{
  require(typeof value==='string'&&/^(?:\d+(?:\.\d+)?|\.\d+)px$/.test(value),'radius must be finite computed px');
  const number=Number(value.slice(0,-2));require(Number.isFinite(number)&&number>=0,'nonfinite radius');return number;
 };
 require(Array.isArray(radii)&&radii.length===4,'four circular radii required');
 const values=radii.map(value=>{
  require(typeof value==='string','radius must be computed px');
  const pair=value.trim().split(/\s+/);
  require(pair.length===1||pair.length===2,'radius syntax');
  const radius=px(pair[0]);
  if(pair.length===2)require(px(pair[1])===radius,'elliptical radius');
  return radius;
 });
 const [tl,tr,bl,br]=values;
 require(tl+tr<=rect.width&&bl+br<=rect.width&&tl+bl<=rect.height&&tr+br<=rect.height,
  'overlapping radii require unsupported used-radius scaling');
 return values.map((radius,index)=>{
  const inward=Math.max(3,radius-(radius-3)/Math.sqrt(2));
  require(inward<rect.width/2&&inward<rect.height/2,'four distinct corner regions');
  const right=index%2===1,bottom=index>=2;
  return {corner:['top-left','top-right','bottom-left','bottom-right'][index],radius,inward,
   x:right?rect.right-inward:rect.left+inward,y:bottom?rect.bottom-inward:rect.top+inward};
 });
}
// An unsupported corner-*-shape property returns ''. In that case the browser
// has only the border-radius contour. Chromium 153 serializes round as
// superellipse(1). Accept ONLY these exact computed forms, without normalization.
function supportedCircularCornerShape(value){
 return value===''||value==='round'||value==='superellipse(1)';
}
function installContourSampler(logPrefix){
 const px=value=>{
  if(!/^(?:\d+(?:\.\d+)?|\.\d+)px$/.test(value)||!Number.isFinite(Number(value.slice(0,-2))))
   throw Error('Unsupported contour geometry: border must be finite computed px');
  return Number(value.slice(0,-2));
 };
 window.__fileControlsContourSample=node=>{
  if(window!==window.top||!node.isConnected)throw Error('Unsupported contour geometry: detached or framed control');
  const rect=node.getBoundingClientRect().toJSON(),style=getComputedStyle(node),transforms=[],ancestors=[];
  const cornerShapes=['top-left','top-right','bottom-left','bottom-right'].map(corner=>{
   const raw=style.getPropertyValue(`corner-${corner}-shape`);
   return {corner,raw,quoted:JSON.stringify(raw)};
  });
  // Synchronous console events are collected in Node, not in app state/UI. Emit
  // ALL raw values before validation, and again on rejection: a failed evaluate
  // must not discard the computed value that explains the failed guard.
  const record=stage=>console.debug(logPrefix+JSON.stringify({stage,at:Date.now(),
   nodeId:window.__fileControlsNodeId(node),tag:node.tagName,cornerShapes}));
  record('before-validation');
  for(const {corner,raw} of cornerShapes){
   if(!window.__fileControlsSupportedCircularCornerShape(raw)){
    record('rejected');
    throw Error(`Unsupported contour geometry: noncircular corner shape ${corner}=${JSON.stringify(raw)}; `+
     `cornerShapes=${JSON.stringify(cornerShapes)}`);
   }
  }
  for(let ancestor=node;ancestor;ancestor=ancestor.parentElement){
   const computed=getComputedStyle(ancestor);
   transforms.push(Object.fromEntries(['transform','perspective','translate','rotate','scale','offsetPath','transformStyle','zoom']
    .map(key=>[key,computed[key]])));
   ancestors.push({nodeId:window.__fileControlsNodeId(ancestor),x:ancestor.scrollLeft,y:ancestor.scrollTop});
  }
  const radii=[style.borderTopLeftRadius,style.borderTopRightRadius,style.borderBottomLeftRadius,style.borderBottomRightRadius];
  const viewport={width:innerWidth,height:innerHeight,scale:visualViewport?.scale??1,
   offsetLeft:visualViewport?.offsetLeft??0,offsetTop:visualViewport?.offsetTop??0};
  const points=window.__fileControlsCircularContour({rect,radii,transforms,fragments:node.getClientRects().length,viewport});
  const borders={left:px(style.borderLeftWidth),top:px(style.borderTopWidth),
   right:px(style.borderRightWidth),bottom:px(style.borderBottomWidth)};
  if(borders.left+borders.right>=rect.width||borders.top+borders.bottom>=rect.height)
   throw Error('Unsupported contour geometry: empty padding box');
  const hit=point=>{
   const element=document.elementFromPoint(point.x,point.y);
   return {...point,reachable:point.x>=0&&point.y>=0&&point.x<innerWidth&&point.y<innerHeight&&
    !!element&&node.contains(element),hit:element&&{nodeId:window.__fileControlsNodeId(element),tag:element.tagName}};
  };
  return {nodeId:window.__fileControlsNodeId(node),name:node.getAttribute('aria-label')||node.textContent.trim(),
   tag:node.tagName,rect,radii,cornerShapes,borders,transforms,viewport,scroll:{x:scrollX,y:scrollY,ancestors},
   basis:'four contour-relative native hit probes',corners:points.map(hit),
   legacyCorners:[[rect.left+3,rect.top+3],[rect.right-3,rect.top+3],[rect.left+3,rect.bottom-3],[rect.right-3,rect.bottom-3]]
    .map(([x,y],index)=>hit({corner:points[index].corner,x,y}))};
 };
}
function nativeSizeOracle(measured,label){
 assert.ok(measured.rect.width>=44&&measured.rect.height>=44,`${label}: target below 44px`);
}
function nativeHitOracle(measured,label){
 assert.ok(['BUTTON','SUMMARY'].includes(measured.tag),`${label}: native control required`);
 nativeSizeOracle(measured,label);
 assert.deepEqual(measured.corners.map(point=>point.corner),['top-left','top-right','bottom-left','bottom-right'],
  `${label}: four distinct contour regions required`);
 assert.ok(measured.corners.every(point=>point.reachable===true),`${label}: obscured/offscreen contour probe`);
}
function contourLogicChecks(){
 const rect={left:0,top:0,right:100,bottom:44,width:100,height:44};
 const plain={transform:'none',perspective:'none',translate:'none',rotate:'none',scale:'none',
  offsetPath:'none',transformStyle:'flat',zoom:'1'};
 const base={rect,radii:['0px','0px','0px','0px'],transforms:[plain],fragments:1,
  viewport:{width:320,height:740,scale:1,offsetLeft:0,offsetTop:0}};
 const square=circularContour(base);
 assert.deepEqual(square.map(({x,y})=>({x,y})),[{x:3,y:3},{x:97,y:3},{x:3,y:41},{x:97,y:41}]);
 contourOracle.logicChecks.push({check:'square radius 0 uses 3px',passed:true});
 const circle=circularContour({...base,radii:['10px','10px 10px','10px','10px']});
 assert.ok(circle.every(point=>Math.abs(point.inward-5.050252531694167)<1e-12));
 assert.deepEqual(circle.map(point=>point.corner),square.map(point=>point.corner));
 contourOracle.logicChecks.push({check:'circular radius 10 uses 5.050252531694167px',passed:true});
 const invalid=[
  ['nonfinite radius',{radii:['Infinitypx','0px','0px','0px']},/finite computed px/],
  ['nonfinite rect',{rect:{...rect,width:Infinity}},/nonfinite rect/],
  ['elliptical radius',{radii:['10px 11px','0px','0px','0px']},/elliptical radius/],
  ['percentage radius',{radii:['10%','0px','0px','0px']},/finite computed px/],
  ['negative radius',{radii:['-1px','0px','0px','0px']},/finite computed px/],
  ['overlapping radii',{radii:['30px','30px','30px','30px']},/overlapping radii/],
  ['transform',{transforms:[{...plain,transform:'matrix(1, 0, 0, 1, 0, 0)'}]},/transform\/perspective/],
  ['perspective',{transforms:[{...plain,perspective:'100px'}]},/transform\/perspective/],
  ['fragmented box',{fragments:2},/fragmented border box/],
  ['inconsistent box',{rect:{...rect,right:101}},/inconsistent border box/],
  ['coincident regions',{rect:{...rect,right:6,width:6}},/distinct corner regions/],
  ['scaled viewport',{viewport:{...base.viewport,scale:2}},/visual viewport mapping/]
 ];
 for(const [name,change,message] of invalid){
  assert.throws(()=>circularContour({...base,...change}),message);
  contourOracle.logicChecks.push({check:`reject ${name}`,passed:true});
 }
 checks.push({check:'contour formula and unsupported geometry logic checks',count:contourOracle.logicChecks.length});
}
function cornerShapeLogicChecks(){
 for(const [raw,expected] of [
  ['',true],['round',true],['superellipse(1)',true],
  ['bevel',false],['scoop',false],['superellipse(0)',false],['superellipse(2)',false],
  ['unknown',false],['Round',false],[' round',false],['round ',false],['superellipse(1.0)',false],[null,false]
 ]){
  const actual=supportedCircularCornerShape(raw);
  const row={raw,quoted:JSON.stringify(raw),expected,actual,passed:actual===expected};
  contourOracle.cornerShapeChecks.push(row);
  assert.equal(actual,expected,`Computed corner-shape predicate: ${row.quoted}`);
 }
 checks.push({check:'computed corner-shape exact-value predicate checks',count:contourOracle.cornerShapeChecks.length});
}
async function computedCornerPreflight(target,owner){
 const preflight={passed:false,client:owner.label,timeoutMs:2000,
  browser:{name:owner.browser.browserType().name(),version:owner.browser.version()},
  observationRange:{start:contourOracle.cornerShapeObservations.length,end:null}};
 contourOracle.browserPreflight=preflight;
 try{
  // Same browser/context and installed sampler as the journey; no second launch,
  // retry, app dispatch or product CSS mutation. Both source and installed runs
  // execute this gate before session creation. The temporary native control is
  // removed synchronously in finally, including when either sampler call fails.
  preflight.result=await bounded(target.evaluate(()=>{
   const result={userAgent:navigator.userAgent,platform:navigator.platform,removed:false,
    supported:{round:CSS.supports('corner-top-left-shape','round'),bevel:CSS.supports('corner-top-left-shape','bevel')},
    positive:null,negative:null,error:null};
   const control=document.createElement('button');
   control.type='button';control.textContent='Contour preflight';
   control.style.cssText='all:initial;position:fixed;left:8px;top:8px;display:block;box-sizing:border-box;'+
    'width:120px;height:44px;margin:0;padding:0;border:2px solid black;border-radius:10px;corner-shape:round;'+
    'background:white;color:black;font:12px sans-serif;pointer-events:auto;z-index:2147483647;';
   try{
    document.documentElement.append(control);
    result.positive=window.__fileControlsContourSample(control);
    control.style.setProperty('corner-top-left-shape','bevel');
    result.negative={corner:'top-left',authored:'bevel',
     raw:getComputedStyle(control).getPropertyValue('corner-top-left-shape'),accepted:null,error:null};
    result.negative.quoted=JSON.stringify(result.negative.raw);
    try{result.negative.sample=window.__fileControlsContourSample(control);result.negative.accepted=true}
    catch(error){result.negative.accepted=false;result.negative.error={name:error.name,message:error.message,stack:error.stack}}
   }catch(error){result.error={name:error.name,message:error.message,stack:error.stack}}
   finally{control.remove();result.removed=!control.isConnected}
   return result;
  }),preflight.timeoutMs,'Computed corner-shape browser preflight exceeded 2 seconds');
  preflight.observationRange.end=contourOracle.cornerShapeObservations.length;
  const {result}=preflight;
  assert.equal(result.removed,true,'Preflight native button was not removed');
  assert.equal(result.error,null,`Browser preflight failed: ${JSON.stringify(result.error)}`);
  assert.deepEqual(result.supported,{round:true,bevel:true},'Browser cannot demonstrate actual corner-shape discrimination');
  assert.equal(result.positive.tag,'BUTTON');
  assert.ok(result.positive.cornerShapes.every(({raw})=>raw!==''&&supportedCircularCornerShape(raw)),
   'Preflight must observe a supported, computed round form, not an unsupported-property empty value');
  nativeHitOracle(result.positive,'computed-round preflight');
  assert.ok(['bevel','superellipse(0)'].includes(result.negative.raw),'Preflight did not compute the authored bevel');
  assert.equal(supportedCircularCornerShape(result.negative.raw),false);
  assert.equal(result.negative.accepted,false,'Same sampler accepted a noncircular corner');
  assert.match(result.negative.error?.message||'',/Unsupported contour geometry: noncircular corner shape top-left=/);
  const observed=contourOracle.cornerShapeObservations.slice(preflight.observationRange.start,preflight.observationRange.end);
  assert.deepEqual(contourOracle.cornerShapeCaptureErrors,[],'Corner-shape console evidence capture failed');
  assert.deepEqual(observed.map(row=>row.stage),['before-validation','before-validation','rejected'],
   'Preflight must retain raw computed values before both validations and on rejection');
  assert.deepEqual(observed[0].cornerShapes,result.positive.cornerShapes);
  assert.deepEqual(observed[1].cornerShapes,observed[2].cornerShapes);
  assert.equal(observed[2].cornerShapes[0].raw,result.negative.raw);
  preflight.passed=true;
  checks.push({check:'actual browser computed round accepted and noncircular corner rejected by same sampler'});
 }catch(error){preflight.error=errorEvidence(error);throw error}
 finally{preflight.observationRange.end=contourOracle.cornerShapeObservations.length}
}
async function contourNegativeChecks(control){
 await control.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
 const before=await control.evaluate(node=>window.__fileControlsContourSample(node));
 nativeHitOracle(before,'negative baseline');
 for(const dimension of ['width','height']){
  assert.throws(()=>nativeHitOracle({...before,rect:{...before.rect,[dimension]:43.99}},'negative size'),/target below 44px/);
  contourOracle.negativeCounts.size++;
  checks.push({check:'negative control: native size below 44px rejected',dimension});
 }
 for(const [index,point] of before.corners.entries()){
  const variant={...before,corners:before.corners.map((value,i)=>i===index?{...value,reachable:false}:value)};
  assert.throws(()=>nativeHitOracle(variant,'negative region'),/obscured\/offscreen contour probe/);
  contourOracle.negativeCounts.regions++;
  checks.push({check:'negative control: individual contour region rejected',corner:point.corner});
 }
 // A real pointer-intercepting 2x2px DOM overlay, not just a falsified sample.
 // It covers exactly the first probe; no app CSS, layout, or event is changed.
 const negative={label:'negative-contour-overlay',before,removed:false};
 geometry.push(negative);
 const overlay=await control.evaluateHandle((node,point)=>{
  const overlay=document.createElement('div');overlay.dataset.fileControlsNegative='one-contour-probe';
  overlay.style.cssText=`all:initial;position:fixed;left:${point.x-1}px;top:${point.y-1}px;width:2px;height:2px;`+
   'box-sizing:border-box;margin:0;padding:0;border:0;border-radius:0;pointer-events:auto;z-index:2147483647;';
  document.documentElement.append(overlay);return overlay;
 },before.corners[0]);
 try{
  const blocked=await control.evaluate(node=>window.__fileControlsContourSample(node));
  // Do not mix a deliberately failed sample into positive geometry.corners.
  negative.sample=blocked;
  assert.deepEqual(blocked.rect,before.rect,'Overlay must not change control layout');
  assert.deepEqual(blocked.corners.map(({x,y})=>({x,y})),before.corners.map(({x,y})=>({x,y})));
  assert.deepEqual(blocked.corners.map(point=>point.reachable),[false,true,true,true],'Overlay must obstruct exactly ONE probe');
  negative.hitOverlay=await overlay.evaluate((node,point)=>document.elementFromPoint(point.x,point.y)===node,before.corners[0]);
  assert.equal(negative.hitOverlay,true);
  assert.throws(()=>nativeHitOracle(blocked,'negative overlay'),/obscured\/offscreen contour probe/);
  negative.rejected=true;
  contourOracle.negativeCounts.obstruction++;
  checks.push({check:'negative control: real one-probe pointer overlay rejected by same contour oracle',corner:before.corners[0].corner});
 }finally{
  try{
   negative.removed=await overlay.evaluate(node=>{node.remove();return !node.isConnected});
   assert.equal(negative.removed,true,'Negative overlay was not removed');
  }
  finally{await overlay.dispose()}
 }
 const restored=await control.evaluate(node=>window.__fileControlsContourSample(node));
 negative.restored=restored;
 nativeHitOracle(restored,'overlay removed');
 assert.deepEqual(restored.corners,before.corners,'Removing overlay must restore the original hit targets');
}

async function measureControls(target,controls,label){
 const diagnostic=cornerDiagnosticMode&&label==='desktop';
 for(const [controlIndex,control] of controls.entries()){
  await control.scrollIntoViewIfNeeded();
  // The inspector has a sticky heading. Center within real scroll containers
  // before hit testing rather than accepting a button covered by that heading.
  await control.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
  const sample=(node,diagnostic)=>{
   const contour=diagnostic?null:window.__fileControlsContourSample(node);
   const r=node.getBoundingClientRect(),inset=3,style=getComputedStyle(node);
   const points=[[r.left+inset,r.top+inset],[r.right-inset,r.top+inset],
    [r.left+inset,r.bottom-inset],[r.right-inset,r.bottom-inset]];
   const hits=[];
   const measured={name:node.getAttribute('aria-label')||node.textContent.trim(),tag:node.tagName,className:node.className,
    rect:r.toJSON(),computed:{height:style.height,minHeight:style.minHeight,minWidth:style.minWidth,
     padding:style.padding,borderWidth:style.borderWidth,boxSizing:style.boxSizing,
     whiteSpace:style.whiteSpace,overflowWrap:style.overflowWrap},
    context:{inspector:!!node.closest('.a-canvas-inspector'),viewTools:!!node.closest('.a-canvas-view-tools')},
    ...(contour||{}),viewport:contour?.viewport||{width:innerWidth,height:innerHeight},corners:contour?.corners||points.map(([x,y],index)=>({x,y,
     reachable:x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&node.contains(
      diagnostic?(hits[index]=document.elementFromPoint(x,y)):document.elementFromPoint(x,y))}))};
   if(diagnostic){
    // No await/RAF, repeated oracle, UI write or text capture. The oracle above
    // and this diagnostic share the FIRST rect and exact elementFromPoint hits.
    const capture={complete:false,first:{rect:measured.rect,corners:measured.corners}};
    measured.cornerDiagnostic=capture;
    try{
     const id=window.__fileControlsNodeId;
     const describe=element=>element?{nodeId:id(element),tag:element.tagName,connected:element.isConnected}:null;
     const properties=['borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius',
      'borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth',
      'borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle',
      'pointerEvents','display','visibility','opacity','position','zIndex','transform','transformOrigin',
      'clip','clipPath','overflowX','overflowY','overflowClipMargin'];
     const computed=value=>Object.fromEntries(properties.map(key=>[key,value[key]]));
     const corners=measured.corners.map((point,index)=>{
      const stack=document.elementsFromPoint(point.x,point.y);
      return {...point,corner:['top-left','top-right','bottom-left','bottom-right'][index],
       hitTested:index in hits,hit:describe(hits[index]),stack:stack.slice(0,8).map(describe),
       stackTruncated:stack.length>8};
     });
     const ancestors=[];let parent=node.parentElement;
     for(;parent&&ancestors.length<16;parent=parent.parentElement){
      ancestors.push({...describe(parent),rect:parent.getBoundingClientRect().toJSON(),
       computed:computed(getComputedStyle(parent)),scroll:{x:parent.scrollLeft,y:parent.scrollTop,
        clientWidth:parent.clientWidth,clientHeight:parent.clientHeight,
        scrollWidth:parent.scrollWidth,scrollHeight:parent.scrollHeight}});
     }
     Object.assign(capture.first,{at:Date.now(),node:describe(node),corners,computed:computed(style),
      controlRadius:style.getPropertyValue('--a-control-radius'),ancestors,ancestorsTruncated:!!parent,
      viewport:{...measured.viewport,dpr:devicePixelRatio,visual:visualViewport&&{
       width:visualViewport.width,height:visualViewport.height,offsetLeft:visualViewport.offsetLeft,
       offsetTop:visualViewport.offsetTop,pageLeft:visualViewport.pageLeft,pageTop:visualViewport.pageTop,scale:visualViewport.scale}},
      scroll:{x:scrollX,y:scrollY},fonts:{status:document.fonts.status,ready:document.fonts.status==='loaded'}});
     capture.complete=true;
    }catch{
     // Missing diagnostics must fail, but never replace an original false corner.
     capture.captureError='Synchronous corner diagnostic capture failed';
    }
   }
   return measured;
  };
  let result,disposeError;
  if(diagnostic){
   const handle=await control.elementHandle();
   assert.ok(handle,'Corner diagnostic control handle missing');
   try{result=await handle.evaluate(sample,true)}
   finally{try{await handle.dispose()}catch(error){disposeError=error}}
  }else result=await control.evaluate(sample,false);
  const {cornerDiagnostic,...measured}=result;
  if(diagnostic)cornerDiagnostics.push({label,controlIndex,...cornerDiagnostic,
   ...(disposeError?{disposeError:errorEvidence(disposeError)}:{})});
  geometry.push({label,...measured});
  if(diagnostic){
   nativeSizeOracle(measured,`${label} ${measured.name}`);
   assert.ok(measured.corners.every(point=>point.reachable),`${label} ${measured.name}: obscured/offscreen legacy corner`);
  }else nativeHitOracle(measured,`${label} ${measured.name}`);
  if(diagnostic)assert.equal(cornerDiagnostic?.complete,true,'Corner diagnostic capture missing or incomplete');
  if(disposeError)throw disposeError;
 }
 assert.ok(await target.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Document horizontal overflow');
}
// One synchronous, RAF-aligned page read per sample: include OFFSCREEN siblings,
// not only the path just centered by the existing journey. No scroll or UI writes.
function fileFrameSnapshot(anchor){
 return new Promise((resolve,reject)=>requestAnimationFrame(rafAt=>{try{
  const id=window.__fileControlsNodeId,rect=node=>node.getBoundingClientRect().toJSON();
  const describe=node=>({nodeId:id(node),tag:node.tagName,id:node.id,className:node.getAttribute('class'),
   connected:node.isConnected});
  const properties=['display','visibility','opacity','position','zIndex','top','transform','clipPath',
   'width','height','minWidth','minHeight','maxWidth','maxHeight','boxSizing','margin','padding',
   'borderWidth','overflowX','overflowY','whiteSpace','overflowWrap','wordBreak','textOverflow',
   'fontSize','lineHeight','color','backgroundColor','pointerEvents','gridTemplateColumns','gap'];
  const computed=node=>{const style=getComputedStyle(node);return Object.fromEntries(properties.map(key=>[key,style[key]]))};
  const details=anchor.matches('details.a-canvas-file-path')?[anchor]:
   [...anchor.closest('#workspace-canvas').querySelectorAll('details.a-canvas-file-path')];
  const headers=[...document.querySelectorAll('header,.a-canvas-artifact-toolbar')];
  const toolbars=[...document.querySelectorAll('.a-canvas-artifact-toolbar')];
  const selector='code,p,summary,button,label,strong,[role="status"],[role="alert"]';
  const nodes=[...new Set([anchor,...details.flatMap(node=>[node,...node.querySelectorAll(selector)]),
   ...headers,...toolbars.flatMap(node=>[...node.querySelectorAll(selector)])])];
  const textRects=node=>{
   // Paragraphs can contain code blocks. Keep their OWN label fragments separate
   // from the code's lines rather than calling the whole paragraph a label line.
   const contents=node.tagName==='CODE'?[node]:[...node.childNodes].filter(child=>child.nodeType===3&&child.textContent.trim());
   return contents.flatMap(child=>{const range=document.createRange();range.selectNodeContents(child);
    return [...range.getClientRects()].filter(r=>r.width>0&&r.height>0).map(r=>r.toJSON())});
  };
  const elements=nodes.map(node=>({...describe(node),label:node.getAttribute('aria-label'),text:node.textContent,rect:rect(node),
   rects:[...node.getClientRects()].map(r=>r.toJSON()),textRects:textRects(node),computed:computed(node),
   path:node.tagName==='CODE',header:headers.includes(node),open:node.open??null}));
  const ancestors=new Set();
  for(const node of nodes)for(let parent=node;parent;parent=parent.parentElement)ancestors.add(parent);
  const scrollContainers=[...ancestors].filter(node=>{
   const style=getComputedStyle(node);
   return node===document.scrollingElement||/(auto|scroll|hidden|clip)/.test(style.overflowX+' '+style.overflowY)||
    node.scrollWidth>node.clientWidth||node.scrollHeight>node.clientHeight;
  }).map(node=>({...describe(node),rect:rect(node),x:node.scrollLeft,y:node.scrollTop,
   clientWidth:node.clientWidth,clientHeight:node.clientHeight,scrollWidth:node.scrollWidth,scrollHeight:node.scrollHeight,
   overflowX:getComputedStyle(node).overflowX,overflowY:getComputedStyle(node).overflowY}));
  const scrollIds=new Set(scrollContainers.map(node=>node.nodeId));
  nodes.forEach((node,index)=>{
   const ids=[];for(let parent=node;parent;parent=parent.parentElement)if(scrollIds.has(id(parent)))ids.push(id(parent));
   elements[index].scrollContainerIds=ids;
  });
  const overlap=(a,b)=>{
   const left=Math.max(a.left,b.left),top=Math.max(a.top,b.top),right=Math.min(a.right,b.right),bottom=Math.min(a.bottom,b.bottom);
   return right>left&&bottom>top?{left,top,right,bottom,width:right-left,height:bottom-top}:null;
  };
  const intersections=[],headerIntersections=[];
  for(let a=0;a<elements.length;a++)for(let b=a+1;b<elements.length;b++){
   const first=elements[a],second=elements[b],box=overlap(first.rect,second.rect);
   if(!box)continue;
   const text=[];
   first.textRects.forEach((r,i)=>second.textRects.forEach((s,j)=>{const intersection=overlap(r,s);
    if(intersection)text.push({firstLine:i,secondLine:j,rect:intersection})}));
   intersections.push({first:first.nodeId,second:second.nodeId,rect:box,text,
    nested:nodes[a].contains(nodes[b])||nodes[b].contains(nodes[a])});
  }
  for(const [index,item] of elements.entries())for(const [line,r] of item.textRects.entries())for(const header of headers){
   if(header.contains(nodes[index]))continue;
   const intersection=overlap(r,rect(header));if(!intersection)continue;
   const x=intersection.left+intersection.width/2,y=intersection.top+intersection.height/2;
   const inViewport=x>=0&&y>=0&&x<innerWidth&&y<innerHeight,hit=inViewport?document.elementFromPoint(x,y):null;
   headerIntersections.push({nodeId:item.nodeId,line,headerId:id(header),rect:intersection,
    sample:{x,y,inViewport,hit:hit&&{nodeId:id(hit),tag:hit.tagName,className:hit.getAttribute('class')},
     headerOnTop:!!hit&&header.contains(hit)}});
  }
  resolve({at:Date.now(),rafAt,state:{documentId:id(document),url:location.href,
   anchorId:id(anchor),anchorConnected:anchor.isConnected,
   viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio,visual:visualViewport&&{
    width:visualViewport.width,height:visualViewport.height,offsetLeft:visualViewport.offsetLeft,
    offsetTop:visualViewport.offsetTop,pageLeft:visualViewport.pageLeft,pageTop:visualViewport.pageTop,scale:visualViewport.scale}},
   scroll:{x:scrollX,y:scrollY,containers:scrollContainers},elements,intersections,headerIntersections}});
 }catch(error){reject(error)}}));
}
async function captureFileFrame(target,anchor,label){
 const path=join(evidence,label+'.png');
 if(target.viewportSize()?.width!==320){await target.screenshot({path});return}
 const capture={label,png:{path},visualJudgment:'pending',
  binding:{atomic:false,status:'not-matched',basis:'Exact before/after RAF samples only; no continuous or atomic pixel-stability claim.'}};
 fileFrameCaptures.push(capture);
 let handle;
 try{
  handle=await anchor.elementHandle();
  capture.before=await target.evaluate(fileFrameSnapshot,handle);
  const png=await target.screenshot({path}); // Immediate next browser operation; same existing shot.
  capture.png.sha256=digest(png);capture.png.bytes=png.length;
  capture.after=await target.evaluate(fileFrameSnapshot,handle);
  const before=capture.before.state,after=capture.after.state,same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const scroll=state=>({x:state.scroll.x,y:state.scroll.y,
   containers:state.scroll.containers.map(({nodeId,x,y})=>({nodeId,x,y}))});
  Object.assign(capture.binding,{
   domIdentityUnchanged:before.anchorConnected&&after.anchorConnected&&before.documentId===after.documentId&&
    before.anchorId===after.anchorId&&same(before.elements.map(item=>item.nodeId),after.elements.map(item=>item.nodeId)),
   viewportUnchanged:same(before.viewport,after.viewport),scrollUnchanged:same(scroll(before),scroll(after)),
   layoutUnchanged:same(before,after)});
  if(before.viewport.width===320&&after.viewport.width===320&&capture.binding.domIdentityUnchanged&&
   capture.binding.viewportUnchanged&&capture.binding.scrollUnchanged&&capture.binding.layoutUnchanged)capture.binding.status='matched-before-after-only';
 }catch(error){capture.error=errorEvidence(error);throw error}
 finally{await handle?.dispose()}
}
async function measure(target,details,label){
 await measureControls(target,[details.locator('summary'),...names.map(name=>button(details,name))],label);
 assert.ok(await details.evaluate(node=>node.scrollWidth<=node.clientWidth),'File-details horizontal overflow');
 await captureFileFrame(target,details,label+'-controls');
 await details.locator('summary').scrollIntoViewIfNeeded();
 await captureFileFrame(target,details,label+'-paths');
 // Inspect the text itself, separately from button visibility. Center each path
 // in the real scroll container; 320px shots additionally retain sibling geometry
 // and explicit PNG brackets, including raw occlusion under the sticky heading.
 // This investigates the earlier narrow path-overlap observation without
 // assuming that reachable buttons imply unobscured path text.
 const codes=details.locator('code');
 for(let index=0;index<await codes.count();index++){
  const code=codes.nth(index);
  await code.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
  const textGeometry=await code.evaluate(node=>{
   const range=document.createRange();range.selectNodeContents(node);
   const style=getComputedStyle(node),rect=node.getBoundingClientRect();
   const lines=[...range.getClientRects()].filter(r=>r.width>0&&r.height>0).map(r=>{
    const y=r.top+r.height/2,inset=Math.min(1,r.width/4);
    return {rect:r.toJSON(),points:[r.left+inset,r.left+r.width/2,r.right-inset].map(x=>{
     const hit=document.elementFromPoint(x,y);
     return {x,y,reachable:x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&node.contains(hit),
      hit:hit&&{tag:hit.tagName,className:hit.className}};
    })};
   });
   return {text:node.textContent,rect:rect.toJSON(),clientWidth:node.clientWidth,scrollWidth:node.scrollWidth,
    computed:{whiteSpace:style.whiteSpace,overflowWrap:style.overflowWrap,fontSize:style.fontSize,lineHeight:style.lineHeight},
    viewport:{width:innerWidth,height:innerHeight},lines};
  });
  geometry.push({label:label+'-path-text',index,...textGeometry});
  await captureFileFrame(target,details,`${label}-path-${index}`);
  assert.ok(textGeometry.lines.length>0,`${label} path ${index}: no rendered text lines`);
  assert.ok(textGeometry.scrollWidth<=textGeometry.clientWidth,`${label} path ${index}: horizontal text overflow`);
  assert.ok(textGeometry.lines.every(line=>line.points.every(point=>point.reachable)),
   `${label} path ${index}: text line is covered or outside viewport`);
 }
}
async function cornerTap(control,corner){
 await control.scrollIntoViewIfNeeded();
 await control.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
 assert.ok(['top-left','top-right','bottom-left','bottom-right'].includes(corner));
 const measured=await control.evaluate((node,corner)=>{
  const describe=element=>element&&({tag:element.tagName,id:element.id,className:element.className,
   label:element.getAttribute('aria-label')||element.textContent.trim().slice(0,120)});
  const sample=()=>{
   const contour=window.__fileControlsContourSample(node),r=contour.rect,borders=contour.borders;
   const point=contour.corners.find(point=>point.corner===corner);
   const intended={x:point.x,y:point.y};
   const oldPosition={x:intended.x-r.left,y:intended.y-r.top};
   // Playwright's explicit positions start at the padding box, not the border
   // box used by getBoundingClientRect and the shared contour oracle. Preserve
   // the old WRONG conversion as a negative even when it still hits this button.
   const position={x:oldPosition.x-borders.left,y:oldPosition.y-borders.top};
   const hit=point=>({point,reachable:point.x>=0&&point.y>=0&&point.x<innerWidth&&point.y<innerHeight&&
    node.contains(document.elementFromPoint(point.x,point.y)),
    elements:document.elementsFromPoint(point.x,point.y).map(describe)});
   const fromPosition=offset=>({x:r.left+borders.left+offset.x,y:r.top+borders.top+offset.y});
   return {...contour,corner,node:describe(node),
    intended,position,oldPosition,corrected:hit(fromPosition(position)),old:hit(fromPosition(oldPosition))};
  };
  const measured=sample();
  window.__fileControlsLastTap=null;
  const listener=event=>{
   try{
    window.__fileControlsLastTap={...sample(),event:{x:event.clientX,y:event.clientY,
     pointerType:event.pointerType,isTrusted:event.isTrusted,target:describe(event.target),sameNode:node.contains(event.target)}};
   }catch(error){window.__fileControlsLastTap={captureError:error.message}}
  };
  // Observe the delivered event even if it lands on a different native target.
  document.addEventListener('pointerdown',listener,{capture:true,once:true});
  window.__fileControlsCancelTap=()=>document.removeEventListener('pointerdown',listener,true);
  return measured;
 },corner);
 try{
  const {corners,...tapBefore}=measured;
  geometry.push({label:'corner-tap-before',...tapBefore,acceptance:{basis:measured.basis,corners}});
  nativeHitOracle(measured,`touch ${corner}`);
  const exactCorner=sample=>{
   assert.deepEqual(sample.point,measured.intended,'Tap must preserve the measured contour viewport point');
   assert.ok(sample.reachable,'Exact contour probe must hit the native control');
  };
  exactCorner(measured.corrected);
  if(measured.borders.left||measured.borders.top){
   assert.throws(()=>exactCorner(measured.old),/Tap must preserve the measured contour viewport point/);
   contourOracle.negativeCounts.paddingOrigin++;
   checks.push({check:'old padding-origin conversion rejected by same exact-contour oracle',corner,
    oldPoint:measured.old.point,intended:measured.intended,oldReachable:measured.old.reachable});
  }else assert.deepEqual(measured.old.point,measured.corrected.point,'Zero border has no conversion difference');
  await control.tap({position:measured.position});
  const delivered=await control.evaluate(()=>window.__fileControlsLastTap);
  const {corners:deliveredCorners,...tapDelivered}=delivered||{};
  geometry.push({label:'corner-tap-delivered',...tapDelivered,
   acceptance:{basis:delivered?.basis,corners:deliveredCorners}});
  assert.ok(delivered?.event?.sameNode,'Touch must reach the native control');
  assert.equal(delivered.nodeId,measured.nodeId,'Native control was replaced before touch delivery');
  for(const field of ['rect','radii','borders','transforms','viewport','scroll','intended','position'])
   assert.deepEqual(delivered[field],measured[field],`Control ${field} changed between measurement and gesture`);
  nativeHitOracle(delivered,`delivered touch ${corner}`);
  exactCorner(delivered.corrected);
  assert.equal(delivered.event.pointerType,'touch');assert.equal(delivered.event.isTrusted,true);
  // Chromium may quantize fractional CSS coordinates; retain actual delivery.
  // This tolerance is ONLY for event delivery, never for padding reconstruction.
  assert.ok(Math.abs(delivered.event.x-measured.intended.x)<1&&
   Math.abs(delivered.event.y-measured.intended.y)<1,'Delivered touch missed the measured contour probe');
 }finally{
  await control.evaluate(()=>{window.__fileControlsCancelTap?.();delete window.__fileControlsCancelTap});
 }
}

async function connect(owner,options){
 const label=owner.label,target=await ownerPage(owner,ready.url,options);pageLabels.set(target,label);
 target.on('pageerror',error=>errors.push(error.message));
 target.on('console',message=>{
  const text=message.text();if(!text.startsWith(cornerShapeLogPrefix))return;
  try{
   assert.ok(contourOracle.cornerShapeObservations.length<512,'Corner-shape evidence exceeded 512 observations');
   contourOracle.cornerShapeObservations.push({client:label,...JSON.parse(text.slice(cornerShapeLogPrefix.length))});
  }catch(error){
   if(!contourOracle.cornerShapeCaptureErrors.length)contourOracle.cornerShapeCaptureErrors.push(errorEvidence(error));
  }
 });
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
 await target.addInitScript({content:`window.__fileControlsCircularContour=(${circularContour.toString()});`+
  `window.__fileControlsSupportedCircularCornerShape=(${supportedCircularCornerShape.toString()});`+
  `(${installContourSampler.toString()})(${JSON.stringify(cornerShapeLogPrefix)});`});
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
 await captureFileFrame(target,primary,label+'-primary');
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
 if(!cornerDiagnosticMode){contourLogicChecks();cornerShapeLogicChecks()}
 const desktopOwner=await launchOwner('desktop',browserOwners);
 page=await connect(desktopOwner,desktopOptions);
 if(!cornerDiagnosticMode)await computedCornerPreflight(page,desktopOwner);
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
 if(cornerDiagnosticMode){
  assert.equal(cornerDiagnostics.length,4,'Corner diagnostic must retain summary and all three controls');
  assert.ok(cornerDiagnostics.every(row=>row.complete),'Corner diagnostic capture missing or incomplete');
  cornerDiagnosticComplete=true;
  return; // Diagnostic checkpoint only, never a full-journey qualification.
 }
 await contourNegativeChecks(button(details,names[2]));

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
 checks.push({check:'390px/320px emulated touch, actual contour taps, 44px targets, four contour-relative native hit probes and saved PNGs'});
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
 const {paddingOrigin,...contourNegatives}=contourOracle.negativeCounts;
 assert.deepEqual(contourNegatives,{size:2,regions:4,obstruction:1},'Required contour negatives did not complete');
 assert.ok(paddingOrigin>0,'No nonzero-border padding conversion negative ran');
 assert.equal(paddingOrigin,geometry.filter(row=>row.label==='corner-tap-before'&&(row.borders.left||row.borders.top)).length,
  'Every nonzero-border touch must reject the old padding conversion');
 assert.deepEqual(contourOracle.cornerShapeCaptureErrors,[],'Corner-shape console evidence capture failed');
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
  ...(cornerDiagnosticMode?{mode:'corner-diagnostic',diagnosticComplete:cornerDiagnosticComplete,
   stoppedAfter:cornerDiagnosticComplete?'first-desktop-measure':null,cornerDiagnostics}:{}),
  contourOracle,
  installed:ready&&{packageRoot:ready.packageRoot,verified:ready.installedVerified,staticSha256:ready.staticSha256},
  seed,checks,geometry,fileFrameCaptures,downloads,requests,commands,metadataExchanges,detailsReadiness,preCleanupSnapshots,
  errors,cleanup,fixtureCleanup:records.find(row=>row.cleanup)?.cleanup,
  browserCleanup:browserOwners.map(owner=>({label:owner.label,closed:owner.closed,connected:owner.browser?.isConnected()??false})),
  limits:['Synthetic runtime and fixture bearer authentication; no provider, login, remote server or physical touch-device claim.',
   'Clipboard/403/wrong-byte faults are deliberate test injections; positive clipboard reads and downloaded files are real browser results.',
   'The two later HTML previews execute deliberately as setup; copy/download must retain each original frame and execution token.',
   'Exact-byte identity covers ordinary saved UTF-8 HTML/Markdown, not Babylon engine-prepending exports.',
   '320px fileFrameCaptures preserve raw intersections/occlusion at existing scroll positions; sampling is not a visual pass.',
   'Geometry reads are atomic within each RAF callback, but PNGs are separate. matched-before-after-only is bracket equality, not proof of unchanged intervening frames. Visual judgment remains pending.',
   'PNG capture and measured reachability do not claim independent human visual review.']},null,2)+'\n')]);
 const writeErrors=writes.filter(row=>row.status==='rejected').map(row=>row.reason);
 if(writeErrors.length)failure=new AggregateError([...(failure?[failure]:[]),...writeErrors],'File controls evidence write failed');
 const entries=await readdir(evidence,{withFileTypes:true});
 assert.ok(entries.every(entry=>entry.isFile()),'Export only ordinary evidence files, never fixture sockets/profile/state');
}
if(failure)throw failure;
console.log(cornerDiagnosticMode?`Generated file controls corner diagnostic complete (not full qualification); evidence: ${evidence}`:
 `Generated file controls passed; evidence: ${evidence}`);
