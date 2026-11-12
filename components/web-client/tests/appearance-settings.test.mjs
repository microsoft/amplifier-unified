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
const componentEntry=`export {AppearanceSettings,MotionStatus} from './appearance-settings.jsx';export {useAppearanceEditor,appearanceEditorStorageKey} from './appearance-editor.jsx';export {SettingsLayoutContext} from './settings-layout';export {clearAppearanceCatalogs} from './appearance-catalog';`;
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
const {AppearanceSettings,MotionStatus,useAppearanceEditor,appearanceEditorStorageKey,SettingsLayoutContext,clearAppearanceCatalogs}=module.exports;
const thumbnailStubs={...stubs,'./appearance-thumbnail-scene':`export const renderAppearanceThumbnail=(css,mode)=>'<main data-thumbnail-mode="'+mode+'">'+css+'</main>';`};
delete thumbnailStubs['./appearance-thumbnail'];
const thumbnailCompiled=await compileSettings(thumbnailStubs),thumbnailModule={exports:{}};
new Function('require','module','exports',thumbnailCompiled.outputFiles[0].text)(require,thumbnailModule,thumbnailModule.exports);
const {
 AppearanceSettings:AppearanceSettingsWithThumbnail,
 SettingsLayoutContext:ThumbnailSettingsLayoutContext,
 clearAppearanceCatalogs:clearThumbnailCatalogs,
}=thumbnailModule.exports;
const namespace='amplifier.appearance.v2:'+'a'.repeat(32);
const replacementNamespace='amplifier.appearance.v2:'+'b'.repeat(32);
const testEditorKey=appearanceEditorStorageKey(namespace);
test.beforeEach(()=>clearAppearanceCatalogs());
function memoryStorage(entries=[]){
 const values=new Map(entries),reads=[],writes=[],deletes=[];
 return {
  getItem(key){reads.push(key);return values.get(key)||null},
  setItem(key,value){writes.push([key,value]);values.set(key,value)},
  removeItem(key){deletes.push(key);values.delete(key)},
  entries:()=>[...values],reads,writes,deletes,
 };
}
function EditorHost({storage,open=true,active=true,...props}){
 const editor=useAppearanceEditor(props.state.appearance?.theme||props.state.theme,props.referenceCss??'',{storage,namespace:props.state.appearance?.namespace||namespace});
 return open?React.createElement(SettingsLayoutContext.Provider,{value:{active}},React.createElement(AppearanceSettings,{...props,editor})):null;
}
const textContent=node=>typeof node==='string'?node:(node.children||[]).map(textContent).join('');
const button=(root,label)=>root.findAllByType('button').find(node=>node.props['aria-label']===label||textContent(node)===label);
const input=(root,id)=>root.findByProps({id});

function EditorProbe({capture,...props}){
 const editor=useAppearanceEditor(props.theme,'',{storage:props.storage,namespace:props.namespace});
 capture(editor);return null;
}
const privateDraft={
 entry:{id:'saved:private.css',source:'saved',fingerprint:'c'.repeat(64),name:'Private target'},
 name:'Unsent private name',description:'Private description',css:'#amp-one{color:purple}',dirty:true,
};

test('ownerless and invalid-owner private editors never restore unattached/legacy data or write a null storage key',async()=>{
 for(const owner of [undefined,null,'','unattached','appearance-test',
  'amplifier.appearance-experiment-0801:'+'a'.repeat(32),
  'amplifier.appearance.v2:'+'A'.repeat(32)]){
  assert.equal(appearanceEditorStorageKey(owner),null);
  const raw=JSON.stringify({version:3,draft:privateDraft});
  const entries=[
   ['amplifier.appearance-editor.v3:unattached',raw],['amplifier.appearance-editor.v2',raw],
   ['amplifier.appearance-editor-experiment-0801.v2',raw],['null',raw],[testEditorKey,raw],
  ];
  const saved=memoryStorage(entries);let storageCalls=0,renderer,editor;
  const storage=()=>{storageCalls++;return saved};
  try{
   await act(async()=>{renderer=create(React.createElement(EditorProbe,{
    namespace:owner,storage,theme:{name:'Current production',css:'current CSS'},capture:value=>{editor=value},
   }))});
   assert.equal(editor.draft.name,'Current production');assert.equal(editor.draft.dirty,false);
   assert.equal(storageCalls,0);assert.deepEqual(saved.reads,[]);
   await act(async()=>editor.setDraft({...editor.draft,name:'Private ownerless change',dirty:true}));
   assert.equal(editor.draft.name,'Private ownerless change','Explicit text edits remain usable in this tab.');
   assert.match(editor.storageError,/draft stays in this tab.*could not be saved for reload.*Keep this tab open/);
   assert.equal(storageCalls,0);assert.deepEqual(saved.reads,[]);assert.deepEqual(saved.writes,[]);
   assert.deepEqual(saved.deletes,[]);assert.deepEqual(saved.entries(),entries);
  }finally{if(renderer)await act(async()=>renderer.unmount())}
 }
});

