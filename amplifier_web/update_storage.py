"""Private staging copies: copy-on-write where supported, never writable hardlinks."""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path


async def snapshot_shared_config(proof, source, target):
    """Copy only shared qualification inputs under the caller's copy owner."""
    from .update_health import candidate_copy

    target.mkdir(mode=0o700)
    for name in ("settings.yaml", "keys.env", "routing"):
        original = source / name
        if original.is_dir():
            await candidate_copy(proof, shutil.copytree, original, target / name)
        elif original.is_file():
            await candidate_copy(proof, shutil.copy2, original, target / name)


def _stage_address(home, stage):
    from .windows_files import address

    home, stage = address(home), address(stage)
    if ('..' in home.parts or '..' in stage.parts
            or stage.parent != home / 'updates/releases'
            or not re.fullmatch('[a-f0-9]{32}', stage.name)):
        raise ValueError('Not an installation stage')
    return home, stage


def _identity(info):
    return info.st_dev, info.st_ino


@contextmanager
def _posix_directory(path, expected=None):
    """Open every component without following links; keep and recheck the chain."""
    with ExitStack() as pins:
        def open_directory(name, parent=None):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            pins.callback(os.close, fd)
            return fd
        fd = open_directory(path.anchor)
        chain = []
        current = Path(path.anchor)
        for name in path.parts[1:]:
            parent, fd = fd, open_directory(name, fd)
            current /= name
            info = os.fstat(fd)
            if expected and current in expected and _identity(info) != expected[current]:
                raise ValueError('Stage identity changed')
            chain.append((parent, name, fd))
        def check():
            for parent, name, child in chain:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or _identity(info) != _identity(os.fstat(child)):
                    raise ValueError('Directory identity changed')
        check()
        yield fd, check


