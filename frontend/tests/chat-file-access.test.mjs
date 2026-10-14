import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatFileAccess}=await server.ssrLoadModule('/src/chat-file-access.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const report=(sessionId,commandId,extra={})=>({sessionId,commandId,scope:'session',revision:'preview-1',
 overridden:['denied_write_paths'],allowed:['/workspace'],denied:['/workspace/private'],
 current:{allowed:['/workspace'],denied:['/workspace/private']},
 inherited:{allowed:['/workspace','/inherited-extra'],denied:[]},...extra});
const button=(root,action)=>root.root.findAllByType('button').find(node=>node.props['data-action']===action);
const text=root=>JSON.stringify(root.toJSON());

test('reset previews wider inherited values and uses exact session revision; success waits for completion',async()=>{
 let calls=[],state={},root;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true,operationId:'op-'+calls.length}};
 const render=()=>React.createElement(ChatFileAccess,{state,session:{id:'owner'},act});
 await renderAct(async()=>{root=create(render())});
 assert.deepEqual(calls,[{name:'permissions.get',args:{sessionId:'owner',scope:'session'}}]);
 await renderAct(async()=>{state={permissions:report('owner','op-1'),managementResults:{'op-1':{phase:'ready'}}};root.update(render())});
 assert.match(text(root),/\/inherited-extra/);assert.match(text(root),/can allow more files, or fewer/);
 await renderAct(async()=>button(root,'permissions.reset').props.onClick());
 assert.deepEqual(calls[1],{name:'permissions.reset',args:{sessionId:'owner',scope:'session',expectedRevision:'preview-1'}});
 assert.equal(button(root,'permissions.reset').props.disabled,true);
 assert.doesNotMatch(text(root),/Workspace defaults restored for this chat\./);
 await renderAct(async()=>{state={permissions:report('owner','op-2',{overridden:[],revision:'preview-2',detail:'Workspace defaults restored for this chat.'}),managementResults:{'op-2':{phase:'ready'}}};root.update(render())});
 assert.match(text(root),/Workspace defaults restored for this chat\./);
 assert.equal(button(root,'permissions.reset').props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('stale-preview failure keeps edits and never displays another chat result',async()=>{
 let state={},calls=[],root;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true,operationId:'op-'+calls.length}};
 const render=()=>React.createElement(ChatFileAccess,{state,session:{id:'owner'},act});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>{state={permissions:report('owner','op-1'),managementResults:{'op-1':{phase:'ready'}}};root.update(render())});
 const field=()=>root.root.findAllByType('textarea').find(node=>node.props.id==='chat-write-denied');
 await renderAct(async()=>field().props.onChange({target:{value:'/edited'}}));
 await renderAct(async()=>button(root,'permissions.save').props.onClick());
 assert.deepEqual(calls[1].args,{sessionId:'owner',scope:'session',expectedRevision:'preview-1',allowed:['/workspace'],denied:['/edited']});
 await renderAct(async()=>{state={permissions:report('peer','peer-read',{allowed:['/peer-private']}),managementResults:{'op-2':{phase:'error',error:'Workspace defaults changed. Reload the preview.'}}};root.update(render())});
 assert.equal(field().props.value,'/edited');
 assert.match(text(root),/Workspace defaults changed/);assert.doesNotMatch(text(root),/peer-private/);
 await renderAct(async()=>root.unmount());
});

test('missing or foreign completion requires a new preview and cannot enable mutation',async()=>{
 let state={},root;
 const act=async()=>({accepted:true,operationId:'read'});
 const render=()=>React.createElement(ChatFileAccess,{state,session:{id:'owner'},act});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>{state={permissions:report('peer','read'),managementResults:{read:{phase:'ready'}}};root.update(render())});
 assert.match(text(root),/Reload the preview/);
 assert.equal(button(root,'permissions.reset'),undefined);
 await renderAct(async()=>root.unmount());
});
