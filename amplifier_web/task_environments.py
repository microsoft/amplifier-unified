"""Opt-in, task-owned test environments using a pinned managed DTU core.

The resource ledger owns environments; the existing operation request journal
owns command admission. Neither a reconnect nor an uncertain result replays an
effect. The isolated helper imports DTU from its managed installation only.
"""
from __future__ import annotations

import asyncio
from contextlib import closing, nullcontext
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
from . import test_environment_configs as backend_configs
from . import test_environment_preparation as dependencies
from . import test_environment_profiles as profiles
from . import test_environment_recovery as recovery
from . import test_environment_setup as setup

REPOSITORY = 'https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe'
DTU_COMMIT = '8f644b3d8d8b89f0bb3ae9853bb531017dc98a04'
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
        **setup.definitions(schema, string),
        'workspace.environments.read': ('Inspect this task’s test environments, retained results and current backend availability. Does not launch, resume or replay work.', schema(scope)),
        'workspace.environments.start': ('Copy this task’s current Git working area into one approved isolated test environment and prepare locked dependencies for the selected profile. No original writable mounts or injected credentials. Keep requestId stable; unknown launches never replay.', schema({**request, 'profileId': {'enum': [item['id'] for item in profiles.catalog()]}})),
        'workspace.environments.execute': ('Run an approved command in this task’s environment, retaining bounded output. Keep requestId stable. Timeout means the command may still be running, not cancelled.', schema({**owned, 'command': {**string(16384), 'minLength': 1}, 'timeoutSeconds': {'type': 'integer', 'minimum': 1, 'maximum': 300}}, [*owned, 'command'])),
        'workspace.environments.stop': ('Stop this task’s containers at an exact revision and observe the result. Keeps source, artifacts, containers and volumes; never implies removal.', schema(owned)),
        'workspace.environments.remove': ('Remove an already stopped owned environment after preserving its source and artifacts. Optional full retention preserves the complete Incus environment; Docker supports files only. Unknown export or teardown never replays.', schema({**owned, 'retentionMode': {'enum': ['files', 'full']}}, list(owned))),
        'workspace.environments.reconcile': ('Inspect this exact owned environment and repair its metadata without starting, stopping, deleting or replaying backend work. Retire ownership only after the original backend proves physical absence; earlier uncertain operations stay uncertain.', schema(owned)),
        'workspace.environments.exportArtifacts': ('Recover a bounded, verified artifacts archive from this exact stopped owned environment. No workspace export, restart, command execution or deletion. Does not satisfy preservation required for removal. Unknown exports never replay.', schema(owned)),
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
        return validate_configuration_value(app, value)
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'unavailable', 'The managed Linux test host needs setup, or its approved backend configuration changed.'


def validate_configuration_value(app, value):
    """Qualify operator-owned fields independently of the currently selected file."""
    if not isinstance(value, dict):
        raise ValueError('The operator configuration must be an object.')
    if value.get('enabled') is not True:
        return None, 'disabled', 'Test environments are disabled on this host.'
    if set(value) - {'enabled', 'installationId', 'commit', 'backend', 'incus', 'dockerHost', 'image', 'maxRunning', 'maxInstances', 'minFreeBytes', 'retentionMode'}:
        raise ValueError('The operator configuration contains unsupported settings.')
    if 'retentionMode' in value and value['retentionMode'] not in {'files', 'full'}:
        raise ValueError('Choose files or full environment retention.')
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
    backend = value.get('backend', 'docker')
    if backend == 'incus':
        if any(key in value for key in ('dockerHost', 'image', 'maxRunning', 'maxInstances', 'minFreeBytes')):
            raise ValueError('Incus settings belong in its explicit backend configuration.')
        from .incus_environment_worker import validate_configuration
        incus = validate_configuration(value.get('incus'))
        return {**value, 'backend': 'incus', 'incus': incus, 'python': str(python)}, 'ready', 'Isolated Linux environment on the configured Incus host; offline network and no host source mounts.'
    if backend != 'docker' or 'incus' in value:
        raise ValueError('Choose one explicitly configured test backend.')
    if value.get('retentionMode') == 'full':
        raise ValueError('Docker supports source and artifact retention; full environment retention requires Incus.')
    docker = value.get('dockerHost', '')
    if not isinstance(docker, str) or not docker.startswith('unix:///') or '\x00' in docker or '..' in Path(docker[7:]).parts:
        raise ValueError('Only an explicitly configured local Docker Unix socket is supported.')
    image = value.get('image', APPROVED_IMAGE)
    if image != APPROVED_IMAGE:
        raise ValueError('Only the app’s approved Python image digest is supported.')
    for key in ('maxRunning', 'maxInstances', 'minFreeBytes'):
        if key in value and (type(value[key]) is not int or value[key] < 0):
            raise ValueError('Capacity policy values must be nonnegative integers.')
    return {**value, 'image': image, 'python': str(python),
        'admissionRoot': str(Path.home() / '.cache' / 'amplifier-test-admission')}, 'ready', 'Isolated Linux Python environment; offline network and no host source mounts.'


