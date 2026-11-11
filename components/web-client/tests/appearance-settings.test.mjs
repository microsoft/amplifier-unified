// Exercise the real editor component with only its visual child controls stubbed.
// Shared theme props change just as main.jsx changes them after a peer's Apply.
import test from 'node:test';
import assert from 'node:assert/strict';
import {sparseExecutionVisibilityPatch} from '../src/appearance-preferences.js';
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
 './theme-presentation':`export const useThemeScheme=scheme=>scheme==='dark'?'dark':'light';`,
 './appearance-thumbnail':`export const AppearanceThumbnail=()=>null;`,
 'lucide-react':`export const Check=()=>null,Download=Check,FolderOpen=Check,Palette=Check,Upload=Check;`,
};
const componentEntry=`export {AppearanceSettings} from './appearance-settings.jsx';export {useAppearanceEditor,appearanceEditorStorageKey} from './appearance-editor.jsx';export {SettingsLayoutContext} from './settings-layout';export {clearAppearanceCatalogs} from './appearance-catalog';`;
async function compileSettings(visualChildren){
 return build({
  stdin:{contents:componentEntry,resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},
  bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty'},
  plugins:[{name:'visual-children',setup(bundler){
   bundler.onResolve({filter:/.*/},args=>Object.hasOwn(visualChildren,args.path)?{path:args.path,namespace:'visual-child'}:undefined);
   bundler.onLoad({filter:/.*/,namespace:'visual-child'},args=>({contents:visualChildren[args.path],loader:'js'}));
  }}],
 });
}
const compiled=await compileSettings(stubs),module={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(require,module,module.exports);
const {AppearanceSettings,useAppearanceEditor,appearanceEditorStorageKey,SettingsLayoutContext,clearAppearanceCatalogs}=module.exports;
const thumbnailStubs={...stubs,'./appearance-thumbnail-scene':`export const renderAppearanceThumbnail=(css,mode)=>'<main data-thumbnail-mode="'+mode+'">'+css+'</main>';`};
delete thumbnailStubs['./appearance-thumbnail'];
const thumbnailCompiled=await compileSettings(thumbnailStubs),thumbnailModule={exports:{}};
new Function('require','module','exports',thumbnailCompiled.outputFiles[0].text)(require,thumbnailModule,thumbnailModule.exports);
const {
 AppearanceSettings:AppearanceSettingsWithThumbnail,
 SettingsLayoutContext:ThumbnailSettingsLayoutContext,
 clearAppearanceCatalogs:clearThumbnailCatalogs,
}=thumbnailModule.exports;
const testEditorKey=appearanceEditorStorageKey('unattached');
test.beforeEach(()=>clearAppearanceCatalogs());
function memoryStorage(){const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)}}
function EditorHost({storage,open=true,active=true,...props}){
 const editor=useAppearanceEditor(props.state.appearance?.theme||props.state.theme,props.referenceCss??'',{storage,namespace:props.state.appearance?.namespace});
 return open?React.createElement(SettingsLayoutContext.Provider,{value:{active}},React.createElement(AppearanceSettings,{...props,editor})):null;
}
const textContent=node=>typeof node==='string'?node:(node.children||[]).map(textContent).join('');
const button=(root,label)=>root.findAllByType('button').find(node=>node.props['aria-label']===label||textContent(node)===label);
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
 const props={state:{theme:{name:'Current',css:'#amp-one{color:black}'}},act:dispatch,storage,referenceCss:'',advancedOnly:true};
 let renderer;
 try{
  await act(async()=>{renderer=create(React.createElement(EditorHost,props))});
  await act(async()=>{await button(renderer.root,'Edit Ocean').props.onClick()});
  await act(async()=>{await input(renderer.root,'theme-name').props.onChange({target:{value:'My edited Ocean'}})});
  await act(async()=>{await input(renderer.root,'theme-css').props.onChange({target:{value:'#amp-one{color:purple}'}})});
  await act(async()=>{await input(renderer.root,'theme-description').props.onChange({target:{value:'My local description'}})});
  const peerTheme={name:'Peer appearance',css:'#amp-one{color:blue}'};
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-name').props.value,'My edited Ocean');
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  assert.equal(input(renderer.root,'theme-description').props.value,'My local description');
  const privateDraft=JSON.parse(saved.getItem(testEditorKey));
  assert.equal(privateDraft.version,2);
  assert.equal(privateDraft.draft.name,'My edited Ocean');
  assert.equal(privateDraft.draft.css,'#amp-one{color:purple}');
  assert.ok(!calls.some(row=>row.action==='view.update'),'Advanced editor drafts stay private to this browser tab.');
  // Close/reopen unmounts Settings, but the main-held draft remains atomic.
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme},open:false}))});
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  // A full tab reload restores the exact target and payload from sessionStorage.
  await act(async()=>{renderer.unmount()});
  const stored=JSON.parse(saved.getItem(testEditorKey));
  assert.equal(stored.draft.entry.id,original.id);
  assert.equal(stored.draft.entry.fingerprint,original.fingerprint);
  assert.equal(stored.draft.description,'My local description');
  await act(async()=>{renderer=create(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-name').props.value,'My edited Ocean');
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  assert.equal(input(renderer.root,'theme-description').props.value,'My local description');
  const preview=renderer.root.findAllByType('button').find(node=>node.props['data-action']==='appearance.preview'&&!node.props['aria-label']);
  await act(async()=>{await preview.props.onClick()});
  assert.deepEqual(calls.filter(row=>row.action==='appearance.preview').at(-1).args,{name:'My edited Ocean',css:'#amp-one{color:purple}'});
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
  await act(async()=>{renderer=create(React.createElement(EditorHost,{state:{theme:{}},act:dispatch,storage,referenceCss:''}))});
  await act(async()=>{await button(renderer.root,'Edit Ocean').props.onClick()});
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

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const actionButton=(root,action)=>root.findAllByType('button').find(node=>node.props['data-action']===action&&!node.props['aria-label']);
const entryA={id:'saved:ocean.css',source:'saved',fingerprint:'a'.repeat(64),name:'Ocean',description:'Original',css:'#amp-one{color:red}'};
const entryB={id:'saved:forest.css',source:'saved',fingerprint:'b'.repeat(64),name:'Forest',description:'Second',css:'#amp-one{color:green}'};
async function heldEditor(operation){
 const saved=memoryStorage(),storage=()=>saved,held=deferred(),calls=[];
 const items=new Map([entryA,entryB].map(entry=>[entry.id,entry]));
 const result=operation==='save'?{...entryA,id:'saved:copy.css',fingerprint:'c'.repeat(64)}:{...entryA,fingerprint:'c'.repeat(64)};
 let shouldHold=true,nextHold;
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='theme.list')return {accepted:true,result:{items:[...items.values()],errors:[]}};
  if(action==='theme.read')return {accepted:true,result:items.get(args.id)};
  if(action==='theme.'+operation){
   const pending=shouldHold?held:nextHold;shouldHold=false;nextHold=undefined;
   const receipt=pending?await pending.promise:{accepted:true,result};
   if(receipt?.accepted){if(operation==='delete')items.delete(args.id);else items.set(receipt.result.id,receipt.result)}
   return receipt;
  }
  return {accepted:true};
 };
 const props={state:{theme:{name:'Current',css:'#amp-one{color:black}'}},act:dispatch,storage,referenceCss:''};
 let renderer;
 await act(async()=>{renderer=create(React.createElement(EditorHost,props))});
 await act(async()=>{await button(renderer.root,'Edit Ocean').props.onClick()});
 const start=async()=>{
  if(operation==='delete')await act(async()=>{button(renderer.root,'Delete Ocean').props.onClick()});
  let completion;
  await act(async()=>{completion=actionButton(renderer.root,'theme.'+operation).props.onClick()});
  assert.equal(input(renderer.root,'theme-name').props.disabled,true,'The active mount locks during its own mutation');
  return {completion};
 };
 const remount=async()=>{
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,open:false}))});
  await act(async()=>{renderer.update(React.createElement(EditorHost,props))});
  assert.equal(input(renderer.root,'theme-name').props.disabled,false,'A reopened editor is usable without replaying a pending mutation');
 };
 const reactivate=async()=>{
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,active:false}))});
  await act(async()=>{renderer.update(React.createElement(EditorHost,props))});
  assert.equal(input(renderer.root,'theme-name').props.disabled,false,'Returning to an inactive Settings section releases only its abandoned local lock');
 };
 return {saved,held,calls,items,result,start,remount,reactivate,
  setActive:active=>act(async()=>renderer.update(React.createElement(EditorHost,{...props,active}))),
  holdNext(){return nextHold=deferred()},get root(){return renderer.root},close:()=>act(async()=>renderer.unmount())};
}
async function setFields(root,values){
 for(const [id,value] of Object.entries(values))await act(async()=>{await input(root,id).props.onChange({target:{value}})});
}
const newerFields={'theme-name':'Successor draft','theme-description':'Successor description','theme-css':'#amp-one{color:purple}'};

