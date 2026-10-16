import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AIConnections}=await server.ssrLoadModule('/src/ai-connections.jsx');
const {NotificationSettings}=await server.ssrLoadModule('/src/notification-settings.jsx');
test.after(()=>server.close());globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test('AI setup welcome is only shown after an authoritative empty provider read',()=>{
 const render=setup=>renderToStaticMarkup(React.createElement(AIConnections,{state:{setup},act:()=>Promise.resolve({accepted:true}),navigate:()=>{}}));
 assert.doesNotMatch(render({}),/Start with one connection/);
 assert.match(render({}),/Loading AI connections/);
 assert.match(render({providers:[],providersLoadedAt:1}),/Start with one connection/);
 assert.doesNotMatch(render({operations:{'providers.list:':{phase:'error',error:'Offline'}}}),/Start with one connection/);
 assert.match(render({operations:{'providers.list:':{phase:'error',error:'Offline'}}}),/Retry loading connections/);
 assert.doesNotMatch(render({providers:[{id:'one',module:'provider-openai',enabled:true,config:{model:'test'}}],providersLoadedAt:1}),/Use for images/);
});
test('notification toggle writes one preference without committing or clearing private connection drafts',async t=>{
 // The permission hook observes browser events; provide that environment while
 // keeping these tests focused on saved preferences and private draft receipts.
 const prior={window:globalThis.window,document:globalThis.document};
 globalThis.window=new EventTarget();globalThis.document=new EventTarget();
 t.after(()=>{for(const [key,value] of Object.entries(prior)){if(value===undefined)delete globalThis[key];else globalThis[key]=value}});
 const calls=[];let renderer;
 let state={view:{maintenanceDraft:{notifyServer:'https://unfinished.example'}},notificationSettings:{desktop:true,preview:false,enabled:false,server:'https://ntfy.sh'}};
 const send=async(action,args)=>{calls.push([action,args]);return {accepted:true,operationId:'toggle'}};
 const render=()=>React.createElement(NotificationSettings,{state,act:send});
 await act(()=>{renderer=create(render())});
 try{
  const inputs=renderer.root.findAllByType('input');
  await act(()=>inputs.find(n=>n.props.id==='notify-token').props.onChange({target:{value:'unsaved-secret'}}));
  await act(()=>inputs.find(n=>n.props.type==='checkbox'&&n.props.checked===true).props.onChange({target:{checked:false}}));
  assert.deepEqual(calls,[['notifications.save',{patch:{desktop:false}}]]);
  assert.equal(renderer.root.findAllByType('input').filter(n=>n.props.type==='checkbox')[1].props.checked,false);
  state={...state,actionStatus:{'notifications.save':{commandId:'toggle',phase:'ready'}}};await act(()=>renderer.update(render()));
  assert.equal(renderer.root.findByProps({id:'notify-token'}).props.value,'unsaved-secret');
  await act(()=>renderer.root.findAllByType('input').filter(n=>n.props.type==='checkbox')[3].props.onChange({target:{checked:true}}));
  assert.deepEqual(calls.at(-1),['notifications.save',{patch:{preview:true}}]);
  state={...state,actionStatus:{'notifications.save':{commandId:'toggle',phase:'error',error:'Offline'}}};await act(()=>renderer.update(render()));
  assert.equal(renderer.root.findAllByType('input').filter(n=>n.props.type==='checkbox')[3].props.checked,false);
  assert.equal(renderer.root.findByProps({id:'notify-token'}).props.value,'unsaved-secret');
 }finally{await act(()=>renderer.unmount())}
});