def _regular_key(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Not a single regular credential file')


def remove_stage_keys(home, stage, *, expected=None):
    """Remove only one stage's keys.env. None means absent/removed; no secret facts.

    Refusals are retryable, not update failures. Windows verifies private ACLs,
    exact bytes and exclusive file identity through its existing boundary.
    POSIX never reads credentials for removal and unlinks relative to pinned fds.
    """
    try:
        home, stage = _stage_address(home, stage)
        shared = stage / 'shared-config'
        if os.name == 'nt':
            from .windows_files import directory
            with directory(shared, owned_root=home, create=False) as pinned:
                if expected and any(_identity(path.lstat()) != identity
                                    for path, identity in expected.items()):
                    raise ValueError('Stage identity changed')
                digest = hashlib.sha256(pinned.read_private('keys.env', limit=1024 * 1024)).hexdigest()
                pinned.remove_private('keys.env', digest)
        else:
            with _posix_directory(shared, expected) as (fd, check):
                info = os.stat('keys.env', dir_fd=fd, follow_symlinks=False)
                _regular_key(info)
                check()
                if os.stat('keys.env', dir_fd=fd, follow_symlinks=False) != info:
                    raise ValueError('Credential identity changed')
                os.unlink('keys.env', dir_fd=fd)
        return None
    except FileNotFoundError:
        return None
    except PermissionError:
        return 'refused'
    except OSError:
        return 'io'
    except Exception:
        return 'refused'


def refresh_stage_keys(home, stage, source):
    """Revalidation alone gets current credentials, never new settings/routing.

    Synchronous bounded I/O leaves no background copy thread on cancellation.
    Return only a reason code, never bytes, digests or filesystem exception text.

    None means one of two things: the stage holds a copy of the current keys
    file, or the live home has no keys file and the stage holds no copy. After
    any other result the stage may hold a copy that this call did not write. A
    caller must not qualify with such a copy: remove it first (remove_stage_keys).
    """
    try:
        home, stage = _stage_address(home, stage)
        from .windows_files import address, directory
        source = address(source)
        if '..' in source.parts:
            raise ValueError('Unsafe shared home')
        try:
            if os.name == 'nt':
                with directory(source, owned_root=source, create=False) as pinned:
                    content = pinned.read_private('keys.env', limit=1024 * 1024)
            else:
                with _posix_directory(source) as (fd, check):
                    # Read what the product's own reader and the stage snapshot
                    # read: a link to a regular file too (a person's own keys
                    # file). Only the stage side is written, and it stays strict.
                    key = os.open('keys.env', os.O_RDONLY | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(key, 'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise ValueError('Not a regular credential file')
                        content = stream.read(1024 * 1024 + 1)
                        if len(content) > 1024 * 1024:
                            raise ValueError('Credential file exceeds private-file bound')
                    check()
        except FileNotFoundError:
            # A legacy copy must not stand in for credentials the person removed.
            return remove_stage_keys(home, stage)
        if os.name == 'nt':
            with directory(stage / 'shared-config', owned_root=home, create=False) as pinned:
                pinned.publish_private('keys.env', content, replace=True)
        else:
            with _posix_directory(stage / 'shared-config') as (fd, check):
                def check_target():
                    try:
                        _regular_key(os.stat('keys.env', dir_fd=fd, follow_symlinks=False))
                    except FileNotFoundError:
                        pass
                check_target()
                temporary = '.stage-keys-' + uuid.uuid4().hex
                key = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=fd)
                try:
                    with os.fdopen(key, 'wb') as stream:
                        stream.write(content)
                    check()
                    check_target()
                    os.replace(temporary, 'keys.env', src_dir_fd=fd, dst_dir_fd=fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
        return None
    except PermissionError:
        return 'refused'
    except OSError:
        return 'io'
    except Exception:
        return 'refused'


def _ended_without_admission(stage):
    """The record of a chat's own setup says that its preparation failed or was stopped.

    That preparation writes ``profile-attempt.json`` (runtime_profiles). Its
    coroutine has ended when the status is ``failed`` or ``cancelled``, and
    nothing uses the stage again. Anything else is no answer, and the caller
    then waits for age: another status, an entry that is no regular file, a
    record that is too long or that cannot be read.

    The record decides only when the stage's copy of the keys file may go,
    never what is removed. The entry must be a regular file by its name (a link
    is refused on every platform) and again by the open file; the open neither
    follows a link nor waits, where the platform has those flags.
    """
    try:
        path = stage / 'profile-attempt.json'
        if not stat.S_ISREG(path.lstat().st_mode):
            return False
        record = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        try:
            if not stat.S_ISREG(os.fstat(record).st_mode):
                return False
            content = b''
            while len(content) <= 1024 * 1024:
                part = os.read(record, 1024 * 1024 + 1 - len(content))
                if not part:
                    break
                content += part
        finally:
            os.close(record)
        if len(content) > 1024 * 1024:
            return False
        attempt = json.loads(content)
        return isinstance(attempt, dict) and attempt.get('status') in ('failed', 'cancelled')
    except Exception:
        return False


async def scrub_stage_keys(manager):
    """Key-only maintenance, independent of unused-code policy; never retire trees.

    At most eight key-removal attempts per pass. The scan offset rotates over
    later entries on subsequent idle passes. Entries before the offset are only
    listed and counted, without a look at them. The second of a pass starts
    before that listing. At an entry after the offset the pass stops when that
    second has gone, but it handles at least one entry first, so a slow folder
    or a long listing cannot hold the scan at one place.
    """
    from .runtime_profiles import owned
    from .update_health import pending_preparation
    from .update_retention import maintenance_blocked

    # Other maintenance callers can supply a partial manager. Without both
    # owners, key scrubbing has no admission/preparation proof; leave it alone.
    if getattr(manager, 'lock', None) is None or getattr(manager, 'diagnostics', None) is None:
        return

    def blocked():
        state = manager.service.state['updates']
        return maintenance_blocked(manager) or bool(
            state.get('pendingRestart') or state.get('pendingReplacement'))

    if blocked():
        return
    # Match activation's lifecycle -> update lock order, and never wait for an
    # in-progress update. There are no awaits once the update lock is acquired.
    async with manager.service.runtime_lifecycle():
        if blocked():
            return
        async with manager.lock:
            batch = pending_preparation(manager.diagnostics.state) or {}
            protected = {batch.get('revision'), (batch.get('preparation') or {}).get('candidate')}
            root = manager.directory / 'releases'
            offset = getattr(manager, '_stage_keys_offset', 0)
            deadline, attempts = time.monotonic() + 1, 0
            try:
                with os.scandir(root) as entries:
                    progressed = False
                    for index, entry in enumerate(entries):
                        if index < offset:
                            continue
                        if progressed and time.monotonic() >= deadline:
                            return
                        progressed = True
                        manager._stage_keys_offset = index + 1
                        # A chat's own setup that this process prepares now owns its stage,
                        # whatever the clock says about the age of that stage.
                        if (not re.fullmatch('[a-f0-9]{32}', entry.name) or entry.name in protected
                                or owned(entry.name)):
                            continue
                        stage = root / entry.name
                        shared = stage / 'shared-config'
                        try:
                            stage_info, shared_info = stage.lstat(), shared.lstat()
                            if not all(stat.S_ISDIR(info.st_mode) for info in (stage_info, shared_info)):
                                continue
                            try:
                                validated = stat.S_ISREG((stage / 'validated.json').lstat().st_mode)
                            except FileNotFoundError:
                                validated = False
                            # A failed or stopped chat setup has settled by its own record.
                            # For every other stage without a receipt only age speaks: a
                            # record that still says "preparing" does not prove an end.
                            if (not validated
                                    and max(stage_info.st_mtime, shared_info.st_mtime) >= time.time() - 86400
                                    and not _ended_without_admission(stage)):
                                continue
                            # Already-scrubbed stages do not consume the effect budget.
                            (shared / 'keys.env').lstat()
                            attempts += 1
                            remove_stage_keys(manager.home, stage, expected={
                                stage: _identity(stage_info), shared: _identity(shared_info)})
                            if attempts >= 8:
                                return
                        except (OSError, ValueError):
                            continue
                manager._stage_keys_offset = 0
            except OSError:
                return


def clone_file(source, target):
    source, target = Path(source), Path(target)
    try:
        if sys.platform == "darwin":
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            if libc.clonefile(os.fsencode(source), os.fsencode(target), 0) != 0:
                raise OSError(ctypes.get_errno(), "File clone unavailable")
        elif sys.platform.startswith("linux"):
            import fcntl

            with source.open("rb") as original, target.open("xb") as copy:
                fcntl.ioctl(copy.fileno(), 0x40049409, original.fileno())  # FICLONE
        else:
            raise OSError("File clone unavailable")
        shutil.copystat(source, target)
        return str(target)
    except OSError:
        if target.exists():
            target.unlink()
        return shutil.copy2(source, target)


def copy_snapshot(source, target):
    """Distinct file identities prevent staged changes reaching live workers."""
    copied = shutil.copytree(source, target, symlinks=True, copy_function=clone_file)
    _refresh_copied_indexes(Path(target))
    return copied


def _refresh_copied_indexes(target):
    """Refresh only new cache copies, after copytree has applied final modes.

    Git's read-only checks cannot persist stat entries for the new file identities.
    Keep that write here, inside the caller's existing copy/cancellation owner.
    This is metadata preparation, not admission: dirty/staged changes stay intact.
    """
    from .runtime_qualification import _installed_git_environment, _installed_checkout_environment
    from .staged_sources import _metadata_owned

    cache = target / "cache"
    if cache.is_symlink() or not cache.is_dir():
        return
    for root in cache.iterdir():
        try:
            _metadata_owned(root, target)
        except ValueError:
            # Linked or redirected layouts retain the original copy behavior.
            # Their ordinary downstream admission/verification still applies.
            continue
        git_dir, index = root / ".git", root / ".git/index"
        if not index.is_file():
            continue
        env = dict(_installed_git_environment() or os.environ)
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"):
            env.pop(name, None)
        env = _installed_checkout_environment(root, env)
        env.update(GIT_OPTIONAL_LOCKS="1", GIT_INDEX_FILE=str(index.resolve()))
        git_mode, index_mode = stat.S_IMODE(git_dir.stat().st_mode), stat.S_IMODE(index.stat().st_mode)
        previous_shared = set(git_dir.glob("sharedindex.*"))
        try:
            git_dir.chmod(git_mode | stat.S_IWUSR)
            index.chmod(index_mode | stat.S_IWUSR)
            subprocess.run(
                ["git", "--git-dir", str(git_dir.resolve()), "--work-tree", str(root.resolve()),
                 "status", "--porcelain", "--untracked-files=no"],
                cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                check=True, timeout=30,
            )
        finally:
            try:
                index.chmod(index_mode)
                # A split index may create another derived file in .git.
                for shared in set(git_dir.glob("sharedindex.*")) - previous_shared:
                    if stat.S_ISREG(shared.lstat().st_mode):
                        shared.chmod(index_mode)
            finally:
                git_dir.chmod(git_mode)
