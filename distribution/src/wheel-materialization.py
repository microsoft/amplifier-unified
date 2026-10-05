"""Publisher-only wheel member verification. No extraction or destination reads.

Input is one bounded JSON header line followed by exact wheel bytes. Installed
file expectations and pathname relocation are explicit; bytes are never changed.
Generated installer files require another independently reviewed derivation.
"""
import hashlib
import io
import json
from pathlib import PurePosixPath
import stat
import sys
import zipfile

LIMIT = 64 * 1024 * 1024


def canonical(name, directory=False):
    if not isinstance(name, str) or not name or '\\' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError()
    value = name[:-1] if directory and name.endswith('/') else name
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError()
    return value


def verify():
    header = sys.stdin.buffer.readline(16 * 1024 * 1024 + 1)
    if len(header) > 16 * 1024 * 1024 or not header.endswith(b'\n'):
        raise ValueError()
    request = json.loads(header)
    archive = sys.stdin.buffer.read(LIMIT + 1)
    if len(archive) > LIMIT or hashlib.sha256(archive).hexdigest() != request['sha256']:
        raise ValueError()
    with zipfile.ZipFile(io.BytesIO(archive)) as wheel:
        infos = wheel.infolist()
        if not 1 <= len(infos) <= 65536:
            raise ValueError()
        names = {}
        for info in infos:
            canonical(info.filename, info.is_dir())
            if info.filename in names or info.flag_bits & 1:
                raise ValueError()
            mode = info.external_attr >> 16
            if info.is_dir():
                if stat.S_IFMT(mode) not in (0, stat.S_IFDIR):
                    raise ValueError()
            elif (info.create_system != 3 or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                  or mode & 0o7000 or mode & 0o777 not in (0o644, 0o755)
                  or info.external_attr & 0x18):
                # Ordinary Unix wheel members may carry only permission bits.
                # Explicit 0644/0755 is required; no directory/volume flags,
                # symlink/special types, privilege bits or unspecified modes.
                raise ValueError()
            names[info.filename] = info
        total = 0
        for entry in request['files']:
            path = canonical(entry['path'])
            member = canonical(request['members'][path])
            if path.split('/')[-1] in ('direct_url.json', 'INSTALLER', 'REQUESTED') or any(p in ('bin', 'Scripts') for p in path.split('/')[:-1]) or '.data/scripts/' in member:
                raise ValueError()
            info = names[member]
            if info.is_dir() or info.file_size != entry['bytes'] or info.file_size > LIMIT or (info.external_attr >> 16) & 0o777 != entry['mode']:
                raise ValueError()
            total += info.file_size
            if total > LIMIT:
                raise ValueError()
            digest = hashlib.sha256()
            size = 0
            with wheel.open(info) as stream:
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > entry['bytes'] or size > LIMIT:
                        raise ValueError()
                    digest.update(chunk)
            if size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
                raise ValueError()
    print(json.dumps({'verifiedFiles': len(request['files'])}))


try:
    verify()
except Exception:
    # Never disclose archive members, private paths or arbitrary parser errors.
    sys.stderr.write('source_assembly_wheel_provenance_invalid\n')
    sys.exit(1)
