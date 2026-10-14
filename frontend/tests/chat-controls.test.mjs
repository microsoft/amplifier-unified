import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ModelControl,readAttachment}=await server.ssrLoadModule('/src/chat-controls.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
for(const commit of ['onPointerUp','onKeyUp','onBlur'])test('reasoning effort survives a background view refresh until '+commit,async()=>{
 const calls=[],session={id:'chat',status:'idle'},selection={instance:'one',model:'model',effort:'high'};
 let state={view:{composerModel:{open:true,sessionId:'chat',...selection}},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers:[{id:'one',info:{defaults:{model:'model'},config_fields:[{id:'reasoning_effort',choices:['low','medium','high']}] }}],effective:selection}}}},root;
 const render=()=>React.createElement(ModelControl,{state,session,act:async(name,args)=>calls.push({name,args}),working:false});
 try{
  await renderAct(async()=>root=create(render()));
  const slider=()=>root.root.findByProps({id:'chat-effort'});
  await renderAct(async()=>slider().props.onChange({currentTarget:{value:'1'}}));
  // Streaming/view polling can replace an equal server object before release.
  state={...state,view:{...state.view,composerModel:{...state.view.composerModel}}};
  await renderAct(async()=>root.update(render()));
  assert.equal(slider().props['aria-valuetext'],'medium');
  await renderAct(async()=>slider().props[commit]({currentTarget:{value:String(slider().props.value)}}));
  assert.deepEqual(calls.filter(c=>c.args.operation==='provider.queueSelection').map(c=>c.args.args),[{...selection,effort:'medium'}]);
  await renderAct(async()=>slider().props.onBlur({currentTarget:{value:'1'}}));
  assert.equal(calls.filter(c=>c.args.operation==='provider.queueSelection').length,1);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('attachment intake accepts 32 MiB and rejects oversized files before reading',async()=>{
 const previous=globalThis.FileReader;let reads=0;
 globalThis.FileReader=class{readAsDataURL(){reads++;this.result='data:image/png;base64,fixture';this.onload()}};
 try{
  for(const size of [8*1024*1024+1,22*1024*1024,32*1024*1024])assert.equal(await readAttachment({size,name:'camera.png'}),'fixture');
  for(const size of [0,32*1024*1024+1])await assert.rejects(readAttachment({size,name:'bad.png'}),/32 MB/);
  assert.equal(reads,3);
 }finally{globalThis.FileReader=previous}
});
test('provider configuration changes refresh draft choices without retaining stale defaults',async()=>{
 const calls=[];let state={configurationRevision:0,view:{newSessionDraft:{workspace:'/trial',selection:{}}},setup:{providersRequestedWorkspace:'/trial'},draftDefaults:{'["/trial",""]':{phase:'ready',effective:{instance:'old',model:'old-model'},providers:[{id:'old',info:{defaults:{model:'old-model'}}}]}}},root;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const render=()=>React.createElement(ModelControl,{state,act,working:false});
 await renderAct(async()=>{root=create(render());});
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/old-model/);
 state={...state,configurationRevision:1,draftDefaults:{},setup:{}};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/Loading model/);
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.equal(calls.filter(row=>row.name==='configuration.defaults').length,1);
 assert.equal(calls.filter(row=>row.name==='providers.list').length,1);
 // An explicit draft choice survives discovery and a preference change.
 state={...state,view:{newSessionDraft:{workspace:'/trial',selection:{instance:'chosen',model:'chosen-model'}}}};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/chosen-model/);
 await renderAct(async()=>root.unmount());
});
test('a new workspace stays idle until its model controls are explicitly opened',async()=>{
 const calls=[],session={id:'new-workspace-chat',status:'idle',deferRuntimeUntilInteraction:true};
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}},state={view:{}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session,act,ensureSession:async()=>session,working:false}))});
 assert.ok(!calls.some(call=>call.args?.operation==='configuration.providers'));
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.ok(calls.some(call=>call.name==='runtime.control'&&call.args.operation==='configuration.catalog'));
 await renderAct(async()=>root.unmount());
});
test('late catalogs preserve selector choices and shared provider actions',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}},session={id:'chat',status:'idle'};
 let state={view:{composerModel:{open:true,sessionId:'chat',instance:'openai',model:'first'}},runtimeControl:{chat:{}}},root;
 const render=()=>React.createElement(ModelControl,{state,session,act,ensureSession:async()=>session,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAllByProps({id:'chat-model'}).filter(n=>typeof n.type==='string').length,1);
 state={...state,runtimeControl:{chat:{'configuration.providers':{providers:[{id:'openai',info:{defaults:{model:'first'}}}],effective:{instance:'openai',model:'first'}},modelCatalogs:{openai:{phase:'ready',models:[{id:'first'},{id:'second'}]}}}}};
 await renderAct(async()=>root.update(render()));
 const picker=()=>root.root.findAllByProps({id:'chat-model'}).find(n=>typeof n.type==='string');
 await renderAct(async()=>picker().props.onChange({target:{value:'second'}}));
 assert.deepEqual(calls.filter(c=>c.args.operation==='provider.queueSelection').map(c=>c.args.args),[{instance:'openai',model:'second'}]);
 state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,modelCatalogs:{openai:{phase:'ready',models:[]}}}}};
 await renderAct(async()=>root.update(render()));
 assert.equal(picker().type,'select');
 assert.equal(picker().props.value,'second');
 // An agent uses this same action; its returned catalog must update the trigger.
 state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,'configuration.providers':{...state.runtimeControl.chat['configuration.providers'],pinned:true,effective:{instance:'openai',model:'agent-choice'}}}}};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/openai · agent-choice/);
 assert.ok(root.root.findAllByType('span').some(n=>n.children.includes('openai · agent-choice')));
 await renderAct(async()=>root.unmount());
});

