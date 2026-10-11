"""Import workspace names and ordered pins from a reviewed, closed main snapshot.

This offline adapter creates a new owner. It does not activate it, change the
source, create folders, replay commands, or read canonical conversation history.
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
import stat
import tempfile

from filelock import FileLock
from .owner import Owner, identity, text
from . import workspace_navigation as navigation

MAX_SNAPSHOT = 256 * 1024 * 1024
MAX_METADATA = 16 * 1024 * 1024
KEYS = ('workspaces', 'pinnedWorkspaceIds')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def fields(db):
    """Read the latest globals, including main's scoped state-record overlays."""
    if db.execute("SELECT type FROM sqlite_master WHERE name='state'").fetchone() != ('table',):
        raise ValueError('Legacy application state table is missing')
    if db.execute('SELECT count(*) FROM state').fetchone()[0] != 1:
        raise ValueError('Expected one captured application state')
    base = db.execute('SELECT id,json_valid(value) FROM state').fetchone()
    if base != (1, 1):
        raise ValueError('Invalid captured application state')
    result, overlaid = {}, set()
    overlay = db.execute("SELECT type FROM sqlite_master WHERE name='state_records'").fetchone()
    if overlay is not None:
        if overlay != ('table',):
            raise ValueError('Legacy state records must be a table')
        for key in KEYS:
            rows = db.execute("SELECT substr(value,1,?),length(CAST(value AS BLOB)) FROM state_records WHERE kind='global' AND id=? LIMIT 2",
                              (MAX_METADATA+1, key)).fetchall()
            if len(rows) > 1:
                raise ValueError('Duplicate workspace state record')
            if not rows:
                continue
            overlaid.add(key)
            raw, size = rows[0]
            if not isinstance(raw, str) or size > MAX_METADATA:
                raise ValueError('Oversized workspace state record')
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {'present','value'}
                    or type(value['present']) is not bool):
                raise ValueError('Invalid workspace state record')
            if value['present']:
                result[key] = value['value']
    for key in KEYS:
        if key in overlaid:
            continue
        # The latest overlay wins even if an obsolete base value is malformed.
        # Do not materialize old monolithic message bodies in Python.
        kind, size = db.execute('SELECT json_type(value,?),length(CAST(json_extract(value,?) AS BLOB)) FROM state WHERE id=1',
                               ('$.'+key, '$.'+key)).fetchone()
        if kind is not None:
            if kind != 'array' or size > MAX_METADATA:
                raise ValueError('Invalid or oversized workspace metadata')
            result[key] = json.loads(db.execute('SELECT json_extract(value,?) FROM state WHERE id=1', ('$.'+key,)).fetchone()[0])
    if 'workspaces' not in result:
        raise ValueError('Captured workspace registrations are unavailable')
    return result


