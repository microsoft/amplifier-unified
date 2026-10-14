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
const {McpAppVisibilityProvider,MCP_VISIBILITY,mcpConnectionKey}=await server.ssrLoadModule('/src/mcp-app-lifecycle.jsx');
test.after(()=>server.close());
const response=value=>({ok:true,status:200,text:async()=>JSON.stringify(value)});
const available={source:'available',status:'ready',message:'Tool view connected',canReconnect:true,bindingRevision:'saved-binding'};
test.beforeEach(()=>{globalThis.fetch=async path=>{assert.ok(path.endsWith('/status'));return response(available)}});

test('new and legacy launches initialize only through exact saved-result boundary; missing retained results are visible',async()=>{
 for(const kind of ['new','legacy','missing']){
  const target={viewId:'inline-saved',resourceId:'saved',resourceRevision:'r1',generation:2};
  const canvas={...target,id:'saved',open:true,mcp:{operationId:'prior-launch',...(kind==='new'?{savedResult:{$resource:'saved-result'}}:{})}};
  const paths=[],delivered=[],reports=[];let component;
  globalThis.fetch=async path=>{
   paths.push(path);const url=new URL(path,'http://fixture.invalid');
   for(const [key,value] of Object.entries(target))assert.equal(url.searchParams.get(key),String(value));
   if(url.pathname.endsWith('/status'))return response(available);
   assert.equal(url.pathname,'/api/canvas/saved/result');
   return response({result:kind==='missing'?null:{structuredContent:{saved:'launch'}}});
  };
  await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas,act:async(name,args)=>reports.push({name,args})}),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
  const bridge=mcpTestBridges.at(-1);bridge.sendToolResult=async result=>delivered.push(result);
  try{
   await act(async()=>bridge.oninitialized());
   assert.equal(paths.filter(path=>new URL(path,'http://fixture.invalid').pathname.endsWith('/result')).length,1);
   assert.equal(paths.some(path=>path.includes('/operations/')||path.includes('/tools/call')),false);
   if(kind==='missing'){
    assert.equal(delivered.length,0);
    assert.ok(JSON.stringify(component.toJSON()).includes('saved launch result is unavailable'));
    assert.ok(reports.some(row=>row.args.status==='error'));
   }else assert.deepEqual(delivered,[{structuredContent:{saved:'launch'}}]);
  }finally{await act(async()=>component.unmount())}
 }
});

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
  if(path.endsWith('/status'))return response(available);
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
  if(path.endsWith('/status'))return response(available);
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
  if(path.endsWith('/status'))return response(available);
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
  if(path.endsWith('/status'))return response(available);
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
test('missing saved source is reported before frame navigation or a misleading handshake timeout',async()=>{
 globalThis.fetch=async()=>response({...available,source:'unavailable',status:'source_unavailable',canReconnect:false,message:'Saved source is unavailable; its reference remains.'});
 const frame={contentWindow:{}},before=mcpTestBridges.length,reports=[];let component;
 await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:'missing',open:true},act:async(name,args)=>reports.push(args)}),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 try{
  assert.equal(mcpTestBridges.length,before);assert.equal(frame.src,undefined);
  assert.equal(reports.at(-1).message,'Saved source is unavailable; its reference remains.');
  assert.equal(component.root.findByProps({'aria-label':'Reconnect tool view'}).props.disabled,true);
 }finally{await act(async()=>component.unmount())}
});
test('explicit reconnect uses the reviewed binding and retains the mounted frame and input',async()=>{
 const frame={contentWindow:{},draft:'unsaved'},sent=[];let component;
 globalThis.fetch=async(path,options)=>{
  if(path.endsWith('/status'))return response({...available,status:'disconnected',message:'Saved source available; reconnect.'});
  if(path==='/api/actions'){sent.push(JSON.parse(options.body));return response({operationId:'reconnect-receipt'})}
  assert.equal(path,'/api/smart-tools/operations/reconnect-receipt');return response({status:'completed',result:available});
 };
 await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas:{id:'retained',open:true,mcp:{}},act:async()=>({})}),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1),count=mcpTestBridges.length,src=frame.src;
 try{
  await act(async()=>bridge.oninitialized());
  await act(async()=>component.root.findByProps({'aria-label':'Reconnect tool view'}).props.onClick());
  assert.equal(sent.length,1);assert.equal(sent[0].action,'smartTools.reconnectView');
  assert.deepEqual(sent[0].args,{canvasId:'retained',expectedBindingRevision:'saved-binding'});
  assert.equal(mcpTestBridges.length,count);assert.equal(frame.src,src);assert.equal(frame.draft,'unsaved');
  assert.equal(component.root.findByProps({'data-phase':'ready'}).props.role,'status');
 }finally{await act(async()=>component.unmount())}
});

