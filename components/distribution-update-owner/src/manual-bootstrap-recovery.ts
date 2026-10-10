import {lstat} from 'node:fs/promises';
import {isAbsolute,join,resolve,relative} from 'node:path';
import {createHash} from 'node:crypto';
import {isDeepStrictEqual as equal} from 'node:util';
import {authorityKey,readAuthority,createAuthority} from './manual-authority.js';
import {inspectExistingStateBindings,type ExistingStateBinding} from './existing-state.js';
import {serviceIdentity,sameService,type ServiceIdentity} from './service-types.js';
import {witnessDigest,type SystemdSourceWitness,type FailedBootstrapExitObserver} from './systemd-witness.js';

const hash=(value:unknown)=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const sha=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
async function absent(path:string){try{await lstat(path);}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return;throw e;}throw Error('bootstrap_recovery_authority_exists');}
function absolute(path:string){if(!isAbsolute(path)||resolve(path)!==path)throw Error('bootstrap_recovery_path_invalid');return path;}
function owners(ids:string[]){if(!Array.isArray(ids)||!ids.length||ids.length>128||new Set(ids).size!==ids.length||ids.some(x=>typeof x!=='string'||!x))throw Error('bootstrap_recovery_owner_census_invalid');return [...ids].sort();}

/** A trusted owner aggregate must derive this from actual stopped state/receipts,
 * not operator text or the absence of a ready file. It covers every writer and
 * external effect owner. Unsupported/unknown coverage must refuse. */
export interface FailedBootstrapQualification {
  schema:'failed-bootstrap-stopped-state-v1';
  predecessor:ServiceIdentity;
  witnessDigest:string;
  predecessorBindingDigest:string;
  successorBindingDigest:string;
  inventoryDigest:string;
  evidenceDigest:string;
  owners:string[];
  coverage:'complete';
  activeWriters:0;
  pendingEffects:0;
  /** New or unaccounted effects must refuse. Historical unknown receipts are
   * immutable evidence, not active writers and never relabeled completed. */
  unaccountedEffects:0;
  historicalUnknown:{count:number;digest:string};
  publicReadyObserved:false;
  admissionObserved:false;
}
export interface FailedBootstrapRecoveryOptions {
  sourceDirectory:string;
  claimDirectory:string;
  supervisorDirectory:string;
  predecessorBindings:ExistingStateBinding[];
  expectedOwners:string[];
  successor:{directory:string;unit:string;expected:ServiceIdentity;bindings:ExistingStateBinding[]};
  observer:FailedBootstrapExitObserver;
  /** Actual continuously held start/writer exclusion, owned by the trusted
   * composition. Snapshot DTOs are NOT exclusion. Remains held through app
   * owner acquisition; the launcher must assert it again before opening intake.
   * This callback never creates masks, claims, or evidence on the caller's behalf. */
  assertExclusionHeld():Promise<void>;
  /** Re-run before preparation and before the successor creates any app owner.
   * A previously restored manual service or any changed state must refuse. */
  qualifyStoppedState():Promise<FailedBootstrapQualification>;
}
interface Intent {
  sourceDirectory:string;claimDirectory:string;supervisorDirectory:string;
  predecessorBindings:ExistingStateBinding[];expectedOwners:string[];
  successor:{directory:string;unit:string;expected:ServiceIdentity;bindings:ExistingStateBinding[]};
}
function intent(options:FailedBootstrapRecoveryOptions):Intent {
  const sourceDirectory=absolute(options.sourceDirectory),claimDirectory=absolute(options.claimDirectory),supervisorDirectory=absolute(options.supervisorDirectory);
  const successor={...structuredClone(options.successor),directory:absolute(options.successor.directory),expected:serviceIdentity(options.successor.expected)};
  if(new Set([sourceDirectory,claimDirectory,supervisorDirectory,successor.directory]).size!==4||!/^[A-Za-z0-9_.@:-]+\.service$/.test(successor.unit))throw Error('bootstrap_recovery_successor_invalid');
  const paths=[sourceDirectory,claimDirectory,supervisorDirectory,successor.directory];
  for(const a of paths)for(const b of paths)if(a!==b){const r=relative(a,b);if(r!=='..'&&!r.startsWith('../')&&!isAbsolute(r))throw Error('bootstrap_recovery_authority_overlap');}
  // Data roots remain the exact original paths/inodes. Only explicitly reviewed
  // file bindings may differ (for example a corrected TLS/config file).
  const dirs=(b:ExistingStateBinding[])=>b.filter(x=>x.kind==='directory').map(x=>({id:x.id,path:absolute(x.path)})).sort((a,b)=>a.id.localeCompare(b.id));
  if(!equal(dirs(options.predecessorBindings),dirs(successor.bindings)))throw Error('bootstrap_recovery_data_roots_changed');
  return {sourceDirectory,claimDirectory,supervisorDirectory,predecessorBindings:structuredClone(options.predecessorBindings),expectedOwners:owners(options.expectedOwners),successor};
}
async function qualify(options:FailedBootstrapRecoveryOptions,i:Intent){
  await options.assertExclusionHeld();
  const source=await readAuthority(i.sourceDirectory,await authorityKey(i.sourceDirectory));
  if(source.schema!=='manual-systemd-source-v1'||!['starting','ready'].includes(source.phase)||source.claim||source.fenceId)throw Error('bootstrap_recovery_source_not_eligible');
  const prior=serviceIdentity(source.expected);
  if(prior.instanceId===i.successor.expected.instanceId||!sameService({...i.successor.expected,instanceId:prior.instanceId,releaseDigest:prior.releaseDigest},prior))throw Error('bootstrap_recovery_successor_invalid');
  if(source.phase==='ready'&&!equal(owners(source.owners),i.expectedOwners))throw Error('bootstrap_recovery_owner_census_mismatch');
  if(source.phase==='starting'&&source.owners.length!==0)throw Error('bootstrap_recovery_source_not_eligible');
  if(source.witness.unit===i.successor.unit)throw Error('bootstrap_recovery_original_unit_must_remain');
  await absent(i.claimDirectory);await absent(i.supervisorDirectory);
  const predecessorBindingDigest=await inspectExistingStateBindings(i.predecessorBindings);
  const successorBindingDigest=await inspectExistingStateBindings(i.successor.bindings);
  if(predecessorBindingDigest!==source.dataBindingDigest)throw Error('bootstrap_recovery_original_bindings_changed');
  const exit=await options.observer.confirmFailedBootstrapExited(source.witness);
  if(exit.schema!=='failed-bootstrap-exit-v1'||!equal(exit.witness,source.witness))throw Error('bootstrap_recovery_exit_unconfirmed');
  const qualification=await options.qualifyStoppedState();
  if(qualification.schema!=='failed-bootstrap-stopped-state-v1'||!sameService(qualification.predecessor,prior)||
    qualification.witnessDigest!==witnessDigest(source.witness)||qualification.predecessorBindingDigest!==predecessorBindingDigest||
    qualification.successorBindingDigest!==successorBindingDigest||!sha(qualification.inventoryDigest)||!sha(qualification.evidenceDigest)||
    !equal(owners(qualification.owners),i.expectedOwners)||qualification.coverage!=='complete'||qualification.activeWriters!==0||
    qualification.pendingEffects!==0||qualification.unaccountedEffects!==0||
    !Number.isSafeInteger(qualification.historicalUnknown?.count)||qualification.historicalUnknown.count<0||!sha(qualification.historicalUnknown.digest)||qualification.publicReadyObserved!==false||qualification.admissionObserved!==false)
    throw Error('bootstrap_recovery_stopped_state_unqualified');
  // Recheck after the aggregate; no source or binding drift can be hidden by a
  // long qualification. The caller owns exclusion of external writers.
  await options.observer.confirmFailedBootstrapExited(source.witness);
  await absent(i.claimDirectory);await absent(i.supervisorDirectory);
  if(!equal(source,await readAuthority(i.sourceDirectory,await authorityKey(i.sourceDirectory)))||
    predecessorBindingDigest!==await inspectExistingStateBindings(i.predecessorBindings)||
    successorBindingDigest!==await inspectExistingStateBindings(i.successor.bindings))
    throw Error('bootstrap_recovery_state_changed');
  await options.assertExclusionHeld();
  return {sourceDigest:hash(source),exit,qualification};
}

