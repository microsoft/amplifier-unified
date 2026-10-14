"""Physical task storage owned by the existing workspace-resource ledger.

This owner allocates local bytes only. It never changes tool permissions,
launches a browser/database server, deletes contents or replays uncertain work.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

from filelock import FileLock, Timeout as LockTimeout

KINDS = ('scratch-directory', 'browser-profile', 'sqlite-database')
MAX_ROOT = 32
MAX_ACTIVE = 512


def definitions(schema, string):
    scope = {'workspaceId': string(100), 'sessionId': string(200)}
    return {
        'workspace.resources.provision': ('Allocate one task-owned scratch directory, browser-profile directory or local SQLite file outside source repositories. Does not launch processes or expand file permissions. Stable retry returns the same retained allocation.', schema({**scope,
            'kind': {'enum': list(KINDS)}, 'label': string(120)}, [*scope, 'kind'])),
        'workspace.resources.read': ('Inspect this task’s bounded owned storage without starting workers or recreating missing files. Current reference file-tool access is an observation, not a new grant.', schema({**scope,
            'id': string(100), 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 32}}, [*scope])),
        'workspace.resources.retire': ('Retire a task-owned allocation at its exact revision, retaining every file. Does not delete data, close an external browser/database, or claim its effects finished.', schema({**scope,
            'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1}}, [*scope, 'id', 'expectedRevision'])),
    }


def connect(home):
    directory = home / 'workspace-resources'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError('The resource owner directory was replaced.')
    target = directory / 'reservations.sqlite3'
    if target.is_symlink():
        raise ValueError('The resource owner ledger was replaced.')
    db = sqlite3.connect(target, timeout=5)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS allocations(id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
            root TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS allocations_root ON allocations(root,status);
        CREATE INDEX IF NOT EXISTS allocations_page ON allocations(root);
        CREATE INDEX IF NOT EXISTS allocations_status ON allocations(status);
    ''')
    target.chmod(0o600)
    return db


def identity(info):
    return [info.st_dev, info.st_ino]


def save(db, row):
    db.execute('INSERT OR REPLACE INTO allocations VALUES(?,?,?,?,?)',
               (row['id'], row['workspaceId'], row['sessionId'], row['status'], json.dumps(row)))


def get(db, sid, rid):
    found = db.execute('SELECT body FROM allocations WHERE id=? AND root=?', (rid, sid)).fetchone()
    if not found:
        raise ValueError('Choose an allocation owned by this conversation.')
    return json.loads(found[0])


def directory(home, row):
    return home / 'workspace-resources' / 'owned' / hashlib.sha256(row['sessionId'].encode()).hexdigest() / row['id']


def public(home, row):
    root = directory(home, row)
    return {**{key: value for key, value in row.items() if key not in {'directoryIdentity', 'fileIdentity'}},
            'directory': str(root), 'path': str(root / 'database.sqlite3' if row['kind'] == 'sqlite-database' else root),
            'retention': 'preserved-until-explicit-cleanup', 'fileAccessChanged': False}


def _open_directory(parent, name, *, create=False):
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def open_parent(home, row, *, create=False):
    """Pin every managed path component; a symlink cannot redirect an effect."""
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in ('workspace-resources', 'owned', hashlib.sha256(row['sessionId'].encode()).hexdigest()):
            child = _open_directory(fd, part, create=create)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def observe(home, row):
    """Read identity only: no content scan, repair, creation or process probe."""
    if not row.get('directoryIdentity'):
        return {**row, 'observation': 'unverified'}
    try:
        parent = open_parent(home, row)
        try:
            fd = _open_directory(parent, row['id'])
        finally:
            os.close(parent)
        try:
            if identity(os.fstat(fd)) != row['directoryIdentity']:
                raise ValueError('Changed directory identity')
            if row['kind'] == 'sqlite-database':
                info = os.stat('database.sqlite3', dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or identity(info) != row.get('fileIdentity'):
                    raise ValueError('Changed database identity')
            return {**row, 'observation': 'present'}
        finally:
            os.close(fd)
    except FileNotFoundError:
        return {**row, 'observation': 'missing'}
    except (OSError, ValueError):
        return {**row, 'observation': 'changed-or-unavailable'}


def prepare(home, workspace, sid, actor, args, command_id):
    if not command_id or len(command_id) > 200:
        raise ValueError('Supply a stable command ID for resource allocation.')
    signature = hashlib.sha256(json.dumps(['task-resource', workspace, sid, actor, args], sort_keys=True).encode()).hexdigest()
    with closing(connect(home)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT fingerprint,result FROM commands WHERE id=?', (command_id,)).fetchone()
        if prior:
            if prior[0] != signature:
                raise ValueError('This resource command already belongs to different arguments or an owner.')
            return get(db, sid, json.loads(prior[1])['allocationId']), False
        total, owned = db.execute("SELECT count(*),coalesce(sum(root=?),0) FROM allocations WHERE status!='retired'", (sid,)).fetchone()
        if total >= MAX_ACTIVE or owned >= MAX_ROOT:
            raise ValueError('Task resource capacity is full. Inspect and retire unused allocations first.')
        row = dict(id=str(uuid.uuid4()), workspaceId=workspace, sessionId=sid, owner=actor,
                   kind=args['kind'], label=args.get('label', '').strip(), status='prepared', revision=1,
                   createdAt=time.time(), commandId=command_id)
        save(db, row)
        db.execute('INSERT INTO commands VALUES(?,?,?)', (command_id, signature, json.dumps({'allocationId': row['id']})))
        return row, True


def suppress(home, row, reason):
    with closing(connect(home)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        row = get(db, row['sessionId'], row['id'])
        if row['status'] == 'prepared':
            row.update(status='suppressed', reason=reason, revision=row['revision']+1)
            save(db, row)
        return row


def allocate(home, initial, admit=None):
    """One admitted local effect; any retry only observes its durable state."""
    lock = home / 'workspace-resources' / ('allocation-' + initial['id'] + '.lock')
    with FileLock(str(lock), timeout=5), closing(connect(home)) as db:
        if admit:
            admit()  # Main-loop authority check after any owner-lock wait.
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = get(db, initial['sessionId'], initial['id'])
            if row['status'] != 'prepared':
                return row
            row.update(status='allocating', revision=row['revision']+1)
            save(db, row)
        parent = fd = None
        try:
            parent = open_parent(home, row, create=True)
            # Never reuse a directory that predates this effect.
            os.mkdir(row['id'], mode=0o700, dir_fd=parent)
            fd = _open_directory(parent, row['id'])
            row['directoryIdentity'] = identity(os.fstat(fd))
            with db:
                save(db, row)
            if row['kind'] == 'sqlite-database':
                # Create valid database bytes in memory, then write only through
                # the pinned exclusive destination. SQLite never follows a path
                # supplied by a peer or a newly substituted filesystem symlink.
                with closing(sqlite3.connect(':memory:')) as memory:
                    memory.execute('PRAGMA user_version=0')
                    content = memory.serialize()
                out = os.open('database.sqlite3', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=fd)
                with os.fdopen(out, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                    row['fileIdentity'] = identity(os.fstat(stream.fileno()))
            os.fsync(fd)
            os.fsync(parent)
            if observe(home, row)['observation'] != 'present':
                raise ValueError('The resource path changed during allocation.')
            row.update(status='ready', revision=row['revision']+1)
        except BaseException:
            row.update(status='unknown', revision=row['revision']+1,
                       reason='Allocation did not finish with verified ownership. Preserve and inspect the resource; it was not replayed.')
            with db:
                save(db, row)
            raise
        finally:
            if fd is not None:
                os.close(fd)
            if parent is not None:
                os.close(parent)
        with db:
            save(db, row)
        return row


def read(home, sid, args):
    with closing(connect(home)) as db:
        limit, offset = args.get('limit', 25), args.get('offset', 0)
        rows = ([get(db, sid, args['id'])] if args.get('id') else [json.loads(value[0]) for value in db.execute(
            'SELECT body FROM allocations WHERE root=? ORDER BY rowid DESC LIMIT ? OFFSET ?', (sid, limit+1, offset))])
        return {'items': [public(home, observe(home, recover(home, db, row))) for row in rows[:limit]],
                'nextOffset': offset + limit if len(rows) > limit else None}


def recover(home, db, row):
    """A free physical owner lock distinguishes interruption from live work."""
    if row['status'] != 'allocating':
        return row
    try:
        with FileLock(str(home / 'workspace-resources' / ('allocation-' + row['id'] + '.lock')), timeout=0):
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = get(db, row['sessionId'], row['id'])
                if row['status'] == 'allocating':
                    row.update(status='unknown', revision=row['revision']+1,
                               reason='The allocation owner stopped before recording completion. All contents are retained; no effect was replayed.')
                    save(db, row)
                return row
    except LockTimeout:
        return row


def retire(home, sid, actor, args, command_id, admit=None):
    if not command_id:
        raise ValueError('Supply a stable command ID for retirement.')
    uuid.UUID(args['id'])
    signature = hashlib.sha256(json.dumps(['retire-task-resource', sid, actor, args], sort_keys=True).encode()).hexdigest()
    lock = home / 'workspace-resources' / ('allocation-' + args['id'] + '.lock')
    # A live physical effect owns this lock. Retirement never races its final
    # identity write or changes its state while an external effect is pending.
    with FileLock(str(lock), timeout=0), closing(connect(home)) as db, db:
        if admit:
            admit()
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT fingerprint,result FROM commands WHERE id=?', (command_id,)).fetchone()
        if prior:
            if prior[0] != signature:
                raise ValueError('This resource command already belongs to a different retirement.')
            return json.loads(prior[1])
        row = get(db, sid, args['id'])
        if row['revision'] != args['expectedRevision']:
            raise ValueError('The allocation changed. Inspect its current revision.')
        # A free owner lock proves no live allocation thread here. Contents,
        # including an interrupted effect's uncertain bytes, are all retained.
        row.update(status='retired', revision=row['revision']+1, retiredAt=time.time())
        save(db, row)
        result = public(home, row)
        db.execute('INSERT INTO commands VALUES(?,?,?)', (command_id, signature, json.dumps(result)))
        return result


async def access(app, sid, rows):
    method = getattr(app.runtime, 'resource_access', None)
    paths = [row['path'] for row in rows]
    unknown = {'status': 'unknown', 'reason': 'No ready supported file-policy observation. Allocation does not grant file access.'}
    if not method or not paths:
        return [{**row, 'access': unknown} for row in rows]
    try:
        async with asyncio.timeout(12):
            report = await method(sid, paths)
        observed = report.get('paths', {}) if isinstance(report, dict) and report.get('status') == 'observed' else {}
        return [{**row, 'access': observed.get(row['path'], unknown)} for row in rows]
    except (RuntimeError, ValueError, TimeoutError):
        return [{**row, 'access': unknown} for row in rows]


async def dispatch(app, action, args, origin, command_id, caller):
    from .service import AppError
    from .collaboration import PRINCIPAL
    user = origin in {'ui', 'user'}
    sid = args['sessionId']
    if not user and sid != caller:
        raise AppError('Task resources belong to the calling conversation.', 403)
    source = app._session(sid, hydrate=False)
    native = source.get('runtimeSessionId') or source.get('nativeIdentity') or sid
    if source.get('sessionKind', 'root') != 'root' or not user and PRINCIPAL.get() != native:
        raise AppError('Task storage requires its host-bound root owner.', 403)
    workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
    if workspace is None or source['workspace'] != workspace['path']:
        raise AppError('Choose this conversation’s current workspace.', 403)
    actor = {'sessionId': sid, 'principal': (source.get('runtimeSessionId') or source.get('nativeIdentity') or sid) if user else PRINCIPAL.get()}
    captured = tuple(source.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision'))
    def guard():
        current = app._session(sid, hydrate=False)
        if captured != tuple(current.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')):
            raise AppError('The task moved or stopped before resource admission.', 409)
        if not user:
            app.collaboration.active_binding(current)
            if reason := app.assignments.blocked(current):
                raise AppError(reason, 409)
    loop = asyncio.get_running_loop()
    async def locked_guard():
        async with app.lock:
            guard()
    def admit():
        asyncio.run_coroutine_threadsafe(locked_guard(), loop).result(timeout=10)
    try:
        if action == 'workspace.resources.read':
            page = await asyncio.to_thread(read, app.data_dir, sid, args)
        else:
            guard()
            if action == 'workspace.resources.provision':
                row, fresh = await asyncio.to_thread(prepare, app.data_dir, workspace['id'], sid, actor, args, command_id)
                if fresh:
                    try:
                        guard()
                    except BaseException:
                        await asyncio.to_thread(suppress, app.data_dir, row, 'Task authority changed before resource admission.')
                        raise
                    # Cancellation cannot stop a filesystem thread. Its durable
                    # owner finishes the admitted effect before releasing control.
                    try:
                        row = await app.worktrees.finish_thread(allocate, app.data_dir, row, admit)
                    except BaseException:
                        await asyncio.to_thread(suppress, app.data_dir, row, 'Allocation admission or completion was interrupted; inspect the retained resource.')
                        raise
                page = await asyncio.to_thread(read, app.data_dir, sid, {'id': row['id']})
            else:
                result = await app.worktrees.finish_thread(retire, app.data_dir, sid, actor, args, command_id, admit)
                page = {'items': [result], 'nextOffset': None}
        page['items'] = await access(app, sid, page['items'])
        if captured[0] != app._session(sid, hydrate=False).get('workspace'):
            raise AppError('The task moved. Inspect resources from its current workspace.', 409)
        return {'accepted': True, 'result': page}
    except (ValueError, OSError, sqlite3.Error, LockTimeout) as exc:
        raise AppError(str(exc), 409) from None
