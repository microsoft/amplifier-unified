import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {artifactPublications,artifactOverrideKey,publicationMode,presentationCapabilities,canvasOpenCommand,canvasOpenNotice,publicationOutputIds,inlineViewAccess,artifactViewCanvas,requireArtifactBody,followsLatest} from '../src/artifact-presentation.js';
import {imageGalleryEntries,imageGallerySelection} from '../src/image-generation.js';
import {createArtifactOverrides,createInlineOwnership,inlineEditNotice} from '../src/conversation-navigation.js';

const item=(kind='image',chat,version=2)=>({row:{id:'saved-artifact',sessionId:'conversation',kind,title:'Saved result'},version,publication:{messageId:'turn',version,...(chat?{chat}:{})}});
const state={client:{hostInstanceId:'fixture-host'},selectedSessionId:'conversation',canvasWorkspace:{views:[]}};
const storage=()=>{
 const entries=new Map();
 return {getItem:key=>entries.get(key)||null,setItem:(key,value)=>entries.set(key,value)};
};

test('all five combinations keep publication version independent of Canvas intent',()=>{
 const actions=[{name:'canvas.present'}];
 for(const chat of ['compact','inline','none']){
  const saved=item('image',chat),command=canvasOpenCommand(saved,'turn',actions);
  assert.equal(publicationMode(saved),chat);
  assert.deepEqual(command.args,{sessionId:'conversation',id:'saved-artifact',version:2,messageId:'turn',presentation:{chat,canvas:'open'}});
  assert.equal(command.action,'canvas.present');
 }
 assert.equal(publicationMode(item('image')),'inline');
 assert.equal(publicationMode(item('html')),'inline');
 assert.equal(publicationMode(item('markdown')),'compact');
 assert.equal(publicationMode(item('pdf')),'compact');
 assert.equal(publicationMode(item('canvas-app')),'compact');
 assert.equal(publicationMode(item('image','invalid')),'inline');
});

test('explicit publication identity and host capabilities do not infer renderer grants',()=>{
 const row={...item('markdown').row,publications:[{messageId:'turn',version:1,chat:'inline'},{messageId:'turn',version:2,chat:'compact'},{messageId:'other',version:3,chat:'none'}]};
 assert.deepEqual(artifactPublications([row],'turn').map(value=>[value.version,publicationMode(value)]),[[1,'inline'],[2,'compact']]);
 assert.deepEqual(artifactPublications([{...row,kind:'settings'}],'turn'),[]);
 assert.deepEqual(artifactPublications([{...row,publications:[]}],'turn'),[],'An explicitly library-only artifact stays library-only');
 assert.equal(presentationCapabilities(item('pdf')).inline,true);
 assert.equal(presentationCapabilities(item('gif')).inline,false,'Legacy host cannot mount retained GIF inline');
 for(const kind of ['canvas-app','mcp-app','browser','a2ui']){
  const value=item(kind);
  assert.equal(presentationCapabilities(value).inline,false,'A native kind alone cannot establish lifecycle admission');
  value.row.presentationCapabilities={compact:true,inline:true,canvas:true};
  value.row.capabilities=['arbitrary.active.grant'];
  assert.equal(presentationCapabilities(value).inline,true,'Only the server-projected native lifecycle enables inline');
  value.row.presentationCapabilities.inline=false;
  assert.equal(presentationCapabilities(value).inline,false);
 }
 const unavailable=item();
 unavailable.row.presentationCapabilities={compact:true,inline:false,canvas:false,reason:'Renderer unavailable'};
 assert.deepEqual(presentationCapabilities(unavailable),{compact:true,inline:false,canvas:false,reason:'Renderer unavailable'});
 const media=item('mp4');media.row.presentationCapabilities={compact:true,inline:true,canvas:true};
 assert.equal(presentationCapabilities(media).inline,true);
});

