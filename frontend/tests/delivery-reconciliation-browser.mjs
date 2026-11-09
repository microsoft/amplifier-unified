// Real Vite UI with synthetic HTTP/SSE; no live server, provider, or runtime.
// Run from frontend: node tests/delivery-reconciliation-browser.mjs
import {createShellHttpFixture} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/delivery-reconciliation';
await mkdir(artifact,{recursive:true});
const outboxKey='amplifier.messageOutbox.v1',originalText='Keep this historical request and its reference.';
const message=(inputId='original',delivery={status:'unknown',error:'Transport interrupted. Delivery is uncertain.'})=>({
 id:'saved-'+inputId,inputId,role:'user',text:inputId==='original'?originalText:`Synthetic saved request: ${inputId}`,
 attachments:[{id:'a',name:'reference.txt',size:12}],delivery,
});
const session=id=>({
 id,title:id,sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',
 historyManaged:true,historyLoaded:true,messages:[message()],workers:[],
});
let state={
 revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},
 sessions:[session('chat'),session('other')],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],
 selectedSessionId:'chat',selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},
 canvas:{open:false},
};
let historyScope='synthetic-host/account-one';
const shell=createShellHttpFixture(()=>state);
const wireState=()=>{const value=shell.state();return {...value,client:{...value.client,historyScope}}};
const seed=[
 {id:'local-original',commandId:'original',sessionId:'chat',text:originalText,attachmentIds:['a'],attachments:[{id:'a',name:'reference.txt',size:12}],status:'unknown',error:'Transport interrupted. Delivery is uncertain.'},
 {id:'other-unsent',commandId:'unrelated-input',sessionId:'other',text:'Keep this separate failed input.',attachmentIds:[],status:'failed',error:'Known failure; not eligible for automatic reconciliation.'},
];
const calls=[],pending=[],errors=[],screenshots=[],completed=[];
const executions=new Map([['chat/original',1]]);
let senderCounter=0,duplicateExecution=0,vite,browser,page;
const recoveryCalls=()=>calls.filter(call=>call.action==='conversation.delivery');
const effects=()=>calls.filter(call=>['conversation.send','conversation.retry','message.edit','session.delete'].includes(call.action));

// Extend the small shell fixture's EventSource sequencing locally. Exercise the
// production stream onopen -> amplifier-reconnected path; never stub React hooks
// or invoke the production dispatch API from the fixture.
function installSequencedTransport({seed,outboxKey}){
 const sources=new Set();let online=false;
 Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>online});
 window.EventSource=class extends EventTarget{
  constructor(){super();this.readyState=0;sources.add(this);queueMicrotask(async()=>{
   const state=await fetch('/api/state').then(response=>response.json());
   if(this.readyState===2)return;
   this.readyState=1;this.onopen?.();
   this.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}));
  });}
  close(){this.readyState=2;sources.delete(this);}
 };
 window.emitState=state=>sources.forEach(source=>{
  if(source.readyState!==2)source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}));
 });
 window.fixtureTransport={
  disconnect(){online=false;for(const source of sources){source.readyState=0;source.onerror?.(new Event('error'));}},
  reconnect(state){
   online=true;
   for(const source of sources){
    source.readyState=1;source.onopen?.();
    source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}));
   }
  },
 };
 // This context is created by this test. Its only stored messages are synthetic.
 sessionStorage.setItem(outboxKey,JSON.stringify(seed));
}

