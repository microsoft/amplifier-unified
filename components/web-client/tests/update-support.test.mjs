import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {UpdateSupport}=await server.ssrLoadModule('/src/update-support.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const report={schema:'amplifier-update-diagnostics-v1',runtime:{sourceChanges:[{package:'amplifier-fixture',recorded:{kind:'git'},installed:{kind:'editable'}}]}};
test('one click collects and copies a complete report through the shared action',async()=>{
 const calls=[],copied=[];let root;
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>copied.push(text)}}});
 await renderAct(async()=>{root=create(React.createElement(UpdateSupport,{state:{updates:{}},act:async(name,args)=>{calls.push([name,args]);return {accepted:true,result:report}}}))});
 await renderAct(async()=>root.root.findAll(n=>n.type==='button'&&n.props['data-action']==='updates.diagnostics')[0].props.onClick());
 assert.deepEqual(calls,[['updates.diagnostics',undefined]]);const value=JSON.parse(copied[0]);assert.deepEqual(value.runtime,report.runtime);assert.equal(value.client.updates.phase,'unknown');
 assert.match(JSON.stringify(root.toJSON()),/Diagnostics copied/);
 await renderAct(async()=>root.unmount());
});
test('blocked clipboard retains a report without changing installed sources',async()=>{
 const calls=[];let root;
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async()=>{throw Error('denied')}}}});
 const act=async(name,args)=>{calls.push([name,args]);return {accepted:true,result:report}};
 await renderAct(async()=>{root=create(React.createElement(UpdateSupport,{state:{updates:{phase:'error'}},act}))});
 assert.equal(calls.length,0);
 await renderAct(async()=>root.root.findAll(n=>n.type==='button'&&n.props['data-action']==='updates.diagnostics')[0].props.onClick());
 const value=JSON.parse(root.root.findByType('textarea').props.value);assert.deepEqual(value.runtime,report.runtime);assert.equal(value.client.updates.phase,'error');
 assert.deepEqual(calls,[['updates.diagnostics',undefined]]);
 await renderAct(async()=>root.unmount());
});

test('both padded diagnostic actions keep their identity and native pending state',async()=>{
 let root,release,completion;const calls=[],copied=[];
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>copied.push(text)}}});
 const act=(name,args)=>{calls.push([name,args]);return new Promise(resolve=>{release=resolve})};
 try{
  await renderAct(async()=>{root=create(React.createElement(UpdateSupport,{state:{updates:{}},act}))});
  const buttons=()=>root.root.findAllByType('button');
  assert.equal(buttons().length,2);
  assert.ok(buttons().every(button=>button.props.className==='a-soft'),'diagnostic actions stay buttons, not flush link paint');
  assert.ok(buttons().every(button=>button.findAllByType('svg').length===1&&button.findAllByType('span').length===1));
  assert.deepEqual(calls,[],'mount is passive');
  await renderAct(async()=>{completion=buttons()[0].props.onClick()});
  assert.ok(buttons().every(button=>button.props.disabled===true));
  assert.match(JSON.stringify(root.toJSON()),/Collecting diagnostics/);
  await renderAct(async()=>{release({accepted:true,result:report});await completion});
  assert.ok(buttons().every(button=>button.props.disabled===false));
  assert.deepEqual(calls,[['updates.diagnostics',undefined]]);
  assert.equal(copied.length,1);
 }finally{await renderAct(async()=>root?.unmount())}
});
