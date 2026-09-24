import {shellFor} from './shell-host.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatDetails,WorkspaceManager}=await server.ssrLoadModule('/src/shell/navigation-components.jsx');
const {WorkspaceRail:Rail,ChatRename,AgentCanvas,A2UISurface,reopenCanvas,SessionHistoryControls}=await server.ssrLoadModule('/src/shell-panels.jsx');
function WorkspaceRail(props){return React.createElement(Rail,{...props,shell:shellFor(props.state,props.act)})}
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const initial=()=>({view:{navExpanded:true,navSimple:false},workspaceExplorer:{path:'/',parentPath:null,breadcrumbs:[{name:'/',path:'/'}],filter:'',page:1,pages:1,totalWorkspaces:2,rows:[{path:'/one',name:'one',workspaceId:'one',chatCount:2,canBrowse:false,unread:0},{path:'/two',name:'two',workspaceId:'two',chatCount:1,canBrowse:false,unread:0}]},workspaces:[{id:'one',name:'One',path:'/one',available:true},{id:'two',name:'Two',path:'/two',available:true}],selectedWorkspaceId:'one',sessions:[{id:'a',title:'First plan',workspace:'/one'},{id:'b',title:'Another plan',workspace:'/one'},{id:'c',title:'Other workspace',workspace:'/two'}]});

test('workspace drill-in filters independently of Recent',async()=>{
 const state=initial();state.view.navWorkspaceList=false;state.view.navFilter='First*';let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 const workspace=root.root.findByProps({'aria-label':'Chats in One'});
 assert.deepEqual(workspace.findAll(node=>node.props.className==='a-nav-chat-select').map(n=>n.props['aria-label']),['First plan']);
 assert.equal(root.root.findByProps({'data-sidebar-section':'recent'}).findAllByProps({className:'a-nav-chat-select'}).length,3);
 await renderAct(async()=>root.unmount());
});

test('three sections collapse independently without changing drafts, selection or folder browsing',async()=>{
 const state=initial(),calls=[];state.view.navWorkspacePath='/saved/folder';state.view.draft='Unsent message';
 const act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state.view={...state.view,...args.patch};return {accepted:true}};
 let root;const render=()=>React.createElement(WorkspaceRail,{state,act});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAllByProps({'aria-label':'Chat view'}).length,0);
 const section=id=>root.root.findByProps({'data-sidebar-section':id});
 for(const id of ['pinned','workspaces','recent']){
  await renderAct(async()=>section(id).findAllByType('button')[0].props.onClick());
  await renderAct(async()=>root.update(render()));
  assert.equal(section(id).findAllByType('button')[0].props['aria-expanded'],false);
 }
 assert.deepEqual(state.view.navSectionsCollapsed,['pinned','workspaces','recent']);
 await renderAct(async()=>section('workspaces').findAllByType('button')[0].props.onClick());
 await renderAct(async()=>root.update(render()));
 assert.equal(section('workspaces').findAllByType('button')[0].props['aria-expanded'],true);
 assert.equal(section('recent').findAllByType('button')[0].props['aria-expanded'],false);
 assert.equal(state.view.navWorkspacePath,'/saved/folder');assert.equal(state.view.draft,'Unsent message');
 assert.ok(calls.every(call=>call.name==='view.update'));
 await renderAct(async()=>root.root.findByProps({'aria-label':'New chat'}).props.onClick());
 assert.ok(calls.some(call=>call.name==='session.draft'));
 await renderAct(async()=>root.unmount());
});

