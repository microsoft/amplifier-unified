"""Unified's stopped-owner adapter to Foundation canonical media conversion.

Foundation owns the bounded parser, assets-first candidate, physical activation,
per-file journal and sealed checkpoint verifier. Unified owns source selection,
real execution exclusion, operation/Stop admission and display-index joins.
Legacy inline uploads/screenshots lack original retention admission: preview
reports that hold rather than inventing an origin or renewed expiry.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import threading

from .session_files import project_slug, sessions_dir
from .errors import AppError


class CanonicalHeld(ValueError):
    """Protected or uncertain state; preserve all source/recovery files."""

IDENTITY_FIELDS = ('id', 'workspace', 'runtimeSessionId', 'nativeIdentity',
                   'executionRevision', 'interruptionRevision')


def _binding(source):
    return {key: source.get(key) for key in IDENTITY_FIELDS}


def _stamp(path):
    info = path.lstat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _registered(app, row):
    if not app.db.execute("SELECT 1 FROM sqlite_master WHERE name='history_local_rows'").fetchone():
        return None
    values = app.db.execute('SELECT id FROM history_local_rows WHERE native_project=? AND native_id=? LIMIT 2',
                            (row['_project'], row['sessionId'])).fetchall()
    if len(values) != 1:
        return None
    try:
        source = app._session(values[0][0], hydrate=False)
    except AppError as error:
        if error.status not in {404, 409}:
            raise
        return None
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
    expected = sessions_dir(source['workspace']) / native / 'transcript.jsonl'
    if (native != row['sessionId'] or project_slug(source['workspace']) != row['_project']
            or expected != Path(row['_path'])):
        return None
    return source


def _qualified(source, row):
    # A catalog entry/advisory shared lock does not fence a standalone writer
    # retaining an open FD. Worker and unknown historical owners stay explicit.
    return (source is not None and row.get('kind') == 'root'
            and row.get('_metadata', {}).get('host') == 'amplifier-unified')


def _operation_dir(path, operation):
    name = hashlib.sha256(operation['operationId'].encode()).hexdigest()
    return path.parent / 'recovery' / 'storage-optimize' / name


async def _settle_thread(function, cancel):
    worker = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel.set()
        # Never release the actual execution lease while a cancelled coroutine's
        # thread can still replace, flush or checkpoint an open native file.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not worker.cancelled():
            worker.exception()  # Consume a settled failure; cancellation wins.
        raise


async def preview(app, row, operation):
    """Whole-source read-only inventory under real native execution exclusion."""
    if os.name != 'posix':
        return {**row, 'status': 'unsupported', 'reason': 'Canonical conversion is qualified on POSIX only'}
    if not operation.get('extractImages'):
        return {**row, 'status': 'held', 'reason': 'Canonical history is not disposable diagnostic detail'}
    try:
        from amplifier_foundation.session import media_conversion as conversion
        from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    except ImportError:
        return {**row, 'status': 'unsupported', 'reason': 'Compatible canonical conversion owner is not installed'}
    boundary = None
    cancel = threading.Event()
    try:
        async with app.lock:
            source = _registered(app, row)
            if not _qualified(source, row):
                return {**row, 'status': 'held', 'reason': 'Writer exclusion for this historical or worker source is unqualified'}
            binding = _binding(source)
            candidate_boundary = app.runtime.stopped_boundary(source)
            candidate_boundary.__enter__()
            boundary = candidate_boundary
        path = Path(row['_path'])

        def inspect():
            guard = NativeTranscriptGuard(path)
            if _stamp(path) != row['_stamp']:
                raise CanonicalHeld('Source changed since discovery')
            backup = path.with_name('transcript.jsonl.backup')
            plan = conversion.preview(path, owner=source['id'], session_id=row['sessionId'],
                                      fence=lambda _: nullcontext(),
                                      backups=(backup,) if backup.exists() else ())
            guard.check()
            return plan

        plan = await _settle_thread(inspect, cancel)
        async with app.lock:
            latest = _registered(app, row)
            if latest is None or _binding(latest) != binding:
                raise CanonicalHeld('Canonical execution identity changed during preview')
        return {**row, '_sourceId': binding['id'], '_sourceBinding': binding,
                '_canonicalPlan': asdict(plan),
                '_sourceSha256': plan.source.sha256, 'status': 'held',
                'canonicalRecords': plan.records, 'canonicalInlineImages': plan.inline_images,
                'reason': ('Original image provenance and retention admission are unavailable; legacy pixels were preserved'
                           if plan.inline_images else 'No canonical inline images require extraction')}
    except (ValueError, RuntimeError, OSError):
        return {**row, 'status': 'held', 'reason': 'Canonical source, recovery state or stopped-writer ownership could not be verified'}
    finally:
        if boundary is not None:
            boundary.__exit__(None, None, None)


def activate_candidate(plan, directory, *, media_store, guard, admit, cancelled=lambda: False,
                       resume=False):
    """Thin physical-activation seam; caller retains its actual execution lease.

    Candidate construction must already use a genuine original admission owner.
    This function creates no such admission. Foundation owns all physical writes,
    saved intent/reconciliation and the exact bounded legacy checkpoint ACK.
    """
    from amplifier_foundation.session import media_activation as activation
    if plan.selected_records != plan.records or media_store.session_id != plan.session_id:
        raise CanonicalHeld('Only the exact complete owning canonical session may be activated')
    # The reusable helper verifies its own saved plan. Bind that plan to the
    # host's reviewed preview too, not merely to an equal session/owner string.
    saved_plan = Path(directory) / 'plan.json'
    if saved_plan.is_symlink():
        raise CanonicalHeld('Redirected canonical plan is protected')
    with saved_plan.open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024 or json.loads(raw) != json.loads(json.dumps(asdict(plan))):
        raise CanonicalHeld('Saved canonical plan differs from the reviewed preview')
    if not resume:
        guard.check()
    invoke = activation.resume_candidate if resume else activation.apply_candidate
    result = invoke(Path(directory), Path(plan.source.path).parent,
        session_id=plan.session_id, owner=plan.owner, fence=lambda _: nullcontext(),
        admit=admit, asset_reader=lambda ref: media_store.resolve_ref(ref, committed=False),
        commit_media=media_store.acknowledge_checkpoint, cancelled=cancelled)
    if result.state != 'acknowledged':
        raise CanonicalHeld('Canonical activation remains ' + result.state)
    guard.saved()
    return result


def _invalidate(app, source, row):
    # Invalidate only the actual target, not all historical sessions. Indexes
    # remain rebuildable views, never a canonical resume/retention authority.
    index = app.history.index
    with index._lock:
        index._pending_invalidations.add((row['_project'], row['sessionId'], 'transcript'))
    app.history.windows.invalidate({source['id']})


async def _join(app):
    # Owns its own service.lock; never await this while holding app.lock. This
    # metadata-only join preserves newer titles/status and loads no transcript.
    return await app.history.refresh_catalog(force=False) is True


async def apply(app, row, operation, *, resume=False):
    """Resume a host-admitted saved operation, never infer authority from files.

    No current legacy producer supplies the required original admission. Thus
    preview never sets _action and ordinary legacy apply remains held. A saved
    candidate path alone cannot enable this branch; only the canonical owner can
    bind that private operation-ledger action after a real admission is added.
    """
    if row.get('_action') != 'canonical-activation':
        return {**row, 'status': 'held', 'reason':
                'Original image admission is unavailable; no historical conversion was started'}
    try:
        from amplifier_foundation.session.media_conversion import ConversionPlan, SourceIdentity
        from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
        from amplifier_foundation.session import media_activation  # noqa: F401
    except ImportError:
        return {**row, 'status': 'unsupported', 'reason': 'Compatible canonical activation owner is not installed'}
    from .host.storage import SessionStore
    from .storage_cleanup import check
    from .storage_optimize import owner
    path = Path(row['_path'])
    directory = _operation_dir(path, operation)
    boundary = None
    cancel = threading.Event()
    loop = asyncio.get_running_loop()
    attempted = False
    invalidated = False
    try:
        async with app.lock:
            check(app, operation.get('_authority'))
            source = _registered(app, row)
            if (not _qualified(source, row) or source['id'] != row.get('_sourceId')
                    or _binding(source) != row.get('_sourceBinding')):
                raise CanonicalHeld('Canonical ownership is unavailable')
            candidate_boundary = app.runtime.stopped_boundary(source)
            candidate_boundary.__enter__()
            boundary = candidate_boundary

        async def admission():
            async with app.lock:
                try:
                    current = owner(app)._get(operation['operationId'], operation['_owner'])
                except AppError as error:
                    if error.status != 404:
                        raise
                    raise CanonicalHeld('Canonical operation is unavailable') from error
                check(app, current.get('_authority'))
                if current['status'] != 'applying' or cancel.is_set():
                    raise CanonicalHeld('Canonical work was cancelled or stopped')
                latest = _registered(app, row)
                if latest is None or _binding(latest) != row['_sourceBinding']:
                    raise CanonicalHeld('Canonical execution identity changed')
                return True

        def perform():
            raw = row['_canonicalPlan']
            plan = ConversionPlan(**{**raw, 'source': SourceIdentity(**raw['source']),
                                    'backups': tuple(SourceIdentity(**item) for item in raw['backups'])})
            if (plan.source.path != str(path) or plan.source.sha256 != row['_sourceSha256']
                    or plan.owner != source['id'] or plan.session_id != row['sessionId']):
                raise CanonicalHeld('Canonical preview scope changed')
            store = SessionStore(path.parent.parent).media(row['sessionId'])
            try:
                return activate_candidate(plan, directory, media_store=store,
                    guard=NativeTranscriptGuard(path),
                    admit=lambda *_: asyncio.run_coroutine_threadsafe(admission(), loop).result(),
                    cancelled=cancel.is_set, resume=resume)
            finally:
                store.close()

        attempted = True
        result = await _settle_thread(perform, cancel)
        async with app.lock:
            _invalidate(app, source, row)
            invalidated = True
        joined = await _join(app)
        return {**row, 'status': 'completed', 'reason': 'Exact canonical checkpoint verified; original recovery bytes remain',
                'viewJoin': 'completed' if joined else 'pending',
                'canonicalCheckpoint': {'sha256': result.sha256, 'bytes': result.bytes,
                    'records': result.records, 'durability': 'legacy-checkpoint-backed'},
                '_accounting': {'inlineRemoved': None, 'assetsAdded': None,
                    'refsAndJournalAdded': None, 'recoveryRetained': sum(
                        item['size'] for item in (row['_canonicalPlan']['source'], *row['_canonicalPlan']['backups'])),
                    'unlinked': 0, 'filesystemDelta': None}}
    except (ValueError, RuntimeError, OSError):
        return {**row, 'status': 'attempting', 'reason':
                'Canonical replacement or checkpoint remains unconfirmed; preserved evidence requires reconciliation'}
    finally:
        try:
            if attempted and not invalidated:
                # Covers lost ACK, partial failure and cancellation AFTER the
                # owned thread has settled. Invalidation is not a success claim.
                # This synchronous owner-loop fence cannot itself be abandoned
                # by a second cancellation before releasing native ownership.
                _invalidate(app, source, row)
        finally:
            if boundary is not None:
                boundary.__exit__(None, None, None)
