import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {readOutbox,outboxMessages,useMessageOutbox}=await server.ssrLoadModule('/src/message-outbox.jsx');
const {externalMessageAttribution}=await server.ssrLoadModule('/src/external-participants.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const scope='["owned-host","/workspace","room"]';
const room={id:'room',externalAgent:{participants:[{id:'alpha',label:'Alpha now'},{id:'beta',label:'Beta now'}]}};
const key='amplifier.messageOutbox.v1';
const pending={id:'local',commandId:'input',sessionId:null,scope,text:'Ask Beta once',attachmentIds:[],attachments:[],status:'sending',recipientAgentId:'beta',recipientAgentLabel:'Original Beta',creation:{id:'creation',setup:{workspace:'/workspace',externalAgentIds:['alpha','beta'],location:{kind:'workspace'}}}};
const memory=value=>{const values=new Map([[key,JSON.stringify(value)]]);return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,String(v))}};

test('reload preserves the captured recipient and creation roster without resending or rebinding',()=>{
 const storage=memory([pending]);
 const [loaded]=readOutbox(storage);
 assert.equal(loaded.status,'unknown');
 assert.equal(loaded.recipientAgentId,'beta');assert.equal(loaded.recipientAgentLabel,'Original Beta');
 assert.deepEqual(loaded.creation,pending.creation);
 const [before]=outboxMessages([],[loaded],null,scope);
 assert.equal(before.recipientAgentId,'beta');assert.equal(externalMessageAttribution(before,room),'To Original Beta');
 assert.deepEqual(outboxMessages([],[loaded],null,'different-host'),[]);
 assert.deepEqual(JSON.parse(storage.getItem(key)),[pending],'Passive recovery does not write or mutate the submitted target');
});

test('binding a created chat and explicit retry status updates preserve the submitted recipient through another reload',async()=>{
 const previous=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage');
 Object.defineProperty(globalThis,'sessionStorage',{configurable:true,writable:true,value:memory([pending])});
 let root,outbox;function Harness(){outbox=useMessageOutbox();return null}
 try{
  await act(async()=>{root=create(React.createElement(Harness))});
  await act(async()=>outbox.update('local',{sessionId:'room',status:'failed',error:'The attach was refused'}));
  await act(async()=>outbox.update('local',{commandId:'new-explicit-input',status:'sending',error:''}));
  const [saved]=readOutbox(sessionStorage);
  assert.equal(saved.recipientAgentId,'beta');assert.equal(saved.recipientAgentLabel,'Original Beta');assert.deepEqual(saved.creation,pending.creation);
  assert.equal(saved.commandId,'new-explicit-input');assert.equal(saved.sessionId,'room');assert.equal(saved.status,'unknown');
  const [message]=outboxMessages([],outbox.entries,'room',scope);
  assert.equal(message.recipientAgentId,'beta');assert.equal(message.localDelivery.recipientAgentId,'beta');
  assert.equal(externalMessageAttribution(message,room),'To Original Beta','Current roster labels do not relabel the saved submission');
 }finally{
  await act(async()=>root?.unmount());
  if(previous)Object.defineProperty(globalThis,'sessionStorage',previous);else delete globalThis.sessionStorage;
 }
});

test('a durable receipt keeps its canonical recipient while local recovery retains the original envelope',()=>{
 const entry={...pending,sessionId:'room',status:'unknown'};
 const canonical={id:'saved',inputId:'input',role:'user',text:pending.text,recipientAgentId:'beta',recipientAgentLabel:'Saved Beta',delivery:{status:'sending'}};
 const [message]=outboxMessages([canonical],[entry],'room',scope);
 assert.equal(message.recipientAgentId,'beta');assert.equal(message.recipientAgentLabel,'Saved Beta');
 assert.equal(message.localDelivery.recipientAgentId,'beta');assert.equal(message.localDelivery.recipientAgentLabel,'Original Beta');
 assert.equal(externalMessageAttribution(message,room),'To Saved Beta');
 const [accepted]=outboxMessages([{...canonical,delivery:{status:'accepted'}}],[entry],'room',scope);
 assert.equal(accepted.localDelivery,undefined);assert.equal(accepted.recipientAgentId,'beta');
});

test('ordinary native outbox recovery does not invent an external recipient',()=>{
 const {recipientAgentId,recipientAgentLabel,creation,...native}=pending;
 const loaded=readOutbox(memory([native]));
 const [message]=outboxMessages([],loaded,null,scope);
 assert.equal(message.recipientAgentId,undefined);assert.equal(message.recipientAgentLabel,undefined);
 assert.equal(externalMessageAttribution(message,{id:'native'}),'');
 assert.equal(message.localDelivery.commandId,'input');assert.equal(message.text,pending.text);
});
