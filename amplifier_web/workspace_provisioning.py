"""Retained workspace setup receipts. Git imports never run under the app lock."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import selectors
import time
import uuid

from filelock import FileLock
from filelock import Timeout as LockTimeout
from amplifier_worktrees.git import atomic
from .workspace_placement import _validate_created_directory

GIT_TIMEOUT_SECONDS = 180
GIT_STDOUT_LIMIT = 65536
GIT_STDERR_LIMIT = 1048576


def receipt_path(home, identity):
    uuid.UUID(identity)
    return home / 'workspace-setup' / (identity + '.json')


def inspect(home, identity):
    try:
        return json.loads(receipt_path(home, identity).read_text())
    except FileNotFoundError:
        raise ValueError('No starter setup receipt exists for this workspace.') from None


def recover(home, identity):
    """Observe durable receipts after restart; never replay Git or scaffold work."""
    target = receipt_path(home, identity)
    try:
        with FileLock(str(target) + '.lock', timeout=0):
            value = inspect(home, identity)
            if value['status'] in {'pending', 'running'}:
                if value.get('rootGit', {}).get('status') == 'running':
                    value['rootGit']['status'] = 'unknown'
                for row in value['repositories']:
                    if row['status'] == 'running':
                        row.update(status='unknown', error='Import interrupted. Inspect retained work before retrying.')
                value.update(status='interrupted', error='Setup was interrupted. No operations were replayed.',
                             revision=value['revision'] + 1)
                atomic(target, value)
            return value
    except LockTimeout:
        return inspect(home, identity)  # Another live owner holds its setup lease.


def initialize(home, placement):
    identity = placement['planId']
    target = receipt_path(home, identity)
    target.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target) + '.lock'):
        if target.exists():
            return inspect(home, identity)
        starter = placement.get('starter')
        if not starter:
            return None
        rows = [{**row, 'status': 'pending'} for row in starter['repositories']]
        value = {'id': identity, 'revision': 1, 'path': placement['path'],
                 'directoryIdentity': placement['directoryIdentity'], 'starter': starter,
                 'status': 'pending', 'files': [], 'repositories': rows, 'error': None}
        atomic(target, value)
        return value


def retry(home, identity, revision):
    target = receipt_path(home, identity)
    with FileLock(str(target) + '.lock', timeout=0):
        value = inspect(home, identity)
        _validate_created_directory(value)
        if value['revision'] != revision:
            raise ValueError('Workspace setup changed. Inspect its latest receipt before retrying.')
        if value['status'] == 'ready':
            return value
        if value.get('rootGit', {}).get('status') in {'running', 'unknown'}:
            raise ValueError('Workspace Git initialization was interrupted. Preserve and inspect its metadata; it was not replayed.')
        # Explicit retry admits only unfinished steps. Completed imports are
        # never fetched/merged/reset. Unknown clone outcomes require inspection.
        if any(row['status'] in {'running', 'unknown'} for row in value['repositories']):
            raise ValueError('An import was interrupted. Inspect its retained folder; it was not replayed.')
        value.update(status='pending', error=None, revision=value['revision'] + 1)
        atomic(target, value)
        return value


def _child_fd(root, name):
    try:
        os.mkdir(name, dir_fd=root)
    except FileExistsError:
        pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)


def _write_new(root, directory, filename, content):
    fd = _child_fd(root, directory) if directory else os.dup(root)
    temporary = '.starter-' + uuid.uuid4().hex
    try:
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, filename, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            return 'preserved'
        return 'created'
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.close(fd)


def _raw_git(fd, *args, overrides=(), allow_missing=False, extra_fds=()):
    # The child inherits the pinned directory, so replacing a path or ancestor
    # cannot redirect Git into a borrowed checkout. No shell or repo hooks.
    directory = f'/proc/self/fd/{fd}' if Path('/proc/self/fd').is_dir() else f'/dev/fd/{fd}'
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_SSH_COMMAND': 'ssh -oBatchMode=yes -oStrictHostKeyChecking=yes'}
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
                 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        env.pop(name, None)  # A caller's checkout cannot redirect the pinned cwd.
    process = subprocess.Popen(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                               '-c', 'protocol.file.allow=never', *overrides, *args],
                               cwd=directory, pass_fds=(fd, *extra_fds), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    output = bytearray()
    totals = {'stdout': 0, 'stderr': 0}
    settled = False
    with selectors.DefaultSelector() as streams:
        streams.register(process.stdout, selectors.EVENT_READ, 'stdout')
        streams.register(process.stderr, selectors.EVENT_READ, 'stderr')
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        try:
            while streams.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError(f'Repository access timed out after {GIT_TIMEOUT_SECONDS:g} seconds. Check access before retrying.')
                for key, _ in streams.select(min(.05, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        streams.unregister(key.fileobj)
                        continue
                    totals[key.data] += len(chunk)
                    limit = GIT_STDOUT_LIMIT if key.data == 'stdout' else GIT_STDERR_LIMIT
                    if totals[key.data] > limit:
                        raise ValueError('Repository output exceeds the setup limit. Retained files were not removed.')
                    if key.data == 'stdout':
                        output.extend(chunk)
            try:
                process.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ValueError(f'Repository access timed out after {GIT_TIMEOUT_SECONDS:g} seconds. Check access before retrying.') from None
            settled = True
        finally:
            # Reap the whole process group on all exits, including output limit
            # or an unexpected decoding/read failure; no disk spool can grow.
            if not settled or process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            process.stdout.close()
            process.stderr.close()
        if process.returncode and not (allow_missing and process.returncode == 1):
            raise ValueError('Repository access failed. Check its source, branch and Git credentials before retrying.')
        return output.decode().strip()


def _git(fd, *args):
    # Match managed-worktree hardening while keeping a pinned cwd descriptor.
    keys = _raw_git(fd, 'config', '--null', '--name-only', '--get-regexp',
                    r'^filter\..*\.(clean|smudge|process|required)$', allow_missing=True)
    drivers = {key.rsplit('.', 1)[0] for key in keys.split('\0') if key}
    overrides = []
    for driver in sorted(drivers):
        for suffix, value in (('clean', ''), ('smudge', ''), ('process', ''), ('required', 'false')):
            overrides.extend(('-c', driver + '.' + suffix + '=' + value))
    return _raw_git(fd, *args, overrides=overrides)


def _workspace_git(root, value, target):
    """Initialize only our allocated root; never reinitialize borrowed metadata."""
    saved = value.get('rootGit', {})
    if saved.get('status') in {'ready', 'preserved'}:
        return
    if saved:
        raise ValueError('Workspace Git initialization was interrupted. Preserve and inspect .git; initialization was not replayed.')
    try:
        existing = os.stat('.git', dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing:
        if stat.S_ISLNK(existing.st_mode):
            raise ValueError('Existing workspace Git metadata is a symlink. It was preserved; no initialization was performed.')
        value['rootGit'] = {'status': 'preserved', 'note': 'Existing Git metadata and ignore rules were not modified.'}
        atomic(target, value)
        return
    # Reserve the Git directory exclusively before admission is persisted.
    os.mkdir('.git', mode=0o700, dir_fd=root)
    info = os.stat('.git', dir_fd=root, follow_symlinks=False)
    value['rootGit'] = {'status': 'running', 'directoryIdentity': [info.st_dev, info.st_ino]}
    atomic(target, value)
    try:
        metadata = os.open('.git', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            opened = os.fstat(metadata)
            if [opened.st_dev, opened.st_ino] != value['rootGit']['directoryIdentity']:
                raise ValueError('Workspace Git metadata changed during initialization. Preserve and inspect it.')
            if os.listdir(metadata):
                raise ValueError('Workspace Git metadata is not empty before initialization. Preserve and inspect it; borrowed Git was not modified.')
            descriptor = f'/proc/self/fd/{metadata}' if Path('/proc/self/fd').is_dir() else f'/dev/fd/{metadata}'
            _raw_git(root, '--git-dir=' + descriptor, 'init', '--template=', '--initial-branch=main', extra_fds=(metadata,))
            # Git cannot infer a worktree from a descriptor spelling. Persist
            # a portable parent-relative binding, never the transient fd path.
            _raw_git(root, '--git-dir=' + descriptor, 'config', '--local', 'core.bare', 'false', extra_fds=(metadata,))
            _raw_git(root, '--git-dir=' + descriptor, 'config', '--local', 'core.worktree', '..', extra_fds=(metadata,))
            ignored = (
                '# Workspace notes only; projects retain independent Git history.\n'
                '# Exclude all child folders (including future repositories).\n'
                '/*/\n'
                '*.env\n.env*\nkeys.env\n*.key\n*.pem\n*.p12\n*.pfx\n'
                '*.log\n*.tmp\n.DS_Store\n'
            )
            # This is newly-owned metadata; never mutate a borrowed repository.
            status = _write_new(metadata, 'info', 'exclude', ignored)
            if status != 'created':
                raise ValueError('Unexpected workspace Git exclusion file. Preserve and inspect it.')
            status = _write_new(root, '', '.gitignore', ignored)
            value['files'].append({'path': '.gitignore', 'status': status})
            if status == 'preserved':
                raise ValueError('Existing .gitignore was preserved. Its rules need review before workspace Git exclusions can be claimed safe; initialization was retained, not repeated.')
            current = os.stat('.git', dir_fd=root, follow_symlinks=False)
            if [current.st_dev, current.st_ino] != value['rootGit']['directoryIdentity']:
                raise ValueError('Workspace Git location changed. Preserve and inspect the retained metadata.')
            probes = ['.amplifier/settings.yaml', '.env', 'keys.env', 'project-check/source.txt',
                      *[row['directory'] + '/source.txt' for row in value['repositories']]]
            for probe in probes:
                # Higher-precedence negations in a preserved .gitignore can
                # override info/exclude. Refuse protected readiness, not files.
                if _raw_git(root, 'check-ignore', '--no-index', '--', probe, allow_missing=True) != probe:
                    raise ValueError('Preserved ignore rules override workspace exclusions. Review .gitignore; existing files and Git metadata were retained.')
        finally:
            os.close(metadata)
        value['rootGit']['status'] = 'ready'
        atomic(target, value)
    except (ValueError, OSError):
        value['rootGit']['status'] = 'unknown'
        atomic(target, value)
        raise


def run(home, identity, progress=None):
    target = receipt_path(home, identity)
    with FileLock(str(target) + '.lock', timeout=0):
        value = inspect(home, identity)
        if value['status'] != 'pending':
            return value
        _validate_created_directory(value)
        root = os.open(value['path'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(root)
            if [info.st_dev, info.st_ino] != value['directoryIdentity']:
                raise ValueError('The workspace folder changed. No setup was performed.')
            starter = value['starter']
            value.update(status='running', revision=value['revision'] + 1)
            atomic(target, value)
            if not value.get('scaffolded'):
                if starter.get('scratch'):
                    from .workspace_starters import SCRATCH_TEMPLATE
                    status = _write_new(root, '', 'SCRATCH.md', SCRATCH_TEMPLATE)
                    value['files'].append({'path': 'SCRATCH.md', 'status': status})
                if starter['instructions']:
                    status = _write_new(root, '', 'AGENTS.md', starter['instructions'] + '\n')
                    value['files'].append({'path': 'AGENTS.md', 'status': status})
                    # The host loads .amplifier/AGENTS.md automatically. The
                    # relative include keeps root guidance/memory portable.
                if starter['instructions'] or starter.get('scratch'):
                    includes = ('@../AGENTS.md\n' if starter['instructions'] else '') + ('@../SCRATCH.md\n' if starter.get('scratch') else '')
                    status = _write_new(root, '.amplifier', 'AGENTS.md', includes)
                    value['files'].append({'path': '.amplifier/AGENTS.md', 'status': status})
                if starter['bundle']:
                    import yaml
                    settings = {'bundle': {'active': starter['bundle']}}
                    if starter.get('bundleSource'):
                        settings['bundle']['added'] = {starter['bundle']: starter['bundleSource']}
                    status = _write_new(root, '.amplifier', 'settings.yaml', yaml.safe_dump(settings))
                    value['files'].append({'path': '.amplifier/settings.yaml', 'status': status})
                if starter.get('rootGit'):
                    _workspace_git(root, value, target)
                # Mark even empty scaffolds to make repeated imports independent.
                value['scaffolded'] = True
                atomic(target, value)
                if progress:
                    if progress(json.loads(json.dumps(value))) is False:
                        value['status'] = 'interrupted'
                        atomic(target, value)
                        return value
            for row in value['repositories']:
                if row['status'] == 'ready':
                    continue
                if row['status'] in {'running', 'unknown'}:
                    row.update(status='unknown', error='Interrupted import retained; inspect it before any new import.')
                    continue
                try:
                    try:
                        existing = os.stat(row['directory'], dir_fd=root, follow_symlinks=False)
                    except FileNotFoundError:
                        if row.get('directoryIdentity'):
                            raise ValueError('The previously created repository folder is missing. Inspect it before retrying.')
                        os.mkdir(row['directory'], dir_fd=root)
                        existing = os.stat(row['directory'], dir_fd=root, follow_symlinks=False)
                    else:
                        if (not stat.S_ISDIR(existing.st_mode)
                                or row.get('directoryIdentity') != [existing.st_dev, existing.st_ino]):
                            raise ValueError('The destination folder already exists or changed. Its files were preserved.')
                    child = os.open(row['directory'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (existing.st_dev, existing.st_ino) or os.listdir(child):
                            raise ValueError('The repository folder changed or contains files. Inspect it before retrying.')
                        row.update(status='running', directoryIdentity=[opened.st_dev, opened.st_ino], error=None)
                        atomic(target, value)
                        args = ['clone', '--no-recurse-submodules']
                        if row['ref']:
                            args += ['--branch', row['ref']]
                        _git(child, *args, '--', row['url'], '.')
                        sha = _git(child, 'rev-parse', 'HEAD')
                        branch = _git(child, 'branch', '--show-current')
                        if _git(child, 'status', '--porcelain'):
                            raise ValueError('The imported repository has unexpected changes; its files were retained.')
                        current = os.stat(row['directory'], dir_fd=root, follow_symlinks=False)
                        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                            raise ValueError('The repository location changed during import. Inspect the retained work.')
                    finally:
                        os.close(child)
                    row.update(status='ready', sha=sha, branch=branch, error=None)
                except (OSError, ValueError) as exc:
                    row.update(status='failed', error=str(exc) if isinstance(exc, ValueError) else 'Repository setup failed; existing files were preserved.')
                    if row.get('directoryIdentity'):
                        try:
                            check = os.open(row['directory'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                            try:
                                if os.listdir(check):
                                    row['status'] = 'unknown'
                                    row['error'] += ' Retained files need inspection; no automatic reclone.'
                            finally:
                                os.close(check)
                        except OSError:
                            row['status'] = 'unknown'
                value['revision'] += 1
                atomic(target, value)
                if progress:
                    if progress(json.loads(json.dumps(value))) is False:
                        value['status'] = 'interrupted'
                        atomic(target, value)
                        return value
            _validate_created_directory(value)
            value.update(status='ready' if all(row['status'] == 'ready' for row in value['repositories']) else 'partial', error=None)
        except (ValueError, OSError) as exc:
            value.update(status='failed', error=str(exc) if isinstance(exc, ValueError) else 'Workspace scaffold could not be written. Existing files were preserved.')
        finally:
            os.close(root)
        value['revision'] += 1
        atomic(target, value)
        return value


def reconcile(home, identity):
    """Qualify exact retained imports using local, read-only Git checks."""
    target = receipt_path(home, identity)
    with FileLock(str(target) + '.lock', timeout=0):
        value = inspect(home, identity)
        _validate_created_directory(value)
        root = os.open(value['path'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for row in value['repositories']:
                if row['status'] != 'unknown':
                    continue
                try:
                    fd = os.open(row['directory'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                    try:
                        opened = os.fstat(fd)
                        if [opened.st_dev, opened.st_ino] != row.get('directoryIdentity'):
                            raise ValueError('The retained folder changed. No import was adopted.')
                        if _git(fd, 'config', '--get', 'remote.origin.url') != row['url']:
                            raise ValueError('Repository source differs from the reviewed starter.')
                        if _git(fd, 'status', '--porcelain'):
                            raise ValueError('Retained import has changes. Preserve and review those files before retrying.')
                        sha = _git(fd, 'rev-parse', 'HEAD')
                        branch = _git(fd, 'branch', '--show-current')
                        if row['ref'] and _git(fd, 'rev-parse', row['ref']) != sha:
                            raise ValueError('The retained checkout does not match the requested ref.')
                        row.update(status='ready', sha=sha, branch=branch, error=None)
                    finally:
                        os.close(fd)
                except (ValueError, OSError) as exc:
                    row['error'] = str(exc) if isinstance(exc, ValueError) else 'Retained import cannot be verified. Files were not changed.'
            _validate_created_directory(value)
        finally:
            os.close(root)
        value.update(status='ready' if value.get('scaffolded') and all(row['status'] == 'ready' for row in value['repositories']) else 'partial',
                     revision=value['revision'] + 1)
        atomic(target, value)
        return value
