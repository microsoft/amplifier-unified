import test from 'node:test';
import assert from 'node:assert/strict';
import {createFeedbackCapability} from '../src/index.js';
import {Connection} from '../src/connection.js';
test('missing agent publication policy returns a useful no-effect result without starting the owner',async()=>{
 const cap=createFeedbackCapability({owner:{command:'/must-not-start'},uploadOwner:{resourceProviders:[{scheme:'amplifier-attachment'}],close:async()=>{}},inspectSession:async()=>{throw Error('Unexpected read')}});
 try {
  for(const operation of ['feedback.submit','feedback.comment','feedback.update','feedback.close','feedback.reopen','feedback.excerpt.stage','feedback.attachments.add']){
   const result=await cap.action({version:1,topic:'feedback',operation,args:{requestId:'exact-request'}},{origin:'agent'});
   assert.equal(result.accepted,false);assert.equal(result.result.executed,false);assert.equal(result.result.status,'authorization_required');assert.equal(result.result.requestId,'exact-request');
   assert.match(result.result.error.message,/No report was submitted/);
  }
 }finally{await cap.close();}
});

test('agent addition approval displays the authoritative file manifest and destination',async t=>{
 const args={requestId:'addition',feedbackId:'report',reviewRequestId:'review',confirmedFiles:[{id:'file',sha256:'a'.repeat(64)}]},review={repository:'example/reports',repositoryId:42,visibility:'public',manifest:[{id:'file',name:'reviewed.txt',sha256:'a'.repeat(64),size:12}],disclosure:'Public: anyone can read these files.'},calls=[];
 t.mock.method(Connection.prototype,'request',async(method,payload)=>{calls.push(payload);if(payload.operation==='feedback.receipt')return {operation:'feedback.attachments.review',status:'completed',feedbackId:'report',review};return {status:'completed'};});
 const cap=createFeedbackCapability({owner:{},uploadOwner:{resourceProviders:[{scheme:'amplifier-attachment'}],close:async()=>{}},authorizeFeedback:async request=>{assert.deepEqual(request.args,{...args,review});assert.equal(calls.length,1);}});
 try{await cap.action({version:1,topic:'feedback',operation:'feedback.attachments.add',args},{origin:'agent'});assert.deepEqual(calls.map(row=>row.operation),['feedback.receipt','feedback.attachments.add']);assert.deepEqual(calls[1].args,args);}finally{await cap.close()}
});
