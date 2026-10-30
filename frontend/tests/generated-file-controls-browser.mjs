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
import {chromium,expect} from '@playwright/test';
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
const records=[],checks=[],geometry=[],downloads=[],requests=[],commands=[],errors=[];
let logs='',lineBuffer='',browser,page,ready,seed,failure,cleanup,timer;
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
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
 const response=await target.request.post(ready.url+'/fixture/file-controls/'+suffix,{data:await identity(target)});
 assert.equal(response.status(),200,`Fixture ${suffix}: ${await response.text()}`);
 return response.json();
}
async function fixtureRead(target){
 const response=await target.request.get(ready.url+'/fixture/file-controls');
 assert.equal(response.status(),200);
 return response.json();
}
function card(target,row){
 return target.locator(`section[data-artifact-id="${row.id}"][data-artifact-version="${row.version}"]`);
}
function detailsFor(target,row){return card(target,row).locator('details.a-canvas-file-path')}
const button=(details,name)=>details.getByRole('button',{name,exact:true});
const names=['Copy full path','Copy relative path','Download saved file'];

async function openDetails(target,row,mode='click'){
 const details=detailsFor(target,row),summary=details.locator('summary');
 await expect(summary).toHaveText('File details');
 if(!await details.evaluate(node=>node.open)){
  if(mode==='keyboard'){
   await card(target,row).getByRole('button',{name:'Preview in chat',exact:true}).focus();
   await target.keyboard.press('Tab');await expect(summary).toBeFocused();
   await target.keyboard.press('Enter');
  }else if(mode==='touch')await summary.tap();
  else await summary.click();
 }
 await expect(details.getByText(`Saved version ${row.version}`,{exact:true})).toBeVisible();
 return details;
}

// The same existence/path oracle is used unchanged for positive and deliberately
// missing-control cases. It never synthesizes replacement controls.
async function controlsOracle(details,row){
 assert.equal(await details.evaluate(node=>node.tagName),'DETAILS');
 const expected=row.filePaths;
 for(const [name,present] of [
  ['Copy full path',!!expected.absolute],['Copy relative path',!!expected.relative],['Download saved file',true]]){
  assert.equal(await button(details,name).count(),present?1:0,`Required control mismatch: ${name}`);
 }
 const codes=await details.locator('code').allTextContents();
 for(const value of Object.values(expected))assert.ok(codes.includes(value),`Missing displayed path/base: ${value}`);
 if(!expected.absolute)await expect(details).toContainText('Saved in this chat; no backing filesystem path.');
 else if(!expected.relative)await expect(details).toContainText('No relative path:');
 assert.equal(await details.locator('iframe').count(),0,'File details must not mount a preview');
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
 const event=target.waitForEvent('download',{timeout:5000});
 // Attach both rejection handlers immediately, including when a failed gesture
 // leaves the download wait to time out during cleanup.
 const [download]=await Promise.all([event,trigger(button(details,'Download saved file'))]);
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
 await expect(details.locator('.a-saved-file-download [role="status"]')).toHaveText('Download started. Check your browser’s downloads.');
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

async function measure(target,details,label){
 for(const control of [details.locator('summary'),...names.map(name=>button(details,name))]){
  await control.scrollIntoViewIfNeeded();
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
 assert.ok(await details.evaluate(node=>node.scrollWidth<=node.clientWidth),'File-details horizontal overflow');
 await target.screenshot({path:join(evidence,label+'-controls.png')});
 await details.locator('summary').scrollIntoViewIfNeeded();
 await target.screenshot({path:join(evidence,label+'-paths.png')});
}
async function cornerTap(control,corner){
 await control.scrollIntoViewIfNeeded();
 const bounds=await control.boundingBox();assert.ok(bounds);
 await control.tap({position:corner==='top-left'?{x:3,y:3}:{x:bounds.width-3,y:bounds.height-3}});
}

async function connect(options){
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},...options});
 await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:ready.url});
 const target=await context.newPage();target.setDefaultTimeout(4000);target.setDefaultNavigationTimeout(10000);
 target.on('pageerror',error=>errors.push(error.message));
 target.on('request',request=>{
  const pathname=new URL(request.url()).pathname;
  if(pathname.startsWith('/api/canvas/'))requests.push({path:pathname,query:new URL(request.url()).search});
  if(request.method()==='POST'&&pathname==='/api/actions'){
   const payload=request.postDataJSON();commands.push(payload?.action==='shell.command'?(payload.args.command||payload.args):payload);
  }
 });
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

