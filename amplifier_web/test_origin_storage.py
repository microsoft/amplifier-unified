"""Portable local receipts for work executed on an approved remote machine."""
from contextlib import closing
from pathlib import Path

from filelock import FileLock

from . import test_origin_files as files


def verify_binding(payload, universe, backend):
    expected = {'backendIdentity': backend, 'rootOwner': payload['rootOwner'],
                'operationId': payload['operationId'], 'universeId': universe}
    if payload.get('expectedBackendIdentity') is not None and payload['expectedBackendIdentity'] != backend:
        raise ValueError('The actual backend identity changed.')
    files.publish(Path(payload['root']) / 'backend-binding.json', expected)


def observe(home, row):
    from . import task_resources as storage
    if not row.get('directoryIdentity'):
        return {**row, 'observation': 'unverified'}
    try:
        with files.directory(storage.directory(home, row), private=True) as path:
            if storage.identity(path.stat()) != row['directoryIdentity']:
                raise ValueError('The local test evidence directory changed.')
        return {**row, 'observation': 'present'}
    except FileNotFoundError:
        return {**row, 'observation': 'missing'}
    except (ValueError, OSError):
        return {**row, 'observation': 'changed-or-unavailable'}


def allocate(home, initial, admit):
    from . import task_resources as storage
    if initial.get('kind') != 'test-environment' or not initial.get('testMachine') or initial.get('storageRoot'):
        raise ValueError('Portable allocation requires an owned remote test environment.')
    lock = home / 'workspace-resources' / ('allocation-' + initial['id'] + '.lock')
    with FileLock(str(lock), timeout=5), closing(storage.connect(home)) as db:
        if admit:
            admit()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = storage.get(db, initial['sessionId'], initial['id'])
            if row['status'] != 'prepared':
                return row
            row.update(status='allocating', revision=row['revision'] + 1)
            storage.save(db, row)
        try:
            target = storage.directory(home, row)
            with files.directory(target.parent, create=True, private=True):
                target.mkdir(mode=0o700)  # Never adopt an existing directory.
                with files.directory(target, private=True):
                    row['directoryIdentity'] = storage.identity(target.stat())
                    with db:
                        storage.save(db, row)
                if observe(home, row)['observation'] != 'present':
                    raise ValueError('The local test evidence directory changed.')
            row.update(status='ready', revision=row['revision'] + 1)
        except BaseException:
            row.update(status='unknown', revision=row['revision'] + 1,
                       reason='The local test evidence allocation is unconfirmed. It was not replayed.')
            with db:
                storage.save(db, row)
            raise
        with db:
            storage.save(db, row)
        return row
