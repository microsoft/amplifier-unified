import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {createHash,webcrypto} from 'node:crypto';
import {artifactPublications,artifactOverrideKey,publicationMode,presentationCapabilities,canvasOpenCommand,canvasOpenNotice,publicationOutputIds,inlineViewAccess,artifactViewCanvas,requireArtifactBody,followsLatest,retainedThumbnailTuple,legacyGenerationCandidate,qualifiedLegacyGeneration} from '../src/artifact-presentation.js';
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
 const slots=[],effects=[],writes=[],edits=createInlineOwnership();let index=0,serial=0;
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
  ...hooks,useLayoutEffect:hooks.useEffect,artifactOverrideKey,publicationMode,presentationCapabilities,canvasOpenCommand,canvasOpenNotice,inlineViewAccess,artifactViewCanvas,requireArtifactBody,followsLatest,overrides,inlineOwnership:edits,inlineEditNotice,
  targetOf:view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]])),
  request:async(path,options)=>{
   const body=await request(path,options),params=new URL(path,'https://fixture.invalid').searchParams;
   return {...Object.fromEntries(['viewId','resourceRevision'].map(field=>[field,params.get(field)])),
    generation:Number(params.get('generation')),...body,...(body.app?{app:{manifest:{},...body.app}}:{})};
  },AbortController,URLSearchParams,crypto:{randomUUID:()=>`preview-command-${++serial}`},
  window:{amplifier:{getActions:()=>actions}},
  ...(!visible?{IntersectionObserver:class{observe(){}disconnect(){}}}:{})
 });
 return {writes,edits,
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
    const hold=await harness.edits.prepareTransition(args,meta.id,args.followLatest,{...state,canvasWorkspace:{views:[view]}});
    hold.sent=true;
    if(unknown){unknown=false;throw Object.assign(Error('Version change delivery unconfirmed'),{unconfirmed:true})}
    return new Promise(resolve=>{resolveFollow=async receipt=>{
     await harness.edits.completeTransition(hold,receipt,{...state,canvasWorkspace:{views:[view]}});resolve(receipt);
    }});
   }
   return {accepted:true};
  }});
 const render=()=>harness.render({state:{...state,canvasWorkspace:{views:[view]}}});
 render();render();await settle();render();await settle();
 const detach=harness.edits.registerRenderer(initial,{prepare:async()=>({token:'synthetic-proof'}),validate:()=>true,release(){}});
 await render().follow(true);assert.match(render().notice,/unconfirmed/);
 view=next;
 assert.equal(render().revoked,false,'Unconfirmed version change is not access revocation');
 const retry=render().follow(true);await settle();
 assert.deepEqual(calls.filter(row=>row.action==='canvas.views.follow')[0],calls.filter(row=>row.action==='canvas.views.follow')[1]);
 assert.equal(render().revoked,false,'State may precede its own accepted receipt');
 resolveFollow({accepted:true,result:next});await retry;render();await settle();
 const result=render();assert.equal(result.revoked,false);assert.equal(result.view.generation,2);assert.equal(result.view.followLatest,true);
 detach();harness.dispose();
});

