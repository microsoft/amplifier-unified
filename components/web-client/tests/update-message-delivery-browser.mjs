import './composer-test-helpers.mjs';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

// Mounted client with controlled HTTP/state ordering. Backend checkpoint/release
// behavior has separate native coverage; this fixture never executes a message
// or installs anything. Retained-update projections below are authored fixture
// inputs, NOT evidence that a real host qualified a receipt, selection or target.
const state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},
 sessions:[{id:'chat',title:'Update delivery',sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
const shell=createShellHttpFixture(()=>state),waiting=[],calls=[],errors=[],cases=[];
const evidence=process.env.AMPLIFIER_TEST_ARTIFACTS;
const args=process.argv.slice(2);
assert.ok(!args.length||args.length===1&&args[0]==='--retained-recovery-only',
 'Usage: node tests/update-message-delivery-browser.mjs [--retained-recovery-only]');
const retainedOnly=args[0]==='--retained-recovery-only';
const mode=retainedOnly?'retained-recovery-only':'full-update-message-delivery';
const historicalDelivery={status:retainedOnly?'not-covered':'pending',
 reason:retainedOnly?'Explicit focused selection: historical message/lost-ack delivery is not covered. Its earlier failed run remains failed.':null};
const routeErrors=[],evidenceErrors=[];
let phase='setup',rejectRouteFailure;
const routeFailure=new Promise((_resolve,reject)=>{rejectRouteFailure=reject});
const recovery={scope:'synthetic client HTTP/state fixture; host eligibility and actual installation not covered',
 checkpoints:[],screenshots:[],passed:false};
let installAttempts=0,passed=false,failure=null;
let browser,vite,page;
const chat=()=>state.sessions[0];
const outbox=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]'));
const bubble=id=>page.locator(`[data-input-id="${id}"]`);
const composer=()=>page.getByRole('textbox',{name:'Message Amplifier'});
async function publish(){state.revision++;await page.evaluate(value=>window.emitState(value),shell.state())}
async function persistReceipt(){
 if(!evidence)return;
 await mkdir(evidence,{recursive:true});
 await writeFile(evidence+'/receipt.json',JSON.stringify({mode,phase,passed,failure,historicalDelivery,cases,
  sendPosts:calls.filter(call=>call.action==='conversation.send').length,
  distinctSavedInputs:new Set(chat().messages.map(message=>message.inputId)).size,
  pageErrors:errors,routeErrors,evidenceErrors,retainedRecovery:recovery,
  actions:calls,updateActions:updateCalls(),syntheticInstallAttempts:installAttempts,
  backendExecution:'synthetic HTTP/state fixture; no actual updater, model, install or restart'},null,2)+'\n');
}
async function capture(name,target=page){
 if(!evidence)return;
 await mkdir(evidence,{recursive:true});
 await target.screenshot({path:evidence+'/'+name+'.png',animations:'disabled'});
 recovery.screenshots.push(name+'.png');
}
const updateCalls=()=>calls.filter(call=>['updates.check','updates.install'].includes(call.action));
async function retainedRecovery(){
 const application={id:'application',kind:'app',label:'Amplifier Unified',status:'current',
  current:'0.0.0',latest:'0.0.0',releaseNotes:[]};
 const retained={phase:'checked',available:0,lastAttempt:100,application,
  items:[{id:'retained',label:'Retained fixture component',kind:'bundle / module',usage:'configured',updateTier:'other',
   status:'check_failed',sourceUpdate:'failed',current:'a'.repeat(40),
   detail:'The source download failed. Its working version was retained.'}],
  componentResults:[{id:'retained',status:'failed',revision:'a'.repeat(40)}],
  sequence:{stage:'complete',included:{status:'current',available:0},other:{status:'failed',available:0,issues:1}},
  currentStatus:{status:'action-required',actionRequired:true,action:'sources',
   title:'Some components kept their working versions',
   detail:'Review the failed source. Its working version is kept.'}};
 const eligible={...retained,currentStatus:{...retained.currentStatus,action:'install',
  detail:'The working version is kept. Choose Retry installation to try the retained component again.'}};
 const updates=page.locator('[data-part="updates"]');
 const controls=updates.locator('[data-part="update-controls"]');
 const retry=controls.getByRole('button',{name:'Retry installation',exact:true});
 const check=controls.getByRole('button',{name:'Check for updates',exact:true});
 const install=updates.locator('button[data-action="updates.install"]');
 async function project(value){
  phase='retained: project '+(value.currentStatus?.detail||'missing current status');
  state.updates=structuredClone(value);await publish();
  // Fence each negative assertion to this exact injected frame, not an older
  // DOM that also happens to have no install button.
  await page.waitForFunction(({revision,updates})=>{
   const observed=window.amplifier.getState();
   return observed.revision>=revision&&JSON.stringify(observed.updates)===JSON.stringify(updates);
  },{revision:state.revision,updates:state.updates});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 }
 async function unchanged(label,expectedCalls){
  phase='retained: '+label;
  // Bounded idle observation supplements the maintained unit fake-timer checks.
  await page.waitForTimeout(250);
  assert.deepEqual(updateCalls().map(call=>call.action),expectedCalls,label);
  assert.equal(installAttempts,0,label);
  const observed=await page.evaluate(()=>window.amplifier.getState().updates);
  assert.equal(observed.lastAttempt,100,label);assert.equal(observed.available,0,label);
  assert.equal(observed.items[0].status,'check_failed',label);
  assert.equal(observed.items[0].sourceUpdate,'failed',label);
  recovery.checkpoints.push({case:label,actions:[...expectedCalls],syntheticInstallAttempts:0,lastAttempt:100,available:0});
 }
 await page.setViewportSize({width:1280,height:900});
 await project(retained);
 // Reuse maintained Settings navigation and real clicks; no dispatch fallback.
 await openSettingsPage(page,'updates',{activate:(_label,control,options)=>control.click(options)});
 await expect(updates).toBeVisible();await expect(check).toBeEnabled();
 await expect(install).toHaveCount(0);
 await unchanged('retained-only mount does not install',[]);
 await check.click();
 await expect.poll(()=>updateCalls().map(call=>call.action)).toEqual(['updates.check']);
 await expect(check).toBeEnabled();
 await unchanged('Check is read-only with respect to installation',['updates.check']);
 await capture('retained-check-only',controls);

 // The synthetic peer explicitly publishes the proposed eligible host action.
 // There is deliberately no eligibility algorithm in this fixture or client.
 await project(eligible);
 await expect(install).toHaveCount(1);await expect(retry).toBeVisible();await expect(retry).toBeEnabled();
 await expect(updates.locator('.a-update-current-action button')).toHaveCount(0);
 await expect(updates.locator('.a-update-current-action')).toContainText('Checking for updates does not install them.');
 await expect(updates.locator('[data-part="component-update-results"]')).toContainText('Failed — working version retained');
 await expect(updates.locator('[data-part="available-updates"] .a-update-count')).toHaveCount(0);
 await unchanged('eligible projection alone does not install',['updates.check']);
 await capture('retained-retry-desktop',controls);
 await capture('retained-retry-explanation',updates.locator('.a-update-current-action'));
 // A reload with the same projection is also not an install gesture.
 await page.reload();await expect(retry).toBeVisible();await expect(retry).toBeEnabled();
 await unchanged('reload does not consume the explicit retry',['updates.check']);
 await page.setViewportSize({width:390,height:844});
 await expect(retry).toBeVisible();await expect(retry).toBeEnabled();
 await capture('retained-retry-mobile',controls);
 await check.click();
 await expect.poll(()=>updateCalls().map(call=>call.action)).toEqual(['updates.check','updates.check']);
 await expect(check).toBeEnabled();
 await unchanged('Check with Retry visible still does not install',['updates.check','updates.check']);

 for(const [label,action] of [
  ['unknown outcome','diagnostics'],['stale receipt','sources'],['missing target','sources'],
  ['check required','check'],['null action',null],['missing action',undefined],['unsupported action','retry'],
 ]){
  // These are the host's NON-install projections for guarded negative cases,
  // not client judgments about a stale receipt or a missing/private target.
  await project({...retained,currentStatus:{...retained.currentStatus,action,detail:label}});
  await expect(install).toHaveCount(0);
  await unchanged(label+' revokes Retry',['updates.check','updates.check']);
  if(label==='missing target')await capture('retained-missing-target',controls);
  await project(eligible);await expect(retry).toBeEnabled();
 }
 for(const currentStatus of [null,undefined]){
  await project({...retained,currentStatus});await expect(install).toHaveCount(0);
  await unchanged(currentStatus===null?'null projection':'missing projection',['updates.check','updates.check']);
 }
 const guards=[
  ['checking',{phase:'checking'}],['staging',{phase:'staging'}],['validating',{phase:'validating'}],['activating',{phase:'activating'}],
  ['pending application',{pendingApp:{latest:'0.0.1'}}],['pending components',{pendingRelease:'candidate'}],
  ['pending tool',{pendingSmartTools:[{previous:'fixture-tool'}]}],
  ['replacement outcome unknown',{pendingReplacement:{}}],
  ['restart uncertain',{pendingRestart:{requestStatus:'uncertain'}}],
  ['restart rejected',{pendingRestart:{requestStatus:'rejected'},phase:'error',error:'Synthetic restart failed'}],
  ['next stage queued',{sequence:{stage:'other',nextStage:'other'}}],
  ['included proceed guard',{sequence:{stage:'included',included:{issues:1}},includedCanProceed:false}],
  ['development app guard',{application:{...application,status:'update',canInstall:false}}],
 ];
 for(const [label,patch] of guards){
  await project({...eligible,...patch});
  await expect(updates.locator('button[data-action="updates.install"]:enabled')).toHaveCount(0);
  await unchanged(label+' guards explicit retry',['updates.check','updates.check']);
  if(label==='replacement outcome unknown')await capture('retained-replacement-guard',controls);
 }
 await project(eligible);await expect(retry).toBeEnabled();
 phase='retained: explicit Retry installation';
 await retry.click();
 await expect.poll(()=>updateCalls().map(call=>call.action)).toEqual(['updates.check','updates.check','updates.install']);
 await expect.poll(()=>installAttempts).toBe(1);
 // During staging the existing sequence overview owns the visible title,
 // rather than the idle currentStatus title supplied by this synthetic peer.
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.phase)).toBe('staging');
 await expect(updates.locator('[data-part="application-update"]')).toContainText('Updating other components');
 await expect(updates.locator('button[data-action="updates.install"]:enabled')).toHaveCount(0);
 await capture('retained-explicit-attempt',controls);
 await page.reload();
 await expect(updates.locator('[data-part="application-update"]')).toContainText('Updating other components');
 await page.waitForTimeout(250);
 assert.deepEqual(updateCalls().map(call=>call.action),['updates.check','updates.check','updates.install']);
 assert.equal(installAttempts,1,'Rerender and reload must not replay explicit installation');
 assert.equal(state.updates.available,0);
 recovery.checkpoints.push({case:'one explicit Retry produces one synthetic install; reload does not replay',
  actions:updateCalls().map(call=>call.action),syntheticInstallAttempts:installAttempts,available:0});
 recovery.passed=true;
}
async function send(text){
 phase='historical: '+text;
 await composer().fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(()=>waiting.length).toBe(1);
 const item=waiting.shift();assert.equal(item.body.args.text,text);
 await expect(bubble(item.body.id)).toHaveCount(1);
 return item;
}
function retain(item){
 const message={id:'saved-'+item.body.id,inputId:item.body.id,role:'user',text:item.body.args.text,
  createdAt:Date.now()/1000,delivery:{status:'queued_for_update'}};
 chat().messages.push(message);return message;
}
async function saved(item,label){
 const row=bubble(item.body.id);
 await expect(row).toHaveCount(1);
 await expect(row.locator('.a-message-delivery[role="status"]')).toHaveText('Saved · waiting for the app update. You do not need to send it again.');
 await expect(row.getByRole('button',{name:/Retry|Check delivery|Send again|Discard unsent/})).toHaveCount(0);
 assert.doesNotMatch(await row.innerText(),/Not sent|Delivery not confirmed/);
 await expect.poll(outbox).toEqual([]);
 assert.equal(calls.filter(call=>call.id===item.body.id).length,1,'The original submission is not replayed');
 cases.push({case:label,inputId:item.body.id,renderedCopies:1,posts:1,outboxEntries:0});
}
async function runJourney(){
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  cacheDir:fileURLToPath(new URL('../.vite-update-message-fixture',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(installFixtureEvents);
 await page.route('**/api/**',async route=>{
  const request=route.request();
  let body;
  try{
  const path=new URL(request.url()).pathname,response=shell.response(request);
  if(response!==undefined)return await route.fulfill({json:response});
  if(path==='/api/state')return await route.fulfill({json:shell.state()});
  if(path==='/api/actions'&&request.method()==='GET')return await route.fulfill({json:[]});
  if(path!=='/api/actions')return await route.fulfill({json:{ok:true}});
  body=request.postDataJSON();calls.push(body);
  if(retainedOnly)assert.ok(!body.action.startsWith('conversation.'),
   'Retained-recovery-only must not send, retry or inspect message delivery: '+JSON.stringify(body));
  if(body.action==='conversation.send'){waiting.push({route,body});return}
  assert.ok(!['conversation.retry','conversation.delivery'].includes(body.action),
   'Saved update input must not request retry or delivery recovery: '+JSON.stringify(body));
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  if(body.action.startsWith('updates.'))assert.ok(['updates.check','updates.install'].includes(body.action),
   'The recovery fixture must not request a restart, rollback or other updater action');
  if(body.action==='updates.install'){
   // Record only a synthetic attempt; never invoke an updater, host or model.
   installAttempts++;
   state.updates={...state.updates,phase:'staging',lastAttempt:101,
    currentStatus:{status:'working',actionRequired:false,action:null,title:'Updating retained fixture components',
     detail:'A synthetic installation request was recorded. No actual installation runs.'}};
  }
  state.revision++;return await route.fulfill({json:{accepted:true,state:shell.state()}});
  }catch(error){
   // Playwright route callbacks run outside the awaited journey. Never throw
   // here: retain the exact request first, then fail the main awaited owner so
   // screenshots, final receipt and cleanup still execute.
   routeErrors.push({phase,method:request.method(),url:request.url(),
    postData:request.postData(),body,error:String(error.stack||error),
    revision:state.revision,selectedSessionId:state.selectedSessionId,
    messageDeliveries:chat().messages.map(({inputId,delivery})=>({inputId,delivery}))});
   failure=String(error.stack||error);passed=false;
   if(historicalDelivery.status==='running')historicalDelivery.status='failed';
   console.error(JSON.stringify({fixtureRouteFailure:routeErrors.at(-1)}));
   try{await persistReceipt()}catch(writeError){evidenceErrors.push('route receipt: '+writeError.message)}
   try{await route.abort('failed')}catch(abortError){evidenceErrors.push('route abort: '+abortError.message)}
   rejectRouteFailure(error);
  }
 });
 await page.goto(vite.resolvedUrls.local[0]);await composer().waitFor();

 if(!retainedOnly){
 historicalDelivery.status='running';
 const acknowledged=await send('Keep this message through the update.');retain(acknowledged);state.revision++;
 await acknowledged.route.fulfill({json:{accepted:true,delivery:'queued_for_update',state:shell.state()}});
 await saved(acknowledged,'known queued acknowledgment');

 const stateFirst=await send('The saved state arrives before the HTTP failure.');retain(stateFirst);await publish();
 await saved(stateFirst,'canonical receipt precedes lost acknowledgment');
 await stateFirst.route.abort('failed');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions[0].messages.length)).toBe(2);
 await saved(stateFirst,'late transport failure does not recreate outbox');

 const ackFirst=await send('The HTTP failure arrives before the saved state.');await ackFirst.route.abort('failed');
 await expect(bubble(ackFirst.body.id).getByText('Delivery not confirmed',{exact:false})).toBeVisible();
 assert.equal((await outbox())[0].commandId,ackFirst.body.id);
 retain(ackFirst);await publish();await saved(ackFirst,'canonical receipt resolves uncertain outbox');

 const cold=await send('Reload with the original saved input identity.');await cold.route.abort('failed');
 await expect(bubble(cold.body.id).getByText('Delivery not confirmed',{exact:false})).toBeVisible();
 assert.equal((await outbox())[0].commandId,cold.body.id);
 // The server saved the input while the client was disconnected. Bootstrap on
 // reload must reconcile its retained local entry without posting it again.
 retain(cold);state.revision++;
 await composer().fill('Keep this newer unsent draft.');
 await page.reload();await composer().waitFor();
 await saved(cold,'refresh reconciles original identity');
 await expect(composer()).toHaveDraft('Keep this newer unsent draft.');
 for(const item of [acknowledged,stateFirst,ackFirst,cold])await expect(bubble(item.body.id)).toHaveCount(1);

 const stoppedWhileDisconnected=await send('Stop retains this input before its acknowledgment arrives.');
 await stoppedWhileDisconnected.route.abort('failed');
 await expect(bubble(stoppedWhileDisconnected.body.id).getByText('Delivery not confirmed',{exact:false})).toBeVisible();
 const disconnectedMessage=retain(stoppedWhileDisconnected);
 chat().status='stopped';
 const held=chat().messages.find(message=>message.inputId===acknowledged.body.id);
 held.delivery={status:'held',reason:'Stopped after this message was saved. Nothing was sent.'};
 disconnectedMessage.delivery={...held.delivery};state.revision++;
 // This client never saw queued_for_update. The canonical held receipt still
 // acknowledges saving, while explicitly declining delivery after Stop.
 await page.reload();await composer().waitFor();
 await page.setViewportSize({width:390,height:844});
 for(const item of [acknowledged,stoppedWhileDisconnected]){
  await expect(bubble(item.body.id)).toHaveCount(1);
  await expect(bubble(item.body.id).locator('.a-message-delivery[role="status"]')).toHaveText(held.delivery.reason);
  await expect(bubble(item.body.id).getByRole('button',{name:/Retry|Send again|Check delivery/})).toHaveCount(0);
  assert.doesNotMatch(await bubble(item.body.id).innerText(),/Added to the active run|Received by the active run|waiting for the app update/);
 }
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Phone status remains in the viewport');
 await expect.poll(outbox).toEqual([]);
 cases.push({case:'Stop-held remains saved without claiming delivery',inputId:acknowledged.body.id,status:'held'});
 cases.push({case:'lost acknowledgment then Stop reconciles retained outbox on refresh',inputId:stoppedWhileDisconnected.body.id,status:'held'});
 assert.equal(calls.filter(call=>call.action==='conversation.send').length,5);
 assert.equal(new Set(chat().messages.map(message=>message.inputId)).size,5);
 await capture('held-mobile');
 historicalDelivery.status='passed';
 }
 phase='retained recovery';
 await retainedRecovery();
 assert.equal(calls.filter(call=>call.action==='conversation.send').length,retainedOnly?0:5);
 assert.deepEqual(routeErrors,[]);assert.deepEqual(errors,[]);passed=true;phase='complete';
 console.log(retainedOnly?
  'PASS (retained-recovery-only synthetic client fixture): explicit Retry, Check-only, revoked projections and guards; no automatic install or replay. Historical message/lost-ack delivery NOT COVERED; earlier failure remains failed. Actual host eligibility/installation not covered.':
  'PASS (full synthetic client fixture): update-time saved delivery and Stop-held mobile status; retained-only Check never installs, explicit Retry records one installation request, non-install projections and guards prevent retry, no replay. Actual host eligibility/installation not covered.');
}
try{
 await Promise.race([runJourney(),routeFailure]);
}catch(error){
 failure=String(error.stack||error);passed=false;
 if(historicalDelivery.status==='running')historicalDelivery.status='failed';
 if(page)await capture('failure').catch(error=>recovery.screenshots.push('failure capture unavailable: '+error.message));
 throw error;
}finally{
 try{
  await persistReceipt();
 }finally{try{if(browser)await browser.close()}finally{if(vite)await vite.close()}}
}