test('local overrides survive reload, separate clients and exact publication versions, and stay bounded',()=>{
 const firstStorage=storage(),otherStorage=storage(),first=createArtifactOverrides(()=>firstStorage,2);
 const id=artifactOverrideKey(state,item(),'turn');
 assert.equal(first.get(id),undefined);
 assert.equal(first.set(id,'compact'),true);
 assert.equal(createArtifactOverrides(()=>firstStorage).get(id),'compact');
 assert.equal(createArtifactOverrides(()=>otherStorage).get(id),undefined);
 for(const changed of [
  artifactOverrideKey({...state,client:{hostInstanceId:'another-host'}},item(),'turn'),
  artifactOverrideKey(state,{...item(),row:{...item().row,sessionId:'other-conversation'}},'turn'),
  artifactOverrideKey(state,item('image',undefined,3),'turn'),
  artifactOverrideKey(state,item(),'different-publication')
 ])assert.equal(first.get(changed),undefined);
 first.set('second','inline');first.set('third','compact');
 assert.equal(first.get(id),undefined);assert.equal(first.set('bad','none'),false);
 const failed=createArtifactOverrides(()=>{throw Error('Storage unavailable')});
 assert.equal(failed.set(id,'inline'),false);assert.equal(failed.get(id),'inline','Private in-memory choice remains usable');
});

test('only explicit publication output identities suppress the corresponding legacy item',()=>{
 const rows=[{...item('gif').row,outputId:'row-only',publications:[{messageId:'turn',version:2,outputId:'retained-output'}]},
  {...item('mp4').row,id:'other-media',publications:[{messageId:'other-turn',version:1,outputId:'other-output'}]}];
 assert.deepEqual([...publicationOutputIds(rows,'turn')],['retained-output']);
 assert.deepEqual([...publicationOutputIds([{...rows[0],publications:[{messageId:'turn',version:1}],sha256:'a'.repeat(64)}],'turn')],[],
  'Equal hashes, text, row IDs and a row-level outputId never create a binding');
 assert.deepEqual([...publicationOutputIds([{...rows[0],publications:[{messageId:'turn',version:2,chat:'none',outputId:'retained-output'}]}],'turn')],['retained-output']);
});

test('discovery chooses one action before sending and per-surface outcomes stay visible',()=>{
 assert.deepEqual(canvasOpenCommand(item(),'turn',[{name:'canvas.select'}]),{action:'canvas.select',args:{id:'saved-artifact',version:2}});
 assert.equal(canvasOpenNotice({accepted:true,result:{publication:{status:'published'},canvas:{status:'opened'}}}),'');
 for(const status of ['dirty-blocked','no-client','unavailable','unknown']){
  assert.match(canvasOpenNotice({accepted:true,result:{publication:{status:'published'},canvas:{status}}}),new RegExp(status));
 }
 assert.equal(canvasOpenNotice({accepted:true,result:{canvas:{status:'dirty-blocked',reason:'Finish your edit'}}}),'Finish your edit');
 assert.equal(canvasOpenNotice({accepted:true,result:{publication:{status:'rejected',reason:'Publication was kept'},canvas:{status:'opened'}}}),'Publication was kept',
  'An opened Canvas does not erase an independent publication refusal');
 assert.match(canvasOpenNotice({accepted:true}),/could not be confirmed/);
 assert.equal(canvasOpenNotice({accepted:false,error:'Settings changes were kept.'}),'Settings changes were kept.');
});

