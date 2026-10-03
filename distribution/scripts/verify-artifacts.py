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
        names=set(archive.getnames())
        def entrypoints(value):
            if isinstance(value,str):yield value
            elif isinstance(value,dict):
                for item in value.values():yield from entrypoints(item)
            elif isinstance(value,list):
                for item in value:yield from entrypoints(item)
        for entry in entrypoints({key:manifest[key] for key in ('main','module','types','bin','exports') if key in manifest}):
            if '*' in entry:raise ValueError('Wildcard entrypoint needs an explicit qualification rule: '+name)
            target='package/'+entry.removeprefix('./')
            if target not in names:raise ValueError('Packaged entrypoint missing: '+name+' '+entry)
    if (manifest['name'],manifest['version'])!=(name,row['version']):raise ValueError('Artifact identity changed')
    seen.add(name)
if seen!=set(receipt['components']):raise ValueError('Stale component receipt')
print(f'{len(seen)} independently packaged component identities and SHA-256 values verified')
