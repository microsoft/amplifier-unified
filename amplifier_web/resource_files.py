"""Immutable, content-addressed artifact files; SQLite contains only an index."""
import hashlib
import json
from pathlib import Path


def root(db):
    path = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
    return Path(path).parent / 'artifacts' if path else None


def put(db, value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    identity = hashlib.sha256(text.encode()).hexdigest()
    directory = root(db)
    if directory is None:  # In-memory fixtures have no filesystem lifecycle.
        indexed = text
    else:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / (identity + '.json')
        if not path.exists():
            from .host.storage import SessionStore
            SessionStore._atomic(path, text)
        indexed = json.dumps({'$blob': identity})
    db.execute('INSERT OR IGNORE INTO state_resources VALUES (?, ?)', (identity, indexed))
    return {'$resource': identity, 'bytes': len(text.encode())}


def resolve(db, identity, value):
    if isinstance(value, dict) and set(value) == {'$blob'}:
        if value['$blob'] != identity:
            raise ValueError('Invalid artifact index')
        return json.loads((root(db) / (identity + '.json')).read_text())
    return value


def references(value):
    if isinstance(value, dict):
        if isinstance(value.get('$resource'), str):
            yield value['$resource']
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def collect(db, state):
    """Mark all retained state/receipt references, including nested references."""
    from .state_storage import resource
    pending = list(references(state))
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='smart_tool_operations'").fetchone():
        for (text,) in db.execute('SELECT value FROM smart_tool_operations'):
            pending.extend(references(json.loads(text)))
    marked = set()
    while pending:
        identity = pending.pop()
        if identity in marked:
            continue
        marked.add(identity)
        if db.execute('SELECT 1 FROM state_resources WHERE id=?', (identity,)).fetchone():
            pending.extend(references(resource(db, identity)))
    stale = [row[0] for row in db.execute('SELECT id FROM state_resources') if row[0] not in marked]
    db.executemany('DELETE FROM state_resources WHERE id=?', ((identity,) for identity in stale))
    # Files are deleted only after the caller commits the pruned references.
    return stale


def remove_files(db, identities):
    directory = root(db)
    if directory:
        for identity in identities:
            (directory / (identity + '.json')).unlink(missing_ok=True)
