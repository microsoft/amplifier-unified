"""Disclosed CI acquisition adapter, NOT installed application code.

Loaded only by the private fixture's PYTHONPATH/sitecustomize. The three patched
attributes are release_metadata, the EXACT preview tag lookup in download_command,
and app_release_artifacts.prepare. All other downloads, verification, binding,
admission, launches, worker, probes, graph, doctor and health remain production.
This asserts local candidate delivery, never official published-release trust.
An observational Python audit hook counts manager-dispatch attempts; it neither
replaces launch nor changes argv/environment or enters isolated worker/probes.
Mac bootstrap is counted only for the exact reserved, closed private helper.plist;
legacy submit and unknown/mismatched definitions are ignored, never replayed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys


PATCHES = ('reset.release_metadata', 'reset.download_command', 'app_release_artifacts.prepare')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private_bytes(path, limit=16_384):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('redirected private helper input')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('nonprivate helper input')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('helper input exceeds bound')
    return raw, [info.st_dev, info.st_ino]


def launchd_reservation(config, folder):
    if (type(config.get('uid')) is not int or config['uid'] != os.getuid()
            or folder.parent != Path(config['home']) / 'installation-repair'
            or not re.fullmatch('[a-f0-9]{32}', folder.name)):
        raise ValueError('helper reservation owner changed')
    return {'kind': 'launchd-user-job', 'label': 'com.microsoft.amplifier-unified.repair.' + folder.name,
            'domain': 'gui/' + str(config['uid']), 'plistPath': str(folder / 'helper.plist')}


def checked_launchd_inputs(config, folder):
    """Read-only a0d829e8 contract check, never prepare/rewrite/dispatch a job.

    Called by passive audit before bootstrap and by the external custody owner.
    Acquisition reserves names only: production has not created the plist then.
    """
    reservation = launchd_reservation(config, folder)
    owner = json.loads(private_bytes(Path(config['root']) / 'owner.json', 65_536)[0])
    helpers = owner.get('helpers')
    if (not isinstance(helpers, list) or len(helpers) != 1
            or helpers[0].get('launchdReservation') != reservation
            or helpers[0].get('folder') != str(folder) or helpers[0].get('basePython') != config['basePython']
            or helpers[0].get('label') != reservation['label']):
        raise ValueError('missing exact provisional helper custody')
    plan = json.loads(private_bytes(folder / 'plan.json', 4_000_000)[0])
    operation = json.loads(private_bytes(folder / 'operation.json', 4_000_000)[0])
    if (plan.get('operationId') != folder.name or operation.get('operationId') != folder.name
            or plan.get('home') != config['home'] or plan.get('uid') != config['uid']
            or plan.get('basePython') != config['basePython']):
        raise ValueError('private helper plan owner changed')
    expected = {**reservation, 'cleanupState': 'retained-for-explicit-review', 'cleanupOwner': 'installation-owner',
                'detail': 'The one-shot registration may remain after exit. Remove only this exact settled job after reviewing its receipt; do not relaunch it.'}
    job = plan.get('helperJob')
    if (not isinstance(job, dict) or set(job) != {*expected, 'plistSHA256'}
            or any(job[key] != value for key, value in expected.items())
            or job != operation.get('helperJob') or not isinstance(job['plistSHA256'], str)
            or not re.fullmatch('[a-f0-9]{64}', job['plistSHA256'])):
        raise ValueError('private plan/operation helper custody changed')
    definition = {'Label': reservation['label'],
                  'ProgramArguments': [config['basePython'], '-I', '-S',
                                       str(folder / 'installation_repair_worker.py'), str(folder)],
                  'WorkingDirectory': str(folder), 'RunAtLoad': True, 'KeepAlive': False,
                  'ProcessType': 'Background', 'StandardOutPath': os.devnull,
                  'StandardErrorPath': os.devnull, 'Umask': 0o077}
    raw, inode = private_bytes(folder / 'helper.plist')
    if (hashlib.sha256(raw).hexdigest() != job['plistSHA256']
            or raw != plistlib.dumps(definition, sort_keys=True)):
        raise ValueError('closed private helper definition changed')
    return job, {'sha256': job['plistSHA256'], 'stat': inode}, definition


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
            elif Path(argv[0]).name == 'launchctl' and len(argv) == 4 and argv[1] == 'bootstrap':
                folder = Path(argv[3]).parent
                job, _, _ = checked_launchd_inputs(config, folder)
                if list(argv[1:]) != ['bootstrap', job['domain'], job['plistPath']]:
                    return
                labels, pattern = [job['label']], r'com\.microsoft\.amplifier-unified\.repair\.[a-f0-9]{32}'
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
    helper = {'label': label, 'folder': str(folder), 'basePython': config['basePython']}
    if native.sys.platform == 'darwin':
        helper['launchdReservation'] = launchd_reservation(config, folder)
    owner['helpers'].append(helper)
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