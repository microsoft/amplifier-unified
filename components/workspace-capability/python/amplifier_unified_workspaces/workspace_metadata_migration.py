"""Reviewed offline transfer of Main's workspace sidecar records.

Only captured metadata is copied. No starter, Git import, resource cleanup,
legacy command or folder allocation is executed by migration.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .workspace_starters import BUILTINS, FIELDS, definition, repository

MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 20001
HEX = re.compile(r'[a-f0-9]{64}')


def _json(raw):
    def invalid_number(_):
        raise ValueError('Invalid numeric metadata')
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise ValueError('Duplicate workspace metadata field')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_number)


def _read(path, digest):
    if not isinstance(digest, str) or not HEX.fullmatch(digest):
        raise ValueError('Reviewed workspace metadata digest required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
            raise ValueError('Workspace metadata must be a bounded regular file')
        raw = stream.read(MAX_FILE + 1)
        after = os.fstat(stream.fileno())
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if stamp(before) != stamp(after) or len(raw) > MAX_FILE or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Workspace metadata changed since review')
        return raw


def _inventory(root):
    """Require the manifest to cover every captured file in these three owners."""
    if root.resolve() != root:
        raise ValueError('Workspace metadata directory must be canonical')
    names = set()
    if os.path.lexists(root / 'workspace-starters.json'):
        names.add('workspace-starters.json')
    for name in ('workspace-setup', 'workspace-resources'):
        path = root / name
        if not os.path.lexists(path):
            continue
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError('Workspace metadata directory cannot be a symbolic link')
        for entry in path.iterdir():
            if entry.name.endswith('.json'):
                names.add(name + '/' + entry.name)
                if len(names) > MAX_FILES:
                    raise ValueError('Workspace metadata exceeds migration capacity')
    return names


def _revision(row):
    if not isinstance(row, dict) or type(row.get('revision')) is not int or row['revision'] < 0:
        raise ValueError('Invalid workspace metadata revision')


def _commands(row):
    commands = row.get('commands', {})
    if not isinstance(commands, dict) or len(commands) > 200:
        raise ValueError('Invalid retained workspace command receipts')
    for key, value in commands.items():
        if (not isinstance(key, str) or not key or len(key) > 1000 or not isinstance(value, dict)
                or not isinstance(value.get('fingerprint'), str) or not isinstance(value.get('result'), dict)):
            raise ValueError('Invalid retained workspace command receipt')


def _starter(row):
    if not isinstance(row, dict):
        raise ValueError('Invalid saved workspace starter')
    # Retain former bundle references as data. A future explicit create still
    # checks the current configured bundle catalog before admitting any work.
    definition({key: row[key] for key in FIELDS if key in row}, {row.get('bundle', '')})


def _validate(name, row):
    _revision(row)
    if name == 'workspace-starters.json':
        items = row.get('items')
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError('Invalid workspace starter library')
        ids = {item['id'] for item in BUILTINS}
        for item in items:
            _starter(item); _revision(item)
            key = item.get('id')
            if not isinstance(key, str) or not key or len(key) > 100 or key in ids or item.get('builtIn') is not False:
                raise ValueError('Invalid or duplicate custom starter identity')
            ids.add(key)
        _commands(row)
    elif name.startswith('workspace-resources/'):
        if not re.fullmatch(r'[a-f0-9]{32}|[a-f0-9]{64}', Path(name).stem):
            raise ValueError('Invalid workspace inventory identity')
        items = row.get('resources')
        if not isinstance(items, list) or len(items) > 10000:
            raise ValueError('Invalid workspace resource inventory')
        ids = set()
        for item in items:
            _revision(item)
            key = item.get('id')
            if not isinstance(key, str) or not key or key in ids or len(key) > 200:
                raise ValueError('Invalid or duplicate workspace resource identity')
            ids.add(key)
            if item.get('status') not in {'active', 'reaped', 'observed_absent'}:
                raise ValueError('Unknown workspace resource status')
            for field in ('kind', 'resourceId', 'owner', 'note', 'evidence'):
                if not isinstance(item.get(field, ''), str):
                    raise ValueError('Invalid workspace resource description')
        _commands(row)
    else:
        key = str(uuid.UUID(Path(name).stem))
        if Path(name).stem != key or row.get('id') != key:
            raise ValueError('Workspace setup identity mismatch')
        _starter(row.get('starter'))
        if not isinstance(row.get('path'), str) or not Path(row['path']).is_absolute():
            raise ValueError('Invalid workspace setup path')
        directory = row.get('directoryIdentity')
        if not isinstance(directory, list) or len(directory) != 2 or any(type(x) is not int or x < 0 for x in directory):
            raise ValueError('Invalid workspace setup directory identity')
        if row.get('status') not in {'pending', 'running', 'interrupted', 'ready', 'partial', 'failed'}:
            raise ValueError('Unknown workspace setup status')
        repos = row.get('repositories')
        if not isinstance(repos, list) or len(repos) > 20 or not isinstance(row.get('files'), list):
            raise ValueError('Invalid workspace setup steps')
        for item in repos:
            repository({key: item[key] for key in ('url', 'directory', 'ref') if key in item})
            if item.get('status') not in {'pending', 'running', 'unknown', 'ready', 'failed'}:
                raise ValueError('Unknown repository setup status')
        if 'rootGit' in row and (not isinstance(row['rootGit'], dict) or row['rootGit'].get('status') not in {'pending', 'running', 'unknown', 'ready', 'failed', 'preserved'}):
            raise ValueError('Unknown workspace Git setup status')


def transfer(owner, value, mapping, manifest_path, expected_sha256):
    """Populate the private staging owner only after validating the whole set."""
    manifest_path = Path(manifest_path).absolute()
    root = manifest_path.parent
    raw_manifest = _read(manifest_path, expected_sha256)
    manifest = _json(raw_manifest)
    if not isinstance(manifest, dict) or set(manifest) != {'version', 'files'} or type(manifest['version']) is not int or manifest['version'] != 1 or not isinstance(manifest['files'], dict):
        raise ValueError('Invalid workspace metadata manifest')
    names = _inventory(root)
    if names != set(manifest['files']) or len(names) > MAX_FILES:
        raise ValueError('Workspace metadata manifest must include every captured record')
    captured, parsed, total = {}, {}, 0
    for name, digest in manifest['files'].items():
        if name != 'workspace-starters.json' and not re.fullmatch(r'workspace-(?:setup|resources)/[a-f0-9-]+\.json', name):
            raise ValueError('Invalid workspace metadata path')
        raw = _read(root / name, digest)
        total += len(raw)
        if total > MAX_TOTAL:
            raise ValueError('Workspace metadata exceeds migration capacity')
        record = _json(raw); _validate(name, record)
        captured[name], parsed[name] = raw, record
    if names != _inventory(root):
        raise ValueError('Workspace metadata changed during capture')
    links, inventories, used = [], [], set()
    for row in value['workspaces']:
        key = row.get('setupId')
        if key is not None:
            if not isinstance(key, str):
                raise ValueError('Invalid workspace setup reference')
            name = 'workspace-setup/' + key + '.json'
            setup = parsed.get(name)
            if not setup or setup['path'] != row['path'] or key in used:
                raise ValueError('Missing, ambiguous or mismatched workspace setup receipt')
            used.add(key); links.append((row['path'], key, name))
        # Main keys inventories by UUID hex; newer owner captures use a path
        # identity digest. Accept either exact spelling, never merge both.
        candidates = ['workspace-resources/' + hashlib.sha256(row['id'].encode()).hexdigest() + '.json']
        try:candidates.append('workspace-resources/' + uuid.UUID(row['id']).hex + '.json')
        except ValueError:pass
        found = [name for name in candidates if name in captured]
        if len(found) > 1:
            raise ValueError('Ambiguous workspace resource inventory identity')
        if found:
            inventories.append((found[0], 'workspace-resources/' + hashlib.sha256(mapping[row['id']].encode()).hexdigest() + '.json'))
    def write(name, raw):
        path = owner.directory / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(raw); path.chmod(0o600)
    write('legacy-workspace-metadata/manifest.json', raw_manifest)
    for name, raw in captured.items():
        write('legacy-workspace-metadata/' + name, raw)
    if 'workspace-starters.json' in captured:
        write('workspace-starters.json', captured['workspace-starters.json'])
    for old, new in inventories:
        write(new, captured[old])
    for path, key, name in links:
        write(name, captured[name])
        with owner.db() as db:
            db.execute('INSERT INTO workspace_setup_links VALUES(?,?)', (path, key))
            db.execute("INSERT OR REPLACE INTO meta VALUES('workspaceSetupLinks','1')")
    return dict(sourceSha256=expected_sha256, retainedFiles=len(captured), customStarters=len(parsed.get('workspace-starters.json', {}).get('items', [])),
                setupReceipts=len(links), resourceInventories=len(inventories), originalBytesRetained=True, workReplayed=False)
