import {test} from 'node:test';
import assert from 'node:assert/strict';
import {composeCapabilities} from '../src/capabilities.js';
test('composition forwards only declared read-only ephemeral observations',async()=>{
 const owner={manifest:{version:1,topics:{outputs:{version:1}},actions:{'outputs.mediaReport':{topic:'outputs',operation:'outputs.mediaReport'}}},quiescenceAccess:{'outputs.mediaReport':'read'},ephemeralActions:{'outputs.mediaReport':true},action:async(_request,context)=>({result:{origin:context.origin,account:context.account}})};
 const capability=composeCapabilities([owner],{account:'selected'});
 assert.deepEqual(capability.ephemeralActions,{'outputs.mediaReport':true});
 assert.deepEqual(await capability.action({topic:'outputs',operation:'outputs.mediaReport'},{origin:'ui'}),{result:{origin:'ui',account:'selected'}});
 for(const invalid of [{quiescenceAccess:{}},{ephemeralActions:{missing:true}},{ephemeralActions:{'outputs.mediaReport':false}}])assert.throws(()=>composeCapabilities([{...owner,...invalid}]),/Invalid ephemeral/);
});
