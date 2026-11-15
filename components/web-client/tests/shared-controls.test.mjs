import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ArtifactRuntime}=await server.ssrLoadModule('/src/artifact-runtime.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

test('document action group retains independent inspection intents and pending locks',async()=>{
 const calls=[];let root,settle,completion;
 const dispatch=(name,args)=>{calls.push({name,args});return new Promise(resolve=>{settle=resolve})};
 await act(async()=>{root=create(React.createElement(ArtifactRuntime,{sessionId:'selected',act:dispatch}))});
 try{
  const group=()=>root.root.findByProps({className:'a-control-actions'});
  const buttons=()=>group().findAllByType('button');
  assert.equal(buttons().length,2);
  assert.equal(calls.length,0,'Mount is passive');
  await act(async()=>{completion=buttons()[0].props.onClick()});
  assert.deepEqual(calls,[{name:'runtime.dependencies',args:{sessionId:'selected'}}]);
  assert.ok(buttons().every(button=>button.props.disabled));
  await act(async()=>{settle({result:{}});await completion});
  assert.ok(buttons().every(button=>!button.props.disabled));
  await act(async()=>{completion=buttons()[1].props.onClick()});
  assert.deepEqual(calls.at(-1),{name:'runtime.dependencies',args:{sessionId:'selected',verifyImports:true}});
  await act(async()=>{settle({result:{}});await completion});
 }finally{await act(async()=>root.unmount())}
});