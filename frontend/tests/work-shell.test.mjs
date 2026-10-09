import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkNavigationContext,browsePatch}=await vite.ssrLoadModule('/src/work-navigation.js');
const {WorkSurface,LiveChatActivity}=await vite.ssrLoadModule('/src/work-shell.jsx');
const {ConversationList,useRecentShortcuts}=await vite.ssrLoadModule('/src/shell/navigation-components.jsx');
const {WorkspaceExplorer}=await vite.ssrLoadModule('/src/workspace-explorer.jsx');
const {AgentCanvas}=await vite.ssrLoadModule('/src/shell-panels.jsx');
const {newChatSetup}=await vite.ssrLoadModule('/src/new-chat.jsx');
const {fitPanels}=await vite.ssrLoadModule('/src/panel-layout.jsx');
const {WorkspacePicker}=await vite.ssrLoadModule('/src/workspace-setup.jsx');
const {WorkHeader}=await vite.ssrLoadModule('/src/work-shell.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
const workspace={id:'b',path:'/research',name:'Research',available:true};
const row={workspaceId:'b',path:'/research',name:'Research',chatCount:1};
const scope={mode:'all',workspaceId:null,filter:'',selectedSessionId:'a',section:'recent',showAgentCreated:false};
const page={items:[{id:'b-chat',title:'Research ideas',workspace:'/research',workspaceId:'b'}],scope,total:1,pages:1,index:0,start:0,end:1};
const snapshot={view:{},selectedSessionId:'a',selectedWorkspaceId:'b',workspaces:[workspace],library:{bounded:true,workspaceCount:1},workspaceShortcuts:[row],recentShortcuts:page.items,recentNavigation:{...page,remaining:0,limit:20,scope:{...scope,section:'shortcuts',showAgentCreated:false,limit:20,viewRevision:0}},workspaceExplorer:{rows:[row],mode:'recent'},sidebarNavigation:{pinned:{items:[],total:0,pages:1},recent:page,recentView:{}},sharedHistory:{}};
const host={instanceId:'chats',getSnapshot:()=>snapshot,subscribe:()=>()=>{},dispatch:async()=>({accepted:true})};
const navigation={browse(){},create(){},newChat(){}};
const render=child=>React.createElement(WorkNavigationContext.Provider,{value:navigation},child);
test('compact sidebar has shortcuts; filtering lives in the main surface',()=>{
 const html=renderToStaticMarkup(render(React.createElement(ConversationList,{host})));
 assert.match(html,/Search chats/);assert.match(html,/All workspaces/);
 assert.doesNotMatch(html,/All chats|Review chats|View all chats|of 1 recent chats|a-recent-controls/);
 assert.doesNotMatch(html,/Filter conversations|Filter workspaces|Browse folders/);
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>host};
 const main=renderToStaticMarkup(render(React.createElement(WorkSurface,{shell,state:{view:{workSurface:'chats'}},act:host.dispatch})));
 assert.match(main,/Research ideas/);assert.match(main,/Filter conversations/);
});
test('mismatched bounded Recent scope stays pending instead of using a local fallback',()=>{
 const mismatched={...snapshot,sidebarNavigation:{...snapshot.sidebarNavigation,
  recent:{...page,scope:{...scope,showAgentCreated:true}}}};
 const mismatchedHost={...host,getSnapshot:()=>mismatched};
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>mismatchedHost};
 const main=renderToStaticMarkup(render(React.createElement(WorkSurface,{shell,state:{view:{workSurface:'chats'}},act:host.dispatch})));
 assert.match(main,/Loading conversations/);assert.doesNotMatch(main,/Research ideas/);
});
test('workspace shortcut browses without changing execution scope',async()=>{
 const calls=[],selects=[];let root;
 await act(async()=>{root=create(React.createElement(WorkspaceExplorer,{state:snapshot,compact:true,act:async(...args)=>calls.push(args),onSelect:row=>selects.push(row.workspaceId)}))});
 await act(async()=>root.root.findByProps({'aria-label':'Open chats in /research'}).props.onClick());
 assert.deepEqual(selects,['b']);assert.deepEqual(calls,[]);
 assert.deepEqual(browsePatch('workspace','b'),{workSurface:'workspace',workWorkspaceId:'b',workWorkspaceTab:'chats'});
 await act(async()=>root.unmount());
});
test('row New chat is an adjacent standalone action bound to the row, not the global folder',async()=>{
 const calls=[],selects=[];let root;
 const state={...snapshot,settings:{workspace:'/wrong'},view:{workWorkspaceId:'elsewhere'}};
 await act(async()=>{root=create(React.createElement(WorkspaceExplorer,{state,compact:true,act:async(...args)=>{calls.push(args);return {accepted:true}},onSelect:row=>selects.push(row.workspaceId)}))});
 const button=root.root.findByProps({'aria-label':'New chat in /research'});
 assert.equal(button.type,'button');assert.equal(button.props.disabled,false);
 assert.notEqual(button.parent.type,'button');
 assert.equal(button.parent.findByProps({'aria-label':'Details and actions for Research'}).type,'button');
 let stopped=false;
 await act(async()=>button.props.onClick({stopPropagation(){stopped=true}}));
 assert.equal(stopped,true);assert.deepEqual(selects,[]);
 assert.deepEqual(calls,[['session.draft',{workspace:'/research',workspaceId:'b',location:{kind:'workspace'}}]]);
 await act(async()=>root.update(React.createElement(WorkspaceExplorer,{state:{...state,workspaceShortcuts:[{...row,available:false}]},compact:true,act:host.dispatch})));
 assert.equal(root.root.findByProps({'aria-label':'New chat in /research'}).props.disabled,true);
 await act(async()=>root.update(React.createElement(WorkspaceExplorer,{state:{...state,workspaceShortcuts:[{...row,path:'../invalid'}]},compact:true,act:host.dispatch})));
 assert.equal(root.root.findByProps({'aria-label':'New chat in ../invalid'}).props.disabled,true);
 await act(async()=>root.unmount());
});
test('workspace flyout pins and reorders the complete workspace vector, separately from chats',async()=>{
 const calls=[];let root,details;
 const state={...snapshot,pinnedWorkspaceIds:['unavailable','b','off-page'],pinnedSessionIds:['chat-pin'],workspaceShortcuts:[{...row,pinned:true}]};
 await act(async()=>{root=create(React.createElement(WorkspaceExplorer,{state,compact:true,act:async(...args)=>calls.push(args)}))});
 const navigationRow=root.root.find(node=>typeof node.props.details==='function'&&node.props.label==='Research');
 await act(async()=>{details=create(navigationRow.props.details({close(){}}))});
 await act(async()=>details.root.findByProps({'aria-label':'Move workspace up /research'}).props.onClick());
 assert.deepEqual(calls[0],['workspace.pinOrder',{ids:['b','unavailable','off-page']}]);
 await act(async()=>details.root.findByProps({'aria-label':'Move workspace down /research'}).props.onClick());
 assert.deepEqual(calls[1],['workspace.pinOrder',{ids:['unavailable','off-page','b']}]);
 await act(async()=>details.root.findByProps({'aria-label':'Unpin workspace /research'}).props.onClick());
 assert.deepEqual(calls[2],['workspace.pin',{id:'b',pinned:false}]);
 assert.deepEqual(state.pinnedWorkspaceIds,['unavailable','b','off-page']);
 assert.deepEqual(state.pinnedSessionIds,['chat-pin']);
 await act(async()=>details.unmount());await act(async()=>root.unmount());
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
test('new chat has no hidden global-folder fallback and respects visible workspace context',()=>{
 const state={settings:{workspace:'/hidden'},workspaces:[workspace],selectedWorkspaceId:'b',view:{}};
 assert.equal(newChatSetup(state).workspace,'/research');
 assert.equal(newChatSetup({...state,selectedWorkspaceId:null}).location.kind,'managed');
 assert.equal(newChatSetup({...state,view:{workSurface:'chats'}}).workspace,'');
 assert.equal(newChatSetup({...state,selectedWorkspaceId:null,view:{workSurface:'workspace',workWorkspaceId:'b'}}).workspace,'/research');
 assert.equal(newChatSetup({...state,view:{newSessionDraft:{workspace:'/explicit'}}}).location.kind,'workspace');
});
test('collapsed navigation gives all space back, including between chat and canvas',()=>{
 const collapsed=fitPanels({available:1000,canvasOpen:true,canvasWidth:900});
 assert.equal(collapsed.nav,0);assert.equal(collapsed.canvas,628);
 assert.equal(collapsed.nav+collapsed.canvas+360+12,1000);
 assert.equal(fitPanels({available:675,canvasOpen:true}).overlay,false);
 assert.equal(fitPanels({available:671,canvasOpen:true}).overlay,true);
 const expanded=fitPanels({available:1000,navPinned:true,canvasOpen:true});
 assert.ok(expanded.nav+expanded.canvas+360+24<=1000);
});
test('collapsed sidebar toggle lives in the header without duplicate chat controls',()=>{
 const html=renderToStaticMarkup(render(React.createElement(WorkHeader,{state:{view:{}},session:{id:'a',title:'Chat'},presentation:{},act:host.dispatch})));
 assert.match(html,/Open navigation/);assert.doesNotMatch(html,/Chat controls/);
 assert.match(html,/aria-label="Chat"[^>]*><svg[^>]*lucide-message-circle/);
 assert.doesNotMatch(html,/aria-label="Chat"[^>]*><svg[^>]*lucide-ellipsis/);
 assert.match(html,/<span>Chat<\/span>/);assert.match(html,/lucide-chevron-down/);
 const expanded=renderToStaticMarkup(render(React.createElement(WorkHeader,{state:{view:{navPinned:true}},presentation:{},act:host.dispatch})));
 assert.doesNotMatch(expanded,/Open navigation/);
 const draft=renderToStaticMarkup(render(React.createElement(WorkHeader,{state:{workspaces:[workspace],selectedWorkspaceId:'b',view:{newSessionDraft:{workspace:'',location:{kind:'managed'}}}},presentation:{},act:host.dispatch})));
 assert.doesNotMatch(draft,/Research/);
});
test('workspace picker searches the host catalog, pages results, and keeps actions separate',async()=>{
 let root;const calls=[],choices=[];
 const dispatch=async(name,args)=>{calls.push({name,args});return {accepted:true,result:{items:[{id:'w'+args.offset,name:'Reports',path:'/deep/team/'+(args.query||'q1')+'/reports'}],nextOffset:args.offset===0?40:null,total:41}}};
 await act(async()=>{root=create(React.createElement(WorkspacePicker,{state:{},setup:{workspace:'',location:{kind:'managed'}},act:dispatch,onChange:value=>choices.push(value)}))});
 await act(async()=>await new Promise(resolve=>setTimeout(resolve,20)));
 assert.equal(root.root.findAllByType('select').length,0);
 assert.deepEqual(calls[0],{name:'workspace.list',args:{query:'',offset:0,limit:40}});
 const button=text=>root.root.findAllByType('button').find(node=>node.children.includes(text));
 await act(async()=>button('Next').props.onClick());
 await act(async()=>await new Promise(resolve=>setTimeout(resolve,20)));
 assert.equal(calls.at(-1).args.offset,40);
 await act(async()=>root.root.findByProps({'aria-label':'Find a workspace'}).props.onChange({target:{value:'q4'}}));
 await act(async()=>await new Promise(resolve=>setTimeout(resolve,180)));
 assert.deepEqual(calls.at(-1).args,{query:'q4',offset:0,limit:40});
 await act(async()=>root.root.findByProps({'aria-label':'Reports · /deep/team/q4/reports'}).props.onClick());
 assert.deepEqual(choices,[{workspace:'/deep/team/q4/reports',location:{kind:'workspace'}}]);
 assert.ok(button('New workspace'));assert.ok(button('Use existing folder'));
 await act(async()=>root.unmount());
});

test('browsing retains live call controls and an explicitly scoped stop action',async()=>{
 const calls=[];let root;
 await act(async()=>{root=create(React.createElement(LiveChatActivity,{browsing:true,working:true,session:{id:'running',title:'Report'},callActive:true,voice:{status:'connected',muted:false},act:async(...args)=>calls.push(args)},React.createElement('div',{'aria-label':'Screen sharing active'},'Stop sharing')))});
 await act(async()=>root.root.findByProps({'data-action':'conversation.stop'}).props.onClick());
 await act(async()=>root.root.findByProps({'data-action':'call.end'}).props.onClick());
 await act(async()=>root.root.findByProps({'data-action':'call.mute'}).props.onClick());
 assert.deepEqual(calls,[['conversation.stop',{sessionId:'running'}],['call.end'],['call.mute',{muted:true}]]);
 assert.ok(root.root.findByProps({'aria-label':'Screen sharing active'}));
 await act(async()=>root.update(React.createElement(LiveChatActivity,{browsing:false,working:true,session:{id:'running'}})));
 assert.equal(root.root.findAllByProps({'data-action':'conversation.stop'}).length,0);
 await act(async()=>root.unmount());
});

test('call work stays stoppable in chat and targets the call even after navigating elsewhere',async()=>{
 const calls=[];let root;
 const callSession={id:'voice-chat',title:'Voice task',status:'working'};
 const render=props=>React.createElement(LiveChatActivity,{callActive:true,browsing:false,working:false,session:{id:'other-chat'},voice:{status:'connected',muted:true},callSession,act:async(...args)=>calls.push(args),...props});
 await act(async()=>{root=create(render())});
 const stop=()=>root.root.findByProps({'data-action':'conversation.stop'});
 assert.equal(stop().props.disabled,false);
 await act(async()=>stop().props.onClick());
 assert.deepEqual(calls,[['conversation.stop',{sessionId:'voice-chat'}]]);
 assert.match(root.root.findByProps({'data-action':'call.mute'}).props.title,/Unmute/);
 assert.match(root.root.findByProps({'data-action':'call.end'}).props.title,/work continues/);
 assert.ok(root.root.findAllByType('small').some(node=>node.children.includes('Microphone muted. Replies and work continue.')));
 await act(async()=>root.update(render({callSession:{...callSession,status:'stopping'}})));
 assert.equal(stop().props.disabled,true);
 assert.ok(stop().children.includes('Stopping work…'));
 // An idle foreground turn can still have active delegated work.
 await act(async()=>root.update(render({callSession:{...callSession,status:'idle',workers:[{status:'running'}]}})));
 assert.equal(stop().props.disabled,false);
 await act(async()=>root.update(render({callSession:{...callSession,historyReadOnlyReason:'Moved elsewhere'}})));
 assert.equal(stop().props.disabled,true);
 await act(async()=>root.update(render({callSession:{...callSession,status:'idle'}})));
 assert.equal(root.root.findAllByProps({'data-action':'conversation.stop'}).length,0);
 await act(async()=>root.update(render({callSession:undefined})));
 assert.equal(root.root.findAllByProps({'data-action':'conversation.stop'}).length,0);
 await act(async()=>root.unmount());
});
test('quiet navigation keeps discovery status, failures and refresh access',()=>{
 const state={...snapshot,sharedHistory:{loading:true,issueCount:2,error:'History unavailable'}};
 const html=renderToStaticMarkup(render(React.createElement(ConversationList,{host:{...host,getSnapshot:()=>state}})));
 assert.match(html,/Finding existing chats/);assert.match(html,/Some saved folders or chats need attention/);
 assert.match(html,/History unavailable/);assert.match(html,/Refresh workspaces and chats/);
});

test('agent-created reveal is one module-local presentation action',async()=>{
 const calls=[];let root;
 await act(async()=>{root=create(render(React.createElement(ConversationList,{host:{...host,dispatch:async(...args)=>calls.push(args)}})))});
 const button=root.root.findAllByType('button').find(node=>node.children.includes('Show agent-created'));
 assert.equal(button.props['aria-pressed'],false);
 await act(async()=>button.props.onClick());
 assert.deepEqual(calls,[['view.update',{patch:{navRecentLimit:20,navShowAgentCreated:true}}]]);
 await act(async()=>root.unmount());
});

function recentFixture(limit=20,showAgentCreated=false,viewRevision=0,total=125){
 const items=Array.from({length:Math.min(limit,total)},(_,i)=>({id:'recent-'+i,title:'Recent '+i,workspace:'/research',workspaceId:'b'}));
 return {items,total,remaining:total-items.length,end:items.length,limit,dataRevision:viewRevision,
  scope:{mode:'all',workspaceId:null,filter:'',selectedSessionId:'a',section:'shortcuts',showAgentCreated,limit,viewRevision}};
}
test('quiet Recent grows past 100 to natural exhaustion with only an explicit keyboard fallback',async()=>{
 let state={...snapshot,recentNavigation:recentFixture(20,false,0,101),view:{navRecentView:{navFilter:'retained'}}},root;
 const calls=[],listeners=new Set(),browsed=[];
 const dynamic={...host,getSnapshot:()=>state,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)},dispatch:async(name,args)=>{
  calls.push([name,args]);const {navRecentLimit,navShowAgentCreated}=args.patch;
  state={...state,view:{...state.view,...args.patch},recentNavigation:recentFixture(navRecentLimit,navShowAgentCreated,calls.length,101)};
  listeners.forEach(fn=>fn());return {accepted:true};
 }};
 await act(async()=>{root=create(React.createElement(WorkNavigationContext.Provider,{value:{...navigation,browse:kind=>browsed.push(kind)}},React.createElement(ConversationList,{host:dynamic})))});
 const button=text=>root.root.findAllByType('button').find(node=>node.children.includes(text));
 const rows=()=>root.root.findAll(node=>node.props['data-session-id']?.startsWith('recent-')&&node.type==='div');
 assert.equal(calls.length,0,'mount never fills the viewport');
 for(const limit of [20,40,60,80,100,120]){
  assert.equal(rows().length,Math.min(limit,101));
  assert.equal(new Set(rows().map(row=>row.props['data-session-id'])).size,rows().length);
  if(limit<120){
   assert.equal(button('Load older chats').type,'button');
   assert.ok(button('Load older chats').props.style.minHeight>=44);
   await act(async()=>button('Load older chats').props.onClick());
  }
 }
 assert.equal(button('Load older chats'),undefined);
 assert.equal(button('View all chats'),undefined);assert.equal(button('All chats'),undefined);
 await act(async()=>button('Show agent-created').props.onClick());
 assert.equal(rows().length,101);assert.equal(state.view.navRecentLimit,120);
 assert.deepEqual(state.view.navRecentView,{navFilter:'retained'});
 assert.deepEqual(browsed,[]);
 state={...state,recentNavigation:recentFixture(120,true,7,7)};
 await act(async()=>listeners.forEach(fn=>fn()));
 assert.equal(rows().length,7);assert.equal(button('Load older chats'),undefined);assert.equal(button('View all chats'),undefined);
 assert.equal(button('All chats'),undefined);
 assert.ok(calls.every(([name,args])=>name==='view.update'&&Object.keys(args.patch).sort().join(',')==='navRecentLimit,navShowAgentCreated'));
 await act(async()=>root.unmount());
});

