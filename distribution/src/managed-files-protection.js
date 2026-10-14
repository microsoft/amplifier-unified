import {DatabaseSync} from 'node:sqlite';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';

const canonical = value => JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const token = value => {
  if (typeof value !== 'string' || !value || value.length > 200 || /[\x00-\x1f]/.test(value)) throw Error('Bounded managed-files identity required');
  return value;
};
const sessionId = value => typeof value === 'string' && /^ahp-session:\/[^\s\x00-\x1f]{1,480}$/.test(value);
const sha256 = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const refused = (reason, protectionState) => Object.assign(Error(reason), {data: {executed: false, ...(protectionState ? {protectionState} : {})}});
const bounded = (value, maximum = 262144) => {
  if (canonical(value) === undefined || Buffer.byteLength(canonical(value)) > maximum) throw Error('Managed-files evidence exceeds its bound');
  return value;
};

function selection(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input) || !Array.isArray(input.descendants ?? [])) throw refused('Exact reviewed managed-files operation and bounded family required');
  const sessions = [input.session, ...(input.descendants ?? [])], allocation = input.allocation;
  if (!input || Object.keys(input).some(key => !['commandId', 'session', 'descendants', 'operation', 'reviewHash', 'allocation'].includes(key)) ||
      input.operation !== 'dispose-owned-files' || !sha256(input.reviewHash) ||
      !Array.isArray(input.descendants ?? []) || sessions.length > 101 || new Set(sessions).size !== sessions.length || !sessions.every(sessionId)) {
    throw refused('Exact reviewed managed-files operation and bounded family required');
  }
  token(input.commandId);
  if (!allocation || Object.keys(allocation).sort().join(',') !== 'allocationHash,allocationId,bytes,entryCount,executionDirectory,treeHash' ||
      typeof allocation.allocationId !== 'string' || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(allocation.allocationId) ||
      !sha256(allocation.allocationHash) || !sha256(allocation.treeHash) ||
      !['entryCount', 'bytes'].every(key => Number.isSafeInteger(allocation[key]) && allocation[key] >= 0) ||
      typeof allocation.executionDirectory !== 'string' || !allocation.executionDirectory.startsWith('/') || allocation.executionDirectory === '/' ||
      allocation.executionDirectory.length > 8192 || /[\x00-\x1f\\]/.test(allocation.executionDirectory) ||
      allocation.executionDirectory.split('/').slice(1).some(part => !part || part === '.' || part === '..')) {
    throw refused('Exact native-reviewed managed allocation required');
  }
  return bounded({commandId: input.commandId, session: input.session, sessions, operation: input.operation, reviewHash: input.reviewHash, allocation: structuredClone(allocation)});
}

function referenceReport(value, sessions) {
  bounded(value, 65536);
  if (!value || !['complete', 'partial'].includes(value.coverage) || !Array.isArray(value.protected) || value.protected.length > sessions.length ||
      !Array.isArray(value.omissions) || value.omissions.length > 101 || new Set(value.protected.map(row => row.session)).size !== value.protected.length ||
      value.protected.some(row => !sessions.includes(row.session) || !Array.isArray(row.reasons) || !row.reasons.length || row.reasons.length > 32 ||
        row.reasons.some(reason => typeof reason !== 'string' || !reason || reason.length > 200 || /[\x00-\x1f]/.test(reason)))) {
    throw Error('Owner reference coverage is malformed or unbounded');
  }
  return structuredClone(value);
}

/** Only the trusted host's indexed effect journal supplies this proof. The
 * host confirms durable native completion before setting status completed. */
function effectProof(row, receipt) {
  if (!receipt || receipt.commandId !== row.commandId || receipt.session !== row.session || receipt.operation !== row.operation ||
      receipt.reviewHash !== row.reviewHash || receipt.preservesCanonical !== true || canonical(receipt.allocation) !== canonical(row.allocation) ||
      !Array.isArray(receipt.descendants) || canonical(receipt.descendants) !== canonical(row.sessions.slice(1)) || receipt.familyCount !== row.sessions.length - 1 ||
      !(receipt.status === 'completed' && receipt.executed !== false || receipt.status === 'refused' && receipt.executed === false)) {
    throw Error('Managed-files effect lacks exact conclusive authority');
  }
  return {commandId: row.commandId, session: row.session, operation: row.operation, reviewHash: row.reviewHash,
    allocation: row.allocation, descendants: row.sessions.slice(1), familyCount: row.sessions.length - 1, preservesCanonical: true, status: receipt.status, executed: receipt.status === 'completed'};
}

/** Product-reference exclusion only. Native/Core history ownership remains the
 * host/native operation's responsibility. The configured census must exclude
 * this facade and duplicate native/admin/transfer gates; never infer that census
 * from browser input or reuse retentionHide as file-removal authority. */
