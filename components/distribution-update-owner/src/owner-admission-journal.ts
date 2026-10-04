import {createHash} from 'node:crypto';
import type {DatabaseSync} from 'node:sqlite';
import {admissionAbortBinding, type AdmissionAbortBinding, type AdmissionAbortOwnerReceipt} from './admission-abort.js';
import {token} from './types.js';

const canonical=(v:unknown):string=>JSON.stringify(v,(_k,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const hash=(v:unknown)=>createHash('sha256').update(canonical(v)).digest('hex');
const bound=(v:any)=>admissionAbortBinding(Object.fromEntries(['commandId','fenceId','purpose','instanceId','dataScope'].map(k=>[k,v?.[k]])));
type RecordValue={ownerId:string;binding:AdmissionAbortBinding;disposition:'acquired'|'not-acquired'|'released';proofSignature?:string;receipt?:AdmissionAbortOwnerReceipt};

/** Shared mechanics only for synchronous Node forwarding gates. Business owners
 * must prove their own effects and attempted subowners independently. The caller
 * owns the database schema, lifetime exclusion and acquisition transaction. */
export class OwnerAdmissionJournal {
 constructor(private db:DatabaseSync, private ownerId:string){token(ownerId);}
 private read(b:AdmissionAbortBinding):RecordValue|null {
  const row=this.db.prepare('SELECT body FROM admission_attempts WHERE id=?').get(b.fenceId);
  if(!row)return null;
  const value=JSON.parse(String(row.body)) as RecordValue;
  if(value.ownerId!==this.ownerId||canonical(value.binding)!==canonical(b)||!['acquired','not-acquired','released'].includes(value.disposition))throw Error('owner_admission_binding_conflict');
  return value;
 }
 private save(value:RecordValue){this.db.prepare('INSERT INTO admission_attempts VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body').run(value.binding.fenceId,canonical(value));}
 wasRefused(context:unknown){
  const prior=this.read(bound(context));
  if(prior?.receipt)throw Error('owner_admission_already_settled');
  return prior?.disposition==='not-acquired';
 }
 record(context:unknown,disposition:'acquired'|'not-acquired'){
  const b=bound(context),prior=this.read(b);
  if(prior?.receipt||prior?.disposition==='released')throw Error('owner_admission_already_settled');
  // A repeated busy observation cannot erase an earlier actual acquisition.
  if(disposition==='not-acquired'&&prior)return;
  // Refusal is the durable outcome of this exact attempt, even after work
  // finishes or the owner reopens. The Host must use a new fence to retry;
  // otherwise recovery could mistake a later acquisition for no effect.
  if(prior?.disposition==='not-acquired')throw Error('owner_admission_refused');
  this.save({ownerId:this.ownerId,binding:b,disposition});
 }
 released(context:unknown){
  const b=bound(context),prior=this.read(b);
  // Legacy held fences remain usable by their original release API, but never
  // gain retroactive admission-abort authority from a missing journal row.
  if(!prior)return;
  if(prior.receipt)throw Error('owner_admission_already_settled');
  if(prior.disposition!=='acquired')throw Error('owner_admission_release_unconfirmed');
  this.save({...prior,disposition:'released'});
 }
 assertNotAborted(context:unknown){if(this.read(bound(context))?.receipt)throw Error('owner_admission_already_settled');}
 abort(context:any,{held,active,removeHeld}:{held:unknown;active:number;removeHeld:()=>void}):AdmissionAbortOwnerReceipt {
  const b=bound(context),p=context?.proof;
  if(!p||Object.keys(p).sort().join(',')!=='commandId,dataScope,fenceId,instanceId,kind,purpose,receiptId,verified'||p.kind!=='distribution-admission-abort'||p.verified!==true||canonical(bound(p))!==canonical(b))throw Error('owner_admission_abort_proof_unconfirmed');
  token(p.receiptId);
  const prior=this.read(b),signature=hash(p);
  if(!prior)throw Error('owner_admission_attempt_unconfirmed');
  if(prior.receipt){if(prior.proofSignature!==signature)throw Error('owner_admission_abort_proof_changed');return structuredClone(prior.receipt);}
  if(active!==0)throw Error('owner_admission_work_active');
  if(held&&(canonical(bound(held))!==canonical(b)||prior.disposition!=='acquired')||!held&&prior.disposition==='acquired')throw Error('owner_admission_hold_unconfirmed');
  const {purpose,...binding}=b,status=prior.disposition==='not-acquired'?'not-acquired':'released';
  const receipt:AdmissionAbortOwnerReceipt={...binding,ownerId:this.ownerId,status,receiptId:hash({ownerId:this.ownerId,binding,status,signature})};
  // The receipt and hold removal are one durable commit. A lost reply is then a
  // passive receipt read on retry, never another acquire or business mutation.
  this.db.exec('BEGIN IMMEDIATE');
  try{this.save({...prior,disposition:status,proofSignature:signature,receipt});if(held)removeHeld();this.db.exec('COMMIT');}
  catch(error){this.db.exec('ROLLBACK');throw error;}
  return structuredClone(receipt);
 }
}
