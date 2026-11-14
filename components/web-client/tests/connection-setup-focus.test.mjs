import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.window=new EventTarget();
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {useConnectionSetup}=await server.ssrLoadModule('/src/setup-journey.jsx');
test.after(()=>server.close());
function Probe({state,session,dispatch}){useConnectionSetup(state,session,dispatch);return null}
async function fixture(){
 const state={view:{setupJourney:'done',panel:null},settings:{workspace:'/one'},setup:{providersLoadedAt:1,providersWorkspace:'/one',providers:[]}};
 let session={id:'chat',workspace:'/one'},component;const calls=[];
 const dispatch=(action,args)=>{calls.push({action,args});if(action==='view.update')state.view={...state.view,...args.patch};return Promise.resolve({accepted:true})};
 const render=()=>React.createElement(Probe,{state,session,dispatch});
 const update=()=>act(async()=>component.update(render()));
 await act(async()=>{component=create(render())});await update();
 return {state,calls,update,setSession:value=>{session=value},focus:()=>act(async()=>window.dispatchEvent(new Event('focus'))),close:()=>act(async()=>component.unmount())};
}
test('focus refresh after setup dismissal does not reopen it for the unchanged empty catalog',async()=>{
 const f=await fixture();try{
  assert.equal(f.calls.filter(row=>row.action==='view.update').length,1);
  f.state.view.panel=null;await f.update();
  await f.focus();assert.deepEqual(f.calls.at(-1),{action:'providers.list',args:{sessionId:'chat'}});
  f.state.setup.providersLoadedAt=2;await f.update();
  assert.equal(f.state.view.panel,null);
  assert.equal(f.calls.filter(row=>row.action==='view.update').length,1);
 }finally{await f.close()}
});
test('a different workspace still gets its first empty-connection prompt',async()=>{
 const f=await fixture();try{
  f.state.view.panel=null;f.setSession({id:'other',workspace:'/two'});
  f.state.setup={providersLoadedAt:2,providersWorkspace:'/two',providers:[]};await f.update();
  assert.equal(f.calls.filter(row=>row.action==='view.update').length,2);
  assert.equal(f.state.view.panel,'settings');
 }finally{await f.close()}
});
test('focus still discovers a new connection and loss of that connection can prompt again',async()=>{
 const f=await fixture();try{
  f.state.view.panel=null;await f.update();await f.focus();
  f.state.setup={...f.state.setup,providersLoadedAt:2,providers:[{id:'new',enabled:true}]};await f.update();
  assert.equal(f.state.view.panel,null);
  f.state.setup={...f.state.setup,providersLoadedAt:3,providers:[]};await f.update();
  assert.equal(f.state.view.panel,'settings');
  assert.equal(f.calls.filter(row=>row.action==='view.update').length,2);
 }finally{await f.close()}
});

test('a late empty local catalog cannot displace external connection Settings or reopen it on close',async()=>{
 const calls=[],dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true}};
 let state={settings:{workspace:'/one'},view:{panel:'settings',settingsExpanded:['external-agents']},setup:{providersLoadedAt:0,providersWorkspace:'/one',providers:[]}},root;
 const render=()=>React.createElement(Probe,{state,dispatch});
 try{
  await act(async()=>{root=create(render())});
  state={...state,setup:{...state.setup,providersLoadedAt:1}};
  await act(async()=>{root.update(render());window.dispatchEvent(new Event('focus'))});
  assert.deepEqual(calls,[]);
  state={...state,view:{...state.view,panel:null}};
  await act(async()=>root.update(render()));assert.deepEqual(calls,[],'Closing the editor does not reopen native onboarding');
  await act(async()=>window.dispatchEvent(new Event('focus')));
  assert.deepEqual(calls,[{action:'providers.list',args:{}}],'Ordinary native discovery remains available after leaving the external editor');
 }finally{await act(async()=>root?.unmount())}
});

for(const mode of ['draft','group-draft','chat'])test(`external ${mode} needs no native provider onboarding or focus discovery`,async()=>{
 const calls=[],dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true}};
 const state={settings:{workspace:'/one'},view:{setupJourney:'welcome',...(mode==='draft'?{newSessionDraft:{externalAgentId:'remote'}}:mode==='group-draft'?{newSessionDraft:{externalAgentIds:['alpha','beta']}}:{})},setup:{providersLoadedAt:1,providersWorkspace:'/one',providers:[]}};
 const session=mode==='chat'?{id:'external',workspace:'/one',externalAgent:{id:'remote'}}:undefined;let root;
 try{
  await act(async()=>{root=create(React.createElement(Probe,{state,session,dispatch}))});
  await act(async()=>window.dispatchEvent(new Event('focus')));assert.deepEqual(calls,[]);
 }finally{await act(async()=>root?.unmount())}
});
