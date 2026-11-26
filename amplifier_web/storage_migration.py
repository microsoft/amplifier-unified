"""One-time, rollback-preserving storage upgrade and bounded receipt retention."""
import asyncio
import json
import logging
import sqlite3
import time

from .resource_files import collect, put, remove_files, root
from .state_storage import resource

RESULT_LIMIT = 200
RESULT_BYTES = 32_000_000
RESULT_DAYS = 30
RESULT_EXPIRY_BATCH = 32
# Exclude unresolved outcomes and receipt-only history before ordering candidates.
RESULT_CANDIDATE = """json_extract(value,'$.status') IN ('completed','failed')
    AND json_extract(value,'$.resultExpired') IS NOT 1
    AND (json_type(value,'$.result') IS NOT NULL OR json_type(value,'$.arguments') IS NOT NULL)
    AND json_type(value,'$.updatedAt') IN ('integer','real')"""
BACKGROUND_PRUNE_LIMIT = 1024
BACKGROUND_EDGE_BATCH = 64
# These bound actual additional work, not the number of snapshots seen. Normal
# chat progress can replace several small roots during one large initial mark.
BACKGROUND_CATCHUP_RESOURCES = 4096
BACKGROUND_CATCHUP_SECONDS = 5
BACKGROUND_DRAIN_SECONDS = 5


def index_operations(db):
    db.execute("CREATE INDEX IF NOT EXISTS smart_tool_result_retention ON smart_tool_operations "
               "(json_extract(value,'$.updatedAt') DESC) WHERE " + RESULT_CANDIDATE)


def refresh_operations(db, state):
    smart = state.get('smartTools', {})
    for item in [*smart.get('operations', []), smart.get('inspectedOperation', {})]:
        row = db.execute('SELECT value FROM smart_tool_operations WHERE id=?', (item.get('id'),)).fetchone()
        if row:
            item.clear()
            item.update(json.loads(row[0]))


def trim_operations(db, state, *, expire_limit=None):
    """Keep small at-most-once receipts forever, recent full results within budget."""
    retained = size = 0
    expired = 0
    changed = False
    # The partial index skips receipt-only history and unresolved operations.
    cursor = db.execute("SELECT id FROM smart_tool_operations WHERE " + RESULT_CANDIDATE
                        + " ORDER BY json_extract(value,'$.updatedAt') DESC")
    for (identity,) in cursor:
        text = db.execute('SELECT value FROM smart_tool_operations WHERE id=?', (identity,)).fetchone()[0]
        operation = json.loads(text)
        fields = {key: operation[key] for key in ('result', 'arguments') if key in operation}
        if not fields:
            continue
        cost = sum(value.get('bytes', 0) if isinstance(value, dict) and '$resource' in value
                   else len(json.dumps(value).encode()) for value in fields.values())
        if (retained < RESULT_LIMIT and size + cost <= RESULT_BYTES
                and operation.get('updatedAt', time.time()) >= time.time() - RESULT_DAYS * 86400):
            retained += 1
            size += cost
            # Older receipts embedded full results. Externalize only what survives.
            for key, value in fields.items():
                if not (isinstance(value, dict) and '$resource' in value) and len(json.dumps(value)) > 16_000:
                    operation[key] = put(db, value)
        else:
            for key in fields:
                operation.pop(key, None)
            operation['resultExpired'] = True
            operation['detail'] = 'Full result retention expired. The execution receipt remains; work is never replayed.'
            expired += 1
        encoded = json.dumps(operation)
        if encoded != text:
            db.execute('UPDATE smart_tool_operations SET value=? WHERE id=?', (encoded, identity))
            changed = True
        if expire_limit is not None and expired >= expire_limit:
            break
    if changed:
        refresh_operations(db, state)
    return {'expired': expired, 'changed': changed}