test('pin and unpin use shared actions without selecting chats or rewriting activity',async()=>{
 const state=initial(),calls=[];state.view.navChatScope='all';state.pinnedSessionIds=['b'];
 state.sessions[0].recentActivityAt=30;state.sessions[1].recentActivityAt=1;state.sessions[2].recentActivityAt=20;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 let root;const render=()=>React.createElement(WorkspaceRail,{state,act,session:state.sessions[0]});
 await renderAct(async()=>{root=create(render())});
 const rows=()=>root.root.findAll(node=>node.type==='div'&&node.props['data-session-id']).map(node=>node.props['data-session-id']);
 assert.deepEqual(rows(),['b','a','c']);
 assert.equal(root.root.findByProps({'aria-label':'Pinned chats'}).findAll(node=>node.type==='div'&&node.props['data-session-id']).length,1);
 let detail;const renderDetail=chat=>React.createElement(ChatDetails,{chat,model:{state,act,draft:{},choose:()=>{},setDraft:()=>{}},now:100,close:()=>{}});
 await renderAct(async()=>{detail=create(renderDetail({...state.sessions[1],pinned:true}))});
 const unpin=detail.root.findByProps({'aria-label':'Unpin Another plan'});
 assert.equal(unpin.props['aria-pressed'],true);
 await renderAct(async()=>unpin.props.onClick());
 assert.deepEqual(calls,[{name:'session.pin',args:{id:'b',pinned:false}}]);
 state.pinnedSessionIds=[];
 await renderAct(async()=>root.update(render()));
 assert.deepEqual(rows(),['a','c','b']);
 await renderAct(async()=>detail.update(renderDetail(state.sessions[2])));
 await renderAct(async()=>detail.root.findByProps({'aria-label':'Pin Other workspace'}).props.onClick());
 await renderAct(async()=>detail.unmount());
 assert.deepEqual(calls.at(-1),{name:'session.pin',args:{id:'c',pinned:true}});
 assert.deepEqual(state.sessions.map(row=>row.recentActivityAt),[30,1,20]);
 assert.equal(calls.some(call=>call.name==='session.select'),false);
 await renderAct(async()=>root.unmount());
});

test('sidebar collapse, workspace drill-in and chat selection use shared actions',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act}))});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Close navigation'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{navPinned:false,navExpanded:false}}});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Open chats in /two'}).props.onClick());
 assert.ok(calls.some(call=>call.name==='workspace.select'&&call.args.id==='two'));
 assert.equal(calls.at(-1).args.patch.navWorkspaceList,false);
 await renderAct(async()=>root.root.findAllByProps({className:'a-nav-chat-select'})[1].props.onClick());
 assert.ok(calls.some(call=>call.name==='session.select'&&call.args.id==='b'));
 await renderAct(async()=>root.unmount());
});

test('chat rename opens the flyout editor and submits its new title',async()=>{
 const state=initial(),chat=state.sessions[1],calls=[];
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const setDraft=value=>{state.view.workspaceDraft=value;calls.push({name:'view.update',args:{patch:{workspaceDraft:value}}})};
 let root;const render=()=>React.createElement(ChatDetails,{chat,model:{state,act,draft:state.view.workspaceDraft||{},setDraft,prefix:'nav-chats'},now:100,close:()=>{}});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Rename Another plan'}).props.onClick());
 await renderAct(async()=>root.update(render()));
 const input=root.root.findByProps({id:'nav-chats-name'});assert.equal(input.props.value,'Another plan');
 await renderAct(async()=>input.props.onChange({target:{value:'Renamed plan'}}));
 await renderAct(async()=>root.root.findByType(ChatRename).findByType('form').props.onSubmit({preventDefault(){}}));
 assert.ok(calls.some(call=>call.name==='session.rename'&&call.args.id==='b'&&call.args.title==='Renamed plan'));
 assert.equal(calls.filter(call=>call.name==='view.update').length,2);
 await renderAct(async()=>root.unmount());
});

test('registration removal requires explicit confirm and preserves unrelated chat actions',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};state.view.workspaceDraft={mode:'remove',id:'two',name:'Two'};let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act}))});
 assert.match(JSON.stringify(root.toJSON()),/files and chats will stay/);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.deepEqual(calls[0],{name:'workspace.remove',args:{id:'two'}});
 assert.equal(calls.some(call=>call.name==='session.delete'),false);
 await renderAct(async()=>root.unmount());
});