// Exercise the actual component control/lifecycle closures, without mistaking
// this synthetic hook harness for rendered-browser acceptance.
function component({dispatch,request=async()=>({id:'saved-artifact',kind:'image',open:true,content:'fixture-body'}),actions=[],saved=item('image','inline'),visible=true,overrides=createArtifactOverrides(()=>storage())}){
 const source=readFileSync(new URL('../src/inline-artifacts.jsx',import.meta.url),'utf8');
 const start=source.indexOf('export function GeneralArtifact('),end=source.indexOf(" if(mode==='none')return null;",start);
 assert.ok(start>=0&&end>start);
 const slots=[],effects=[],writes=[];let index=0,serial=0;
 const hooks={
  useRef(value){const i=index++;slots[i]??={current:value};return slots[i]},
  useState(value){const i=index++;slots[i]??={value:typeof value==='function'?value():value};
   return [slots[i].value,next=>{slots[i].value=typeof next==='function'?next(slots[i].value):next;writes.push({i,value:slots[i].value})}]},
  useEffect(fn,deps){const i=index++,old=slots[i];
   if(!old||deps.some((value,j)=>!Object.is(value,old.deps[j]))){
    const entry={deps,cleanup:old?.cleanup};slots[i]=entry;
    effects.push(()=>{entry.cleanup?.();entry.cleanup=fn()});
   }
  }
 };
 const render=runInNewContext(`${source.slice(start,end).replace('export ','')}
 return {mode,loaded,view,revoked,refusal,notice,override,busy,root,owned,opening,changeMode,open,follow,bodyError,setBodyAttempt};}\nGeneralArtifact`,{
  ...hooks,useLayoutEffect:hooks.useEffect,artifactOverrideKey,publicationMode,presentationCapabilities,canvasOpenCommand,canvasOpenNotice,inlineViewAccess,artifactViewCanvas,requireArtifactBody,followsLatest,overrides,inlineOwnership:createInlineOwnership(),inlineEditNotice,
  targetOf:view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]])),
  request,AbortController,URLSearchParams,crypto:{randomUUID:()=>`preview-command-${++serial}`},
  window:{amplifier:{getActions:()=>actions}},
  ...(!visible?{IntersectionObserver:class{observe(){}disconnect(){}}}:{})
 });
 return {writes,
  render(patch={}){index=0;const result=render({item:saved,messageId:'turn',state:{...state,canvasWorkspace:{views:[inlineView]}},dispatch,...patch});result.root.current={};
   while(effects.length)effects.shift()();return result},
  dispose(){for(const entry of slots)entry?.cleanup?.()}
 };
}
const settle=async()=>{for(let n=0;n<12;n++)await Promise.resolve()};
const inlineView={viewId:'inline-fixture',resourceId:'saved-artifact',resourceRevision:'revision',generation:1,renderer:'builtin.canvas.image',choices:[],available:true,view:{}};

test('actual dirty collapse preserves the owned renderer and writes no private view update',async()=>{
 const calls=[];let dirty=true;
 const harness=component({dispatch:async(action,args)=>{
  calls.push({action,args});
  if(action==='canvas.views.inline')return {accepted:true,result:inlineView};
  if(action==='canvas.views.release'&&dirty)throw Error('Finish or cancel this preview edit before closing it.');
  return {accepted:true};
 }});
 harness.render();harness.render();await settle();
 let rendered=harness.render();assert.ok(rendered.loaded);
 await rendered.changeMode('compact',true);rendered=harness.render();
 assert.equal(rendered.mode,'inline');assert.ok(rendered.loaded);assert.match(rendered.notice,/Finish or cancel/);
 assert.equal(rendered.override,undefined);
 dirty=false;await rendered.changeMode('compact',true);rendered=harness.render();
 assert.equal(rendered.mode,'compact');assert.equal(rendered.loaded,null);assert.equal(rendered.override,'compact');
 await settle();assert.equal(calls.filter(row=>row.action==='canvas.views.release').length,2,'Refused release plus one accepted release, no cleanup duplicate');
 assert.ok(calls.every(row=>row.action!=='view.update'));harness.dispose();
});

test('actual capacity refusal retains the publication default and exposes a compact retry handoff',async()=>{
 const calls=[];
 const harness=component({dispatch:async(action,args)=>{calls.push({action,args});throw Error('Close a preview to open another here, or open this artifact in Canvas.')}});
 harness.render();harness.render();await settle();
 const rendered=harness.render();
 assert.equal(rendered.mode,'inline');assert.equal(rendered.loaded,null);assert.match(rendered.refusal,/Close a preview/);
 assert.equal(rendered.override,undefined);assert.equal(calls.length,1,'No eviction, replay, or replacement action');
 harness.dispose();
});

test('actual publication default change cannot discard a dirty view or retarget its generation',async()=>{
 const calls=[];let dirty=true;
 const harness=component({dispatch:async(action,args)=>{
  calls.push({action,args});if(action==='canvas.views.inline')return {accepted:true,result:inlineView};
  if(dirty)throw Error('Finish or cancel this preview edit before closing it.');
  return {accepted:true};
 }});
 harness.render();harness.render();await settle();harness.render();
 const changed=item('image','compact');
 const refreshed={...state,canvasWorkspace:{views:[{...inlineView,dirty:true}]}};
 harness.render({item:changed,state:refreshed});await settle();
 const retained=harness.render({item:changed,state:refreshed});
 assert.equal(retained.mode,'inline');assert.ok(retained.loaded);assert.match(retained.notice,/Finish or cancel/);
 assert.equal(calls.at(-1).args.generation,1,'Release targets the original exact owned generation');
 assert.equal(calls.filter(row=>row.action==='canvas.views.inline').length,1,'Publication refresh does not remount or replay');
 dirty=false;await retained.changeMode('compact',true);harness.render({item:changed,state:refreshed});harness.dispose();
});

