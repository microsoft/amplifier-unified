import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {InstallationRepair,installationRepairHealthy}=await server.ssrLoadModule('/src/installation-repair.jsx');
const {MaintenanceSettings}=await server.ssrLoadModule('/src/maintenance.jsx');
const {SettingsPageContext}=await server.ssrLoadModule('/src/settings-ui.jsx');
const {UpdateDiagnostics,diagnosticReceipt}=await server.ssrLoadModule('/src/update-diagnostics.jsx');
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const preview={supported:true,previewId:'a'.repeat(32),hostInstanceId:'host-one',hostLabel:'Fixture host',dataDir:'/fixture/data',scope:'application-installation',target:{version:'1.2.3',revision:'c'.repeat(40),source:'stable'},installedExtras:['browser'],preserves:['conversations','settings','credentials','workspaces','source-selections','downloaded-sources','operation-receipts','optional-features'],replaces:['uv-tool-environment','application-launch-pointer'],restart:'managed-service',expiresAt:9999999999};
const operation={operationId:'b'.repeat(32),previewId:preview.previewId,hostInstanceId:'host-one',status:'accepted',stage:'accepted',reinstalled:false,serviceState:'not-checked',healthVerified:false,updatedAt:1};
const state=(projection={preview:null,operation:null})=>({maintenance:{installationRepair:projection}});
const byAction=(root,name)=>root.root.findByProps({'data-action':'maintenance.installationRepair'+name});
const content=root=>JSON.stringify(root.toJSON());
let storage;
test.beforeEach(()=>{storage=new Map();globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}});
async function mounted(projection,handler){
 let root;const calls=[];
 const dispatch=async(name,args,options)=>{calls.push([name,args,options]);if(handler)return handler(name,args);return{accepted:true,result:name.endsWith('Preview')?preview:name.endsWith('Confirm')?operation:{operation:null}}};
 await act(async()=>{root=create(React.createElement(InstallationRepair,{state:state(projection),act:dispatch}))});
 return {root,calls,dispatch};
}
async function acknowledge(root){await act(()=>root.root.findByType('input').props.onChange({target:{checked:true}}))}

test('preview explains exact host, preservation, extras, restart and runtime/reset separation',async()=>{
 const {root,calls}=await mounted();
 assert.equal(calls.filter(([name])=>name.endsWith('Confirm')).length,0);
 await act(()=>byAction(root,'Preview').props.onClick());
 for(const pattern of [/Fixture host/,/host-one/,/1.2.3/,/Workspace files/,/Credentials/,/Downloaded source caches/,/Operation and recovery receipts/,/Optional features kept/,/browser/,/Application installation only/,/does not queue a repair/,/not runtime dependency repair or a data reset/])assert.match(content(root),pattern);
 assert.equal(byAction(root,'Confirm').props.disabled,true);
 await acknowledge(root);await act(()=>byAction(root,'Confirm').props.onClick());
 assert.deepEqual(calls.at(-1),['maintenance.installationRepairConfirm',{previewId:preview.previewId,hostInstanceId:'host-one',confirm:true},{propagateError:true}]);
 assert.match(content(root),/Repair accepted/);assert.doesNotMatch(content(root),/Installation repair verified healthy/);
 assert.ok(storage.size);await act(()=>root.unmount());
});

test('same tick double confirmation and saved callback after settlement never replay',async()=>{
 const {root,calls}=await mounted({preview,operation:null});await acknowledge(root);
 const click=byAction(root,'Confirm').props.onClick;
 await act(()=>Promise.all([click(),click()]));
 await act(()=>click());
 assert.equal(calls.filter(([name])=>name.endsWith('Confirm')).length,1);
 await act(()=>root.unmount());await click();
 assert.equal(calls.filter(([name])=>name.endsWith('Confirm')).length,1);
});

