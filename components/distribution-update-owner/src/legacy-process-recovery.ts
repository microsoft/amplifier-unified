import {createHash, randomUUID} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import {open} from 'node:fs/promises';
import {constants} from 'node:fs';
import {join} from 'node:path';
import {createAuthority, authorityKey, readAuthority, writeAuthority} from './manual-authority.js';
import {identity, same, token, type ReleaseIdentity, type RunningIdentity} from './types.js';
import {serviceIdentity, type ServiceIdentity} from './service-types.js';
import type {HostReleaseProof, HostReleaseRequest} from './host-control.js';

const canonical = (value: unknown): string => JSON.stringify(value, (_key, item) =>
  item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
export const legacyRecoveryDigest = (value: unknown) => createHash('sha256').update(canonical(value)).digest('hex');
const equal = (a: unknown, b: unknown) => canonical(a) === canonical(b);
async function lockAuthority(directory: string) {
  // Kernel-released on controller death; unlike an exclusive marker file this
  // permits passive ready observation after a lost controller. The permanent
  // authority directory still prevents creation of another effect permit.
  const path=join(directory,'controller.sqlite3');
  const file=await open(path,constants.O_RDWR|constants.O_CREAT|constants.O_NOFOLLOW,0o600);
  try { const s=await file.stat();if(!s.isFile()||s.uid!==process.getuid?.()||(s.mode&0o077)||s.nlink!==1)throw Error('maintenance_lock_invalid'); }
  finally { await file.close(); }
  const db=new DatabaseSync(path);
  try { db.exec('PRAGMA busy_timeout=0; BEGIN EXCLUSIVE');return ()=>db.close(); }
  catch(error){db.close();throw error;}
}
function hash(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) throw Error('maintenance_digest_invalid');
  return value;
}
function object(value: unknown): Record<string, any> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('maintenance_record_invalid');
  return value as Record<string, any>;
}
function exact(value: unknown, fields: string[]): Record<string, any> {
  const result = object(value);
  if (Object.keys(result).sort().join(',') !== fields.sort().join(',')) throw Error('maintenance_record_invalid');
  return result;
}

/** Storage/installation bindings, not a claim that interrupted work had no
 * effects. The trusted installation adapter reads these from existing authority
 * and journals. No maintenance implementation may synthesize admittedRunning,
 * a zero-active-work lease, or a successful original retirement. */