/** Explicit local reconciliation only; never a handoff/retirement/exit proof.
 * Keeps the original guard forever and authorizes one exact linked successor.
 * Reopening cannot reissue or replay an uncertain permit. */
export async function prepareFailedBootstrapRecovery(options:FailedBootstrapRecoveryOptions){
  const i=intent(options),checked=await qualify(options,i);
  await absent(i.successor.directory);
  const directory=join(i.sourceDirectory,'failed-bootstrap-recovery');
  const record={schema:'failed-bootstrap-recovery-v1',intent:i,...checked};
  await createAuthority(directory,record);
  await options.assertExclusionHeld();
  return {schema:'failed-bootstrap-recovery-permit-v1' as const,directory,digest:hash(record),predecessor:checked.qualification.predecessor,successor:i.successor.expected};
}

/** Internal launcher hook. Consumes the permit BEFORE creating the new source
 * authority or any application owner. A partial failure remains consumed. */
export async function consumeFailedBootstrapRecovery(options:FailedBootstrapRecoveryOptions,launch:{directory:string;expected:ServiceIdentity;witness:SystemdSourceWitness;bindingDigest:string}){
  const i=intent(options),directory=join(i.sourceDirectory,'failed-bootstrap-recovery');
  const record=await readAuthority(directory,await authorityKey(directory));
  if(record.schema!=='failed-bootstrap-recovery-v1'||!equal(record.intent,i)||launch.directory!==i.successor.directory||
    !sameService(launch.expected,i.successor.expected)||launch.witness.unit!==i.successor.unit||
    launch.bindingDigest!==record.qualification.successorBindingDigest)
    throw Error('bootstrap_recovery_permit_mismatch');
  const checked=await qualify(options,i);
  if(!equal(checked,({sourceDigest:record.sourceDigest,exit:record.exit,qualification:record.qualification})))throw Error('bootstrap_recovery_review_stale');
  await absent(i.successor.directory);
  await createAuthority(join(directory,'consumed'),{schema:'failed-bootstrap-recovery-consumption-v1',permitDigest:hash(record),successor:launch.expected,witness:launch.witness});
  await options.assertExclusionHeld();
  return {predecessor:i.sourceDirectory,permitDigest:hash(record)};
}

/** Read-only authenticated source facts for a composition's stopped-state
 * inspector. This does not issue or consume recovery/handoff authority. */
export async function inspectFailedBootstrapSource(directory:string){
  const source=await readAuthority(absolute(directory),await authorityKey(directory));
  if(source.schema!=='manual-systemd-source-v1'||!['starting','ready'].includes(source.phase)||source.claim||source.fenceId)
    throw Error('bootstrap_recovery_source_not_eligible');
  return {predecessor:serviceIdentity(source.expected),witness:structuredClone(source.witness) as SystemdSourceWitness,
    dataBindingDigest:String(source.dataBindingDigest),owners:[...source.owners] as string[],phase:source.phase as 'starting'|'ready'};
}
