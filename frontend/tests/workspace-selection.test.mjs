import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {navigationSnapshot} from '../src/shell/navigation-snapshot.js';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkspaceRow}=await vite.ssrLoadModule('/src/workspace-explorer.jsx');
test.after(()=>vite.close());
const render=(workspaceId,state)=>renderToStaticMarkup(React.createElement(WorkspaceRow,{
 row:{workspaceId,path:'/work/'+workspaceId,name:workspaceId},state,now:0,compact:true
}));
const selected=(id,state,value)=>assert.match(render(id,state),new RegExp(`aria-pressed="${value}"`));
test('chat selection ignores a remembered workspace browse route even while pages are retained',()=>{
 const raw={selectedWorkspaceId:'previous',view:{},navigationResourcePending:{pinnedNavigation:{status:'pending'}}};
 const current=navigationSnapshot(raw,{selectedSessionId:'chat',selectedWorkspaceId:'current',
  view:{workSurface:'chat',workWorkspaceId:'previous'}});
 selected('previous',current,false);selected('current',current,true);
 assert.equal(current.navigationWorkspacePending,true);
 assert.equal(current.view.workWorkspaceId,'previous','remembered browsing location remains available');
});
test('explicit workspace browsing and pinned component scope keep their own selected workspace',()=>{
 const state={selectedSessionId:'chat',selectedWorkspaceId:'current',view:{workSurface:'workspace',workWorkspaceId:'browse'}};
 selected('browse',navigationSnapshot({view:{}},state),true);
 selected('current',navigationSnapshot({view:{}},state),false);
 const pinned=navigationSnapshot({selectedWorkspaceId:'fixed',view:{}},state,{mode:'pinned',workspaceId:'fixed'});
 selected('fixed',pinned,true);selected('browse',pinned,false);
});
