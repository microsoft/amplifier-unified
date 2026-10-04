import {DatabaseSync} from 'node:sqlite';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {serviceIdentity,sameService} from '@amplifier/unified-distribution-update-owner';

const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const digest=value=>createHash('sha256').update(canonical(value)).digest('hex');
const exact=value=>{
 const binding=Object.fromEntries(['fenceId','commandId','purpose','instanceId','dataScope'].map(key=>{if(typeof value[key]!=='string'||!value[key]||value[key].length>256)throw Error('Bounded facade fence identity required');return [key,value[key]];}));
 if(binding.purpose==='service-stop'){
  binding.serviceIdentity=serviceIdentity(value.serviceIdentity);
  if(binding.serviceIdentity.instanceId!==binding.instanceId||binding.serviceIdentity.dataScope!==binding.dataScope)throw Error('Facade service identity must match its fence');
 }
 return binding;
};
const serviceProof=(binding,outcome,proof)=>{
 const expected=binding.serviceIdentity,observed=serviceIdentity(proof?.observed);
 if(proof.kind!=='service-lifecycle'||!sameService(proof.expected,expected)||
    !sameService({...observed,instanceId:expected.instanceId},expected)||observed.instanceId!==proof.instanceId)
  throw Error('Exact service facade release proof required');
 if(outcome==='ready'){
  if(proof.serviceOutcome!=='resumed'||!proof.resumeCommandId||!proof.exitReceiptId||!proof.readyReceiptId||proof.refusalReceiptId!==undefined)throw Error('Confirmed service resume proof required');
 }else if(proof.serviceOutcome!=='stop-refused'||!proof.refusalReceiptId||proof.resumeCommandId!==undefined||proof.exitReceiptId!==undefined||proof.readyReceiptId!==undefined)
  throw Error('Confirmed no-effect service refusal proof required');
};

