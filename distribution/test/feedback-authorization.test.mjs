import {test} from 'node:test';
import assert from 'node:assert/strict';
import {feedbackPublicationAuthorization} from '../src/feedback.js';
test('installed feedback policy binds each proposed remote write to its authenticated originating conversation',async()=>{
 const calls=[],authorize=feedbackPublicationAuthorization({account:'owner',confirmCapability:async(...args)=>calls.push(args)});
 const context={account:'owner',origin:'agent',session:{uri:'ahp-session:/origin'}},args={requestId:'reviewed',title:'Report',body:'Exact proposed report'};
 for(const operation of ['feedback.submit','feedback.comment','feedback.update','feedback.close','feedback.reopen','feedback.excerpt.stage'])await authorize({operation,args,context});
 assert.equal(calls.length,6);assert.ok(calls.every(row=>row[0]==='ahp-session:/origin'&&row[1].args===args));assert.equal(calls[0][1].title,'Send this feedback report?');
 for(const caller of [{...context,account:'foreign'},{...context,origin:'ui'},{...context,session:undefined}])await assert.rejects(authorize({operation:'feedback.submit',args,context:caller}),/authenticated originating/);
 await assert.rejects(authorize({operation:'unadvertised',args,context}),/Unsupported/);assert.equal(calls.length,6);
 assert.equal(feedbackPublicationAuthorization({account:'owner'}),undefined);
});
test('rejected review cannot fall through to publication authorization',async()=>{
 const authorize=feedbackPublicationAuthorization({account:'owner',confirmCapability:async()=>{throw Error('Rejected by user');}});
 await assert.rejects(authorize({operation:'feedback.submit',args:{requestId:'once'},context:{account:'owner',origin:'agent',session:'ahp-session:/origin'}}),/Rejected by user/);
});