def upgrade(service):
    """Runs before accepting connections; keep the complete old DB for rollback."""
    db, state = service.db, service.state
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_layout'").fetchone():
        return
    existing = db.execute('SELECT 1 FROM state WHERE id=1').fetchone()
    if existing:
        folder = service.data_dir / 'backups' / 'shared-storage-v1'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = folder / 'app.sqlite3'
        if not backup.exists():
            temporary = folder / 'app.sqlite3.pending'
            with sqlite3.connect(temporary) as target:
                db.backup(target)
            temporary.chmod(0o600)
            temporary.replace(backup)
    from .session_projection import migrate
    migrate(service.data_dir, state)
    for item in state.get('canvasArtifacts', []):
        reference = item.get('body', {}).get('$resource')
        if not reference:
            continue
        body = resource(db, reference)
        if 'mcp' in body:
            item['mcpState'] = put(db, body['mcp'])
            item['body'] = put(db, {key: value for key, value in body.items() if key != 'mcp'})
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='smart_tool_operations'").fetchone():
        index_operations(db)
        trim_operations(db, state)
    from .state_storage import normalize_state
    normalize_state(state, db)
    stale = collect(db, state)
    # Preserve existing IDs referenced by history, even when their original
    # serialization used different whitespace/key order.
    directory = root(db)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    from .host.storage import SessionStore
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    for identity, text in db.execute('SELECT id,value FROM state_resources'):
        value = json.loads(text)
        if isinstance(value, dict) and set(value) == {'$blob'}:
            continue
        SessionStore._atomic(directory / (identity + '.json'), text)
        db.execute('UPDATE state_resources SET value=? WHERE id=?', (json.dumps({'$blob': identity}), identity))
    from .session_projection import persist
    saved = persist(service.data_dir, state, service._view_cache)
    from .state_records import checkpoint
    checkpoint(db, saved)
    db.execute('CREATE TABLE storage_layout(version INTEGER NOT NULL)')
    db.execute('INSERT INTO storage_layout VALUES(1)')
    db.commit()
    remove_files(db, stale)
    # Startup migration is offline. Do not shrink multi-GB databases while the
    # server is accepting requests; ordinary retention reuses freed pages.
    if existing:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('VACUUM')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')


def maintenance(service):
    """Admit at most one owned sweep after committing the state snapshot.

    Synchronous callers retain the old synchronous boundary. Live services defer
    body traversal to workers and revalidate before taking deletion authority.
    """
    now = time.monotonic()
    if getattr(service, 'closed', False) or getattr(service, 'backup_in_progress', False):
        return
    task = getattr(service, '_storage_maintenance_task', None)
    if task is not None and not task.done():
        return
    if now - getattr(service, '_last_storage_sweep', now - 61) < 60:
        return
    service._last_storage_sweep = now
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _maintenance_sync(service)
    else:
        service._storage_maintenance_task = loop.create_task(_maintenance_async(service))


def _roots(service):
    # A bound client is only a projection: it hides shared presentation fields
    # and all other clients. Retention must see every retained in-memory root.
    clients = getattr(service, 'clients', None)
    return [service._state, *clients.records.values()] if clients else service._state


def _prune(service, marked, *, limit=None, candidates=None, scan=True):
    from .resource_files import prune_marked, sweep_unindexed_locked
    if marked is None:
        scan = getattr(service, '_resource_scan', None)
        if scan is not None:
            scan.close()
            service._resource_scan = None
        service._resource_scan_status = {'blocked': True, 'complete': False, 'inspected': 0, 'removed': 0}
        service.db.rollback()
        return
    if scan:
        service._resource_scan, service._resource_scan_status = sweep_unindexed_locked(
            service.db, getattr(service, '_resource_scan', None))
    else:
        # EOF belongs to this pass only. Don't restart scandir on every remaining
        # indexed page, or repeatedly report the preceding page's removals.
        service._resource_scan_status = {'blocked': False, 'complete': True, 'inspected': 0, 'removed': 0}
    stale = prune_marked(service.db, marked, limit=limit, candidates=candidates)
    service.db.commit()
    return stale


def _finish(service, marked):
    stale = _prune(service, marked)
    if stale is not None:
        remove_files(service.db, stale)


async def _finish_async(service, marked, *, after='', scan=True, indexed=True):
    from .resource_files import remove_files_async
    # Keyset pages bound INSPECTED rows, not only successful deletions. Otherwise
    # each 1024 removals rescans an arbitrarily large retained prefix.
    candidates = ([row[0] for row in service.db.execute(
        'SELECT id FROM state_resources WHERE id>? ORDER BY id LIMIT ?',
        (after, BACKGROUND_PRUNE_LIMIT))] if marked is not None and indexed else [])
    stale = _prune(service, marked, candidates=candidates, scan=scan)
    if stale is not None:
        complete = not indexed or len(candidates) < BACKGROUND_PRUNE_LIMIT
        # This is only a traversal cursor, never a mark or deletion authority.
        # Preserve progress if the drain budget ends in a retained-heavy prefix.
        # New rows behind it wait until the next wrap, with a fresh full proof.
        service._resource_prune_after = '' if complete else candidates[-1]
        removed = await remove_files_async(service.db, stale)
        service._resource_scan_status['indexedRemoved'] = len(stale)
        service._resource_scan_status['indexedInspected'] = len(candidates)
        return {'indexedPruned': len(stale),
                'indexedFilesRemoved': (removed or {}).get('filesRemoved', 0),
                'indexedBytesRemoved': (removed or {}).get('bytesRemoved', 0),
                'unindexedFilesRemoved': service._resource_scan_status.get('removed', 0),
                'after': candidates[-1] if candidates else after,
                'complete': complete}
    return {}


