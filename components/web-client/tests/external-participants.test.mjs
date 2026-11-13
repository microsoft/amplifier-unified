import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ComposerOptions}=await server.ssrLoadModule('/src/composer-options.jsx');
const {MessageEntry,StreamingMessage}=await server.ssrLoadModule('/src/message-actions.jsx');
const {newChatSetup}=await server.ssrLoadModule('/src/new-chat.jsx');
const {draftExternalAgentIds}=await server.ssrLoadModule('/src/external-participants.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const session={id:'room',status:'idle',externalAgent:{label:'Group label is not a speaker',participants:[{id:'alpha',label:'Alpha'},{id:'beta',label:'Beta'}]}};
const state={view:{}};
const render=message=>renderToStaticMarkup(React.createElement(MessageEntry,{message,session,state,act:()=>{},working:false}));

test('new chat captures the selected roster and existing workspace without native configuration',()=>{
 const draft={workspace:'/chosen',title:'Together',externalAgentIds:['alpha','beta'],bundle:'unused',selection:{model:'unused'}};
 assert.deepEqual(newChatSetup({view:{newSessionDraft:draft}}),{workspace:'/chosen',title:'Together',externalAgentIds:['alpha','beta'],location:{kind:'workspace'}});
 assert.deepEqual(newChatSetup({view:{newSessionDraft:{...draft,externalAgentIds:[],externalAgentId:'alpha'}}}),{workspace:'/chosen',title:'Together',externalAgentId:'alpha',location:{kind:'workspace'}});
 assert.deepEqual(draftExternalAgentIds({externalAgentId:'alpha',externalAgentIds:[]}),['alpha']);
 assert.deepEqual(newChatSetup({view:{},workspaces:[]}),{title:'',workspace:'',bundle:'',selection:{},location:{kind:'managed'}});
 assert.deepEqual(newChatSetup({view:{newSessionDraft:{externalAgentId:'',externalAgentIds:[]}}}),{title:'',workspace:'',bundle:'',selection:{},location:{kind:'managed'}});
});

test('recipient selection is explicit, local and disabled while the room is working',async()=>{
 let root;const changes=[],calls=[];
 const element=(recipientAgentId,working=false)=>React.createElement(ComposerOptions,{state,session,act:(...args)=>calls.push(args),recipientAgentId,onRecipientChange:id=>changes.push(id),working});
 try{
  await act(async()=>{root=create(element('alpha'))});
  assert.deepEqual(root.root.findByType('select').findAllByType('option').map(node=>node.children.join('')),['Choose an agent','Alpha','Beta']);
  await act(async()=>root.root.findByType('select').props.onChange({target:{value:'beta'}}));
  assert.deepEqual(changes,['beta']);assert.deepEqual(calls,[]);
  await act(async()=>root.update(element('beta',true)));
  assert.equal(root.root.findByType('select').props.disabled,true);
  await act(async()=>root.root.findByType('select').props.onChange({target:{value:'alpha'}}));assert.deepEqual(changes,['beta']);
 }finally{await act(async()=>root?.unmount())}
});

test('unknown or unset recipient is not silently replaced by the first agent',async()=>{
 let root;
 const element=id=>React.createElement(ComposerOptions,{state,session,act:()=>{},recipientAgentId:id,onRecipientChange:()=>{}});
 try{
  await act(async()=>{root=create(element('removed'))});
  assert.equal(root.root.findByType('select').props.value,'removed');
  assert.equal(root.root.findAllByType('option').find(node=>node.props.value==='removed').props.disabled,true);
  await act(async()=>root.update(element(undefined)));assert.equal(root.root.findByType('select').props.value,'');
 }finally{await act(async()=>root?.unmount())}
});

test('saved replies identify their actual author, including a saved label after catalog rename',()=>{
 const html=render({id:'answer',role:'assistant',text:'The answer',source:'external',agentId:'beta',agentLabel:'Beta at send time'});
 assert.match(html,/Beta at send time message/);assert.match(html,/>Beta at send time<\/p>/);
 assert.doesNotMatch(html,/Group label is not a speaker|Alpha message/);
 const legacy=render({id:'older',role:'assistant',text:'Unknown author'});
 assert.match(legacy,/External agent message/);assert.doesNotMatch(legacy,/Group label is not a speaker/);
});

test('directed user message retains the original recipient independently of the next selected agent',()=>{
 const html=render({id:'input',role:'user',text:'Please check',recipientAgentId:'alpha',recipientAgentLabel:'Original Alpha'});
 assert.match(html,/>To Original Alpha<\/p>/);assert.doesNotMatch(html,/>To Beta</);
});

test('streaming text identifies the active participant and never the group label',()=>{
 const html=renderToStaticMarkup(React.createElement(StreamingMessage,{session:{...session,streaming:'Working on it',externalAgent:{...session.externalAgent,activeAgentId:'beta'}},state}));
 assert.match(html,/>Beta<\/p>/);assert.match(html,/Working on it/);assert.doesNotMatch(html,/Group label is not a speaker/);
});
