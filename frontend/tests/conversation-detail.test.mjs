import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {useConversationDetail}=await server.ssrLoadModule('/src/conversation-detail.jsx');
const {inlineOwnership}=await server.ssrLoadModule('/src/conversation-navigation.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
const source=id=>({id,messages:[{id:id+'-last',text:'Current'}],messageWindow:{offset:1,before:id+'-last'},execution:{nodes:[],turns:[],segments:[]},executionWindow:{offset:1,before:'g-last',part:'groups'}});
const response=value=>new Response(JSON.stringify(value),{headers:{'content-type':'application/json'}});
test('startup, no selection and new-chat transitions have no retained window to dereference',async()=>{
 let detail,root,value=null;
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  assert.equal(detail.session,null);
  value=source('one');await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session.id,'one');
  value=undefined;await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session,undefined);
  value=source('two');await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session.id,'two');
 }finally{await act(async()=>root?.unmount())}
});
test('unchanged message branches survive parent renders and unrelated session updates',async()=>{
 let detail,root,value=source('one');
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  const original=detail.session,rows=original.messages;
  await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session,original);
  value={...value,status:'working'};await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session.messages,rows);assert.equal(detail.session.status,'working');
  value={...value,messageAnnotations:{'one-last':{reactions:['like']}}};
  await act(async()=>root.update(React.createElement(Probe)));
  assert.deepEqual(detail.session.messages[0].reactions,['like']);
  value={...value,messages:[{...value.messages[0],text:'Edited!'}]};
  await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session.messages[0].text,'Edited!');
  assert.equal(rows[0].text,'Current');
  value={...value,messageAnnotations:{}};await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session.messages[0].reactions,undefined);
 }finally{await act(async()=>root?.unmount())}
});
test('paired history failure preserves both windows and a retry commits them together',async()=>{
 let detail,root,pending=[],value=source('one');const original=globalThis.fetch;
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.earlier()});
  assert.equal(pending.length,2);
  value={...value,status:'working',ownership:{status:'yielded'},collaborationGeneration:{id:'during-paging'},approvals:[{id:'current'}]};
  await act(async()=>root.update(React.createElement(Probe)));
  for(const key of ['status','ownership','collaborationGeneration','approvals'])assert.equal(detail.session[key],value[key],'Frozen paging retains only display fields');
  await act(async()=>pending[1](response({items:[{id:'g-first'}],turns:[],offset:0,before:null})));
  assert.equal(detail.session.execution.segments.length,0);
  await act(async()=>{pending[0](new Response(JSON.stringify({error:'Unavailable'}),{status:503}));await loading});
  assert.equal(detail.session.messages.length,1);assert.equal(detail.session.execution.segments.length,0);
  assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
  pending=[];await act(async()=>{loading=detail.earlier()});
  await act(async()=>{pending[0](response({items:[{id:'one-first'}],offset:0}));pending[1](response({items:[{id:'g-first'}],turns:[],offset:0,before:null}));await loading});
  assert.deepEqual(detail.session.messages.map(row=>row.id),['one-first','one-last']);
  assert.equal(detail.session.execution.segments.length,1);assert.equal(detail.session.executionWindow.offset,0);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});
test('late history cannot attach to a chat after leaving and returning',async()=>{
 let detail,root,pending=[],value=source('one');const original=globalThis.fetch;
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.earlier()});
  value=source('two');await act(async()=>root.update(React.createElement(Probe)));
  value=source('one');await act(async()=>root.update(React.createElement(Probe)));
  await act(async()=>{pending[0](response({items:[{id:'stale'}],offset:0}));pending[1](response({items:[],turns:[],offset:0}));await loading});
  assert.deepEqual(detail.session.messages.map(row=>row.id),['one-last']);assert.equal(detail.busy,'');
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('native paging waits for loaded history rather than the admission receipt',async()=>{
 let detail,root,value={id:'native',messages:[{id:'last'}],sharedHistoryOffset:100};
 const calls=[];
 function Probe(){detail=useConversationDetail(value,null,async(...args)=>{calls.push(args);return {accepted:true}});return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.earlier()});
  assert.equal(calls.length,1);assert.equal(detail.busy,'conversation');
  value={...value,historyLoading:true};await act(async()=>root.update(React.createElement(Probe)));
  assert.deepEqual(detail.session.messages.map(row=>row.id),['last']);
  value={...value,historyLoading:false,sharedHistoryOffset:0,messages:[{id:'first'},{id:'last'}]};
  await act(async()=>{root.update(React.createElement(Probe))});await act(async()=>await loading);
  assert.deepEqual(detail.session.messages.map(row=>row.id),['first','last']);assert.equal(detail.busy,'');
 }finally{await act(async()=>root?.unmount())}
});

