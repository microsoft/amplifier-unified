import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {MessageDelivery}=await server.ssrLoadModule('/src/message-delivery.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const text=node=>typeof node==='string'?node:(node?.children||[]).map(text).join('');
const button=(root,label)=>root.root.findAllByType('button').find(row=>text(row)===label);
async function fixture(saved=true){
 const calls=[],retryCalls=[],message={id:'m',inputId:'original',role:'user',text:'Request'},session={id:'chat',status:'stopped',messages:saved?[message]:[]};
 const dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true,result:action==='conversation.delivery'?{delivery:'unknown',message:'Checking does not resend it.'}:{delivery:'accepted',resent:true}}};
 const retry=async m=>retryCalls.push(m);let root;
 await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session,delivery:{status:'unknown'},localDelivery:saved?null:{commandId:'original'},dispatch,retry}))});
 return {root,calls,retryCalls,session};
}
test('another browser can check saved delivery and confirm one explicit resend',async()=>{
 const {root,calls,retryCalls}=await fixture();
 await act(async()=>button(root,'Check delivery').props.onClick());
 assert.deepEqual(calls,[{action:'conversation.delivery',args:{sessionId:'chat',inputId:'original'}}]);
 assert.match(JSON.stringify(root.toJSON()),/Checking does not resend it/);
 await act(async()=>button(root,'Send again').props.onClick());
 assert.equal(calls.length,1);assert.match(JSON.stringify(root.toJSON()),/could repeat/);
 await act(async()=>button(root,'Cancel').props.onClick());assert.equal(calls.length,1);
 await act(async()=>button(root,'Send again').props.onClick());
 await act(async()=>button(root,'Send this message again').props.onClick());
 assert.deepEqual(calls[1],{action:'conversation.retry',args:{sessionId:'chat',inputId:'original',confirmUncertain:true}});
 assert.equal(retryCalls.length,0);await act(async()=>root.unmount());
});
test('a local-only message uses its original outbox identity only after confirmation',async()=>{
 const {root,calls,retryCalls}=await fixture(false);
 await act(async()=>button(root,'Check delivery').props.onClick());
 assert.equal(retryCalls.length,0);
 await act(async()=>button(root,'Send again').props.onClick());
 await act(async()=>button(root,'Send this message again').props.onClick());
 assert.equal(retryCalls[0].inputId,'original');assert.equal(calls.length,1);
 await act(async()=>root.unmount());
});
test('known startup failure retries the saved identity without an uncertainty confirmation',async()=>{
 const calls=[],message={id:'m',inputId:'original',role:'user',text:'Request'},session={id:'chat',status:'error',messages:[message]};let root;
 await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session,delivery:{status:'failed'},localDelivery:{commandId:'original',status:'failed'},dispatch:async(action,args)=>{calls.push({action,args});return {result:{delivery:'accepted'}}},retry:()=>{throw Error('Must reuse saved input')}}))});
 await act(async()=>button(root,'Retry').props.onClick());
 assert.deepEqual(calls,[{action:'conversation.retry',args:{sessionId:'chat',inputId:'original',confirmUncertain:true}}]);
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/could repeat that work/);
 await act(async()=>root.unmount());
});
