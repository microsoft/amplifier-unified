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
 const callbacks=new Map(),out=[],calls=[],contentWindow={postMessage:data=>out.push(data)},frameNode={contentWindow,inert:false};
 globalThis.window={addEventListener:(name,fn)=>callbacks.set(name,fn),removeEventListener:name=>callbacks.delete(name)};
 let canvas={id:'fixture',sessionId:'session',title:'Test',viewId:'primary',resourceId:'fixture',resourceRevision:'r1',generation:0,app:{revision:1,stateRevision:0,state:{tab:'draw'},manifest:{},versions:[],requests:[]},...initial},component;
 const dispatch=async(name,args)=>{calls.push({name,args});const receipt=dispatcher&&await dispatcher(name,args);return receipt??{accepted:true,result:{app:{...canvas.app,stateRevision:canvas.app.stateRevision+1}}}};
 await act(async()=>{component=create(React.createElement(CanvasAppViewer,{canvas,dispatch}),{createNodeMock:node=>node.type==='iframe'?frameNode:null})});
 const channel=out.at(-1).channel;
 const send=async data=>act(async()=>callbacks.get('message')({source:contentWindow,data:{type:'canvas-app',id:'fixture',channel,revision:1,stateRevision:0,editVersion:1,...data}}));
 return {component,calls,out,send,frameNode,dirty:()=>JSON.stringify(component.toJSON()).includes('Unsaved input'),
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

test('missing native source initialization becomes a targeted error with explicit clean Retry, never blank readiness or repeated app work',async()=>{
 const original=globalThis.setTimeout,originalClear=globalThis.clearTimeout;
 let failInitialization;
 globalThis.setTimeout=(fn,delay)=>delay===15000?(failInitialization=fn,{fixtureTimer:true}):original(fn,delay);
 globalThis.clearTimeout=id=>id?.fixtureTimer?undefined:originalClear(id);
 let f;
 try{
  f=await fixture(undefined,{viewId:'inline-source'});
  const firstChannel=f.out.at(-1).channel;
  await act(async()=>failInitialization());
  assert.ok(JSON.stringify(f.component.toJSON()).includes('saved surface did not initialize'));
  const report=f.calls.find(row=>row.name==='canvas.views.status');
  assert.equal(report.args.viewId,'inline-source');assert.equal(report.args.status,'error');
  assert.equal(f.calls.some(row=>row.name.startsWith('canvas.apps.')),false);
  await act(async()=>f.component.root.findAllByType('button').find(node=>node.props.children==='Retry saved surface').props.onClick());
  await act(async()=>f.component.root.findByType('iframe').props.onLoad());
  assert.notEqual(f.out.at(-1).channel,firstChannel,'Explicit Retry mounts a fresh iframe channel');
  assert.equal(f.calls.some(row=>row.name.startsWith('canvas.apps.')),false,'Retry only reloads saved source');
 }finally{await f?.close();globalThis.setTimeout=original;globalThis.clearTimeout=originalClear}
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

test('native host review/resolve/restore use shown inline quartet and review freshness, not pinned primary',async()=>{
 const target={viewId:'inline-controls',resourceId:'fixture',resourceRevision:'r1',generation:3};
 const request={id:'theme-request',status:'pending',action:'theme.preview',summary:'Review theme'};
 const app={revision:2,stateRevision:4,state:{},manifest:{},versions:[{version:1},{version:2}],requests:[request]};
 let inspectedApp=app;
 const f=await fixture(async name=>name==='canvas.apps.inspect'?{accepted:true,result:{app:inspectedApp,requestInput:{css:':root{color:red}'}}}:undefined,{...target,selectedVersion:null,readOnlyVersion:false,app});
 const button=name=>f.component.root.findAllByType('button').find(node=>node.props.children===name);
 try{
  await act(async()=>button('Review theme change').props.onClick());
  await act(async()=>button('Approve theme change').props.onClick());
  await act(async()=>button('Decline').props.onClick());
  await act(async()=>f.component.root.findAllByType('button').find(node=>String(node.props.children).includes('Restore revision')).props.onClick());
  for(const name of ['inspect','resolve','restore']){
   const call=f.calls.find(row=>row.name==='canvas.apps.'+name);assert.ok(call);
   assert.deepEqual(Object.fromEntries(Object.keys(target).map(field=>[field,call.args[field]])),target);
   if(name==='inspect'){assert.equal(call.args.expectedRevision,undefined);assert.equal(call.args.expectedStateRevision,undefined)}
   else{assert.equal(call.args.expectedRevision,2);assert.equal(call.args.expectedStateRevision,4)}
  }
  await act(async()=>button('Review theme change').props.onClick());
  await f.update({app:{...app,stateRevision:5}});
  const before=f.calls.filter(row=>row.name==='canvas.apps.resolve').length;
  await act(async()=>button('Approve theme change').props.onClick());
  assert.equal(f.calls.filter(row=>row.name==='canvas.apps.resolve').length,before);
  assert.ok(JSON.stringify(f.component.toJSON()).includes('changed after review'));
  await f.update({readOnlyVersion:true,selectedVersion:2});
  assert.ok(f.component.root.findAllByType('button').filter(node=>String(node.props.children).includes('Restore revision')).every(node=>node.props.disabled));
 }finally{await f.close()}
});

test('native prepare uses exact channel/token/edit version, vetoes delayed editing and never checkpoints',async()=>{
 const target={viewId:'inline-transition',resourceId:'fixture',resourceRevision:'r1',generation:0};
 const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'session',canvasWorkspace:{views:[target]}};
 const unregister=inlineOwnership.register(scope,target,{sessionId:'session',messageId:'turn',version:1});
 const f=await fixture(undefined,target);
 const ack={op:'transition',blocked:true,quiescent:true,editVersion:0,localPending:false,queued:0,pending:0,gestures:0,draftPending:false,uncertain:false};
 try{
  let prepared;
  await act(async()=>{prepared=inlineOwnership.prepareTransition(target,'first',false,scope)});
  assert.equal(f.frameNode.inert,true);assert.equal(f.frameNode.tabIndex,-1);
  assert.equal(f.out.at(-1).prepareTransition,'first');assert.equal(f.out.some(row=>row.checkpoint),false);
  await f.send({...ack,token:'stale'});
  assert.equal(inlineOwnership.transition(target).proof,undefined);
  // Delayed editing lands after parent preparation; dirty report need not
  // settle for the local guard to refuse before POST.
  await f.send({op:'editing',editVersion:1});
  const rejected=assert.rejects(prepared,/pending, changed or uncertain/);
  await f.send({...ack,token:'first'});
  await rejected;assert.equal(f.frameNode.inert,false);assert.equal(f.dirty(),true);
  await f.send({op:'dirty',requestId:'clean',editVersion:1,args:{dirty:false}});
  await act(async()=>{prepared=inlineOwnership.prepareTransition(target,'second',false,scope)});
  await f.send({...ack,token:'second',editVersion:1});
  const hold=await prepared;inlineOwnership.validateTransition(hold,scope);
  await f.send({op:'state',requestId:'disabled',args:{patch:{value:'not accepted'}}});
  assert.equal(f.calls.some(row=>row.name==='canvas.apps.state'),false);
  assert.match(f.out.at(-1).error,/disabled/);
  inlineOwnership.refuseTransition(hold);assert.equal(f.frameNode.inert,false);
 }finally{unregister();await f.close()}
});

test('known hold release reconciles an already changed definition; unknown and late dirty input keep the old frame',async()=>{
 for(const lateDirty of [false,true]){
  const target={viewId:'inline-release',resourceId:'fixture',resourceRevision:'r1',generation:1};
  const scope={client:{id:'tab',hostInstanceId:'host'},selectedSessionId:'session',canvasWorkspace:{views:[target]}};
  const unregister=inlineOwnership.register(scope,target,{sessionId:'session',messageId:'turn',version:1});
  const f=await fixture(undefined,target);
  try{
   const oldSrc=f.component.root.findByType('iframe').props.src,oldChannel=f.out.at(-1).channel;
   let prepared;
   await act(async()=>{prepared=inlineOwnership.prepareTransition(target,'release-once',true,scope)});
   await f.send({op:'transition',token:'release-once',blocked:true,quiescent:true,editVersion:0,localPending:false,queued:0,pending:0,gestures:0,draftPending:false,uncertain:false});
   const hold=await prepared;hold.sent=true;
   await f.update({resourceRevision:'r2',generation:2,app:{revision:2,stateRevision:0,state:{},manifest:{},versions:[],requests:[]}});
   assert.equal(f.component.root.findByType('iframe').props.src,oldSrc);
   assert.equal(f.frameNode.inert,true,'Unknown delivery never releases or remounts');
   if(lateDirty)await f.send({op:'editing',editVersion:1});
   await act(async()=>inlineOwnership.refuseTransition(hold));
   if(lateDirty){
    assert.equal(f.component.root.findByType('iframe').props.src,oldSrc);
    assert.equal(f.dirty(),true);assert.equal(f.out.at(-1).channel,oldChannel);
   }else{
    assert.notEqual(f.component.root.findByType('iframe').props.src,oldSrc);
    await act(async()=>f.component.root.findByType('iframe').props.onLoad());
    assert.notEqual(f.out.at(-1).channel,oldChannel);
   }
  }finally{unregister();await f.close()}
 }
});
