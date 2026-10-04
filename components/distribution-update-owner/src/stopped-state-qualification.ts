import {createHash} from 'node:crypto';
import {isDeepStrictEqual as equal} from 'node:util';
import {isAbsolute,resolve} from 'node:path';
import {inspectExistingStateBindings,type ExistingStateBinding} from './existing-state.js';
import {serviceIdentity,type ServiceIdentity} from './service-types.js';
import {witnessDigest,type SystemdSourceWitness} from './systemd-witness.js';
import {type FailedBootstrapQualification} from './manual-bootstrap-recovery.js';

const hash=(v:unknown)=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const sha=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const count=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>=0;
const sort=(a:string[])=>[...a].sort();
function unique(ids:string[]){return Array.isArray(ids)&&ids.length>0&&ids.every(x=>typeof x==='string'&&x.length>0)&&new Set(ids).size===ids.length;}
export interface StoppedStateArtifact {packageName:string;version:string;revision:string;digest:string}
export interface StoppedStateRoot {id:string;path:string}
export interface HistoricalUnknownEvidence {count:number;digest:string}
export interface OwnerStoppedStateInspection {
 schema:'owner-stopped-state-v1';ownerId:string;artifact:StoppedStateArtifact;
 roots:StoppedStateRoot[];coverage:'complete'|'unsupported';
 activeWriters:number;pendingEffects:number;unaccountedEffects:number;
 historicalUnknown:HistoricalUnknownEvidence;admissionObserved:boolean;evidenceDigest:string;
}
export interface StoppedStateOwnerInspector {
 ownerId:string;artifact:StoppedStateArtifact;roots:StoppedStateRoot[];
 /** Independently verified pre-failure unknown receipts, not derived from the
  * current state. Matching retains uncertainty; it never authorizes replay. */
 historicalBaseline:HistoricalUnknownEvidence;
 inspectStoppedState():Promise<OwnerStoppedStateInspection>;
}
export interface StoppedStateInventory {
 schema:'stopped-storage-inventory-v1';coverage:'complete'|'unsupported';
 roots:StoppedStateRoot[];digest:string;
}
export interface StoppedStateExclusion {
 schema:'stopped-writer-exclusion-v1';coverage:'complete'|'unsupported';
 activeWriters:number;publicReadyObserved:boolean;admissionObserved:boolean;digest:string;
}
export interface StoppedStateAggregateOptions {
 predecessor:ServiceIdentity;witness:SystemdSourceWitness;
 predecessorBindings:ExistingStateBinding[];successorBindings:ExistingStateBinding[];
 expectedOwners:string[];roots:StoppedStateRoot[];inspectors:StoppedStateOwnerInspector[];
 /** The signed composition supplies actual OS/service/native-writer inspection.
  * Every writer, including restored/manual services, must be accounted for. */
 inspectWriterExclusion():Promise<StoppedStateExclusion>;
 /** Recompute all declared roots and receipt stores read-only. A copied audit
  * JSON or a declaration-only storage inventory does not meet this contract. */
 inspectStorageInventory():Promise<StoppedStateInventory>;
}
function roots(value:StoppedStateRoot[]){
 if(!Array.isArray(value)||!unique(value.map(r=>r?.id))||value.some(r=>!isAbsolute(r.path)||resolve(r.path)!==r.path))throw Error('stopped_state_roots_invalid');
 return [...value].sort((a,b)=>a.id.localeCompare(b.id));
}
function artifact(a:StoppedStateArtifact){
 if(!a||typeof a.packageName!=='string'||!a.packageName||typeof a.version!=='string'||!a.version||
  typeof a.revision!=='string'||!a.revision||!sha(a.digest))throw Error('stopped_state_artifact_invalid');
 return structuredClone(a);
}
function historical(h:HistoricalUnknownEvidence){if(!h||!count(h.count)||!sha(h.digest))throw Error('stopped_state_historical_evidence_invalid');return structuredClone(h);}
function exclusion(v:StoppedStateExclusion){
 if(v?.schema!=='stopped-writer-exclusion-v1'||v.coverage!=='complete'||v.activeWriters!==0||
  v.publicReadyObserved!==false||v.admissionObserved!==false||!sha(v.digest))throw Error('stopped_state_writers_unqualified');
 return v;
}
/** Aggregate owner-owned read-only evidence; never open an owner's database
 * with its mutating constructor or infer safety merely because a PID exited.
 * Inspectors execute concurrently, bounded by the explicit <=128 owner census.
 * Root inventory and writer exclusion are repeated around the entire inspection.
 */
