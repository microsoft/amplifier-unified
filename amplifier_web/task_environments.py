"""Opt-in, task-owned test environments using a pinned managed DTU core.

The resource ledger owns environments; the existing operation request journal
owns command admission. Neither a reconnect nor an uncertain result replays an
effect. The isolated helper imports DTU from its managed installation only.
"""
from __future__ import annotations

import asyncio
from contextlib import closing, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

from filelock import FileLock, Timeout as LockTimeout

from . import task_resources as storage

REPOSITORY = 'https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe'
# Updated with the qualified core revision when the integration is finalized.
DTU_COMMIT = ''
APPROVED_IMAGE = 'docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f'
KIND = 'test-environment'
PROFILE = 'linux-python'
OUTPUT_LIMIT = 128 * 1024
SUPPORTED_HOST = sys.platform.startswith('linux')


def definitions(schema, string):
    scope = {'workspaceId': string(100), 'sessionId': string(200)}
    request = {**scope, 'requestId': {**string(200), 'minLength': 1}}
    owned = {**request, 'id': string(100), 'expectedRevision': {'type': 'integer', 'minimum': 1}}
    return {
        'workspace.environments.read': ('Inspect this task’s test environments, retained results and current backend availability. Does not launch, resume or replay work.', schema(scope)),
        'workspace.environments.start': ('Copy this task’s current Git working area into one approved isolated test environment. No original writable mounts or injected credentials. Keep requestId stable; unknown launches never replay.', schema({**request, 'profileId': {'enum': [PROFILE]}})),
        'workspace.environments.execute': ('Run an approved command in this task’s environment, retaining bounded output. Keep requestId stable. Timeout means the command may still be running, not cancelled.', schema({**owned, 'command': {**string(16384), 'minLength': 1}, 'timeoutSeconds': {'type': 'integer', 'minimum': 1, 'maximum': 300}}, [*owned, 'command'])),
        'workspace.environments.stop': ('Stop this task’s containers at an exact revision and observe the result. Keeps source, artifacts, containers and volumes; never implies removal.', schema(owned)),
        'workspace.environments.remove': ('Remove an already stopped owned environment only after preserving its source and artifacts. Unknown export or teardown leaves ownership unresolved and never replays.', schema(owned)),
    }


def configuration(app):
    """Operator-owned opt-in, never a request-provided executable or profile."""
    target = app.data_dir / 'test-environments.json'
    if not target.exists():
        return None, 'disabled', 'Test environments have not been enabled. A managed Linux test host needs setup.'
    if not SUPPORTED_HOST:
        return None, 'unavailable', 'This version needs a managed Linux test host. Local setup is not available on this platform.'
    try:
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('The operator configuration must be a private regular file.')
        value = json.loads(target.read_text())
        if not isinstance(value, dict):
            raise ValueError('The operator configuration must be an object.')
        if value.get('enabled') is not True:
            return None, 'disabled', 'Test environments are disabled on this host.'
        if set(value) - {'enabled', 'installationId', 'commit', 'dockerHost', 'image'}:
            raise ValueError('The operator configuration contains unsupported settings.')
        if not DTU_COMMIT or value.get('commit') != DTU_COMMIT:
            raise ValueError('The managed backend is not pinned to this app’s qualified revision.')
        manager = getattr(app, 'smart_tools', None)
        installation = next((row for row in manager.state['installations'] if row['id'] == value.get('installationId')), None) if manager else None
        if not installation or installation.get('status') != 'installed' or installation.get('commit') != DTU_COMMIT or installation.get('repository', '').removesuffix('.git') != REPOSITORY or installation.get('extras'):
            raise ValueError('Install the qualified DTU core without provider extras before enabling it.')
        identity = installation['id']
        if not re.fullmatch(r'[a-f0-9]{24}', identity):
            raise ValueError('The managed installation identity is invalid.')
        root = app.data_dir / 'smart-tools' / 'installs' / identity
        python = root / 'venv' / 'bin' / 'python'
        if installation.get('python') != str(python) or not python.is_file() or root.is_symlink() or (root / 'venv').is_symlink():
            raise ValueError('The exact managed backend interpreter is unavailable.')
        docker = value.get('dockerHost', '')
        if not isinstance(docker, str) or not docker.startswith('unix:///') or '\x00' in docker or '..' in Path(docker[7:]).parts:
            raise ValueError('Only an explicitly configured local Docker Unix socket is supported.')
        image = value.get('image', APPROVED_IMAGE)
        if image != APPROVED_IMAGE:
            raise ValueError('Only the app’s approved Python image digest is supported.')
        return {**value, 'image': image, 'python': str(python)}, 'ready', 'Isolated Linux Python environment; offline network and no host source mounts.'
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'unavailable', 'The managed Linux test host needs setup, or its approved backend configuration changed.'