test('actual unknown Canvas open retries only its exact original envelope and publication mode',async()=>{
 const calls=[];let outcome=async()=>{throw Object.assign(Error('Delivery unconfirmed'),{unconfirmed:true})};
 const harness=component({saved:item('markdown','compact'),visible:false,actions:[{name:'canvas.present'}],
  dispatch:(action,args,meta)=>{calls.push({action,args,meta});return outcome()}});
 let rendered=harness.render();
 await rendered.changeMode('inline',true);rendered=harness.render();await rendered.open();
 assert.equal(calls.length,1);assert.equal(calls[0].action,'canvas.present');
 assert.equal(calls[0].args.presentation.chat,'compact','Private expansion must not rewrite the publication');
 outcome=async()=>({accepted:false,error:'Settings changes were kept.'});
 await harness.render().open();assert.deepEqual(calls[1],calls[0]);
 outcome=async()=>({accepted:true,result:{publication:{status:'unchanged'},canvas:{status:'no-client',reason:'No eligible client'}}});
 await harness.render().open();assert.deepEqual(calls[2],calls[0]);
 rendered=harness.render();assert.equal(rendered.opening.current,null);assert.equal(rendered.notice,'No eligible client');
 assert.ok(calls.every(row=>row.action==='canvas.present'),'No automatic fallback to another mutation');
 harness.dispose();
});

test('actual clean unmount releases once and late body delivery never mounts or replays',async()=>{
 let finish;const calls=[];
 const harness=component({dispatch:async(action,args)=>{calls.push({action,args});return {accepted:true,result:inlineView}},
  request:()=>new Promise(done=>{finish=done})});
 harness.render();harness.render();await settle();
 harness.render();await settle();
 harness.dispose();await settle();const writes=harness.writes.length;
 finish({id:'saved-artifact',kind:'image'});await settle();
 assert.equal(harness.writes.length,writes);
 assert.equal(calls.filter(row=>row.action==='canvas.views.inline').length,1);
 assert.equal(calls.filter(row=>row.action==='canvas.views.release').length,1);
});

test('admitted exact view snapshot uses its own app state and explicit read-only numbered selection',()=>{
 const body={id:'app',kind:'canvas-app',app:{state:{value:'saved'}},content:'saved definition'};
 const view={...inlineView,resourceId:'app',publishedVersion:2,selectedVersion:2,latestVersion:2,app:{revision:2,stateRevision:0,state:{value:'numbered'}}};
 let result=artifactViewCanvas(body,view);
 assert.equal(result.app,view.app);assert.equal(result.readOnlyVersion,true,'Number equal to Latest is still read-only');
 const live={...view,selectedVersion:null,followLatest:true,app:{revision:2,stateRevision:3,state:{value:'current'}}};
 result=artifactViewCanvas(body,live);
 assert.equal(result.app.state.value,'current');assert.equal(result.readOnlyVersion,false);
 assert.equal(body.app.state.value,'saved','Original saved body is never mutated');
 for(const kind of ['browser','a2ui','image','markdown'])assert.equal(followsLatest(kind),false);
 for(const invalid of [null,{}, {id:'app',kind:'canvas-app'}, {id:'mcp',kind:'mcp-app'}, {id:'doc',kind:'markdown'}, {id:'ui',kind:'a2ui',surface:{root:'missing',components:[]}}]){
  assert.throws(()=>requireArtifactBody(invalid),/source is unavailable/);
 }
});

