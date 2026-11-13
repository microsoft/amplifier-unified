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
const {draftExternalAgentIds,isSharedExternalConversation}=await server.ssrLoadModule('/src/external-participants.js');
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

test('only explicitly shared rooms omit recipient selection, including while agents work',async()=>{
 let root;const calls=[],changes=[],shared={...session,status:'working',externalAgent:{...session.externalAgent,participation:'shared'}};
 const element=selected=>React.createElement(ComposerOptions,{state,session:selected,working:true,act:(...args)=>calls.push(args),recipientAgentId:'removed',onRecipientChange:id=>changes.push(id)});
 try{
  await act(async()=>{root=create(element(shared))});
  assert.equal(isSharedExternalConversation(shared),true);
  assert.equal(isSharedExternalConversation(session),false);
  assert.equal(isSharedExternalConversation({externalAgent:{id:'one'}}),false);
  assert.equal(isSharedExternalConversation(null),false);
  assert.equal(root.root.findAllByType('select').length,0);
  assert.match(root.root.findByType('span').children.join(''),/Everyone in this chat can reply/);
  assert.deepEqual(calls,[]);assert.deepEqual(changes,[]);
  await act(async()=>root.update(element(session)));
  assert.equal(root.root.findByType('select').props.value,'removed');
  assert.equal(root.root.findByType('select').props.disabled,true);
 }finally{await act(async()=>root?.unmount())}
});

test('shared streams keep each participant identity and activity through independent completion',async()=>{
 let root;
 const element=(participants,externalStreams={})=>React.createElement(StreamingMessage,{state,session:{...session,streaming:'Unattributed old stream',externalStreams,externalAgent:{...session.externalAgent,participation:'shared',participants}}});
 const agents=[{id:'alpha',label:'Alpha',status:'working'},{id:'beta',label:'Beta',status:'listening'}];
 const streams={alpha:{id:'same-wire-id',text:'First draft',inputId:'first'},beta:{id:'same-wire-id',text:'Second draft',inputId:'second'}};
 const text=node=>node.children.map(child=>typeof child==='string'?child:text(child)).join('');
 try{
  await act(async()=>{root=create(element([{...agents[0],status:'connecting'},agents[1]]))});
  assert.match(text(root.root),/AlphaConnecting/);assert.match(text(root.root),/BetaConsidering/);
  await act(async()=>root.update(element(agents,{alpha:streams.alpha})));
  let cards=root.root.findAllByType('article');
  assert.deepEqual(cards.map(card=>card.props['data-agent-id']),['alpha','beta']);
  assert.match(text(cards[0]),/AlphaFirst draftWorking/);assert.match(text(cards[1]),/BetaConsidering/);
  assert.doesNotMatch(text(root.root),/Unattributed old stream|Group label is not a speaker/);
  await act(async()=>root.update(element(agents,streams)));
  cards=root.root.findAllByType('article');assert.match(text(cards[0]),/AlphaFirst draft/);assert.match(text(cards[1]),/BetaSecond draft/);
  await act(async()=>root.update(element([{...agents[0],status:'idle'},{...agents[1],status:'working'}],{beta:streams.beta})));
  cards=root.root.findAllByType('article');assert.equal(cards.length,1);
  assert.equal(cards[0].props['aria-label'],'Beta activity');assert.match(text(cards[0]),/BetaSecond draftWorking/);
  await act(async()=>root.update(element([{...agents[0],status:'unknown'},{...agents[1],status:'working'}],{beta:streams.beta})));
  cards=root.root.findAllByType('article');assert.equal(cards.length,2);
  assert.match(text(cards[0]),/AlphaDelivery could not be confirmed\. Nothing was resent\./);
  assert.match(text(cards[1]),/BetaSecond draftWorking/);assert.equal(root.root.findAllByType('button').length,0);
  await act(async()=>root.update(element([{...agents[0],status:'error',error:'Connection closed.'},{...agents[1],status:'working'}],{beta:streams.beta})));
  assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Connection closed.');
  await act(async()=>root.update(element(agents.map(agent=>({...agent,status:'idle'})),{removed:{text:'No longer a participant'}})));
  assert.equal(root.root.findAllByType('article').length,0);
 }finally{await act(async()=>root?.unmount())}
});

test('a shared post is not falsely addressed to one agent and saved replies retain their author',()=>{
 const shared={...session,externalAgent:{...session.externalAgent,participation:'shared'}};
 const html=renderToStaticMarkup(React.createElement(MessageEntry,{message:{id:'post',role:'user',text:'Alpha, could you check this?'},session:shared,state,act:()=>{},working:true}));
 assert.match(html,/Alpha, could you check this/);assert.doesNotMatch(html,/To Alpha|To Beta/);
 const reply=renderToStaticMarkup(React.createElement(MessageEntry,{message:{id:'reply',role:'assistant',agentId:'beta',agentLabel:'Saved Beta',text:'Checked',source:'external'},session:shared,state,act:()=>{},working:true}));
 assert.match(reply,/Saved Beta message/);assert.doesNotMatch(reply,/Group label is not a speaker/);
});

test('native and singleton streaming keep the existing single stream behavior',()=>{
 const native=renderToStaticMarkup(React.createElement(StreamingMessage,{session:{id:'native',streaming:'Native answer'},state}));
 assert.match(native,/Native answer/);assert.doesNotMatch(native,/data-agent-id|activity|External agent/);
 const single=renderToStaticMarkup(React.createElement(StreamingMessage,{session:{id:'single',streaming:'Remote answer',externalAgent:{id:'alpha',label:'Only Alpha'}},state}));
 assert.match(single,/>Only Alpha<\/p>/);assert.match(single,/Remote answer/);assert.doesNotMatch(single,/data-agent-id|activity/);
});
