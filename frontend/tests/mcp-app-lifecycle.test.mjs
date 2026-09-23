import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
import {createPendingView} from '../src/pending-view.js';
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.location={origin:'http://fixture.invalid'};
globalThis.mcpTestBridges=[];
const listeners=new Map();
globalThis.document={visibilityState:'visible',addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)};
globalThis.window={matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}})};
const server=await createServer({resolve:{alias:{'@modelcontextprotocol/ext-apps/app-bridge':fileURLToPath(new URL('./fixtures/mcp-app-bridge-stub.js',import.meta.url))}},server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {McpAppViewer}=await server.ssrLoadModule('/src/mcp-app-viewer.jsx');
const {McpAppVisibilityProvider,MCP_VISIBILITY}=await server.ssrLoadModule('/src/mcp-app-lifecycle.jsx');
test.after(()=>server.close());
const response=value=>({ok:true,status:200,text:async()=>JSON.stringify(value)});

test('canvas, Library and document visibility update context without remounting an edited iframe',async()=>{
 const frame={contentWindow:{},draft:'unsaved'},canvas={id:'view',open:true};let component,visible=true;
 const render=()=>React.createElement(McpAppVisibilityProvider,{visible},React.createElement(McpAppViewer,{canvas,act:async()=>({})}));
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1),initialSrc=frame.src,initialCount=mcpTestBridges.length;
 try{
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],true);
  canvas.open=false;await act(async()=>component.update(render()));assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  canvas.open=true;visible=false;await act(async()=>component.update(render()));assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  visible=true;await act(async()=>component.update(render()));assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],true);
  document.visibilityState='hidden';await act(async()=>listeners.get('visibilitychange')());assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  document.visibilityState='visible';await act(async()=>listeners.get('visibilitychange')());assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],true);
  assert.equal(mcpTestBridges.length,initialCount);assert.equal(frame.src,initialSrc);assert.equal(frame.draft,'unsaved');assert.equal(bridge.closed,undefined);
 }finally{await act(async()=>component.unmount())}
 assert.equal(bridge.closed,true);assert.equal(listeners.has('visibilitychange'),false);
});
test('optimistic restore pauses MCP reads until acknowledgement and retains the edited iframe on failure',async()=>{
 const pending=createPendingView(),reports=[],sent=[],frame={contentWindow:{},draft:'unsaved'};
 let state={selectedSessionId:'chat',client:{hostInstanceId:'host'},canvas:{id:'acknowledged',open:true},view:{}},component;
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/tools'))return response({tools:[{name:'read',annotations:{readOnlyHint:true}}]});
  assert.equal(state.canvas.open,true,'A tool call must not race the server visibility acknowledgement');
  sent.push(JSON.parse(options.body));return response({status:'completed',result:{content:[]}});
 };
 const render=()=>React.createElement(McpAppViewer,{canvas:pending.apply(state).canvas,act:async(name,args)=>reports.push({name,args})});
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1),initialSrc=frame.src,initialCount=mcpTestBridges.length;
 const update=()=>component.update(render());
 const begin=async open=>{let token;await act(async()=>{token=pending.addCanvas(state,open);update()});return token};
 const finish=async(token,open)=>act(async()=>{state={...state,canvas:{...state.canvas,open}};pending.settle(token);update()});
 try{
  await act(async()=>bridge.oncalltool({name:'read'}));assert.equal(sent.length,1);
  const hide=await begin(false);
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  await finish(hide,false);
  const restore=await begin(true);
  assert.equal(pending.apply(state).canvas.open,true,'The panel still paints before acknowledgement');
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false);
  await act(async()=>assert.rejects(bridge.oncalltool({name:'read'}),/paused/));
  assert.equal(sent.length,1,'No server request is admitted while restore is pending');
  await finish(restore,true);
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],true);
  await act(async()=>bridge.oncalltool({name:'read'}));assert.equal(sent.length,2);
  const hideAgain=await begin(false);await finish(hideAgain,false);
  const rejectedRestore=await begin(true);await finish(rejectedRestore,false);
  assert.equal(bridge.contexts.at(-1)[MCP_VISIBILITY],false,'A failed restore cannot enable reads');
  await act(async()=>assert.rejects(bridge.oncalltool({name:'read'}),/paused/));
  assert.equal(sent.length,2);assert.equal(reports.filter(row=>row.args.status==='error').length,0);
  assert.equal(mcpTestBridges.length,initialCount);assert.equal(frame.src,initialSrc);assert.equal(frame.draft,'unsaved');assert.equal(bridge.closed,undefined);
 }finally{await act(async()=>component.unmount())}
});
test('only explicitly read-only calls are coalesced; mutations and unknown tools retain separate IDs',async()=>{
 let release;const slow=new Promise(resolve=>release=resolve),sent=[];
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/tools'))return response({tools:[{name:'read',annotations:{readOnlyHint:true}},{name:'write',annotations:{readOnlyHint:false}}]});
  const body=JSON.parse(options.body);sent.push(body);if(body.name==='read')await slow;
  return response({status:'completed',result:{content:[]}});
 };
 let component;await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:'calls',open:true},act:async()=>({})}),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
 const bridge=mcpTestBridges.at(-1);
 try{
  let calls;await act(async()=>{
   calls=[bridge.oncalltool({name:'read'}),bridge.oncalltool({name:'read'}),bridge.oncalltool({name:'write'}),bridge.oncalltool({name:'write'}),bridge.oncalltool({name:'unknown'}),bridge.oncalltool({name:'unknown'})];
   await new Promise(resolve=>setImmediate(resolve));
  });
  assert.equal(sent.filter(row=>row.name==='read').length,1);
  assert.equal(sent.filter(row=>row.name==='write').length,2);
  assert.equal(sent.filter(row=>row.name==='unknown').length,2);
  assert.equal(new Set(sent.map(row=>row.id)).size,5);
  await act(async()=>{release();await Promise.all(calls)});
 }finally{await act(async()=>component.unmount())}
});
test('catalog failure preserves forwarding and request identity for unclassified tools',async()=>{
 const sent=[];
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/tools'))throw Error('Catalog temporarily unavailable');
  sent.push(JSON.parse(options.body));return response({status:'completed',result:{content:[]}});
 };
 let component;await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:'missing-catalog',open:true},act:async()=>({})}),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
 const bridge=mcpTestBridges.at(-1);
 try{
  await act(async()=>Promise.all([bridge.oncalltool({name:'save'}),bridge.oncalltool({name:'save'})]));
  assert.equal(sent.length,2);assert.notEqual(sent[0].id,sent[1].id);
 }finally{await act(async()=>component.unmount())}
});
test('expected paused or overloaded reads do not publish host errors or attention',async()=>{
 let release;const slow=new Promise(resolve=>release=resolve),reports=[],sent=[];
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/tools'))return response({tools:[{name:'read',annotations:{readOnlyHint:true}}]});
  sent.push(JSON.parse(options.body));await slow;return response({status:'completed',result:{content:[]}});
 };
 let component;const canvas={id:'suspended',open:false},actCommand=async(name,args)=>{reports.push({name,args})};
 const render=()=>React.createElement(McpAppViewer,{canvas,act:actCommand});
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
 const bridge=mcpTestBridges.at(-1);
 try{
  await act(async()=>assert.rejects(bridge.oncalltool({name:'read'}),/paused/));assert.equal(sent.length,0);
  canvas.open=true;await act(async()=>component.update(render()));
  let pending;await act(async()=>{
   pending=Array.from({length:6},(_,i)=>bridge.oncalltool({name:'read',arguments:{i}}));
   await assert.rejects(bridge.oncalltool({name:'read',arguments:{i:6}}),/Too many/);
  });
  assert.equal(sent.length,2);assert.equal(reports.filter(row=>row.args.status==='error').length,0);
  await act(async()=>{release();await Promise.all(pending)});
 }finally{await act(async()=>component.unmount())}
});
