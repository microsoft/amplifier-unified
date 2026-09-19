import test from 'node:test';
import assert from 'node:assert/strict';
import {chatPage,headerChatChoices,workspaceChats,CHAT_PAGE_SIZE} from '../src/chat-navigation.js';
const fixture=()=>({view:{},selectedWorkspaceId:'project',selectedSessionId:'chat-0',workspaces:[{id:'project',path:'/fixture'},{id:'other',path:'/other'}],sessions:Array.from({length:5000},(_,i)=>({id:'chat-'+i,title:'Saved chat '+i,workspaceId:'project',workspace:'/fixture'}))});

test('large navigation lists use bounded pages while keeping all chats searchable',()=>{
 const state=fixture(),workspace=state.workspaces[0],first=chatPage(state,workspace);
 assert.equal(first.items.length,CHAT_PAGE_SIZE);assert.equal(first.total,5000);assert.equal(first.pages,50);
 state.view.navChatPage={...first.scope,index:1};
 assert.equal(chatPage(state,workspace).items[0].id,'chat-100');
 state.view.navFilter='Saved chat 4999';
 const filtered=chatPage(state,workspace);assert.equal(filtered.items.length,1);assert.equal(filtered.items[0].id,'chat-4999');assert.equal(filtered.index,0);
 assert.equal(state.sessions.length,5000);
});

test('selection moves to its page while page controls can browse elsewhere',()=>{
 const state=fixture(),workspace=state.workspaces[0];state.selectedSessionId='chat-4901';
 const selected=chatPage(state,workspace);assert.equal(selected.index,49);assert.ok(selected.items.some(chat=>chat.id==='chat-4901'));
 state.view.navChatPage={...selected.scope,index:0};assert.equal(chatPage(state,workspace).index,0);
 state.selectedSessionId='chat-2400';assert.equal(chatPage(state,workspace).index,24);
 state.view.navChatPage={...chatPage(state,workspace).scope,index:900000};assert.equal(chatPage(state,workspace).index,49);
});

test('header choices stay bounded and keep the selected chat available',()=>{
 const state=fixture();state.selectedSessionId='chat-4901';
 state.sessions.unshift({id:'elsewhere',title:'Other workspace chat',workspaceId:'other'});
 const choices=headerChatChoices(state);assert.equal(choices.items.length,100);assert.equal(choices.total,5000);
 assert.ok(choices.items.some(chat=>chat.id==='chat-4901'));assert.equal(choices.items.some(chat=>chat.id==='elsewhere'),false);
});

test('unresolved workspaces are scoped by ID and never group every null path',()=>{
 const workspace={id:'missing-a',path:null},sessions=[{id:'a',workspaceId:'missing-a',workspace:null},{id:'b',workspaceId:'missing-b',workspace:null},{id:'legacy',workspace:null}];
 assert.deepEqual(workspaceChats(sessions,workspace).map(row=>row.id),['a']);
});