test('bounded workspace registrations use the full catalog count for removal availability',async()=>{
 const state=initial();state.workspaces=state.workspaces.slice(0,1);state.library={bounded:true,workspaceCount:3000};let root;
 const render=()=>React.createElement(WorkspaceManager,{host:shellFor(state,async()=>({accepted:true})).hostFor({id:'workspaces'})});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findByProps({'aria-label':'Remove workspace registration'}).props.disabled,false);
 state.library.workspaceCount=1;await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByProps({'aria-label':'Remove workspace registration'}).props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('A2UI button records exactly the declared action while text stays inert',async()=>{
 const calls=[],surface={surfaceId:'plan',root:'root',components:[{id:'root',component:{Column:{children:{explicitList:['intro','button']}}}},{id:'intro',component:{Text:{text:{literalString:'<script>window.evil=true</script>'}}}},{id:'button',component:{Button:{child:'label',action:{name:'review'}}}},{id:'label',component:{Text:{text:{literalString:'Review plan'}}}}]};let root;
 await renderAct(async()=>{root=create(React.createElement(A2UISurface,{surface,act:async(name,args)=>calls.push({name,args})}))});
 assert.equal(root.root.findAllByType('script').length,0);
 await renderAct(async()=>root.root.findByType('button').props.onClick());
 assert.deepEqual(calls,[{name:'canvas.event',args:{surfaceId:'plan',componentId:'button',name:'review'}}]);
 await renderAct(async()=>root.unmount());
});

test('canvas keyboard resize persists bounded width and closes through public action',async()=>{
 const calls=[],state={view:{canvasWidth:440},canvas:{open:true,kind:'text',content:'Document',title:'Preview'}},act=async(name,args)=>calls.push({name,args});let root;
 await renderAct(async()=>{root=create(React.createElement(AgentCanvas,{state,act}))});
 await renderAct(async()=>root.root.findByProps({role:'separator'}).props.onKeyDown({key:'ArrowLeft',preventDefault(){}}));
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{canvasWidth:460}}});
 await renderAct(async()=>root.root.findByProps({role:'separator'}).props.onKeyDown({key:'End',preventDefault(){}}));
 assert.equal(calls.at(-1).args.patch.canvasWidth,root.root.findByProps({role:'separator'}).props['aria-valuemax']);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Close canvas panel'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'canvas.visibility',args:{open:false}});
 await renderAct(async()=>root.unmount());
});

test('reopening the canvas keeps its saved snapshot without creating another artifact',async()=>{
 let called;
 await reopenCanvas({canvas:{kind:'markdown',path:'/one/plan.md',title:'Plan',content:'Old content',events:[{}]}},async(name,args)=>{called={name,args}});
 assert.deepEqual(called,{name:'canvas.visibility',args:{open:true}});
});


test('rename preserves failed text and newer drafts across external renames',async()=>{
 let finish;const calls=[];let cancelled=0;
 const act=async(name,args)=>{calls.push({name,args});return await new Promise(resolve=>finish=resolve)};
 let chat={id:'a',title:'Original'},root;
 const render=()=>React.createElement(ChatRename,{chat,act,cancel:()=>cancelled++});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByType('input').props.onChange({target:{value:'My new name'}}));
 let pending;
 await renderAct(async()=>{pending=root.root.findByType('form').props.onSubmit({preventDefault(){}})});
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.equal(calls.length,1);
 await renderAct(async()=>{finish(undefined);await pending});
 assert.equal(cancelled,0);assert.equal(root.root.findByType('input').props.value,'My new name');
 assert.equal(root.root.findByProps({role:'alert'}).children.length,1);
 chat={...chat,title:'Agent supplied title'};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByType('input').props.value,'My new name');
 await renderAct(async()=>root.unmount());
});


test('automatic discovery reports loading/errors and refreshes through the shared action',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>calls.push({name,args});state.sharedHistory={loading:true};let root;
 const render=()=>React.createElement(WorkspaceRail,{state,act});
 await renderAct(async()=>{root=create(render())});
 assert.match(JSON.stringify(root.toJSON()),/Finding existing chats/);
 assert.equal(root.root.findByProps({'aria-label':'Refresh workspaces and chats'}).props.disabled,true);
 state.sharedHistory={error:'One project could not be read.'};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({role:'alert'}).children.join(''),/could not be read/);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Refresh workspaces and chats'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'history.refresh',args:{}});
 await renderAct(async()=>root.unmount());
});

