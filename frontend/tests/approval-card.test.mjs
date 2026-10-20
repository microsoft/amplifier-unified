import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ApprovalCard}=await server.ssrLoadModule('/src/approval-card.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
for(const decision of ['approve','deny'])test(`note accompanies ${decision}, targets the rendered chat and survives failure`,async()=>{
 let root;const calls=[];
 await act(async()=>{root=create(React.createElement(ApprovalCard,{approval:{id:'approval'},sessionId:'original-chat',act:async(...args)=>{calls.push(args);throw Error('Not confirmed')}}))});
 await act(async()=>root.root.findByType('textarea').props.onChange({target:{value:'Use the workspace copy.'}}));
 await act(async()=>root.root.findAllByType('button')[decision==='approve'?0:1].props.onClick());
 assert.deepEqual(calls,[['approval.respond',{sessionId:'original-chat',id:'approval',decision,comment:'Use the workspace copy.'}]]);
 assert.equal(root.root.findByType('textarea').props.value,'Use the workspace copy.');
 assert.match(JSON.stringify(root.toJSON()),/Not confirmed/);
 await act(async()=>root.unmount());
});
