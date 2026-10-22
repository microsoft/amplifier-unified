// Real built client + host action/event/private-draft paths; synthetic runtime.
// No mocked consumed-ID selector, invented state revisions, model or live account.
import './composer-test-helpers.mjs';
import {installAttachmentObserver} from './attachment-consumption-observer.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {bounded,withActionRoute,observed,resultOf,combinedFailure} from './chat-reference-harness.mjs';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks/attachment-consumption');
const fixtureRoot=process.env.AMPLIFIER_TEST_FIXTURE_ROOT||join(root,'tests/fixtures');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDioAAAAASUVORK5CYII=','base64');
const names=['detector-countercheck','accepted-two-cycle','failed-recovery','unknown-reload'];
const report={schemaVersion:1,scenario:'attachment-consumption',runtime:'synthetic',liveProvider:false,
 cases:names.map(id=>({id,status:'unrun'})),errors:[],routeLifecycle:[],traces:[],observations:[]};
await mkdir(output,{recursive:true});
const save=()=>writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
await save();
let browser,base,primary,stderr='',fixtureExit;
const secondaryErrors=[],seenFailures=new Set();
function rememberFailure(error,phase,caseId){
 const failures=error instanceof AggregateError?[...(error.cause?[error.cause]:[]),...error.errors]:[error];
 for(const failure of failures){
  if(failure instanceof AggregateError){rememberFailure(failure,phase,caseId);continue;}
  if(seenFailures.has(failure))continue;seenFailures.add(failure);
  const detail={phase,caseId,name:failure.name,message:failure.message,stack:failure.stack};
  if(!primary){primary=failure;report.primary=detail;}
  else {secondaryErrors.push(failure);(report.secondaryErrors??=[]).push(detail);}
 }
}
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 ['-u',join(fixtureRoot,'attachment_consumption_ui_server.py')],{stdio:['ignore','pipe','pipe']});
const ownedStop=ownFixture(fixture,{label:'Attachment consumption fixture'});
const stop=async()=>{fixtureExit=await ownedStop();report.fixtureExit=fixtureExit;};
fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});