test('reading-window restoration does not issue a jump or override newer navigation intent',async()=>{
 let detail,root,pending=[],value=source('one'),current=true;const original=globalThis.fetch;
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.around('older',{jump:false,isCurrent:()=>current})});
  await act(async()=>{pending.shift()(response({messages:[{id:'older'}],execution:{nodes:[],turns:[]}}));await loading});
  assert.equal(detail.focused,true);assert.equal(detail.jumpRequest,null);
  await act(async()=>detail.latest(false));
  await act(async()=>{loading=detail.around('stale',{jump:false,isCurrent:()=>current})});
  current=false;
  await act(async()=>{pending.shift()(response({messages:[{id:'stale'}],execution:{nodes:[],turns:[]}}));await loading});
  assert.equal(detail.focused,false);assert.equal(detail.busy,'');assert.equal(detail.jumpRequest,null);
  assert.deepEqual(detail.session.messages.map(row=>row.id),['one-last']);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('typing during an around fetch and automatic tail eviction keep the same private editor',async()=>{
 let detail,root,value=source('one'),pending=[],instance,unmounts=0;
 const original=globalThis.fetch,view={viewId:'inline-edit',resourceId:'doc',resourceRevision:'r1',generation:1};
 const scope={client:{id:'tab',hostInstanceId:'host'},canvasWorkspace:{views:[view]}};
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Editor(){
  const identity=React.useRef({}),[text,setText]=React.useState('');
  React.useEffect(()=>{const unregister=inlineOwnership.register(scope,view,{sessionId:'one',messageId:'one-last',version:2});return()=>{unregister();unmounts++}},[]);
  instance={identity:identity.current,text,type:value=>{setText(value);inlineOwnership.intent(view,true)}};
  return React.createElement('input',{value:text,onChange:event=>instance.type(event.target.value)});
 }
 function Probe(){detail=useConversationDetail(value);return React.createElement('section',null,detail.controls,...detail.session.messages.map(row=>row.id==='one-last'?React.createElement(Editor,{key:row.id}):React.createElement('p',{key:row.id},row.id)))}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  const identity=instance.identity;let loading;
  await act(async()=>{loading=detail.around('earlier')});
  await act(async()=>instance.type('PRIVATE TEXT'));
  inlineOwnership.settle(inlineOwnership.intent(view,true),{accepted:false});
  await act(async()=>{pending.shift()(response({messages:[{id:'earlier'}],execution:{nodes:[],turns:[]}}));assert.equal(await loading,false)});
  assert.equal(instance.identity,identity);assert.equal(instance.text,'PRIVATE TEXT');assert.equal(unmounts,0);
  const shown=detail.session;
  value={...value,messages:[{id:'new-tail'}],messageWindow:{offset:10,total:20},status:'working',
   ownership:{status:'blocked',source:'fixture-peer'},historyReadOnlyReason:'History only',workspaceAvailable:false,
   collaborationGeneration:{id:'generation-new',terminal:false},draftAttachments:[{id:'new-attachment'}],approvals:[{id:'new-approval',status:'pending'}],
   execution:{nodes:[{id:'new-work'}],turns:[],segments:[],imageGeneration:[{id:'live-image-job'}]}};
  await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(instance.identity,identity);assert.equal(instance.text,'PRIVATE TEXT');assert.equal(unmounts,0);
  assert.deepEqual(detail.session.messages.map(row=>row.id),['one-last']);
  assert.equal(detail.session.messages,shown.messages);assert.equal(detail.session.messageWindow,shown.messageWindow);
  for(const key of ['status','ownership','historyReadOnlyReason','workspaceAvailable','collaborationGeneration','draftAttachments','approvals'])
   assert.equal(detail.session[key],value[key],key+' remains live while the display window is pinned');
  assert.equal(detail.session.execution.imageGeneration,value.execution.imageGeneration);
  assert.equal(detail.session.execution.nodes,shown.execution.nodes);
  // Exact host revocation is not a local navigation refusal.
  inlineOwnership.reconcile({...scope,canvasWorkspace:{views:[]}});
  await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(unmounts,1);assert.deepEqual(detail.session.messages.map(row=>row.id),['new-tail']);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('focused latest(false), around earlier/later, and additive paging respect the mounted anchor',async()=>{
 let detail,root,value=source('one'),pending=[],unregister;
 const original=globalThis.fetch,view={viewId:'inline-history',resourceId:'doc',resourceRevision:'r2',generation:2};
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.around('older')});
  await act(async()=>{pending.shift()(response({messages:[{id:'older'}],before:'before',after:'after',execution:{nodes:[],turns:[]}}));await loading});
  unregister=inlineOwnership.register({},view,{sessionId:'one',messageId:'older',version:2});
  inlineOwnership.intent(view,true);
  for(const destination of ['before','after','search-anchor']){
   await act(async()=>{loading=detail.around(destination)});
   await act(async()=>{pending.shift()(response({messages:[{id:destination}],execution:{nodes:[],turns:[]}}));assert.equal(await loading,false)});
   assert.deepEqual(detail.session.messages.map(row=>row.id),['older']);
  }
  await act(async()=>assert.equal(detail.latest(false),false));
  assert.equal(detail.focused,true);
  await act(async()=>inlineOwnership.settle(inlineOwnership.intent(view,false),{accepted:true}));
  await act(async()=>assert.equal(detail.latest(false),true));assert.equal(detail.focused,false);
  unregister();unregister=inlineOwnership.register({},view,{sessionId:'one',messageId:'one-last',version:1});inlineOwnership.intent(view,true);
  await act(async()=>{loading=detail.earlier()});
  await act(async()=>{pending.shift()(response({items:[{id:'first'}],offset:0}));pending.shift()(response({items:[],turns:[],offset:0}));await loading});
  assert.deepEqual(detail.session.messages.map(row=>row.id),['first','one-last']);
 }finally{unregister?.();await act(async()=>root?.unmount());globalThis.fetch=original}
});
