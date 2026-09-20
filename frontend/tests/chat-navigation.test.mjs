import test from 'node:test';
import assert from 'node:assert/strict';
import {chatPage,headerChatChoices,workspaceChats,visibleWorkspaces,workspaceLabel,CHAT_PAGE_SIZE} from '../src/chat-navigation.js';
const fixture=()=>({view:{},selectedWorkspaceId:'project',selectedSessionId:'chat-0',workspaces:[{id:'project',path:'/fixture',available:true},{id:'other',path:'/other',available:true}],sessions:Array.from({length:5000},(_,i)=>({id:'chat-'+i,title:'Saved chat '+i,workspaceId:'project',workspace:'/fixture'}))});

test('shared CLI IDs are searchable without replacing internal keys',()=>{
 const state=fixture();
 state.sessions=[{id:'internal-import',runtimeSessionId:'cli-root-123',workspaceId:'project'},
  {id:'other-import',nativeIdentity:'cli-root-123',workspaceId:'other'},
  {id:'unified-root-456',workspaceId:'project'}];
 state.view={navChatScope:'all',navFilter:'cli-root'};state.pinnedSessionIds=['internal-import'];
 const before=structuredClone(state);
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['internal-import','other-import']);
 assert.deepEqual(state,before);
 state.view.navChatScope='workspace';
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['internal-import']);
 state.view.navFilter='unified-root';
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['unified-root-456']);
});

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

test('all chats include only roots in existing folders and group pins before activity order',()=>{
 const state={view:{navChatScope:'all'},selectedWorkspaceId:'missing',pinnedSessionIds:['pin-old','pin-new','worker','missing'],
  workspaces:[{id:'a',name:'Client work',path:'/work/project',available:true},{id:'b',name:'Personal',path:'/personal/project',available:true},{id:'missing',path:'/gone/project',available:false}],
  sessions:[
   {id:'new',workspaceId:'b',recentActivityAt:40},
   {id:'pin-old',workspaceId:'a',recentActivityAt:1},
   {id:'equal-one',workspaceId:'a',recentActivityAt:30},
   {id:'pin-new',workspaceId:'b',recentActivityAt:2},
   {id:'equal-two',workspace:'/work/project',recentActivityAt:30},
   {id:'worker',workspaceId:'a',sessionKind:'worker',recentActivityAt:100},
   {id:'missing',workspaceId:'missing',recentActivityAt:100},
   {id:'unregistered',workspace:'/elsewhere',recentActivityAt:100},
  ]};
 const before=structuredClone(state),page=chatPage(state);
 assert.deepEqual(page.items.map(row=>row.id),['pin-new','pin-old','new','equal-one','equal-two']);
 assert.deepEqual(page.items.slice(0,2).map(row=>row.pinned),[true,true]);
 assert.equal(page.items[0].workspace,'/personal/project');
 assert.equal(page.scope.mode,'all');assert.equal(page.scope.workspaceId,null);
 assert.deepEqual(state,before,'navigation must not mutate recency or session records');
});

test('recent order accepts finite nonnegative activity and ignores metadata-only updates',()=>{
 const state={view:{},selectedWorkspaceId:'w',workspaces:[{id:'w',path:'/work',available:true}],sessions:[
  {id:'zero',workspaceId:'w',recentActivityAt:0,createdAt:100},
  {id:'new',workspaceId:'w',recentActivityAt:9,createdAt:1},
  {id:'invalid',workspaceId:'w',recentActivityAt:Infinity,createdAt:7},
  {id:'negative',workspaceId:'w',recentActivityAt:-1,createdAt:6},
  {id:'old-renamed',workspaceId:'w',createdAt:5,updatedAt:9000},
  {id:'string',workspaceId:'w',recentActivityAt:'10000',createdAt:4},
 ]};
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['new','invalid','negative','old-renamed','string','zero']);
 state.pinnedSessionIds=['old-renamed'];
 assert.equal(chatPage(state).items[0].id,'old-renamed');
 state.pinnedSessionIds=[];
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['new','invalid','negative','old-renamed','string','zero']);
});