async function readiness(){
 return new Promise((resolveReady,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',fail);};
  const fail=error=>{cleanup();reject(error);};
  const exited=(code,signal)=>fail(Error('Fixture exited before readiness: '+(code??signal)+' '+stderr));
  const data=chunk=>{pending+=chunk;for(let end;(end=pending.indexOf('\n'))>=0;){
   const line=pending.slice(0,end);pending=pending.slice(end+1);let row;try{row=JSON.parse(line)}catch{continue}
   if(row.fixture!=='attachment-consumption')continue;
   if(row.pid!==fixture.pid||!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(row.base))return fail(Error('Wrong fixture identity'));
   cleanup();resolveReady(row);return;
  }if(pending.length>8000)fail(Error('Readiness output exceeded bound'));};
  const timer=setTimeout(()=>fail(Error('Fixture startup deadline: '+stderr)),30000);
  fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',fail);
 });
}
const call=(page,action,args={})=>bounded(page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]),15000,'App action deadline: '+action);
const state=page=>bounded(page.evaluate(()=>window.amplifier.getState()),5000,'State read deadline');
const trace=page=>bounded(page.evaluate(()=>window.attachmentTrace.read()),5000,'Trace read deadline');
async function control(page,args){const response=await page.request.post(base+'/fixture/attachment-consumption',{data:args,timeout:7000});assert.equal(response.status(),200);return response.json();}
async function inspect(page){const current=await state(page);const response=await page.request.get(base+'/fixture/attachment-consumption?clientId='+encodeURIComponent(current.client.id),{timeout:5000});assert.equal(response.status(),200);return response.json();}
async function observation(page,label){const data=await inspect(page);report.observations.push({label,...data});return data;}
async function frames(page,count=3){await bounded(page.evaluate(count=>new Promise(resolveFrames=>{const next=()=>--count<=0?resolveFrames():requestAnimationFrame(next);requestAnimationFrame(next);}),count),5000,'Frame observation deadline');}
async function cleanTrace(page,label){await frames(page);const data=await trace(page);report.traces.push({label,...data});assert.equal(data.capped,false,'Trace must never silently truncate');assert.deepEqual(data.violations,[],label+': consumed attachments reappeared at DOM/render boundary');}
async function upload(page,name){
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name,mimeType:'image/png',buffer:png});
 await expect.poll(async()=>{const s=await state(page);return s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments?.find(x=>x.name===name)?.id;},{timeout:10000}).toMatch(/^[a-f0-9]{32}$/);
 const s=await state(page);return s.sessions.find(x=>x.id===s.selectedSessionId).draftAttachments.find(x=>x.name===name);
}
async function original(page,row){const response=await page.request.get(base+row.url,{timeout:5000});assert.equal(response.status(),200);assert.equal(hash(await response.body()),hash(png),'Original attachment bytes unchanged');}
async function watch(page,owner,ids){await page.evaluate(([owner,ids])=>window.attachmentTrace.watch(owner,ids),[owner,ids]);}
async function waitRuntimeSend(page,owner,previous){
 await expect.poll(async()=>{const data=await inspect(page);return data.sends.filter(x=>x.sessionId===owner).length;},{timeout:10000}).toBe(previous+1);
 return (await inspect(page)).sends.filter(x=>x.sessionId===owner).at(-1).inputId;
}
async function consumed(page,ids){
 await expect.poll(async()=>{const data=await trace(page);return ids.every(id=>data.rules.find(r=>r.id===id)?.armed);},{timeout:10000}).toBe(true);
}
async function settled(page,command){
 await page.waitForFunction(command=>!JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]').some(r=>r.commandId===command),command,{timeout:10000});
}
async function setupPage(){
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.route(url=>url.origin!==base,route=>route.abort('blockedbyclient'));
 await context.addInitScript(installAttachmentObserver);
 await context.addInitScript(()=>{
  const Native=window.WebSocket;
  window.attachmentSockets=[];
  window.WebSocket=class extends Native{constructor(...args){super(...args);window.attachmentSockets.push(this)}};
 });
 const page=await context.newPage();page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(15000);
 page.on('pageerror',error=>{report.errors.push(error.message);rememberFailure(error,'pageerror');});
 try{
  await page.goto(base);
  await page.waitForFunction(()=>!!window.amplifier?.getState()?.client?.id,null,{timeout:15000});
  // Current release starts a fresh view at adaptive Home. Explicit UI actions
  // select test chats rather than assuming an obsolete initial composer.
  const ownerResult=await call(page,'session.create',{title:'Attachment owner',bundle:'anchors'});
  const owner=ownerResult.sessionId||ownerResult.result?.sessionId||(await state(page)).selectedSessionId;
  const otherResult=await call(page,'session.create',{title:'Other attachment draft',bundle:'anchors'});
  const other=otherResult.sessionId||otherResult.result?.sessionId||(await state(page)).selectedSessionId;
  await call(page,'session.select',{id:owner});
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
  return {context,page,owner,other};
 }catch(error){
  const cleanup=[];
  try{await bounded(context.close(),10000,'Startup context close');}catch(closeError){cleanup.push(closeError);}
  throw combinedFailure(error,cleanup);
 }
}

async function detector(page,owner){
 const id='f'.repeat(32);
 await page.locator('.a-composer').evaluate((form,id)=>{const marker=document.createElement('a');marker.href='/api/attachments/'+id;marker.textContent='Synthetic detector control';marker.id='attachment-detector-control';form.append(marker);},id);
 await watch(page,owner,[id]);
 // Initial clear, uncached wrapper insertion, then empty/remove the wrapper in
 // ONE task. Final DOM/live addedNodes alone cannot recover the intermediate ID.
 await page.locator('.a-composer').evaluate((form,id)=>{
  form.querySelector('#attachment-detector-control').remove();
  const wrapper=document.createElement('span'),marker=document.createElement('a');
  marker.href='/api/attachments/'+id;wrapper.append(marker);form.append(wrapper);
  marker.remove();wrapper.remove();
 },id);
 await consumed(page,[id]);
 const synchronous=await trace(page);
 assert.ok(synchronous.violations.some(row=>row.id===id&&row.owner===owner&&row.kind==='added-node'),'Detector must reject initial-clear/same-task emptied-wrapper reappearance');
 const firstClear=synchronous.events.findIndex(row=>row.kind==='optimistic-clear-observed'&&row.id===id);
 const firstReturn=synchronous.events.findIndex(row=>row.kind==='added-node'&&row.id===id);
 assert.ok(firstClear>=0&&firstReturn>firstClear,'Removal arms before later addition in the same batch');
 const foreignId='e'.repeat(32);
 await page.evaluate(([owner,id])=>window.attachmentTrace.watch(owner,[id],true),['different-fixture-owner',foreignId]);
 await page.locator('.a-composer').evaluate((form,id)=>{const marker=document.createElement('a');marker.href='/api/attachments/'+id;form.append(marker);marker.remove();},foreignId);
 assert.equal((await trace(page)).violations.some(row=>row.id===foreignId),false,'Different-chat rule cannot create a violation in this owner');
 const beforeEmpty=(await trace(page)).violations.length;
 await page.locator('.a-composer').evaluate(form=>{const wrapper=document.createElement('span');form.append(wrapper);wrapper.remove();});
 assert.equal((await trace(page)).violations.length,beforeEmpty,'An empty wrapper cannot inherit stale cached IDs');
 await page.locator('.a-composer').evaluate((form,id)=>{const marker=document.createElement('a');marker.href='/api/attachments/'+id;marker.textContent='One-frame detector control';form.append(marker);requestAnimationFrame(()=>requestAnimationFrame(()=>marker.remove()));},id);
 await frames(page,4);
 const data=await trace(page);assert.equal(data.capped,false);assert.ok(data.violations.some(row=>row.id===id&&row.kind==='animation-frame'),'Detector must reject a presented-frame candidate');
 assert.equal(await page.locator('.a-composer a[href$="'+id+'"]').count(),0);
 report.traces.push({label:'deliberate-detector-countercheck-not-product-reproduction',...data});
}

