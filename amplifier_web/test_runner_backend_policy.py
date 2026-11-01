"""Mutable admission limits for one registered target account and Incus project.

The registered package and backend configuration remain immutable. These limits
are read only at future admission points; saving them never touches a running
environment or changes an existing owner's endpoint, package, or cleanup route.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

DEFAULTS = {'maxRunning': 0, 'maxInstances': 0, 'minFreeBytes': 10 * 1024**3}
MAXIMUM = 2**53 - 1
MAX_RECORD = 16384


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate(limits):
    if not isinstance(limits, dict) or set(limits) - DEFAULTS.keys():
        raise ValueError('Choose only supported test environment limits.')
    if any(type(value) is not int or not 0 <= value <= MAXIMUM for value in limits.values()):
        raise ValueError('Test environment limits must be whole numbers of zero or more.')
    return dict(limits)


def _binding(identity):
    if (not isinstance(identity, dict) or set(identity) != {'backend', 'daemon', 'project'}
            or identity['backend'] != 'incus'
            or any(not isinstance(identity[key], str) or not 1 <= len(identity[key]) <= 256
                   for key in ('daemon', 'project'))):
        raise ValueError('An exact registered Incus backend identity is required.')
    return {'account': os.getuid(), 'backend': dict(identity)}


def _check_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise ValueError('Test environment settings must use private regular files.')


@contextmanager
def _locked(root, binding):
    import fcntl

    root = Path(root)
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError('Test environment settings cannot traverse links.')
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Test environment settings require private controller storage.')
    stem = 'backend-policy-' + _digest(binding)
    fd = os.open(root / (stem + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _check_file(os.fstat(fd))
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield root / (stem + '.json')
    finally:
        os.close(fd)


def _read(path, binding):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {'version': 1, 'binding': binding, 'generation': 0, 'limits': {}}
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        _check_file(info)
        if info.st_size > MAX_RECORD:
            raise ValueError('Test environment settings exceed their supported size.')
        value = json.load(stream)
    if (not isinstance(value, dict) or set(value) != {'version', 'binding', 'generation', 'limits'}
            or type(value['version']) is not int or value['version'] != 1
            or value['binding'] != binding or type(value['generation']) is not int
            or not 0 <= value['generation'] <= MAXIMUM):
        raise ValueError('Test environment settings do not match this registered backend.')
    validate(value['limits'])
    return value


def _write(path, value):
    content = json.dumps(value, sort_keys=True).encode()
    if len(content) > MAX_RECORD:
        raise ValueError('Test environment settings exceed their supported size.')
    temporary = path.with_name('.backend-policy-' + uuid.uuid4().hex)
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot(value, config):
    defaults = {key: config.get(key, default) for key, default in DEFAULTS.items()}
    validate(defaults)
    return {'revision': _digest([value, defaults]), 'limits': dict(value['limits']),
            'defaults': defaults, 'effective': {**defaults, **value['limits']},
            'scope': 'All cooperating registered controllers using this account and Incus daemon/project on this testing machine. Other accounts and older controllers may use different limits.',
            'behavior': 'Applies to future admissions. Lowering a limit does not stop active work. Zero disables that limit; machine resource budgets still apply.'}


def snapshot(root, backend_identity, config):
    """Read current policy without changing configuration or running work."""
    binding = _binding(backend_identity)
    with _locked(root, binding) as path:
        return _snapshot(_read(path, binding), config)


def save(root, backend_identity, config, expected_revision, limits):
    """CAS publication; a lost acknowledgement is reconciled by reading again."""
    limits = validate(limits)
    binding = _binding(backend_identity)
    with _locked(root, binding) as path:
        current = _read(path, binding)
        if _snapshot(current, config)['revision'] != expected_revision:
            raise ValueError('Test environment limits changed. Refresh before saving.')
        if current['generation'] >= MAXIMUM:
            raise ValueError('The settings revision limit was reached.')
        updated = {**current, 'generation': current['generation'] + 1, 'limits': limits}
        _write(path, updated)
        return _snapshot(updated, config)


def apply(root, backend_identity, config):
    """Copy only mutable admission integers, retaining the original connection."""
    result = copy.deepcopy(config)
    result.update(snapshot(root, backend_identity, config)['effective'])
    return result
