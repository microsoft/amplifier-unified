#!/usr/bin/env python3
"""Prepare, but never start, the task-owned DTU reading replica service."""
import json
import os
from pathlib import Path
import pwd
import sqlite3
import subprocess

BASE = Path('/var/lib/unified-scale-replica-20261015')
CODE = Path('/opt/unified-scale-replica-20261015')
UNIT = 'unified-scale-replica-20261015.service'
REVISION = '1d993e0bb32bc90b75fc66f56f1b9014c2d63f87'


def private_json(path, value, uid=0, gid=0):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    os.chown(path, uid, gid)


def main():
    assert os.geteuid() == 0
    assert Path('/run/systemd/container').read_text().strip() == 'lxc'
    assert BASE.is_dir() and not BASE.is_symlink() and CODE.is_dir()
    account = pwd.getpwnam('unified-dev')
    uid, gid = account.pw_uid, account.pw_gid
    # The unprivileged extractor and service must traverse their own private
    # parent; an earlier root-created 0700 parent otherwise blocks later shards.
    os.chown(BASE, uid, gid)
    BASE.chmod(0o700)
    seed = BASE / 'seed'
    # No initial scale baseline from missing catalogs, primary history or blobs.
    expected = ['databases-0', 'ancillary-0'] + [f'{kind}-{i}' for kind in ('primary', 'artifacts') for i in range(8)]
    captures = []
    for name in expected:
        receipt = json.loads((seed / 'capture' / (name + '.json')).read_text())
        if any(row.get('stable') is False for row in receipt['files']):
            raise ValueError('Unstable initial capture must be reconciled before startup')
        captures.append({'name': name, 'files': len(receipt['files']),
                         'bytes': sum(row['bytes'] for row in receipt['files'])})
    root = BASE / 'rootfs'
    if (root / 'replica-owner.json').exists():
        raise ValueError('Replica root already prepared; preserve it rather than reset')
    root.mkdir(mode=0o700, exist_ok=True)
    def directory(absolute, owned=False):
        path = root / absolute.lstrip('/')
        path.mkdir(parents=True, exist_ok=True, mode=0o755)
        if owned:
            os.chown(path, uid, gid)
            path.chmod(0o700)
        return path
    for value in ('/usr', '/proc', '/dev', '/run/replica', '/etc', '/tmp', '/opt/unified-dev/source',
                  '/opt/replica-tools', '/home/bkrabach/.amplifier', '/home/bkrabach/.amplifier-unified'):
        directory(value)
    directory('/tmp', owned=True)
    directory(account.pw_dir, owned=True)
    for name, destination in (('bin', 'usr/bin'), ('sbin', 'usr/sbin'), ('lib', 'usr/lib')):
        (root / name).symlink_to(destination)
    python = Path('/opt/unified-dev/source/.venv/bin/python').resolve().parent.parent
    directory(str(python))
    python_alias = Path(os.readlink('/opt/unified-dev/source/.venv/bin/python')).parent.parent
    if python_alias != python:
        (root / str(python_alias).lstrip('/')).symlink_to(python.name)
    directory('/opt/replica-venv')
    dependencies = CODE / 'dependencies'
    dependencies.mkdir(exist_ok=True)
    if not os.path.ismount(dependencies):
        subprocess.run(['mount', '--bind', '/opt/unified-dev/source/.venv', str(dependencies)], check=True)
        subprocess.run(['mount', '-o', 'remount,bind,ro', str(dependencies)], check=True)
    # Minimal account/DNS files, never the DTU's account secrets or credentials.
    (root / 'etc/passwd').write_text(f'root:x:0:0:root:/root:/bin/sh\nunified-dev:x:{uid}:{gid}:Replica:{account.pw_dir}:/bin/sh\n')
    (root / 'etc/group').write_text(f'root:x:0:\nunified-dev:x:{gid}:\n')
    (root / 'etc/nsswitch.conf').write_text('passwd: files\ngroup: files\nhosts: files\n')
    (root / 'etc/hosts').write_text('127.0.0.1 localhost\n::1 localhost\n')
    inventory = json.loads((BASE / 'workspaces-source.json').read_text())
    made, missing, unsupported = [], [], []
    for row in inventory['workspaces']:
        value = row['path']
        if not row.get('supported') or not value.startswith('/') or '..' in Path(value).parts:
            unsupported.append(value)
            continue
        if row.get('isDir'):
            directory(value, owned=True)
            made.append(value)
        else:
            missing.append(value)
    # The copied scheduler has no active schedules in this capture. Fail closed
    # if a different snapshot introduces any until its pause conversion is reviewed.
    db = sqlite3.connect(seed / 'app/schedules.sqlite3')
    schedules = db.execute('SELECT count(*) FROM schedules').fetchone()[0]
    db.close()
    if schedules:
        raise ValueError('Nonempty schedules require explicit paused-clone conversion')
    config = seed / 'app/config'
    private_json(config / 'notifications.json', {'desktop': False, 'enabled': False,
        'server': 'https://127.0.0.1:1', 'topic': '', 'token': '', 'preview': False,
        'completionBadge': False}, uid, gid)
    private_json(seed / 'app/diagnostics/config.json', {'enabled': False, 'destinations': [],
        'streams': [], 'retentionDays': 30, 'maxRecords': 25000, 'providerRequests': False}, uid, gid)
    for parent in (config, seed / 'app/diagnostics'):
        os.chown(parent, uid, gid)
    runtime = BASE / 'run'
    runtime.mkdir(mode=0o700, exist_ok=True)
    os.chown(runtime, uid, gid)
    marker = {'purpose': 'private-scale-replica-20261015', 'sourceRevision': REVISION,
              'appHome': '/home/bkrabach/.amplifier-unified', 'nativeHome': '/home/bkrabach/.amplifier',
              'initialCapture': captures, 'eventsComplete': False,
              'workspaceSkeletons': len(made), 'originalMissingWorkspaces': len(missing),
              'unsupportedWorkspacePaths': len(unsupported)}
    private_json(root / 'replica-owner.json', marker, uid, gid)
    # Traverse the private root while keeping snapshot directories private.
    root.chmod(0o755)
    unit = f'''[Unit]
Description=Private read-only-execution scale replica 20261015
After=network.target

[Service]
Type=simple
User=unified-dev
Group=unified-dev
RootDirectory={root}
WorkingDirectory=/opt/unified-dev/source
BindReadOnlyPaths=/usr
BindReadOnlyPaths={CODE}/source:/opt/unified-dev/source
BindReadOnlyPaths={dependencies}:/opt/replica-venv
BindReadOnlyPaths={python}
BindReadOnlyPaths={CODE}/tools:/opt/replica-tools
BindPaths={seed}/app:/home/bkrabach/.amplifier-unified
BindPaths={seed}/native:/home/bkrabach/.amplifier
BindPaths={runtime}:/run/replica
PrivateNetwork=yes
PrivateDevices=yes
NoNewPrivileges=yes
UMask=0077
MemoryMax=4G
CPUQuota=200%
TasksMax=256
TimeoutStopSec=30
Restart=no
ExecStart=/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 AMPLIFIER_HOME=/home/bkrabach/.amplifier AMPLIFIER_WEB_HOME=/home/bkrabach/.amplifier-unified AMPLIFIER_UNIFIED_IMPORT_HOME=/tmp/excluded-legacy-import AMPLIFIER_SESSION_STATE_HOME=/tmp/private-session-state PYTHONPATH=/opt/unified-dev/source PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=/tmp/replica-cache /opt/replica-venv/bin/python /opt/replica-tools/scale_replica_app.py
'''
    path = Path('/etc/systemd/system') / UNIT
    if path.exists():
        raise ValueError('Replica service unit already exists; inspect instead of overwriting')
    path.write_text(unit)
    override = path.with_name(path.name + '.d')
    override.mkdir()
    (override / 'zzzz-replica-isolation.conf').write_text('[Service]\nPrivateNetwork=yes\nNoNewPrivileges=yes\n')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    effective = subprocess.check_output(['systemctl', 'show', UNIT, '-p', 'PrivateNetwork', '--value'], text=True).strip()
    if effective != 'yes':
        raise ValueError('Inherited unit configuration disabled private networking')
    private_json(BASE / 'prepared.json', {**marker, 'unit': UNIT, 'started': False,
        'runtimeExecution': False, 'network': 'own loopback only',
        'existingDevelopmentInstanceChanged': False})
    print(json.dumps({'prepared': True, 'started': False, 'initialCaptureFiles': sum(x['files'] for x in captures),
                      'workspaceSkeletons': len(made), 'missingWorkspaces': len(missing)}))


if __name__ == '__main__':
    main()
