import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ConfigurationRecovery}=await server.ssrLoadModule('/src/configuration-recovery.jsx');
const {ModuleSettings}=await server.ssrLoadModule('/src/bundles.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const session=(id='owner')=>({id,title:id,pendingConfiguration:{operationId:'op-'+id,revision:3,phase:'error',effect:'unknown'},configurationBusy:true});
const report=(s,extra={})=>({sessionId:s.id,operationId:s.pendingConfiguration.operationId,revision:3,attempts:1,state:{phase:'error',effect:'unknown'},history:[],recovery:{canReconcile:true,canResume:false,reason:'Check the saved outcome.'},...extra});
const button=(root,verb)=>root.root.findAllByType('button').find(n=>n.props['data-action']==='configuration.operation.'+verb);
const text=root=>JSON.stringify(root.toJSON());

test('held view is visible without a plan and never inspects or edits its saved draft',async()=>{
 const s=session(),calls=[],state={view:{moduleEditor:{sessionId:s.id,text:'uncommitted-draft'}}};let root;
 const act=async(action,args)=>{calls.push({action,args});return {accepted:true,result:report(s)}};
 await renderAct(async()=>{root=create(React.createElement(ModuleSettings,{state,session:s,act}))});
 assert.match(text(root),/Original configuration change/);
 assert.deepEqual(calls.map(c=>c.action),['configuration.operation.read']);
 assert.equal(state.view.moduleEditor.text,'uncommitted-draft');
 assert.equal(button(root,'resume').props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('healthy opening retains automatic inspection and ordinary apply refresh',async()=>{
 let root,s={id:'healthy',title:'Healthy',configuration:{plan:{tools:[]}}};
 const state={view:{moduleEditor:{sessionId:s.id,text:'{"tools":[]}',appliedAt:1}}},calls=[];
 const act=async(action,args)=>{calls.push({action,args});return {accepted:true,result:{...report(session()),sessionId:s.id}}};
 const render=()=>React.createElement(ModuleSettings,{state,session:s,act});
 await renderAct(async()=>{root=create(render())});
 assert.ok(calls.some(c=>c.action==='configuration.inspect'&&c.args.id===s.id));
 await renderAct(async()=>{
  s={...s,pendingConfiguration:{phase:'ready',effect:'mounted',appliedAt:2},configuration:{plan:{tools:[],hooks:[]}}};
  root.update(render());
 });
 assert.ok(calls.some(c=>c.action==='view.update'&&c.args.patch.moduleEditor.appliedAt===2));
 await renderAct(async()=>root.unmount());
});

test('real buttons use original operation CAS, acceptance stays queued, and clicks do not double-submit',async()=>{
 let s=session(),view=report(s),root;const calls=[];
 const act=async(action,args,meta)=>{
  calls.push({action,args,meta});
  if(action.endsWith('.reconcile')){view=report(s,{revision:4,state:{phase:'error',effect:'none'},recovery:{canResume:true,canReconcile:true}});s={...s,pendingConfiguration:{...s.pendingConfiguration,revision:4}}}
  if(action.endsWith('.resume'))view=report(s,{revision:5,state:{phase:'queued',effect:'none'},recovery:{canResume:false,canReconcile:false}});
  return {accepted:true,result:view};
 };
 const render=()=>React.createElement(ConfigurationRecovery,{session:s,act});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>{await button(root,'reconcile').props.onClick();root.update(render())});
 assert.equal(button(root,'resume').props.disabled,false);
 await renderAct(async()=>{const click=button(root,'resume').props.onClick;await Promise.all([click(),click()])});
 const resume=calls.filter(c=>c.action.endsWith('.resume'));
 assert.equal(resume.length,1);
 assert.deepEqual(resume[0].args,{sessionId:'owner',operationId:'op-owner',expectedRevision:4});
 assert.equal(resume[0].meta.id,'configuration-recovery-op-owner-4-resume');
 assert.match(text(root),/not mounted yet/);assert.doesNotMatch(text(root),/Changes are applied to the loaded session/);
 await renderAct(async()=>root.unmount());
});

test('lost acknowledgement preserves a read-only recovery route and never resends the same revision',async()=>{
 const s=session();let root;const calls=[];
 const act=async(action)=>{calls.push(action);if(action.endsWith('.resume'))throw Error('private failure');return {accepted:true,result:report(s,{state:{phase:'error',effect:'none'},recovery:{canResume:true,canReconcile:true}})}};
 await renderAct(async()=>{root=create(React.createElement(ConfigurationRecovery,{session:s,act}))});
 await renderAct(async()=>button(root,'resume').props.onClick());
 assert.match(text(root),/request result is not confirmed/);assert.doesNotMatch(text(root),/private failure/);
 await renderAct(async()=>button(root,'read').props.onClick());
 assert.equal(button(root,'resume').props.disabled,true);
 assert.equal(calls.filter(c=>c.endsWith('.resume')).length,1);
 await renderAct(async()=>root.unmount());
});

test('foreign responses cannot enable a control and a stale owner completion cannot replace a new owner',async()=>{
 let root,resolve;const owner=session(),peer=session('peer');
 const act=async()=>new Promise(done=>{resolve=done});
 await renderAct(async()=>{root=create(React.createElement(ConfigurationRecovery,{key:owner.id,session:owner,act}))});
 const oldResolve=resolve;
 await renderAct(async()=>root.update(React.createElement(ConfigurationRecovery,{key:peer.id,session:peer,act})));
 await renderAct(async()=>oldResolve({accepted:true,result:report(owner,{recovery:{canResume:true}})}));
 assert.doesNotMatch(text(root),/op-owner/);assert.equal(button(root,'resume').props.disabled,true);
 await renderAct(async()=>resolve({accepted:true,result:report(owner,{recovery:{canResume:true}})}));
 assert.match(text(root),/Status could not be confirmed/);assert.equal(button(root,'resume').props.disabled,true);
 await renderAct(async()=>root.unmount());
});

for(const effect of ['unknown','saved_not_mounted'])test(`${effect} stays held regardless of unrelated global action success`,async()=>{
 const s=session();let root;
 const act=async()=>({accepted:true,result:report(s,{state:{phase:'error',effect},recovery:{canResume:false,canReconcile:false,reason:'Held.'}})});
 await renderAct(async()=>{root=create(React.createElement(ModuleSettings,{session:s,state:{view:{},actionStatus:{'configuration.apply':{phase:'ready',target:{id:'peer'}}}},act}))});
 assert.equal(button(root,'resume').props.disabled,true);assert.doesNotMatch(text(root),/Changes are applied to the loaded session/);
 await renderAct(async()=>root.unmount());
});