export function createManagedFilesProtection({directory, instanceId, dataScope, participants, readEffectReceipt, onMayBeIdle = () => {}}) {
  token(instanceId); token(dataScope);
  if (!Array.isArray(participants) || !participants.length || participants.length > 64 ||
      new Set(participants.map(participant => participant.id)).size !== participants.length || typeof readEffectReceipt !== 'function') {
    throw Error('Exact managed-files participant census and effect authority required');
  }
  participants = participants.map(participant => ({...participant}));
  for (const participant of participants) token(participant.id);
  mkdirSync(directory, {recursive: true, mode: 0o700});
  const lock = new DatabaseSync(join(directory, 'owner-lock.sqlite3'));
  try { lock.exec('PRAGMA busy_timeout=0; PRAGMA journal_mode=DELETE; BEGIN EXCLUSIVE'); }
  catch (error) { lock.close(); throw error; }
  let db;
  try {
    db = new DatabaseSync(join(directory, 'protection.sqlite3'));
    db.exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS protections(command TEXT PRIMARY KEY,state TEXT NOT NULL,body TEXT NOT NULL); CREATE INDEX IF NOT EXISTS protection_state ON protections(state); CREATE INDEX IF NOT EXISTS protection_unfinished ON protections(command) WHERE state NOT IN ('released','refused')");
    // Exclusive process ownership precedes recovery. At most one protection may
    // be unfinished, so startup never materializes the completed receipt log.
    const unsettled = db.prepare("SELECT body FROM protections INDEXED BY protection_unfinished WHERE state NOT IN ('released','refused') LIMIT 2").all();
    if (unsettled.length > 1) throw Error('Conflicting unfinished managed-files protection records');
    for (const {body} of unsettled) {
      const row = JSON.parse(body);
      if (row.context.dataScope !== dataScope) throw Error('Managed-files protection belongs to another data scope');
      row.state = 'unknown'; row.reason = 'Coordinator restarted; original effect and owner receipts require inspection';
      db.prepare('UPDATE protections SET state=?,body=? WHERE command=?').run(row.state, canonical(row), row.commandId);
    }
  } catch (error) { db?.close(); lock.close(); throw error; }
  let closed = false, active = 0;
  const live = new Map(), releases = new Map(), owners = new Map(participants.map(participant => [participant.id, participant]));
  const get = commandId => { const row = db.prepare('SELECT body FROM protections WHERE command=?').get(commandId); return row ? JSON.parse(row.body) : null; };
  const save = row => db.prepare('INSERT INTO protections VALUES(?,?,?) ON CONFLICT(command) DO UPDATE SET state=excluded.state,body=excluded.body').run(row.commandId, row.state, canonical(bounded(row)));
  const view = row => row ? bounded({commandId: row.commandId, session: row.session, operation: row.operation, reviewHash: row.reviewHash,
    allocation: row.allocation, preservesCanonical: true, state: row.state,
    owners: row.owners.map(owner => ({id: owner.id, state: owner.state, ...(owner.references ? {references: owner.references} : {})})),
    ...(row.reason ? {reason: row.reason} : {})}) : null;
  const check = () => { if (closed) throw Error('Managed-files protection is closed'); };
  const idle = () => { if (!active) { try { Promise.resolve(onMayBeIdle()).catch(() => {}); } catch {} } };
  const retainUnknown = async row => {
    row.state = 'unknown'; save(row);
    for (const owner of row.owners) {
      if (owner.state === 'released' || owner.state === 'pending') continue;
      const lease = live.get(row.commandId)?.get(owner.id);
      owner.state = 'unknown'; save(row);
      if (lease) { try { await lease.release('unknown'); } catch { /* Unconfirmed exclusion stays retained. */ } }
    }
    live.delete(row.commandId);
  };

  const releaseInternal = async commandId => {
    const row = get(commandId);
    if (!row) throw Error('Original managed-files protection receipt required');
    if (row.state === 'released' || row.state === 'refused') return view(row);
    let effect;
    try {
      effect = effectProof(row, await readEffectReceipt(row.session, commandId));
      if (row.effect && canonical(row.effect) !== canonical(effect)) throw Error('Original terminal managed-files evidence changed');
    } catch {
      row.reason = 'Original managed-files effect is not conclusively settled; product owner intake remains protected';
      await retainUnknown(row); throw Error(row.reason);
    }
    if (!row.proof) {
      const outcome = row.context.instanceId === instanceId ? 'unchanged' : 'ready';
      row.effect = effect;
      row.proof = {verified: true, fenceId: row.context.fenceId, commandId, dataScope: row.context.dataScope,
        outcome, instanceId, receiptId: 'managed-files:' + digest({selection: row.signature, effect})};
      row.state = 'releasing'; save(row); // Exact proof precedes the first owner release.
    }
    const failures = [];
    for (const owner of [...row.owners].reverse()) {
      if (owner.state === 'pending' || owner.state === 'released') continue;
      const lease = live.get(commandId)?.get(owner.id), participant = owners.get(owner.id);
      try {
        if (lease) await lease.release(row.proof.outcome, row.proof);
        else if (participant?.managedFiles?.version === 1 && participant.managedFiles.preservesCanonical === true && typeof participant.reconcileRelease === 'function') {
          await participant.reconcileRelease({...row.context, outcome: row.proof.outcome, proof: row.proof});
        } else throw Error('Original owner managed-files reconciliation unavailable');
        owner.state = 'released'; save(row);
      } catch { owner.state = 'unknown'; failures.push(owner.id); save(row); }
    }
    live.delete(commandId); row.state = failures.length ? 'unknown' : 'released';
    if (failures.length) row.reason = 'Unconfirmed managed-files owner release: ' + failures.join(','); else delete row.reason;
    save(row);
    if (failures.length) throw Error(row.reason);
    return view(row);
  };
  const release = commandId => {
    check(); token(commandId);
    if (releases.has(commandId)) return releases.get(commandId);
    if (active) return Promise.reject(Error('Managed-files coordination is still active'));
    active++;
    const pending = releaseInternal(commandId).finally(() => { releases.delete(commandId); active--; idle(); });
    releases.set(commandId, pending); return pending;
  };

  return {
    managedFiles: {version: 1, preservesCanonical: true},
    async acquire(input) {
      check(); const selected = selection(input), commandId = selected.commandId, signature = digest(selected), prior = get(commandId);
      if (prior) throw refused(prior.signature === signature ? 'Original protection already exists; inspect its exact receipt' : 'Managed-files command belongs to a different review', prior.state);
      if (active || db.prepare("SELECT 1 FROM protections INDEXED BY protection_unfinished WHERE state NOT IN ('released','refused') LIMIT 1").get()) throw refused('Another managed-files protection has not settled');
      if (participants.some(participant => participant.managedFiles?.version !== 1 || participant.managedFiles.preservesCanonical !== true ||
          typeof participant.acquire !== 'function' || typeof participant.reconcileRelease !== 'function')) throw refused('Configured product owner managed-files coverage is incomplete');
      const context = {fenceId: 'managed-files:' + digest({commandId, signature}), commandId, purpose: 'managed-files-disposal', instanceId, dataScope};
      const row = {...selected, signature, context, state: 'acquiring', owners: participants.map(participant => ({id: participant.id, state: 'pending'}))};
      const leases = new Map(); live.set(commandId, leases); save(row); active++;
      try {
        for (const owner of row.owners) {
          owner.state = 'acquiring'; save(row);
          const lease = await owners.get(owner.id).acquire(context);
          if (!lease) { owner.state = 'pending'; save(row); throw refused('Product owner has current work: ' + owner.id); }
          if (lease.ownerId !== owner.id || lease.fenceId !== context.fenceId || typeof lease.release !== 'function') throw Error('Owner returned a different managed-files lease');
          leases.set(owner.id, lease); owner.state = 'held'; save(row);
          if (typeof lease.inspectManagedFilesReferences !== 'function') throw refused('Distinct managed-file reference coverage is unavailable: ' + owner.id);
          const references = referenceReport(await lease.inspectManagedFilesReferences({sessions: row.sessions, limit: 101, allocation: row.allocation}), row.sessions);
          if (references.coverage !== 'complete' || references.protected.length || references.omissions.length) {
            owner.references = references; save(row); throw refused('Owner retains file references or incomplete coverage: ' + owner.id);
          }
        }
        row.state = 'held'; save(row);
        return {ownerIds: row.owners.map(owner => owner.id), release: () => release(commandId)};
      } catch (error) {
        // No native operation was dispatched by this coordinator. Only known
        // live acquisitions can be unwound before returning a refusal to host.
        row.admission = {status: 'refused', executed: false}; save(row);
        if (row.owners.some(owner => owner.state === 'acquiring')) {
          row.reason = 'Owner acquisition acknowledgement is uncertain'; await retainUnknown(row);
        } else {
          let uncertain = false;
          for (const owner of [...row.owners].reverse()) if (owner.state === 'held') {
            try { await leases.get(owner.id).release('unchanged', {kind: 'admission-refused'}); owner.state = 'released'; save(row); }
            catch { owner.state = 'unknown'; uncertain = true; save(row); }
          }
          row.state = uncertain ? 'unknown' : 'refused';
          row.reason = error?.data?.executed === false ? String(error.message).slice(0, 1000) : 'Owner reference inspection was not confirmed';
          save(row); live.delete(commandId);
        }
        throw refused(row.reason, row.state);
      } finally { active--; idle(); }
    },
    receipt(commandId) { check(); return view(get(token(commandId))); },
    reconcile: release,
    close() { if (active) throw Error('Managed-files coordination is still active'); if (closed) return; closed = true; db.close(); lock.close(); },
  };
}
