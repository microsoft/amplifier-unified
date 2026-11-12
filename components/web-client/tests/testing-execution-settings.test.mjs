import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ExecutionLimits}=await server.ssrLoadModule('/src/testing-execution-settings.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

const concurrency={
 maxTaskChatsPerSource:3,maxEnvironmentsPerChat:2,maxOwnedResources:20,maxOwnedResourcesPerChat:5,
 maxPendingPeerInputs:0,maxPendingPeerInputsPerSender:0,maxPendingPeerInputsPerAssignment:0,
 maxPeerMessagesPerMinute:60,maxPendingWatchesPerTarget:4
};
const queue={maxPending:10,maxPendingPerChat:0,defaultWaitSeconds:0};
const profile={id:'linux-python',label:'Python',cpus:2,memoryMiB:2048,pids:256,tmpMiB:128,shmMiB:64,defaultTimeoutSeconds:60,maxTimeoutSeconds:300,preparationTimeoutSeconds:120};
const initial=()=>({
 revision:7,concurrency:{...concurrency},effectiveConcurrency:{...concurrency},
 queue:{...queue},effectiveQueue:{...queue},profiles:{'linux-python':{cpus:2}},effectiveProfiles:[{...profile}]
});
const button=(root,label)=>root.root.findAllByType('button').find(node=>node.children.includes(label));
const edit=(root,label,value)=>act(async()=>root.root.findByProps({'aria-label':label}).props.onChange({target:{value}}));
const noClaims=root=>assert.doesNotMatch(JSON.stringify(root.toJSON()),/Cooperative reservations|held and uncertain claims|lock physical infrastructure/i);

for(const retiredKeysPresent of [false,true]){
 test(`execution settings omit retired claim quotas and preserve other save values (${retiredKeysPresent?'retained keys supplied':'retired keys absent'})`,async()=>{
  let root,data=initial();const calls=[];
  // Probe stale input too: no hidden draft or later save may resurrect these keys.
  if(retiredKeysPresent){
   data.concurrency.maxResourceReservations=8;data.concurrency.maxResourceReservationsPerChat=2;
   data.effectiveConcurrency.maxResourceReservations=8;data.effectiveConcurrency.maxResourceReservationsPerChat=2;
  }
  const dispatch=async(name,args)=>{
   calls.push([name,args]);
   assert.ok(['testing.execution.read','testing.execution.save'].includes(name));
   if(name==='testing.execution.save')data={...initial(),...args,revision:8};
   return {accepted:true,result:data};
  };
  try{
   await act(async()=>{root=create(React.createElement(ExecutionLimits,{act:dispatch}))});
   noClaims(root);assert.equal(button(root,'Save execution limits').props.disabled,true);
   assert.equal(root.root.findByProps({'aria-label':'Pending messages and reserved replies per chat'}).props.value,'0');
   await edit(root,'Owned resources across this installation','21');
   await edit(root,'Python: CPU cores','4');
   const queueFields=root.root.findAllByType('fieldset').find(node=>node.findByType('legend').children.includes('Queue'));
   await act(async()=>queueFields.findAllByType('input')[0].props.onChange({target:{value:'15'}}));
   assert.equal(button(root,'Save execution limits').props.disabled,false);
   assert.equal(calls.length,1,'editing alone does not save');
   await act(async()=>button(root,'Save execution limits').props.onClick());
   assert.deepEqual(calls[1],['testing.execution.save',{
    expectedRevision:7,concurrency:{...concurrency,maxOwnedResources:21},queue:{...queue,maxPending:15},profiles:{'linux-python':{cpus:4}}
   }]);
   noClaims(root);assert.equal(button(root,'Save execution limits').props.disabled,true);
   await edit(root,'Owned resources across this installation','22');
   await act(async()=>button(root,'Discard execution edits and refresh').props.onClick());
   assert.equal(root.root.findByProps({'aria-label':'Owned resources across this installation'}).props.value,'21');
   assert.equal(button(root,'Save execution limits').props.disabled,true);
   assert.deepEqual(calls.map(([name])=>name),['testing.execution.read','testing.execution.save','testing.execution.read']);
  }finally{await act(async()=>root?.unmount())}
 });
}

test('a lost execution-save acknowledgement reads normalized settings without repeating the save',async()=>{
 let root,data=initial();const calls=[];
 const dispatch=async(name,args)=>{
  calls.push([name,args]);
  if(name==='testing.execution.save'){
   data={...data,...args,revision:8};
   throw Error('Save response lost');
  }
  assert.equal(name,'testing.execution.read');
  return {accepted:true,result:data};
 };
 try{
  await act(async()=>{root=create(React.createElement(ExecutionLimits,{act:dispatch}))});
  await edit(root,'Owned resources per chat','6');
  await act(async()=>button(root,'Save execution limits').props.onClick());
  assert.deepEqual(calls.map(([name])=>name),['testing.execution.read','testing.execution.save','testing.execution.read']);
  assert.match(JSON.stringify(root.toJSON()),/The save was not repeated/);
  assert.equal(Object.hasOwn(calls[1][1].concurrency,'maxResourceReservations'),false);
  assert.equal(Object.hasOwn(calls[1][1].concurrency,'maxResourceReservationsPerChat'),false);
  assert.equal(button(root,'Save execution limits').props.disabled,true);
  noClaims(root);
 }finally{await act(async()=>root?.unmount())}
});