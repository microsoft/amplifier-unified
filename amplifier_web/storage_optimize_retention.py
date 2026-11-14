"""Host adapter to CI's public guarded diagnostic retention operation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from .capture_index import hold_replacement, rebind_replaced_capture, CaptureDrift


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(65536):
            result.update(block)
    return result.hexdigest()


def policy(operation):
    from context_intelligence.prune.policy import Policy
    cutoffs = operation['cutoffs']
    request = datetime.fromisoformat(cutoffs['requestBefore'].replace('Z', '+00:00')) if cutoffs['requestBefore'] else datetime.min.replace(tzinfo=timezone.utc)
    response = datetime.fromisoformat(cutoffs['responseBefore'].replace('Z', '+00:00')) if cutoffs['responseBefore'] else None
    return Policy(request, include_responses=response is not None,
                  diagnostic_sidecars=True, response_cutoff=response)


def inspect(row, operation):
    from context_intelligence.prune.operations import PruneRequest, prune_file
    path = Path(row['_path'])
    result = prune_file(path, PruneRequest(path.parent, policy(operation)))
    # Only owner-authored scalar facts; never expose exceptions or raw paths.
    if result.status == 'would-prune':
        return {**row, 'status': 'eligible', 'reason': 'Known diagnostic copies reached the reviewed UTC cutoff',
                '_sourceSha256': file_hash(path), '_action': 'diagnostic-retention'}
    return {**row, 'status': 'held' if result.status in {'skipped', 'unchanged', 'kept', 'unchanged-recent'} else 'malformed',
            'reason': 'Retention owner retained this file or could not establish its eligibility'}


def _receipt(path):
    from context_intelligence.prune.coordination import receipt_path
    name = receipt_path(path)
    if not name.exists():
        return None
    if name.is_symlink():
        raise CaptureDrift('Redirected retention receipt')
    with name.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise CaptureDrift('Retention receipt exceeds its metadata bound')
    value = json.loads(raw)
    if value.get('format') != 'context-intelligence-prune' or type(value.get('version')) is not int or value['version'] != 1:
        raise CaptureDrift('Unsupported retention receipt')
    return value


def _witness(receipt):
    return {'old': receipt['source'], 'new': receipt['expected']}


def _mapping(path, receipt):
    manifest = receipt.get('diagnostic_manifest')
    if not isinstance(manifest, dict) or Path(manifest.get('name', '')).name != manifest.get('name'):
        raise CaptureDrift('Retention record mapping is unavailable')
    name = path.parent / manifest['name']
    if name.is_symlink() or file_hash(name) != manifest['sha256']:
        raise CaptureDrift('Retention mapping identity changed')
    db = sqlite3.connect(name.as_uri() + '?mode=ro', uri=True)
    try:
        if db.execute('SELECT version FROM format WHERE name=?', ('ci-diagnostic-retention',)).fetchone() != (1,):
            raise CaptureDrift('Unsupported retention mapping')
        for values in db.execute('SELECT old_offset,old_bytes,old_sha256,new_offset,new_bytes,new_sha256 '
                                 'FROM record_map ORDER BY ordinal'):
            yield {'old': dict(zip(('offset', 'bytes', 'sha256'), values[:3])),
                   'new': dict(zip(('offset', 'bytes', 'sha256'), values[3:]))}
    finally:
        db.close()


async def apply(app, row, operation, *, resume=False):
    """Keep native writer exclusion and diagnostics serialization until settled."""
    from context_intelligence.prune.operations import PruneRequest, prune_file
    from .storage_cleanup import check
    from .storage_optimize import owner
    path = Path(row['_path'])
    identity = operation['operationId'] + ':' + hashlib.sha256(str(path).encode()).hexdigest()
    loop = asyncio.get_running_loop()
    cancelled = threading.Event()
    boundary = None
    async with app.lock:
        check(app, operation.get('_authority'))
        source = app._session(row['_sourceId'], hydrate=False)
        boundary = app.runtime.stopped_boundary(source)
        boundary.__enter__()
    try:
        async with app.diagnostics.flush_lock:
            async def admission(prepared=None):
                async with app.lock:
                    current = owner(app)._get(operation['operationId'], operation['_owner'])
                    check(app, current.get('_authority'))
                    if current['status'] != 'applying' or cancelled.is_set():
                        raise CaptureDrift('Saved-storage work was cancelled or stopped')
                    if prepared:
                        if prepared['source']['sha256'] != row['_sourceSha256']:
                            raise CaptureDrift('Retention receipt belongs to a different source generation')
                        with app.diagnostics._db() as db:
                            hold_replacement(db, path, identity, _witness(prepared))

            def poll():
                receipt = _receipt(path)
                loop_future = asyncio.run_coroutine_threadsafe(admission(receipt), loop)
                loop_future.result()

            def perform():
                receipt = _receipt(path)
                if receipt:
                    # Reconcile only; never call prune_file again after its
                    # irreversible marker exists, even after lost acknowledgement.
                    if not resume:
                        raise CaptureDrift('A prior retention attempt requires explicit reconciliation')
                    if receipt['source']['sha256'] != row['_sourceSha256']:
                        raise CaptureDrift('Retention receipt belongs to a different source generation')
                    current = file_hash(path)
                    if current != receipt['expected']['sha256']:
                        raise CaptureDrift('Prior retention replacement is unconfirmed')
                    return None, receipt
                if file_hash(path) != row['_sourceSha256']:
                    raise CaptureDrift('Source changed after preview')
                result = prune_file(path, PruneRequest(path.parent, policy(operation),
                    apply=True, writers_stopped=True, before_commit=poll))
                return result, _receipt(path)

            worker = asyncio.create_task(asyncio.to_thread(perform))
            try:
                result, receipt = await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled.set()
                # Do not release the native fence while an owned thread can
                # still be replacing files. It observes cancellation at its
                # mandatory owner callback, then leaves recoverable intent.
                await asyncio.gather(worker, return_exceptions=True)
                raise
            if receipt and file_hash(path) == receipt['expected']['sha256']:
                def rebind():
                    with app.diagnostics._db() as db:
                        return rebind_replaced_capture(db, path, identity, _witness(receipt),
                                                       _mapping(path, receipt))
                await asyncio.to_thread(rebind)
            if result is None:
                return {**row, 'status': 'held', 'reason':
                    'Diagnostic reference expiry was reconciled; earlier unlink outcome remains unknown and was not replayed'}
            if result.status != 'pruned':
                return {**row, 'status': 'held', 'reason': 'The retention owner declined apply; original evidence remains'}
            return {**row, 'status': 'completed' if not result.reason else 'held',
                    'reason': 'Diagnostic expiry completed' if not result.reason else
                              'Reference expiry completed but final durability or unlink needs inspection',
                    '_accounting': {'unlinked': result.asset_bytes_unlinked,
                        'inlineRemoved': result.bytes_saved, 'recoveryRetained': 0,
                        'assetsAdded': 0, 'filesystemDelta': None}}
    finally:
        if boundary is not None:
            boundary.__exit__(None, None, None)