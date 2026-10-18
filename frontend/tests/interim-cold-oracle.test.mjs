import test from 'node:test';
import assert from 'node:assert/strict';
import {coldBody,coldOrder,assertColdOrder,assertColdBody} from './interim-cold-oracle.mjs';

test('cold browser oracle accepts complete independently named call bodies and order',()=>{
 assertColdOrder(coldOrder());
 for(const index of [1,2])assertColdBody(coldBody(index),index,'positive');
});
test('cold browser oracle rejects preview-only output even with both boundary markers',()=>{
 for(const index of [1,2]){
  const preview=coldBody(index).slice(0,2000)+`\nCOLD-BODY-${index}-END`;
  assert.throws(()=>assertColdBody(preview,index,'preview'),assert.AssertionError);
 }
});
test('cold browser oracle rejects the other call body, with unchanged kind counts',()=>{
 assert.throws(()=>assertColdBody(coldBody(2),1,'wrong pair'),assert.AssertionError);
 assert.throws(()=>assertColdBody(coldBody(1),2,'wrong pair'),assert.AssertionError);
});
test('cold browser oracle rejects swapped pairs and a swapped model alone',()=>{
 const pairs=coldOrder();[pairs[1],pairs[4]]=[pairs[4],pairs[1]];[pairs[2],pairs[5]]=[pairs[5],pairs[2]];
 assert.throws(()=>assertColdOrder(pairs),assert.AssertionError);
 const models=coldOrder();[models[2],models[5]]=[models[5],models[2]];
 assert.throws(()=>assertColdOrder(models),assert.AssertionError);
});
test('cold browser oracle rejects work before progress and missing or duplicate identities',()=>{
 const early=coldOrder();[early[0],early[1]]=[early[1],early[0]];
 assert.throws(()=>assertColdOrder(early),assert.AssertionError);
 const duplicate=coldOrder();duplicate[4]={...duplicate[1]};
 assert.throws(()=>assertColdOrder(duplicate),assert.AssertionError);
 const missing=coldOrder();delete missing[1].callId;
 assert.throws(()=>assertColdOrder(missing),assert.AssertionError);
});