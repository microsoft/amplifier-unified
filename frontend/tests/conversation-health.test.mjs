import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ConversationDetails,ConversationError}=await server.ssrLoadModule('/src/conversation-controls.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
test('details and recovery use shared actions with immediate pending feedback and no duplicate submission',async()=>{
 let release;const calls=[],act=(name,args)=>{calls.push({name,args});return new Promise(resolve=>{release=resolve})};
 const session={id:'chat',runtimeSessionId:'native',status:'error',workspace:'/workspace',bundle:'work',error:'Manager turn failed'};
 let root,operation;await renderAct(async()=>{root=create(React.createElement(ConversationDetails,{session,act}))});
 // Read-only inspection starts when the details component opens.
 assert.deepEqual(calls,[{name:'session.inspect',args:{id:'chat'}}]);
 assert.equal(root.root.findByProps({'data-action':'session.recover'}).props.disabled,true);
 await renderAct(async()=>{release({accepted:true,result:{failure:{summary:'Invalid image',errorType:'InvalidRequestError'}}});await Promise.resolve()});
 assert.match(JSON.stringify(root.toJSON()),/Invalid image/);
 await renderAct(async()=>{operation=root.root.findByProps({'data-action':'session.recover'}).props.onClick()});
 await renderAct(async()=>root.root.findByProps({'data-action':'session.recover'}).props.onClick());
 assert.equal(calls.length,2);assert.deepEqual(calls[1],{name:'session.recover',args:{id:'chat'}});
 await renderAct(async()=>{release({accepted:false});await operation});
 assert.match(JSON.stringify(root.toJSON()),/Could not create/);await renderAct(async()=>root.unmount());
});
test('dismissal does not remove settings details; active work cannot be recovered',async()=>{
 const session={id:'chat',status:'working',error:'failed'},act=async()=>({accepted:true});let root;
 await renderAct(async()=>{root=create(React.createElement(ConversationError,{session,act,state:{attention:{items:[{id:'session:chat',read:true}]}}}))});
 assert.equal(root.toJSON(),null);
 await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session,act,initiallyOpen:true})));
 assert.equal(root.root.findByProps({'data-action':'session.recover'}).props.disabled,true);
 assert.match(JSON.stringify(root.toJSON()),/Copy session ID/);
 await renderAct(async()=>root.update(React.createElement(ConversationDetails,{session:{...session,status:'error',workers:[{status:'working'}]},act,initiallyOpen:true})));
 assert.equal(root.root.findByProps({'data-action':'session.recover'}).props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('error banner has no details or copy buttons',async()=>{
 let root;await renderAct(async()=>{root=create(React.createElement(ConversationError,{session:{id:'chat',error:'failed'},state:{},act:async()=>({accepted:true})}))});
 assert.equal(root.root.findAllByType('button').length,0);assert.equal(root.toJSON().props.className,'a-alert');
 await renderAct(async()=>root.unmount());
});