const row=inputId=>page.locator(`[data-input-id="${inputId}"]`);
const check=inputId=>row(inputId).getByRole('button',{name:'Check delivery',exact:true});
const checking=inputId=>row(inputId).getByRole('button',{name:'Checking…',exact:true});
const warning=inputId=>row(inputId).getByText('Delivery not confirmed',{exact:true});
const outbox=()=>page.evaluate(key=>JSON.parse(sessionStorage.getItem(key)||'[]'),outboxKey);
async function emit(){
 const revision=++state.revision;
 await page.evaluate(value=>window.emitState(value),wireState());
 await expect.poll(()=>page.evaluate(()=>window.amplifier?.getState().revision)).toBeGreaterThanOrEqual(revision);
}
async function readFor(sessionId,inputId,scope=historyScope){
 await expect.poll(()=>pending.filter(request=>request.body.args.sessionId===sessionId&&request.body.args.inputId===inputId&&request.scope===scope).length).toBe(1);
 return pending.find(request=>request.body.args.sessionId===sessionId&&request.body.args.inputId===inputId&&request.scope===scope);
}
async function answer(request,result){
 assert.ok(request&&!request.settled,'Each deferred HTTP request is completed exactly once');
 request.settled=true;
 pending.splice(pending.indexOf(request),1);
 // Keep the server message unknown. Receipt reconciliation, not a conveniently
 // changed snapshot, must remove the warning and exact outbox entry.
 await request.route.fulfill({json:{...result,state:wireState()}});
}
const receipt=(request,delivery='accepted',extra={})=>({
 accepted:true,result:{
  sessionId:request.body.args.sessionId,inputId:request.body.args.inputId,delivery,
  message:delivery==='accepted'?'Received. Nothing was resent.':'Still uncertain. Nothing was resent.',...extra,
 },
});
async function screenshot(name){
 const path=`${artifact}/${name}.png`;
 await page.screenshot({path});screenshots.push(path);
}
function assertPassive(){
 assert.equal(senderCounter,0,'No conversation.send or conversation.retry may run');
 assert.equal(duplicateExecution,0,'No synthetic accepted input may execute again');
 assert.deepEqual(effects(),[]);
 for(const call of recoveryCalls())assert.deepEqual(Object.keys(call.args).sort(),['inputId','sessionId'],'No recovery scope or payload is promoted into action authority');
}
async function saveResult(passed,error){
 await writeFile(`${artifact}/browser-result.json`,JSON.stringify({
  passed,evidence:'Real bundled Vite UI; synthetic HTTP/SSE and synthetic execution counters only.',
  viewports:[{width:1280,height:900},{width:390,height:844}],completed,
  senderCounter,duplicateExecution,calls:recoveryCalls(),effects:effects(),errors,screenshots,
  ...(error?{failure:String(error.stack||error)}:{}),
 },null,2));
}

