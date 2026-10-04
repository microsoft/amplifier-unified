import {createHash} from 'node:crypto';
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const hash=value=>createHash('sha256').update(canonical(value)).digest('hex');
const token=value=>{if(typeof value!=='string'||!value||value.length>200||/[\x00-\x1f]/.test(value))throw Error('owner_admission_binding_invalid');return value;};
const bound=value=>{const result=Object.fromEntries(['commandId','fenceId','purpose','instanceId','dataScope'].map(key=>[key,token(value?.[key])]));if(result.purpose!=='distribution-update')throw Error('owner_admission_purpose_invalid');return result;};
/** Local standalone-owner mechanics. The trusted host authenticates supervisor
 * evidence before this callback; no client action exposes this proof port.
 * Caller retains its actual process/worker exclusion throughout each transaction. */
export class OwnerAdmissionJournal {
 constructor(db,ownerId){this.db=db;this.ownerId=token(ownerId);}
 read(binding){const row=this.db.prepare('SELECT body FROM admission_attempts WHERE id=?').get(binding.fenceId);if(!row)return null;const value=JSON.parse(row.body);if(value.ownerId!==this.ownerId||canonical(value.binding)!==canonical(binding)||!['attempting','acquired','not-acquired','released'].includes(value.disposition))throw Error('owner_admission_binding_conflict');return value;}
 save(value){this.db.prepare('INSERT INTO admission_attempts VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body').run(value.binding.fenceId,canonical(value));}
 record(context,disposition){const binding=bound(context),prior=this.read(binding);if(prior?.receipt||prior?.disposition==='released')throw Error('owner_admission_already_settled');if(prior?.disposition==='not-acquired'){if(disposition==='not-acquired')return;throw Error('owner_admission_already_refused');}if(disposition==='not-acquired'&&prior?.disposition==='acquired')return;this.save({ownerId:this.ownerId,binding,disposition});}
 released(context){const prior=this.read(bound(context));if(!prior)return;if(prior.receipt)throw Error('owner_admission_already_settled');if(prior.disposition!=='acquired')throw Error('owner_admission_release_unconfirmed');this.save({...prior,disposition:'released'});}
 refused(context){return this.read(bound(context))?.disposition==='not-acquired';}
 assertNotAborted(context){if(this.read(bound(context))?.receipt)throw Error('owner_admission_already_settled');}
 request(context){const binding=bound(context),proof=context?.proof;if(!proof||Array.isArray(proof)||Object.keys(proof).sort().join(',')!=='commandId,dataScope,fenceId,instanceId,kind,purpose,receiptId,verified'||proof.kind!=='distribution-admission-abort'||proof.verified!==true||canonical(bound(proof))!==canonical(binding))throw Error('owner_admission_abort_proof_unconfirmed');token(proof.receiptId);const prior=this.read(binding),signature=hash(proof);if(!prior)throw Error('owner_admission_attempt_unconfirmed');if(prior.receipt&&prior.proofSignature!==signature)throw Error('owner_admission_abort_proof_changed');return {binding,prior,signature};}
 receipt(context){const {prior}=this.request(context);return prior.receipt?structuredClone(prior.receipt):null;}
 abort(context,{held,active,removeHeld}){
  const {binding,prior,signature}=this.request(context);if(prior.receipt)return structuredClone(prior.receipt);
  if(active!==0)throw Error('owner_admission_work_active');
  if(prior.disposition==='attempting'||held&&(held.ownerId!==this.ownerId||canonical(bound(held))!==canonical(binding)||prior.disposition!=='acquired')||!held&&prior.disposition==='acquired')throw Error('owner_admission_hold_unconfirmed');
  const {purpose,...identity}=binding,status=prior.disposition==='not-acquired'?'not-acquired':'released';
  const receipt={...identity,ownerId:this.ownerId,status,receiptId:hash({ownerId:this.ownerId,binding,status,signature})};
  this.db.exec('BEGIN IMMEDIATE');try{this.save({...prior,disposition:status,proofSignature:signature,receipt});if(held)removeHeld();this.db.exec('COMMIT');}catch(error){this.db.exec('ROLLBACK');throw error;}
  return structuredClone(receipt);
 }
}
export function migrateAdmissionJournal(db){db.exec('BEGIN IMMEDIATE');try{if(Number(db.prepare('PRAGMA user_version').get().user_version)<2)db.exec('CREATE TABLE admission_attempts(id TEXT PRIMARY KEY,body TEXT NOT NULL)');db.exec('PRAGMA user_version=2;COMMIT');}catch(error){db.exec('ROLLBACK');throw error;}}