def selection(value, owner):
    rows = value['workspaces']
    if not isinstance(rows, list) or len(rows) > navigation.MAX_PINS:
        raise ValueError('Workspace registry exceeds migration capacity')
    mapping, selected, paths = {}, [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid workspace registration')
        old = text(row.get('id'), 'original workspace ID', 200)
        if old in mapping:
            raise ValueError('Duplicate original workspace identity')
        # Missing folders are retained. Unresolved identities and symlink/path
        # aliases require separate review; never guess or merge their targets.
        raw = text(row.get('path'), 'original workspace path')
        path = owner.authorize(raw, existing=False)
        if str(path) != raw:
            raise ValueError('Original workspace path is not canonical; resolve its identity before migration')
        if raw in paths:
            raise ValueError('Multiple workspace aliases share a path; resolve before migration')
        paths.add(raw)
        name = text(row.get('name'), 'workspace name', 200)
        if any(ord(char) < 32 for char in name):
            raise ValueError('Invalid workspace name')
        mapping[old] = identity(path)
        selected.append((path, name))
    raw_pins = value.get('pinnedWorkspaceIds', [])
    if (not isinstance(raw_pins, list) or len(raw_pins) > navigation.MAX_PINS
            or any(not isinstance(item, str) or len(item) > 200 for item in raw_pins)):
        raise ValueError('Invalid workspace pin preferences')
    # Main resolves pins by registration membership and first occurrence. Keep
    # its behavior, while retaining the unmodified raw source for inspection.
    pins = list(dict.fromkeys(mapping[item] for item in raw_pins if item in mapping))
    return selected, mapping, pins, len(raw_pins)-len(pins)


async def forbidden(*args, **kwargs):
    raise RuntimeError('Offline migration cannot access the catalog or execute work')


async def import_workspaces(source, destination, *, expected_sha256, config):
    """Publish one fully validated inactive owner; never merge into an owner."""
    if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
        raise ValueError('Reviewed snapshot digest required')
    if not isinstance(config, dict) or set(config) != {'allowedRoots','defaultRoot'}:
        raise ValueError('Explicit owner roots required; state location is the new destination')
    source, destination = Path(source), Path(destination).absolute()
    if os.path.lexists(destination):
        raise ValueError('Destination already exists; nothing overwritten or repeated')
    if destination.parent.resolve() != destination.parent:
        raise ValueError('Destination parent must be canonical')
    if any(os.path.lexists(str(source)+suffix) for suffix in ('-wal','-shm','-journal')):
        raise ValueError('Use a closed captured snapshot without SQLite companions')
    stage = Path(tempfile.mkdtemp(prefix='.workspace-migration-', dir=destination.parent))
    owner, reader = None, None
    try:
        original = stage/'legacy-app.sqlite3'
        # Pin the original inode while copying. A path swap or live write can
        # never be mistaken for the snapshot whose bytes were reviewed.
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as incoming, original.open('xb') as outgoing:
            info = os.fstat(incoming.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SNAPSHOT:
                raise ValueError('Source must be a bounded regular SQLite snapshot')
            digest, size = hashlib.sha256(), 0
            while block := incoming.read(1024*1024):
                size += len(block)
                if size > MAX_SNAPSHOT:
                    raise ValueError('Captured database exceeds migration capacity')
                digest.update(block); outgoing.write(block)
            current = os.fstat(incoming.fileno())
            stamp = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            if stamp(current) != stamp(info) or digest.hexdigest() != expected_sha256:
                raise ValueError('Snapshot changed since review')
        original.chmod(0o600)
        reader = sqlite3.connect(original.as_uri()+'?mode=ro&immutable=1', uri=True)
        reader.execute('PRAGMA trusted_schema=OFF')
        if reader.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Captured database failed integrity check')
        value = fields(reader)
        reader.close(); reader = None
        owner = Owner({**config, 'stateDirectory':str(stage)}, forbidden)
        selected, mapping, pins, filtered = selection(value, owner)
        # All paths and preferences have been validated before writing rows.
        for path, name in selected:
            owner.record(path, name, False)
        with owner.db() as db:
            navigation.write(db, navigation.saved(db), pins)
        await owner.close(); owner = None
        receipt = dict(version=1, scope='workspace-registration-names-and-pins',
                       sourceSha256=expected_sha256, registrations=len(selected), pins=len(pins),
                       ignoredStaleOrDuplicatePins=filtered, workReplayed=False,
                       workspaceFoldersCreated=False, activationProvided=False,
                       omissions=['Workspace setup/resource ledgers and custom starters require their own migration',
                                  'Client selection and drafts remain client-owned',
                                  'Legacy command receipts remain in the original snapshot; they are not replayable commands'])
        for name, body in [('identity-mapping.json',mapping),('migration.json',receipt)]:
            (stage/name).write_text(encoded(body)+'\n'); (stage/name).chmod(0o600)
        with FileLock(str(destination)+'.migration.lock', timeout=0):
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
    parser.add_argument('--config', required=True, help='JSON with explicit allowedRoots and defaultRoot')
    args = parser.parse_args()
    print(encoded(asyncio.run(import_workspaces(args.source, args.destination,
          expected_sha256=args.sha256, config=json.loads(Path(args.config).read_text())))))


if __name__ == '__main__':
    main()
