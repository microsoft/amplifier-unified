"""Offline migration of explicit legacy memories into a new, inactive owner.

Input is a reviewed, closed SQLite snapshot, not a running legacy installation.
The original snapshot and its digest remain beside the converted owner. This
adapter does not migrate automatic consolidation or create execution authority.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from .owner import Owner

MAX_SNAPSHOT = 256 * 1024 * 1024
MAX_ROWS = 100_000
MAX_RECORD = 2_000_000
AUTOMATIC_TABLES = ('memory_attempts', 'memory_suppression', 'memory_automation')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def mappings(value):
    if not isinstance(value, dict) or set(value) != {'sessions', 'workspaces'}:
        raise ValueError('Explicit session and workspace mappings required')
    result = json.loads(encode(value))
    for kind, rows in result.items():
        if not isinstance(rows, dict) or len(rows) > MAX_ROWS:
            raise ValueError('Invalid mapping capacity')
        for old, new in rows.items():
            if not isinstance(old, str) or len(old) > 8192 or not isinstance(new, str):
                raise ValueError('Invalid identity mapping')
            if any(ord(c) < 32 for c in old + new):
                raise ValueError('Invalid identity mapping')
            if kind == 'sessions':
                if not old or not re.fullmatch(r'ahp-session:/[A-Za-z0-9_-]{1,200}', new):
                    raise ValueError('Exact AHP session mapping required')
            elif new and (not os.path.isabs(new) or os.path.normpath(new) != new or new == '/'):
                raise ValueError('Exact workspace history path mapping required')
        if len(set(rows.values())) != len(rows):
            raise ValueError('Many-to-one mappings would merge distinct memory scopes')
    return result


def mapped(value, kind, mapping):
    if not isinstance(value, str) or value not in mapping[kind]:
        raise ValueError('Unmapped ' + kind + ' identity; source preserved')
    return mapping[kind][value]


def record(raw):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_RECORD:
        raise ValueError('Invalid or oversized memory record')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Expected a memory object')
    return value


def note(raw, identity, revision, mapping):
    value = record(raw)
    if (not isinstance(identity, str) or not identity or len(identity) > 200
            or value.get('id') != identity or type(value.get('revision')) is not int or value.get('revision') != revision
            or type(revision) is not int or revision < 1
            or not isinstance(value.get('text'), str) or not value['text'].strip()):
        raise ValueError('Memory identity, revision or text is invalid')
    # Rebinding a quotation requires a separately verified message locator. A
    # scope mapping alone must never turn old evidence into current authority.
    provenance = value.get('provenance')
    if not isinstance(provenance, dict):
        raise ValueError('Memory provenance is unavailable')
    if (value.get('source') or any(value.get(k) for k in ('automationKey', 'automationSourceKey', 'supersedes', 'supersededBy'))
            or provenance.get('origin') == 'consolidation'):
        raise ValueError('Referenced or automatic memories require source-evidence migration')
    scope, target = value.get('scope'), value.get('target')
    if scope == 'global':
        if target != '':
            raise ValueError('Invalid global memory scope')
    elif scope in {'task', 'workspace'}:
        value['target'] = mapped(target, 'sessions' if scope == 'task' else 'workspaces', mapping)
    else:
        raise ValueError('Unknown memory scope')
    # Retain historical provenance verbatim. It describes the original write;
    # it is neither a new human message nor permission in the receiving chat.
    return value


def rows(db, table, columns):
    present = db.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
    if not present:
        return []
    if present[0] != 'table':
        raise ValueError('Memory authority must be a table')
    if db.execute('SELECT count(*) FROM ' + table).fetchone()[0] > MAX_ROWS:
        raise ValueError('Memory table exceeds migration capacity')
    # Reject large bodies in SQLite before materializing them in Python.
    for column in columns:
        if db.execute('SELECT 1 FROM ' + table + ' WHERE length(CAST(' + column + ' AS BLOB))>? LIMIT 1', (MAX_RECORD,)).fetchone():
            raise ValueError('Memory table contains an oversized record')
    return db.execute('SELECT ' + ','.join(columns) + ' FROM ' + table).fetchall()


async def import_explicit_memories(source, destination, *, expected_sha256, mapping):
    """Publish only a fully validated new directory; never overwrite or merge."""
    mapping = mappings(mapping)
    if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
        raise ValueError('Reviewed snapshot digest required')
    source, destination = Path(source), Path(destination).absolute()
    if source.is_symlink() or not source.is_file():
        raise ValueError('Source must be a regular captured database')
    source = source.resolve(strict=True)
    if os.path.lexists(destination):
        raise ValueError('Destination already exists; nothing overwritten or repeated')
    if destination.parent.resolve() != destination.parent:
        raise ValueError('Destination parent must be canonical')
    if source.stat().st_size > MAX_SNAPSHOT:
        raise ValueError('Captured database exceeds migration capacity')
    if any(os.path.lexists(str(source) + suffix) for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Use a closed captured snapshot without SQLite companions')
    if sha256(source) != expected_sha256:
        raise ValueError('Snapshot changed since review')
    stage = Path(tempfile.mkdtemp(prefix='.memory-migration-', dir=destination.parent))
    owner = None
    reader = None
    try:
        original = stage / 'legacy-recall.sqlite3'
        shutil.copyfile(source, original)
        original.chmod(0o600)
        if sha256(original) != expected_sha256:
            raise ValueError('Snapshot changed during capture')
        reader = sqlite3.connect(original.as_uri() + '?mode=ro&immutable=1', uri=True)
        if reader.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Captured database failed integrity check')
        for table in ('memories', 'memory_versions', 'memory_receipts'):
            if reader.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone() != ('table',):
                raise ValueError('Legacy memory authority schema is incomplete')
        for table in AUTOMATIC_TABLES:
            if reader.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() and reader.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone():
                raise ValueError('Automatic memory history requires consolidation migration')

        notes = {}
        for identity, scope, target, revision, raw in rows(reader, 'memories', ['id', 'scope', 'target', 'revision', 'value']):
            value = note(raw, identity, revision, mapping)
            if record(raw).get('scope') != scope or record(raw).get('target') != target or identity in notes:
                raise ValueError('Memory scope columns disagree with saved value')
            notes[identity] = value
        versions = []
        current_versions = set()
        for identity, revision, raw in rows(reader, 'memory_versions', ['id', 'revision', 'value']):
            value = note(raw, identity, revision, mapping)
            current = notes.get(identity)
            if not current or revision > current['revision'] or (value['scope'], value['target']) != (current['scope'], current['target']):
                raise ValueError('Memory revision history disagrees with current note')
            if revision == current['revision'] and value != current:
                raise ValueError('Current memory revision differs from saved history')
            if revision == current['revision']:
                current_versions.add(identity)
            versions.append((identity, revision, encode(value)))
        if set(notes) != current_versions:
            raise ValueError('Current memory revision is missing from history')
        settings = []
        for workspace, raw in rows(reader, 'memory_settings', ['workspace', 'value']):
            value = record(raw)
            target = mapped(workspace, 'workspaces', mapping)
            if value.get('workspace', workspace) != workspace or type(value.get('revision')) is not int or value['revision'] < 0:
                raise ValueError('Invalid memory settings revision or scope')
            if any(type(value.get(key)) is not bool for key in ('contribute', 'use')):
                raise ValueError('Explicit memory consent values required')
            excluded = value.get('excludedSessions', [])
            if not isinstance(excluded, list) or len(excluded) > 500:
                raise ValueError('Invalid excluded sessions')
            value['excludedSessions'] = [mapped(sid, 'sessions', mapping) for sid in excluded]
            if type(value.get('maxCallsPerDay')) is not int or not 1 <= value['maxCallsPerDay'] <= 10:
                raise ValueError('Invalid memory call limit')
            value['workspace'] = target
            settings.append((target, encode(value)))
        receipts = rows(reader, 'memory_receipts', ['id', 'fingerprint', 'result'])
        # Preserve command identities and their original fingerprints exactly.
        # A changed retry remains a conflict rather than creating another note.
        for identity, fingerprint, raw in receipts:
            if not isinstance(identity, str) or not isinstance(fingerprint, str):
                raise ValueError('Invalid retained memory receipt')
            record(raw)

        async def forbidden(*args, **kwargs):
            raise AssertionError('Migration cannot invoke a host, provider or remote service')
        owner = Owner({'dataDir': str(stage)}, forbidden, forbidden)
        with owner.store.atomic():
            owner.store.db.executemany('INSERT INTO memories VALUES(?,?,?,?,?)', [(i, v['scope'], v['target'], v['revision'], encode(v)) for i, v in notes.items()])
            owner.store.db.executemany('INSERT INTO memory_versions VALUES(?,?,?)', versions)
            owner.store.db.executemany('INSERT INTO memory_settings VALUES(?,?)', settings)
            owner.store.db.executemany('INSERT INTO memory_receipts VALUES(?,?,?)', receipts)
        await owner.close()
        owner = None
        reader.close()
        reader = None
        receipt = {'schema': 'amplifier-explicit-memory-migration-v1', 'sourceSha256': expected_sha256,
                   'mappingSha256': hashlib.sha256(encode(mapping).encode()).hexdigest(),
                   'counts': {'memories': len(notes), 'versions': len(versions), 'settings': len(settings), 'receipts': len(receipts)},
                   'sourceRetained': True, 'executionStarted': False, 'activated': False,
                   'scope': 'explicit-memories-only', 'automaticMemoryMigrated': False}
        (stage / 'migration.json').write_text(encode(receipt) + '\n')
        (stage / 'migration.json').chmod(0o600)
        # Serialize independent invocations targeting the same new owner. The
        # lock is outside the staged directory and never grants running access.
        from filelock import FileLock
        with FileLock(str(destination) + '.migration.lock', timeout=0):
            if os.path.lexists(destination):
                raise ValueError('Destination appeared during migration; nothing overwritten')
            os.rename(stage, destination)
        return receipt
    finally:
        if owner:
            await owner.close()
        if reader:
            reader.close()
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--mapping', required=True, help='Reviewed JSON with sessions and workspaces maps')
    args = parser.parse_args()
    print(encode(asyncio.run(import_explicit_memories(args.source, args.destination,
        expected_sha256=args.sha256, mapping=json.loads(Path(args.mapping).read_text())))))


if __name__ == '__main__':
    main()
