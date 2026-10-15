import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
// Exercise the Settings flow; media decoding has its own tests.
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',
 optimizeDeps:{noDiscovery:true,include:[]},plugins:[{name:'inert-media-test',enforce:'pre',
 resolveId(id){if(id==='./inline-media')return '\0inert-media'},
 load(id){if(id==='\0inert-media')return 'export function MediaFigure(){return null}'}}]});
const {OutputSettings}=await server.ssrLoadModule('/src/outputs.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
for(const mimeType of ['image/gif','video/mp4']){
 test(`saved ${mimeType} detail uses exact provenance and the existing media figure`,async()=>{
  const selected={id:'saved-media',sessionId:'own',kind:'media',title:'Retained',
   filename:'clip',version:1,sha256:'a'.repeat(64),bytes:90,linked:false,
   media:{mimeType},origin:{source:'agent',messageId:'historical-origin'},versionEvidence:'snapshot'};
  let root;
  const calls=[],act=async(action,args)=>{calls.push({action,args});return {accepted:true,
   result:action==='outputs.list'?{items:[selected],nextOffset:null}:selected}};
  await renderAct(async()=>{root=create(React.createElement(OutputSettings,{session:{id:'own'},act}))});
  const button=action=>root.root.findAllByType('button').find(node=>node.props['data-action']===action);
  await renderAct(async()=>button('outputs.list').props.onClick());
  await renderAct(async()=>button('outputs.read').props.onClick());
  const figure=root.root.find(node=>node.type?.name==='MediaFigure').props;
  const html=JSON.stringify(root.toJSON());
  assert.ok(html.includes('historical-origin')&&html.includes('Immutable saved content'));
  assert.equal(figure.item.id,'saved-media');
  assert.equal(figure.messageId,'historical-origin');
  assert.equal(figure.dispatch,act);
  assert.equal(figure.openInCanvas,mimeType==='image/gif');
  assert.ok(!html.includes('External reference only'));
  assert.deepEqual(calls.at(-1),{action:'outputs.read',args:{sessionId:'own',id:'saved-media'}});
  await renderAct(async()=>root.unmount());
 });
}