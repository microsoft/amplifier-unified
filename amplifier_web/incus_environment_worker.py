"""Qualified Incus adapter for the existing owned test-environment protocol.

The host supplies operator configuration and durable request identities. This
helper never chooses a backend, installs software, overrides policy, retries an
uncertain effect, or adopts environments belonging to another root task.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import quote

QUALIFIED_VERSION = '0.4.0'
# Reviewed legacy PR #37 (0eb3485); launcher bytes vary with installation path.
QUALIFIED_PACKAGE_SHA256 = '5875826fc5f7dcb3eb70709b20043cb5b7ee6632bbb7c471246013e42cbe7f39'
OUTPUT_BYTES = 8192
CONTROL_BYTES = 65536
EXPORT_BYTES = 8 * 1024 ** 3
FREE_RESERVE_BYTES = 1024 ** 3

# This file runs under the separately managed snapshot-core interpreter with -I.
# Load only the adjacent app-owned helper, not ambient sys.path or a workspace.
_spec = importlib.util.spec_from_file_location('_unified_environment_common', Path(__file__).with_name('test_environment_worker.py'))
common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(common)


class BackendFailure(RuntimeError):
    def __init__(self, code, message, details=None, admitted=True):
        super().__init__(message)
        self.code, self.details, self.admitted = code, details or {}, admitted


def private_directory(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute private directory is required.')
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Private backend directories cannot traverse symlinks.')
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Backend directories must be private and owned by this user.')
    finally:
        os.close(descriptor)
    return path


def file_digest(path, maximum=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest, size = hashlib.sha256(), 0
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('A regular backend file is required.')
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if maximum is not None and size > maximum:
                raise ValueError('The backend file exceeds its size bound.')
            digest.update(chunk)
    return digest.hexdigest(), size


def package_digest(directory):
    """Hash executable package source; deployment must separately pin dependencies."""
    root = Path(directory)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError('The qualified legacy package directory is unavailable.')
    if any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('Package source cannot use symlinks.')
    paths = sorted(root.rglob('*.py'))
    if not paths or len(paths) > 256:
        raise ValueError('The qualified legacy package source is incomplete.')
    digest = hashlib.sha256()
    for path in paths:
        content_hash, _ = file_digest(path, 2 * 1024 * 1024)
        digest.update(path.relative_to(root).as_posix().encode() + b'\0' + content_hash.encode() + b'\n')
    return digest.hexdigest()


def validate_configuration(value):
    """Read-only qualification of an explicitly installed legacy CLI generation."""
    required = {'executable', 'executableSha256', 'packageSha256', 'incusExecutable', 'configDir', 'admissionDir', 'project', 'image'}
    allowed = required | {'daemonFingerprint', 'maxRunning', 'maxInstances', 'minFreeBytes', 'interpreter', 'packageDirectory'}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - allowed:
        raise ValueError('The qualified Incus backend configuration is incomplete.')
    config = dict(value)
    for key in ('executableSha256', 'packageSha256', 'image'):
        if not isinstance(config[key], str) or not re.fullmatch(r'[a-f0-9]{64}', config[key]):
            raise ValueError('Backend code and image identities must be exact SHA-256 values.')
    if config['packageSha256'] != QUALIFIED_PACKAGE_SHA256:
        raise ValueError('The legacy package pin is not the app-qualified generation.')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}', config['project']):
        raise ValueError('An explicit Incus project is required.')
    if config.get('daemonFingerprint') is not None and not re.fullmatch(r'[a-f0-9]{64}', config['daemonFingerprint']):
        raise ValueError('The daemon identity is invalid.')
    for key in ('configDir', 'admissionDir'):
        config[key] = str(private_directory(config[key]))
    for key in ('executable', 'incusExecutable'):
        path = Path(config[key])
        info = path.lstat()
        if not path.is_absolute() or '..' in path.parts or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or not os.access(path, os.X_OK):
            raise ValueError('Backend executables must be absolute regular files protected from other users.')
    if Path(config['incusExecutable']).name != 'incus':
        raise ValueError('The selected Incus executable must be named incus.')
    if file_digest(config['executable'], 65536)[0] != config['executableSha256']:
        raise ValueError('The qualified legacy launcher changed.')
    line = Path(config['executable']).read_text().splitlines()[0]
    interpreter = Path(line[2:]) if line.startswith('#!/') else None
    if not interpreter or not interpreter.is_file() or ' ' in str(interpreter):
        raise ValueError('The legacy launcher needs an explicit Python interpreter.')
    # Normal venv interpreter symlinks are legitimate. The package source and
    # launcher, not the venv symlink spelling, define this qualification.
    probe = run([str(interpreter), '-I', '-c',
        'import importlib.util,importlib.metadata,json; s=importlib.util.find_spec("amplifier_bundle_digital_twin_universe"); print(json.dumps({"version":importlib.metadata.version("amplifier-bundle-digital-twin-universe"),"directory":next(iter(s.submodule_search_locations))}))'],
        env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, timeout=10)
    if probe['exit_code'] or probe['truncated']:
        raise ValueError('The qualified legacy interpreter is unavailable.')
    installed = json.loads(probe['stdout'])
    if installed['version'] != QUALIFIED_VERSION or package_digest(installed['directory']) != config['packageSha256']:
        raise ValueError('The installed legacy backend is not this app\'s qualified generation.')
    config.update(interpreter=str(interpreter), packageDirectory=installed['directory'])
    for key, env_key in [('maxRunning', 'AMPLIFIER_DTU_MAX_RUNNING'), ('maxInstances', 'AMPLIFIER_DTU_MAX_INSTANCES'), ('minFreeBytes', 'AMPLIFIER_DTU_MIN_FREE_BYTES')]:
        # Preserve explicit operator environment policy when host configuration
        # is first qualified. The isolated helper receives the resolved values.
        raw = config.get(key, os.environ.get(env_key))
        if raw is not None:
            if isinstance(raw, bool) or not re.fullmatch(r'\d+', str(raw)):
                raise ValueError('Capacity policy values must be nonnegative integers.')
            config[key] = int(raw)
    return config


def environment(config, root):
    result = {'PATH': str(Path(config['incusExecutable']).parent) + os.pathsep + os.defpath,
              'HOME': str(root), 'LANG': 'C.UTF-8', 'INCUS_CONF': config['configDir'],
              'AMPLIFIER_DTU_INCUS_PROJECT': config['project'], 'AMPLIFIER_DTU_ADMISSION_DIR': config['admissionDir']}
    for key, env_key in [('maxRunning', 'AMPLIFIER_DTU_MAX_RUNNING'), ('maxInstances', 'AMPLIFIER_DTU_MAX_INSTANCES'), ('minFreeBytes', 'AMPLIFIER_DTU_MIN_FREE_BYTES')]:
        if key in config:
            result[env_key] = str(config[key])
    return result


def run(arguments, env, timeout=30, watched_file=None, output_bytes=OUTPUT_BYTES):
    """Drain bounded output; an overflow is truncation, a timeout is uncertainty."""
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, start_new_session=True)
    output, errors = bytearray(), bytearray()
    truncated = False
    completed = False
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, errors)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise BackendFailure('timeout', 'The backend response timed out; the operation may still be running.')
                if watched_file is not None and watched_file.exists() and watched_file.lstat().st_size > EXPORT_BYTES:
                    raise BackendFailure('export_limit', 'The retained export exceeded its size limit. The environment was preserved.')
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        room = max(0, output_bytes - len(key.data))
                        key.data.extend(chunk[:room])
                        truncated |= len(chunk) > room
        process.wait(timeout=max(1, deadline - time.monotonic()))
        completed = True
        return {'exit_code': process.returncode, 'stdout': output.decode('utf-8', 'replace'),
                'stderr': errors.decode('utf-8', 'replace'), 'truncated': truncated}
    finally:
        if process.poll() is None or not completed:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        process.stdout.close()
        process.stderr.close()


def cli(config, env, arguments, timeout=30):
    result = run([config['executable'], *arguments], env, timeout, output_bytes=CONTROL_BYTES)
    if result['truncated']:
        raise BackendFailure('response_limit', 'The backend receipt exceeded its bound; inspect the recorded operation.')
    try:
        value = json.loads(result['stdout'])
    except (ValueError, TypeError):
        raise BackendFailure('backend_unknown', 'The backend did not return a structured receipt.')
    if result['exit_code'] or not isinstance(value, dict):
        code = value.get('error', 'backend_unknown') if isinstance(value, dict) else 'backend_unknown'
        denied = code in {'execution_capacity', 'retained_capacity', 'storage_pressure', 'storage_unknown', 'admission_busy', 'request_conflict', 'name_conflict', 'state_unsafe'}
        raise BackendFailure(code, 'The backend did not admit this request.' if denied else 'The backend outcome needs reconciliation.', value if isinstance(value, dict) else {}, admitted=not denied)
    return value


def incus(config, env, arguments, timeout=30, watched_file=None):
    control = arguments[0] == 'query'
    if control:
        arguments = ['query', arguments[1] + ('&' if '?' in arguments[1] else '?') + 'project=' + quote(config['project'], safe='')]
    else:
        arguments = ['--project', config['project'], *arguments]
    return run([config['incusExecutable'], *arguments], env, timeout, watched_file, output_bytes=CONTROL_BYTES if control else OUTPUT_BYTES)


def checked(result):
    if result['exit_code']:
        raise BackendFailure('backend_unknown', 'The backend operation did not complete successfully.')
    return result


def identity(payload):
    if any(not isinstance(payload.get(key), str) or not payload[key] or len(payload[key]) > 200 for key in ('operationId', 'rootOwner')):
        raise ValueError('A durable launch identity and root owner are required.')
    return 'ut-' + hashlib.sha256((payload['rootOwner'] + '\0' + payload['operationId']).encode()).hexdigest()[:32]


def observe(config, env, name, payload, confinement=False):
    request = cli(config, env, ['launch-status', payload['operationId']])
    if request['state'] in {'not_recorded', 'removed', 'failed'}:
        return request, None
    if request.get('name') != name or request.get('owner_root') != payload['rootOwner'] or request['state'] == 'conflict':
        raise BackendFailure('ownership_conflict', 'The environment no longer matches this task\'s ownership.')
    if 'instance_status' not in request:
        return request, None
    raw = checked(incus(config, env, ['query', '/1.0/instances/' + quote(name, safe='')]))
    if raw['truncated']:
        raise BackendFailure('response_limit', 'The environment observation exceeded its bound.')
    instance = json.loads(raw['stdout'])
    metadata = instance.get('config', {})
    if metadata.get('user.dtu.managed-by') != 'amplifier-digital-twin' or metadata.get('user.dtu.owner-root') != payload['rootOwner'] or metadata.get('user.dtu.request-id') != payload['operationId']:
        raise BackendFailure('ownership_conflict', 'The environment ownership changed; no effect was attempted.')
    if confinement:
        devices = instance.get('expanded_devices', {})
        cfg = instance.get('expanded_config', {})
        if (instance.get('profiles') or set(devices) != {'root'} or devices['root'].get('type') != 'disk'
                or devices['root'].get('path') != '/' or devices['root'].get('source')
                or metadata.get('user.dtu.offline') != 'true' or cfg.get('security.privileged') != 'false'
                or cfg.get('security.nesting') != 'false'):
            raise BackendFailure('isolation_changed', 'The approved environment isolation changed; no check was started.')
    return request, instance


def persist_attempt(root, value):
    path = root / 'source-attempt.json'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute_call(payload):
    root = private_directory(payload['root'])
    config = validate_configuration(payload['incus'])
    env = environment(config, root)
    action = payload['action']
    name = None if action == 'capacity' else identity(payload)
    if payload.get('universeId') and payload['universeId'] != name:
        raise ValueError('The retained environment identity changed.')
    snapshot = payload.get('snapshot')
    try:
        if action == 'capacity':
            capacity = cli(config, env, ['capacity'])
            scope = capacity['scope']
        else:
            response = checked(incus(config, env, ['query', '/1.0']))
            if response['truncated']:
                raise BackendFailure('response_limit', 'The backend identity observation exceeded its bound.', admitted=False)
            backend = json.loads(response['stdout'])['environment']
            scope = {'project': backend['project'], 'daemon': backend['certificate_fingerprint']}
        if scope['project'] != config['project'] or (config.get('daemonFingerprint') and scope['daemon'] != config['daemonFingerprint']):
            raise BackendFailure('backend_changed', 'The configured Incus daemon or project changed.', admitted=False)
        if action == 'capacity':
            return {'ok': True, 'status': 'available' if capacity['available'] else 'waiting', 'capacity': capacity, 'available': capacity['available']}
        if action == 'prepare':
            from digital_twin_universe import lib
            snapshot = common.serialize(lib.export_repository_snapshot(Path(payload['source']), root / 'source.tar'))
            (root / 'snapshot.json').write_text(json.dumps(snapshot))
            return {'ok': True, 'universeId': name, 'snapshot': snapshot}
        if action == 'start':
            common.source_identity(name, snapshot)
            if file_digest(root / 'source.tar', 512 * 1024 ** 2)[0] != snapshot['archive_sha256']:
                raise BackendFailure('snapshot_changed', 'The exact source archive changed; nothing was launched.', admitted=False)
            profile = {'name': 'unified-owned-offline', 'description': 'Owned offline test environment',
                'base': {'image': 'local:' + config['image'], 'config': {'security.privileged': 'false', 'security.nesting': 'false', 'limits.memory': '1GiB', 'limits.cpu': '2', 'limits.processes': '128'}},
                'provision': {'setup_cmds': ['mkdir -p /workspace /artifacts', 'python3 --version']}}
            profile_path = root / 'incus-profile.json'
            profile_path.write_text(json.dumps(profile))
            cli(config, env, ['launch', str(profile_path), '--name', name, '--owner-root', payload['rootOwner'], '--request-id', payload['operationId'], '--offline'], 600)
            observe(config, env, name, payload, confinement=True)
            if common.source_ready(root, name, snapshot):
                return {'ok': True, 'universeId': name, 'sourceReady': True, 'status': 'running', 'services': []}
            if (root / 'source-attempt.json').exists():
                raise BackendFailure('source_unknown', 'Source installation was previously attempted; it was not repeated.')
            persist_attempt(root, common.source_identity(name, snapshot))
            checked(incus(config, env, ['file', 'push', str(root / 'source.tar'), name + '/artifacts/.source.tar'], 120))
            unpack = "import tarfile; tarfile.open('/artifacts/.source.tar').extractall('/workspace', filter='data'); import os; os.unlink('/artifacts/.source.tar')"
            checked(incus(config, env, ['exec', name, '--', 'python3', '-c', unpack], 120))
            common.retain_source_ready(root, name, snapshot)
            return {'ok': True, 'universeId': name, 'sourceReady': True, 'status': 'running', 'services': []}
        request, instance = observe(config, env, name, payload, confinement=action in {'read', 'execute'})
        state = request['state']
        ready = common.source_ready(root, name, snapshot)
        if action == 'read':
            status = {'not_recorded': 'not-started', 'removed': 'removed', 'failed': 'not-started', 'stopped': 'stopped'}.get(state, 'running' if state == 'ready' and ready else 'unknown')
            return {'ok': True, 'universeId': name, 'sourceReady': ready, 'status': status, 'services': [], 'launch': request}
        if instance is None:
            raise BackendFailure('launch_unknown', 'No owned instance is confirmed for this operation.')
        if action == 'execute':
            if state != 'ready' or not ready:
                raise BackendFailure('source_unknown', 'The exact source and running environment are not confirmed. No check started.', admitted=False)
            seconds = payload.get('timeoutSeconds', 60)
            if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 300:
                raise ValueError('The command timeout must be between 1 and 300 seconds.')
            command = payload['command']
            if not isinstance(command, str) or not command or len(command) > 16384:
                raise ValueError('The command is invalid.')
            result = incus(config, env, ['exec', name, '--cwd', '/workspace', '--', '/bin/sh', '-lc', command], seconds)
            return {'ok': True, 'universeId': name, 'result': result}
        if action == 'stop':
            if instance.get('status') != 'Stopped':
                checked(incus(config, env, ['stop', name], 120))
            _, after = observe(config, env, name, payload)
            stopped = after is not None and after.get('status') == 'Stopped'
            return {'ok': stopped, 'universeId': name, 'status': 'stopped' if stopped else 'unknown', 'message': 'Stopped; source and artifacts remain retained.' if stopped else 'Stop is not confirmed.'}
        if action == 'remove':
            if instance.get('status') != 'Stopped':
                raise BackendFailure('not_stopped', 'Removal requires an observed stopped environment.', admitted=False)
            if shutil.disk_usage(root).free < EXPORT_BYTES + FREE_RESERVE_BYTES:
                raise BackendFailure('retention_storage', 'There is not enough free space for a bounded retained export. The stopped environment remains.', admitted=False)
            destination = root / ('retained-' + hashlib.sha256(payload['requestId'].encode()).hexdigest())
            destination.mkdir(mode=0o700)
            archive = destination / 'instance.tar'
            try:
                checked(incus(config, env, ['export', name, str(archive), '--instance-only', '--compression', 'none'], 300, watched_file=archive))
                digest, size = file_digest(archive, EXPORT_BYTES)
                with archive.open('rb') as stream:
                    os.fsync(stream.fileno())
                archives = [{'id': name, 'source': '/', 'retainedPaths': ['/workspace', '/artifacts'], 'destination': str(archive), 'bytes': size, 'sha256': digest}]
                common.retain_manifest(destination, archives)
            except Exception:
                (destination / 'incomplete.json').write_text(json.dumps({'status': 'unknown', 'environmentPreserved': True, 'partialArchive': str(archive)}))
                raise
            cli(config, env, ['destroy', name, '--owner-root', payload['rootOwner'], '--request-id', payload['operationId']], 120)
            after = cli(config, env, ['launch-status', payload['operationId']])
            removed = after['state'] == 'removed'
            return {'ok': removed, 'universeId': name, 'status': 'removed' if removed else 'unknown', 'archives': archives,
                    'message': 'The complete source and artifact filesystem was retained before removal.' if removed else 'The export is retained; removal is not confirmed.'}
        raise ValueError('Unsupported managed test environment action.')
    except BackendFailure as exc:
        return {'ok': False, 'universeId': name, 'snapshot': snapshot, 'status': 'not-started' if action == 'start' and not exc.admitted else 'unknown',
                'code': exc.code, 'effectAdmitted': exc.admitted,
                'admissionDenied': action == 'start' and not exc.admitted and exc.code in {'execution_capacity', 'retained_capacity', 'storage_pressure', 'storage_unknown', 'admission_busy'}, 'capacity': exc.details.get('capacity'), 'message': str(exc)}


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read(65537))
        value = execute_call(payload)
    except Exception:
        value = {'ok': False, 'status': 'unknown', 'message': 'The qualified backend did not return a confirmed result. Inspect retained state; this request will not replay.'}
    sys.stdout.write(json.dumps(value))


if __name__ == '__main__':
    main()