for(const outcome of ['success','failure']){
 test(`held save exposes accessible progress and clears it after ${outcome}`,async()=>{
  const host=await heldEditor('save');
  try{
   await host.start();
   const saving=()=>host.root.findAllByProps({role:'status'}).find(node=>textContent(node)==='Saving appearance…');
   assert.ok(saving(),'The active save has an accessible pending status.');
   assert.equal(input(host.root,'theme-name').props.disabled,true);
   await act(async()=>{
    if(outcome==='success')host.held.resolve({accepted:true,result:host.result});
    else host.held.reject(Error('Save unavailable'));
    await new Promise(resolve=>setImmediate(resolve));
   });
   assert.equal(saving(),undefined,'The pending save status clears when its request settles.');
   assert.equal(input(host.root,'theme-name').props.disabled,false);
   if(outcome==='failure')assert.match(host.root.findByProps({role:'alert'}).props.children,/Save unavailable/);
   else assert.equal(host.root.findAllByProps({role:'alert'}).length,0);
  }finally{await host.close()}
 });
}

for(const operation of ['save','update','delete']){
 for(const successor of ['edit','target']){
  test(`held ${operation} completion after remount preserves successor ${successor}, fields and fingerprint`,async()=>{
   const host=await heldEditor(operation);
   try{
    const {completion}=await host.start();
    await host.remount();
    if(successor==='target')await act(async()=>{await button(host.root,'Edit Forest').props.onClick()});
    await setFields(host.root,newerFields);
    const expected=JSON.parse(host.saved.getItem(testEditorKey));
    await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
    for(const [id,value] of Object.entries(newerFields))assert.equal(input(host.root,id).props.value,value);
    assert.deepEqual(JSON.parse(host.saved.getItem(testEditorKey)),expected,'No late result can mark newer intent clean or change its target');
    assert.equal(input(host.root,'theme-name').props.disabled,false);
    assert.equal(host.calls.filter(call=>call.action==='theme.'+operation).length,1,'Remount/completion cannot replay a mutation');
    if(operation==='delete')assert.equal(host.items.has(entryA.id),false,'The confirmed deletion still happened');
    else assert.deepEqual(host.items.get(host.result.id),host.result,'The confirmed saved result remains in the library');
   }finally{await host.close()}
  });
 }
 test(`held ${operation} can settle the unchanged persistent draft after remount`,async()=>{
  const host=await heldEditor(operation);
  try{
   const {completion}=await host.start();
   await host.remount();
   await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
   const stored=JSON.parse(host.saved.getItem(testEditorKey));
   assert.equal(stored.draft.entry?.id||null,operation==='delete'?null:host.result.id);
   assert.equal(stored.draft.entry?.fingerprint||null,operation==='delete'?null:host.result.fingerprint);
   assert.equal(stored.draft.dirty,operation==='delete');
   assert.equal(input(host.root,'theme-name').props.disabled,false);
  }finally{await host.close()}
 });
 for(const outcome of ['rejected','missing','thrown']){
  test(`${operation} ${outcome} completion preserves draft and releases its lock without retry`,async()=>{
   const host=await heldEditor(operation);
   try{
    const expected=host.saved.getItem(testEditorKey),{completion}=await host.start();
    await act(async()=>{
     if(outcome==='thrown')host.held.reject(Error('Mutation unavailable'));
     else host.held.resolve(outcome==='missing'?undefined:{accepted:false,error:'Mutation rejected'});
     await completion;
    });
    assert.equal(host.saved.getItem(testEditorKey),expected,'Failure or unknown completion is not a confirmed save/delete');
    assert.equal(input(host.root,'theme-name').props.disabled,false);
    assert.equal(host.root.findAllByProps({role:'alert'}).length,1,'Failure has visible feedback');
    assert.equal(host.calls.filter(call=>call.action==='theme.'+operation).length,1);
    await setFields(host.root,newerFields);
    // A deliberate retry is possible after settlement, but never automatic.
    if(operation==='delete')await act(async()=>{button(host.root,'Delete Ocean').props.onClick()});
    await act(async()=>{await actionButton(host.root,'theme.'+operation).props.onClick()});
    assert.equal(host.calls.filter(call=>call.action==='theme.'+operation).length,2);
    assert.equal(input(host.root,'theme-name').props.disabled,false);
   }finally{await host.close()}
  });
 }
}