test('failed Recent read retains rows; explicit Retry is shell.query, never a write replay',async()=>{
 let current={...snapshot,recentNavigation:recentFixture()},api,root;
 const calls=[];
 function Probe(){api=useRecentShortcuts({clientId:'test-client',instanceId:'chats',getSnapshot:()=>current},current,async(...args)=>{calls.push(args);return {accepted:true}});return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 await act(async()=>api.change({limit:40}));
 assert.equal(api.page.items.length,20);assert.match(api.error,/previous chats are kept/);
 const previousFetch=globalThis.fetch,reads=[];
 globalThis.fetch=async(_url,options)=>{
  reads.push(JSON.parse(options.body));
  return {ok:true,status:200,headers:{get:()=>null},text:async()=>JSON.stringify({accepted:true,result:{recentNavigation:recentFixture(40,false,1)}})};
 };
 try{
  await act(async()=>api.retry());
  assert.equal(api.page.items.length,40);assert.equal(api.error,'');
  assert.equal(calls.length,1);assert.equal(reads.length,1);assert.equal(reads[0].action,'shell.query');
  assert.deepEqual(reads[0].args,{clientId:'test-client',instanceId:'chats'});
 }finally{globalThis.fetch=previousFetch;await act(async()=>root.unmount())}
});

test('change and retry share synchronous singleflight; a late response cannot replace a changed scope',async()=>{
 let current={...snapshot,recentNavigation:recentFixture()},api,root;
 const held=[];
 function Probe(){api=useRecentShortcuts({getSnapshot:()=>current},current,()=>new Promise(resolve=>held.push(resolve)));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 let first;
 await act(async()=>{
  first=api.change({showAgentCreated:true});
  api.change({showAgentCreated:false});api.retry();
 });
 assert.equal(held.length,1);
 current={...current,selectedSessionId:'other',recentNavigation:{...recentFixture(20,true,1),
  scope:{...recentFixture(20,true,1).scope,selectedSessionId:'a'}}};
 await act(async()=>{held[0]({accepted:true});await first;root.update(React.createElement(Probe))});
 assert.equal(api.page.scope.viewRevision,0);assert.equal(api.page.scope.showAgentCreated,false);
 assert.match(api.error,/previous chats are kept/);
 await act(async()=>root.unmount());
});

test('Retry prefers a newer host snapshot received while its read was delayed',async()=>{
 let current={...snapshot,recentNavigation:recentFixture(40,false,1)},api,root;
 const writes=[];
 function Probe(){api=useRecentShortcuts({clientId:'retry-client',instanceId:'chats',getSnapshot:()=>current},current,async(...args)=>{writes.push(args);return {accepted:true}});return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 await act(async()=>api.change({limit:60}));
 assert.equal(api.page.limit,40);assert.equal(api.blocked,true);
 const previousFetch=globalThis.fetch;
 let resolveRead;
 globalThis.fetch=()=>new Promise(resolve=>{resolveRead=resolve});
 try{
  let retry;
  await act(async()=>{retry=api.retry()});
  current={...current,recentNavigation:recentFixture(80,true,3)};
  await act(async()=>root.update(React.createElement(Probe)));
  await act(async()=>{
   resolveRead({ok:true,status:200,headers:{get:()=>null},text:async()=>JSON.stringify({accepted:true,result:{recentNavigation:recentFixture(60,false,2)}})});
   await retry;
  });
  assert.equal(api.page.limit,80);assert.equal(api.settings.showAgentCreated,true);
  assert.equal(api.page.scope.viewRevision,3);assert.equal(api.blocked,false);
  assert.equal(writes.length,1);
 }finally{globalThis.fetch=previousFetch;await act(async()=>root.unmount())}
});

test('failed Load older requires reconciliation and never skips to a larger limit',async()=>{
 let root;const writes=[];
 const state={...snapshot,recentNavigation:recentFixture()};
 await act(async()=>{root=create(render(React.createElement(ConversationList,{host:{...host,getSnapshot:()=>state,dispatch:async(...args)=>{writes.push(args);throw Error('Unconfirmed Recent write')}}})))});
 const button=text=>root.root.findAllByType('button').find(node=>node.children.includes(text));
 await act(async()=>button('Load older chats').props.onClick());
 assert.equal(button('Load older chats').props['aria-disabled'],true);
 await act(async()=>button('Load older chats').props.onClick());
 await act(async()=>button('Show agent-created').props.onClick());
 assert.equal(writes.length,1);assert.equal(writes[0][1].patch.navRecentLimit,40);
 assert.ok(button('Retry'));
 const previousFetch=globalThis.fetch;
 let resolveRead;
 globalThis.fetch=()=>new Promise(resolve=>{resolveRead=resolve});
 try{
  let retry;
  await act(async()=>{retry=button('Retry').props.onClick();button('Retry').props.onClick()});
  assert.equal(button('Show agent-created').props['aria-disabled'],true);
  await act(async()=>button('Show agent-created').props.onClick());
  assert.equal(writes.length,1);
  await act(async()=>{
   resolveRead({ok:true,status:200,headers:{get:()=>null},text:async()=>JSON.stringify({accepted:true,result:{recentNavigation:recentFixture()}})});
   await retry;
  });
  assert.equal(button('Show agent-created').props['aria-disabled'],false);
 }finally{globalThis.fetch=previousFetch;await act(async()=>root.unmount())}
});

test('old matching limit with no new revision is not a successful write acknowledgement',async()=>{
 let api,root;const current={...snapshot,recentNavigation:recentFixture()};
 function Probe(){api=useRecentShortcuts({getSnapshot:()=>current},current,async()=>({accepted:true}));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 await act(async()=>api.change({showAgentCreated:false}));
 assert.equal(api.blocked,true);assert.equal(api.page.scope.viewRevision,0);
 assert.match(api.error,/previous chats are kept/);
 await act(async()=>root.unmount());
});

test('expanded Recent scope uses independent host scope, not a late response self-matching its identity',async()=>{
 const expected={mode:'workspace',workspaceId:'b',filter:'',clientId:'scope-client',instanceId:'chats',generation:2};
 let current={...snapshot,generation:2,recentScope:expected,recentNavigation:{...recentFixture(),
  scope:{...recentFixture().scope,...expected}}},api,root;
 const scopedHost={clientId:'scope-client',instanceId:'chats',getSnapshot:()=>current};
 function Probe(){api=useRecentShortcuts(scopedHost,current,async()=>({accepted:true}));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 for(const [key,value] of Object.entries({mode:'all',workspaceId:'elsewhere',clientId:'other',instanceId:'other',generation:3})){
  current={...current,recentNavigation:{...recentFixture(40,false,10),
   scope:{...recentFixture(40,false,10).scope,...expected,[key]:value}}};
  await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(api.page.limit,20,key);
 }
 await act(async()=>root.unmount());
});

test('a stale origin scope cannot self-match after the saved origin setting changes',async()=>{
 let current={...snapshot,recentNavigation:recentFixture(),view:{navShowAgentCreated:false}},api,root;
 function Probe(){api=useRecentShortcuts({getSnapshot:()=>current},current,async()=>({accepted:true}));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 current={...current,view:{navShowAgentCreated:true},recentNavigation:recentFixture(40,false,2)};
 await act(async()=>root.update(React.createElement(Probe)));
 assert.equal(api.page.limit,20);assert.equal(api.page.scope.showAgentCreated,false);
 current={...current,recentNavigation:recentFixture(40,true,2)};
 await act(async()=>root.update(React.createElement(Probe)));
 assert.equal(api.page.limit,40);assert.equal(api.page.scope.showAgentCreated,true);
 await act(async()=>root.unmount());
});