test('every native MCP read, call, context, reconnect and receipt poll retains the full inline target',async()=>{
 const target={viewId:'inline-mcp',resourceId:'saved-mcp',resourceRevision:'definition-2',generation:6};
 const canvas={...target,id:'saved-mcp',open:true,revision:2,mcp:{serverId:'fixture-server',operationId:'launch',savedResult:true,toolArguments:{amount:7}}};
 const calls=[],reports=[],frame={contentWindow:{}};let component;
 globalThis.fetch=async(path,options={})=>{
  const url=new URL(path,'http://fixture.invalid');calls.push({url,body:options.body&&JSON.parse(options.body)});
  if(url.pathname==='/api/actions'){
   const body=JSON.parse(options.body);
   assert.deepEqual(Object.fromEntries(Object.keys(target).map(field=>[field,body.args[field]])),target);
   return response(body.action==='smartTools.reconnectView'?{operationId:'reconnect'}:{});
  }
  for(const [field,value] of Object.entries(target))assert.equal(url.searchParams.get(field),String(value),path);
  if(url.pathname.endsWith('/status'))return response(available);
  if(url.pathname.endsWith('/result'))return response({result:{structuredContent:{count:7}}});
  if(url.pathname.endsWith('/tools'))return response({tools:[{name:'counter_add'}]});
  if(url.pathname.endsWith('/resources'))return response({resources:[]});
  if(url.pathname.endsWith('/tools/call'))return response({status:'completed',result:{structuredContent:{count:8}}});
  if(url.pathname.endsWith('/reconnect'))return response({status:'completed',result:available});
  throw Error('Unexpected frontend route: '+path);
 };
 await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas,act:async(name,args)=>reports.push({name,args})}),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const bridge=mcpTestBridges.at(-1);
 try{
  const source=new URL(frame.src,'http://fixture.invalid');
  for(const [field,value] of Object.entries(target))assert.equal(source.searchParams.get(field),String(value));
  await act(async()=>bridge.oninitialized());
  await act(async()=>bridge.onlisttools());
  await act(async()=>bridge.onreadresource({uri:'fixture://read'}));
  await act(async()=>bridge.onlistresources({}));
  await act(async()=>bridge.onlistresourcetemplates({}));
  await act(async()=>bridge.onupdatemodelcontext({structuredContent:{visible:'inline fixture'}}));
  await act(async()=>bridge.oncalltool({name:'counter_add',arguments:{amount:1}}));
  await act(async()=>component.root.findByProps({'aria-label':'Reconnect tool view'}).props.onClick());
  assert.equal(calls.filter(row=>row.url.pathname.endsWith('/tools/call')).length,1,'Mount/status/result/reconnect do not launch');
  assert.equal(calls.filter(row=>row.body?.action==='smartTools.context').length,1);
  assert.equal(calls.filter(row=>row.body?.action==='smartTools.reconnectView').length,1);
  assert.ok(reports.every(row=>row.args.id===canvas.id));
 }finally{await act(async()=>component.unmount())}
});

test('historical MCP handlers refuse writes and context; late old generation cannot call or report through the new bridge',async()=>{
 const sent=[],reports=[],frame={contentWindow:{}};let component;
 globalThis.fetch=async(path,options={})=>{
  const url=new URL(path,'http://fixture.invalid');
  if(url.pathname.endsWith('/status'))return response(available);
  if(url.pathname.endsWith('/tools'))return response({tools:[]});
  sent.push({path,options});return response({status:'completed',result:{content:[]}});
 };
 let canvas={id:'saved',viewId:'inline',resourceId:'saved',resourceRevision:'r1',generation:1,readOnlyVersion:true,selectedVersion:1,open:true};
 const render=()=>React.createElement(McpAppViewer,{canvas,act:async(name,args)=>reports.push({name,args})});
 await act(async()=>{component=create(render(),{createNodeMock:node=>node.type==='iframe'?frame:null})});
 const old=mcpTestBridges.at(-1);
 try{
  await act(async()=>assert.rejects(old.oncalltool({name:'counter_add'}),/read-only/));
  await act(async()=>old.onupdatemodelcontext({content:[]}));
  assert.equal(sent.length,0);
  canvas={...canvas,resourceRevision:'r2',generation:2,readOnlyVersion:false,selectedVersion:null};
  await act(async()=>component.update(render()));
  assert.equal(old.closed,true);
  const before=reports.length;
  await assert.rejects(old.oncalltool({name:'counter_add'}),/generation closed/);
  await assert.rejects(old.onupdatemodelcontext({content:[]}),/generation closed/);
  assert.throws(()=>old.onreadresource({uri:'fixture://old'}),/generation closed/);
  await old.oninitialized();
  assert.equal(reports.length,before);assert.equal(sent.length,0);
  await act(async()=>mcpTestBridges.at(-1).oncalltool({name:'counter_add'}));
  assert.equal(sent.length,1);
  assert.equal(new URL(sent[0].path,'http://fixture.invalid').searchParams.get('generation'),'2');
 }finally{await act(async()=>component.unmount())}
});

