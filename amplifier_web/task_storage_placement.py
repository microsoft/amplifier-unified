"""Read-only placement bindings for storage carried by an owned worktree.

The worktree owner alone moves folders and reconciles its receipts. This reader
follows completed moves without scanning other tasks or changing either owner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import uuid

from amplifier_worktrees.git import common
from .host_identity import local_host_identity, require_local_host

MAX_RECORD_BYTES = 1024 * 1024


def _identity(path):
    if path.resolve(strict=True) != path:
        raise ValueError('The task storage folder was replaced by a symbolic link.')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('The task storage folder is not a directory.')
    return [info.st_dev, info.st_ino]


def _record(home, identity):
    if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
        raise ValueError('Invalid task storage checkout identity.')
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in ('managed-worktrees', 'records'):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(identity + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
                raise ValueError('The task storage checkout record is unavailable or too large.')
            encoded = stream.read(MAX_RECORD_BYTES + 1)
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError('The task storage checkout record is too large.')
        value = json.loads(encoded)
        if not isinstance(value, dict) or value.get('id') != identity or value.get('owned') is not True:
            raise ValueError('Task storage must retain its exact owned checkout record.')
        host = value.get('executionHost')
        if host:
            if not isinstance(host, dict) or host.get('scope') != 'local':
                raise ValueError('Task storage belongs to a different execution host.')
            require_local_host(host.get('id'))
        return value
    finally:
        os.close(fd)


def _checkout(home, record, expected_identity=None):
    """Validate only the existing owner's completed move chain, never settle it."""
    base = home / 'managed-worktrees'
    places = {key: base / key / record['id'] for key in ('checkouts', 'archives')}
    current = places['checkouts']
    if record.get('status') not in {'ready', 'archived'}:
        raise ValueError('The task storage checkout move is unresolved; inspect its owner.')
    operations = record.get('archiveOperations', {})
    if not isinstance(operations, dict):
        raise ValueError('The task storage checkout move record is invalid.')
    moved_identity = expected_identity
    for operation in operations.values():
        if not isinstance(operation, dict) or operation.get('status') not in {'applied', 'not_applied'}:
            raise ValueError('The task storage checkout move is unresolved; inspect its owner.')
        action = operation.get('action')
        if action not in {'archive', 'restore'}:
            raise ValueError('The task storage checkout move record is invalid.')
        source = places['checkouts' if action == 'archive' else 'archives']
        target = places['archives' if action == 'archive' else 'checkouts']
        observed_identity = operation.get('directoryIdentity')
        if (operation.get('source') != str(source) or operation.get('target') != str(target)
                or current != source or not isinstance(observed_identity, list)
                or len(observed_identity) != 2
                or moved_identity is not None and observed_identity != moved_identity):
            raise ValueError('The task storage checkout move no longer matches its retained binding.')
        moved_identity = observed_identity
        if operation['status'] == 'applied':
            current = target
    if (record.get('path') != str(current)
            or record['status'] != ('archived' if current == places['archives'] else 'ready')):
        raise ValueError('The task storage checkout path changed without a completed move.')
    actual_identity = _identity(current)
    if moved_identity is not None and moved_identity != actual_identity:
        raise ValueError('The task storage checkout directory was replaced.')
    return current, actual_identity


def capture(home, execution_root):
    """Capture placement once; legacy and DTU allocations do not call this."""
    home = Path(home).expanduser().resolve()
    execution = Path(execution_root).expanduser().resolve(strict=True)
    result = {'storageRoot': str(execution), 'storageRootIdentity': _identity(execution)}
    managed = home / 'managed-worktrees'
    if not execution.is_relative_to(managed):
        return result
    parts = execution.relative_to(managed).parts
    if len(parts) < 2 or parts[0] not in {'checkouts', 'archives'}:
        raise ValueError('Choose a working folder, not managed checkout metadata.')
    record = _record(home, parts[1])
    checkout, checkout_identity = _checkout(home, record)
    if not execution.is_relative_to(checkout):
        raise ValueError('The task execution folder no longer belongs to this checkout.')
    actual_root, actual_repository = common(checkout)
    if actual_root != checkout or str(actual_repository) != record.get('repository'):
        raise ValueError('The task storage checkout repository binding changed.')
    if not isinstance(record.get('signature'), str) or not record['signature']:
        raise ValueError('The task storage checkout creation binding is unavailable.')
    result['storageCheckout'] = {
        'id': record['id'], 'signature': record['signature'], 'repository': record['repository'],
        'sessionId': record.get('sessionId'), 'source': record.get('source'),
        'executionHost': record.get('executionHost'), 'localHostId': local_host_identity()['id'],
        'directoryIdentity': checkout_identity,
        'relativePath': execution.relative_to(checkout).as_posix(),
    }
    # Capture must not silently adopt a move or replacement occurring during Git
    # inspection. Future reads follow only the same pinned directory identity.
    if root(home, result) != execution:
        raise ValueError('The task execution folder moved during storage preparation.')
    return result


def root(home, row):
    """Resolve a retained root, or None for the unchanged app-home layout."""
    if 'storageRoot' not in row:
        return None
    home = Path(home).expanduser().resolve()
    if not isinstance(row['storageRoot'], str) or not row['storageRoot']:
        raise ValueError('The retained task storage root is invalid.')
    recorded = Path(row['storageRoot'])
    if not recorded.is_absolute():
        raise ValueError('The retained task storage root is invalid.')
    binding = row.get('storageCheckout')
    if binding is None:
        current = recorded
    else:
        if not isinstance(binding, dict):
            raise ValueError('The retained task storage checkout binding is invalid.')
        require_local_host(binding.get('localHostId'))
        record = _record(home, binding.get('id'))
        if any(record.get(key) != binding.get(key) for key in
               ('signature', 'repository', 'sessionId', 'source', 'executionHost')):
            raise ValueError('The task storage checkout owner binding changed.')
        checkout, _ = _checkout(home, record, binding.get('directoryIdentity'))
        if not isinstance(binding.get('relativePath'), str) or not binding['relativePath']:
            raise ValueError('The retained task storage relative folder is invalid.')
        relative = Path(binding['relativePath'])
        if relative.is_absolute() or '..' in relative.parts or '.git' in relative.parts:
            raise ValueError('The retained task storage relative folder is invalid.')
        current = checkout / relative
    if _identity(current) != row.get('storageRootIdentity'):
        raise ValueError('The task storage execution folder was replaced.')
    return current
