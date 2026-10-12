import test from 'node:test';
import assert from 'node:assert/strict';
import {PresentationResets} from '../dist/presentation.js';
const legacy={enabled:true,ready:true,revision:4,source:'host:fixture:conversation-presentation-v1',projectionRevision:4,phase:'ready'};
const identity='1b9dd372-a775-4653-b052-e63a986d2113';
const inspect=value=>new PresentationResets({inspectConversationPresentation:async()=>value}).inspect();
test('readiness retains the optional exact catalog identity and accepts old peers',async()=>{
 for(const value of [legacy,{...legacy,acknowledgedIndexIncarnation:identity},{...legacy,ready:false,phase:'checking',reason:'catalog-unavailable',acknowledgedIndexIncarnation:identity}]){
  const result=await inspect(value);assert.deepEqual(result,value);assert.notEqual(result,value);
 }
});
test('malformed catalog identity and unknown fields remain unconfirmed',async()=>{
 for(const value of [null,12,{},'',identity+'\n','replacement'])await assert.rejects(inspect({...legacy,acknowledgedIndexIncarnation:value}),/Invalid host presentation readiness/);
 await assert.rejects(inspect({...legacy,uncheckedField:true}),/Invalid host presentation readiness/);
});
