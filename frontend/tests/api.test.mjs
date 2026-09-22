import test from 'node:test';
import assert from 'node:assert/strict';
import {request,applyIconTooltips} from '../src/api.js';

test('commands send structured JSON to same origin and return server receipt',async()=>{
 const original=globalThis.fetch;
 try {globalThis.fetch=async(path,options)=>{assert.equal(path,'/api/actions');assert.equal(options.credentials,'same-origin');assert.equal(options.headers['Content-Type'],'application/json');assert.deepEqual(JSON.parse(options.body),{action:'conversation.send',args:{text:'Hello'}});return new Response(JSON.stringify({accepted:true,revision:4}))};assert.deepEqual(await request('/api/actions',{method:'POST',body:{action:'conversation.send',args:{text:'Hello'}}}),{accepted:true,revision:4});}finally{globalThis.fetch=original}
});
test('server validation errors remain visible and do not report success',async()=>{
 const original=globalThis.fetch;try{globalThis.fetch=async()=>new Response(JSON.stringify({accepted:false,error:'Select a conversation first.'}),{status:400});await assert.rejects(request('/api/actions',{method:'POST',body:{}}),/Select a conversation first/);}finally{globalThis.fetch=original}
});
test('unexpected HTML response gives useful connection error',async()=>{
 const original=globalThis.fetch;try{globalThis.fetch=async()=>new Response('<html>proxy failure</html>',{status:502});await assert.rejects(request('/api/state'),/unexpected response \(502\)/);}finally{globalThis.fetch=original}
});
test('icon tooltips mirror accessible labels without replacing richer authored text',()=>{
 const automatic={title:'',dataset:{},classList:{contains:()=>false},querySelector:()=>({}),getAttribute:()=> 'Rename conversation'};
 const authored={title:'Attach files · up to 8 MB',dataset:{},classList:{contains:()=>true},querySelector:()=>null,getAttribute:()=> 'Add attachments'};
 const root={querySelectorAll:()=>[automatic,authored]};
 applyIconTooltips(root);
 assert.equal(automatic.title,'Rename conversation');
 assert.equal(automatic.dataset.iconTitle,'auto');
 assert.equal(authored.title,'Attach files · up to 8 MB');
 automatic.getAttribute=()=> 'Rename selected conversation';
 applyIconTooltips(root);
 assert.equal(automatic.title,'Rename selected conversation');
});

test('live timers anchor to the host response clock instead of a skewed device clock',async()=>{
 const {hostNow}=await import('../src/api.js'),original=globalThis.fetch;
 try{
  globalThis.fetch=async()=>new Response('{}',{headers:{date:'Sun, 20 Sep 2026 12:00:00 GMT'}});
  await request('/api/state');const anchored=Date.parse('2026-09-20T12:00:00Z')/1000;
  assert.ok(Math.abs(hostNow()-anchored)<1);
 }finally{globalThis.fetch=original}
});

test('an older host date arriving after a newer response cannot rewind elapsed time',async()=>{
 const {hostNow}=await import('../src/api.js'),{elapsedLabel}=await import('../src/timeline-data.js'),original=globalThis.fetch;
 let finishSlow;
 try{
  globalThis.fetch=path=>path==='/api/slow'?new Promise(resolve=>finishSlow=resolve):Promise.resolve(new Response('{}',{headers:{date:'Thu, 20 Jan 2050 12:00:10 GMT'}}));
  const slow=request('/api/slow');await request('/api/fast');
  const before=hostNow(),record={phase:'running',startedAt:Date.parse('2050-01-20T12:00:00Z')/1000},elapsed=elapsedLabel(record,before);
  finishSlow(new Response('{}',{headers:{date:'Thu, 20 Jan 2050 12:00:05 GMT'}}));await slow;
  assert.ok(hostNow()>=before);assert.equal(elapsedLabel(record,hostNow()),elapsed);
 }finally{globalThis.fetch=original}
});

test('publishing rejection preserves authoritative unknown receipt even on HTTP409',async()=>{
 const original=globalThis.fetch,receipt={requestId:'admitted-import',state:'unknown',reconciliationError:{code:'invalid_response'}};
 try{
  globalThis.fetch=async()=>new Response(JSON.stringify({accepted:false,error:'The remote receipt is malformed',code:'invalid_response',receipt}),{status:409});
  await assert.rejects(request('/api/actions',{method:'POST',body:{}}),error=>{
   assert.equal(error.status,409);assert.equal(error.code,'invalid_response');assert.deepEqual(error.receipt,receipt);return true;
  });
 }finally{globalThis.fetch=original}
});
