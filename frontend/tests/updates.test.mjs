import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
const {diagnosticReceipt,reconciledFailure}=await server.ssrLoadModule('/src/update-diagnostics.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;

const application={id:'application',kind:'app',label:'Amplifier Unified',current:'0.6.3',latest:'v0.6.3',status:'current',channel:'github-releases'};
const component={id:'bundle',label:'Community bundle',status:'update',current:'aaaaaaa',latest:'bbbbbbb'};
function state(updates={}){return {view:{},settings:{updates:{}},updates:{application,items:[],...updates}};}
function render(updates={},view={}){return renderToStaticMarkup(React.createElement(UpdateSettings,{state:{...state(updates),view},act:()=>{}}));}

const release={version:'0.6.4',title:'Shared settings',changes:['Workspace settings now apply.'],notices:[{id:'shared',title:'Review shared settings',detail:'A setting affects other apps.',action:'Check your workspace.'}]};
const noticeId='release-notice:0.6.4:shared';
test('changelog separates installed, upcoming and earlier versions and escapes authored text',()=>{
 const html=render({application:{...application,releaseNotes:[release,{...release,version:'0.6.3',notices:[]},{...release,version:'0.6.2',title:'<script>bad()</script>',notices:[]}]}});
 assert.match(html,/Changelog/);assert.match(html,/Upcoming/);assert.match(html,/Earlier release/);
 assert.match(html,/Check your workspace/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
 assert.match(html,/available offline/);
});

test('reviewing a high-impact notice uses its fingerprint and retains the changelog',async()=>{
 const calls=[];let root;
 const initial={...state({application:{...application,releaseNotes:[release],status:'update'}}),attention:{items:[{id:noticeId,read:false,fingerprint:'notice-v1'}]}};
 await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state:initial,act:(name,args)=>calls.push({name,args})}))});
 const button=root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='attention.read')[0];
 await renderAct(async()=>button.props.onClick());
 assert.deepEqual(calls,[{name:'attention.read',args:{ids:[noticeId],fingerprints:{[noticeId]:'notice-v1'}}}]);
 const reviewed={...initial,attention:{items:[{...initial.attention.items[0],read:true}]}};
 await renderAct(async()=>root.update(React.createElement(UpdateSettings,{state:reviewed,act:()=>{}})));
 assert.equal(root.root.findAllByProps({'aria-label':'High-impact changes'}).length,0);
 assert.match(JSON.stringify(root.toJSON()),/Check your workspace/);
 assert.match(JSON.stringify(root.toJSON()),/Reviewed/);
 assert.equal(root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='updates.install')[0].props.disabled,false);
 await renderAct(async()=>root.unmount());
});

test('missing remote notes are honest without hiding installed history or disabling installation',()=>{
 const html=render({application:{...application,status:'update',latest:'v0.6.4',releaseNotes:[release],releaseNotesWarning:'Release notes for the available update could not be loaded.'}});
 assert.match(html,/could not be loaded/);assert.match(html,/View published release/);
 assert.match(html,/<button class="a-primary" data-action="updates.install"/);
});

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
 assert.match(restarting,/Application installed. Restarting the local host/);
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

test('a rejected restart keeps its work gate but displays actionable failure instead of restarting',()=>{
 const html=render({phase:'activating',pendingRestart:{version:'0.6.4',requestStatus:'rejected'},
  error:'The app installed, but the managed restart request was rejected. Run amplifier-unified service restart.',
  detail:'Waiting for a healthy restarted host. New work remains paused.'});
 assert.match(html,/Needs attention/);
 assert.match(html,/Restart needs attention/);
 assert.match(html,/a-check-result error/);
 assert.match(html,/amplifier-unified service restart/);
 assert.match(html,/<button class="a-primary" disabled="" data-action="updates.install"/);
 assert.doesNotMatch(html,/Restarting|this page will reconnect/);
});

