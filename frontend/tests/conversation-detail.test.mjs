import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {useConversationDetail}=await server.ssrLoadModule('/src/conversation-detail.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
const source=id=>({id,messages:[{id:id+'-last',text:'Current'}],messageWindow:{offset:1,before:id+'-last'},execution:{nodes:[],turns:[],segments:[]},executionWindow:{offset:1,before:'g-last',part:'groups'}});
const response=value=>new Response(JSON.stringify(value),{headers:{'content-type':'application/json'}});
test('paired history failure preserves both windows and a retry commits them together',async()=>{
 let detail,root,pending=[],value=source('one');const original=globalThis.fetch;
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.earlier()});
  assert.equal(pending.length,2);
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
