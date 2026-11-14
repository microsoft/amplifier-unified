"""Exact installed package evidence for managed runner qualification.

This probe executes only in the candidate's isolated interpreter. It never calls
a provider or starts a guest. Behavioral qualification is a separate required
step; metadata alone cannot authorize a new runner.
"""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import sys
import sysconfig
import time


class InventoryUnavailable(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('Retained runtime inventory is unavailable.')


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


def retained_identity(prefix):
    """File-only v1 inventory of an ordinary venv of this trusted interpreter.

    This mode runs under the HOST base Python with -I -S, never target Python.
    It deliberately refuses site/path customization and unknown layouts instead
    of emulating their executable discovery. Hash framing/order match identity().
    No candidate module, metadata hook, .pth, or sysconfig code is imported.
    """
    from contextlib import ExitStack
    from email.parser import BytesParser

    prefix = Path(prefix).absolute()
    deadline = time.monotonic() + 20
    entries, total = 0, 0
    digest = hashlib.sha256()
    snapshots = []
    directories = []
    inventories = []
    absent = []

    def stamp(info):
        # A read can update atime; it is not an input mutation.
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def guard(info):
        if (info.st_uid != os.getuid() or info.st_mode & 0o022
                or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise InventoryUnavailable('runtime_inventory_custody_unavailable')

    with ExitStack() as stack:
        def directory(path):
            fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            current = Path(path.anchor)
            for part in path.parts[1:]:
                current /= part
                parent = fd
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                directories.append((parent, part, (info.st_dev, info.st_ino, info.st_uid, info.st_mode)))
                if current.is_relative_to(prefix):
                    guard(info)
            guard(os.fstat(fd))
            return fd

        root = directory(prefix)

        def no_overrides(fd):
            # CPython path overrides may take effect before site, even under
            # -I -S. Inspect names only; never execute or read these files.
            names = sorted(os.listdir(fd))
            if any(name.endswith('._pth') or name == 'pyvenv.cfg' for name in names):
                raise InventoryUnavailable('runtime_inventory_startup_unsupported')
            absent.append((fd, names))

        def read(parent, name, *, limit=64 * 1024**2):
            nonlocal total
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(handle, 'rb') as stream:
                before = os.fstat(stream.fileno())
                guard(before)
                if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                    raise InventoryUnavailable('runtime_inventory_rejected')
                content = bytearray()
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > 1024**3 or len(content) + len(chunk) > limit or time.monotonic() > deadline:
                        raise InventoryUnavailable('runtime_inventory_limit')
                    content.extend(chunk)
                if stamp(before) != stamp(os.fstat(stream.fileno())):
                    raise InventoryUnavailable('runtime_inventory_changed')
            snapshots.append((parent, name, before))
            return bytes(content)

        cfg = read(root, 'pyvenv.cfg', limit=16384)
        settings = dict(line.split('=', 1) for line in cfg.decode().splitlines() if '=' in line)
        settings = {k.strip(): v.strip() for k, v in settings.items()}
        if settings.get('include-system-site-packages', '').lower() != 'false':
            raise InventoryUnavailable('runtime_inventory_layout_unsupported')
        # No candidate executable is ever launched. Require its ordinary venv
        # symlink to denote this exact already-running trusted base executable.
        executable = Path(sys.executable).resolve(strict=True)
        bin_fd = directory(prefix / 'bin')
        no_overrides(bin_fd)
        link = os.stat('python', dir_fd=bin_fd, follow_symlinks=False)
        if not stat.S_ISLNK(link.st_mode) or (prefix / 'bin/python').resolve(strict=True) != executable:
            raise InventoryUnavailable('runtime_inventory_custody_unavailable')
        snapshots.append((bin_fd, 'python', link))
        bases = {Path(sysconfig.get_path(name, scheme='posix_prefix',
                     vars={'base': str(prefix), 'platbase': str(prefix)})).absolute()
                 for name in ('purelib', 'platlib')}
        if any(not base.is_relative_to(prefix) for base in bases):
            raise InventoryUnavailable('runtime_inventory_layout_unsupported')
        metadata, package = [], False
        count = 0

        def walk(fd, relative):
            nonlocal entries, count, package
            names = sorted(os.listdir(fd))
            before = os.fstat(fd)
            inventories.append((fd, stamp(before), names))
            for name in names:
                entries += 1
                if entries > 100000 or time.monotonic() > deadline:
                    raise InventoryUnavailable('runtime_inventory_limit')
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                guard(info)
                path = relative / name
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    stack.callback(os.close, child)
                    if stamp(os.fstat(child)) != stamp(info):
                        raise InventoryUnavailable('runtime_inventory_changed')
                    yield from walk(child, path)
                    if stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) != stamp(info):
                        raise InventoryUnavailable('runtime_inventory_changed')
                else:
                    content = read(fd, name)
                    # Legacy site discovery can execute/redirect through these
                    # files; do not pretend a file-only traversal emulates it.
                    if (name.endswith('.pth') or name.startswith(('sitecustomize.', 'usercustomize.'))
                            or path.parts[0:1] == ('__pycache__',) and name.startswith(('sitecustomize.', 'usercustomize.'))):
                        raise InventoryUnavailable('runtime_inventory_startup_unsupported')
                    if path.as_posix() in ('digital_twin_universe/__init__.py', 'digital_twin_universe/__init__.pyc'):
                        package = True
                    if name == 'METADATA' and len(path.parts) == 2 and path.parts[0].endswith('.dist-info'):
                        if len(content) > 65536:
                            raise InventoryUnavailable('runtime_inventory_layout_unsupported')
                        message = BytesParser().parsebytes(content, headersonly=True)
                        if message.get('Name', '').lower().replace('_', '-') == 'digital-twin-universe':
                            metadata.append(message.get('Version'))
                    yield path, content
                    count += 1
            if sorted(os.listdir(fd)) != names or stamp(os.fstat(fd)) != stamp(before):
                raise InventoryUnavailable('runtime_inventory_changed')

        for base in sorted(bases):
            fd = directory(base)
            for relative, content in walk(fd, Path()):
                digest.update((base / relative).relative_to(prefix).as_posix().encode() + b'\0')
                digest.update(content)
                digest.update(b'\0')
        if not count or not package or len(metadata) != 1:
            raise InventoryUnavailable('runtime_inventory_layout_unsupported')
        exe_fd = directory(executable.parent)
        no_overrides(exe_fd)
        digest.update(read(exe_fd, executable.name, limit=256 * 1024**2))
        digest.update(cfg)
        for parent, name, info in snapshots:
            if stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != stamp(info):
                raise InventoryUnavailable('runtime_inventory_changed')
        for parent, name, expected in directories:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_uid, info.st_mode) != expected:
                raise InventoryUnavailable('runtime_inventory_changed')
        for fd, expected, names in inventories:
            if stamp(os.fstat(fd)) != expected or sorted(os.listdir(fd)) != names:
                raise InventoryUnavailable('runtime_inventory_changed')
        for fd, names in absent:
            if sorted(os.listdir(fd)) != names:
                raise InventoryUnavailable('runtime_inventory_changed')
        if (prefix / 'bin/python').resolve(strict=True) != executable:
            raise InventoryUnavailable('runtime_inventory_changed')
        return {'version': 1, 'runtimeSha256': digest.hexdigest(), 'files': count,
                'packageVersion': metadata[0], 'pythonVersion': sys.version.split()[0]}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--retained-prefix' and sys.flags.no_site and sys.flags.isolated:
        try:
            result = retained_identity(sys.argv[2])
        except InventoryUnavailable as exc:
            result = {'unavailable': exc.code}
        except (OSError, ValueError, TypeError, KeyError):
            result = {'unavailable': 'runtime_inventory_rejected'}
        print(json.dumps(result, sort_keys=True))
    elif sys.argv[1:] not in ([], ['--incus-engine']):
        raise ValueError('Unsupported runner identity mode.')
    else:
        print(json.dumps(identity(incus_engine=bool(sys.argv[1:])), sort_keys=True))