test('unconfirmed restart request waits for readiness without promising completion',()=>{
 const html=render({phase:'activating',pendingRestart:{version:'0.6.4',requestStatus:'uncertain'},
  detail:'Restart request confirmation was interrupted. Waiting for a healthy restarted host.'});
 assert.match(html,/Awaiting restarted host/);
 assert.match(html,/Awaiting restart…/);
 assert.match(html,/confirmation was interrupted/);
 assert.doesNotMatch(html,/this page will reconnect|installed and restarted successfully|Latest release installed/);
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

test('restart request acceptance is distinct from readiness and acknowledgement',()=>{
 const events=[['service-restart-request','accepted'],['restart-readiness','uncertain'],['restart-ack','succeeded'],['restart-reconcile','succeeded'],['service-restart-request','rejected']].map(([phase,status],index)=>({id:String(index),attemptId:'request',phase,status}));
 const html=render({diagnostics:{attemptId:'request',events}},{maintenanceDraft:{updateDiagnosticsExpanded:true}});
 assert.match(html,/Request service restart/);
 assert.match(html,/Check restarted host readiness/);
 assert.match(html,/Confirm restarted version/);
 assert.match(html,/Verify current installation/);
 for(const [status,title] of [['accepted','Accepted'],['uncertain','Unconfirmed'],['rejected','Rejected']]){
  const row=html.match(new RegExp('<li class="'+status+'">(.*?)</li>'))?.[1]||'';
  assert.match(row,new RegExp(title));
  assert.doesNotMatch(row,/lucide-check/);
 }
});

const reconciliation={attemptId:failure.attemptId,version:application.current,revision:'c'.repeat(40),verifiedAt:101};
function reconciledUpdates(patch={}){
 const oldFailure={...failure,phase:'service-restart',exitCode:-15};
 return {phase:'installed',error:null,application:{...application,runningRevision:reconciliation.revision},
  reconciliation,diagnostics:{attemptId:failure.attemptId,lastFailure:oldFailure,events:[oldFailure,{id:'reconciled',attemptId:failure.attemptId,phase:'restart-reconcile',status:'succeeded'}]},...patch};
}
test('verified current installation labels retained restart failure as resolved historical evidence',()=>{
 const updates=reconciledUpdates();
 const html=render(updates,{maintenanceDraft:{updateDiagnosticsExpanded:true}});
 assert.equal(reconciledFailure(updates),true);
 assert.match(html,/a-update-failure resolved/);
 assert.match(html,/Previous update issue resolved/);
 assert.match(html,/current installation is verified healthy/);
 assert.match(html,/<li class="failed historical">/);
 assert.match(html,/Failed \(historical\)/);
 assert.match(html,/Exit -15/);
 assert.doesNotMatch(html,/Last update issue:|Confirm restarted version/);
 const receipt=JSON.parse(diagnosticReceipt(updates.diagnostics,updates.reconciliation));
 assert.equal(receipt.lastFailure.status,'failed');
 assert.equal(receipt.lastFailure.exitCode,-15);
 assert.deepEqual(receipt.reconciliation,reconciliation);
});

test('stale or incomplete reconciliation cannot hide a current failure',()=>{
 for(const patch of [
  {reconciliation:{...reconciliation,attemptId:'different'}},
  {reconciliation:{...reconciliation,version:'0.0.0'}},
  {reconciliation:{...reconciliation,revision:'different'}},
  {reconciliation:{...reconciliation,verifiedAt:undefined}},
  {error:'A new error'},
  {diagnostics:{lastFailure:{...failure,attemptId:'new-failed-attempt'}}},
  {pendingRestart:{version:application.current}},
  {pendingApp:{version:application.current}},
  {pendingReplacement:{}},
 ]){
  const updates=reconciledUpdates(patch);
  assert.equal(reconciledFailure(updates),false);
  const html=render(updates);
  assert.match(html,/Last update issue:/);
  assert.doesNotMatch(html,/Previous update issue resolved|a-update-failure resolved/);
 }
});

test('uncertain replacement never appears current or offers another installation',()=>{
 for(const phase of ['error','interrupted','activating']){
  const html=render({phase,pendingReplacement:{},canRollback:true});
  assert.match(html,/Needs attention/);
  assert.match(html,/installation outcome is unknown/);
  assert.match(html,/Work remains paused/);
  assert.match(html,/Installation needs verification/);
  assert.doesNotMatch(html,/Latest release installed/);
  for(const action of ['updates.check','updates.install','updates.rollback']){
   const button=html.match(new RegExp('<button[^>]*data-action="'+action+'"[^>]*>'))?.[0]||'';
   assert.match(button,/disabled=""/);
  }
 }
});

test('a new update check does not turn a verified historical failure back into an active issue',()=>{
 for(const phase of ['checking','checked','available']){
  const updates=reconciledUpdates({phase});
  assert.equal(reconciledFailure(updates),true);
  const html=render(updates);
  assert.match(html,/Previous update issue resolved/);
  assert.doesNotMatch(html,/Last update issue:/);
 }
});

test('copied receipts include version revisions but exclude raw output and extra reconciliation fields',()=>{
 const event={...failure,expectedRevision:'c'.repeat(40),observedRevision:'d'.repeat(40),stdout:'secret output',path:'/private/folder'};
 const receipt=JSON.parse(diagnosticReceipt({attemptId:failure.attemptId,lastFailure:event,events:[event]},{...reconciliation,stdout:'secret output',path:'/private/folder'}));
 assert.equal(receipt.lastFailure.expectedRevision,event.expectedRevision);
 assert.equal(receipt.events[0].observedRevision,event.observedRevision);
 assert.equal(receipt.lastFailure.status,'failed');
 assert.doesNotMatch(JSON.stringify(receipt),/secret output|private\/folder|stdout"|path"/);
});

test('unknown usage keeps failure conditions visible separately even with inventory collapsed',()=>{
 const initial=state({items:[
   {id:'old',kind:'bundle / module',label:'Historical source',ref:'master',usage:'unknown',status:'check_failed'},
   {id:'dirty',kind:'bundle / module',label:'Edited source',ref:'main',usage:'unknown',status:'local_changes',detail:'Tracked source changes preserved.'},
   {id:'active',kind:'bundle / module',label:'Configured source',usage:'configured',status:'check_failed'},
 ],lastCheck:100});
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:initial,act:()=>{}}));
 assert.match(html,/Other cached sources/);
 assert.match(html,/2 sources: 1 check failed, 1 with local changes/);
 assert.match(html,/Historical source/);
 assert.match(html,/Tracked source changes preserved/);
 assert.match(html,/Needs attention/);
 assert.match(html,/Configured source/);
 assert.doesNotMatch(html,/aria-label="Current"|All sources.*current/);
 assert.match(html,/may still be needed by your bundles/);
});

