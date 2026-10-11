"""Immutable, content-addressed artifact files; SQLite contains only an index."""
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
import re
import stat
from pathlib import Path


def root(db):
    path = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
    return Path(path).parent / 'artifacts' if path else None


@dataclass(frozen=True)
class Prepared:
    text: str
    identity: str
    size: int


def prepare(value):
    """Pure canonical preparation, safe to run without a database in a worker."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    encoded = text.encode()
    return Prepared(text, hashlib.sha256(encoded).hexdigest(), len(encoded))


def write_prepared(directory, prepared):
    """Existing file protocol; caller must retain SQLite writer exclusion."""
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (prepared.identity + '.json')
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != prepared.size:
            raise ValueError('Existing artifact file conflicts with its content identity; file preserved.')
    else:
        from .host.storage import SessionStore
        SessionStore._atomic(path, prepared.text)


def _stage(db, prepared):
    directory = root(db)
    indexed = prepared.text if directory is None else json.dumps({'$blob': prepared.identity})
    owns_transaction = not db.in_transaction
    if owns_transaction:
        db.execute('BEGIN IMMEDIATE')
    db.execute('SAVEPOINT resource_put')
    return directory, indexed, owns_transaction


def _rollback_put(db, owns_transaction):
    db.execute('ROLLBACK TO resource_put')
    db.execute('RELEASE resource_put')
    if owns_transaction:
        db.rollback()


def put(db, value):
    prepared = prepare(value)
    directory, indexed, owns_transaction = _stage(db, prepared)
    try:
        db.execute('INSERT OR IGNORE INTO state_resources VALUES (?, ?)', (prepared.identity, indexed))
        write_prepared(directory, prepared)
        db.execute('RELEASE resource_put')
    except BaseException:
        _rollback_put(db, owns_transaction)
        raise
    return {'$resource': prepared.identity, 'bytes': prepared.size}


async def settle_worker(function, *args):
    """Cancellation cannot release an owned write while its worker still runs."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            break
    if cancelled:
        # Retrieve any worker failure, but preserve the caller's cancellation.
        try:
            task.result()
        except BaseException:
            pass
        raise asyncio.CancelledError()
    return task.result()


async def put_prepared_async(db, prepared):
    """Owner-thread DB, detached file worker; caller holds its app lock.

    Like put(), successful return leaves commit to the caller. No DB or cursor
    crosses into the worker. Keep the caller lock until commit/rollback so GC on
    the same connection cannot enter; other connections use BEGIN IMMEDIATE.
    """
    directory, indexed, owns_transaction = _stage(db, prepared)
    try:
        db.execute('INSERT OR IGNORE INTO state_resources VALUES (?, ?)', (prepared.identity, indexed))
        await settle_worker(write_prepared, directory, prepared)
        db.execute('RELEASE resource_put')
    except BaseException:
        _rollback_put(db, owns_transaction)
        raise
    return {'$resource': prepared.identity, 'bytes': prepared.size}


async def put_async(db, value):
    prepared = await settle_worker(prepare, value)
    return await put_prepared_async(db, prepared)


@dataclass(frozen=True)
class Detached:
    directory: Path | None
    identity: str
    indexed: str


