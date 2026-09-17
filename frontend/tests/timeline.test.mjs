import test from 'node:test';
import assert from 'node:assert/strict';
import {executionData,messageTurnId,treeForTurn,usageLabel} from '../src/timeline-data.js';
test('turns attach to their user input and preserve tool-worker-subtool hierarchy',()=>{
 const data={turns:[{id:'input-1'}],nodes:[{id:'tool-a',turnId:'input-1',kind:'tool'},{id:'worker-a',parentId:'tool-a',turnId:'input-1',kind:'worker'},{id:'tool-b',parentId:'worker-a',turnId:'input-1',kind:'tool'},{id:'llm-b',parentId:'tool-b',turnId:'input-1',kind:'llm'}]};
 assert.equal(messageTurnId({role:'user',inputId:'input-1'},data.turns),'input-1');
 assert.equal(messageTurnId({role:'assistant',inputId:'input-1'},data.turns),null);
 const tree=treeForTurn(data,'input-1');assert.deepEqual(tree.roots.map(n=>n.id),['tool-a']);assert.equal(tree.children.get('worker-a')[0].id,'tool-b');
});
test('cycles and orphan actions remain inspectable without recursive traversal failure',()=>{
 const data={nodes:[{id:'a',parentId:'b',turnId:'x'},{id:'b',parentId:'a',turnId:'x'},{id:'orphan',parentId:'missing',turnId:'x'}]};
 assert.equal(treeForTurn(data,'x').roots.length,2);
});
test('usage distinguishes actual, estimated, partial, and unavailable cost',()=>{
 assert.match(usageLabel({totalTokens:1500,costUsd:.012,costType:'reported'}).text,/1.5k tokens · \$0.012/);
 assert.match(usageLabel({totalTokens:1500,costUsd:.012,costType:'estimated'}).text,/≈\$/);
 const partial=usageLabel({totalTokens:1500,costUsd:.012,costType:'partial',calls:2,pricedCalls:1});assert.match(partial.text,/\+$/);assert.match(partial.title,/1 of 2/);
 const unknown=usageLabel({calls:1,totalTokens:100,costUsd:0,costType:'unavailable'});assert.match(unknown.text,/cost unavailable/);assert.ok(!unknown.text.includes('$0'));
 assert.equal(usageLabel({calls:0,totalTokens:0,costUsd:0,costType:'unavailable'}),null);
});
test('legacy worker and tool observations form one nested group without duplicating job/session records',()=>{
 const data=executionData({runtimeEvents:[{type:'runtime.tool',phase:'pre',callId:'call-a',tool:'delegate'}],workers:[{id:'job-a',callId:'call-a',kind:'job',status:'running'},{id:'session-a',sessionId:'session-a',callId:'call-a',kind:'session',status:'running'}]});
 assert.equal(data.nodes.filter(n=>n.kind==='worker').length,1);
 const worker=data.nodes.find(n=>n.kind==='worker');assert.equal(worker.parentId,'tool:call-a');assert.equal(worker.workerId,'session-a');assert.equal(data.turns[0].id,'observed-activity');
});