test('private production editor restores only version-3 drafts from its exact namespaced key without rewriting on reload',async()=>{
 const key=`amplifier.appearance-editor.v3:${namespace}`;
 assert.equal(testEditorKey,key);
 const raw=JSON.stringify({version:3,draft:privateDraft});
 const saved=memoryStorage([[key,raw]]),storage=()=>saved;let renderer,editor;
 const props={namespace,storage,capture:value=>{editor=value},theme:{name:'Host appearance',css:'host CSS'}};
 try{
  await act(async()=>{renderer=create(React.createElement(EditorProbe,props))});
  assert.deepEqual(editor.draft,privateDraft);
  assert.deepEqual(saved.reads,[key]);assert.deepEqual(saved.writes,[]);assert.deepEqual(saved.deletes,[]);
  await act(async()=>renderer.update(React.createElement(EditorProbe,{...props,theme:{name:'Restarted host',css:'new host CSS'}})));
  assert.deepEqual(editor.draft,privateDraft,'A host-generation/theme refresh cannot replace the private dirty draft.');
  await act(async()=>renderer.unmount());renderer=null;
  await act(async()=>{renderer=create(React.createElement(EditorProbe,props))});
  assert.deepEqual(editor.draft,privateDraft);
  assert.deepEqual(saved.entries(),[[key,raw]]);assert.deepEqual(saved.writes,[]);
  assert.deepEqual(saved.deletes,[]);
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});

test('private production editor never reads, migrates, rewrites or deletes retained legacy and 0.80.1 drafts',async()=>{
 const keys=[
  'amplifier.appearance-editor.v1','amplifier.appearance-editor.v2',
  'amplifier.appearance-editor-experiment-0801.v2',
  `amplifier.appearance-editor-experiment-0801.v2:${namespace}`,
  `amplifier.appearance-editor.v2:${namespace}`,
 ];
 const entries=keys.map(key=>[key,JSON.stringify({version:2,draft:privateDraft})]);
 const saved=memoryStorage(entries),storage=()=>saved;let renderer,editor;
 try{
  await act(async()=>{renderer=create(React.createElement(EditorProbe,{
   namespace,storage,theme:{name:'Production',css:'production CSS'},capture:value=>{editor=value},
  }))});
  assert.equal(editor.draft.name,'Production');assert.equal(editor.draft.css,'production CSS');
  assert.equal(editor.draft.dirty,false);assert.equal(editor.draft.entry,null);
  assert.deepEqual(saved.reads,[testEditorKey]);
  assert.deepEqual(saved.entries(),entries);assert.deepEqual(saved.writes,[]);assert.deepEqual(saved.deletes,[]);
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});

test('private production editor rejects old or malformed records at the new key without overwriting retained bytes',async()=>{
 for(const record of [
  {version:1,draft:privateDraft},{version:2,draft:privateDraft},{version:'3',draft:privateDraft},
  {version:4,draft:privateDraft},{version:3,draft:{...privateDraft,dirty:'true'}},
  {version:3,draft:{...privateDraft,entry:{...privateDraft.entry,fingerprint:'invalid'}}},
  null,[],
 ]){
  const raw=JSON.stringify(record),saved=memoryStorage([[testEditorKey,raw]]),storage=()=>saved;let renderer,editor;
  try{
   await act(async()=>{renderer=create(React.createElement(EditorProbe,{
    namespace,storage,theme:{name:'Validated production',css:'production CSS'},capture:value=>{editor=value},
   }))});
   assert.equal(editor.draft.name,'Validated production');assert.equal(editor.draft.dirty,false);
   assert.deepEqual(saved.entries(),[[testEditorKey,raw]]);assert.deepEqual(saved.writes,[]);assert.deepEqual(saved.deletes,[]);
  }finally{if(renderer)await act(async()=>renderer.unmount())}
 }
});

test('private editor namespace replacement invalidates old draft tokens while retaining both namespaces for explicit recovery',async()=>{
 const nextKey=appearanceEditorStorageKey(replacementNamespace);
 const saved=memoryStorage([[testEditorKey,JSON.stringify({version:3,draft:privateDraft})]]),storage=()=>saved;
 let renderer,editor;
 const props={namespace,storage,theme:{name:'Production',css:'production CSS'},capture:value=>{editor=value}};
 try{
  await act(async()=>{renderer=create(React.createElement(EditorProbe,props))});
  const old=editor,oldDraft=editor.draft;
  await act(async()=>renderer.update(React.createElement(EditorProbe,{...props,namespace:replacementNamespace})));
  assert.equal(editor.draft.name,'Production');assert.equal(editor.draft.dirty,false);
  let changed;
  await act(async()=>{changed=old.compareAndSetDraft(oldDraft,{...oldDraft,name:'Late old operation'})});
  assert.equal(changed,false);assert.deepEqual(saved.writes,[]);
  await act(async()=>editor.setDraft({...editor.draft,name:'New host private draft',dirty:true}));
  assert.deepEqual(saved.writes.map(([key])=>key),[nextKey]);
  assert.equal(JSON.parse(saved.entries().find(([key])=>key===nextKey)[1]).version,3);
  await act(async()=>renderer.update(React.createElement(EditorProbe,props)));
  assert.deepEqual(editor.draft,privateDraft,'Returning to the original namespace restores that namespace only.');
  assert.equal(saved.entries().length,2);assert.deepEqual(saved.deletes,[]);
  assert.equal(saved.reads.every(key=>[testEditorKey,nextKey].includes(key)),true);
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});

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
  assert.equal(privateDraft.version,3);
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

test('saved library Export ZIP dispatches only the selected item id under the strict host schema',async()=>{
 const host=await heldEditor('save');
 try{
  const row=host.root.findAllByProps({className:'a-appearance-saved-row'})
   .find(node=>textContent(node.findByType('strong'))===entryB.name);
  assert.ok(row,'Exercise the real Forest library row, not the Ocean editor export control.');
  const exportButton=button(row,'Export ZIP');
  assert.equal(exportButton.props['data-action'],'theme.export');
  await act(async()=>{exportButton.props.onClick();await new Promise(resolve=>setImmediate(resolve))});
  assert.deepEqual(host.calls.filter(call=>call.action==='theme.export'),[
   {action:'theme.export',args:{id:entryB.id}},
  ],'The host admits id only; format and other extra properties must not be sent.');
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
 return {namespace,generation:'generation-1',availability:'connected',activeScope,writeScope:activeScope,
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
  await act(async()=>button(renderer.root,'Use appearance accent').props.onClick());
  assert.deepEqual(changes,[],'Resetting an absent override cannot write the computed light-dark color.');
  assert.equal(renderer.root.findByProps({'aria-label':'Accent color'}).props.value,'#5140ef');
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
    namespace,generation:'generation-1',expectedRevision:1,
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

const libraryNotice=renderer=>textContent(renderer.root.findByProps({className:'a-appearance-notice'}));
const unavailableAlerts=renderer=>renderer.root.findAllByProps({role:'alert'})
 .filter(node=>/selected appearance.*unavailable/i.test(textContent(node)));
const assertNoFalseLibraryAssurance=text=>assert.doesNotMatch(text,
 /active appearance (?:is|remains|stays) unchanged|saving never changes the active appearance/i);

for(const scope of ['shared','client'])for(const selected of [true,false])for(const operation of ['update','delete']){
 test(`${scope} ${operation} of a ${selected?'selected':'nonselected'} library entry reports profile consequences without a preference write`,async()=>{
  const selectedId=selected?entryA.id:entryB.id;
  const appearance=preferenceSnapshot({
   activeScope:scope,
   shared:{appearanceId:scope==='shared'?selectedId:entryA.id,scheme:'dark',density:'compact',accent:'#112233'},
   localValues:{appearanceId:scope==='client'?selectedId:entryA.id,scheme:'light',density:'comfortable',layout:'work',accent:'#445566'},
  });
  appearance.theme={...(selected?entryA:entryB),available:true};
  const state={client:{hostInstanceId:`library-${scope}-${selected}-${operation}`},appearance};
  const before=structuredClone(state),shared=appearance.shared,local=appearance.local;
  const sharedValues=shared.values,localValues=local.values,theme=appearance.theme,effective=appearance.effective;
  const saved=memoryStorage(),calls=[],changes=[],selections=[],items=new Map([entryA,entryB].map(item=>[item.id,item]));
  const fields={'theme-name':'Updated Ocean','theme-description':'Updated library description','theme-css':'#amp-one{color:purple}'};
  const dispatch=async(action,args)=>{
   calls.push({action,args});
   if(action==='theme.list')return {accepted:true,result:{items:[...items.values()],errors:[]}};
   if(action==='theme.read')return {accepted:true,result:items.get(args.id)};
   if(action==='theme.update'){
    const result={...entryA,name:args.name,description:args.description,css:args.css,fingerprint:'c'.repeat(64)};
    items.set(entryA.id,result);return {accepted:true,result};
   }
   if(action==='theme.delete'){items.delete(args.id);return {accepted:true}}
   return {accepted:true};
  };
  const props={state,act:dispatch,storage:()=>saved,referenceCss:'',
   onPreferenceChange:(...args)=>{changes.push(args);return false},
   onPreferenceSelect:(...args)=>{selections.push(args);return false},
  };
  let renderer;
  try{
   await act(async()=>{renderer=create(React.createElement(EditorHost,props))});
   const initialSelection=galleryCards(renderer).find(node=>node.props['data-selected']);
   assert.equal(initialSelection.props['data-appearance-card'],selectedId);
   if(operation==='update'){
    await act(async()=>{button(renderer.root,'Edit Ocean').props.onClick();await new Promise(resolve=>setImmediate(resolve))});
    await setFields(renderer.root,fields);
   }else{
    await act(async()=>button(renderer.root,'Delete Ocean').props.onClick());
    const confirmation=textContent(renderer.root.findByProps({'aria-label':'Delete appearance confirmation'}));
    assert.match(confirmation,/Ocean/);
    assert.match(confirmation,/profiles selecting this entry.*Reference.*(?:another|different) appearance/i);
    assertNoFalseLibraryAssurance(confirmation);
   }
   await act(async()=>{actionButton(renderer.root,'theme.'+operation).props.onClick();await new Promise(resolve=>setImmediate(resolve))});
   assert.deepEqual(calls.filter(call=>call.action==='theme.'+operation),[{
    action:'theme.'+operation,args:{id:entryA.id,expectedFingerprint:entryA.fingerprint,...(operation==='update'?{
     name:fields['theme-name'],description:fields['theme-description'],css:fields['theme-css'],
    }:{})},
   }],'A library mutation uses the exact read/list fingerprint, never a profile or whole-preference payload.');
   const notice=libraryNotice(renderer);
   if(operation==='update'){
    assert.match(notice,/Library entry updated/);
    assert.match(notice,/profiles selecting this entry.*updated appearance/i);
    assert.match(notice,/other preferences.*unchanged/i);
    assert.equal(items.get(entryA.id).fingerprint,'c'.repeat(64));
   }else{
    assert.match(notice,/Deleted from your library/);
    assert.match(notice,/profiles selecting this entry.*Reference.*(?:another|different) appearance/i);
    assert.equal(items.has(entryA.id),false);
   }
   assertNoFalseLibraryAssurance(textContent(renderer.root));
   assert.deepEqual(changes,[]);assert.deepEqual(selections,[],'Library operations never select a profile.');
   assert.equal(calls.some(call=>call.action.startsWith('appearance.preferences.')),false,
    'No appearance.preferences.update, selection or reset is dispatched as a side effect.');
   assert.deepEqual(state,before,'Library receipts cannot mutate the selected id, revisions, scheme, density or any other preference.');
   const renderedState=renderer.root.findByType(AppearanceSettings).props.state;
   assert.equal(renderedState,state);assert.equal(renderedState.appearance,appearance);
   assert.equal(appearance.shared,shared);assert.equal(appearance.local,local);
   assert.equal(shared.values,sharedValues);assert.equal(local.values,localValues);
   assert.equal(appearance.theme,theme);assert.equal(appearance.effective,effective);
   assert.equal(neutralControl(renderer).props['aria-pressed'],false,'Deletion does not automatically select Reference.');
  }finally{if(renderer)await act(async()=>renderer.unmount())}
 });
}

test('Advanced customization distinguishes saving a copy from updating profiles that select a library entry',async()=>{
 const renderer=await renderPreferencePage({dispatch:galleryDispatch});
 try{
  const help=renderer.root.findByProps({className:'a-appearance-advanced'}).findAllByType('p').map(textContent).join(' ');
  assert.match(help,/save as (?:a )?copy.*(?:unselected|(?:does not|doesn.t|never).*(?:select|active appearance))/i);
  assert.match(help,/updat(?:e|es|ing).*entry.*profiles.*select/i);
  assertNoFalseLibraryAssurance(help);
 }finally{await act(async()=>renderer.unmount())}
});
for(const activeScope of ['shared','client']){
 test(`${activeScope} primary appearance controls stay simple; Advanced disclosure neither writes nor discards fields`,async()=>{
  const calls=[],renderer=await renderPreferencePage({appearance:preferenceSnapshot({activeScope}),dispatch:async(action,args)=>{calls.push({action,args});return galleryDispatch(action,args)}});
  try{
   const advanced=renderer.root.findByProps({className:'a-appearance-more'});
   assert.equal(advanced.type,'details');assert.equal(advanced.props.open,undefined);
   const advancedRows=advanced.findAll(node=>typeof node.props['data-appearance-preference']==='string').map(node=>node.props['data-appearance-preference']);
   assert.deepEqual(new Set(advancedRows),new Set(['layout','decorations','interfaceDetail','conversationStatus','collapseInterimReplies','workStartView','planPlacement','executionDetail','executionVisibility','executionMetrics']));
   assert.equal(advanced.findAllByProps({className:'a-appearance-saved'}).length,1);
   assert.equal(advanced.findAllByProps({className:'a-appearance-import'}).length,1);
   const outside=renderer.root.findAll(node=>typeof node.props['data-appearance-preference']==='string'&&!advancedRows.includes(node.props['data-appearance-preference'])).map(node=>node.props['data-appearance-preference']);
   assert.deepEqual(outside,['appearanceId','scheme','density','accent']);
   assert.equal(renderer.root.findAllByProps({'data-appearance-motion':true}).length,1);
   assert.equal(advanced.findAllByProps({'data-appearance-motion':true}).length,0);
   assert.equal(advanced.props.onToggle,undefined,'Native disclosure has no write handler');
   assert.equal(calls.some(call=>call.action.startsWith('appearance.preferences.')),false);
  }finally{await act(async()=>renderer.unmount())}
 });
}
test('Advanced fold/reopen retains the mounted dirty editor and performs no preference or draft writes',async()=>{
 const saved=memoryStorage(),calls=[],changes=[],selections=[],disclosures=new Map();
 const props={state:{appearance:preferenceSnapshot({activeScope:'shared'})},storage:()=>saved,
  act:async(action,args)=>{calls.push({action,args});return galleryDispatch(action,args)},
  onPreferenceChange:(...args)=>changes.push(args),onPreferenceSelect:(...args)=>selections.push(args)};
 let renderer;
 try{
  await act(async()=>{renderer=create(React.createElement(EditorHost,props),{
   createNodeMock:element=>{
    if(element.type!=='details')return null;
    const node={open:false};disclosures.set(element.props.className,node);return node;
   },
  })});
  const advanced=disclosures.get('a-appearance-more'),customization=disclosures.get('a-appearance-advanced');
  assert.ok(advanced&&customization);
  advanced.open=true;customization.open=true;
  await act(async()=>input(renderer.root,'theme-css').props.onChange({target:{value:'#amp-one{color:purple}'}}));
  await act(async()=>input(renderer.root,'theme-name').props.onChange({target:{value:'Private unsaved look'}}));
  const mounted=renderer.root.findByType(AppearanceSettings),css=input(renderer.root,'theme-css'),name=input(renderer.root,'theme-name');
  const draft=mounted.props.editor.draft,writes=structuredClone(saved.writes),entries=saved.entries();
  assert.equal(draft.dirty,true);
  assert.match(textContent(renderer.root.findByProps({className:'a-appearance-disclosure-status'})),/Unsaved editor changes/);
  const beforeCalls=calls.length;
  advanced.open=false;
  await act(async()=>renderer.update(React.createElement(EditorHost,props)));
  advanced.open=true;
  await act(async()=>renderer.update(React.createElement(EditorHost,props)));
  assert.equal(disclosures.get('a-appearance-more'),advanced,'The outer disclosure was not remounted');
  assert.equal(disclosures.get('a-appearance-advanced'),customization,'The nested editor disclosure was not remounted');
  assert.equal(customization.open,true,'Reopening the parent preserves the nested disclosure');
  assert.equal(renderer.root.findByType(AppearanceSettings),mounted);
  assert.equal(input(renderer.root,'theme-css'),css);assert.equal(input(renderer.root,'theme-name'),name);
  assert.equal(mounted.props.editor.draft,draft,'Native folding does not reset or replace the private draft owner');
  assert.equal(css.props.value,'#amp-one{color:purple}');assert.equal(name.props.value,'Private unsaved look');
  assert.deepEqual(saved.writes,writes);assert.deepEqual(saved.entries(),entries);
  assert.equal(calls.length,beforeCalls);assert.deepEqual(changes,[]);assert.deepEqual(selections,[]);
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});
for(const scope of ['shared','client']){
 test(`${scope} pending and failed advanced preference reveals the still-mounted controls without replay`,async()=>{
  const held=deferred(),changes=[],selections=[],calls=[],advanced={open:false};
  const appearance=preferenceSnapshot({activeScope:scope}),before=structuredClone(appearance);
  let renderer;
  try{
   await act(async()=>{renderer=create(React.createElement(PreferenceHost,{
    appearance,dispatch:async(action,args)=>{calls.push({action,args});return galleryDispatch(action,args)},
    onPreferenceChange:(...args)=>{changes.push(args);return held.promise},
    onPreferenceSelect:(...args)=>selections.push(args),
   }),{createNodeMock:element=>element.type==='details'&&element.props.className==='a-appearance-more'?advanced:null})});
   assert.equal(advanced.open,false);
   const layout=renderer.root.findByProps({'aria-label':'Layout'}),field=fieldRow(renderer,'layout');
   await act(async()=>layout.props.onChange({target:{value:'work'}}));
   assert.equal(advanced.open,true,'A pending hidden preference is revealed');
   assert.match(textContent(renderer.root.findByProps({className:'a-appearance-disclosure-status'})),/Saving/);
   advanced.open=false; // A reader can fold while the admitted edit remains pending.
   await act(async()=>{held.reject(Error('Synthetic advanced preference refused'));await held.promise.catch(()=>{})});
   assert.equal(advanced.open,true,'A later actionable failure reopens the folded controls');
   assert.match(textContent(renderer.root.findByProps({className:'a-appearance-disclosure-status'})),/Needs attention/);
   assert.match(textContent(field),/Synthetic advanced preference refused/);
   assert.equal(renderer.root.findByProps({'aria-label':'Layout'}),layout,'The same input and feedback owner survive disclosure');
   assert.equal(changes.length,1);assert.equal(changes[0][0],'layout');assert.equal(changes[0][2].scope,scope);
   assert.deepEqual(selections,[]);assert.deepEqual(appearance,before,'Failure disclosure cannot rewrite a profile');
   assert.ok(calls.every(call=>call.action==='theme.list'),'Revealing failed controls performs no preference/action replay');
  }finally{if(renderer)await act(async()=>renderer.unmount())}
 });
}
test('Motion status truthfully follows live device changes and removes its listener',async()=>{
 const previous=globalThis.matchMedia,listeners=new Set(),query={matches:false,addEventListener:(name,fn)=>listeners.add(fn),removeEventListener:(name,fn)=>listeners.delete(fn)};
 globalThis.matchMedia=value=>{assert.equal(value,'(prefers-reduced-motion: reduce)');return query};
 let renderer;
 try{
  await act(async()=>{renderer=create(React.createElement(MotionStatus))});
  assert.match(textContent(renderer.root),/Standard motion is on/);
  query.matches=true;await act(async()=>{for(const listener of listeners)listener()});
  assert.match(textContent(renderer.root),/Reduced motion is on/);
  assert.equal(renderer.root.findAllByType('button').length,0);
  assert.equal(renderer.root.findAllByType('input').length,0);
  await act(async()=>renderer.unmount());renderer=null;assert.equal(listeners.size,0);
 }finally{if(renderer)await act(async()=>renderer.unmount());if(previous===undefined)delete globalThis.matchMedia;else globalThis.matchMedia=previous}
});

for(const scope of ['shared','client'])for(const catalogState of ['present','missing','loading','failed']){
 test(`${scope} unavailable selected appearance is visible with a ${catalogState} catalog and never resets a profile`,async()=>{
  const appearance=preferenceSnapshot({
   activeScope:scope,shared:{appearanceId:entryA.id,scheme:'dark',density:'compact'},
   localValues:{appearanceId:entryA.id,scheme:'light',density:'comfortable',layout:'work'},
  });
  appearance.theme={id:entryA.id,name:entryA.name,available:false,css:''};
  const before=structuredClone(appearance),calls=[],changes=[],selections=[],loading=deferred();
  const dispatch=async(action,args)=>{
   calls.push({action,args});
   if(action==='theme.list'){
    if(catalogState==='failed')throw Error('Library connection failed');
    if(catalogState==='loading')return loading.promise;
    return {accepted:true,result:{items:catalogState==='present'?[entryA,entryB]:[entryB],errors:[]}};
   }
   return {accepted:true};
  };
  const renderer=await renderPreferencePage({appearance,dispatch,
   onPreferenceChange:(...args)=>{changes.push(args);return false},
   onPreferenceSelect:(...args)=>{selections.push(args);return false},
  });
  try{
   const alerts=unavailableAlerts(renderer);assert.equal(alerts.length,1);
   const warning=textContent(alerts[0]);
   assert.match(warning,/Ocean/,'The warning identifies the unavailable selected appearance, even if its catalog row remains.');
   assert.match(warning,/selection.*retain/i);assert.match(warning,/Reference.*(?:shown|instead)|(?:shown|showing).*Reference/i);
   assert.match(warning,/choose another appearance/i);assert.match(warning,/Restore neutral appearance/);
   assert.equal(neutralControl(renderer).props.disabled,false);
   assert.equal(neutralControl(renderer).props['aria-pressed'],false,'Reference rendering is not a changed selected id.');
   if(catalogState==='failed')assert.ok(renderer.root.findAllByProps({role:'alert'}).some(node=>/Library connection failed/.test(textContent(node))),
    'The independent library error must remain visible alongside the selection warning.');
   assert.deepEqual(changes,[]);assert.deepEqual(selections,[]);
   assert.equal(calls.some(call=>call.action.startsWith('appearance.preferences.')),false);
   assert.deepEqual(appearance,before,'Unavailable package state is never an automatic reset or whole-profile rewrite.');
  }finally{
   await act(async()=>renderer.unmount());
   if(catalogState==='loading')await act(async()=>{
    loading.resolve({accepted:true,result:{items:[],errors:[]}});await loading.promise;
   });
  }
 });
}

for(const scope of ['shared','client'])test(`${scope} unavailable warning falls back to the retained id and neutral recovery writes only its captured field`,async()=>{
 const appearance=preferenceSnapshot({
  activeScope:scope,shared:{appearanceId:entryA.id,scheme:'dark',density:'compact',layout:'conversation'},
  localValues:{appearanceId:entryA.id,scheme:'light',density:'compact',layout:'work'},
 });
 appearance.theme={id:entryA.id,available:false,css:''};
 const before=structuredClone(appearance),calls=[],changes=[],selections=[],held=deferred(),other=scope==='shared'?'client':'shared';
 const options={appearance,dispatch:async(action,args)=>{calls.push({action,args});return galleryDispatch(action,args)},
  onPreferenceChange:(...args)=>{changes.push(args);return held.promise},
  onPreferenceSelect:(...args)=>{selections.push(args);return false},
 };
 const renderer=await renderPreferencePage(options);
 try{
  assert.equal(unavailableAlerts(renderer).length,1);
  assert.ok(textContent(unavailableAlerts(renderer)[0]).includes(entryA.id),'A missing display name cannot hide the retained selection identity.');
  assert.deepEqual(changes,[],'Rendering a fallback does not select Reference.');
  await act(async()=>neutralControl(renderer).props.onClick());
  assert.deepEqual(changes,[['appearanceId','reference',preferenceTarget(scope)]]);
  assert.equal(renderer.root.findByProps({name:'appearance-scheme',value:scope==='shared'?'dark':'light'}).props.checked,true);
  assert.equal(renderer.root.findByProps({'aria-label':'Density'}).props.value,'compact');
  assert.equal(unavailableAlerts(renderer).length,0,'A pending explicit Reference selection must not retain the old selected-id warning.');
  const switched={...appearance,activeScope:other,writeScope:other};
  await setPreferenceAppearance(renderer,options,switched);
  assert.equal(unavailableAlerts(renderer).length,1,'The other profile still retains the unavailable entry.');
  await act(async()=>{held.resolve({status:'stored',revision:2});await held.promise});
  assert.deepEqual(changes,[['appearanceId','reference',preferenceTarget(scope)]],
   'A late neutral receipt stays bound to the captured profile and never resets scheme or density.');
  assert.equal(renderer.root.findByProps({name:'appearance-scheme',value:other==='shared'?'dark':'light'}).props.checked,true);
  assert.equal(renderer.root.findByProps({'aria-label':'Density'}).props.value,'compact');
  assert.deepEqual(selections,[]);
  assert.deepEqual(appearance,before);
  assert.equal(calls.some(call=>call.action.startsWith('appearance.preferences.')),false);
  assertSilentPreferenceSuccess(renderer);
 }finally{await act(async()=>renderer.unmount())}
});

test('unavailable selection warning follows explicit committed availability, not catalog absence or a preview theme',async()=>{
 const initial=preferenceSnapshot({rendered:{appearanceId:entryA.id}});
 const calls=[],options={appearance:initial,dispatch:async(action,args)=>{
  calls.push({action,args});return {accepted:true,result:{items:[entryB],errors:[]}};
 }};
 const renderer=await renderPreferencePage(options);
 try{
  for(const available of [undefined,true,null]){
   await setPreferenceAppearance(renderer,options,{...initial,theme:{id:entryA.id,name:entryA.name,available}});
   assert.equal(unavailableAlerts(renderer).length,0,'A missing catalog row is not authoritative unavailability.');
  }
  await setPreferenceAppearance(renderer,options,{...initial,theme:{id:entryA.id,available:true},
   preview:{theme:{id:entryB.id,name:'Unavailable preview',available:false}}});
  assert.equal(unavailableAlerts(renderer).length,0,'Temporary preview metadata cannot declare the committed selection unavailable.');
  await setPreferenceAppearance(renderer,options,{...initial,theme:{id:entryA.id,name:entryA.name,available:false},
   preview:{theme:{id:entryB.id,name:'Working preview',available:true}}});
  assert.equal(unavailableAlerts(renderer).length,1,'An available preview does not repair the retained unavailable selection.');
  const reference=preferenceSnapshot();
  await setPreferenceAppearance(renderer,options,{...reference,theme:{id:entryA.id,name:entryA.name,available:false}});
  assert.equal(unavailableAlerts(renderer).length,0,'An old unavailable theme frame cannot warn that the current Reference selection is missing.');
  const changed=preferenceSnapshot({rendered:{appearanceId:entryB.id}});
  await setPreferenceAppearance(renderer,options,{...changed,theme:{id:entryA.id,name:entryA.name,available:false}});
  assert.equal(unavailableAlerts(renderer).length,0,'Unavailability from the previous selection cannot be attributed to a different selected id.');
  assert.equal(calls.some(call=>call.action.startsWith('appearance.preferences.')),false);
 }finally{await act(async()=>renderer.unmount())}
});

test('first browser selection captures the shared CAS and later scope switches retain the local profile',async()=>{
 const selections=[];
 const initial=preferenceSnapshot({activeScope:'shared',localValues:null,localRevision:4,sharedRevision:9,storageVersion:'missing'});
 const renderer=await renderPreferencePage({appearance:initial,onPreferenceSelect:async(scope,gesture)=>{selections.push([scope,gesture]);return {status:'stored',undoId:'profile-undo'}}});
 try{
  await act(async()=>button(renderer.root,'This browser').props.onClick());
  assert.deepEqual(selections,[['client',{namespace,generation:'generation-1',expectedRevision:4,expectedStorageVersion:'missing',expectedSharedRevision:9,retry:false}]]);
  assert.equal(button(renderer.root,'Global').props['aria-pressed'],true,'The source changes only after the storage acknowledgement updates state.');
  await act(async()=>renderer.update(React.createElement(PreferenceHost,{appearance:preferenceSnapshot({activeScope:'client',localRevision:5,storageVersion:'browser-v2',rendered:{scheme:'dark'}}),onPreferenceSelect:async(scope,gesture)=>{selections.push([scope,gesture]);return {status:'stored'}}})));
  await act(async()=>button(renderer.root,'Global').props.onClick());
  assert.deepEqual(selections[1],['shared',{namespace,generation:'generation-1',expectedRevision:5,expectedStorageVersion:'browser-v2',retry:false}]);
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
  const captured={namespace,generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',scope:'client',retry:false};
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
  assert.deepEqual(changes[1],['layout','conversation',{namespace,generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',scope:'client',retry:false}]);
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

const flushPreference=()=>new Promise(resolve=>setImmediate(resolve));
const setPreferenceAppearance=async(renderer,options,appearance)=>act(async()=>renderer.update(React.createElement(PreferenceHost,{...options,appearance})));

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
  assert.deepEqual(changes[0][2],{namespace,generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',scope:'client',retry:false});
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
  assert.deepEqual(changes[0][2],{namespace,generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',scope:'client',retry:false});
  assert.equal(renderer.root.findByProps({name:'appearance-scheme',value:'dark'}).props.checked,true);
  await act(async()=>{held.resolve({status:'stored',revision:2,undoId:'scheme-confirmed'});await flushPreference()});
  assert.equal(rowFeedback(renderer,'scheme'),'','A confirmed preference is quiet while its stream frame is pending.');
  assertSilentPreferenceSuccess(renderer);
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

const accentControl=renderer=>renderer.root.findByProps({'aria-label':'Accent color'});
const preferenceTarget=scope=>({
 namespace,generation:'generation-1',expectedRevision:1,
 ...(scope==='client'?{expectedStorageVersion:'browser-version'}:{}),scope,retry:false,
});
const accentSnapshot=(scope,overrides={})=>preferenceSnapshot({
 activeScope:scope,shared:{appearanceId:entryA.id,accent:'#112233',density:'compact'},
 localValues:{appearanceId:entryB.id,accent:'#445566',layout:'work'},...overrides,
});
const rowFeedback=(renderer,field)=>fieldRow(renderer,field).findAllByProps({'data-feedback-for':field}).map(textContent).join('');
function assertSilentPreferenceSuccess(renderer){
 assert.equal(renderer.root.findAll(node=>node.props?.['data-feedback-for']).length,0,'No persistent preference success feedback is rendered.');
 assert.equal(renderer.root.findAllByProps({className:'a-appearance-profile-feedback'}).length,0,'No persistent profile success feedback is rendered.');
 assertNoPreferenceSuccessControls(renderer);
}
function assertNoPreferenceSuccessControls(renderer){
 assert.doesNotMatch(textContent(renderer.root),/Saved\.|Using appearance accent\.|Appearance profile changed\.|Preference change undone\./);
 assert.equal(renderer.root.findAllByType('button').some(node=>/\bUndo\b/i.test(node.props['aria-label']||textContent(node))),false,'Preference/profile successes never expose Undo.');
 assert.equal(renderer.root.findAllByProps({'data-action':'appearance.preferences.undo'}).length,0);
}

for(const scope of ['shared','client']){
 for(const operation of ['accent','density','different-look','accent-reset','neutral-reset']){
  test(`${scope} ${operation} retains guarded Saving, then no Saved, appearance-accent notice or Undo`,async()=>{
   const held=deferred(),changes=[],initial=accentSnapshot(scope),profile=scope==='shared'?'shared':'local';
   const field=operation==='density'?'density':operation==='different-look'||operation==='neutral-reset'?'appearanceId':'accent';
   const value=operation==='accent'?'#abcdef':operation==='accent-reset'?null:operation==='neutral-reset'?'reference':
    operation==='different-look'?(scope==='shared'?entryB.id:entryA.id):(scope==='shared'?'comfortable':'compact');
   const options={appearance:initial,dispatch:galleryDispatch,onPreferenceChange:(...args)=>{changes.push(args);return held.promise}};
   const renderer=await renderPreferencePage(options);
   const control=()=>field==='accent'?accentControl(renderer):field==='density'?renderer.root.findByProps({'aria-label':'Density'}):
    operation==='neutral-reset'?neutralControl(renderer):galleryCards(renderer).find(node=>node.props['data-appearance-card']===value).findByType('input');
   const start=()=>operation==='accent-reset'?button(renderer.root,'Use appearance accent').props.onClick():
    operation==='neutral-reset'?control().props.onClick():control().props.onChange({target:{value}});
   try{
    await act(async()=>{start();start()});
    assert.deepEqual(changes,[[field,value,preferenceTarget(scope)]],'Only the captured field/profile is written once.');
    assert.equal(control().props.disabled,true);
    if(field==='accent')assert.equal(button(renderer.root,'Use appearance accent').props.disabled,true);
    assert.match(rowFeedback(renderer,field),/Saving/);
    assert.ok(fieldRow(renderer,field).findAllByProps({role:'status'}).some(node=>/Saving/.test(textContent(node))),'Pending work retains an accessible status.');
    assertNoPreferenceSuccessControls(renderer);
    assert.equal(renderer.root.findByProps({'aria-label':'Layout'}).props.disabled,false);
    await act(async()=>{start()});
    assert.equal(changes.length,1,'A directly invoked pending callback cannot enqueue duplicate intent.');
    await act(async()=>{
     held.resolve({accepted:true,result:{status:'stored',revision:2,undoId:'host-public-undo-token'}});
     await flushPreference();
    });
    assert.equal(control().props.disabled,false);
    assertSilentPreferenceSuccess(renderer);
    const values={...initial[profile].values,[field]:value};
    if(value===null||field==='appearanceId')delete values.accent;
    await setPreferenceAppearance(renderer,options,{...initial,[profile]:{...initial[profile],revision:2,values}});
    assertSilentPreferenceSuccess(renderer);
    if(field==='appearanceId'){
     assert.equal(neutralControl(renderer).props['aria-pressed'],value==='reference');
     assert.deepEqual(galleryCards(renderer).filter(node=>node.props['data-selected']).map(node=>node.props['data-appearance-card']),value==='reference'?[]:[value]);
    }
    else if(value!==null)assert.equal(control().props.value,value);
    assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false,'Explicit reset remains usable.');
    assert.equal(changes.length,1,'Neither success nor its state frame replays the mutation.');
   }finally{await act(async()=>renderer.unmount())}
  });
 }

 test(`${scope} profile selection retains guarded Saving, then no persistent success or Undo`,async()=>{
  const held=deferred(),selections=[],initial=accentSnapshot(scope),target=scope==='shared'?'client':'shared';
  const options={appearance:initial,onPreferenceSelect:(...args)=>{selections.push(args);return held.promise}};
  const renderer=await renderPreferencePage(options),targetLabel=target==='shared'?'Global':'This browser';
  try{
   await act(async()=>{button(renderer.root,targetLabel).props.onClick();button(renderer.root,targetLabel).props.onClick()});
   assert.equal(selections.length,1);
   assert.deepEqual(selections[0],[target,{namespace,generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',retry:false}]);
   const feedback=renderer.root.findByProps({className:'a-appearance-profile-feedback'});
   assert.equal(feedback.props.role,'status');assert.match(textContent(feedback),/Saving/);
   assert.equal(button(renderer.root,'Global').props.disabled,true);
   assert.equal(button(renderer.root,'This browser').props.disabled,true);
   assertNoPreferenceSuccessControls(renderer);
   await act(async()=>{held.resolve({status:'stored',revision:2,undoId:'host-profile-undo-token'});await flushPreference()});
   assert.equal(button(renderer.root,'Global').props.disabled,false);
   assert.equal(button(renderer.root,'This browser').props.disabled,false);
   assertSilentPreferenceSuccess(renderer);
   await setPreferenceAppearance(renderer,options,{...initial,activeScope:target,writeScope:target,local:{...initial.local,revision:2}});
   assert.equal(button(renderer.root,targetLabel).props['aria-pressed'],true);
   assertSilentPreferenceSuccess(renderer);
   assert.equal(selections.length,1);
  }finally{await act(async()=>renderer.unmount())}
 });
}

for(const operation of ['accent','profile'])for(const outcome of ['missing','awaiting-storage','failed-result','rejected','thrown']){
 test(`${operation} ${outcome} completion retains failure/Retry without false success or automatic replay`,async()=>{
  const calls=[],initial=accentSnapshot('client');
  const fail=async(...args)=>{
   calls.push(args);
   if(outcome==='thrown')throw Error('Change outcome unknown.');
   if(outcome==='missing')return undefined;
   if(outcome==='rejected')return {accepted:false,error:'Change refused.'};
   return {accepted:true,result:outcome==='failed-result'?{status:'failed',error:'Change failed.'}:{status:'awaiting-storage',commandId:'unknown-command'}};
  };
  const renderer=await renderPreferencePage({appearance:initial,...(operation==='accent'?{onPreferenceChange:fail}:{onPreferenceSelect:fail})});
  try{
   await act(async()=>{
    if(operation==='accent')accentControl(renderer).props.onChange({target:{value:'#abcdef'}});
    else button(renderer.root,'Global').props.onClick();
    await flushPreference();
   });
   const feedback=operation==='accent'?fieldRow(renderer,'accent').findByProps({'data-feedback-for':'accent'}):
    renderer.root.findByProps({className:'a-appearance-profile-feedback'});
   assert.match(textContent(feedback),/not confirmed|has not confirmed|unknown|refused|failed/);
   if(operation==='profile'){
    assert.equal(feedback.props.role,'alert');
    assert.equal(button(renderer.root,'This browser').props['aria-pressed'],true,'Unconfirmed selection cannot falsely switch the active profile.');
   }
   const retry=button(feedback,'Retry');assert.ok(retry,'Unconfirmed changes retain deliberate recovery.');
   assertNoPreferenceSuccessControls(renderer);
   assert.equal(accentControl(renderer).props.disabled,false);
   assert.equal(button(renderer.root,'Global').props.disabled,false);
   assert.equal(calls.length,1,'Failure/unknown completion is never automatically retried.');
   await act(async()=>{retry.props.onClick(buttonEvent);await flushPreference()});
   assert.equal(calls.length,2);
   assert.deepEqual(calls[1],operation==='accent'?['accent','#abcdef',{...preferenceTarget('client'),retry:true}]:
    ['shared',{namespace,generation:'generation-1',expectedRevision:1,expectedStorageVersion:'browser-version',retry:true}]);
   assertNoPreferenceSuccessControls(renderer);
  }finally{await act(async()=>renderer.unmount())}
 });
}

for(const scope of ['shared','client'])test(`failed ${scope} accent Retry keeps its captured profile after visiting the other profile`,async()=>{
 const changes=[],initial=accentSnapshot(scope),other=scope==='shared'?'client':'shared';
 const options={appearance:initial,onPreferenceChange:async(...args)=>{
  changes.push(args);if(changes.length===1)throw Error('Captured accent not confirmed.');
  return {status:'stored',revision:2,undoId:'host-retry-undo-token'};
 }};
 const renderer=await renderPreferencePage(options);
 try{
  await act(async()=>{accentControl(renderer).props.onChange({target:{value:'#abcdef'}});await flushPreference()});
  assert.match(rowFeedback(renderer,'accent'),/Captured accent not confirmed/);
  assert.ok(button(fieldRow(renderer,'accent'),'Retry'));
  await setPreferenceAppearance(renderer,options,{...initial,activeScope:other,writeScope:other});
  assert.equal(rowFeedback(renderer,'accent'),'','The other profile cannot present or retry this failure.');
  assert.equal(button(fieldRow(renderer,'accent'),'Retry'),undefined);
  assert.equal(accentControl(renderer).props.value,other==='shared'?'#112233':'#445566');
  await setPreferenceAppearance(renderer,options,initial);
  await act(async()=>{button(fieldRow(renderer,'accent'),'Retry').props.onClick(buttonEvent);await flushPreference()});
  assert.deepEqual(changes,[['accent','#abcdef',preferenceTarget(scope)],['accent','#abcdef',{...preferenceTarget(scope),retry:true}]]);
  assert.equal(accentControl(renderer).props.value,'#abcdef');
  assertSilentPreferenceSuccess(renderer);
 }finally{await act(async()=>renderer.unmount())}
});

for(const scope of ['shared','client']){
 test(`a failed ${scope} accent reset is not a successful no-op when reset is clicked again`,async()=>{
  const changes=[],renderer=await renderPreferencePage({appearance:accentSnapshot(scope),onPreferenceChange:async(...args)=>{
   changes.push(args);throw Error('Accent reset refused.');
  }});
  try{
   for(let attempt=1;attempt<=2;attempt++){
    assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false);
    await act(async()=>{button(renderer.root,'Use appearance accent').props.onClick();await flushPreference()});
    assert.equal(changes.length,attempt,'A failed null overlay is not evidence that the override was removed.');
    assert.deepEqual(changes.at(-1),['accent',null,preferenceTarget(scope)]);
    assert.match(rowFeedback(renderer,'accent'),/Accent reset refused/);
    assert.doesNotMatch(rowFeedback(renderer,'accent'),/Using appearance accent|Saved\./);
    assert.equal(button(renderer.root,'Undo Accent color change'),undefined);
   }
  }finally{await act(async()=>renderer.unmount())}
 });

 for(const order of ['failure-first','receipt-first'])test(`${scope} failed accent reconciles against a newer receipt before its stream frame (${order})`,async()=>{
  const held=deferred(),changes=[],initial=accentSnapshot(scope),profile=scope==='shared'?'shared':'local';
  const renderer=await renderPreferencePage({appearance:initial,onPreferenceChange:(...args)=>{
   changes.push(args);return args[0]==='accent'?held.promise:{status:'stored',revision:2,undoId:'layout-undo'};
  }});
  const fail=()=>act(async()=>{held.reject(Error('Old accent failed.'));await flushPreference()});
  try{
   await act(async()=>accentControl(renderer).props.onChange({target:{value:'#abcdef'}}));
   if(order==='failure-first'){
    await fail();
    assert.match(rowFeedback(renderer,'accent'),/Old accent failed/);
    assert.equal(accentControl(renderer).props.value,'#abcdef');
   }
   await act(async()=>renderer.root.findByProps({'aria-label':'Layout'}).props.onChange({target:{value:'conversation'}}));
   if(order==='receipt-first')await fail();
   assert.equal(initial[profile].revision,1,'No newer stream frame was supplied.');
   assert.equal(accentControl(renderer).props.value,initial[profile].values.accent,'The obsolete failure cannot leave an invisible optimistic accent behind.');
   assert.equal(rowFeedback(renderer,'accent'),'');
   assert.equal(button(fieldRow(renderer,'accent'),'Retry'),undefined);
   assert.equal(renderer.root.findByProps({'aria-label':'Layout'}).props.value,'conversation','The receipt watermark must not discard stored values before their stream frame.');
   assert.equal(rowFeedback(renderer,'layout'),'');
   assertSilentPreferenceSuccess(renderer);
   assert.deepEqual(changes.map(([field])=>field),['accent','layout']);
  }finally{await act(async()=>renderer.unmount())}
 });
}

for(const replaced of ['namespace','generation'])for(const operation of ['field-change','profile-select']){
 test(`late ${operation} failure after ${replaced} replacement cannot resurrect old feedback or Retry`,async()=>{
  const held=deferred(),changes=[],selections=[],initial=accentSnapshot('client'),options={appearance:initial,
   onPreferenceChange:(...args)=>{changes.push(args);return held.promise},
   onPreferenceSelect:(...args)=>{selections.push(args);return held.promise},
  };
  const renderer=await renderPreferencePage(options);
  try{
   if(operation==='field-change'){
    await act(async()=>accentControl(renderer).props.onChange({target:{value:'#abcdef'}}));
   }else{
    await act(async()=>{button(renderer.root,'Global').props.onClick();await flushPreference()});
   }
   const replacement={...initial,[replaced]:replaced==='namespace'?replacementNamespace:'replacement-generation'};
   await setPreferenceAppearance(renderer,options,replacement);
   await act(async()=>{held.reject(Error('Old document operation failed.'));await flushPreference()});
   assert.equal(rowFeedback(renderer,'accent'),'');
   assert.equal(renderer.root.findAllByProps({className:'a-appearance-profile-feedback'}).length,0);
   assert.equal(button(renderer.root,'Retry'),undefined);
   assertSilentPreferenceSuccess(renderer);
   assert.equal(accentControl(renderer).props.value,initial.local.values.accent);
   assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false);
   assert.equal(changes.length,operation==='field-change'?1:0);
   assert.equal(selections.length,operation==='profile-select'?1:0,'Identity replacement never replays the old operation.');
  }finally{await act(async()=>renderer.unmount())}
 });
}

test('an old Appearance Retry handler cannot dispatch during a held accent edit',async()=>{
 const held=deferred(),changes=[],initial=accentSnapshot('client'),options={appearance:initial,dispatch:galleryDispatch,
  onPreferenceChange:(...args)=>{
   changes.push(args);
   if(args[0]==='accent')return held.promise;
   if(args[0]==='appearanceId')throw Error('Appearance change failed.');
   return {status:'stored',revision:args[0]==='layout'?3:2,undoId:'appearance-undo'};
  },
 };
 const renderer=await renderPreferencePage(options);
 try{
  await act(async()=>{neutralControl(renderer).props.onClick();await flushPreference()});
  const oldHandler=button(fieldRow(renderer,'appearanceId'),'Retry').props.onClick;
  await act(async()=>accentControl(renderer).props.onChange({target:{value:'#abcdef'}}));
  await act(async()=>{
   oldHandler(buttonEvent);
   button(fieldRow(renderer,'appearanceId'),'Retry').props.onClick(buttonEvent);
  });
  assert.deepEqual(changes.map(([field])=>field),['appearanceId','accent'],'Related Retry cannot enqueue another appearance change.');
  assert.equal(renderer.root.findByProps({'aria-label':'Layout'}).props.disabled,false);
  await act(async()=>renderer.root.findByProps({'aria-label':'Layout'}).props.onChange({target:{value:'conversation'}}));
  assert.deepEqual(changes.map(([field])=>field),['appearanceId','accent','layout']);
  await act(async()=>{held.reject(Error('Accent outcome unknown.'));await flushPreference()});
  assert.equal(accentControl(renderer).props.disabled,false);
  assert.equal(button(renderer.root,'Undo Appearance change'),undefined);
 }finally{await act(async()=>renderer.unmount())}
});

for(const scope of ['shared','client']){
 test(`Use appearance accent resets only the captured ${scope} accent across a profile switch`,async()=>{
  const held=deferred(),changes=[],selections=[],actions=[],initial=accentSnapshot(scope),before=structuredClone(initial);
  const options={appearance:initial,dispatch:async(action,args)=>{
   actions.push([action,args]);return galleryDispatch(action,args);
  },onPreferenceChange:(...args)=>{changes.push(args);return held.promise},onPreferenceSelect:(...args)=>{selections.push(args)}};
  const renderer=await renderPreferencePage(options);
  try{
   assert.equal(accentControl(renderer).props.value,scope==='shared'?'#112233':'#445566');
   assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false);
   await act(async()=>button(renderer.root,'Use appearance accent').props.onClick());
   assert.deepEqual(changes,[['accent',null,preferenceTarget(scope)]],'Reset is one field deletion, not a replacement profile.');
   const otherScope=scope==='shared'?'client':'shared';
   await setPreferenceAppearance(renderer,options,{...initial,activeScope:otherScope,writeScope:otherScope});
   assert.equal(accentControl(renderer).props.value,otherScope==='shared'?'#112233':'#445566');
   await act(async()=>{held.resolve({status:'stored',revision:2,undoId:'accent-reset-undo'});await flushPreference()});
   assert.equal(rowFeedback(renderer,'accent'),'','Completion belongs to the captured profile, not the newly visible one.');
   await setPreferenceAppearance(renderer,options,initial);
   assert.equal(rowFeedback(renderer,'accent'),'','Confirmed reset is quiet even before the captured profile stream frame arrives.');
   assertSilentPreferenceSuccess(renderer);
   const profile=scope==='shared'?'shared':'local',after={...initial[profile].values};delete after.accent;
   await setPreferenceAppearance(renderer,options,{
    ...initial,[profile]:{...initial[profile],revision:2,values:after},
   });
   assert.notEqual(accentControl(renderer).props.value,before[profile].values.accent);
   assert.deepEqual(initial,before,'The component cannot mutate either authoritative profile.');
   assert.equal(changes.length,1);assert.deepEqual(selections,[]);
   assert.equal(actions.some(([action])=>action.startsWith('appearance.preferences.')),false,'Reset uses the field callback only.');
  }finally{await act(async()=>renderer.unmount())}
 });

 test(`Use appearance accent stays available with no ${scope} override and never writes the displayed fallback`,async()=>{
  const changes=[],actions=[],renderer=await renderPreferencePage({
   appearance:preferenceSnapshot({activeScope:scope}),dispatch:async(action,args)=>{
    actions.push(action);return galleryDispatch(action,args);
   },onPreferenceChange:async(...args)=>{changes.push(args);return {status:'stored'}},
  });
  try{
   assert.equal(accentControl(renderer).props.value,'#000000','A display-only fallback is not an explicit override.');
   for(let count=0;count<2;count++){
    assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false);
    await act(async()=>button(renderer.root,'Use appearance accent').props.onClick());
   }
   assert.deepEqual(changes,[]);
   assert.equal(actions.some(action=>action.startsWith('appearance.preferences.')),false);
   assertSilentPreferenceSuccess(renderer);
  }finally{await act(async()=>renderer.unmount())}
 });

 for(const field of ['accent','appearanceId'])test(`pending ${scope} ${field} locks duplicate accent/appearance edits but not unrelated controls`,async()=>{
  const held=deferred(),changes=[],options={appearance:accentSnapshot(scope),dispatch:galleryDispatch,onPreferenceChange:(...args)=>{
   changes.push(args);return args[0]===field?held.promise:{status:'stored',revision:2};
  }};
  const renderer=await renderPreferencePage(options);
  try{
   const start=field==='accent'?()=>accentControl(renderer).props.onChange({target:{value:'#abcdef'}}):()=>neutralControl(renderer).props.onClick();
   await act(async()=>{start();start()});
   assert.deepEqual(changes,[[field,field==='accent'?'#abcdef':'reference',preferenceTarget(scope)]]);
   assert.equal(accentControl(renderer).props.disabled,true);
   assert.equal(button(renderer.root,'Use appearance accent').props.disabled,true);
   assert.equal(neutralControl(renderer).props.disabled,true);
   assert.ok(fieldRow(renderer,'appearanceId').findAllByType('input').every(node=>node.props.disabled));
   assert.equal(button(renderer.root,'Use Ocean').props['aria-disabled'],true);
   await act(async()=>{
    button(renderer.root,'Use appearance accent').props.onClick();
    accentControl(renderer).props.onChange({target:{value:'#fedcba'}});
    neutralControl(renderer).props.onClick();
    galleryCards(renderer)[0].findByType('input').props.onChange();
   });
   assert.equal(changes.length,1,'Even a directly invoked disabled callback cannot enqueue related intent.');
   assert.equal(renderer.root.findByProps({'aria-label':'Layout'}).props.disabled,false);
   assert.equal(renderer.root.findByProps({name:'appearance-scheme',value:'dark'}).props.disabled,false);
   await act(async()=>renderer.root.findByProps({'aria-label':'Layout'}).props.onChange({target:{value:'conversation'}}));
   assert.deepEqual(changes.map(([key])=>key),[field,'layout']);
   await act(async()=>{held.resolve({status:'stored',revision:2});await flushPreference()});
   assert.equal(accentControl(renderer).props.disabled,false);
   assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false);
  }finally{await act(async()=>renderer.unmount())}
 });

 for(const outcome of ['stored','rejected'])test(`a ${outcome} ${scope} appearance switch cannot report the failed old accent as saved`,async()=>{
  const changes=[],held=deferred(),initial=accentSnapshot(scope),options={appearance:initial,dispatch:galleryDispatch,onPreferenceChange:(...args)=>{
   changes.push(args);
   if(args[0]==='accent')throw Error('Old accent failed.');
   return held.promise;
  }};
  const renderer=await renderPreferencePage(options);
  try{
   await act(async()=>{accentControl(renderer).props.onChange({target:{value:'#abcdef'}});await flushPreference()});
   assert.match(rowFeedback(renderer,'accent'),/Old accent failed/);
   assert.ok(button(fieldRow(renderer,'accent'),'Retry'));
   await act(async()=>neutralControl(renderer).props.onClick());
   assert.match(rowFeedback(renderer,'appearanceId'),/Saving/);
   assert.doesNotMatch(rowFeedback(renderer,'appearanceId'),/Saved\./);
   await act(async()=>{
    held.resolve(outcome==='stored'?{status:'stored',revision:2,undoId:'appearance-switch-undo'}:{accepted:false,error:'Appearance switch refused.'});
    await flushPreference();
   });
   if(outcome==='stored'){
    assert.equal(rowFeedback(renderer,'accent'),'','A confirmed different appearance removes old accent failure/Retry.');
    const profile=scope==='shared'?'shared':'local',values={...initial[profile].values,appearanceId:'reference'};
    delete values.accent;
    await setPreferenceAppearance(renderer,options,{...initial,[profile]:{...initial[profile],revision:2,values}});
    assert.equal(accentControl(renderer).props.value,'#000000','The failed accent overlay must not mask the new appearance accent.');
    assert.equal(rowFeedback(renderer,'appearanceId'),'');
    assertSilentPreferenceSuccess(renderer);
   }else{
    assert.match(rowFeedback(renderer,'appearanceId'),/Appearance switch refused/);
    assert.doesNotMatch(rowFeedback(renderer,'appearanceId'),/Saved\./);
    assert.equal(button(renderer.root,'Undo Appearance change'),undefined);
    assert.match(rowFeedback(renderer,'accent'),/Old accent failed/);
   }
   assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false);
   assert.deepEqual(changes.map(([field,value])=>[field,value]),[['accent','#abcdef'],['appearanceId','reference']]);
  }finally{await act(async()=>renderer.unmount())}
 });

 test(`${scope} accent success stays quiet across equal, other-profile and newer same-profile frames`,async()=>{
  const initial=accentSnapshot(scope),options={appearance:initial,onPreferenceChange:async()=>({status:'stored',revision:2,undoId:'accent-undo'})};
  const renderer=await renderPreferencePage(options);
  try{
   await act(async()=>accentControl(renderer).props.onChange({target:{value:'#abcdef'}}));
   assertSilentPreferenceSuccess(renderer);
   const profile=scope==='shared'?'shared':'local',other=scope==='shared'?'local':'shared';
   const confirmed={...initial,[profile]:{...initial[profile],revision:2,values:{...initial[profile].values,accent:'#abcdef'}}};
   await setPreferenceAppearance(renderer,options,confirmed);
   assertSilentPreferenceSuccess(renderer);
   const otherAdvanced={...confirmed,[other]:{...confirmed[other],revision:90}};
   await setPreferenceAppearance(renderer,options,otherAdvanced);
   assertSilentPreferenceSuccess(renderer);
   await setPreferenceAppearance(renderer,options,{...otherAdvanced,[profile]:{...confirmed[profile],revision:3}});
   assertSilentPreferenceSuccess(renderer);
   assert.equal(accentControl(renderer).props.value,'#abcdef');
   assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false);
  }finally{await act(async()=>renderer.unmount())}
 });

 for(const outcome of ['stored','failed'])test(`late older ${scope} accent ${outcome} cannot resurrect feedback after a newer appearance frame`,async()=>{
  const held=deferred(),changes=[],initial=accentSnapshot(scope),options={appearance:initial,onPreferenceChange:(...args)=>{
   changes.push(args);return held.promise;
  }};
  const renderer=await renderPreferencePage(options);
  try{
   await act(async()=>accentControl(renderer).props.onChange({target:{value:'#abcdef'}}));
   const profile=scope==='shared'?'shared':'local',values={...initial[profile].values,appearanceId:'reference'};delete values.accent;
   await setPreferenceAppearance(renderer,options,{...initial,[profile]:{...initial[profile],revision:3,values}});
   await act(async()=>{
    if(outcome==='stored')held.resolve({status:'stored',revision:2,undoId:'old-accent-undo'});
    else held.reject(Error('Late old accent failed.'));
    await flushPreference();
   });
   assert.equal(rowFeedback(renderer,'accent'),'');
   assert.equal(button(renderer.root,'Undo Accent color change'),undefined);
   assert.equal(button(fieldRow(renderer,'accent'),'Retry'),undefined);
   assert.equal(accentControl(renderer).props.value,'#000000');
   assert.equal(button(renderer.root,'Use appearance accent').props.disabled,false);
   await act(async()=>button(renderer.root,'Use appearance accent').props.onClick());
   assert.equal(changes.length,1,'Reset of the now absent override is still a no-op.');
  }finally{await act(async()=>renderer.unmount())}
 });
}
