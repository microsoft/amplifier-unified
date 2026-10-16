#!/usr/bin/env python3
"""Prepare a sibling combined replica; never start a service or historical work."""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess

BASE = Path('/var/lib/unified-scale-runtime-20261015')
READING = Path('/var/lib/unified-scale-replica-20261015')
CODE = Path('/opt/unified-scale-runtime-20261015')
UNIT = 'unified-scale-runtime-20261015.service'
APP = '/home/bkrabach/.amplifier-unified'
NATIVE = '/home/bkrabach/.amplifier'


def main(args):
    if os.geteuid() != 0 or Path('/run/systemd/container').read_text().strip() != 'lxc':
        raise RuntimeError('Owned DTU root preparation only')
    if BASE.exists() or (Path('/etc/systemd/system') / UNIT).exists():
        raise RuntimeError('Sibling already exists; inspect rather than overwrite')
    if not (CODE / 'source/amplifier_web/server.py').is_file():
        raise RuntimeError('Exact frozen source required before preparation')
    os.umask(0o077)
    account = pwd.getpwnam('unified-dev')
    uid, gid = account.pw_uid, account.pw_gid
    BASE.mkdir(mode=0o700)
    os.chown(BASE, uid, gid)
    root, writable = BASE / 'rootfs', BASE / 'writable'
    root.mkdir(mode=0o755); root.chmod(0o755)
    writable.mkdir(mode=0o700); os.chown(writable, uid, gid)

    def folder(path, owner=False):
        path.mkdir(parents=True, exist_ok=True, mode=0o755)
        if owner:
            os.chown(path, uid, gid); path.chmod(0o700)
        return path

    def target(path, owner=False):
        return folder(root / str(path).lstrip('/'), owner)

    mounts = []
    def bind(source, destination, readonly):
        source = Path(source)
        if not source.is_dir() or source.is_symlink():
            raise RuntimeError('Expected a real owned directory for mount')
        target(destination)
        mounts.append({'source': str(source), 'target': str(destination), 'readOnly': readonly})

    for path in ('/usr', '/proc', '/dev', '/run/replica', '/etc', '/tmp', APP, NATIVE,
                 account.pw_dir, '/opt/synthetic-workspace', '/opt/synthetic-config'):
        target(path, path in ('/tmp', account.pw_dir, '/opt/synthetic-workspace', '/opt/synthetic-config'))
    for name in ('bin', 'sbin', 'lib'):
        (root / name).symlink_to('usr/' + name)
    (root / 'etc/passwd').write_text(f'root:x:0:0:root:/root:/bin/sh\nunified-dev:x:{uid}:{gid}:Replica:{account.pw_dir}:/bin/sh\n')
    (root / 'etc/group').write_text(f'root:x:0:\nunified-dev:x:{gid}:\n')
    (root / 'etc/nsswitch.conf').write_text('passwd: files\ngroup: files\nhosts: files\n')
    (root / 'etc/hosts').write_text('127.0.0.1 localhost\n::1 localhost\n')
    bind('/usr', '/usr', True)
    bind(CODE / 'source', '/opt/unified-dev/source', True)
    bind(CODE / 'tools', '/opt/replica-tools', True)
    # A stable dependency alias avoids nesting a bind below the source mount.
    bind('/opt/unified-scale-replica-20261015/dependencies', '/opt/replica-venv', True)
    for interpreter in ('/opt/unified-dev/source/.venv/bin/python', args.worker_python):
        real = Path(interpreter).resolve().parent.parent
        if not any(row['target'] == str(real) for row in mounts):
            bind(real, real, True)
        alias = Path(os.readlink(interpreter)).parent.parent
        if alias.is_absolute() and alias != real:
            location = root / str(alias).lstrip('/')
            location.parent.mkdir(parents=True, exist_ok=True)
            if not location.exists(): location.symlink_to(real.name)
    worker_env = Path(args.worker_python).parent.parent
    bind(worker_env, '/opt/replica-worker', True)
    for dependency in args.dependency_root:
        bind(dependency, dependency, True)

    seed = READING / 'seed'
    app = folder(writable / 'app', True)
    databases = []
    for source in sorted((seed / 'app').glob('*.sqlite3')):
        destination = app / source.name
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as original, sqlite3.connect(destination) as clone:
            original.execute('BEGIN')
            original.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
            original.backup(clone, pages=256)
        destination.chmod(0o600); os.chown(destination, uid, gid)
        databases.append({'name': source.name, 'bytes': destination.stat().st_size})
    # Config/auth are fresh. Existing blob data has a read-only lower and a
    # private upper; no expanded corpus or historical files are duplicated.
    overlays = []
    for name in ('sessions', 'artifacts', 'attachments'):
        lower = seed / 'app' / name
        if not lower.exists(): continue
        upper = folder(writable / (name + '-upper'), True)
        work = folder(writable / (name + '-work'), True)
        merged = folder(app / name, True)
        subprocess.run(['mount', '-t', 'overlay', 'overlay', '-o',
                        f'lowerdir={lower},upperdir={upper},workdir={work}', str(merged)], check=True)
        overlays.append({'lowerReadOnly': str(lower), 'upper': str(upper), 'work': str(work), 'merged': str(merged)})
    bind(app, APP, False)
    # Preserve every native project path. Foundation requires owner identity
    # on each ancestor; the scaffold is nonwritable after preparation.
    projects = target(NATIVE + '/projects')
    native_count = 0
    for source in sorted((seed / 'native/projects').iterdir()):
        if not source.is_dir() or source.is_symlink():
            raise RuntimeError('Unexpected native project type')
        if any(char in source.name for char in ':"\n%'):
            raise RuntimeError('Native project needs explicit systemd path escaping')
        bind(source, NATIVE + '/projects/' + source.name, True)
        native_count += 1
    # The synthetic directory slug is computed using the frozen app helper at
    # runtime and must already exist as a separately writable directory.
    synthetic_slug = '-opt-synthetic-workspace'
    # Foundation uses a stable project_slug function; do not guess its encoding.
    proc = subprocess.run(['/opt/unified-dev/source/.venv/bin/python', '-c',
        'from amplifier_web.session_files import project_slug; print(project_slug("/opt/synthetic-workspace"))'],
        env={**os.environ, 'PYTHONPATH': str(CODE / 'source')}, check=True, capture_output=True, text=True)
    synthetic_slug = proc.stdout.strip()
    if not synthetic_slug or '/' in synthetic_slug or (projects / synthetic_slug).exists():
        raise RuntimeError('Synthetic project overlaps captured identity')
    folder(projects / synthetic_slug, True)
    os.chown(projects, uid, gid); projects.chmod(0o555)
    # Fresh shared config may be written only by the fixture, not into copied
    # project directories. The native parent holds no copied keys/settings.
    os.chown(root / NATIVE.lstrip('/'), uid, gid)
    inventory = json.loads((READING / 'workspaces-source.json').read_text())
    for row in inventory['workspaces']:
        path = row.get('path', '')
        if row.get('supported') and row.get('isDir') and path.startswith('/') and '..' not in Path(path).parts:
            target(path)  # root-owned empty skeleton, not writable project code
    run = folder(BASE / 'run', True)
    bind(run, '/run/replica', False)
    marker = {'purpose': 'private-scale-runtime-20261015', 'appHome': APP, 'nativeHome': NATIVE,
              'sourceRevision': args.revision, 'syntheticWorkspace': '/opt/synthetic-workspace',
              'syntheticNativeProject': synthetic_slug, 'databases': databases,
              'nativeProjectMounts': native_count, 'mounts': mounts, 'overlays': overlays,
              'captureEventsComplete': False, 'noApplicationStarted': True}
    # Preparation uses a private umask. Root-owned namespace scaffolding must
    # remain traversable after systemd drops to the unprivileged app account.
    for current, directories, files in os.walk(root, followlinks=False):
        path = Path(current)
        if path.stat().st_uid == 0: path.chmod(0o755)
    for name in ('passwd', 'group', 'nsswitch.conf', 'hosts'):
        (root / 'etc' / name).chmod(0o644)
    (root / 'replica-owner.json').write_text(json.dumps(marker, indent=2) + '\n')
    (root / 'replica-owner.json').chmod(0o644)
    lines = ['[Unit]', 'Description=Owned copied-scale synthetic-runtime acceptance', '[Service]',
        'Type=simple', 'User=unified-dev', 'Group=unified-dev', f'RootDirectory={root}',
        'WorkingDirectory=/opt/unified-dev/source']
    for row in mounts:
        key = 'BindReadOnlyPaths' if row['readOnly'] else 'BindPaths'
        def quote(value):
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        lines.append(f'{key}={quote(row["source"])}:{quote(row["target"])}')
    lines += ['PrivateNetwork=yes', 'PrivateDevices=yes', 'NoNewPrivileges=yes',
        'UMask=0077', 'MemoryMax=12G', 'CPUQuota=600%', 'TasksMax=512', 'Restart=no',
        'TimeoutStopSec=45',
        'ExecStart=/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 '
        'PYTHONPATH=/opt/unified-dev/source:/opt/unified-dev/source/tests/fixtures:/opt/replica-tools '
        f'AMPLIFIER_HOME={NATIVE} AMPLIFIER_WEB_HOME={APP} '
        'AMPLIFIER_SESSION_STATE_HOME=/tmp/synthetic-session-state '
        'AMPLIFIER_UNIFIED_IMPORT_HOME=/tmp/excluded-legacy AMPLIFIER_RUNTIME_IMMUTABLE=1 '
        'XDG_CACHE_HOME=/tmp/private-cache /opt/replica-venv/bin/python /opt/replica-tools/scale_runtime_app.py']
    unit = Path('/etc/systemd/system') / UNIT
    unit.write_text('\n'.join(lines) + '\n')
    dropin = unit.with_name(UNIT + '.d'); dropin.mkdir()
    (dropin / 'zzzz-replica-isolation.conf').write_text('[Service]\nPrivateNetwork=yes\nNoNewPrivileges=yes\n')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if subprocess.check_output(['systemctl', 'show', UNIT, '-p', 'PrivateNetwork', '--value'], text=True).strip() != 'yes':
        raise RuntimeError('PrivateNetwork override failed')
    (BASE / 'prepared.json').write_text(json.dumps(marker, indent=2) + '\n')
    print(json.dumps({'prepared': True, 'started': False, 'nativeProjectMounts': native_count,
                      'databaseBytes': sum(row['bytes'] for row in databases)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision', required=True)
    parser.add_argument('--worker-python', required=True)
    parser.add_argument('--dependency-root', action='append', default=[])
    main(parser.parse_args())