for(const outcome of ['success','failure']){
 test(`late ${outcome} from an old mount cannot settle a successor operation's lock or draft`,async()=>{
  const host=await heldEditor('save');
  try{
   const {completion}=await host.start();
   await host.remount();
   await setFields(host.root,newerFields);
   const expected=host.saved.getItem(testEditorKey),newSave=host.holdNext();
   let newCompletion;
   await act(async()=>{newCompletion=actionButton(host.root,'theme.save').props.onClick()});
   assert.equal(input(host.root,'theme-name').props.disabled,true);
   await act(async()=>{
    if(outcome==='failure')host.held.reject(Error('Old mutation unavailable'));
    else host.held.resolve({accepted:true,result:host.result});
    await completion;
   });
   assert.equal(host.saved.getItem(testEditorKey),expected);
   assert.equal(host.root.findAllByProps({role:'alert'}).length,0);
   assert.equal(input(host.root,'theme-name').props.disabled,true,'Only the successor receipt releases its submission lock');
   const result={...entryB,name:newerFields['theme-name'],css:newerFields['theme-css'],description:newerFields['theme-description']};
   await act(async()=>{newSave.resolve({accepted:true,result});await newCompletion});
   assert.equal(input(host.root,'theme-name').props.disabled,false);
   assert.equal(JSON.parse(host.saved.getItem(testEditorKey)).draft.entry.id,result.id);
   assert.equal(host.calls.filter(call=>call.action==='theme.save').length,2);
  }finally{await host.close()}
 });
}

test('changing a draft away and back still supersedes the earlier mutation revision',async()=>{
 const host=await heldEditor('save');
 try{
  await setFields(host.root,{'theme-description':'Already dirty before submission'});
  const submitted=host.saved.getItem(testEditorKey);
  const {completion}=await host.start();
  await host.remount();
  await setFields(host.root,{'theme-name':'Temporary title'});
  await setFields(host.root,{'theme-name':entryA.name});
  const expected=host.saved.getItem(testEditorKey);
  assert.equal(expected,submitted,'Every field, target, fingerprint and dirty flag is equal again; only the revision changed');
  await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
  assert.equal(host.saved.getItem(testEditorKey),expected,'Compare the draft revision, not merely equal field values');
 }finally{await host.close()}
});

test('a new read fingerprint on the same target supersedes the held update result',async()=>{
 const host=await heldEditor('update');
 try{
  const {completion}=await host.start();
  await host.remount();
  host.items.set(entryA.id,{...entryA,fingerprint:'d'.repeat(64)});
  await act(async()=>{await button(host.root,'Edit Ocean').props.onClick()});
  const expected=host.saved.getItem(testEditorKey);
  await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
  assert.equal(host.saved.getItem(testEditorKey),expected);
  assert.equal(JSON.parse(expected).draft.entry.fingerprint,'d'.repeat(64));
 }finally{await host.close()}
});

test('section deactivation releases abandoned UI ownership without letting its late receipt unlock a new operation',async()=>{
 const host=await heldEditor('save');
 try{
  const {completion}=await host.start();
  await host.reactivate();
  await setFields(host.root,newerFields);
  const expected=host.saved.getItem(testEditorKey),next=host.holdNext();
  let nextCompletion;
  await act(async()=>{nextCompletion=actionButton(host.root,'theme.save').props.onClick()});
  await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
  assert.equal(host.saved.getItem(testEditorKey),expected);
  assert.equal(input(host.root,'theme-name').props.disabled,true);
  await act(async()=>{next.reject(Error('Successor save unavailable'));await nextCompletion});
  assert.equal(input(host.root,'theme-name').props.disabled,false);
  assert.match(host.root.findByProps({role:'alert'}).props.children,/Successor save unavailable/);
 }finally{await host.close()}
});

test('a failure arriving on an inactive section remains visible for the unchanged draft on return',async()=>{
 const host=await heldEditor('save');
 try{
  const expected=host.saved.getItem(testEditorKey),{completion}=await host.start();
  await host.setActive(false);
  await act(async()=>{host.held.reject(Error('Inactive save failed'));await completion});
  await host.setActive(true);
  assert.equal(host.saved.getItem(testEditorKey),expected);
  assert.equal(input(host.root,'theme-name').props.disabled,false);
  assert.match(host.root.findByProps({role:'alert'}).props.children,/Inactive save failed/);
 }finally{await host.close()}
});