for(const isDraft of [false,true])test('authoritative empty refresh replaces cached options, not the saved selection: '+(isDraft?'draft':'chat'),async()=>{
 const calls=[],providers=[{id:'one',sharedCatalogKey:'account-one',info:{defaults:{model:'saved'}}}];
 let entry={phase:'ready',models:['saved','retired']},root;
 const render=()=>{
  const state={configurationRevision:1,view:{composerModel:{open:true,sessionId:isDraft?null:'chat',instance:'one',model:'saved'},newSessionDraft:{workspace:'/trial',selection:{instance:'one',model:'saved'}}},
   modelCatalogs:{'account-one':entry},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:1,providers,effective:{instance:'one',model:'saved'}}}},
   setup:{providersRequestedWorkspace:'/trial',providers,providerCatalogs:{one:entry}},
   draftDefaults:{'["/trial",""]':{phase:'ready',providers,effective:{instance:'one',model:'saved'}}}};
  return React.createElement(ModelControl,{state,session:isDraft?null:{id:'chat'},act:async(name,args)=>calls.push({name,args})});
 };
 const values=()=>root.root.findByProps({id:'chat-model'}).findAllByType('option').map(n=>n.props.value);
 try{
  await renderAct(async()=>root=create(render()));assert.deepEqual(values(),['saved','retired']);
  for(const transient of [{phase:'working'},{phase:'error'},{phase:'ready',modelsDeferred:true}]){
   entry=transient;await renderAct(async()=>root.update(render()));assert.ok(values().includes('retired'));
  }
  const refresh=root.root.findAllByType('button').find(n=>n.children.includes('Refresh models'));
  await renderAct(async()=>refresh.props.onClick());
  assert.equal(isDraft?calls.at(-1).args.refresh:calls.at(-1).args.args.refresh,true);
  entry={phase:'ready',models:[]};await renderAct(async()=>root.update(render()));
  assert.deepEqual(values(),['saved']);assert.equal(root.root.findByProps({id:'chat-model'}).props.value,'saved');
  entry={phase:'ready',modelsDeferred:true};await renderAct(async()=>root.update(render()));assert.deepEqual(values(),['saved']);
  entry={phase:'ready',models:['saved','fresh']};await renderAct(async()=>root.update(render()));assert.deepEqual(values(),['saved','fresh']);
  assert.equal(calls.some(c=>c.args?.operation==='provider.queueSelection'),false);
  assert.equal(calls.some(c=>c.name==='view.update'&&c.args?.patch?.newSessionDraft),false);
 }finally{await renderAct(async()=>root?.unmount())}
});


