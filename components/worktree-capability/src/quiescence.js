import {managedParticipant,emptyManaged} from './managed-files.js';
import {emptyRetention,retentionParticipant} from './retention.js';
const canonicalProof=value=>Array.isArray(value)?value.map(canonicalProof):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonicalProof(value[key])])):value;
const releaseSignature=(outcome,proof)=>JSON.stringify([outcome,canonicalProof(proof)]);
import {DatabaseSync} from 'node:sqlite';
import {join} from 'node:path';
import {watch} from 'node:fs';
import {serviceIdentity,validateServiceRelease} from './service-lifecycle.js';
function serviceContext(result,value){if(result.purpose==='service-stop'){result.serviceIdentity=serviceIdentity(value.serviceIdentity);if(result.serviceIdentity.instanceId!==result.instanceId||result.serviceIdentity.dataScope!==result.dataScope)throw Error('Service identity differs from owner context');}else if(value.serviceIdentity!==undefined)throw Error('Service identity requires service-stop purpose');return result;}
const keys=['ownerId','fenceId','commandId','purpose','instanceId','dataScope'];
function context(ownerId,value){const result={ownerId,...Object.fromEntries(keys.slice(1).map(key=>[key,value?.[key]]))};if(Object.values(result).some(v=>typeof v!=='string'||!v||v.length>200||/[\x00-\x1f]/.test(v))||!['recovery','distribution-update','service-stop','retention-hide','managed-files-disposal'].includes(result.purpose))throw Error('Bounded exact worktree quiescence context required');return serviceContext(result,value);}
const same=(left,right)=>keys.every(key=>left[key]===right[key])&&JSON.stringify(left.serviceIdentity)===JSON.stringify(right.serviceIdentity);
/** One OS-held owner plus a durable intake fence; no snapshot of idle implies a lease. */
export class WorktreeQuiescence{
 constructor(directory,onMayBeIdle=()=>{}){
  this.directory=directory;this.active=0;this.onMayBeIdle=onMayBeIdle;this.closed=false;
  this.lock=new DatabaseSync(join(directory,'worktrees-owner-lock.sqlite'));
  try{this.lock.exec('PRAGMA busy_timeout=0;PRAGMA journal_mode=DELETE;CREATE TABLE IF NOT EXISTS owner(id INTEGER);BEGIN EXCLUSIVE');}
  catch(error){this.lock.close();throw Error('Worktrees directory already has an owner or its exclusive lease is unavailable',{cause:error});}
  try{
   this.db=new DatabaseSync(join(directory,'worktree-quiescence.sqlite'));this.db.exec('PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;CREATE TABLE IF NOT EXISTS fence(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT);CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,value TEXT);');
   this.workers=new DatabaseSync(join(directory,'worktree-workers.sqlite'));this.workers.exec("PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY,state TEXT NOT NULL);CREATE INDEX IF NOT EXISTS worker_state ON workers(state)");
   this.workerLock=new DatabaseSync(join(directory,'worktree-worker-lock.sqlite'));this.workerLock.exec('PRAGMA busy_timeout=0;PRAGMA journal_mode=DELETE;CREATE TABLE IF NOT EXISTS lease(id INTEGER)');this.workerHeld=false;
   const prior=this.current();if(prior)this.save({...prior,state:'unknown'});
  }catch(error){this.workerLock?.close();this.workers?.close();this.db?.close();this.lock.close();throw error;}
 }
 current(){const row=this.db.prepare('SELECT value FROM fence WHERE id=1').get();return row&&JSON.parse(row.value);}
 save(value){this.db.exec('BEGIN IMMEDIATE');try{this.db.prepare('INSERT OR REPLACE INTO fence VALUES(1,?)').run(JSON.stringify(value));this.db.prepare('INSERT OR REPLACE INTO receipts VALUES(?,?)').run(value.fenceId,JSON.stringify(value));this.db.exec('COMMIT')}catch(error){this.db.exec('ROLLBACK');throw error;}}
 assertOpen(){if(this.closed||this.current())throw Object.assign(Error('Worktree owner intake closed; no new effect admitted'),{code:-32009,executed:false});}
 inspect(){return {intakeClosed:!!this.current(),fence:this.current(),activeRequests:this.active,workerLifetimes:Number(this.workers.prepare("SELECT count(*) n FROM workers WHERE state='pending'").get().n),coverage:'worktree-owner',gaps:this.coverageGaps?.()??[]};}
 notice(){if(this.noticeTimer||this.closed)return;this.noticeTimer=setTimeout(()=>{this.noticeTimer=undefined;try{Promise.resolve(this.onMayBeIdle()).catch(()=>{})}catch{}},0);this.noticeTimer.unref();}
 watchWorkers(){if(this.watcher)return;try{this.watcher=watch(this.directory,{persistent:false},(_event,name)=>{if(this.closed)return;if(!String(name).startsWith('worktree-workers.sqlite'))return;this.notice();if(!this.inspect().workerLifetimes){this.watcher.close();this.watcher=undefined;}});this.watcher.on('error',()=>{this.watcher?.close();this.watcher=undefined;});if(!this.inspect().workerLifetimes){this.watcher.close();this.watcher=undefined;this.notice();}}catch{}}
 async effect(callback){this.assertOpen();this.active++;try{return await callback()}finally{this.active--;this.notice();}}
 participant(ownerId='worktrees'){
  return managedParticipant(retentionParticipant({id:ownerId,serviceStop:{version:1},acquire:async input=>{
   const value=context(ownerId,input),prior=this.current();
   if(prior){if(!same(prior,value)||prior.state!=='held')throw Error('Worktree fence requires exact authoritative reconciliation');return this.lease(value);}
   if(this.db.prepare('SELECT 1 FROM receipts WHERE id=?').get(value.fenceId))throw Error('Retained worktree fence cannot be acquired again');
   if(this.inspect().workerLifetimes){this.watchWorkers();return null;}if(this.active||this.inspect().gaps.length)return null;
   this.save({...value,state:'checking'});
   try{this.workerLock.exec('BEGIN EXCLUSIVE');this.workerHeld=true;}catch(error){this.db.exec('BEGIN IMMEDIATE');try{this.db.prepare('DELETE FROM receipts WHERE id=?').run(value.fenceId);this.db.prepare('DELETE FROM fence WHERE id=1').run();this.db.exec('COMMIT')}catch(e){this.db.exec('ROLLBACK');throw e;}return null;}
   this.save({...value,state:'held'});this.liveFence=value.fenceId;return this.lease(value);
  },reconcileRelease:async input=>this.release(context(ownerId,input),input.outcome,input.proof)},args=>{const empty=emptyRetention(args.context,args,this.current());return this.retentionReferences?.(args.sessions)??empty;}),args=>{emptyManaged(args.context,args,this.current());return this.managedReferences(args);});
 }
 lease(value){return {ownerId:value.ownerId,fenceId:value.fenceId,release:async(outcome,proof)=>this.release(value,outcome,proof,true)};}
 release(value,outcome,proof,live=false){
  const prior=this.current();if(!prior){const row=this.db.prepare('SELECT value FROM receipts WHERE id=?').get(value.fenceId),receipt=row&&JSON.parse(row.value);if(receipt?.state==='released'&&same(receipt,value)&&receipt.releaseSignature===releaseSignature(outcome,proof))return;throw Error('Exact worktree fence required');}
  if(!same(prior,value))throw Error('Exact worktree fence required');
  if(outcome==='unknown'){this.liveFence=undefined;this.save({...prior,state:'unknown'});return;}
  const refused=proof?.kind==='admission-refused'&&Object.keys(proof).length===1&&prior.state==='held'&&outcome==='unchanged'&&(prior.purpose!=='service-stop'||live&&this.liveFence===prior.fenceId);
  const verified=proof?.verified===true&&proof.fenceId===prior.fenceId&&proof.commandId===prior.commandId&&proof.dataScope===prior.dataScope&&proof.outcome===outcome&&['ready','unchanged'].includes(outcome)&&typeof proof.instanceId==='string'&&proof.instanceId.length>0&&proof.instanceId.length<=200&&typeof proof.receiptId==='string'&&proof.receiptId.length>0&&proof.receiptId.length<=200&&((proof.instanceId===prior.instanceId)===(outcome==='unchanged'));
  if(prior.purpose==='service-stop'&&!refused)validateServiceRelease(prior,outcome,proof);
  if(!refused&&!verified)throw Error('Verified exact worktree release proof required');
  if(this.workerHeld){this.workerLock.exec('ROLLBACK');this.workerHeld=false;}
  this.db.exec('BEGIN IMMEDIATE');try{this.db.prepare('INSERT OR REPLACE INTO receipts VALUES(?,?)').run(prior.fenceId,JSON.stringify({...prior,state:'released',outcome,releaseSignature:releaseSignature(outcome,proof)}));this.db.prepare('DELETE FROM fence WHERE id=1').run();this.db.exec('COMMIT');this.liveFence=undefined;}catch(error){this.db.exec('ROLLBACK');throw error;}
 }
 assertClosable(){if(this.active)throw Error('Worktree work is still running; closing cannot manufacture quiescence');}
 close(){if(this.closed)return;this.assertClosable();this.closed=true;this.watcher?.close();if(this.noticeTimer)clearTimeout(this.noticeTimer);try{this.db.close();this.workers.close();this.workerLock.close();}finally{try{this.lock.exec('ROLLBACK')}finally{this.lock.close()}}}
}