async function twoCycles(page,owner,other){
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const a=await upload(page,'cycle-A.png');await original(page,a);await composer.fill('Submit A');
 await watch(page,owner,[a.id]);
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const first=await waitRuntimeSend(page,owner,0);await consumed(page,[a.id]);
 await composer.fill('Keep next draft B');
 // Hold an exact real attachment-add HTTP reply, after real host admission.
 // The service and state stream remain active; no content/revision is forged.
 let release,seen;const gate=new Promise(r=>{release=r}),arrived=new Promise(r=>{seen=r});
 let held=false;
 await withActionRoute(page,{label:'late-B-upload-response',trace:report.routeLifecycle,release,
  handle:async route=>{
   const body=route.request().method()==='POST'?route.request().postDataJSON():null;
   if(!held&&body?.action==='attachment.add'&&body.args.name==='cycle-B.png'){
    held=true;const response=await route.fetch({timeout:10000});assert.equal(response.status(),200);seen();
    await bounded(gate,15000,'B upload reply release deadline');await route.fulfill({response});
   }else await route.continue();
  }},async()=>{
   await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'cycle-B.png',mimeType:'image/png',buffer:png});
   await bounded(arrived,10000,'Expected real B upload response');
   await call(page,'session.select',{id:other});
   await composer.fill('Other chat text stays here');release();
  });
 await expect.poll(async()=>(await inspect(page)).attachments[owner]?.some(x=>x.name==='cycle-B.png'),{timeout:10000}).toBe(true);
 const otherState=await state(page);assert.equal(otherState.selectedSessionId,other);
 assert.equal(otherState.sessions.find(x=>x.id===other)?.draftAttachments?.length||0,0,'Late upload never retargets other chat');
 await call(page,'session.select',{id:owner});await expect(composer).toHaveDraft('Keep next draft B');
 const b=(await inspect(page)).attachments[owner].find(x=>x.name==='cycle-B.png');await original(page,b);
 await observation(page,'A send RPC held; B attached to original owner');
 await control(page,{op:'accept',inputId:first});await settled(page,first);
 const beforeReturn=await observation(page,'A positive input.delivered before send return');
 assert.equal(beforeReturn.sends.find(x=>x.inputId===first).returned,false,'Controlled RPC return is still held');
 await frames(page);report.traces.push({label:'A accepted before RPC return',...await trace(page)});
 // Release even on a baseline failure so the fixture does not need forced teardown.
 await control(page,{op:'finish',inputId:first});
 await expect.poll(async()=>(await inspect(page)).sends.find(x=>x.inputId===first).returned,{timeout:10000}).toBe(true);
 await expect.poll(async()=>(await inspect(page)).attachments[owner]?.map(x=>x.id),{timeout:10000}).toEqual([b.id]);
 await expect(composer).toHaveDraft('Keep next draft B');await expect(composer).toBeEditable();
 await expect(page.locator('.a-composer img[alt="cycle-B.png"]')).toBeVisible();
 await cleanTrace(page,'A submission through acceptance/outbox retirement/re-enable');
 await original(page,a);await original(page,b);

 await watch(page,owner,[b.id]);await page.getByRole('button',{name:'Send message',exact:true}).click();
 const second=await waitRuntimeSend(page,owner,1);await consumed(page,[b.id]);
 const c=await upload(page,'cycle-C.png');await composer.fill('Keep draft C after B');
 await control(page,{op:'accept',inputId:second});await settled(page,second);
 await frames(page);report.traces.push({label:'B accepted before RPC return',...await trace(page)});
 await control(page,{op:'finish',inputId:second});
 await expect.poll(async()=>(await inspect(page)).attachments[owner]?.map(x=>x.id),{timeout:10000}).toEqual([c.id]);
 await expect(page.locator('.a-composer img[alt="cycle-C.png"]')).toBeVisible();
 await expect(composer).toHaveDraft('Keep draft C after B');
 await cleanTrace(page,'Second B-to-C cycle');
 const saved=await observation(page,'two-cycle originals and owner');
 const messages=saved.sessions.find(x=>x.id===owner).messages.filter(x=>[first,second].includes(x.inputId)&&x.role==='user');
 assert.deepEqual(messages.map(x=>x.attachments.map(f=>f.id)),[[a.id],[b.id]],'Second send must not reuse first batch');
 assert.equal(saved.sends.filter(x=>x.sessionId===owner).length,2,'Exactly two explicit runtime sends');
 for(const row of [a,b,c])await original(page,row);
 await call(page,'session.select',{id:other});await expect(composer).toHaveDraft('Other chat text stays here');
 await call(page,'session.select',{id:owner});await expect(composer).toHaveDraft('Keep draft C after B');
 await cleanTrace(page,'Chat switch preserves consumed and new identities');
 try{
  const connected=page.locator('.a-footer-status');
  await expect(connected).toContainText('Connected');
  const open=await page.evaluate(()=>window.attachmentSockets.filter(socket=>socket.readyState===WebSocket.OPEN).length);
  assert.ok(open>0,'Observe a real open transport before disconnecting');
  await bounded(page.context().setOffline(true),5000,'Enter offline mode');
  // Network emulation does not close an already-open WebSocket. Use the same
  // retained real-socket close path as reconnect-retention, not forged app state.
  await bounded(page.evaluate(()=>window.attachmentSockets.forEach(socket=>socket.close())),5000,'Close owned state transports');
  await expect(connected).toContainText('Reconnecting');
  await expect(page.locator('[data-part="connection-notice"]')).toBeVisible({timeout:15000});
  await expect(composer).toHaveDraft('Keep draft C after B');
  report.transport={observedOpenSockets:open,disconnectObserved:true};
 }finally{
  await bounded(page.context().setOffline(false),5000,'Restore fixture network');
  await bounded(page.evaluate(()=>window.dispatchEvent(new Event('online'))),5000,'Wake existing reconnect handler');
 }
 await expect(page.locator('.a-footer-status')).toContainText('Connected',{timeout:15000});
 await expect(page.locator('[data-part="connection-notice"]')).toHaveCount(0,{timeout:15000});
 report.transport.reconnectObserved=true;
 await expect(page.locator('.a-composer img[alt="cycle-C.png"]')).toBeVisible();
 await cleanTrace(page,'Actual state transport reconnect retains draft C');
 const oldClient=(await state(page)).client.id;
 // New document observer watches accepted IDs from its first tick, rather than
 // taking only a settled screenshot after client reattachment.
 await page.addInitScript(({owner,ids})=>window.addEventListener('DOMContentLoaded',()=>window.attachmentTrace.watch(owner,ids,true),{once:true}),{owner,ids:[a.id,b.id]});
 await page.reload();await expect(composer).toHaveDraft('Keep draft C after B');
 await expect(page.locator('.a-composer img[alt="cycle-C.png"]')).toBeVisible();
 assert.notEqual((await state(page)).client.id,oldClient);
 await expect.poll(async()=>(await inspect(page)).attachments[owner]?.map(x=>x.id),{timeout:10000}).toEqual([c.id]);
 await cleanTrace(page,'Reload/client reconnect does not restore consumed IDs');
 report.transport.reloadObserved=true;
 assert.equal((await inspect(page)).sends.filter(x=>x.sessionId===owner).length,2,'Reattachment never resends');
}