export interface LegacyRecoveryBinding {
  expected: ServiceIdentity;
  original: {commandId: string; fenceId: string; receiptDigest: string};
  prepared: ReleaseIdentity;
  configurationDigest: string;
  owners: {id: string; bindingDigest: string}[];
  historyDigest: string;
  unknownOutcomesDigest: string;
}
export interface LegacyRecoverySnapshot {
  binding: LegacyRecoveryBinding;
  original: {
    status: 'unknown'; phase: 'admission_requested';
    admission: null; admittedRunning: null; activation: null;
  };
  fence: {
    commandId: string; fenceId: string; instanceId: string; dataScope: string;
    purpose: 'distribution-update'; phase: 'unknown'; owners: string[];
  };
}
export interface LegacyMaintenanceCustody {
  /** A retained kernel identity for the ENTIRE service, including its original
   * supervisor. Direct-child exit, process-name search, or a numeric PID is not
   * sufficient. The maintenance controller runs outside that service. */
  witnessDigest: string;
  exited: Promise<void>;
  /** Confirm the exact unit invocation remains excluded from restart, every
   * bound process exited and its entire cgroup subtree is empty. Must reject
   * custody drift, surviving/outside writers and ambiguous process identity. */
  confirmExited(): Promise<void>;
  close(): void;
}
export interface LegacyMaintenancePorts {
  /** Existing-installation authority, exact legacy unknown receipt and complete
   * owner bindings. This is read-only; it must not open mutating owner stores. */
  inspectRetained(): Promise<LegacyRecoverySnapshot>;
  /** Verify exact signed prepared bytes and compatible retained composition. */
  verifyPrepared(target: ReleaseIdentity): Promise<boolean>;
  /** Establish external maintenance exclusion and retain whole-service exit
   * custody BEFORE stop intent. This must not stop/adopt the old process. */
  bindCustody(binding: LegacyRecoveryBinding): Promise<LegacyMaintenanceCustody>;
  /** Explicit operator-authorized interruption. At most once, even if the
   * response is lost. It is NOT graceful retirement or cancellation of remote
   * work; all remote/tool outcomes remain in their original journals. */
  interruptService(binding: LegacyRecoveryBinding): Promise<void>;
  /** A separately reviewed retained-state launcher, never initial provisioning,
   * PID adoption or a retry of the old activation. At most one invocation. */
  startRetained(request: {recoveryId: string; binding: LegacyRecoveryBinding; instanceId: string}): Promise<void>;
  /** Authenticated process-owned ready observation, not a port/health probe. */
  inspectReplacement(): Promise<ServiceIdentity & {identity: ReleaseIdentity; ready: boolean} | null>;
  /** Qualify the replacement's real writer locks and exact retained intake
   * holds. Required again during passive observation after controller loss. */
  assertReplacementHeld(instanceId: string): Promise<void>;
}
export interface LegacyMaintenanceReceipt {
  schema: 'legacy-process-maintenance-v1';
  recoveryId: string;
  binding: LegacyRecoveryBinding;
  originalDisposition: 'unsettled' | 'interrupted';
  originalOutcome: 'unknown';
  phase: 'prepared' | 'interrupt_requested' | 'stopped' | 'restart_requested' | 'ready';
  witnessDigest: string;
  nextInstanceId: string;
  observed?: ServiceIdentity & {identity: ReleaseIdentity; ready: true};
}
export function legacyRecoveryBinding(input: unknown): LegacyRecoveryBinding {
  const v = exact(input, ['expected', 'original', 'prepared', 'configurationDigest', 'owners', 'historyDigest', 'unknownOutcomesDigest']);
  exact(v.expected, ['installationId','ownerId','dataScope','instanceId','releaseDigest']);
  const expected = serviceIdentity(v.expected);
  const o = exact(v.original, ['commandId', 'fenceId', 'receiptDigest']);
  const original = {commandId: token(o.commandId), fenceId: token(o.fenceId), receiptDigest: hash(o.receiptDigest)};
  exact(v.prepared, ['id', 'version', 'revision', 'digest']);
  const prepared = identity(v.prepared);
  if (prepared.digest === expected.releaseDigest) throw Error('maintenance_replacement_required');
  if (!Array.isArray(v.owners) || !v.owners.length || v.owners.length > 128) throw Error('maintenance_census_invalid');
  const owners = v.owners.map((row: unknown) => {
    const r = exact(row, ['id', 'bindingDigest']); return {id: token(r.id), bindingDigest: hash(r.bindingDigest)};
  });
  if (new Set(owners.map((row: {id: string}) => row.id)).size !== owners.length) throw Error('maintenance_census_invalid');
  return {expected, original, prepared, configurationDigest: hash(v.configurationDigest), owners,
    historyDigest: hash(v.historyDigest), unknownOutcomesDigest: hash(v.unknownOutcomesDigest)};
}
function snapshot(input: unknown): LegacyRecoverySnapshot {
  const v = exact(input, ['binding', 'original', 'fence']), binding = legacyRecoveryBinding(v.binding);
  if (!equal(v.original, {status:'unknown', phase:'admission_requested', admission:null, admittedRunning:null, activation:null}))
    throw Error('maintenance_original_not_legacy_unknown');
  const expected = {commandId:binding.original.commandId, fenceId:binding.original.fenceId,
    instanceId:binding.expected.instanceId, dataScope:binding.expected.dataScope,
    purpose:'distribution-update', phase:'unknown', owners:binding.owners.map(row=>row.id)};
  if (!equal(v.fence, expected)) throw Error('maintenance_fence_or_census_changed');
  return structuredClone({binding, original:v.original, fence:v.fence});
}
export function legacyMaintenanceReceipt(input: unknown): LegacyMaintenanceReceipt {
  const v = object(input);
  exact(v, ['schema','recoveryId','binding','originalDisposition','originalOutcome','phase','witnessDigest','nextInstanceId',
    ...(v.observed === undefined ? [] : ['observed'])]);
  const binding = legacyRecoveryBinding(v.binding);
  if (v.schema !== 'legacy-process-maintenance-v1' || v.originalOutcome !== 'unknown' ||
      !['prepared','interrupt_requested','stopped','restart_requested','ready'].includes(v.phase)) throw Error('maintenance_record_invalid');
  if (v.originalDisposition !== (['prepared','interrupt_requested'].includes(v.phase) ? 'unsettled' : 'interrupted'))
    throw Error('maintenance_record_invalid');
  token(v.recoveryId); token(v.nextInstanceId); hash(v.witnessDigest);
  if (v.nextInstanceId === binding.expected.instanceId) throw Error('maintenance_instance_reused');
  if (v.phase === 'ready') {
    exact(v.observed, ['installationId','ownerId','dataScope','instanceId','releaseDigest','identity','ready']);
    const observed = v.observed;
    serviceIdentity(observed); identity(observed.identity);
    if (observed.ready !== true || observed.installationId !== binding.expected.installationId ||
        observed.ownerId !== binding.expected.ownerId || observed.dataScope !== binding.expected.dataScope ||
        observed.instanceId !== v.nextInstanceId || observed.releaseDigest !== binding.prepared.digest ||
        !same(observed.identity, binding.prepared)) throw Error('maintenance_readiness_unconfirmed');
  } else if (v.observed !== undefined) throw Error('maintenance_readiness_unconfirmed');
  return structuredClone(v) as LegacyMaintenanceReceipt;
}
const parseReceipt=legacyMaintenanceReceipt;