test('unresolved folders are hidden but the launcher lets users choose a valid workspace',async()=>{
 const state=initial();state.workspaces=[{id:'native-one',name:'One',path:null,available:false},{id:'native-two',name:'Two',path:null,available:false}];state.selectedWorkspaceId='native-two';state.workspaceExplorer={rows:[],totalWorkspaces:0};
 state.sessions=[{id:'a',title:'Project one chat',workspaceId:'native-one',workspace:null},{id:'b',title:'Project two chat',workspaceId:'native-two',workspace:null}];let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 const rows=root.root.findAll(node=>node.props.className==='a-nav-chat-select');
 assert.equal(rows.length,0);
 assert.equal(root.root.findAllByProps({'data-action':'workspace.select'}).length,0);
 assert.match(JSON.stringify(root.toJSON()),/Create a workspace to start a chat/);
 assert.ok(!root.root.findByProps({'aria-label':'New chat'}).props.disabled);
 await renderAct(async()=>root.unmount());
});

test('native history paging, failures and unavailable workspaces have visible shared controls',async()=>{
 const calls=[],act=async(name,args)=>calls.push({name,args});let loadingOlder=0;
 let session={id:'native-chat',historyLoaded:false,historyLoading:true,sharedHistoryOffset:80};let root;
 const render=()=>React.createElement(SessionHistoryControls,{session,act,onLoadEarlier:()=>loadingOlder++});
 await renderAct(async()=>{root=create(render())});
 assert.match(JSON.stringify(root.toJSON()),/Loading conversation/);
 assert.equal(root.root.findByProps({'data-action':'session.history'}).props.disabled,true);
 session={...session,historyLoaded:true,historyLoading:false};
 await renderAct(async()=>root.update(render()));
 await renderAct(async()=>root.root.findByProps({'data-action':'session.history'}).props.onClick());
 assert.equal(loadingOlder,1);assert.deepEqual(calls.at(-1),{name:'session.history',args:{id:'native-chat',before:80,limit:100}});
 session={...session,historyError:'The transcript could not be read.',workspaceAvailable:false};
 await renderAct(async()=>root.update(render()));
 assert.match(JSON.stringify(root.toJSON()),/read its saved chats/);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
 await renderAct(async()=>root.root.findByProps({'data-action':'session.select'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'session.select',args:{id:'native-chat'}});
 await renderAct(async()=>root.unmount());
});

test('workspace chat actions offer Archive but neither Remove nor Delete',async()=>{
 const state=initial(),calls=[];let root;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const model={state,act,draft:{},setDraft:value=>calls.push(value),choose:()=>{}};
 await renderAct(async()=>{root=create(React.createElement(ChatDetails,{chat:state.sessions[0],model,now:100,close:()=>{}}))});
 const labels=root.root.findAllByType('button').map(node=>node.props['aria-label']||node.children.join(''));
 assert.ok(labels.includes('Archive First plan'));assert.ok(!labels.some(label=>/Remove|Delete/.test(label)));
 await renderAct(async()=>root.update(React.createElement(ChatDetails,{chat:{...state.sessions[0],location:{kind:'managed'}},model,now:100,close:()=>{}})));
 await renderAct(async()=>root.root.findByProps({'aria-label':'Delete First plan'}).props.onClick());
 assert.deepEqual(calls,[{mode:'chat-delete',id:'a',name:'First plan'}]);
 assert.match(JSON.stringify(root.toJSON()),/No workspace/);
 await renderAct(async()=>root.unmount());
});

test('Recent pagination stays bounded and is shared with agents',async()=>{
 const state=initial();state.selectedSessionId='chat-0';state.sessions=Array.from({length:5000},(_,i)=>({id:'chat-'+i,title:'Saved '+i,workspace:'/one'}));
 const calls=[],act=async(name,args)=>{calls.push({name,args});if(name==='view.update')Object.assign(state.view,args.patch);return {accepted:true}};let root;
 const render=()=>React.createElement(WorkspaceRail,{state,act});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAllByProps({className:'a-nav-chat-select'}).length,40);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Show more conversations'}).props.onClick());
 assert.deepEqual(calls.at(-1).args.patch.navRecentView.navChatPage,{mode:'all',workspaceId:null,filter:'',selectedSessionId:'chat-0',section:'recent',index:1});
 await renderAct(async()=>root.update(render()));
 const rows=root.root.findAllByProps({className:'a-nav-chat-select'});
 assert.equal(rows[0].props['aria-label'],'Saved 40');assert.equal(rows.length,40);
 await renderAct(async()=>root.unmount());
});


