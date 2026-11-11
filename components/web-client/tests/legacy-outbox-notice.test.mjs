// Keep the historical test command, but test retention without a legacy notice.
// These are real outbox hook/projection checks; the sibling browser journey owns
// absence of UI, real composer gestures and HTTP effects. No substitute notice.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {readOutbox,outboxMessages,outboxMatches,useMessageOutbox}=await server.ssrLoadModule('/src/message-outbox.jsx');
const {deliveryScope}=await server.ssrLoadModule('/src/pending-delivery-checks.js');
const {MessageDelivery}=await server.ssrLoadModule('/src/message-delivery.jsx');
const originalActEnvironment=Object.getOwnPropertyDescriptor(globalThis,'IS_REACT_ACT_ENVIRONMENT');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(async()=>{
 await server.close();
 if(originalActEnvironment)Object.defineProperty(globalThis,'IS_REACT_ACT_ENVIRONMENT',originalActEnvironment);
 else delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

const outboxKey='amplifier.messageOutbox.v1',metadataKey='amplifier.legacyOutboxNotice.v1',draftKey='amplifier.pendingDrafts.v1';
const draftBytes='\n{"owner":"fixture-client","rows":[{"sessionId":"chat","revision":"draft-revision","text":"PRIVATE next draft"}]}\n';
const state={client:{historyScope:'fixture-history',hostInstanceId:'fixture-host'}};
const session={id:'chat',workspace:'/fixture',nativeIdentity:'native-chat',status:'idle',messages:[]};
const scope=deliveryScope(state,session);
const foreignScopes=[
 deliveryScope({client:{historyScope:'other-history',hostInstanceId:'fixture-host'}},session),
 deliveryScope(state,{...session,workspace:'/elsewhere'}),
 deliveryScope(state,{...session,nativeIdentity:'other-native'}),
];
const entry=(id,extra={})=>({id,commandId:'same-input',sessionId:'chat',status:'unknown',
 text:`PRIVATE retained body ${id}`,attachmentIds:['PRIVATE-attachment'],
 attachments:[{id:'PRIVATE-attachment',name:'PRIVATE-reference.txt'}],...extra});
const rawFor=rows=>'\n'+JSON.stringify(rows,null,2)+'\n';
function deepFreeze(value){
 if(value&&typeof value==='object'&&!Object.isFrozen(value)){
  for(const child of Object.values(value))deepFreeze(child);
  Object.freeze(value);
 }
 return value;
}
const retained=deepFreeze([
 entry('unbound-unknown'),entry('unbound-failed',{status:'failed'}),
 entry('unbound-new-chat',{sessionId:null}),
 ...foreignScopes.map((scope,index)=>entry(`foreign-${index}`,{scope})),
]);
const roots=new Set();
let originalGlobals,values,access,fault,storage,metadata;
test.beforeEach(()=>{
 originalGlobals=new Map(['window','navigator','sessionStorage'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 metadata='["old-private-dismissal-metadata"]';
 values=new Map([[outboxKey,rawFor(retained)],[metadataKey,metadata],[draftKey,draftBytes]]);
 access=[];fault='none';
 storage={
  getItem(key){access.push(['read',key]);if(fault==='read')throw Error('PRIVATE storage read');return values.get(key)??null},
  setItem(key,value){access.push(['write',key]);if(fault==='write')throw Error('PRIVATE storage write');values.set(key,String(value))},
  removeItem(key){access.push(['remove',key]);values.delete(key)},
 };
 const window=new EventTarget(),navigator={onLine:true};
 Object.assign(window,{sessionStorage:storage,navigator});
 for(const [key,value] of Object.entries({sessionStorage:storage,window,navigator}))
  Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
});
test.afterEach(async()=>{
 try{
  await act(async()=>{for(const root of roots)root.unmount();roots.clear()});
  assert.equal(values.get(metadataKey),metadata,'Old notice metadata is neither migrated nor removed');
  assert.equal(values.get(draftKey),draftBytes,'Outbox work cannot alter the draft journal');
  assert.deepEqual(access.filter(([,key])=>[metadataKey,draftKey].includes(key)),[],
   'Neither obsolete notice metadata nor draft storage belongs to outbox projection');
 }finally{
  for(const [key,descriptor] of originalGlobals){
   if(descriptor)Object.defineProperty(globalThis,key,descriptor);
   else delete globalThis[key];
  }
 }
});
async function mount(){
 let api,root;
 function Harness(){api=useMessageOutbox();return null}
 const render=()=>act(async()=>{root=create(React.createElement(Harness));roots.add(root)});
 await render();
 return {get api(){return api},
  async rerender(){await act(async()=>root.update(React.createElement(Harness)))},
  async remount(){await act(async()=>root.unmount());roots.delete(root);await render()},
 };
}
function assertRetained(f){
 assert.deepEqual(f.api.entries,retained);
 assert.equal(values.get(outboxKey),rawFor(retained),'Reading/rerendering cannot reserialize retained payload');
 assert.deepEqual(access.filter(([operation])=>operation!=='read'),[]);
}

test('real hook retains unbound and foreign copies byte-for-byte without adopting matching chat/input IDs',async()=>{
 const f=await mount();assertRetained(f);
 for(const selected of [session,{...session,id:'other'},null]){
  assert.deepEqual(outboxMessages([],deepFreeze(f.api.entries),selected?.id,deliveryScope(state,selected)),[]);
  await f.rerender();assertRetained(f);
 }
 const saved=deepFreeze({id:'saved',inputId:'same-input',role:'user',text:'Current server-owned text',delivery:{status:'accepted'}});
 const messages=outboxMessages([saved],f.api.entries,'chat',scope);
 assert.deepEqual(messages,[saved]);assert.equal(messages[0],saved);
 for(const targetScope of [scope,undefined,null,'']){
  await act(async()=>f.api.acknowledge('chat','same-input',{delivery:'accepted'},targetScope));
  assertRetained(f);
 }
 await act(async()=>window.dispatchEvent(new Event('amplifier-reconnected')));
 await f.remount();assertRetained(f);
});

for(const [label,other] of [['host/history',foreignScopes[0]],['workspace',foreignScopes[1]],['native identity',foreignScopes[2]]]){
 test(`${label} mismatch cannot project or retire a same-ID retained copy`,async()=>{
  const rows=deepFreeze([entry('foreign',{scope:other}),entry('unbound')]);
  values.set(outboxKey,rawFor(rows));
  const f=await mount();
  assert.equal(outboxMatches(rows[0],scope),false);
  assert.deepEqual(outboxMessages([],f.api.entries,'chat',scope),[]);
  await act(async()=>f.api.acknowledge('chat','same-input',{delivery:'accepted'},scope));
  assert.deepEqual(f.api.entries,rows);assert.equal(values.get(outboxKey),rawFor(rows));
  assert.deepEqual(access.filter(([operation])=>operation!=='read'),[]);
 });
}

test('exact scoped controls still project and acknowledge only the matching entry',async()=>{
 const current=entry('current',{scope,text:'Current scoped request'}),rows=deepFreeze([...retained,current]);
 values.set(outboxKey,rawFor(rows));
 const f=await mount();
 assert.deepEqual(outboxMessages([],f.api.entries,'chat',scope).map(row=>row.localDelivery),[current]);
 const saved=deepFreeze({id:'saved',inputId:'same-input',role:'user',text:'Current saved text',delivery:{status:'unknown'}});
 const projected=outboxMessages([saved],f.api.entries,'chat',scope);
 assert.equal(projected.length,1);assert.equal(projected[0].text,saved.text);
 assert.deepEqual(projected[0].localDelivery,current);
 for(const [chat,input,receipt] of [['other','same-input',{delivery:'accepted'}],['chat','other',{delivery:'accepted'}],['chat','same-input',{delivery:'unknown'}]])
  await act(async()=>f.api.acknowledge(chat,input,receipt,scope));
 assert.equal(values.get(outboxKey),rawFor(rows));
 await act(async()=>f.api.acknowledge('chat','same-input',{delivery:'accepted'},scope));
 assert.deepEqual(f.api.entries,retained);
 assert.deepEqual(JSON.parse(values.get(outboxKey)),retained);
 await f.remount();assert.deepEqual(f.api.entries,retained);
 await act(async()=>f.api.update('current',{status:'unknown'}));
 assert.deepEqual(f.api.entries,retained,'A late reply cannot recreate the retired scoped copy');
});

for(const status of ['unknown','failed']){
 test(`real scoped ${status} delivery UI remains available, unlike unbound copies`,async()=>{
  const local=entry('current',{scope,status,text:'Current scoped request'}),calls=[];
  const [message]=outboxMessages([],deepFreeze([...retained,local]),'chat',scope);
  let root;
  await act(async()=>{
   root=create(React.createElement(MessageDelivery,{message,session,delivery:local,localDelivery:local,
    recovery:{scope,connection:'fixture-host'},
    dispatch:async(action,args)=>{calls.push({action,args});return {accepted:true,result:{...args,delivery:'unknown'}}},
    retry:()=>assert.fail('No automatic retry'),discard:()=>assert.fail('No automatic discard')}));
   roots.add(root);
  });
  const rendered=JSON.stringify(root.toJSON());
  assert.match(rendered,status==='unknown'?/Delivery not confirmed/:/Not sent/);
  assert.doesNotMatch(rendered,/Retained copies|earlier message copies|legacy-outbox-notice/);
  assert.deepEqual(calls,status==='unknown'?[{action:'conversation.delivery',args:{sessionId:'chat',inputId:'same-input'}}]:[]);
  assert.equal(values.get(outboxKey),rawFor(retained));
 });
}

for(const value of ['', '{corrupt', 'null', '["old-private-dismissal-metadata"]']){
 test(`obsolete metadata ${JSON.stringify(value)} is never consulted or rewritten`,async()=>{
  metadata=value;values.set(metadataKey,value);
  const f=await mount();await f.rerender();await f.remount();assertRetained(f);
 });
}

for(const failure of ['getter','read','write']){
 test(`unavailable storage (${failure}) keeps real hook usable without inventing legacy state`,async()=>{
  fault=failure;
  if(failure==='getter'){
   const get=()=>{access.push(['getter',null]);throw Error('PRIVATE storage getter')};
   Object.defineProperty(globalThis,'sessionStorage',{configurable:true,get});
   Object.defineProperty(window,'sessionStorage',{configurable:true,get});
  }
  const f=await mount();
  const loaded=failure==='write'?retained:[];
  assert.deepEqual(f.api.entries,loaded);assert.equal(f.api.storageError,false,'Read failure is not a fabricated pending send');
  assert.deepEqual(outboxMessages([],f.api.entries,'chat',scope),[]);
  await f.rerender();assert.equal(values.get(outboxKey),rawFor(retained));
  assert.deepEqual(access.filter(([operation])=>['write','remove'].includes(operation)),[]);
  if(failure==='read'){
   assert.ok(access.some(([operation,key])=>operation==='read'&&key===outboxKey));
   await f.remount();assert.deepEqual(f.api.entries,[]);
   assert.equal(values.get(outboxKey),rawFor(retained));
   return; // No submission claim when the retained journal could not be read.
  }
  // Explicit current input is a positive control, not an automatic legacy action.
  const current=entry('new-current',{commandId:'new-current-input',scope,status:'sending',text:'New user input'});
  await act(async()=>f.api.update(current.id,current));
  assert.deepEqual(f.api.entries,[...loaded,current]);
  assert.equal(outboxMessages([],f.api.entries,'chat',scope)[0].localDelivery.scope,scope);
  assert.equal(f.api.storageError,true);
  assert.ok(access.some(([operation])=>operation===(failure==='getter'?'getter':'write')));
  assert.equal(values.get(outboxKey),rawFor(retained),'Failed persistence is not claimed as successful');
 });
}

test('empty outbox is empty, and a new current entry has exact scope without migrating retained copies',async()=>{
 values.set(outboxKey,'[]');
 assert.deepEqual(readOutbox(storage),[]);
 const empty=await mount();assert.deepEqual(empty.api.entries,[]);
 values.set(outboxKey,rawFor(retained));
 const f=await mount(),current=entry('current-new',{commandId:'current-new-input',scope,status:'sending',text:'Fresh current input'});
 await act(async()=>f.api.update(current.id,current));
 assert.deepEqual(f.api.entries,[...retained,current]);
 assert.deepEqual(JSON.parse(values.get(outboxKey)),[...retained,current]);
 await f.remount();
 const last=f.api.entries.at(-1);
 assert.equal(last.scope,scope);assert.equal(last.status,'unknown','Reload retains the real pending-delivery semantics');
 assert.deepEqual(f.api.entries.slice(0,-1),retained);
 assert.deepEqual(outboxMessages([],f.api.entries,'chat',scope).map(row=>row.id),['current-new']);
});

test('without a history namespace, host identity must still match and absent identity grants no scope',()=>{
 const first=deliveryScope({client:{hostInstanceId:'host-one'}},session);
 const second=deliveryScope({client:{hostInstanceId:'host-two'}},session);
 const rows=deepFreeze([entry('bound',{scope:first}),entry('unbound')]);
 assert.notEqual(first,second);assert.equal(deliveryScope({},session),null);
 assert.deepEqual(outboxMessages([],rows,'chat',first).map(row=>row.id),['bound']);
 for(const target of [second,null,undefined,''])assert.deepEqual(outboxMessages([],rows,'chat',target),[]);
});
