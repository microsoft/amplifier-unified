import { DatabaseSync, backup } from 'node:sqlite';
import { lstat, realpath, mkdtemp, chmod, rm } from 'node:fs/promises';
import { isAbsolute, join } from 'node:path';
import { tmpdir } from 'node:os';
import { token, type OwnerState } from './types.js';
import { serviceIdentity, sameService, serviceReceipt, type ServiceIdentity, type ServiceRecord } from './service-types.js';

export interface OfflineSnapshotRequest {
  dataDirectory: string;
  inventoryDigest: string;
  expected: ServiceIdentity;
  stoppedCommandId: string;
  participantIds: string[];
}
export interface OfflineSnapshotProof {
  schema: 'distribution-offline-snapshot-proof-v1';
  inventoryDigest: string;
  expected: ServiceIdentity;
  stoppedCommandId: string;
  qualifiedOwners: string[];
  stoppedReceipt: ReturnType<typeof serviceReceipt>;
  supervisorClosed: true;
  ledgersFrozen: true;
}

async function privatePath(path: string, directory: boolean) {
  const info = await lstat(path);
  if (!isAbsolute(path) || path !== await realpath(path) || info.isSymbolicLink() ||
      (directory ? !info.isDirectory() : !info.isFile()) ||
      (process.platform !== 'win32' && (info.mode & 0o077)))
    throw Error('private_offline_namespace_required');
}
function refuseLiveOwner(db: DatabaseSync) {
  const lease = db.prepare('SELECT pid FROM owner WHERE id=1').get();
  if (!lease) return;
  const pid = Number(lease.pid);
  if (!Number.isSafeInteger(pid) || pid < 1) throw Error('offline_owner_unconfirmed');
  try { process.kill(pid, 0); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ESRCH') return;
    throw Error('offline_owner_unconfirmed');
  }
  // PID liveness only refuses a competing writer. It NEVER proves service exit.
  throw Error('offline_supervisor_must_be_closed');
}

/** Offline only. Require qualified saved exit, then hold both writer locks for
 * the entire capture callback. No live stop, adoption, reconciliation or replay.
 * Use read connections for SQLite backup while independent writer transactions
 * prevent mutations; raw WAL files are never treated as standalone snapshots. */
export async function withOfflineSupervisorSnapshot<T>(request: OfflineSnapshotRequest,
  capture: (snapshot: {proof: OfflineSnapshotProof; ledgers: {updates: string; service: string}; state: OwnerState}) => Promise<T>): Promise<T> {
  if (!/^[a-f0-9]{64}$/.test(request.inventoryDigest)) throw Error('invalid_inventory_digest');
  const expected = serviceIdentity(request.expected), stopId = token(request.stoppedCommandId);
  if (!Array.isArray(request.participantIds) || !request.participantIds.length || request.participantIds.length > 128 ||
      new Set(request.participantIds).size !== request.participantIds.length) throw Error('invalid_snapshot_participants');
  request.participantIds.forEach(id => token(id));
  await privatePath(request.dataDirectory, true);
  const paths = [join(request.dataDirectory, 'owner', 'updates.sqlite3'), join(request.dataDirectory, 'service', 'service.sqlite3')];
  for (const path of paths) { await privatePath(path.slice(0,path.lastIndexOf('/')),true); await privatePath(path,false); }
  const locks: DatabaseSync[] = [], readers: DatabaseSync[] = [];
  let temporary: string | undefined;
  try {
    for (const path of paths) {
      const db = new DatabaseSync(path); locks.push(db);
      db.exec('PRAGMA busy_timeout=0;BEGIN IMMEDIATE');
      refuseLiveOwner(db);
    }
    const [updates, service] = locks;
    const row = service.prepare('SELECT value FROM commands WHERE id=?').get(stopId);
    if (!row) throw Error('offline_qualified_stop_required');
    const stopped: ServiceRecord = JSON.parse(String(row.value));
    const binding = JSON.parse(String(service.prepare('SELECT value FROM binding WHERE id=1').get()?.value));
    const state: OwnerState = JSON.parse(String(updates.prepare('SELECT value FROM state WHERE id=1').get()?.value));
    if (state.schema !== 1 || state.dataScope !== expected.dataScope || state.current?.identity.digest !== expected.releaseDigest ||
        binding.installationId !== expected.installationId || binding.dataScope !== expected.dataScope || binding.ownerId !== expected.ownerId ||
        stopped.commandId !== stopId || stopped.operation !== 'stop' || stopped.status !== 'stopped' || stopped.phase !== 'stopped' ||
        !sameService(stopped.expected, expected) || !stopped.fenceId || stopped.resumeCommandId ||
        !stopped.exitProof || stopped.exitProof.ownerId !== expected.ownerId || stopped.exitProof.instanceId !== expected.instanceId ||
        !Number.isFinite(stopped.exitProof.observedAt) || !stopped.target || stopped.target.identity.digest !== expected.releaseDigest ||
        !Array.isArray(stopped.qualifiedOwners) || request.participantIds.some(id => !stopped.qualifiedOwners!.includes(id)))
      throw Error('offline_qualified_stop_required');
    const unresolved = service.prepare("SELECT 1 FROM commands WHERE json_extract(value,'$.status') IN ('running','unknown') OR json_extract(value,'$.admissionSettlement.state') IN ('pending','unknown') LIMIT 1").get();
    if (unresolved) throw Error('offline_service_outcome_unconfirmed');
    const proof: OfflineSnapshotProof = {schema:'distribution-offline-snapshot-proof-v1',inventoryDigest:request.inventoryDigest,
      expected,stoppedCommandId:stopId,qualifiedOwners:[...stopped.qualifiedOwners],stoppedReceipt:serviceReceipt(stopped),supervisorClosed:true,ledgersFrozen:true};
    temporary = await mkdtemp(join(tmpdir(),'unified-offline-ledgers-')); await chmod(temporary,0o700);
    const ledgers = {updates:join(temporary,'updates.sqlite3'),service:join(temporary,'service.sqlite3')};
    for (let i=0;i<paths.length;i++) {
      const reader = new DatabaseSync(paths[i],{readOnly:true}); readers.push(reader);
      const destination = i===0?ledgers.updates:ledgers.service;
      await backup(reader,destination); await chmod(destination,0o600);
    }
    return await capture({proof,ledgers,state:structuredClone(state)});
  } finally {
    for (const db of readers.reverse()) db.close();
    for (const db of locks.reverse()) { try { db.exec('ROLLBACK'); } catch {} db.close(); }
    if (temporary) await rm(temporary,{recursive:true,force:true});
  }
}
