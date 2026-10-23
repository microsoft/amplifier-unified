"""Fixed stdio controller for registered native Linux Incus test targets.

The origin owns the canonical job. This controller owns the actual backend
operation and its durable dispatch receipt. A missing reply never admits the
same effect again, and no caller supplies an executable or an absolute path.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

# This entry point is shipped in a hash-qualified, private immutable directory.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from amplifier_web import incus_environment_worker as worker
from amplifier_web import test_environment_preparation as preparation
from amplifier_web import test_environment_profiles as profiles
from amplifier_web import test_host_resources as machine
from amplifier_web import test_runner_backend_policy as backend_policy

MAX_SOURCE = 512 * 1024**2
CHUNK = 512 * 1024
MAX_CONTROL = 1024 * 1024
MUTATIONS = {'start', 'execute', 'job_start', 'job_cancel', 'stop', 'remove', 'finishCleanup', 'exportArtifacts', 'prepareDependencies', 'cleanupDependencies'}
ACTIONS = MUTATIONS | {'prepare', 'capacity', 'read', 'job_read', 'reconcile', 'source', 'artifact', 'describe', 'machine.read', 'machine.save', 'cancelPreparation', 'capacity.read', 'capacity.save'}
PAYLOAD = {'action', 'source', 'backendKey', 'expectedBackendIdentity', 'retentionMode', 'snapshot', 'operationId', 'universeId', 'rootOwner', 'resourceId', 'resourceRevision', 'admissionRevision', 'requestId', 'command', 'jobId', 'profileId', 'resourceRequest', 'timingRequest', 'timeoutSeconds', 'limits', 'offset', 'content', 'archiveIndex', 'exportRequestId', 'expectedRevision'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def home():
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def state():
    path = home() / '.local/state/amplifier-test-runners'
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Runner storage cannot traverse links.')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    worker.private_directory(path)
    return path


@contextmanager
def authority_locked(root):
    # Readers must not see the immutable writer's temporary second hard link.
    # This persistent account lock is separate from long allocation operations.
    path = root / '.authority.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        def check():
            info = os.fstat(fd)
            current = path.stat(follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1
                    or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
                raise ValueError('The runner authority lock changed.')
        check()
        fcntl.flock(fd, fcntl.LOCK_EX)
        check()
        yield
    finally:
        os.close(fd)


def authority_id(root, *, create=False):
    with authority_locked(root):
        path = root / 'authority.json'
        value = read(path)
        if value is None and create:
            try:
                worker.common.write_private_record(path, {'version': 1, 'id': uuid.uuid4().hex})
            except FileExistsError:
                pass
            value = read(path)
        if not isinstance(value, dict) or value.get('version') != 1 or not re.fullmatch(r'[a-f0-9]{32}', value.get('id', '')):
            raise ValueError('The target authority must be registered first.')
        return value['id']


def read(path, default=None):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return default
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > MAX_CONTROL:
            raise ValueError('A runner receipt is not a private bounded file.')
        return json.load(stream)


def write(path, value):
    preparation.write(path, value)


@contextmanager
def locked(root):
    fd = os.open(root / '.controller.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError('The runner owner lock changed.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def configuration():
    # Registration is operator setup. Routine calls only use this retained exact
    # configuration, never a caller-selected file or changed active app config.
    path = Path(__file__).resolve().parent.parent / 'backend.json'
    value = read(path)
    if digest(value) != path.parent.name:
        raise ValueError('The registered backend configuration changed.')
    return worker.validate_configuration(value)


def backend(config, root):
    env = worker.environment(config, root)
    data = worker.checked(worker.incus(config, env, ['query', '/1.0']))
    value = json.loads(data['stdout'])['environment']
    identity = {'backend': 'incus', 'daemon': value['certificate_fingerprint'], 'project': value['project']}
    if identity['project'] != config['project'] or config.get('daemonFingerprint') not in (None, identity['daemon']):
        raise ValueError('The registered backend identity changed.')
    target = worker.host_target(config, env)
    if not target.get('supported') or target.get('machineId') != machine.identity():
        raise ValueError('A registered controller requires its native local Incus daemon.')
    return identity, target


def owner(payload):
    for key in ('rootOwner', 'resourceId', 'operationId', 'backendKey'):
        if not isinstance(payload.get(key), str) or not 1 <= len(payload[key]) <= 200:
            raise ValueError('An exact original owner is required.')
    return {key: payload[key] for key in ('rootOwner', 'resourceId', 'operationId', 'backendKey')}


def row_for(payload):
    return {'id': payload['resourceId'], 'sessionId': payload['rootOwner'], 'launchOperationId': payload['operationId'],
            'backendKey': payload['backendKey'], 'resourceRequest': payload['resourceRequest'],
            'preparationRequired': profiles.get(payload['profileId'])['preparationRequired']}


def source_chunk(root, payload):
    snapshot = payload.get('snapshot')
    if not isinstance(snapshot, dict) or not re.fullmatch(r'[a-f0-9]{64}', str(snapshot.get('archive_sha256', ''))):
        raise ValueError('An exact source archive hash is required.')
    data = base64.b64decode(payload.get('content', ''), validate=True)
    offset = payload.get('offset')
    if type(offset) is not int or offset < 0 or len(data) > CHUNK or offset + len(data) > MAX_SOURCE:
        raise ValueError('The source chunk exceeds its bound.')
    expected = read(root / 'source-binding.json')
    if expected is None:
        write(root / 'source-binding.json', snapshot)
    elif expected != snapshot:
        raise ValueError('This owner already has a different source snapshot.')
    path = root / 'source.tar'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError('The source archive changed.')
        size = info.st_size
        if offset < size:
            stream.seek(offset)
            if stream.read(len(data)) != data:
                raise ValueError('The source chunk conflicts with retained bytes.')
        elif offset == size:
            stream.seek(offset)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        else:
            raise ValueError('The source chunk is out of order.')
        return {'ok': True, 'offset': max(size, offset + len(data))}


def prepare(root, payload, config, target):
    snapshot = read(root / 'source-binding.json')
    if snapshot != payload.get('snapshot') or worker.file_digest(root / 'source.tar', MAX_SOURCE)[0] != snapshot['archive_sha256']:
        raise ValueError('The complete frozen source has not arrived.')
    name = worker.identity(payload)
    identity, _ = backend(config, root)
    worker.common.verify_binding({**payload, 'root': str(root)}, name, identity, prepare=True)
    immutable = {'owner': owner(payload), 'snapshot': snapshot, 'profileId': payload['profileId'], 'resourceRequest': payload['resourceRequest'], 'timingRequest': payload.get('timingRequest')}
    saved = read(root / 'owner.json')
    if saved is not None and saved != immutable:
        raise ValueError('The immutable target owner changed.')
    if saved is None:
        write(root / 'owner.json', immutable)
    # Preparation allocates only controller-owned files. Reservation is at actual
    # build/start admission, after the origin has committed its original target.
    return {'ok': True, 'universeId': name, 'snapshot': snapshot, 'backendIdentity': identity,
            'hostTarget': {'supported': False, 'reason': 'The registered target controller owns machine admission.'},
            'targetMachine': target['machineId']}


def artifact(root, payload):
    record = read(root / ('effect-' + digest([payload.get('exportRequestId'), 'remove']) + '.json'))
    if record is None:
        record = read(root / ('effect-' + digest([payload.get('exportRequestId'), 'exportArtifacts']) + '.json'))
    if record is None:
        record = read(root / ('effect-' + digest([payload.get('exportRequestId'), 'finishCleanup']) + '.json'))
    if not record or record.get('state') != 'completed':
        raise ValueError('The exact retained export is unavailable.')
    index, offset = payload.get('archiveIndex'), payload.get('offset')
    archives = record.get('result', {}).get('archives', [])
    if type(index) is not int or not 0 <= index < len(archives) or type(offset) is not int or offset < 0:
        raise ValueError('Choose a bounded retained archive page.')
    path = Path(archives[index]['destination'])
    if not path.is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('The retained archive no longer belongs to this owner.')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size != archives[index]['bytes']:
            raise ValueError('The retained archive identity changed.')
        stream.seek(offset)
        content = stream.read(CHUNK)
    return {'ok': True, 'content': base64.b64encode(content).decode(), 'offset': offset + len(content),
            'bytes': archives[index]['bytes'], 'sha256': archives[index]['sha256']}


def dispatch(request, *, _configuration=None, _engine_owner=None):
    if not isinstance(request, dict) or set(request) != {'version', 'machineId', 'backendIdentity', 'payload'} or request['version'] != 1:
        raise ValueError('Unsupported registered runner protocol.')
    payload = request['payload']
    if not isinstance(payload, dict) or set(payload) - PAYLOAD or payload.get('action') not in ACTIONS:
        raise ValueError('Unsupported registered runner operation.')
    if _configuration is not None:
        # Private updater invocation only: these arguments are never parsed
        # from the stdio request and cannot authorize another target owner.
        if not _engine_owner or (payload.get('action') not in {'capacity', 'describe'}
                                  and payload.get('rootOwner') != _engine_owner):
            raise ValueError('The candidate engine requires its exact qualification owner.')
        config = worker.validate_configuration(_configuration, _engine_owner=_engine_owner)
    else:
        if _engine_owner is not None:
            raise ValueError('An engine qualification configuration is required.')
        config = configuration()
    base = state()
    identity, target = backend(config, base)
    action = payload['action']
    authority = authority_id(base, create=action == 'describe')
    if action != 'describe' and (request['machineId'] != authority or request['backendIdentity'] != identity):
        raise ValueError('The registered machine or backend changed.')
    if action == 'describe':
        return {'ok': True, 'version': 1, 'machineId': authority, 'backendIdentity': identity, 'nativeMachineId': machine.identity(),
                'accountId': hashlib.sha256(str(os.getuid()).encode()).hexdigest(),
                'profiles': [p['id'] for p in profiles.catalog()], 'incus': config,
                'machineResources': machine.snapshot()}
    if action == 'machine.read':
        return {'ok': True, 'machineResources': machine.snapshot()}
    if action == 'machine.save':
        return {'ok': True, 'machineResources': machine.save(payload['expectedRevision'], payload['limits'])}
    if action == 'capacity.read':
        return {'ok': True, 'backendPolicy': backend_policy.snapshot(base, identity, config)}
    if action == 'capacity.save':
        return {'ok': True, 'backendPolicy': backend_policy.save(base, identity, config, payload['expectedRevision'], payload['limits'])}
    if action == 'capacity':
        config = backend_policy.apply(base, identity, config)
        if _engine_owner:
            config.update(maxRunning=0, maxInstances=0)
        result = worker.execute_call({'action': 'capacity', 'root': str(base), 'incus': config}, **({'_engine_owner': _engine_owner} if _engine_owner else {}))
        result['machineResources'] = machine.snapshot()
        return result
    original = owner(payload)
    root = base / ('owner-' + digest(original))
    root.mkdir(mode=0o700, exist_ok=True)
    worker.private_directory(root)
    if action == 'cancelPreparation':
        # A cancellation marker is independently publishable while the long
        # preparation holds its effect lock. It grants no launch authority.
        saved = read(root / 'owner.json')
        if not saved or saved['owner'] != original or saved['snapshot'] != payload.get('snapshot') or saved['resourceRequest'] != payload.get('resourceRequest') or saved.get('timingRequest') != payload.get('timingRequest'):
            raise ValueError('Cancellation requires the exact original target owner.')
        folder = root / 'preparation'
        folder.mkdir(mode=0o700, exist_ok=True)
        worker.private_directory(folder)
        write(folder / 'cancel.json', {'cancelRequested': True})
        return {'ok': True, 'status': 'cancellation-requested', 'message': 'Cancellation reached the original builder. Stopping and cleanup must still be observed.'}
    with locked(root):
        if action == 'source':
            return source_chunk(root, payload)
        if action == 'prepare':
            profiles.resources(payload.get('profileId'), payload.get('resourceRequest'))
            profiles.timing(payload.get('profileId'), payload.get('timingRequest'))
            return prepare(root, payload, config, target)
        saved = read(root / 'owner.json')
        if not saved or saved['owner'] != original or saved['snapshot'] != payload.get('snapshot') or saved['resourceRequest'] != payload.get('resourceRequest') or saved['profileId'] != payload.get('profileId') or saved.get('timingRequest') != payload.get('timingRequest'):
            raise ValueError('The original source and target owner are required.')
        if action == 'artifact':
            return artifact(root, payload)
        if action in {'execute', 'job_start'}:
            profiles.validate_timeout(saved['profileId'], payload.get('timeoutSeconds'), saved.get('timingRequest'))
        row = row_for(payload)
        reservation = read(root / 'reservation.json')
        if reservation:
            row['hostReservation'] = reservation
        intent = {k: v for k, v in payload.items() if k not in {'resourceRevision', 'admissionRevision', 'limits'}}
        record_path = root / ('effect-' + digest([payload.get('requestId'), action]) + '.json')
        if action in MUTATIONS:
            if not isinstance(payload.get('requestId'), str) or not payload['requestId']:
                raise ValueError('A stable effect request is required.')
            previous = read(record_path)
            if previous:
                if previous['intent'] != intent:
                    raise ValueError('The original effect arguments changed.')
                if action != 'cleanupDependencies' or previous.get('result', {}).get('complete') is True:
                    return previous.get('result', {'ok': False, 'status': 'unknown', 'message': 'The original target operation has no confirmed result. It was not repeated.'})
                # Cleanup is an explicit recovery step: the original effect
                # lock has settled, and preparation.cleanup reobserves the
                # exact builder/image owner before bounded removal. It cannot
                # launch or repeat a test; an incomplete observation may improve.
            # This is the target-side dispatch fence. Persist before *any* build,
            # create, test, stop or export effect; keep it after lost replies.
        if action in {'start', 'prepareDependencies'}:
            config = backend_policy.apply(base, identity, config)
            if _engine_owner:
                # Only the private, exact-owner update probe has this context.
                # Storage thresholds and the machine reservation remain active.
                config.update(maxRunning=0, maxInstances=0)
            reservation = machine.reserve(row, target)
            if not reservation.get('admitted'):
                return {'ok': False, 'status': 'not-started', 'effectAdmitted': False, 'admissionDenied': True,
                        'machineResources': reservation, 'message': 'Waiting for room on the registered test machine.'}
            write(root / 'reservation.json', reservation)
            row['hostReservation'] = reservation
        if action in MUTATIONS:
            write(record_path, {'state': 'admitted', 'intent': intent})
        runtime_payload = {k: v for k, v in payload.items() if k in PAYLOAD and k not in {'content', 'offset', 'limits'}}
        runtime_payload.update(root=str(root), incus=config)
        if action == 'prepareDependencies':
            receipt = preparation.prepare({'backend': 'incus', 'incus': config}, root, saved['snapshot'], saved['profileId'], payload['requestId'], resource_request=saved['resourceRequest'], host_reservation=reservation, timing_request=saved.get('timingRequest'))
            write(root / 'prepared.json', receipt)
            machine.settle(row, phase='launching')
            result = {'ok': True, 'preparedDependencies': receipt}
        elif action == 'cleanupDependencies':
            result = {'ok': True, **preparation.cleanup({'backend': 'incus', 'incus': config}, root)}
            if result.get('complete') and not read(root / 'launch-dispatched.json'):
                machine.settle(row, release=True)
        else:
            prepared = read(root / 'prepared.json')
            if prepared:
                preparation.validate_receipt(prepared, saved['snapshot'], saved['profileId'])
                runtime_payload['incus'] = {**config, 'image': prepared['image']}
            if action == 'start':
                write(root / 'launch-dispatched.json', {'requestId': payload['requestId']})
            result = worker.execute_call(runtime_payload, **({'_engine_owner': _engine_owner} if _engine_owner else {}))
            if action == 'start' and result.get('ok'):
                machine.settle(row)
            if action == 'reconcile':
                settle_observed_reservation(root, runtime_payload, result, row)
                reconcile_launch(root, runtime_payload, result, row)
            elif action == 'read' and result.get('status') in {'stopped', 'removed', 'absent', 'unknown'}:
                if preparation_settled(root) and machine.held(row):
                    try:
                        observed = worker.execute_call({**runtime_payload, 'action': 'reconcile'}, **({'_engine_owner': _engine_owner} if _engine_owner else {}))
                        if settle_observed_reservation(root, runtime_payload, observed, row):
                            # Failed inventory is not evidence; replace it only
                            # after exact settled native ownership/absence proof.
                            result = {**(result if result.get('ok') is True else {}),
                                      'ok': True, 'universeId': worker.identity(runtime_payload),
                                      'status': 'removed' if observed['status'] == 'absent' else observed['status']}
                    except (OSError, ValueError, TimeoutError):
                        pass  # Keep both the uncertain effect and its reservation.
            # Unknown creation retains capacity even after absent inventory.
            # Release only after our completed launch followed by exact stopped
            # or removed owner evidence; no TTL and no origin assertions.
            launch = read(root / 'launch-dispatched.json')
            completed_launch = read(root / ('effect-' + digest([launch['requestId'], 'start']) + '.json')) if launch else None
            if (action in {'stop', 'remove', 'finishCleanup'} and result.get('ok') and result.get('status') in {'stopped', 'removed'}
                    and completed_launch and completed_launch.get('state') == 'completed'
                    and (completed_launch.get('result', {}).get('ok') or completed_launch.get('result', {}).get('creationSettled'))):
                machine.settle(row, release=True)
            if action == 'start' and result.get('effectAdmitted') is False and not prepared:
                machine.settle(row, release=True)
        if action in MUTATIONS:
            write(record_path, {'state': 'completed', 'intent': intent, 'result': result})
        return result



def preparation_settled(root):
    return (not read(root / 'preparation/intent.json') or bool(read(root / 'prepared.json'))
            or (read(root / 'preparation/cleanup.json', {}) or {}).get('complete') is True)


def settle_observed_reservation(root, payload, result, row):
    """Under this target owner's lock, preserve effects and settle only compute."""
    if (not preparation_settled(root) or not machine.held(row)
            or result.get('ok') is not True or result.get('status') not in {'stopped', 'absent'}
            or not isinstance(payload.get('expectedBackendIdentity'), dict)):
        return
    observed = result.get('observation')
    if not isinstance(observed, dict):
        return
    expected = worker.common.binding(payload, worker.identity(payload), payload['expectedBackendIdentity'])
    if (any(observed.get(key) != value for key, value in expected.items())
            or observed.get('admissionSettled') is not True
            or (result['status'] == 'stopped' and observed.get('ownership') != 'matched')
            or (result['status'] == 'absent' and (observed.get('ownership') != 'absent'
                                               or observed.get('absenceConfirmed') is not True))):
        return
    proof = {'version': 1, 'state': 'observed', 'environmentStatus': result['status'],
             'observation': observed, 'observedAt': time.time()}
    path = root / 'reservation-recovery.json'
    write(path, proof)
    machine.settle(row, release=True)
    write(path, {**proof, 'state': 'released'})
    return True


