"""Passive evidence from the current worker. Never imports requested modules."""
import asyncio
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import time

MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_MODULES = 64
MAX_SECONDS = 6
MODULE = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z')


def identities(session, requested):
    rows, mounts, objects = {}, [], []
    names = ['amplifier_core', 'amplifier_foundation', *requested]
    coordinator = getattr(session, 'coordinator', None)
    if coordinator:
        for section in ('orchestrator', 'context', 'providers', 'tools'):
            value = coordinator.get(section)
            values = value.items() if isinstance(value, dict) else [(section, value)]
            for offset, (key, obj) in enumerate(values):
                if offset >= MAX_MODULES or len(mounts) >= MAX_MODULES:
                    break
                if obj is None:
                    continue
                # Follow only the host's ordinary provider wrapper field;
                # never enumerate properties or invoke an inspection hook.
                for depth in range(4):
                    objects.append(obj)
                    name = type(obj).__module__
                    names.append(name)
                    mounts.append({'section': section, 'name': str(key)[:200], 'wrapperDepth': depth,
                                   'module': name, 'class': type(obj).__qualname__[:200]})
                    try:
                        fields = object.__getattribute__(obj, '__dict__')
                    except AttributeError:
                        fields = {}
                    original = fields.get('original') if isinstance(fields, dict) else None
                    if original is None or original is obj or len(mounts) >= MAX_MODULES:
                        break
                    obj = original
    for name in dict.fromkeys(names):
        if len(rows) >= MAX_MODULES:
            break
        module = sys.modules.get(name)
        objects.append(module)
        # __dict__ avoids triggering user-defined module __getattr__.
        values = vars(module) if module is not None else {}
        origin = values.get('__file__')
        rows[name] = {'module': name, 'loaded': module is not None,
                      'origin': origin if isinstance(origin, str) and len(origin) <= 4000 else None}
    # Private strong references prevent id reuse across detached I/O. These
    # objects never enter public provenance or saved command receipts.
    return list(rows.values()), mounts, objects


def hashes(rows):
    total, started = 0, time.monotonic()
    result = []
    for row in rows:
        value = {**row, 'fileStatus': 'unknown', 'importedBytesVerified': False}
        try:
            if not row['origin'] or total >= MAX_TOTAL or time.monotonic() - started > 5:
                raise ValueError('No bounded file evidence.')
            path = Path(row['origin']).resolve()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > min(MAX_FILE, MAX_TOTAL - total):
                    raise ValueError('Source outside inspection limits.')
                data = stream.read(MAX_FILE + 1)
                after = os.fstat(stream.fileno())
            total += len(data)
            current = path.stat(follow_symlinks=False)
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if len(data) != before.st_size or any(getattr(before, key) != getattr(after, key) or getattr(after, key) != getattr(current, key) for key in fields):
                raise ValueError('Source changed during inspection.')
            value.update(origin=str(path), fileStatus='observed', currentFileSha256=hashlib.sha256(data).hexdigest(),
                         bytes=len(data))
        except (OSError, ValueError):
            pass
        result.append(value)
    return result


def snapshot(session, rows, mounts, *, stable=True):
    return {'status': 'observed' if stable else 'unknown', 'identityStable': stable,
            'capturedAt': time.time(), 'modules': rows, 'mounts': mounts,
            'mounted': session is not None, 'python': sys.executable,
            'runtimeSessionId': getattr(session, 'session_id', None),
            'limits': {'modules': MAX_MODULES, 'requestedModules': 16, 'fileBytes': MAX_FILE, 'totalBytes': MAX_TOTAL},
            'limitations': ['Loaded origin and current file bytes do not prove the exact imported bytes.',
                'Inspection covers only listed modules; subprocess, JavaScript and transitive dependency closure are not proven.',
                'No imports, installs, tests or worker startup were performed.']}


def capture_mount(session):
    """Record bounded in-memory identities; readiness never waits for file I/O."""
    rows, mounts, _ = identities(session, ())
    value = snapshot(session, [{**row, 'fileStatus': 'unknown', 'importedBytesVerified': False}
                               for row in rows], mounts)
    value['limitations'].append('Mount-time file bytes were not inspected; request provenance explicitly to observe current files.')
    return value


async def observe(session, requested=(), *, mounted=None):
    if (not isinstance(requested, (tuple, list)) or len(requested) > 16
            or any(not isinstance(name, str) or len(name) > 200 or not MODULE.fullmatch(name) for name in requested)):
        raise ValueError('Choose at most sixteen explicit module names.')
    rows, mounts, objects = identities(session, requested)
    captured = rows
    try:
        async with asyncio.timeout(MAX_SECONDS):
            rows = await asyncio.to_thread(hashes, rows)
    except TimeoutError:
        rows = [{**row, 'fileStatus': 'unknown', 'importedBytesVerified': False} for row in rows]
    current, current_mounts, current_objects = identities(session, requested)
    stable = (current == captured and current_mounts == mounts
              and len(objects) == len(current_objects)
              and all(before is after for before, after in zip(objects, current_objects)))
    prior = {row['module']: row for row in (mounted or {}).get('modules', [])}
    for row in rows:
        stamp = prior.get(row['module'])
        if stamp and stamp.get('origin') == row.get('origin') and stamp.get('fileStatus') == 'observed':
            row['fileObservedAtMount'] = {'sha256': stamp['currentFileSha256'], 'capturedAt': mounted['capturedAt']}
            row['changedSinceMount'] = (row['currentFileSha256'] != stamp['currentFileSha256']) if row.get('fileStatus') == 'observed' else None
    return snapshot(session, rows, mounts, stable=stable)
