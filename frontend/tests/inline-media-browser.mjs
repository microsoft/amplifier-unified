// Authored acceptance, not qualification. Only synthetic/public sample bytes.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {hostname,homedir} from 'node:os';
import {chromium,expect,request} from '@playwright/test';
import assert from 'node:assert/strict';
import './composer-test-helpers.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const evidenceDir=process.env.INLINE_MEDIA_EVIDENCE_DIR;
if(!evidenceDir)throw Error('Supply INLINE_MEDIA_EVIDENCE_DIR owned by this verification task');
const python=process.env.PYTHON||process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python');
const mode=process.env.INLINE_MEDIA_MODE||'source';
const headed=process.env.INLINE_MEDIA_HEADED==='1';
if(process.env.INLINE_MEDIA_HEADED&&!['0','1'].includes(process.env.INLINE_MEDIA_HEADED))
 throw Error('INLINE_MEDIA_HEADED must be 0 or 1');
const selectedPhases=process.env.INLINE_MEDIA_PHASES?new Set(JSON.parse(process.env.INLINE_MEDIA_PHASES)):null;
if(selectedPhases&&![...selectedPhases].every(name=>typeof name==='string'))
 throw Error('INLINE_MEDIA_PHASES must contain phase names');
const browserArgs=process.env.CHROMIUM_ARGS?JSON.parse(process.env.CHROMIUM_ARGS):
 process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[];
if(!Array.isArray(browserArgs)||browserArgs.some(arg=>typeof arg!=='string'||arg.startsWith('--disable-web-security')))
 throw Error('CHROMIUM_ARGS must be an array of existing executor arguments, without disabling web security');
const receipts=[],contexts=new Set(),pages=new Set(),fixtureRows=[],pageErrors=[];
const physicalNames=['mp4-native-controls','visibility','keyboard-200-percent'];
const scenarioPhases=['gif-live-attach','gif-resume','gif-report-cadence','report-readback-negative-control',
 'mp4-native','mp4-native-controls','mp4-decode-error','mp4-explicit-cleanup','partial-disposal','large-native',
 'reduced-motion','visibility','offscreen','decoder-error','unsupported-decoder','decoder-late-cleanup',
 'preservation','endpoints','layout','keyboard-200-percent'];
let browser,fixture,boot,sharedStorage,sharedContext,exit,forcedKill=false;
await mkdir(resolve(evidenceDir),{recursive:true});
const privateHeaders={'X-Inline-Media-Fixture':'synthetic-control'};
const phasePath=name=>join(evidenceDir,name+'.json');
const privateStrings=[root,resolve(evidenceDir),homedir(),hostname(),python,
 process.env.CHROMIUM_EXECUTABLE,process.env.INLINE_MEDIA_GIF,process.env.INLINE_MEDIA_MP4,
 process.env.INLINE_MEDIA_LARGE_GIF].filter(value=>typeof value==='string'&&value.length>3)
 .sort((a,b)=>b.length-a.length);