/** Private supervisor composition only. The Host verifier consumes the same
 * authenticated receipt independently. Release retries read original durable
 * Host receipts first; no new hold or stop/start capability is exposed here. */
export interface LegacyMaintenanceSettlementPort {
  binding: LegacyRecoveryBinding;
  receipt(): Promise<LegacyMaintenanceReceipt>;
  prepared(): Promise<import('./types.js').PreparedRelease>;
  inspectRelease(): Promise<unknown>;
  release(request: {commandId:string;fenceId:string;outcome:'ready'}): Promise<unknown>;
}
export function assertLegacyMaintenanceRelease(value: unknown, proof: LegacyMaintenanceReceipt): void {
  const r=object(value),b=proof.binding;
  if(r.released!==true||r.intakeClosed!==false||r.commandId!==b.original.commandId||r.fenceId!==b.original.fenceId||
      r.instanceId!==proof.nextInstanceId||r.receiptId!==proof.recoveryId||r.outcome!=='ready')
    throw Error('maintenance_holds_not_released');
}

/** Source-only maintenance primitive. Its concrete installation/custody/launch
 * adapters require independent review. No CLI, automatic invocation, fallback
 * to ordinary activation, journal editing or initial-claim reuse is provided.
 * The existing private directory is a permanent replay guard: reopening after
 * controller loss is passive inspection, never authority to repeat an effect. */
export async function prepareLegacyProcessRecovery(options: {
  directory: string; recoveryId: string; expected: LegacyRecoveryBinding; ports: LegacyMaintenancePorts;
}) {
  const binding = legacyRecoveryBinding(options.expected), recoveryId = token(options.recoveryId), ports = options.ports;
  async function unchanged() {
    if (!equal(snapshot(await ports.inspectRetained()).binding, binding)) throw Error('maintenance_retained_state_changed');
  }
  await unchanged();
  if (!await ports.verifyPrepared(binding.prepared)) throw Error('maintenance_target_unqualified');
  const custody = await ports.bindCustody(binding);
  let value: LegacyMaintenanceReceipt = {schema:'legacy-process-maintenance-v1', recoveryId, binding,
    originalDisposition:'unsettled', originalOutcome:'unknown', phase:'prepared',
    witnessDigest:hash(custody.witnessDigest), nextInstanceId:randomUUID()};
  let key: Buffer, unlock: ()=>void;
  try { await unchanged(); key = await createAuthority(options.directory, value); unlock=await lockAuthority(options.directory); }
  catch (error) { custody.close(); throw error; }
  let busy = false, closed = false;
  async function exclusively<T>(operation: () => Promise<T>): Promise<T> {
    if (closed || busy) throw Error('maintenance_controller_unavailable');
    busy = true;
    try { return await operation(); } finally { busy = false; }
  }
  const save = async (next: LegacyMaintenanceReceipt) => {
    // Advance memory before durable write. Even an uncertain fsync/write reply
    // cannot authorize repeating stop/start in this controller.
    value = parseReceipt(next); await writeAuthority(options.directory, key, value);
  };
  return {
    receipt: () => structuredClone(value),
    interrupt: () => exclusively(async () => {
      if (value.phase !== 'prepared') throw Error('maintenance_effect_already_requested');
      await unchanged();
      await save({...value, phase:'interrupt_requested'});
      await ports.interruptService(binding);
      return structuredClone(value);
    }),
    confirmStopped: () => exclusively(async () => {
      if (value.phase !== 'interrupt_requested') throw Error('maintenance_stop_not_requested');
      await custody.exited;
      await custody.confirmExited(); await unchanged(); await custody.confirmExited();
      await save({...value, phase:'stopped', originalDisposition:'interrupted'});
      return structuredClone(value);
    }),
    startReplacement: () => exclusively(async () => {
      if (value.phase !== 'stopped') throw Error('maintenance_restart_not_authorized');
      await custody.confirmExited(); await unchanged();
      if (!await ports.verifyPrepared(binding.prepared)) throw Error('maintenance_target_unqualified');
      await custody.confirmExited();
      await save({...value, phase:'restart_requested'});
      await ports.startRetained({recoveryId, binding:structuredClone(binding), instanceId:value.nextInstanceId});
      return structuredClone(value);
    }),
    observeReplacement: () => exclusively(async () => {
      if (value.phase !== 'restart_requested' && value.phase !== 'ready') throw Error('maintenance_restart_not_requested');
      await unchanged();
      if (!await ports.verifyPrepared(binding.prepared)) throw Error('maintenance_target_unqualified');
      const observed = await ports.inspectReplacement();
      const next = parseReceipt({...value, phase:'ready', observed});
      await ports.assertReplacementHeld(next.nextInstanceId);
      if (value.phase === 'ready' && !equal(value, next)) throw Error('maintenance_readiness_changed');
      if (value.phase !== 'ready') await save(next);
      return structuredClone(value);
    }),
    close() { if (busy) throw Error('maintenance_controller_busy'); if(closed)return;closed = true; custody.close(); unlock(); },
  };
}