async function recovery(page,owner,mode){
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const row=await upload(page,mode+'.png');await composer.fill('Retain '+mode+' submission');
 await control(page,{op:'next-mode',mode});
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const input=await waitRuntimeSend(page,owner,0);
 const expected=mode==='failed'?'failed':'unknown';
 await page.waitForFunction(([id,status])=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]').some(x=>x.commandId===id&&x.status===status),[input,expected],{timeout:10000});
 await composer.fill('New '+mode+' draft');
 await expect.poll(async()=>(await inspect(page)).drafts[owner],{timeout:10000}).toBe('New '+mode+' draft');
 const before=await observation(page,mode+' recovery before reload');
 assert.ok(before.attachments[owner].some(x=>x.id===row.id),'Unconfirmed/rejected original is not silently consumed');
 await original(page,row);
 if(mode==='unknown'){
  await page.reload();await expect(composer).toHaveDraft('New unknown draft');
  const check=page.getByRole('button',{name:'Check delivery',exact:true});await expect(check).toBeVisible();
  const result=observed(call(page,'conversation.delivery',{sessionId:owner,inputId:input}));
  const delivery=await resultOf(result);
  assert.equal(delivery.result?.delivery,'unknown','Explicit inspection must retain UNKNOWN without positive evidence');
 }else await expect(page.getByRole('button',{name:'Retry',exact:true})).toBeVisible();
 await frames(page);
 const after=await observation(page,mode+' recovery retained');
 await expect(composer).toHaveDraft('New '+mode+' draft');
 assert.equal(after.selectedSessionId,owner,'Recovery remains bound to the original chat');
 assert.equal(after.drafts[owner],'New '+mode+' draft','Final saved newer draft is retained');
 assert.ok(after.attachments[owner]?.some(x=>x.id===row.id),'Final original attachment association remains with its exact owner');
 assert.equal(after.sends.filter(x=>x.sessionId===owner).length,1,'Recovery inspection/reload must not replay');
 const rows=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]'));
 const retained=rows.find(x=>x.commandId===input);assert.ok(retained);
 assert.equal(retained.sessionId,owner);assert.equal(retained.status,expected);
 assert.deepEqual(retained.attachmentIds,[row.id]);assert.equal(retained.text,'Retain '+mode+' submission');
 await original(page,row);
}