test('one sidebar has Pinned, Workspaces and Recent with no duplicate recent pins',async()=>{
 const state=initial();state.pinnedSessionIds=['a'];state.view.navWorkspaceList=true;
 let root;await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 assert.deepEqual(root.root.findAll(node=>typeof node.type==='string'&&node.props['data-sidebar-section']).map(node=>node.props['data-sidebar-section']),['pinned','workspaces','recent']);
 assert.equal(root.root.findAllByProps({'data-session-id':'a'}).filter(node=>typeof node.type==='string').length,1);
 assert.equal(root.root.findAllByProps({'data-session-id':'b'}).filter(node=>typeof node.type==='string').length,1);
 assert.equal(root.root.findAllByProps({'data-session-id':'c'}).filter(node=>typeof node.type==='string').length,1);
 assert.equal(root.root.findAllByProps({'data-part':'workspace-explorer'}).length,1);
 assert.equal(root.root.findAllByProps({'aria-label':'New workspace'}).length,1);
 await renderAct(async()=>root.unmount());
});


test('single workspace explorer expands its preview including empty registered folders',async()=>{
 const state=initial();state.workspaceExplorer.mode='recent';
 state.workspaceExplorer.rows=Array.from({length:12},(_,i)=>({workspaceId:'w'+i,path:'/w'+i,name:'Folder '+i,chatCount:0}));
 let root;await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 assert.equal(root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='workspace.select').length,8);
 const more=root.root.findAllByType('button').find(button=>button.children[0]==='More workspaces');
 await renderAct(async()=>more.props.onClick());
 assert.equal(root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='workspace.select').length,12);
 assert.equal(root.root.findAllByProps({'data-part':'workspace-explorer'}).length,1);
 await renderAct(async()=>root.unmount());
});

function pinnedNavigation(simple){
 const state=initial();state.view.navSimple=simple;state.view.navWorkspaceList=false;
 state.pinnedSessionIds=['a','hidden','b','off-page'];
 state.homeNavigation={items:state.sessions.slice(0,2).map(chat=>({...chat,pinned:true}))};
 state.workspaceOverview={items:state.workspaces};return state;
}
for(const simple of [true,false]){
 const label=simple?'default sidebar':'All chats sidebar';
 test(`${label} restores keyboard pin ordering without dropping hidden pins`,async()=>{
  const state=pinnedNavigation(simple),calls=[];let root;
  await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async(name,args)=>{calls.push({name,args});return {accepted:true}}}))});
  const handle=title=>root.root.findByProps({'aria-label':'Reorder '+title});
  const key=(key,altKey=true)=>({key,altKey,preventDefault(){}});
  await renderAct(async()=>{handle('First plan').props.onKeyDown(key('ArrowUp'));handle('Another plan').props.onKeyDown(key('ArrowDown'))});
  assert.equal(calls.length,0);
  await renderAct(async()=>handle('Another plan').props.onKeyDown(key('ArrowUp')));
  assert.deepEqual(calls.at(-1),{name:'session.pinOrder',args:{ids:['b','a','hidden','off-page']}});
  await renderAct(async()=>handle('First plan').props.onKeyDown(key('ArrowDown')));
  assert.deepEqual(calls.at(-1),{name:'session.pinOrder',args:{ids:['hidden','b','a','off-page']}});
  assert.ok(calls.every(call=>call.name==='session.pinOrder'));
  await renderAct(async()=>root.unmount());
 });
 test(label+' uses the shared Settings reorder control and full chat contents',async()=>{
  const state=pinnedNavigation(simple);let root;
  await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
  const list=root.root.findByProps({className:'a-reorder-list a-pinned-chats'});
  assert.equal(typeof list.props.onPointerMove,'function');
  const handle=root.root.findByProps({'aria-label':'Reorder Another plan'});
  assert.equal(handle.props.draggable,false);assert.equal(typeof handle.props.onPointerDown,'function');
  const pinned=root.root.findByProps({'data-sidebar-section':'pinned'});
  assert.equal(pinned.findAllByProps({className:'a-nav-chat-workspace'}).length,2);
  assert.equal(pinned.findAllByProps({'data-view-source':'activity-time'}).length,2);
  await renderAct(async()=>root.unmount());
 });
}
test('default sidebar prevents duplicate pending reorders and allows retry after a rejected receipt',async()=>{
 const state=pinnedNavigation(true),calls=[];let root,settle;
 const act=async(name,args)=>{calls.push({name,args});return calls.length===1?new Promise(resolve=>{settle=resolve}):{accepted:true}};
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act}))});
 const handle=()=>root.root.findByProps({'aria-label':'Reorder Another plan'});
 const event={altKey:true,key:'ArrowUp',preventDefault(){}};
 await renderAct(async()=>{handle().props.onKeyDown(event);handle().props.onKeyDown(event)});
 assert.equal(calls.length,1);assert.equal(handle().props['aria-disabled'],true);assert.equal(handle().props.draggable,false);
 await renderAct(async()=>settle({accepted:true,result:{accepted:false,error:'Pin order changed. Please retry.'}}));
 assert.equal(!!handle().props['aria-disabled'],false);
 assert.ok(root.root.findAllByProps({role:'alert'}).some(node=>node.children.includes('Pin order changed. Please retry.')));
 assert.deepEqual(state.pinnedSessionIds,['a','hidden','b','off-page']);
 await renderAct(async()=>handle().props.onKeyDown(event));assert.equal(calls.length,2);
 assert.equal(root.root.findAllByProps({role:'alert'}).some(node=>node.children.includes('Pin order changed. Please retry.')),false);
 await renderAct(async()=>root.unmount());
});


