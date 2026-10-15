#!/usr/bin/env python3
"""Emit private settings structure only; never import credentials into a replica.

This is an inventory, not an executable replica configuration. Every scalar value
is replaced by its type (and string length). Credential-named fields are omitted.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import time

import yaml

DENY = {'apikey', 'token', 'accesstoken', 'refreshtoken', 'secret', 'clientsecret',
        'privatekey', 'password', 'authorization', 'credentials', 'credential',
        'cookie', 'cookies', 'keys', 'env', 'environment', 'headers'}


def shape(value, depth=0):
    if depth > 32:
        raise ValueError('Settings nesting exceeds inventory bound')
    if isinstance(value, dict):
        return {str(key): shape(item, depth + 1) for key, item in value.items()
                if ''.join(c for c in str(key).lower() if c.isalnum()) not in DENY}
    if isinstance(value, list):
        return [shape(item, depth + 1) for item in value]
    if isinstance(value, str):
        return {'scalarType': 'string', 'length': len(value)}
    return {'scalarType': type(value).__name__}


def main():
    home = Path.home()
    config = home / '.amplifier-unified/config'
    paths = [home / '.amplifier/settings.yaml', config / 'settings.yaml',
             *sorted(config.glob('shared-settings-*.json'))]
    rows = []
    for path in paths:
        if not path.exists():
            continue
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 1048576:
                raise ValueError('Unsupported settings inventory source')
            data = stream.read(1048577)
            after = os.fstat(stream.fileno())
        if len(data) > 1048576 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Settings changed during bounded inventory')
        document = json.loads(data) if path.suffix == '.json' else yaml.safe_load(data)
        rows.append({'path': str(path.relative_to(home)), 'bytes': len(data),
                     'sha256': hashlib.sha256(data).hexdigest(), 'shape': shape(document)})
    print(json.dumps({'capturedAt': time.time(), 'kind': 'structure-only',
                      'importedIntoReplica': False, 'files': rows}))


if __name__ == '__main__':
    main()