try{
 const ready=await readiness();base=ready.base;
 const built=await readFile(join(root,'amplifier_web/static/build.json'));
 assert.equal(ready.buildSha256,hash(built),'Fixture imports the exact candidate web build');
 report.build={sha256:ready.buildSha256,metadata:JSON.parse(built),fixturePackageRoot:ready.packageRoot,
  installedFixture:resolve(fixtureRoot)!==resolve(join(root,'tests/fixtures'))};
 browser=await chromium.launch({headless:true,timeout:20000});
 for(const row of report.cases){
  row.status='running';await save();let context,page;
  try{
   const env=await setupPage();({context,page}=env);
   const delivered=await page.request.get(base+'/build.json',{timeout:5000});assert.equal(delivered.status(),200);assert.equal(hash(await delivered.body()),ready.buildSha256,'HTTP build metadata matches fixture import');
   if(row.id==='detector-countercheck')await detector(page,env.owner);
   else if(row.id==='accepted-two-cycle')await twoCycles(page,env.owner,env.other);
   else await recovery(page,env.owner,row.id==='failed-recovery'?'failed':'unknown');
   row.status='passed';
  }catch(error){row.status='failed';row.error=error.message;rememberFailure(error,'case',row.id);}
  finally{
   if(page&&!page.isClosed()){
    try{report.traces.push({label:row.id+' terminal',...await trace(page)});}
    catch(error){row.status='failed';(row.cleanupErrors??=[]).push(error.message);rememberFailure(error,'terminal-trace',row.id);}
    try{await control(page,{op:'release-all'});}
    catch(error){row.status='failed';(row.cleanupErrors??=[]).push(error.message);rememberFailure(error,'release-all',row.id);}
   }
   if(context)try{await bounded(context.close(),10000,'Case browser-context cleanup');}
   catch(error){row.status='failed';(row.cleanupErrors??=[]).push(error.message);rememberFailure(error,'context-close',row.id);}
   await save();
  }
 }
 assert.deepEqual(report.errors,[],'Browser page errors');
 if(primary)throw primary;
}catch(error){rememberFailure(error,'outer');report.error=primary.message;}
finally{
 // Ask the existing helper to throw its complete cleanup aggregate, so failures
 // are retained here rather than only printed behind an earlier primary.
 try{await finishFixture({label:'Attachment consumption',close:[['Chromium',()=>browser?.close()]],stop});}
 catch(error){report.cleanupError=error.message;rememberFailure(error,'fixture-browser-cleanup');}
 report.fixtureExit=fixtureExit||null;report.complete=report.cases.every(row=>['passed','failed'].includes(row.status));
 report.passed=!primary&&report.complete&&report.cases.every(row=>row.status==='passed')&&fixtureExit?.code===0;
 report.fixtureStderr=stderr;await save();
}
if(!report.passed)throw combinedFailure(primary,secondaryErrors)||Error('Attachment consumption evidence incomplete');
console.log('Attachment consumption: actual host/browser two-cycle journey and recovery passed; synthetic runtime, no native/provider claim.');
