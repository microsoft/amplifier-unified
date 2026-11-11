import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {UpdateRestart,UpdateBlockers}=await server.ssrLoadModule('/src/update-restart.jsx');
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const target={hostInstanceId:'host-one',revision:'a'.repeat(40),generation:'b'.repeat(32),attemptId:'attempt-one'};
const updates={phase:'app-staged',forceRestartTarget:target,pendingApp:{...target,latest:'v99.0.0'},blockers:[{kind:'conversation',label:'Running chat',sessionId:'one'}]};
const button=(root,text)=>root.root.findAllByType('button').find(row=>row.children.join('')===text);

test('force entry is inside expanded blocker disclosure and absent without a staged target',()=>{
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:{updates,settings:{updates:{}},view:{}},act:()=>{}}));
 assert.match(html,/<details[^>]*data-part="update-blockers"[^>]*>[\s\S]*Force restart…[\s\S]*<\/details>/);
 assert.doesNotMatch(html,/role="alertdialog"/);
 assert.equal(renderToStaticMarkup(React.createElement(UpdateRestart,{updates:{},act:()=>{}})),'');
});

test('observed reasons are grouped without deduplication or inflating distinct chat counts',()=>{
 const blockers=[
  {kind:'conversation',label:'Running conversation',sessionId:'one',title:'One'},
  {kind:'worker',label:'Queued worker',sessionId:'one',title:'One'},
  {kind:'runtime',label:'Preparing conversation',sessionId:'two',title:'Two'},
  {kind:'configuration',label:'Held operation',reasonCode:'operation-bound-recovery',sessionId:'three'},
  {kind:'configuration',label:'Global configuration lock',reasonCode:'management-lock-held',scope:'global'},
  {kind:'configuration',label:'New configuration reason',reasonCode:'future-reason',sessionId:'three'},
  {kind:'future-kind',label:'New host reason',sessionId:'four'},
  {kind:'checkout-handoff',label:'Handoff needs inspection',sessionId:'four'},
  {kind:'worker',label:'Queued worker',sessionId:'one',title:'One'},
 ];
 const before=structuredClone(blockers);
 const html=renderToStaticMarkup(React.createElement(UpdateBlockers,{updates:{...updates,blockers},act:()=>{}}));
 assert.match(html,/View update blockers \(9 reasons · 4 distinct chats\)/);
 assert.match(html,/Running, preparing, and background work \(4 reasons · 2 distinct chats\)/);
 assert.match(html,/Configuration recovery holds \(3 reasons · 1 distinct chat\)/);
 assert.match(html,/Other update blockers \(2 reasons · 1 distinct chat\)/);
 assert.equal((html.match(/<li>/g)||[]).length,9);
 for(const row of blockers)assert.ok(html.includes(row.label));
 assert.match(html,/can remain unresolved even when they no longer block an update/);
 assert.doesNotMatch(html,/View active work/);
 assert.deepEqual(blockers,before);
});

test('configuration-only blockers are not called running work and omitted holds are not invented',()=>{
 const blockers=[{kind:'configuration',label:'Operation remains held',sessionId:'held'}];
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:{updates:{...updates,blockers,
  blockerObservation:{configurationHoldCount:4,configurationHolds:[{sessionRef:'not-a-chat-id',quiescent:true}]}
 },settings:{updates:{}},view:{}},act:()=>{}}));
 assert.match(html,/View update blockers \(1 reason · 1 distinct chat\)/);
 assert.match(html,/Configuration recovery holds/);
 assert.doesNotMatch(html,/Running, preparing, and background work|Other update blockers|not-a-chat-id|4 reasons/);
 assert.match(html,/Force restart/);
 assert.equal(renderToStaticMarkup(React.createElement(UpdateBlockers,{updates:{blockers:[]},act:()=>{}})),'');
});

