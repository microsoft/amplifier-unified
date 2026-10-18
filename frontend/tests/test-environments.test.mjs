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
 const resource={id:'env',revision:9,status:'removed',lastOperation:{state:'outcome_unknown'},lastRecovery:{state:'completed'},retention:{accounting:{verified:true,totalBytes:2048,format:'tar',policy:'files'}}};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{...props,act:async()=>accepted({items:[resource]})}))});
  assert.match(text(root.toJSON()),/Latest saved copy: 2 KiB/);assert.match(text(root.toJSON()),/Last request: outcome unknown/);assert.match(text(root.toJSON()),/Last recovery check: completed/);
  await act(async()=>root.unmount());resource.retention.accounting.verified=false;
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{...props,act:async()=>accepted({items:[resource]})}))});
  assert.match(text(root.toJSON()),/storage size has not been verified/);assert.doesNotMatch(text(root.toJSON()),/Latest saved copy: 0/);
 }finally{await act(async()=>root?.unmount())}
});

for(const full of [false,true])test(`removal keeps ${full?'an explicitly selected complete copy':'the smaller export by default'}`,async()=>{
 let root;const calls=[],resource={id:'env',revision:8,status:'stopped',recovery:{available:true},retention:{mode:'files',fullAvailable:true}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?{items:[resource],capability:{status:'unavailable'}}:{request:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>button(root,'Remove environment…').props.onClick());
  assert.equal(calls.length,1,'opening the choice has no backend effect');
  const checkbox=root.root.findByProps({type:'checkbox'});assert.equal(checkbox.props.checked,false);
  if(full)await act(async()=>checkbox.props.onChange({target:{checked:true}}));
  await act(async()=>button(root,'Save files and remove environment').props.onClick());
  const mutations=calls.filter(row=>row.action!=='workspace.environments.read');assert.equal(mutations.length,1);
  assert.equal(mutations[0].action,'workspace.environments.remove');assert.equal(mutations[0].args.retentionMode,full?'full':'files');
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

test('setup uses the same narrow host action and shows durable progress',async()=>{
 let root;const calls=[],setup={status:'disabled',reason:'Not set up',backends:[{id:'docker',label:'Docker',available:true,reason:'Available'}],operation:null};
 const dispatch=async(action,args)=>{calls.push({action,args});if(action!=='workspace.environments.read')setup.operation={id:'setup',state:'running',detail:'Downloading the approved base image.'};return accepted(action==='workspace.environments.read'?{items:[],capability:{status:'disabled'},setup}:{operation:setup.operation})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  assert.match(text(root.toJSON()),/already installed local test service/);
  await act(async()=>button(root,'Set up with Docker').props.onClick());
  const mutation=calls.find(row=>row.action!=='workspace.environments.read');
  assert.equal(mutation.action,'workspace.environments.setup');assert.equal(mutation.args.backend,'docker');assert.ok(mutation.args.requestId);
  assert.deepEqual(Object.keys(mutation.args).sort(),['backend','requestId','sessionId','workspaceId']);
  assert.match(text(root.toJSON()),/Downloading the approved base image/);assert.equal(button(root,'Set up with Docker').props.disabled,true);
 }finally{await act(async()=>root?.unmount())}
});

test('browser check profile uses its server budget and automatic preparation',async()=>{
 let root;const calls=[],view={items:[],capability:{status:'ready',profiles:[{id:'linux-python',label:'Python',defaultTimeoutSeconds:600},{id:'linux-browser',label:'Browser',defaultTimeoutSeconds:1200,preparationRequired:true}]}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?view:{request:{state:'queued'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  const form=root.root.findByProps({'aria-label':'Queue isolated check'});
  await act(async()=>form.findByType('select').props.onChange({target:{value:'linux-browser'}}));
  await act(async()=>form.findByType('textarea').props.onChange({target:{value:'npm test'}}));
  assert.match(text(root.toJSON()),/20-minute limit/);assert.match(text(root.toJSON()),/prepares this project’s locked dependencies/);
  await act(async()=>form.props.onSubmit({preventDefault(){}}));
  const mutation=calls.find(row=>row.action!=='workspace.environments.read');assert.equal(mutation.action,'workspace.tests.submit');
  assert.equal(mutation.args.profileId,'linux-browser');assert.equal(mutation.args.timeoutSeconds,1200);
 }finally{await act(async()=>root?.unmount())}
});

test('unsupported platform explains needs without a misleading setup button',async()=>{
 let root;const setup={status:'unavailable',reason:'Local setup is qualified on Linux.',backends:[{id:'docker',label:'Docker',available:false,reason:'Use a supported Linux host.'}]};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:async()=>accepted({items:[],setup,capability:{status:'unavailable'}}),active:true}))});
  assert.match(text(root.toJSON()),/Use a supported Linux host/);assert.equal(button(root,'Set up with Docker'),undefined);
 }finally{await act(async()=>root?.unmount())}
});

test('verified setup completion reuses original request and remains owner scoped',async()=>{
 let root;const calls=[],setup={status:'unavailable',requestId:'original',operation:{state:'outcome_unknown',canComplete:true,backend:'incus',sessionId:'a',workspaceId:'w'}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?{items:[],setup,capability:{status:'unavailable'}}:{operation:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>button(root,'Finish verified setup').props.onClick());
  const mutation=calls.find(row=>row.action==='workspace.environments.setup');
  assert.deepEqual(mutation.args,{workspaceId:'w',sessionId:'a',requestId:'original',backend:'incus'});
  await act(async()=>{root.update(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'other',act:dispatch,active:true}))});
  assert.equal(button(root,'Finish verified setup').props.disabled,true);
  assert.equal(button(root,'Check setup result').props.disabled,true);
  assert.equal(button(root,'Clean up interrupted setup').props.disabled,true);
 }finally{await act(async()=>root?.unmount())}
});
