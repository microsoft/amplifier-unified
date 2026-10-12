import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {HistoryCleanups} from '../dist/history-cleanup.js';
import {createRecoveryCapabilities} from '../dist/index.js';
const hash='a'.repeat(64),revision='b'.repeat(64),requested={project:'project-one',nativeSessionId:'native-one'};
const census={version:1,revision,selection:requested,sessions:[{nativeSessionId:'native-one',cwd:'/workspace/one'}]};
const selection={version:1,nativeRevision:revision,nativeSessionCount:1,selection:requested,sessions:[{session:'ahp-session:/one'}],ownerCensus:{version:1,coverage:'all-configured-owners-held',nativeRevision:revision,revision:'c'.repeat(64)}};
const metadata={version:1,historyCleanup:{version:1,previewRequired:true,receipt:true,requiresRecoveryAdminLease:true,trustedOwnerCensus:true,workspacePreserved:true,stableLocksPreserved:true,automaticReplay:false}};
function fixture({loseNative=false,loseProjection=false,unknownNative=false,stale=false,protectedReference=false}={}){
 const receipts=new Map(),projections=new Map(),calls=[],saved=[];
 const port={version:1,inspect:async()=>{if(protectedReference)throw Error('future schedule');return structuredClone(selection)},settle:async args=>{
  calls.push('projection');assert.equal(saved.at(-1).historyCleanup.projectionCommandId,args.commandId);
  const receipt={commandId:args.commandId,operation:'history.cleanup.hideChats',state:'succeeded',result:{removed:true,workspacePreserved:true,nativeCommandId:args.nativeReceipt.commandId,nativeRevision:revision,sessions:['ahp-session:/one'],replayed:false}};projections.set(args.commandId,receipt);
  if(loseProjection)throw Error('lost projection reply');return {receipt};
 },receipt:async id=>({receipt:projections.get(id)})};
 const native=async(op,args)=>{
  calls.push(op);
  if(op==='maintenance.history.census')return structuredClone(census);
  if(op==='maintenance.history.preview')return {kind:'shared-history-removal',previewHash:hash,confirmationId:revision,selection:requested,title:'Saved conversation',sessionCount:1,inventory:{bytes:9},expiresAt:Date.now()/1000+600,workspacePreserved:true,stableLocksPreserved:true,receiptsPreserved:true,permanent:true,replayed:false};
  if(op==='maintenance.receipt')return {receipt:receipts.get(args.commandId)};
  assert.equal(op,'maintenance.history.remove');assert.equal(saved.at(-1).nativeCommandId,args.commandId);
  if(stale)throw Object.assign(Error('changed history'),{data:{executed:false}});
  if(unknownNative)throw Error('native response unknown');
  const receipt={commandId:args.commandId,operation:op,state:'succeeded',result:{removed:true,previewHash:hash,confirmationId:revision,selection:requested,sessionCount:1,removedBytes:9,workspacePreserved:true,stableLocksPreserved:true,receiptsPreserved:true,replayed:false}};receipts.set(args.commandId,receipt);
  if(loseNative)throw Error('lost native reply');return {receipt};
 };
 const cleanup=new HistoryCleanups(port,native);
 const review={id:'review',accountId:'account',commandId:'review',operation:'recovery.history.prepare',args:{selection:requested},fence:{purpose:'recovery'},context:{clientId:'one'},sessions:[]};
 const effect=async()=>{await cleanup.perform(review,()=>saved.push(structuredClone(review)));if(protectedReference)return review;assert.equal(review.terminalState,'prepared');const job={...structuredClone(review),id:'effect',commandId:'remove',terminalState:undefined,operation:'recovery.history.remove',args:{confirmationId:revision,previewHash:hash}};await cleanup.perform(job,()=>saved.push(structuredClone(job)));return job;};
 return {cleanup,port,native,calls,saved,effect};
}
test('history cleanup requires complete negotiated Native and trusted Host support and explicit user selection',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'history-cleanup-')),f=fixture();
 const options={directory,nativeAuthority:'one',nativeAdmin:f.native,authorize:async()=>({accountId:'one'}),resolveSession:async()=>{throw Error('No runtime')},quiescence:{}};
 for(const additions of [{},{nativeMaintenance:metadata},{historyCleanup:f.port}]){const owner=createRecoveryCapabilities({...options,...additions});try{assert.equal(owner.manifest.actions['recovery.history.remove'],undefined);}finally{await owner.close()}}
 const owner=createRecoveryCapabilities({...options,nativeMaintenance:metadata,historyCleanup:f.port});
 try{
  assert.ok(owner.manifest.actions['recovery.history.remove']);
  const invoke=(args,origin='ui')=>owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.history.prepare',commandId:'review',args},{clientId:'one',origin});
  await assert.rejects(invoke({selection:{...requested,path:'/untrusted'},reviewed:true}),/not permitted/);
  await assert.rejects(invoke({selection:requested,reviewed:true},'agent'),/account-level user review/);
  assert.equal(f.calls.length,0);
 }finally{await owner.close();await rm(directory,{recursive:true,force:true})}
});
test('history deletion and navigation commands are persisted before their effects',async()=>{const f=fixture(),job=await f.effect();assert.equal(job.terminalState,'succeeded');assert.equal(job.result.productArtifactsPreserved,true);assert.equal(f.calls.filter(x=>x==='maintenance.history.remove').length,1);assert.equal(f.calls.filter(x=>x==='projection').length,1)});
test('protected references refuse review without dispatching removal',async()=>{const f=fixture({protectedReference:true}),job=await f.effect();assert.equal(job.terminalState,'refused');assert.deepEqual(f.calls,['maintenance.history.census'])});
test('changed Native review refuses without changing navigation',async()=>{const f=fixture({stale:true}),job=await f.effect();assert.equal(job.terminalState,'refused');assert.equal(f.calls.includes('projection'),false)});
test('lost projection reply recovers original receipt without repeating effects',async()=>{const f=fixture({loseProjection:true}),job=await f.effect();assert.equal(job.terminalState,undefined);await f.cleanup.inspect(job,()=>{});assert.equal(job.terminalState,'succeeded');assert.equal(f.calls.filter(x=>x==='projection').length,1);assert.equal(f.calls.filter(x=>x==='maintenance.history.remove').length,1)});
test('lost Native reply requires explicit metadata-only finish',async()=>{const f=fixture({loseNative:true}),job=await f.effect();await f.cleanup.inspect(job,()=>{});assert.equal(job.terminalState,undefined);assert.equal(job.reason,'history-removed-navigation-not-confirmed');assert.equal(f.calls.includes('projection'),false);await f.cleanup.finish(job,()=>f.saved.push(structuredClone(job)));assert.equal(job.terminalState,'succeeded');assert.equal(f.calls.filter(x=>x==='maintenance.history.remove').length,1)});
test('unknown Native effect cannot dispatch projection even on explicit finish',async()=>{const f=fixture({unknownNative:true}),job=await f.effect();await f.cleanup.inspect(job,()=>{});await assert.rejects(f.cleanup.finish(job,()=>{}),/original history removal receipt/);assert.equal(job.terminalState,undefined);assert.equal(f.calls.includes('projection'),false);assert.equal(f.calls.filter(x=>x==='maintenance.history.remove').length,1)});