function publicEvidence(value){
 if(typeof value==='string'){
  for(const item of privateStrings)value=value.replaceAll(item,'<private>');
  return value.replace(/(?:\/(?:home|Users)\/[^\s"'<>]+|[A-Za-z]:\\Users\\[^\s"'<>]+)/g,'<private-path>');
 }
 if(Array.isArray(value))return value.map(publicEvidence);
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,value])=>[publicEvidence(key),publicEvidence(value)]));
 return value;
}
const writeEvidence=(path,value)=>writeFile(path,JSON.stringify(publicEvidence(value),null,2));
async function record(name,status,evidence={},required=true){
 const row={phase:name,status,required,evidence:publicEvidence(evidence)};
 receipts.push(row);await writeEvidence(phasePath(name),row);
 console.log(JSON.stringify({phase:name,status,required}));
 return row;
}
class CantCheck extends Error{}
async function phase(name,work,{required=true}={}){
 if(selectedPhases&&!selectedPhases.has(name)&&!['shared-setup','cleanup'].includes(name))
  return record(name,'NOT_RUN',{reason:'Explicit partial verification selection'},false);
 try{return await record(name,'PASS',await work(),required)}
 catch(error){
  const snapshots=[];
  for(const [index,page] of [...pages].filter(page=>!page.isClosed()).slice(-5).entries()){
   const filename=`${name}-terminal-${index}.png`;
   try{await page.screenshot({path:join(evidenceDir,filename)});snapshots.push({filename,saved:true})}
   catch(capture){snapshots.push({filename,saved:false,reason:String(capture.message)})}
  }
  return await record(name,error instanceof CantCheck?(required?'CANT_CHECK':'NOT_QUALIFIED'):'FAIL',
   {reason:String(error.message).replaceAll(root,'<source>/'),snapshots},required);
 }
}
const action=(page,name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const canvasPixels=figure=>figure.locator('canvas').evaluate(node=>{
 const data=node.getContext('2d').getImageData(0,0,node.width,node.height).data;
 // Small synthetic pixels are exact; large proof is sampled, not a memory copy.
 const points=[0,Math.floor(node.width/2),node.width*node.height-1];
 return {width:node.width,height:node.height,pixels:data.length<=128?Array.from(data):points.map(p=>Array.from(data.slice(p*4,p*4+4)))};
});

// Observe REAL native decoder work and application draws. Do not substitute
// decoded frames or clocks. Test-only gates delay delivery for late cleanup.
function installProbe({unsupported=false,corrupt=false,hold=false}={}){
 const fetch=window.fetch,posts=window.__mediaReportPosts=[];
 window.fetch=function(input,options){
  if(options?.method==='POST'&&new URL(typeof input==='string'?input:input.url,location.href).pathname==='/api/actions'){
   const body=typeof options.body==='string'?JSON.parse(options.body):null;
   if(body?.action==='outputs.mediaReport')posts.push({at:performance.now(),...body.args});
  }
  return fetch.call(this,input,options);
 };
 // Instrument the actual application-owned native callbacks/methods before
 // React mounts. Browser auto-pause/DOM removal cannot satisfy these counters.
 const native=window.__nativeMediaProbe={frames:[],nodes:new WeakMap()};
 const row=node=>{
  if(!native.nodes.has(node))native.nodes.set(node,{pause:0,cancel:[],cancelledPending:0,removeSrc:0,load:0,requests:0,presented:0,pending:null});
  return native.nodes.get(node);
 };
 const proto=HTMLVideoElement.prototype;
 const requestFrame=proto.requestVideoFrameCallback,cancelFrame=proto.cancelVideoFrameCallback;
 if(requestFrame)proto.requestVideoFrameCallback=function(callback){
  const node=this,owned=row(node);owned.requests++;
  const id=requestFrame.call(node,(at,metadata)=>{
   owned.pending=null;owned.presented++;
   const canvas=document.createElement('canvas');canvas.width=16;canvas.height=12;
   const context=canvas.getContext('2d');context.drawImage(node,0,0,16,12);
   native.frames.push({id:node.closest('[data-media-id]')?.dataset.mediaId,
    presentedFrames:owned.presented,nativePresentedFrames:metadata.presentedFrames,
    mediaTime:metadata.mediaTime,pixels:Array.from(context.getImageData(0,0,16,12).data),at});
   callback(at,metadata);
  });
  owned.pending=id;return id;
 };
 if(cancelFrame)proto.cancelVideoFrameCallback=function(id){
  const owned=row(this);owned.cancel.push(id);
  if(owned.pending===id){owned.cancelledPending++;owned.pending=null}
  return cancelFrame.call(this,id);
 };
 const pause=proto.pause,load=proto.load,remove=Element.prototype.removeAttribute;
 proto.pause=function(){row(this).pause++;return pause.call(this)};
 proto.load=function(){row(this).load++;return load.call(this)};
 Element.prototype.removeAttribute=function(name){
  if(this instanceof HTMLVideoElement&&name==='src')row(this).removeSrc++;
  return remove.call(this,name);
 };
 const Native=globalThis.ImageDecoder;
 const probe=window.__mediaProbe={decoders:0,frames:0,active:0,peakActive:0,peakFrames:0,
  draws:[],decoded:[],created:0,closed:0,frameClosed:0,held:[],hold,perDecoder:{}};
 if(unsupported){Object.defineProperty(globalThis,'ImageDecoder',{value:undefined});return}
 if(!Native)return;
 const frames=new WeakMap();
 globalThis.ImageDecoder=class extends Native{
  constructor(args){super(args);this.probeId=++probe.created;probe.decoders++;probe.perDecoder[this.probeId]={live:0,peak:0}}
  async decode(args){
   probe.peakActive=Math.max(probe.peakActive,++probe.active);
   try{
    if(corrupt)throw new Error('Synthetic decoder failure');
    const result=await super.decode(args),frame=result.image,owner=probe.perDecoder[this.probeId];
    frames.set(frame,{index:args.frameIndex,decoderId:this.probeId});
    probe.frames++;probe.peakFrames=Math.max(probe.peakFrames,probe.frames);
    owner.peak=Math.max(owner.peak,++owner.live);
    const close=frame.close.bind(frame);let closed=false;
    frame.close=()=>{if(!closed){closed=true;probe.frames--;owner.live--;probe.frameClosed++}return close()};
    probe.decoded.push({index:args.frameIndex,decoderId:this.probeId,timestamp:frame.timestamp,duration:frame.duration});
    if(probe.hold)await new Promise(done=>probe.held.push(done));
    return result;
   }finally{probe.active--}
  }
  close(){if(!this.probeClosed){this.probeClosed=true;probe.decoders--;probe.closed++}return super.close()}
 };
 const draw=CanvasRenderingContext2D.prototype.drawImage;
 CanvasRenderingContext2D.prototype.drawImage=function(frame,...args){
  const result=draw.call(this,frame,...args),info=frames.get(frame);
  if(info)probe.draws.push({...info,id:this.canvas.closest('[data-media-id]')?.dataset.mediaId,
   at:performance.now(),timestamp:frame.timestamp,duration:frame.duration});
  return result;
 };
}
const probeState=page=>page.evaluate(()=>{
 const {held,...data}=window.__mediaProbe;return {...data,heldCount:held.length};
});
const draws=(page,id)=>page.evaluate(id=>window.__mediaProbe.draws.filter(row=>row.id===id),id);
const nativeFrames=(page,id)=>page.evaluate(id=>window.__nativeMediaProbe.frames.filter(row=>row.id===id),id);
const dispatchedReports=(page,id)=>page.evaluate(id=>window.__mediaReportPosts.filter(row=>row.id===id),id);
const retainedVideoState=page=>page.evaluate(()=>{
 const node=window.__retainedNativeVideo,owned=window.__nativeMediaProbe.nodes.get(node);
 return {connected:node.isConnected,hasSrc:node.hasAttribute('src'),paused:node.paused,...owned,cancel:[...owned.cancel]};
});
const clockWait=(page,milliseconds)=>page.evaluate(ms=>new Promise(done=>{
 const deadline=performance.now()+ms;
 function tick(){if(performance.now()>=deadline)done();else requestAnimationFrame(tick)}
 requestAnimationFrame(tick);
}),milliseconds);
async function control(page,path,data){
 const response=await page.request.post(boot.url+'/api/fixture/'+path,{headers:privateHeaders,data});
 assert.equal(response.status(),200,await response.text());return response.json();
}
async function attach(page,name,instance=0){
 const result=await control(page,'media',{name,instance});
 assert.equal(result.accepted,true);
 return result.result;
}
async function newClient(options={}){
 // Distinct pages have distinct host client identities. One browser context
 // preserves that discriminator without unsupported single-process multi-contexts.
 if(!sharedContext)sharedContext=await browser.newContext({baseURL:boot.url,viewport:{width:1280,height:950},storageState:sharedStorage});
 const context=sharedContext;
 contexts.add(context);
 const page=await context.newPage();pages.add(page);
 page.on('pageerror',error=>pageErrors.push(String(error.stack||error.message)));
 await page.addInitScript(installProbe,options);
 if(options.suppressReports){
  // Negative control: real pixels still advance, but the host must not acquire
  // an observation. Never create a synthetic report to make readback green.
  await page.route('**/api/actions',async route=>{
   const body=route.request().postDataJSON();
   if(body?.action==='outputs.mediaReport'){
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:true,result:{}})});
   }else await route.continue();
  });
 }
 if(options.corruptVideoId){
  await page.route('**/api/outputs/*/media?*',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname!==`/api/outputs/${options.corruptVideoId}/media`||url.searchParams.has('download'))
    return route.continue();
   // Fetch through normal auth/tuple/Range guards first. Only this client's
   // inline response is test-corrupted; original downloads remain untouched.
   const response=await route.fetch();assert.ok([200,206].includes(response.status()));
   await route.fulfill({status:200,contentType:'video/mp4',body:Buffer.from('synthetic undecodable MP4')});
  });
 }
 if(!sharedStorage){
  await page.goto(boot.url+'/login');
  await page.getByLabel('Username').fill('media-fixture');
  await page.getByLabel('Password').fill('fixture-password');
  // Register BEFORE login navigates into the app; no ready-DOM inference.
  const empty=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/outputs/media'&&
   new URL(response.url()).searchParams.get('messageId')===boot.messageId);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  const response=await empty;assert.equal(response.status(),200);
  assert.equal(await response.finished(),null);assert.deepEqual((await response.json()).items,[]);
  sharedStorage=await context.storageState();
  await record('empty-descriptor-completed','PASS',{status:200,items:[],completed:true});
 }else await page.goto(boot.url);
 await page.waitForSelector('#amp-one');
 const article=page.locator(`[data-message-id="${boot.messageId}"]`);
 await expect(article).toBeVisible();
 return {page,context,article};
}
async function unmountAndClose(client){
 const {page,context}=client;
 await action(page,'session.create',{title:'Synthetic cleanup conversation'});
 await expect.poll(()=>page.locator('[data-media-id]').count()).toBe(0);
 await expect.poll(async()=>{const p=await probeState(page);return p.frames+p.decoders+p.active}).toBe(0);
 const evidence=await probeState(page);
 await page.close();assert.equal(page.isClosed(),true);pages.delete(page);
 if(context.pages().length===0){await context.close();contexts.delete(context);sharedContext=null}
 return evidence;
}
async function restoreSession(page){
 await action(page,'session.select',{id:boot.sessionId});
 await expect(page.locator(`[data-message-id="${boot.messageId}"]`)).toBeVisible();
}
const figureFor=(page,id)=>page.locator(`[data-media-id="${id}"]`);
async function readyFigure(page,id){
 const figure=figureFor(page,id);
 await figure.scrollIntoViewIfNeeded();
 await expect(figure).toHaveAttribute('data-media-state','ready');
 return figure;
}