test('actual explicit Latest follows the same publication view, blocks dirty changes and returns to its exact published number',async()=>{
 const saved=item('canvas-app','inline'),calls=[];
 saved.row.presentationCapabilities={compact:true,inline:true,canvas:true};
 let view={...inlineView,renderer:'builtin.canvas.canvas-app',publishedVersion:2,selectedVersion:2,latestVersion:3,followLatest:false,dirty:false};
 const harness=component({saved,request:async()=>({id:'saved-artifact',kind:'canvas-app',app:{revision:2,stateRevision:0,state:{},versions:[],requests:[]}}),
  dispatch:async(action,args,meta)=>{
   calls.push({action,args,meta});
   if(action==='canvas.views.inline')return {accepted:true,result:view};
   if(action==='canvas.views.follow'){
    const before=view;view={...view,followLatest:args.followLatest,selectedVersion:args.followLatest?null:2,generation:view.generation+1};
    assert.equal(args.viewId,before.viewId);assert.equal(args.generation,before.generation);
    return {accepted:true,result:view};
   }
   return {accepted:true};
  }});
 const render=patch=>harness.render({state:{...state,canvasWorkspace:{views:[view]}},...patch});
 render();render();await settle();render();await settle();
 await render().follow(true);await settle();render();await settle();
 assert.equal(render().view.followLatest,true);
 assert.equal(render().view.publishedVersion,2);
 view={...view,dirty:true};
 await render().follow(false);
 assert.match(render().notice,/Finish or cancel/);
 assert.equal(calls.filter(row=>row.action==='canvas.views.follow').length,1);
 view={...view,dirty:false};
 await render().follow(false);render();await settle();render();await settle();
 assert.equal(render().view.selectedVersion,2);
 assert.equal(calls.filter(row=>row.action==='canvas.views.inline').length,1,'No replacement admission sidesteps dirty protection');
 assert.ok(calls.every(row=>!['canvas.present','canvas.select','smartTools.call','view.update'].includes(row.action)));
 harness.dispose();await settle();
 assert.equal(calls.filter(row=>row.action==='canvas.views.release').length,1);
 assert.equal(calls.at(-1).args.generation,3,'Cleanup addresses only the last accepted generation');
});

test('actual live app consumes canonical view updates while primary and another client cannot retarget it',async()=>{
 const saved=item('canvas-app','inline');saved.row.presentationCapabilities={inline:true,canvas:true,compact:true};
 let view={...inlineView,resourceId:saved.row.id,followLatest:true,publishedVersion:2,selectedVersion:null,app:{revision:2,stateRevision:0,state:{value:'first'}}};
 const harness=component({saved,dispatch:async()=>({accepted:true,result:view}),request:async()=>({id:saved.row.id,kind:'canvas-app',app:view.app})});
 const render=()=>harness.render({state:{...state,canvas:{app:{state:{value:'WRONG PRIMARY'}}},canvasWorkspace:{views:[view]}}});
 render();render();await settle();render();await settle();render();
 view={...view,app:{...view.app,stateRevision:1,state:{value:'current'}}};
 assert.equal(render().view.app.state.value,'current');
 view={...view,generation:2,resourceRevision:'new-definition',app:{...view.app,revision:3}};
 assert.equal(render().revoked,false,'Live same-publication canonical design update retains the component owner');
 const foreign=harness.render({state:{...state,client:{hostInstanceId:'foreign'},canvasWorkspace:{views:[view]}}});
 assert.equal(foreign.revoked,true);harness.dispose();
});

test('actual missing source stays an explicit failure; Retry re-reads the same admitted view',async()=>{
 let reads=0;const calls=[];
 const harness=component({dispatch:async(action,args)=>{calls.push({action,args});return {accepted:true,result:inlineView}},
  request:async()=>++reads===1?{id:'saved-artifact',kind:'image'}:{id:'saved-artifact',kind:'image',content:'pixels'}});
 harness.render();harness.render();await settle();harness.render();await settle();
 let result=harness.render();assert.match(result.bodyError,/source is unavailable/);assert.equal(result.loaded.canvas,null);
 result.setBodyAttempt(value=>value+1);harness.render();await settle();result=harness.render();
 assert.equal(result.bodyError,'');assert.equal(result.loaded.canvas.content,'pixels');
 assert.equal(calls.filter(row=>row.action==='canvas.views.inline').length,1);harness.dispose();
});

