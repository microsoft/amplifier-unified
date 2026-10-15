#!/usr/bin/env python3
"""Extract only helper-produced regular files; verify all bytes before qualification."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile


def extract(root, stream):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    owner = root.stat()
    if os.geteuid() == 0 and owner.st_uid != 0:
        # Ongoing event shards may arrive after the private reading service is
        # available. Extract with that owned user's authority, never root's.
        os.setgroups([])
        os.setgid(owner.st_gid)
        os.setuid(owner.st_uid)
    received = {}
    receipt = None
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or '..' in name.parts or name.parts[0] not in {'app','native','capture'}:
                raise ValueError('Unexpected snapshot member')
            if member.name in received:
                raise ValueError('Duplicate snapshot member')
            target = root.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            for parent in (target, *target.parents):
                if parent == root.parent:
                    break
                if parent.is_symlink():
                    raise ValueError('Symlink in replica destination')
            incoming = archive.extractfile(member)
            digest = hashlib.sha256()
            count = 0
            temporary = target.with_name(target.name + '.replica-incoming')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, 'wb') as output:
                    while chunk := incoming.read(1024 * 1024):
                        digest.update(chunk); count += len(chunk); output.write(chunk)
                if count != member.size:
                    raise ValueError('Short snapshot member')
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            received[member.name] = {'sha256': digest.hexdigest(), 'bytes': count}
            if name.parts[0] == 'capture':
                if receipt is not None or count > 128 * 1024 * 1024:
                    raise ValueError('Invalid capture receipt')
                receipt = json.loads(target.read_text())
    if receipt is None:
        raise ValueError('Capture receipt missing')
    expected = set()
    for row in receipt['files']:
        expected.add(row['path'])
        if received.get(row['path']) != {'sha256': row['sha256'], 'bytes': row['bytes']}:
            raise ValueError('Snapshot byte verification failed')
        if 'before' in row:
            stamp = row['before']['mtimeNs']
            os.utime(root / row['path'], ns=(stamp, stamp), follow_symlinks=False)
    actual = {name for name in received if not name.startswith('capture/')}
    if expected != actual:
        raise ValueError('Unexpected capture membership')
    return {'phase': receipt['phase'], 'shard': receipt['shard'], 'files': len(expected),
            'bytes': sum(received[n]['bytes'] for n in expected),
            'unstable': sum(row.get('stable') is False for row in receipt['files']),
            'allHashesMatch': True, 'extractorStartedApplication': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(extract(args.root, sys.stdin.buffer)))
