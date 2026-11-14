"""CI's assets-first image conversion bound to Unified writer/index ownership."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading

from .capture_index import CaptureDrift, hold_replacement, rebind_replaced_capture
from .storage_optimize_retention import file_hash, source_owner, settle_owned


def operation_directory(row, operation):
    # Operation identity and location are host-owned, never a browser path.
    return Path(row['_path']).parent / ('.unified-optimize-' + operation['operationId'])


def record_mapping(directory, journal):
    path = directory / 'records.jsonl'
    if path.is_symlink() or file_hash(path) != journal['mapping_sha256']:
        raise CaptureDrift('Historical capture mapping changed')
    with path.open('rb') as stream:
        raw = stream.readline(65537)
        header = json.loads(raw)
        if (len(raw) > 65536 or header.get('format') != 'ci-optimize-record-mapping'
                or type(header.get('version')) is not int or header['version'] != 1):
            raise CaptureDrift('Unsupported historical capture mapping')
        for direction, digest_key, size_key in (('old', 'source_sha256', 'source_bytes'),
                                                ('new', 'candidate_sha256', 'candidate_bytes')):
            if (header[direction]['sha256'], header[direction]['bytes']) != (journal[digest_key], journal[size_key]):
                raise CaptureDrift('Capture mapping belongs to a different generation')
        while raw := stream.readline(65537):
            if len(raw) > 65536 or not raw.endswith(b'\n'):
                raise CaptureDrift('Historical mapping row exceeds its metadata bound')
            yield json.loads(raw)


def journal_read(directory):
    path = directory / 'journal.json'
    if path.is_symlink():
        raise CaptureDrift('Redirected historical operation journal')
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise CaptureDrift('Historical operation journal exceeds its metadata bound')
    value = json.loads(raw)
    if (value.get('format') != 'context-intelligence-optimize'
            or type(value.get('version')) is not int or value['version'] != 1):
        raise CaptureDrift('Unsupported historical operation journal')
    return value


async def apply(app, row, operation, *, resume=False, media=None):
    """Use the real CI stage/apply/resume APIs; no local rewrite algorithm.

    The media binding is an actual Foundation CaptureMediaOwner with original
    provenance supplied by an owning upload/capture receipt. An unidentified
    historical image is held, not assigned durable policy from its timestamp.
    """
    from context_intelligence.optimize import stage, apply as commit, resume as reconcile
    from .storage_cleanup import check
    from .storage_optimize import owner
    if media is None:
        from .storage_optimize_media import capture_owner
        media = capture_owner(app, row, operation)
    path = Path(row['_path'])
    directory = operation_directory(row, operation)
    cancelled = threading.Event()
    loop = asyncio.get_running_loop()
    async with app.lock:
        check(app, operation.get('_authority'))
        source = source_owner(app, row)
        boundary = app.runtime.stopped_boundary(source)
        boundary.__enter__()
    try:
        async with app.diagnostics.flush_lock:
            async def admission():
                async with app.lock:
                    current = owner(app)._get(operation['operationId'], operation['_owner'])
                    check(app, current.get('_authority'))
                    source_owner(app, row)
                    if cancelled.is_set() or current['status'] != 'applying':
                        raise CaptureDrift('Historical conversion was cancelled or stopped')
            def poll():
                asyncio.run_coroutine_threadsafe(admission(), loop).result()

            def perform():
                poll()
                if directory.exists():
                    if not resume:
                        raise CaptureDrift('Historical operation already exists; inspect and explicitly resume')
                    journal = journal_read(directory)
                else:
                    if resume:
                        # No staged operation is positively known: no historical
                        # mutation is repeated merely because a directory is missing.
                        raise CaptureDrift('Historical staging receipt is unavailable')
                    if file_hash(path) != row['_sourceSha256']:
                        raise CaptureDrift('Capture changed after preview')
                    journal = stage(path, directory, session_id=row['sessionId'],
                                    media=media, writers_stopped=True, poll=poll)
                if (journal['source_sha256'] != row['_sourceSha256']
                        or journal['source'] != str(path) or journal['session_id'] != row['sessionId']):
                    raise CaptureDrift('Historical operation belongs to another source')
                witness = {'old': {'sha256': journal['source_sha256'], 'bytes': journal['source_bytes']},
                           'new': {'sha256': journal['candidate_sha256'], 'bytes': journal['candidate_bytes']}}
                with app.diagnostics._db() as db:
                    hold_replacement(db, path, operation['operationId'], witness)
                journal = (reconcile if resume else commit)(
                    directory, media=media, writers_stopped=True, poll=poll)
                # Commit and exact index rebind have distinct journals. A crash
                # here leaves a durable hold which ordinary indexing cannot erase.
                with app.diagnostics._db() as db:
                    rebind_replaced_capture(db, path, operation['operationId'], witness,
                                            record_mapping(directory, journal))
                return journal

            journal = await settle_owned(perform, cancelled)
            accounting = journal['accounting']
            return {**row, 'status': 'completed', 'reason': 'Image copies externalized; exact original recovery generation retained',
                    '_captureOperation': str(directory),
                    '_installedSha256': journal['candidate_sha256'],
                    '_accounting': {'inlineRemoved': accounting['inline_bytes_removed'],
                        'assetsAdded': accounting['new_asset_bytes'],
                        'refsAndJournalAdded': None,
                        'recoveryRetained': accounting['retained_recovery_bytes'],
                        'unlinked': 0, 'filesystemDelta': None}}
    finally:
        boundary.__exit__(None, None, None)


async def apply_combined(app, row, operation, *, resume=False, media=None):
    """Two owner transactions, not a fictitious atomic whole-file operation.

    Checkpoint the image stage before entering expiry. Both maps, the original,
    and the permanent replay fence remain. On an unknown second stage, explicit
    resume skips the completed first stage and only reconciles its exact receipt.
    """
    from .storage_optimize import owner
    from .storage_optimize_retention import apply as expire
    if media is None:
        from .storage_optimize_media import capture_owner
        media = capture_owner(app, row, operation)
    stages = row.get('_stages', {})
    current = row
    if 'images' not in stages:
        converted = await apply(app, row, operation, resume=resume, media=media)
        if converted['status'] != 'completed':
            return converted
        current = {**converted, 'status': 'attempting',
            '_stages': {'images': converted['_accounting']},
            '_sourceSha256': converted['_installedSha256'],
            '_accounting': combine_accounting(converted['_accounting'], {}),
            'stageAccounting': {'images': converted['_accounting'], 'diagnosticExpiry': {}},
            'stages': {'images': 'completed', 'diagnosticExpiry': 'pending'}}
        # The admitted row ordinal is host-owned; no client path or cursor can
        # choose which file's durable checkpoint is replaced.
        owner(app)._file(operation['operationId'], row['_ordinal'], current)
        stages = current['_stages']
    # Expiry can remove a reference as well as change the installed file hash.
    # Its actual diagnostic owner must verify the final closure again, never
    # promote the original diagnostic ACK into canonical transcript authority.
    directory = Path(current['_captureOperation'])
    def acknowledge_final():
        media.acknowledge(directory / 'assets.jsonl', source=Path(row['_path']),
                          sha256=file_hash(Path(row['_path'])))
    expired = await expire(app, current, operation, resume=resume, after_rebind=acknowledge_final)
    capture_bytes, expiry_bytes = stages['images'], expired.get('_accounting', {})
    accounting = combine_accounting(capture_bytes, expiry_bytes)
    public_stages = {'images': capture_bytes, 'diagnosticExpiry': expiry_bytes}
    if expired['status'] != 'completed':
        return {**current, 'status': 'held', 'reason':
            'Image stage completed; diagnostic expiry is held for exact reconciliation',
            '_accounting': accounting, 'stageAccounting': public_stages,
            'stages': {'images': 'completed', 'diagnosticExpiry': 'held'}}
    return {**current, 'status': 'completed', 'reason':
        'Image and diagnostic expiry stages completed; original recovery bytes remain retained',
        'stageAccounting': public_stages,
        'stages': {'images': 'completed', 'diagnosticExpiry': 'completed'}, '_accounting': accounting}


def combine_accounting(first, second):
    """Unknown stage contributions cannot become image-only aggregate facts."""
    return {key: first[key] + second[key]
            if type(first.get(key)) is int and type(second.get(key)) is int else None
            for key in ('inlineRemoved', 'assetsAdded', 'refsAndJournalAdded',
                        'recoveryRetained', 'unlinked', 'filesystemDelta')}