test('lost acknowledgement survives remount and exact status reads never repeat confirm',async()=>{
 let current=null;
 const handler=(name,args)=>{
  if(name.endsWith('Status'))return {accepted:true,result:{operation:current}};
  if(name.endsWith('Confirm')){assert.ok(storage.size,'reference must exist before request');throw Error('connection lost')}
 };
 let {root,calls,dispatch}=await mounted({preview,operation:null},handler);
 await acknowledge(root);await act(()=>byAction(root,'Confirm').props.onClick());
 assert.match(content(root),/outcome is unknown/);assert.equal(byAction(root,'Preview').props.disabled,true);
 await act(()=>root.unmount());
 await act(()=>{root=create(React.createElement(InstallationRepair,{state:state(),act:dispatch}))});
 assert.deepEqual(calls.at(-1)[1],{previewId:preview.previewId});
 assert.match(content(root),/outcome remains unconfirmed/);assert.equal(byAction(root,'Preview').props.disabled,true);
 current={...operation,status:'running',updatedAt:2};
 await act(()=>byAction(root,'Status').props.onClick());
 assert.match(content(root),/Repair in progress/);
 assert.equal(calls.filter(([name])=>name.endsWith('Confirm')).length,1);
 await act(()=>root.unmount());
});

test('unrelated healthy receipt cannot resolve saved confirmation or enable another repair',async()=>{
 storage.set('amplifier.installation-repair.v1',JSON.stringify({previewId:preview.previewId,hostInstanceId:'host-one'}));
 const other={...operation,previewId:'d'.repeat(32),status:'ready',healthVerified:true,reinstalled:true,serviceState:'running'};
 const {root}=await mounted({preview:null,operation:other},()=>({accepted:true,result:{operation:other}}));
 assert.doesNotMatch(content(root),/Installation repair verified healthy/);
 assert.equal(byAction(root,'Preview').props.disabled,true);
 await act(()=>root.unmount());
});

test('host projection after lost HTTP wins and readiness requires all backend health facts',async()=>{
 const {root,dispatch}=await mounted({preview:null,operation});
 for(const [key,value] of [['status','reinstalled'],['healthVerified',false],['reinstalled',false],['serviceState','unknown'],['operationId','invalid']]){
  const receipt={...operation,status:'ready',reinstalled:true,healthVerified:true,serviceState:'running',[key]:value};
  assert.equal(installationRepairHealthy(receipt),false,key);
 }
 const ready={...operation,status:'ready',reinstalled:true,healthVerified:true,serviceState:'running',updatedAt:3};
 await act(()=>root.update(React.createElement(InstallationRepair,{state:state({preview:null,operation:ready}),act:dispatch})));
 assert.match(content(root),/Installation repair verified healthy/);
 await act(()=>root.unmount());
});

test('unsupported native Windows preview has no confirmation and missing capability has no action',async()=>{
 const {root}=await mounted({preview:{supported:false,reason:'Native Windows installation repair is unsupported.'},operation:null});
 assert.match(content(root),/Native Windows installation repair is unsupported/);
 assert.equal(root.root.findAllByProps({'data-action':'maintenance.installationRepairConfirm'}).length,0);
 await act(()=>root.unmount());
 const html=renderToStaticMarkup(React.createElement(InstallationRepair,{state:{},act:()=>assert.fail('no mutation')}));
 assert.match(html,/does not advertise/);assert.doesNotMatch(html,/<button/);
});

test('storage failure prevents confirm, explicit refusal permits fresh review, malformed ack stays locked',async()=>{
 for(const mode of ['storage','refused','malformed']){
  const {root,calls}=await mounted({preview,operation:null},name=>name.endsWith('Status')?{accepted:true,result:{operation:null}}:mode==='refused'?{accepted:false,error:'Finish active work.'}:{accepted:true});
  await acknowledge(root);
  if(mode==='storage')globalThis.sessionStorage.setItem=()=>{throw Error('denied')};
  await act(()=>byAction(root,'Confirm').props.onClick());
  assert.equal(calls.filter(([name])=>name.endsWith('Confirm')).length,mode==='storage'?0:1);
  assert.equal(byAction(root,'Preview').props.disabled,mode==='malformed');
  if(mode==='refused')assert.match(content(root),/Finish active work/);
  await act(()=>root.unmount());storage.clear();globalThis.sessionStorage.setItem=(key,value)=>storage.set(key,value);
 }
});

