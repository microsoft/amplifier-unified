"""Private staging copies: copy-on-write where supported, never writable hardlinks."""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import itertools
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


# An earlier version of the app copied its own config and routing folders into
# each stage. One idle pass handles at most this many entries (files, links and
# folders) of those copies, in at most this many stages. An entry that the pass
# must leave is one of these entries too. A larger copy goes on at a later
# pass. The removal runs on the host's event loop, as the removal of a keys
# file does, and on Windows each file goes through the private-file boundary:
# the count bounds that time. A hosted Windows runner needed about 5 ms for one
# entry.
STAGE_CONFIG_ENTRIES = 64
STAGE_CONFIG_STAGES = 8
_STAGE_CONFIG_DEPTH = 32


class _Work:
    """What one pass, or one call, did to such copies, and the most entries that it may handle."""

    def __init__(self, limit):
        self.limit, self.removed, self.stayed = limit, 0, 0

    def remaining(self):
        return max(0, self.limit - self.removed - self.stayed)

    def full(self):
        return self.remaining() <= 0


class _Moved(Exception):
    """A folder of the walk is no longer the entry of its name in the folder above it."""


def _reparse(info):
    """A link, a junction or another reparse point. It is never followed."""
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _posix_remove_tree(parent, name, device, work, still, depth=0):
    """Remove the folder ``name`` of the open folder ``parent``, the deepest entries first.

    Each step is relative to an open folder and follows no link: a link in the
    tree is unlinked as a link, and its target stays. Return None when the
    folder is gone, 'more' when the walk stopped at the most entries that the
    work may handle, else a reason code. An entry that cannot be removed stays
    and counts, and the other entries are still handled. The listing of a
    folder takes no more entries than the work may still handle, and one more.

    ``still()`` raises when a folder above this one is no longer the entry of
    its name. Directly before each removal the walk asks it and looks at this
    folder's own name again. A folder that was moved or replaced ends the walk
    of the stage (_Moved). The walk looks at these folders one after another.
    No call can stop another process of the same account from moving a folder
    after the walk looked at it and before the removal, as for the keys file
    (remove_stage_keys). Such a removal then still takes only an entry of a
    folder that was this copy when the walk opened it, or an empty folder that
    has the copy's name at that moment.

    A folder of another device is refused. A mount of the same device at an
    entry of the copy is not told apart from a folder; only an administrator
    can make one.
    """
    reason = _posix_tree(parent, name, device, work, still, depth)
    if reason not in (None, 'more') and not work.full():
        work.stayed += 1  # This folder stays. The count never passes its limit.
    return reason


def _posix_tree(parent, name, device, work, still, depth):
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISDIR(info.st_mode) or info.st_dev != device
                or depth >= _STAGE_CONFIG_DEPTH):
            return 'refused'

        def here():
            try:
                still()
                now = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except Exception:
                raise _Moved() from None
            if _identity(now) != _identity(info):
                raise _Moved()

        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if _identity(os.fstat(fd)) != _identity(info):
                return 'refused'
            reason = None
            # The listing first: a removal during a listing can make it leave out an entry.
            # One entry more than the work may handle tells that the folder has more.
            with os.scandir(fd) as listing:
                entries = list(itertools.islice(listing, work.remaining() + 1))
            cut = len(entries) > work.remaining()
            for entry in entries:
                if work.full():
                    return 'more'
                try:
                    if entry.is_dir(follow_symlinks=False):
                        result = _posix_remove_tree(fd, entry.name, device, work, here, depth + 1)
                        if result == 'more':
                            return result
                        reason = reason or result
                    else:
                        here()
                        os.unlink(entry.name, dir_fd=fd)
                        work.removed += 1
                except FileNotFoundError:
                    pass
                except PermissionError:
                    work.stayed += 1
                    reason = reason or 'refused'
                except OSError:
                    work.stayed += 1
                    reason = reason or 'io'
        finally:
            os.close(fd)
        if cut:
            return 'more'
        if reason:
            return reason
        if work.full():
            return 'more'
        here()
        os.rmdir(name, dir_fd=parent)
        work.removed += 1
        return None
    except FileNotFoundError:
        return None
    except PermissionError:
        return 'refused'
    except OSError:
        return 'io'