async function run(){
 ready=await startup;
 if(process.env.AMPLIFIER_TEST_INSTALLED==='1'||process.env.AMPLIFIER_TEST_PACKAGE_ROOT)assert.equal(ready.installedVerified,true);
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote','--disable-gpu']:[]});
 page=await connect({});
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
 await page.request.post(ready.url+'/fixture/file-controls/placement',{data:await identity(page)});
 await expect(button(details,'Copy relative path')).toHaveCount(0);
 await expect(details.getByText(html.filePaths.relative,{exact:true})).toHaveCount(0);
 await expect(details.getByText(html.filePaths.absolute,{exact:true})).toBeVisible();
 await page.request.post(ready.url+'/fixture/file-controls/placement',{data:{...await identity(page),restore:true}});
 await expect(button(details,'Copy relative path')).toBeVisible();
 await controlsOracle(details,html);
 // Delay an old metadata response across two placement revisions.
 await details.locator('summary').click();
 let releaseMetadata,metadataAccepted,metadataDelivered;
 const metadataGate=new Promise(resolve=>{releaseMetadata=resolve});
 const acceptedGate=new Promise(resolve=>{metadataAccepted=resolve});
 const deliveredGate=new Promise(resolve=>{metadataDelivered=resolve});
 const delayMetadata=async route=>{
  const data=route.request().postDataJSON();
  if(data?.action!=='canvas.versions.inspect'||data.args?.id!==html.id)return route.continue();
  const response=await route.fetch();metadataAccepted();await metadataGate;await route.fulfill({response});metadataDelivered();
 };
 await page.route('**/api/actions',delayMetadata);
 try{
  await details.locator('summary').click();await acceptedGate;
  await page.unroute('**/api/actions',delayMetadata);
  await page.request.post(ready.url+'/fixture/file-controls/placement',{data:await identity(page)});
  await expect.poll(()=>page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(row=>row.id===s.selectedSessionId)?.workingDirectory})).toBe(seed.workspace);
  await expect(button(details,'Copy relative path')).toHaveCount(0);
  // The app serializes actions; release the old receipt before expecting the
  // replacement metadata request to finish. Assert after both deliveries.
  releaseMetadata();await deliveredGate;
  await expect(details).toContainText('No relative path:');
  await expect(details.getByText(seed.workspace,{exact:true})).toBeVisible();
  await expect(button(details,'Copy relative path')).toHaveCount(0);
 }finally{releaseMetadata();await page.unroute('**/api/actions',delayMetadata)}
 await page.request.post(ready.url+'/fixture/file-controls/placement',{data:{...await identity(page),restore:true}});
 await expect(button(details,'Copy relative path')).toBeVisible();
 checks.push({check:'open details refreshes current working folder and rejects delayed prior-placement metadata'});
 const desktopCommands=commands.slice(controlsStart);
 assert.deepEqual(desktopCommands.filter(row=>['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action)),[],
  'File actions must not select/mount previews or send messages');

 // A real touch-enabled context, not just a resized desktop. Reuse the same
 // retained artifact and positive oracles at both narrow widths.
 const mobile=await connect({viewport:{width:390,height:844},isMobile:true,hasTouch:true,deviceScaleFactor:1});
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
  await touchDetails.locator('summary').tap();
 }
 assert.deepEqual(commands.slice(touchStart).filter(row=>['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action)),[]);
 checks.push({check:'390px/320px emulated touch, actual corner taps, 44px targets, four-corner hit testing and saved PNGs'});
 const final=await fixtureRead(page);
 assert.deepEqual(final.sent,[]);assert.equal(final.sourceDeleted,true);assert.equal(final.sourceExists,false);
 assert.equal(final.newer.revision,2);
 assert.deepEqual(requests.filter(row=>row.path.endsWith('/document')),[],'No HTML document request is allowed for details/download');
 assert.deepEqual(errors,[]);
}

try{
 await Promise.race([run(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Focused file-controls proof exceeded 60 seconds')),60000)})]);
}catch(error){
 failure=error;
 if(page&&!page.isClosed())await page.screenshot({path:join(evidence,'failure.png'),timeout:3000}).catch(()=>{});
}finally{
 clearTimeout(timer);
 try{
  await finishFixture({label:'Generated file controls fixture',close:[['browser',()=>browser?.close()]],stop:async()=>{cleanup=await stop()}});
  assert.equal(records.find(row=>row.cleanup)?.cleanup.homeRemoved,true,'Fixture-owned state/socket directory was not removed');
 }catch(error){failure=failure?new AggregateError([failure,error],'File controls proof and cleanup failed'):error}
 await writeFile(join(evidence,'fixture.log'),logs);
 await writeFile(join(evidence,'observations.json'),JSON.stringify({passed:!failure,error:failure?.stack,
  installed:ready&&{packageRoot:ready.packageRoot,verified:ready.installedVerified,staticSha256:ready.staticSha256},
  seed,checks,geometry,downloads,requests,cleanup,fixtureCleanup:records.find(row=>row.cleanup)?.cleanup,
  limits:['Synthetic runtime and fixture bearer authentication; no provider, login, remote server or physical touch-device claim.',
   'Clipboard/403/wrong-byte faults are deliberate test injections; positive clipboard reads and downloaded files are real browser results.',
   'PNG capture and measured reachability do not claim independent human visual review.']},null,2)+'\n');
 const entries=await readdir(evidence,{withFileTypes:true});
 assert.ok(entries.every(entry=>entry.isFile()),'Export only ordinary evidence files, never fixture sockets/profile/state');
}
if(failure)throw failure;
console.log(`Generated file controls passed; evidence: ${evidence}`);
