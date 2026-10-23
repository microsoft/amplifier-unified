import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {MaintenanceControls}=await server.ssrLoadModule('/src/maintenance-controls.jsx');
const {ActivityPanel}=await server.ssrLoadModule('/src/attention.jsx');
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
test.after(()=>server.close());globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const open={epoch:null,revision:1,phase:'open',held:false,inventoryComplete:true,counts:{},total:0,readyForRestart:false,canResume:false,canPause:true};
const paused={...open,epoch:'pause-one',revision:2,phase:'paused',held:true,counts:{suspended:1},total:1,readyForRestart:true,canResume:true,canPause:false};
const state=status=>({hostMaintenance:status,sessions:[],view:{},settings:{},updates:{}});
const button=(root,label)=>root.root.findAllByType('button').find(row=>row.children.join('')===label);
const text=root=>JSON.stringify(root.toJSON());
const make=async(status,dispatch,props={})=>{let root;await act(async()=>{root=create(React.createElement(MaintenanceControls,{state:state(status),act:dispatch,...props}))});return root};
const change=async(root,status,dispatch,props={})=>act(async()=>root.update(React.createElement(MaintenanceControls,{state:state(status),act:dispatch,...props})));
const preview={accepted:true,result:{hostInstanceId:'host-one',targetDigest:'exact-owned-digest',ownedRoots:2}};