@contextmanager
def _windows_hold(folder):
    """Windows: open one folder of a copy as that exact object, and give the removal of that object.

    The private-file boundary pins a folder with a handle that asks for its
    attributes only. Windows lets another program rename a folder that has
    only such a handle. So the walk opens its own handle first. The open
    refuses a junction (and each other reparse point, by the same test), a
    file, and a path that goes through a junction (windows_files). The handle
    asks for the right to delete and shares reading only. While it is open,
    Windows refuses every program the rename of this folder and its removal by
    its path, and the rename of a folder above it: the path of the folder
    names this object until the handle is closed. The function that this gives
    removes the folder through the handle: this object, only when it is empty,
    and never what its path names at that moment.

    A native case shows each of these on a hosted Windows runner: the rename
    that passes with the boundary's pin alone, the refusals while the handle
    is open (by this process and by another one), the removal through the
    handle, and the three refusals of the open.
    """
    import ctypes
    from .windows_files import BYTE, _API
    api = _API()
    handle = api.open(folder, directory=True, cleanup=True)
    try:
        def remove():
            delete = BYTE(1)  # FileDispositionInfo: delete this open object when its last handle is closed.
            api.ok(api.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)))
        yield remove
    finally:
        api.close(handle)


def _windows_remove_tree(folder, home, work, depth=0):
    """Windows: remove one folder through the private-file boundary, the deepest entries first.

    The walk holds the folder first (_windows_hold), and every folder of the
    copy above it is held in the same way. Then the boundary pins the folder
    and every folder above it, checks their ACLs, and deletes each file as the
    exact object whose bytes it read. A link, a junction, a file above 1 MiB,
    a file with an ACL that is not private and a file that another program
    holds open all stay, with their folders, and each counts; the other
    entries are still handled. The listing of a folder takes no more entries
    than the work may still handle, and one more.

    The pin, the listing and the step into a folder of the copy go by the
    path. The hold keeps that path on the held folder for all of that time. A
    folder is removed only when it is empty, after its own pin is closed, and
    through its hold: never by its path. What takes a name of the copy before
    the walk comes to that name is handled as what it is: a folder as a folder
    of the copy, a link or a junction not at all.
    """
    reason = _windows_tree(folder, home, work, depth)
    if reason not in (None, 'more') and not work.full():
        work.stayed += 1  # This folder stays. The count never passes its limit.
    return reason


def _windows_tree(folder, home, work, depth):
    from .windows_files import directory
    try:
        if depth >= _STAGE_CONFIG_DEPTH:
            return 'refused'
        with _windows_hold(folder) as remove:
            reason = None
            with directory(folder, owned_root=home, create=False) as pinned:
                with os.scandir(folder) as listing:
                    entries = list(itertools.islice(listing, work.remaining() + 1))
                cut = len(entries) > work.remaining()
                for entry in entries:
                    if work.full():
                        return 'more'
                    try:
                        child = entry.stat(follow_symlinks=False)
                        if _reparse(child):
                            work.stayed += 1
                            reason = reason or 'refused'
                        elif stat.S_ISDIR(child.st_mode):
                            result = _windows_remove_tree(folder / entry.name, home, work, depth + 1)
                            if result == 'more':
                                return result
                            reason = reason or result
                        else:
                            digest = hashlib.sha256(
                                pinned.read_private(entry.name, limit=1024 * 1024)).hexdigest()
                            pinned.remove_private(entry.name, digest)
                            work.removed += 1
                    except FileNotFoundError:
                        pass
                    except PermissionError:
                        work.stayed += 1
                        reason = reason or 'refused'
                    except OSError:
                        work.stayed += 1
                        reason = reason or 'io'
                    except Exception:
                        work.stayed += 1
                        reason = reason or 'refused'
            if cut:
                return 'more'
            if reason:
                return reason
            if work.full():
                return 'more'
            remove()
            work.removed += 1
            return None
    except FileNotFoundError:
        return None
    except PermissionError:
        return 'refused'
    except OSError:
        return 'io'
    except Exception:
        return 'refused'


