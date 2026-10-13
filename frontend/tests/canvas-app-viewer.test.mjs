import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.location={origin:'http://fixture.invalid'};
globalThis.document={head:{},documentElement:{},getElementById:()=>null};
globalThis.getComputedStyle=()=>({getPropertyValue:()=>'',colorScheme:'light'});
globalThis.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
globalThis.MutationObserver=class {observe(){} disconnect(){}};
globalThis.requestAnimationFrame=()=>1;globalThis.cancelAnimationFrame=()=>{};
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {CanvasAppViewer}=await server.ssrLoadModule('/src/canvas-app-viewer.jsx');
const {inlineOwnership}=await server.ssrLoadModule('/src/conversation-navigation.js');
test.after(()=>server.close());
async function fixture(dispatcher,initial={}){
 const callbacks=new Map(),out=[],calls=[],contentWindow={postMessage:data=>out.push(data)};
 globalThis.window={addEventListener:(name,fn)=>callbacks.set(name,fn),removeEventListener:name=>callbacks.delete(name)};
 let canvas={id:'fixture',sessionId:'session',title:'Test',viewId:'primary',resourceId:'fixture',resourceRevision:'r1',generation:0,app:{revision:1,stateRevision:0,state:{tab:'draw'},manifest:{},versions:[],requests:[]},...initial},component;
 const dispatch=async(name,args)=>{calls.push({name,args});const receipt=dispatcher&&await dispatcher(name,args);return receipt??{accepted:true,result:{app:{...canvas.app,stateRevision:canvas.app.stateRevision+1}}}};
 await act(async()=>{component=create(React.createElement(CanvasAppViewer,{canvas,dispatch}),{createNodeMock:node=>node.type==='iframe'?{contentWindow}:null})});
 const channel=out.at(-1).channel;
 const send=async data=>act(async()=>callbacks.get('message')({source:contentWindow,data:{type:'canvas-app',id:'fixture',channel,revision:1,stateRevision:0,editVersion:1,...data}}));
 return {component,calls,out,send,dirty:()=>JSON.stringify(component.toJSON()).includes('Unsaved input'),
  update:async patch=>act(async()=>{canvas={...canvas,...patch};component.update(React.createElement(CanvasAppViewer,{canvas,dispatch}))}),
  close:()=>act(async()=>component.unmount())};
}
test('unrelated events never clear custom edits; only the acknowledged edit is committed',async()=>{
 const f=await fixture();try{
  await f.send({op:'editing',editVersion:1});assert.ok(f.dirty());
  await f.send({op:'event',requestId:'tab',args:{name:'setTab',payload:{tab:'clock'}}});assert.ok(f.dirty());
  await f.send({op:'editing',editVersion:2});
  await f.send({op:'state',requestId:'old',args:{patch:{strokes:[]}},commit:1});assert.ok(f.dirty());
  await f.send({op:'state',requestId:'save',editVersion:2,args:{patch:{strokes:[]}},commit:2});assert.equal(f.dirty(),false);
 }finally{await f.close()}
});

test('native inline state, event and request carry the actual target and stable request identity',async()=>{
 const target={viewId:'inline-app',resourceId:'fixture',resourceRevision:'saved-r1',generation:4};
 const f=await fixture(undefined,target);
 try{
  for(const op of ['state','event','request']){
   const requestId='exact-'+op,args=op==='state'?{patch:{tab:'clock'}}:op==='event'?{name:'choose',payload:{value:'clock'}}:{name:'preview',input:{}};
   await f.send({op,requestId,args});
   const call=f.calls.find(row=>row.name==='canvas.apps.'+op);
   assert.deepEqual(Object.fromEntries(Object.keys(target).map(field=>[field,call.args[field]])),target);
   assert.equal(call.args.id,'fixture');assert.equal(call.args.sessionId,'session');
   assert.equal(call.args.expectedRevision,1);assert.equal(call.args.expectedStateRevision,0);
   await f.send({op,requestId,args});
   assert.equal(f.calls.filter(row=>row.name==='canvas.apps.'+op).length,1,'Duplicate iframe request never reruns host work');
  }
 }finally{await f.close()}
});

test('numbered native app remains read-only even at the latest revision; another live view can still edit',async()=>{
 const historical=await fixture(undefined,{viewId:'inline-history',selectedVersion:1,readOnlyVersion:true});
 try{
  for(const op of ['state','event','request','dirty'])await historical.send({op,requestId:'blocked-'+op,args:{patch:{tab:'wrong'}}});
  await historical.send({op:'editing'});
  assert.equal(historical.calls.length,0);assert.equal(historical.dirty(),false);
  assert.match(historical.out.at(-1).error,/read-only/);
 }finally{await historical.close()}
 const live=await fixture(undefined,{viewId:'inline-live',selectedVersion:null,readOnlyVersion:false});
 try{await live.send({op:'state',requestId:'allowed',args:{patch:{tab:'clock'}}});assert.equal(live.calls[0].name,'canvas.apps.state');assert.equal(live.calls[0].args.viewId,'inline-live')}
 finally{await live.close()}
});

