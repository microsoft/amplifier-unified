import test from 'node:test';
import assert from 'node:assert/strict';
import {imageJobs,imageJobLabel,imageGalleryEntries,imageGallerySelection} from '../src/image-generation.js';
import {legacyGenerationCandidate,retainedThumbnailTuple} from '../src/artifact-presentation.js';

test('saved results replace only matching image work in the same chat and turn',()=>{
 const jobs=[{id:'a',messageId:'origin',requestId:'a',phase:'running',operation:'generate'},
  {id:'b',messageId:'origin',requestId:'b',phase:'running',operation:'edit'},
  {id:'c',messageId:'later',requestId:'c',phase:'running',operation:'generate'}];
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:jobs}}],canvasArtifacts:[
  {kind:'image',sessionId:'s',messageId:'origin',imageRequestId:'a'},
  {sessionId:'other',messageId:'origin',imageRequestId:'b'},
  {sessionId:'s',messageId:'later',imageRequestId:'b'}]};
 assert.deepEqual(imageJobs(state,'origin').map(row=>row.id),['b']);
 assert.equal(imageJobLabel(imageJobs(state,'origin')[0]),'Editing image…');
 assert.deepEqual(imageJobs({...state,selectedSessionId:'missing'},'origin'),[]);
});

test('stopped and unknown work do not imply ongoing progress or automatic retry',()=>{
 assert.equal(imageJobLabel({phase:'completed'}),'Image generated');
 assert.equal(imageJobLabel({phase:'error'}),'Image generation did not finish');
 for(const phase of ['unknown','interrupted'])assert.equal(imageJobLabel({phase}),'Image generation stopped; check activity for its outcome');
});


test('gallery retains request order and selection identity through out-of-order receipts',()=>{
 const jobs=['a','b','c'].map(id=>({id,requestId:id,messageId:'origin',phase:'running'}));
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:jobs}}]};
 const image=id=>({row:{id:'image-'+id,kind:'image',sessionId:'s',imageRequestId:id},version:1});
 const second=image('b'),first=image('a');
 const pending=imageGalleryEntries(state,'origin',[]);
 const mixed=imageGalleryEntries(state,'origin',[second]);
 assert.equal(mixed[0].id,pending[0].id);
 assert.equal(imageGallerySelection(mixed,pending[1].id).item,second,'The pending selection reconciles explicitly to its resolved output');
 assert.equal(mixed[1].id,'artifact:image-b:1');
 assert.equal(mixed[0].item,undefined);assert.equal(mixed[1].item,second);
 const finished=imageGalleryEntries(state,'origin',[second,first]);
 assert.equal(finished[0].item,first);assert.equal(finished[1].item,second);
 assert.deepEqual(imageGalleryEntries(state,'later',[]),[]);
 const revised={...first,version:2};
 assert.equal(imageGalleryEntries(state,'origin',[first,revised]).length,3,'An ordinary later revision does not inherit the initial generation marker');
 assert.deepEqual(imageGalleryEntries({...state,selectedSessionId:'other'},'origin',[first]),[],'An unmatched request ID is not verified generation evidence');
});


test('correcting a rejected image request keeps one slot and the completed result',()=>{
 const rejected={id:'first',requestId:'same-effect',messageId:'origin',operation:'generate',phase:'error'};
 const completed={...rejected,id:'second',phase:'completed'};
 const state=jobs=>({selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:jobs}}]});
 const item={row:{id:'saved',kind:'image',sessionId:'s',imageRequestId:'same-effect'},version:1};
 const before=imageGalleryEntries(state([rejected]),'origin',[]);
 const after=imageGalleryEntries(state([rejected,completed]),'origin',[item]);
 assert.equal(after.length,1);
 assert.equal(imageGallerySelection(after,before[0].id).id,after[0].id);
 assert.equal(after[0].id,'artifact:saved:1');
 assert.equal(after[0].job.phase,'completed');
 assert.equal(after[0].item,item);
 assert.equal(imageGalleryEntries(state([rejected,{...completed,requestId:'another'}]),'origin',[]).length,2);
});

test('ordinary PNG and arbitrary unmatched request IDs never enter the generation gallery',()=>{
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:[]}}]};
 const ordinary={row:{id:'screenshot',kind:'image',sessionId:'s'},version:1};
 const unmatched={row:{...ordinary.row,id:'plot',imageRequestId:'not-a-host-job'},version:1};
 assert.deepEqual(imageGalleryEntries(state,'origin',[ordinary,unmatched]),[]);
 const pending={...state,sessions:[{id:'s',execution:{imageGeneration:[{id:'work',requestId:'verified',messageId:'origin',operation:'generate',phase:'running'}]}}]};
 const entries=imageGalleryEntries(pending,'origin',[ordinary,unmatched]);
 assert.equal(entries.length,1);assert.equal(entries[0].item,undefined);
 const wrongOwner={row:{id:'wrong',kind:'image',sessionId:'another-session',imageRequestId:'verified'},version:1};
 assert.equal(imageGalleryEntries(pending,'origin',[wrongOwner])[0].item,undefined);
});

