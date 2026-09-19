import test from 'node:test';
import assert from 'node:assert/strict';
import {chatPage,headerChatChoices,workspaceChats,visibleWorkspaces,workspaceLabel,CHAT_PAGE_SIZE} from '../src/chat-navigation.js';
const fixture=()=>({view:{},selectedWorkspaceId:'project',selectedSessionId:'chat-0',workspaces:[{id:'project',path:'/fixture'},{id:'other',path:'/other'}],sessions:Array.from({length:5000},(_,i)=>({id:'chat-'+i,title:'Saved chat '+i,workspaceId:'project',workspace:'/fixture'}))});

test('workspace navigation includes only verified folders without discarding registrations',()=>{
 const workspaces=[
  {id:'a',name:'project',path:'/Users/me/work/project',available:true},
  {id:'b',name:'project',path:'/Users/me/personal/project',available:true},
  {id:'missing',path:'/old/project',available:false},
  {id:'unknown',path:null,available:false},
  {id:'unchecked',path:'/unchecked/project'},
 ];
 assert.deepEqual(visibleWorkspaces({workspaces}).map(w=>w.id),['a','b']);
 assert.equal(workspaces.length,5);
 workspaces[2].available=true;
 assert.deepEqual(visibleWorkspaces({workspaces}).map(w=>w.id),['a','b','missing']);
});

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

test('primary chat lists exclude workers, preserve independent forks, and count only roots',()=>{
 const state=fixture();state.sessions=[
  {id:'root',workspaceId:'project',sessionKind:'root'},
  {id:'worker',title:'Worker-only title',workspaceId:'project',sessionKind:'worker',parentId:'root'},
  {id:'legacy-worker',workspaceId:'project',nativeParentId:'root'},
  {id:'web-fork',workspaceId:'project',parentId:'root',forkTranscript:[]},
  {id:'cli-fork',workspaceId:'project',sessionKind:'root',nativeParentId:'root'},
  {id:'legacy-root',workspaceId:'project',nativeParentId:null},
 ];state.selectedSessionId='worker';
 const expected=['root','web-fork','cli-fork','legacy-root'];
 assert.deepEqual(workspaceChats(state.sessions,state.workspaces[0]).map(row=>row.id),expected);
 assert.deepEqual(workspaceChats(state.sessions).map(row=>row.id),expected);
 assert.equal(chatPage(state,state.workspaces[0]).total,4);
 assert.deepEqual(headerChatChoices(state).items.map(row=>row.id),expected,'selected workers must not reappear in the header');
 state.view.navFilter='Worker-only';assert.equal(chatPage(state,state.workspaces[0]).total,0);
 assert.equal(state.sessions.length,6,'worker history stays available in app state');
});

test('direct subagent history handles aliased native parents without including forks or other projects',async()=>{
 const {directSubagentChats}=await import('../src/chat-navigation.js');
 const parent={id:'web-parent',nativeIdentity:'native-parent',nativeProject:'one'};
 const sessions=[parent,
  {id:'aliased',sessionKind:'worker',parentId:'web-parent'},
  {id:'native',sessionKind:'worker',nativeParentId:'native-parent',nativeProject:'one'},
  {id:'other-project',sessionKind:'worker',nativeParentId:'native-parent',nativeProject:'two'},
  {id:'grandchild',sessionKind:'worker',parentId:'aliased'},
  {id:'fork',sessionKind:'root',parentId:'web-parent',nativeParentId:'native-parent',nativeProject:'one'},
  {id:'web-fork',parentId:'web-parent',forkTranscript:[]},
 ];
 assert.deepEqual(directSubagentChats(sessions,parent).map(row=>row.id),['aliased','native']);
 assert.deepEqual(directSubagentChats(sessions,null),[]);
});

test('legacy forks and edits with native lineage retain independent chat navigation',async()=>{
 const {isTopLevelChat}=await import('../src/chat-navigation.js');
 assert.equal(isTopLevelChat({nativeParentId:'parent',forkTranscript:[{role:'user',content:'Plan'}]}),true);
 assert.equal(isTopLevelChat({nativeParentId:'parent',editOrigin:{turn:1}}),true);
 assert.equal(isTopLevelChat({nativeParentId:'parent',forkTranscript:[]}),false);
 assert.equal(isTopLevelChat({sessionKind:'worker',nativeParentId:'parent',forkTranscript:[{}],editOrigin:{turn:1}}),false,'explicit worker classification wins');
});

test('workspace labels lead with full paths and retain custom names',()=>{
 assert.equal(workspaceLabel({path:'/Users/me/work/project',name:'project'}),'/Users/me/work/project');
 assert.equal(workspaceLabel({path:'/Users/me/work/project',name:'Client work'}),'/Users/me/work/project · Client work');
});
