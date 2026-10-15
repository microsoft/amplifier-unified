import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
// Exercise the actual caller, output detail and GIF command. Pixel decoding has
// its own tests; only the unrelated publishing panel is inert here.
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',
 optimizeDeps:{noDiscovery:true,include:[]},plugins:[{name:'inert-publishing-test',enforce:'pre',
 resolveId(id){if(id==='./publishing')return '\0inert-publishing'},
 load(id){if(id==='\0inert-publishing')return 'export function PublishingSettings(){return null}'}}]});
const {OutputSettings}=await server.ssrLoadModule('/src/outputs.jsx');
const {AgentCanvas}=await server.ssrLoadModule('/src/shell-panels.jsx');
const {MediaFigure}=await server.ssrLoadModule('/src/inline-media.jsx');
const {request}=await server.ssrLoadModule('/src/api.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

const json=(value,status=200)=>new Response(JSON.stringify(value),{status});
async function library(t,{mimeType='image/gif',shell=false,open=()=>json({accepted:true})}={}){
 const selected={id:'saved-media',sessionId:'own',kind:'media',title:'Retained',
  filename:'clip',version:1,sha256:'a'.repeat(64),bytes:90,linked:false,
  media:{mimeType,loopCount:null},origin:{source:'agent',messageId:'historical-origin'},versionEvidence:'snapshot'};
 const calls=[],actCalls=[],reported=[];
 const globals={
  location:{origin:'https://example.test'},
  document:{hidden:false,addEventListener(){},removeEventListener(){}},
  matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}}),
  ImageDecoder:undefined,IntersectionObserver:undefined,
  fetch:async(path,options)=>{
   assert.equal(path,'/api/actions');
   const command=JSON.parse(options.body);calls.push(command);
   if(command.action==='canvas.openGif')return open(command);
   if(command.action==='outputs.list')return json({accepted:true,result:{items:[selected],nextOffset:null}});
   if(command.action==='outputs.read')return json({accepted:true,result:selected});
   assert.equal(command.action,'outputs.mediaReport');
   return json({accepted:true});
  }
 };
 const descriptors=Object.fromEntries(Object.keys(globals).map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 for(const [name,value] of Object.entries(globals))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});
 let root;
 t.after(async()=>{
  try{if(root)await renderAct(async()=>root.unmount())}
  finally{for(const [name,descriptor] of Object.entries(descriptors))if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name]}
 });
 const dispatch=(action,args,meta={})=>request('/api/actions',{method:'POST',body:{action,args,id:meta.id||crypto.randomUUID()}});
 // Match the App's ordinary act adapter: it deliberately reports and swallows.
 const act=(...args)=>{actCalls.push(args[0]);return dispatch(...args).catch(error=>{reported.push(error)})};
 const state={selectedSessionId:'own',sessions:[{id:'own'},{id:'other'}],canvas:{open:true},
  view:{canvasDraft:{library:true,delivery:'outputs'}}};
 const render=()=>React.createElement(shell?AgentCanvas:OutputSettings,shell?{state,act,dispatch}:{session:{id:'own'},act,dispatch});
 await renderAct(async()=>{root=create(render())});
 const button=action=>root.root.findAllByType('button').find(node=>node.props['data-action']===action);
 await renderAct(async()=>button('outputs.list').props.onClick());
 await renderAct(async()=>button('outputs.read').props.onClick());
 return {root,state,render,button,selected,calls,actCalls,reported,
  opens:()=>calls.filter(row=>row.action==='canvas.openGif'),
  alerts:()=>root.root.findByType(MediaFigure).findAllByProps({role:'alert'}).map(node=>node.children.join(''))};
}

