// Exercise the real editor component with only its visual child controls stubbed.
// Shared theme props change just as main.jsx changes them after a peer's Apply.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {build} from 'esbuild';

globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const require=createRequire(new URL('../package.json',import.meta.url));
const stubs={
 './settings-layout':`import React from 'react';export const SettingsLayoutContext=React.createContext({active:true});`,
 './settings-ui':`export const FileDrop=()=>null;`,
 './working-label':`export const WorkingLabel=({children})=>children;`,
 './theme-presentation':`export const ThemeDecorationControl=()=>null;`,
 './execution-observability.jsx':`export const ObservabilityPreferences=()=>null;`,
 'lucide-react':`export const Check=()=>null,Download=Check,Upload=Check,Palette=Check,Sun=Check,Moon=Check,Monitor=Check,FolderOpen=Check,RefreshCw=Check;`,
};
const compiled=await build({
 stdin:{contents:`export {AppearanceSettings} from './appearance-settings.jsx';export {useAppearanceEditor,appearanceEditorKey} from './appearance-editor.jsx';`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},
 bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty'},
 plugins:[{name:'visual-children',setup(bundler){
  bundler.onResolve({filter:/.*/},args=>Object.hasOwn(stubs,args.path)?{path:args.path,namespace:'visual-child'}:undefined);
  bundler.onLoad({filter:/.*/,namespace:'visual-child'},args=>({contents:stubs[args.path],loader:'js'}));
 }}],
});
const module={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(require,module,module.exports);
const {AppearanceSettings,useAppearanceEditor,appearanceEditorKey}=module.exports;
function memoryStorage(){const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)}}
function EditorHost({storage,open=true,...props}){
 const editor=useAppearanceEditor(props.state.theme,props.defaultSkin,{storage});
 return open?React.createElement(AppearanceSettings,{...props,editor}):null;
}
const button=(root,label)=>root.findAllByType('button').find(node=>node.props['aria-label']===label||node.props.children===label);
const input=(root,id)=>root.findByProps({id});

test('saved target and dirty payload survive cross-client committed-theme prop replacement',async()=>{
 const original={id:'saved:ocean.css',source:'saved',fingerprint:'a'.repeat(64),name:'Ocean',description:'Original',css:'#amp-one{color:red}'};
 const calls=[];
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='theme.list')return {accepted:true,result:{items:[original],errors:[]}};
  if(action==='theme.read')return {accepted:true,result:original};
  if(action==='theme.update')return {accepted:true,result:{...original,...args,fingerprint:'c'.repeat(64)}};
  return {accepted:true};
 };
 const saved=memoryStorage(),storage=()=>saved;
 const props={state:{theme:{name:'Current',css:'#amp-one{color:black}'}},shell:{composition:{presentation:{}}},act:dispatch,storage,preview:false,setPreview:()=>{},defaultSkin:'#amp-one{}',advancedOnly:true};
 let renderer;
 try{
  await act(async()=>{renderer=create(React.createElement(EditorHost,props))});
  await act(async()=>{await button(renderer.root,'Preview Ocean').props.onClick()});
  await act(async()=>{await input(renderer.root,'theme-name').props.onChange({target:{value:'My edited Ocean'}})});
  await act(async()=>{await input(renderer.root,'theme-css').props.onChange({target:{value:'#amp-one{color:purple}'}})});
  await act(async()=>{await input(renderer.root,'theme-description').props.onChange({target:{value:'My local description'}})});
  const peerTheme={name:'Peer appearance',css:'#amp-one{color:blue}'};
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-name').props.value,'My edited Ocean');
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  assert.equal(input(renderer.root,'theme-description').props.value,'My local description');
  assert.deepEqual(calls.filter(row=>row.action==='view.update').at(-1).args.patch,{themeDraft:'#amp-one{color:purple}',themeDraftName:'My edited Ocean',themePreview:false});
  // Close/reopen unmounts Settings, but the main-held draft remains atomic.
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme},open:false}))});
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  // A full tab reload restores the exact target and payload from sessionStorage.
  await act(async()=>{renderer.unmount()});
  const stored=JSON.parse(saved.getItem(appearanceEditorKey));
  assert.equal(stored.entry.id,original.id);
  assert.equal(stored.entry.fingerprint,original.fingerprint);
  assert.equal(stored.description,'My local description');
  await act(async()=>{renderer=create(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-name').props.value,'My edited Ocean');
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  assert.equal(input(renderer.root,'theme-description').props.value,'My local description');
  const preview=renderer.root.findAllByType('button').find(node=>node.props['data-action']==='theme.preview'&&!node.props['aria-label']);
  await act(async()=>{await preview.props.onClick()});
  assert.deepEqual(calls.filter(row=>row.action==='theme.preview').at(-1).args,{name:'My edited Ocean',css:'#amp-one{color:purple}'});
  const update=renderer.root.findAllByType('button').find(node=>node.props['data-action']==='theme.update');
  await act(async()=>{await update.props.onClick()});
  assert.deepEqual(calls.find(row=>row.action==='theme.update').args,{
   id:original.id,expectedFingerprint:original.fingerprint,name:'My edited Ocean',
   css:'#amp-one{color:purple}',description:'My local description',
  });
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});

test('stale update rejection retains all local draft fields and the original read fingerprint',async()=>{
 const original={id:'saved:ocean.css',source:'saved',fingerprint:'a'.repeat(64),name:'Ocean',description:'Original',css:'#amp-one{color:red}'};
 const updates=[];
 const dispatch=async(action,args)=>{
  if(action==='theme.list')return {accepted:true,result:{items:[{...original,fingerprint:'b'.repeat(64)}],errors:[]}};
  if(action==='theme.read')return {accepted:true,result:original};
  if(action==='theme.update'){updates.push(args);throw Error('This appearance changed. Read it again.')}
  return {accepted:true};
 };
 let renderer;
 const saved=memoryStorage(),storage=()=>saved;
 try{
  await act(async()=>{renderer=create(React.createElement(EditorHost,{state:{theme:{}},shell:{composition:{presentation:{}}},act:dispatch,storage,preview:false,setPreview:()=>{},defaultSkin:'#amp-one{}'}))});
  await act(async()=>{await button(renderer.root,'Preview Ocean').props.onClick()});
  for(const [id,value] of [['theme-name','Draft title'],['theme-css','#amp-one{color:purple}'],['theme-description','Draft description']]){
   await act(async()=>{await input(renderer.root,id).props.onChange({target:{value}})});
  }
  const update=()=>renderer.root.findAllByType('button').find(node=>node.props['data-action']==='theme.update');
  await act(async()=>{await update().props.onClick()});
  assert.equal(input(renderer.root,'theme-name').props.value,'Draft title');
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  assert.equal(input(renderer.root,'theme-description').props.value,'Draft description');
  assert.match(renderer.root.findByProps({role:'alert'}).props.children,/appearance changed/);
  await act(async()=>{await update().props.onClick()});
  assert.equal(updates.length,2);
  assert.ok(updates.every(args=>args.expectedFingerprint===original.fingerprint));
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});