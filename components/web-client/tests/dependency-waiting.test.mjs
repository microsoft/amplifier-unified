import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DependencyWaiting}=await vite.ssrLoadModule('/src/dependency-waiting.jsx');
test.after(()=>vite.close());
const waiting={phase:'pending',targets:[{sessionId:'worker',title:'Worker'}]};
const render=props=>renderToStaticMarkup(React.createElement(DependencyWaiting,{act:()=>assert.fail('render must not dispatch'),...props}));

test('empty chat startup and cleared selection render without dependency activity',()=>{
 for(const activity of [undefined,null])for(const sessionId of [undefined,null,'']){
  assert.equal(render({activity,sessionId}),'');
 }
 assert.equal(render({sessionId:'manager'}),'');
});

test('dependency rows require an identified selected chat and matching activity',()=>{
 for(const sessionId of [undefined,null,''])assert.equal(render({sessionId,activity:{sessionId,waiting}}),'');
 assert.equal(render({sessionId:'new-chat',activity:{sessionId:'manager',waiting}}),'');
 assert.equal(render({sessionId:'manager',activity:{sessionId:'manager'}}),'');
});

test('matching selected activity renders the existing dependency link and result',()=>{
 const activity={sessionId:'manager',kind:'waiting',waiting};
 assert.match(render({sessionId:'manager',activity}),/Waiting on /);
 assert.match(render({sessionId:'manager',activity}),/<button[^>]*title="Worker"[^>]*aria-label="Worker"[^>]*data-action="session.select"[^>]*>Worker<\/button>/);
 const returned=render({sessionId:'manager',activity:{...activity,waiting:{...waiting,phase:'returned',targets:[{...waiting.targets[0],phase:'returned',label:'Result ready'}]}}});
 assert.match(returned,/Result ready/);
 assert.doesNotMatch(returned,/Waiting on/);
});

test('only neutral pending and deferred dependencies get Waiting on copy',()=>{
 for(const phase of [undefined,'pending','deferred']){
  const html=render({sessionId:'manager',activity:{sessionId:'manager',kind:'waiting',waiting:{phase,targets:[{...waiting.targets[0],phase,label:'Dependency deferred'}]}}});
  assert.match(html,/Waiting on /);
  assert.doesNotMatch(html,/Dependency deferred/);
 }
 for(const phase of ['failed','unknown','cancelled','decision','returned']){
  const html=render({sessionId:'manager',activity:{sessionId:'manager',kind:'attention',waiting:{phase,targets:[{...waiting.targets[0],phase,label:'Host '+phase}]}}});
  assert.match(html,new RegExp('Host '+phase));
  assert.doesNotMatch(html,/Waiting on/);
 }
});

test('mixed phases, interrupted chats and aggregate evidence stay distinct',()=>{
 for(const status of ['error','failed','stopped','cancelled','interrupted','paused']){
  const html=render({sessionId:'manager',activity:{sessionId:'manager',waiting:{...waiting,targets:[{...waiting.targets[0],status}]}}});
  assert.match(html,new RegExp('chat '+status));
  assert.doesNotMatch(html,/Waiting on/);
 }
 const aggregate=render({sessionId:'manager',activity:{sessionId:'manager',waiting:{...waiting,phase:'unknown',label:'Outcome unknown'}}});
 assert.match(aggregate,/Outcome unknown/);assert.doesNotMatch(aggregate,/Waiting on/);
 const mixed=render({sessionId:'manager',activity:{sessionId:'manager',waiting:{...waiting,targets:[waiting.targets[0],{sessionId:'second',title:'Second',phase:'returned',label:'Result ready'}]}}});
 assert.match(mixed,/2 chat dependencies/);assert.match(mixed,/Result ready/);assert.doesNotMatch(mixed,/Waiting on/);
});

test('multiple targets stay expandable, bounded references and unavailable names remain honest',()=>{
 const targets=[...waiting.targets,{sessionId:'second',title:'Second',available:false}];
 for(const hasMore of [false,true]){
  const html=render({sessionId:'manager',activity:{sessionId:'manager',kind:'waiting',waiting:{...waiting,targets,hasMore}}});
  assert.match(html,hasMore?/<summary>Waiting on 2\+ chats<\/summary>/:/<summary>Waiting on 2 chats<\/summary>/);
  assert.match(html,/title="Second">Second<\/span><span> \(unavailable\)/);
  assert.equal((html.match(/data-action="session.select"/g)||[]).length,1);
  assert.equal(html.includes('More references are not shown.'),hasMore);
 }
});

test('aggregate evidence survives a different target outcome and closed disclosures',()=>{
 for(const phase of ['unknown','decision','failed','cancelled','returned','reported']){
  for(const targets of [
   [{sessionId:'release',title:'Release',phase:'returned',label:'Result ready'}],
   [{sessionId:'release',title:'Release',status:'failed'}],
   [{sessionId:'release',title:'Release',phase:'returned',label:'Result ready'},waiting.targets[0]],
  ]){
   const html=render({sessionId:'manager',activity:{sessionId:'manager',kind:'attention',label:'Host '+phase,waiting:{phase,targets}}});
   assert.match(html,new RegExp('Host '+phase));
   if(targets.length>1)assert.match(html,new RegExp('<summary>[^<]*Host '+phase+'[^<]*Result ready[^<]*</summary>'));
  }
 }
 const html=render({sessionId:'manager',activity:{sessionId:'manager',kind:'waiting',label:'Dependency pending',waiting:{...waiting,targets:[{sessionId:'release',title:'Release',phase:'returned',label:'Result ready'},waiting.targets[0]]}}});
 assert.match(html,/<summary>[^<]*Result ready[^<]*<\/summary>/);
 assert.doesNotMatch(html,/Waiting on/);
});
