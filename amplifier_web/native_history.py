"""Read-only, cached discovery of Amplifier's shared project/session files.

The directory names are storage identifiers, not reversible encodings of paths.
Only an explicit working directory whose CLI slug matches the project is trusted.
No transcript, event stream, runtime, or CLI host is loaded to build this index.
Call ``scan`` from a worker thread: older CLI metadata can contain large configs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from .naming import automatic_metadata
import copy
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from .session_files import amplifier_home, project_slug

_MAX_METADATA = 8 * 1024 * 1024
_FIELDS = (
    'name', 'title', 'description', 'name_source', 'bundle', 'bundle_name',
    'parent_id', 'parent_session_id', 'agent_name', 'created', 'created_at', 'started_at', 'updated_at',
    'last_updated', 'last_event_at', 'ended_at', 'turn_count', 'working_dir',
    'cwd', 'project_dir', 'workspace', 'model', 'status', 'forked_from_turn', 'forked_at',
)


def _text(value):
    return value[:8192] if isinstance(value, str) and value.strip() else None


def _timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()
        except (ValueError, OverflowError):
            return None
    return None


def _signature(path):
    value = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('Shared metadata must be a regular file')
    return (value.st_ino, value.st_mtime_ns, value.st_size)


def _small_metadata(value):
    if not isinstance(value, dict):
        raise ValueError('Shared metadata must be an object')
    result = {key: value[key] for key in _FIELDS if isinstance(value.get(key), (str, int, float))}
    for key in ('parent_id', 'parent_session_id'):
        if key in value and value[key] is None:
            result[key] = None  # Explicit root identity must survive compaction.
    fork = value.get('fork')
    if isinstance(fork, dict) and _text(fork.get('source_session_id')) and type(fork.get('through_user_turn')) is int:
        result['_independent_fork'] = True
    # Some CLI versions only persisted the session cwd inside its config.
    config = value.get('config')
    if isinstance(config, dict):
        for key in ('working_dir', 'cwd', 'project_dir', 'workspace'):
            if not result.get(key) and isinstance(config.get(key), str):
                result[key] = config[key]
    return result


def classify_session(identity, *metadata_sources):
    """Distinguish runtime children from independent roots and fork lineage.

    Foundation CLI forks are independent roots with parent_id plus the documented
    forked_from_turn/forked_at fields. Other native parent identities indicate
    runtime children. Explicit native root metadata wins over stale CI metadata.
    Only missing lineage falls back to app-cli's is_top_level_session convention
    (child IDs contain '_'); span IDs are never mistaken for full parent IDs.
    """
    for metadata in metadata_sources:
        parent_keys = [key for key in ('parent_id', 'parent_session_id')
                       if key in metadata and (metadata[key] is None or isinstance(metadata[key], str))]
        parent_key = next((key for key in parent_keys if _text(metadata[key])),
                          parent_keys[0] if parent_keys else None)
        parent = _text(metadata.get(parent_key)) if parent_key else None
        forked = (metadata.get('_independent_fork') is True or
                  (type(metadata.get('forked_from_turn')) is int and metadata['forked_from_turn'] >= 0
                   and bool(_text(metadata.get('forked_at')))))
        if forked:
            return 'root', parent
        if parent_key is not None:
            return ('worker' if parent else 'root'), parent
    return ('worker' if '_' in identity else 'root'), None


class NativeHistory:
    """Index existing native files without creating or changing any of them.

    ``home`` defaults to session_files.amplifier_home(), including AMPLIFIER_HOME.
    ``known_workspaces`` may contain registered absolute folder paths (or objects
    with a ``path`` field); their exact CLI slugs can resolve older metadata.
    Returned values are detached from the cache and contain no conversation text.
    """

    def __init__(self, home=None, *, known_workspaces=()):
        self.home = Path(home).expanduser().resolve() if home is not None else amplifier_home()
        self.known_workspaces = known_workspaces
        self._files = {}
        self._projects = {}
        self._lock = threading.Lock()
        self._reads = 0

    def _read(self, path, issues, project, identity=None):
        previous = self._files.get(path)
        try:
            signature = _signature(path)
        except FileNotFoundError:
            # Atomic replacement or an incremental save can briefly hide a file.
            return previous[1] if previous else {}
        except (OSError, ValueError):
            issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': identity})
            return previous[1] if previous else {}
        if previous and previous[0] == signature:
            if previous[2]:
                issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': identity})
            return previous[1]
        failed = False
        try:
            if signature[2] > _MAX_METADATA:
                raise ValueError('Shared metadata is too large')
            with path.open('rb') as source:
                self._reads += 1
                raw = source.read(_MAX_METADATA + 1)
            if len(raw) > _MAX_METADATA:
                raise ValueError('Shared metadata is too large')
            value = _small_metadata(json.loads(raw))
        except (OSError, ValueError, RecursionError):
            failed = True
            issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': identity})
            # Cache the failed stat too: keep the last valid summary and retry
            # when the writer actually changes the file, not on every poll.
            value = previous[1] if previous else {}
        self._files[path] = (signature, value, failed)
        return value

    def _native_metadata(self, directory, issues, project):
        """Canonical parsing/backup recovery belongs to Foundation."""
        from amplifier_foundation.session.history import SessionHistoryStore
        path = directory / 'metadata.json'
        previous = self._files.get(path)
        try:
            stamps = []
            for candidate in (path, directory / 'metadata.json.backup'):
                try:
                    stamp = _signature(candidate)
                    if stamp[2] > _MAX_METADATA:
                        raise ValueError('Shared metadata is too large')
                    stamps.append(stamp)
                except FileNotFoundError:
                    stamps.append(None)
            if not any(stamps) and previous:
                return previous[1]
            signature = tuple(stamps)
            if previous and previous[0] == signature:
                if previous[2]:
                    issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': directory.name})
                return previous[1]
            self._reads += 1
            reader = SessionHistoryStore(directory, session_id=directory.name)
            value = _small_metadata(reader.load_metadata())
            failed = False
            if reader.diagnostics:
                issues.append({'kind': 'recovered', 'nativeProject': project, 'nativeIdentity': directory.name})
        except (OSError, ValueError, RecursionError):
            failed = True
            signature = locals().get('signature')
            value = previous[1] if previous else {}
            issues.append({'kind': 'unreadable', 'nativeProject': project, 'nativeIdentity': directory.name})
        self._files[path] = (signature, value, failed)
        return value

    @staticmethod
    def _directories(directory):
        with os.scandir(directory) as entries:
            return sorted((Path(entry.path) for entry in entries
                           if not entry.name.startswith('.') and entry.is_dir(follow_symlinks=False)),
                          key=lambda path: path.name)

    @staticmethod
    def _working_dir(metadata, slug):
        for key in ('working_dir', 'cwd', 'project_dir', 'workspace'):
            value = _text(metadata.get(key))
            if not value:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                continue
            try:
                resolved = str(candidate.resolve())
                if project_slug(resolved) == slug:
                    return resolved
            except (OSError, ValueError, RuntimeError):
                continue
        return None

    def _scan_project(self, project, known, issues):
        slug = project.name
        try:
            directories = self._directories(project / 'sessions')
        except FileNotFoundError:
            directories = []
        except OSError:
            issues.append({'kind': 'unreadable', 'nativeProject': slug})
            return self._projects.get(slug)
        rows = []
        candidates = set(known.get(slug, ()))
        project_metadata = self._read(project / 'metadata.json', issues, slug)
        candidate = self._working_dir(project_metadata, slug)
        if candidate:
            candidates.add(candidate)
        previous_rows = {row['nativeIdentity']: row for row in self._projects.get(slug, {}).get('sessions', [])}
        for directory in directories:
            # CLI worker IDs can contain ':' and '_'. All existing basenames
            # are safe to index; root execution has a narrower ID contract.
            native = self._native_metadata(directory, issues, slug)
            capture = self._read(directory / 'context-intelligence' / 'metadata.json', issues, slug, directory.name)
            naming = self._read(directory / 'naming.json', issues, slug, directory.name)
            meta = {**capture, **({key: value for key, value in naming.items()
                        if key in {'name', 'description', 'name_source'}} if not native.get('name') else {}), **native}
            for source in (native, capture):
                candidate = self._working_dir(source, slug)
                if candidate:
                    candidates.add(candidate)
            try:
                transcript = _signature(directory / 'transcript.jsonl')
            except FileNotFoundError:
                try:
                    transcript = _signature(directory / 'transcript.jsonl.backup')
                except (OSError, ValueError):
                    transcript = None
            except (OSError, ValueError):
                transcript = None
                issues.append({'kind': 'unreadable', 'nativeProject': slug, 'nativeIdentity': directory.name})
            turns = meta.get('turn_count')
            turns = turns if isinstance(turns, int) and not isinstance(turns, bool) and turns >= 0 else None
            # CI diagnostics and runtime probes also make session directories.
            # A real saved transcript or a positive native turn count is needed.
            previous_row = previous_rows.get(directory.name)
            if not (transcript and transcript[2]) and not (turns and turns > 0) and previous_row is None:
                continue
            updated = max([_timestamp(meta.get(key)) or 0 for key in
                           ('updated_at', 'last_updated', 'last_event_at', 'ended_at')]
                          + [transcript[1] / 1e9 if transcript else (previous_row or {}).get('updatedAt', 0)])
            created = next((_timestamp(meta.get(key)) for key in ('created', 'created_at', 'started_at')
                            if _timestamp(meta.get(key)) is not None), updated)
            # Renaming or re-saving metadata must not make an old conversation
            # recent. The transcript's write is conversation activity; without
            # it retain the prior activity or an explicit captured event time.
            recent = (transcript[1] / 1e9 if transcript and transcript[2] else
                      max([_timestamp(meta.get('last_event_at')) or 0,
                           (previous_row or {}).get('recentActivityAt', 0)] +
                          [_timestamp(meta.get(key)) or 0 for key in ('created', 'created_at', 'started_at')]))
            bundle = _text(meta.get('bundle_name')) or _text(meta.get('bundle'))
            if bundle:
                bundle = bundle.removeprefix('bundle:')
                if bundle == 'unknown':
                    bundle = None
            name = (_text(meta.get('name')) or _text(meta.get('title'))
                    or _text(meta.get('agent_name')) or f'Conversation {directory.name[:8]}')
            kind, parent = classify_session(directory.name, native, capture)
            rows.append({
                'id': uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{slug}/{directory.name}').hex,
                'nativeIdentity': directory.name, 'nativeProject': slug,
                'name': name, 'title': name, 'description': _text(meta.get('description')) or '',
                'bundle': bundle, 'parentId': parent, 'sessionKind': kind,
                'createdAt': created, 'updatedAt': updated, 'recentActivityAt': recent, 'turnCount': turns,
                'transcriptAvailable': bool(transcript and transcript[2]),
                'transcriptRevision': list(transcript[1:]) if transcript else None,
                'nameSource': _text(meta.get('name_source')), 'autoName': automatic_metadata(meta),
            })
        # A matching slug is necessary, but not sufficient when distinct paths
        # collide (e.g. /a-b/c and /a/b-c). Never silently choose one.
        path = next(iter(candidates)) if len(candidates) == 1 else None
        if path is None:
            issues.append({'kind': 'unresolved-workspace', 'nativeProject': slug})
        workspace_id = uuid.uuid5(uuid.NAMESPACE_URL, path or f'amplifier-project:{slug}').hex
        workspace = {
            'id': workspace_id, 'nativeProject': slug, 'path': path,
            'name': (Path(path).name or path) if path else slug,
            'available': bool(path and Path(path).is_dir()),
            'sessionCount': sum(row['sessionKind'] == 'root' for row in rows),
            'workerSessionCount': sum(row['sessionKind'] == 'worker' for row in rows),
        }
        for row in rows:
            row.update(workspace=path, workspaceId=workspace_id)
            reason = None
            if row['sessionKind'] == 'worker':
                reason = 'Worker sessions are read-only; continuing them as root chats would lose their worker configuration.'
            elif not re.fullmatch(r'[A-Za-z0-9-]{1,128}', row['nativeIdentity']):
                reason = 'This legacy session identifier is not supported for shared root execution.'
            elif not row['transcriptAvailable']:
                reason = 'The saved transcript is not available yet.'
            elif not path:
                reason = 'The original workspace folder could not be determined from its saved metadata.'
            elif not workspace['available']:
                reason = 'The original workspace folder is no longer available.'
            elif not row['bundle']:
                reason = 'This session does not record a resumable bundle.'
            row.update(canResume=reason is None, readOnlyReason=reason)
        return {'workspace': workspace, 'sessions': rows}

    def scan(self, *, known_workspaces=None):
        """Refresh changed metadata and return all projects and saved sessions."""
        with self._lock:
            self._reads = 0
            issues = []
            known = {}
            for item in self.known_workspaces if known_workspaces is None else known_workspaces:
                value = item.get('path') if isinstance(item, dict) else item
                if not isinstance(value, (str, Path)) or not Path(value).expanduser().is_absolute():
                    continue
                path = str(Path(value).expanduser().resolve())
                known.setdefault(project_slug(path), set()).add(path)
            try:
                projects = self._directories(self.home / 'projects')
            except FileNotFoundError:
                projects = []
            except OSError:
                projects = None
                issues.append({'kind': 'unreadable-root'})
            if projects is not None:
                current = {}
                for project in projects:
                    result = self._scan_project(project, known, issues)
                    if result is not None:
                        current[project.name] = result
                self._projects = current
                existing = set(current)
                self._files = {path: value for path, value in self._files.items()
                               if path.relative_to(self.home / 'projects').parts[0] in existing}
            workspaces = [project['workspace'] for project in self._projects.values()]
            sessions = [row for project in self._projects.values() for row in project['sessions']]
            sessions.sort(key=lambda row: (row['updatedAt'], row['id']), reverse=True)
            return copy.deepcopy({'workspaces': workspaces, 'sessions': sessions,
                                  'sessionCount': sum(row['sessionKind'] == 'root' for row in sessions),
                                  'workerSessionCount': sum(row['sessionKind'] == 'worker' for row in sessions),
                                  'issues': issues, 'metadataReads': self._reads})
