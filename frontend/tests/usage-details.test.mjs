import {test} from 'node:test';
import assert from 'node:assert/strict';
import {usageMetrics,cachedPercent,compactBreakdown} from '../src/usage-details.js';
import {segmentUsage,combinedWork} from '../src/timeline-data.js';

test('CLI-compatible totals include writes once and never re-add cache reads or reasoning',()=>{
 const usage={inputTokens:95000,cacheReadTokens:80000,cacheWriteTokens:5000,outputTokens:1000,reasoningTokens:400,costUsd:1.62,costType:'reported'};
 const metrics=usageMetrics(usage);
 assert.equal(metrics.grossInputTokens.value,100000);assert.equal(metrics.grossTotalTokens.value,101000);
 assert.equal(cachedPercent(metrics),'80%');assert.equal(metrics.reasoningTokens.value,400);
 assert.equal(compactBreakdown(usage),'In 100k (80% cached) · Out 1k');
});
test('missing usage is inspectable',()=>{assert.equal(usageMetrics(null).grossTotalTokens.value,null)});

test('zero, unavailable, pending and mismatched coverage do not invent cache percentages',()=>{
 assert.equal(cachedPercent(usageMetrics({inputTokens:10,cacheReadTokens:0})),'0%');
 assert.equal(cachedPercent(usageMetrics({inputTokens:0,cacheReadTokens:0})),null);
 assert.equal(cachedPercent(usageMetrics({inputTokens:10})),null);
 assert.equal(cachedPercent(usageMetrics({inputTokens:10,cacheReadTokens:11})),null);
 assert.equal(cachedPercent(usageMetrics({inputTokens:1000,cacheReadTokens:1})),'<1%');
 const partial={inputTokens:10,cacheReadTokens:2,calls:2,metricKnownCalls:{inputTokens:2,cacheReadTokens:1}};
 assert.equal(cachedPercent(usageMetrics(partial)),null);
 assert.equal(usageMetrics({costUsd:0,costType:'unavailable'}).costUsd.value,null);
 assert.equal(usageMetrics({costUsd:0,costType:'reported'}).costUsd.value,0);
});
test('segmented and combined work carry metric coverage and reasoning without inventing zero',()=>{
 const one=segmentUsage([{kind:'llm',usage:{inputTokens:10,outputTokens:2,cacheReadTokens:5,reasoningTokens:1}}]);
 const two=segmentUsage([{kind:'llm',usage:{inputTokens:30,outputTokens:3}}]);
 const merged=combinedWork([{aggregateUsage:one},{aggregateUsage:two}],100).usage,metrics=usageMetrics(merged);
 assert.equal(metrics.grossTotalTokens.value,45);assert.equal(metrics.reasoningTokens.value,1);
 assert.equal(metrics.reasoningTokens.status,'partial');assert.equal(cachedPercent(metrics),null);
 assert.equal(usageMetrics(two).cacheReadTokens.value,null);
});

test('cache writes use the same input denominator and suppress incomplete percentages',()=>{
 assert.equal(cachedPercent(usageMetrics({inputTokens:95,cacheWriteTokens:5}),'cacheWriteTokens'),'5%');
 assert.equal(cachedPercent(usageMetrics({inputTokens:95,cacheWriteTokens:0}),'cacheWriteTokens'),'0%');
 assert.equal(cachedPercent(usageMetrics({inputTokens:95}),'cacheWriteTokens'),null);
 assert.equal(cachedPercent(usageMetrics({inputTokens:95,cacheWriteTokens:5,calls:2,metricKnownCalls:{inputTokens:2,cacheWriteTokens:1}}),'cacheWriteTokens'),null);
});
