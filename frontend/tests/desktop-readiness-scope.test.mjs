import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DesktopReadiness}=await server.ssrLoadModule('/src/desktop-readiness.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
const session={id:'synthetic-chat'};
const report={sessionId:session.id,observedAt:1,host:{host:{label:'Synthetic host'},platform:'fixture',python:{version:'fixture',path:'/fixture/python'},computerUsePackage:{status:'missing'}},nativeObservation:{available:false,status:'unsupported'},worker:{status:'unavailable',reason:'No worker started'},voiceObservation:{source:{kind:'window',label:'Earlier selected source'},expiresAt:100},nextActions:[]};
const state=()=>({computerVisual:{id:'grant-one',sessionId:session.id,available:true}});

for(const change of [{id:'replacement',sessionId:session.id,available:true},{available:false}]){
 test(`conversation screen ${change.available?'replacement':'revoke'} clears an old setup report`,async()=>{
  let current=state(),calls=0,root;
  const act=async()=>{calls++;return {result:report}};
  await renderAct(async()=>{root=create(React.createElement(DesktopReadiness,{state:current,session,act}))});
  await renderAct(async()=>root.root.findByProps({'data-action':'desktop.readiness'}).props.onClick());
  assert.match(JSON.stringify(root.toJSON()),/Earlier selected source/);
  current={...current,computerVisual:change};
  await renderAct(async()=>root.update(React.createElement(DesktopReadiness,{state:current,session,act})));
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Earlier selected source/);
  assert.equal(calls,1,'permission changes must not automatically recheck or capture');
  await renderAct(async()=>root.unmount());
 });
}

test('a late setup result cannot restore a revoked conversation grant',async()=>{
 let release,root,current=state();const pending=new Promise(resolve=>release=resolve),act=()=>pending;
 await renderAct(async()=>{root=create(React.createElement(DesktopReadiness,{state:current,session,act}))});
 let request;
 await renderAct(async()=>{request=root.root.findByProps({'data-action':'desktop.readiness'}).props.onClick()});
 current={...current,computerVisual:{available:false}};
 await renderAct(async()=>root.update(React.createElement(DesktopReadiness,{state:current,session,act})));
 await renderAct(async()=>{release({result:report});await request});
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/Earlier selected source/);
 await renderAct(async()=>root.unmount());
});
