import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkProjectionStatus}=await server.ssrLoadModule('/src/work-projection-status.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('cold loading and retry remain visible, then disappear after recovery without hiding conversation',async()=>{
 let root;
 const render=progress=>React.createElement('main',null,React.createElement('p',{id:'saved'},'Saved conversation'),React.createElement(WorkProjectionStatus,{session:{id:'selected',workProjection:progress}}));
 await act(async()=>{root=create(render({status:'loading'}))});
 assert.match(JSON.stringify(root.toJSON()),/Loading work details/);
 assert.equal(root.root.findAllByProps({role:'status'}).length,1);
 assert.equal(root.root.findAllByProps({'className':'a-progress-spinner'}).length,1);
 await act(async()=>root.update(render({status:'retrying',reason:'memory_limit'})));
 assert.match(JSON.stringify(root.toJSON()),/Retrying shortly/);
 assert.equal(root.root.findByProps({id:'saved'}).children.join(''),'Saved conversation');
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/memory_limit/);
 await act(async()=>root.update(render({status:'ready'})));
 assert.equal(root.root.findAllByProps({role:'status'}).length,0);
 assert.equal(root.root.findByProps({id:'saved'}).children.join(''),'Saved conversation');
 await act(async()=>root.unmount());
});
test('unrelated or unavailable metadata does not invent loading work',async()=>{
 let root;
 for(const progress of [undefined,{status:'ready'},{status:'future'}]){
  await act(async()=>{root=create(React.createElement(WorkProjectionStatus,{session:{workProjection:progress}}))});
  assert.equal(root.toJSON(),null);
  await act(async()=>root.unmount());
 }
});
test('a deterministic loading limit is unavailable without an endless retry spinner',async()=>{
 let root;
 await act(async()=>{root=create(React.createElement(WorkProjectionStatus,{session:{workProjection:{status:'unavailable',reason:'output_limit'}}}))});
 assert.match(JSON.stringify(root.toJSON()),/loading limit/);
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/Retrying|output_limit/);
 assert.equal(root.root.findAllByProps({'className':'a-progress-spinner'}).length,0);
 await act(async()=>root.unmount());
});
