import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHistoryCleanupCapabilities} from '../src/history-cleanup.js';
import {createDistribution} from '../src/index.js';
const sid='ahp-session:/one',hash='a'.repeat(64);
const binding={fenceId:'update-fence',commandId:'update',purpose:'distribution-update',instanceId:'owned',dataScope:'owned'};
async function fixture(t,host){const directory=await mkdtemp(join(tmpdir(),'cleanup-facade-'));const create=()=>createHistoryCleanupCapabilities({host:()=>host,directory,authorize:async caller=>{if(caller.account!=='account')throw Error('Denied account');}});t.after(()=>rm(directory,{recursive:true,force:true}));return create;}
const call=(owner,operation,args={},commandId='command',context={account:'account',clientId:'browser-one'})=>owner.action({version:1,channel:'ahp-root://',topic:'history-cleanup',operation:'cleanup.'+operation,args,commandId},context);
test('cleanup uses authenticated identity and refuses caller-supplied paths, identities and oversized effects',async t=>{
 const observed=[],create=await fixture(t,{previewRetention:async args=>{observed.push(args);return {items:[],candidateCount:0};}}),owner=create();
 try{
  await call(owner,'preview',{modifiedBefore:123,limit:50,protectSessionIds:[sid]});assert.equal(observed[0].connectionId,'browser-one');
  await call(owner,'preview',{modifiedBefore:123},'agent-review',{account:'account',actorId:'agent:native-one'});assert.equal(observed[1].connectionId,'agent:native-one');
  for(const args of [{modifiedBefore:123,connectionId:'spoof'},{modifiedBefore:123,deletePath:'/tmp'},{modifiedBefore:123,limit:51},{modifiedBefore:123,protectSessionIds:[sid,sid]}])await assert.rejects(call(owner,'preview',args));
  await assert.rejects(call(owner,'preview',{modifiedBefore:123},'foreign',{account:'foreign',clientId:'browser-one'}),/Denied/);
  assert.equal(observed.length,2);
  await assert.rejects(call(owner,'apply',{reviewId:'review',reviewHash:hash,sessionIds:Array(51).fill(sid)}));
 }finally{await owner.close();}
});
test('lost cleanup acknowledgement recovers exact host receipt through a persisted facade fence without repeating effects',async t=>{
 let effects=0;const receipts=new Map(),host={applyRetention:async args=>{effects++;receipts.set(args.commandId,{...args,status:'completed',items:[{session:sid,status:'hidden'}]});throw Error('Lost acknowledgement');},retentionReceipt:async id=>receipts.get(id)};
 const create=await fixture(t,host);let owner=create();
 try{
  await assert.rejects(call(owner,'apply',{reviewId:'review',reviewHash:hash,sessionIds:[sid]},'original'),/Lost acknowledgement/);
  assert.ok(await owner.quiescenceParticipant.acquire(binding));await owner.close();owner=create();
  const result=await call(owner,'receipt',{commandId:'original'});assert.equal(result.result.receipt.status,'completed');assert.equal(result.result.replayed,false);assert.equal(effects,1);
  await assert.rejects(call(owner,'apply',{reviewId:'review',reviewHash:hash,sessionIds:[sid]},'new'),/intake is closed/);
 }finally{await owner.close();}
});
test('actual forwarding blocks service/update admission, and capability reads perform no catalog or native discovery',async t=>{
 let resolve;const waiting=new Promise(r=>resolve=r);let calls=0;
 const create=await fixture(t,{previewRetention:async()=>{calls++;await waiting;return {items:[]};}}),owner=create();
 try{
  const state=await owner.read({topic:'history-cleanup',scope:'host',uri:'amplifier-capability://history-cleanup'},{account:'account'});assert.equal(state.data.historyCleanup.canonicalHistoryPreserved,true);assert.equal(calls,0);
  const pending=call(owner,'preview',{modifiedBefore:123});await new Promise(r=>setImmediate(r));assert.equal(await owner.quiescenceParticipant.acquire(binding),null);
  resolve();await pending;const held=await owner.quiescenceParticipant.acquire(binding);assert.ok(held);await held.release('unchanged',{kind:'admission-refused'});
  assert.equal(owner.quiescenceParticipant.serviceStop.version,1);
 }finally{resolve();await owner.close();}
});

test('explicit protection reconciliation forwards only the original child receipt identity',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'cleanup-reconcile-'));t.after(()=>rm(directory,{recursive:true,force:true}));const calls=[];
 const owner=createHistoryCleanupCapabilities({directory,authorize:async()=>{},host:()=>{throw Error('must not repeat host effect');},protection:()=>({reconcile:async id=>{calls.push(id);return {state:'released'};}})});
 try{const result=await call(owner,'reconcile',{commandId:'original-child'});assert.deepEqual(calls,['original-child']);assert.equal(result.result.protection.state,'released');assert.equal(result.result.replayed,false);}finally{await owner.close();}
});

test('installed catalog composes cold cleanup with exact root-owner provenance and no agent startup',{skip:!process.env.CATALOG_PYTHON},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'cleanup-composed-'));let app;
 try{
  const workspace=join(directory,'workspace'),web=join(directory,'web');await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'<!doctype html><title>Owned fixture</title>');
  app=await createDistribution({account:'account',stateDirectory:join(directory,'state'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],historyCleanup:true,quiescence:{instanceId:'owned',dataScope:'owned'},engines:[{id:'offline',label:'Must stay dormant',command:join(directory,'must-not-start')}],catalogProcess:{command:process.env.CATALOG_PYTHON,args:['-I','-m','amplifier_session_catalog','serve','--db',join(directory,'catalog.sqlite'),'--scan-interval','0','--workspace-check-interval','0']}});
  assert.equal(app.quiescence.coverage.capabilities['history-cleanup'],'history-cleanup');
  const inventory=await app.storageInventory();
  assert.match(inventory.owners.find(o=>o.id==='history-cleanup').revision,/^sha256:[a-f0-9]{64}$/);
  assert.equal(inventory.omissions.some(o=>o.id==='provenance:history-cleanup'),false);
  const result=await app.host.invokeCapability({channel:'ahp-root://',topic:'history-cleanup',operation:'cleanup.preview',version:1,args:{modifiedBefore:1},commandId:'cold-review'},{actorId:'owned-reviewer',clientId:'owned-browser',origin:'ui'});
  assert.equal(result.result.candidateCount,0);assert.equal(result.result.nativeFilesRead,false);assert.equal(result.result.removalAuthorized,false);
 }finally{await app?.close();await rm(directory,{recursive:true,force:true});}
});
