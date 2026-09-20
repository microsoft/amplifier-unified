import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ProviderSettings}=await server.ssrLoadModule('/src/setup.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

test('opening settings loads saved configuration and keeps subsequent edits during refresh',async()=>{
 const calls=[],dispatch=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 let state={view:{providerEditor:{id:'openai',module:'provider-openai'}},setup:{}},root;
 const render=()=>React.createElement(ProviderSettings,{state,session:{id:'s',workspace:'/work',status:'stopped'},act:dispatch});
 await renderAct(async()=>{root=create(render())});
 assert.equal(calls.filter(c=>c.name==='providers.list').length,1);
 assert.equal(calls.find(c=>c.name==='providers.list').args.sessionId,'s');
 state={...state,setup:{providersLoadedAt:Date.now()/1000+1,providersWorkspace:'/work',providers:[{id:'openai',module:'provider-openai',config:{default_model:'saved-model',reasoning_effort:'high'},credential:{envVar:'CUSTOM_KEY',available:true}}]}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAll(node=>['input','select'].includes(node.type)&&node.props.id==='provider-model')[0].props.value,'saved-model');
 assert.equal(root.root.findByProps({id:'provider-key-env'}).props.value,'CUSTOM_KEY');
 await renderAct(async()=>root.root.findAll(node=>['input','select'].includes(node.type)&&node.props.id==='provider-model')[0].props.onChange({target:{value:'my-edited-model'}}));
 state={...state,setup:{...state.setup,providersLoadedAt:Date.now()/1000+2}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAll(node=>['input','select'].includes(node.type)&&node.props.id==='provider-model')[0].props.value,'my-edited-model');
 await renderAct(async()=>root.unmount());
});

test('native choice edits persist in the save action; model checks have immediate progress',async()=>{
 const calls=[];let resolveModels;
 const dispatch=(name,args)=>{calls.push({name,args});return name==='providers.models'?new Promise(resolve=>{resolveModels=resolve}):Promise.resolve({accepted:true})};
 const state={view:{providerEditor:{id:'openai',module:'provider-openai',model:'model',envVar:'OPENAI_API_KEY',credentialMode:'environment',config:'{}'}},setup:{providers:[{id:'openai',module:'provider-openai'}],metadata:{'provider-openai':{info:{config_fields:[{id:'reasoning_effort',choices:['low','high'],field_type:'choice'}]}}}}};
 let root;
 await renderAct(async()=>{root=create(React.createElement(ProviderSettings,{state,act:dispatch}))});
 await renderAct(async()=>root.root.findByProps({id:'provider-option-reasoning_effort'}).props.onChange({target:{value:'high'}}));
 const button=action=>root.root.findAll(node=>node.type==='button'&&node.props['data-action']===action)[0];
 await renderAct(async()=>button('providers.save').props.onClick());
 assert.equal(calls.find(c=>c.name==='providers.save').args.config.reasoning_effort,'high');
 await renderAct(async()=>{button('providers.models').props.onClick()});
 assert.equal(button('providers.models').props.disabled,true);
 assert.match(JSON.stringify(root.toJSON()),/Discovering models/);
 await renderAct(async()=>resolveModels({accepted:true}));
 await renderAct(async()=>root.unmount());
});

test('provider test results remain clear when another action overwrites global management status',async()=>{
 const {ResultNotice}=await server.ssrLoadModule('/src/settings-ui.jsx');
 let root;
 await renderAct(async()=>{root=create(React.createElement(ResultNotice,{phase:'ready',message:'Provider check passed',detail:'32 models returned'}))});
 assert.equal(root.root.findByProps({role:'status'}).props.className,'a-check-result success');
 await renderAct(async()=>root.update(React.createElement(ResultNotice,{phase:'error',message:'Provider authentication failed'})));
 assert.equal(root.root.findByProps({role:'alert'}).props.className,'a-check-result error');
 await renderAct(async()=>root.unmount());
});

test('model selector uses provider catalog and falls back only when no list exists',async()=>{
 const {ModelSelect}=await server.ssrLoadModule('/src/model-select.jsx');
 let root;const props={id:'choice',label:'Model',value:'saved-model',state:{view:{}},act:async()=>{},onChange:()=>{}};
 await renderAct(async()=>{root=create(React.createElement(ModelSelect,{...props,entry:{phase:'ready',models:[{id:'catalog-model'}]}}))});
 assert.equal(root.root.findAllByType('select').length,1);
 assert.equal(root.root.findAllByType('input').length,0);
 assert.equal(root.root.findByType('select').props.value,'saved-model');
 await renderAct(async()=>root.update(React.createElement(ModelSelect,{...props,entry:{phase:'working',models:[]}})));
 assert.equal(root.root.findByType('select').props.disabled,true);
 assert.ok(root.root.findAllByType('option').some(row=>row.props.value==='catalog-model'),'Keep same-provider options during refresh');
 const changed=[],committed=[];
 await renderAct(async()=>root.update(React.createElement(ModelSelect,{...props,onChange:value=>changed.push(value),onCommit:value=>committed.push(value),entry:{phase:'error',models:[]}})));
 assert.equal(root.root.findAllByType('select').length,0);
 assert.ok(root.root.findAllByType('option').some(row=>row.props.value==='catalog-model'),'Retain cached models as suggestions after failure');
 const manual=root.root.findByType('input');
 await renderAct(async()=>{manual.props.onChange({target:{value:'new-model-id'}});manual.props.onBlur({target:{value:'new-model-id'}})});
 assert.deepEqual(changed,['new-model-id']);assert.deepEqual(committed,['new-model-id']);
 await renderAct(async()=>root.update(React.createElement(ModelSelect,{...props,catalogKey:'other-provider',entry:{phase:'working',models:[]}})));
 assert.ok(!root.root.findAllByType('option').some(row=>row.props.value==='catalog-model'),'Do not borrow options from another provider');
 await renderAct(async()=>root.update(React.createElement(ModelSelect,{...props,entry:{phase:'ready',supported:false,models:[]}})));
 assert.equal(root.root.findAllByType('select').length,0);assert.equal(root.root.findByType('input').props.value,'saved-model');
 await renderAct(async()=>root.unmount());
});