test('actual follow receipt can lag its new host generation; unknown delivery retries one unchanged envelope',async()=>{
 const saved=item('canvas-app','inline');saved.row.presentationCapabilities={inline:true,canvas:true,compact:true};
 const initial={...inlineView,followLatest:false,publishedVersion:2,selectedVersion:2};
 const next={...initial,followLatest:true,selectedVersion:null,generation:2};
 let view=initial,resolveFollow,unknown=true;const calls=[];
 const harness=component({saved,request:async()=>({id:saved.row.id,kind:'canvas-app',app:{revision:2,stateRevision:0,state:{},versions:[],requests:[]}}),
  dispatch:async(action,args,meta)=>{
   calls.push({action,args,meta});
   if(action==='canvas.views.inline')return {accepted:true,result:initial};
   if(action==='canvas.views.follow'){
    if(unknown){unknown=false;throw Object.assign(Error('Version change delivery unconfirmed'),{unconfirmed:true})}
    return new Promise(resolve=>{resolveFollow=resolve});
   }
   return {accepted:true};
  }});
 const render=()=>harness.render({state:{...state,canvasWorkspace:{views:[view]}}});
 render();render();await settle();render();await settle();
 await render().follow(true);assert.match(render().notice,/unconfirmed/);
 view=next;
 assert.equal(render().revoked,false,'Unconfirmed version change is not access revocation');
 const retry=render().follow(true);await settle();
 assert.deepEqual(calls.filter(row=>row.action==='canvas.views.follow')[0],calls.filter(row=>row.action==='canvas.views.follow')[1]);
 assert.equal(render().revoked,false,'State may precede its own accepted receipt');
 resolveFollow({accepted:true,result:next});await retry;render();await settle();
 const result=render();assert.equal(result.revoked,false);assert.equal(result.view.generation,2);assert.equal(result.view.followLatest,true);
 harness.dispose();
});

test('actual owned preview does not retarget a revoked generation or reused target from another client',async()=>{
 for(const patch of [
  {canvasWorkspace:{views:[{...inlineView,generation:2}]}},
  {client:{hostInstanceId:'fixture-host',id:'another-client'},canvasWorkspace:{views:[inlineView]}},
  {client:{hostInstanceId:'another-host'},canvasWorkspace:{views:[inlineView]}}
 ]){
  const harness=component({dispatch:async()=>({accepted:true,result:inlineView})});
  harness.render();harness.render();await settle();harness.render();
  const result=harness.render({state:{...state,...patch}});
  assert.equal(result.revoked,true);assert.equal(result.owned.current.view.generation,1);
  await settle();assert.match(harness.render({state:{...state,...patch}}).notice,/removed access/);
  harness.dispose();
 }
});

test('actual gallery retains initial and explicitly selected slots across job reorder/pruning',()=>{
 const source=readFileSync(new URL('../src/inline-artifacts.jsx',import.meta.url),'utf8');
 const start=source.indexOf('function ImageGallery('),end=source.indexOf(' return <div',start);
 let selected,initialized=false;
 const render=runInNewContext(source.slice(start,end)+'return {entry,selected,setSelected};} ImageGallery',{
  useState:init=>{if(!initialized){selected=init();initialized=true}return [selected,value=>{selected=value}]},
  useEffect:fn=>fn(),imageJobLabel:()=>'',titleOf:()=>'',imageGallerySelection
 });
 const a={id:'request:a',item:item()},b={id:'request:b',item:item()};
 assert.equal(render({entries:[a,b]}).entry,a);
 assert.equal(render({entries:[b,a]}).entry,a,'Pruning/reorder cannot change the initially shown output');
 render({entries:[b,a]}).setSelected(b.id);
 assert.equal(render({entries:[a,b]}).entry,b,'Explicit selection remains on the same output slot');
 assert.equal(render({entries:[a]}).entry,a,'Actual removal permits fallback');
 const saved=(id,requestId)=>({row:{id,kind:'image',sessionId:'s'},version:1,publication:{
  messageId:'turn',version:1,chat:'inline',outputId:'out-'+id,imageGeneration:{verified:true,requestId,operation:'generate',parentId:null}
 }});
 const items=[saved('c','unrelated'),saved('a','shared'),saved('b','shared')];
 const galleryState={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:[
  {id:'j-c',requestId:'unrelated',messageId:'turn'},{id:'j-ab',requestId:'shared',messageId:'turn'}
 ]}}]};
 const entries=imageGalleryEntries(galleryState,'turn',items);
 render({entries}).setSelected(entries.find(entry=>entry.item.row.id==='b').id);
 for(const chat of ['compact','none']){
  const changed=items.map(item=>item.row.id==='a'?{...item,publication:{...item.publication,chat}}:item);
  for(const next of [galleryState,{...galleryState,sessions:[{id:'s'}]}]){
   assert.equal(render({entries:imageGalleryEntries(next,'turn',changed)}).entry.item.row.id,'b','Actual selection does not fall back to unrelated C');
  }
 }
});

