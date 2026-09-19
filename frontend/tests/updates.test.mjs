import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;

const application={id:'application',kind:'app',label:'Amplifier Unified',current:'0.6.3',latest:'v0.6.3',status:'current',channel:'github-releases'};
const component={id:'bundle',label:'Community bundle',status:'update',current:'aaaaaaa',latest:'bbbbbbb'};
function state(updates={}){return {view:{},settings:{updates:{}},updates:{application,items:[],...updates}};}
function render(updates={}){return renderToStaticMarkup(React.createElement(UpdateSettings,{state:state(updates),act:()=>{}}));}

test('installed app and published release remain visible with source inventory closed',()=>{
 const html=render({items:Array.from({length:87},(_,id)=>({id:String(id),label:'Hidden source '+id,status:'current'}))});
 assert.match(html,/Application release status/);
 assert.match(html,/<dt>Installed<\/dt><dd>0\.6\.3<\/dd>/);
 assert.match(html,/<dt>Latest release<\/dt><dd>v0\.6\.3<\/dd>/);
 assert.match(html,/Latest release installed/);
 assert.match(html,/Published GitHub releases/);
 assert.match(html,/Show all 87 sources/);
 assert.doesNotMatch(html,/Hidden source 0/);
});

test('app ahead of the published release is honest about release-channel lag',()=>{
 const html=render({application:{...application,latest:'v0.5.6',releaseBehind:true,detail:'The installed version is newer than the latest published release.'}});
 assert.match(html,/Ahead of published release/);
 assert.match(html,/v0\.5\.6/);
 assert.doesNotMatch(html,/Latest release installed|Install app update/);
});

test('app version is visible before checking, and failed checks retain a clear result',()=>{
 const before=render({application:{...application,status:'not_checked',latest:null}});
 assert.match(before,/0\.6\.3/);
 assert.match(before,/Not checked/);
 const failed=render({application:{...application,status:'check_failed',detail:'Sign in with GitHub CLI to check releases.'}});
 assert.match(failed,/a-check-result error/);
 assert.match(failed,/Sign in with GitHub CLI/);
 assert.match(failed,/0\.6\.3/);
 assert.doesNotMatch(failed,/Latest release installed/);
});

test('application and component updates have separate effects and no duplicate app row',()=>{
 const app={...application,status:'update',latest:'v0.6.4'};
 const html=render({application:app,items:[app,component],appAvailable:true});
 assert.equal((html.match(/Amplifier Unified/g)||[]).length,1);
 assert.match(html,/Install app update/);
 assert.match(html,/1 available/);
 assert.match(html,/Community bundle/);
 assert.match(html,/app updates first and restarts/);
 assert.match(html,/next resumed turn/);
 assert.match(html,/Show all 1 source/);
});

test('staged app, restart, interrupted install and successful restart report their real states',()=>{
 const pendingApp={...application,status:'update',latest:'v0.6.4'};
 const staged=render({pendingApp,phase:'app-staged',detail:'Waiting for active work to finish.'});
 assert.match(staged,/Ready to restart/);
 assert.match(staged,/smart tools/);
 assert.match(staged,/Apply app update/);
 assert.doesNotMatch(staged,/a-check-result success/);
 const restarting=render({phase:'activating',pendingRestart:{version:'0.6.4'},detail:'Application installed. Restarting the local host…'});
 assert.match(restarting,/Restarting…/);
 assert.match(restarting,/this page will reconnect/);
 assert.match(restarting,/a-check-result pending/);
 const interrupted=render({phase:'interrupted',detail:'Application installation was interrupted.'});
 assert.match(interrupted,/a-check-result error/);
 assert.match(interrupted,/Application installation was interrupted/);
 const installed=render({phase:'installed',detail:'Application update installed and restarted successfully.'});
 assert.match(installed,/a-check-result success/);
 assert.match(installed,/installed and restarted successfully/);
});

test('prepared app failure is visibly an error and allows the existing install action to retry',async()=>{
 const calls=[];
 let root;
 const app={...application,status:'update',latest:'v0.6.4'};
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state({pendingApp:app,phase:'error',error:'App activation failed. Retry when idle.'}),act:(name,args)=>calls.push({name,args})}))});
 const button=root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='updates.install')[0];
 assert.equal(button.props.disabled,false);
 assert.match(JSON.stringify(root.toJSON()),/App activation failed/);
 await renderAct(async()=>button.props.onClick());
 assert.equal(calls[0].name,'updates.install');
 await renderAct(async()=>root.unmount());
});

