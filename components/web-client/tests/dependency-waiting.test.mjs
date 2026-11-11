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
 assert.match(render({sessionId:'manager',activity}),/Waiting for <button[^>]*data-action="session.select"[^>]*>Worker<\/button>/);
 const returned=render({sessionId:'manager',activity:{...activity,waiting:{...waiting,phase:'returned',targets:[{...waiting.targets[0],phase:'returned',label:'Result ready'}]}}});
 assert.match(returned,/Result ready/);
 assert.doesNotMatch(returned,/Waiting for/);
});