for(const operation of ['read','revert'])test(`a late ${operation} callback after section return cannot replace a newer editor target`,async()=>{
 const held=deferred(),saved=memoryStorage(),storage=()=>saved,calls=[];let hold=false,renderer;
 const dispatch=async(action,args)=>{
  calls.push([action,args]);
  if(action==='theme.list')return {accepted:true,result:{items:[entryA,entryB],errors:[]}};
  if(action==='theme.read'){
   if(hold&&args.id===entryA.id)return held.promise;
   return {accepted:true,result:args.id===entryB.id?entryB:entryA};
  }
  return {accepted:true};
 };
 const props={state:{theme:{name:'Current',css:'#amp-one{color:black}'}},act:dispatch,storage,referenceCss:''};
 try{
  await act(async()=>{renderer=create(React.createElement(EditorHost,props))});
  if(operation==='revert')await act(async()=>button(renderer.root,'Edit Ocean').props.onClick());
  hold=true;
  await act(async()=>button(renderer.root,operation==='read'?'Edit Ocean':'Revert editor changes').props.onClick());
  assert.equal(input(renderer.root,'theme-name').props.disabled,true);
  await act(async()=>renderer.update(React.createElement(EditorHost,{...props,active:false})));
  await act(async()=>renderer.update(React.createElement(EditorHost,props)));
  assert.equal(input(renderer.root,'theme-name').props.disabled,false,'Section return releases the abandoned read lock.');
  await act(async()=>button(renderer.root,'Edit Forest').props.onClick());
  await setFields(renderer.root,newerFields);
  const expected=saved.getItem(testEditorKey);
  await act(async()=>{held.resolve({accepted:true,result:entryA});await held.promise});
  assert.equal(saved.getItem(testEditorKey),expected);
  assert.equal(input(renderer.root,'theme-name').props.value,newerFields['theme-name']);
  assert.equal(JSON.parse(expected).draft.entry.id,entryB.id);
  assert.equal(input(renderer.root,'theme-name').props.disabled,false);
  assert.equal(calls.filter(([action,args])=>action==='theme.read'&&args.id===entryA.id).length,operation==='read'?1:2);
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});

test('saved library controls identify the exact item in their accessible names',async()=>{
 const host=await heldEditor('save');
 try{
  for(const name of ['Ocean','Forest'])for(const action of ['Use','Edit','Delete']){
   assert.equal(button(host.root,`${action} ${name}`).props['aria-label'],`${action} ${name}`);
  }
  await act(async()=>button(host.root,'Delete Forest').props.onClick());
  assert.match(host.root.findByProps({'aria-label':'Delete appearance confirmation'}).findByType('p').children.join(''),/Forest/);
 }finally{await host.close()}
});

for(const operation of ['save','update','delete']){
 test(`${operation} failure retained while inactive disappears when its draft is superseded before returning`,async()=>{
  const host=await heldEditor(operation);
  try{
   const {completion}=await host.start();
   await host.setActive(false);
   await act(async()=>{host.held.reject(Error('Inactive draft mutation failed'));await completion});
   const unchanged=host.saved.getItem(testEditorKey);
   assert.match(host.root.findByProps({role:'alert'}).props.children,/Inactive draft mutation failed/);
   await setFields(host.root,newerFields);
   const newer=host.saved.getItem(testEditorKey);assert.notEqual(newer,unchanged);
   await host.setActive(true);
   assert.equal(host.saved.getItem(testEditorKey),newer,'Late failure cannot write the private draft.');
   assert.equal(host.root.findAllByProps({role:'alert'}).length,0,'Only the failed draft revision owns that error.');
   assert.equal(input(host.root,'theme-name').props.disabled,false);
   assert.equal(host.calls.filter(call=>call.action==='theme.'+operation).length,1,'Returning never retries an abandoned mutation.');
  }finally{await host.close()}
 });
 for(const successor of ['unchanged','edit','operation']){
  test(`${operation} failure after section return respects ${successor} feedback ownership`,async()=>{
   const host=await heldEditor(operation);
   try{
    const {completion}=await host.start();
    await host.reactivate();
    if(successor==='edit')await setFields(host.root,newerFields);
    const expected=host.saved.getItem(testEditorKey);
    let next,nextCompletion;
    if(successor==='operation'){
     next=host.holdNext();
     await act(async()=>{
      if(operation==='delete')button(host.root,'Delete Ocean').props.onClick();
     });
     await act(async()=>{nextCompletion=actionButton(host.root,'theme.'+operation).props.onClick()});
    }
    await act(async()=>{host.held.reject(Error('Returned section mutation failed'));await completion});
    assert.equal(host.saved.getItem(testEditorKey),expected);
    const alerts=host.root.findAllByProps({role:'alert'});
    if(successor==='unchanged')assert.match(alerts[0]?.props.children||'',/Returned section mutation failed/);
    else assert.equal(alerts.length,0,'Superseded intent owns its own failure feedback');
    assert.equal(input(host.root,'theme-name').props.disabled,successor==='operation');
    if(next){
     await act(async()=>{next.reject(Error('Successor mutation failed'));await nextCompletion});
     assert.match(host.root.findByProps({role:'alert'}).props.children,/Successor mutation failed/);
     assert.equal(input(host.root,'theme-name').props.disabled,false);
    }
    assert.equal(host.calls.filter(row=>row.action==='theme.'+operation).length,successor==='operation'?2:1);
   }finally{await host.close()}
  });
 }
}

const preferenceDefaults={
 appearanceId:'reference',scheme:'system',layout:'balanced',decorations:true,density:'comfortable',
 interfaceDetail:'standard',conversationStatus:false,collapseInterimReplies:false,workStartView:'appearance',
 planPlacement:'composer',executionDetail:'standard',executionVisibility:{},executionMetrics:[],
};
function preferenceSnapshot({activeScope='client',rendered={},shared={},localValues,localRevision=1,sharedRevision=1,storageVersion='browser-version'}={}){
 const sharedValues={...preferenceDefaults,...shared};
 const browserValues=localValues===null?null:{...preferenceDefaults,...(localValues||rendered)};
 const effective=activeScope==='client'?(browserValues||sharedValues):sharedValues;
 return {namespace:'appearance-test',generation:'generation-1',availability:'connected',activeScope,writeScope:activeScope,
  theme:{name:'Reference',css:''},renderedPreferences:structuredClone(effective),effective:structuredClone(effective),
  local:{revision:localRevision,storageVersion,values:browserValues,reported:true,error:null},
  shared:{values:sharedValues,revision:sharedRevision}};
}
function PreferenceHost({active=true,appearance=preferenceSnapshot(),dispatch=async action=>action==='theme.list'?{accepted:true,result:{items:[],errors:[]}}:{accepted:true,result:{status:'stored',undoId:'unit-undo'}},...props}={}){
 return React.createElement(SettingsLayoutContext.Provider,{value:{active}},React.createElement(AppearanceSettings,{
  state:{client:{hostInstanceId:'appearance-preference-tests'}},appearance,act:dispatch,editor:{},...props,
 }));
}
async function renderPreferencePage(options={}){
 let renderer;await act(async()=>{renderer=create(React.createElement(PreferenceHost,options))});return renderer;
}
const fieldRow=(renderer,field)=>renderer.root.findByProps({'data-appearance-preference':field});
const buttonEvent={currentTarget:{getAttribute:()=>null}};

test('accent picker resolves computed light-dark color without persisting the derived value',async()=>{
 const previousDocument=globalThis.document,previousWindow=globalThis.window,listeners=new Map(),computedTargets=[];
 const appRoot={children:[],appendChild(node){this.children.push(node);node.parentNode=this;return node},removeChild(node){this.children.splice(this.children.indexOf(node),1);node.parentNode=null;return node}};
 globalThis.document={activeElement:null,documentElement:appRoot,getElementById:id=>id==='amp-one'?appRoot:null,
  createElement:tag=>({tagName:tag.toUpperCase(),style:{},parentNode:null}),
  addEventListener:(name,listener)=>listeners.set(name,listener),removeEventListener:(name,listener)=>listeners.delete(name)};
 globalThis.window={getComputedStyle(node){
  computedTargets.push(node);
  if(node===appRoot)return {getPropertyValue:property=>property==='--a-accent'?'light-dark(#5140ef, #b1a4ff)':''};
  assert.equal(node.parentNode,appRoot);
  assert.equal(node.style.color,'var(--a-accent)');
  return {color:'rgb(81, 64, 239)'};
 }};
 const changes=[];let renderer;
 try{
  renderer=await renderPreferencePage({onPreferenceChange:async(...args)=>{changes.push(args);return {status:'stored'}}});
  assert.equal(renderer.root.findByProps({'aria-label':'Accent color'}).props.value,'#5140ef');
  assert.ok(computedTargets.some(node=>node!==appRoot),'The resolved color comes from a temporary computed-color probe.');
  assert.equal(appRoot.children.length,0,'The probe is removed after reading its computed color.');
  assert.deepEqual(changes,[],'A display-only derived color is not persisted before the user edits it.');
 }finally{
  if(renderer)await act(async()=>renderer.unmount());
  if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument;
  if(previousWindow===undefined)delete globalThis.window;else globalThis.window=previousWindow;
 }
});

test('Global and This browser use the same complete controls on one settings page',async()=>{
 const actions=[],dispatch=async action=>{actions.push(action);return action==='theme.list'?{accepted:true,result:{items:[],errors:[]}}:{accepted:true,result:{status:'stored'}}};
 const shared=await renderPreferencePage({appearance:preferenceSnapshot({activeScope:'shared'}),dispatch});
 let local;
 try{
  local=await renderPreferencePage({appearance:preferenceSnapshot({activeScope:'client'}),dispatch});
  const fields=renderer=>renderer.root.findAll(node=>node.props?.['data-appearance-preference']).map(node=>node.props['data-appearance-preference']).sort();
  assert.deepEqual(fields(shared),[...Object.keys(preferenceDefaults),'accent'].sort());
  assert.equal(fields(shared).length,14);
  assert.deepEqual(fields(local),fields(shared));
  for(const [renderer,selected] of [[shared,'Global'],[local,'This browser']]){
   const controls=renderer.root.findAllByProps({'data-action':'appearance.preferences.select'});
   assert.equal(controls.length,2);
   assert.equal(button(renderer.root,'Global').props['aria-pressed'],selected==='Global');
   assert.equal(button(renderer.root,'This browser').props['aria-pressed'],selected==='This browser');
   assert.equal(renderer.root.findAll(node=>node.props?.className==='a-appearance-local-indicator').length,0);
   assert.equal(renderer.root.findAllByType('button').some(node=>/share|clear|use shared setting/i.test(textContent(node))),false);
  }
  assert.equal(actions.some(action=>action.startsWith('appearance.preferences.')),false,'Settings entry does not select or write a profile.');
 }finally{await act(async()=>shared.unmount());if(local)await act(async()=>local.unmount())}
});

const neutralControl=renderer=>renderer.root.findByProps({'data-appearance-neutral':true});
const galleryCards=renderer=>renderer.root.findAll(node=>node.type==='label'&&node.props['data-appearance-card']);
const galleryDispatch=async action=>action==='theme.list'
 ?{accepted:true,result:{items:[entryA,entryB],errors:[]}}
 :{accepted:true,result:{status:'stored'}};
async function renderThumbnailPage(appearance,dispatch){
 let renderer;
 await act(async()=>{
  renderer=create(React.createElement(ThumbnailSettingsLayoutContext.Provider,{value:{active:true}},
   React.createElement(AppearanceSettingsWithThumbnail,{
    state:{client:{hostInstanceId:`thumbnail-regression-${appearance.activeScope}`}},appearance,act:dispatch,editor:{},
   })),{
   createNodeMock:element=>element.type==='div'&&element.props.className==='a-appearance-thumbnail'?{clientWidth:320}:null,
  });
  await new Promise(resolve=>setImmediate(resolve));
  await new Promise(resolve=>setImmediate(resolve));
 });
 return renderer;
}

for(const scope of ['shared','client']){
 test(`appearance gallery renders real thumbnails and one semantic selection for ${scope}`,async()=>{
  const previousResizeObserver=globalThis.ResizeObserver,previousIntersectionObserver=globalThis.IntersectionObserver;
  globalThis.ResizeObserver=class{constructor(callback){this.callback=callback}observe(){}disconnect(){}};
  globalThis.IntersectionObserver=class{constructor(callback){this.callback=callback}observe(target){this.callback([{isIntersecting:true,target}])}disconnect(){}};
  let renderer;
  try{
   clearThumbnailCatalogs();
   const appearance=preferenceSnapshot({activeScope:scope,shared:{appearanceId:entryA.id,scheme:'dark'},
    localValues:{appearanceId:entryB.id,scheme:'light'}});
   const dispatch=async(action,args)=>{
    if(action==='theme.list')return {accepted:true,result:{items:[entryA,entryB],errors:[]}};
    if(action==='theme.read')return {accepted:true,result:args.id===entryA.id?entryA:entryB};
    return {accepted:true};
   };
   renderer=await renderThumbnailPage(appearance,dispatch);
   const cards=galleryCards(renderer),selectedId=scope==='shared'?entryA.id:entryB.id;
   assert.deepEqual(cards.map(card=>card.props['data-appearance-card']),[entryA.id,entryB.id]);
   assert.deepEqual(cards.map(card=>card.props['data-selected']),[entryA.id===selectedId,entryB.id===selectedId]);
   assert.equal(cards.filter(card=>card.props['data-selected']).length,1,'Exactly one card receives the visible selected outline.');
   const radios=cards.map(card=>card.findByType('input'));
   assert.deepEqual(radios.map(radio=>radio.props.checked),[entryA.id===selectedId,entryB.id===selectedId]);
   assert.ok(radios.every((radio,index)=>radio.props.type==='radio'&&radio.props.name==='appearanceId'&&
    radio.props.className==='a-appearance-radio-input'&&radio.props['aria-label']===[entryA.name,entryB.name][index]));
   for(const [index,card] of cards.entries()){
    const caption=card.findByProps({className:'a-appearance-card-caption'});
    assert.equal(textContent(caption),[entryA.name,entryB.name][index],'Captions remain name-only.');
    assert.equal(caption.findAllByType('small').length,0);
    assert.equal(caption.findAllByType('svg').length,0,'No additional selected check or icon is visible.');
    assert.equal(card.findAllByType('button').length,0,'No selected pill is rendered.');
   }
   const mode=scope==='shared'?'dark':'light',thumbnails=renderer.root.findAllByProps({className:'a-appearance-thumbnail'});
   assert.equal(thumbnails.length,2);
   assert.ok(thumbnails.every(thumbnail=>thumbnail.props['data-thumbnail-state']==='ready'));
   assert.ok(thumbnails.every(thumbnail=>thumbnail.props['data-thumbnail-mode']===mode));
   assert.deepEqual(renderer.root.findAllByType('iframe').map(frame=>frame.props.title),[
    `Ocean: ${mode} sample`,`Forest: ${mode} sample`,
   ]);
   const neutral=neutralControl(renderer);
   assert.equal(neutral.props['aria-pressed'],false,'The neutral recovery control is not a second selection marker.');
   assert.equal(neutral.parent.props.role==='radiogroup',false);
  }finally{
   if(renderer)await act(async()=>renderer.unmount());
   clearThumbnailCatalogs();
   if(previousResizeObserver===undefined)delete globalThis.ResizeObserver;else globalThis.ResizeObserver=previousResizeObserver;
   if(previousIntersectionObserver===undefined)delete globalThis.IntersectionObserver;else globalThis.IntersectionObserver=previousIntersectionObserver;
  }
 });
}

for(const scope of ['shared','client']){
 test(`initial Reference selection is indicated outside the ${scope} gallery without a fake package`,async()=>{
  const changes=[],renderer=await renderPreferencePage({
   appearance:preferenceSnapshot({activeScope:scope}),dispatch:galleryDispatch,
   onPreferenceChange:async(...args)=>{changes.push(args);return {status:'stored'}},
  });
  try{
   const neutral=neutralControl(renderer);
   assert.equal(neutral.type,'button');
   assert.equal(neutral.props['aria-label'],'Restore neutral appearance');
   assert.equal(neutral.props['aria-pressed'],true);
   assert.equal(neutral.props.disabled,false);
   assert.equal(neutral.parent.props.role==='radiogroup',false,'Reference recovery stays outside the package gallery.');
   assert.deepEqual(galleryCards(renderer).map(node=>node.props['data-appearance-card']),[entryA.id,entryB.id]);
   assert.ok(galleryCards(renderer).every(node=>node.props['data-selected']===false));
   await act(async()=>neutral.props.onClick());
   assert.deepEqual(changes,[],'Restoring an already active Reference is a no-op.');
  }finally{await act(async()=>renderer.unmount())}
 });

 test(`neutral return edits only the captured ${scope} profile across a source switch`,async()=>{
  const held=deferred(),changes=[],otherScope=scope==='shared'?'client':'shared';
  const initial=preferenceSnapshot({activeScope:scope,shared:{appearanceId:entryA.id,scheme:'light'},localValues:{appearanceId:entryB.id,scheme:'dark'}});
  const options={appearance:initial,dispatch:galleryDispatch,onPreferenceChange:(...args)=>{changes.push(args);return held.promise}};
  const renderer=await renderPreferencePage(options);
  try{
   assert.equal(neutralControl(renderer).props['aria-pressed'],false);
   assert.equal(galleryCards(renderer).filter(node=>node.props['data-selected']).length,1);
   await act(async()=>neutralControl(renderer).props.onClick());
   assert.deepEqual(changes,[['appearanceId','reference',{
    namespace:'appearance-test',generation:'generation-1',expectedRevision:1,
    ...(scope==='client'?{expectedStorageVersion:'browser-version'}:{}),scope,retry:false,
   }]]);
   assert.equal(neutralControl(renderer).props['aria-pressed'],true);
   assert.equal(neutralControl(renderer).props.disabled,true);
   assert.ok(galleryCards(renderer).every(node=>node.props['data-selected']===false));
   assert.ok(fieldRow(renderer,'appearanceId').findAllByType('input').every(node=>node.props.disabled));
   assert.equal(renderer.root.findByProps({'aria-label':'Layout'}).props.disabled,false);
   await act(async()=>neutralControl(renderer).props.onClick());
   assert.equal(changes.length,1,'Pending neutral restoration cannot dispatch twice.');
   const switched={...initial,activeScope:otherScope,writeScope:otherScope};
   await act(async()=>renderer.update(React.createElement(PreferenceHost,{...options,appearance:switched})));
   assert.equal(neutralControl(renderer).props['aria-pressed'],false);
   assert.equal(neutralControl(renderer).props.disabled,false);
   const otherId=otherScope==='shared'?entryA.id:entryB.id;
   assert.equal(galleryCards(renderer).find(node=>node.props['data-selected']).props['data-appearance-card'],otherId);
   await act(async()=>{held.resolve({status:'stored',revision:2});await held.promise});
   assert.equal(neutralControl(renderer).props['aria-pressed'],false,'Late completion cannot change the newly displayed profile.');
   assert.equal(galleryCards(renderer).find(node=>node.props['data-selected']).props['data-appearance-card'],otherId);
   assert.deepEqual(initial.shared.values.appearanceId,entryA.id);
   assert.deepEqual(initial.local.values.appearanceId,entryB.id);
   await act(async()=>renderer.update(React.createElement(PreferenceHost,options)));
   assert.equal(neutralControl(renderer).props['aria-pressed'],true,'The captured profile retains its neutral return while awaiting state.');
   assert.equal(changes.length,1,'Restoration neither selects a profile nor edits the other one.');
  }finally{await act(async()=>renderer.unmount())}
 });
}

for(const failed of [false,true])test(`neutral recovery remains available while the library is ${failed?'unavailable':'loading'}`,async()=>{
 const renderer=await renderPreferencePage({dispatch:async()=>{if(failed)throw Error('Library unavailable');return {accepted:true,result:{items:[],errors:[]}}}});
 try{
  assert.equal(neutralControl(renderer).props.disabled,false);
  assert.equal(neutralControl(renderer).props['aria-pressed'],true);
  assert.equal(renderer.root.findAllByType('p').some(node=>textContent(node)==='Loading appearances…'),!failed);
  if(failed)assert.match(renderer.root.findByProps({role:'alert'}).props.children,/Library unavailable/);
 }finally{await act(async()=>renderer.unmount())}
});

test('first browser selection captures the shared CAS and later scope switches retain the local profile',async()=>{
 const selections=[];
 const initial=preferenceSnapshot({activeScope:'shared',localValues:null,localRevision:4,sharedRevision:9,storageVersion:'missing'});
 const renderer=await renderPreferencePage({appearance:initial,onPreferenceSelect:async(scope,gesture)=>{selections.push([scope,gesture]);return {status:'stored',undoId:'profile-undo'}}});
 try{
  await act(async()=>button(renderer.root,'This browser').props.onClick());
  assert.deepEqual(selections,[['client',{namespace:'appearance-test',generation:'generation-1',expectedRevision:4,expectedStorageVersion:'missing',expectedSharedRevision:9,retry:false}]]);
  assert.equal(button(renderer.root,'Global').props['aria-pressed'],true,'The source changes only after the storage acknowledgement updates state.');
  await act(async()=>renderer.update(React.createElement(PreferenceHost,{appearance:preferenceSnapshot({activeScope:'client',localRevision:5,storageVersion:'browser-v2',rendered:{scheme:'dark'}}),onPreferenceSelect:async(scope,gesture)=>{selections.push([scope,gesture]);return {status:'stored'}}})));
  await act(async()=>button(renderer.root,'Global').props.onClick());
  assert.deepEqual(selections[1],['shared',{namespace:'appearance-test',generation:'generation-1',expectedRevision:5,expectedStorageVersion:'browser-v2',retry:false}]);
 }finally{await act(async()=>renderer.unmount())}
});

test('execution visibility stays sparse (including false), empty metrics stay empty',async()=>{
 assert.deepEqual(sparseExecutionVisibilityPatch({step:false},{step:true,turn:true,conversation:true}),{step:false});
 assert.deepEqual(sparseExecutionVisibilityPatch({step:true},{step:true}),{});
 assert.deepEqual(sparseExecutionVisibilityPatch({},{}),{});
 const changes=[],appearance=preferenceSnapshot({rendered:{executionMetrics:['calls']}});
 const renderer=await renderPreferencePage({appearance,onPreferenceChange:async(...args)=>{changes.push(args);return {accepted:true,result:{status:'stored'}}}});
 try{
  const visibility=fieldRow(renderer,'executionVisibility').findAllByType('input')[0];
  await act(async()=>visibility.props.onChange({target:{checked:false}}));
  const metrics=fieldRow(renderer,'executionMetrics').findAllByType('input').at(-1);
  await act(async()=>metrics.props.onChange({target:{checked:false}}));
  const captured={namespace:'appearance-test',generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',scope:'client',retry:false};
  assert.deepEqual(changes[0],['executionVisibility',{step:false},captured]);
  assert.deepEqual(changes[1],['executionMetrics',[],captured]);
 }finally{await act(async()=>renderer.unmount())}
});

test('pending fields reject duplicate edits without locking unrelated settings',async()=>{
 const held=deferred(),changes=[];
 const renderer=await renderPreferencePage({onPreferenceChange:(field,value,options)=>{
  changes.push([field,value,options]);return field==='scheme'?held.promise:{accepted:true,result:{status:'stored'}};
 }});
 try{
  const dark=renderer.root.findByProps({name:'appearance-scheme',value:'dark'});
  await act(async()=>dark.props.onChange());
  const pending=renderer.root.findByProps({name:'appearance-scheme',value:'dark'});
  assert.equal(pending.props.disabled,true);
  await act(async()=>pending.props.onChange());
  await act(async()=>renderer.root.findByProps({'aria-label':'Layout'}).props.onChange({target:{value:'conversation'}}));
  assert.deepEqual(changes.map(([field])=>field),['scheme','layout']);
  assert.deepEqual(changes[1],['layout','conversation',{namespace:'appearance-test',generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',scope:'client',retry:false}]);
  await act(async()=>{held.resolve({accepted:true,result:{status:'stored'}});await held.promise});
 }finally{await act(async()=>renderer.unmount())}
});

test('a failed local edit retries the captured field and profile without share or clear controls',async()=>{
 const calls=[],appearance=preferenceSnapshot({rendered:{scheme:'dark'},shared:{scheme:'system'}});
 const renderer=await renderPreferencePage({appearance,onPreferenceChange:async(...args)=>{
  calls.push(args);if(calls.length===1)throw Error('The browser did not confirm this change.');
  return {status:'stored'};
 }});
 try{
  await act(async()=>{renderer.root.findByProps({name:'appearance-scheme',value:'light'}).props.onChange();await new Promise(resolve=>setImmediate(resolve))});
  assert.equal(renderer.root.findByProps({name:'appearance-scheme',value:'light'}).props.checked,true);
  assert.equal(renderer.root.findAllByType('button').some(node=>/share|clear|use shared setting/i.test(textContent(node))),false);
  const retry=button(renderer.root,'Retry');assert.ok(retry);
  await act(async()=>{retry.props.onClick({currentTarget:{getAttribute:()=>null}});await new Promise(resolve=>setImmediate(resolve))});
  assert.equal(calls.length,2);
  assert.deepEqual(calls.map(([field,value])=>[field,value]),[['scheme','light'],['scheme','light']]);
  assert.deepEqual(calls.map(([, ,options])=>options.scope),['client','client']);
  assert.equal(calls[0][2].retry,false);assert.equal(calls[1][2].retry,true);
  assert.equal(renderer.root.findByProps({name:'appearance-scheme',value:'light'}).props.checked,true);
 }finally{await act(async()=>renderer.unmount())}
});

test('Undo focuses its anchored row only while focus remains on the Undo control',async()=>{
 const previousDocument=globalThis.document,listeners=new Map();let focused=0,renderer;
 globalThis.document={activeElement:null,getElementById:()=>null,addEventListener:(name,listener)=>listeners.set(name,listener),removeEventListener:(name,listener)=>listeners.delete(name)};
 try{
  renderer=await renderPreferencePage({onPreferenceChange:async()=>({accepted:true,result:{status:'stored',undoId:'scheme-undo'}}),onPreferenceUndo:async()=>({accepted:true,result:{status:'stored',effective:{scheme:'dark'}}})});
  await act(async()=>renderer.root.findByProps({name:'appearance-scheme',value:'light'}).props.onChange());
  const status={focus:()=>focused++},row={querySelector:()=>status},trigger={closest:()=>row};
  globalThis.document.activeElement=trigger;
  await act(async()=>{button(renderer.root,'Undo Color mode change').props.onClick({currentTarget:trigger});await new Promise(resolve=>setImmediate(resolve))});
  assert.equal(focused,1);
 }finally{
  if(renderer)await act(async()=>renderer.unmount());
  if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument;
 }
});

const flushPreference=()=>new Promise(resolve=>setImmediate(resolve));
const setPreferenceAppearance=async(renderer,options,appearance)=>act(async()=>renderer.update(React.createElement(PreferenceHost,{...options,appearance})));

test('held Undo remains focusable and ignores duplicate activations',async()=>{
 const heldUndo=deferred(),undoIds=[];
 const renderer=await renderPreferencePage({
  onPreferenceChange:async()=>({accepted:true,result:{status:'stored',undoId:'scheme-undo'}}),
  onPreferenceUndo:undoId=>{undoIds.push(undoId);return heldUndo.promise},
 });
 try{
  await act(async()=>renderer.root.findByProps({name:'appearance-scheme',value:'light'}).props.onChange());
  const undo=button(renderer.root,'Undo Color mode change'),trigger={closest:()=>null};
  await act(async()=>{
   undo.props.onClick({currentTarget:trigger});
   undo.props.onClick({currentTarget:trigger});
  });
  const pending=button(renderer.root,'Undo Color mode change');
  assert.equal(pending.props.disabled,undefined,'Pending Undo remains a native focusable button.');
  assert.equal(pending.props['aria-disabled'],true);
  assert.equal(pending.props['aria-busy'],true);
  assert.equal(pending.props['data-operation-pending'],true);
  assert.equal(pending.props['data-operation-aria-disabled'],true);
  assert.deepEqual(undoIds,['scheme-undo'],'Immediate repeated click/keyboard activations start only one Undo.');
  await act(async()=>pending.props.onClick({currentTarget:trigger}));
  assert.deepEqual(undoIds,['scheme-undo'],'A later activation while pending is ignored.');
  assert.equal(renderer.root.findByProps({'aria-label':'Layout'}).props.disabled,false,'Unrelated preferences remain usable.');
  await act(async()=>{heldUndo.resolve({accepted:true,result:{status:'stored',effective:{scheme:'dark'}}});await heldUndo.promise;await flushPreference()});
  assert.equal(button(renderer.root,'Undo Color mode change'),undefined,'Confirmed Undo removes its trigger.');
 }finally{await act(async()=>renderer.unmount())}
});

test('a queued edit stays bound to its captured profile after switching to Global',async()=>{
 const held=deferred(),changes=[];
 const options={appearance:preferenceSnapshot({activeScope:'client',rendered:{scheme:'light'},shared:{scheme:'system'}}),
  onPreferenceChange:(...args)=>{changes.push(args);return changes.length===1?held.promise:{status:'stored',revision:2}}};
 const renderer=await renderPreferencePage(options);
 try{
  await act(async()=>renderer.root.findByProps({name:'appearance-scheme',value:'dark'}).props.onChange());
  await setPreferenceAppearance(renderer,options,preferenceSnapshot({activeScope:'shared',rendered:{scheme:'light'},shared:{scheme:'system'}}));
  await act(async()=>renderer.root.findByProps({name:'appearance-scheme',value:'dark'}).props.onChange());
  assert.deepEqual(changes.map(([, ,target])=>target.scope),['client','shared']);
  assert.deepEqual(changes[0][2],{namespace:'appearance-test',generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',scope:'client',retry:false});
  assert.equal(button(renderer.root,'Global').props['aria-pressed'],true);
  await act(async()=>{held.resolve({status:'stored',revision:2});await flushPreference()});
 }finally{await act(async()=>renderer.unmount())}
});

test('reselecting a pending local value before its state arrives does not dispatch twice',async()=>{
 const held=deferred(),changes=[],options={appearance:preferenceSnapshot({activeScope:'client',rendered:{scheme:'light'}}),
  onPreferenceChange:(...args)=>{changes.push(args);return held.promise}};
 const renderer=await renderPreferencePage(options);
 try{
  const dark=renderer.root.findByProps({name:'appearance-scheme',value:'dark'});
  await act(async()=>dark.props.onChange());
  await act(async()=>renderer.root.findByProps({name:'appearance-scheme',value:'dark'}).props.onChange());
  assert.equal(changes.length,1);
  assert.deepEqual(changes[0][2],{namespace:'appearance-test',generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',scope:'client',retry:false});
  assert.equal(renderer.root.findByProps({name:'appearance-scheme',value:'dark'}).props.checked,true);
  await act(async()=>{held.resolve({status:'stored',revision:2,undoId:'scheme-confirmed'});await flushPreference()});
  assert.ok(button(renderer.root,'Undo Color mode change'));
  await setPreferenceAppearance(renderer,options,preferenceSnapshot({activeScope:'client',localRevision:2,storageVersion:'browser-v2',rendered:{scheme:'dark'}}));
  assert.equal(renderer.root.findByProps({name:'appearance-scheme',value:'dark'}).props.checked,true);
 }finally{await act(async()=>renderer.unmount())}
});

for(const firstOutcome of ['failed','stored-before-state'])test(`a fresh visibility reversal after ${firstOutcome} sends sparse true from the currently displayed baseline`,async()=>{
 const held=deferred(),changes=[];
 const initial=preferenceSnapshot({rendered:{executionVisibility:{step:true,turn:false}}});
 const options={appearance:initial,onPreferenceChange:(...args)=>{
  changes.push(args);
  if(changes.length>1)return held.promise;
  return firstOutcome==='failed'?Promise.reject(Error('First visibility change failed.')):Promise.resolve({status:'stored',revision:2});
 }};
 const renderer=await renderPreferencePage(options),controls=()=>fieldRow(renderer,'executionVisibility').findAllByType('input');
 try{
  await act(async()=>{controls()[0].props.onChange({target:{checked:false}});await flushPreference()});
  assert.deepEqual(controls().map(node=>node.props.checked),[false,false,true]);
  assert.equal(controls()[0].props.disabled,false);
  await act(async()=>controls()[0].props.onChange({target:{checked:true}}));
  assert.deepEqual(changes.map(([field,value])=>[field,value]),[
   ['executionVisibility',{step:false}],['executionVisibility',{step:true}],
  ],'Fresh true must not disappear because the authoritative frame still says true.');
  assert.deepEqual(controls().map(node=>node.props.checked),[true,false,true]);
  assert.ok(controls().every(node=>node.props.disabled));
  await act(async()=>{held.resolve({status:'stored',revision:3});await flushPreference()});
  await setPreferenceAppearance(renderer,options,preferenceSnapshot({localRevision:2,rendered:{executionVisibility:{step:false,turn:false}}}));
  assert.equal(controls()[0].props.checked,true,'A late earlier frame cannot replace the reversal.');
  await setPreferenceAppearance(renderer,options,preferenceSnapshot({localRevision:3,rendered:{executionVisibility:{step:true,turn:false}}}));
  assert.deepEqual(controls().map(node=>node.props.checked),[true,false,true]);
  assert.equal(changes.length,2);
 }finally{await act(async()=>renderer.unmount())}
});

test('Undo completion does not steal focus after the user moves elsewhere on the page',async()=>{
 const previousDocument=globalThis.document,listeners=new Map(),heldUndo=deferred();let focused=0,renderer;
 globalThis.document={activeElement:null,getElementById:()=>null,addEventListener:(name,listener)=>listeners.set(name,listener),removeEventListener:(name,listener)=>listeners.delete(name)};
 try{
  renderer=await renderPreferencePage({onPreferenceChange:async()=>({status:'stored',undoId:'scheme-undo'}),onPreferenceUndo:()=>heldUndo.promise});
  await act(async()=>renderer.root.findByProps({name:'appearance-scheme',value:'light'}).props.onChange());
  const trigger={closest:()=>({querySelector:()=>({focus:()=>focused++})})};globalThis.document.activeElement=trigger;
  await act(async()=>{button(renderer.root,'Undo Color mode change').props.onClick({currentTarget:trigger});});
  globalThis.document.activeElement={};listeners.get('focusin')();
  await act(async()=>{heldUndo.resolve({status:'stored',effective:{scheme:'dark'}});await flushPreference()});
  assert.equal(focused,0);
 }finally{
  if(renderer)await act(async()=>renderer.unmount());
  if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument;
 }
});

test('late Undo after leaving the page cannot focus or overwrite the successor',async()=>{
 const previousDocument=globalThis.document,listeners=new Map(),heldUndo=deferred();let focused=0,oldPage,nextPage;
 globalThis.document={activeElement:null,getElementById:()=>null,addEventListener:(name,listener)=>listeners.set(name,listener),removeEventListener:(name,listener)=>listeners.delete(name)};
 try{
  oldPage=await renderPreferencePage({onPreferenceChange:async()=>({accepted:true,result:{status:'stored',undoId:'scheme-undo'}}),onPreferenceUndo:()=>heldUndo.promise});
  await act(async()=>oldPage.root.findByProps({name:'appearance-scheme',value:'light'}).props.onChange());
  const status={focus:()=>focused++},row={querySelector:()=>status},trigger={closest:()=>row};
  globalThis.document.activeElement=trigger;
  const undo=button(oldPage.root,'Undo Color mode change');
  await act(async()=>{undo.props.onClick({currentTarget:trigger});});
  const pendingUndo=button(oldPage.root,'Undo Color mode change');
  assert.equal(pendingUndo.props.disabled,undefined,'A pending Undo remains a focusable native button.');
  assert.equal(pendingUndo.props['aria-disabled'],true,'Pending state is announced without removing focusability.');
  assert.equal(pendingUndo.props['aria-busy'],true);
  await act(async()=>oldPage.unmount());
  nextPage=await renderPreferencePage({appearance:preferenceSnapshot({rendered:{scheme:'system'}})});
  await act(async()=>{heldUndo.resolve({accepted:true,result:{status:'stored',effective:{scheme:'dark'}}});await heldUndo.promise;await new Promise(resolve=>setImmediate(resolve))});
  assert.equal(focused,0);
  assert.equal(nextPage.root.findByProps({name:'appearance-scheme',value:'system'}).props.checked,true);
 }finally{
  if(oldPage)await act(async()=>oldPage.unmount());if(nextPage)await act(async()=>nextPage.unmount());
  if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument;
 }
});