test('each reason keeps its original chat link and selection rejection does not navigate',async()=>{
 for(const accepted of [true,false]){
  const calls=[];let root;
  const blockers=[{kind:'configuration',label:'Hold',sessionId:'one',title:'One'},{kind:'unknown',label:'Unknown',sessionId:'one',title:'One'}];
  await act(async()=>{root=create(React.createElement(UpdateBlockers,{updates:{blockers},act:async(...args)=>{calls.push(args);return{accepted}}}))});
  assert.deepEqual(calls,[]);
  const links=root.root.findAllByProps({'data-action':'session.select'});
  assert.equal(links.length,2);
  await act(async()=>links[1].props.onClick());
  assert.deepEqual(calls,accepted?[['session.select',{id:'one'}],['view.update',{patch:{panel:null,workSurface:'chat'}}]]:[['session.select',{id:'one'}]]);
  await act(async()=>root.unmount());
 }
});

test('confirmation cancels without effects and submits exactly the displayed target once',async()=>{
 let root;const calls=[];
 const dispatch=async(action,args)=>{calls.push({action,args});return{accepted:true,requestId:'request-one'}};
 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates,act:dispatch}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 assert.equal(calls.length,0);
 const text=JSON.stringify(root.toJSON());
 assert.match(text,/will be interrupted/);assert.match(text,/will not be replayed automatically/);assert.match(text,/Saved conversation history and drafts/);assert.match(text,/checkout handoffs/);
 await act(async()=>button(root,'Cancel').props.onClick());
 assert.equal(calls.length,0);
 await act(async()=>button(root,'Force restart…').props.onClick());
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 assert.deepEqual(calls,[{action:'updates.forceRestart',args:{...target,confirm:true}}]);
 assert.equal(button(root,'Force restart…').props.disabled,true);
 await act(async()=>root.unmount());
});

for(const field of ['hostInstanceId','revision','generation','attemptId'])test(`changed ${field} cannot consume old confirmation`,async()=>{
 let root;const calls=[];
 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates,act:(...args)=>calls.push(args)}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 await act(async()=>root.update(React.createElement(UpdateRestart,{updates:{...updates,forceRestartTarget:{...target,[field]:'new'}},act:(...args)=>calls.push(args)})));
 assert.equal(button(root,'Interrupt work and restart').props.disabled,true);
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 assert.equal(calls.length,0);
 await act(async()=>root.unmount());
});

test('only matching asynchronous rejection unlocks a fresh explicit confirmation',async()=>{
 let root;const calls=[];
 const dispatch=async(...args)=>{calls.push(args);return{accepted:true,requestId:'current'}};
 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates,act:dispatch}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 await act(async()=>root.update(React.createElement(UpdateRestart,{updates:{...updates,forceRestartResult:{requestId:'older',status:'rejected',detail:'Old refusal'}},act:dispatch})));
 assert.equal(button(root,'Force restart…').props.disabled,true);
 await act(async()=>root.update(React.createElement(UpdateRestart,{updates:{...updates,forceRestartResult:{requestId:'current',status:'rejected',detail:'Review and confirm again'}},act:dispatch})));
 assert.equal(button(root,'Force restart…').props.disabled,false);
 assert.equal(calls.length,1);
 await act(async()=>button(root,'Force restart…').props.onClick());
 assert.equal(calls.length,1);
 await act(async()=>root.unmount());
});

test('newer availability does not relabel the exact prepared restart confirmation',async()=>{
 let root;const calls=[];const dispatch=async(...args)=>{calls.push(args);return{accepted:true,requestId:'reviewed'}};
 const newer={...updates,application:{latest:'v99.0.1'}};
 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates:newer,act:dispatch}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 assert.match(JSON.stringify(root.toJSON()),/prepared Amplifier 99\.0\.0/);
 assert.match(JSON.stringify(root.toJSON()),/99\.0\.1/);
 assert.match(JSON.stringify(root.toJSON()),/Cancel and let the newer release finish preparing/);
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 assert.deepEqual(calls,[['updates.forceRestart',{...target,confirm:true}]]);
 await act(async()=>root.unmount());

 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates:newer,act:dispatch}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 await act(async()=>root.update(React.createElement(UpdateRestart,{updates:{...newer,pendingApp:{...newer.pendingApp,latest:'v99.0.1'},forceRestartTarget:{...target,generation:'c'.repeat(32)}},act:dispatch})));
 assert.match(JSON.stringify(root.toJSON()),/prepared Amplifier 99\.0\.0/);
 assert.equal(button(root,'Interrupt work and restart').props.disabled,true);
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 assert.equal(calls.length,1);
 await act(async()=>root.unmount());
});