test('completed edit and variation receipts retain stable slots and all exact versions',()=>{
 const jobs=[{id:'generate',requestId:'original',messageId:'origin',operation:'generate',phase:'completed'},
  {id:'edit',requestId:'edited',messageId:'origin',operation:'edit',phase:'running'}];
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:jobs}}]};
 const original={row:{id:'image',kind:'image',sessionId:'s',imageRequestId:'original'},version:1};
 const edited={row:{id:'variant',kind:'image',sessionId:'s',imageRequestId:'edited'},version:1};
 const before=imageGalleryEntries(state,'origin',[original]);
 assert.equal(imageJobLabel(before[1].job),'Editing image…');
 const completed={...state,sessions:[{id:'s',execution:{imageGeneration:[jobs[0],{...jobs[1],phase:'completed'}]}}]};
 const after=imageGalleryEntries(completed,'origin',[edited,original,retained('variant','edited',2,'edit')]);
 assert.equal(after[0].id,before[0].id);
 assert.equal(imageGallerySelection(after,before[1].id).item,edited);
 assert.equal(after[1].item,edited);assert.equal(after[2].item.version,2);
 for(const chat of ['none','compact']){
  const hidden={...edited,publication:{chat}};
  assert.equal(imageGalleryEntries(completed,'origin',[original,hidden]).length,1,'A saved non-inline publication does not leave a false pending gallery slot');
 }
});

function retained(id,requestId,version=1,operation='generate',parentId=null,chat='inline'){
 return {row:{id,kind:'image',sessionId:'s',imageRequestId:requestId},version,
  publication:{messageId:'origin',version,chat,outputId:'output-'+id+'-'+version,
   imageGeneration:{verified:true,requestId,operation,parentId}}};
}

test('trusted exact publications retain generation and edit identity beyond 32 jobs, no jobs and reload',()=>{
 const original=retained('original','generated'),edited=retained('edited','edit-request',1,'edit','original');
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:[
  {id:'job',requestId:'generated',messageId:'origin',operation:'generate',phase:'completed'},
  {id:'edit',requestId:'edit-request',messageId:'origin',operation:'edit',phase:'completed'}
 ]}}]};
 const before=imageGalleryEntries(state,'origin',[original,edited]);
 const pruned={...state,sessions:[{id:'s',execution:{imageGeneration:Array.from({length:32},(_,n)=>({id:'new-'+n,requestId:'new-'+n,messageId:'later',operation:'generate',phase:'completed'}))}}]};
 const after=imageGalleryEntries(pruned,'origin',[original,edited]);
 assert.deepEqual(after.map(entry=>entry.id),before.map(entry=>entry.id));
 assert.equal(after[0].item,original);assert.equal(after[1].item,edited);
 const reloaded=JSON.parse(JSON.stringify({...pruned,sessions:[{id:'s',execution:{imageGeneration:[]}}]}));
 assert.deepEqual(imageGalleryEntries(reloaded,'origin',[original,edited]).map(entry=>entry.id),before.map(entry=>entry.id));
});

test('one request with multiple outputs and an edit has distinct stable slots through pruning',()=>{
 const items=[retained('a','generate'),retained('b','generate'),retained('c','edit',1,'edit','a')];
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:[
  {id:'j1',requestId:'generate',messageId:'origin',operation:'generate'},
  {id:'j2',requestId:'edit',messageId:'origin',operation:'edit'}
 ]}}]};
 const before=imageGalleryEntries(state,'origin',items),pruned={...state,sessions:[{id:'s'}]};
 const after=imageGalleryEntries(pruned,'origin',items);
 assert.equal(new Set(before.map(entry=>entry.id)).size,3);
 for(const item of items)assert.equal(after.find(entry=>entry.item===item).id,before.find(entry=>entry.item===item).id);
 for(const chat of ['compact','none']){
  const hidden=items.map(item=>({...item,publication:{...item.publication,chat}}));
  assert.deepEqual(imageGalleryEntries(state,'origin',hidden),[]);
 }
});

test('selected B is never reassigned when sibling A alone becomes compact or hidden with unrelated C first',()=>{
 const c=retained('c','unrelated'),a=retained('a','shared'),b=retained('b','shared');
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:[
  {id:'job-c',requestId:'unrelated',messageId:'origin',phase:'completed'},
  {id:'job-ab',requestId:'shared',messageId:'origin',phase:'completed'}
 ]}}]};
 const before=imageGalleryEntries(state,'origin',[c,a,b]),selected=before.find(entry=>entry.item===b).id;
 assert.equal(selected,'artifact:b:1');
 for(const chat of ['compact','none']){
  const items=[c,{...a,publication:{...a.publication,chat}},b];
  for(const next of [state,{...state,sessions:[{id:'s'}]},JSON.parse(JSON.stringify({...state,sessions:[{id:'s'}]}))]){
   const entries=imageGalleryEntries(next,'origin',items);
   assert.equal(entries[0].item,c,'The unrelated first entry makes accidental fallback observable');
   assert.equal(imageGallerySelection(entries,selected).item,b);
   assert.equal(entries.find(entry=>entry.item===b).id,selected);
  }
 }
 const revised=retained('b','shared',2);
 const versions=imageGalleryEntries(state,'origin',[c,a,b,revised]);
 assert.equal(imageGallerySelection(versions,selected).item,b);
 assert.notEqual(versions.find(entry=>entry.item===revised).id,selected);
 const pending=imageGalleryEntries(state,'origin',[]);
 assert.equal(imageGallerySelection(before,pending[1].id).item,a,'Only a progress placeholder aliases the first resolved output');
});

