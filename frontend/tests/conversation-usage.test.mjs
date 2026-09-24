import {test} from 'node:test';
import assert from 'node:assert/strict';
import {usageMetric,usageExport} from '../src/conversation-usage.js';
test('missing, partial, pending and estimated usage remain distinct from zero',()=>{
 assert.equal(usageMetric({value:null,status:'empty'}),'No recorded calls');
 assert.equal(usageMetric({value:null,status:'pending'}),'Pending');
 assert.equal(usageMetric({value:null,status:'unknown'}),'Unavailable');
 assert.equal(usageMetric({value:0,status:'known'},true),'$0');
 assert.match(usageMetric({value:1.25,estimatedCalls:1,pendingCalls:2,unknownCalls:3},true),/^\$1.25 · includes estimates · 2 pending · 3 unavailable$/);
});
test('copy contains the complete aggregate and coverage, not only paginated calls or budget settings',()=>{
 const snapshot={observedAt:5,budget:{enabled:true},usage:{sessionId:'s',calls:100,metrics:{grossTotalTokens:{value:500}},providers:[],coverage:'observed only',receipts:[{id:'one'}],scope:'root and descendants',source:'receipts',excludedUnboundCalls:2}};
 const result=usageExport(snapshot);
 assert.equal(result.calls,100);assert.equal(result.metrics.grossTotalTokens.value,500);assert.equal(result.coverage,'observed only');assert.equal(result.excludedUnboundCalls,2);
 assert.ok(!('receipts' in result));assert.ok(!('budget' in result));
});
