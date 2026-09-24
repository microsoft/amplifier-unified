import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkNavigationContext,browsePatch}=await vite.ssrLoadModule('/src/work-navigation.js');
const {WorkSurface}=await vite.ssrLoadModule('/src/work-shell.jsx');
const {ConversationList}=await vite.ssrLoadModule('/src/shell/navigation-components.jsx');
const {WorkspaceExplorer}=await vite.ssrLoadModule('/src/workspace-explorer.jsx');
const {AgentCanvas}=await vite.ssrLoadModule('/src/shell-panels.jsx');
const {newChatSetup}=await vite.ssrLoadModule('/src/new-chat.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
const workspace={id:'b',path:'/research',name:'Research',available:true};
const row={workspaceId:'b',path:'/research',name:'Research',chatCount:1};
const scope={mode:'all',workspaceId:null,filter:'',selectedSessionId:'a',section:'recent'};
const page={items:[{id:'b-chat',title:'Research ideas',workspace:'/research',workspaceId:'b'}],scope,total:1,pages:1,index:0,start:0,end:1};
const snapshot={view:{},selectedSessionId:'a',selectedWorkspaceId:'b',workspaces:[workspace],library:{bounded:true,workspaceCount:1},workspaceShortcuts:[row],recentShortcuts:page.items,workspaceExplorer:{rows:[row],mode:'recent'},sidebarNavigation:{pinned:{items:[],total:0,pages:1},recent:page,recentView:{}},sharedHistory:{}};
const host={instanceId:'chats',getSnapshot:()=>snapshot,subscribe:()=>()=>{},dispatch:async()=>({accepted:true})};
const navigation={browse(){},create(){},newChat(){}};
const render=child=>React.createElement(WorkNavigationContext.Provider,{value:navigation},child);
test('compact sidebar has shortcuts; filtering lives in the main surface',()=>{
 const html=renderToStaticMarkup(render(React.createElement(ConversationList,{host})));
 assert.match(html,/Search chats/);assert.match(html,/All workspaces/);assert.match(html,/All chats/);
 assert.doesNotMatch(html,/Filter conversations|Filter workspaces|Browse folders/);
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>host};
 const main=renderToStaticMarkup(render(React.createElement(WorkSurface,{shell,state:{view:{workSurface:'chats'}},act:host.dispatch})));
 assert.match(main,/Research ideas/);assert.match(main,/Filter conversations/);
});
test('workspace shortcut browses without changing execution scope',async()=>{
 const calls=[],selects=[];let root;
 await act(async()=>{root=create(React.createElement(WorkspaceExplorer,{state:snapshot,compact:true,act:async(...args)=>calls.push(args),onSelect:row=>selects.push(row.workspaceId)}))});
 await act(async()=>root.root.findByProps({'aria-label':'Open chats in /research'}).props.onClick());
 assert.deepEqual(selects,['b']);assert.deepEqual(calls,[]);
 assert.deepEqual(browsePatch('workspace','b'),{workSurface:'workspace',workWorkspaceId:'b',workWorkspaceTab:'chats'});
 await act(async()=>root.unmount());
});
test('browsing suppresses the retained canvas host',()=>{
 const state={view:{},selectedSessionId:'a',canvas:{open:true},sessions:[{id:'a',messages:[]}]};
 const html=renderToStaticMarkup(React.createElement(AgentCanvas,{state,act:host.dispatch,suppressed:true}));
 assert.match(html,/id="workspace-canvas"[^>]*hidden=""/);assert.match(html,/Chat overview/);assert.match(html,/Sources/);
});
test('workspace choices retain draft model and bundle',()=>{
 const setup={workspace:'/research',location:{kind:'workspace'},bundle:'custom',selection:{instance:'provider',model:'actual',effort:'high'}};
 assert.deepEqual(newChatSetup({view:{newSessionDraft:setup}}),{title:'',...setup});
});
