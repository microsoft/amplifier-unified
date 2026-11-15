import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {StorageCleanupSettings}=await vite.ssrLoadModule('/src/storage-cleanup-settings.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
const policy={revision:1,enabled:false,keepDays:14,maxUnusedBytes:0,workspaceArchiveKeepDays:null,operationOutputKeepDays:null};

test('cleanup save, reload, discard and review share a bounded action group without changing draft semantics',async()=>{
 let root;const calls=[],storage=new Map();
 globalThis.sessionStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
 const dispatch=async(name,args)=>{calls.push({name,args});assert.equal(name,'storage.cleanup.read','this test never changes cleanup policy or deletes data');return {accepted:true,result:{policy}}};
 try{
  await act(async()=>{root=create(React.createElement(StorageCleanupSettings,{act:dispatch}))});
  const group=()=>root.root.findByProps({className:'a-dialog-actions a-control-actions'});
  const text=node=>node.children.join('');
  assert.deepEqual(group().findAllByType('button').map(text),['Save cleanup settings','Reload cleanup settings','Review unused copies']);
  await act(async()=>root.root.findAllByType('input').find(node=>node.props.type==='checkbox').props.onChange({target:{checked:true}}));
  assert.deepEqual(group().findAllByType('button').map(text),['Save cleanup settings','Reload cleanup settings','Discard edits and use saved settings','Review unused copies']);
  assert.equal(group().findAllByType('button')[0].props.disabled,false);
  await act(async()=>group().findAllByType('button').find(node=>text(node)==='Discard edits and use saved settings').props.onClick());
  assert.equal(root.root.findAllByType('input').find(node=>node.props.type==='checkbox').props.checked,false);
  assert.equal(group().findAllByType('button')[0].props.disabled,true);
  assert.deepEqual(calls,[{name:'storage.cleanup.read',args:{}}]);
 }finally{await act(async()=>root?.unmount())}
});