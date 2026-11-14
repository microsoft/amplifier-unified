"""Owner-scoped facts about retained qualification; never an acceptance path.

Only the exact resource's private configuration and exact proof are read. No
current selection, updater, backend adapter or candidate interpreter is called.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time

from . import managed_runner_updates as updates
from . import test_environment_configs as configs
from . import test_origin_files as files
from .managed_runner_binding import STATIC_CORE_COMMITS

ACTION = 'workspace.environments.runner.read'
HASH = r'[a-f0-9]{64}'
RUNTIME_FIELDS = {'version', 'runtimeSha256', 'files', 'packageVersion', 'pythonVersion'}
INVENTORY_REASONS = frozenset('runtime_inventory_' + suffix for suffix in (
    'custody_unavailable', 'layout_unsupported', 'changed', 'limit', 'rejected', 'startup_unsupported'))


class Unavailable(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('Runner diagnostic evidence is unavailable.')


def runtime_projection(value):
    # A projection is not the equality oracle: compare the complete validated
    # objects, so additional fields can never silently become a match.
    if (not isinstance(value, dict) or set(value) != RUNTIME_FIELDS
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['runtimeSha256'], str)
            or not re.fullmatch(HASH, value['runtimeSha256'])
            or type(value['files']) is not int or not 1 <= value['files'] <= 100000
            or any(not isinstance(value[k], str) or not re.fullmatch(r'[0-9][a-zA-Z0-9.+!_-]{0,79}', value[k])
                   for k in ('packageVersion', 'pythonVersion'))):
        raise Unavailable('runtime_identity_rejected')
    return dict(value)


def read_json(directory, name):
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    with directory.open(name, private=True) as stream:
        before = os.fstat(stream.fileno())
        files.private_info(before, configs.MAX_BYTES)
        content = stream.read(configs.MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        if stamp(before) != stamp(after) or len(content) > configs.MAX_BYTES:
            raise Unavailable('record_changed')
    # Check the name still denotes the opened file, not an unlinked replacement.
    if stamp(directory.stat(name)) != stamp(after):
        raise Unavailable('record_changed')
    return json.loads(content)


def unchanged_directory(path, original):
    with files.directory(path, private=True) as current:
        before, after = os.fstat(original.fd), os.fstat(current.fd)
        if (before.st_dev, before.st_ino, before.st_uid, before.st_mode) != (
                after.st_dev, after.st_ino, after.st_uid, after.st_mode):
            raise Unavailable('record_changed')


def probe(prefix):
    """Run host-owned inventory code with the host base interpreter, without site.

    Never invoke the retained interpreter: -I alone still executes .pth hooks.
    The collector supports only an ordinary venv of this exact host interpreter;
    unsupported custody/layout produces no current identity and no fallback.
    """
    executable = getattr(sys, '_base_executable', None)
    if not executable or not Path(executable).is_absolute():
        raise Unavailable('runtime_probe_unsupported')
    command = [executable, '-I', '-S', '-B', str(Path(__file__).with_name('managed_runner_identity.py')),
               '--retained-prefix', str(prefix)]
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'},
                                   cwd='/', start_new_session=True)
    except OSError:
        raise Unavailable('runtime_probe_unavailable') from None
    output = bytearray()
    deadline = time.monotonic() + 30
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise Unavailable('runtime_probe_timeout')
                for selected, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(selected.fileobj.fileno(), 4097 - len(output))
                    output.extend(chunk)
                    if len(output) > 4096:
                        raise Unavailable('runtime_probe_oversize')
                    if not chunk:
                        selector.unregister(selected.fileobj)
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise Unavailable('runtime_probe_unavailable')
        try:
            value = json.loads(output)
        except (ValueError, TypeError, KeyError):
            raise Unavailable('runtime_probe_malformed') from None
        if (isinstance(value, dict) and set(value) == {'unavailable'}
                and isinstance(value['unavailable'], str) and value['unavailable'] in INVENTORY_REASONS):
            raise Unavailable(value['unavailable'])
        try:
            return runtime_projection(value)
        except (ValueError, TypeError, KeyError):
            raise Unavailable('runtime_probe_malformed') from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def differences(expected, recorded):
    # Field presence matters, but never publish unknown keys supplied by a file.
    missing = object()
    names = []
    for name in sorted(configs.FIELDS - {'maxRunning', 'maxInstances', 'minFreeBytes'}):
        if name == 'incus' and isinstance(expected.get(name), dict) and isinstance(recorded.get(name), dict):
            names.extend('incus.' + field for field in sorted(configs.INCUS_FIELDS - {'maxRunning', 'maxInstances', 'minFreeBytes'})
                         if expected[name].get(field, missing) != recorded[name].get(field, missing))
        elif expected.get(name, missing) != recorded.get(name, missing):
            names.append(name)
    return names


def inspect(home, row):
    result = {'version': 1, 'id': row['id'], 'sessionId': row['sessionId'], 'revision': row['revision'],
              'status': 'blocked', 'code': 'retained_binding_missing', 'readOnly': True,
              'effectsAuthorized': False, 'physical': 'not_observed',
              'qualification': {'status': 'not_inspected'},
              'runtime': {'collection': 'not_attempted'},
              'limitation': 'Inventory is not behavioral qualification or permission to accept changed bytes. Original outcomes remain unchanged.'}
    try:
        if not updates.supported_private_files() or not sys.platform.startswith('linux'):
            raise Unavailable('platform_unsupported')
        if not row.get('backendConfigRetained'):
            return result
        identity = row.get('backendKey')
        if not isinstance(identity, str) or not re.fullmatch(HASH, identity):
            raise Unavailable('retained_binding_rejected')
        try:
            with files.directory(Path(home) / 'workspace-resources' / 'backend-configs', private=True) as folder:
                config = configs.read_at(folder.fd, identity)
                # Retained file hash and installation must agree with the physical
                # owner; a same-repository/commit proof is never borrowed.
                if (config.get('installationId') != row.get('backendInstallation')
                        or not re.fullmatch(r'[a-f0-9]{24}', config.get('installationId', ''))
                        or not re.fullmatch(r'[a-f0-9]{40}', config.get('commit', ''))):
                    raise Unavailable('retained_binding_rejected')
                bound = updates.execution_binding(config)
                result['binding'] = {'expectedSha256': updates.key(bound), 'differingFields': []}
                if config.get('remoteRunner') or config.get('originSnapshotVersion'):
                    raise Unavailable('remote_runtime_unsupported')
                if (not config.get('runnerQualificationRequired')
                        and config['commit'] in STATIC_CORE_COMMITS):
                    raise Unavailable('qualification_not_required')
                proof_path = updates.qualification_path(home, config)
                try:
                    with files.directory(proof_path.parent, private=True) as proofs:
                        try:
                            value = read_json(proofs, proof_path.name)
                        except FileNotFoundError:
                            result['qualification']['status'] = 'absent'
                            raise Unavailable('qualification_missing') from None
                        except (OSError, ValueError, TypeError):
                            result['qualification']['status'] = 'rejected'
                            raise Unavailable('qualification_rejected') from None
                        result['qualification']['status'] = 'found'
                        if (not isinstance(value, dict) or value.get('version') != 1
                                or not isinstance(value.get('normalized'), dict)
                                or set(value['normalized']) - configs.FIELDS
                                or 'incus' in value['normalized'] and (not isinstance(value['normalized']['incus'], dict)
                                    or set(value['normalized']['incus']) - configs.INCUS_FIELDS)):
                            raise Unavailable('qualification_corrupt')
                        result['qualification']['behavioralProof'] = (
                            'recorded' if isinstance(value.get('proof'), dict) and all(
                                value['proof'].get(k) is True for k in ('probeRemoved', 'artifactVerified', 'checkPassed'))
                            else 'incomplete')
                        recorded = updates.execution_binding(value['normalized'])
                        result['binding'].update(proofSha256=updates.key(recorded), differingFields=differences(bound, recorded))
                        try:
                            result['runtime']['recorded'] = runtime_projection(value.get('runtime'))
                        except Unavailable:
                            raise Unavailable('qualification_corrupt') from None
                        if recorded != bound:
                            raise Unavailable('execution_binding_mismatch')
                        prefix = Path(home) / 'smart-tools' / 'installs' / config['installationId'] / 'venv'
                        if config.get('python') != str(prefix / 'bin' / 'python'):
                            raise Unavailable('runtime_binding_rejected')
                        result['runtime']['collection'] = 'unavailable'
                        current = probe(prefix)
                        if read_json(proofs, proof_path.name) != value or configs.read_at(folder.fd, identity) != config:
                            raise Unavailable('record_changed')
                        unchanged_directory(proof_path.parent, proofs)
                        unchanged_directory(Path(home) / 'workspace-resources' / 'backend-configs', folder)
                        result['runtime'].update(collection='collected', current=current)
                        result.update(status='observed', code='runtime_identity_matches' if current == value['runtime'] else 'runtime_identity_changed')
                        return result
                except FileNotFoundError:
                    result['qualification']['status'] = 'absent'
                    raise Unavailable('qualification_missing') from None
                except (OSError, ValueError, TypeError) as exc:
                    if isinstance(exc, Unavailable):
                        raise
                    result['qualification']['status'] = 'rejected'
                    raise Unavailable('qualification_rejected') from None
        except FileNotFoundError:
            raise Unavailable('retained_binding_missing') from None
        except (OSError, ValueError, TypeError) as exc:
            if isinstance(exc, Unavailable):
                raise
            raise Unavailable('retained_binding_rejected') from None
    except Unavailable as exc:
        result['code'] = exc.code
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        result['code'] = 'diagnostic_unavailable'
    return result


async def read(app, args, origin, caller):
    from .test_environment_recovery import read_removal
    from .errors import AppError
    # Metadata and canonical owner reads stay on their owning event loop. Only
    # private file/inventory work leaves it. No effect lock is held across await.
    row, owner = read_removal(app, args, origin, caller, runner=True)
    result = await asyncio.to_thread(inspect, app.data_dir, row)
    if read_removal(app, args, origin, caller, runner=True) != (row, owner):
        raise AppError('Runner inspection changed or is busy.', 409)
    return result