def _windows_times(folder, info):
    """Windows: write a folder's times through a handle of that folder.

    ``os.utime`` with a path follows a link or a junction at the path, and on
    Windows it has no other form. The handle is of the entry itself. A link, a
    junction and a path that names the folder through another spelling are
    refused (windows_files), and then no time is written.
    """
    import ctypes
    import struct
    from .windows_files import _API, _INVALID
    api = _API()
    # READ_CONTROL, and the attributes to read and to write. The entry itself, and a folder.
    handle = api.CreateFileW(str(folder), 0x20180, 7, None, 3, 0x2200000, None)
    if handle == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        api.info(handle, folder, directory=True)
        epoch = 116444736000000000  # 1970, in the units of 100 ns since 1601 that Windows keeps.
        # FILE_BASIC_INFO: creation, access, write, change, attributes. A zero leaves a value as it is.
        times = struct.pack('<qqqqI4x', 0, info.st_atime_ns // 100 + epoch, info.st_mtime_ns // 100 + epoch, 0, 0)
        buffer = ctypes.create_string_buffer(times, len(times))
        api.ok(api.SetFileInformationByHandle(handle, 0, buffer, len(times)))
    finally:
        api.close(handle)


def _keep_times(folder, info):
    """Put a stage folder's times back after a removal directly below it.

    A keys file of a stage with no receipt waits 24 hours from that time
    (scrub_stage_keys). Those hours are for what a preparation wrote: this
    removal must not start them again. ``folder`` is an open folder, or on
    Windows a path.
    """
    try:
        if os.name == 'nt':
            _windows_times(folder, info)
        else:
            os.utime(folder, ns=(info.st_atime_ns, info.st_mtime_ns))
    except Exception:
        pass  # The keys file then waits its 24 hours from this removal.


def _posix_stage_config(stage, expected, work):
    reason, before = None, work.removed
    with _posix_directory(stage, expected) as (fd, check):
        info = os.fstat(fd)
        try:
            for name in ('config', 'routing'):
                result = _posix_remove_tree(fd, name, info.st_dev, work, check)
                if result == 'more':
                    return result
                reason = reason or result
        finally:
            if work.removed != before:
                _keep_times(fd, info)
    return reason


def _windows_stage_config(home, stage, expected, work):
    from .windows_files import directory
    reason, before = None, work.removed
    with directory(stage, owned_root=home, create=False):
        info = stage.lstat()
        if expected and any(_identity(path.lstat()) != identity
                            for path, identity in expected.items()):
            raise ValueError('Stage identity changed')
        try:
            for name in ('config', 'routing'):
                result = _windows_remove_tree(stage / name, home, work)
                if result == 'more':
                    return result
                reason = reason or result
        finally:
            if work.removed != before:
                _keep_times(stage, info)
    return reason


def _remove_stage_config(home, stage, expected, work):
    """remove_stage_config with the work of a whole pass: the reason only. It never raises."""
    try:
        home, stage = _stage_address(home, stage)
        if expected:
            # The walk opens the stage by its whole path. A caller can name it from a relative app home.
            from .windows_files import address
            expected = {address(path): identity for path, identity in expected.items()}
        if os.name == 'nt':
            return _windows_stage_config(home, stage, expected, work)
        return _posix_stage_config(stage, expected, work)
    except FileNotFoundError:
        return None  # No such stage.
    except PermissionError:
        return 'refused'
    except OSError:
        return 'io'
    except Exception:
        return 'refused'  # Also a folder that was moved or replaced during the walk.


def remove_stage_config(home, stage, *, expected=None, limit=STAGE_CONFIG_ENTRIES):
    """Remove only one stage's copies of the app's own config and routing folders.

    Only the entries ``config`` and ``routing`` directly below the stage go,
    with their content. Nothing reads them. An entry of that name that is a
    link, a junction or no folder is refused: it stays and is never followed.

    Return (reason, count). The reason is None when neither entry is left.
    'more' says that the call stopped at its limit and the copy has entries
    left; 'refused' and 'io' say that an entry could not be removed and stays.
    The limit is the most entries (files, links and folders) that the call
    handles: an entry that it removes, and an entry that stays. The count is
    the number of entries that this call removed. What is removed stays
    removed, and a later call goes on with the rest. Neither value holds a
    name, a size or bytes of a file. The call never raises; a reason is
    retryable and is no update failure.

    A later call lists each folder from its start again. So a folder whose
    first ``limit`` listed entries all stay is not handled further: the
    entries behind them stay too, and so does a folder of the copy that the
    walk reaches only after that folder.
    """
    work = _Work(limit)
    return _remove_stage_config(home, stage, expected, work), work.removed


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
    then leaves the stage alone: another status, an entry that is no regular
    file, a record that is too long or that cannot be read.

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


def _scrub_stage_config(manager, root, protected):
    """The part of the idle pass that removes stage copies of the app's config and routing folders.

    scrub_stage_keys calls it with its own admission, under the update lock.
    It has its own place in the listing and its own bounds, so it changes no
    rule of the keys file: at most STAGE_CONFIG_STAGES stages with such a copy
    and STAGE_CONFIG_ENTRIES entries in one pass, and one look at the clock
    between two stages, as for the keys file. One pass goes at most once around
    the stages, and it starts where the pass before stopped. So a copy that was
    cut, or a stage whose entries stay, gets its next turn after every other
    stage. A pass that starts at a saved place reads the list of stages from
    that place to the end and then, when its bounds permit, from the start to
    that place: at most two readings. It handles no stage twice, also when
    another process changes the list between the two readings; a stage can
    then wait for the next pass.
    It handles every stage that still has such an entry: with or without
    a receipt, a keys file or shared-config, and at every age, because nothing
    reads the copies. It leaves the stage of a preparation that is not
    reconciled. It never raises.
    """
    try:
        start = getattr(manager, '_stage_config_offset', 0)
        deadline, stages, work = time.monotonic() + 1, 0, _Work(STAGE_CONFIG_ENTRIES)
        progressed, handled = False, set()
        # From the saved place to the end of the listing, then from its start to the saved place.
        for first, end in ((start, None), (0, start)) if start else ((0, None),):
            with os.scandir(root) as entries:
                for index, entry in enumerate(entries):
                    if index < first:
                        continue
                    if end is not None and index >= end:
                        break
                    if (stages >= STAGE_CONFIG_STAGES or work.full()
                            or (progressed and time.monotonic() >= deadline)):
                        return
                    progressed = True
                    manager._stage_config_offset = index + 1
                    if (not re.fullmatch('[a-f0-9]{32}', entry.name) or entry.name in protected
                            or entry.name in handled):
                        continue
                    stage = root / entry.name
                    try:
                        info = stage.lstat()
                        if not stat.S_ISDIR(info.st_mode) or not any(
                                os.path.lexists(stage / name) for name in ('config', 'routing')):
                            continue
                    except OSError:
                        continue
                    stages += 1
                    handled.add(entry.name)
                    _remove_stage_config(manager.home, stage, {stage: _identity(info)}, work)
            if end is None:
                manager._stage_config_offset = 0
    except Exception:
        return  # A later pass tries again.


async def scrub_stage_keys(manager):
    """Key-only maintenance, independent of unused-code policy; never retire trees.

    At most eight key-removal attempts per pass. The scan offset rotates over
    later entries on subsequent idle passes. Entries before the offset are only
    listed and counted, without a look at them. The second of a pass starts
    before that listing. At an entry after the offset the pass stops when that
    second has gone, but it handles at least one entry first, so a slow folder
    or a long listing cannot hold the scan at one place.

    Before that scan, under the same admission, the pass removes the copies of
    the app's own config and routing folders that an earlier version made in a
    stage (_scrub_stage_config). That removal has its own bounds and its own
    place in the listing. It puts the times of a stage folder back, so it does
    not start the 24 hours of a keys file again.
    """
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
            _scrub_stage_config(manager, root, protected)
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
                        if not re.fullmatch('[a-f0-9]{32}', entry.name) or entry.name in protected:
                            continue
                        stage = root / entry.name
                        shared = stage / 'shared-config'
                        try:
                            stage_info, shared_info = stage.lstat(), shared.lstat()
                            if not all(stat.S_ISDIR(info.st_mode) for info in (stage_info, shared_info)):
                                continue
                            # Already-scrubbed stages cost this one look and no effect budget.
                            (shared / 'keys.env').lstat()
                            try:
                                validated = stat.S_ISREG((stage / 'validated.json').lstat().st_mode)
                            except FileNotFoundError:
                                validated = False
                            if not validated:
                                try:
                                    (stage / 'profile-attempt.json').lstat()
                                except FileNotFoundError:
                                    if max(stage_info.st_mtime, shared_info.st_mtime) >= time.time() - 86400:
                                        continue
                                else:
                                    # A chat's own setup owns this stage. Age is not settlement:
                                    # only its own record says that the preparation ended. Nothing
                                    # proves that no other app process still prepares there.
                                    if not _ended_without_admission(stage):
                                        continue
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