// Run the actual shared gallery admission hook, not a stand-in authorization
// policy. Both Preview and Thumbnail consume this hook; DOM checks are authored
// separately in general-inline-ownership-browser.mjs.
function imageViewHarness({dispatch,request=async()=>({kind:'image',content:'PRIVATE_PIXELS'})}={}){
 const source=readFileSync(new URL('../src/inline-artifacts.jsx',import.meta.url),'utf8');
 const start=source.indexOf('function useImageView('),end=source.indexOf('// Body downloads',start);
 assert.ok(start>=0&&end>start);
 const slots=[],effects=[],writes=[],calls=[];let index=0;
 const hooks={
  useRef(value){const i=index++;slots[i]??={current:value};return slots[i]},
  useState(value){const i=index++;slots[i]??={value:typeof value==='function'?value():value};
   return [slots[i].value,next=>{slots[i].value=typeof next==='function'?next(slots[i].value):next;writes.push(i)}]},
  useEffect(fn,deps){const i=index++,old=slots[i];
   if(!old||deps.some((value,j)=>!Object.is(value,old.deps[j]))){
    const entry={deps,cleanup:old?.cleanup};slots[i]=entry;
    effects.push(()=>{entry.cleanup?.();entry.cleanup=fn()});
   }
  }
 };
 const invoke=runInNewContext(source.slice(start,end)+'useImageView',{
  ...hooks,useLayoutEffect:hooks.useEffect,inlineViewAccess,AbortController,URLSearchParams,
  crypto:{randomUUID:()=>String(calls.length)},request,
  targetOf:view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(field=>[field,view[field]]))
 });
 const send=async(action,args)=>{
  calls.push({action,args});
  return dispatch?dispatch(action,args):{accepted:true,result:inlineView,stateRevision:2};
 };
 const initial={...state,revision:1,client:{...state.client,id:'tab'},canvasWorkspace:{views:[]}};
 return {calls,writes,
  render({value=initial,enabled=true,attempt=0}={}){
   index=0;const result=invoke(item(),'turn',value,send,enabled,attempt);
   while(effects.length)effects.shift()();return result;
  },
  bodies:()=>slots.flatMap(slot=>slot?.value?.canvas?.content?[slot.value.canvas.content]:[]),
  dispose(){for(const entry of slots)entry?.cleanup?.()}
 };
}
const imageState=(views=[inlineView],patch={})=>({...state,revision:2,client:{...state.client,id:'tab'},canvasWorkspace:{views},...patch});

test('actual gallery admission waits for its host revision without falsely revoking startup',async()=>{
 const harness=imageViewHarness();
 harness.render();await settle();
 let result=harness.render();
 assert.equal(result.revoked,false);assert.ok(!result.canvas,'An older state cannot display an unconfirmed body');
 result=harness.render({value:imageState()});
 assert.equal(result.revoked,false);assert.equal(result.canvas.content,'PRIVATE_PIXELS');
 result=harness.render({value:imageState([{...inlineView,view:{zoom:2}}],{revision:3})});
 assert.equal(result.canvas.view.zoom,2,'Current exact view metadata is used');
 harness.dispose();await settle();
 assert.equal(harness.calls.filter(row=>row.action==='canvas.views.release').length,1);
});