def _maintenance_sync(service):
    from .resource_files import marked_references
    service.db.execute('BEGIN IMMEDIATE')
    try:
        _finish(service, marked_references(service.db, _roots(service)))
    except BaseException:
        service.db.rollback()
        raise


async def _maintenance_async(service):
    """Optimistic immutable-resource walk; only capture and prune hold app.lock.

    No writer transaction or application lock spans durable JSON/body reading.
    Before deletion, recheck every root and source index under the writer boundary.
    Bounded file deletion retains exclusion until its worker has settled.
    Catch-up budgets charge new resources/time, not snapshot counts. Reuse the
    conservative closure across a time-bounded drain, yielding between indexed
    pages and revalidating fresh roots before EACH deletion boundary.
    """
    from .resource_files import references, detach, ReferenceEdges, settle_worker
    from . import resource_roots
    from .performance_diagnostics import measure, count

    edges = getattr(service, '_resource_edges', None)
    if edges is None:
        edges = service._resource_edges = ReferenceEdges()
    before = (edges.hits, edges.misses, edges.bytes_read)
    deferred = None
    reason = None
    catchups = 0
    catchup_resources = 0
    catchup_deadline = None
    drain_deadline = None
    after = getattr(service, '_resource_prune_after', '')
    complete = False
    scan_complete = False
    removed = {}
    started = time.monotonic()
    outcome = 'interrupted'
    stage = 'root-capture-unavailable'

    def live_roots():
        state = _roots(service)
        rows = state if isinstance(state, list) else [state]
        if any(isinstance(row, dict) and row.get('_viewRecoveryPending') for row in rows):
            return None
        # Mutable objects stay on the event-loop owner. This walks references
        # without decoding JSON or hydrating ColdRecords. A short time/field
        # admission limit here made otherwise valid large installations defer
        # forever; the canonical cycle/traversal guard still fails closed.
        with measure(service, 'storage.gc.roots.live', synchronous=True):
            return set(references(state))

    def durable_roots(snapshot):
        with measure(service, 'storage.gc.roots.durable', synchronous=True):
            return resource_roots.references(snapshot)

    def inspect(batch):
        with measure(service, 'storage.gc.body', synchronous=True):
            return {child for source in batch.values() for child in edges.read(source)}

    def unavailable():
        return service.closed or getattr(service, 'backup_in_progress', False)

    async def finish(current):
        # Caller retains app.lock from the current live-root capture, with no
        # intervening await. Durable/index witnesses are checked in the writer
        # transaction before pruning; file cleanup retains the same exclusion.
        nonlocal valid, reason, stage, removed, deferred, outcome, after, complete, drain_deadline, scan_complete
        service.db.execute('BEGIN IMMEDIATE')
        try:
            with measure(service, 'storage.gc.roots', synchronous=True):
                if valid and current is None:
                    valid, reason = False, 'presentation-recovery-pending'
                if valid and not (current | saved) <= sources.keys():
                    valid, reason = False, 'live-roots-changed'
                if valid and resource_roots.capture(service.db) != snapshot:
                    valid, reason = False, 'durable-roots-changed'
            if valid:
                try:
                    valid = all(detach(service.db, key) == value for key, value in sources.items())
                except ValueError:
                    valid = False
                if not valid:
                    reason = 'resource-index-changed'
            # A slow revalidation/catch-up can use the remainder of the drain
            # budget. Do not admit another destructive batch after it expires.
            if valid and drain_deadline is not None and time.monotonic() >= drain_deadline:
                service.db.rollback()
                return
            stage = 'file-cleanup-unavailable'
            if drain_deadline is None:
                drain_deadline = time.monotonic() + BACKGROUND_DRAIN_SECONDS
            batch_result = await _finish_async(service, set(sources) if valid else None,
                                               after=after, scan=not scan_complete,
                                               indexed=not complete)
            scan_complete = service._resource_scan_status.get('complete', False)
            after = batch_result.pop('after', after)
            complete = batch_result.pop('complete', True)
            for key, value in batch_result.items():
                removed[key] = removed.get(key, 0) + value
            if not valid:
                deferred = {'deferred': True, 'reason': reason}
                service._resource_scan_status.update(deferred)
            else:
                service._storage_maintenance_error = None
                outcome = 'completed'
        except BaseException:
            service.db.rollback()
            raise

    try:
        async with service.lock:
            if unavailable():
                return
            with measure(service, 'storage.gc.roots', synchronous=True):
                initial = live_roots()
                snapshot = resource_roots.capture(service.db)
        stage = 'durable-roots-unavailable'
        saved = await settle_worker(durable_roots, snapshot) if initial is not None else set()
        if initial is not None:
            initial.update(saved)
        pending, sources = list(initial or ()), {}
        valid = initial is not None
        if not valid:
            reason = 'presentation-recovery-pending'
        while valid:
            while pending and valid:
                identities = set()
                while pending and len(identities) < BACKGROUND_EDGE_BATCH:
                    identity = pending.pop()
                    if identity not in sources:
                        identities.add(identity)
                if not identities:
                    continue
                if catchup_deadline is not None:
                    catchup_resources += len(identities)
                    if (catchup_resources > BACKGROUND_CATCHUP_RESOURCES
                            or time.monotonic() >= catchup_deadline):
                        valid, reason = False, 'root-catchup-budget'
                        break
                try:
                    async with service.lock:
                        if unavailable():
                            return
                        batch = {identity: detach(service.db, identity) for identity in identities}
                    # One handoff for a small metadata batch, not one per retained
                    # file. Bodies still stay outside the app/writer boundary;
                    # the complete mark is revalidated before any deletion.
                    pending.extend(await settle_worker(inspect, batch))
                    sources.update(batch)
                except (OSError, ValueError, TypeError, KeyError, RecursionError):
                    valid, reason = False, 'resource-body-unavailable'
            if not valid:
                break
            # A save can replace a large immutable session snapshot while we
            # walk. Extend the conservative mark, including the new closure,
            # instead of requiring an idle interval for the entire first walk.
            stage = 'root-capture-unavailable'
            async with service.lock:
                if unavailable():
                    return
                if drain_deadline is not None and time.monotonic() >= drain_deadline:
                    return
                current = live_roots()
                final_snapshot = resource_roots.capture(service.db)
                if (current is not None and final_snapshot == snapshot
                        and (current | saved) <= sources.keys()):
                    # Nothing can mutate live roots before finish takes its
                    # writer boundary. Rewalking the same graph adds no proof.
                    await finish(current)
                    if not valid or (complete and scan_complete) or time.monotonic() >= drain_deadline:
                        return
                    # finish's file worker settled before releasing app/writer
                    # exclusion. A new page must capture roots again after yield.
                    continue_drain = True
                else:
                    continue_drain = False
            if continue_drain:
                await asyncio.sleep(0)
                continue
            if current is None:
                valid, reason = False, 'presentation-recovery-pending'
                break
            if catchup_deadline is None:
                catchup_deadline = time.monotonic() + BACKGROUND_CATCHUP_SECONDS
            if time.monotonic() >= catchup_deadline:
                valid, reason = False, 'root-catchup-budget'
                break
            if final_snapshot != snapshot:
                stage = 'durable-roots-unavailable'
                saved = await settle_worker(durable_roots, final_snapshot)
                snapshot = final_snapshot
            pending = list((current | saved) - sources.keys())
            if time.monotonic() >= catchup_deadline:
                valid, reason = False, 'root-catchup-budget'
                break
            catchups += 1
        async with service.lock:
            if unavailable():
                return
            current = live_roots() if valid else None
            await finish(current)
    except asyncio.CancelledError:
        outcome = 'cancelled'
        raise  # Detached readers have settled; no writer boundary is retained.
    except Exception as exc:
        service._storage_maintenance_error = type(exc).__name__
        scan = getattr(service, '_resource_scan', None)
        if scan is not None:
            scan.close()
            service._resource_scan = None
        deferred = {'deferred': True, 'reason': stage}
        service._resource_scan_status = {'blocked': True, 'complete': False,
                                        'inspected': 0, 'removed': 0, **deferred}
        logging.getLogger(__name__).warning('Storage maintenance deferred.', exc_info=True)
    finally:
        if deferred is not None:
            outcome = 'deferred'
            count(service, 'storage.gc.rootsDeferred')
        if outcome == 'completed':
            count(service, 'storage.gc.passesCompleted')
            for key in ('indexedPruned', 'indexedFilesRemoved', 'indexedBytesRemoved', 'unindexedFilesRemoved'):
                count(service, 'storage.gc.' + key, removed.get(key, 0))
        count(service, 'storage.gc.rootCatchups', catchups)
        diagnostics = getattr(service, 'performance_diagnostics', None)
        if diagnostics is not None:
            diagnostics.record_storage_collection(outcome=outcome,
                reason=deferred['reason'] if deferred else None,
                elapsed_ms=(time.monotonic() - started) * 1000,
                catchups=catchups, **removed)
        count(service, 'storage.gc.edgeHits', edges.hits - before[0])
        count(service, 'storage.gc.edgeMisses', edges.misses - before[1])
        count(service, 'storage.gc.bodyBytesRead', edges.bytes_read - before[2])


async def close_maintenance(service):
    task = getattr(service, '_storage_maintenance_task', None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        service._storage_maintenance_task = None