test('independent lifecycle candidate defers installation controls while retaining backup/runtime and reset guidance',()=>{
 const html=renderToStaticMarkup(React.createElement(SettingsPageContext.Provider,{value:'repair'},React.createElement(MaintenanceSettings,{state:state(),act:()=>{}})));
 assert.match(html,/Repair runtime/);assert.match(html,/Create full backup/);assert.match(html,/Data reset is separate/);
 assert.doesNotMatch(html,/Repair installation|Verify repaired installation|data-action="maintenance.installationRepair|data-part="installation-repair"|data-action="maintenance.reset"/);
});

test('deferred maintenance never mounts installation actions even when a host advertises them',async()=>{
 const calls=[];let root;
 const advertised=state({preview,operation:{...operation,status:'reinstalled',reinstalled:true,healthVerified:false},verifyTarget:{operationId:operation.operationId,hostInstanceId:'current-host',confirm:true}});
 await act(()=>{root=create(React.createElement(SettingsPageContext.Provider,{value:'repair'},React.createElement(MaintenanceSettings,{state:advertised,act:(...args)=>calls.push(args)})))});
 assert.deepEqual(calls,[]);
 assert.equal(root.root.findAllByProps({'data-part':'installation-repair'}).length,0);
 assert.equal(root.root.findAllByType('button').filter(button=>button.props['data-action']?.startsWith('maintenance.installationRepair')).length,0);
 await act(()=>root.unmount());
 const source=await readFile(new URL('../src/maintenance.jsx',import.meta.url),'utf8');
 assert.doesNotMatch(source,/InstallationRepair|installation-repair/,'feature source remains unimported by the maintenance entry');
 const registry=JSON.parse(await readFile(new URL('./browser-checks.json',import.meta.url),'utf8'));
 assert.equal(registry.scenarios.some(row=>row.id==='installation-repair'||row.command?.some(value=>value.includes('installation-repair'))),false);
});

test('status for a projected operation uses current exact preview; fresh status wins timestamp ties',async()=>{
 let found={...operation,status:'running'};
 const {root,calls,dispatch}=await mounted({preview:null,operation:found},()=>({accepted:true,result:{operation:found}}));
 assert.deepEqual(calls[0][1],{previewId:preview.previewId});
 found={...operation,status:'ready',reinstalled:true,healthVerified:true,serviceState:'running'};
 await act(()=>byAction(root,'Status').props.onClick());
 assert.match(content(root),/Installation repair verified healthy/);
 await act(()=>root.update(React.createElement(InstallationRepair,{state:state({preview:null,operation:{...operation,status:'running'}}),act:dispatch})));
 assert.match(content(root),/Installation repair verified healthy/);
 await act(()=>root.unmount());
});

test('old gesture is inert after displayed preview changes',async()=>{
 const {root,calls,dispatch}=await mounted({preview,operation:null});await acknowledge(root);
 const stale=byAction(root,'Confirm').props.onClick;
 await act(()=>root.update(React.createElement(InstallationRepair,{state:state({preview:{...preview,previewId:'e'.repeat(32)},operation:null}),act:dispatch})));
 await act(()=>stale());
 assert.equal(calls.filter(([name])=>name.endsWith('Confirm')).length,0);
 assert.equal(byAction(root,'Confirm').props.disabled,true);
 await act(()=>root.unmount());
});

test('canonical admission before late HTTP refusal retains identity and forbids resubmission',async()=>{
 let reject;
 const {root,calls,dispatch}=await mounted({preview,operation:null},name=>name.endsWith('Status')?{accepted:true,result:{operation:null}}:new Promise((_resolve,no)=>{reject=no}));
 await acknowledge(root);let pending;
 await act(()=>{pending=byAction(root,'Confirm').props.onClick()});
 await act(()=>root.update(React.createElement(InstallationRepair,{state:state({preview:null,operation}),act:dispatch})));
 await act(async()=>{reject(Object.assign(Error('Late refusal'),{accepted:false}));await pending});
 assert.equal(byAction(root,'Preview').props.disabled,true);assert.ok(storage.size);
 assert.equal(calls.filter(([name])=>name.endsWith('Confirm')).length,1);
 await act(()=>root.unmount());
});

test('another unsettled host operation prevents a new repair after this tab completed',async()=>{
 const ready={...operation,status:'ready',reinstalled:true,healthVerified:true,serviceState:'running'};
 storage.set('amplifier.installation-repair.v1',JSON.stringify({previewId:preview.previewId,hostInstanceId:'host-one'}));
 const {root,dispatch}=await mounted({preview:null,operation:ready},()=>({accepted:true,result:{operation:ready}}));
 await act(()=>root.update(React.createElement(InstallationRepair,{state:state({preview:null,operation:{...operation,previewId:'d'.repeat(32)}}),act:dispatch})));
 assert.match(content(root),/Another installation repair is not settled/);
 assert.equal(byAction(root,'Preview').props.disabled,true);
 await act(()=>root.unmount());
});

test('another active projected operation supersedes an old status receipt without local intent',async()=>{
 const ready={...operation,status:'ready',reinstalled:true,healthVerified:true,serviceState:'running'};
 const {root,dispatch}=await mounted({preview:null,operation:ready},()=>({accepted:true,result:{operation:ready}}));
 const next={...operation,operationId:'d'.repeat(32),previewId:'e'.repeat(32),status:'running'};
 await act(()=>root.update(React.createElement(InstallationRepair,{state:state({preview:null,operation:next}),act:dispatch})));
 assert.equal(byAction(root,'Preview').props.disabled,true);
 assert.match(content(root),/Repair in progress/);assert.doesNotMatch(content(root),/Installation repair verified healthy/);
 await act(()=>root.unmount());
});

test('incomplete ready receipt is unverified and does not unlock another repair',async()=>{
 const {root}=await mounted({preview:null,operation:{...operation,status:'ready',healthVerified:false}});
 assert.match(content(root),/Repair needs verification/);assert.equal(byAction(root,'Preview').props.disabled,true);
 await act(()=>root.unmount());
});

test('host-owned application/component failure wording and bounded context remain unresolved',()=>{
 const currentStatus={status:'action-required',actionRequired:true,action:'diagnostics',title:'Application updated; component update needs review',detail:'The verified application is separate from this prior component attempt.',failureKind:'ecosystem',failureAttemptId:'f'.repeat(32),failurePhase:'ecosystem-runtime-lock'};
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:{updates:{currentStatus},view:{}},act:()=>{}}));
 assert.match(html,/Application updated; component update needs review/);
 assert.match(html,/The verified application is separate from this prior component attempt/);
 assert.match(html,/Unresolved component update/);assert.match(html,/f{32}/);
 assert.match(html,/ecosystem runtime lock/);assert.doesNotMatch(html,/You’re up to date/);
 const unsafe=renderToStaticMarkup(React.createElement(UpdateSettings,{state:{updates:{currentStatus:{...currentStatus,failureAttemptId:'/private/SECRET',failurePhase:'https://SECRET'}},view:{}},act:()=>{}}));
 assert.doesNotMatch(unsafe,/SECRET/);
});

