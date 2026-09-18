import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkspaceRail,ChatRename,AgentCanvas,A2UISurface,reopenCanvas}=await server.ssrLoadModule('/src/shell-panels.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const initial=()=>({view:{navExpanded:true},workspaces:[{id:'one',name:'One',path:'/one'},{id:'two',name:'Two',path:'/two'}],selectedWorkspaceId:'one',sessions:[{id:'a',title:'First plan',workspace:'/one'},{id:'b',title:'Another plan',workspace:'/one'},{id:'c',title:'Other workspace',workspace:'/two'}]});

test('workspace rail scopes chats to registered workspace and honors fnmatch filters',async()=>{
 const state=initial();state.view.navFilter='First*';let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true}),session:state.sessions[0]}))});
 const rows=root.root.findAll(node=>node.props.className==='a-nav-chat-select');
 assert.equal(rows.length,1);assert.equal(rows[0].props.title,'First plan');
 await renderAct(async()=>root.unmount());
});

test('rail pin, workspace selection, chat selection and drafts all use shared actions',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act,session:state.sessions[0]}))});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Pin navigation open'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{navPinned:true,navExpanded:true}}});
 await renderAct(async()=>root.root.findByProps({id:'nav-workspace'}).props.onChange({target:{value:'two'}}));
 assert.deepEqual(calls.at(-1),{name:'workspace.select',args:{id:'two'}});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Rename workspace'}).props.onClick());
 assert.deepEqual(calls.at(-1).args.patch.workspaceDraft,{mode:'rename',id:'one',name:'One'});
 await renderAct(async()=>root.root.findAll(node=>node.props.className==='a-nav-chat-select')[1].props.onClick());
 assert.ok(calls.some(call=>call.name==='session.select'&&call.args.id==='b'));
 await renderAct(async()=>root.unmount());
});

test('chat rename opens the editor for that conversation and submits its new title',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state.view={...state.view,...args.patch};return {accepted:true}};let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act,session:state.sessions[0]}))});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Rename Another plan'}).props.onClick());
 await renderAct(async()=>root.update(React.createElement(WorkspaceRail,{state,act,session:state.sessions[0]})));
 const input=root.root.findByProps({id:'nav-workspace-name'});
 assert.equal(input.props.value,'Another plan');
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
