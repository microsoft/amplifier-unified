import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const [{ProviderSettings,RoutingSettings},{RuntimeSettings},{TurnTimeline},{RegistrySettings},{MaintenanceSettings}]=await Promise.all(['setup.jsx','runtime-settings.jsx','timeline.jsx','registry.jsx','maintenance.jsx'].map(file=>server.ssrLoadModule('/src/'+file)));
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
const {ConversationDetails}=await server.ssrLoadModule('/src/conversation-controls.jsx');
const act=()=>{};
test.after(()=>server.close());
test('setup panels render redacted provider configuration and valid required routing roles',()=>{
 const state={view:{providerEditor:{module:'provider-openai',credentialMode:'private'},routingEditor:{matrix:{roles:{general:{description:'General',candidates:[{provider:'openai',model:'model'}]},fast:{description:'Fast',candidates:[{provider:'openai',model:'model'}]}}}}},setup:{providers:[{id:'openai',module:'provider-openai',config:{api_key:'[REDACTED]'},credentialsConfigured:true}],matrices:[]}};
 const provider=renderToStaticMarkup(React.createElement(ProviderSettings,{state,session:{id:'s',status:'ready'},act}));
 assert.match(provider,/credentials ready/);assert.match(provider,/type="password"/);assert.match(provider,/data-action="providers.save"/);
 const routing=renderToStaticMarkup(React.createElement(RoutingSettings,{state,act}));
 assert.match(routing,/general description/);assert.match(routing,/data-collection-id="fast"/);assert.match(routing,/routing.save/);
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
 const state={view:{settingsExpanded:['registries'],registryDraft:{open:true,tab:'sources'}},registry:{sources:[{kind:'module',name:'tool-custom',source:'/workspace/tool',scope:'project'}]}};
 const html=renderToStaticMarkup(React.createElement(RegistrySettings,{state,act}));
 assert.match(html,/tool-custom/);assert.match(html,/data-action="sources.save"/);assert.match(html,/data-action="sources.remove"/);
});
test('recovery requires a matching preview and explicit reset text',()=>{
 const state={view:{settingsExpanded:['reset'],maintenanceDraft:{openSections:['reset'],resetParts:['runtime'],resetConfirmation:''}},maintenance:{resetPreview:{parts:['runtime'],paths:['/private/runtime']}}};
 const html=renderToStaticMarkup(React.createElement(MaintenanceSettings,{state,act}));
 assert.match(html,/Type RESET/);assert.match(html,/disabled=""[^>]*data-action="maintenance.reset"[^>]*>Apply selected reset/);
 const changed={...state,view:{...state.view,maintenanceDraft:{...state.view.maintenanceDraft,resetParts:['settings']}}};
 assert.doesNotMatch(renderToStaticMarkup(React.createElement(MaintenanceSettings,{state:changed,act})),/Apply selected reset/);
});

test('module behavior validation opt-in and real results are visible through shared controls',()=>{
 const state={view:{settingsExpanded:['registries'],registryDraft:{open:true,tab:'modules',id:'tool-fixture',behavioral:true,behaviorResultsExpanded:true}},registry:{validation:{id:'tool-fixture',passed:true,checks:[],behavioral:{passed:false,exitCode:1,tests:[{name:'tool response contract',status:'failed'},{name:'optional streaming',status:'skipped'}]}}}};
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

test('available updates are visible without opening the full source inventory',()=>{
 const state={view:{},settings:{},updates:{lastCheck:1,available:2,items:[{id:'current',label:'Already current source',status:'current'},{id:'changed',label:'Updated community bundle',status:'update',current:'abc',latest:'def'},{id:'pinned',label:'Pinned library',status:'pinned'}]}};
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state,act}));
 assert.match(html,/Ecosystem updates/);assert.match(html,/Updated community bundle/);assert.match(html,/1 available/);
 assert.doesNotMatch(html,/Already current source|Pinned library/);assert.match(html,/Show all 3 sources/);
 state.view.maintenanceDraft={updatesExpanded:true};
 const all=renderToStaticMarkup(React.createElement(UpdateSettings,{state,act}));
 assert.match(all,/Already current source/);assert.match(all,/Pinned library/);
});
test('no pending updates still distinguishes failed checks from current sources',()=>{
 const state={view:{},settings:{},updates:{lastCheck:1,items:[{id:'failed',label:'Offline source',status:'check_failed'}]}};
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state,act}));
 assert.match(html,/No installable updates remain, but some checks or local changes need review/);assert.match(html,/Needs attention/);assert.match(html,/Offline source/);assert.match(html,/a-check-result error/);
});

test('provider model results and metadata choices are visible even after other management actions finish',()=>{
 const state={view:{providerEditor:{id:'openai',module:'provider-openai',model:'gpt-6-astra',config:'{"reasoning_effort":"high"}'}},management:{phase:'ready',operation:'notifications.get'},setup:{modelCatalogs:{openai:[{id:'gpt-6-astra',display_name:'Astra'}]},metadata:{'provider-openai':{info:{config_fields:[{id:'reasoning_effort',display_name:'Reasoning effort',field_type:'choice',choices:['low','high'],requires_model:true},{id:'enabled',field_type:'boolean'}]}}},operations:{'providers.models:openai':{phase:'ready'}}}};
 const html=renderToStaticMarkup(React.createElement(ProviderSettings,{state,act}));
 assert.match(html,/Found 1 models/);assert.doesNotMatch(html,/Available models/);assert.match(html,/<select id="provider-model-catalog"/);
 assert.match(html,/<select id="provider-option-reasoning_effort"/);
 assert.match(html,/<option value="high" selected="">high/);
 assert.match(html,/<select id="provider-option-enabled"/);
 state.setup.operations['providers.models:openai']={phase:'error',error:'Provider check timed out. Please retry.'};
 assert.match(renderToStaticMarkup(React.createElement(ProviderSettings,{state,act})),/Provider check timed out/);
});


test('native CLI history is automatic and stale sharing settings fall back to maintenance menu',()=>{
 const state={view:{settingsSection:'maintenance',settingsExpanded:['shared-history']},sessions:[{id:'native',title:'CLI chat'}],selectedSessionId:'native'};
 const menu=renderToStaticMarkup(React.createElement(MaintenanceSettings,{state,act}));
 assert.match(menu,/Conversation history/);
 assert.doesNotMatch(menu,/Same-chat CLI and web|Browse shared conversations/);
 state.view.settingsExpanded=['history'];
 const history=renderToStaticMarkup(React.createElement(MaintenanceSettings,{state,act}));
 assert.match(history,/CLI projects and conversations appear automatically/);
 assert.match(history,/data-action="history.export"/);
 assert.match(history,/data-action="history.importFile"/);
 assert.doesNotMatch(history,/data-action="history.import"|Browse saved conversations/);
});

test('module mount failures expose remediation without another disclosure',()=>{
 const session={id:'fixture',status:'error',error:'Configured modules failed to mount',moduleFailures:[{module:'tool-fixture',reason_code:'invalid_entry_point',guidance:'Check the module entry point and async mount function.'}]};
 const html=renderToStaticMarkup(React.createElement(ConversationDetails,{session,act}));
 assert.match(html,/Configured modules could not load/);
 assert.match(html,/tool-fixture/);assert.match(html,/Check the module entry point and async mount function/);
 assert.doesNotMatch(html,/<summary>Runtime message/);
});
