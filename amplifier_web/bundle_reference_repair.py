"""Name-only repair of legacy Work chats; never mount or replace a bundle."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import AsyncExitStack
import json
from pathlib import Path
import stat

from .session_files import amplifier_home, project_slug

LEGACY = {'Work', 'bundle:Work'}


def read_metadata(path):
    info = path.lstat()
    if (any(parent.is_symlink() for parent in (path, *list(path.parents)[:4]))
            or not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024):
        raise ValueError('Unsafe or oversized session metadata.')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Session metadata must be an object.')
    for key in ('bundle_name', 'bundle', 'working_dir', 'workspace'):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ValueError('Session routing metadata must contain strings.')
    return value


def discover():
    """Include unopened and zero-turn roots, independently of UI filters."""
    from .native_history import classify_session
    rows, issues = [], []
    projects = amplifier_home() / 'projects'
    paths = {path.parent / 'metadata.json' for name in ('metadata.json', 'metadata.json.backup')
             for path in projects.glob('*/sessions/*/' + name)}
    for path in paths:
        try:
            if not path.exists():
                backup = read_metadata(path.with_suffix('.json.backup'))
                if (backup.get('bundle_name') or backup.get('bundle')) in LEGACY:
                    issues.append({'nativeProject': path.parents[2].name,
                        'nativeIdentity': path.parent.name, 'status': 'missing-primary'})
                continue
            metadata = read_metadata(path)
            if (metadata.get('bundle_name') or metadata.get('bundle')) not in LEGACY:
                continue
            if classify_session(path.parent.name, metadata)[0] != 'root':
                continue
            rows.append({'directory': path.parent, 'nativeIdentity': path.parent.name,
                'nativeProject': path.parents[2].name,
                'workspace': metadata.get('working_dir') or metadata.get('workspace')})
        except (OSError, ValueError):
            issues.append({'nativeProject': path.parents[2].name,
                'nativeIdentity': path.parent.name, 'status': 'unreadable'})
    return rows, issues


def reference_status(home, row):
    from .host.config import read_config
    from .host.bundle_paths import canonical_bundle_reference, local_bundle_path
    workspace = row.get('workspace')
    if not workspace or not Path(workspace).is_dir():
        return 'workspace-unavailable'
    if project_slug(workspace) != row['nativeProject']:
        return 'identity-conflict'
    # Never change an explicit local Work bundle or a differently sourced alias.
    config = read_config(workspace, home=home, session_id=row['nativeIdentity'])
    if local_bundle_path(config, 'Work') or local_bundle_path(config, 'work'):
        return 'local-bundle'
    target = config.registrations.get('work')
    if not target:
        return 'target-unregistered'
    if 'Work' in config.registrations:
        if config.registrations['Work'] != target:
            return 'alias-conflict'
    elif canonical_bundle_reference(config, 'Work') != 'work':
        return 'alias-conflict'
    return None


def repair_metadata(home, row, *, apply):
    """Caller holds execution ownership through view publication."""
    from amplifier_foundation.session.metadata import metadata_lock
    from amplifier_foundation.session.history import SessionHistoryStore
    conflict = reference_status(home, row)
    if conflict:
        return conflict
    if not apply:
        return 'eligible'
    directory = row['directory']
    with metadata_lock(directory):
        metadata = read_metadata(directory / 'metadata.json')
        selected = metadata.get('bundle_name') or metadata.get('bundle')
        if selected == 'work':
            return 'already-canonical'
        if selected not in LEGACY:
            return 'changed-since-preview'
        if any(metadata.get(key) not in {*LEGACY, 'work', None, ''}
               for key in ('bundle_name', 'bundle')):
            return 'reference-conflict'
        metadata['bundle_name'] = 'work'
        if 'bundle' in metadata:
            metadata['bundle'] = 'work'
        SessionHistoryStore(directory)._save_metadata_unlocked(metadata)
        if read_metadata(directory / 'metadata.json') != metadata:
            raise RuntimeError('Bundle metadata repair could not be verified.')
        return 'changed'


async def canonicalize(service, *, apply=False):
    """Repair exact legacy IDs and publish views, preserving all other fields."""
    from .naming import directory_for
    rows, issues = await asyncio.to_thread(discover)
    candidates = {str(row['directory']): row for row in rows}
    async with service.lock:
        for session in service.state['sessions']:
            if session.get('sessionKind', 'root') != 'root' or not session.get('workspace'):
                continue
            directory = directory_for(service.data_dir, session)
            if str(directory) not in candidates and session.get('bundle') not in LEGACY:
                continue
            row = candidates.setdefault(str(directory), {
                'directory': directory,
                'nativeIdentity': session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id'],
                'nativeProject': session.get('nativeProject') or project_slug(session['workspace']),
                'workspace': session.get('workspace')})
            row.setdefault('sessionIds', []).append(session['id'])
    results = []
    runtime = service.runtime
    for row in candidates.values():
        result = {key: row[key] for key in ('nativeProject', 'nativeIdentity')}
        result['sessionIds'] = row.get('sessionIds', [])
        from amplifier_foundation.session.shared_state import SharedSessionStore, SessionBusyError
        async with AsyncExitStack() as locks:
            busy = False
            if apply and runtime is not None:
                for sid in sorted(row.get('sessionIds', [])):
                    lock = runtime._locks.setdefault(sid, asyncio.Lock())
                    if lock.locked():
                        busy = True
                        break
                    await locks.enter_async_context(lock)
                    worker = runtime.workers.get(sid)
                    if worker and worker['process'].returncode is None and not worker.get('parked'):
                        busy = True
                        break
            if busy:
                result['status'] = 'busy'
            else:
                try:
                    conflict = await asyncio.to_thread(reference_status, service.data_dir, row)
                    if conflict:
                        result['status'] = conflict
                        results.append(result)
                        continue
                    if apply:
                        # acquire() is nonblocking. Do not put acquisition in an
                        # unowned thread: cancellation could lose a held handle.
                        held = SharedSessionStore(row['workspace'], row['nativeIdentity']).acquire(
                            app='amplifier-unified-bundle-repair')
                        locks.callback(held.release)
                    path = row['directory'] / 'metadata.json'
                    if not path.exists():
                        # A UI-only chat has no native identity yet; do not
                        # invent history or promote a legacy checkpoint.
                        legacy = Path(service.data_dir) / 'sessions' / row['nativeIdentity'] / 'checkpoint.json'
                        native = any((row['directory'] / name).exists() for name in
                                     ('metadata.json.backup', 'transcript.jsonl', 'transcript.jsonl.backup'))
                        result['status'] = ('missing-primary' if native else 'legacy-only' if legacy.exists()
                                            else 'changed' if apply else 'eligible')
                    else:
                        write = asyncio.create_task(asyncio.to_thread(
                            repair_metadata, service.data_dir, row, apply=apply))
                        try:
                            result['status'] = await asyncio.shield(write)
                        except asyncio.CancelledError:
                            # A cancelled action cannot release execution
                            # ownership while its metadata write is still live.
                            while not write.done():
                                try:
                                    await asyncio.shield(write)
                                except asyncio.CancelledError:
                                    continue
                            write.result()
                            raise
                    if apply and result['status'] in {'changed', 'already-canonical'}:
                        async with service.lock:
                            for sid in row.get('sessionIds', []):
                                current = service._session(sid)
                                if (directory_for(service.data_dir, current) != row['directory']
                                        or current.get('bundle') not in {*LEGACY, 'work'}):
                                    result['status'] = 'view-conflict'
                                    continue
                                if current.get('bundle') in LEGACY:
                                    current['bundle'] = 'work'
                                if (runtime is not None and current.get('status') == 'starting'
                                        and current.get('failure') and not runtime.workers.get(sid)):
                                    # Admission is held and no worker owns this
                                    # failed attempt. Repair isn't proof of ready.
                                    current['status'] = 'error'
                                    current.pop('progress', None)
                                    current['preparation'] = {'status': 'cold'}
                                if runtime is not None:
                                    cached = runtime.workers.get(sid, {}).get('start_session')
                                    if cached and cached.get('bundle') in LEGACY:
                                        cached['bundle'] = 'work'
                                    retired = runtime._retired.get(sid)
                                    if retired and retired[0].get('bundle') in LEGACY:
                                        retired[0]['bundle'] = 'work'
                            service._publish_changes(sessions=set(row.get('sessionIds', [])))
                except SessionBusyError:
                    result['status'] = 'busy'
                except (OSError, ValueError, RuntimeError) as exc:
                    result.update(status='failed', errorType=type(exc).__name__)
            results.append(result)
    results.extend(issues)
    counts = dict(Counter(row['status'] for row in results))
    report = {'applied': apply, 'from': 'Work', 'to': 'work', 'workReplayed': False,
        'counts': counts, 'sessions': results,
        'complete': apply and all(row['status'] in {'changed', 'already-canonical'} for row in results)}
    async with service.lock:
        service.state.setdefault('maintenance', {})['bundleReferences'] = report
        service._publish_changes(globals={'maintenance'})
    return report