/** Read-only private receipt source. A caller-controlled JSON body is never
 * maintenance authority. Missing, interrupted or unauthenticated state throws. */
export async function inspectLegacyProcessRecovery(directory: string): Promise<LegacyMaintenanceReceipt> {
  const key = await authorityKey(directory);
  return parseReceipt(await readAuthority(directory, key));
}

/** Finish ONLY the saved restart observation after controller loss. No exit
 * custody is reconstructed and no stop/start capability is accepted. A lost
 * start acknowledgement can become ready through the saved exact instance,
 * signed target, retained installation and original fence; absent readiness
 * remains unknown. Calling this never consumes another launch permit. */
export async function observeLegacyProcessRecovery(options: {
  directory: string; expected: LegacyRecoveryBinding;
  ports: Pick<LegacyMaintenancePorts,'inspectRetained'|'verifyPrepared'|'inspectReplacement'|'assertReplacementHeld'>;
}): Promise<LegacyMaintenanceReceipt> {
  const binding=legacyRecoveryBinding(options.expected),key=await authorityKey(options.directory);
  const unlock=await lockAuthority(options.directory);
  try {
    const prior=parseReceipt(await readAuthority(options.directory,key));
    if(!equal(prior.binding,binding)||!['restart_requested','ready'].includes(prior.phase))throw Error('maintenance_restart_not_requested');
    if(!equal(snapshot(await options.ports.inspectRetained()).binding,binding))throw Error('maintenance_retained_state_changed');
    if(!await options.ports.verifyPrepared(binding.prepared))throw Error('maintenance_target_unqualified');
    const next=parseReceipt({...prior,phase:'ready',observed:await options.ports.inspectReplacement()});
    await options.ports.assertReplacementHeld(next.nextInstanceId);
    if(!equal(prior,parseReceipt(await readAuthority(options.directory,key))))throw Error('maintenance_receipt_changed');
    if(prior.phase==='ready'&&!equal(prior,next))throw Error('maintenance_readiness_changed');
    if(prior.phase!=='ready')await writeAuthority(options.directory,key,next);
    return next;
  } finally { unlock(); }
}

/** Existing owner release shape, backed by a DISTINCT authoritative maintenance
 * receipt. "ready" describes the replacement process only; it says nothing
 * about the original activation, native retirement, or remote business work.
 * Wiring this verifier is an explicit signed-composition change, never a
 * fallback after ordinary verification fails. No new per-feature protocol. */
export function createLegacyMaintenanceReleaseVerifier(options: {
  binding: LegacyRecoveryBinding;
  receipt(): Promise<LegacyMaintenanceReceipt>;
  inspectRunning(): Promise<RunningIdentity | null>;
  verifyPrepared(target: ReleaseIdentity): Promise<boolean>;
}) {
  const binding = legacyRecoveryBinding(options.binding);
  return async (request: HostReleaseRequest & {owners: string[]}): Promise<HostReleaseProof> => {
    const proof = parseReceipt(await options.receipt());
    const actual = await options.inspectRunning();
    if (proof.phase !== 'ready' || !equal(proof.binding, binding) || !actual?.ready ||
        request.outcome !== 'ready' || request.purpose !== 'distribution-update' ||
        request.commandId !== binding.original.commandId || request.fenceId !== binding.original.fenceId ||
        request.instanceId !== binding.expected.instanceId || request.dataScope !== binding.expected.dataScope ||
        !equal(request.owners, binding.owners.map(row=>row.id)) || actual.instanceId !== proof.nextInstanceId ||
        actual.dataScope !== binding.expected.dataScope || !same(actual.identity, binding.prepared) ||
        !await options.verifyPrepared(binding.prepared)) throw Error('maintenance_release_unconfirmed');
    // Re-read authenticated authority after external inspection/verification.
    if (!equal(proof, parseReceipt(await options.receipt()))) throw Error('maintenance_receipt_changed');
    return {verified:true, fenceId:request.fenceId, commandId:request.commandId, outcome:'ready',
      instanceId:actual.instanceId, dataScope:actual.dataScope, receiptId:proof.recoveryId};
  };
}