for(const [reason,pattern] of Object.entries({'package-index-auth-failed':/credentials and access/,'package-index-unavailable':/availability and uv index/,'package-not-found':/cannot make a missing package available/,'dependency-resolution-failed':/requirements could not be resolved/,'package-tls-failed':/Do not disable certificate verification/,'package-network-failed':/network, proxy/}))test('actionable sanitized guidance: '+reason,()=>{
 const failure={id:'one',attemptId:'attempt',status:'failed',phase:'candidate-install',reason,package:'example-package',constraint:'>=1.0,<2',stderr:'https://user:SECRET@private/path',source:'/private/feed'};
 const diagnostics={lastFailure:failure,events:[failure]};
 const html=renderToStaticMarkup(React.createElement(UpdateDiagnostics,{state:{updates:{diagnostics},view:{maintenanceDraft:{updateDiagnosticsExpanded:true}}},act:()=>{}}));
 assert.match(html,pattern);assert.match(html,/Required &gt;=1.0,&lt;2/);assert.doesNotMatch(html,/SECRET|private\/feed|maintenance.reset/);
 const exported=JSON.parse(diagnosticReceipt(diagnostics));assert.equal(exported.lastFailure.constraint,'>=1.0,<2');
 assert.equal(JSON.stringify(exported).includes('SECRET'),false);
 assert.equal(JSON.parse(diagnosticReceipt({lastFailure:{...failure,constraint:'https://SECRET/private'}})).lastFailure.constraint,undefined);
});
