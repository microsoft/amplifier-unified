import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {navigationResources} from '../src/shell/navigation-demand.js';

const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {useShell}=await vite.ssrLoadModule('/src/shell/runtime.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
const instance={id:'chats',package:'builtin.chats',slot:'navigation'};
const state=view=>({revision:1,shellDataKey:'same-data',selectedSessionId:'chat-a',selectedWorkspaceId:'workspace-a',sessions:[],view});
function snapshot(input,resources=navigationResources(input)){
 return {revision:1,resolvedInstances:[instance],effectiveComposition:{instances:[instance]},snapshots:{chats:{view:input.view,selectedSessionId:input.selectedSessionId,selectedWorkspaceId:input.selectedWorkspaceId,library:{bounded:true},navigationResources:resources}}};
}

for(const outcome of ['success','error'])test(`late ${outcome} for a replaced navigation demand cannot settle the new surface`,async()=>{
 const previous={fetch:globalThis.fetch,document:globalThis.document,window:globalThis.window};
 globalThis.document=Object.assign(new EventTarget(),{hidden:false});globalThis.window=new EventTarget();
 const calls=[];let root,current;
 globalThis.fetch=(path,options)=>{
  if(path.startsWith('/api/actions'))return Promise.resolve(new Response('{}'));
  return new Promise(resolve=>calls.push({path,options,resolve}));
 };
 const dispatch=async()=>({accepted:true});
 function Probe({input}){current=useShell(input,dispatch,'test-client',input);return null}
 const chat=state({workSurface:'chat'}),search=state({workSurface:'chats'});
 try{
  await act(async()=>{root=create(React.createElement(Probe,{input:chat}))});
  assert.equal(calls.length,1);
  assert.deepEqual(JSON.parse(new URL(calls[0].path,'http://fixture').searchParams.get('navigationResources')),navigationResources(chat));
  await act(async()=>root.update(React.createElement(Probe,{input:search})));
  await act(async()=>{void current.refresh();void current.refresh();});
  assert.equal(calls.length,1,'invalidations share the held request');
  await act(async()=>calls[0].resolve(new Response(JSON.stringify(outcome==='success'?snapshot(chat):{error:'Old surface failed'}),{status:outcome==='success'?200:500})));
   assert.equal(calls.length,2,'changed demand coalesces into one replacement read');
   assert.equal(current.data,null,'the old response cannot become a new-surface baseline');
   assert.equal(current.error,'','a stale error cannot replace the new surface');
   assert.deepEqual(JSON.parse(new URL(calls[1].path,'http://fixture').searchParams.get('navigationResources')),navigationResources(search));
   await act(async()=>calls[1].resolve(new Response(JSON.stringify(snapshot(search)))));
   assert.equal(current.ready,true);assert.equal(current.data.snapshots.chats.view.workSurface,'chats');
 }finally{
  if(root)await act(async()=>root.unmount());
  Object.assign(globalThis,previous);
 }
});
