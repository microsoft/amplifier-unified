import {DatabaseSync} from 'node:sqlite';
import {mkdirSync,chmodSync} from 'node:fs';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import type {Job,Json} from './types.js';
import type {FenceContext} from './types.js';
import {exact,admissionAbortProof} from './admission-abort.js';
import type {AdmissionAbortProof,AdmissionAbortReceipt} from './admission-abort.js';
import {inspectStore,storeVersion,tableDefinitions,indexDefinitions} from './store-schema.js';
export class Store {
 readonly db:DatabaseSync;private ownership:DatabaseSync;private closed=false;
 constructor(directory:string){
  // Validate existing authority before even opening the separate lease database writable.
  inspectStore(directory);
  mkdirSync(directory,{recursive:true,mode:0o700});
  const lockPath=join(directory,'recovery-owner-lock.sqlite');this.ownership=new DatabaseSync(lockPath);
  try{chmodSync(lockPath,0o600);this.ownership.exec('PRAGMA busy_timeout=0; PRAGMA journal_mode=DELETE; BEGIN EXCLUSIVE');}
  catch(error){this.ownership.close();throw Error('Recovery owner already active or exclusive ownership unavailable',{cause:error});}
  const path=join(directory,'recovery.sqlite');
  let fresh:boolean;
  try{fresh=inspectStore(directory)==='fresh';this.db=new DatabaseSync(path);}catch(error){this.ownership.close();throw error;}
  try{
   chmodSync(path,0o600);
   this.db.exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; BEGIN IMMEDIATE');
   // A complete unversioned store is the only automatic adoption. Missing tables
   // cannot establish historical provenance and must never become empty authority.
   if(fresh){for(const sql of Object.values(tableDefinitions))this.db.exec(sql);this.db.exec('INSERT INTO revision VALUES(1,0)');}
   // Retain the existing exceptional rebuild behavior for derived indexes.
   for(const sql of Object.values(indexDefinitions))this.db.exec(sql.replace('CREATE INDEX','CREATE INDEX IF NOT EXISTS'));
   this.db.exec(`PRAGMA user_version=${storeVersion}`);
   // A restarted owner has no authority to replay commands or assume a former lease survived.
   const interrupted=this.db.prepare("UPDATE jobs SET state='unknown',revision=revision+1,payload=json_set(payload,'$.state','unknown','$.revision',revision+1,'$.reason','owner-restarted-no-replay') WHERE state IN ('queued','quiescing','running','releasing')").run();if(interrupted.changes)this.bump();
   this.db.exec('COMMIT');
  }catch(error){try{this.db.close();}finally{this.ownership.close();}throw error;}
 }
 revision(){return Number(this.db.prepare('SELECT value FROM revision WHERE id=1').get()!.value);}
 get(id:string):Job|undefined{const row=this.db.prepare('SELECT payload FROM jobs WHERE id=?').get(id);return row?JSON.parse(String(row.payload)):undefined;}
 command(account:string,command:string):Job|undefined{const row=this.db.prepare('SELECT payload FROM jobs WHERE account=? AND command=?').get(account,command);return row?JSON.parse(String(row.payload)):undefined;}
 insert(job:Job){this.db.prepare('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)').run(job.id,job.accountId,job.commandId,job.state,job.createdAt,job.revision,JSON.stringify(job));this.bump();}
 save(job:Job){job.updatedAt=Date.now();job.revision++;this.db.prepare('UPDATE jobs SET state=?,revision=?,payload=? WHERE id=?').run(job.state,job.revision,JSON.stringify(job),job.id);this.bump();}
 private bump(){this.db.exec('UPDATE revision SET value=value+1 WHERE id=1');}
 // Unknown visibility only affects its own receipt/projection, not native/app
 // maintenance. Active calls still join real shutdown; legacy fences stay held.
 unsettled(except?:string){return this.db.prepare("SELECT id,state FROM jobs WHERE state IN ('queued','quiescing','running','releasing','unknown') AND (state!='unknown' OR json_extract(payload,'$.presentation') IS NULL OR json_extract(payload,'$.fence') IS NOT NULL) AND id!=? LIMIT 1").get(except??'');}
 page(account:string,limit:number,before?:{created:number;id:string}){
  return before?this.db.prepare('SELECT id,command,state,created,revision FROM jobs WHERE account=? AND (created<? OR (created=? AND id<?)) ORDER BY created DESC,id DESC LIMIT ?').all(account,before.created,before.created,before.id,limit):this.db.prepare('SELECT id,command,state,created,revision FROM jobs WHERE account=? ORDER BY created DESC,id DESC LIMIT ?').all(account,limit);
 }
 fence():Json|undefined{const row=this.db.prepare('SELECT payload FROM fence WHERE id=1').get();return row?JSON.parse(String(row.payload)):undefined;}
 setFence(value:Json){this.db.prepare('INSERT INTO fence VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload').run(JSON.stringify(value));}
 private participantRecord(fenceId:string):Json|undefined{const row=this.db.prepare('SELECT command_id,signature,payload FROM participant_releases WHERE fence_id=?').get(fenceId);return row?{commandId:row.command_id,signature:row.signature,payload:JSON.parse(String(row.payload))}:undefined;}
 private saveAdmission(context:Readonly<FenceContext>,payload:Json){this.db.prepare('INSERT INTO participant_releases VALUES(?,?,?,?) ON CONFLICT(fence_id) DO UPDATE SET signature=excluded.signature,payload=excluded.payload').run(context.fenceId,context.commandId,'admission-v1',JSON.stringify(payload));}
 /** The existing per-fence journal retains original acquisition/refusal authority.
  * Legacy release rows and legacy holds never acquire this authority retroactively. */
 acquireAdmission(context:Readonly<FenceContext>,busy:boolean):boolean{
  const row=this.participantRecord(context.fenceId),held=this.fence();
  if(row){
   const value=row.payload;
   if(row.commandId!==context.commandId||value.kind!=='admission-v1'||exact(value.context)!==exact(context))throw Error('Admission differs from retained original authority');
   if(value.abort||value.release||value.acquisition!==false)throw Error('Original admission cannot be acquired again');
   return false;
  }
  // Preserve the established refusal for a legacy hold, without inventing a
  // refusal receipt that would contradict its unknown original acquisition.
  if(held?.fenceId===context.fenceId)return false;
  const acquired=!busy&&!held;
  this.db.exec('BEGIN IMMEDIATE');try{
   this.saveAdmission(context,{kind:'admission-v1',context,acquisition:acquired});
   if(acquired)this.setFence(context);
   this.db.exec('COMMIT');return acquired;
  }catch(error){this.db.exec('ROLLBACK');throw error;}
 }
 releaseReceipt(fenceId:string):Json|undefined{
  const row=this.participantRecord(fenceId);if(!row)return;
  if(row.payload.kind!=='admission-v1')return {commandId:row.commandId,signature:row.signature,...row.payload};
  if(row.payload.abort)throw Error('Admission abort requires its distinct retained proof');
  return row.payload.release?{commandId:row.commandId,...row.payload.release}:undefined;
 }
 completeRelease(context:Json,signature:string,evidence:Json|undefined,settlement?:Json){
  this.db.exec('BEGIN IMMEDIATE');try{
   const row=this.participantRecord(context.fenceId),release={context,evidence:evidence??null,releasedAt:Date.now(),signature,...(settlement?{settlement}:{})};
   if(row){
    if(row.payload.kind!=='admission-v1'||row.commandId!==context.commandId||exact(row.payload.context)!==exact(context)||row.payload.acquisition!==true||row.payload.abort)throw Error('Release differs from original admission');
    this.saveAdmission(context as FenceContext,{...row.payload,release});
   }else this.db.prepare('INSERT INTO participant_releases VALUES(?,?,?,?)').run(context.fenceId,context.commandId,signature,JSON.stringify(release));
   this.db.exec('DELETE FROM fence WHERE id=1');this.db.exec('COMMIT');
  }catch(error){this.db.exec('ROLLBACK');throw error;}
 }
 abortAdmission(context:Readonly<FenceContext>,input:AdmissionAbortProof):AdmissionAbortReceipt{
  const proof=admissionAbortProof(context,input);
  this.db.exec('BEGIN IMMEDIATE');try{
   const row=this.participantRecord(context.fenceId),record=row?.payload,held=this.fence();
   if(!record||record.kind!=='admission-v1'||row?.commandId!==context.commandId||exact(record.context)!==exact(context))throw Error('No exact original admission acquisition or refusal journal');
   if(record.abort){
    if(exact(record.abort.proof)!==exact(proof))throw Error('Admission abort proof differs from retained receipt');
    this.db.exec('COMMIT');return structuredClone(record.abort.receipt);
   }
   if(held&&exact(held)!==exact(context))throw Error('Another or uncertain recovery fence is held');
   let status:AdmissionAbortReceipt['status'];
   if(record.acquisition===true){
    if(!held&&exact(record.release?.settlement)!==exact({outcome:'unchanged',proof:{kind:'admission-refused'}}))throw Error('Acquired admission has no pre-effect settlement evidence');
    status='released';
   }else if(record.acquisition===false&&!held)status='not-acquired';
   else throw Error('Original admission remains unknown or contradicts held intake');
   const {fenceId,commandId,instanceId,dataScope}=context;
   const receipt:AdmissionAbortReceipt={ownerId:'recovery',fenceId,commandId,instanceId,dataScope,status,receiptId:randomUUID()};
   this.saveAdmission(context,{...record,abort:{proof,receipt}});
   if(held)this.db.exec('DELETE FROM fence WHERE id=1');
   this.db.exec('COMMIT');return receipt;
  }catch(error){this.db.exec('ROLLBACK');throw error;}
 }
 close(){if(this.closed)return;this.closed=true;try{this.db.close();}finally{this.ownership.close();}}
}