test('unknown update remains installable while unknown pins and current status stay distinct',()=>{
 const html=render({items:[
   {...component,usage:'unknown'},
   {id:'pin',label:'Pinned dependency',usage:'unknown',status:'pinned'},
   {id:'current',label:'Current dependency',usage:'unknown',status:'current'},
 ]});
 assert.match(html,/1 available/);
 assert.match(html,/3 sources: 1 with updates, 1 pinned, 1 current/);
 assert.match(html,/data-action="updates.install"/);
 assert.doesNotMatch(html,/disabled="" data-action="updates.install"/);
 assert.doesNotMatch(html,/Needs attention/);
});

test('runtime preflight identifies the module and recovery action and completes parent progress',()=>{
 const attemptId='e'.repeat(32),commandId='f'.repeat(32);
 const parent={id:'start',attemptId,commandId,phase:'ecosystem-stage',status:'started'};
 const preflight={id:'blocked',attemptId,phase:'ecosystem-runtime-preflight',status:'failed',errorType:'ValueError',package:'amplifier-legacy-hooks',reason:'protected-runtime-source'};
 const events=[parent,{id:'checkout',attemptId,phase:'ecosystem-checkout',status:'succeeded'},preflight,{...parent,id:'end',status:'failed'}];
 const diagnostics={attemptId,lastFailure:preflight,events};
 const html=render({phase:'error',diagnostics},{maintenanceDraft:{updateDiagnosticsExpanded:true}});
 assert.match(html,/Last update issue: Check installed worker sources/);
 assert.match(html,/amplifier-legacy-hooks/);
 assert.match(html,/Local source changes or an explicit dependency override were preserved/);
 assert.match(html,/Bundles &amp; modules/);
 assert.doesNotMatch(html,/Running/);
 assert.match(html,/Switch staged source/);
 assert.equal(JSON.parse(diagnosticReceipt(diagnostics)).lastFailure.reason,'protected-runtime-source');
});