for(const mimeType of ['image/gif','video/mp4']){
 test(`saved ${mimeType} detail uses exact provenance and the existing media figure`,async t=>{
  const {root,calls,actCalls}=await library(t,{mimeType});
  const figure=root.root.findByType(MediaFigure).props;
  const html=JSON.stringify(root.toJSON());
  assert.ok(html.includes('historical-origin')&&html.includes('Immutable saved content'));
  assert.equal(figure.item.id,'saved-media');
  assert.equal(figure.messageId,'historical-origin');
  assert.equal(figure.openInCanvas,mimeType==='image/gif');
  assert.ok(!html.includes('External reference only'));
  assert.deepEqual(calls.find(row=>row.action==='outputs.read').args,{sessionId:'own',id:'saved-media'});
  assert.deepEqual(actCalls,['outputs.list','outputs.read']);
 });
}

test('Canvas library GIF retries an uncertain response with the identical command and an inline error',async t=>{
 let attempts=0;
 const flow=await library(t,{shell:true,open:()=>++attempts===1?{
  ok:true,status:200,headers:new Headers(),text:async()=>{throw new TypeError('Synthetic lost acknowledgement')}
 }:json({accepted:true})});
 await renderAct(async()=>flow.button('canvas.openGif').props.onClick());
 assert.equal(flow.opens().length,1,'An uncertain response does not trigger an automatic replay');
 assert.deepEqual(flow.alerts(),['The connection to Amplifier was interrupted.']);
 assert.equal(flow.button('canvas.openGif').props.disabled,false);
 const original=flow.opens()[0];
 assert.ok(original.id);
 assert.deepEqual(original.args,{sessionId:'own',messageId:'historical-origin',
  id:'saved-media',version:1,sha256:'a'.repeat(64)});
 // Ordinary host updates preserve the mounted request owner.
 await renderAct(async()=>flow.root.update(flow.render()));
 await renderAct(async()=>flow.button('canvas.openGif').props.onClick());
 assert.equal(flow.opens().length,2);
 assert.deepEqual(flow.opens()[1],original);
 assert.deepEqual(flow.alerts(),[]);
 assert.deepEqual(flow.actCalls,['outputs.list','outputs.read'],'GIF admission bypasses the swallowing act adapter');
 assert.deepEqual(flow.reported,[]);
 await renderAct(async()=>flow.button('canvas.openGif').props.onClick());
 assert.notEqual(flow.opens()[2].id,original.id,'Confirmed success releases the command identity');
});

test('Canvas library GIF shows a known host refusal inline and releases its command identity',async t=>{
 let attempts=0;
 const flow=await library(t,{shell:true,open:()=>++attempts===1?
  json({accepted:false,error:'Retained GIF access was refused.'},409):json({accepted:true})});
 await renderAct(async()=>flow.button('canvas.openGif').props.onClick());
 assert.equal(flow.opens().length,1);
 assert.deepEqual(flow.alerts(),['Retained GIF access was refused.']);
 await renderAct(async()=>flow.button('canvas.openGif').props.onClick());
 assert.equal(flow.opens().length,2);
 assert.notEqual(flow.opens()[1].id,flow.opens()[0].id,'A known rejection is not an uncertain delivery');
 assert.deepEqual(flow.opens()[1].args,flow.opens()[0].args);
 assert.deepEqual(flow.alerts(),[]);
 assert.deepEqual(flow.reported,[]);
 assert.deepEqual(flow.actCalls,['outputs.list','outputs.read']);
});

test('the parent selected-session key discards the prior output detail and GIF error',async t=>{
 const flow=await library(t,{shell:true,open:()=>{throw new TypeError('Synthetic disconnect')}});
 await renderAct(async()=>flow.button('canvas.openGif').props.onClick());
 assert.equal(flow.alerts().length,1);
 flow.state.selectedSessionId='other';
 await renderAct(async()=>flow.root.update(flow.render()));
 assert.equal(flow.root.root.findAllByType(MediaFigure).length,0);
 assert.equal(flow.root.root.findAllByProps({'aria-label':'Saved output detail'}).length,0);
 assert.equal(flow.root.root.findAllByProps({role:'alert'}).length,0);
 assert.equal(flow.opens().length,1,'Changing conversations does not replay an uncertain command');
});