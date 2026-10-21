import test from 'node:test';
import assert from 'node:assert/strict';
import {workStream} from '../src/work-stream.js';

function scene(){
 const worker={id:'w',kind:'worker',turnId:'t',sessionId:'child',parentId:'d',phase:'completed',label:'Review',
  exchange:{id:'d',startedAt:1,endedAt:30,delegateRequest:'request',delivery:'separate'}};
 const child=(id,at)=>({id,kind:'tool',label:'read_file',turnId:'t',sessionId:'child',parentId:'w',startedAt:at,endedAt:at+1});
 return {turns:[{id:'t'}],nodes:[worker,child('read',4),
  {id:'follow',kind:'tool',label:'live_worker',turnId:'t',sessionId:'main',delegate:worker,startedAt:22,
   exchange:{id:'follow',delegateRequest:'again',phase:'completed',startedAt:22}},
  {id:'reply2',kind:'exchange',turnId:'t',delegate:worker,startedAt:30,exchange:{id:'reply2',delegateResponse:'two',report:true}},
  {id:'main-model',kind:'llm',turnId:'t',sessionId:'main',rootSessionId:'main',startedAt:20,endedAt:21},
  child('search',5),
  {id:'reply1',kind:'exchange',turnId:'t',delegate:worker,startedAt:18,exchange:{id:'reply1',delegateResponse:'one',report:true}}]};
}
test('requests, replies and both owners operations interleave without mutating accounting',()=>{
 const data=scene(),before=JSON.stringify(data);
 const entries=workStream(data,'t').entries.filter(entry=>entry.kind!=='worker-status');
 assert.deepEqual(entries.map(entry=>entry.id),['d:delegateRequest','read','search','reply1:delegateResponse','main-model','follow:delegateRequest','reply2:delegateResponse']);
 assert.match(entries.find(entry=>entry.id==='read').actor,/^Delegate/);
 assert.equal(entries.find(entry=>entry.id==='main-model').actor,'Main chat');
 assert.equal(JSON.stringify(data),before);
});
test('inline return uses tool end, not the request start or first completed child',()=>{
 const data=scene();data.nodes=data.nodes.filter(node=>node.kind!=='exchange'&&node.id!=='follow');
 data.nodes[0].exchange={id:'d',startedAt:1,endedAt:30,delegateRequest:'ask',delegateResponse:'answer'};
 const entries=workStream(data,'t').entries.filter(entry=>entry.kind!=='worker-status');
 assert.equal(entries.at(-1).id,'d:delegateResponse');
 assert.equal(entries.at(-1).at,30);
});
test('record-local saved response keeps unknown association and no fabricated request',()=>{
 const node={id:'saved',kind:'tool',turnId:'t',sessionId:'child',association:'unverified',delegateResponse:'recorded'};
 const entries=workStream({nodes:[node]},'t').entries;
 assert.deepEqual(entries.map(entry=>entry.id),['saved:delegateResponse']);
 assert.match(entries[0].direction,/association unverified/);
 assert.equal(entries[0].at,undefined);
});
test('a terminal follow-up receipt is not a reply',()=>{
 const entries=workStream(scene(),'t').entries;
 assert.equal(entries.filter(entry=>entry.id==='follow:delegateResponse').length,0);
});
test('unknown timestamps retain their source boundary rather than sorting as zero',()=>{
 const nodes=[{id:'a',kind:'tool',turnId:'t',startedAt:3},{id:'unknown',kind:'tool',turnId:'t'},
  {id:'b',kind:'tool',turnId:'t',startedAt:2}];
 assert.deepEqual(workStream({nodes},'t').entries.map(entry=>entry.id),['a','unknown','b']);
});
test('running reply placeholders cannot reorder child operations after later main work',()=>{
 const data=scene();
 data.nodes=data.nodes.filter(node=>!['reply1','reply2','follow'].includes(node.id));
 data.nodes[0].phase='running';data.nodes[0].exchange={id:'d',startedAt:1,phase:'running',delegateRequest:'ask'};
 data.nodes.find(node=>node.id==='read').phase='running';
 delete data.nodes.find(node=>node.id==='read').endedAt;
 assert.deepEqual(workStream(data,'t').entries.filter(entry=>entry.kind!=='worker-status').map(entry=>entry.id),
  ['d:delegateRequest','read','search','main-model']);
});
test('failed request without a spawned worker retains explicit error receipt access',()=>{
 const node={id:'failed',kind:'tool',label:'delegate',turnId:'t',phase:'error',
  exchange:{id:'failed',phase:'error',startedAt:1,endedAt:2,delegateRequest:'ask',errorDetail:{id:'failed',field:'error'}}};
 const entry=workStream({nodes:[node]},'t').entries[0];
 assert.equal(entry.receipt,true);assert.equal(entry.followup,false);
 assert.deepEqual(entry.node.exchange.errorDetail,{id:'failed',field:'error'});
});
test('legacy worker without an exchange gets status only, not invented parent messages',()=>{
 const node={id:'legacy',kind:'worker',turnId:'t',summary:'retained',label:'Worker'};
 assert.deepEqual(workStream({nodes:[node]},'t').entries.map(entry=>entry.kind),['worker-status']);
});