def config_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def public(home, row):
    return {key: value for key, value in {
        'id': row['id'], 'sessionId': row['sessionId'], 'workspaceId': row['workspaceId'],
        'revision': row['revision'], 'profileId': row['profileId'],
        'status': row.get('environmentState', 'unknown'), 'universeId': row.get('universeId'),
        'source': row['source'], 'snapshot': row.get('snapshot'), 'services': row.get('services', []),
        'lastOperation': row.get('lastOperation'), 'reason': row.get('reason'), 'observation': row.get('observation'),
        'retention': {'path': str(storage.directory(home, row)), 'preserved': True,
                      'policy': 'Stop retains container data. Remove exports source and artifacts before deleting owned containers and volumes. Retained host files are never deleted here.'},
    }.items() if value is not None}


def get(home, sid, identity):
    with closing(storage.connect(home)) as db:
        row = storage.get(db, sid, identity)
    if row['kind'] != KIND:
        raise ValueError('This resource is not an owned test environment.')
    return row


def save(home, row, **changes):
    row = {**row, **changes, 'revision': row['revision'] + 1}
    with closing(storage.connect(home)) as db, db:
        storage.save(db, row)
    return row


def installation_lock(home, identity):
    if not re.fullmatch(r'[a-f0-9]{16,64}', identity):
        raise ValueError('The managed installation identity is invalid.')
    with closing(storage.connect(home)):
        pass
    return FileLock(str(home / 'workspace-resources' / ('backend-' + identity + '.lock')), timeout=5)


def remove_installation(home, identity, folder, *, ignore_errors=False):
    """Existing install removal cannot orphan a resource's recovery backend."""
    with installation_lock(home, identity), closing(storage.connect(home)) as db:
        if db.execute("SELECT 1 FROM allocations WHERE status!='retired' AND json_extract(body,'$.kind')=? AND json_extract(body,'$.backendInstallation')=? LIMIT 1", (KIND, identity)).fetchone():
            raise ValueError('This backend still owns test environments. Remove those environments before removing or replacing its installation.')
        shutil.rmtree(folder, ignore_errors=ignore_errors)