test('draft model controls discover without creating a session and save choices through shared view actions',async()=>{
 const calls=[];let state={settings:{workspace:'/future'},view:{newSessionDraft:{workspace:'/future',bundle:'work',selection:{}}}},root;
 const act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state={...state,view:{...state.view,...args.patch}};return {accepted:true}};
 const render=()=>React.createElement(ModelControl,{state,session:null,act,ensureSession:async()=>{throw Error('Must not create a chat')},working:false});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.deepEqual(calls.find(c=>c.name==='providers.list').args,{workspace:'/future'});
 state={...state,draftDefaults:{'["/future","work"]':{phase:'ready',effective:{instance:'one',model:'first'}}},setup:{providersRequestedWorkspace:'/future',providers:[{id:'one',module:'provider-test',config:{model:'first'}}],providerCatalogs:{one:{phase:'ready',models:['first','chosen']}}}};
 await renderAct(async()=>root.update(render()));
 const picker=()=>root.root.findAllByProps({id:'chat-model'}).find(n=>typeof n.type==='string');
 await renderAct(async()=>picker().props.onChange({target:{value:'chosen'}}));
 assert.deepEqual(state.view.newSessionDraft.selection,{instance:'one',model:'chosen'});
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findAllByType('span').some(n=>n.children.includes('provider-test · chosen')));
 assert.equal(root.root.findByProps({id:'chat-provider'}).findAllByType('option').some(n=>n.children.includes('Use bundle default')),false);
 assert.equal(calls.some(c=>['session.create','runtime.control'].includes(c.name)),false);
 assert.equal(state.view.newSessionDraft.bundle,'work');await renderAct(async()=>root.unmount());
});

test('inherited choices are displayed without pinning and the popup opens before requests finish',async()=>{
 const calls=[];let state={settings:{workspace:'/new'},workspaces:[{id:'new',path:'/new'}],selectedWorkspaceId:'new',view:{},setup:{providersRequestedWorkspace:'/new'},draftDefaults:{'["/new",""]':{phase:'ready',bundle:'work',effective:{instance:'one',model:'actual-model',effort:'high'},providers:[{id:'one',info:{defaults:{model:'actual-model'}}}]}}};
 const act=(name,args)=>{calls.push({name,args});return new Promise(()=>{})};let root;
 await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,act,working:false}))});
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('one · actual-model (high)'));
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.equal(root.root.findAllByType('section').length,1);
 assert.equal(calls.some(c=>c.name==='session.create'||c.args?.patch?.newSessionDraft),false);
 assert.equal(root.root.findAllByType('input').length,0);
 state={...state,view:{newSessionDraft:{workspace:'/new',bundle:'another',selection:{}}}};
 await renderAct(async()=>root.update(React.createElement(ModelControl,{state,act,working:false})));
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('one · actual-model (high)'));
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.equal(calls.some(c=>c.name==='providers.list'),false);
 await renderAct(async()=>root.unmount());
});

