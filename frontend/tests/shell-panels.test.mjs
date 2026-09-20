import {shellFor} from './shell-host.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatDetails}=await server.ssrLoadModule('/src/shell/navigation-components.jsx');
const {WorkspaceRail:Rail,ChatRename,AgentCanvas,A2UISurface,reopenCanvas,SessionHistoryControls}=await server.ssrLoadModule('/src/shell-panels.jsx');
function WorkspaceRail(props){return React.createElement(Rail,{...props,shell:shellFor(props.state,props.act)})}
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const initial=()=>({view:{navExpanded:true},workspaceExplorer:{path:'/',parentPath:null,breadcrumbs:[{name:'/',path:'/'}],filter:'',page:1,pages:1,totalWorkspaces:2,rows:[{path:'/one',name:'one',workspaceId:'one',chatCount:2,canBrowse:false,unread:0},{path:'/two',name:'two',workspaceId:'two',chatCount:1,canBrowse:false,unread:0}]},workspaces:[{id:'one',name:'One',path:'/one',available:true},{id:'two',name:'Two',path:'/two',available:true}],selectedWorkspaceId:'one',sessions:[{id:'a',title:'First plan',workspace:'/one'},{id:'b',title:'Another plan',workspace:'/one'},{id:'c',title:'Other workspace',workspace:'/two'}]});

test('workspace rail scopes chats to registered workspace and honors fnmatch filters',async()=>{
 const state=initial();state.view.navFilter='First*';let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true}),session:state.sessions[0]}))});
 const rows=root.root.findAll(node=>node.props.className==='a-nav-chat-select');
 assert.equal(rows.length,1);assert.equal(rows[0].props['aria-label'],'First plan');
 await renderAct(async()=>root.unmount());
});

test('all-chat switch hides the folder explorer without changing its location or new-chat target',async()=>{
 const state=initial(),calls=[];state.view.navWorkspacePath='/saved/folder';
 const act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state.view={...state.view,...args.patch};return {accepted:true}};
 let root;const render=()=>React.createElement(WorkspaceRail,{state,act,session:state.sessions[0]});
 await renderAct(async()=>{root=create(render())});
 const switcher=root.root.findByProps({role:'group','aria-label':'Chat view'});
 await renderAct(async()=>switcher.findAllByType('button')[1].props.onClick());
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{navChatScope:'all'}}});
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAllByProps({'data-part':'workspace-explorer'}).length,0);
 assert.deepEqual(root.root.findAll(node=>node.type==='small'&&node.props.className==='a-nav-chat-workspace').map(node=>node.children.join('')),['/one','/one','/two']);
 const newChat=root.root.findByProps({'aria-label':'New chat in workspace'});
 assert.equal(newChat.props.title,'New chat in /one');
 await renderAct(async()=>newChat.props.onClick());
 assert.ok(calls.some(call=>call.name==='session.create'&&call.args.workspace==='/one'));
 assert.equal(state.view.navWorkspacePath,'/saved/folder');
 await renderAct(async()=>root.root.findByProps({role:'group','aria-label':'Chat view'}).findAllByType('button')[0].props.onClick());
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAllByProps({'data-part':'workspace-explorer'}).length,1);
 assert.equal(state.view.navWorkspacePath,'/saved/folder');
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

test('rail pin, workspace selection, chat selection and drafts all use shared actions',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act,session:state.sessions[0]}))});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Pin navigation open'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{navPinned:true,navExpanded:true}}});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Open chats in /two'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'workspace.select',args:{id:'two'}});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Rename workspace'}).props.onClick());
 assert.deepEqual(calls.at(-1).args.patch.workspaceDraft,{mode:'rename',id:'one',name:'One'});
 await renderAct(async()=>root.root.findAll(node=>node.props.className==='a-nav-chat-select')[1].props.onClick());
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
 const render=()=>React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})});
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
 assert.deepEqual(calls.at(-1),{name:'canvas.close',args:{}});
 await renderAct(async()=>root.unmount());
});

test('reopening the canvas keeps its saved snapshot without creating another artifact',async()=>{
 let called;
 await reopenCanvas({canvas:{kind:'markdown',path:'/one/plan.md',title:'Plan',content:'Old content',events:[{}]}},async(name,args)=>{called={name,args}});
 assert.deepEqual(called,{name:'canvas.reopen',args:{}});
});


test('rename preserves failed text, prevents duplicate submission, and reflects agent renames',async()=>{
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
 assert.equal(root.root.findByType('input').props.value,'Agent supplied title');
 await renderAct(async()=>root.unmount());
});


test('automatic discovery reports loading/errors and refreshes through the shared action',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>calls.push({name,args});state.sharedHistory={loading:true};let root;
 const render=()=>React.createElement(WorkspaceRail,{state,act});
 await renderAct(async()=>{root=create(render())});
 assert.match(JSON.stringify(root.toJSON()),/Finding projects and chats/);
 assert.equal(root.root.findByProps({'aria-label':'Refresh workspaces and chats'}).props.disabled,true);
 state.sharedHistory={error:'One project could not be read.'};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({role:'alert'}).children[1].children.join(''),/could not be read/);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Refresh workspaces and chats'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'history.refresh',args:{}});
 await renderAct(async()=>root.unmount());
});

test('unresolved native project folders are hidden and cannot start a chat',async()=>{
 const state=initial();state.workspaces=[{id:'native-one',name:'One',path:null,available:false},{id:'native-two',name:'Two',path:null,available:false}];state.selectedWorkspaceId='native-two';state.workspaceExplorer={rows:[],totalWorkspaces:0};
 state.sessions=[{id:'a',title:'Project one chat',workspaceId:'native-one',workspace:null},{id:'b',title:'Project two chat',workspaceId:'native-two',workspace:null}];let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 const rows=root.root.findAll(node=>node.props.className==='a-nav-chat-select');
 assert.equal(rows.length,0);
 assert.equal(root.root.findAllByProps({'data-action':'workspace.select'}).length,0);
 assert.match(JSON.stringify(root.toJSON()),/Create a workspace to start a chat/);
 assert.equal(root.root.findByProps({'aria-label':'New chat in workspace'}).props.disabled,true);
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

test('remove chat confirmation preserves shared history explicitly',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};state.view.workspaceDraft={mode:'chat-delete',id:'a',name:'First plan'};let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act}))});
 assert.match(JSON.stringify(root.toJSON()),/shared history stays on disk/);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.deepEqual(calls[0],{name:'session.delete',args:{id:'a'}});
 await renderAct(async()=>root.unmount());
});

test('conversation pagination stays bounded and is shared with agents',async()=>{
 const state=initial();state.selectedSessionId='chat-0';state.sessions=Array.from({length:5000},(_,i)=>({id:'chat-'+i,title:'Saved '+i,workspace:'/one'}));
 const calls=[],act=async(name,args)=>{calls.push({name,args});if(name==='view.update')Object.assign(state.view,args.patch);return {accepted:true}};let root;
 const render=()=>React.createElement(WorkspaceRail,{state,act});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAll(node=>node.props.className==='a-nav-chat-select').length,100);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Show more conversations'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{navChatPage:{mode:'workspace',workspaceId:'one',filter:'',selectedSessionId:'chat-0',index:1}}}});
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAll(node=>node.props.className==='a-nav-chat-select')[0].props['aria-label'],'Saved 100');
 assert.equal(root.root.findAll(node=>node.props.className==='a-nav-chat-select').length,100);
 await renderAct(async()=>root.unmount());
});