test('native body contracts accept deferred exact sources and typed retained media, not invented eager bodies',()=>{
 const target={viewId:'inline-source',resourceId:'native',resourceRevision:'exact-saved',generation:4};
 const base={id:'native',sessionId:'chat',...target},pointer={$resource:'a'.repeat(64),bytes:100};
 for(const kind of ['html','babylon','pdf']){
  const body={...base,kind,contentResource:pointer};
  assert.equal(requireArtifactBody(body,target),body);
  for(const damage of [{contentResource:{}},{contentResource:{$resource:'bad',bytes:100}},
   {contentResource:{...pointer,bytes:0}},{generation:5},{id:'other'}])
   assert.throws(()=>requireArtifactBody({...body,...damage},target),/source is unavailable/);
 }
 for(const kind of ['gif','mp4']){
  const body={...base,kind,mediaReference:{messageId:'turn',item:{id:'output',sessionId:'chat',version:2,
   sha256:'b'.repeat(64),title:'Saved media',filename:'saved.'+kind,
   media:{mimeType:kind==='gif'?'image/gif':'video/mp4',loopCount:null}}}};
  assert.equal(requireArtifactBody(body,target),body);
  assert.throws(()=>requireArtifactBody({...body,mediaReference:undefined,content:'not a retained reference'},target));
  assert.throws(()=>requireArtifactBody({...body,mediaReference:{...body.mediaReference,item:{...body.mediaReference.item,sessionId:'foreign'}}},target));
 }
 for(const [kind,fields] of [
  ['canvas-app',{app:{revision:1,manifest:{},state:{}}}],
  ['mcp-app',{mcp:{serverId:'connector',configuration:'saved-config'}}],
  ['a2ui',{surface:{root:'root',components:[{id:'root',component:{Text:{text:{literalString:'Actual surface'}}}}]}}]
 ]){
  const body={...base,kind,...fields};assert.equal(requireArtifactBody(body,target),body);
  assert.throws(()=>requireArtifactBody({...base,kind,content:'not native data'},target));
 }
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

// Retain the original admission negatives for the selected gallery preview.
// Thumbnails now have an independent retained-read lifetime tested below.
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

const thumbnailItem=()=>({row:{id:'generated',kind:'image',sessionId:'conversation',workspaceId:'project',revision:2,
 versions:[{version:1,kind:'image',body:{$resource:'original-body'},outputBinding:{
  outputId:'output',outputVersion:1,sessionId:'conversation',sha256:'a'.repeat(64)}},
 {version:2,kind:'image',body:{$resource:'later-body'},outputBinding:{
  outputId:'later-output',outputVersion:2,sessionId:'conversation',sha256:'b'.repeat(64)}}]},
 version:1,publication:{messageId:'turn',version:1,chat:'inline',outputId:'output',
 imageGeneration:{verified:true,requestId:'receipt',operation:'generate',parentId:null}}});
const thumbnailState=()=>({...state,selectedWorkspaceId:'project',client:{hostInstanceId:'fixture-host',id:'tab'}});

test('retained thumbnail tuple binds exact version/output/hash without adopting the latest body or a view',()=>{
 const value=thumbnailItem(),scope=thumbnailState(),tuple=retainedThumbnailTuple(value,'turn',scope);
 const url=new URL(tuple.url,'https://fixture.example');
 assert.equal(url.pathname,'/api/outputs/output/image');
 assert.deepEqual(Object.fromEntries(url.searchParams),{
  sessionId:'conversation',artifactId:'generated',version:'1',messageId:'turn',sha256:'a'.repeat(64)});
 assert.equal(retainedThumbnailTuple(value,'turn',{...scope,canvasWorkspace:{views:[]}}).identity,tuple.identity,
  'Selected preview release does not revoke an independent thumbnail');
 assert.equal(retainedThumbnailTuple(value,'turn',{...scope,revision:100}).identity,tuple.identity,
  'Unrelated state/history updates do not fetch or decode again');
 value.row.versions[1].body={$resource:'different-latest'};
 assert.equal(retainedThumbnailTuple(value,'turn',scope).identity,tuple.identity);
 value.row.versions[0].body={$resource:'changed-exact-body'};
 assert.notEqual(retainedThumbnailTuple(value,'turn',scope).identity,tuple.identity);
 for(const key of ['hostInstanceId','id']){
  assert.notEqual(retainedThumbnailTuple(value,'turn',{...scope,client:{...scope.client,[key]:'other'}}).identity,tuple.identity);
 }
 for(const patch of [{selectedSessionId:'other'},{selectedWorkspaceId:'other'}])
  assert.equal(retainedThumbnailTuple(value,'turn',{...scope,...patch}),null);
 assert.equal(retainedThumbnailTuple(value,'foreign',scope),null);
 for(const chat of ['none','compact']){
  assert.equal(retainedThumbnailTuple({...value,publication:{...value.publication,chat}},'turn',scope),null);
 }
 value.publication.imageGeneration.verified=false;
 assert.equal(retainedThumbnailTuple(value,'turn',scope),null);
});

test('legacy thumbnail has no invented hash; partial or mismatched new bindings never fall back',()=>{
 const scope=thumbnailState();
 for(const change of [
  binding=>{delete binding.sha256},binding=>{binding.outputId='wrong'},
  binding=>{binding.sessionId='foreign'},binding=>{binding.outputVersion=0}
 ]){
  const value=thumbnailItem();change(value.row.versions[0].outputBinding);
  assert.equal(retainedThumbnailTuple(value,'turn',scope),null);
 }
 const legacy=thumbnailItem();delete legacy.row.versions[0].outputBinding;
 const tuple=retainedThumbnailTuple(legacy,'turn',scope);
 assert.ok(tuple);assert.equal(tuple.sha256,undefined);
 assert.equal(new URL(tuple.url,'https://fixture.example').searchParams.has('sha256'),false);
 assert.ok(!tuple.identity.includes('later-body'),'Legacy requested identity never borrows a current body');
 legacy.row.versions=[];legacy.row.revision=2;
 assert.equal(retainedThumbnailTuple(legacy,'turn',scope),null,'An unavailable old version is not Latest');
});

// Actual Thumbnail hook closures, with fake pixels and no rendered DOM. This
// deliberately does not claim browser decoding or authenticated-host acceptance.
function thumbnailHarness(decode=async()=> 'SMALL_PIXELS'){
 const source=readFileSync(new URL('../src/inline-artifacts.jsx',import.meta.url),'utf8');
 const start=source.indexOf('function Thumbnail('),end=source.indexOf(' return <div ref={root} className="a-gallery-choice"',start);
 const slots=[],effects=[],writes=[],fetches=[];let index=0;
 const button={getAttribute:()=> 'Image 1: Concept',setAttribute(){},addEventListener(){},removeEventListener(){}};
 const hooks={
  useRef(value){const at=index++;slots[at]??={current:value===undefined?{closest:()=>button}:value};return slots[at]},
  useState(value){const at=index++;slots[at]??={value};return [slots[at].value,next=>{
   slots[at].value=typeof next==='function'?next(slots[at].value):next;writes.push(at);
  }]},
  useEffect(fn,deps){const at=index++,old=slots[at];if(!old||deps.some((value,n)=>!Object.is(value,old.deps[n]))){
   const entry={deps,cleanup:old?.cleanup};slots[at]=entry;effects.push(()=>{entry.cleanup?.();entry.cleanup=fn()});
  }}
 };
 const invoke=runInNewContext(source.slice(start,end)+'return {result,retry,identity};} Thumbnail',{
  ...hooks,useLayoutEffect:hooks.useEffect,retainedThumbnailTuple,AbortController,titleOf:()=> 'Concept',
  IntersectionObserver:class{constructor(fn){this.fn=fn}observe(){this.fn([{isIntersecting:true}])}disconnect(){}},
  thumbnailWork:(_signal,work)=>Promise.resolve().then(work),
  thumbnailPixels:(tuple,signal)=>{fetches.push({tuple,signal});return decode(tuple,signal)}
 });
 return {fetches,writes,
  render(saved=thumbnailItem(),value=thumbnailState()){
   index=0;const result=invoke({item:saved,index:0,messageId:'turn',state:value});
   while(effects.length)effects.shift()();return result;
  },
  dispose(){for(const slot of slots)slot?.cleanup?.()}
 };
}

test('actual thumbnail hook keeps one read per tuple and explicitly retries a failure',async()=>{
 let reads=0;
 const harness=thumbnailHarness(async()=>{if(++reads===1)throw Error('Temporary failure');return 'SMALL_PIXELS'});
 const saved=thumbnailItem(),scope=thumbnailState();
 harness.render(saved,scope);harness.render(saved,scope);await settle();
 let rendered=harness.render(saved,scope);assert.equal(rendered.result.error,true);
 harness.render(saved,{...scope,revision:100});await settle();
 assert.equal(reads,1,'There is no automatic retry after state updates');
 rendered.retry({type:'keydown',key:'Enter',preventDefault(){},stopPropagation(){}});
 harness.render(saved,scope);await settle();
 assert.equal(harness.render(saved,scope).result.pixels,'SMALL_PIXELS');assert.equal(reads,2);
 assert.equal(harness.render(saved,{...scope,canvasWorkspace:{views:[]}}).result.pixels,'SMALL_PIXELS');
 assert.equal(harness.fetches.length,2,'View release neither clears nor refetches the thumbnail');
 harness.dispose();
});

test('actual thumbnail hook aborts and discards late fetch/decode on every owning identity change',async()=>{
 for(const mutation of ['unmount','host','client','session','publication','output','body','version']){
  const pending=[];
  const harness=thumbnailHarness((_tuple,signal)=>new Promise(resolve=>pending.push({signal,resolve})));
  const saved=thumbnailItem(),scope=thumbnailState();
  harness.render(saved,scope);harness.render(saved,scope);await settle();
  assert.equal(pending.length,1);
  if(mutation==='unmount')harness.dispose();
  else{
   const changed=structuredClone(saved),value=structuredClone(scope);
   if(mutation==='host')value.client.hostInstanceId='other-host';
   if(mutation==='client')value.client.id='other-tab';
   if(mutation==='session')value.selectedSessionId='other-chat';
   if(mutation==='publication')changed.publication.chat='none';
   if(mutation==='output')changed.publication.outputId='foreign';
   if(mutation==='body')changed.row.versions[0].body={$resource:'new-body'};
   if(mutation==='version'){changed.version=2;changed.publication.version=2;changed.publication.outputId='later-output'}
   assert.equal(harness.render(changed,value).result,null,'Derived pixels are removed during the scope-changing render');
  }
  assert.equal(pending[0].signal.aborted,true);const writes=harness.writes.length;
  pending[0].resolve('OLD_LATE_PIXELS');await settle();
  assert.equal(harness.writes.length,writes,'Late completion cannot write old pixels into the new owner');
  harness.dispose();
 }
});

test('actual thumbnail reload reauthorizes the same tuple and never reuses a disposed page result',async()=>{
 let late;
 const oldPage=thumbnailHarness(()=>new Promise(resolve=>{late=resolve}));
 oldPage.render();oldPage.render();await settle();oldPage.dispose();
 const newPage=thumbnailHarness(async()=> 'REAUTHORIZED_SMALL_PIXELS');
 newPage.render();newPage.render();await settle();
 assert.equal(newPage.fetches.length,1);
 assert.equal(newPage.render().result.pixels,'REAUTHORIZED_SMALL_PIXELS');
 late('DISPOSED_PAGE_PIXELS');await settle();
 assert.equal(newPage.render().result.pixels,'REAUTHORIZED_SMALL_PIXELS');
 assert.equal(oldPage.fetches[0].signal.aborted,true);
 newPage.dispose();
});

test('actual thumbnail slot pool bounds whole decode lifetimes and removes cancelled queued jobs',async()=>{
 const source=readFileSync(new URL('../src/inline-artifacts.jsx',import.meta.url),'utf8');
 const start=source.indexOf('const thumbnailQueue='),end=source.indexOf('async function thumbnailPixels(',start);
 const work=runInNewContext(source.slice(start,end)+'thumbnailWork',{DOMException,Promise});
 let running=0,peak=0;const complete=[];
 const controllers=Array.from({length:10},()=>new AbortController());
 const tasks=controllers.map((controller,n)=>work(controller.signal,()=>new Promise(resolve=>{
  running++;peak=Math.max(peak,running);complete.push(()=>{running--;resolve(n)});
 })).catch(error=>error.name));
 await settle();assert.equal(complete.length,2);
 controllers[7].abort();
 while(complete.length){complete.shift()();await settle()}
 const values=await Promise.all(tasks);
 assert.equal(values[7],'AbortError');assert.equal(peak,2);
 assert.equal(values.filter(value=>Number.isInteger(value)).length,9);
});

test('actual retained pixel boundary checks bytes/hash/PNG before decode and closes full-size intermediates',async()=>{
 const source=readFileSync(new URL('../src/inline-artifacts.jsx',import.meta.url),'utf8');
 const start=source.indexOf('async function thumbnailResponseBytes('),end=source.indexOf('function Thumbnail(',start);
 const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64');
 const sha256=createHash('sha256').update(png).digest('hex');
 for(const damage of [null,'absent-length','hash','absent-hash','bad-hash','tuple-hash','not-png','dimension','byte-limit','length','malformed-length','content-type','late-decode']){
  const data=Uint8Array.from(png);
  if(damage==='dimension')new DataView(data.buffer).setUint32(16,4097);
  if(damage==='not-png')data[0]=0;
  const hash=createHash('sha256').update(data).digest('hex'),controller=new AbortController();
  const canvas={width:0,height:0,getContext:()=>({drawImage(){}}),toDataURL:()=> 'DERIVED_SMALL_DATA_URI'};
  let decoded=0,closed=0,bodyReads=0,requestPath,options;
  const pixelRead=runInNewContext(source.slice(start,end)+'thumbnailPixels',{
   Uint8Array,DataView,Blob,DOMException,crypto:webcrypto,
   clientUrl:path=>path+'&clientId=attached',document:{createElement:()=>canvas},
   fetch:async(path,args)=>{requestPath=path;options=args;return {
    ok:true,headers:new Map([
     ['Content-Type',damage==='content-type'?'text/html':'image/png'],
     ['Content-Length',damage==='absent-length'?null:damage==='malformed-length'?'1e2':String(damage==='byte-limit'?8*1024*1024+1:damage==='length'?data.length+1:data.length)],
     ['X-Amplifier-Image-SHA256',damage==='hash'?'0'.repeat(64):damage==='absent-hash'?null:damage==='bad-hash'?'invalid':hash]]),
    arrayBuffer:async()=>assert.fail('Unbounded arrayBuffer must not be used'),
    body:{cancel:async()=>{},getReader(){bodyReads++;let done=false;return {
     read:async()=>{if(done)return {done:true};done=true;return {done:false,value:data}},
     cancel:async()=>{},releaseLock(){}
    }}}
   }},
   createImageBitmap:async()=>{
    decoded++;if(damage==='late-decode')controller.abort();
    return {width:1,height:1,close(){closed++}};
   }
  });
  const tuple={url:'/api/outputs/output/image?sessionId=conversation',
   sha256:damage==='tuple-hash'?'f'.repeat(64):damage==='dimension'||damage==='not-png'?hash:sha256};
  if(damage===null||damage==='absent-length'){
   assert.equal(await pixelRead(tuple,controller.signal),'DERIVED_SMALL_DATA_URI');
   assert.equal(decoded,1);assert.equal(closed,1);assert.equal(bodyReads,1);
  }else{
   await assert.rejects(pixelRead(tuple,controller.signal));
   assert.equal(decoded,damage==='late-decode'?1:0);
   assert.equal(closed,decoded);
   if(['byte-limit','content-type','absent-hash','bad-hash','malformed-length','tuple-hash'].includes(damage))assert.equal(bodyReads,0);
  }
  assert.equal(canvas.width,0);assert.equal(canvas.height,0);
  assert.ok(requestPath.endsWith('&clientId=attached'));
  assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');
 }
});

function trueLegacyItem(id='legacy'){
 const value=thumbnailItem();
 value.row.id=id;value.row.imageRequestId='candidate-hint';
 delete value.publication.outputId;delete value.publication.imageGeneration;
 delete value.row.versions[0].outputBinding;
 return value;
}
function legacyResult(item,scope){
 const candidate=legacyGenerationCandidate(item,'turn',scope);
 return {identity:candidate.identity,value:{status:'generated',sessionId:item.row.sessionId,
  artifactId:item.row.id,messageId:'turn',version:item.version,outputId:'canonical-'+item.row.id,
  outputVersion:3,sha256:'c'.repeat(64),body:candidate.saved.body,contentResource:candidate.saved.contentResource??null,
  imageGeneration:{verified:true,requestId:'verified-receipt',operation:'generate',parentId:null}}};
}

test('true legacy absence consumes only exact host metadata without changing saved publication/body evidence',()=>{
 const value=trueLegacyItem(),scope=thumbnailState(),before=structuredClone(value);
 assert.equal(retainedThumbnailTuple(value,'turn',scope),null,'The hint is not read or classification authority');
 const qualified={...value,legacyGeneration:legacyResult(value,scope)},tuple=retainedThumbnailTuple(qualified,'turn',scope);
 assert.ok(tuple);assert.equal(tuple.sha256,'c'.repeat(64));
 assert.equal(new URL(tuple.url,'https://fixture.invalid').pathname,'/api/outputs/canonical-legacy/image');
 assert.deepEqual(value,before);
 assert.equal(retainedThumbnailTuple(qualified,'turn',{...scope,revision:100}).identity,tuple.identity);
 for(const patch of [{selectedWorkspaceId:null},{selectedSessionId:'foreign'},{client:{...scope.client,id:'foreign'}}])
  assert.equal(retainedThumbnailTuple(qualified,'turn',{...scope,...patch}),null);
 for(const field of ['outputId','imageGeneration']){
  for(const explicit of [null,undefined,{},'forged']){
   const malformed={...value,publication:{...value.publication,[field]:explicit},legacyGeneration:qualified.legacyGeneration};
   assert.equal(legacyGenerationCandidate(malformed,'turn',scope),null);
   assert.equal(retainedThumbnailTuple(malformed,'turn',scope),null);
  }
 }
 const managed={...qualified,row:{...qualified.row,workspaceId:null}};
 const managedScope={...scope,selectedWorkspaceId:null};
 managed.legacyGeneration=legacyResult(managed,managedScope);
 assert.ok(retainedThumbnailTuple(managed,'turn',managedScope));
 assert.equal(retainedThumbnailTuple(managed,'turn',scope),null,'Host and client must both use null managed workspace');
 for(const field of ['outputVersion','sha256','body','version','artifactId','messageId','imageGeneration']){
  const wrong=structuredClone(qualified);
  wrong.legacyGeneration.value[field]=null;
  assert.equal(retainedThumbnailTuple(wrong,'turn',scope),null);
 }
});

function legacyHarness(dispatch){
 const source=readFileSync(new URL('../src/inline-artifacts.jsx',import.meta.url),'utf8');
 const start=source.indexOf('function useLegacyGenerations('),end=source.indexOf('export function ArtifactLinks(',start);
 const slots=[],effects=[],writes=[];let index=0;
 const hooks={
  useRef(value){const at=index++;slots[at]??={current:value};return slots[at]},
  useState(value){const at=index++;slots[at]??={value};return [slots[at].value,next=>{
   slots[at].value=typeof next==='function'?next(slots[at].value):next;writes.push(at);
  }]},
  useEffect(fn,deps){const at=index++,old=slots[at];if(!old||deps.some((value,n)=>!Object.is(value,old.deps[n]))){
   const entry={deps,cleanup:old?.cleanup};slots[at]=entry;effects.push(()=>{entry.cleanup?.();entry.cleanup=fn()});
  }}
 };
 const invoke=runInNewContext(source.slice(start,end)+'useLegacyGenerations',{
  ...hooks,AbortController,legacyGenerationCandidate,qualifiedLegacyGeneration,imageGalleryEntries,
  thumbnailWork:(signal,work)=>signal.aborted?Promise.reject(new DOMException('cancelled','AbortError')):Promise.resolve().then(work)
 });
 return {writes,render(items=[trueLegacyItem()],scope=thumbnailState()){
  index=0;const result=invoke(items,'turn',scope,dispatch);while(effects.length)effects.shift()();return result;
 },dispose(){for(const slot of slots)slot?.cleanup?.()}};
}

test('displayed legacy consumer has visible pending/error, explicit retry and no unrelated-state requery',async()=>{
 const value=trueLegacyItem(),scope=thumbnailState(),calls=[];let fail=true;
 const harness=legacyHarness(async(action,args)=>{
  calls.push({action,args});if(fail)throw Error('Qualification temporarily unavailable');
  return {accepted:true,result:{legacyGeneration:legacyResult(value,scope).value}};
 });
 assert.equal(harness.render([value],scope).pending.length,1);await settle();
 let result=harness.render([value],scope);
 assert.match(result.pending[0].error,/temporarily unavailable/);
 harness.render([value],{...scope,revision:99});await settle();assert.equal(calls.length,1);
 fail=false;result.retry(result.pending[0].identity);harness.render([value],scope);await settle();
 result=harness.render([value],scope);assert.equal(result.pending.length,0);
 assert.ok(retainedThumbnailTuple(result.rows[0],'turn',scope));
 harness.render([structuredClone(value)],{...scope,revision:100});await settle();
 assert.equal(calls.length,2,'Equal source facts and unrelated state do not requery');
 assert.ok(calls.every(call=>call.action==='canvas.versions.inspect'&&call.args.qualifyLegacyGeneration===true&&!call.args.includeSource));
 harness.dispose();
});

test('late legacy qualification after scope/source/unlink/suppression changes is discarded mount-locally',async()=>{
 for(const change of ['host','client','session','workspace','body','publication','unlink','unmount']){
  const value=trueLegacyItem(),scope=thumbnailState();let finish;
  const harness=legacyHarness(()=>new Promise(resolve=>{finish=resolve}));
  harness.render([value],scope);await settle();
  const modified=structuredClone(value),next=structuredClone(scope);
  if(change==='host')next.client.hostInstanceId='other';
  if(change==='client')next.client.id='other';
  if(change==='session')next.selectedSessionId='other';
  if(change==='workspace')next.selectedWorkspaceId='other';
  if(change==='body')modified.row.versions[0].body={$resource:'different'};
  if(change==='publication')modified.publication.chat='none';
  if(change==='unmount')harness.dispose();
  else harness.render(change==='unlink'?[]:[modified],next);
  const writes=harness.writes.length;
  finish({accepted:true,result:{legacyGeneration:legacyResult(value,scope).value}});await settle();
  assert.equal(harness.writes.length,writes,'Stale inspection cannot revive gallery metadata');
  harness.dispose();
 }
});

test('streaming reader cancels overflow/abort and enforces safe optional length without arrayBuffer',async()=>{
 const source=readFileSync(new URL('../src/inline-artifacts.jsx',import.meta.url),'utf8');
 const start=source.indexOf('async function thumbnailResponseBytes('),end=source.indexOf('async function thumbnailPixels(',start);
 const read=runInNewContext(source.slice(start,end)+'thumbnailResponseBytes',{Uint8Array,DOMException});
 for(const mode of ['absent','valid','malformed','unsafe','lying-short','truncated','overflow','abort']){
  const controller=new AbortController();let reads=0,cancelled=0,released=0;
  const chunks=mode==='overflow'?[new Uint8Array(8*1024*1024),new Uint8Array(1),new Uint8Array(100)]:
   [new Uint8Array(30),new Uint8Array(30)];
  const length={valid:'60',malformed:'60garbage',unsafe:'9007199254740992','lying-short':'45',truncated:'61'}[mode]??null;
  const response={headers:new Map([['Content-Length',length]]),arrayBuffer:()=>assert.fail('Unbounded read'),
   body:{getReader:()=>({read:async()=>{
    reads++;if(mode==='abort'&&reads===1)controller.abort();
    return chunks.length?{done:false,value:chunks.shift()}:{done:true};
   },cancel:async()=>{cancelled++},releaseLock(){released++}})}};
  if(['absent','valid'].includes(mode))assert.equal((await read(response,controller.signal)).byteLength,60);
  else await assert.rejects(read(response,controller.signal));
  if(['malformed','unsafe'].includes(mode)){assert.equal(reads,0);assert.equal(released,0)}
  else assert.equal(released,1);
  if(['overflow','abort','lying-short'].includes(mode))assert.ok(cancelled>0);
  if(mode==='overflow')assert.equal(reads,2,'Overflow cancels before another chunk is consumed');
 }
});