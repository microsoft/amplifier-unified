import {preflightLedger,validateLedger,markLedger} from './sqlite-ledger-schema.js';
import {OwnerAdmissionJournal} from './owner-admission-journal.js';
import {DatabaseSync} from 'node:sqlite';
import {chmodSync} from 'node:fs';
import {join,resolve,sep} from 'node:path';
import {createHash} from 'node:crypto';
import {createAuthority,authorityKey,readAuthority} from './manual-authority.js';
import {token} from './types.js';
import {serviceIdentity,sameService} from './service-types.js';
const canonical=(v:any):string=>JSON.stringify(v,(_k,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const binding=(v:any)=>{
 const b:any=Object.fromEntries(['fenceId','commandId','purpose','instanceId','dataScope'].map(k=>[k,token(v[k])]));
 if(b.purpose==='service-stop'){b.serviceIdentity=serviceIdentity(v.serviceIdentity);if(b.serviceIdentity.instanceId!==b.instanceId||b.serviceIdentity.dataScope!==b.dataScope)throw Error('manual_ingress_identity_mismatch');}return b;
};
/** Owns network forwarding lifetime and its own hold ledger only. No queued
 * business actions, history/session references or retained managed-file paths.
 * Business mutation owners MUST remain independent required participants. */
export async function createManualIngressGate(options:{directory:string;id:string;onMayBeIdle?:()=>void}){
 const id=token(options.id);
 let newNamespace=true;
 try{await createAuthority(options.directory,{schema:'manual-ingress-v1'});}
 catch(e){if((e as NodeJS.ErrnoException).code!=='EEXIST')throw e;newNamespace=false;const key=await authorityKey(options.directory);const authority=await readAuthority(options.directory,key);if(!authority||Object.keys(authority).length!==1||authority.schema!=='manual-ingress-v1')throw Error('manual_ingress_ledger_unconfirmed');}
 const lockPath=join(options.directory,'lock.sqlite3'),dbPath=join(options.directory,'intake.sqlite3');
 const fresh=preflightLedger(dbPath,'manual_ingress',undefined,!newNamespace);
 const lock=new DatabaseSync(lockPath);chmodSync(lockPath,0o600);
 try{lock.exec('PRAGMA busy_timeout=0; BEGIN EXCLUSIVE');}catch(e){lock.close();throw e;}
 let db:DatabaseSync|undefined,begun=false;
 try{
  db=new DatabaseSync(dbPath);chmodSync(dbPath,0o600);
  db.exec('PRAGMA synchronous=FULL');if(fresh)db.exec('PRAGMA journal_mode=WAL');
  db.exec('BEGIN IMMEDIATE');begun=true;validateLedger(db,'manual_ingress',undefined,fresh);
  db.exec('CREATE TABLE IF NOT EXISTS held(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,signature TEXT NOT NULL)');
  // Only validated legacy schemas migrate. Missing v2 authority is not repaired.
  if(Number(db.prepare('PRAGMA user_version').get()!.user_version)<2)db.exec('CREATE TABLE admission_attempts(id TEXT PRIMARY KEY,body TEXT NOT NULL)');
  markLedger(db,2);db.exec('COMMIT');begun=false;
 }catch(e){if(begun)db?.exec('ROLLBACK');db?.close();lock.close();throw e;}
 const ledger=db,admissions=new OwnerAdmissionJournal(ledger,id);
 let active=0,closed=false;
 const read=()=>{if(closed)throw Error('manual_ingress_closed');const r=ledger.prepare('SELECT body FROM held WHERE id=1').get();return r?JSON.parse(String(r.body)):null;};
 const save=(v:any)=>ledger.prepare('INSERT OR REPLACE INTO held VALUES(1,?)').run(canonical(v));
 async function release(c:any,outcome:string,proof:any,live=false){
  const b=binding(c),sig=createHash('sha256').update(canonical({b,outcome,proof})).digest('hex'),prior=ledger.prepare('SELECT signature FROM releases WHERE id=?').get(b.fenceId);
  if(b.purpose==='distribution-update')admissions.assertNotAborted(b);
  if(prior){if(prior.signature!==sig)throw Error('manual_ingress_release_changed');return;}
  const h=read();if(!h||canonical(binding(h))!==canonical(b))throw Error('manual_ingress_release_unconfirmed');
  if(outcome==='unknown'){save({...h,phase:'unknown'});return;}
  if(!['ready','unchanged'].includes(outcome))throw Error('manual_ingress_release_unconfirmed');
  // Recovery is maintenance in the same host identity. A new process being
  // ready cannot establish that this exact recovery job finished unchanged.
  if(b.purpose==='recovery'&&outcome!=='unchanged')throw Error('manual_ingress_release_unconfirmed');
  if(proof?.kind==='admission-refused'){
   if(!live||outcome!=='unchanged'||h.phase!=='held')throw Error('manual_ingress_release_unconfirmed');
  }else{
   if(proof?.verified!==true||proof.fenceId!==b.fenceId||proof.commandId!==b.commandId||proof.dataScope!==b.dataScope||proof.outcome!==outcome||!proof.receiptId||
    (outcome==='unchanged'?proof.instanceId!==b.instanceId:!proof.instanceId||proof.instanceId===b.instanceId))throw Error('manual_ingress_release_unconfirmed');
   if(b.purpose==='service-stop'){
    const observed=serviceIdentity(proof.observed);
    if(proof.kind!=='service-lifecycle'||!sameService(proof.expected,b.serviceIdentity)||!sameService({...observed,instanceId:b.instanceId},b.serviceIdentity)||observed.instanceId!==proof.instanceId||
      (outcome==='ready'?proof.serviceOutcome!=='resumed'||!proof.exitReceiptId||!proof.readyReceiptId||!proof.resumeCommandId:
       proof.serviceOutcome!=='stop-refused'||!proof.refusalReceiptId))throw Error('manual_ingress_release_unconfirmed');
   }
  }
  ledger.exec('BEGIN IMMEDIATE');try{if(b.purpose==='distribution-update'&&outcome==='unchanged')admissions.released(b);ledger.prepare('INSERT INTO releases VALUES(?,?)').run(b.fenceId,sig);ledger.exec('DELETE FROM held; COMMIT');}catch(e){ledger.exec('ROLLBACK');throw e;}
 }
 const participant={id,serviceStop:{version:1 as const},retentionHide:{version:1 as const},managedFiles:{version:1 as const,preservesCanonical:true as const},
  async acquire(c:any){
   const b=binding(c);if(!['service-stop','distribution-update','recovery','retention-hide','managed-files-disposal'].includes(b.purpose))return null;
   if(b.purpose==='distribution-update'&&admissions.wasRefused(b))return null;
   // Maintenance protects this adapter's state, not network lifetime. The
   // initiating HTTP/WS request must be able to deliver its response/receipt.
   if(read()||(drainsNetwork(b.purpose)&&active)){if(b.purpose==='distribution-update')admissions.record(b,'not-acquired');return null;}
   if(ledger.prepare('SELECT 1 FROM releases WHERE id=?').get(b.fenceId))throw Error('manual_ingress_fence_reused');
   ledger.exec('BEGIN IMMEDIATE');try{if(b.purpose==='distribution-update')admissions.record(b,'acquired');save({...b,phase:'held'});ledger.exec('COMMIT');}catch(e){ledger.exec('ROLLBACK');throw e;}let live=true;
   const inspect=(request:any,managed:boolean)=>{
    const h=read();if(!live||!h||h.phase!=='held'||canonical(binding(h))!==canonical(b))throw Error('manual_ingress_lease_not_live');
    const {sessions,limit=101,allocation:a}=request??{};
    if(!Array.isArray(sessions)||!sessions.length||sessions.length>101||!Number.isInteger(limit)||limit<1||limit>101||sessions.length>limit||new Set(sessions).size!==sessions.length||
      sessions.some((s:any)=>typeof s!=='string'||!/^ahp-session:\/[^\s\x00-\x1f]{1,480}$/.test(s)))throw Error('manual_ingress_selection_invalid');
    if(managed){
     if(!a||Object.keys(a).sort().join(',')!=='allocationHash,allocationId,bytes,entryCount,executionDirectory,treeHash'||!/^([a-f0-9]{8})(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(a.allocationId)||
      !['allocationHash','treeHash'].every(k=>typeof a[k]==='string'&&/^[a-f0-9]{64}$/.test(a[k]))||!['entryCount','bytes'].every(k=>Number.isSafeInteger(a[k])&&a[k]>=0)||
      typeof a.executionDirectory!=='string'||!a.executionDirectory.startsWith('/')||a.executionDirectory==='/'||a.executionDirectory.length>8192||/[\x00-\x1f\\]/.test(a.executionDirectory)||resolve(a.executionDirectory)!==a.executionDirectory)
       throw Error('manual_ingress_selection_invalid');
     const path=a.executionDirectory,own=options.directory;
     if(path===own||own.startsWith(path+sep)||path.startsWith(own+sep))throw Error('manual_ingress_state_protected');
    }
    const selection=canonical({sessions,limit,...(managed?{allocation:a}:{})});
    if(h.selection&&h.selection!==selection)throw Error('manual_ingress_selection_changed');save({...h,selection});
    return {coverage:'complete',protected:[],omissions:[]};
   };
   return {ownerId:id,fenceId:b.fenceId,
    ...(b.purpose==='retention-hide'?{inspectRetentionReferences:async(r:any)=>inspect(r,false)}:{}),
    ...(b.purpose==='managed-files-disposal'?{inspectManagedFilesReferences:async(r:any)=>inspect(r,true)}:{}),
    release:async(outcome:string,proof:any)=>{const original=live;live=false;await release(b,outcome,proof,original);}};
  },
  reconcileRelease:async(r:any)=>release(r,r.outcome,r.proof),
  abortAdmission:async(c:any)=>admissions.abort(c,{held:read(),active,removeHeld:()=>{ledger.exec('DELETE FROM held');}}),
 };
 return {participant,
  enter(){const h=read();if(h&&drainsNetwork(h.purpose))return null;active++;let done=false;return ()=>{if(!done){done=true;active--;
   if(active===0)queueMicrotask(()=>{if(!closed)try{options.onMayBeIdle?.();}catch{/* Notification cannot alter admission truth. */}});
  }};},
  inspect(){return {active,held:read()};},
  close(){if(closed)return;if(active)throw Error('manual_ingress_active');closed=true;ledger.close();lock.close();},
 };
}
const drainsNetwork=(purpose:string)=>purpose==='service-stop'||purpose==='distribution-update';
