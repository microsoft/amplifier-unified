"""Fixed dependency preparation recipe. Runs only inside the owned build guest.

This file is copied as data into a build guest, never run in the app host. Source
dependency installation can execute package build code there, with outbound
network but without host files, inherited credentials or a container socket.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

PREFIX = Path('/opt/unified-prepared')
UV_VERSION = '0.9.18'
NODE_VERSION = '22.16.0'


def run(*args, cwd=None, capture=False):
    return subprocess.check_output(args, cwd=cwd, text=True) if capture else subprocess.run(args, cwd=cwd, check=True)


def main():
    plan = json.loads(Path('/prepare/plan.json').read_text())
    os.environ.clear()
    os.environ.update(PATH='/usr/local/bin:/usr/bin:/bin', HOME='/tmp/build-home',
                      LANG='C.UTF-8', DEBIAN_FRONTEND='noninteractive',
                      GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
                      PIP_CONFIG_FILE='/dev/null', UV_NO_CONFIG='1',
                      UV_PROJECT_ENVIRONMENT=str(PREFIX / 'venv'),
                      UV_PYTHON_INSTALL_DIR=str(PREFIX / 'python'),
                      PLAYWRIGHT_BROWSERS_PATH=str(PREFIX / 'browsers'))
    Path('/tmp/build-home').mkdir(mode=0o700, exist_ok=True)
    PREFIX.mkdir(parents=True, exist_ok=True)
    run('apt-get', 'update')
    run('apt-get', 'install', '-y', '--no-install-recommends', 'ca-certificates', 'git',
        'build-essential', 'python3', 'python3-venv', 'pkg-config', 'libffi-dev')
    run('python3', '-m', 'venv', str(PREFIX / 'bootstrap'))
    bootstrap = str(PREFIX / 'bootstrap/bin/python')
    run(bootstrap, '-m', 'pip', '--isolated', 'install', '--index-url',
        'https://pypi.org/simple', 'uv==' + UV_VERSION)
    uv = str(PREFIX / 'bootstrap/bin/uv')
    # Install an exact-source wheel for package metadata and console scripts;
    # virtual projects naturally install dependencies only. Python source checks
    # still prefer the exact workspace through the executor's PYTHONPATH.
    # The manifest's default groups are honored, not every optional integration.
    run(uv, 'sync', '--locked', '--no-editable', '--python',
        '3.13', cwd='/workspace')
    inventory = {'profileId': plan['profileId'], 'preparationId': plan['preparationId'],
                 'sourceSha256': plan['sourceSha256'], 'lockfiles': plan['lockfiles'],
                 'uv': run(uv, '--version', capture=True).strip(),
                 'python': run(str(PREFIX / 'venv/bin/python'), '--version', capture=True).strip()}
    if plan['profileId'] == 'browser-test':
        architecture = {'aarch64': 'arm64', 'x86_64': 'x64'}.get(os.uname().machine)
        if not architecture:
            raise ValueError('This browser profile supports Linux arm64 and x64.')
        name = 'node-v' + NODE_VERSION + '-linux-' + architecture
        filename = name + '.tar.xz'
        base = 'https://nodejs.org/dist/v' + NODE_VERSION + '/'
        sums = urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=60).read().decode()
        expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] == filename)
        target = Path('/tmp') / filename
        with urllib.request.urlopen(base + filename, timeout=120) as source, target.open('wb') as output:
            shutil.copyfileobj(source, output)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('The fixed Node runtime download failed its checksum.')
        with tarfile.open(target) as archive:
            archive.extractall(PREFIX, filter='data')
        (PREFIX / name).rename(PREFIX / 'node-runtime')
        os.environ['PATH'] = str(PREFIX / 'node-runtime/bin') + ':' + os.environ['PATH']
        project = Path('/workspace') / plan['nodeDirectory']
        run('npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', cwd=project)
        # esbuild's platform executable is present in the locked optional package;
        # npm lifecycle scripts are not needed for Vite/Playwright installation.
        cli = project / 'node_modules/playwright/cli.js'
        if not cli.is_file():
            raise ValueError('The locked browser project must include Playwright.')
        run('node', str(cli), 'install', '--with-deps', 'chromium', cwd=project)
        # Python browser probes use the same Playwright release as the locked
        # JavaScript project. Resolve its platform wheels once, retain their
        # exact hashes, then install only from that local wheel set.
        wheels = PREFIX / 'browser-wheels'
        wheels.mkdir()
        run(bootstrap, '-m', 'pip', '--isolated', 'download', '--only-binary=:all:',
            '--index-url', 'https://pypi.org/simple', '--dest', str(wheels),
            '--python-version', '3.13', 'playwright==' + plan['playwrightVersion'])
        wheel_lock = [{'filename': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                      for path in sorted(wheels.iterdir())]
        run(uv, 'pip', 'install', '--python', str(PREFIX / 'venv/bin/python'),
            '--no-index', '--find-links', str(wheels), 'playwright==' + plan['playwrightVersion'])
        inventory['browserWheels'] = wheel_lock
        shutil.rmtree(wheels)
        run('apt-get', 'install', '-y', '--no-install-recommends', 'xvfb', 'xauth', 'libxss1', 'x11-utils')
        (PREFIX / 'node').mkdir(exist_ok=True)
        shutil.move(str(project / 'node_modules'), str(PREFIX / 'node' / plan['nodeKey']))
        inventory['node'] = {'version': run('node', '--version', capture=True).strip(),
                             'archiveSha256': actual, 'playwright': plan['playwrightVersion']}
    inventory['pythonPackages'] = run(uv, 'pip', 'freeze', '--python', str(PREFIX / 'venv/bin/python'), capture=True).splitlines()
    inventory['systemPackages'] = run('dpkg-query', '-W', '-f=${Package}=${Version}\n', capture=True).splitlines()
    inventory['preparedFiles'] = {}
    for directory in ('browsers', 'node'):
        digest = hashlib.sha256()
        for path in sorted((PREFIX / directory).rglob('*')) if (PREFIX / directory).exists() else []:
            digest.update(str(path.relative_to(PREFIX)).encode())
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
        inventory['preparedFiles'][directory] = digest.hexdigest()
    (PREFIX / 'inventory.json').write_text(json.dumps(inventory, sort_keys=True))
    shutil.copyfile('/prepare/initialize.py', PREFIX / 'initialize.py')
    (PREFIX / 'plan.json').write_text(json.dumps(plan, sort_keys=True))
    # Keep all prepared dependencies accessible to the unprivileged check user.
    for root, directories, files in os.walk(PREFIX):
        for name in directories:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o555)
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o444)
    shutil.rmtree('/tmp/build-home')
    shutil.rmtree('/root/.cache', ignore_errors=True)
    shutil.rmtree('/var/lib/apt/lists', ignore_errors=True)
    target = locals().get('target')
    if target:
        target.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