test('Activity and Updates expose the same controls without a staged update',()=>{
 for(const Component of [ActivityPanel,UpdateSettings]){
  const html=renderToStaticMarkup(React.createElement(Component,{state:state(open),act:()=>{}}));
  assert.match(html,/Pause and stop work/);assert.match(html,/Pause all/);assert.match(html,/Stop now…/);
  assert.doesNotMatch(html,/Force restart…/);
 }
});
test('accepted pause does not claim a safe boundary and duplicate clicks are fenced',async()=>{
 const calls=[];let resolve;const dispatch=(...args)=>{calls.push(args);return new Promise(done=>resolve=done)};
 const root=await make(open,dispatch);
 await act(async()=>{button(root,'Pause all').props.onClick();button(root,'Pause all').props.onClick()});
 assert.equal(calls.length,1);assert.ok(calls[0][2].id);assert.match(text(root),/Sending your request/);
 await act(async()=>resolve({accepted:true}));
 assert.doesNotMatch(text(root),/All tracked work is paused/);
 await change(root,{...paused,phase:'pausing',readyForRestart:false,canResume:false,counts:{waiting:1}},dispatch);
 assert.match(text(root),/New work is held/);assert.match(text(root),/Waiting will not automatically stop it/);
 assert.doesNotMatch(text(root),/All tracked work is paused/);
 assert.equal(button(root,'Resume paused work').props.disabled,true);
 await act(async()=>root.unmount());
});
test('resume binds the displayed epoch and never sends a conversation prompt',async()=>{
 const calls=[],dispatch=async(...args)=>{calls.push(args);return {accepted:true}};
 const root=await make(paused,dispatch);
 await act(async()=>button(root,'Resume paused work').props.onClick());
 assert.equal(calls.length,1);assert.deepEqual(calls[0].slice(0,2),['maintenance.resume',{epoch:'pause-one'}]);
 await act(async()=>root.unmount());
});
test('unsupported and incomplete work never reads as a completed pause',async()=>{
 for(const status of [{...paused,counts:{unsupported:1},readyForRestart:false,canResume:false},{...paused,inventoryComplete:false,readyForRestart:false,canResume:false}]){
  const root=await make(status,async()=>({accepted:true}));
  assert.match(text(root),/Work is held/);assert.doesNotMatch(text(root),/All tracked work is paused/);
  assert.equal(button(root,'Resume paused work').props.disabled,true);
  await act(async()=>root.unmount());
 }
});
test('resume follows canonical readiness and never infers permission from phase or count',async()=>{
 for(const canResume of [false,undefined]){
  const root=await make({...paused,canResume},async()=>({accepted:true}));
  assert.equal(button(root,'Resume paused work').props.disabled,true);
  await act(async()=>root.unmount());
 }
});
test('auxiliary work remains visible without promising remote cancellation',async()=>{
 const root=await make({...paused,readyForRestart:false,canResume:false,auxiliary:{
  settled:false,scope:'host-observed',remoteCancellationConfirmed:false,
  items:[{kind:'voice',label:'A voice call is active',detail:'End the call before restarting.'},
   {kind:'smart-tools',label:'A Smart Tool operation is still running',detail:'Wait for the operation to settle.'},
   {kind:'naming',label:'Chat naming is still running',detail:'Wait for the current naming request.'}],
 }},async()=>({accepted:true}));
 assert.match(text(root),/A voice call is active/);assert.match(text(root),/Chat naming is still running/);
 assert.match(text(root),/Smart Tool operation is still running/);assert.match(text(root),/work observed by this host/);
 assert.match(text(root),/does not confirm that work already accepted by an external service has stopped/);
 assert.doesNotMatch(text(root),/All tracked work is paused/);
 assert.equal(button(root,'Resume paused work').props.disabled,true);
 await act(async()=>root.unmount());
});
test('released uncertainty allows only canonical fresh Pause and never duplicate Resume',async()=>{
 const calls=[],dispatch=async(...args)=>{calls.push(args);return {accepted:true}};
 const released={...open,phase:'uncertain',epoch:'released-pause',counts:{uncertain:1},canPause:true};
 const root=await make(released,dispatch);
 assert.equal(button(root,'Pause all').props.disabled,false);
 assert.equal(button(root,'Resume paused work'),undefined);
 assert.match(text(root),/New work can run/);assert.doesNotMatch(text(root),/New work is held/);
 await act(async()=>button(root,'Pause all').props.onClick());
 assert.equal(calls.length,1);assert.equal(calls[0][0],'maintenance.pause');
 for(const canPause of [false,undefined]){
  await change(root,{...released,canPause},dispatch);
  assert.equal(button(root,'Pause all').props.disabled,true);
 }
 await act(async()=>root.unmount());
});
test('stop only previews until explicit confirmation and Cancel is passive',async()=>{
 const calls=[],dispatch=async(...args)=>{calls.push(args);return preview};
 const root=await make(open,dispatch);
 await act(async()=>button(root,'Stop now…').props.onClick());
 assert.equal(calls.length,1);assert.equal(calls[0][0],'maintenance.stopPreview');
 assert.match(text(root),/No update or restart is requested/);assert.match(text(root),/cannot be undone/);
 await act(async()=>button(root,'Cancel').props.onClick());assert.equal(calls.length,1);
 await act(async()=>button(root,'Stop now…').props.onClick());
 await act(async()=>button(root,'Stop owned work').props.onClick());
 assert.deepEqual(calls.at(-1).slice(0,2),['maintenance.stopNow',{hostInstanceId:'host-one',targetDigest:'exact-owned-digest',confirm:true}]);
 await act(async()=>root.unmount());
});
for(const field of ['epoch','revision'])test(`a changed ${field} invalidates Stop confirmation`,async()=>{
 const calls=[],dispatch=async(...args)=>{calls.push(args);return preview};
 const root=await make(paused,dispatch);await act(async()=>button(root,'Stop now…').props.onClick());
 await change(root,{...paused,[field]:field==='epoch'?'new-epoch':3},dispatch);
 assert.equal(button(root,'Stop owned work').props.disabled,true);
 await act(async()=>button(root,'Stop owned work').props.onClick());assert.equal(calls.length,1);
 await act(async()=>root.unmount());
});
test('late Stop preview cannot bind changed work',async()=>{
 let finish;const root=await make(open,()=>new Promise(resolve=>finish=resolve));
 await act(async()=>{button(root,'Stop now…').props.onClick()});
 await change(root,paused,async()=>preview);
 await act(async()=>finish(preview));
 assert.equal(button(root,'Stop owned work'),undefined);assert.match(text(root),/Work changed/);
 await act(async()=>root.unmount());
});
test('lost acknowledgement retries only the same explicit command identity',async()=>{
 const calls=[],dispatch=async(...args)=>{calls.push(args);if(calls.length===1)throw Error('Connection lost');return {accepted:true,duplicate:true}};
 const root=await make(open,dispatch);
 await act(async()=>button(root,'Pause all').props.onClick());
 assert.equal(calls.length,1);assert.match(text(root),/No new request has been sent automatically/);
 assert.equal(button(root,'Pause all').props.disabled,true);
 await act(async()=>button(root,'Retry same request').props.onClick());
 assert.deepEqual(calls[0],calls[1]);assert.equal(button(root,'Retry same request'),undefined);
 await act(async()=>root.unmount());
});
test('explicit stale-target refusal permits a fresh confirmation, not automatic retry',async()=>{
 const calls=[],dispatch=async(...args)=>{calls.push(args);if(args[0]==='maintenance.stopNow')throw Object.assign(Error('Target changed'),{status:409});return preview};
 const root=await make(open,dispatch);
 await act(async()=>button(root,'Stop now…').props.onClick());
 await act(async()=>button(root,'Stop owned work').props.onClick());
 assert.equal(calls.length,2);assert.equal(button(root,'Retry same request'),undefined);
 assert.equal(button(root,'Stop now…').props.disabled,false);assert.match(text(root),/Target changed/);
 await act(async()=>root.unmount());
});
test('detail reads are bounded, fenced and absent while hidden',async()=>{
 const calls=[];let finish;
 const dispatch=(...args)=>{calls.push(args);return new Promise(resolve=>finish=resolve)};
 const root=await make(paused,dispatch);assert.equal(calls.length,0);
 await act(async()=>root.root.findByType('details').props.onToggle({currentTarget:{open:true}}));
 assert.deepEqual(calls[0].slice(0,2),['maintenance.status',{offset:0,limit:25}]);
 await change(root,{...paused,revision:3},dispatch);
 await act(async()=>finish({accepted:true,result:{...paused,roots:[{sessionId:'old',phase:'suspended',reason:'OBSOLETE'}]}}));
 assert.doesNotMatch(text(root),/OBSOLETE/);
 const count=calls.length;await change(root,{...paused,revision:4},dispatch,{active:false});
 assert.equal(calls.length,count);
 await act(async()=>root.unmount());
});
test('settled emergency Stop can reopen admission without offering automatic restart',async()=>{
 const root=await make({...paused,phase:'paused',counts:{stopped:1}},async()=>({accepted:true}));
 assert.match(text(root),/Work has stopped/);assert.equal(button(root,'Resume paused work').props.disabled,false);
 assert.doesNotMatch(text(root),/Force restart/);
 await act(async()=>root.unmount());
});
