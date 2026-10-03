#!/usr/bin/env python3
"""Fail closed when an independently packaged component has no exact receipt."""
from pathlib import Path
import hashlib,json,tarfile
root=Path(__file__).resolve().parent.parent
package=json.loads((root/'package.json').read_text());receipt=json.loads((root/'components.json').read_text())
seen=set()
for name,source in package['dependencies'].items():
    if not source.startswith('file:'):continue
    path=root/source[5:];row=receipt['components'].get(name)
    if row is None or row['artifact']!=source[5:]:raise ValueError('Missing component receipt: '+name)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Artifact hash changed: '+name)
    if len(row['revision'])!=40 or any(char not in '0123456789abcdef' for char in row['revision']):raise ValueError('Exact source revision required')
    with tarfile.open(path) as archive:
        manifest=json.load(archive.extractfile('package/package.json'))
    if (manifest['name'],manifest['version'])!=(name,row['version']):raise ValueError('Artifact identity changed')
    seen.add(name)
if seen!=set(receipt['components']):raise ValueError('Stale component receipt')
print(f'{len(seen)} independently packaged component identities and SHA-256 values verified')