def reconcile_launch(root, payload, result, row):
    launch = read(root / 'launch-dispatched.json')
    if not launch or not result.get('ok'):
        return
    path = root / ('effect-' + digest([launch['requestId'], 'start']) + '.json')
    original = read(path)
    observed = result.get('observation', {})
    if not isinstance(payload.get('expectedBackendIdentity'), dict):
        return
    expected = worker.common.binding(payload, worker.identity(payload), payload['expectedBackendIdentity'])
    unresolved = original and (original.get('state') == 'admitted' or
        original.get('state') == 'completed' and original.get('result', {}).get('status') == 'unknown' and not original.get('result', {}).get('creationSettled'))
    if (not unresolved
            or any(observed.get(key) != value for key, value in expected.items())
            or observed.get('admissionSettled') is not True
            or observed.get('ownership') not in {'matched', 'absent'}):
        return
    state = result.get('status')
    if state not in {'running', 'stopped', 'absent'} or state == 'absent' and observed.get('absenceConfirmed') is not True:
        return
    # DTU's exact original launch receipt is terminal. This settles the target
    # fence without repeating create; an inventory-only absence cannot do so.
    write(path, {**original, 'state': 'completed', 'result': {'ok': False, 'status': 'removed' if state == 'absent' else state,
        'universeId': worker.identity(payload), 'reconciled': True, 'creationSettled': True, 'observation': observed,
        'message': 'The original create settled. Source installation and test execution were not replayed or claimed successful.'}})
    if state in {'stopped', 'absent'}:
        if (not read(root / 'preparation/intent.json') or read(root / 'prepared.json')
                or (read(root / 'preparation/cleanup.json', {}) or {}).get('complete')):
            machine.settle(row, release=True)
    else:
        machine.settle(row)

def main():
    try:
        if sys.version_info < (3, 13):
            if '--qualified-runtime' in sys.argv:
                raise ValueError('The registered adapter requires Python 3.13 or later for bounded archive handling.')
            config = configuration()
            interpreter = config['interpreter']
            os.execv(interpreter, [interpreter, '-I', '-B', str(Path(__file__).resolve()), '--qualified-runtime'])
        encoded = sys.stdin.buffer.read(MAX_CONTROL + 1)
        if len(encoded) > MAX_CONTROL:
            raise ValueError('The controller request exceeds its bound.')
        result = dispatch(json.loads(encoded))
    except BlockingIOError:
        result = {'ok': False, 'status': 'unknown', 'code': 'owner_busy', 'message': 'The original target operation is still active. No new operation started.'}
    except Exception:
        result = {'ok': False, 'status': 'unknown', 'code': 'target_unconfirmed', 'message': 'The registered target did not confirm this operation. Keep its original owner; do not replay or move it.'}
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()
