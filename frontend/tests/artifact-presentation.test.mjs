import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {artifactPublications,artifactOverrideKey,publicationMode,presentationCapabilities,canvasOpenCommand,canvasOpenNotice,publicationOutputIds} from '../src/artifact-presentation.js';
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
  value.row.presentationCapabilities={compact:true,inline:true,canvas:true};
  value.row.capabilities=['arbitrary.active.grant'];
  assert.equal(presentationCapabilities(value).inline,false,'An advanced native lifecycle is still pending');
  assert.match(presentationCapabilities(value).reason,/qualification is pending/);
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
 return {mode,loaded,revoked,refusal,notice,override,busy,root,owned,opening,changeMode,open};}\nGeneralArtifact`,{
  ...hooks,artifactOverrideKey,publicationMode,presentationCapabilities,canvasOpenCommand,canvasOpenNotice,overrides,inlineOwnership:createInlineOwnership(),inlineEditNotice,
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
 harness.dispose();await settle();const writes=harness.writes.length;
 finish({id:'saved-artifact',kind:'image'});await settle();
 assert.equal(harness.writes.length,writes);
 assert.equal(calls.filter(row=>row.action==='canvas.views.inline').length,1);
 assert.equal(calls.filter(row=>row.action==='canvas.views.release').length,1);
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
  useEffect:fn=>fn(),imageJobLabel:()=>'',titleOf:()=>''
 });
 const a={id:'request:a',item:item()},b={id:'request:b',item:item()};
 assert.equal(render({entries:[a,b]}).entry,a);
 assert.equal(render({entries:[b,a]}).entry,a,'Pruning/reorder cannot change the initially shown output');
 render({entries:[b,a]}).setSelected(b.id);
 assert.equal(render({entries:[a,b]}).entry,b,'Explicit selection remains on the same output slot');
 assert.equal(render({entries:[a]}).entry,a,'Actual removal permits fallback');
});