import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {SubagentHistory,SubagentHistoryButton}=await server.ssrLoadModule('/src/subagent-history.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

test('subagent drilldown is bounded, searchable, and uses shared view and session actions',async()=>{
 const parent={id:'root',title:'A project',sessionKind:'root'},state={view:{},sessions:[parent,...Array.from({length:123},(_,index)=>({id:'worker-'+index,title:'Research '+index,sessionKind:'worker',parentId:'root'})),{id:'fork',title:'Independent fork',parentId:'root',sessionKind:'root'}]};
 const calls=[],act=async(name,args)=>{calls.push({name,args});if(name==='view.update')Object.assign(state.view,args.patch);return {accepted:true}};
 let button,list;
 await renderAct(async()=>{button=create(React.createElement(SubagentHistoryButton,{state,session:parent,act}))});
 assert.match(JSON.stringify(button.toJSON()),/123/);
 await renderAct(async()=>button.root.findByType('button').props.onClick());
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{panel:'subagent-history',subagentHistory:{sessionId:'root',filter:'',index:0}}}});
 const render=()=>React.createElement(SubagentHistory,{state,session:parent,act});
 await renderAct(async()=>{list=create(render())});
 assert.equal(list.root.findAllByProps({'data-action':'session.select'}).length,50);
 const more=list.root.findAllByType('button').find(node=>node.children.includes('More subagents'));
 await renderAct(async()=>more.props.onClick());
 assert.equal(calls.at(-1).args.patch.subagentHistory.index,1);
 await renderAct(async()=>list.update(render()));
 assert.equal(list.root.findAllByProps({'data-action':'session.select'})[0].props.title,'worker-50');
 await renderAct(async()=>list.root.findByType('input').props.onChange({target:{value:'Research 122'}}));
 assert.deepEqual(calls.at(-1).args.patch.subagentHistory,{sessionId:'root',filter:'Research 122',index:0});
 await renderAct(async()=>list.update(render()));
 const children=list.root.findAllByProps({'data-action':'session.select'});assert.equal(children.length,1);
 await renderAct(async()=>children[0].props.onClick());
 assert.deepEqual(calls.slice(-2),[{name:'session.select',args:{id:'worker-122'}},{name:'view.update',args:{patch:{panel:null}}}]);
 await renderAct(async()=>{button.unmount();list.unmount()});
});

test('failed subagent selection keeps the drilldown open and no children needs no button',async()=>{
 const parent={id:'root'},state={view:{},sessions:[parent]},calls=[];
 const act=async(name,args)=>{calls.push({name,args});return {accepted:false}};let root;
 await renderAct(async()=>{root=create(React.createElement(SubagentHistoryButton,{state,session:parent,act}))});
 assert.equal(root.toJSON(),null);
 state.sessions.push({id:'child',sessionKind:'worker',parentId:'root'});
 await renderAct(async()=>root.update(React.createElement(SubagentHistory,{state,session:parent,act})));
 await renderAct(async()=>root.root.findByProps({'data-action':'session.select'}).props.onClick());
 assert.deepEqual(calls,[{name:'session.select',args:{id:'child'}}]);
 await renderAct(async()=>root.unmount());
});
