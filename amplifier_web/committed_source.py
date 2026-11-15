"""Canonical transcript selection, shared by host readers.

Recognition is not activation. A damaged or partially installed v2 namespace is
still authoritative: it must never uncover a stale legacy/private checkpoint.
Foundation owns format validation, committed byte bounds and writer mechanisms.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import threading
import os
import re
import atexit

LEGACY_NAMES = ('transcript.jsonl', 'transcript.jsonl.backup')
SOURCE_FILES = (*LEGACY_NAMES, 'transcript.jsonl.append-pending', 'transcript.v2',
                'transcript.v2/current.json', 'transcript.v2/ready.json',
                'transcript.v2/migration.json', 'transcript.jsonl/format.json',
                'transcript.jsonl.backup/format.json')


def versioned(directory):
    try:
        (Path(directory) / 'transcript.v2').lstat()
    except FileNotFoundError:
        return False
    return True


def present(directory):
    root = Path(directory)
    if versioned(root):
        return True
    for name in LEGACY_NAMES:
        try:
            (root / name).lstat()
        except FileNotFoundError:
            continue
        return True
    return False


def refuse_legacy_write(directory):
    if versioned(directory):
        raise ValueError('A committed transcript requires an owner-bound T2 operation; legacy writing is refused.')


def revision(directory):
    """Cheap invalidation witness, not proof of readable committed content."""
    values = []
    for name in source_files(directory):
        try:
            info = (Path(directory) / name).lstat()
            values.append([info.st_dev, info.st_ino, info.st_mode, info.st_size,
                           info.st_mtime_ns, info.st_ctime_ns])
        except (FileNotFoundError, NotADirectoryError):
            values.append(None)
    return ['committed', hashlib.sha256(json.dumps(values).encode()).hexdigest()]


def source_files(directory):
    """Bounded descriptor selection for invalidation only, never read authority."""
    paths = list(SOURCE_FILES)
    if versioned(directory):
        try:
            fd = os.open(Path(directory) / 'transcript.v2/current.json',
                         os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                raw = stream.read(65537)
            if len(raw) <= 65536:
                value = json.loads(raw)
                body = value.get('body')
                if isinstance(body, str) and re.fullmatch(r'g-[A-Za-z0-9_.-]{1,221}\.jsonl', body):
                    paths.append('transcript.v2/' + body)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            pass
    return paths


def reader(directory):
    try:
        from amplifier_foundation.session.transcript_v2 import CommittedTranscriptReader
    except ImportError:
        raise ValueError('This runtime cannot read the committed transcript format; original files are unchanged.') from None
    return CommittedTranscriptReader(directory)


def load(directory, *, session_id):
    """Explicit full-history operation, not a paging or background-read helper."""
    from amplifier_foundation.session.history import SessionHistoryStore
    history = SessionHistoryStore(directory, session_id=session_id)
    if not versioned(directory):
        return history.load(include_events=False)
    pool = SourcePool()
    try:
        with pool.source(directory, {'project': str(Path(directory).parent), 'nativeId': session_id}) as source:
            prefix = source.commit
            messages = []
            for index in range(prefix.message_count):
                bounds = source.message_range(index)
                # Explicit resume/export materializes the complete context,
                # including its largest row; ordinary windows never use this.
                messages.append(json.loads(b''.join(source.iter_bytes(bounds.start, bounds.end))))
            metadata = history.load_metadata()
            source.read_range(0, 0)
    finally:
        pool.close()
    return SimpleNamespace(messages=messages, metadata=metadata,
                           diagnostics=list(history.diagnostics), commit=prefix)


class SourcePool:
    """At most eight checked sources; no decoded history retained.

    Cold open verifies O(history) once and builds Foundation's anonymous offset
    index. Warm pages only check source identity and read their bounded rows.
    Eviction closes the index. Any failed check discards that source; a cursor
    never authorizes advancing it to another commit.
    """

    def __init__(self):
        self._sources = OrderedDict()
        self._condition = threading.Condition()
        self._closed = False

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.wait_for(lambda: all(entry['users'] == 0 for entry in self._sources.values()))
            while self._sources:
                _, entry = self._sources.popitem()
                if entry['context'] is not None:
                    entry['context'].__exit__(None, None, None)

    @contextmanager
    def source(self, directory, owner):
        root = Path(directory).absolute()
        key = (str(root), owner['project'], owner['nativeId'])
        with self._condition:
            if self._closed:
                raise ValueError('Committed sources are closed.')
            entry = self._sources.get(key)
            if entry is None:
                if len(self._sources) >= 8:
                    retired = next((key for key, value in self._sources.items() if value['users'] == 0), None)
                    if retired is None:
                        raise ValueError('Committed source readers are busy.')
                    old = self._sources.pop(retired)
                    if old['context'] is not None:
                        old['context'].__exit__(None, None, None)
                entry = {'lock': threading.RLock(), 'context': None, 'source': None, 'users': 0}
                self._sources[key] = entry
            entry['users'] += 1
            self._sources.move_to_end(key)
        try:
            with entry['lock']:
                if entry['source'] is not None:
                    try:
                        entry['source'].read_range(0, 0)
                    except (ValueError, OSError):
                        entry['context'].__exit__(None, None, None)
                        entry.update(context=None, source=None)
                if entry['source'] is None:
                    try:
                        from amplifier_foundation.session.transcript_source import open_committed_source
                    except ImportError:
                        raise ValueError('This runtime cannot read bounded committed transcript ranges.') from None
                    context = open_committed_source(root)
                    source = context.__enter__()
                    entry.update(context=context, source=source)
                    if source.commit.session_id != owner['nativeId']:
                        context.__exit__(None, None, None)
                        entry.update(context=None, source=None)
                        raise ValueError('The committed source belongs to a different session.')
                try:
                    yield entry['source']
                except BaseException:
                    entry['context'].__exit__(None, None, None)
                    entry.update(context=None, source=None)
                    raise
        finally:
            with self._condition:
                entry['users'] -= 1
                self._condition.notify_all()


_background_sources = SourcePool()
atexit.register(_background_sources.close)


def background_sources():
    """Process-owned, eight-source LRU for repeated small read-only requests."""
    return _background_sources


def commit_identity(source, owner):
    value = asdict(source.commit)
    revision = hashlib.sha256(json.dumps([owner, value], sort_keys=True,
                                        separators=(',', ':')).encode()).hexdigest()
    return value, revision


def opening(source, *, max_bytes, max_rows):
    """Bounded opening records; an oversized record stops this sample."""
    rows, remaining = [], max_bytes
    for index in range(min(source.commit.message_count, max_rows)):
        bounds = source.message_range(index)
        size = bounds.end - bounds.start
        if size > remaining:
            break
        rows.append((bounds.start, source.window(index, 1, max_bytes=max_bytes).messages[0]))
        remaining -= size
    source.read_range(0, 0)
    return rows


@contextmanager
def guard(directory, session_id):
    """A checked source lifetime for explicit snapshot/fork/asset transfers.

    Conflict detection only, never exclusion of external writers. The caller
    must check again before publishing its dependent result.
    """
    if not versioned(directory):
        from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
        yield NativeTranscriptGuard(Path(directory) / 'transcript.jsonl')
        return
    pool = SourcePool()
    owner = {'project': str(Path(directory).parent), 'nativeId': session_id}
    try:
        with pool.source(directory, owner) as source:
            yield SimpleNamespace(check=lambda: source.read_range(0, 0), source=source)
    finally:
        pool.close()


class OwnedTranscript:
    """Synchronous Foundation writer seam; the caller owns custody/lifetime."""

    def __init__(self, directory, session_id, *, fence):
        if not callable(fence):
            raise TypeError('A source-lifetime writer exclusion fence is required.')
        from amplifier_foundation.session.transcript_writer import TranscriptWriter
        self.directory = Path(directory).absolute()
        self.session_id = session_id
        self.writer = TranscriptWriter(self.directory, fence=fence)

    def _expected(self, expected):
        if not versioned(self.directory) or expected.session_id != self.session_id:
            raise ValueError('An exact committed witness for this session is required.')

    def append(self, settled_suffix, *, expected, operation_id, admit):
        self._expected(expected)
        return self.writer.append(settled_suffix, expected=expected, operation_id=operation_id, admit=admit)

    def rewrite(self, messages, *, expected, operation_id, admit):
        self._expected(expected)
        return self.writer.rewrite(messages, expected=expected, operation_id=operation_id, admit=admit)

    def reconcile(self, *, operation_id, expected, suffix_sha256, kind):
        self._expected(expected)
        return self.writer.reconcile(operation_id=operation_id, expected=expected,
                                     suffix_sha256=suffix_sha256, kind=kind)