export function createStoppedStateQualifier(options:StoppedStateAggregateOptions):()=>Promise<FailedBootstrapQualification>{
 const predecessor=serviceIdentity(options.predecessor),witness=structuredClone(options.witness);
 if(!unique(options.expectedOwners)||options.expectedOwners.length>128||!Array.isArray(options.inspectors)||
  !unique(options.inspectors.map(i=>i.ownerId))||!equal(sort(options.expectedOwners),sort(options.inspectors.map(i=>i.ownerId))))
  throw Error('stopped_state_owner_census_mismatch');
 const allRoots=roots(options.roots),required=sort(options.expectedOwners);
 const inspectors=options.inspectors.map(i=>({...i,artifact:artifact(i.artifact),roots:roots(i.roots),historicalBaseline:historical(i.historicalBaseline)}));
 for(const i of inspectors)for(const r of i.roots)if(!allRoots.some(x=>equal(x,r)))throw Error('stopped_state_owner_root_mismatch');
 if(allRoots.some(r=>!inspectors.some(i=>i.roots.some(x=>equal(x,r)))))throw Error('stopped_state_root_unowned');
 for(const b of [...options.predecessorBindings,...options.successorBindings].filter(b=>b.kind==='directory'))
  if(!allRoots.some(r=>r.path===b.path))throw Error('stopped_state_binding_uncovered');
 // Copy authority-bearing descriptors now; a mutable caller cannot silently
 // swap the exact reviewed graph during the asynchronous inspection.
 const priorBindings=structuredClone(options.predecessorBindings),nextBindings=structuredClone(options.successorBindings);
 const inspectExclusion=options.inspectWriterExclusion,inspectInventory=options.inspectStorageInventory;
 const inventory=async()=>{const v=await inspectInventory();if(v?.schema!=='stopped-storage-inventory-v1'||v.coverage!=='complete'||
  !equal(roots(v.roots),allRoots)||!sha(v.digest))throw Error('stopped_state_inventory_unqualified');return v;};
 return async()=>{
  const beforeWriters=structuredClone(exclusion(await inspectExclusion()));
  const beforeInventory=structuredClone(await inventory());
  const beforeBindings=await Promise.all([inspectExistingStateBindings(priorBindings),inspectExistingStateBindings(nextBindings)]);
  const evidence=(await Promise.all(inspectors.map(async i=>{
   const v=await i.inspectStoppedState();
   if(v?.schema!=='owner-stopped-state-v1'||v.ownerId!==i.ownerId||!equal(v.artifact,i.artifact)||
    !equal(roots(v.roots),i.roots)||v.coverage!=='complete'||v.activeWriters!==0||v.pendingEffects!==0||
    v.unaccountedEffects!==0||v.admissionObserved!==false||!sha(v.evidenceDigest))throw Error('stopped_state_owner_unqualified:'+i.ownerId);
   if(!equal(historical(v.historicalUnknown),i.historicalBaseline))throw Error('stopped_state_historical_evidence_changed:'+i.ownerId);
   return structuredClone(v);
  }))).sort((a,b)=>a.ownerId.localeCompare(b.ownerId));
  const afterInventory=await inventory(),afterWriters=exclusion(await inspectExclusion());
  const afterBindings=await Promise.all([inspectExistingStateBindings(priorBindings),inspectExistingStateBindings(nextBindings)]);
  if(!equal(beforeInventory,afterInventory)||!equal(beforeWriters,afterWriters)||!equal(beforeBindings,afterBindings))throw Error('stopped_state_changed_during_inspection');
  const historicalUnknown={count:evidence.reduce((n,v)=>n+v.historicalUnknown.count,0),
   digest:hash(evidence.map(v=>({ownerId:v.ownerId,...v.historicalUnknown})))};
  return {schema:'failed-bootstrap-stopped-state-v1',predecessor,witnessDigest:witnessDigest(witness),
   predecessorBindingDigest:beforeBindings[0],successorBindingDigest:beforeBindings[1],
   inventoryDigest:beforeInventory.digest,evidenceDigest:hash({writers:beforeWriters,owners:evidence}),
   owners:required,coverage:'complete',activeWriters:0,pendingEffects:0,unaccountedEffects:0,historicalUnknown,
   publicReadyObserved:false,admissionObserved:false};
 };
}
