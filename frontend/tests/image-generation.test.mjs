import test from 'node:test';
import assert from 'node:assert/strict';
import {imageJobs,imageJobLabel} from '../src/image-generation.js';

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
