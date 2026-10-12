import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {BackupCleanups,backupAvailable} from '../dist/backup-cleanup.js';
import {createRecoveryCapabilities} from '../dist/index.js';
const coverage='completed-native-private-archives-only',hash='b'.repeat(64),artifact='a'.repeat(32);
const capability={version:1,backupCleanup:{version:1,previewRequired:true,receipt:true,requiresRecoveryAdminLease:true,newestProtected:true,recoveryReferencesProtected:true,automaticReplay:false}};
function fixture({lost=false,unknown=false,stale=false}={}){
 const calls=[],saved=[],receipts=new Map();
 const native=async(op,args)=>{
  calls.push(op);if(op==='maintenance.receipt')return {receipt:receipts.get(args.commandId)};
  if(op==='maintenance.backups.preview')return {coverage,kind:'private-backup-removal',previewHash:hash,artifact:{artifactId:artifact,removable:true},permanent:true,inventory:{bytes:21,entries:3},expiresAt:Date.now()/1000+600};
  assert.equal(op,'maintenance.backups.remove');assert.equal(saved.at(-1).nativeCommandId,args.commandId);
  if(stale)throw Object.assign(Error('referenced after review'),{data:{executed:false}});
  if(unknown)throw Error('unconfirmed effect');
  const receipt={commandId:args.commandId,operation:op,state:'succeeded',result:{coverage,artifactId:artifact,previewHash:hash,removed:true,removedBytes:21,newestProtected:true,recoveryReferencesProtected:true,canonicalFilesChanged:0,replayed:false}};receipts.set(args.commandId,receipt);if(lost)throw Error('lost reply');return {receipt};
 };
 const owner=new BackupCleanups(native),review={id:'review',accountId:'one',commandId:'review',operation:'recovery.backups.prepare',args:{artifactId:artifact},context:{clientId:'one'},sessions:[]};
 const perform=async()=>{await owner.perform(review,()=>saved.push(structuredClone(review)));assert.equal(review.terminalState,'prepared');const job={...structuredClone(review),id:'remove',commandId:'remove',operation:'recovery.backups.remove',terminalState:undefined,args:{confirmationId:artifact}};await owner.perform(job,()=>saved.push(structuredClone(job)));return job;};
 return {owner,perform,calls};
}
test('backup cleanup negotiates the full protected-retirement contract and rejects agents or paths',async()=>{
 assert.equal(backupAvailable({version:1,backupCleanup:{...capability.backupCleanup,newestProtected:false}}),false);
 const directory=await mkdtemp(join(tmpdir(),'backup-contract-'));const owner=createRecoveryCapabilities({directory,nativeAuthority:'native',nativeMaintenance:capability,nativeAdmin:async()=>{throw Error('unexpected dispatch')},authorize:async()=>({accountId:'one'}),resolveSession:async()=>{throw Error('unexpected session')},quiescence:{}});
 try{
  assert.ok(owner.manifest.actions['recovery.backups.remove']);
  const act=(args,origin='ui')=>owner.action({version:1,topic:'recovery',channel:'ahp-root://',commandId:'review',operation:'recovery.backups.prepare',args},{clientId:'one',origin});
  await assert.rejects(act({artifactId:artifact,reviewed:true,path:'/external'}),/not permitted/);
  await assert.rejects(act({artifactId:artifact,reviewed:true},'agent'),/account-level/);
 }finally{await owner.close();await rm(directory,{recursive:true,force:true});}
});
test('backup effect records its command before dispatch and validates protected removal',async()=>{const f=fixture(),job=await f.perform();assert.equal(job.terminalState,'succeeded');assert.equal(job.result.canonicalFilesChanged,0);});
test('stale backup review refuses without claiming removal',async()=>{const f=fixture({stale:true}),job=await f.perform();assert.equal(job.terminalState,'refused');assert.equal(job.result,undefined);});
test('lost backup reply is recovered only from the original receipt',async()=>{const f=fixture({lost:true}),job=await f.perform();assert.equal(job.terminalState,undefined);await f.owner.inspect(job,()=>{});assert.equal(job.terminalState,'succeeded');assert.equal(f.calls.filter(x=>x==='maintenance.backups.remove').length,1);});
test('unproven backup removal stays unknown without replay',async()=>{const f=fixture({unknown:true}),job=await f.perform();await f.owner.inspect(job,()=>{});assert.equal(job.terminalState,undefined);assert.equal(f.calls.filter(x=>x==='maintenance.backups.remove').length,1);});
