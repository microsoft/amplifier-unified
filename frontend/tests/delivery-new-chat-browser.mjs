// Real Vite UI, synthetic HTTP/SSE only. No model, runtime, or provider execution.
// Intentional first sends belong to this fixture, not the passive-recovery counts.
// Run from frontend: node tests/delivery-new-chat-browser.mjs
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/delivery-new-chat';
const outboxKey='amplifier.messageOutbox.v1';
const sourceA={historyScope:'synthetic-host/account-a',hostInstanceId:'fixture-host-a'};
const sourceB={historyScope:'synthetic-host/account-b',hostInstanceId:'fixture-host-b'};
const workspace='/fixture',createdId='created',nativeId='native-created';
const createdScope=JSON.stringify([sourceA.historyScope,workspace,nativeId]);
const newScope=JSON.stringify([sourceA.historyScope,null,null]);
const cases=[],reports=[];let vite,browser;

function initialState(){
 return {
  revision:1,settings:{workspace},runtime:{available:true},
  view:{navPinned:true,workSurface:'chat',setupJourney:'done',newSessionDraft:{workspace}},
  // A previous chat keeps this fixture on the ordinary New chat surface rather
  // than the empty-install onboarding journey; no conversation is selected.
  sessions:[{id:'previous',title:'Previous conversation',sessionKind:'root',workspace,workspaceId:'project',
   status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],
  workspaces:[{id:'project',name:'Fixture',path:workspace,available:true}],
  selectedSessionId:null,selectedWorkspaceId:'project',
  setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],
   providersLoadedAt:1,providersWorkspace:workspace},
  registeredBundles:[{value:'work',label:'Work'}],
  draftDefaults:{[JSON.stringify([workspace,''])]:{
   phase:'ready',bundle:'work',effective:{instance:'fixture',model:'fixture-model'},providers:[{id:'fixture'}],
  }},
  canvas:{open:false},
 };
}

// The shared shell fixture has one fixed host. Adapt only its public envelope
// identity so HTTP shell resources and SSE frames agree when this test switches
// sources; no production hook or action implementation is replaced.
function identify(value,source){
 if(!value||typeof value!=='object')return value;
 return {...value,
  ...(value.hostInstanceId?{hostInstanceId:source.hostInstanceId}:{}),
  ...(value.client?{client:{...value.client,...source}}:{}),
  ...(value.shellResources?{shellResources:{...value.shellResources,hostInstanceId:source.hostInstanceId}}:{}),
 };
}

