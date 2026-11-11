// Historical browser ID/command retained. Real Vite UI, fresh synthetic HTTP/SSE
// contexts only: no legacy warning OR badge, no adoption, replay or journal repair.
// Scoped reconciliation remains in delivery-reconciliation-browser.mjs.
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/legacy-outbox-notice';
const payloadKey='amplifier.messageOutbox.v1',metadataKey='amplifier.legacyOutboxNotice.v1';
const metadata='["old-private-dismissal-metadata"]',privateMarker='legacy-private-';
const historyScope='synthetic-history',draft='Keep this next unsent thought';
const scopeFor=(host=historyScope,workspace='/fixture',native='chat')=>JSON.stringify([host,workspace,native]);
const seed=[
 {id:'unbound-unknown',commandId:'shared-input',sessionId:'chat',createdAt:123,
  text:'legacy-private-unknown-body <img src="/api/legacy-private-image">',status:'unknown',
  error:'legacy-private-unknown-error',attachmentIds:['legacy-private-attachment'],
  attachments:[{id:'legacy-private-attachment',name:'legacy-private-reference.txt',size:19,
   url:'/api/attachments/legacy-private-attachment',content:'legacy-private-attachment-body'}],
  document:{version:2,segments:[{type:'text',text:'legacy-private-document-body'}]}},
 {id:'unbound-failed',commandId:'failed-input',sessionId:'other',createdAt:124,
  text:'legacy-private-failed-body',status:'failed',error:'legacy-private-failed-error',attachmentIds:[]},
 {id:'unbound-new-chat',commandId:'new-chat-input',sessionId:null,
  text:'legacy-private-new-chat-body',status:'unknown',attachmentIds:[]},
 ...[scopeFor('foreign-history'),scopeFor(historyScope,'/elsewhere'),scopeFor(historyScope,'/fixture','other-native')]
  .map((scope,index)=>({id:`foreign-${index}`,commandId:'shared-input',sessionId:'chat',scope,
   text:`legacy-private-foreign-${index}`,status:'unknown',attachmentIds:[]})),
];
// Whitespace catches even a parse/reserialize of an otherwise unchanged journal.
const raw='\n'+JSON.stringify(seed,null,2)+'\n';
const session=id=>({id,title:id,sessionKind:'root',workspace:'/fixture',workspaceId:'project',
 nativeIdentity:id,status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]});
const freshState=()=>({
 revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:false},
 sessions:[{...session('chat'),messages:[{id:'saved-current',inputId:'shared-input',role:'user',
  text:'Current server-owned message with the same input ID',delivery:{status:'accepted'}}]},session('other')],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],
 selectedSessionId:'chat',selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],
  providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false},
});
const cases=[],screenshots=[];
let vite,browser,origin;