try{
 vite=await createServer({
  configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},
  optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']},
 });await vite.listen();
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(installSequencedTransport,{seed,outboxKey});
 await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname,response=shell.response(request);
  if(response!==undefined)return route.fulfill({json:response});
  if(path==='/api/state')return route.fulfill({json:wireState()});
  if(path==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=request.postDataJSON();calls.push(body);
  if(body.action==='conversation.delivery'){
   pending.push({route,body,scope:historyScope,settled:false});return;
  }
  if(['conversation.send','conversation.retry'].includes(body.action)){
   senderCounter++;
   const identity=`${body.args.sessionId}/${body.args.inputId||body.id}`;
   if(executions.has(identity))duplicateExecution++;
   executions.set(identity,(executions.get(identity)||0)+1);
   return route.fulfill({json:{accepted:false,error:'This passive fixture refuses all send and retry effects.',state:wireState()}});
  }
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  if(body.action==='session.select')state.selectedSessionId=body.args.id;
  state.revision++;
  return route.fulfill({json:{accepted:true,state:wireState()}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 await expect(warning('original')).toBeVisible();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Keep the next unsent thought');
 const retained=await outbox();
 assert.deepEqual(retained,seed);
 assert.equal(recoveryCalls().length,0,'Offline mount must not request delivery');
 for(let i=0;i<3;i++){state.sessions[0].title=`Offline state revision ${i}`;await emit();}
 assert.equal(recoveryCalls().length,0);
 completed.push('offline mount and unrelated frames: no delivery requests');

 // Lost ACK recovery: no Check click or retry, and exactly one read after SSE open.
 await page.evaluate(value=>window.fixtureTransport.reconnect(value),wireState());
 const first=await readFor('chat','original');
 await expect(checking('original')).toBeDisabled();
 await expect(row('original').getByRole('button',{name:'Dismiss warning',exact:true})).toBeDisabled();
 for(let i=0;i<3;i++){state.sessions[0].title=`Pending read revision ${i}`;await emit();}
 assert.equal(recoveryCalls().length,1,'State updates must not poll');
 await screenshot('desktop-rechecking');
 await answer(first,receipt(first));
 await expect(warning('original')).toHaveCount(0);
 await expect.poll(async()=>(await outbox()).map(entry=>entry.id)).toEqual(['other-unsent']);
 await expect(row('original').getByText(originalText,{exact:true})).toBeVisible();
 await expect(row('original').getByText(/reference.txt/)).toBeVisible();
 await expect(composer).toHaveDraft('Keep the next unsent thought');
 await emit();
 await expect(warning('original')).toHaveCount(0);
 assert.equal(recoveryCalls().length,1);
 assert.deepEqual(state.sessions[0].messages[0].delivery.status,'unknown','Fixture did not hide the warning through a snapshot');
 assertPassive();await screenshot('desktop-reconciled');
 completed.push('lost ACK: exact positive automatic receipt; original history, attachment and draft retained; unrelated outbox preserved');

 // The no-local-copy case represents another browser with only a saved input.
 state.sessions[0].messages=[message('saved-only')];await emit();
 const saved=await readFor('chat','saved-only');
 assert.ok(!(await outbox()).some(entry=>entry.commandId==='saved-only'));
 await answer(saved,receipt(saved));
 await expect(warning('saved-only')).toHaveCount(0);
 await expect(row('saved-only').getByText('Synthetic saved request: saved-only',{exact:true})).toBeVisible();
 await expect(row('saved-only').getByText(/reference.txt/)).toBeVisible();
 completed.push('saved exact input reconciles without local copy');

 // Absence, malformed/generic acceptance, and conflicting identity never imply
 // success. Each is a new exact input, not a retry loop against one fixture row.
 const negativeCases=[
  ['unknown',request=>receipt(request,'unknown')],
  ['not-saved',request=>receipt(request,'not_saved',{message:'No saved receipt was found. Nothing was resent.'})],
  ['absent',()=>({accepted:true})],
  ['unbound',()=>({accepted:true,result:{delivery:'accepted'}})],
  ['wrong-input',request=>receipt(request,'accepted',{inputId:'not-this-input'})],
  ['conflict',request=>receipt(request,'accepted',{accepted:false})],
 ];
 for(const [name,result] of negativeCases){
  const inputId='negative-'+name;
  state.sessions[0].messages=[message(inputId)];await emit();
  const request=await readFor('chat',inputId),count=recoveryCalls().length;
  await answer(request,result(request));
  await expect(warning(inputId)).toBeVisible();
  await expect(check(inputId)).toBeEnabled();
  for(let revision=0;revision<3;revision++){state.sessions[0].title=`Unrelated ${name} ${revision}`;await emit();}
  assert.equal(recoveryCalls().length,count,'Settled uncertainty must not poll');
  assert.deepEqual((await outbox()).map(entry=>entry.id),['other-unsent']);
  if(name==='not-saved'){
   await page.setViewportSize({width:390,height:844});
   await expect(warning(inputId)).toBeVisible();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile recovery must not overflow');
   await screenshot('mobile-no-evidence');
  }
  assertPassive();completed.push(`honest negative result: ${name}`);
 }

 // Chat navigation remounts the row without replacing its pending operation.
 // Same input ID in another chat must not share that operation or its result.
 state.sessions[0].messages=[message('race-input')];
 state.sessions[1].messages=[message('race-input')];await emit();
 const chatRead=await readFor('chat','race-input');
 state.selectedSessionId='other';await emit();
 const otherRead=await readFor('other','race-input');
 state.selectedSessionId='chat';await emit();
 await expect(checking('race-input')).toBeDisabled();
 assert.equal(recoveryCalls().filter(call=>call.args.sessionId==='chat'&&call.args.inputId==='race-input').length,1,'Pending remount must not duplicate the read');
 // Native disabled click is deliberately inert; the component test also invokes
 // the handler directly to cover automatic/manual ownership independent of DOM.
 await checking('race-input').evaluate(button=>button.click());
 assert.equal(recoveryCalls().filter(call=>call.args.sessionId==='chat'&&call.args.inputId==='race-input').length,1);
 state.selectedSessionId='other';await emit();
 await answer(chatRead,receipt(chatRead));
 await expect(warning('race-input')).toBeVisible();
 await expect(checking('race-input')).toBeDisabled();
 await answer(otherRead,receipt(otherRead,'unknown',{message:'Other chat is still uncertain. Nothing was resent.'}));
 await expect(row('race-input').getByText('Other chat is still uncertain. Nothing was resent.',{exact:true})).toBeVisible();
 await expect(check('race-input')).toBeEnabled();
 completed.push('pending remount/manual dedup; late positive for another chat does not clear selected warning');

 // Source identity is a nonsecret namespace supplied by state.client, not action
 // authority. Replacing it must fence the old request even for identical IDs.
 state.sessions[1].messages=[message('source-race')];await emit();
 const oldScope=historyScope,oldSourceRead=await readFor('other','source-race',oldScope);
 historyScope='synthetic-host/account-two';await emit();
 const newSourceRead=await readFor('other','source-race');
 assert.notEqual(oldSourceRead,newSourceRead);
 await answer(oldSourceRead,receipt(oldSourceRead));
 await expect(warning('source-race')).toBeVisible();
 await expect(checking('source-race')).toBeDisabled();
 await answer(newSourceRead,receipt(newSourceRead,'unknown',{message:'New source has no verified receipt. Nothing was resent.'}));
 await expect(row('source-race').getByText('New source has no verified receipt. Nothing was resent.',{exact:true})).toBeVisible();
 await expect(check('source-race')).toBeEnabled();
 assertPassive();await screenshot('mobile-source-isolation');
 completed.push('late positive from old source cannot settle the new source');

 // Exact active input, Stop-held, known failure, queued/sending and steering are
 // not automatically recovered or resumed, even after another SSE open.
 state.sessions[1].status='working';
 state.sessions[1].execution={currentTurnId:'active-input'};
 state.sessions[1].messages=[
  message('active-input'),
  message('stop-held',{status:'held',reason:'Message retained after Stop. Nothing was sent.'}),
  message('known-failure',{status:'failed',error:'Known startup failure.'}),
  message('sending',{status:'sending'}),message('queued',{status:'queued'}),
  message('update-queued',{status:'queued_for_update'}),
  {...message('steering'),steering:{generationId:'run',disposition:'unknown'}},
 ];
 await emit();
 const excludedCount=recoveryCalls().length;
 await page.evaluate(()=>window.fixtureTransport.disconnect());
 await page.evaluate(value=>window.fixtureTransport.reconnect(value),wireState());
 await expect(row('stop-held').getByText('Message retained after Stop. Nothing was sent.',{exact:true})).toBeVisible();
 await expect(row('known-failure').getByText('Not sent',{exact:true})).toBeVisible();
 await expect(row('steering').getByText('Steering delivery could not be confirmed. Nothing was resent.',{exact:true})).toBeVisible();
 for(let revision=0;revision<3;revision++)await emit();
 assert.equal(recoveryCalls().length,excludedCount);
 assert.deepEqual((await outbox()).map(entry=>entry.id),['other-unsent']);
 assertPassive();await screenshot('mobile-held-and-active');
 completed.push('active, Stop-held, failed, sending, queued, update-queued and steering: no automatic check or resend');
 assert.deepEqual(errors,[]);
 assert.equal(pending.length,0,'All synthetic requests settled');
 await saveResult(true);
 console.log(`Delivery reconciliation browser passed: ${completed.length} checkpoints; senderCounter=0, duplicateExecution=0; artifacts ${artifact}`);
}catch(error){
 if(page)try{await screenshot('failure')}catch{}
 await saveResult(false,error);
 throw error;
}finally{
 if(browser)await browser.close();
 if(vite)await vite.close();
}