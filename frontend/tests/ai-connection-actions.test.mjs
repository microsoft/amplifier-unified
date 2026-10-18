import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {readFile} from 'node:fs/promises';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AIConnections}=await vite.ssrLoadModule('/src/ai-connections.jsx');
const {ConfigurationBoundary}=await vite.ssrLoadModule('/src/configuration-scope.jsx');
const {SettingsDraftProvider}=await vite.ssrLoadModule('/src/settings-drafts.jsx');
const {aiServices}=await vite.ssrLoadModule('/src/ai-connections-data.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>vite.close());
const state=editor=>({view:{aiConnectionEditor:editor},setup:{providers:[],providersLoadedAt:1}});

test('the supported service list is the same shared host catalog',async()=>{
 assert.deepEqual(aiServices,JSON.parse(await readFile(new URL('../../amplifier_web/ai_services.json',import.meta.url),'utf8')));
 assert.equal(aiServices.length,6);
});

test('Connect AI and service choice use semantic actions and track the returned credential operation',async()=>{
 let root,current=state({}),calls=[];
 const dispatch=async(action,args)=>{calls.push([action,args]);return {accepted:true,result:action==='settings.connection.select'?{editor:{id:'new-openai',module:'provider-openai'},credentialCheck:{action:'providers.credentials',operationId:'lookup',status:'pending'}}:{editor:{}}}};
 const render=()=>React.createElement(AIConnections,{state:current,act:dispatch,navigate:()=>{}});
 try{
  await act(async()=>{root=create(render())});
  await act(async()=>root.root.findByProps({'data-action':'settings.connection.start'}).props.onClick());
  assert.deepEqual(calls.find(([action])=>action==='settings.connection.start'),['settings.connection.start',{scope:'global'}]);
  current=state({step:'services'});await act(async()=>root.update(render()));
  const choice=root.root.findAll(node=>node.type?.name==='SettingsLink'&&node.props.title==='OpenAI API')[0];
  await act(async()=>choice.props.onClick());
  assert.deepEqual(calls.find(([action])=>action==='settings.connection.select'),['settings.connection.select',{scope:'global',module:'provider-openai'}]);
  assert.equal(calls.filter(([action])=>action==='providers.credentials').length,0,'select already owns the read-only lookup');
  assert.equal(calls.filter(([action])=>action==='providers.save'||action==='providers.login').length,0);
  assert.ok(JSON.stringify(root.toJSON()).includes('Working on this connection'));
  current={...current,setup:{...current.setup,operations:{'providers.credentials:provider-openai':{commandId:'lookup',phase:'ready'}}}};
  await act(async()=>root.update(render()));assert.ok(!JSON.stringify(root.toJSON()).includes('Working on this connection'));
 }finally{await act(async()=>root?.unmount())}
});

test('semantic connection actions retain explicit workspace ownership and scoped editor state',async()=>{
 let root,scoped,calls=[];
 const target={kind:'workspace',workspaceId:'other'};
 try{
  await act(async()=>{root=create(React.createElement(ConfigurationBoundary,{target,state:{view:{aiConnectionEditor:{id:'global'},configurationDrafts:{'workspace:other':{aiConnectionEditor:{id:'local'}}}}},act:(...args)=>calls.push(args)},value=>{scoped=value;return null}))});
  assert.equal(scoped.state.view.aiConnectionEditor.id,'local');
  scoped.act('settings.connection.select',{module:'provider-openai',scope:'project',sessionId:'unrelated'});
  assert.deepEqual(calls,[['settings.connection.select',{module:'provider-openai',scope:'project',configurationTarget:target}]]);
  scoped.act('settings.connection.services',{});
  assert.deepEqual(calls.at(-1),['settings.connection.services',{}],'catalog read has no configuration owner arguments');
 }finally{await act(async()=>root?.unmount())}
});

test('external connection selection keeps a private key until review or discard, never transfers it',async()=>{
 let root,current=state({step:'connect',id:'old',module:'provider-openai',credentialMode:'private'}),resolve,entry,calls=[];
 const registry={register:(_,value)=>{entry=value},remove:()=>{},hasUnsaved:()=>entry?.dirty,confirmClose:()=>new Promise(done=>resolve=done)};
 const render=()=>React.createElement(SettingsDraftProvider,{value:registry},React.createElement(AIConnections,{state:current,act:async(...args)=>{calls.push(args);return {accepted:true}},navigate:()=>{}}));
 const password=()=>root.root.findByProps({type:'password'});
 try{
  await act(async()=>{root=create(render())});await act(async()=>password().props.onChange({target:{value:'private-test-key'}}));
  assert.equal(entry.dirty,true);
  current=state({step:'connect',id:'new',module:'provider-anthropic',credentialMode:'private'});
  await act(async()=>root.update(render()));assert.equal(password().props.value,'private-test-key');
  await act(async()=>resolve(false));assert.equal(calls.at(-1)[1].patch.aiConnectionEditor.id,'old');
  current=state({step:'connect',id:'old',module:'provider-openai',credentialMode:'private'});await act(async()=>root.update(render()));
  current=state({step:'connect',id:'new',module:'provider-anthropic',credentialMode:'private'});await act(async()=>root.update(render()));
  const beforeDiscard=calls.length;
  await act(async()=>{entry.discard();resolve(true)});
  assert.equal(password().props.value,'');assert.equal(calls.at(-1)[1].patch.aiConnectionEditor.id,'new');
  assert.ok(calls.slice(beforeDiscard).every(([,args])=>args.patch?.aiConnectionEditor?.id==='new'),'discarding the old private form cannot reset an externally selected editor');
  assert.ok(!JSON.stringify(calls).includes('private-test-key'));
 }finally{await act(async()=>root?.unmount())}
});
