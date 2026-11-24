import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {once} from 'node:events';
import {bounded} from './chat-reference-harness.mjs';
import {chromium,expect} from '@playwright/test';

const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/readiness102-workspace';
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/readiness102_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
const fixtureClosed=once(fixture,'close');
fixtureClosed.catch(()=>{}); // Observed during owned cleanup, including early startup errors.
let browser,browser2,page,peer,target,failure;
const queryEvidence={schema:1,records:[],dropped:0,errors:[],errorsDropped:0,bodyOversize:0,snapshots:[]},reads=[];
const short=value=>typeof value==='string'?value.slice(0,240):value;
const fields=(value,names)=>Object.fromEntries(names.filter(name=>value?.[name]!==undefined).map(name=>[name,short(value[name])]));
const diagnosticError=(stage,error)=>{
 if(queryEvidence.errors.length<16)queryEvidence.errors.push({stage,error:String(error?.message||error).slice(0,1000)});
 else queryEvidence.errorsDropped++;
};
const pendingSummary=value=>Object.fromEntries(Object.entries(value||{}).slice(0,20).map(([name,row])=>
 [name,fields(row,['status','retryAfterMs'])]));
const envelopeSummary=value=>{
 if(!value||typeof value!=='object')throw Error('Shell response is not an object');
 return {...fields(value,['clientId','hostInstanceId','bootstrapKey','shellBootstrapKey','demandId','stateRevision']),
  scope:Array.isArray(value.scope)?value.scope.slice(0,5).map(short):null,
  instances:Object.fromEntries(Object.entries(value.instances||{}).slice(0,16).map(([id,entry])=>[id,{
   ...fields(entry,['generation','stateRevision']),pending:pendingSummary(entry.pending),
   resources:Object.fromEntries(Object.entries(entry.resources||{}).slice(0,20).map(([name,row])=>[name,{
    ...fields(row,['revision']),...(name==='workHome'?{groups:Object.fromEntries(
     Object.entries(row.value?.workHome?.groups||{}).slice(0,8).map(([kind,group])=>[kind,{
      ...fields(group,['total','index']),ids:(group.items||[]).slice(0,50).map(item=>short(item.id))}]))}:{})
   }]))
  }]))};
};
function observeQueries(p,label){
 const requests=new Map();
 p.on('request',request=>{
  try{
   const url=new URL(request.url());let args,command;
   if(url.pathname==='/api/shell')args={clientId:url.searchParams.get('clientId'),demandId:url.searchParams.get('demandId'),
    scope:JSON.parse(url.searchParams.get('scope')||'null'),resources:JSON.parse(url.searchParams.get('navigationResources')||'[]')};
   else if(url.pathname==='/api/shell/resources')args=request.postDataJSON();
   else if(url.pathname==='/api/actions'&&request.method()==='POST'){
    const outer=request.postDataJSON();
    command=outer?.action==='shell.command'?(outer.args?.command||outer.args):outer;
    if(command?.action!=='view.update')return;
    const patch=command.args?.patch||{};
    if(!['workSurface','workWorkspaceId','workWorkspaceTab','workWorkspaceChats'].some(key=>Object.hasOwn(patch,key)))return;
    args={requestId:outer?.id,patch:fields(patch,['workSurface','workWorkspaceId','workWorkspaceTab'])};
   }else return;
   if(queryEvidence.records.length>=128){queryEvidence.dropped++;return}
   const row={index:queryEvidence.records.length,page:label,path:url.pathname,method:request.method(),
    ...fields(args,['clientId','demandId','reason','requestId']),...(args?.patch?{patch:args.patch}:{}),
    scope:Array.isArray(args?.scope)?args.scope.slice(0,5).map(short):null,
    resources:Array.isArray(args?.resources)?args.resources.slice(0,20).map(short):[]};
   queryEvidence.records.push(row);requests.set(request,row);
  }catch(error){diagnosticError(label+':request',error)}
 });
 p.on('response',response=>{
  try{
   const row=requests.get(response.request());if(!row)return;
   row.httpStatus=response.status();
   reads.push(bounded(response.body(),5000,'Shell response observation timed out').then(bytes=>{
    row.bytes=bytes.length;if(bytes.length>512000){queryEvidence.bodyOversize++;row.bodyOmitted=true;return}
    const value=JSON.parse(bytes.toString('utf8'));
    if(row.path==='/api/actions')row.receipt=fields(value,['accepted','revision','stateRevision','hostInstanceId']);
    else row.envelope=envelopeSummary(row.path==='/api/shell'?value.shellResources:value);
   }).catch(error=>{row.observationError=true;diagnosticError(label+':response',error)}));
  }catch(error){diagnosticError(label+':response-callback',error)}
 });
 p.on('requestfailed',request=>{
  try{const row=requests.get(request);if(row)row.failure=fields(request.failure(),['errorText'])}
  catch(error){diagnosticError(label+':request-failed',error)}
 });
}
async function captureQueryFailure(error){
 queryEvidence.primaryError=String(error.message).slice(0,2000);
 for(const [label,p] of [['primary',page],['peer',peer]]){
  if(!p||p.isClosed())continue;
  try{
   const snapshot=await bounded(p.evaluate(()=>{
    const state=window.amplifier?.getState(),shell=window.amplifier?.getShellState();
    return {clientId:state?.client?.id,hostInstanceId:state?.client?.hostInstanceId,revision:state?.revision,
     selectedSessionId:state?.selectedSessionId,selectedWorkspaceId:state?.selectedWorkspaceId,
     view:Object.fromEntries(['workSurface','workWorkspaceId','workWorkspaceTab'].map(k=>[k,state?.view?.[k]])),
     shellRevision:shell?.revision,instances:Object.fromEntries(Object.entries(shell?.shellResources?.instances||{}).slice(0,16)
      .map(([id,entry])=>[id,{generation:entry.generation,stateRevision:entry.stateRevision,pending:entry.pending,
       resources:Object.keys(entry.resources||{}).slice(0,20)}])),
     diagnostics:document.documentElement.getAttribute('data-amplifier-shell-diagnostics')?.slice(0,16000),
     alerts:[...document.querySelectorAll('[role=alert]')].slice(0,12).map(node=>node.textContent.slice(0,500))};
   }),3000,'Query failure state observation timed out');
   queryEvidence.snapshots.push({page:label,...snapshot});
  }catch(capture){diagnosticError(label+':snapshot',capture)}
 }
 if(page&&!page.isClosed()&&target)try{
  // This existing read starts no capture, clears no retry and publishes no state.
  const response=await page.request.get(target+'/api/diagnostics/performance',{timeout:5000});
  queryEvidence.performanceStatus=response.status();
  const body=await bounded(response.body(),3000,'Performance evidence body timed out');
  if(body.length>1000000){queryEvidence.bodyOversize++;queryEvidence.performanceOmitted=true}
  else{
   const value=JSON.parse(body.toString('utf8'));
   queryEvidence.performance={...fields(value,['hostInstanceId','version']),
    shellQueries:value.shellQueries?.slice(-64),shellResourceRebuilds:value.shellResourceRebuilds,
    counters:Object.fromEntries(Object.entries(value.counters||{}).filter(([key])=>key.startsWith('shell.')).slice(0,100))};
  }
 }catch(capture){diagnosticError('performance',capture)}
 await bounded(Promise.all(reads),6000,'Query observers did not settle');
 await writeFile(output+'/query-failure.json',JSON.stringify(queryEvidence,null,2)+'\n');
}
try{
 target=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true,args:process.env.UNIFIED_BROWSER_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const options={viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}};
 browser2=await chromium.launch({headless:true,args:process.env.UNIFIED_BROWSER_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const first=await browser.newContext(options),second=await browser2.newContext(options);
 page=await first.newPage();peer=await second.newPage();const errors=[];
 observeQueries(page,'primary');observeQueries(peer,'peer');
 for(const p of [page,peer])p.on('pageerror',error=>errors.push(error.message));
 // Exercise the existing production bundle served by the fixture, not a
 // separate Vite development transform with different source/style inputs.
 const url=target;
 const dispatch=(p,action,args={})=>p.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const view=(p,patch)=>dispatch(p,'view.update',{patch});
 for(const p of [page,peer]){await p.goto(url);await showFixtureConversation(p,{navPinned:true});await p.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId)}
 const component=JSON.parse(await readFile(new URL('../../amplifier_web/static/client-component.json',import.meta.url),'utf8'));
 const served=await page.request.get(url+'/client-component.json');assert.equal(served.status(),200);
 assert.deepEqual(await served.json(),component,'Actual fixture serves the selected built client');
 const info=await page.evaluate(async()=>await(await fetch('/api/fixture/readiness')).json());
 for(const p of [page,peer]){await dispatch(p,'session.select',{id:info.sessionId});await view(p,{panel:null,workSurface:'chat'})}
 await dispatch(page,'session.markUnread',{id:info.sessionId});
 const unread=p=>p.evaluate(id=>(window.amplifier.getState().attention?.items||[]).find(row=>row.id==='unread:'+id),info.sessionId);
 await expect.poll(async()=>!!(await unread(peer))&&!((await unread(peer)).read)).toBe(true);
 await peer.bringToFront();await peer.waitForTimeout(1200);
 assert.equal((await unread(peer)).read,false,'an already open peer must not acknowledge a new manual mark');
 await dispatch(peer,'session.select',{id:info.sessionId});
 await peer.locator('.a-messages').evaluate(element=>element.scrollTop=element.scrollHeight);
 await expect.poll(async()=>(await unread(peer))?.read,{timeout:10000}).toBe(true);
 await expect.poll(async()=>(await unread(page))?.read).toBe(true);
 await page.bringToFront();
 await view(page,{workSurface:'workspace',workWorkspaceId:info.workspaceId,workWorkspaceTab:'chats'});
 const workspaceCard=page.locator(`.a-workspace-chats article[data-session-id="${info.sessionId}"]`);
 await expect(workspaceCard.getByRole('heading',{name:'Plan demo',exact:true})).toBeVisible();
 await expect(page.getByRole('region',{name:'Workspace chats',exact:true})
  .getByRole('region',{name:'Pick up again',exact:true}).locator(`article[data-session-id="${info.sessionId}"]`)).toBeVisible();
 // Compact idle cards omit their status footer visually; the visible saved-chat
 // group above, not that hidden label, carries the retained-work meaning.
 await expect(workspaceCard.locator('.a-work-status')).toHaveAttribute('data-kind','idle');
 await expect(workspaceCard.locator('.a-work-status')).toHaveText('Saved conversation');
 // Verify the actual reported trajectory, not a historical-plan lookup.
 // Completed checklist items remain agent reports, never durable-task completion.
 assert.equal(info.planReports.length,2);
 for(const [index,report] of info.planReports.entries()){
  const status=index?'completed':'pending',call=`plan-${index}`;
  assert.deepEqual(report.plan,{items:[{content:'Review the demo',status}]});
  assert.equal(report.events.length,2);
  for(const [step,event] of report.events.entries())assert.deepEqual(event,{
   id:`tool:${info.sessionId}:${call}`,revision:step+1,toolCallId:call,
   sessionId:info.sessionId,rootSessionId:info.sessionId,kind:'tool',label:'todo',
   phase:step?'completed':'running'});
 }
 const reportedHistory=info.planReports.map(({plan})=>({
  done:plan.items.filter(item=>item.status==='completed').length,total:plan.items.length}));
 assert.deepEqual(reportedHistory,[{done:0,total:1},{done:1,total:1}]);
 const lastReported=info.planReports.at(-1).plan;
 const canvasWasOpen=await page.evaluate(()=>!!window.amplifier.getState().canvas.open);
 await view(page,{workSurface:'chat'});
 if(!canvasWasOpen)await page.getByRole('button',{name:'Open canvas',exact:true}).click();
 await page.locator('#workspace-canvas .a-chat-overview-trigger[aria-label="Chat overview"]').click();
 const plan=page.getByRole('region',{name:'Chat overview',exact:true}).getByRole('region',{name:'Plan',exact:true});
 await expect(plan.locator('summary')).toContainText('1 of 1 done');
 const disclosure=plan.locator('details');
 if(await disclosure.evaluate(node=>node.open))await plan.locator('summary').click();
 await expect(plan.locator('ol')).toBeHidden();
 await plan.locator('summary').click();
 await expect(plan.locator('li')).toHaveCount(1);
 await expect(plan.locator('li > span')).toHaveText('Review the demo');
 await expect(plan.locator('li > small')).toHaveText('Done');
 await expect(plan.getByText('Last checklist reported by the agent.',{exact:true})).toBeVisible();
 const displayedPlan=await page.evaluate(()=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId)?.execution?.plan);
 assert.deepEqual(displayedPlan,lastReported);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),info.sessionId);
 if(!canvasWasOpen)await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>!!window.amplifier.getState().canvas.open)).toBe(canvasWasOpen);
 await dispatch(page,'workspace.archive',{id:info.workspaceId});
 await view(page,{workSurface:'workspaces'});
 const filter=page.getByRole('combobox',{name:'Workspace archive filter'});
 await filter.selectOption('archived');
 await page.getByRole('searchbox',{name:'Filter workspaces'}).fill('Cleanup demo');
 await expect(page.locator('.a-work-browser .a-workspace-row').filter({hasText:'Cleanup demo'})).toBeVisible();
 await page.reload();await expect(filter).toHaveValue('archived');
 const row=page.locator('.a-work-browser .a-workspace-row').filter({hasText:'Cleanup demo'});
 await row.getByRole('button',{name:/details|options/i}).click();
 await page.getByRole('button',{name:'Restore workspace',exact:true}).click();
 await filter.selectOption('active');
 await expect(row).toBeVisible();
 await view(page,{panel:'settings',settingsSection:'privacy',settingsExpanded:['storage']});
 await expect(page.getByRole('region',{name:'Storage usage'})).toBeVisible();
 const storage=page.getByRole('region',{name:'Storage usage'});
 await expect(storage.locator('.a-storage-categories li')).toHaveCount(6);
 await expect(storage.locator('.a-storage-items')).toHaveCount(0);
 assert.equal(await storage.locator('.a-storage-categories button').first().evaluate(el=>getComputedStyle(el).display),'grid');
 await page.screenshot({path:output+'/storage-overview.png'});
 await page.getByRole('button',{name:/^Backups /}).click();
 await expect(storage.getByText('Newest backup · kept',{exact:true})).toBeVisible();
 await storage.getByRole('button',{name:/^Review cleanup for Private backup/}).click();
 await storage.getByRole('button',{name:'Review backup contents',exact:true}).click();
 await expect(storage.getByText(/The newest backup is kept/)).toBeVisible();
 await storage.getByRole('button',{name:'Permanently delete backup',exact:true}).click();
 await expect(storage.getByRole('status').filter({hasText:/Backup removed/})).toHaveText(/Backup removed/);
 let stored=await page.evaluate(async()=>await(await fetch('/api/fixture/readiness')).json());
 assert.equal(stored.oldBackupExists,false);assert.equal(stored.newBackupExists,true);
 await storage.getByRole('button',{name:'Close',exact:true}).click();
 await storage.getByRole('navigation',{name:'Storage navigation'}).getByRole('button',{name:'Storage',exact:true}).click();
 await page.getByRole('button',{name:/^Session data /}).click();
 await storage.getByRole('button',{name:'Show items I can review for cleanup'}).click();
 await storage.getByRole('button',{name:'Review cleanup for Storage managed chat',exact:true}).click();
 await expect(storage.getByRole('button',{name:'Delete permanently',exact:true})).toBeVisible();
 await storage.getByRole('button',{name:'Keep chat',exact:true}).click();
 await storage.getByRole('navigation',{name:'Storage navigation'}).getByRole('button',{name:'Storage',exact:true}).click();
 await page.getByRole('button',{name:/^Workspace files/}).click();
 await expect(storage.locator('.a-storage-items li')).toHaveCount(5);
 assert.equal(await storage.locator('.a-storage-items li').first().getByRole('button').first().textContent(),'Storage example 6');
 await storage.getByRole('combobox',{name:'Sort storage'}).selectOption('name');
 await expect(storage.locator('.a-storage-items li').first()).toContainText('Cleanup demo');
 await storage.getByRole('combobox',{name:'Sort storage'}).selectOption('size');
 await storage.getByRole('button',{name:/Show more/}).click();
 // The remembered fixture cwd is outside the workspace root and must be excluded.
 await expect(storage.locator('.a-storage-items li')).toHaveCount(8);
 await expect(storage.getByText(/The root itself and folders outside it are not scanned/)).toBeVisible();
 await storage.locator('.a-storage-heading').scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'/storage-category.png'});
 await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('#amp-one').evaluate(el=>el.scrollWidth<=el.clientWidth));
 await storage.locator('.a-storage-heading').scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'/storage-mobile.png'});
 await page.setViewportSize({width:1280,height:900});
 await storage.getByRole('searchbox',{name:'Filter storage'}).fill('Cleanup demo');

 await page.getByRole('button',{name:'Cleanup demo',exact:true}).click();
 await page.getByRole('button',{name:'nested',exact:true}).click();
 await storage.getByRole('navigation',{name:'Storage navigation'}).getByRole('button',{name:'Cleanup demo',exact:true}).click();
 await page.getByRole('button',{name:'Review workspace file cleanup',exact:true}).click();
 await expect(page.getByText('App-managed workspace',{exact:true})).toBeVisible();
 const remove=page.getByRole('button',{name:'Permanently delete workspace files'});
 await expect(remove).toBeDisabled();
 await page.getByRole('textbox',{name:'Confirm workspace folder path'}).fill(info.workspace);
 // The receipt must survive the post-delete progressive scan replacing the
 // workspace detail component, not merely flash before that response arrives.
 const remeasured=page.waitForResponse(async response=>{
  if(response.request().method()!=='POST'||!response.url().includes('/api/actions'))return false;
  const request=response.request().postDataJSON();
  if(request?.action!=='storage.inspect'||!request.args?.rootId)return false;
  try{return (await response.json()).result?.scanning===false}catch{return false}
 });
 await remove.click();
 await remeasured;
 await expect(page.getByRole('status').filter({hasText:'Workspace files deleted'})).toBeVisible();
 assert.equal(await page.evaluate(async()=> (await(await fetch('/api/fixture/readiness')).json()).filesExist),false);
 await view(page,{panel:null,workSurface:'chat'});
 await dispatch(page,'session.select',{id:info.sessionId});
 await expect(page.getByText('This conversation will be retained.',{exact:true})).toBeVisible();
 await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('#amp-one').evaluate(el=>el.scrollWidth<=el.clientWidth));
 assert.deepEqual(errors,[]);
 await writeFile(output+'/observations.json',JSON.stringify({passed:true,clientSource:component.source,build:component.build,
  reportedPlanEvents:info.planReports,reportedHistory,displayedPlan,planSemantics:'Last successful checklist reported through synthetic production event ingress, not historical recovery or durable-task/deliverable completion',
  canvasVisibilityRestored:true,initialCanvasOpen:canvasWasOpen,retainedSessionId:info.sessionId,oldBackupRemoved:true,newBackupKept:true,
  workspaceFilesDeleted:true,chatRetained:true,errors},null,2)+'\n');
 console.log('Readiness102 workspace browser passed: two-device unread acknowledgement, last-reported plan expansion, archive/search/reload/restore, storage drilldown and exact-path cleanup with retained chat, dark/reduced-motion narrow layout.');
}catch(error){
 failure=error;
 try{await captureQueryFailure(error)}catch(capture){failure=new AggregateError([error,capture],'Readiness failure and evidence retention failure',{cause:error})}
 throw failure;
}
finally{
 const cleanup=[];
 for(const [name,close] of [['peer',()=>browser2?.close()],['primary',()=>browser?.close()]])
  try{await bounded(Promise.resolve().then(close),10000,'Readiness102 '+name+' browser cleanup timed out')}catch(error){cleanup.push(error)}
 if(fixture.exitCode===null&&fixture.signalCode===null)fixture.kill('SIGTERM');
 try{
  const [code,signal]=await bounded(fixtureClosed,5000,'Readiness102 fixture cleanup timed out');
  assert.ok(code===0||signal==='SIGTERM','Fixture stopped normally or by its owned SIGTERM');
 }catch(error){
  cleanup.push(error);
  if(fixture.exitCode===null&&fixture.signalCode===null)fixture.kill('SIGKILL');
  try{await bounded(fixtureClosed,2000,'Readiness102 fixture reap timed out')}catch(reap){cleanup.push(reap)}
 }
 try{
  await bounded(Promise.all(reads),6000,'Query observers did not settle after cleanup');
  await writeFile(output+'/query-observations.json',JSON.stringify(queryEvidence,null,2)+'\n');
 }catch(error){cleanup.push(error)}
 if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Readiness102 cleanup incomplete',{cause:failure||cleanup[0]});
}
