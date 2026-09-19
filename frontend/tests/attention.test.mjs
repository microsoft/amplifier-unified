import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AttentionBadge,ActivityPanel,readItems}=await server.ssrLoadModule('/src/attention.jsx');
const {FeedbackNotice}=await server.ssrLoadModule('/src/feedback.jsx');
const {browserPreviewPolicy,BrowserPreview}=await server.ssrLoadModule('/src/canvas-library.jsx');
const {WorkspaceRail}=await server.ssrLoadModule('/src/shell-panels.jsx');
test.after(()=>server.close());
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
test('completion badges roll up across workspaces, collapsed navigation, activity and chat rows',()=>{
 const state={view:{},selectedWorkspaceId:'one',workspaces:[{id:'one',name:'One',path:'/one'},{id:'two',name:'Two',path:'/two'}],sessions:[{id:'a',title:'Finished plan',workspace:'/one'}],attention:{unread:2,settingsUnread:0,sections:{chats:2},sessions:{a:1},workspaces:{one:1,two:1},items:[{id:'completion:a',title:'Response ready',label:'Finished plan',sessionId:'a',fingerprint:'g1'}]}};
 assert.match(render(WorkspaceRail,{state,act:()=>{}}),/Two · 1 ready/);
 assert.match(render(WorkspaceRail,{state,act:()=>{}}),/2 unread items/);
 assert.match(render(ActivityPanel,{state,act:()=>{}}),/Finished plan/);
 assert.equal(render(AttentionBadge,{state,settings:true}),'');
});
test('acknowledgement includes the exact observed fingerprints',async()=>{
 let sent;await readItems((name,args)=>sent={name,args},[{id:'completion:a',fingerprint:'g1'}]);
 assert.deepEqual(sent,{name:'attention.read',args:{ids:['completion:a'],fingerprints:{'completion:a':'g1'}}});
});
test('feedback status is available outside the form for all outcomes',()=>{
 for(const status of ['sending','submitted','failed','unknown']){
  const receipt={requestId:'req-123',status,message:`Outcome ${status}`};
  const state={view:{panel:null},feedback:{requests:[receipt]},attention:{items:[{...receipt,id:'feedback:req-123',fingerprint:status}]}};
  const html=render(FeedbackNotice,{state,act:()=>{}});
  assert.match(html,status==='sending'?/sending in the background/:new RegExp(`Outcome ${status}`));
  assert.match(html,/View (status|feedback)/);
  if(status==='submitted')assert.match(html,/a-check-result success/);
  if(['failed','unknown'].includes(status))assert.match(html,/a-check-result error/);
  state.attention.items[0].read=true;
  if(status!=='sending')assert.equal(render(FeedbackNotice,{state,act:()=>{}}),'');
 }
});
test('browser preview detects mixed content and handles secure loopback exceptions',()=>{
 assert.equal(browserPreviewPolicy('http://100.67.72.37:5190/content','https://host.local:8941/').blocked,true);
 assert.equal(browserPreviewPolicy('https://site.example/','https://host.local/').blocked,false);
 assert.equal(browserPreviewPolicy('http://127.0.0.1:5190/','https://host.local/').blocked,false);
 assert.equal(browserPreviewPolicy('http://[::1]:5190/','https://host.local/').localWarning,true);
 assert.equal(browserPreviewPolicy('http://localhost:5190/','http://localhost:8941/').localWarning,false);
 assert.equal(browserPreviewPolicy('http://localhost.evil.example/','https://host.local/').blocked,true);
});
test('external browser fallback is visible without expanding canvas controls',()=>{
 const html=render(BrowserPreview,{canvas:{id:'frame',kind:'browser',url:'https://example.com',title:'Example'},act:()=>{}});
 assert.match(html,/a-browser-help/);assert.match(html,/Open in browser/);assert.match(html,/Preview help/);assert.doesNotMatch(html,/allow-same-origin/);
});