function watchReports(page,id){
 const rows=[];
 const listener=request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const body=request.postDataJSON();
  if(body?.action==='outputs.mediaReport'&&(!id||body.args.id===id))
   rows.push({at:performance.now(),...body.args});
 };
 page.on('request',listener);
 return {rows,stop:()=>page.off('request',listener)};
}
async function readOutput(page,item){
 const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 const response=await page.request.post(boot.url+'/api/actions',{headers:{'X-Amplifier-Client':clientId},
  data:{action:'outputs.read',args:{sessionId:item.sessionId,id:item.id}}});
 assert.equal(response.status(),200,await response.text());
 const body=await response.json();assert.equal(body.accepted,true);
 const result=body.result;
 for(const key of ['id','sessionId','version','sha256'])assert.equal(result[key],item[key],`Exact read tuple: ${key}`);
 return {clientId,result};
}
function requireObservation({clientId,result},{playback,renderState='ready',frames,mediaTime,since}){
 const observation=result.browserObservation;
 assert.equal(observation.status,'observed','Real reports required: pixels alone cannot qualify readback');
 assert.equal(observation.clientId,clientId);
 assert.equal(observation.evidence,'authenticated-ui-report');assert.equal(observation.modelAttestation,false);
 assert.equal(observation.renderState,renderState);assert.equal(observation.playback,playback);
 assert.equal(result.playback,playback);assert.equal(result.saved,true);assert.equal(result.attached,true);
 assert.ok(Number.isFinite(observation.serverTimestamp));
 assert.ok(observation.serverTimestamp>=since/1000&&Math.abs(Date.now()/1000-observation.serverTimestamp)<10,
  'Fresh server timestamp, not cached/synthetic evidence');
 assert.equal(observation.presentedFrames,frames);assert.equal(observation.mediaTime,mediaTime);
 assert.ok(observation.generation>0&&observation.sequence>0);
 return observation;
}
async function observeLatest(page,item,expected){
 // Wait only for REAL app transport completion. Readback is exact-client and
 // never POSTs outputs.mediaReport itself.
 await expect.poll(async()=>{
  const {result}=await readOutput(page,item),o=result.browserObservation;
  return o.status==='observed'&&o.playback===expected.playback&&o.renderState===(expected.renderState||'ready')&&
   o.presentedFrames===expected.frames&&o.mediaTime===expected.mediaTime;
 }).toBe(true);
 return requireObservation(await readOutput(page,item),expected);
}

