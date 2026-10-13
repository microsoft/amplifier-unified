import test from 'node:test';
import assert from 'node:assert/strict';
import {imageJobs,imageJobLabel,imageGalleryEntries} from '../src/image-generation.js';

test('saved results replace only matching image work in the same chat and turn',()=>{
 const jobs=[{id:'a',messageId:'origin',requestId:'a',phase:'running',operation:'generate'},
  {id:'b',messageId:'origin',requestId:'b',phase:'running',operation:'edit'},
  {id:'c',messageId:'later',requestId:'c',phase:'running',operation:'generate'}];
 const state={selectedSessionId:'s',sessions:[{id:'s',execution:{imageGeneration:jobs}}],canvasArtifacts:[
  {sessionId:'s',messageId:'origin',imageRequestId:'a'},
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
 assert.deepEqual(mixed.map(entry=>entry.id),pending.map(entry=>entry.id));
 assert.equal(mixed[0].item,undefined);assert.equal(mixed[1].item,second);
 const finished=imageGalleryEntries(state,'origin',[second,first]);
 assert.equal(finished[0].item,first);assert.equal(finished[1].item,second);
 assert.deepEqual(imageGalleryEntries(state,'later',[]),[]);
 const revised={...first,version:2};
 assert.equal(imageGalleryEntries(state,'origin',[first,revised]).length,4,'Additional versions are preserved');
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
 assert.equal(after[0].id,before[0].id);
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
 const after=imageGalleryEntries(completed,'origin',[edited,original,{...edited,version:2}]);
 assert.deepEqual(after.slice(0,2).map(row=>row.id),before.map(row=>row.id));
 assert.equal(after[1].item,edited);assert.equal(after[2].item.version,2);
 for(const chat of ['none','compact']){
  const hidden={...edited,publication:{chat}};
  assert.equal(imageGalleryEntries(completed,'origin',[original,hidden]).length,1,'A saved non-inline publication does not leave a false pending gallery slot');
 }
});
