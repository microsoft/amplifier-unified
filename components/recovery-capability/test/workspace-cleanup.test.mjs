import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {WorkspaceCleanups} from '../dist/workspace-cleanup.js';
import {createRecoveryCapabilities} from '../dist/index.js';
const hash='b'.repeat(64),selection={workspaceId:'workspace-one',path:'/workspace/one',revision:'owner-census',coverage:'all-configured-owners-held'};
const metadata={version:1,workspaceCleanup:{version:1,previewRequired:true,receipt:true,requiresRecoveryAdminLease:true,trustedOwnerCensus:true,historyPreserved:true,automaticReplay:false}};
function fixture({loseNative=false,loseArchive=false,unknownNative=false,stale=false}={}){
 const receipts=new Map(),archives=new Map(),calls=[],saved=[];
 const port={version:1,inspect:async()=>({...selection}),archive:async(args)=>{
  calls.push('archive');assert.equal(saved.at(-1).workspaceCleanup.archiveCommandId,args.commandId);
  const receipt={commandId:args.commandId,operation:'workspace.cleanup.archive',state:'succeeded',result:{workspaceId:selection.workspaceId,archived:true,historyPreserved:true,nativeCommandId:args.nativeReceipt.commandId,replayed:false}};archives.set(args.commandId,receipt);
  if(loseArchive)throw Error('lost archive acknowledgement');return {receipt};
 },receipt:async id=>({receipt:archives.get(id)})};
 const native=async(op,args)=>{
  calls.push(op);
  if(op==='maintenance.workspace.preview')return {previewHash:hash,workspaceId:selection.workspaceId,confirmationPath:selection.path,historyPreserved:true,permanent:true,includesUncommittedWork:true,replayed:false,expiresAt:Date.now()/1000+600,inventory:{bytes:9,entries:2}};
  if(op==='maintenance.receipt')return {receipt:receipts.get(args.commandId)};
  assert.equal(op,'maintenance.workspace.remove');assert.equal(saved.at(-1).nativeCommandId,args.commandId);
  if(stale)throw Object.assign(Error('changed folder'),{data:{executed:false}});
  if(unknownNative)throw Error('native response unknown');
  const receipt={commandId:args.commandId,operation:op,state:'succeeded',result:{workspaceId:selection.workspaceId,previewHash:hash,removed:true,historyPreserved:true,canonicalFilesChanged:0,removedBytes:9,replayed:false}};receipts.set(args.commandId,receipt);
  if(loseNative)throw Error('lost native acknowledgement');return {receipt};
 };
 const cleanup=new WorkspaceCleanups(port,native);
 const review={id:'review',accountId:'account',commandId:'review',operation:'recovery.workspace.prepare',args:{workspaceId:selection.workspaceId},fence:{purpose:'recovery'},context:{clientId:'one'},sessions:[]};
 const effect=async()=>{await cleanup.perform(review,()=>saved.push(structuredClone(review)));assert.equal(review.terminalState,'prepared');const job={...structuredClone(review),id:'effect',commandId:'remove',terminalState:undefined,operation:'recovery.workspace.remove',args:{confirmationPath:selection.path,previewHash:hash}};await cleanup.perform(job,()=>saved.push(structuredClone(job)));return job;};
 return {cleanup,port,native,calls,saved,effect};
}
test('cleanup is hidden unless both native and trusted product ownership are complete',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'cleanup-contract-')),f=fixture();
 const options={directory,nativeAuthority:'one',nativeAdmin:f.native,authorize:async()=>({accountId:'one'}),resolveSession:async()=>{throw Error('No runtime')},quiescence:{}};
 for(const additions of [{},{nativeMaintenance:metadata},{workspaceCleanup:f.port}]){
  const owner=createRecoveryCapabilities({...options,...additions});try{assert.equal(owner.manifest.actions['recovery.workspace.remove'],undefined);}finally{await owner.close();}
 }
 const owner=createRecoveryCapabilities({...options,nativeMaintenance:metadata,workspaceCleanup:f.port});
 try{
  assert.ok(owner.manifest.actions['recovery.workspace.remove']);
  const invoke=(args,origin='ui')=>owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.workspace.prepare',commandId:'review',args},{clientId:'one',origin});
  await assert.rejects(invoke({workspaceId:'one',path:'/untrusted',reviewed:true}),/not permitted/);
  await assert.rejects(invoke({workspaceId:'one',reviewed:true},'agent'),/account-level user review/);
  assert.equal(f.calls.length,0);
 }finally{await owner.close();await rm(directory,{recursive:true,force:true});}
});
test('reviewed cleanup persists each command before its effect and completes only after archival',async()=>{
 const f=fixture(),job=await f.effect();assert.equal(job.terminalState,'succeeded');assert.equal(job.result.historyPreserved,true);assert.equal(job.result.archived,true);assert.deepEqual(f.calls,['maintenance.workspace.preview','maintenance.workspace.remove','archive']);
});
test('stale native review refuses without archiving',async()=>{
 const f=fixture({stale:true}),job=await f.effect();assert.equal(job.terminalState,'refused');assert.equal(f.calls.includes('archive'),false);
});
test('lost archival reply is recovered by original receipt, never by repeating either effect',async()=>{
 const f=fixture({loseArchive:true}),job=await f.effect();assert.equal(job.terminalState,undefined);await f.cleanup.inspect(job,()=>{});assert.equal(job.terminalState,'succeeded');assert.equal(f.calls.filter(x=>x==='archive').length,1);assert.equal(f.calls.filter(x=>x==='maintenance.workspace.remove').length,1);
});
test('lost native reply preserves completed deletion but does not guess that archival happened',async()=>{
 const f=fixture({loseNative:true}),job=await f.effect();await f.cleanup.inspect(job,()=>{});assert.equal(job.terminalState,undefined);assert.equal(job.reason,'folder-removed-archival-not-confirmed');assert.equal(f.calls.includes('archive'),false);assert.equal(f.calls.filter(x=>x==='maintenance.workspace.remove').length,1);
});
test('unknown original native effect remains uncertain and no archival is dispatched',async()=>{
 const f=fixture({unknownNative:true}),job=await f.effect();await f.cleanup.inspect(job,()=>{});assert.equal(job.terminalState,undefined);assert.equal(f.calls.includes('archive'),false);assert.equal(f.calls.filter(x=>x==='maintenance.workspace.remove').length,1);
});
test('explicit finish after a lost native reply archives only, using original effect evidence',async()=>{
 const f=fixture({loseNative:true}),job=await f.effect();await f.cleanup.finish(job,()=>f.saved.push(structuredClone(job)));
 assert.equal(job.terminalState,'succeeded');assert.equal(f.calls.filter(x=>x==='maintenance.workspace.remove').length,1);assert.equal(f.calls.filter(x=>x==='archive').length,1);
});
test('explicit finish refuses an unknown native effect without dispatching archival',async()=>{
 const f=fixture({unknownNative:true}),job=await f.effect();await assert.rejects(f.cleanup.finish(job,()=>{}),/original cleanup receipt/);
 assert.equal(f.calls.includes('archive'),false);assert.equal(f.calls.filter(x=>x==='maintenance.workspace.remove').length,1);
});