test('visible update controls use the shared action registry and only promise ecosystem rollback',async()=>{
 const calls=[];let root;
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:state({items:[component],canRollback:true}),act:(name,args)=>calls.push({name,args})}))});
 for(const action of ['updates.check','updates.install','updates.rollback']){
  const button=root.root.findAll(node=>node.type==='button'&&node.props['data-action']===action)[0];
  assert.equal(button.props.disabled,false);
  await renderAct(async()=>button.props.onClick());
 }
 assert.deepEqual(calls.map(call=>call.name),['updates.check','updates.install','updates.rollback']);
 assert.match(JSON.stringify(root.toJSON()),/Roll back ecosystem/);
 await renderAct(async()=>root.unmount());
});

const failure={id:'failure',at:100,attemptId:'a'.repeat(32),commandId:'b'.repeat(32),kind:'application',phase:'replacement-probe',status:'failed',exitCode:1,durationMs:250,probe:{ok:false,stage:'imports',errorType:'ModuleNotFoundError'}};
test('failed update shows its phase and correlation ID with details collapsed',()=>{
 const html=render({diagnostics:{attemptId:failure.attemptId,lastFailure:failure,events:[failure]}});
 assert.match(html,/Check installed package/);
 assert.match(html,/ModuleNotFoundError/);
 assert.match(html,/Exit 1/);
 assert.match(html,new RegExp(failure.attemptId));
 assert.match(html,/View update details/);
 assert.doesNotMatch(html,/update-diagnostic-receipt/);
});

test('diagnostic disclosure is agent-controllable and displays only sanitized receipt fields',async()=>{
 const calls=[];let root;
 const diagnostics={attemptId:failure.attemptId,lastFailure:{...failure,stdout:'raw secret'},events:[{...failure,stdout:'raw secret',env:{secret:'hidden'}}]};
 const initial=state({diagnostics});
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:initial,act:(name,args)=>calls.push({name,args})}))});
 const toggle=root.root.findAll(node=>node.type==='button'&&node.props.className?.includes('a-update-diagnostics-toggle'))[0];
 await renderAct(async()=>toggle.props.onClick());
 assert.equal(calls[0].name,'view.update');
 assert.equal(calls[0].args.patch.maintenanceDraft.updateDiagnosticsExpanded,true);
 const expanded={...initial,view:{maintenanceDraft:{updateDiagnosticsExpanded:true}}};
 await renderAct(async()=>root.update(React.createElement(UpdateSettings,{state:expanded,act:()=>{}})));
 const receipt=root.root.findByProps({id:'update-diagnostic-receipt'});
 assert.equal(receipt.props.readOnly,true);
 assert.match(receipt.props.value,/replacement-probe/);
 assert.doesNotMatch(receipt.props.value,/raw secret|hidden|stdout"|env"/);
 const parsed=JSON.parse(receipt.props.value);
 assert.equal(parsed.lastFailure.commandId,failure.commandId);
 await renderAct(async()=>root.unmount());
});

test('completed retry retains history without showing an active failure notice',()=>{
 const done={...failure,id:'done',phase:'restart-ack',status:'succeeded',exitCode:0,probe:undefined};
 const html=render({diagnostics:{attemptId:done.attemptId,events:[failure,done],latest:done}});
 assert.match(html,/View update details/);
 assert.doesNotMatch(html,/a-update-failure"/);
});

test('unknown usage keeps failure conditions visible separately even with inventory collapsed',()=>{
 const initial=state({items:[
   {id:'old',kind:'bundle / module',label:'Historical source',ref:'master',usage:'unknown',status:'check_failed'},
   {id:'dirty',kind:'bundle / module',label:'Edited source',ref:'main',usage:'unknown',status:'local_changes',detail:'Tracked source changes preserved.'},
   {id:'active',kind:'bundle / module',label:'Configured source',usage:'configured',status:'check_failed'},
 ],lastCheck:100});
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:initial,act:()=>{}}));
 assert.match(html,/Other cached sources \/ usage unverified/);
 assert.match(html,/2 sources: 1 check failed, 1 local changes/);
 assert.match(html,/Historical source/);
 assert.match(html,/Tracked source changes preserved/);
 assert.match(html,/Needs attention/);
 assert.match(html,/Configured source/);
 assert.doesNotMatch(html,/aria-label="Current"|All sources.*current/);
 assert.match(html,/may be transitive dependencies/);
});

test('unknown update remains installable while unknown pins and current status stay distinct',()=>{
 const html=render({items:[
   {...component,usage:'unknown'},
   {id:'pin',label:'Pinned dependency',usage:'unknown',status:'pinned'},
   {id:'current',label:'Current dependency',usage:'unknown',status:'current'},
 ]});
 assert.match(html,/1 available/);
 assert.match(html,/3 sources: 0 check failed, 0 local changes, 1 updates, 1 pinned, 1 current/);
 assert.match(html,/data-action="updates.install"/);
 assert.doesNotMatch(html,/disabled="" data-action="updates.install"/);
 assert.doesNotMatch(html,/Needs attention/);
});