test('actual gallery purges body and derived thumbnail pixels on exact target or scope revocation, without revival',async()=>{
 for(const value of [
  imageState([],{revision:3}),
  ...['viewId','resourceId','resourceRevision','generation'].map(field=>imageState([{...inlineView,[field]:field==='generation'?2:'different'}],{revision:3})),
  imageState([inlineView],{client:{hostInstanceId:'other-host',id:'tab'},revision:3}),
  imageState([inlineView],{client:{hostInstanceId:'fixture-host',id:'other-tab'},revision:3})
 ]){
  const harness=imageViewHarness();
  harness.render();await settle();let result=harness.render({value:imageState()});
  assert.equal(result.canvas.content,'PRIVATE_PIXELS');
  result.replaceImageContent('DERIVED_THUMBNAIL');result=harness.render({value:imageState()});
  assert.equal(result.canvas.content,'DERIVED_THUMBNAIL');
  result=harness.render({value});assert.equal(result.revoked,true);assert.ok(!result.canvas);
  assert.deepEqual(harness.bodies(),[],'No original or downsampled content survives revocation');
  result=harness.render({value:imageState([inlineView],{revision:4})});
  assert.equal(result.revoked,true);assert.ok(!result.canvas,'An old exact target cannot revive the revoked body');
  assert.equal(harness.calls.filter(row=>row.action==='canvas.views.inline').length,1,'No automatic re-admission');
  harness.dispose();await settle();
 }
});

test('actual gallery missing admission at reached revision is revoked; local close/reopen is explicit new admission',async()=>{
 const missing=imageViewHarness();
 missing.render();await settle();
 assert.equal(missing.render({value:imageState([])}).revoked,true);
 assert.deepEqual(missing.bodies(),[]);missing.dispose();
 const harness=imageViewHarness();
 harness.render();await settle();assert.ok(harness.render({value:imageState()}).canvas);
 const closed=harness.render({value:imageState(),enabled:false});
 assert.equal(closed.revoked,false);assert.ok(!closed.canvas);
 harness.render({value:imageState(),enabled:true});await settle();
 assert.ok(harness.render({value:imageState()}).canvas);
 assert.equal(harness.calls.filter(row=>row.action==='canvas.views.inline').length,2);
 harness.dispose();
});

test('actual gallery failed body cleanup leaves an explicit retry, not a false host revocation or automatic replay',async()=>{
 let revision=1,downloads=0;
 const harness=imageViewHarness({
  dispatch:async action=>action==='canvas.views.inline'?{accepted:true,result:inlineView,stateRevision:++revision}:{accepted:true},
  request:async()=>{if(++downloads===1)throw Error('Temporary body failure');return {kind:'image',content:'RETRIED_PIXELS'}}
 });
 harness.render();await settle();
 let result=harness.render({value:imageState([])});
 assert.equal(result.revoked,false);assert.equal(result.error,'Temporary body failure');assert.ok(!result.canvas);
 assert.equal(harness.calls.filter(row=>row.action==='canvas.views.inline').length,1);
 // Even reappearing old target metadata cannot restore content after cleanup.
 result=harness.render({value:imageState()});assert.ok(!result.canvas);
 harness.render({value:imageState([]),attempt:1});await settle();
 result=harness.render({value:imageState([inlineView],{revision:3}),attempt:1});
 assert.equal(result.revoked,false);assert.equal(result.error,'');assert.equal(result.canvas.content,'RETRIED_PIXELS');
 assert.equal(harness.calls.filter(row=>row.action==='canvas.views.inline').length,2);
 harness.dispose();
});

test('actual gallery unmount and host removal discard late fetches and release only their owned admission',async()=>{
 for(const unmount of [false,true]){
  let finish,signal;
  const harness=imageViewHarness({request:(_path,options)=>{signal=options.signal;return new Promise(resolve=>{finish=resolve})}});
  harness.render();await settle();harness.render({value:imageState()});
  if(unmount)harness.dispose();else assert.equal(harness.render({value:imageState([],{revision:3})}).revoked,true);
  assert.equal(signal.aborted,true);const writes=harness.writes.length;
  finish({kind:'image',content:'LATE_PIXELS'});await settle();
  assert.equal(harness.writes.length,writes);assert.deepEqual(harness.bodies(),[]);
  assert.equal(harness.calls.filter(row=>row.action==='canvas.views.release').length,1);
  if(!unmount)harness.dispose();
 }
 let admit;
 const harness=imageViewHarness({dispatch:action=>action==='canvas.views.inline'?new Promise(resolve=>{admit=resolve}):Promise.resolve({accepted:true})});
 harness.render();harness.dispose();const writes=harness.writes.length;
 admit({accepted:true,result:inlineView,stateRevision:2});await settle();
 assert.equal(harness.writes.length,writes);assert.equal(harness.calls.filter(row=>row.action==='canvas.views.release').length,1);
});