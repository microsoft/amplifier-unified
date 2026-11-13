"""Disclosed CI acquisition adapter, NOT installed application code.

Loaded only by the private fixture's PYTHONPATH/sitecustomize. The three patched
attributes are release_metadata, the EXACT preview tag lookup in download_command,
and app_release_artifacts.prepare. All other downloads, verification, binding,
admission, launches, worker, probes, graph, doctor and health remain production.
This asserts local candidate delivery, never official published-release trust.
An observational Python audit hook counts manager-dispatch attempts; it neither
replaces launch nor changes argv/environment or enters isolated worker/probes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


PATCHES = ('reset.release_metadata', 'reset.download_command', 'app_release_artifacts.prepare')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def observe_dispatches(config):
    def audit(event, arguments):
        if event != 'subprocess.Popen':
            return
        # Audit events include env/argv. Never serialize either: retain only an
        # exact repair label and the observing service PID. Ignore all other work.
        try:
            argv = arguments[1]
            if not isinstance(argv, (list, tuple)) or not argv:
                return
            if Path(argv[0]).name == 'systemd-run':
                labels = [word.removeprefix('--unit=') for word in argv
                          if isinstance(word, str) and word.startswith('--unit=')]
                pattern = r'amplifier-installation-repair-[a-f0-9]{32}'
            elif Path(argv[0]).name == 'launchctl' and list(argv[1:3]) == ['submit', '-l']:
                labels, pattern = list(argv[3:4]), r'com\.microsoft\.amplifier-unified\.repair\.[a-f0-9]{32}'
            else:
                return
            if len(labels) != 1 or not re.fullmatch(pattern, labels[0]):
                return
            raw = json.dumps({'label': labels[0], 'servicePID': os.getpid()}).encode() + b'\n'
            fd = os.open(Path(config['root']) / 'dispatch-audit.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'ab') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            # Observability must NEVER alter dispatch behavior. Missing audit
            # evidence instead fails acceptance in the external driver.
            return
    sys.addaudithook(audit)


def helper_custody(config, folder):
    """Reserve the exact not-yet-dispatched job before acquisition returns.

    No native mutation here. This is fixture accounting, not a patch to launch or
    admission. A preexisting label, including an inactive job, is not ours.
    """
    if sha(config['nativeHarness']) != config['nativeHarnessSHA256']:
        raise RuntimeError('native custody helper changed')
    spec = importlib.util.spec_from_file_location('_repair_custody', config['nativeHarness'])
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    env = native.manager_environment()
    label = native.helper_label(folder.name)
    if native.sys.platform == 'linux':
        native.check(native.properties(label + '.service', env).get('LoadState') == 'not-found',
                     'preexisting repair helper registration')
    else:
        native.check(not native.launch_job(label, env), 'preexisting repair helper registration')
    owner_path = Path(config['root']) / 'owner.json'
    owner = native.load(owner_path)
    native.check(not owner['helpers'], 'fixture already acquired a helper; do not replay')
    owner['helpers'].append({'label': label, 'folder': str(folder), 'basePython': config['basePython']})
    native.save(owner_path, owner)


def install(manifest):
    config = read(manifest)
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or sha(__file__) != config['adapterSHA256']):
        raise RuntimeError('acquisition adapter requires its exact disclosed CI fixture')
    from amplifier_web import app_release_artifacts as artifacts, reset

    original_download = reset.download_command
    version, revision = config['version'], config['revision']
    exact_lookup = ('git', 'ls-remote', 'https://github.com/' + reset.REPOSITORY,
                    'refs/tags/v' + version, 'refs/tags/v' + version + '^{}')

    def release_metadata(env):
        return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False}

    def download_command(*args, env, timeout=120, **kwargs):
        if args == exact_lookup and not kwargs:
            return subprocess.CompletedProcess(args, 0, revision + '\trefs/tags/v' + version + '\n', '')
        return original_download(*args, env=env, timeout=timeout, **kwargs)

    async def prepare(folder, selected_version, selected_revision, *, delivery=None):
        folder = Path(folder)
        expected_parent = Path(config['home']) / 'installation-repair'
        if (selected_version != version or selected_revision != revision or delivery is not None
                or folder.parent != expected_parent or not re.fullmatch('[a-f0-9]{32}', folder.name)
                or folder.is_symlink() or folder.resolve() != folder or not folder.is_dir()):
            raise ValueError('local candidate acquisition target changed')
        descriptor = config['artifact']
        # Use the REAL verifier on both original and operation-owned bytes.
        artifacts.verify(descriptor, version, revision)
        target = folder / artifacts.filename(version)
        with target.open('xb') as output, Path(descriptor['path']).open('rb') as source:
            os.fchmod(output.fileno(), 0o600)
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        acquired = {**descriptor, 'path': str(target)}
        artifacts.verify(acquired, version, revision)
        helper_custody(config, folder)
        return acquired

    reset.release_metadata = release_metadata
    reset.download_command = download_command
    artifacts.prepare = prepare
    observe_dispatches(config)