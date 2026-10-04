#!/usr/bin/env python3
"""Fail closed when an independently packaged component has no exact receipt."""
from pathlib import Path
import hashlib
import json
import tarfile

PROTOCOL = '@amplifier/unified-protocol-extensions'
DATA_EXPORTS = {'./schemas/*': './schemas/*', './fixtures/*': './fixtures/*'}
MAX_PROTOCOL_MEMBERS = 1024
MAX_PROTOCOL_MEMBER_BYTES = 1024 * 1024
MAX_PROTOCOL_BYTES = 8 * 1024 * 1024


def entrypoints(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from entrypoints(item)
    elif isinstance(value, list):
        for item in value:
            yield from entrypoints(item)


def has_wildcard(value):
    if isinstance(value, str):
        return '*' in value
    if isinstance(value, dict):
        return any('*' in key or has_wildcard(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_wildcard(item) for item in value)
    return False


def verify_archive(path, name, version):
    with tarfile.open(path) as archive:
        members = {}
        total = 0
        for member in archive:
            if name == PROTOCOL:
                # npm data-directory exports may only cover bounded regular
                # files. Never extract, follow links, or normalize unsafe names.
                parts = member.name.split('/')
                if (not member.isfile() or len(member.name) > 1024
                        or parts[0] != 'package' or len(parts) < 2
                        or any(part in ('', '.', '..') for part in parts)
                        or any(ord(char) < 32 or ord(char) == 127 for char in member.name)
                        or '\\' in member.name or '*' in member.name
                        or member.name in members):
                    raise ValueError('Unsafe protocol archive member: ' + member.name)
                total += member.size
                if (len(members) >= MAX_PROTOCOL_MEMBERS
                        or member.size < 0 or member.size > MAX_PROTOCOL_MEMBER_BYTES
                        or total > MAX_PROTOCOL_BYTES):
                    raise ValueError('Protocol data archive exceeds qualification bounds')
            members[member.name] = member
        manifest_member = members.get('package/package.json')
        if manifest_member is None or not manifest_member.isfile():
            raise ValueError('Packaged component manifest missing or nonregular')
        manifest = json.load(archive.extractfile(manifest_member))
        if (manifest['name'], manifest['version']) != (name, version):
            raise ValueError('Artifact identity changed')
        exact = []
        for field in ('main', 'module', 'types', 'bin'):
            if field in manifest:
                if has_wildcard(manifest[field]):
                    raise ValueError('Wildcard entrypoint needs an explicit qualification rule: ' + name)
                exact.extend(entrypoints(manifest[field]))
        exports = manifest.get('exports', {})
        if isinstance(exports, dict):
            for key, value in exports.items():
                if '*' in key or has_wildcard(value):
                    if name != PROTOCOL or key not in DATA_EXPORTS or value != DATA_EXPORTS[key]:
                        raise ValueError('Wildcard entrypoint needs an explicit qualification rule: ' + name)
                    prefix = 'package/' + value[2:-1]
                    covered = [member for member in members.values() if member.name.startswith(prefix)]
                    if not covered or any(member.size == 0 for member in covered):
                        raise ValueError('Protocol data export has missing or empty targets: ' + key)
                else:
                    exact.extend(entrypoints(value))
        else:
            if has_wildcard(exports):
                raise ValueError('Wildcard entrypoint needs an explicit qualification rule: ' + name)
            exact.extend(entrypoints(exports))
        for entry in exact:
            target = 'package/' + entry.removeprefix('./')
            if target not in members:
                raise ValueError('Packaged entrypoint missing: ' + name + ' ' + entry)


def verify(root):
    package = json.loads((root / 'package.json').read_text())
    receipt = json.loads((root / 'components.json').read_text())
    seen = set()
    for name, source in package['dependencies'].items():
        if not source.startswith('file:'):
            continue
        path = root / source[5:]
        row = receipt['components'].get(name)
        if row is None or row['artifact'] != source[5:]:
            raise ValueError('Missing component receipt: ' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Artifact hash changed: ' + name)
        if len(row['revision']) != 40 or any(char not in '0123456789abcdef' for char in row['revision']):
            raise ValueError('Exact source revision required')
        verify_archive(path, name, row['version'])
        seen.add(name)
    if seen != set(receipt['components']):
        raise ValueError('Stale component receipt')
    return len(seen)


if __name__ == '__main__':
    print(f'{verify(Path(__file__).resolve().parent.parent)} independently packaged component identities and SHA-256 values verified')
