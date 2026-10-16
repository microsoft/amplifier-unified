import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {TaskEnvironments}=await server.ssrLoadModule('/src/workspace-test-environments.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalStorage=globalThis.sessionStorage,storage=new Map();
globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
test.beforeEach(()=>storage.clear());
test.after(()=>{globalThis.sessionStorage=originalStorage;return server.close()});
const key='amplifier-test-environment:w:a';
const accepted=result=>({accepted:true,result});
const page={items:[],capability:{status:'ready',profiles:[{id:'linux-python',label:'Python'}]}};
const text=node=>typeof node==='string'?node:(node?.children||[]).map(text).join('');
const button=(root,label)=>root.root.findAllByType('button').find(node=>text(node)===label);
const read={action:'workspace.environments.read',args:{workspaceId:'w',sessionId:'a'}};

for(const available of [true,false])test(`original environment cleanup remains ${available?'available':'blocked'} when new execution is unavailable`,async()=>{
 let root;const calls=[],resource={id:'env',revision:8,status:'stopped',recovery:{available,reason:'Original connection unavailable'}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?{items:[resource],capability:{status:'unavailable',reason:'New backend is unavailable'}}:{request:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  assert.equal(button(root,'Run check').props.disabled,true);
  assert.equal(button(root,'Remove environment…').props.disabled,!available);
  assert.equal(button(root,'Check and recover').props.disabled,!available);
  if(available){
   await act(async()=>button(root,'Check and recover').props.onClick());
   const mutation=calls.find(row=>row.action!=='workspace.environments.read');
   assert.equal(mutation.action,'workspace.environments.reconcile');assert.equal(mutation.args.id,'env');assert.equal(mutation.args.expectedRevision,8);
   assert.equal(calls.filter(row=>row.action!=='workspace.environments.read').length,1,'recovery must not run, stop or remove an environment');
  }else assert.match(text(root.toJSON()),/Original connection unavailable/);
 }finally{await act(async()=>root?.unmount())}
});

test('retained size is shown only from verified accounting and an uncertain operation stays visible',async()=>{
 let root;const props={workspaceId:'w',sessionId:'a',active:true};
 const resource={id:'env',revision:9,status:'removed',lastOperation:{state:'outcome_unknown'},lastRecovery:{state:'completed'},retention:{accounting:{verified:true,totalBytes:2048,format:'files'}}};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{...props,act:async()=>accepted({items:[resource]})}))});
  assert.match(text(root.toJSON()),/Saved files: 2 KiB/);assert.match(text(root.toJSON()),/Last request: outcome unknown/);assert.match(text(root.toJSON()),/Last recovery check: completed/);
  await act(async()=>root.unmount());resource.retention.accounting.verified=false;
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{...props,act:async()=>accepted({items:[resource]})}))});
  assert.match(text(root.toJSON()),/storage size has not been verified/);assert.doesNotMatch(text(root.toJSON()),/Saved files: 0/);
 }finally{await act(async()=>root?.unmount())}
});

for(const outcome of ['success','transport error','confirmed rejection'])test(`a mutation settled with ${outcome} while hidden releases controls on reopen without replay`,async()=>{
 let settle,root;const calls=[];
 const dispatch=(action,args,meta)=>{
  calls.push({action,args,...(meta?{meta}:{})});
  if(action==='workspace.environments.read')return Promise.resolve(accepted(page));
  if(calls.filter(row=>row.action!=='workspace.environments.read').length===1)return new Promise((resolve,reject)=>{settle=()=>outcome==='transport error'?reject(Error('Lost response')):resolve(outcome==='confirmed rejection'?{accepted:false,error:'Rejected'}:accepted({request:{state:'complete'}}))});
  return Promise.resolve(accepted({request:{state:'complete'}}));
 };
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>root.root.findByProps({'aria-label':'Start test environment'}).props.onSubmit({preventDefault(){}}));
  const saved=JSON.parse(storage.get(key)).pending,original=calls[1];
  assert.equal(saved.action,'workspace.environments.start');assert.equal(saved.args.requestId,original.args.requestId);assert.equal(saved.id,original.meta.id);
  assert.equal(button(root,'Refresh test environments').props.disabled,true);
  await act(async()=>root.update(React.createElement(TaskEnvironments,{...props,active:false})));
  await act(async()=>settle());
  assert.doesNotMatch(text(root.toJSON()),/Waiting for the environment request/);
  assert.equal(calls.length,2,'hidden completion does not refresh or replay');
  await act(async()=>root.update(React.createElement(TaskEnvironments,props)));
  assert.deepEqual(calls,[read,original,read],'reopen only refreshes the scoped observation');
  assert.equal(button(root,'Refresh test environments').props.disabled,false);
  if(outcome==='transport error'){
   assert.deepEqual(JSON.parse(storage.get(key)).pending,saved);
   assert.equal(button(root,'Start test environment').props.disabled,true);
   const retry=button(root,'Check the same environment request');assert.equal(Boolean(retry.props.disabled),false);
   await act(async()=>retry.props.onClick());
   assert.deepEqual(calls[3],original,'explicit recovery uses the same request identity and arguments');assert.deepEqual(calls[4],read);
  }else assert.equal(button(root,'Start test environment').props.disabled,false);
  assert.equal(JSON.parse(storage.get(key)).pending,null);
 }finally{await act(async()=>root?.unmount())}
});