test('bare forgery, mismatched owner/output/publication version and ordinary new revision cannot classify generation',()=>{
 const valid=retained('a','request',1),state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:[{id:'j',requestId:'request',messageId:'origin',operation:'generate'}]}}]};
 for(const item of [
  {...valid,row:{...valid.row,sessionId:'other'}},
  {...valid,version:2},
  {...valid,publication:{...valid.publication,messageId:'other'}},
  {...valid,publication:{...valid.publication,outputId:null}},
  {...valid,publication:{...valid.publication,imageGeneration:{...valid.publication.imageGeneration,verified:false}}},
  {...valid,version:2,publication:{messageId:'origin',version:2,outputId:'ordinary',chat:'inline'}},
  {row:{id:'forged',kind:'image',mimeType:'image/png',sessionId:'s',imageRequestId:'unmatched'},version:1}
 ]){
  assert.equal(imageGalleryEntries(state,'origin',[item]).filter(entry=>entry.item).length,0);
 }
 assert.equal(imageGalleryEntries({...state,sessions:[{id:'s'}]},'origin',[{row:{...valid.row},version:1}]).length,0);
 assert.equal(imageGalleryEntries(state,'origin',[valid])[0].item,valid);
});

test('two true legacy variants with jobs empty retain distinct selection only after exact metadata qualification',()=>{
 const state={selectedSessionId:'s',selectedWorkspaceId:null,client:{hostInstanceId:'host',id:'tab'},sessions:[{id:'s',execution:{imageGeneration:[]}}]};
 const legacy=id=>({row:{id,kind:'image',sessionId:'s',workspaceId:null,imageRequestId:'same-request',revision:2,
  versions:[{version:1,kind:'image',body:{$resource:'original-'+id}},{version:2,kind:'image',body:{$resource:'ordinary-'+id}}]},
  version:1,publication:{messageId:'origin',version:1}});
 const originals=[legacy('a'),legacy('b')],before=structuredClone(originals);
 assert.deepEqual(imageGalleryEntries(state,'origin',originals),[],'First-version hint without jobs is never authority');
 const qualify=(item,scope)=>({...item,legacyGeneration:{identity:legacyGenerationCandidate(item,'origin',scope).identity,value:{
  status:'generated',sessionId:'s',artifactId:item.row.id,messageId:'origin',version:1,outputId:'out-'+item.row.id,
  outputVersion:1,sha256:'a'.repeat(64),body:item.row.versions[0].body,contentResource:null,
  imageGeneration:{verified:true,requestId:'same-request',operation:'generate',parentId:null}
 }}});
 const items=originals.map(item=>qualify(item,state)),entries=imageGalleryEntries(state,'origin',items);
 assert.equal(entries.length,2);assert.equal(new Set(entries.map(entry=>entry.id)).size,2);
 const selected=entries[1].id;
 assert.equal(imageGallerySelection(imageGalleryEntries(state,'origin',items.slice(1)),selected).item.row.id,'b');
 const reloaded=JSON.parse(JSON.stringify(state)),newMount=originals.map(item=>qualify(item,reloaded));
 assert.deepEqual(imageGalleryEntries(reloaded,'origin',newMount).map(entry=>entry.id),entries.map(entry=>entry.id));
 assert.ok(newMount.every(item=>retainedThumbnailTuple(item,'origin',reloaded)));
 assert.deepEqual(originals,before,'Qualification does not rewrite immutable IDs/body/version or publication');
 const negative={...items[0],legacyGeneration:{...items[0].legacyGeneration,value:{status:'not-generated'}}};
 const rememberedJob={...state,sessions:[{id:'s',execution:{imageGeneration:[{requestId:'same-request',messageId:'origin'}]}}]};
 assert.equal(imageGalleryEntries(rememberedJob,'origin',[negative]).filter(entry=>entry.item).length,0,
  'A canonical negative cannot be overridden by the first-version candidate hint/recent job');
 const later={...items[1],version:2,publication:{messageId:'origin',version:2}};
 assert.equal(imageGalleryEntries(state,'origin',[later]).length,0);
 for(const field of ['outputId','imageGeneration'])for(const value of [null,undefined,{},'forged']){
  const partial={...items[0],publication:{...items[0].publication,[field]:value}};
  const jobs={...state,sessions:[{id:'s',execution:{imageGeneration:[{requestId:'same-request',messageId:'origin'}]}}]};
  assert.equal(imageGalleryEntries(jobs,'origin',[partial]).filter(entry=>entry.item).length,0,'Explicit malformed fields never use job or legacy fallback');
 }
});
