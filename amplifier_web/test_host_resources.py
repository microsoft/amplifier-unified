"""Resource reservations for cooperating native-Linux runners of one OS account.

This is an admission index, never a job scheduler. Every entry is bound to the
canonical environment allocation and original launch operation. Unknown work is
held until the existing owner obtains positive stop/removal evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import stat
import struct
import subprocess
import uuid

FIELDS = ('maxReservedCpu', 'maxReservedMemoryMiB', 'minAvailableMemoryMiB',
          'minFreeBytes', 'maxLoadPercent', 'maxPreparing')
MAXIMUM = 2**53 - 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def identity():
    if platform.system() != 'Linux':
        return None
    try:
        value = Path('/etc/machine-id').read_text().strip()
        if len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
            return None
        return digest(['native-linux', value])
    except OSError:
        return None


def directory():
    # HOME/XDG and app data roots can vary by installation/helper. The account
    # database plus physical host identity is the common authority location.
    import pwd
    host = identity()
    if not host:
        raise ValueError('A registered native Linux machine authority is unavailable.')
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/amplifier-test-resources' / host


def activated_daemon(path, daemon_names):
    """Verify root systemd's fixed socket-to-service binding, never a proxy."""
    units = {'incusd': 'incus', 'dockerd': 'docker'}
    for name in daemon_names:
        unit = units.get(name)
        if unit is None:
            continue
        def property_value(suffix, field):
            result = subprocess.run(['/usr/bin/systemctl', 'show', unit + suffix,
                '--property=' + field, '--value'], capture_output=True, timeout=3,
                env={'PATH': os.defpath, 'LANG': 'C'})
            if result.returncode or len(result.stdout) > 4096:
                raise ValueError('The activated daemon could not be verified.')
            return result.stdout.decode().strip()
        listens = property_value('.socket', 'Listen').splitlines()
        if not any(value.endswith(' (Stream)') and Path(value[:-9]).resolve() == Path(path).resolve() for value in listens):
            continue
        if property_value('.socket', 'Triggers') != unit + '.service':
            continue
        pid = property_value('.service', 'MainPID')
        if not pid.isdigit() or int(pid) <= 1:
            continue
        if Path('/proc/' + pid + '/comm').read_text().strip() != name:
            continue
        if property_value('.service', 'MainPID') == pid:
            return True
    return False


def local_target(endpoint, daemon_names):
    """Prove a local kernel peer; socket aliases resolve to the same host budget.

    A desktop VM, TCP endpoint, SSH tunnel, remote Incus or unrecognized proxy
    does not acquire an authority merely because the client runs locally.
    """
    unavailable = {'supported': False, 'reason': 'This runner needs a registered authority on its execution machine. Existing backend limits still apply.'}
    if not identity() or not isinstance(endpoint, str) or not endpoint.startswith('unix://'):
        return unavailable
    try:
        path = endpoint[7:]
        if not path and daemon_names == {'incusd'}:
            path = '/var/lib/incus/unix.socket'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(path)
            pid, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        name = Path(f'/proc/{pid}/comm').read_text().strip()
        if name not in daemon_names and not (pid == 1 and uid == 0 and name == 'systemd' and activated_daemon(path, daemon_names)):
            return unavailable
        return {'supported': True, 'machineId': identity(), 'scope': 'native_linux_host_account'}
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return unavailable


def observe():
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    cpu = os.cpu_count() or 1
    disk = os.statvfs(directory())
    return {'cpuCount': cpu, 'memoryMiB': int(memory['MemTotal'].split()[0]) // 1024,
            'availableMemoryMiB': int(memory['MemAvailable'].split()[0]) // 1024,
            'freeBytes': disk.f_bavail * disk.f_frsize,
            'loadPercent': round(os.getloadavg()[0] * 100 / cpu)}


def validate(values):
    if not isinstance(values, dict) or set(values) - set(FIELDS):
        raise ValueError('Choose only supported machine resource limits.')
    if any(type(v) is not int or not 0 <= v <= MAXIMUM for v in values.values()):
        raise ValueError('Machine limits must be whole numbers of zero or more.')
    return dict(values)