// Extend the existing synthetic EventSource, keeping production onopen/reconnect
// handling. This is a transport peer, not a React hook or dispatch replacement.
function installReconnect(){
 const Base=window.EventSource,sources=new Set();let online=true;
 Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>online});
 window.EventSource=class extends Base{
  constructor(...args){super(...args);sources.add(this)}
  close(){sources.delete(this);super.close()}
 };
 window.fixtureTransport={
  disconnect(){online=false;for(const source of sources){source.readyState=0;source.onerror?.(new Event('error'))}},
  reconnect(state){online=true;for(const source of sources){source.readyState=1;source.onopen?.();
   source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}))}},
 };
}
function installStorageCase({payloadKey,metadataKey,raw,metadata,fault,privateMarker}){
 const storage=sessionStorage,local=localStorage,proto=Storage.prototype;
 const get=proto.getItem,set=proto.setItem,remove=proto.removeItem,clear=proto.clear;
 const marker='legacy-notice-fixture-seeded';
 if(get.call(storage,marker)!=='1'){
  set.call(storage,payloadKey,raw);set.call(storage,metadataKey,metadata);set.call(storage,marker,'1');
 }
 const access=[],blocked={getter:0,read:0,write:0};let rendered=false,noise=false;
 const track=(operation,target,key)=>{
  if(key===payloadKey||key===metadataKey)access.push({operation,key,area:target===storage?'session':'local'});
 };
 proto.getItem=function(key){
  track('read',this,key);
  if(this===storage&&fault==='read'){blocked.read++;throw new DOMException('Synthetic storage read refusal','SecurityError')}
  return get.call(this,key);
 };
 proto.setItem=function(key,value){
  track('write',this,key);
  if(this===storage&&fault==='write'){blocked.write++;throw new DOMException('Synthetic storage write refusal','QuotaExceededError')}
  return set.call(this,key,value);
 };
 proto.removeItem=function(key){
  track('remove',this,key);
  if(this===storage&&fault==='write'){blocked.write++;throw new DOMException('Synthetic storage remove refusal','SecurityError')}
  return remove.call(this,key);
 };
 proto.clear=function(){
  if(this===storage||this===local)access.push({operation:'clear',key:payloadKey,area:this===storage?'session':'local'});
  if(this===storage&&fault==='write'){blocked.write++;throw new DOMException('Synthetic storage clear refusal','SecurityError')}
  return clear.call(this);
 };
 if(fault==='getter')Object.defineProperty(window,'sessionStorage',{configurable:true,get(){
  blocked.getter++;throw new DOMException('Synthetic storage getter refusal','SecurityError');
 }});
 const inspect=node=>{
  const value=(node?.textContent||'')+(node?.outerHTML||'');
  if(value.includes(privateMarker)||value.includes(metadata))rendered=true;
  if(/legacy-outbox-notice|earlier message copies|Retained copies \(|notice preference could not be saved/.test(value))noise=true;
 };
 new MutationObserver(records=>{
  for(const record of records){
   inspect(record.target);for(const node of record.addedNodes)inspect(node);
   if(record.oldValue?.includes(privateMarker)||record.oldValue?.includes(metadata))rendered=true;
   if(/legacy-outbox-notice|earlier message copies|Retained copies \(|notice preference could not be saved/.test(record.oldValue||''))noise=true;
  }
 }).observe(document,{subtree:true,childList:true,characterData:true,characterDataOldValue:true,
  attributes:true,attributeOldValue:true});
 window.legacyNoticeFixture={snapshot(){
  inspect(document.body);
  // Test-owned readback bypasses injected denial, never the application under test.
  return {raw:get.call(storage,payloadKey),metadata:get.call(storage,metadataKey),access:[...access],
   blocked:{...blocked},rendered,noise,localPayload:get.call(local,payloadKey),localMetadata:get.call(local,metadataKey)};
 }};
}

async function runCase(fault){
 const result={fault,passed:false,completed:[],snapshots:[],errors:[],blockedRequests:[],privateRequests:0,actionNames:[]};
 cases.push(result);
 const state=freshState(),shell=createShellHttpFixture(()=>state),calls=[],drafts=new Map();
 const wireState=()=>{const value=shell.state();return {...value,client:{...value.client,historyScope}}};
 let allowedSend=null,pendingSend=null,acceptedSend=false;
 const context=await browser.newContext({viewport:{width:1280,height:900},serviceWorkers:'block'});
 const page=await context.newPage();
 page.setDefaultTimeout(8000);page.setDefaultNavigationTimeout(15000);
 page.on('pageerror',error=>result.errors.push(error.message));
 page.on('request',request=>{
  const value=request.url()+(request.postData()||'');
  if(value.includes(privateMarker)||value.includes(metadata))result.privateRequests++;
 });
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const snapshot=()=>page.evaluate(()=>window.legacyNoticeFixture.snapshot());
 const sends=()=>calls.filter(call=>call.action==='conversation.send');
 async function screenshot(name){
  const path=`${artifact}/${fault}-${name}.png`;await page.screenshot({path});screenshots.push(path);
 }
 async function cleanUI(){
  await expect(page.locator('[data-part="legacy-outbox-notice"]')).toHaveCount(0);
  await expect(page.getByText(/earlier message copies|Retained copies \(|notice preference could not be saved/)).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Hide notice',exact:true})).toHaveCount(0);
  await expect(page.getByText(/Delivery not confirmed|Not sent/,{exact:true})).toHaveCount(0);
  await expect(page.locator('[data-input-id="failed-input"],[data-input-id="new-chat-input"]')).toHaveCount(0);
  await expect(composer).toBeEditable();
 }
 async function settled(){
  await expect(composer).toBeEditable();
  await page.waitForLoadState('networkidle',{timeout:8000});
 }
 async function invariant(label){
  await cleanUI();
  const value=await snapshot();
  if(!acceptedSend){
   assert.equal(value.raw,raw,'Passive legacy retention is byte-for-byte, not migration');
   assert.deepEqual(value.access.filter(row=>row.key===payloadKey&&row.operation!=='read'),[]);
  }else assert.deepEqual(JSON.parse(value.raw),seed,'Explicit current send retires only its own scoped copy');
  assert.equal(value.metadata,metadata);assert.deepEqual(value.access.filter(row=>row.key===metadataKey),[],
   'No read, write or remove of obsolete notice metadata');
  assert.equal(value.rendered,false,'Retained payload/metadata never enters DOM, including transient nodes');
  assert.equal(value.noise,false,'Neither warning nor badge may flash before settling');
  assert.equal(value.localPayload,null);assert.equal(value.localMetadata,null);
  assert.equal(result.privateRequests,0);assert.deepEqual(result.errors,[]);assert.deepEqual(result.blockedRequests,[]);
  assert.deepEqual(calls.filter(call=>call.action.startsWith('conversation.')&&call.action!=='conversation.send'),[]);
  assert.equal(sends().length,acceptedSend?1:0,'No automatic legacy send; the optional current send is counted separately');
  result.snapshots.push({label,payloadPreserved:true,metadataUntouched:true,noLegacyDOM:true,
   storageBlocked:value.blocked,currentSends:sends().length});
 }
 async function emit(){
  const revision=++state.revision;await page.evaluate(value=>window.emitFixtureState(value),wireState());
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().revision)).toBeGreaterThanOrEqual(revision);
  await settled();
 }
 async function selectChat(id){
  state.selectedSessionId=id;state.view={...state.view,draft:'',draftDocument:null,...drafts.get(id)};
  await emit();
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(id);
  await expect(composer).toHaveDraft(state.view.draft);
 }
 async function mobile(){
  await page.setViewportSize({width:320,height:844});await expect(composer).toBeInViewport();
  const geometry=await composer.evaluate(node=>{
   const rect=node.getBoundingClientRect();
   return {width:innerWidth,documentWidth:document.documentElement.scrollWidth,left:rect.left,right:rect.right,
    hit:node.contains(document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2))};
  });
  assert.equal(geometry.width,320);assert.ok(geometry.documentWidth<=320);
  assert.ok(geometry.left>=0&&geometry.right<=320&&geometry.hit,'Mobile composer is visible, not covered by a notice');
  await composer.focus();await expect(composer).toBeFocused();
  await page.keyboard.press('ControlOrMeta+End');await page.keyboard.type(' mobile');
  await expect(composer).toHaveDraft(draft+' mobile');
  await expect.poll(()=>drafts.get('chat')?.draft).toBe(draft+' mobile');
  await settled();result.mobile=geometry;
 }
 try{
  // One init script establishes helper ordering; all seed values are synthetic.
  await page.addInitScript({content:`(${installFixtureEvents.toString()})();(${installReconnect.toString()})();(${installStorageCase.toString()})(${JSON.stringify({payloadKey,metadataKey,raw,metadata,fault,privateMarker})});`});
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   const requestText=request.url()+(request.postData()||'');
   if(url.origin!==origin||requestText.includes(privateMarker)||requestText.includes(metadata)){
    result.blockedRequests.push(url.origin!==origin?'external-origin':'retained-payload');return route.abort();
   }
   if(!url.pathname.startsWith('/api/'))return route.continue();
   const response=shell.response(request);
   if(response!==undefined)return route.fulfill({json:response});
   if(url.pathname==='/api/state')return route.fulfill({json:wireState()});
   if(url.pathname==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
   if(url.pathname==='/api/outputs/media')return route.fulfill({json:{items:[],overflow:false,mediaRevision:0}});
   if(url.pathname!=='/api/actions')return route.fulfill({json:{ok:true}});
   const envelope=request.postDataJSON();
   const body=envelope.action==='shell.command'?(envelope.args.command||envelope.args):envelope;
   calls.push(body);result.actionNames.push(body.action);
   if(body.action==='conversation.send'&&allowedSend&&!pendingSend&&sends().length===1&&
      body.args.sessionId==='chat'&&body.args.text===allowedSend&&body.args.attachmentIds?.length===0&&
      !seed.some(row=>row.commandId===body.id)){
    pendingSend={route,body};return;
   }
   if(body.action==='view.update'){
    const patch=body.args.patch||{};
    if(Object.hasOwn(patch,'draft')){
     const id=body.args.sessionId??state.selectedSessionId;
     drafts.set(id,{draft:patch.draft,draftDocument:patch.draftDocument??null});
    }
    state.view={...state.view,...patch};state.revision++;
   }else if(!['shell.report','attention.read','bundles.list','configuration.defaults','providers.list','theme.list'].includes(body.action)&&
            !(body.action==='runtime.control'&&body.args.operation==='configuration.catalog')){
    result.blockedRequests.push(body.action);
    return route.fulfill({status:409,json:{accepted:false,error:'This fixture refuses unexpected actions.'}});
   }
   return route.fulfill({json:{accepted:true,operationId:body.id,state:wireState()}});
  });
  await page.goto(origin);await settled();await invariant('initial');
  await expect(page.locator('[data-input-id="shared-input"]')).toHaveCount(1);
  await expect(page.getByText('Current server-owned message with the same input ID',{exact:true})).toBeVisible();
  await composer.fill(draft);await expect(composer).toHaveDraft(draft);
  await expect.poll(()=>drafts.get('chat')?.draft).toBe(draft);await settled();
  await invariant('draft');await screenshot('desktop-clean');
  state.sessions[0].title='Unrelated state change';await emit();await invariant('unrelated-frame');
  await selectChat('other');await invariant('other-chat');
  await selectChat(null);await invariant('new-chat-composer');
  await composer.fill('Separate new-chat draft');await expect(composer).toHaveDraft('Separate new-chat draft');
  await expect.poll(()=>drafts.get(null)?.draft).toBe('Separate new-chat draft');await settled();
  await selectChat('chat');await expect(composer).toHaveDraft(draft);await invariant('original-chat');
  await page.reload();await settled();await expect(composer).toHaveDraft(draft);await invariant('reload');
  await page.evaluate(()=>window.fixtureTransport.disconnect());
  await page.evaluate(value=>window.fixtureTransport.reconnect(value),wireState());
  await settled();await expect(composer).toHaveDraft(draft);await invariant('reconnect');
  await mobile();await invariant('mobile-keyboard');await screenshot('mobile-clean');
  result.completed.push('No warning or badge on mount, unrelated frame, other/new chat, reload or reconnect; retained bytes and metadata untouched.',
   'Current accepted same-ID history is visible without adopting private retained copies; mobile keyboard draft is usable.');
  if(fault!=='none'){
   const value=await snapshot();assert.ok(value.blocked[fault]>0,'The storage denial must actually be exercised, not skipped');
   result.completed.push('Actual storage denial exercised; composer remains usable without legacy noise.');
  }else{
   // Deliberate new input uses real production send/outbox code. Hold its HTTP
   // receipt to inspect exact scope before retirement; never authorize old IDs.
   allowedSend='Fresh current request';await composer.fill(allowedSend);
   await page.getByRole('button',{name:'Send message',exact:true}).click();
   await expect.poll(()=>!!pendingSend).toBe(true);
   const value=await snapshot(),rows=JSON.parse(value.raw),current=rows.filter(row=>!seed.some(old=>old.id===row.id));
   assert.deepEqual(rows.filter(row=>seed.some(old=>old.id===row.id)),seed);
   assert.equal(current.length,1);assert.equal(current[0].commandId,pendingSend.body.id);
   assert.equal(current[0].sessionId,'chat');assert.equal(current[0].scope,scopeFor());
   assert.equal(current[0].text,allowedSend);assert.equal(current[0].status,'sending');
   assert.equal(value.metadata,metadata);assert.deepEqual(value.access.filter(row=>row.key===metadataKey),[]);
   await expect(page.locator(`[data-input-id="${pendingSend.body.id}"]`)).toHaveCount(1);
   state.sessions[0].messages.push({id:'accepted-current-send',inputId:pendingSend.body.id,role:'user',text:allowedSend,delivery:{status:'accepted'}});
   state.revision++;
   await pendingSend.route.fulfill({json:{accepted:true,delivery:'accepted',state:wireState()}});
   acceptedSend=true;
   await expect.poll(async()=>JSON.parse((await snapshot()).raw)).toEqual(seed);
   await settled();await expect(composer).toHaveDraft('');await invariant('explicit-current-send-accepted');
   await page.reload();await settled();await invariant('accepted-send-reload');
   result.completed.push('One explicit current send had exact host/workspace/native scope and accepted retirement; zero legacy sends or delivery checks.');
   result.currentSend={count:1,exactScope:true,retiredOnlyCurrent:true};
   await screenshot('current-send-accepted');
  }
  result.passed=true;
 }catch(error){try{await screenshot('failure')}catch{}throw error}
 finally{await context.close()}
}
async function saveResult(passed,error){
 await writeFile(`${artifact}/browser-result.json`,JSON.stringify({
  passed,evidence:'Real Vite UI; synthetic HTTP/SSE, fresh contexts, actual injected storage denials. Not host/provider qualification.',
  viewports:[{width:1280,height:900},{width:320,height:844}],cases,screenshots,
  complementaryJourney:'tests/delivery-reconciliation-browser.mjs (separate execution required)',
  ...(error?{failure:String(error.stack||error)}:{}),
 },null,2));
}
await mkdir(artifact,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();origin=new URL(vite.resolvedUrls.local[0]).origin;
 browser=await chromium.launch({headless:true});
 for(const fault of ['none','getter','read','write'])await runCase(fault);
 await saveResult(true);
 console.log(`Legacy outbox absence browser passed: ${cases.length} isolated cases; artifacts ${artifact}`);
}catch(error){await saveResult(false,error);throw error}
finally{try{if(browser)await browser.close()}finally{if(vite)await vite.close()}}
