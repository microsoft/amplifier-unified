import test from 'node:test';
import assert from 'node:assert/strict';
import {shellScope,resourceSnapshots,retainResourceVersions} from '../src/shell/resources.js';
import {createShellDiagnostics} from '../src/shell/diagnostics.js';
const bootstrap={clientId:'client',hostInstanceId:'host',shellBootstrapKey:'key',resolvedInstances:[{id:'chat',package:'builtin.chats'},{id:'button',package:'builtin.actions'},{id:'custom',package:'custom.sidebar'}],instanceGenerations:{chat:1,button:2,custom:3}};
const demand={id:1,scope:['chat','workspace','chat',null,null],resources:['sidebarSearch']};
const envelope=()=>({
 clientId:'client',hostInstanceId:'host',bootstrapKey:'key',demandId:1,scope:demand.scope,
 instances:{
  chat:{generation:1,resources:{
   context:{revision:1,value:{view:{draft:'private'},sidebarNavigation:{recentView:{query:'private'}}}},
   sidebarSearch:{revision:1,value:{sidebarNavigation:{search:{items:[{id:'one'}]}}}},
  }},
  custom:{generation:3,resources:{customSnapshot:{revision:1,value:{ownField:42}}}},
 },
});

test('resource assembly preserves context, exact coverage and independent custom snapshots',()=>{
 const result=resourceSnapshots(bootstrap,envelope(),demand);
 assert.deepEqual(result.chat.navigationResources,['sidebarSearch']);assert.equal(result.chat.sidebarNavigation.search.items[0].id,'one');
 assert.equal(result.chat.sidebarNavigation.recentView.query,'private');assert.equal(result.button,undefined);assert.deepEqual(result.custom,{ownField:42,generation:3});
});
test('missing resources stay uncovered and stale identities or scopes cannot become a baseline',()=>{
 for(const patch of [{clientId:'other'},{hostInstanceId:'other'},{bootstrapKey:'other'},{demandId:2},{scope:['chat','other','chat',null,null]}])assert.equal(resourceSnapshots(bootstrap,{...envelope(),...patch},demand),null);
 const missing=envelope();delete missing.instances.chat.resources.sidebarSearch;
 assert.deepEqual(resourceSnapshots(bootstrap,missing,demand).chat.navigationResources,[]);
 missing.instances.chat.generation=9;assert.equal(resourceSnapshots(bootstrap,missing,demand).chat,undefined);
});
test('per-resource ordering never resurrects removed coverage or crosses demand identities',()=>{
 const prior=envelope();prior.instances.chat.resources.sidebarSearch.revision=5;
 const next=envelope();assert.equal(retainResourceVersions(prior,next).instances.chat.resources.sidebarSearch.revision,5);
 delete next.instances.chat.resources.sidebarSearch;assert.equal(retainResourceVersions(prior,next).instances.chat.resources.sidebarSearch,undefined);
 assert.equal(retainResourceVersions(prior,{...envelope(),demandId:2}).instances.chat.resources.sidebarSearch.revision,1);
});
test('scope excludes irrelevant inactive workspace state',()=>{
 assert.deepEqual(shellScope({selectedSessionId:'chat',selectedWorkspaceId:'workspace',view:{workSurface:'chat',workWorkspaceId:'old',workWorkspaceTab:'files'}}),demand.scope);
 assert.deepEqual(shellScope({view:{workSurface:'workspace',workWorkspaceId:'one'}}),[null,null,'workspace','one','chats']);
});
test('DOM diagnostics are bounded and exclude resource values, paths, filters and drafts',()=>{
 let text;const log=createShellDiagnostics({setAttribute:(name,value)=>{assert.equal(name,'data-amplifier-shell-diagnostics');text=value}});
 for(let i=0;i<200;i++)log.record('demand',{demandId:i,resources:['sidebarSearch','private query'],requestId:'a'.repeat(32),elapsedMs:10,status:200,value:'secret',path:'/private/home',draft:'secret',query:'secret'});
 log.record('secret reason',{draft:'secret'});log.record('error',{requestId:'bad private value'});
 assert.ok(text.length<=8192);const result=JSON.parse(text);assert.ok(result.events.length<=24);assert.equal(result.counts.demand,200);
 assert.doesNotMatch(text,/secret|private|\/home/);assert.deepEqual(result.events[0].resources,['sidebarSearch']);
});
