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
test('late catalogs preserve selector choices and shared provider actions',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}},session={id:'chat',status:'idle'};
 let state={view:{composerModel:{open:true,sessionId:'chat',instance:'openai',model:'first'}},runtimeControl:{chat:{}}},root;
 const render=()=>React.createElement(ModelControl,{state,session,act,ensureSession:async()=>session,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAllByProps({id:'chat-model'}).filter(n=>typeof n.type==='string').length,1);
 state={...state,runtimeControl:{chat:{'configuration.providers':{providers:[{id:'openai',info:{defaults:{model:'first'}}}],effective:{instance:'openai',model:'first'}},modelCatalogs:{openai:{phase:'ready',models:[{id:'first'},{id:'second'}]}}}}};
 await renderAct(async()=>root.update(render()));
 const picker=()=>root.root.findAllByProps({id:'chat-model'}).find(n=>typeof n.type==='string');
 await renderAct(async()=>picker().props.onChange({target:{value:'second'}}));
 assert.deepEqual(calls.filter(c=>c.args.operation==='provider.select').map(c=>c.args.args),[{instance:'openai',model:'second'}]);
 state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,modelCatalogs:{openai:{phase:'ready',models:[]}}}}};
 await renderAct(async()=>root.update(render()));
 assert.equal(picker().type,'select');
 assert.equal(picker().props.value,'second');
 // An agent uses this same action; its returned catalog must update the trigger.
 state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,'configuration.providers':{...state.runtimeControl.chat['configuration.providers'],pinned:true,effective:{instance:'openai',model:'agent-choice'}}}}};
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findAllByType('span').some(n=>n.children.includes('openai · agent-choice')));
 await renderAct(async()=>root.unmount());
});


test('draft model controls discover without creating a session and save choices through shared view actions',async()=>{
 const calls=[];let state={settings:{workspace:'/future'},view:{newSessionDraft:{workspace:'/future',bundle:'work',selection:{}}}},root;
 const act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state={...state,view:{...state.view,...args.patch}};return {accepted:true}};
 const render=()=>React.createElement(ModelControl,{state,session:null,act,ensureSession:async()=>{throw Error('Must not create a chat')},working:false});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.deepEqual(calls.find(c=>c.name==='providers.list').args,{workspace:'/future'});
 state={...state,setup:{providersRequestedWorkspace:'/future',providers:[{id:'one',module:'provider-test',config:{model:'first'}}],providerCatalogs:{one:{phase:'ready',models:['first','chosen']}}}};
 await renderAct(async()=>root.update(render()));
 const picker=()=>root.root.findAllByProps({id:'chat-model'}).find(n=>typeof n.type==='string');
 await renderAct(async()=>picker().props.onChange({target:{value:'chosen'}}));
 assert.deepEqual(state.view.newSessionDraft.selection,{instance:'one',model:'chosen'});
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findAllByType('span').some(n=>n.children.includes('one · chosen')));
 assert.equal(root.root.findByProps({id:'chat-provider'}).findAllByType('option').some(n=>n.children.includes('Use bundle default')),false);
 assert.equal(calls.some(c=>['session.create','runtime.control'].includes(c.name)),false);
 assert.equal(state.view.newSessionDraft.bundle,'work');await renderAct(async()=>root.unmount());
});

test('inherited choices are displayed without pinning and the popup opens before requests finish',async()=>{
 const calls=[];let state={settings:{workspace:'/new'},view:{},setup:{providersRequestedWorkspace:'/new'},draftDefaults:{'["/new",""]':{phase:'ready',bundle:'work',effective:{instance:'one',model:'actual-model',effort:'high'},providers:[{id:'one',info:{defaults:{model:'actual-model'}}}]}}};
 const act=(name,args)=>{calls.push({name,args});return new Promise(()=>{})};let root;
 await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,act,working:false}))});
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('one · actual-model (high)'));
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.equal(root.root.findAllByType('section').length,1);
 assert.equal(calls.some(c=>c.name==='session.create'||c.args?.patch?.newSessionDraft),false);
 assert.equal(root.root.findAllByType('input').length,0);
 state={...state,view:{newSessionDraft:{workspace:'/new',bundle:'another',selection:{}}}};
 await renderAct(async()=>root.update(React.createElement(ModelControl,{state,act,working:false})));
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('one · actual-model (high)'));
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.equal(calls.some(c=>c.name==='providers.list'),false);
 await renderAct(async()=>root.unmount());
});