test('same-type connections stay separate and changing models never changes the connection',async()=>{
 const calls=[],providers=[{id:'opus',info:{id:'anthropic',display_name:'Anthropic',defaults:{model:'opus-model'}}},{id:'fable',info:{id:'anthropic',display_name:'Anthropic',defaults:{model:'fable-model'}}},{id:'openai',info:{id:'openai',display_name:'OpenAI',defaults:{model:'openai-model'}}}];
 const state={view:{composerModel:{open:true,sessionId:'chat',instance:'fable',model:'fable-model'}},runtimeControl:{chat:{'configuration.providers':{providers,effective:{instance:'fable',model:'fable-model'}},modelCatalogs:{fable:{phase:'ready',models:['fable-model','opus-model']},opus:{phase:'ready',models:['opus-only']}}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:async(name,args)=>calls.push({name,args}),working:false}))});
 const choices=root.root.findByProps({id:'chat-provider'}).findAllByType('option');
 assert.deepEqual(choices.map(n=>[n.props.value,n.children.join('')]),[['fable','Anthropic (fable)'],['opus','Anthropic (opus)'],['openai','OpenAI']]);
 assert.equal(root.root.findByProps({id:'chat-provider'}).props.value,'fable');
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/Anthropic \(fable\)/);
 assert.equal(root.root.findByProps({id:'chat-model'}).findAllByType('option').some(n=>n.props.value==='opus-only'),false);
 await renderAct(async()=>root.root.findByProps({id:'chat-model'}).props.onChange({target:{value:'opus-model'}}));
 assert.deepEqual(calls.find(c=>c.args.operation==='provider.queueSelection').args.args,{instance:'fable',model:'opus-model'});
 await renderAct(async()=>root.root.findByProps({id:'chat-provider'}).props.onChange({target:{value:'opus'}}));
 assert.deepEqual(calls.filter(c=>c.args.operation==='provider.queueSelection').at(-1).args.args,{instance:'opus',model:'opus-model'});
 await renderAct(async()=>root.unmount());
});

test('deleted connection remains unavailable until an explicit replacement is selected',async()=>{
 const calls=[],wanted={instance:'removed',model:'saved-model',effort:'high'},providers=[{id:'remaining',info:{id:'anthropic',display_name:'Anthropic',defaults:{model:'different-model'}}}];
 const state={view:{composerModel:{open:true,sessionId:'chat'}},runtimeControl:{chat:{'configuration.providers':{providers,effective:wanted,selection:wanted,selectionIssue:'Choose a replacement.'}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:async(name,args)=>calls.push({name,args})}))});
 assert.equal(root.root.findByProps({id:'chat-provider'}).props.value,'removed');
 assert.equal(root.root.findByProps({id:'chat-model'}).props.value,'saved-model');
 assert.equal(root.root.findByProps({id:'chat-model'}).props.disabled,true);
 assert.ok(root.root.findByProps({id:'chat-provider'}).findAllByType('option').some(n=>n.children.join('')==='removed (unavailable)'));
 assert.equal(calls.some(c=>c.args.operation==='provider.queueSelection'),false);
 await renderAct(async()=>root.root.findByProps({id:'chat-provider'}).props.onChange({target:{value:'remaining'}}));
 assert.deepEqual(calls.find(c=>c.args.operation==='provider.queueSelection').args.args,{...wanted,instance:'remaining'});
 assert.equal(calls.some(c=>c.name==='session.send'),false);
 await renderAct(async()=>root.unmount());
});

test('catalog order does not select a connection when no effective connection is known',async()=>{
 const calls=[],state={view:{composerModel:{open:true,sessionId:'chat'}},runtimeControl:{chat:{'configuration.providers':{providers:[{id:'first',info:{defaults:{model:'first-model'}}}],effective:{}}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:async(name,args)=>calls.push({name,args})}))});
 assert.equal(root.root.findByProps({id:'chat-provider'}).props.value,'');
 assert.equal(root.root.findByProps({id:'chat-model'}).props.disabled,true);
 assert.equal(calls.some(c=>c.args.operation==='provider.queueSelection'),false);
 await renderAct(async()=>root.unmount());
});


