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
import time
import uuid
from collections import OrderedDict

from .session_files import amplifier_home, project_slug

_MAX_METADATA = 8 * 1024 * 1024
_STAMP_FILES = ('metadata.json', 'metadata.json.backup', 'transcript.jsonl',
                'transcript.jsonl.backup', 'naming.json', os.path.join('context-intelligence', 'metadata.json'))
_FIELDS = (
    'name', 'title', 'description', 'name_source', 'bundle', 'bundle_name',
    'parent_id', 'parent_session_id', 'agent_name', 'created', 'created_at', 'started_at', 'updated_at',
    'last_updated', 'last_event_at', 'ended_at', 'turn_count', 'working_dir',
    'cwd', 'project_dir', 'workspace', 'model', 'status', 'forked_from_turn', 'forked_at',
    'session_visibility', 'session_purpose',
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
    """Distinguish internal jobs, runtime children and independent roots.

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
        # Visibility is a producer declaration, not inferred from a prompt,
        # title, invocation mode, TTY, or generic agent/recipe origin. Those
        # also occur in user-requested standalone conversations.
        if metadata.get('session_visibility') == 'internal':
            return 'internal', parent
        if parent_key is not None:
            return ('worker' if parent else 'root'), parent
        if metadata.get('session_visibility') == 'chat':
            return 'root', None
    return ('worker' if '_' in identity else 'root'), None


class NativeHistory:
    """Index existing native files without creating or changing any of them.

    ``home`` defaults to session_files.amplifier_home(), including AMPLIFIER_HOME.
    ``known_workspaces`` may contain registered absolute folder paths (or objects
    with a ``path`` field); their exact CLI slugs can resolve older metadata.
    Returned values are detached from the cache and contain no conversation text.
    """

    def __init__(self, home=None, *, known_workspaces=(), watch=False, cache_path=None):
        self.home = Path(home).expanduser().resolve() if home is not None else amplifier_home()
        self.known_workspaces = known_workspaces
        self._files = {}
        self._file_projects = set()
        self._projects = {}
        self._lock = threading.Lock()
        self._reads = 0
        self._project_inputs = {}
        self._working_dirs = {}
        self._reconcile_at = 0
        self._watch_enabled = watch
        self._watch = None
        self._watch_root = None
        self._watch_retry_at = 0
        self._project_paths = {}
        self._known_input = None
        self._known_paths = {}
        self._snapshot_projects = ()
        self._snapshot_issues = []
        self._snapshot_revision = None
        self._change_bases = OrderedDict()
        self._cache_path = cache_path
        self._catalog = None
        self._cache_loaded = False
        self._cache_error = False
        self._dirty_files = set()

    def _load_catalog(self):
        # Called only from the discovery worker, never in host construction.
        if self._cache_loaded:
            return
        self._cache_loaded = True
        if self._cache_path is None:
            return
        try:
            from .native_catalog import NativeCatalog
            self._catalog = NativeCatalog(self._cache_path, self.home)
            self._projects, self._files = self._catalog.load()
            self._file_projects = set(self._projects)
        except (OSError, ValueError, json.JSONDecodeError):
            self._catalog = None
            self._cache_error = True
        except Exception as exc:
            import sqlite3
            if not isinstance(exc, sqlite3.Error):
                raise
            self._catalog = None
            self._cache_error = True

    @staticmethod
    def _known_signature(known):
        return frozenset(str(item.get('path') if isinstance(item, dict) else item) for item in known)

    def needs_scan(self, known_workspaces):
        """Cheap idle check; a manual refresh still always performs a scan."""
        if (time.monotonic() >= self._reconcile_at or not self._watch
                or not self._watch.unchanged()
                or self._known_signature(known_workspaces) != self._known_input):
            return True
        try:
            info = (self.home / 'projects').stat()
            if (info.st_dev, info.st_ino) != self._watch_root:
                return True
            for workspace in known_workspaces:
                if isinstance(workspace, dict) and bool(workspace.get('path') and Path(workspace['path']).is_dir()) != workspace.get('available'):
                    return True
        except (OSError, ValueError):
            return True
        return False

    def close(self):
        if self._watch:
            self._watch.close()
            self._watch = None

    def _invalidations(self):
        if not self._watch_enabled:
            return False, set()
        root = self.home / 'projects'
        try:
            info = root.stat()
            identity = (info.st_dev, info.st_ino)
        except OSError:
            identity = None
        if self._watch and (identity != self._watch_root or not self._watch.thread.is_alive()):
            self.close()
        if not self._watch and identity and time.monotonic() >= self._watch_retry_at:
            from .history_watch import HistoryWatch
            self._watch_root = identity
            self._watch_retry_at = time.monotonic() + 60
            self._watch = HistoryWatch(root)
        return self._watch.take() if self._watch else (False, set())

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
        self._dirty_files.add(path)
        self._file_projects.add(project)
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
        self._dirty_files.add(path)
        self._file_projects.add(project)
        return value

    @staticmethod
    def _directories(directory):
        with os.scandir(directory) as entries:
            return sorted((Path(entry.path) for entry in entries
                           if not entry.name.startswith('.') and entry.is_dir(follow_symlinks=False)),
                          key=lambda path: path.name)

    def _working_dir(self, metadata, slug):
        for key in ('working_dir', 'cwd', 'project_dir', 'workspace'):
            value = _text(metadata.get(key))
            if not value:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                continue
            try:
                self._project_paths.setdefault(slug, set()).add(str(candidate))
                key = str(candidate)
                if key not in self._working_dirs:
                    self._working_dirs[key] = str(candidate.resolve())
                resolved = self._working_dirs[key]
                if project_slug(resolved) == slug:
                    return resolved
            except (OSError, ValueError, RuntimeError):
                continue
        return None

    def _project_stamp(self, project, known):
        """Probe metadata, not bodies or derived rows, before rebuilding a project.

        Include absent files so new metadata/transcripts are discovered. A
        bounded full reconciliation also re-resolves symlinks and permissions.
        """
        paths = [project / 'metadata.json', project / 'sessions']
        paths.extend(known.get(project.name, ()))
        paths.extend(self._project_paths.get(project.name, ()))
        try:
            with os.scandir(project / 'sessions') as entries:
                directories = sorted((entry for entry in entries
                                      if not entry.name.startswith('.') and entry.is_dir(follow_symlinks=False)),
                                     key=lambda entry: entry.name)
        except FileNotFoundError:
            directories = []
        for directory in directories:
            # Stamps need only a string prefix. Keep the normalized scandir
            # path instead of constructing and converting a temporary Path.
            prefix = directory.path + os.sep
            paths.extend(prefix + name for name in _STAMP_FILES)
        result = [tuple(sorted(known.get(project.name, ())))]
        result.append(tuple((path, str(Path(path).resolve()))
                            for path in sorted(self._project_paths.get(project.name, ()))))
        for path in paths:
            try:
                info = os.stat(path, follow_symlinks=False)
                result.append((str(path), info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            except FileNotFoundError:
                result.append((str(path), None))
        return tuple(result)

    def _scan_project(self, project, known, issues, *, transcript_ids=None):
        slug = project.name
        issue_start = len(issues)
        prior = self._projects.get(slug)
        partial = bool(transcript_ids and prior and prior['workspace'].get('path'))
        if partial:
            # A transcript notification is not proof of directory membership.
            # Do not follow a substituted session/project ancestor.
            try:
                root = self.home / 'projects'
                sessions_root = project / 'sessions'
                safe = (stat.S_ISDIR(project.stat(follow_symlinks=False).st_mode)
                        and stat.S_ISDIR(sessions_root.stat(follow_symlinks=False).st_mode)
                        and sessions_root.resolve().is_relative_to(root.resolve()))
                for identity in transcript_ids:
                    candidate = sessions_root / identity
                    safe = (safe and Path(identity).name == identity
                            and identity not in {'', '.', '..'}
                            and stat.S_ISDIR(candidate.stat(follow_symlinks=False).st_mode)
                            and candidate.resolve().parent == sessions_root.resolve())
                if not safe:
                    partial = False
            except (OSError, ValueError, RuntimeError):
                partial = False
        try:
            directories = ([project / 'sessions' / identity for identity in sorted(transcript_ids)]
                           if partial else self._directories(project / 'sessions'))
        except FileNotFoundError:
            directories = []
        except OSError:
            issues.append({'kind': 'unreadable', 'nativeProject': slug})
            return self._projects.get(slug)
        rows = ([row for row in prior['sessions'] if row['nativeIdentity'] not in transcript_ids]
                if partial else [])
        candidates = set(known.get(slug, ()))
        if partial:
            candidates.add(prior['workspace']['path'])
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
                **({'sessionPurpose': meta['session_purpose']} if kind == 'internal'
                   and isinstance(meta.get('session_purpose'), str)
                   and re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}', meta['session_purpose']) else {}),
                'createdAt': created, 'updatedAt': updated, 'recentActivityAt': recent, 'turnCount': turns,
                'transcriptAvailable': bool(transcript and transcript[2]),
                'transcriptRevision': list(transcript[1:]) if transcript else None,
                'nameSource': _text(meta.get('name_source')), 'autoName': automatic_metadata(meta),
            })
        if partial and candidates != {prior['workspace']['path']}:
            # A concurrent metadata save can change workspace resolution even
            # when the observed notification named only a transcript. That
            # legitimately invalidates all summaries in this one project.
            del issues[issue_start:]
            return self._scan_project(project, known, issues)
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
            'internalSessionCount': sum(row['sessionKind'] == 'internal' for row in rows),
        }
        for row in rows:
            if partial and row['nativeIdentity'] not in transcript_ids:
                continue  # Old immutable summaries are not modified in place.
            row.update(workspace=path, workspaceId=workspace_id)
            reason = None
            if row['sessionKind'] == 'internal':
                reason = 'Internal job history is read-only; it is not an ordinary conversation.'
            elif row['sessionKind'] == 'worker':
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
        rows = [previous_rows[row['nativeIdentity']] if row == previous_rows.get(row['nativeIdentity']) else row
                for row in rows]
        return {'workspace': workspace, 'sessions': rows}

    def scan(self, *, known_workspaces=None, force=False):
        """Refresh changed metadata and return all projects and saved sessions."""
        return self.scan_if_changed(known_workspaces=known_workspaces, force=force)[1]

    def validate_resume(self, source):
        """Fresh admission evidence; cached positive metadata is display only."""
        project, identity = source['nativeProject'], source.get('nativeIdentity') or source['id']
        if (Path(project).name != project or project in {'', '.', '..'}
                or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', identity)):
            raise ValueError('Unsupported native session identity; saved history was preserved.')
        root = self.home / 'projects'
        project_dir = root / project
        directory = project_dir / 'sessions' / identity
        try:
            for path in (project_dir, directory.parent, directory):
                if not stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode):
                    raise ValueError('Native session directory is unavailable or substituted.')
            if not directory.resolve().is_relative_to(root.resolve()):
                raise ValueError('Native session directory is outside shared history.')
            # This independent reader has no persisted or in-memory positive cache.
            verifier = NativeHistory(self.home)
            issues = []
            native = verifier._native_metadata(directory, issues, project)
            capture = verifier._read(directory / 'context-intelligence' / 'metadata.json',
                                     issues, project, identity)
            if issues or not (native or capture):
                raise ValueError('Current native metadata could not be verified; refresh after repair.')
            kind, _ = classify_session(identity, native, capture)
            if kind != 'root':
                raise ValueError('Worker and internal histories cannot be continued as root chats.')
            workspace = source.get('workspace')
            if not workspace or not Path(workspace).is_dir() or project_slug(workspace) != project:
                raise ValueError('Current native workspace could not be verified.')
            for metadata in (native, capture):
                for key in ('working_dir', 'cwd', 'project_dir', 'workspace'):
                    path = metadata.get(key)
                    if isinstance(path, str) and Path(path).is_absolute():
                        if Path(path).expanduser().resolve() != Path(workspace).resolve():
                            raise ValueError('Native workspace metadata changed; refresh before continuing.')
            if not any((directory / name).is_file() and _signature(directory / name)[2]
                       for name in ('transcript.jsonl', 'transcript.jsonl.backup')):
                raise ValueError('The saved native transcript is unavailable.')
        except OSError as exc:
            raise ValueError('Current native files could not be verified; original history was preserved.') from exc

    def scan_changes(self, *, known_workspaces=None, force=False, since=None):
        """Detached changed metadata only; an unknown base returns a reset.

        A failed consumer can retry its old token. The bounded journal retains
        project identities, not mutable copies of the complete library.
        Canonical revalidation still runs before any cached result is returned.
        """
        return self.scan_if_changed(known_workspaces=known_workspaces,
                                    force=force, since=since, _changes=True)

    def scan_if_changed(self, *, known_workspaces=None, force=False, since=None, _changes=False):
        """Return an opaque revision and a detached snapshot, or None if unchanged.

        Discovery, file stamps, watcher recovery and availability checks still run.
        A consumer can retain its own detached snapshot and present its revision
        to avoid sorting/copying the entire library again. Manual refresh always
        returns a snapshot. Revisions belong to this index, not persisted state.
        """
        with self._lock:
            self._load_catalog()
            previous_projects = self._projects
            self._reads = 0
            self._working_dirs = {}
            watching, dirty = self._invalidations()
            reconcile = force or time.monotonic() >= self._reconcile_at
            if reconcile:
                self._reconcile_at = time.monotonic() + 60
            issues = [{'kind': 'unavailable-catalog-cache'}] if self._cache_error else []
            inputs = list(self.known_workspaces if known_workspaces is None else known_workspaces)
            signature = self._known_signature(inputs)
            if watching and not reconcile and signature == self._known_input:
                known = self._known_paths
            else:
                known = {}
                for item in inputs:
                    value = item.get('path') if isinstance(item, dict) else item
                    if not isinstance(value, (str, Path)) or not Path(value).expanduser().is_absolute():
                        continue
                    path = str(Path(value).expanduser().resolve())
                    known.setdefault(project_slug(path), set()).add(path)
                self._known_paths = known
            self._known_input = signature
            dirty_projects = {item[0] if isinstance(item, tuple) else item for item in dirty}
            try:
                projects = self._directories(self.home / 'projects')
            except FileNotFoundError:
                projects = None if self._projects else []
                if self._projects:
                    issues.append({'kind': 'unavailable-root'})
            except OSError:
                projects = None
                issues.append({'kind': 'unreadable-root'})
            if projects is not None:
                current = {}
                for project in projects:
                    cached = self._project_inputs.get(project.name)
                    workspace = self._projects.get(project.name, {}).get('workspace', {})
                    available = bool(workspace.get('path') and Path(workspace['path']).is_dir())
                    known_paths = tuple(sorted(known.get(project.name, ())))
                    if watching and not reconcile and '*' not in dirty_projects and project.name not in dirty_projects and cached and cached[0] and cached[0][0] == known_paths and workspace and workspace['available'] == available:
                        current[project.name] = self._projects[project.name]
                        issues.extend(cached[1])
                        continue
                    transcript_ids = ({item[1] for item in dirty if isinstance(item, tuple)
                                       and len(item) == 3 and item[0] == project.name and item[2] == 'transcript'}
                                      if watching and not reconcile and '*' not in dirty_projects
                                      and project.name not in dirty else set())
                    partial = bool(transcript_ids and workspace.get('path')
                                   and workspace.get('available') == available and cached
                                   and cached[0] and cached[0][0] == known_paths
                                   and not cached[1])
                    try:
                        stamp = (known_paths,) if partial else self._project_stamp(project, known)
                    except (OSError, RuntimeError):
                        stamp = None
                    if not partial and stamp is not None and cached and cached[0] == stamp and workspace and workspace['available'] == available:
                        current[project.name] = self._projects[project.name]
                        issues.extend(cached[1])
                        continue
                    start = len(issues)
                    result = (self._scan_project(project, known, issues, transcript_ids=transcript_ids)
                              if partial else self._scan_project(project, known, issues))
                    if result is not None:
                        previous = self._projects.get(project.name)
                        # Unreadable or unstable stamps can require a rebuild
                        # whose visible metadata is unchanged. Retain its prior
                        # identity only after comparing the complete result.
                        current[project.name] = previous if result == previous else result
                        self._project_inputs[project.name] = (stamp, issues[start:])
                self._projects = current
                existing = set(current)
                self._project_inputs = {key:value for key,value in self._project_inputs.items() if key in existing}
                self._project_paths = {key:value for key,value in self._project_paths.items() if key in existing}
                # File ownership changes only when a project disappears. Avoid
                # parsing tens of thousands of unchanged paths at every timer
                # reconciliation; metadata stamps and discovery still run.
                if self._file_projects - existing:
                    self._files = {path: value for path, value in self._files.items()
                                   if path.relative_to(self.home / 'projects').parts[0] in existing}
                    self._file_projects.intersection_update(existing)
            if self._catalog is not None:
                try:
                    self._catalog.save(self._projects, previous_projects, self._files, self._dirty_files)
                    self._dirty_files.clear()
                except Exception as exc:
                    import sqlite3
                    if not isinstance(exc, (OSError, ValueError, sqlite3.Error)):
                        raise
                    self._catalog = None
                    self._cache_error = True
                    issues.append({'kind': 'unavailable-catalog-cache'})
            projects = tuple(self._projects.items())
            if (self._snapshot_revision is None
                    or len(projects) != len(self._snapshot_projects)
                    or any(name != old_name or project is not old_project
                           for (name, project), (old_name, old_project)
                           in zip(projects, self._snapshot_projects))
                    or issues != self._snapshot_issues):
                # Hold the project references, not only their ids: rebuilt rows
                # must never compare unchanged after an old object is collected.
                if self._snapshot_revision is not None:
                    self._change_bases[self._snapshot_revision] = self._snapshot_projects
                    while len(self._change_bases) > 4:
                        self._change_bases.popitem(last=False)
                self._snapshot_projects = projects
                self._snapshot_issues = copy.deepcopy(issues)
                self._snapshot_revision = object()
            if _changes:
                reset = force or (since is not self._snapshot_revision and since not in self._change_bases)
                old = {} if reset else dict(self._change_bases.get(since, projects))
                upserts, workspaces, removed = [], [], []
                for name, project in projects:
                    prior = old.get(name)
                    if project is prior:
                        continue
                    if prior is None or prior['workspace'] != project['workspace']:
                        workspaces.append(project['workspace'])
                    old_rows = {row['nativeIdentity']: row for row in prior['sessions']} if prior else {}
                    new_rows = {row['nativeIdentity']: row for row in project['sessions']}
                    upserts.extend(row for key, row in new_rows.items() if row != old_rows.get(key))
                    removed.extend((name, key) for key in old_rows.keys() - new_rows.keys())
                removed_projects = old.keys() - self._projects.keys()
                removed.extend((name, row['nativeIdentity']) for name in removed_projects for row in old[name]['sessions'])
                totals = {field: sum(project['workspace'].get(field, 0) for _, project in projects)
                          for field in ('sessionCount', 'workerSessionCount', 'internalSessionCount')}
                changes = copy.deepcopy({
                    'reset': reset, 'workspaces': workspaces, 'sessions': upserts,
                    'removed': removed, 'removedProjects': sorted(removed_projects),
                    'issues': issues, 'projectCount': len(projects), **totals,
                    'metadataReads': self._reads, 'reconciled': reconcile})
                changes['base'] = since
                return self._snapshot_revision, changes
            if not force and since is self._snapshot_revision:
                return self._snapshot_revision, None
            workspaces = [project['workspace'] for project in self._projects.values()]
            sessions = [row for project in self._projects.values() for row in project['sessions']]
            sessions.sort(key=lambda row: (row['updatedAt'], row['id']), reverse=True)
            return self._snapshot_revision, copy.deepcopy({'workspaces': workspaces, 'sessions': sessions,
                                  'sessionCount': sum(row['sessionKind'] == 'root' for row in sessions),
                                  'workerSessionCount': sum(row['sessionKind'] == 'worker' for row in sessions),
                                  'internalSessionCount': sum(row['sessionKind'] == 'internal' for row in sessions),
                                  'issues': issues, 'metadataReads': self._reads})
