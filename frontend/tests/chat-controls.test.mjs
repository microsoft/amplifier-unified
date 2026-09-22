import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ModelControl}=await server.ssrLoadModule('/src/chat-controls.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('a new workspace stays idle until its model controls are explicitly opened',async()=>{
 const calls=[],session={id:'new-workspace-chat',status:'idle',deferRuntimeUntilInteraction:true};
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}},state={view:{}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session,act,ensureSession:async()=>session,working:false}))});
 assert.equal(calls.length,0);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.ok(calls.some(call=>call.name==='runtime.control'&&call.args.operation==='configuration.providers'));
 await renderAct(async()=>root.unmount());
});
test('late catalogs and select-to-text replacement both persist through the shared provider action',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}},session={id:'chat',status:'idle'};
 let state={view:{composerModel:{open:true,sessionId:'chat',instance:'openai',model:'first'}},runtimeControl:{chat:{}}},root;
 const render=()=>React.createElement(ModelControl,{state,session,act,ensureSession:async()=>session,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAllByProps({id:'chat-model'}).filter(n=>typeof n.type==='string').length,0);
 state={...state,runtimeControl:{chat:{'configuration.providers':{providers:[{id:'openai',info:{defaults:{model:'first'}}}],effective:{instance:'openai',model:'first'}},modelCatalogs:{openai:{phase:'ready',models:[{id:'first'},{id:'second'}]}}}}};
 await renderAct(async()=>root.update(render()));
 const picker=()=>root.root.findAllByProps({id:'chat-model'}).find(n=>typeof n.type==='string');
 await renderAct(async()=>picker().props.onChange({target:{value:'second'}}));
 assert.deepEqual(calls.filter(c=>c.args.operation==='provider.select').map(c=>c.args.args),[{instance:'openai',model:'second'}]);
 state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,modelCatalogs:{openai:{phase:'ready',models:[]}}}}};
 await renderAct(async()=>root.update(render()));
 assert.equal(picker().type,'input');
 await renderAct(async()=>picker().props.onChange({target:{value:'custom'}}));
 assert.equal(calls.filter(c=>c.args.operation==='provider.select').length,1);
 await renderAct(async()=>picker().props.onBlur({target:{value:'custom'}}));
 assert.deepEqual(calls.filter(c=>c.args.operation==='provider.select').at(-1).args.args,{instance:'openai',model:'custom'});
 // An agent uses this same action; its returned catalog must update the trigger.
 state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,'configuration.providers':{...state.runtimeControl.chat['configuration.providers'],pinned:true,effective:{instance:'openai',model:'agent-choice'}}}}};
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findAllByType('span').some(n=>n.children.includes('agent-choice')));
 await renderAct(async()=>root.unmount());
});


test('draft model controls discover without creating a session and save choices through shared view actions',async()=>{
 const calls=[];let state={settings:{workspace:'/future'},view:{newSessionDraft:{workspace:'/future',bundle:'work',selection:{}}}},root;
 const act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state={...state,view:{...state.view,...args.patch}};return {accepted:true}};
 const render=()=>React.createElement(ModelControl,{state,session:null,act,ensureSession:async()=>{throw Error('Must not create a chat')},working:false});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.deepEqual(calls.find(c=>c.name==='providers.list').args,{workspace:'/future'});
 state={...state,setup:{providersRequestedWorkspace:'/future',providers:[{id:'one',module:'provider-test',config:{model:'first'}}],providerCatalogs:{one:{phase:'ready',models:['first','chosen']}}}};
 await renderAct(async()=>root.update(render()));
 const picker=()=>root.root.findAllByProps({id:'chat-model'}).find(n=>typeof n.type==='string');
 await renderAct(async()=>picker().props.onChange({target:{value:'chosen'}}));
 assert.deepEqual(state.view.newSessionDraft.selection,{instance:'one',model:'chosen'});
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findAllByType('span').some(n=>n.children.includes('chosen')));
 // A stale pin can still be cleared when discovery is unavailable.
 state={...state,setup:{},actionStatus:{'providers.list':{phase:'error',error:'Unavailable'}}};
 await renderAct(async()=>root.update(render()));
 const reset=root.root.findAllByType('button').find(n=>n.children.includes('Use bundle default'));
 await renderAct(async()=>reset.props.onClick());assert.deepEqual(state.view.newSessionDraft.selection,{});
 assert.equal(calls.some(c=>['session.create','runtime.control'].includes(c.name)),false);
 assert.equal(state.view.newSessionDraft.bundle,'work');await renderAct(async()=>root.unmount());
});
