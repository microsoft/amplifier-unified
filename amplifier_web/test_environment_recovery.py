"""Explicit owner-scoped reconciliation; backend inspection never repeats effects."""
from __future__ import annotations

from contextlib import closing
import json
import os
import re
import sqlite3
import stat
import time
import uuid

from filelock import FileLock

ACTION = 'workspace.environments.reconcile'


def read_removal(app, args, origin, caller):
    """Read one retained pair, never infer a historical binding or repair it.

    No await or mutation admission occurs here. The existing physical lock and
    a read-only ledger connection delimit the snapshot; duplicate reads do not
    create operation records. A mutation grant is irrelevant to this read.
    """
    from . import task_resources as storage
    from .collaboration import PRINCIPAL
    from .service import AppError

    def root_binding():
        sid = args['sessionId']
        if origin not in {'ui', 'user'} and sid != caller:
            raise AppError('Removal inspection requires the recorded root owner.', 403)
        try:
            source = app._session(sid, hydrate=False)
        except AppError:
            raise AppError('Removal inspection requires the recorded root owner.', 403) from None
        principal = source.get('runtimeSessionId') or source.get('nativeIdentity') or sid
        workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
        if (source.get('sessionKind', 'root') != 'root'
                or origin not in {'ui', 'user'} and PRINCIPAL.get() != principal
                or not workspace or source['workspace'] != workspace['path']):
            raise AppError('Removal inspection requires the recorded root owner.', 403)
        return (principal, *(source.get(k) for k in
                ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')))

    captured = root_binding()
    sid, rid = args['sessionId'], args['id']
    try:
        import fcntl  # Same POSIX lock as the managed Linux environment owner.
        if str(uuid.UUID(rid)) != rid:
            raise ValueError('invalid resource identity')
        folder = app.data_dir / 'workspace-resources'
        target = folder / 'reservations.sqlite3'
        if folder.is_symlink() or not stat.S_ISDIR(folder.stat().st_mode) or not stat.S_ISREG(target.lstat().st_mode):
            raise ValueError('unavailable ledger')
        # FileLock's acquire can create/truncate its file. Open the already-owned
        # lock read-only instead; closing releases the same flock without writes.
        descriptor = os.open(folder / ('environment-' + rid + '.lock'),
                             os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise ValueError('invalid lock')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # storage.connect initializes tables/chmods even on a read. Reuse its
            # exact row reader, but never its writable connection initializer.
            with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
                row = storage.get(db, sid, rid)
                if (not isinstance(row, dict) or row.get('id') != rid or row.get('sessionId') != sid
                        or row.get('workspaceId') != args['workspaceId']
                        or row.get('kind') != 'test-environment'
                        or row.get('owner') != {'sessionId': sid, 'principal': captured[0]}):
                    raise AppError('Removal inspection requires the recorded root owner.', 403)
                if type(row.get('revision')) is not int or row['revision'] != args['expectedRevision']:
                    raise AppError('Removal inspection changed or is busy.', 409)
                try:
                    original = app.operations.requests.read(sid, args['originalRequestId'])
                except ValueError:
                    raise AppError('Original removal record is unavailable.', 404) from None
                if (not isinstance(original, dict) or original.get('sessionId') != sid or original.get('requestId') != args['originalRequestId']
                        or original.get('action') != 'workspace.environments.remove'
                        or original.get('resourceId') not in (None, rid)):
                    raise AppError('Original removal is not bound to this resource.', 409)
                state = original.get('state')
                if state not in ('admitting', 'running', 'completed', 'failed', 'outcome_unknown'):
                    raise ValueError('unsupported retained state')
                if (storage.get(db, sid, rid) != row
                        or app.operations.requests.read(sid, args['originalRequestId']) != original
                        or root_binding() != captured):
                    raise AppError('Removal inspection changed or is busy.', 409)
                # Even a matching cb5 resourceId lacks the immutable original
                # admission and executor proof required for safe continuation.
                result = {
                    'version': 1, 'sessionId': sid, 'id': rid, 'revision': row['revision'],
                    'original': {'requestId': args['originalRequestId'], 'state': state},
                    'status': 'held',
                    'code': 'original_binding_missing' if original.get('resourceId') is None
                            else 'original_binding_unverified',
                    'executor': 'unobserved', 'physical': 'not_observed',
                    'continuationAvailable': False, 'riskDecisionAvailable': False,
                }
                if len(json.dumps(result).encode()) > 4096:
                    raise ValueError('projection exceeds bound')
                return result
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, ImportError):
        raise AppError('Removal inspection changed or is busy.', 409) from None


def backend_identity(value):
    if not isinstance(value, dict) or value.get('backend') not in {'docker', 'incus'}:
        raise ValueError('The original backend identity is unavailable.')
    expected = {'backend', 'daemon', 'project'} if value['backend'] == 'incus' else {'backend', 'daemon'}
    if set(value) != expected or any(not isinstance(value[key], str) or not value[key] or len(value[key]) > 512 for key in expected):
        raise ValueError('The original backend identity is invalid.')
    return value


def bind_configuration(app, row, config):
    """Retain backend kind and an original daemon pin, never infer an endpoint."""
    from . import task_environments as owner
    if not config or owner.config_key(config) != row['backendKey']:
        return row
    backend = config.get('backend', 'docker')
    changes = {} if row.get('backendType') == backend else {'backendType': backend}
    if row.get('backendIdentity') or backend != 'incus':
        return owner.save(app.data_dir, row, **changes) if changes else row
    incus = config.get('incus', {})
    fingerprint = incus.get('daemonFingerprint')
    project = incus.get('project')
    if (owner.config_key(config) != row['backendKey'] or not isinstance(fingerprint, str)
            or not re.fullmatch(r'[a-f0-9]{64}', fingerprint) or not project):
        return owner.save(app.data_dir, row, **changes) if changes else row
    identity = backend_identity({'backend': 'incus', 'daemon': fingerprint, 'project': project})
    return owner.save(app.data_dir, row, **changes, backendIdentity=identity, backendIdentitySource='retained-config')


def verify(row, result):
    """Absence also requires settled admission: an empty inventory is insufficient."""
    observation = result.get('observation')
    if not isinstance(observation, dict):
        raise ValueError('The backend did not provide an exact recovery observation.')
    if (observation.get('rootOwner') != row['sessionId']
            or observation.get('operationId') != row['launchOperationId']
            or not isinstance(observation.get('universeId'), str) or not observation['universeId']
            or row.get('universeId') and observation['universeId'] != row['universeId']):
        raise ValueError('The recovery observation does not match this environment owner.')
    status = result.get('status')
    if status == 'unknown':
        return False
    if status not in {'running', 'stopped', 'absent'}:
        raise ValueError('The backend did not provide a supported physical state.')
    actual = backend_identity(observation.get('backendIdentity'))
    if row.get('backendIdentity'):
        if actual != backend_identity(row['backendIdentity']):
            raise ValueError('The recovery observation came from a different backend.')
    elif observation.get('backendIdentitySource') != 'retained-launch':
        raise ValueError('No retained receipt proves this older environment’s original backend.')
    if status == 'absent':
        if (observation.get('ownership') != 'absent' or observation.get('absenceConfirmed') is not True
                or observation.get('admissionSettled') is not True):
            raise ValueError('Physical absence is not proven while a launch may still be outstanding.')
    elif observation.get('ownership') != 'matched':
        raise ValueError('The physical environment does not match its recorded owner.')
    return True


def project(app, row, request):
    from . import task_environments as owner
    if (request.get('action') != ACTION or request.get('state') != 'completed'
            or request.get('resourceId') != row['id'] or request.get('sessionId') != row['sessionId']):
        raise ValueError('The recovery receipt does not match this environment.')
    result = request['result']
    verified = verify(row, result)
    changes = {'pendingRecovery': None, 'lastRecovery': request,
               'observation': {'status': 'observed' if verified else 'unverified', 'observedAt': time.time()},
               'reason': result.get('message', 'Current state was inspected without replaying prior operations.')}
    if verified:
        changes.update(environmentState='removed' if result['status'] == 'absent' else result['status'],
                       backendIdentity=result['observation']['backendIdentity'])
        if result['status'] == 'absent':
            if row.get('preparationPending'):
                # Guest absence does not prove the owned dependency image or
                # interrupted builder was removed. Keep its cleanup reachable.
                changes.update(environmentState='not-started', guestRemoved=True,
                               reason='The test guest is absent. Its owned dependency preparation still needs cleanup.')
            else:
                changes['status'] = 'retired'
    return owner.save(app.data_dir, row, **changes)


def repair(app, row):
    if row.get('pendingRecovery'):
        request = app.operations.requests.read(row['sessionId'], row['pendingRecovery'])
        if request.get('state') == 'completed':
            return project(app, row, request)
    return row


def reconcile(app, row, args, config, request, admit):
    from . import task_environments as owner
    home, sid = app.data_dir, row['sessionId']
    lock = FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0)
    locked = observed = False
    result = None
    try:
        lock.acquire()
        locked = True
        admit()
        row = owner.get(home, sid, row['id'])
        if row['revision'] != args['expectedRevision']:
            raise ValueError('The environment changed. Inspect its current revision.')
        if row['backendKey'] != owner.config_key(config):
            raise ValueError('Recovery requires the exact original backend configuration.')
        row = bind_configuration(app, row, config)
        row = repair(app, row)
        if row.get('pendingRequest'):
            prior = app.operations.requests.read(sid, row['pendingRequest'])
            if owner.confirmed_result(row, prior):
                row = owner.project_result(home, row, prior)
        admit()
        row = owner.save(home, row, pendingRecovery=request['requestId'])
        result = owner.invoke(config, home, row, 'reconcile', args)
        observed = True
        admit()
        if result.get('ok'):
            verify(row, result)
            request = app.operations.requests.finish(request, 'completed', resourceId=row['id'], result=result,
                message=result.get('message', 'Current state was inspected; earlier uncertain operations are unchanged.'))
            row = project(app, row, request)
        else:
            request = app.operations.requests.finish(request, 'failed', resourceId=row['id'], observationResult=result,
                message=result.get('message', 'The original backend could not verify this environment. Ownership remains retained.'))
            row = owner.save(home, row, pendingRecovery=None, lastRecovery=request)
        return {'resource': owner.public(home, {**row, 'recovery': {'available': row['status'] != 'retired'}}), 'request': request}
    except BaseException as exc:
        try:
            recorded = app.operations.requests.read(sid, request['requestId'])
        except (OSError, ValueError, sqlite3.Error):
            recorded = {}
        if recorded.get('state') == 'completed':
            request = recorded
            try:
                if locked:
                    row = project(app, owner.get(home, sid, row['id']), request)
            except (OSError, ValueError, sqlite3.Error):
                row = {**row, 'reason': 'The recovery observation is saved; refresh to repair its metadata. Earlier uncertain operations are unchanged.'}
        else:
            request = app.operations.requests.finish(request, 'failed' if observed or not locked else 'outcome_unknown',
                resourceId=row['id'], observationResult=result,
                message='Recovery did not establish a safe state: ' + str(exc)[:500] + '. No backend effect was replayed.')
            if locked:
                try:
                    row = owner.save(home, owner.get(home, sid, row['id']), pendingRecovery=None, lastRecovery=request)
                except (OSError, ValueError, sqlite3.Error):
                    pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {'resource': owner.public(home, row), 'request': request}
    finally:
        if locked:
            lock.release()