test('dirty native app retains its iframe/channel when a canonical design advances; refused discard retains conflict',async()=>{
 const f=await fixture(async(name,args)=>name==='canvas.views.dirty'&&!args.dirty?{accepted:false,error:'Retain unfinished input'}:undefined,{viewId:'inline-live'});
 const nextTarget={viewId:'inline-live',resourceId:'fixture',resourceRevision:'r2',generation:2};
 const unregister=inlineOwnership.register({},nextTarget,{sessionId:'session',messageId:'publication',version:1});
 try{
  await f.send({op:'editing',editVersion:1});
  const oldFrame=f.component.root.findByType('iframe').props.src,oldChannel=f.out.at(-1).channel;
  await f.update({app:{revision:2,stateRevision:4,state:{tab:'new'},manifest:{},versions:[],requests:[]},resourceRevision:'r2',generation:2});
  assert.equal(f.component.root.findByType('iframe').props.src,oldFrame);
  assert.equal(f.out.at(-1).channel,oldChannel);assert.equal(f.dirty(),true);
  assert.equal(inlineOwnership.blocksTarget(nextTarget),true,'The new binding retains local leave protection, without redirecting old frame operations');
  const discard=f.component.root.findAllByType('button').find(node=>String(node.props.children).includes('Discard unfinished'));
  await act(async()=>discard.props.onClick());
  assert.equal(f.dirty(),true);assert.equal(f.component.root.findByType('iframe').props.src,oldFrame);
  assert.ok(JSON.stringify(f.component.toJSON()).includes('Retain unfinished input'));
 }finally{unregister();await f.close()}
});
test('failed save keeps dirty input and rendering errors survive successful unrelated operations',async()=>{
 const f=await fixture(async name=>{if(name==='canvas.apps.state')throw Error('Conflict')});try{
  await f.send({op:'editing'});await f.send({op:'state',requestId:'save',commit:1,args:{patch:{}}});assert.ok(f.dirty());
  await f.send({op:'status',status:'error',message:'Negative radius'});
  await f.send({op:'dirty',requestId:'dirty',args:{dirty:true}});
  assert.ok(JSON.stringify(f.component.toJSON()).includes('Negative radius'));
  await f.send({op:'status',status:'ready',message:'Repaired'});assert.equal(f.component.root.findAllByProps({role:'alert'}).length,0);
 }finally{await f.close()}
});
test('duplicate snapshots and render statuses are not republished',async()=>{
 const f=await fixture();try{
  const before=f.out.length;await f.update({});assert.equal(f.out.length,before);
  await f.send({op:'status',status:'error',message:'Broken'});await f.send({op:'status',status:'error',message:'Broken'});
  assert.equal(f.calls.filter(c=>c.name==='canvas.views.status').length,1);
 }finally{await f.close()}
});

test('CanvasApp editing intent protects ownership before its serialized host declaration settles',async()=>{
 let resolveDirty;
 const view={viewId:'primary',resourceId:'fixture',resourceRevision:'r1',generation:0};
 const unregister=inlineOwnership.register({},view,{sessionId:'session',messageId:'publication',version:1});
 const f=await fixture(async(name,args)=>{if(name==='canvas.views.dirty'&&args.dirty)await new Promise(resolve=>{resolveDirty=resolve})});
 try{
  await f.send({op:'editing',editVersion:1});
  assert.equal(inlineOwnership.blocksWindow('session',[]),true);
  assert.equal(inlineOwnership.blocksWindow('session',[{id:'publication'}]),false);
  // Rejected/pending declarations never grant leave permission.
  assert.equal(f.dirty(),true);resolveDirty();await act(async()=>{await Promise.resolve()});
  await f.send({op:'dirty',requestId:'clean',args:{dirty:false},editVersion:1});
  assert.equal(inlineOwnership.dirty(),false);
 }finally{resolveDirty?.();unregister();await f.close()}
});

test('a refused CanvasApp clean declaration cannot admit a newer design over private input',async()=>{
 const view={viewId:'primary',resourceId:'fixture',resourceRevision:'r1',generation:0};
 const unregister=inlineOwnership.register({},view,{sessionId:'session',messageId:'publication',version:1});
 const f=await fixture(async(name,args)=>name==='canvas.views.dirty'&&!args.dirty?{accepted:false,error:'Clean declaration rejected'}:undefined);
 try{
  await f.send({op:'editing',editVersion:1});
  await f.send({op:'dirty',requestId:'clean',args:{dirty:false},editVersion:1});
  assert.equal(f.dirty(),true);assert.equal(inlineOwnership.dirty(),true);
  await f.update({app:{revision:2,stateRevision:0,state:{},manifest:{},versions:[],requests:[]}});
  assert.ok(JSON.stringify(f.component.toJSON()).includes('A new design is ready.'));
  assert.ok(JSON.stringify(f.component.toJSON()).includes('Clean declaration rejected'));
 }finally{unregister();await f.close()}
});
