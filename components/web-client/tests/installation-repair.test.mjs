import test from 'node:test';
import assert from 'node:assert/strict';
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

test('backup/runtime controls remain distinct from installation and destructive reset',()=>{
 const html=renderToStaticMarkup(React.createElement(SettingsPageContext.Provider,{value:'repair'},React.createElement(MaintenanceSettings,{state:state(),act:()=>{}})));
 assert.match(html,/Repair runtime/);assert.match(html,/Repair installation/);assert.match(html,/Data reset is separate/);
 assert.doesNotMatch(html,/data-action="maintenance.reset"/);
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

const reinstalled={...operation,status:'reinstalled',reinstalled:true,healthVerified:false};
const verifyTarget={operationId:operation.operationId,hostInstanceId:'current-successor',confirm:true};
const verifyProjection=(op=reinstalled,target=verifyTarget)=>({preview:null,operation:op,verifyTarget:target});
const verificationStatus=root=>root.root.findByProps({'data-part':'installation-repair-verification'}).findAllByType('button').find(button=>button.children.join('')==='Check verification status');
test('verification is health-only, uses current projected successor identity and never auto-submits',async()=>{
 const {root,calls}=await mounted(verifyProjection(),name=>({accepted:true,result:name.endsWith('Status')?{operation:reinstalled}:reinstalled}));
 assert.equal(calls.some(([name])=>name.endsWith('Verify')),false);
 assert.match(content(root),/does not install packages, stop or restart the service/);
 const click=byAction(root,'Verify').props.onClick;
 await act(()=>Promise.all([click(),click()]));await act(()=>click());
 assert.deepEqual(calls.filter(([name])=>name.endsWith('Verify')),[['maintenance.installationRepairVerify',verifyTarget,{propagateError:true}]]);
 assert.match(content(root),/did not establish a healthy installation/);
 assert.doesNotMatch(content(root),/Installation repair verified healthy/);
 await act(()=>byAction(root,'Verify').props.onClick());
 assert.equal(calls.filter(([name])=>name.endsWith('Verify')).length,2,'fresh deliberate click after known failed check');
 await act(()=>root.unmount());await click();assert.equal(calls.filter(([name])=>name.endsWith('Verify')).length,2);
});
test('verify eligibility fails closed for other statuses, contradictory facts, missing or unrelated target',async()=>{
 for(const op of [{...reinstalled,status:'accepted'},{...reinstalled,status:'running'},{...reinstalled,status:'unknown'},{...reinstalled,status:'ready'},{...reinstalled,reinstalled:false},{...reinstalled,healthVerified:undefined},{...reinstalled,healthVerified:true}]){
  const {root}=await mounted(verifyProjection(op));
  assert.equal(root.root.findAllByProps({'data-action':'maintenance.installationRepairVerify'}).length,0);
  await act(()=>root.unmount());
 }
 for(const target of [null,{...verifyTarget,operationId:'c'.repeat(32)},{...verifyTarget,hostInstanceId:''},{...verifyTarget,confirm:false}]){
  const {root}=await mounted(verifyProjection(reinstalled,target));
  assert.equal(root.root.findAllByProps({'data-action':'maintenance.installationRepairVerify'}).length,0);
  await act(()=>root.unmount());
 }
});
test('old verify gesture cannot retarget a new host or operation',async()=>{
 for(const patch of [{verifyTarget:{...verifyTarget,hostInstanceId:'changed'}},{operation:{...reinstalled,operationId:'c'.repeat(32)},verifyTarget:{...verifyTarget,operationId:'c'.repeat(32)}}]){
  const {root,calls,dispatch}=await mounted(verifyProjection());
  const click=byAction(root,'Verify').props.onClick;
  await act(()=>root.update(React.createElement(InstallationRepair,{state:state({...verifyProjection(),...patch}),act:dispatch})));
  await act(()=>click());assert.equal(calls.filter(([name])=>name.endsWith('Verify')).length,0);
  await act(()=>root.unmount());
 }
});
test('unknown verification persists across reload and reconciles by explicit status only',async()=>{
 const handler=name=>{if(name.endsWith('Verify')){assert.ok(storage.get('amplifier.installation-repair-verification.v1'));throw Error('lost ack')}return{accepted:true,result:{operation:reinstalled}}};
 let {root,calls,dispatch}=await mounted(verifyProjection(),handler);
 await act(()=>byAction(root,'Verify').props.onClick());assert.match(content(root),/Verification outcome is unknown/);
 assert.equal(byAction(root,'Verify').props.disabled,true);
 await act(()=>root.unmount());await act(()=>{root=create(React.createElement(InstallationRepair,{state:state(verifyProjection()),act:dispatch}))});
 assert.equal(byAction(root,'Verify').props.disabled,true);
 assert.equal(calls.filter(([name])=>name.endsWith('Verify')).length,1,'remount/status is not a verify gesture');
 await act(()=>verificationStatus(root).props.onClick());
 assert.deepEqual(calls.at(-1).slice(0,2),['maintenance.installationRepairStatus',{operationId:operation.operationId}]);
 assert.match(content(root),/still unverified/);assert.equal(byAction(root,'Verify').props.disabled,false);
 assert.equal(calls.filter(([name])=>name.endsWith('Verify')).length,1);
 await act(()=>root.unmount());
});
test('malformed or unrelated verification result cannot claim health or unlock; exact ready can',async()=>{
 for(const result of [undefined,{...reinstalled,operationId:'c'.repeat(32),status:'ready',healthVerified:true,serviceState:'running'},{...reinstalled,status:'ready',healthVerified:false}]){
  const {root}=await mounted(verifyProjection(),name=>({accepted:true,result:name.endsWith('Status')?{operation:reinstalled}:result}));
  await act(()=>byAction(root,'Verify').props.onClick());
  assert.equal(byAction(root,'Verify').props.disabled,true);assert.doesNotMatch(content(root),/Installation repair verified healthy/);
  await act(()=>root.unmount());storage.clear();
 }
 const ready={...reinstalled,status:'ready',healthVerified:true,serviceState:'running',updatedAt:2};
 const {root}=await mounted(verifyProjection(),name=>({accepted:true,result:name.endsWith('Status')?{operation:reinstalled}:ready}));
 await act(()=>byAction(root,'Verify').props.onClick());assert.match(content(root),/Installation repair verified healthy/);
 await act(()=>root.unmount());
});
test('storage denial prevents a health-check request and explicit refusal is not recovery',async()=>{
 for(const denied of [true,false]){
  const {root,calls}=await mounted(verifyProjection(),name=>name.endsWith('Verify')?{accepted:false,error:'Stale successor.'}:{accepted:true,result:{operation:reinstalled}});
  if(denied)globalThis.sessionStorage.setItem=()=>{throw Error('denied')};
  await act(()=>byAction(root,'Verify').props.onClick());
  assert.equal(calls.filter(([name])=>name.endsWith('Verify')).length,denied?0:1);
  assert.doesNotMatch(content(root),/Installation repair verified healthy/);
  await act(()=>root.unmount());storage.clear();globalThis.sessionStorage.setItem=(key,value)=>storage.set(key,value);
 }
});
test('matching canonical healthy projection settles pending verification without a probe',async()=>{
 storage.set('amplifier.installation-repair-verification.v1',JSON.stringify({operationId:operation.operationId,previewId:operation.previewId,hostInstanceId:verifyTarget.hostInstanceId}));
 const ready={...reinstalled,status:'ready',healthVerified:true,serviceState:'running',updatedAt:2};
 const {root,calls}=await mounted(verifyProjection(ready,null),()=>({accepted:true,result:{operation:ready}}));
 assert.match(content(root),/Installation repair verified healthy/);
 assert.equal(root.root.findAllByProps({'data-part':'installation-repair-verification'}).length,0);
 assert.equal(calls.some(([name])=>name.endsWith('Verify')),false);
 await act(()=>root.unmount());
});