def prepare(home, sid, workspace, actor, source, backend_key, request_id, installation=None):
    with installation_lock(home, installation) if installation else nullcontext(), closing(storage.connect(home)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM allocations WHERE root=? AND status!='retired' AND json_extract(body,'$.kind')=? LIMIT 1", (sid, KIND)).fetchone():
            raise ValueError('This task already owns an environment. Inspect or remove it before starting another.')
        if db.execute("SELECT COUNT(*) FROM allocations WHERE status!='retired'").fetchone()[0] >= storage.MAX_ACTIVE:
            raise ValueError('The resource owner capacity has been reached.')
        row = {'id': str(uuid.uuid4()), 'kind': KIND, 'workspaceId': workspace, 'sessionId': sid,
               'owner': actor, 'source': source, 'profileId': PROFILE, 'backendKey': backend_key,
               'backendInstallation': installation,
               'status': 'prepared', 'environmentState': 'preparing', 'revision': 1,
               'createdAt': time.time(), 'commandId': request_id,
               'launchOperationId': str(uuid.uuid4())}
        storage.save(db, row)
    return row


def invoke(config, home, row, action, args):
    """Bound helper output and lifetime; killing a helper is not command cancellation."""
    folder = storage.directory(home, row)
    payload = {'action': action, 'root': str(folder), 'source': row['source'],
               'operationId': row['launchOperationId'], 'universeId': row.get('universeId'),
               'dockerHost': config['dockerHost'], 'image': config['image'],
               'requestId': args.get('requestId'), 'command': args.get('command'),
               'timeoutSeconds': args.get('timeoutSeconds', 60)}
    helper = Path(__file__).with_name('test_environment_worker.py')
    environment = {'PATH': os.defpath, 'HOME': str(folder), 'LANG': 'C.UTF-8'}
    process = subprocess.Popen([config['python'], '-I', str(helper)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=folder, env=environment, start_new_session=True)
    output, errors = bytearray(), bytearray()
    drained = False
    deadline = time.monotonic() + (650 if action == 'start' else 300 if action == 'prepare' else 30 if action == 'read' else args.get('timeoutSeconds', 60) + 150)
    try:
        process.stdin.write(json.dumps(payload).encode())
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, errors)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError('The backend response timed out. The effect may still be running; inspect it without replay.')
                for selected, _ in selector.select(min(1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(selected.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(selected.fileobj)
                    else:
                        selected.data.extend(chunk[:max(0, OUTPUT_LIMIT - len(selected.data))])
                        if selected.data is output and len(output) >= OUTPUT_LIMIT:
                            raise ValueError('The backend response exceeded its bound; the outcome is unknown.')
        drained = True
        process.wait(timeout=max(1, deadline - time.monotonic()))
        if process.returncode:
            raise ValueError('The managed backend exited without a valid result; inspect the retained environment.')
        value = json.loads(output)
        if not isinstance(value, dict) or 'ok' not in value:
            raise ValueError('The managed backend returned an invalid receipt.')
        return value
    finally:
        if process.poll() is None or not drained:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def effect(app, row, action, args, config, request, admit):
    home, sid = app.data_dir, row['sessionId']
    lock = FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0)
    locked = admitted = False
    try:
        lock.acquire()
        locked = True
        admit()
        row = get(home, sid, row['id'])
        if action != 'start' and row['revision'] != args['expectedRevision']:
            raise ValueError('The environment changed. Inspect its current revision.')
        if row['backendKey'] != config_key(config):
            raise ValueError('The approved backend changed. Restore its exact configuration before managing this environment.')
        if action == 'start':
            row = storage.allocate(home, row, admit)
        elif not (action == 'remove' and not row.get('backendAdmitted')) and storage.observe(home, row)['observation'] != 'present':
            raise ValueError('The retained environment directory changed; no external action was attempted.')
        if action == 'execute' and row.get('environmentState') != 'running':
            raise ValueError('Inspect a running environment before executing a command.')
        if action == 'remove' and row.get('environmentState') not in {'stopped', 'not-started'}:
            raise ValueError('Stop and inspect this environment before removing it.')
        if row['status'] == 'retired':
            raise ValueError('This environment has already been removed.')
        admit()
        row = save(home, row, pendingRequest=request['requestId'],
                   environmentState={'start': 'preparing', 'execute': 'running', 'stop': 'stopping', 'remove': 'removing'}[action])
        if action == 'start':
            prepared = invoke(config, home, row, 'prepare', args)
            if not prepared.get('ok'):
                result = {**prepared, 'status': 'not-started'}
            else:
                row = save(home, row, universeId=prepared['universeId'], snapshot=prepared['snapshot'])
                admit()
                # Identity and exact source provenance precede any Docker effect.
                row = save(home, row, backendAdmitted=True, environmentState='starting')
                admitted = True
                result = invoke(config, home, row, 'start', args)
        elif action == 'remove' and not row.get('backendAdmitted'):
            result = {'ok': True, 'status': 'removed', 'message': 'No environment was launched. All local source and evidence files were retained.'}
        else:
            admitted = True
            result = invoke(config, home, row, action, args)
        changes = {key: result[key] for key in ('universeId', 'snapshot', 'services') if result.get(key) is not None}
        state = 'completed' if result.get('ok') else 'outcome_unknown' if admitted else 'failed'
        if action == 'execute':
            environment_state = 'running' if result.get('ok') else 'unknown'
            if result.get('ok') and result.get('result', {}).get('exit_code', 0) != 0:
                state = 'failed'
        else:
            environment_state = result.get('status', 'unknown')
        if action == 'remove' and result.get('ok') and environment_state == 'removed':
            changes['status'] = 'retired'
        request = app.operations.requests.finish(request, state, resourceId=row['id'], result=result,
            message=result.get('message', 'The result is retained; this request will not replay.'))
        row = save(home, row, **changes, pendingRequest=None, environmentState=environment_state,
                   lastOperation=request, reason=result.get('message'))
        return {'resource': public(home, row), 'request': request}
    except BaseException as exc:
        # Hold the physical owner lock through final persistence. A rejected
        # competing request must never change the active owner's resource.
        request = app.operations.requests.finish(request, 'outcome_unknown' if admitted else 'failed', resourceId=row['id'],
            message=('The operation did not finish with a confirmed result. Inspect retained state; do not repeat it automatically.'
                     if admitted else 'The operation was not admitted. Read the current environment and authority before trying a new request.'))
        if locked and (admitted or action == 'start'):
            try:
                row = get(home, sid, row['id'])
                row = save(home, row, pendingRequest=None, environmentState='unknown' if row.get('backendAdmitted') else 'not-started',
                           lastOperation=request, reason=request['message'])
            except (OSError, ValueError, sqlite3.Error):
                pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {'resource': public(home, row), 'request': request}
    finally:
        if locked:
            lock.release()


def read(app, sid, config):
    home = app.data_dir
    with closing(storage.connect(home)) as db:
        rows = [json.loads(value[0]) for value in db.execute(
            "SELECT body FROM allocations WHERE root=? AND json_extract(body,'$.kind')=? ORDER BY rowid DESC LIMIT 10", (sid, KIND))]
    result = []
    for row in rows:
        if row['status'] != 'retired':
            try:
                with FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0):
                    row = get(home, sid, row['id'])
                    if not row.get('backendAdmitted') and row.get('environmentState') == 'preparing':
                        preparation = app.operations.requests.read(sid, row['commandId'])
                        if preparation['state'] in {'failed', 'outcome_unknown'}:
                            row = save(home, row, environmentState='not-started', lastOperation=preparation,
                                reason='Preparation ended before any environment launch. Local source and evidence are retained.')
                    if row.get('pendingRequest'):
                        request = app.operations.requests.read(sid, row['pendingRequest'])
                        if request['state'] == 'admitting':
                            request = app.operations.requests.finish(request, 'outcome_unknown',
                                message='The operation owner stopped before recording completion. Nothing was replayed.')
                        row = save(home, row, pendingRequest=None, environmentState='unknown' if row.get('backendAdmitted') else 'not-started',
                            lastOperation=request,
                            reason='The operation owner stopped before recording completion. Nothing was replayed.')
                    if config and row.get('backendAdmitted') and row['backendKey'] == config_key(config) and storage.observe(home, row)['observation'] == 'present':
                        try:
                            observed = invoke(config, home, row, 'read', {})
                            if observed.get('ok'):
                                changes = {key: observed[key] for key in ('universeId', 'services', 'snapshot') if key in observed and observed[key] != row.get(key)}
                                if observed.get('status') != row.get('environmentState'):
                                    changes['environmentState'] = observed.get('status', 'unknown')
                                if changes:
                                    row = save(home, row, **changes)
                                row = {**row, 'observation': {'status': 'observed', 'observedAt': time.time()}}
                            else:
                                row = {**row, 'observation': {'status': 'unverified'}, 'reason': observed.get('message', 'Current backend state could not be verified.')}
                        except (OSError, ValueError, TimeoutError):
                            row = {**row, 'observation': {'status': 'unverified'}, 'reason': 'Current backend state could not be verified. Retained results are historical.'}
                    elif row.get('backendAdmitted'):
                        row = {**row, 'observation': {'status': 'unverified'}, 'reason': 'The approved backend is unavailable. Retained results are historical.'}
            except LockTimeout:
                row = {**row, 'observation': {'status': 'operation-in-progress'}}
        result.append(public(home, row))
    return result


async def dispatch(app, action, args, origin, caller, authority):
    from .service import AppError
    from .collaboration import PRINCIPAL
    sid, user = args['sessionId'], origin in {'ui', 'user'}
    if not user and sid != caller:
        raise AppError('Test environments belong to the calling conversation.', 403)
    source = app._session(sid, hydrate=False)
    principal = source.get('runtimeSessionId') or source.get('nativeIdentity') or sid
    if source.get('sessionKind', 'root') != 'root' or not user and PRINCIPAL.get() != principal:
        raise AppError('Test environments require their host-bound root owner.', 403)
    workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
    if not workspace or source['workspace'] != workspace['path']:
        raise AppError('Choose this conversation’s current workspace.', 403)
    captured = tuple(source.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision'))
    config, availability, reason = configuration(app)
    def guard():
        current = app._session(sid, hydrate=False)
        if captured != tuple(current.get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')):
            raise AppError('The task moved or stopped before environment admission.', 409)
        if not user:
            app.collaboration.active_binding(current)
            if blocked := app.assignments.blocked(current):
                raise AppError(blocked, 409)
        app.configuration_authority.check(authority)
        current_config, _, _ = configuration(app)
        if current_config != config:
            raise AppError('The approved test environment configuration changed.', 409)
    try:
        if action.endswith('.read'):
            items = await asyncio.to_thread(read, app, sid, config)
            if captured != tuple(app._session(sid, hydrate=False).get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')):
                raise AppError('The task moved; refresh its environments.', 409)
            return {'accepted': True, 'result': {'capability': {'status': availability, 'reason': reason,
                'profiles': [{'id': PROFILE, 'label': 'Offline Linux Python'}],
                'source': source.get('workingDirectory') or source['workspace']}, 'items': items, 'nextOffset': None}}
        guard()
        if config is None:
            raise AppError(reason, 409)
        actor = {'sessionId': sid, 'principal': principal}
        previous = app.operations.requests.existing(sid, action, args, actor)
        if previous:
            row = get(app.data_dir, sid, previous['resourceId']) if previous.get('resourceId') else None
            return {'accepted': True, 'duplicate': True, 'result': {'resource': public(app.data_dir, row) if row else None, 'request': previous}}
        if not action.endswith('.start'):
            row = await asyncio.to_thread(get, app.data_dir, sid, args['id'])
            if row['workspaceId'] != workspace['id'] or not user and row['owner']['principal'] != principal:
                raise AppError('The environment belongs to another execution owner.', 403)
            if row['revision'] != args['expectedRevision']:
                raise AppError('The environment changed. Inspect its current revision.', 409)
        guard()
        request, fresh = app.operations.requests.begin(sid, action, args, actor)
        if not fresh:
            return {'accepted': True, 'duplicate': True, 'result': {'request': request, 'resource': None}}
        try:
            if action.endswith('.start'):
                row = await asyncio.to_thread(prepare, app.data_dir, sid, workspace['id'], actor,
                    source.get('workingDirectory') or source['workspace'], config_key(config), args['requestId'], config.get('installationId'))
            request = app.operations.requests.finish(request, 'admitting', resourceId=row['id'])
            guard()
        except BaseException:
            app.operations.requests.finish(request, 'failed', message='Admission did not complete; no backend command was started.')
            raise
        loop = asyncio.get_running_loop()
        async def locked_guard():
            async with app.lock:
                guard()
        def admit():
            asyncio.run_coroutine_threadsafe(locked_guard(), loop).result(timeout=10)
        result = await app.worktrees.finish_thread(effect, app, row, action.rsplit('.', 1)[1], args, config, request, admit)
        app.operations.notify()
        return {'accepted': True, 'result': result}
    except (OSError, ValueError, sqlite3.Error, LockTimeout) as exc:
        raise AppError(str(exc), 409) from None
