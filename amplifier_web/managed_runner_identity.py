"""Exact installed package evidence for managed runner qualification.

This probe executes only in the candidate's isolated interpreter. It never calls
a provider or starts a guest. Behavioral qualification is a separate required
step; metadata alone cannot authorize a new runner.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import stat
import sys
import sysconfig


def identity(*, incus_engine=False):
    import importlib.util
    # Locate the top-level package without executing its (possibly changed)
    # source or bytecode while collecting the evidence used to authorize it.
    spec = importlib.util.find_spec('amplifier_bundle_digital_twin_universe' if incus_engine else 'digital_twin_universe')
    if spec is None or not spec.origin:
        raise ValueError('The managed runner package is unavailable.')
    prefix = Path(sys.prefix).resolve(strict=True)
    package = Path(spec.origin).resolve(strict=True)
    if not package.is_relative_to(prefix):
        raise ValueError('Runner import escaped its managed interpreter.')
    digest = hashlib.sha256()
    count = 0
    # Hash installed runtime bytes, including dependency code and native modules;
    # cached and sourceless bytecode can execute even under -I -B, so they
    # belong to the exact inventory. Every managed launch suppresses writes.
    bases = {Path(sysconfig.get_path(name)).absolute() for name in ('purelib', 'platlib')}
    if not any(package.is_relative_to(base) for base in bases):
        raise ValueError('The imported runner is outside the dependency inventory.')
    for base in sorted(bases):
        if not base.is_relative_to(prefix) or not base.is_dir() or any(part.is_symlink() for part in (base, *base.parents) if part.is_relative_to(prefix)):
            raise ValueError('Runner dependencies are not owned files.')
        for path in sorted(base.rglob('*')):
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Runner dependencies contain shared or redirected files.')
            relative = path.relative_to(prefix).as_posix()
            digest.update(relative.encode() + b'\0')
            with path.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b'\0')
            count += 1
            if count > 100000:
                raise ValueError('Runner dependency inventory exceeds its bound.')
    if not count:
        raise ValueError('The runner dependency inventory is empty.')
    for path in (Path(sys.executable).resolve(strict=True), prefix / 'pyvenv.cfg'):
        digest.update(path.read_bytes())
    return {'version': 1, 'runtimeSha256': digest.hexdigest(), 'files': count,
            'packageVersion': importlib.metadata.version('amplifier-bundle-digital-twin-universe' if incus_engine else 'digital-twin-universe'),
            'pythonVersion': sys.version.split()[0]}


if __name__ == '__main__':
    if sys.argv[1:] not in ([], ['--incus-engine']):
        raise ValueError('Unsupported runner identity mode.')
    print(json.dumps(identity(incus_engine=bool(sys.argv[1:])), sort_keys=True))
