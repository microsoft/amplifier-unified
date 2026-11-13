import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ExternalAgentPicker}=await server.ssrLoadModule('/src/external-agent-picker.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const saved={id:'remote',label:'My Instance',revision:'saved-revision'};
const base={client:{hostInstanceId:'host'},view:{newSessionDraft:{workspace:'/chosen',location:{kind:'workspace'},title:'Keep me',selection:{model:'private-draft'}}}};
const result=agents=>({accepted:true,result:{agents}});
const button=(root,text)=>root.root.findAllByType('button').find(node=>node.children.join('')===text);

test('catalog choice updates only the private draft id and never launches or discovers a provider',async()=>{
 let root;const calls=[];
 const dispatch=async(action,args)=>{calls.push([action,args]);return action==='externalAgents.list'?result([saved]):{accepted:true}};
 try{
  await act(async()=>{root=create(React.createElement(ExternalAgentPicker,{state:base,act:dispatch}))});
  assert.deepEqual(calls,[['externalAgents.list',{}]]);
  assert.deepEqual(root.root.findAllByType('option').map(node=>node.children.join('')),['Amplifier','My Instance']);
  await act(async()=>root.root.findByType('select').props.onChange({target:{value:'remote'}}));
  assert.deepEqual(calls[1],['view.update',{patch:{newSessionDraft:{...base.view.newSessionDraft,externalAgentId:'remote'}}}]);
  assert.equal(root.root.findByType('select').props.value,'','selection follows the acknowledged parent draft');
  assert.equal(calls.length,2);
 }finally{await act(async()=>root?.unmount())}
});

test('a missing saved choice stays unavailable until the user explicitly chooses Amplifier',async()=>{
 let root;const calls=[],state={...base,view:{newSessionDraft:{...base.view.newSessionDraft,externalAgentId:'removed'}}};
 const dispatch=async(action,args)=>{calls.push([action,args]);return action==='externalAgents.list'?result([]):{accepted:true}};
 try{
  await act(async()=>{root=create(React.createElement(ExternalAgentPicker,{state,act:dispatch}))});
  assert.equal(root.root.findByType('select').props.value,'removed');
  assert.equal(root.root.findAllByType('option').find(node=>node.props.value==='removed').children.join(''),'removed — unavailable');
  assert.equal(calls.length,1);
  await act(async()=>root.root.findByType('select').props.onChange({target:{value:''}}));
  assert.deepEqual(calls[1][1].patch.newSessionDraft,{...state.view.newSessionDraft,externalAgentId:''});
 }finally{await act(async()=>root?.unmount())}
});

test('refresh and host changes ignore a late catalog and never replace the selected draft',async()=>{
 let root;const requests=[];
 const dispatch=(action,args)=>new Promise(resolve=>requests.push({action,args,resolve}));
 const state={...base,view:{newSessionDraft:{externalAgentId:'remote'}}};
 const element=(value,refreshKey)=>React.createElement(ExternalAgentPicker,{state:value,act:dispatch,refreshKey});
 try{
  await act(async()=>{root=create(element(state,0))});
  await act(async()=>root.update(element(state,1)));
  await act(async()=>requests[1].resolve(result([saved])));
  await act(async()=>requests[0].resolve(result([{id:'stale',label:'Stale agent'}])));
  assert.match(JSON.stringify(root.toJSON()),/My Instance/);assert.doesNotMatch(JSON.stringify(root.toJSON()),/Stale agent/);
  await act(async()=>root.update(element({...state,client:{hostInstanceId:'other'}},1)));
  assert.equal(root.root.findByType('select').props.disabled,true);
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/My Instance/);
  await act(async()=>requests[2].resolve(result([])));
  assert.equal(root.root.findByType('select').props.value,'remote');
  assert.equal(root.root.findAllByType('option').find(node=>node.props.value==='remote').children.join(''),'remote — unavailable');
  assert.equal(requests.length,3);assert.ok(requests.every(row=>row.action==='externalAgents.list'));
 }finally{await act(async()=>root?.unmount())}
});

test('catalog errors are retryable and refused draft changes preserve the current selection',async()=>{
 let root,reads=0;const state={...base,view:{newSessionDraft:{externalAgentId:'remote'}}};
 const dispatch=async action=>action==='externalAgents.list'?(++reads===1?{accepted:false,error:'Catalog unavailable'}:result([saved])):{accepted:false,error:'Draft changed'};
 try{
  await act(async()=>{root=create(React.createElement(ExternalAgentPicker,{state,act:dispatch}))});
  assert.match(JSON.stringify(root.toJSON()),/Catalog unavailable/);
  assert.equal(root.root.findByType('select').props.value,'remote');
  await act(async()=>button(root,'Retry').props.onClick());
  assert.equal(reads,2);assert.match(JSON.stringify(root.toJSON()),/My Instance/);
  await act(async()=>root.root.findByType('select').props.onChange({target:{value:''}}));
  assert.match(JSON.stringify(root.toJSON()),/Draft changed/);
  assert.equal(root.root.findByType('select').props.value,'remote');
 }finally{await act(async()=>root?.unmount())}
});

test('disabled picker cannot change a draft or open settings; configuration navigation is otherwise explicit',async()=>{
 let root;const calls=[];
 const dispatch=async(action,args)=>{calls.push([action,args]);return action==='externalAgents.list'?result([saved]):{accepted:true}};
 const element=disabled=>React.createElement(ExternalAgentPicker,{state:base,act:dispatch,disabled});
 try{
  await act(async()=>{root=create(element(true))});
  await act(async()=>root.root.findByType('select').props.onChange({target:{value:'remote'}}));
  await act(async()=>button(root,'Configure external agents').props.onClick());
  assert.equal(calls.length,1);
  await act(async()=>root.update(element(false)));
  await act(async()=>button(root,'Configure external agents').props.onClick());
  assert.equal(calls.length,2);assert.equal(calls[1][0],'view.update');
  assert.deepEqual(calls[1][1].patch.settingsExpanded,['external-agents']);
 }finally{await act(async()=>root?.unmount())}
});
