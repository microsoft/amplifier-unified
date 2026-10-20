"""Private, immutable snapshots of host-qualified test backend configurations.

Only the host passes normalized configuration here. A resource refers to its
exact configuration hash; neither callers nor old receipts supply executables.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

MAX_BYTES = 65536
FIELDS = {'enabled', 'installationId', 'commit', 'backend', 'incus', 'dockerHost', 'image',
          'maxRunning', 'maxInstances', 'minFreeBytes', 'python', 'admissionRoot', 'retentionMode', 'remoteRunner', 'capacityPolicyId'}
INCUS_FIELDS = {'executable', 'executableSha256', 'packageSha256', 'incusExecutable', 'configDir',
                'admissionDir', 'project', 'image', 'daemonFingerprint', 'maxRunning',
                'maxInstances', 'minFreeBytes', 'interpreter', 'packageDirectory',
                'preparationNetwork', 'preparationStorage'}


def key(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def directory(home):
    path = Path(home) / 'workspace-resources' / 'backend-configs'
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('The retained backend configuration directory was replaced.')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(descriptor)
        raise ValueError('Retained backend configurations require a private owner directory.')
    return descriptor


def read_at(descriptor, identity):
    if not re.fullmatch(r'[a-f0-9]{64}', identity):
        raise ValueError('The retained backend configuration identity is invalid.')
    handle = os.open(identity + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    with os.fdopen(handle, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise ValueError('The retained backend configuration is not a private regular file.')
        value = json.load(stream)
    if not isinstance(value, dict) or set(value) != {'version', 'configuration'} or value['version'] != 1:
        raise ValueError('The retained backend configuration format is invalid.')
    config = value['configuration']
    if not isinstance(config, dict) or set(config) - FIELDS or key(config) != identity:
        raise ValueError('The retained backend configuration identity changed.')
    if 'incus' in config and (not isinstance(config['incus'], dict) or set(config['incus']) - INCUS_FIELDS):
        raise ValueError('The retained Incus configuration contains unsupported fields.')
    return config


def retain(home, config):
    identity = key(config)
    if not isinstance(config, dict) or set(config) - FIELDS:
        raise ValueError('Only a qualified backend configuration can be retained.')
    if 'incus' in config and (not isinstance(config['incus'], dict) or set(config['incus']) - INCUS_FIELDS):
        raise ValueError('Only qualified Incus fields can be retained.')
    content = json.dumps({'version': 1, 'configuration': config}, sort_keys=True).encode()
    if len(content) > MAX_BYTES:
        raise ValueError('The qualified backend configuration exceeds its bound.')
    descriptor = directory(home)
    temporary = '.' + uuid.uuid4().hex + '.tmp'
    try:
        try:
            if read_at(descriptor, identity) != config:
                raise ValueError('The retained backend configuration does not match its identity.')
            return identity
        except FileNotFoundError:
            pass
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        with os.fdopen(handle, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o400)
        try:
            os.link(temporary, identity + '.json', src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        except FileExistsError:
            pass
        os.unlink(temporary, dir_fd=descriptor)
        os.fsync(descriptor)
        if read_at(descriptor, identity) != config:
            raise ValueError('The retained backend configuration changed during publication.')
        return identity
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        os.close(descriptor)


def resolve(app, row, active):
    identity = row['backendKey']
    if not row.get('backendConfigRetained'):
        if active and key(active) == identity:
            return active
        raise ValueError('This older environment has no retained backend configuration. Restore its exact original configuration to inspect or clean it up.')
    descriptor = directory(app.data_dir)
    try:
        config = read_at(descriptor, identity)
    finally:
        os.close(descriptor)
    if config.get('remoteRunner'):
        from .registered_test_runners import qualify_retained
        return qualify_retained(app, config)
    # The active configuration has already passed the same current qualification.
    if active == config:
        return active
    from .task_environments import SUPPORTED_HOST, validate_configuration_value
    if not SUPPORTED_HOST:
        raise ValueError('The retained test backend needs its qualified Linux host.')
    value = {k: v for k, v in config.items() if k not in {'python', 'admissionRoot'}}
    qualified, _, _ = validate_configuration_value(app, value)
    if qualified != config or key(qualified) != identity:
        raise ValueError('The retained backend installation or configuration changed. Restore the exact qualified installation before cleanup.')
    return qualified