test('provider aliases share one choice and model selection retains the correct instance',async()=>{
 const calls=[],providers=[{id:'terra',info:{id:'openai',display_name:'OpenAI',defaults:{model:'terra-model'}}},{id:'astra',info:{id:'openai',display_name:'OpenAI',defaults:{model:'astra-model'}}},{id:'claude',info:{id:'anthropic',display_name:'Anthropic',defaults:{model:'claude-model'}}}];
 const state={view:{composerModel:{open:true,sessionId:'chat',instance:'terra',model:'terra-model'}},runtimeControl:{chat:{'configuration.providers':{providers,effective:{instance:'terra',model:'terra-model'}}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:async(name,args)=>calls.push({name,args}),working:false}))});
 const choices=root.root.findByProps({id:'chat-provider'}).findAllByType('option');assert.deepEqual(choices.map(n=>n.children.join('')),['Anthropic','OpenAI']);
 await renderAct(async()=>root.root.findByProps({id:'chat-model'}).props.onChange({target:{value:'astra-model'}}));
 assert.deepEqual(calls.find(c=>c.args.operation==='provider.select').args.args,{instance:'astra',model:'astra-model'});
 await renderAct(async()=>root.unmount());
});


test('managed drafts do not retain a prior location default while discovery loads',async()=>{
 const calls=[];let state={settings:{workspace:'/prior'},view:{newSessionDraft:{workspace:'/prior',bundle:'',selection:{}}},draftDefaults:{'["/prior",""]':{phase:'ready',bundle:'work',effective:{instance:'private',model:'workspace-model'},providers:[{id:'private',info:{defaults:{model:'workspace-model'}}}]}}};
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};let root;
 const render=()=>React.createElement(ModelControl,{state,act,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('private · workspace-model'));
 state={...state,view:{newSessionDraft:{workspace:'',location:{kind:'managed'},bundle:'',selection:{}}}};
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('Loading model…'));
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.deepEqual(calls.find(row=>row.name==='configuration.defaults').args,{workspace:'',bundle:'',location:{kind:'managed'}});
 assert.deepEqual(calls.find(row=>row.name==='providers.list').args,{workspace:'',location:{kind:'managed'}});
 assert.equal(calls.some(row=>row.name==='session.create'),false);
 await renderAct(async()=>root.unmount());
});

for(const [providerId,label] of [['openai','OpenAI'],['copilot-sdk','GitHub Copilot SDK'],['openai-chatgpt','OpenAI ChatGPT']]){
 test('closed selector identifies '+label+' even for an identical model',async()=>{
  const state={view:{},runtimeControl:{chat:{'configuration.providers':{providers:[{id:'alias',info:{id:providerId,display_name:label}}],effective:{instance:'alias',model:'same-model',effort:'high'}}}}};
  let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:async()=>({accepted:true}),working:false}))});
  assert.deepEqual(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children,[label+' · same-model (high)']);
  await renderAct(async()=>root.unmount());
 });
}
test('runtime report supplies readable provider before opening the selector',async()=>{
 const session={id:'chat',runtimeReport:{provider_choices:[{id:'routing-alias',provider:'copilot-sdk',display_name:'GitHub Copilot SDK',model:'same-model',effort:'high'}],effective_selection:{instance:'routing-alias',model:'same-model',effort:'high'}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state:{view:{}},session,act:async()=>({accepted:true}),working:false}))});
 assert.deepEqual(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children,['GitHub Copilot SDK · same-model (high)']);
 await renderAct(async()=>root.unmount());
});
