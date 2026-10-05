import test from 'node:test';
import assert from 'node:assert/strict';
import {createFeedbackCapability} from '../src/index.js';
test('missing agent publication policy returns a useful no-effect result without starting the owner',async()=>{
 const cap=createFeedbackCapability({owner:{command:'/must-not-start'},uploadOwner:{resourceProviders:[{scheme:'amplifier-attachment'}],close:async()=>{}},inspectSession:async()=>{throw Error('Unexpected read')}});
 try {
  for(const operation of ['feedback.submit','feedback.comment','feedback.update','feedback.close','feedback.reopen','feedback.excerpt.stage']){
   const result=await cap.action({version:1,topic:'feedback',operation,args:{requestId:'exact-request'}},{origin:'agent'});
   assert.equal(result.accepted,false);assert.equal(result.result.executed,false);assert.equal(result.result.status,'authorization_required');assert.equal(result.result.requestId,'exact-request');
   assert.match(result.result.error.message,/No report was submitted/);
  }
 }finally{await cap.close();}
});
