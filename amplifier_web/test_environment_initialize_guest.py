"""Seed writable project dependencies after the exact source is installed."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path


def verify_manifests(workspace, project, locks):
    directory = workspace / project['directory']
    for name in ('package.json', 'package-lock.json'):
        expected_path = '/'.join(filter(None, (project['manifestDirectory'], name)))
        path = directory / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != locks[expected_path]:
            raise ValueError('The selected Node dependency manifests changed.')


def prepare_client(prefix, workspace, project, locks):
    directory = workspace / project['directory']
    if project.get('preparedManifestsOnly'):
        # Repository delivery snapshots carry these three bounded ignored
        # inputs, not a copied dependency tree or a second authored client.
        names = {'package.json', 'package-lock.json', '.client-source.json'}
        if directory.is_symlink() or set(path.name for path in directory.iterdir()) != names:
            raise ValueError('Prepared client manifest staging contains unexpected files; source was retained.')
        verify_manifests(workspace, project, locks)
        record = directory / '.client-source.json'
        if (record.is_symlink() or json.loads(record.read_text())
                != json.loads((workspace / 'components/web-client.source.json').read_text())):
            raise ValueError('Prepared client manifests belong to another selection.')
        # Only this exact generated manifest-only placeholder may be removed.
        shutil.rmtree(directory)
    env = dict(os.environ)
    env['PATH'] = str(prefix / 'node-runtime/bin') + os.pathsep + env.get('PATH', '')
    subprocess.run([str(prefix / 'venv/bin/python'),
                    str(workspace / 'scripts/web_client_build.py'), 'prepare'],
                   cwd=workspace, env=env, check=True, timeout=120)
    verify_manifests(workspace, project, locks)


def verify_runtime_workdir(value):
    """Check the fixed disposable mount before any workload/harness can run."""
    if (not isinstance(value, dict)
            or {key: item for key, item in value.items() if key not in ('sizeMiB', 'tmpMiB')} != {
                'path': '/unified-runtime', 'environmentVariable': 'AMPLIFIER_TEST_RUNTIME',
                'uid': 65534, 'gid': 65534, 'mode': '0700', 'retained': False}
            or type(value.get('sizeMiB')) is not int or type(value.get('tmpMiB')) is not int
            or not 64 <= value['sizeMiB'] + value['tmpMiB'] <= 1048576
            or value['tmpMiB'] != (value['sizeMiB'] + value['tmpMiB']) // 4):
        raise ValueError('The prepared runtime workdir contract changed.')
    path = Path(value['path'])  # A fixed root-level path, never a source/control descendant.
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_uid, info.st_gid) != (65534, 65534) or not path.is_mount()):
        raise ValueError('The prepared runtime workdir is not a private workload-owned mount.')
    filesystem = os.statvfs(path)
    required = os.ST_NOSUID | os.ST_NODEV
    if (filesystem.f_flag & required != required or filesystem.f_flag & (os.ST_NOEXEC | os.ST_RDONLY)
            or filesystem.f_blocks * filesystem.f_frsize != value['sizeMiB'] * 1024 ** 2):
        raise ValueError('The prepared runtime workdir mount flags or size changed.')


def initialize(prefix=Path('/opt/unified-prepared'), workspace=Path('/workspace')):
    plan = json.loads((prefix / 'plan.json').read_text())
    if 'executionContract' in plan:
        contract = json.loads((prefix / 'execution-contract.json').read_text())
        if (contract.get('source') != plan['executionContract']['source']
                or contract.get('profileId') != plan['profileId'] or contract.get('version') != 1
                or contract.get('runtimeWorkdir') != plan['executionContract'].get('runtimeWorkdir')
                or not (prefix / 'venv/bin/python').is_file()):
            raise ValueError('The prepared execution contract is incomplete or belongs to different source.')
        if 'runtimeWorkdir' in contract:
            verify_runtime_workdir(contract['runtimeWorkdir'])
    marker = workspace / '.unified-prepared-dependencies.json'
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != {'preparationId': plan['preparationId']}:
            raise ValueError('The installed dependency preparation changed.')
        return
    if plan['profileId'] == 'browser-test':
        projects = plan.get('nodeProjects', [{'directory': plan['nodeDirectory'],
                                            'key': plan['nodeKey'],
                                            'manifestDirectory': plan['nodeDirectory']}])
        locks = {row['path']: row['sha256'] for row in plan.get('lockfiles', [])}
        for project in projects:
            directory = workspace / project['directory']
            parents = [directory, *directory.parents]
            if (any(path.is_symlink() for path in parents[:parents.index(workspace) + 1])
                    or (project['key'] != 'web-client' and not directory.is_dir())):
                raise ValueError('The project dependency destination changed.')
            target = directory / 'node_modules'
            if target.exists() or target.is_symlink():
                raise ValueError('The source snapshot already supplies project dependency files.')
        for project in projects:
            if project['key'] == 'web-client':
                prepare_client(prefix, workspace, project, locks)
            elif locks:
                verify_manifests(workspace, project, locks)
        for project in projects:
            shutil.copytree(prefix / 'node' / project['key'],
                            workspace / project['directory'] / 'node_modules', symlinks=True)
    marker.write_text(json.dumps({'preparationId': plan['preparationId']}))


if __name__ == '__main__':
    initialize()
