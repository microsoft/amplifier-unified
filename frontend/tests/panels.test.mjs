import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const [{ProviderSettings,RoutingSettings},{RuntimeSettings},{TurnTimeline},{RegistrySettings},{MaintenanceSettings}]=await Promise.all(['setup.jsx','runtime-settings.jsx','timeline.jsx','registry.jsx','maintenance.jsx'].map(file=>server.ssrLoadModule('/src/'+file)));
const act=()=>{};
test.after(()=>server.close());
test('setup panels render redacted provider configuration and valid required routing roles',()=>{
 const state={view:{providerEditor:{module:'provider-openai',credentialMode:'private'}},setup:{providers:[{id:'openai',module:'provider-openai',config:{api_key:'[REDACTED]'},credentialsConfigured:true}],matrices:[]}};
 const provider=renderToStaticMarkup(React.createElement(ProviderSettings,{state,session:{id:'s',status:'ready'},act}));
 assert.match(provider,/credentials ready/);assert.match(provider,/type="password"/);assert.match(provider,/data-action="providers.save"/);
 const routing=renderToStaticMarkup(React.createElement(RoutingSettings,{state,act}));
 assert.match(routing,/general description/);assert.match(routing,/fast description/);assert.match(routing,/routing.save/);
});
test('runtime controls expose only bundle-supported goal actions',()=>{
 const state={view:{runtimeDraft:{tab:'direction'}},runtimeControl:{s:{'catalog.inspect':{capabilities:{goals:false,modes:false}}}}};
 const html=renderToStaticMarkup(React.createElement(RuntimeSettings,{state,session:{id:'s',status:'ready'},act}));
 assert.match(html,/does not expose a goal controller/);assert.match(html,/disabled=""[^>]*data-action="runtime.control"/);
});
test('expanded execution renders nested public summaries and genuine partial usage',()=>{
 const data={turns:[{id:'t',phase:'complete',aggregateUsage:{calls:1,inputTokens:100,outputTokens:20,costUsd:0,costType:'unavailable'}}],nodes:[{id:'tool',kind:'tool',turnId:'t',label:'delegate',phase:'complete'},{id:'child',parentId:'tool',kind:'worker',turnId:'t',label:'Research',summary:'**Result:** complete',phase:'complete'}]};
 const state={view:{executionExpanded:['turn:t','tool','child']}};
 const html=renderToStaticMarkup(React.createElement(TurnTimeline,{data,turnId:'t',state,act}));
 assert.match(html,/Research/);assert.match(html,/<strong>Result:<\/strong>/);assert.match(html,/cost unavailable/);assert.doesNotMatch(html,/\$0\.00/);
});

test('advanced registries expose scoped source controls through the shared registry',()=>{
 const state={view:{registryDraft:{open:true,tab:'sources'}},registry:{sources:[{kind:'module',name:'tool-custom',source:'/workspace/tool',scope:'project'}]}};
 const html=renderToStaticMarkup(React.createElement(RegistrySettings,{state,act}));
 assert.match(html,/tool-custom/);assert.match(html,/data-action="sources.save"/);assert.match(html,/data-action="sources.remove"/);
});
test('recovery requires a matching preview and explicit reset text',()=>{
 const state={view:{maintenanceDraft:{openSections:['reset'],resetParts:['runtime'],resetConfirmation:''}},maintenance:{resetPreview:{parts:['runtime'],paths:['/private/runtime']}}};
 const html=renderToStaticMarkup(React.createElement(MaintenanceSettings,{state,act}));
 assert.match(html,/Type RESET/);assert.match(html,/disabled=""[^>]*data-action="maintenance.reset"[^>]*>Apply selected reset/);
 const changed={...state,view:{maintenanceDraft:{...state.view.maintenanceDraft,resetParts:['settings']}}};
 assert.doesNotMatch(renderToStaticMarkup(React.createElement(MaintenanceSettings,{state:changed,act})),/Apply selected reset/);
});

test('module behavior validation opt-in and real results are visible through shared controls',()=>{
 const state={view:{registryDraft:{open:true,tab:'modules',behavioral:true,behaviorResultsExpanded:true}},registry:{validation:{id:'tool-fixture',passed:true,checks:[],behavioral:{passed:false,exitCode:1,tests:[{name:'tool response contract',status:'failed'},{name:'optional streaming',status:'skipped'}]}}}};
 const html=renderToStaticMarkup(React.createElement(RegistrySettings,{state,act}));
 assert.match(html,/Also run module behavior tests/);assert.match(html,/data-action="modules.validate"/);assert.match(html,/Behavior tests need attention/);assert.match(html,/tool response contract/);assert.match(html,/skipped/);
});

test('provider environment selection shows default, custom availability and no key input',()=>{
 const state={view:{providerEditor:{module:'provider-openai',credentialMode:'environment',envVar:'TEAM_API_KEY'}},setup:{credentialCheck:{module:'provider-openai',defaultEnvVar:'OPENAI_API_KEY',envVar:'TEAM_API_KEY',available:true}}};
 const html=renderToStaticMarkup(React.createElement(ProviderSettings,{state,act}));
 assert.match(html,/OPENAI_API_KEY/);assert.match(html,/TEAM_API_KEY/);assert.match(html,/Found — this variable will supply the key/);
 assert.match(html,/data-action="providers.credentials"/);assert.doesNotMatch(html,/type="password"/);
 state.setup.credentialCheck.available=false;
 assert.match(renderToStaticMarkup(React.createElement(ProviderSettings,{state,act})),/Not found in the app environment/);
});