def detach(db, identity):
    """Capture an indexed read on the database owner thread, without file I/O."""
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
        raise ValueError('Invalid state resource.')
    row = db.execute('SELECT value FROM state_resources WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise ValueError('State resource is unavailable.')
    return Detached(root(db), identity, row[0])


def read_detached(source, *, max_bytes=None):
    """File, JSON and identity work without a live connection or cursor."""
    text = source.indexed
    if max_bytes is not None and len(text.encode()) > max_bytes:
        raise ValueError('Oversized artifact index.')
    value = json.loads(text)
    if isinstance(value, dict) and set(value) == {'$blob'}:
        if value['$blob'] != source.identity or source.directory is None:
            raise ValueError('Invalid artifact index')
        path = source.directory / (source.identity + '.json')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or (max_bytes is not None and before.st_size > max_bytes):
                raise ValueError('Invalid or oversized artifact file.')
            encoded = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if len(encoded) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
                raise ValueError('Artifact changed while reading.')
        if hashlib.sha256(encoded).hexdigest() != source.identity:
            raise ValueError('Artifact content conflicts with its identity.')
        value = json.loads(encoded)
    elif hashlib.sha256(text.encode()).hexdigest() != source.identity:
        raise ValueError('Artifact content conflicts with its identity.')
    return value


def resolve(db, identity, value):
    if isinstance(value, dict) and set(value) == {'$blob'}:
        if value['$blob'] != identity:
            raise ValueError('Invalid artifact index')
        return json.loads((root(db) / (identity + '.json')).read_text())
    return value


def references(value):
    if isinstance(value, Mapping):
        from .cold_display import ColdRecord
        if isinstance(value, ColdRecord):
            # Its manifest is a durable root; do not load payloads during GC.
            yield from references(dict(dict.items(value)))
            return
        if isinstance(value.get('$resource'), str):
            yield value['$resource']
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def retained_references(db, state):
    """References from complete state and durable client/operation records."""
    pending = list(references(state))
    # A hydrated mutable field can drop its in-memory cold reference before its
    # next save. The last committed global manifest still owns that exact blob.
    from .state_records import load
    saved = load(db) if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'").fetchone() else None
    if saved:
        pending.extend(references(saved))
    # Persisted client records remain roots even before ClientViews is loaded
    # at startup, and when their browser is disconnected or another is bound.
    for table in ('smart_tool_operations', 'client_views', 'conversation_shares', 'output_records'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            for (text,) in db.execute('SELECT value FROM ' + table):
                pending.extend(references(json.loads(text)))
    return pending


def restore(db, state, identity, value):
    """Restore an exact missing, directly retained resource without replay."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if hashlib.sha256(encoded.encode()).hexdigest() != identity:
        raise ValueError('Recovery content does not match the saved resource identity.')
    if identity not in retained_references(db, state):
        raise ValueError('Recovery requires an existing saved reference to this resource.')
    from .state_storage import resource
    if db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
        try:
            existing = resource(db, identity)
        except FileNotFoundError:
            pass
        else:
            if existing != value:
                raise ValueError('Existing resource content conflicts with recovery content.')
            return {'id': identity, 'restored': False}
    directory = root(db)
    path = directory / (identity + '.json') if directory else None
    if path and path.exists() and path.read_text() != encoded:
        raise ValueError('Existing resource file conflicts with recovery content.')
    put(db, value)
    return {'id': identity, 'restored': True}


def marked_references(db, state):
    """Return retained graph, or None when any reachability is uncertain."""
    states = state if isinstance(state, list) else [state]
    if any(isinstance(row, dict) and row.get('_viewRecoveryPending') for row in states):
        return None  # a failed presentation rollback can retain unknown blob roots
    from .state_storage import resource
    pending = retained_references(db, state)
    marked = set()
    while pending:
        identity = pending.pop()
        if identity in marked:
            continue
        marked.add(identity)
        if not db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
            return None  # Its unavailable body may retain other sources.
        else:
            try:
                pending.extend(references(resource(db, identity)))
            except (OSError, ValueError, TypeError, KeyError):
                # Unknown nested references cannot be proven unreferenced. Keep
                # all sources until the damaged retained artifact is recovered.
                return None
    return marked


def collect(db, state):
    """Mark all retained state/receipt references, including nested references."""
    owns_transaction = not db.in_transaction
    if owns_transaction:
        db.execute('BEGIN IMMEDIATE')
    marked = marked_references(db, state)
    if marked is None:
        if owns_transaction:
            db.rollback()
        return []
    return prune_marked(db, marked)


def prune_marked(db, marked):
    """Prune using a mark computed under the same caller-owned writer lock."""
    if not db.in_transaction:
        raise ValueError('Resource pruning requires writer exclusion.')
    stale = [row[0] for row in db.execute('SELECT id FROM state_resources') if row[0] not in marked]
    db.executemany('DELETE FROM state_resources WHERE id=?', ((identity,) for identity in stale))
    # Files are deleted only after the caller commits the pruned references.
    return stale


def remove_files(db, identities):
    if db.in_transaction:
        raise ValueError('Commit index pruning before removing resource files.')
    directory = root(db)
    if directory:
        db.execute('BEGIN IMMEDIATE')
        try:
            for identity in identities:
                if not re.fullmatch('[a-f0-9]{64}', identity):
                    raise ValueError('Invalid resource identity.')
                # Another writer may have re-adopted this hash since collection.
                if not db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
                    (directory / (identity + '.json')).unlink(missing_ok=True)
        finally:
            db.rollback()  # only a lock; no data changes in this transaction


def sweep_unindexed(db, state, iterator=None, *, limit=128):
    """Bound file inspection; exclude writers and refuse uncertain reachability.

    Return a live scandir cursor and counts. Callers close the cursor on shutdown.
    Only hash-named regular files and our atomic writer's temporary files qualify.
    """
    if db.in_transaction:
        raise ValueError('Orphan reconciliation requires a committed boundary.')
    db.execute('BEGIN IMMEDIATE')
    try:
        if marked_references(db, state) is None:
            if iterator is not None:
                iterator.close()
            return None, {'blocked': True, 'complete': False, 'inspected': 0, 'removed': 0}
        return sweep_unindexed_locked(db, iterator, limit=limit)
    finally:
        db.rollback()


def sweep_unindexed_locked(db, iterator=None, *, limit=128):
    """Caller holds writer lock and has established a complete retained graph."""
    if not db.in_transaction:
        raise ValueError('Resource sweep requires writer exclusion.')
    try:
        directory = root(db)
        if directory is None or not directory.exists():
            return None, {'blocked': False, 'complete': True, 'inspected': 0, 'removed': 0}
        iterator = iterator or os.scandir(directory)
        inspected = removed = 0
        for _ in range(limit):
            entry = next(iterator, None)
            if entry is None:
                iterator.close()
                return None, {'blocked': False, 'complete': True, 'inspected': inspected, 'removed': removed}
            inspected += 1
            if not entry.is_file(follow_symlinks=False):
                continue
            identity = entry.name[:-5] if re.fullmatch('[a-f0-9]{64}\\.json', entry.name) else None
            temporary = entry.name.startswith('.checkpoint-')
            if identity and db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
                continue
            if identity or temporary:
                Path(entry.path).unlink(missing_ok=True)
                removed += 1
        return iterator, {'blocked': False, 'complete': False, 'inspected': inspected, 'removed': removed}
    except BaseException:
        if iterator is not None:
            iterator.close()
        raise