try{
 await phase('shared-setup',async()=>{
  fixture=spawn(python,['-u',join(root,'tests/fixtures/inline_media_server.py')],{
   cwd:mode==='installed'?resolve(evidenceDir):root,
   env:{...process.env,INLINE_MEDIA_MODE:mode,PYTHONPATH:mode==='installed'?'':process.env.PYTHONPATH||''},
   stdio:['ignore','pipe','pipe']});
  let buffer='';let resolveBoot,rejectBoot;
  const pendingBoot=new Promise((yes,no)=>{resolveBoot=yes;rejectBoot=no});
  fixture.stdout.on('data',chunk=>{
   buffer+=chunk;let at;
   while((at=buffer.indexOf('\n'))>=0){
    const line=buffer.slice(0,at);buffer=buffer.slice(at+1);
    try{const row=JSON.parse(line);fixtureRows.push(row);if(row.url)resolveBoot(row)}catch{}
   }
  });
  // Preserve diagnostics privately, not unchecked fixture stdout in CI uploads.
  let stderr='';fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});
  exit=new Promise(done=>{
   fixture.once('close',(code,signal)=>{done({code,signal});rejectBoot(Error('Fixture closed before boot: '+code))});
   fixture.once('error',error=>{done({code:null,signal:null,spawnError:true});rejectBoot(error)});
  });
  const timeout=setTimeout(()=>rejectBoot(Error('Fixture startup deadline exceeded')),120000);
  try{boot=await pendingBoot}finally{clearTimeout(timeout)}
  browser=await chromium.launch({headless:!headed,
   ...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{}),
   args:browserArgs});
  assert.equal(boot.mode,mode);
  assert.ok(boot.frontendAssets.some(row=>row.filename.endsWith('.js')));
  return {mode:boot.mode,python:boot.python,browserVersion:browser.version(),headed,samples:boot.samples,
   servedPackageFrontend:boot.frontendAssets,
   security:'production authentication, origin boundaries, CSP and media routes unchanged'};
 });
 if(!browser||!boot){
  for(const name of scenarioPhases){
   const physical=physicalNames.includes(name),notSelected=selectedPhases&&!selectedPhases.has(name);
   await record(name,physical||notSelected?'NOT_RUN':'CANT_CHECK',{reason:'Shared setup blocked'},!physical&&!notSelected);
  }
 }else{
  let primary,saved;
  await phase('gif-live-attach',async()=>{
   primary=await newClient();
   const {page,article}=primary;
   await expect(article.locator('[data-media-id]')).toHaveCount(0);
   const nonce=await page.evaluate(()=>window.__mediaDocumentNonce=crypto.randomUUID());
   let navigations=0;
   const navigated=frame=>{if(frame===page.mainFrame())navigations++};
   page.on('framenavigated',navigated);
   saved=await attach(page,'gif');
   const figure=await readyFigure(page,saved.id);
   assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
   const initial=await canvasPixels(figure);
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
   await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
   await figure.locator('canvas').screenshot({path:join(evidenceDir,'gif-pixels.png')});
   const matrix=await control(page,'matrix',{});
   assert.ok(matrix.cases.every(row=>row.rejected&&row.unchanged));
   assert.equal(await page.evaluate(()=>window.__mediaDocumentNonce),nonce);
   assert.equal(navigations,0);page.off('framenavigated',navigated);
   return {saved,initial,changed:await canvasPixels(figure),matrix,navigationUnchanged:true,
    documentNonce:nonce,mainFrameNavigations:navigations,observer:'actual application canvas'};
  });
  await phase('gif-resume',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup failed; MP4 remains independent');
   const {page}=primary,timing=await attach(page,'timing'),figure=await readyFigure(page,timing.id);
   const baseline=(await draws(page,timing.id)).at(-1).at;
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   // Wait for an ACTUAL nonzero frame with enough nonuniform delay to measure.
   await expect.poll(async()=>{
    const row=(await draws(page,timing.id)).at(-1);
    return !!row&&row.at>baseline&&row.index>0&&row.duration>=700000;
   }).toBe(true);
   const frame=(await draws(page,timing.id)).at(-1);
   await page.waitForFunction(frame=>performance.now()-frame.at>=frame.duration/1000*.3,frame);
   await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
   const pausedAt=await page.evaluate(()=>performance.now()),paused=await canvasPixels(figure);
   const stopped=(await draws(page,timing.id)).at(-1);
   assert.equal(stopped.index,frame.index);assert.ok(stopped.index>0);
   const elapsed=pausedAt-frame.at,remaining=frame.duration/1000-elapsed;
   assert.ok(remaining>150&&elapsed>100,'Pause must occur partway, not at the frame boundary');
   await clockWait(page,frame.duration/1000+100);
   assert.deepEqual(await canvasPixels(figure),paused);
   assert.equal((await draws(page,timing.id)).at(-1).at,frame.at);
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   const resumedAt=await page.evaluate(()=>performance.now());
   assert.deepEqual(await canvasPixels(figure),paused,'Resume keeps SAME nonzero frame');
   await expect.poll(async()=>(await draws(page,timing.id)).at(-1).at).toBeGreaterThan(frame.at);
   const next=(await draws(page,timing.id)).at(-1),actual=next.at-resumedAt;
   assert.ok(Math.abs(actual-remaining)<150,JSON.stringify({actual,remaining,elapsed}));
   assert.ok(actual<frame.duration/1000-100,'Resume must not restart full delay');
   await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
   return {sample:timing.sha256,frameIndex:frame.index,elapsedMs:elapsed,remainingMs:remaining,resumedDelayMs:actual,pausedPixels:paused};
  });

  let fast;
  await phase('gif-report-cadence',async()=>{
   const client=await newClient(),{page}=client,watch=watchReports(page);
   try{
    const attachedSince=Date.now();
    fast=await attach(page,'fast');
    const figure=await readyFigure(page,fast.id),initial=await canvasPixels(figure);
    const initialDraws=await draws(page,fast.id);
    await observeLatest(page,fast,{playback:'paused',frames:initialDraws.length,
     mediaTime:initialDraws.at(-1).timestamp/1_000_000,since:attachedSince});
    const since=Date.now(),started=await page.evaluate(()=>performance.now());
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
    await clockWait(page,2200);
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    const ended=await page.evaluate(()=>performance.now()),actual=await draws(page,fast.id),latest=actual.at(-1);
    assert.ok(actual.length>=50,'Real 10 ms GIF must keep advancing; do not meet report cap by slowing frames');
    assert.ok(actual.every(row=>row.duration===10000),'Fixture uses native 10 ms frame durations');
    const observation=await observeLatest(page,fast,{playback:'paused',frames:actual.length,
     mediaTime:latest.timestamp/1_000_000,since});
    const reports=await dispatchedReports(page,fast.id),requests=watch.rows.filter(row=>row.id===fast.id),
     moving=reports.filter(row=>row.playback==='playing');
    assert.equal(requests.length,reports.length,'Dispatch attempts counted against real HTTP POST requests');
    assert.ok(moving.length>=2,'Include at least one real same-state advancing report');
    assert.ok(moving.length<=1+Math.floor((ended-started)/1000));
    for(let n=1;n<moving.length;n++)assert.ok(moving[n].at-moving[n-1].at>=1000,
     'Same-state requests must be spaced by 1 second, not frame modulo bursts');
    // Loading, initial-ready, play and pause are immediate transitions; each
    // contributes separately to the inclusive observation-window bound.
    assert.ok(reports.length<=4+Math.floor((ended-started)/1000));
    const transitions=reports.filter((row,n)=>n===0||
     row.renderState!==reports[n-1].renderState||row.playback!==reports[n-1].playback||row.detail!==reports[n-1].detail);
    assert.deepEqual(transitions.map(row=>[row.renderState,row.playback]),[
     ['loading','paused'],['ready','paused'],['ready','playing'],['ready','paused']]);
    for(let n=0;n<reports.length;n++){
     const row=reports[n];
     assert.equal(row.sessionId,fast.sessionId);assert.equal(row.messageId,boot.messageId);
     assert.equal(row.version,fast.version);assert.equal(row.sha256,fast.sha256);
     assert.equal(row.generation,observation.generation);
     if(n)assert.ok(row.sequence>reports[n-1].sequence);
     if(row.presentedFrames)assert.equal(row.mediaTime,actual[row.presentedFrames-1].timestamp/1_000_000);
    }
    assert.equal(observation.sequence,reports.at(-1).sequence);
    await action(page,'session.create',{title:'Dispose bounded report timer'});
    await expect(page.locator('[data-media-id]')).toHaveCount(0);
    const after=watch.rows.filter(row=>row.id===fast.id).length;
    await clockWait(page,1100);
    assert.equal(watch.rows.filter(row=>row.id===fast.id).length,after,'No delayed reports after unmount');
    return {sample:fast,windowMs:ended-started,actualPresentedFrames:actual.length,actualMediaTime:latest.timestamp/1_000_000,
     reports,requests,transitions,observation,cleanup:await unmountAndClose(client)};
   }finally{watch.stop();if(!page.isClosed())await unmountAndClose(client)}
  });
  await phase('report-readback-negative-control',async()=>{
   const client=await newClient({suppressReports:true}),{page}=client;
   try{
    const item=fast||await attach(page,'fast'),figure=await readyFigure(page,item.id);
    const initial=await canvasPixels(figure),since=Date.now();
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    const actual=await draws(page,item.id),read=await readOutput(page,item);
    assert.equal(read.result.browserObservation.status,'not_observed');
    assert.throws(()=>requireObservation(read,{playback:'paused',frames:actual.length,
     mediaTime:actual.at(-1).timestamp/1_000_000,since}),/Real reports required/);
    return {pixelsAdvanced:true,exactClient:read.clientId,readback:read.result.browserObservation,
     readbackGuardRejected:true,syntheticReportsSubmitted:false,cleanup:await unmountAndClose(client)};
   }finally{if(!page.isClosed())await unmountAndClose(client)}
  });

  // Independent phase: no GIF assertion or decoder capability gates this.
  let videoClient,mp4;
  await phase('mp4-native',async()=>{
   if(!boot.samples.mp4)throw new CantCheck('Supply INLINE_MEDIA_MP4 containing an actual playable synthetic/public MP4');
   videoClient=await newClient();
   const {page}=videoClient;mp4=await attach(page,'mp4');
   const figure=await readyFigure(page,mp4.id),video=figure.locator('video');
   const attrs=await video.evaluate(node=>({controls:node.controls,muted:node.muted,autoplay:node.autoplay,paused:node.paused}));
   assert.deepEqual(attrs,{controls:true,muted:true,autoplay:false,paused:true});
   assert.ok(await video.evaluate(node=>typeof node.requestVideoFrameCallback==='function'));
   const since=Date.now(),watch=watchReports(page,mp4.id);
   await video.evaluate(node=>node.play());
   await expect.poll(async()=>(await nativeFrames(page,mp4.id)).length).toBeGreaterThan(2);
   await expect.poll(async()=>{
    const {result}=await readOutput(page,mp4),o=result.browserObservation;
    return o.status==='observed'&&o.playback==='playing'&&o.presentedFrames>2&&o.mediaTime>0;
   }).toBe(true);
   const playingRead=await readOutput(page,mp4),playing=playingRead.result.browserObservation;
   const frames=await nativeFrames(page,mp4.id);
   const witnessed=frames.find(row=>row.presentedFrames===playing.presentedFrames);
   assert.ok(witnessed,'Readback count must refer to an actual application-owned native callback');
   requireObservation(playingRead,{playback:'playing',frames:witnessed.presentedFrames,mediaTime:witnessed.mediaTime,since});
   assert.ok(frames.at(-1).presentedFrames>frames[0].presentedFrames);
   assert.ok(frames.at(-1).mediaTime>frames[0].mediaTime);
   assert.notDeepEqual(frames.at(-1).pixels,frames[0].pixels);
   await video.evaluate(node=>node.pause());
   // The final native callback can run after pause(). Compare against the LIVE
   // witness inside the poll, not an already-obsolete frozen expected count.
   let pausedPair;
   await expect.poll(async()=>{
    const read=await readOutput(page,mp4),latest=(await nativeFrames(page,mp4.id)).at(-1),o=read.result.browserObservation;
    const matched=o.status==='observed'&&o.playback==='paused'&&o.presentedFrames===latest.presentedFrames&&o.mediaTime===latest.mediaTime;
    if(matched)pausedPair={read,latest};
    return matched;
   }).toBe(true);
   const pausedObservation=requireObservation(pausedPair.read,{playback:'paused',
    frames:pausedPair.latest.presentedFrames,mediaTime:pausedPair.latest.mediaTime,since});
   const nativeReports=await dispatchedReports(page,mp4.id),moving=nativeReports.filter(row=>row.playback==='playing');
   for(let n=1;n<moving.length;n++)assert.ok(moving[n].at-moving[n-1].at>=1000);
   watch.stop();
   const at=await video.evaluate(node=>node.currentTime);
   await clockWait(page,Math.max(100,(frames.at(-1).mediaTime-frames.at(-2).mediaTime)*3000));
   assert.equal(await video.evaluate(node=>node.currentTime),at);
   const seek=await video.evaluate(node=>new Promise((done,no)=>{
    const target=Math.min(node.duration*.65,node.duration-.1);
    if(!Number.isFinite(target)||target<=.1){no(Error('MP4 duration cannot support the seek discriminator'));return}
    const deadline=setTimeout(()=>no(Error('seeked deadline exceeded')),10000);
    node.addEventListener('seeked',()=>{clearTimeout(deadline);done({target,actual:node.currentTime,seeking:node.seeking})},{once:true});
    node.currentTime=target;
   }));
   assert.equal(seek.seeking,false);assert.ok(Math.abs(seek.actual-seek.target)<.08);
   await video.screenshot({path:join(evidenceDir,'mp4-native.png')});
   return {mp4,attrs,frames:frames.map(({pixels,...row})=>({...row,pixelSha256:createHash('sha256').update(Buffer.from(pixels)).digest('hex')})),
    playingObservation:playing,pausedObservation,reportRequests:watch.rows,nativeReports,
    pausedTime:at,seekTarget:seek.target,seekedTime:seek.actual};
  });
  await phase('mp4-native-controls',async()=>{
   if(!videoClient||!mp4)throw new CantCheck('MP4 setup did not reach native controls');
   const {page}=videoClient;
   // UA shadow controls are not portable DOM. Only claim when THIS browser
   // exposes usable accessibility targets; absence is explicit, never PASS.
   const fullscreen=page.getByRole('button',{name:/full.?screen/i}),volume=page.getByRole('slider',{name:/volume/i});
   if(await fullscreen.count()!==1||await volume.count()!==1)
    throw new CantCheck('Browser does not expose native fullscreen/volume controls as actionable accessibility targets');
   await fullscreen.click();
   await expect.poll(()=>page.evaluate(()=>!!document.fullscreenElement)).toBe(true);
   await page.screenshot({path:join(evidenceDir,'mp4-fullscreen.png')});
   await page.keyboard.press('Escape');
   const video=figureFor(page,mp4.id).locator('video');
   const before=await video.evaluate(node=>node.volume);
   await volume.focus();await page.keyboard.press(before>.5?'ArrowDown':'ArrowUp');
   await expect.poll(()=>video.evaluate(node=>node.volume)).not.toBe(before);
   return {fullscreenUI:true,volumeUI:true};
  },{required:false});

  await phase('mp4-decode-error',async()=>{
   if(!mp4)throw new CantCheck('MP4 sample has not been attached');
   const client=await newClient({corruptVideoId:mp4.id}),{page}=client;
   try{
    const since=Date.now(),figure=figureFor(page,mp4.id);
    await figure.scrollIntoViewIfNeeded();
    await expect(figure).toHaveAttribute('data-media-state','error');
    await expect(figure).toContainText('cannot be decoded');
    const download=figure.getByRole('link',{name:'Download original'});
    await expect(download).toBeVisible();
    const response=await page.request.get(await download.getAttribute('href'));
    assert.equal(response.status(),200);
    assert.equal(createHash('sha256').update(await response.body()).digest('hex'),mp4.sha256);
    const observation=await observeLatest(page,mp4,{playback:'paused',renderState:'error',frames:0,mediaTime:0,
     since:since-5000}); // page load may already have reported the decode error
    return {testOnlyCorruptInlineRead:true,downloadSha256:mp4.sha256,distinctError:true,observation,
     cleanup:await unmountAndClose(client)};
   }finally{if(!page.isClosed())await unmountAndClose(client)}
  });
  await phase('mp4-explicit-cleanup',async()=>{
   if(!videoClient||!mp4)throw new CantCheck('MP4 native phase did not retain a client');
   const {page}=videoClient,video=figureFor(page,mp4.id).locator('video'),watch=watchReports(page,mp4.id);
   try{
    await video.scrollIntoViewIfNeeded();
    await video.evaluate(node=>{window.__retainedNativeVideo=node;return node.play()});
    await expect.poll(()=>video.evaluate(node=>node.paused)).toBe(false);
    const before=await retainedVideoState(page);
    assert.equal(before.connected,true);assert.equal(before.hasSrc,true);assert.notEqual(before.pending,null);
    await action(page,'session.create',{title:'Detach actual native MP4 owner'});
    await expect(figureFor(page,mp4.id)).toHaveCount(0);
    await expect.poll(()=>retainedVideoState(page)).toMatchObject({connected:false,hasSrc:false,paused:true});
    const after=await retainedVideoState(page);
    assert.ok(after.pause>before.pause,'Owned pause() call, not browser auto-pause');
    assert.ok(after.cancel.length>before.cancel.length&&after.cancelledPending>before.cancelledPending,
     'Owned cancellation of an actually pending video frame callback');
    assert.ok(after.removeSrc>before.removeSrc,'Owned removeAttribute(src) call');
    assert.ok(after.load>before.load,'Owned load() call after src removal');
    const reports=watch.rows.length;
    await clockWait(page,1100);
    const settled=await retainedVideoState(page);
    assert.equal(settled.presented,after.presented);assert.equal(settled.requests,after.requests);
    assert.equal(watch.rows.length,reports,'No old-native reports after unmount');
    return {before,after,settled,methodWitnesses:true,browserCloseUsedAsProof:false,
     cleanup:await unmountAndClose(videoClient)};
   }finally{watch.stop()}
  });

  await phase('partial-disposal',async()=>{
   const client=await newClient();
   try{
    const saved=await attach(client.page,'partial'),figure=await readyFigure(client.page,saved.id);
    const check=index=>canvasPixels(figure).then(row=>assert.deepEqual(row.pixels,boot.partialPixels[index].flat()));
    await check(0);
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    for(let index=1;index<4;index++){
     await expect.poll(async()=>(await draws(client.page,saved.id)).at(-1)?.index).toBe(index);
     await check(index);
    }
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    await figure.locator('canvas').screenshot({path:join(evidenceDir,'partial-transparent.png')});
    return {disposal:[2,3],expected:boot.partialPixels,cleanup:await unmountAndClose(client)};
   }finally{if(!client.page.isClosed())await unmountAndClose(client)}
  });
  await phase('large-native',async()=>{
   const client=await newClient(),{page}=client;
   try{
    const large=await attach(page,'large');
    assert.equal(large.bytes,boot.samples.large.bytes);
    assert.equal(large.sha256,boot.samples.large.sha256);
    assert.ok(large.bytes>=19_000_000&&large.bytes<=20_100_000);
    assert.deepEqual([large.media.width,large.media.height,large.media.frameCount],[1280,720,26]);
    const figure=await readyFigure(page,large.id),initial=await canvasPixels(figure);
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
    await expect.poll(async()=>(await draws(page,large.id)).at(-1)?.index||0).toBeGreaterThan(0);
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    // Three real application players admit native work concurrently in one
    // realm. Counters observe native decode calls, not mocked source estimates.
    const additional=await Promise.all([attach(page,'large',1),attach(page,'large',2)]);
    const largeIds=[large.id,...additional.map(item=>item.id)];
    assert.equal(new Set(largeIds).size,3);
    for(const id of largeIds){
     await readyFigure(page,id);
     await expect.poll(async()=>(await draws(page,id)).length).toBeGreaterThan(0);
    }
    const decoderIds=await page.evaluate(ids=>ids.map(id=>window.__mediaProbe.draws.find(row=>row.id===id).decoderId),largeIds);
    assert.equal(new Set(decoderIds).size,3,'Three actual application-owned decoders, not an old DOM-count barrier');
    const live=await probeState(page);
    assert.ok(live.peakActive<=2&&live.peakActive>0);
    assert.ok(Object.values(live.perDecoder).every(row=>row.peak<=2));
    const cdp=await client.context.newCDPSession(page);
    const browserMemory=await cdp.send('Runtime.getHeapUsage');
    await cdp.detach();
    const serverMemory=await (await page.request.get(boot.url+'/api/fixture/evidence',{headers:privateHeaders})).json();
    await figure.locator('canvas').screenshot({path:join(evidenceDir,'large-moving-pixels.png')});
    return {sample:{bytes:large.bytes,sha256:large.sha256,width:1280,height:720,frames:26},
     largeIds,decoderIds,observed:live,browserMemory,serverRssBytes:serverMemory.rssBytes,
     nativeCaches:'UNKNOWN: JS/CDP heap and retained VideoFrame counts do not bound browser codec caches or native process heap',
     cleanup:await unmountAndClose(client)};
   }finally{if(!page.isClosed())await unmountAndClose(client)}
  });

  await phase('reduced-motion',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const {page}=primary,figure=await readyFigure(page,saved.id);
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   await page.emulateMedia({reducedMotion:'reduce'});
   await expect(figure).toHaveAttribute('data-playback','paused');
   const pixels=await canvasPixels(figure),frame=(await draws(page,saved.id)).at(-1);
   await clockWait(page,frame.duration/1000+100);
   assert.deepEqual(await canvasPixels(figure),pixels);
   await page.emulateMedia({reducedMotion:'no-preference'});
   return {paused:true,frameIndex:frame.index};
  });
  await phase('visibility',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const {page,context}=primary,figure=await readyFigure(page,saved.id);
   const other=await context.newPage();pages.add(other);
   try{
    await page.bringToFront();await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    await other.goto(boot.url+'/api/health');await other.bringToFront();
    if(!await page.evaluate(()=>document.hidden))throw new CantCheck('This browser does not mark background tabs hidden');
    await expect(figure).toHaveAttribute('data-playback','paused');
    return {actualDocumentHidden:true,paused:true};
   }finally{
    await other.close();pages.delete(other);await page.bringToFront();
    // An unsupported headless visibility sensor leaves playback running.
    // Settle this phase explicitly; the next phase must not inherit that state.
    const pause=figure.getByRole('button',{name:'Pause GIF',exact:true});
    if(await pause.count())await pause.click();
   }
  },{required:false});
  await phase('offscreen',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const {page}=primary,figure=await readyFigure(page,saved.id);
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   // Real scrolling, not an injected intersection report.
   await page.locator('.a-messages').evaluate(node=>{node.scrollTop=0});
   const box=await figure.boundingBox(),pane=await page.locator('.a-messages').boundingBox();
   if(box&&box.y+box.height>pane.y&&box.y<pane.y+pane.height)
    throw new CantCheck('Fixture conversation has insufficient scroll range to hide the media');
   await expect.poll(async()=>await figure.count()===0||await figure.getAttribute('data-playback')==='paused').toBe(true);
   const unmounted=await figure.count()===0;
   if(unmounted)await expect.poll(async()=>{const p=await probeState(page);return p.frames+p.decoders+p.active}).toBe(0);
   else{
    const pixels=await canvasPixels(figure),frame=(await draws(page,saved.id)).at(-1);
    await clockWait(page,frame.duration/1000+100);
    assert.deepEqual(await canvasPixels(figure),pixels);
   }
   if(await figureFor(page,saved.id).count())await figureFor(page,saved.id).scrollIntoViewIfNeeded();
   return {offscreen:true,pausedOrUnmounted:true,unmounted};
  });
  await phase('decoder-error',async()=>{
   const client=await newClient({corrupt:true});
   try{
    const figure=figureFor(client.page,saved?.id|| (await attach(client.page,'gif')).id);
    await expect(figure).toHaveAttribute('data-media-state','error');
    await expect(figure.getByRole('link',{name:'Download original'})).toBeVisible();
    return {distinctError:true,cleanup:await unmountAndClose(client)};
   }finally{if(!client.page.isClosed())await unmountAndClose(client)}
  });
  await phase('unsupported-decoder',async()=>{
   const client=await newClient({unsupported:true});
   try{
    const item=saved||await attach(client.page,'gif'),figure=figureFor(client.page,item.id);
    await expect(figure).toHaveAttribute('data-media-state','error');
    await expect(figure).toContainText('unsupported');
    await expect(figure.getByRole('link',{name:'Download original'})).toBeVisible();
    return {unsupportedExplicit:true,cleanup:await unmountAndClose(client)};
   }finally{if(!client.page.isClosed())await unmountAndClose(client)}
  });
  await phase('decoder-late-cleanup',async()=>{
   const client=await newClient({hold:true}),{page}=client;
   try{
    await expect.poll(async()=>(await probeState(page)).heldCount).toBeGreaterThan(0);
    await action(page,'session.create',{title:'Unmount while native decode is pending'});
    await expect(page.locator('[data-media-id]')).toHaveCount(0);
    await page.evaluate(()=>{window.__mediaProbe.hold=false;window.__mediaProbe.held.splice(0).forEach(done=>done())});
    await expect.poll(async()=>{const p=await probeState(page);return p.frames+p.decoders+p.active}).toBe(0);
    const state=await probeState(page);assert.equal(state.created,state.closed);
    return {lateFrameClosed:true,cleanup:await unmountAndClose(client)};
   }finally{
    if(!page.isClosed()){
     await page.evaluate(()=>window.__mediaProbe.held.splice(0).forEach(done=>done()));
     await unmountAndClose(client);
    }
   }
  });

  await phase('preservation',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const peer=await newClient(),{page}=primary;
   try{
    await restoreSession(page);
    for(const [client,text] of [[primary,'Unsent draft A'],[peer,'Unsent draft B']]){
     await client.page.getByRole('textbox',{name:'Message Amplifier'}).fill(text);
     await expect(client.page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(text);
    }
    for(const p of [page,peer.page])await readyFigure(p,saved.id);
    const scrolls=await Promise.all([page,peer.page].map(p=>p.locator('.a-messages').evaluate(node=>node.scrollTop)));
    const item=await attach(page,'gif',1);
    // Publication/readiness only: do NOT move either reader to the new figure.
    await expect(figureFor(page,item.id)).toHaveAttribute('data-media-state','ready');
    await expect(figureFor(peer.page,item.id)).toHaveAttribute('data-media-state','ready');
    for(const [index,p,text] of [[0,page,'Unsent draft A'],[1,peer.page,'Unsent draft B']]){
     await expect(p.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(text);
     assert.ok(Math.abs(await p.locator('.a-messages').evaluate(node=>node.scrollTop)-scrolls[index])<3);
     assert.equal(await p.evaluate(()=>window.amplifier.getState().canvas.open),false);
    }
    await control(page,'mutate',{name:'gif',operation:'remove-source'});
    await page.reload();await page.waitForSelector('#amp-one');
    await readyFigure(page,saved.id);
    await action(page,'session.create',{title:'Reopen test'});await restoreSession(page);
    await readyFigure(page,saved.id);
    const evidence=await (await page.request.get(boot.url+'/api/fixture/evidence',{headers:privateHeaders})).json();
    assert.deepEqual(evidence.errors,[]);
    assert.ok(!Object.keys(evidence.counters).some(key=>key.startsWith('unexpected:')));
    return {twoClients:true,drafts:['Unsent draft A','Unsent draft B'],scrolls,sourceRemoved:true,reloaded:true,reopened:true,
     automaticRuntimeCalls:0,counters:evidence.counters,peerCleanup:await unmountAndClose(peer)};
   }finally{if(!peer.page.isClosed())await unmountAndClose(peer)}
  });
  await phase('endpoints',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const {page}=primary,figure=await readyFigure(page,saved.id);
   const link=await figure.getByRole('link',{name:'Download original'}).getAttribute('href');
   const url=new URL(link,boot.url);url.searchParams.delete('download');
   const download=await page.request.get(link);
   assert.equal(download.status(),200);
   const data=await download.body();assert.equal(createHash('sha256').update(data).digest('hex'),saved.sha256);
   assert.equal(data.length,saved.bytes);
   assert.ok(download.headers()['content-disposition'].startsWith('attachment;'));
   const get=await page.request.get(url.href);assert.equal(get.status(),200);assert.deepEqual(await get.body(),data);
   const head=await page.request.head(url.href);assert.equal(head.status(),200);assert.equal((await head.body()).length,0);
   assert.equal(Number(head.headers()['content-length']),data.length);
   for(const [range,start,end] of [['bytes=2-4',2,4],['bytes=2-',2,data.length-1],['bytes=-3',data.length-3,data.length-1]]){
    const response=await page.request.get(url.href,{headers:{Range:range}});
    assert.equal(response.status(),206);assert.deepEqual(await response.body(),data.subarray(start,end+1));
    assert.equal(response.headers()['content-range'],`bytes ${start}-${end}/${data.length}`);
   }
   assert.equal((await page.request.get(url.href,{headers:{Range:'bytes=0-1,3-4'}})).status(),400);
   assert.equal((await page.request.get(url.href,{headers:{Range:`bytes=${data.length}-`}})).status(),416);
   for(const [key,value] of [['sessionId','foreign'],['messageId','foreign'],['version','99'],['sha256','0'.repeat(64)],['clientId','foreign']]){
    const wrong=new URL(url);wrong.searchParams.set(key,value);
    const rejected=await page.request.get(wrong.href,{headers:{Range:'malformed'}});
    assert.equal(rejected.status(),key==='clientId'?409:400);
    const payload=await rejected.json();assert.equal(payload.accepted,false);
    assert.equal(typeof payload.error,'string');
    if(key!=='clientId')assert.ok(payload.error.startsWith('Invalid request: '));
   }
   const anonymous=await request.newContext();
   try{assert.ok([401,403].includes((await anonymous.get(url.href)).status()))}finally{await anonymous.dispose()}
   const unlinked=await control(page,'mutate',{name:'gif',operation:'unlink'});
   assert.equal(unlinked.accepted,true);
   const rejected=await page.request.get(url.href);assert.equal(rejected.status(),400);
   assert.deepEqual(await rejected.json(),{accepted:false,error:'Invalid request: Choose the exact linked assistant media version and hash.'});
   await expect(figure).toHaveCount(0);
   return {exactOriginalSha256:saved.sha256,bytes:data.length,GET:200,HEAD:200,Range:206,authRejected:true,
    wrongTuplesRejected:true,unlinkRejected:true};
  });
  await phase('layout',async()=>{
   if(!primary)throw new CantCheck('Browser setup unavailable');
   const {page}=primary;
   // PNG output registry remains unchanged; gallery/pixel read stays usable.
   const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
   const png=await page.request.post(boot.url+'/api/actions',{headers:{'X-Amplifier-Client':clientId},
    data:{action:'outputs.image',args:{sessionId:boot.sessionId,id:boot.pngId,sha256:boot.pngSha256}}});
   assert.equal(png.status(),200);
   const gallery=page.locator('.a-inline-gallery').first();
   await gallery.scrollIntoViewIfNeeded();
   await expect(gallery.locator('.a-inline-image img')).toBeVisible();
   assert.ok(await gallery.locator('.a-inline-image img').evaluate(node=>node.complete&&node.naturalWidth===4));
   const boxes=[];
   for(const width of [1280,390])for(const scheme of ['light','dark']){
    await page.setViewportSize({width,height:width===390?844:950});
    await action(page,'view.update',{patch:{scheme}});
    await page.locator('[data-media-id]').first().scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const overflow=await page.locator('[data-media-id]').evaluateAll(nodes=>nodes.some(node=>{
     const r=node.getBoundingClientRect();return r.left<0||r.right>innerWidth||node.scrollWidth>node.clientWidth+1;
    }));
    assert.equal(overflow,false);
    await page.screenshot({path:join(evidenceDir,`layout-${width}-${scheme}.png`)});
    boxes.push({width,scheme,overflow});
   }
   await page.setViewportSize({width:1280,height:950});
   const play=page.getByRole('button',{name:'Play GIF',exact:true}).first();
   await play.scrollIntoViewIfNeeded();
   const id=await play.evaluate(node=>node.closest('[data-media-id]').dataset.mediaId),figure=figureFor(page,id);
   await play.focus();await page.keyboard.press('Space');
   await expect(figure).toHaveAttribute('data-playback','playing');
   await page.keyboard.press('Space');
   await expect(figure).toHaveAttribute('data-playback','paused');
   return {boxes,keyboard:true,pngGallery:true};
  });
  await phase('keyboard-200-percent',async()=>{
   if(!primary)throw new CantCheck('Browser setup unavailable');
   const {page}=primary;
   try{
   // Browser keyboard zoom, not CSS transform masquerading as zoom.
   await page.keyboard.press('Control+0');
   const before=await page.evaluate(()=>devicePixelRatio);
   let after=before;
   for(let n=0;n<8&&after/before<1.99;n++){
    await page.keyboard.press('Control++');
    after=await page.evaluate(()=>devicePixelRatio);
   }
   if(after/before<1.99||after/before>2.01)throw new CantCheck('This browser does not apply true 200% keyboard page zoom');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const play=page.getByRole('button',{name:'Play GIF',exact:true}).first();
   await play.scrollIntoViewIfNeeded();
   const id=await play.evaluate(node=>node.closest('[data-media-id]').dataset.mediaId),figure=figureFor(page,id);
   await play.focus();await page.keyboard.press('Space');
   await expect(figure).toHaveAttribute('data-playback','playing');
   await page.keyboard.press('Space');
   await expect(figure).toHaveAttribute('data-playback','paused');
   await page.screenshot({path:join(evidenceDir,'keyboard-200-percent.png')});
   return {zoomRatio:after/before,keyboard:true};
   }finally{await page.keyboard.press('Control+0')}
  },{required:false});
 }
}catch(error){
 await record('harness','FAIL',{reason:String(error.message).replaceAll(root,'<source>/')});
}finally{
 await phase('cleanup',async()=>{
  const decoderEvidence=[],errors=[];
  // Inspect/unmount every remaining live realm BEFORE closing it. Closing the
  // browser without observing frame disposal is not cleanup proof.
  for(const context of [...contexts]){
   for(const page of context.pages()){
    if(page.isClosed())continue;
    try{
     if(await page.evaluate(()=>!!window.__mediaProbe)){
      await page.evaluate(()=>{window.__mediaProbe.hold=false;window.__mediaProbe.held.splice(0).forEach(done=>done())});
      await action(page,'session.create',{title:'Final synthetic cleanup'});
      await expect.poll(async()=>{const p=await probeState(page);return p.frames+p.decoders+p.active}).toBe(0);
      decoderEvidence.push(await probeState(page));
     }
    }catch(error){errors.push('Decoder cleanup: '+error.message)}
    finally{
     try{await page.close();assert.equal(page.isClosed(),true);pages.delete(page)}
     catch(error){errors.push('Page close: '+error.message)}
    }
   }
   try{await context.close();contexts.delete(context)}catch(error){errors.push('Context close: '+error.message)}
  }
  if(browser)try{await browser.close();assert.equal(browser.isConnected(),false)}catch(error){errors.push('Browser close: '+error.message)}
  if(fixture&&fixture.exitCode===null){
   fixture.kill('SIGTERM');
   const timer=setTimeout(()=>{forcedKill=true;fixture.kill('SIGKILL')},10000);
   try{await exit}finally{clearTimeout(timer)}
  }
  const processResult=exit?await exit:null;
  const evidence={decoderEvidence,errors,pageErrors,pageClosed:pages.size===0,contextClosed:contexts.size===0,
   browserDisconnected:!browser?.isConnected(),fixture:fixtureRows.filter(row=>row.phase),process:processResult,forcedKill};
  await writeEvidence(join(evidenceDir,'cleanup-observations.json'),evidence);
  assert.equal(forcedKill,false,'Forced kill is a cleanup failure, never success');
  if(fixture){
   assert.equal(processResult.code,0);
   assert.equal(fixtureRows.find(row=>row.phase==='fixture-cleanup')?.closed,true);
   assert.equal(fixtureRows.find(row=>row.phase==='fixture-process')?.temporaryRemoved,true);
  }
  assert.equal(contexts.size,0);assert.equal(pages.size,0);
  assert.deepEqual(errors,[]);
  assert.deepEqual(pageErrors,[]);
  return evidence;
 });
 const failed=receipts.some(row=>row.status==='FAIL'||row.required&&row.status==='CANT_CHECK');
 const remainingPhysical=receipts.filter(row=>physicalNames.includes(row.phase)&&row.status!=='PASS');
 const fullScenario=selectedPhases===null;
 const corePassed=receipts.filter(row=>row.required).every(row=>row.status==='PASS');
 await writeEvidence(join(evidenceDir,'result.json'),{qualified:fullScenario&&!failed&&remainingPhysical.length===0,fullScenario,
  coreChecks:fullScenario?(corePassed?'PASS':'NOT_QUALIFIED'):'NOT_RUN',
  physicalChecks:fullScenario?(remainingPhysical.length?'NOT_QUALIFIED':'PASS'):'NOT_RUN',
  remainingPhysical:remainingPhysical.map(({phase,status,evidence})=>({
   phase,status:'NOT_QUALIFIED',observedStatus:status,reason:evidence.reason})),receipts,
  nativeMemoryGuarantee:false});
 process.exitCode=failed?1:0;
}