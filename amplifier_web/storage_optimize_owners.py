"""Owned-root discovery and adapters, never a second conversion/deletion engine."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat

from .session_files import amplifier_home, project_slug, validate_id


def _metadata(path):
    with path.open('rb') as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError('Metadata exceeds the supported discovery window')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Unsupported metadata')
    return value


def _directories(path):
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    yield Path(entry.path)
    except FileNotFoundError:
        return


def _stamp(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Linked or nonregular source')
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


class OwnerAdapters:
    def __init__(self, app):
        self.app = app

    async def discover(self, operation):
        # Explicit native roots, independent of cwd and catalog inclusion.
        canonical = amplifier_home() / 'projects'
        configured = os.environ.get('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', '').strip()
        captures = Path(configured).expanduser() if configured and '${' not in configured else canonical
        if not captures.is_absolute():
            captures = canonical
        roots = [canonical] + ([captures] if captures != canonical else [])
        selected = project_slug(operation['_workspace']) if operation['_workspace'] else None
        for root in roots:
            for project in _directories(root):
                if operation['scope'] != 'all' and project.name != selected:
                    continue
                for directory in _directories(project / 'sessions'):
                    await asyncio.sleep(0)
                    try:
                        validate_id(directory.name)
                    except ValueError:
                        continue
                    metadata_path = directory / 'metadata.json'
                    capture_meta = directory / 'context-intelligence' / 'metadata.json'
                    try:
                        metadata = _metadata(metadata_path if metadata_path.is_file() else capture_meta)
                        parent = metadata.get('parent_id') or metadata.get('parent_session_id')
                        kind = 'worker' if parent else 'root'
                    except (OSError, ValueError):
                        metadata, parent, kind = {}, None, 'unknown'
                    if operation['scope'] == 'current' and directory.name != operation['_nativeId'] and parent != operation['_nativeId']:
                        continue
                    for name, owner in (('transcript.jsonl', 'foundation'),
                                        ('context-intelligence/events.jsonl', 'context-intelligence')):
                        path = directory / name
                        if owner == 'foundation' and root != canonical:
                            continue
                        if owner == 'context-intelligence' and root != captures:
                            continue
                        if not path.exists():
                            continue
                        row = {'owner': owner, 'sessionId': directory.name, 'kind': kind,
                               'status': 'held', 'reason': 'Owner qualification required', 'bytes': None,
                               '_path': str(path), '_metadata': metadata,
                               '_project': project.name, '_parent': parent}
                        # No pathname may turn redirected ancestors into an
                        # owned capture. Keep an explicit held row, not silent
                        # traversal through a user-created directory link.
                        if any(parent.is_symlink() for parent in (root, project, directory,
                                                                  path.parent)):
                            row['reason'] = 'Redirected storage ancestry is protected'
                            yield row
                            continue
                        try:
                            stamp = _stamp(path)
                            row.update(bytes=stamp[2], _stamp=stamp)
                        except (OSError, ValueError):
                            row['reason'] = 'Source is unreadable, linked or not a regular owned file'
                        yield row

    def _registered(self, row):
        # This is a bounded single-owner query, never hydrate history.
        db = self.app.db
        native = row['sessionId']
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='history_local_rows'").fetchone():
            return None
        found = db.execute('SELECT id FROM history_local_rows WHERE native_id=? LIMIT 2', (native,)).fetchall()
        if len(found) != 1:
            return None
        source = self.app._session(found[0][0], hydrate=False)
        if project_slug(source['workspace']) != row['_project']:
            return None
        return source

    async def preview(self, row, operation):
        if '_stamp' not in row:
            return row
        if row['owner'] == 'foundation':
            try:
                from .storage_optimize_canonical import preview
            except ImportError:
                return {**row, 'status': 'unsupported',
                        'reason': 'Canonical historical adapter is not yet qualified on this installation'}
            return await preview(self.app, row, operation)
        source = self._registered(row)
        if source is None or row['kind'] != 'root':
            return {**row, 'reason': 'Writer exclusion for this historical or worker capture is unqualified'}
        row['_sourceId'] = source['id']
        if operation['extractImages'] and any(operation['cutoffs'].values()):
            return {**row, 'status': 'held', 'reason':
                'Combined image extraction and diagnostic expiry await both qualified stages; neither choice was silently dropped'}
        if not operation['extractImages'] and any(operation['cutoffs'].values()):
            try:
                from .storage_optimize_retention import inspect
                return await asyncio.to_thread(inspect, row, operation)
            except ImportError:
                return {**row, 'status': 'unsupported', 'reason': 'Compatible diagnostic retention owner is not installed'}
        try:
            from context_intelligence.optimize import preview
        except ImportError:
            return {**row, 'status': 'unsupported',
                    'reason': 'Compatible capture Optimize owner is not installed'}
        try:
            result = await asyncio.to_thread(preview, Path(row['_path']), session_id=row['sessionId'])
        except (ValueError, OSError):
            return {**row, 'status': 'malformed', 'reason': 'Capture format or source could not be verified'}
        if _stamp(Path(row['_path'])) != row['_stamp']:
            return {**row, 'status': 'drift', 'reason': 'Source changed during preview'}
        # A preview cannot assert writers stopped from an absent catalog entry
        # or from an advisory lock. Actual apply must acquire native ownership.
        row['_sourceSha256'] = result['source_sha256']
        try:
            from .storage_optimize_media import capture_owner, UnknownHistoricalMedia
            capture_owner(self.app, row, operation)
        except (UnknownHistoricalMedia, ImportError):
            return {**row, 'status': 'held',
                    'reason': 'Original image provenance and expiry are unverified; no pixels were converted'}
        return {**row, 'status': 'eligible', '_action': 'capture-images',
                'reason': 'Known image copies have an original owner receipt'}

    async def apply(self, row, operation, *, resume=False):
        if row['owner'] == 'foundation':
            from .storage_optimize_canonical import apply
            return await apply(self.app, row, operation, resume=resume)
        if row.get('_action') == 'diagnostic-retention':
            from .storage_optimize_retention import apply
            return await apply(self.app, row, operation, resume=resume)
        if row.get('_action') == 'capture-images':
            from .storage_optimize_capture import apply
            return await apply(self.app, row, operation, resume=resume)
        # No guessed compatibility, no direct unlink and no replay. Actual
        # owner API integration is activated only at its qualified dependency.
        return {**row, 'status': 'held', 'reason': 'The selected format owner is not qualified for apply'}