"""One bounded DTU library call in its managed core interpreter.

Invoked by the host with private stdin, never registered as a generic tool/MCP
server. Only fixed profiles and owner-derived paths cross this boundary.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_retention_spec = importlib.util.spec_from_file_location('_environment_retention', Path(__file__).with_name('environment_retention.py'))
retention = importlib.util.module_from_spec(_retention_spec)
_retention_spec.loader.exec_module(retention)


def read_private_record(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 4096:
            raise ValueError('A retained backend proof is unsafe.')
        return json.load(stream)


def write_private_record(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.backend-proof-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Atomic publication, never replace earlier proof.
        Path(temporary).unlink()
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def binding(payload, universe, backend):
    return {'backendIdentity': backend, 'rootOwner': payload['rootOwner'],
            'operationId': payload['operationId'], 'universeId': universe}


def verify_binding(payload, universe, backend, prepare=False):
    """Bind effects to the actual daemon, independent of socket/config spelling."""
    expected = binding(payload, universe, backend)
    path = Path(payload['root']) / 'backend-binding.json'
    recorded = read_private_record(path)
    if payload.get('expectedBackendIdentity') is not None and payload['expectedBackendIdentity'] != backend:
        raise ValueError('The actual backend identity changed.')
    if recorded is not None and recorded != expected:
        raise ValueError('The retained backend owner or identity changed.')
    if prepare and recorded is None:
        write_private_record(path, expected)
        recorded = expected
    if recorded is None and payload.get('expectedBackendIdentity') != backend:
        raise ValueError('The original backend identity is unverified.')
    return 'retained-launch' if recorded is not None else 'host'


def retain_settlement(payload, universe, backend, state):
    value = {**binding(payload, universe, backend), 'state': state}
    path = Path(payload['root']) / ('backend-' + state + '.json')
    previous = read_private_record(path)
    if previous is None:
        write_private_record(path, value)
    elif previous != value:
        raise ValueError('The retained backend settlement changed.')


def settled(payload, universe, backend):
    for state in ('removed', 'launched'):
        try:
            recorded = read_private_record(Path(payload['root']) / ('backend-' + state + '.json'))
        except (OSError, ValueError, TypeError):
            # Incomplete historical proof prevents absence retirement. It must
            # not prevent stopping/preserving a currently observed owned guest.
            return False
        if recorded == {**binding(payload, universe, backend), 'state': state}:
            return True
    return False


def observation(payload, universe, backend, source, ownership='unknown', admission_settled=False, absent=False):
    return {**binding(payload, universe, backend), 'backendIdentitySource': source,
            'ownership': ownership, 'admissionSettled': admission_settled, 'absenceConfirmed': absent}


def docker_backend(payload):
    daemon = docker_json(payload, 'info', '--format', '{{json .}}')
    if not isinstance(daemon.get('ID'), str) or not daemon['ID']:
        raise ValueError('The actual Docker daemon is unavailable.')
    return {'backend': 'docker', 'daemon': daemon['ID']}


def docker_reconcile(payload, universe, backend, source):
    # A successful daemon query, not the library's cached status, proves presence.
    result = subprocess.run(['docker', '--host', payload['dockerHost'], 'ps', '-a',
        '--filter', 'label=com.docker.compose.project=' + universe, '--format', '{{json .}}'],
        check=True, capture_output=True, text=True, timeout=20)
    if len(result.stdout) > 1024 * 1024:
        raise ValueError('The backend observation exceeded its bound.')
    containers = [json.loads(line) for line in result.stdout.splitlines()]
    def check_owner(item):
        labels = item.get('Labels', {})
        if isinstance(labels, str):
            labels = dict(label.split('=', 1) for label in labels.split(',') if '=' in label)
        if any(labels.get(key) != value for key, value in {
            'com.docker.compose.project': universe, 'io.amplifier.root-owner': payload['rootOwner'],
            'io.amplifier.operation-id': payload['operationId']}.items()):
            raise ValueError('A backend resource does not match this operation owner.')
    for item in containers:
        check_owner(item)
    remaining = False
    for kind in ('volume', 'network'):
        resources = subprocess.run(['docker', '--host', payload['dockerHost'], kind, 'ls',
            '--filter', 'label=com.docker.compose.project=' + universe, '--format', '{{json .}}'],
            check=True, capture_output=True, text=True, timeout=20)
        if len(resources.stdout) > 1024 * 1024:
            raise ValueError('The backend storage observation exceeded its bound.')
        rows = [json.loads(line) for line in resources.stdout.splitlines()]
        for item in rows:
            check_owner(item)
        remaining |= bool(rows)
    directory = Path(payload['admissionRoot']) / hashlib.sha256(backend['daemon'].encode()).hexdigest()
    try:
        if any(path.is_symlink() for path in (directory, *directory.parents)):
            raise ValueError('The retained admission directory traverses a symlink.')
        if directory.exists():
            # Reconciliation must not create or modify admission state.
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise ValueError('The retained admission directory is unsafe.')
            pending = any(row['universeId'] == universe for row in pending_admissions(directory))
        else:
            pending = False
        complete = settled(payload, universe, backend) and not pending
    except (OSError, ValueError, TypeError):
        complete = False
    absent = not containers and not remaining and complete
    status = ('stopped' if all(item['State'] in {'exited', 'dead'} for item in containers) else 'running') if containers else 'absent' if absent else 'unknown'
    return {'ok': status != 'unknown', 'status': status, 'universeId': universe,
            'observation': observation(payload, universe, backend, source,
                'matched' if containers else 'absent' if absent else 'unknown', complete, absent)}


def docker_owner(payload, universe, backend, source):
    result = docker_reconcile(payload, universe, backend, source)
    if result['observation']['ownership'] != 'matched' or result['status'] not in {'running', 'stopped'}:
        raise ValueError('The actual owned environment is not confirmed for this effect.')


def docker_json(payload, *args):
    value = subprocess.run(['docker', '--host', payload['dockerHost'], *args],
        check=True, capture_output=True, text=True, timeout=20)
    if len(value.stdout) > 1024 * 1024:
        raise ValueError('Docker observation exceeded its bound.')
    return json.loads(value.stdout)


def admission_directory(payload, daemon):
    root = Path(payload['admissionRoot'])
    if not root.is_absolute() or '..' in root.parts or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('The admission directory cannot traverse symlinks.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths = [root, root / hashlib.sha256(daemon.encode()).hexdigest()]
    for path in paths:
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('The admission directory must be private and owner-controlled.')
    return paths[-1]


def pending_admissions(directory):
    for path in directory.glob('*.json'):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 4096:
                raise ValueError('An admission receipt is unsafe.')
            row = json.load(stream)
        if not isinstance(row.get('universeId'), str) or path.name != hashlib.sha256(row['universeId'].encode()).hexdigest()+'.json':
            raise ValueError('An admission receipt identity is invalid.')
        yield row


def reserve_admission(payload, capacity):
    directory = admission_directory(payload, capacity['scope']['daemon'])
    identity = payload['universeId']
    path = directory / (hashlib.sha256(identity.encode()).hexdigest()+'.json')
    value = {'universeId': identity, 'root': payload['root'], 'operationId': payload['operationId']}
    # An existing uncertain launch is never re-admitted, even under the same ID.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def release_admission(payload, identity):
    daemon = docker_json(payload, 'info', '--format', '{{json .}}')['ID']
    directory = admission_directory(payload, daemon)
    for row in pending_admissions(directory):
        if row['universeId'] == identity:
            if row.get('root') != payload['root'] or row.get('operationId') != payload['operationId']:
                raise ValueError('The uncertain admission belongs to a different owner.')
            (directory / (hashlib.sha256(identity.encode()).hexdigest()+'.json')).unlink()
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def docker_capacity(payload):
    """Observe the actual daemon, including stopped and externally launched DTUs."""
    daemon = docker_json(payload, 'info', '--format', '{{json .}}')
    if not daemon.get('ID') or not daemon.get('DockerRootDir'):
        raise ValueError('The daemon identity or storage location is unavailable.')
    result = subprocess.run(['docker', '--host', payload['dockerHost'], 'ps', '-a',
        '--filter', 'label=com.docker.compose.project', '--format', '{{json .}}'],
        check=True, capture_output=True, text=True, timeout=20)
    if len(result.stdout) > 1024 * 1024:
        raise ValueError('Docker inventory exceeded its bound.')
    projects = {}
    for line in result.stdout.splitlines():
        row = json.loads(line)
        labels = dict(label.split('=', 1) for label in row.get('Labels', '').split(',') if '=' in label)
        identity = labels.get('com.docker.compose.project', '')
        if identity.startswith('dtu-'):
            projects.setdefault(identity, []).append(row['State'])
    pending = sum(row['universeId'] not in projects for row in pending_admissions(admission_directory(payload, daemon['ID'])))
    retained_count = sum(all(state in {'exited', 'dead'} for state in states) for states in projects.values())
    active = len(projects)-retained_count+pending
    total = len(projects)+pending
    disk = os.statvfs(daemon['DockerRootDir'])
    free = disk.f_bavail * disk.f_frsize
    maximum, retained, minimum = payload.get('maxRunning', 15), payload.get('maxInstances', 256), payload.get('minFreeBytes', 10*1024**3)
    blockers = []
    if maximum and active >= maximum:
        blockers.append({'code': 'execution_capacity'})
    if retained and total >= retained:
        blockers.append({'code': 'retained_capacity'})
    if free < minimum:
        blockers.append({'code': 'storage_pressure'})
    return {'schema_version': 1, 'scope': {'daemon': daemon['ID'], 'backend': 'docker'},
        'coordination': 'same_host_user', 'counts': {'active': active, 'stopped_retained': retained_count, 'pending_unobserved': pending, 'total': total},
        'limits': {'max_running': maximum, 'max_instances': retained, 'min_free_bytes': minimum},
        'storage': {'free_bytes': free}, 'blockers': blockers, 'available': not blockers}


@contextmanager
def docker_admission(payload):
    """Serialize cooperating local callers by daemon identity, not socket alias."""
    import fcntl
    observed = docker_capacity(payload)
    root = admission_directory(payload, observed['scope']['daemon'])
    fd = os.open(root/'admission.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError('The admission lock must be a private regular file.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield docker_capacity(payload)
    finally:
        os.close(fd)


def serialize(value):
    return value.model_dump(mode='json')


def source_identity(identity, snapshot):
    if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(key), str) or not snapshot[key]
            for key in ('archive_sha256', 'snapshot_commit', 'snapshot_tree')):
        raise ValueError('The exact source snapshot identity is unavailable.')
    return {'version': 1, 'universeId': identity, 'archiveSha256': snapshot['archive_sha256'],
            'snapshotCommit': snapshot['snapshot_commit'], 'snapshotTree': snapshot['snapshot_tree']}


def source_ready(root, identity, snapshot):
    """A running container alone is not proof that its source was installed."""
    try:
        expected = source_identity(identity, snapshot)
        descriptor = os.open(root / 'source-ready.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 2048:
                return False
            return json.load(stream) == expected
    except (OSError, ValueError, TypeError):
        return False


def retain_source_ready(root, identity, snapshot):
    """Publish exact readiness atomically, only after successful extraction."""
    value = source_identity(identity, snapshot)
    descriptor, temporary = tempfile.mkstemp(prefix='.source-ready-', dir=root)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, root / 'source-ready.json')
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def retain_manifest(directory, archives):
    """Persist archive names and their receipt before the volumes can disappear."""
    with (directory / 'manifest.json').open('x') as stream:
        json.dump(archives, stream)
        stream.flush()
        os.fsync(stream.fileno())
    for path in (directory, directory.parent):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def profile(image, payload=None):
    value = {
        'name': 'unified-test',
        'services': {'twin': {
            'image': image, 'command': ['sleep', 'infinity'],
            'read_only': True, 'cap_drop': ['ALL'],
            'security_opt': ['no-new-privileges:true'],
            'pids_limit': 128, 'mem_limit': '1g', 'cpus': 2,
            'healthcheck': {'test': ['CMD', 'true'], 'interval': '2s'},
            'tmpfs': ['/tmp:rw,noexec,nosuid,size=67108864'],
            'volumes': ['workspace:/workspace', 'artifacts:/artifacts'],
            'networks': ['default'], 'working_dir': '/workspace',
            # Root only inside this container, with no capabilities or host mounts.
            # Docker copy initializes the fresh owned volumes before commands run.
            'user': '0:0',
        }},
        'volumes': {'workspace': {}, 'artifacts': {}},
        'networks': {'default': {'internal': True}},
        'x-dtu': {'twin_machine': 'twin'},
    }
    if payload is not None:
        labels = {'io.amplifier.root-owner': payload['rootOwner'], 'io.amplifier.operation-id': payload['operationId']}
        value['services']['twin']['labels'] = dict(labels)
        for definition in [*value['volumes'].values(), *value['networks'].values()]:
            definition['labels'] = dict(labels)
    return value


def execute_call(payload):
    from digital_twin_universe import lib
    from digital_twin_universe.schemas import BackendConfig, DigitalTwinUniverseError
    root = Path(payload['root'])
    config = BackendConfig(state_root=root / 'backend', docker_host=payload['dockerHost'])
    action = payload['action']
    identity = payload.get('universeId')
    snapshot = payload.get('snapshot')
    try:
        if action == 'capacity':
            return {'ok': True, 'capacity': docker_capacity(payload)}
        if not identity:
            identity = lib.launch_id(payload['operationId'], config=config)
        backend = docker_backend(payload)
        try:
            binding_source = verify_binding(payload, identity, backend, prepare=action == 'prepare')
        except ValueError:
            return {'ok': False, 'status': 'unknown', 'universeId': identity, 'code': 'backend_unverified',
                    'observation': observation(payload, identity, backend, 'unverified')}
        if action == 'reconcile':
            return docker_reconcile(payload, identity, backend, binding_source)
        if action == 'prepare':
            # Source snapshot is immutable and retained even when launch fails.
            snapshot = serialize(lib.export_repository_snapshot(Path(payload['source']), root / 'source.tar'))
            (root / 'snapshot.json').write_text(json.dumps(snapshot))
            return {'ok': True, 'universeId': identity, 'snapshot': snapshot, 'backendIdentity': backend}
        if action == 'start':
            source_identity(identity, snapshot)
            compose = root / 'compose.json'
            compose.write_text(json.dumps(profile(payload['image'], payload)))
            try:
                with docker_admission(payload) as capacity:
                    if capacity['scope']['daemon'] != backend['daemon']:
                        return {'ok': False, 'effectAdmitted': False, 'status': 'not-started', 'code': 'backend_changed',
                                'message': 'The actual daemon changed before admission. Nothing was launched.'}
                    if not capacity['available']:
                        return {'ok': False, 'admissionDenied': True, 'status': 'not-started', 'capacity': capacity,
                            'message': 'Capacity changed before launch. No backend effect was admitted.'}
                    reservation = reserve_admission(payload, capacity)
                    universe = lib.launch(compose, operation_id=payload['operationId'], timeout_seconds=600, config=config)
                    retain_settlement(payload, identity, backend, 'launched')
                    reservation.unlink()
            except BlockingIOError:
                return {'ok': False, 'admissionDenied': True, 'status': 'not-started',
                    'message': 'Another cooperating launch owns admission. No environment was started.'}
            identity = universe.id
            lib.push_files(identity, root / 'source.tar', '/artifacts/.source.tar', config=config)
            unpack = lib.execute(identity, 'python -c "import tarfile; tarfile.open(\'/artifacts/.source.tar\').extractall(\'/workspace\', filter=\'data\')" && rm /artifacts/.source.tar',
                workdir='/', timeout_seconds=60, max_output_bytes=32768, config=config)
            if unpack.exit_code != 0:
                return {'ok': False, 'universeId': identity, 'snapshot': snapshot, 'status': 'unknown',
                        'message': 'The environment launched but source preparation did not complete. Inspect it; launch was not replayed.', 'result': serialize(unpack)}
            retain_source_ready(root, identity, snapshot)
            return {'ok': True, 'universeId': identity, 'sourceReady': True,
                    'status': universe.state, 'services': [serialize(item) for item in universe.services]}
        if not identity:
            # One resource has its own backend root. Exact launch identity is the
            # recovery key when the host lost the original helper response.
            identity = lib.launch_id(payload['operationId'], config=config)
        if action in {'read', 'execute', 'stop', 'remove'}:
            docker_owner(payload, identity, backend, binding_source)
        if action == 'read':
            universe = lib.status(identity, config=config)
            ready = source_ready(root, identity, snapshot)
            return {'ok': True, 'universeId': identity, 'sourceReady': ready,
                    'status': universe.state if ready or universe.state == 'stopped' else 'unknown',
                    'services': [serialize(item) for item in universe.services],
                    'message': None if ready else 'Source preparation is not confirmed for this exact snapshot. Checks are unavailable. Stop and remove the environment to retain any partial files; inspection does not install source again.'}
        if action == 'execute':
            if not source_ready(root, identity, snapshot):
                return {'ok': False, 'universeId': identity, 'sourceReady': False, 'status': 'unknown',
                        'message': 'Source preparation is not confirmed for this exact snapshot. No check was started and source was not installed again.'}
            result = lib.execute(identity, payload['command'], workdir='/workspace',
                timeout_seconds=payload['timeoutSeconds'], max_output_bytes=32768, config=config)
            return {'ok': True, 'universeId': identity, 'result': serialize(result)}
        if action == 'stop':
            result = lib.stop(identity, config=config)
            if result.complete:
                release_admission(payload, identity)
            return {'ok': result.complete, 'universeId': identity,
                    'status': 'stopped' if result.complete else 'unknown', 'result': serialize(result),
                    'message': 'Containers stopped; source and artifacts are retained.' if result.complete else 'Stop was requested but not confirmed. Inspect current state.'}
        if action == 'remove':
            if payload.get('retentionMode', 'files') != 'files':
                return {'ok': False, 'status': 'unknown', 'universeId': identity, 'code': 'retention_unsupported',
                        'message': 'Full environment retention is unavailable for this backend. The stopped environment was preserved.'}
            universe = lib.status(identity, config=config)
            if universe.state != 'stopped' or any(item.state == 'running' for item in universe.services):
                return {'ok': False, 'universeId': identity, 'status': 'unknown', 'message': 'Removal requires observed stopped containers. No data was removed.'}
            destination = root / ('retained-' + hashlib.sha256(payload['requestId'].encode()).hexdigest())
            destination.mkdir(mode=0o700)
            archives = [serialize(lib.export_files(identity, source, destination / name, config=config))
                        for source, name in [('/workspace', 'workspace.tar'), ('/artifacts', 'artifacts.tar')]]
            retained = retention.verify_retention(root, archives)
            retain_manifest(destination, archives)
            result = lib.destroy(identity, config=config)
            if result.complete:
                release_admission(payload, identity)
                retain_settlement(payload, identity, backend, 'removed')
            return {'ok': result.complete, 'universeId': identity,
                    'status': 'removed' if result.complete else 'unknown', 'result': serialize(result),
                    'archives': archives, 'retention': retained, 'message': 'Source and artifacts were retained before removal.' if result.complete else 'Artifacts were retained; teardown is not confirmed.'}
        raise ValueError('Unsupported managed test environment action.')
    except DigitalTwinUniverseError as exc:
        return {'ok': False, 'status': 'unknown', 'universeId': identity or exc.universe_id,
                'snapshot': snapshot, 'code': exc.code,
                'message': 'The backend did not confirm this operation. A command may still be running; inspect the retained environment without replaying the request.'}


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read(65537))
        value = execute_call(payload)
    except Exception:  # noqa: BLE001 - subprocess boundary returns uncertainty, never raw private diagnostics.
        value = {'ok': False, 'status': 'unknown',
                 'message': 'The managed backend failed before returning a confirmed result. Inspect retained state; this request will not replay.'}
    sys.stdout.write(json.dumps(value))


if __name__ == '__main__':
    main()