test('managed drafts do not retain a prior location default while discovery loads',async()=>{
 const calls=[];let state={settings:{workspace:'/prior'},view:{newSessionDraft:{workspace:'/prior',bundle:'',selection:{}}},draftDefaults:{'["/prior",""]':{phase:'ready',bundle:'work',effective:{instance:'private',model:'workspace-model'},providers:[{id:'private',info:{defaults:{model:'workspace-model'}}}]}}};
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};let root;
 const render=()=>React.createElement(ModelControl,{state,act,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('private · workspace-model'));
 state={...state,view:{newSessionDraft:{workspace:'',location:{kind:'managed'},bundle:'',selection:{}}}};
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('Loading model…'));
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.deepEqual(calls.find(row=>row.name==='configuration.defaults').args,{workspace:'',bundle:'',location:{kind:'managed'}});
 assert.deepEqual(calls.find(row=>row.name==='providers.list').args,{workspace:'',location:{kind:'managed'}});
 assert.equal(calls.some(row=>row.name==='session.create'),false);
 await renderAct(async()=>root.unmount());
});

for(const [providerId,label] of [['openai','OpenAI'],['copilot-sdk','GitHub Copilot SDK'],['openai-chatgpt','OpenAI ChatGPT']]){
 test('closed selector identifies '+label+' even for an identical model',async()=>{
  const state={view:{},runtimeControl:{chat:{'configuration.providers':{providers:[{id:'alias',info:{id:providerId,display_name:label}}],effective:{instance:'alias',model:'same-model',effort:'high'}}}}};
  let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:async()=>({accepted:true}),working:false}))});
  assert.deepEqual(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children,[label+' · same-model (high)']);
  await renderAct(async()=>root.unmount());
 });
}
test('runtime report supplies readable provider before opening the selector',async()=>{
 const session={id:'chat',runtimeReport:{provider_choices:[{id:'routing-alias',provider:'copilot-sdk',display_name:'GitHub Copilot SDK',model:'same-model',effort:'high'}],effective_selection:{instance:'routing-alias',model:'same-model',effort:'high'}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state:{view:{}},session,act:async()=>({accepted:true}),working:false}))});
 assert.deepEqual(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children,['GitHub Copilot SDK · same-model (high)']);
 await renderAct(async()=>root.unmount());
});
test('closed conversation and optimistic navigation never browse catalogs',async()=>{
 const calls=[],session={id:'chat',selection:{instance:'private',model:'saved-model'}},act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 let root,state={view:{},navigationPending:true};
 await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session,act})));
 assert.equal(calls.length,0);
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.join(''),/saved-model/);
 state={view:{},configurationRevision:2};await renderAct(async()=>root.update(React.createElement(ModelControl,{state,session,act})));
 assert.equal(calls.length,0);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.equal(calls.filter(c=>c.args?.operation==='configuration.catalog').length,1);
 await renderAct(async()=>root.unmount());
});
for(const shared of [{},{open:false}])test('embedded picker retains server demand after initialization: '+JSON.stringify(shared),async()=>{
 const calls=[],state={view:{composerModel:shared},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers:[],effective:{}}}}};
 let root;
 await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session:{id:'chat'},embedded:true,act:async(name,args)=>calls.push({name,args})})));
 const updates=calls.filter(c=>c.name==='view.update');
 assert.ok(updates.length);assert.ok(updates.every(c=>c.args.patch.composerModel.open&&c.args.patch.composerModel.sessionId==='chat'));
 await renderAct(async()=>root.unmount());
 assert.equal(calls.at(-1).args.patch.composerModel.open,false);
});
test('an empty resolved provider catalog offers setup instead of loading forever',async()=>{
 const calls=[],state={view:{newSessionDraft:{workspace:'/trial'},composerModel:{open:true,sessionId:null}},draftDefaults:{'["/trial",""]':{phase:'ready',providers:[],effective:{}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,act:async(...args)=>calls.push(args)}))});
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('Set up a model'));
 const setup=root.root.findAllByType('button').find(row=>row.children.includes('Add connection'));
 await renderAct(async()=>setup.props.onClick());
 assert.equal(calls.at(-1)[1].patch.panel,'settings');
 assert.deepEqual(calls.at(-1)[1].patch.settingsExpanded,['ai-connections']);
 await renderAct(async()=>root.unmount());
});

