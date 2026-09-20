import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkerRetentionSettings}=await server.ssrLoadModule('/src/worker-retention.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;

test('readiness settings use the shared host action and reject invalid limits',async()=>{
 const calls=[],state={runtime:{retention:{max_warm_workers:32,idle_timeout_hours:12,prewarm_on_select:true}}};let root,release;
 const act=(action,args)=>{calls.push({action,args});return new Promise(resolve=>release=resolve)};
 await renderAct(async()=>{root=create(React.createElement(WorkerRetentionSettings,{state,act}))});
 const field=id=>root.root.findByProps({id}),save=()=>root.root.findByType('button');
 assert.equal(field('warm-worker-count').props.value,'32');
 assert.equal(field('warm-worker-hours').props.value,'12');
 await renderAct(async()=>field('warm-worker-count').props.onChange({target:{value:'1.5'}}));
 assert.equal(save().props.disabled,true);
 await renderAct(async()=>field('warm-worker-count').props.onChange({target:{value:'100'}}));
 await renderAct(async()=>field('warm-worker-hours').props.onChange({target:{value:'336'}}));
 await renderAct(async()=>root.root.findByProps({type:'checkbox'}).props.onChange({target:{checked:false}}));
 let pending;
 await renderAct(async()=>{pending=root.root.findByType('form').props.onSubmit({preventDefault(){}})});
 assert.equal(save().props.disabled,true);
 assert.deepEqual(calls,[{action:'runtime.retention.update',args:{patch:{max_warm_workers:100,idle_timeout_hours:336,prewarm_on_select:false}}}]);
 await renderAct(async()=>{release();await pending});
 assert.equal(save().props.disabled,false);
 await renderAct(async()=>field('warm-worker-count').props.onChange({target:{value:'0'}}));
 assert.equal(save().props.disabled,false,'zero is the supported disable policy');
 await renderAct(async()=>root.unmount());
});