for(const failed of [false,true])test(`a late environment ${failed?'error':'page'} cannot replace the reopened view`,async()=>{
 let settle,root;const calls=[];
 const dispatch=(action,args)=>{calls.push({action,args});if(calls.length===1)return new Promise((resolve,reject)=>{settle=()=>failed?reject(Error('STALE ERROR')):resolve(accepted({...page,capability:{status:'unavailable',reason:'STALE PAGE'}}))});return Promise.resolve(accepted(page))};
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>root.update(React.createElement(TaskEnvironments,{...props,active:false})));
  await act(async()=>root.update(React.createElement(TaskEnvironments,props)));
  await act(async()=>settle());
  assert.doesNotMatch(text(root.toJSON()),/STALE/);assert.equal(button(root,'Start test environment').props.disabled,false);assert.deepEqual(calls,[read,read]);
 }finally{await act(async()=>root?.unmount())}
});

for(const failed of [false,true])test(`a settled ${failed?'transport error':'success'} after unmount preserves recovery without replay on remount`,async()=>{
 let settle,root;const calls=[];
 const dispatch=(action,args,meta)=>{calls.push({action,args,...(meta?{meta}:{})});return action==='workspace.environments.read'?Promise.resolve(accepted(page)):new Promise((resolve,reject)=>{settle=()=>failed?reject(Error('Lost response')):resolve(accepted({request:{state:'complete'}}))})};
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>root.root.findByProps({'aria-label':'Start test environment'}).props.onSubmit({preventDefault(){}}));
  assert.ok(JSON.parse(storage.get(key)).pending);
  await act(async()=>root.unmount());await act(async()=>settle());
  assert.equal(Boolean(JSON.parse(storage.get(key)).pending),failed);assert.equal(calls.length,2);
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  assert.deepEqual(calls[2],read);assert.equal(calls.length,3);assert.equal(Boolean(button(root,'Start test environment').props.disabled),failed);
  if(failed){assert.equal(JSON.parse(storage.get(key)).pending.args.requestId,calls[1].args.requestId);assert.equal(Boolean(button(root,'Check the same environment request').props.disabled),false)}
 }finally{await act(async()=>root?.unmount())}
});

for(const rejected of [false,true])test(`an old mount's late ${rejected?'rejection':'success'} cannot clear a newer durable request`,async()=>{
 let root,settleOld,settleNew;const calls=[];
 const dispatch=(action,args,meta)=>{
  calls.push({action,args,...(meta?{meta}:{})});
  if(action==='workspace.environments.read')return Promise.resolve(accepted(page));
  const mutations=calls.filter(row=>row.action!=='workspace.environments.read');
  if(mutations.length===1)return new Promise(resolve=>{settleOld=()=>resolve(rejected?{accepted:false,error:'Original rejected'}:accepted({request:{state:'complete'}}))});
  if(mutations.length===2)return Promise.resolve(accepted({request:{state:'complete'}}));
  return new Promise(resolve=>{settleNew=()=>resolve(accepted({request:{state:'complete'}}))});
 };
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 const start=()=>root.root.findByProps({'aria-label':'Start test environment'}).props.onSubmit({preventDefault(){}});
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});await act(async()=>start());
  const original=JSON.parse(storage.get(key)).pending;await act(async()=>root.unmount());
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  assert.equal(calls.filter(row=>row.action!=='workspace.environments.read').length,1,'remount does not replay');
  await act(async()=>button(root,'Check the same environment request').props.onClick());
  assert.equal(calls.filter(row=>row.action!=='workspace.environments.read')[1].args.requestId,original.args.requestId);
  await act(async()=>start());const newer=JSON.parse(storage.get(key)).pending;assert.notEqual(newer.id,original.id);assert.notEqual(newer.args.requestId,original.args.requestId);
  await act(async()=>settleOld());assert.deepEqual(JSON.parse(storage.get(key)).pending,newer,'old completion preserves the newer recovery receipt');
  assert.equal(button(root,'Refresh test environments').props.disabled,true,'the new request still owns its pending controls');
  await act(async()=>settleNew());assert.equal(JSON.parse(storage.get(key)).pending,null);assert.equal(button(root,'Start test environment').props.disabled,false);
 }finally{await act(async()=>root?.unmount())}
});
