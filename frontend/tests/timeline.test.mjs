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

test('voice work stays at its saved position across later messages and completion',async()=>{
 const {turnPlacements}=await import('../src/timeline-data.js');
 const messages=[{id:'u',role:'user',createdAt:10},{id:'ack',role:'assistant',createdAt:11},{id:'response',role:'assistant',createdAt:20}];
 const data={turns:[{id:'voice:one',anchorMessageId:'u',startedAt:12},{id:'voice:two',anchorMessageId:'ack',startedAt:13},{id:'voice:three',anchorMessageId:'ack',startedAt:14}],nodes:[]};
 const original=turnPlacements(messages,data);
 assert.deepEqual(original.before,[]);assert.deepEqual(original.after.get('ack'),['voice:two','voice:three']);
 messages.push({id:'next',role:'user',createdAt:30});data.turns.forEach(t=>{t.phase='completed';t.endedAt=35});
 assert.deepEqual(turnPlacements(messages,data),original);
});

test('older voice records use start time, and unknown records never collect at the bottom',async()=>{
 const {turnPlacements}=await import('../src/timeline-data.js');
 const messages=[{id:'u',role:'user',createdAt:10},{id:'ack',role:'assistant',createdAt:11},{id:'response',role:'assistant',createdAt:20}];
 const data={turns:[{id:'voice:old',startedAt:12},{id:'node-only'},{id:'unknown'}],nodes:[{id:'tool',turnId:'node-only',startedAt:15}]};
 const result=turnPlacements(messages,data);
 assert.deepEqual(result.after.get('ack'),['voice:old','node-only']);assert.deepEqual(result.before,['unknown']);assert.equal(result.after.has('response'),false);
});

test('pending startup and partial usage differ from unavailable completed metrics',()=>{
 const missing={calls:1,totalTokens:0,costUsd:0,costType:'unavailable',pricedCalls:0,unknownCalls:1,tokenUnknownCalls:1};
 assert.equal(usageLabel({...missing,tokenPendingCalls:1,costPendingCalls:1}).text,'tokens pending · cost pending');
 assert.match(usageLabel({...missing,tokenPendingCalls:0,costPendingCalls:0},{pending:true}).text,/tokens unavailable · cost unavailable/);
 assert.equal(usageLabel({calls:0,totalTokens:0,costUsd:0},{pending:true}).text,'usage pending');
 const partial=usageLabel({calls:2,totalTokens:120,costUsd:.01,costType:'partial',pricedCalls:1,estimatedCalls:1,unknownCalls:1,tokenUnknownCalls:1,tokenPendingCalls:1,costPendingCalls:1});
 assert.match(partial.text,/120 tokens \+ pending/);assert.match(partial.text,/≈\$0.010 \+ pending/);assert.match(partial.title,/Includes estimated cost/);
 assert.ok(!usageLabel(missing).text.includes('$0'));
});

test('elapsed time uses host timestamps and stops on durable terminal events',async()=>{
 const {elapsedLabel,isRunning}=await import('../src/timeline-data.js');
 assert.equal(elapsedLabel({phase:'running',startedAt:100},112.8),'12s');
 assert.equal(elapsedLabel({phase:'queued',startedAt:100},131),'31s');
 assert.equal(elapsedLabel({phase:'completed',startedAt:100,endedAt:101.4},900),'1.4s');
 assert.equal(elapsedLabel({phase:'error',startedAt:100,endedAt:162},1000),'1m 2s');
 assert.equal(elapsedLabel({phase:'interrupted',startedAt:100},1000),null);
 assert.equal(elapsedLabel({phase:'running',startedAt:200},100),'0s');
 assert.equal(isRunning({phase:'running',endedAt:120}),false);
 const data=executionData({execution:{nodes:[],turns:[{id:'new',startedAt:100,phase:'running'}]}});assert.equal(data.turns[0].id,'new');
});

test('public result links accept only explicit web URLs without credentials',async()=>{
 const {detailLinks}=await import('../src/timeline-data.js');
 assert.deepEqual(detailLinks(JSON.stringify({url:'https://example.com/report',nested:{uri:'file:///private/key',artifact_url:'javascript:alert(1)'},items:[{html_url:'https://user:pass@example.com'},{web_url:'https://example.com/report'}]})),['https://example.com/report']);
 assert.deepEqual(detailLinks('unstructured result'),[]);
});