test('closing one native MCP view closes only its bridge; its sibling reacts to its own server key without launch',async()=>{
 const requests=[],frames=new Map();let component;
 globalThis.fetch=async path=>{requests.push(path);return response(available)};
 const a={id:'one',title:'one',viewId:'inline-one',resourceId:'one',resourceRevision:'r1',generation:1,open:true,mcp:{serverId:'server-one'}};
 const b={...a,id:'two',title:'two',viewId:'inline-two',resourceId:'two',mcp:{serverId:'server-two'}};
 let keepA=true,smartTools={servers:[{id:'server-one',status:'connected'},{id:'server-two',status:'connected'}]};
 const render=()=>React.createElement(React.Fragment,null,...[...(keepA?[a]:[]),b].map(canvas=>React.createElement(McpAppViewer,{key:canvas.viewId,canvas,connectionKey:mcpConnectionKey(canvas,smartTools),act:async()=>({})})));
 await act(async()=>{component=create(render(),{createNodeMock:node=>{
  if(node.type!=='iframe')return null;
  const frame={contentWindow:{},draft:'private-'+node.props.title};frames.set(node.props.title,frame);return frame;
 }})});
 const [first,second]=mcpTestBridges.slice(-2),count=mcpTestBridges.length,src=frames.get('two').src;
 try{
  keepA=false;await act(async()=>component.update(render()));
  assert.equal(first.closed,true);assert.equal(second.closed,undefined);
  const before=requests.length;
  smartTools={servers:[{id:'server-one',status:'connected'},{id:'server-two',status:'disconnected',account:{status:'changed'}}]};
  await act(async()=>component.update(render()));
  assert.equal(requests.length,before+1);
  assert.ok(requests.at(-1).includes('/two/status'));
  assert.equal(mcpTestBridges.length,count);assert.equal(frames.get('two').src,src);assert.equal(frames.get('two').draft,'private-two');
  assert.ok(requests.every(path=>new URL(path,'http://fixture.invalid').pathname.endsWith('/status')));
 }finally{await act(async()=>component.unmount())}
});

test('native MCP forwarding preserves host account, schema and grant refusals; reconnect is explicit and target qualified',async()=>{
 const sent=[];let reason='account identity changed',component;
 globalThis.fetch=async(path,options={})=>{
  const url=new URL(path,'http://fixture.invalid');
  if(url.pathname.endsWith('/status'))return response({...available,status:'account_changed',canReconnect:false,message:reason});
  if(url.pathname.endsWith('/tools'))return response({tools:[{name:'allowed'}]});
  sent.push({path,body:JSON.parse(options.body)});
  return {ok:false,status:409,text:async()=>JSON.stringify({error:reason})};
 };
 const canvas={id:'negative',viewId:'inline-negative',resourceId:'negative',resourceRevision:'r1',generation:2,open:true};
 await act(async()=>{component=create(React.createElement(McpAppViewer,{canvas,act:async()=>({})}),{createNodeMock:node=>node.type==='iframe'?{contentWindow:{}}:null})});
 const bridge=mcpTestBridges.at(-1);
 try{
  assert.equal(sent.length,0,'Status never reconnects or launches');
  for(const refusal of ['account identity changed','saved schema changed','tool grant missing']){
   reason=refusal;
   await act(async()=>assert.rejects(bridge.oncalltool({name:'allowed',arguments:{}}),new RegExp(refusal)));
  }
  assert.equal(sent.length,3,'Only the three explicit calls reach the host; none is retried');
  assert.ok(sent.every(row=>new URL(row.path,'http://fixture.invalid').searchParams.get('viewId')==='inline-negative'));
 }finally{await act(async()=>component.unmount())}
 // Mocked refusals pin frontend propagation, not real host security acceptance.
});