/** Own only the child facade's forwarding lifetime, never the external service. */
export class FacadeFence {
 constructor({directory,id,onMayBeIdle=()=>{},serviceStop=false,retentionHide=false,managedFiles=false}){
  mkdirSync(directory,{recursive:true,mode:0o700});this.id=id;this.serviceStop=serviceStop;this.retentionHide=retentionHide;this.managedFiles=managedFiles;this.onMayBeIdle=onMayBeIdle;this.calls=0;this.waiting=false;this.closed=false;this.closing=false;this.drainers=[];
  this.lease=new DatabaseSync(join(directory,'owner-lock.sqlite3'));
  try{this.lease.exec('PRAGMA busy_timeout=0; PRAGMA journal_mode=DELETE; BEGIN EXCLUSIVE');}
  catch(error){this.lease.close();throw error;}
  try{this.db=new DatabaseSync(join(directory,'intake.sqlite3'));this.db.exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS fence(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,signature TEXT NOT NULL)');const prior=this.fence();if(prior)this.db.prepare('UPDATE fence SET body=? WHERE id=1').run(canonical({...prior,phase:'unknown'}));}
  catch(error){this.lease.close();throw error;}
  this.participant={id,...(serviceStop?{serviceStop:{version:1}}:{}),...(retentionHide?{retentionHide:{version:1}}:{}),...(managedFiles?{managedFiles:{version:1,preservesCanonical:true}}:{}),acquire:async context=>this.acquire(context),reconcileRelease:async context=>this.release(context,context.outcome,context.proof)};
 }
 fence(){const row=this.db.prepare('SELECT body FROM fence WHERE id=1').get();return row?JSON.parse(row.body):null;}
 async run(passive,work){
  if(this.closed||this.closing)throw Error('Facade is closed');
  if(!passive&&this.fence())throw Error('Facade intake is closed; no external request was admitted');
  this.calls++;
  try{return await work();}finally{this.calls--;if(!this.calls){for(const resolve of this.drainers.splice(0))resolve();}if(this.waiting&&!this.calls){this.waiting=false;try{Promise.resolve(this.onMayBeIdle()).catch(()=>{});}catch{/* advisory only */}}}
 }
 acquire(context){
  if(this.closed||this.closing)throw Error('Facade is closed');
  const binding=exact(context);
  if(binding.purpose==='service-stop'&&!this.serviceStop)throw Error('Facade service stop is not configured');
  if(binding.purpose==='retention-hide'&&!this.retentionHide)throw Error('Facade retention protection is not configured');
  if(binding.purpose==='managed-files-disposal'&&!this.managedFiles)throw Error('Facade managed-file protection is not configured');
  if(this.fence()||this.calls){this.waiting=true;return null;}
  if(this.db.prepare('SELECT 1 FROM releases WHERE id=?').get(binding.fenceId))throw Error('A released facade fence cannot be reused');
  this.db.prepare('INSERT INTO fence VALUES(1,?)').run(canonical({...binding,phase:'held'}));
  return {ownerId:this.id,fenceId:binding.fenceId,release:async(outcome,proof)=>this.release(binding,outcome,proof,true),...(binding.purpose==='retention-hide'?{inspectRetentionReferences:async({sessions,limit})=>{
   const held=this.fence();
   if(!held||held.phase!=='held'||canonical(exact(held))!==canonical(binding)||this.calls)throw Error('Exact held facade retention lease required');
   if(!Array.isArray(sessions)||!sessions.length||sessions.length>101||limit!==101||new Set(sessions).size!==sessions.length||sessions.some(s=>typeof s!=='string'||!/^ahp-session:\/.{1,480}$/.test(s)))throw Error('Bounded selected retention family required');
   // This adapter owns only forwarding lifetime, with no deferred session work.
   // External service/native authorities require their own independent proof.
   return {coverage:'complete',protected:[],omissions:[]};
  }}:{}),...(binding.purpose==='managed-files-disposal'?{inspectManagedFilesReferences:async selection=>{
   const held=this.fence();
   if(!held||held.phase!=='held'||canonical(exact(held))!==canonical(binding)||this.calls)throw Error('Exact held facade managed-files lease required');
   const {sessions,limit,allocation}=selection??{};
   if(!Array.isArray(sessions)||!sessions.length||sessions.length>101||limit!==101||new Set(sessions).size!==sessions.length||sessions.some(s=>typeof s!=='string'||!/^ahp-session:\/[^\s\x00-\x1f]{1,480}$/.test(s))||!allocation||Object.keys(allocation).sort().join(',')!=='allocationHash,allocationId,bytes,entryCount,executionDirectory,treeHash'||!/^([a-f0-9]{8})(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(allocation.allocationId)||!['allocationHash','treeHash'].every(k=>/^[a-f0-9]{64}$/.test(allocation[k]))||!['entryCount','bytes'].every(k=>Number.isSafeInteger(allocation[k])&&allocation[k]>=0)||typeof allocation.executionDirectory!=='string'||!allocation.executionDirectory.startsWith('/')||allocation.executionDirectory.length>8192||/[\x00-\x1f]/.test(allocation.executionDirectory))throw Error('Bounded exact managed allocation and family required');
   const selected={sessions,limit,allocation};
   if(held.managedFilesSelection&&canonical(held.managedFilesSelection)!==canonical(selected))throw Error('Managed-files facade review binding changed');
   this.db.prepare('UPDATE fence SET body=? WHERE id=1').run(canonical({...held,managedFilesSelection:selected}));
   // This facade has no deferred filesystem work or retained external-file references.
   // Downstream authorities remain independently required participants.
   return {coverage:'complete',protected:[],omissions:[]};
  }}:{})};
 }
 release(context,outcome,proof,liveRollback=false){
  const binding=exact(context),signature=digest({binding,outcome,proof:proof??null});
  const prior=this.db.prepare('SELECT signature FROM releases WHERE id=?').get(binding.fenceId);
  if(prior){if(prior.signature!==signature)throw Error('Facade release proof changed');return;}
  const held=this.fence();if(!held||canonical(exact(held))!==canonical(binding))throw Error('Exact held facade fence required');
  // An unknown effect invalidates even the original live pre-effect rollback.
  // Only exact authenticated outcome evidence can reopen this held fence.
  if(outcome==='unknown'){this.db.prepare('UPDATE fence SET body=? WHERE id=1').run(canonical({...held,phase:'unknown'}));return;}
  if(!['ready','unchanged'].includes(outcome))throw Error('Invalid facade release outcome');
  if(proof?.kind!=='admission-refused'){
   if(proof?.verified!==true||proof.fenceId!==binding.fenceId||proof.commandId!==binding.commandId||proof.dataScope!==binding.dataScope||proof.outcome!==outcome||!proof.instanceId||!proof.receiptId||outcome==='unchanged'&&proof.instanceId!==binding.instanceId||outcome==='ready'&&proof.instanceId===binding.instanceId)throw Error('Authenticated exact facade release proof required');
   if(binding.purpose==='service-stop')serviceProof(binding,outcome,proof);
  }else if(!liveRollback||outcome!=='unchanged'||held.phase!=='held')throw Error('Pre-effect refusal cannot prove a replacement');
  this.db.exec('BEGIN IMMEDIATE');
  try{this.db.prepare('INSERT INTO releases VALUES(?,?)').run(binding.fenceId,signature);this.db.exec('DELETE FROM fence WHERE id=1; COMMIT');}catch(error){this.db.exec('ROLLBACK');throw error;}
 }
 async drain(){this.closing=true;if(this.calls)await new Promise(resolve=>this.drainers.push(resolve));}
 close(){if(this.closed)return;if(this.calls)throw Error('Facade forwarding is still active');this.closed=true;this.db.close();this.lease.close();}
}