test('inline Auto name now shares the naming action and preserves policy and newer drafts',async()=>{
 let finish;const calls=[];let cancelled=0;
 const act=async(name,args)=>{calls.push({name,args});return new Promise(resolve=>finish=resolve)};
 let chat={id:'native-a',title:'Original',autoName:false,status:'idle'},root;
 const render=()=>React.createElement(ChatRename,{chat,act,cancel:()=>cancelled++});
 await renderAct(async()=>{root=create(render())});
 const generate=()=>root.root.findByProps({'data-action':'session.naming'});
 assert.match(root.root.findByProps({className:'a-nav-name-help'}).children.join(''),/Future automatic naming stays off/);
 let pending;
 await renderAct(async()=>{pending=generate().props.onClick()});
 await renderAct(async()=>generate().props.onClick());
 assert.deepEqual(calls,[{name:'session.naming',args:{id:'native-a',regenerate:true}}]);
 await renderAct(async()=>{finish({accepted:true});await pending});
 assert.equal(cancelled,0);
 chat={...chat,naming:{status:'working'}};
 await renderAct(async()=>root.update(render()));
 assert.equal(generate().props.disabled,true);
 assert.match(root.root.findByProps({role:'status'}).children.join(''),/Generating/);
 await renderAct(async()=>root.root.findByType('input').props.onChange({target:{value:'My newer draft'}}));
 chat={...chat,title:'Generated title',naming:{status:'ready'}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByType('input').props.value,'My newer draft');
 assert.equal(generate().props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('inline automatic name reflects completed results and recoverable errors',async()=>{
 let chat={id:'a',title:'Original',status:'working'},root;
 const render=()=>React.createElement(ChatRename,{chat,act:async()=>({accepted:false,error:'Retry later'}),cancel:()=>{}});
 await renderAct(async()=>{root=create(render())});
 const generate=()=>root.root.findByProps({'data-action':'session.naming'});
 assert.equal(generate().props.disabled,true);
 chat={...chat,status:'idle'};
 await renderAct(async()=>root.update(render()));
 await renderAct(async()=>generate().props.onClick());
 assert.match(root.root.findByProps({role:'alert'}).children.join(''),/Retry later/);
 assert.equal(root.root.findByType('input').props.value,'Original');
 chat={...chat,title:'New automatic name',naming:{status:'ready'}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByType('input').props.value,'New automatic name');
 await renderAct(async()=>root.unmount());
});