test('model picker discloses inherited routing from the shared runtime catalog',async()=>{
 const session={id:'routing-chat',status:'idle'},state={view:{composerModel:{open:true,sessionId:'routing-chat'}},runtimeControl:{'routing-chat':{'configuration.providers':{providers:[],delegationRouting:{modelInheritance:'bundle',resolverActive:true,resolverName:'personal',matrixSource:'user',crossProviderRestriction:'not_enforced'}}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session,act:async()=>{},working:false}))});
 const text=root.root.findByProps({'data-part':'delegation-routing'}).children.join('');
 assert.match(text,/bundle and agent model settings/);assert.match(text,/personal/);assert.match(text,/user settings/);assert.match(text,/Other connected providers may be used/);
 await renderAct(async()=>root.unmount());
});

test('a configured model picker can add or manage connections without changing this conversation',async()=>{
 const calls=[],session={id:'chat',status:'idle'},state={view:{composerModel:{open:true,sessionId:'chat'}},runtimeControl:{chat:{'configuration.providers':{providers:[{id:'one',info:{defaults:{model:'first'}}}],effective:{instance:'one',model:'first'}}}}};
 let root;await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session,act:async(...args)=>calls.push(args)})));
 for(const [label,step] of [['Add connection','services'],['Manage connections','list']]){
  const button=root.root.findAllByType('button').find(row=>row.children.includes(label));assert.ok(button);
  calls.length=0;await renderAct(async()=>button.props.onClick());
  assert.equal(calls.length,1);assert.equal(calls[0][0],'view.update');
  assert.equal(calls[0][1].patch.panel,'settings');assert.equal(calls[0][1].patch.composerModel.open,false);
  assert.equal(calls[0][1].patch.aiConnectionEditor.step,step);
 }
 await renderAct(async()=>root.unmount());
});

for(const lost of ['evicted','restarted'])test('draft discovery recovers '+lost+' results without remount or a message send',async()=>{
 const calls=[];let root,state={hostInstanceId:'first',view:{newSessionDraft:{workspace:'/trial'}},setup:{providersRequestedWorkspace:'/trial'},draftDefaults:{}};
 const render=()=>React.createElement(ModelControl,{state,act:async(name,args)=>{calls.push({name,args});return {accepted:true}}});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
  assert.equal(calls.filter(c=>c.name==='configuration.defaults').length,1);
  if(lost==='evicted'){
   state={...state,draftDefaults:{'["/trial",""]':{phase:'ready',effective:{model:'chosen'},providers:[]}}};
   await renderAct(async()=>root.update(render()));
   state={...state,draftDefaults:{}};
  }else state={...state,hostInstanceId:'restarted'};
  await renderAct(async()=>root.update(render()));
  await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
  assert.equal(calls.filter(c=>c.name==='configuration.defaults').length,2);
  assert.ok(calls.every(c=>!['session.create','conversation.send'].includes(c.name)));
 }finally{await renderAct(async()=>root?.unmount())}
});
test('a rejected draft read can be retried in place without replacing explicit choices',async()=>{
 const calls=[];let root,state={view:{newSessionDraft:{workspace:'/trial',selection:{instance:'picked',model:'explicit'}},composerModel:{open:true,sessionId:null}},draftDefaults:{}};
 const render=()=>React.createElement(ModelControl,{state,act:async(name,args)=>{calls.push({name,args});return name==='configuration.defaults'?undefined:{accepted:true}}});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
  assert.match(JSON.stringify(root.toJSON()),/Could not load model settings/);
  await renderAct(async()=>root.root.findAllByType('button').find(n=>n.children.includes('Retry model settings')).props.onClick());
  await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
  assert.equal(calls.filter(c=>c.name==='configuration.defaults').length,2);
  assert.ok(calls.every(c=>!c.args?.patch?.newSessionDraft));
  assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/explicit/);
 }finally{await renderAct(async()=>root?.unmount())}
});