test('all-chat search matches full workspace paths and aliases as well as chat metadata',()=>{
 const state={view:{navChatScope:'all'},workspaces:[{id:'w',path:'/Users/me/work/project',name:'Client website',available:true}],sessions:[{id:'id-unique',workspaceId:'w',title:'Release plan',description:'Ship Tuesday'}]};
 for(const filter of ['*work/project','client website','release','Tuesday','id-unique']){
  state.view.navFilter=filter;assert.equal(chatPage(state).total,1,filter);
 }
 state.view.navFilter='*personal*';assert.equal(chatPage(state).total,0);
});

test('all chats open newest page despite an old selection and use scoped bounded paging',()=>{
 const state=fixture();state.selectedSessionId='chat-4901';
 state.view.navChatPage={...chatPage(state,state.workspaces[0]).scope,index:49};
 state.view.navChatScope='all';
 const first=chatPage(state);
 assert.equal(first.index,0);assert.equal(first.items.length,100);assert.equal(first.total,5000);
 state.view.navChatPage={...first.scope,index:2};assert.equal(chatPage(state).items[0].id,'chat-200');
 state.pinnedSessionIds=['chat-4901'];state.view.navChatPage=null;
 assert.equal(chatPage(state).items[0].id,'chat-4901');
});

test('matching server projection wins over partial local summaries and stale scopes fall back',()=>{
 const state=fixture();state.view.navChatScope='all';
 const scope=chatPage(state).scope;
 const projection={scope,items:[{id:'server-only',title:'Authoritative',workspace:'/fixture',pinned:true}],total:5200,index:0,pages:52,start:0,end:100};
 state.chatNavigation=projection;
 assert.equal(chatPage(state),projection);
 state.view.navFilter='Saved chat 4999';
 assert.equal(chatPage(state).items[0].id,'chat-4999');
 state.view.navFilter='';state.view.navChatScope='workspace';
 assert.equal(chatPage(state).items[0].id,'chat-0');
});

test('bounded headers retain complete counts and selected choices from their own projection',()=>{
 const state=fixture(),projection={items:[{id:'selected-off-page',title:'Selected'}],total:5000};
 state.sessions=[];state.headerChatNavigation=projection;state.library={bounded:true};
 assert.equal(headerChatChoices(state),projection);
});

test('bounded navigation waits for the real page after optimistic search, scope, or page changes',()=>{
 const state=fixture(),first=chatPage(state);state.chatNavigation=first;state.library={bounded:true};state.sessions=state.sessions.slice(0,100);
 assert.equal(chatPage(state),first);
 state.view.navChatPage={...first.scope,index:1};
 assert.equal(chatPage(state).pending,true);assert.deepEqual(chatPage(state).items,[]);
 state.chatNavigation={...first,index:1,start:100,end:200,items:[{id:'from-next-page'}]};
 assert.equal(chatPage(state),state.chatNavigation);
 state.view.navFilter='outside the published page';assert.equal(chatPage(state).pending,true);
 state.view.navFilter='';state.view.navChatScope='all';assert.equal(chatPage(state).pending,true);
});

test('subagent projection keeps complete counts and waits for changed searches and pages',async()=>{
 const {subagentPage,subagentCount}=await import('../src/chat-navigation.js');
 const parent={id:'root',subagentCount:123},projection={items:[{id:'first'}],total:123,unfilteredTotal:123,index:0,pages:3,start:0,end:50,scope:{sessionId:'root',filter:''}};
 const state={library:{bounded:true},sessions:[parent],view:{},subagentNavigation:projection};
 assert.equal(subagentCount(state,parent),123);assert.equal(subagentPage(state,parent),projection);
 state.view.subagentHistory={sessionId:'root',filter:'research',index:0};assert.equal(subagentPage(state,parent).pending,true);
 state.subagentNavigation={...projection,scope:{sessionId:'root',filter:'research'},total:60,unfilteredTotal:123,pages:2};
 assert.equal(subagentPage(state,parent),state.subagentNavigation);assert.equal(subagentCount(state,parent),123);
 state.view.subagentHistory.index=1;assert.equal(subagentPage(state,parent).pending,true);
 const other={id:'different-root',subagentCount:5};assert.equal(subagentCount(state,other),5,'a previously browsed parent cannot hide the current parent’s workers');
});