def read(path, default=None):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return default
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('The machine resource record is unsafe.')
        return json.load(stream)


def write(path, value):
    temporary = path.with_name('.' + uuid.uuid4().hex)
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(*, blocking=True):
    import fcntl
    root = directory()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('The machine resource directory cannot traverse links.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('The machine resource directory must be private.')
    fd = os.open(root / 'authority.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('The machine resource lock is unsafe.')
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield root
    finally:
        os.close(fd)


def policy(root):
    value = read(root / 'policy.json', {'version': 1, 'limits': {}})
    if not isinstance(value, dict) or set(value) != {'version', 'limits'} or value['version'] != 1:
        raise ValueError('The machine resource policy format is invalid.')
    validate(value['limits'])
    return value


def effective(policy_value, observation):
    defaults = {'maxReservedCpu': observation['cpuCount'] * 2,
                'maxReservedMemoryMiB': observation['memoryMiB'] * 4 // 5,
                'minAvailableMemoryMiB': 1024, 'minFreeBytes': 10 * 1024**3,
                'maxLoadPercent': 200, 'maxPreparing': 0}
    return {**defaults, **policy_value['limits']}


def leases(root):
    paths = list(root.glob('lease-*.json'))
    if len(paths) > 4096:
        raise ValueError('Too many retained machine reservations; reconcile owned work.')
    result = []
    for path in paths:
        value = read(path)
        if (not isinstance(value, dict) or value.get('version') != 1
                or not isinstance(value.get('binding'), dict)
                or set(value['binding']) != {'id', 'sessionId', 'launchOperationId', 'backendKey'}
                or any(not isinstance(v, str) or not v for v in value['binding'].values())
                or not isinstance(value.get('resources'), dict)
                or path.name != 'lease-' + digest(value['binding']) + '.json'
                or value.get('phase') not in {'preparing', 'launching', 'running'}):
            raise ValueError('A machine resource reservation is invalid.')
        request = value['resources']
        if set(request) != {'cpus', 'memoryMiB'} or any(type(v) is not int or v <= 0 for v in request.values()):
            raise ValueError('A machine resource reservation is invalid.')
        result.append(value)
    return result


def snapshot():
    if not identity():
        return {'supported': False, 'reason': 'Shared machine budgets currently require a native Linux runner. Backend limits remain available.'}
    with locked() as root:
        saved, observation, reservations = policy(root), observe(), leases(root)
        return {'supported': True, 'machineId': identity(), 'revision': digest(saved),
                'limits': saved['limits'], 'effective': effective(saved, observation), 'observed': observation,
                'reserved': {'cpus': sum(r['resources']['cpus'] for r in reservations),
                             'memoryMiB': sum(r['resources']['memoryMiB'] for r in reservations),
                             'pendingMemoryMiB': sum(r['resources']['memoryMiB'] for r in reservations if r['phase'] != 'running'),
                             'preparing': sum(r['phase'] == 'preparing' for r in reservations), 'count': len(reservations)},
                'scope': 'Native local runners using this OS account, across Amplifier installations and Docker / Incus. Remote runners, other accounts and older clients are not governed.'}


def save(expected_revision, limits):
    validate(limits)
    with locked() as root:
        if digest(policy(root)) != expected_revision:
            raise ValueError('Machine limits changed in another installation. Refresh before saving.')
        write(root / 'policy.json', {'version': 1, 'limits': limits})
    return snapshot()


def binding(row):
    return {key: row[key] for key in ('id', 'sessionId', 'launchOperationId', 'backendKey')}


def reserve(row, target):
    if not target or not target.get('supported'):
        return {'supported': False, 'reason': (target or {}).get('reason', 'This runner has no registered machine authority. Backend limits still apply.')}
    if target.get('machineId') != identity():
        raise ValueError('The test execution machine changed before resource admission.')
    with locked() as root:
        original = binding(row)
        existing = read(root / ('lease-' + digest(original) + '.json'))
        resources = {key: row['resourceRequest'][key] for key in ('cpus', 'memoryMiB')}
        if existing is not None:
            if existing['binding'] != original or existing['resources'] != resources:
                raise ValueError('The retained machine reservation changed.')
            return existing
        saved, observation, pending = policy(root), observe(), leases(root)
        limits = effective(saved, observation)
        cpu = sum(r['resources']['cpus'] for r in pending)
        memory = sum(r['resources']['memoryMiB'] for r in pending)
        preparing = bool(row.get('preparationRequired'))
        blockers = []
        for key, used in (('maxReservedCpu', cpu + resources['cpus']), ('maxReservedMemoryMiB', memory + resources['memoryMiB']),
                          ('maxPreparing', sum(r['phase'] == 'preparing' for r in pending) + int(preparing))):
            if limits[key] and used > limits[key]:
                blockers.append(key)
        # Pending reservations may not be represented in observed usage yet.
        # The absolute reservation ceiling and headroom observation are separate:
        # do not subtract all running reservations twice from MemAvailable.
        if limits['minAvailableMemoryMiB'] and observation['availableMemoryMiB'] < resources['memoryMiB'] + limits['minAvailableMemoryMiB'] + sum(r['resources']['memoryMiB'] for r in pending if r['phase'] != 'running'):
            blockers.append('memoryPressure')
        if observation['freeBytes'] < limits['minFreeBytes']:
            blockers.append('diskPressure')
        if limits['maxLoadPercent'] and observation['loadPercent'] > limits['maxLoadPercent']:
            blockers.append('cpuPressure')
        if blockers:
            return {'supported': True, 'admitted': False, 'blockers': blockers, 'observed': observation, 'limits': limits}
        value = {'version': 1, 'supported': True, 'admitted': True, 'machineId': identity(),
                 'binding': original, 'resources': resources, 'phase': 'preparing' if preparing else 'launching',
                 'policyRevision': digest(saved), 'limits': limits}
        write(root / ('lease-' + digest(original) + '.json'), value)
        return value


def held(row, *, blocking=True):
    """Inspect one exact existing lease; never initialize a machine authority."""
    if not identity() or not directory().exists():
        return False
    with locked(blocking=blocking) as root:
        original = binding(row)
        saved = read(root / ('lease-' + digest(original) + '.json'))
        if saved is None:
            return False
        resources = {key: row['resourceRequest'][key] for key in ('cpus', 'memoryMiB')}
        if (saved.get('admitted') is not True or saved.get('machineId') != identity()
                or saved.get('binding') != original or saved.get('resources') != resources):
            raise ValueError('The retained machine reservation changed.')
        receipt = row.get('hostReservation')
        if 'hostReservation' in row and not isinstance(receipt, dict):
            raise ValueError('The saved machine reservation is malformed.')
        if receipt and any(receipt.get(key) != saved.get(key) for key in ('machineId', 'binding', 'resources')):
            raise ValueError('The saved machine reservation belongs to another owner.')
        return True


def settle(row, *, release=False, phase='running', blocking=True):
    """Only the canonical owner calls this after confirmed effects/cleanup."""
    receipt = row.get('hostReservation', {})
    if not receipt.get('admitted'):
        # A process can die after reserving but before the canonical row stores
        # the index receipt. Positive owner cleanup can recover that exact gap.
        # Do not initialize authority for historical or remote-only resources.
        if not release or not identity() or not directory().exists():
            return
        with locked(blocking=blocking) as root:
            receipt = read(root / ('lease-' + digest(binding(row)) + '.json'), {})
        if not receipt.get('admitted'):
            return
    if receipt.get('machineId') != identity() or receipt.get('binding') != binding(row):
        raise ValueError('The machine reservation belongs to another owner or host.')
    with locked(blocking=blocking) as root:
        path = root / ('lease-' + digest(binding(row)) + '.json')
        existing = read(path)
        if existing is None:
            return
        if existing['binding'] != receipt['binding'] or existing['resources'] != receipt['resources']:
            raise ValueError('The saved machine reservation changed.')
        if release:
            path.unlink()
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        elif existing['phase'] != phase:
            if phase not in {'launching', 'running'}:
                raise ValueError('Unsupported reservation phase.')
            write(path, {**existing, 'phase': phase})
