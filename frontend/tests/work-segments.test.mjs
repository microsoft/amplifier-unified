import test from 'node:test';
import assert from 'node:assert/strict';
import {splitWork,turnPlacements,workSize} from '../src/timeline-data.js';

const messages=[{id:'user',role:'user',createdAt:1},{id:'interim',role:'assistant',createdAt:10},{id:'answer',role:'assistant',createdAt:20}];
const data={turns:[{id:'turn',anchorMessageId:'user',startedAt:2,endedAt:19,phase:'completed'}],nodes:[
 {id:'first-model',turnId:'turn',kind:'llm',startedAt:2,endedAt:4,phase:'completed',usage:{inputTokens:10,outputTokens:2,totalTokens:12,costUsd:.01}},
 {id:'first-tool',turnId:'turn',kind:'tool',startedAt:4,endedAt:5,phase:'completed',input:'one',output:'ok'},
 {id:'second-tool',turnId:'turn',kind:'tool',startedAt:11,endedAt:12,phase:'completed',input:'two',output:'ok'},
 {id:'second-model',turnId:'turn',kind:'llm',startedAt:13,endedAt:19,phase:'completed',usage:{inputTokens:20,outputTokens:3,totalTokens:23,costUsd:.02}},
]};
test('work splits around interim replies with disjoint timings usage and call counts',()=>{
 const grouped=splitWork(messages,data),placement=turnPlacements(messages,grouped);
 assert.equal(grouped.turns.length,2);assert.deepEqual(placement.after.get('user'),['turn@user']);assert.deepEqual(placement.after.get('interim'),['turn@interim']);assert.equal(placement.after.has('answer'),false);
 assert.deepEqual(grouped.turns.map(t=>[t.startedAt,t.endedAt]),[[2,5],[11,19]]);
 assert.deepEqual(grouped.turns.map(t=>t.aggregateUsage.totalTokens),[12,23]);
 assert.deepEqual(grouped.turns.map(t=>t.nodeCounts),[{tools:1,models:1},{tools:1,models:1}]);
 assert.equal(grouped.turns.reduce((n,t)=>n+t.aggregateUsage.costUsd,0),.03);
});
test('streaming new work does not rename earlier segments or shift them past an answer',()=>{
 const first=splitWork(messages.slice(0,2),{...data,nodes:data.nodes.slice(0,2)});
 const next=splitWork(messages,{...data,nodes:[...data.nodes,{id:'later',kind:'tool',turnId:'turn',phase:'running',startedAt:21}]});
 assert.equal(first.turns[0].id,next.turns[0].id);
 assert.equal(next.turns.at(-1).anchorMessageId,'answer');assert.equal(next.turns.at(-1).phase,'running');assert.equal(next.turns[0].phase,'completed');
});
test('small records do not create meaningless disclosure controls',()=>{
 assert.ok(workSize(data.nodes.slice(0,2))<=15);
 assert.ok(workSize([{kind:'tool',input:'command',output:'x\n'.repeat(20)}])>15);
});
test('canonical segment totals include earlier action pages without moving anchors',()=>{
 const grouped=splitWork(messages,{...data,nodes:[data.nodes[0]],segments:[{id:'turn@user',anchorMessageId:'user',startedAt:2,endedAt:9,phase:'completed',nodeCounts:{tools:90,models:100},aggregateUsage:{totalTokens:5000,costUsd:1}}]});
 assert.equal(grouped.turns[0].nodeCounts.models,100);assert.equal(grouped.turns[0].aggregateUsage.totalTokens,5000);assert.equal(grouped.turns[0].endedAt,9);
});
test('undated native transcript anchors take precedence over fallback timestamps',()=>{
 const grouped=splitWork(messages.map(m=>({...m,createdAt:0,timestampKnown:false})),{...data,nodes:[{...data.nodes[0],anchorMessageId:'interim'}]});
 assert.equal(grouped.turns[0].anchorMessageId,'interim');
});
test('summary tokens include cache writes once while preserving raw counters',async()=>{
 const {usageLabel}=await import('../src/timeline-data.js');
 const usage={inputTokens:4,outputTokens:2,totalTokens:6,cacheReadTokens:3,cacheWriteTokens:100,costUsd:.001,costType:'reported'};
 assert.match(usageLabel(usage).text,/106 tokens/);assert.equal(usage.totalTokens,6);
 assert.match(usageLabel({...usage,grossTotalTokens:106}).text,/106 tokens/);
});