async function runCase(kind){
 const report={case:kind,passed:false,checkpoints:[],errors:[],externalRequests:[],providerExecution:[],
  intentionalSendCount:0,retryCount:0,duplicateExecution:0,screenshots:[]};
 reports.push(report);
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 const page=await context.newPage();
 let state=initialState(),source=sourceA,creation,send;
 const actions=[],seenSends=new Set(),shell=createShellHttpFixture(()=>state);
 const wire=()=>identify(shell.state(),source);
 const current=()=>page.evaluate(()=>window.amplifier.getState());
 const outbox=()=>page.evaluate(key=>JSON.parse(sessionStorage.getItem(key)||'[]'),outboxKey);
 const count=action=>actions.filter(row=>row.action===action).length;
 const screenshot=async name=>{
  const path=`${artifact}/${kind}-${name}.png`;
  await page.screenshot({path});report.screenshots.push(path);
 };
 const emit=async()=>{
  const revision=++state.revision;
  await page.evaluate(value=>window.emitState(value),wire());
  await expect.poll(async()=>(await current()).revision).toBeGreaterThanOrEqual(revision);
 };
 const reply=async(route,extra={})=>{
  state.revision++;
  await route.fulfill({json:{accepted:true,state:wire(),...extra}});
 };
 const assertNoReplay=()=>{
  assert.equal(count('session.create'),1);
  assert.equal(count('conversation.send'),['unconfirmed-creation','late-creation'].includes(kind)?0:1);
  assert.equal(count('conversation.retry'),0);
  assert.equal(report.duplicateExecution,0);
  assert.deepEqual(report.providerExecution,[]);
  assert.deepEqual(report.externalRequests,[]);
 };
 page.on('pageerror',error=>report.errors.push(error.message));
 await page.addInitScript(installFixtureEvents);
 try{
  const url=vite.resolvedUrls.local[0],origin=new URL(url).origin;
  await page.route('**/*',async route=>{
   const request=route.request(),address=new URL(request.url());
   if(address.origin!==origin){
    report.externalRequests.push(request.url());return route.abort('blockedbyclient');
   }
   if(!address.pathname.startsWith('/api/'))return route.continue();
   const response=shell.response(request);
   if(response!==undefined)return route.fulfill({json:identify(response,source)});
   if(address.pathname==='/api/state')return route.fulfill({json:wire()});
   if(address.pathname==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
   if(address.pathname!=='/api/actions')return route.fulfill({json:{ok:true}});
   const raw=request.postDataJSON();
   const body=raw.action==='shell.command'?{...raw,action:raw.args.action,args:raw.args.args}:
    raw.action==='shell.view.update'?{...raw,action:'view.update'}:raw;
   actions.push(body);
   if(body.action==='session.create'){
    assert.equal(request.method(),'POST');
    assert.equal(creation,undefined,'The first submission must create only one chat');
    creation={route,body};return;
   }
   if(body.action==='conversation.send'){
    assert.equal(request.method(),'POST');
    report.intentionalSendCount++;
    if(seenSends.has(body.id))report.duplicateExecution++;
    seenSends.add(body.id);
    if(send)return route.fulfill({status:409,json:{accepted:false,error:'Duplicate first send refused by fixture.'}});
    send={route,body,frame:structuredClone(wire())};return;
   }
   if(body.action==='conversation.retry'){
    report.retryCount++;
    return route.fulfill({status:409,json:{accepted:false,error:'Fixture forbids all retry execution.'}});
   }
   if(/^(worker\.spawn|call\.start|probe\.spawn|runtime\.start)$/.test(body.action)){
    report.providerExecution.push(body.action);
    return route.fulfill({status:409,json:{accepted:false,error:'Fixture has no provider or runtime.'}});
   }
   if(body.action==='conversation.delivery'){
    return reply(route,{result:{sessionId:body.args.sessionId,inputId:body.args.inputId,
     delivery:'unknown',message:'Synthetic passive read only. Nothing was resent.'}});
   }
   if(body.action==='workspace.list')return reply(route,{result:{items:state.workspaces,nextOffset:null}});
   if(body.action==='view.update')state.view={...state.view,...body.args.patch};
   if(body.action==='session.select')state.selectedSessionId=body.args.id;
   return reply(route);
  });
  await page.goto(url);
  const composer=page.getByRole('textbox',{name:'Message Amplifier'});
  await expect(composer).toBeVisible();
  await expect(page.getByRole('banner')).toContainText('New chat');
  assert.equal((await current()).selectedSessionId,null);
  assert.deepEqual(await outbox(),[]);
  const submitted=`First new-chat request: ${kind}`;
  await composer.fill(submitted);
  const sendButton=page.getByRole('button',{name:'Send message',exact:true});
  await expect(sendButton).toBeEnabled();await sendButton.click();
  await expect.poll(()=>!!creation).toBe(true);
  const beforeCreation=await outbox();
  assert.equal(beforeCreation.length,1);
  const original=beforeCreation[0];
  assert.equal(original.text,submitted);
  assert.equal(original.sessionId,null);
  assert.equal(original.scope,newScope);
  assert.equal(original.creation.id,creation.body.id);
  assert.equal(creation.body.args.workspace,workspace);
  assert.equal(count('conversation.send'),0,'Send waits for a canonical created chat');
  report.checkpoints.push('rendered Send retains initial scoped payload and issues one session.create');

  if(kind==='late-creation'){
   // The initial Send is still waiting on creation, not conversation.send.
   // Supply a complete canonical A chat in the late receipt so a bare-id or
   // fallback lookup cannot accidentally make this test pass by being absent.
   const late=structuredClone(wire());
   late.sessions.unshift({
    id:createdId,title:'STALE SOURCE A CREATED CHAT',runtimeSessionId:nativeId,
    nativeIdentity:'fallback-not-the-owner',workspace,workspaceId:'project',sessionKind:'root',
    status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[],
    creationCommandId:creation.body.id,
   });
   late.selectedSessionId=createdId;delete late.view.newSessionDraft;
   await page.evaluate(()=>{
    window.creationSourceAudit=[];
    window.stopCreationSourceAudit=window.amplifier.subscribe(state=>{
     window.creationSourceAudit.push({
      historyScope:state.client?.historyScope,hostInstanceId:state.client?.hostInstanceId,
      selectedSessionId:state.selectedSessionId,sessionIds:state.sessions.map(session=>session.id),
     });
    });
   });
   // Keep B on its own new-chat draft. No action follows the source change.
   source=sourceB;
   state.sessions[0].title='Source B previous conversation';
   await emit();
   const baseline=await current();
   assert.equal(baseline.client.historyScope,sourceB.historyScope);
   assert.equal(baseline.client.hostInstanceId,sourceB.hostInstanceId);
   assert.equal(baseline.selectedSessionId,null);
   assert.equal(baseline.sessions.some(session=>session.id===createdId),false);
   await expect(page.locator(`[data-input-id="${original.commandId}"]`)).toHaveCount(0);
   assert.deepEqual(await outbox(),beforeCreation,'B baseline must not rebind the pending A creation payload');
   assertNoReplay();
   report.checkpoints.push('source B new-chat baseline adopted while source A session.create is still held');

   late.revision=state.revision+100; // Higher revision cannot legitimize an old source.
   report.heldResponse={action:'session.create',status:200,sessionId:createdId,
    historyScope:late.client.historyScope,hostInstanceId:late.client.hostInstanceId,revision:late.revision};
   await creation.route.fulfill({json:{accepted:true,sessionId:createdId,state:late}});
   // This persisted transition proves the real creation/send chain settled;
   // neither route.fulfill nor an unchanged B DOM proves completion.
   await expect.poll(async()=>(await outbox()).find(entry=>entry.id===original.id)?.status).toBe('unknown');
   const retained=await outbox();
   assert.equal(retained.length,1);
   assert.deepEqual(retained[0],{...original,status:'unknown',error:retained[0].error},
    'Only uncertainty/error may change; original A draft scope, identity and payload stay intact');
   assert.equal(retained[0].scope,newScope);
   assert.equal(retained[0].sessionId,null);
   assert.match(retained[0].error,/target changed.*original submission remains saved/i);
   const selected=await current();
   assert.equal(selected.client.historyScope,sourceB.historyScope);
   assert.equal(selected.client.hostInstanceId,sourceB.hostInstanceId);
   assert.equal(selected.selectedSessionId,null);
   assert.equal(selected.sessions.some(session=>session.id===createdId),false);
   await expect(page.getByRole('banner')).toContainText('New chat');
   await expect(page.getByText(submitted,{exact:true})).toHaveCount(0);
   await expect(page.getByText('STALE SOURCE A CREATED CHAT',{exact:true})).toHaveCount(0);
   for(let i=0;i<2;i++)await emit();
   const adopted=await page.evaluate(()=>window.creationSourceAudit);
   assert.ok(adopted.length>=3,'Record B baseline and subsequent actual state adoptions, not only unchanged DOM');
   assert.ok(adopted.every(frame=>frame.historyScope===sourceB.historyScope&&frame.hostInstanceId===sourceB.hostInstanceId&&
    frame.selectedSessionId===null&&!frame.sessionIds.includes(createdId)),
   'Late creation success must never transiently adopt A or select its created chat');
   report.stateAdoptions={count:adopted.length,frames:adopted};
   await page.evaluate(()=>window.stopCreationSourceAudit());
   assert.deepEqual(await outbox(),retained);
   assert.equal(count('conversation.delivery'),0,'Unbound-to-chat A payload cannot reconcile against B');
   assertNoReplay();
   await screenshot('source-b-creation-fenced');
   report.checkpoints.push('higher-revision complete A creation receipt fenced; A new-chat payload stays unknown; zero conversation.send POSTs');
  }else if(kind==='unconfirmed-creation'){
   // A success-shaped id is not the canonical workspace/native identity record.
   await reply(creation.route,{sessionId:createdId});
   await expect.poll(async()=>(await outbox())[0]?.status).toBe('unknown');
   const retained=await outbox();
   assert.equal(retained.length,1);
   assert.equal(retained[0].scope,newScope);
   assert.equal(retained[0].text,submitted);
   assert.match(retained[0].error,/new conversation is not confirmed.*original submission remains saved/i);
   await expect(page.locator('[data-input-id="'+original.commandId+'"]').getByText(/new conversation is not confirmed/i)).toBeVisible();
   assert.equal((await current()).selectedSessionId,null);
   assertNoReplay();
   await screenshot('honest-no-canonical-chat');
   report.checkpoints.push('missing canonical created record retains payload and reports refusal; zero sends');
  }else{
   const created={
    id:createdId,title:'Source A created conversation',runtimeSessionId:nativeId,nativeIdentity:'fallback-not-the-owner',
    workspace,workspaceId:'project',sessionKind:'root',status:'idle',historyManaged:true,historyLoaded:true,
    messages:[],workers:[],creationCommandId:creation.body.id,
   };
   state.sessions.unshift(created);state.selectedSessionId=createdId;delete state.view.newSessionDraft;
   // The receipt gives only the id as its direct result. The full record lives
   // in canonical state and carries fields missing from the old {id} shortcut.
   await reply(creation.route,{sessionId:createdId});
   await expect.poll(()=>!!send).toBe(true);
   assert.equal(send.body.args.sessionId,createdId);
   assert.equal(send.body.args.text,submitted);
   assert.equal(send.body.id,original.commandId);
   await expect.poll(async()=>(await outbox())[0]?.scope).toBe(createdScope);
   const pending=await outbox();
   assert.equal(pending.length,1);
   assert.equal(pending[0].sessionId,createdId);
   assert.equal(pending[0].scope,createdScope);
   assert.equal(pending[0].status,'sending');
   assert.equal(pending[0].text,submitted);
   await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(1);
   for(let i=0;i<2;i++)await emit();
   assertNoReplay();
   report.checkpoints.push('canonical workspace/native identity binds outbox; exact first conversation.send posts once');

   if(kind==='accepted'){
    created.messages=[{id:'received',inputId:send.body.id,role:'user',text:submitted,delivery:{status:'accepted'}}];
    await reply(send.route,{delivery:'accepted'});
    await expect.poll(async()=>(await outbox()).length).toBe(0);
    await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(1);
    await expect(page.getByText(submitted,{exact:true})).toBeVisible();
    await page.reload();await expect(composer).toBeVisible();
    await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(1);
    assert.deepEqual(await outbox(),[]);
    assertNoReplay();
    await screenshot('accepted-once');
    report.checkpoints.push('first send accepted once; reload keeps one saved message and no replay');
   }else{
    // Install B first with identical public/native chat ids so only source
    // identity prevents an A reply from being accepted into B.
    source=sourceB;
    state.sessions=[{...created,title:'Source B conversation',messages:[
     {id:'b-baseline',role:'assistant',text:'Source B baseline stays selected.',displayOnly:true},
    ]}];
    state.view={...state.view,draft:''};
    await emit();
    await expect(page.getByText('Source B baseline stays selected.',{exact:true})).toBeVisible();
    await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(0);
    assert.deepEqual((await current()).client.historyScope,sourceB.historyScope);
    await page.evaluate(()=>{
     window.deliverySourceAudit=[];
     window.stopDeliverySourceAudit=window.amplifier.subscribe(state=>{
      window.deliverySourceAudit.push({historyScope:state.client?.historyScope,hostInstanceId:state.client?.hostInstanceId});
     });
    });
    const late=structuredClone(send.frame);
    // Make this newer numerically than B. Revision ordering alone cannot pass.
    late.revision=state.revision+100;
    const oldChat=late.sessions.find(row=>row.id===createdId);
    oldChat.messages=[{id:'old-send-receipt',inputId:send.body.id,role:'user',text:submitted,
     delivery:{status:kind==='late-success'?'accepted':'unknown'}}];
    oldChat.title='STALE SOURCE A RECEIPT';
    const response=kind==='late-success'
     ?{accepted:true,delivery:'accepted',state:late}
     :{accepted:false,code:'session_busy',error:'Synthetic old-source session_busy',status:409,state:late};
    report.heldResponse={status:kind==='late-success'?200:409,code:response.code,
     historyScope:late.client.historyScope,hostInstanceId:late.client.hostInstanceId,revision:late.revision};
    await send.route.fulfill({status:kind==='late-success'?200:409,json:response});
    // Persisted status changes only after the real send promise/catch settles.
    // Do not infer completion from route.fulfill or an unchanged B screenshot.
    await expect.poll(async()=>(await outbox()).find(entry=>entry.id===original.id)?.status).toBe(kind==='late-success'?'unknown':'failed');
    const retained=await outbox();
    assert.equal(retained.length,1);
    assert.equal(retained[0].scope,createdScope);
    assert.equal(retained[0].sessionId,createdId);
    assert.equal(retained[0].commandId,send.body.id);
    assert.equal(retained[0].text,submitted);
    assert.deepEqual(retained[0].attachmentIds,original.attachmentIds);
    if(kind==='late-success')assert.match(retained[0].error,/target changed|source changed|original submission remains saved/i);
    // session_busy deliberately suppresses the generic error string: its
    // conversation notice is owned elsewhere. The regression is source/state
    // isolation and retained payload, not a new error presentation contract.
    const selected=await current();
    assert.equal(selected.client.historyScope,sourceB.historyScope);
    assert.equal(selected.client.hostInstanceId,sourceB.hostInstanceId);
    assert.equal(selected.selectedSessionId,createdId);
    assert.equal(selected.sessions.find(row=>row.id===createdId).title,'Source B conversation');
    assert.ok(!selected.sessions.some(row=>row.messages?.some(message=>message.inputId===send.body.id)));
    await expect(page.getByText('Source B baseline stays selected.',{exact:true})).toBeVisible();
    await expect(page.getByText(submitted,{exact:true})).toHaveCount(0);
    await expect(page.getByText('STALE SOURCE A RECEIPT',{exact:true})).toHaveCount(0);
    for(let i=0;i<2;i++)await emit();
    const adopted=await page.evaluate(()=>window.deliverySourceAudit);
    assert.ok(adopted.length>0,'Observe real state adoption after the held response settles');
    assert.ok(adopted.every(client=>client.historyScope===sourceB.historyScope&&client.hostInstanceId===sourceB.hostInstanceId),
     'Neither success nor error may transiently adopt an old source frame');
    await page.evaluate(()=>window.stopDeliverySourceAudit());
    assert.deepEqual(await outbox(),retained);
    assert.equal(count('conversation.delivery'),0,'Old scoped outbox never projects or automatically checks against B');
    assertNoReplay();
    await screenshot('source-b-retained');
    report.checkpoints.push('source B baseline precedes held A reply; higher-revision old frame never adopted; A payload retained without projection or replay');
   }
  }
  assert.deepEqual(report.errors,[]);
  assertNoReplay();
  report.actions=actions.map(({action,id,args})=>({action,id,args}));
  report.passed=true;cases.push(kind);
 }catch(error){
  report.actions=actions.map(({action,id,args})=>({action,id,args}));
  report.failure=String(error.stack||error);
  try{await screenshot('failure')}catch{}
  throw error;
 }finally{
  await context.close();
 }
}

await mkdir(artifact,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,
  ...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{}),
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
 });
 for(const kind of ['accepted','late-success','late-session-busy','unconfirmed-creation','late-creation'])await runCase(kind);
 console.log(`New-chat delivery browser passed: ${cases.join(', ')}. Synthetic transport only; no provider execution. Artifacts: ${artifact}`);
}finally{
 await writeFile(`${artifact}/browser-result.json`,JSON.stringify({
  passed:cases.length===5,
  evidence:'Actual rendered new-chat Send with synthetic HTTP/SSE; fresh context per case. No live runtime/provider qualification.',
  reports,
 },null,2));
 if(browser)await browser.close();
 if(vite)await vite.close();
}