def config_key(value):
    return backend_configs.key(value)


def resolve_configuration(app, row, active):
    return backend_configs.resolve(app, row, active)


def public(home, row):
    return {key: value for key, value in {
        'id': row['id'], 'sessionId': row['sessionId'], 'workspaceId': row['workspaceId'],
        'revision': row['revision'], 'profileId': row['profileId'],
        'status': row.get('environmentState', 'unknown'), 'universeId': row.get('universeId'),
        'source': row['source'], 'snapshot': row.get('snapshot'), 'sourceReady': row.get('sourceReady'), 'services': row.get('services', []),
        'lastOperation': row.get('lastOperation'), 'lastRecovery': row.get('lastRecovery'),
        'artifactExport': row.get('artifactExport'), 'lastArtifactExport': row.get('lastArtifactExport'),
        'preparation': dependencies.status(storage.directory(home, row)) if row.get('preparationPending') or row.get('preparedDependencies') else None,
        'preparationCleanup': row.get('preparationCleanup'),
        'reason': row.get('reason'), 'observation': row.get('observation'),
        'recovery': row.get('recovery', {'available': False, 'reason': 'Refresh to inspect the retained recovery backend.'}),
        'retention': {'path': str(storage.directory(home, row)), 'preserved': True,
                      'fullAvailable': row.get('backendType') == 'incus',
                      **({'mode': row['retentionMode']} if row.get('retentionMode') is not None else {}),
                      'policy': 'Stop retains container data. Remove exports source and artifacts before deleting owned containers and volumes. Retained host files are never deleted here.',
                      **({'accounting': row['retainedFiles']} if row.get('retainedFiles') is not None else {})},
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


def prepare(home, sid, workspace, actor, source, backend_key, request_id, installation=None, backend_config=None, profile_id=PROFILE):
    profiles.get(profile_id)
    with installation_lock(home, installation) if installation else nullcontext(), closing(storage.connect(home)) as db, db:
        if backend_config is not None and backend_configs.retain(home, backend_config) != backend_key:
            raise ValueError('The approved backend configuration identity changed before allocation.')
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute("SELECT body FROM allocations WHERE root=? AND status!='retired' AND json_extract(body,'$.kind')=? LIMIT 1", (sid, KIND)).fetchone()
        if prior:
            row = json.loads(prior[0])
            # A crash can occur after reserving local storage but before the
            # queue links it. Only the same unadmitted intent may recover it.
            if (not row.get('backendAdmitted') and row.get('commandId') == request_id
                    and row['owner'] == actor and row['source'] == source
                    and row.get('profileId', PROFILE) == profile_id
                    and row['backendKey'] == backend_key and row['workspaceId'] == workspace):
                return row
            raise ValueError('This task already owns an environment. Inspect or remove it before starting another.')
        if db.execute("SELECT COUNT(*) FROM allocations WHERE status!='retired'").fetchone()[0] >= storage.MAX_ACTIVE:
            raise ValueError('The resource owner capacity has been reached.')
        row = {'id': str(uuid.uuid4()), 'kind': KIND, 'workspaceId': workspace, 'sessionId': sid,
               'owner': actor, 'source': source, 'profileId': profile_id, 'backendKey': backend_key,
               'backendInstallation': installation,
               'backendConfigRetained': backend_config is not None,
               **({'retentionMode': backend_config.get('retentionMode', 'files'),
                   'backendType': backend_config.get('backend', 'docker')} if backend_config is not None else {}),
               'status': 'prepared', 'environmentState': 'preparing', 'revision': 1,
               'createdAt': time.time(), 'commandId': request_id,
               'launchOperationId': str(uuid.uuid4())}
        storage.save(db, row)
    return row


def invoke(config, home, row, action, args):
    """Bound helper output and lifetime; killing a helper is not command cancellation."""
    if action == 'execute' and args.get('durableJob') is True:
        action = 'job_start'
    folder = (home / 'workspace-resources' / 'backend-probes' / config_key(config)
              if action == 'capacity' else storage.directory(home, row))
    if action == 'capacity':
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {'action': action, 'root': str(folder), 'source': row.get('source'),
               'backendKey': row.get('backendKey'),
               'expectedBackendIdentity': row.get('backendIdentity'),
               'retentionMode': args.get('retentionMode', config.get('retentionMode', 'files')) if action == 'remove' else config.get('retentionMode', 'files'),
               'snapshot': row.get('snapshot'),
               'operationId': row.get('launchOperationId'), 'universeId': row.get('universeId'),
               'dockerHost': config.get('dockerHost'), 'image': config.get('image'),
               'rootOwner': row.get('sessionId'),
               'requestId': args.get('requestId'), 'command': args.get('command'),
               'jobId': args.get('jobId', args.get('requestId')),
               'profileId': row.get('profileId', PROFILE),
               'timeoutSeconds': args.get('timeoutSeconds', 60),
               **{key: config[key] for key in ('maxRunning', 'maxInstances', 'minFreeBytes', 'admissionRoot') if key in config}}
    helper = Path(__file__).with_name('test_environment_worker.py')
    if config.get('backend') == 'incus':
        helper = Path(__file__).with_name('incus_environment_worker.py')
        payload['incus'] = config['incus']
    if row.get('preparedDependencies'):
        prepared = dependencies.validate_receipt(row['preparedDependencies'], row['snapshot'], row['profileId'])
        if prepared['backend'] != config.get('backend', 'docker'):
            raise ValueError('The prepared dependencies belong to a different backend.')
        if prepared['backend'] == 'incus':
            payload['incus'] = {**config['incus'], 'image': prepared['image']}
        else:
            payload['image'] = prepared['image']
    environment = {'PATH': os.defpath, 'HOME': str(folder), 'LANG': 'C.UTF-8'}
    process = subprocess.Popen([config['python'], '-I', str(helper)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=folder, env=environment, start_new_session=True)
    output, errors = bytearray(), bytearray()
    drained = False
    deadline = time.monotonic() + (450 if action == 'exportArtifacts' else 650 if action == 'start' else 650 if action == 'remove' and config.get('backend') == 'incus' else 300 if action == 'prepare' else 30 if action in {'read', 'capacity', 'reconcile', 'job_start', 'job_read', 'job_cancel'} else args.get('timeoutSeconds', 60) + 150)
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


def capacity(app, config):
    try:
        result = invoke(config, app.data_dir, {}, 'capacity', {})
        return result['capacity'] if result.get('ok') else {'available': False, 'blockers': [{'code': 'backend_unavailable'}]}
    except (OSError, ValueError, TimeoutError):
        return {'available': False, 'blockers': [{'code': 'capacity_unknown'}]}


def confirmed_result(row, request):
    return (request.get('state') in {'completed', 'failed'}
            and request.get('resourceId') == row['id']
            and request.get('sessionId') == row['sessionId']
            and request.get('action') in {'workspace.environments.' + action for action in ('start', 'execute', 'stop', 'remove', 'exportArtifacts')}
            and isinstance(request.get('result'), dict))


def project_result(home, row, request):
    """Rebuild resource metadata from its exact receipt without another effect."""
    result = request['result']
    action = request['action'].rsplit('.', 1)[-1]
    if action == 'exportArtifacts':
        # Keep the prior failed/unknown removal visible and preserve ownership.
        changes = {'pendingRequest': None, 'lastArtifactExport': request}
        if result.get('ok') and result.get('artifactExport', {}).get('verified') is True:
            changes['artifactExport'] = {**result['artifactExport'], 'requestId': request['requestId'],
                                         'archives': result['archives'] if 'archives' in result else []}
        return save(home, row, **changes)
    changes = {key: result[key] for key in ('universeId', 'snapshot', 'services', 'sourceReady') if result.get(key) is not None}
    if action == 'start' and (result.get('admissionDenied') or result.get('effectAdmitted') is False):
        changes['backendAdmitted'] = False
    environment_state = ('running' if result.get('ok') else 'unknown') if action == 'execute' else result.get('status', 'unknown')
    if action == 'remove' and result.get('ok') and environment_state == 'removed':
        changes['status'] = 'retired'
        if isinstance(result.get('retention'), dict) and result['retention'].get('verified') is True:
            changes['retainedFiles'] = result['retention']
    if action == 'execute' and isinstance(result.get('job'), dict):
        changes['jobObservation'] = result['job']
        environment_state = 'running'
    changes.update(pendingRequest=request['requestId'] if request['state'] == 'running' else None,
                   environmentState=environment_state, lastOperation=request, reason=result.get('message'))
    return save(home, row, **changes)


def cleanup_preparation(home, row, config):
    try:
        result = dependencies.cleanup(config, storage.directory(home, row))
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        result = {'complete': False, 'status': 'unknown', 'reason': 'The owned preparation cleanup could not be verified.'}
    row = save(home, row, preparationCleanup=result, preparationPending=not result['complete'])
    return row, result


def resume_preparation_removal(app, row, config):
    """Finish a recorded guest removal without repeating any guest operation."""
    home, sid = app.data_dir, row['sessionId']
    with FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0):
        row = get(home, sid, row['id'])
        if not row.get('guestRemoved') or not row.get('preparationPending'):
            return row
        if config_key(config) != row['backendKey'] or storage.observe(home, row)['observation'] != 'present':
            raise ValueError('The retained preparation cleanup owner changed.')
        request = app.operations.requests.read(sid, row.get('pendingRequest') or row['lastOperation']['requestId'])
        if (request.get('action') != 'workspace.environments.remove' or request.get('resourceId') != row['id']
                or request.get('sessionId') != sid):
            raise ValueError('The guest removal receipt does not match its cleanup owner.')
        result = row.get('guestRemoval', {})
        if not result.get('ok') or result.get('status') != 'removed':
            raise ValueError('The retained guest removal is unverified.')
        row, cleanup = cleanup_preparation(home, row, config)
        result = {**result, 'preparationCleanup': cleanup}
        if not cleanup['complete']:
            result.update(ok=False, status='not-started', message='The guest is absent; dependency cleanup remains unverified.')
        request = app.operations.requests.finish(request, 'completed' if cleanup['complete'] else 'outcome_unknown',
            resourceId=row['id'], result=result, message='Observed the retained guest removal and completed only its dependency cleanup.')
        return project_result(home, row, request)


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
        if row.get('pendingRequest') and row['pendingRequest'] != request['requestId']:
            prior = app.operations.requests.read(sid, row['pendingRequest'])
            if confirmed_result(row, prior):
                row = project_result(home, row, prior)
        if row['backendKey'] != config_key(config):
            raise ValueError('The approved backend changed. Restore its exact configuration before managing this environment.')
        if action != 'start':
            row = recovery.bind_configuration(app, row, config)
        if action == 'start':
            row = storage.allocate(home, row, admit)
        elif not (action == 'remove' and not row.get('backendAdmitted')) and storage.observe(home, row)['observation'] != 'present':
            raise ValueError('The retained environment directory changed; no external action was attempted.')
        if action == 'execute' and row.get('environmentState') != 'running':
            raise ValueError('Inspect a running environment before executing a command.')
        if action == 'execute' and row.get('durableJob'):
            raise ValueError('This environment already owns a durable check. Observe or cancel that exact job.')
        if action == 'remove' and row.get('environmentState') not in {'stopped', 'not-started'}:
            raise ValueError('Stop and inspect this environment before removing it.')
        if action == 'exportArtifacts' and (not row.get('backendAdmitted') or row.get('guestRemoved')
                                           or row.get('environmentState') != 'stopped'):
            raise ValueError('Artifact recovery requires a stopped owned environment.')
        if row['status'] == 'retired':
            raise ValueError('This environment has already been removed.')
        admit()
        row = save(home, row, pendingRequest=request['requestId'],
                   environmentState={'start': 'preparing', 'execute': 'running', 'stop': 'stopping',
                                     'remove': 'removing', 'exportArtifacts': 'stopped'}[action])
        if action == 'start':
            prepared = invoke(config, home, row, 'prepare', args)
            if not prepared.get('ok'):
                result = {**prepared, 'status': 'not-started'}
            else:
                identity = recovery.backend_identity(prepared.get('backendIdentity'))
                if identity['backend'] != config.get('backend', 'docker'):
                    raise ValueError('The prepared environment belongs to a different backend.')
                row = save(home, row, universeId=prepared['universeId'], snapshot=prepared['snapshot'], backendIdentity=identity)
                if profiles.get(row['profileId'])['preparationRequired']:
                    # Persist ownership before the networked builder can exist.
                    row = save(home, row, preparationPending=True)
                    prepared_dependencies = dependencies.prepare(config, storage.directory(home, row),
                        row['snapshot'], row['profileId'], request['requestId'])
                    row = save(home, row, preparedDependencies=prepared_dependencies)
                admit()
                # Identity and exact source provenance precede any Docker effect.
                row = save(home, row, backendAdmitted=True, environmentState='starting')
                admitted = True
                result = invoke(config, home, row, 'start', args)
        elif action == 'remove' and (not row.get('backendAdmitted') or row.get('guestRemoved')):
            result = row.get('guestRemoval') or {'ok': True, 'status': 'removed', 'message': 'The test guest is absent. All local source and evidence files were retained.'}
        else:
            admitted = True
            if action == 'execute' and args.get('durableJob') is True:
                # Save the exact identity/command before admission. A crash in
                # either commit gap may be observed, but never re-admitted.
                row = save(home, row, durableJob={key: args[key] for key in ('requestId', 'command', 'timeoutSeconds')})
                request = app.operations.requests.finish(request, 'running', resourceId=row['id'],
                    message='The durable guest admission is being observed; it will not be replayed.')
                result = invoke(config, home, row, action, args)
            else:
                result = invoke(config, home, row, action, args)
        if action == 'remove' and result.get('ok') and result.get('status') == 'removed':
            # Commit the guest result before removing its prepared image. A
            # restart can finish image cleanup without repeating guest teardown.
            row = save(home, row, guestRemoved=True, guestRemoval=result)
            if row.get('preparationPending'):
                row, cleanup = cleanup_preparation(home, row, config)
                result = {**result, 'preparationCleanup': cleanup}
                if not cleanup['complete']:
                    result.update(ok=False, status='not-started', message='The test guest was removed; its owned dependency preparation still needs cleanup.')
        not_admitted = result.get('admissionDenied') or result.get('effectAdmitted') is False
        if action == 'start' and not_admitted and row.get('preparationPending'):
            row, cleanup = cleanup_preparation(home, row, config)
            result = {**result, 'preparationCleanup': cleanup}
            if not cleanup['complete']:
                result.update(admissionDenied=False, status='not-started', message='Launch was declined; owned dependency preparation cleanup is still unverified.')
                result.pop('effectAdmitted', None)
                not_admitted = False
        state = 'completed' if result.get('ok') else 'failed' if not_admitted else 'outcome_unknown' if admitted else 'failed'
        if action == 'execute' and result.get('ok') and result.get('result', {}).get('exit_code', 0) != 0:
            state = 'failed'
        if action == 'execute' and args.get('durableJob') is True:
            state = durable_state(result)
        request = app.operations.requests.finish(request, state, resourceId=row['id'], result=result,
            message=result.get('message', 'The result is retained; this request will not replay.'))
        row = project_result(home, row, request)
        return {'resource': public(home, row), 'request': request}
    except BaseException as exc:
        # Hold the physical owner lock through final persistence. A rejected
        # competing request must never change the active owner's resource.
        # A returned terminal commit is already proof. A subsequent read fault
        # cannot authorize overwriting that receipt with an unknown outcome.
        recorded = request if confirmed_result(row, request) else {}
        if not recorded:
            try:
                recorded = app.operations.requests.read(sid, request['requestId'])
            except (OSError, ValueError, sqlite3.Error):
                recorded = {}
        if confirmed_result(row, recorded):
            # The receipt and resource projection have separate commit points.
            # Retry only the projection; never downgrade or replay a known effect.
            request = recorded
            try:
                if locked:
                    row = project_result(home, get(home, sid, row['id']), request)
            except (OSError, ValueError, sqlite3.Error):
                row = {**row, 'observation': {'status': 'unverified'},
                       'reason': 'The operation result is saved, but environment metadata could not be updated. Inspect again to repair the metadata; the operation will not replay.'}
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {'resource': public(home, row), 'request': request}
        preparation_cleanup = None
        if locked and action == 'start' and not admitted:
            row = get(home, sid, row['id'])
            if row.get('preparationPending'):
                row, preparation_cleanup = cleanup_preparation(home, row, config)
        request = app.operations.requests.finish(request, 'outcome_unknown' if admitted or row.get('preparationPending') else 'failed', resourceId=row['id'],
            result={'ok': False, 'status': 'not-started' if not admitted else 'unknown',
                    'preparationCleanup': preparation_cleanup, 'message': str(exc)[:500]},
            message=('The operation did not finish with a confirmed result. Inspect retained state; do not repeat it automatically.'
                     if admitted else 'The operation was not admitted. Read the current environment and authority before trying a new request.'))
        if locked and (admitted or action == 'start'):
            try:
                row = get(home, sid, row['id'])
                if action == 'exportArtifacts':
                    row = save(home, row, pendingRequest=None, lastArtifactExport=request)
                else:
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


def durable_state(result):
    job = result.get('job', {})
    if not result.get('ok') or job.get('state') == 'unknown' or not job:
        return 'outcome_unknown'
    if job.get('terminal'):
        return {'passed': 'completed', 'failed': 'failed', 'timed_out': 'failed', 'cancelled': 'cancelled'}.get(job['state'], 'outcome_unknown')
    return 'running'


def observe_job(app, row, config, *, cancel=False):
    """Observe/cancel one admitted guest identity, without replay or long locks."""
    home, sid = app.data_dir, row['sessionId']
    with FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0):
        row = get(home, sid, row['id'])
        job = row.get('durableJob')
        if not job or row['status'] == 'retired':
            raise ValueError('No live durable job is recorded for this owned environment.')
        request = app.operations.requests.read(sid, job['requestId'])
        if request.get('resourceId') != row['id'] or request.get('action') != 'workspace.environments.execute':
            raise ValueError('The durable operation belongs to a different environment.')
        if request.get('result', {}).get('job', {}).get('terminal') is True:
            return request
        if row['backendKey'] != config_key(config):
            raise ValueError('The original backend configuration is required for job observation.')
        row = recovery.bind_configuration(app, row, config)
        if storage.observe(home, row)['observation'] != 'present':
            raise ValueError('The retained environment directory changed.')
        result = invoke(config, home, row, 'job_cancel' if cancel else 'job_read', job)
        state = durable_state(result)
        if request.get('state') == state and request.get('result') == result:
            return request
        request = app.operations.requests.finish(request, state, resourceId=row['id'], result=result,
            message='Observed the exact durable guest job; no command was repeated.')
        project_result(home, row, request)
        return request


def cancel_preparation(app, row):
    """Publish cancellation intent without waiting on the builder's long lock."""
    row = get(app.data_dir, row['sessionId'], row['id'])
    if row.get('profileId', PROFILE) == PROFILE or row.get('backendAdmitted'):
        return
    if storage.observe(app.data_dir, row)['observation'] != 'present':
        return  # Admission checks the saved queue cancellation before allocation.
    folder = storage.directory(app.data_dir, row) / 'preparation'
    folder.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('The owned preparation directory changed.')
        try:
            fd = os.open('cancel.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        except FileExistsError:
            return
        with os.fdopen(fd, 'w') as stream:
            json.dump({'cancelRequested': True}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read(app, sid, config):
    home = app.data_dir
    with closing(storage.connect(home)) as db:
        rows = [json.loads(value[0]) for value in db.execute(
            "SELECT body FROM allocations WHERE root=? AND json_extract(body,'$.kind')=? ORDER BY rowid DESC LIMIT 10", (sid, KIND))]
    result, configurations = [], {}
    for row in rows:
        selected = None
        availability = {'available': False, 'reason': 'This environment is already retired.'}
        if row['status'] != 'retired':
            identity = (row['backendKey'], bool(row.get('backendConfigRetained')))
            if identity not in configurations:
                try:
                    configurations[identity] = (resolve_configuration(app, row, config), {'available': True})
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    configurations[identity] = (None, {'available': False, 'reason': str(exc)})
            selected, availability = configurations[identity]
        if row['status'] != 'retired':
            try:
                with FileLock(str(home / 'workspace-resources' / ('environment-' + row['id'] + '.lock')), timeout=0):
                    row = get(home, sid, row['id'])
                    try:
                        row = recovery.repair(app, row)
                    except (OSError, ValueError, sqlite3.Error):
                        row = {**row, 'observation': {'status': 'unverified'},
                               'reason': 'The saved recovery result is retained, but its metadata could not be repaired. Refresh to inspect it again.'}
                        result.append(public(home, {**row, 'recovery': availability}))
                        continue
                    if row['status'] == 'retired':
                        result.append(public(home, {**row, 'recovery': {'available': False, 'reason': 'Physical absence has been confirmed.'}}))
                        continue
                    if not row.get('backendAdmitted') and row.get('environmentState') == 'preparing':
                        preparation = app.operations.requests.read(sid, row['commandId'])
                        if preparation['state'] in {'failed', 'outcome_unknown'}:
                            row = save(home, row, environmentState='not-started', lastOperation=preparation,
                                reason='Preparation ended before any environment launch. Local source and evidence are retained.')
                    if row.get('pendingRequest'):
                        request = app.operations.requests.read(sid, row['pendingRequest'])
                        if confirmed_result(row, request):
                            row = project_result(home, row, request)
                        elif (request['state'] == 'running'
                              and row.get('durableJob', {}).get('requestId') == request['requestId']):
                            # The guest, not this short host helper, owns the
                            # running process. A passive list is not a crash.
                            pass
                        else:
                            if request['state'] == 'admitting':
                                request = app.operations.requests.finish(request, 'outcome_unknown',
                                    message='The operation owner stopped before recording completion. Nothing was replayed.')
                            if request['action'] == 'workspace.environments.exportArtifacts':
                                row = save(home, row, pendingRequest=None, lastArtifactExport=request)
                            else:
                                row = save(home, row, pendingRequest=None, environmentState='unknown' if row.get('backendAdmitted') else 'not-started',
                                    lastOperation=request,
                                    reason='The operation owner stopped before recording completion. Nothing was replayed.')
                    if row['status'] == 'retired':
                        result.append(public(home, {**row, 'recovery': {'available': False, 'reason': 'This environment is already retired.'}}))
                        continue
                    if selected and row.get('backendAdmitted') and storage.observe(home, row)['observation'] == 'present':
                        try:
                            row = recovery.bind_configuration(app, row, selected)
                            observed = invoke(selected, home, row, 'read', {})
                            if observed.get('ok'):
                                changes = {key: observed[key] for key in ('universeId', 'services', 'snapshot', 'sourceReady') if key in observed and observed[key] != row.get(key)}
                                if observed.get('status') != row.get('environmentState'):
                                    changes['environmentState'] = observed.get('status', 'unknown')
                                if observed.get('message') and observed['message'] != row.get('reason'):
                                    changes['reason'] = observed['message']
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
        result.append(public(home, {**row, 'recovery': availability}))
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
    historical = action.rsplit('.', 1)[-1] in {'stop', 'remove', 'reconcile', 'exportArtifacts'}
    row = None
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
        if historical and row is not None:
            current_config = resolve_configuration(app, row, current_config)
        if current_config != config:
            raise AppError('The approved test environment configuration changed.', 409)
    try:
        actor = {'sessionId': sid, 'principal': principal}
        if action in {'workspace.environments.setup', 'workspace.environments.setup.reconcile', 'workspace.environments.setup.cleanup'}:
            async def setup_guard():
                async with app.lock:
                    guard()
            if action == 'workspace.environments.setup.reconcile':
                value = await setup.reconcile(app, args, actor)
            else:
                guard()
                operation = setup.request if action == 'workspace.environments.setup' else setup.cleanup
                value = await operation(app, args, actor, guard=setup_guard)
            return {'accepted': True, 'result': value}
        if action.endswith('.read'):
            items = await asyncio.to_thread(read, app, sid, config)
            setup_state = await setup.read(app)
            if captured != tuple(app._session(sid, hydrate=False).get(key) for key in ('workspace', 'workingDirectory', 'executionRevision', 'interruptionRevision')):
                raise AppError('The task moved; refresh its environments.', 409)
            return {'accepted': True, 'result': {'capability': {'status': availability, 'reason': reason,
                'profiles': profiles.catalog(),
                'source': source.get('workingDirectory') or source['workspace']}, 'items': items, 'nextOffset': None,
                'setup': setup_state,
                'checks': [app.test_queue.public(item) for item in app.test_queue.rows(sid=sid, limit=20)]}}
        guard()
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
            if historical:
                config = await asyncio.to_thread(resolve_configuration, app, row, config)
        if config is None:
            raise AppError(reason, 409)
        if action.endswith('.remove') and args.get('retentionMode') == 'full' and config.get('backend', 'docker') != 'incus':
            raise AppError('Full environment retention is available only for this environment’s original Incus backend.', 409)
        guard()
        request, fresh = app.operations.requests.begin(sid, action, args, actor)
        if not fresh:
            return {'accepted': True, 'duplicate': True, 'result': {'request': request, 'resource': None}}
        try:
            if action.endswith('.start'):
                row = await asyncio.to_thread(prepare, app.data_dir, sid, workspace['id'], actor,
                    source.get('workingDirectory') or source['workspace'], config_key(config), args['requestId'], config.get('installationId'), config, args.get('profileId', PROFILE))
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
        if action.endswith('.reconcile'):
            result = await app.worktrees.finish_thread(recovery.reconcile, app, row, args, config, request, admit)
        else:
            result = await app.worktrees.finish_thread(effect, app, row, action.rsplit('.', 1)[1], args, config, request, admit)
        app.operations.notify()
        return {'accepted': True, 'result': result}
    except (OSError, ValueError, sqlite3.Error, LockTimeout) as exc:
        raise AppError(str(exc), 409) from None
