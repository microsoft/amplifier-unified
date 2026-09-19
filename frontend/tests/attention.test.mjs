import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AttentionBadge,ActivityPanel,readItems,completionToRead}=await server.ssrLoadModule('/src/attention.jsx');
const {FeedbackNotice}=await server.ssrLoadModule('/src/feedback.jsx');
const {browserPreviewPolicy,BrowserPreview}=await server.ssrLoadModule('/src/canvas-library.jsx');
const {WorkspaceRail}=await server.ssrLoadModule('/src/shell-panels.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
test('completion badges roll up across workspaces, collapsed navigation, activity and chat rows',()=>{
 const state={view:{},selectedWorkspaceId:'one',workspaceExplorer:{rows:[{path:'/one',name:'one',workspaceId:'one',chatCount:1,unread:1,canBrowse:false},{path:'/two',name:'two',workspaceId:'two',chatCount:1,unread:1,canBrowse:false}],totalWorkspaces:2},workspaces:[{id:'one',name:'One',path:'/one',available:true},{id:'two',name:'Two',path:'/two',available:true}],sessions:[{id:'a',title:'Finished plan',workspace:'/one'}],attention:{unread:2,settingsUnread:0,sections:{chats:2},sessions:{a:1},workspaces:{one:1,two:1},items:[{id:'completion:a',title:'Response ready',label:'Finished plan',sessionId:'a',fingerprint:'g1'}]}};
 assert.match(render(WorkspaceRail,{state,act:()=>{}}),/Open chats in \/two/);
 assert.match(render(WorkspaceRail,{state,act:()=>{}}),/2 unread items/);
 assert.match(render(ActivityPanel,{state,act:()=>{}}),/Finished plan/);
 assert.equal(render(AttentionBadge,{state,settings:true}),'');
});
test('acknowledgement includes the exact observed fingerprints',async()=>{
 let sent;await readItems((name,args)=>sent={name,args},[{id:'completion:a',fingerprint:'g1'}]);
 assert.deepEqual(sent,{name:'attention.read',args:{ids:['completion:a'],fingerprints:{'completion:a':'g1'}}});
});
test('viewing a conversation can only acknowledge its completion, not errors or permissions',()=>{
 const state={selectedSessionId:'target',attention:{items:[{id:'session:target',sessionId:'target'},{id:'approval:permission',sessionId:'target'},{id:'completion:other',sessionId:'other'}]}};
 assert.equal(completionToRead(state),undefined);
 const completion={id:'completion:target',sessionId:'target'};state.attention.items.push(completion);
 assert.equal(completionToRead(state),completion);completion.read=true;
 assert.equal(completionToRead(state),undefined);
});
test('off-page errors and approvals open their owning conversation through shared actions',async()=>{
 const state={attention:{items:[{id:'session:failed',sessionId:'failed',title:'Conversation needs attention'},{id:'approval:permission',sessionId:'blocked',title:'Approval requested'}]}},calls=[];
 const act=async(name,args)=>{calls.push({name,args})};let root;
 await renderAct(async()=>{root=create(React.createElement(ActivityPanel,{state,act}))});
 const controls=root.root.findAllByProps({'data-action':'session.select'});
 for(const control of controls)await renderAct(async()=>control.props.onClick());
 assert.deepEqual(calls,[{name:'session.select',args:{id:'failed'}},{name:'view.update',args:{patch:{panel:null}}},{name:'session.select',args:{id:'blocked'}},{name:'view.update',args:{patch:{panel:null}}}]);
 await renderAct(async()=>root.unmount());
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
