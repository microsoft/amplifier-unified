import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {BundleControl,BundleDefaults}=await server.ssrLoadModule('/src/bundle-controls.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
test('late catalog preserves selection and shared actions target the explicit conversation',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 let session={id:'chat',bundle:'anchors',status:'idle'},state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}}},root;
 const render=()=>React.createElement(BundleControl,{state,session,act,working:false});
 await renderAct(async()=>{root=create(render())});state={...state,registeredBundles:[{name:'work',label:'Work',value:'work'}]};
 await renderAct(async()=>root.update(render()));assert.equal(root.root.findAllByProps({id:'conversation-bundle'}).find(n=>n.type==='select').props.value,'work');
 await renderAct(async()=>root.root.findByProps({'data-action':'bundle.preview'}).props.onClick());assert.deepEqual(calls.find(row=>row.name==='bundle.preview').args,{sessionId:'chat',bundle:'work'});
 session={...session,bundlePreview:{bundle:'work',previewId:'token',modelCompatible:true,changes:{}},bundleChange:{phase:'ready',action:'bundle.preview'}};
 await renderAct(async()=>root.update(render()));await renderAct(async()=>root.root.findByProps({'data-action':'bundle.switch'}).props.onClick());assert.deepEqual(calls.find(row=>row.name==='bundle.switch').args,{sessionId:'chat',bundle:'work',previewId:'token',resetModel:false});
 session={...session,bundleChange:{phase:'error',error:'Failed mount'}};await renderAct(async()=>root.update(render()));assert.equal(root.root.findAllByProps({id:'conversation-bundle'}).find(n=>n.type==='select').props.value,'work');assert.equal(root.root.findByProps({role:'alert'}).findByType('strong').children.join(''),'Failed mount');await renderAct(async()=>root.unmount());
});
test('default selection remains a draft until saved with an explicit scope',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}},state={view:{},bundleDefaults:{app:null,shared:'anchors',effective:'anchors',source:'shared',workspacePath:'/workspace'},registeredBundles:[{name:'work',value:'work'}]};
 let root;await renderAct(async()=>{root=create(React.createElement(BundleDefaults,{state,act}))});await renderAct(async()=>root.root.findAllByProps({id:'default-bundle'}).find(n=>n.type==='select').props.onChange({target:{value:'work'}}));assert.equal(calls.filter(row=>row.name==='bundle.default').length,0);
 await renderAct(async()=>root.root.findAllByProps({'data-action':'bundle.default'})[0].props.onClick());assert.deepEqual(calls.find(row=>row.name==='bundle.default').args,{scope:'app',bundle:'work',workspace:'/workspace'});await renderAct(async()=>root.unmount());
});
