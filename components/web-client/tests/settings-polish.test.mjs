// Structural/behavioral regressions only. Integrated browser checks own reflow,
// focus visibility, clipping, palettes and density; CSS source is not geometry.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {readFile} from 'node:fs/promises';

const require=createRequire(import.meta.url);
const voice={id:'voice-fixture',name:'Fixture voice',description:'Model help',source:'https://example.invalid',voices:[{id:'marin',name:'Marin',description:'Voice help'}]};
const compiled=await build({
 stdin:{contents:`export {VoiceSettings} from './settings-personal.jsx';export {MachineResources} from './testing-settings.jsx';export {TestRunnerBackendLimits} from './test-runner-backend-limits.jsx';export {WorktreeControls} from './worktrees.jsx';`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},
 bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom'],loader:{'.css':'empty'},
 plugins:[{name:'isolated-settings-inputs',setup(bundler){
  bundler.onResolve({filter:/voice_options\.json$/},()=>({path:'voice',namespace:'fixture'}));
  bundler.onResolve({filter:/^\.\/dictation-controls$/},()=>({path:'dictation',namespace:'fixture'}));
  bundler.onResolve({filter:/^\.\/integration-evidence$/},()=>({path:'integration',namespace:'fixture'}));
  bundler.onLoad({filter:/.*/,namespace:'fixture'},args=>({contents:args.path==='voice'?`export default ${JSON.stringify({models:[voice]})}`:args.path==='dictation'?'export const DictationSettings=()=>null;':'export const IntegrationEvidence=()=>null;',loader:'js'}));
 }}],
});
const module={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(require,module,module.exports);
const {VoiceSettings,MachineResources,TestRunnerBackendLimits,WorktreeControls}=module.exports;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const text=node=>typeof node==='string'?node:(node.children||[]).map(text).join('');
const button=(root,label)=>root.root.findAllByType('button').find(node=>text(node)===label);

test('voice connection choices have one semantic group and field help remains associated without preview or save',async()=>{
 const calls=[];let root;
 try{
  await act(async()=>{root=create(React.createElement(VoiceSettings,{
   state:{settings:{preferredVoice:voice.id,fallbackVoice:voice.id,voiceName:'marin'},voiceConfiguration:{available:true,environmentAvailable:true,source:'environment'}},
   act:async(name,args)=>{calls.push([name,args]);return {accepted:true}},
  }))});
  const sources=root.root.findByProps({className:'a-voice-key-sources'});
  assert.equal(sources.findByType('legend').children[0],'API key source');
  assert.equal(sources.findAllByType('input').length,2);
  assert.deepEqual(sources.findAllByType('input').map(node=>node.props.name),['voice-key-source','voice-key-source']);
  for(const id of ['preferred-voice','fallback-speaking-voice']){
   const control=root.root.findByProps({id});
   assert.equal(root.root.findByProps({id:control.props['aria-describedby']}).type,'p');
   assert.ok(root.root.findAllByType('label').some(node=>node.props.htmlFor===id));
  }
  const privateChoice=sources.findAllByType('input')[1];
  await act(async()=>privateChoice.props.onChange());
  assert.equal(root.root.findByProps({id:'voice-api-key'}).props.type,'password');
  assert.deepEqual(calls,[['voice.configuration',undefined]],'grouping and choosing a key source cannot save credentials or play a paid preview');
 }finally{if(root)await act(async()=>root.unmount())}
});

for(const kind of ['machine','backend'])test(`${kind} save/refresh actions share a wrapping group and retain distinct behavior`,async()=>{
 let root;const calls=[];
 const data=kind==='machine'?{
  supported:true,revision:3,limits:{},effective:{maxReservedCpu:4,maxReservedMemoryMiB:1024,minAvailableMemoryMiB:1024,minFreeBytes:0,maxLoadPercent:100,maxPreparing:2},
  reserved:{cpus:0,memoryMiB:0,count:0,preparing:0},observed:{availableMemoryMiB:1024,freeBytes:0},
 }:{revision:3,limits:{},defaults:{maxRunning:2,maxInstances:4,minFreeBytes:0},effective:{maxRunning:2,maxInstances:4,minFreeBytes:0}};
 const prefix=kind==='machine'?'testing.machine.':'testing.runners.capacity.';
 const dispatch=async(name,args)=>{calls.push([name,args]);assert.equal(name,prefix+'read');return {accepted:true,result:data}};
 const Component=kind==='machine'?MachineResources:TestRunnerBackendLimits;
 try{
  await act(async()=>{root=create(React.createElement(Component,{act:dispatch,id:'runner',name:'Fixture'}))});
  const saveLabel=kind==='machine'?'Save machine budgets':'Save environment limits';
  const refreshLabel=kind==='machine'?'Refresh machine resources':'Discard environment edits and refresh';
  const save=button(root,saveLabel),refresh=button(root,refreshLabel);
  assert.equal(save.parent,refresh.parent);
  assert.equal(save.parent.props.className,'a-dialog-actions');
  assert.equal(save.props.disabled,true,'presentation work cannot make an unchanged form saveable');
  await act(async()=>refresh.props.onClick());
  assert.deepEqual(calls.map(([name])=>name),[prefix+'read',prefix+'read']);
  assert.ok(root.root.findAllByType('fieldset').length>0,'native grouped fields remain');
 }finally{if(root)await act(async()=>root.unmount())}
});

test('component layout contracts keep help/action rhythm and lightweight nesting scoped',async()=>{
 const appearance=await readFile(new URL('../src/appearance-settings.css',import.meta.url),'utf8');
 const everyday=await readFile(new URL('../src/settings-everyday.css',import.meta.url),'utf8');
 const routing=await readFile(new URL('../src/settings-collections.css',import.meta.url),'utf8');
 assert.match(appearance,/\.a-appearance-saved-actions\{[^}]*flex-wrap:wrap;[^}]*gap:10px/);
 assert.match(appearance,/\.a-appearance-import \.a-file-drop>div:not\(\.a-check-result\)\{display:grid;gap:8px/);
 assert.match(appearance,/@media\(max-width:720px\)[^\n]*a-appearance-saved-row\{grid-template-columns:minmax\(0,1fr\)\}/);
 assert.match(everyday,/\.a-voice-settings \.a-everyday-disclosure\{border:0;border-top:1px solid var\(--a-line\)/);
 assert.match(everyday,/label\.a-field\{display:flex;flex-direction:column;align-items:stretch;gap:6px;margin:0/);
 assert.match(routing,/\.a-routing-selected\{padding:16px 0 0;border:0;border-top:1px solid var\(--a-line\)/);
 assert.match(routing,/\.a-routing-json\{display:grid;gap:8px;min-width:0/);
});

test('archived checkout preservation help stays with the resource, outside its button-only action group',async()=>{
 let root;const calls=[];
 try{
  await act(async()=>{root=create(React.createElement(WorktreeControls,{
   session:{id:'fixture',workspace:'/home/fixture',worktrees:[{id:'archived',path:'/retained/checkout',source:'/source',status:'archived',owned:true,revision:9}]},
   act:async(...args)=>calls.push(args),
  }))});
  const article=root.root.findByProps({'data-worktree-id':'archived'});
  const help=article.findAllByType('p').find(node=>text(node).startsWith('All files and Git history'));
  assert.equal(help.parent,article);
  const actions=article.findByProps({className:'a-dialog-actions'});
  assert.equal(actions.findAllByType('p').length,0);
  assert.deepEqual(actions.findAllByType('button').map(node=>node.props['data-action']),['worktree.status','worktree.restore']);
  assert.deepEqual(calls,[],'rendering a retained checkout never inspects, restores or removes it');
 }finally{if(root)await act(async()=>root.unmount())}
});
