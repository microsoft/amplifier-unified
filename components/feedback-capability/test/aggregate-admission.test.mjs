import test from 'node:test';
import assert from 'node:assert/strict';
const root=process.env.FEEDBACK_PACKAGE_MODULE?new URL('./',new URL(process.env.FEEDBACK_PACKAGE_MODULE,'file:')):new URL('../src/',import.meta.url);
const {AggregateAdmission}=await import(new URL('aggregate-admission.js',root));
const {Connection}=await import(new URL('connection.js',root));
const {createFeedbackCapability}=await import(new URL('index.js',root));
const context={commandId:'original-command',fenceId:'original-fence',purpose:'distribution-update',instanceId:'original-instance',dataScope:'owned-data'};
const proof={...context,kind:'distribution-admission-abort',verified:true,receiptId:'authenticated-abort'};
const ownerId='configured:feedback',children=[ownerId+':uploads',ownerId+':python'];
const acquired={acquired:true,fenceId:context.fenceId,intakeClosed:true};
const refused={acquired:false,executed:false,reason:'fixture busy'};
const receipt=(id,status='released')=>({ownerId:id,status,receiptId:'retained-'+id,commandId:context.commandId,fenceId:context.fenceId,instanceId:context.instanceId,dataScope:context.dataScope});
// Protocol sequencing fixture only. Actual SQLite/restart acceptance is in
// admission-installed.test.mjs against sealed Python and resources packages.
function fixture(options={}){
 const state={journal:null,events:[],effects:[],receipts:new Map(),active:0,entered:false};
 const originalAcquire=id=>{
  assert.equal(state.journal.attempts.at(-1).childOwnerId,id);assert.equal(state.journal.attempts.at(-1).status,'pending');state.events.push('acquire:'+id);
  const value=options.refused===id?refused:acquired;state.effects.push({operation:'acquire',id});
  state.receipts.set(id,receipt(id,value.acquired?'released':'not-acquired'));
  if(options.loseAcquire===id){options.loseAcquire=null;throw Error('Lost acquire acknowledgement');}return structuredClone(value);
 };
 const abort=id=>{state.events.push('abort:'+id);assert.deepEqual(state.journal.abortProof,proof);const value=state.receipts.get(id);assert.ok(value);if(!state.effects.some(row=>row.operation==='abort'&&row.id===id))state.effects.push({operation:'abort',id});if(options.loseAbort===id){options.loseAbort=null;throw Error('Lost abort acknowledgement');}return {...value,...(options.badReceipt===id?{ownerId:'substituted-owner'}:{})};};
 const owner={admissionPending:false,request:async(method,args)=>{
  if(method==='initialize')return {quiescence:options.unsupported?{}:{aggregateAdmission:{version:1},admissionAbort:{version:1}}};
  if(method==='quiescence.acquire')return originalAcquire(children[1]);
  if(method==='quiescence.abortAdmission'){assert.equal(args.ownerId,children[1]);return abort(children[1]);}
  assert.equal(method,'quiescence.aggregateAdmission');assert.deepEqual(args.context,context);assert.equal(args.ownerId,ownerId);const {operation}=args;state.events.push(operation);
  if(operation==='read')return structuredClone(state.journal);
  if(operation==='begin')state.journal={version:1,context,ownerId,owners:args.owners,attempts:[]};
  const journal=state.journal;
  if(operation==='attempt')journal.attempts.push({childOwnerId:args.childOwnerId,status:'pending'});
  if(operation==='result')Object.assign(journal.attempts.find(row=>row.childOwnerId===args.childOwnerId),{acquisition:args.acquisition,status:args.acquisition.acquired?'acquired':'refused'});
  if(operation==='abortIntent')journal.abortProof=args.proof;
  if(operation==='abortReceipt')journal.attempts.find(row=>row.childOwnerId===args.childOwnerId).abortReceipt=args.receipt;
  if(operation==='complete')journal.receipt=receipt(ownerId,journal.attempts.some(row=>row.abortReceipt.status==='released')?'released':'not-acquired');
  if(options.loseJournal===operation){options.loseJournal=null;throw Error('Lost durable journal acknowledgement');}
  return structuredClone(operation==='complete'?journal.receipt:journal);
 }};
 const uploads={id:children[0],acquire:async()=>{const value=originalAcquire(children[0]);return value.acquired?{ownerId:children[0],fenceId:context.fenceId,release:async()=>state.events.push('rollback:uploads')}:null;},abortAdmission:async input=>{assert.deepEqual(input,{...context,proof});return abort(children[0]);},reconcileRelease:async()=>state.events.push('release:uploads')};
 const create=()=>new AggregateAdmission({ownerId,owner,uploads,releaseOwner:async()=>state.events.push('release:python'),active:()=>state.active,enter:()=>{if(state.entered)throw Error('Concurrent transition');state.entered=true;},leave:()=>{state.entered=false;}});
 return {state,owner,uploads,options,create,adapter:create()};
}
test('both original attempts precede dispatch; abort retains all exact receipts in reverse',async()=>{
 const f=fixture(),lease=await f.adapter.acquire(context);assert.equal(lease.ownerId,ownerId);
 const value=await f.adapter.abort({...context,proof});assert.deepEqual(value,receipt(ownerId));
 assert.deepEqual(f.state.events.filter(row=>row.startsWith('abort:')),['abort:'+children[1],'abort:'+children[0]]);
 assert.deepEqual(f.state.journal.attempts.map(row=>row.abortReceipt),children.map(id=>receipt(id)));
 const effects=structuredClone(f.state.effects);assert.deepEqual(await f.create().abort({...context,proof}),value);assert.deepEqual(f.state.effects,effects);
 await assert.rejects(f.create().acquire(context),/cannot dispatch/);await assert.rejects(lease.release('unchanged',{kind:'admission-refused'}),/distinct retained proof/);
});
for(const id of children)test('unknown original '+id+' is settled only by its exact child receipt',async()=>{
 const f=fixture({loseAcquire:id});await assert.rejects(f.adapter.acquire(context),/Lost acquire/);assert.equal(f.state.journal.attempts.at(-1).status,'pending');
 assert.equal((await f.create().abort({...context,proof})).status,'released');assert.equal(f.state.effects.filter(row=>row.operation==='acquire'&&row.id===id).length,1);
});
test('upload refusal never dispatches Python and returns proven not-acquired',async()=>{
 const f=fixture({refused:children[0]});assert.equal(await f.adapter.acquire(context),null);assert.equal((await f.adapter.abort({...context,proof})).status,'not-acquired');assert.equal(f.state.journal.attempts.length,1);assert.ok(!f.state.events.includes('acquire:'+children[1]));
});
test('Python refusal preserves its receipt and the uploads pre-effect rollback',async()=>{
 const f=fixture({refused:children[1]});assert.equal(await f.adapter.acquire(context),null);assert.ok(f.state.events.includes('rollback:uploads'));assert.equal((await f.adapter.abort({...context,proof})).status,'released');assert.equal(f.state.journal.attempts[1].abortReceipt.status,'not-acquired');
});
for(const operation of ['attempt','result','abortReceipt','complete'])test('lost '+operation+' reply never replays acquisition or completed abort effects',async()=>{
 const f=fixture();if(['attempt','result'].includes(operation)){f.options.loseJournal=operation;await assert.rejects(f.adapter.acquire(context),/Lost durable/);if(operation==='attempt'){await assert.rejects(f.adapter.abort({...context,proof}));assert.equal(f.state.effects.length,0);return;}}
 else{await f.adapter.acquire(context);f.options.loseJournal=operation;await assert.rejects(f.adapter.abort({...context,proof}),/Lost durable/);}
 const acquiredCount=f.state.effects.filter(row=>row.operation==='acquire').length;assert.equal((await f.create().abort({...context,proof})).status,'released');assert.equal(f.state.effects.filter(row=>row.operation==='acquire').length,acquiredCount);
 assert.equal(new Set(f.state.effects.filter(row=>row.operation==='abort').map(row=>row.id)).size,f.state.effects.filter(row=>row.operation==='abort').length);
});
test('lost subowner abort acknowledgement retries its exact receipt without reexecution',async()=>{
 const f=fixture({loseAbort:children[1]});await f.adapter.acquire(context);await assert.rejects(f.adapter.abort({...context,proof}),/Lost abort/);assert.equal(f.state.journal.attempts[1].abortReceipt,undefined);
 await f.create().abort({...context,proof});assert.equal(f.state.events.filter(row=>row==='abort:'+children[1]).length,2);assert.equal(f.state.effects.filter(row=>row.operation==='abort'&&row.id===children[1]).length,1);
});
test('wrong subowner identity blocks aggregate completion and preserves earlier receipt',async()=>{
 const f=fixture({badReceipt:children[0]});await f.adapter.acquire(context);await assert.rejects(f.adapter.abort({...context,proof}),/Exact original/);assert.ok(f.state.journal.attempts[1].abortReceipt);assert.equal(f.state.journal.receipt,undefined);
 f.options.badReceipt=null;await f.create().abort({...context,proof});assert.equal(f.state.events.filter(row=>row==='abort:'+children[1]).length,1);
});
test('missing original journal and generic proof never contact child aborts',async()=>{
 const f=fixture();await assert.rejects(f.adapter.abort({...context,proof}),/No original/);await assert.rejects(f.adapter.abort({...context,proof:{...proof,kind:'service-lifecycle'}}),/distinct authenticated/);assert.equal(f.state.effects.length,0);
});
test('changed proof after partial settlement never contacts remaining children',async()=>{
 const f=fixture({badReceipt:children[0]});await f.adapter.acquire(context);await assert.rejects(f.adapter.abort({...context,proof}));const before=f.state.events.length;
 await assert.rejects(f.create().abort({...context,proof:{...proof,receiptId:'changed'}}),/differs/);assert.equal(f.state.events.slice(before).filter(row=>row.startsWith('abort:')).length,0);
});
test('local active work produces a durable zero-child refusal; abort waits for settlement',async()=>{
 const f=fixture();f.state.active=1;assert.equal(await f.adapter.acquire(context),null);assert.deepEqual(f.state.journal.attempts,[]);await assert.rejects(f.adapter.abort({...context,proof}),/pending/);
 f.state.active=0;assert.equal((await f.adapter.abort({...context,proof})).status,'not-acquired');assert.equal(f.state.effects.length,0);
});
test('outstanding owner replies block abort before journal mutation',async()=>{
 const f=fixture();await f.adapter.acquire(context);f.owner.admissionPending=true;await assert.rejects(f.adapter.abort({...context,proof}),/pending/);assert.equal(f.state.journal.abortProof,undefined);
});
test('unsupported owner refuses before any child acquire',async()=>{
 const f=fixture({unsupported:true});await assert.rejects(f.adapter.acquire(context),/contract unavailable/);assert.equal(f.state.effects.length,0);assert.equal(f.state.journal,null);
});
test('reconcile release strips additional transport fields from the original context',async()=>{
 const f=fixture();await f.adapter.acquire(context);const normal={verified:true,receiptId:'normal'};await f.adapter.release({...context,outcome:'unchanged',proof:normal},'unchanged',normal);assert.ok(f.state.events.includes('release:python'));assert.ok(f.state.events.includes('release:uploads'));
});
test('timed-out transport replies remain pending until their actual late response',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});const owner=new Connection({},async()=>{},()=>{});let sent;owner.write=row=>{sent=row;};
 const call=owner.send('quiescence.acquire',context),rejected=assert.rejects(call,/timed out/);t.mock.timers.tick(90000);await rejected;
 assert.equal(owner.admissionPending,true);assert.equal(owner.pending.size,1);await owner.receive(JSON.stringify({id:sent.id,result:acquired}));assert.equal(owner.admissionPending,false);
});
test('disconnect never turns an uncertain request into idle proof',async()=>{
 const owner=new Connection({},async()=>{},()=>{});owner.write=()=>{};const call=owner.send('quiescence.acquire',context),rejected=assert.rejects(call,/disconnected/);owner.fail('disconnected');await rejected;assert.equal(owner.pending.size,0);assert.equal(owner.admissionPending,true);
});
test('pending host callbacks also prevent admission abort',async()=>{
 let settle;const pending=new Promise(resolve=>{settle=resolve;});const owner=new Connection({},()=>pending,()=>{});owner.write=()=>{};
 const call=owner.receive(JSON.stringify({id:'host:1',method:'host/readExport',params:{}}));assert.equal(owner.admissionPending,true);settle({});await call;assert.equal(owner.admissionPending,false);
});
function publicFixture(t,request,inspectSession=async()=>{}){
 const f=fixture();t.mock.method(Connection.prototype,'request',async function(method,args){return request?request(f,method,args):f.owner.request(method,args);});
 const never=async()=>assert.fail('No public upload effect is allowed during admission');
 const cap=createFeedbackCapability({owner:{},uploadOwner:{resourceProviders:[{scheme:'amplifier-attachment',read:never,write:never,resolve:never}],quiescenceParticipant:id=>{assert.equal(id,children[0]);return f.uploads;},close:async()=>{}},inspectSession});
 return {f,cap,participant:cap.quiescenceParticipant(ownerId)};
}
test('public adapter counts authorization/session lookup before a Python request is sent',async t=>{
 let release;const gate=new Promise(resolve=>{release=resolve;});const {cap,participant,f}=publicFixture(t,(f,method,args)=>method==='action'?{fixture:true}:f.owner.request(method,args),()=>gate);
 const action=cap.action({version:1,topic:'feedback',operation:'feedback.get',args:{sessionId:'ahp-session:/owned'}},{origin:'ui'});
 assert.equal(await participant.acquire(context),null);assert.deepEqual(f.state.journal.attempts,[]);await assert.rejects(participant.abortAdmission({...context,proof}),/pending/);release();await action;
 assert.equal((await participant.abortAdmission({...context,proof})).status,'not-acquired');assert.equal(f.state.effects.length,0);
});
test('public adapter reuses its participant and excludes interleaving calls during abort',async t=>{
 let release,entered;const gate=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
 const {cap,participant}=publicFixture(t,async(f,method,args)=>{if(method==='quiescence.aggregateAdmission'&&args.operation==='abortIntent'){entered();await gate;}return f.owner.request(method,args);});
 assert.equal(cap.quiescenceParticipant(ownerId),participant);await participant.acquire(context);const abort=participant.abortAdmission({...context,proof});await started;
 await assert.rejects(cap.resourceProviders[0].write({}),/transition is in progress/);await assert.rejects(cap.action({},{}),/transition is in progress/);await assert.rejects(participant.abortAdmission({...context,proof}),/already in progress/);
 release();assert.equal((await abort).status,'released');
});
