"""Explicit, installation-only repair; the detached worker owns its receipt.

No reset, update-fence reconciliation, input replay, or cache retirement happens
here. The private on-disk operation is authoritative across host replacement.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import time
import tomllib
from urllib.parse import urlsplit
import uuid

PROTOCOL = 1
ACTIVE = {'accepted', 'running', 'unknown', 'reinstalled'}
RELEASED = {'ready', 'rolled-back', 'failed'}
PRESERVES = ['conversations', 'settings', 'credentials', 'workspaces',
             'source-selections', 'downloaded-sources', 'operation-receipts',
             'optional-features']
REPLACES = ['uv-tool-environment', 'application-launch-pointer']
IDENTITY = re.compile(r'[a-f0-9]{32}')


def root(service):
    return Path(service.data_dir) / 'installation-repair'


def _private(path, home, *, directory=False):
    """Reject redirected/shared repair evidence, including all owned parents."""
    path, home = Path(path), Path(home)
    relative = path.relative_to(home)
    for part in [home, *(home.joinpath(*relative.parts[:i])
                         for i in range(1, len(relative.parts) + 1))]:
        info = part.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & (0o022 if part == home else 0o077)):
            raise ValueError('Installation repair evidence is not private.')
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
        raise ValueError('Installation repair evidence has an unsupported type.')
    return path


def _read(path, home):
    _private(path, home)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('Installation repair receipt is not private.')
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Installation repair receipt is too large.')
    return json.loads(raw)


def _write(path, value, home):
    from .deployment import write_private
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _private(path.parent, home, directory=True)
    if path.exists() or path.is_symlink():
        _private(path, home)
    write_private(path, json.dumps(value, sort_keys=True) + '\n')
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _admission(service):
    """Cross-process admission, distinct from the host's shared reset lease."""
    from .reset import repair_lock
    folder = root(service)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    _private(folder, service.data_dir, directory=True)
    with repair_lock(folder / 'admission'):
        yield


def _folder(service, identity):
    if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
        raise ValueError('Choose an exact installation repair operation.')
    return root(service) / identity


def _latest(service):
    identity = _admissions(service)['current']
    return _operation(service, identity) if identity else None


def _admissions(service):
    """One atomic admission owns both current identity and exact preview lookup."""
    path = root(service) / 'admissions.json'
    if not path.exists() and not path.is_symlink():
        legacy = root(service) / 'latest.json'
        if legacy.exists() or legacy.is_symlink():
            raise ValueError('An older repair admission needs explicit owner review; it was not cleared.')
        return {'schema': 1, 'current': None, 'previews': {}}
    value = _read(path, service.data_dir)
    if (not isinstance(value, dict) or set(value) != {'schema', 'current', 'previews'}
            or type(value['schema']) is not int or value['schema'] != 1
            or not isinstance(value['previews'], dict)
            or any(not isinstance(key, str) or not IDENTITY.fullmatch(key)
                   or not isinstance(identity, str) or not IDENTITY.fullmatch(identity)
                   for key, identity in value['previews'].items())
            or value['current'] is not None and value['current'] not in value['previews'].values()):
        raise ValueError('The installation repair admission is unrecognized.')
    return value


def _operation(service, identity):
    operation = _read(_folder(service, identity) / 'operation.json', service.data_dir)
    if (not isinstance(operation, dict) or operation.get('operationId') != identity
            or not isinstance(operation.get('previewId'), str)
            or not IDENTITY.fullmatch(operation['previewId'])
            or not isinstance(operation.get('hostInstanceId'), str)
            or operation.get('status') not in ACTIVE | RELEASED
            or not isinstance(operation.get('stage'), str)
            or type(operation.get('reinstalled')) is not bool
            or type(operation.get('healthVerified')) is not bool
            or operation.get('serviceState') not in {'not-checked', 'stopped', 'running', 'unknown'}):
        raise ValueError('The installation repair receipt has an unrecognized shape.')
    if operation['status'] == 'ready' and not (
            operation['reinstalled'] and operation['healthVerified']
            and operation['serviceState'] == 'running'):
        raise ValueError('The installation repair ready receipt is incomplete.')
    return operation


def initialize(service):
    """Restore only bounded display metadata, never dispatch a saved helper."""
    operation = None
    if sys.platform in {'linux', 'darwin'}:
        try:
            operation = _latest(service)
        except (OSError, ValueError, KeyError, TypeError):
            operation = {'status': 'unknown', 'stage': 'receipt-unreadable',
                         'detail': 'The saved installation repair could not be read. Do not repeat it.',
                         'reinstalled': False, 'healthVerified': False, 'serviceState': 'unknown'}
    service.state.setdefault('maintenance', {})['installationRepair'] = {
        'preview': None, 'operation': operation, 'verifyTarget': _verify_target(service, operation),
        'reconcileTarget': _reconcile_target(service, operation)}
    service._installation_repair_gate = (
        operation.get('operationId') or 'receipt-unreadable'
        if operation and operation.get('status') in ACTIVE else None)
    service._installation_repair_lock = asyncio.Lock()
    service._installation_repair_uncommitted = None


def held(service):
    identity = getattr(service, '_installation_repair_gate', None)
    if not identity:
        return False
    uncommitted = getattr(service, '_installation_repair_uncommitted', None)
    if uncommitted and uncommitted['operationId'] == identity:
        try:
            # An existing, readable, byte-logically identical PRIOR admission
            # plus this generation's no-dispatch witness proves no admission.
            # Missing/empty/unreadable index is NOT proof and cannot clear.
            path = root(service) / 'admissions.json'
            if (path.is_file() and _admissions(service) == uncommitted['prior']
                    and identity not in uncommitted['prior']['previews'].values()):
                service._installation_repair_gate = None
                service._installation_repair_uncommitted = None
                return False
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        operation = _latest(service)
        if (operation and operation.get('operationId') == identity
                and operation.get('status') in RELEASED):
            service._installation_repair_gate = None
            return False
    except (OSError, ValueError, TypeError, KeyError):
        pass  # An unreadable receipt never releases admission or starts repair.
    return True


def guard(service):
    if held(service):
        raise ValueError('Installation repair owns this host. Inspect its saved operation; no new work was admitted.')


def blocks_action(action, args):
    from .update_drain import new_work
    from .workspace_work_effects import SAFE_ACTIONS
    if action in SAFE_ACTIONS or action in {
            'updates.diagnostics', 'configuration.operation.read',
            'conversation.stop', 'worker.stop', 'call.end',
            'coordination.interrupt', 'assignment.stop', 'task.stop',
            'schedule.pause', 'schedule.cancel'}:
        return False
    return (new_work(action, args) or action.startswith((
        'configuration.', 'runtime.', 'bundle.', 'bundles.', 'sources.',
        'modules.', 'providers.', 'routing.', 'permissions.', 'locations.',
        'updates.', 'schedule.', 'coordination.', 'assignment.'))
        or action in {'session.create', 'session.fork', 'session.recover',
                      'workspace.create', 'workspace.remove', 'workspace.initialize',
                      'maintenance.reset', 'maintenance.repair', 'maintenance.resetCleanup',
                      'settings.update', 'externalAgents.save', 'externalAgents.remove',
                      'operations.submit', 'operations.write', 'kernels.create',
                      'kernels.execute', 'kernels.reset', 'voice.preview', 'voice.configure',
                      'conversation.configure'})


def _pointer(home):
    path = Path(home) / 'updates/application-active.json'
    if not path.exists() and not path.is_symlink():
        return {'exists': False, 'sha256': None}
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise ValueError('The application launch pointer is redirected.')
    return {'exists': True, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _file_identity(path):
    path = Path(path)
    info = path.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022 and not path.is_symlink():
        raise ValueError('The installation entry point is not owned by this account.')
    return {'path': str(path), 'stat': [info.st_dev, info.st_ino],
            'link': os.readlink(path) if path.is_symlink() else None,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _declared_extras(prefix, home):
    """Union observed features with owning install/generation declarations.

    Broken/missing optional distributions must not silently remove a feature.
    Unknown/malformed declarations refuse repair rather than guess.
    """
    from .app_updates import installed_extras, validated_extras
    from .application_generations import active, bootstrap_identity
    receipt = Path(prefix) / 'uv-receipt.toml'
    if receipt.is_symlink() or not receipt.is_file():
        raise ValueError('The owning uv installation receipt could not be verified.')
    raw = receipt.read_bytes()
    tool = tomllib.loads(raw.decode()).get('tool', {})
    declared = set()
    for row in tool.get('requirements', []):
        if row.get('name') == 'amplifier-unified':
            declared.update(validated_extras(row.get('extras', [])))
    generation = active(home)
    if generation:
        if (generation.get('bootstrap') != bootstrap_identity(prefix)
                or Path(sys.prefix).resolve() != generation['prefix'].resolve()):
            raise ValueError('The active generation does not belong to this serving bootstrap.')
        qualified = json.loads((generation['folder'] / 'validated.json').read_text())
        declared.update(validated_extras(qualified.get('extras', [])))
    declared.update(validated_extras(installed_extras()))
    return sorted(declared), hashlib.sha256(raw).hexdigest()


def _service(home, launcher):
    """Bind a literal generated definition AND its loaded manager-owned PID."""
    from . import deployment_service as services, reset
    commands = reset.service_commands(home)
    if commands is None or not services.current_process_is_unit_managed(home):
        raise ValueError('Repair requires this host to run in its generated background service.')
    if sys.platform == 'linux':
        path = services.unit_path()
        text = path.read_text()
        if not services._generated_unit_matches(text, home):
            raise ValueError('The background service has custom launch arguments.')
        # Refuse effective overrides or a not-yet-loaded definition.
        result = reset.command('systemctl', '--user', 'show', reset.UNIT,
            '--property=MainPID', '--property=FragmentPath', '--property=DropInPaths',
            '--property=NeedDaemonReload', capture=True, timeout=10)
        properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if (properties.get('MainPID') != str(os.getpid())
                or properties.get('FragmentPath') != str(path)
                or properties.get('DropInPaths') or properties.get('NeedDaemonReload') != 'no'):
            raise ValueError('The loaded service does not uniquely own this running host.')
        import shlex
        args = shlex.split(next(line[10:] for line in text.splitlines() if line.startswith('ExecStart=')))
    else:
        path = services.launchd_path()
        definition = plistlib.loads(path.read_bytes())
        if 'Program' in definition or definition.get('EnableGlobbing'):
            raise ValueError('The launch agent overrides its generated executable.')
        args = definition.get('ProgramArguments', [])
        if (not isinstance(args, list) or len(args) != 7
                or args[1:3] != ['--no-open', '--data-dir']
                or args[3] != str(home) or args[4] != '--workspace'
                or not Path(args[5]).is_absolute() or args[6] != 'serve'):
            raise ValueError('The launch agent has custom launch arguments.')
        result = reset.command('launchctl', 'print', f'gui/{os.getuid()}/{reset.LABEL}',
                               capture=True, timeout=10)
        pids = re.findall(r'^\s*pid = (\d+)\s*$', result.stdout, re.M)
        if pids != [str(os.getpid())]:
            raise ValueError('The loaded launch agent does not own this running host.')
        programs = re.findall(r'^\s*program = (.+?)\s*$', result.stdout, re.M)
        if programs != [str(args[0])]:
            raise ValueError('The loaded launch agent executable differs from its definition.')
    if not args or Path(args[0]).resolve() != Path(launcher).resolve():
        raise ValueError('The service does not launch the verified uv tool.')
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
        raise ValueError('The service definition is redirected or writable by another account.')
    process_start = reset.command('ps', '-p', str(os.getpid()), '-o', 'lstart=',
                                  capture=True, timeout=10).stdout.strip()
    if not process_start:
        raise ValueError('The managed host start identity could not be verified.')
    return {'commands': [list(row) if row is not None else None for row in commands], 'path': str(path),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'pid': os.getpid(),
            'processStart': process_start}


async def binding(service):
    from . import app_updates, reset
    from .auth import data_identity
    if sys.platform not in {'linux', 'darwin'}:
        raise ValueError('In-app installation repair is not qualified on native Windows or this platform.')
    manager = service.update_manager
    if manager is None:
        raise ValueError('The update owner is unavailable.')
    home = Path(service.data_dir).resolve()
    if home == Path.home().resolve() or home == Path(home.anchor):
        raise ValueError('Repair requires a dedicated application data directory.')
    _private(home, home, directory=True)
    uv, launcher, python, previous = await app_updates.installed_target()
    prefix = Path(previous['installation'])
    tool_root, bin_root = prefix.parent, Path(launcher).parent
    if prefix.is_symlink() or home.is_relative_to(prefix) or prefix.is_relative_to(home):
        raise ValueError('The uv installation must be separate from application data.')
    base = Path(getattr(sys, '_base_executable', '')).resolve()
    if not base.is_file() or not os.access(base, os.X_OK) or base.is_relative_to(prefix) or base.is_relative_to(home):
        raise ValueError('A stable base Python outside the repair target is required.')
    owner = await asyncio.to_thread(_service, home, launcher)
    extras, receipt_digest = await asyncio.to_thread(_declared_extras, prefix, home)
    from .application_generations import bootstrap_identity
    bootstrap = await asyncio.to_thread(bootstrap_identity, prefix)
    launcher_identity = await asyncio.to_thread(_file_identity, launcher)
    from .installation_repair_worker import executable_identity
    executables = await asyncio.to_thread(lambda: {
        'uv': executable_identity(uv), 'basePython': executable_identity(base)})
    info = home.stat()
    env = reset.environment()
    env.update(UV_TOOL_DIR=str(tool_root), UV_TOOL_BIN_DIR=str(bin_root))
    if 'managed-browser' in extras:
        from .app_features import managed_browser_cache
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(managed_browser_cache(home))
    from .update_readiness import probe_targets
    urls, pin = await asyncio.to_thread(probe_targets, manager)
    urls = [url for url in urls if urlsplit(url).scheme == 'https'
            and ipaddress.ip_address(urlsplit(url).hostname).is_loopback]
    if not urls or pin is None:
        raise ValueError('Repair requires a pinned HTTPS loopback listener. Configure TLS before repair; no control credential was sent.')
    return {'home': str(home), 'dataStat': [info.st_dev, info.st_ino],
            'dataIdentity': data_identity(home), 'uid': os.getuid(),
            'hostInstanceId': service.instance_id, 'runningIdentity': copy.deepcopy(manager.running_identity),
            'toolRoot': str(tool_root), 'binRoot': str(bin_root), 'uv': str(uv),
            'basePython': str(base), 'service': owner, 'extras': extras,
            'executables': executables,
            'bootstrap': bootstrap, 'uvReceiptDigest': receipt_digest,
            'launcher': launcher_identity,
            'pointer': await asyncio.to_thread(_pointer, home),
            'health': {'urls': urls, 'pin': pin.fingerprint.hex() if pin is not None else None},
            'env': env}


def require_idle(service):
    from .update_blockers import assess
    manager = service.update_manager
    state = service.state.get('updates', {})
    if (service.closed or not manager or manager.closed or manager.lock.locked()
            or manager.check_task and not manager.check_task.done()
            or any(state.get(key) for key in ('pendingRestart', 'pendingReplacement',
                'pendingApp', 'pendingRelease', 'pendingRollback', 'pendingSmartTools'))
            or state.get('phase') in {'activating', 'checking', 'staging', 'app-staging'}
            or (state.get('drain') or {}).get('phase') in {'waiting', 'draining', 'replacing', 'releasing'}
            or getattr(manager, '_manual_handoff_commit_unknown', False)):
        raise ValueError('Finish or inspect the existing update operation before installation repair.')
    if assess(service, application=True)['blockers']:
        raise ValueError('Finish active work before confirming installation repair.')
    runtime = service.runtime
    if (runtime is None or not callable(getattr(runtime, 'has_pending_operations', None))
            or runtime.has_pending_operations() or getattr(runtime, '_preparations', None)):
        raise ValueError('Finish active conversation runtime work before installation repair.')
    management = service.management
    if management and (management.lock.locked() or any(
            not task.done() for task in management.pending_config_tasks.values())):
        raise ValueError('Finish configuration management before installation repair.')
    if service.runtime_lifecycle_lock.locked():
        raise ValueError('Finish runtime lifecycle work before installation repair.')
    operations = getattr(service, 'operations', None)
    if operations:
        if operations.pending or operations.journal.db.execute(
                "SELECT 1 FROM operations WHERE json_extract(value,'$.state') "
                "IN ('queued','running','cancel_requested') LIMIT 1").fetchone():
            raise ValueError('Finish owned processes and computation kernels before installation repair.')
    from .update_health import pending_preparation
    if pending_preparation(manager.diagnostics.state):
        raise ValueError('Finish update preparation before installation repair.')


def _publish(service, *, preview=None, operation=None, replace_preview=False):
    projection = service.state.setdefault('maintenance', {}).setdefault(
        'installationRepair', {'preview': None, 'operation': None})
    if replace_preview:
        projection['preview'] = preview
    projection['operation'] = copy.deepcopy(operation)
    projection['verifyTarget'] = _verify_target(service, operation)
    projection['reconcileTarget'] = _reconcile_target(service, operation)
    service._publish_changes(globals={'maintenance'})
    return copy.deepcopy(projection)


def _verify_target(service, operation):
    if (operation and operation.get('status') == 'reinstalled'
            and operation.get('reinstalled') is True and operation.get('healthVerified') is False):
        return {'operationId': operation['operationId'],
                'hostInstanceId': service.instance_id, 'confirm': True}
    return None


def _reconcile_target(service, operation):
    # This is eligibility to inspect, never assertion that unknown work had no
    # effect. The locked worker must positively prove that before releasing.
    if (operation and operation.get('operationId')
            and operation.get('status') in {'accepted', 'running', 'unknown'}
            and operation.get('reinstalled') is False):
        return {'operationId': operation['operationId'],
                'hostInstanceId': service.instance_id, 'confirm': True}
    return None


async def preview(service):
    async with service._installation_repair_lock:
        try:
            observed = await binding(service)
            from . import reset, app_release_artifacts as artifacts
            # Resolve once; confirmation never silently selects a moving latest.
            release = await asyncio.to_thread(reset.release_metadata, observed['env'])
            version = artifacts.stable_version(release['tag_name'])
            if release.get('draft') or release.get('prerelease'):
                raise ValueError('Repair requires a published stable release.')
            output = await asyncio.to_thread(reset.download_command, 'git', 'ls-remote',
                'https://github.com/' + reset.REPOSITORY, 'refs/tags/v' + version,
                'refs/tags/v' + version + '^{}', env=observed['env'], timeout=60)
            rows = dict((ref, sha) for sha, ref in (line.split() for line in output.stdout.splitlines()))
            revision = rows.get('refs/tags/v' + version + '^{}', rows.get('refs/tags/v' + version, ''))
            if not re.fullmatch('[a-f0-9]{40}', revision):
                raise ValueError('The published release has no exact source revision.')
            identity = uuid.uuid4().hex
            target = {'version': version, 'revision': revision,
                'source': artifacts.SOURCE + '/releases/download/v' + version + '/' + artifacts.filename(version)}
            view = {'supported': True, 'previewId': identity,
                'hostInstanceId': service.instance_id, 'hostLabel': __import__('socket').gethostname(),
                'dataDir': observed['home'], 'scope': 'application-installation',
                'target': target, 'installedExtras': observed['extras'],
                'preserves': PRESERVES, 'replaces': REPLACES, 'restart': 'managed-service',
                'expiresAt': time.time() + 600,
                'detail': 'Finish active work first. A fresh candidate is checked before stopping this host. Downloaded sources and unresolved outcomes are not reset.'}
            with _admission(service):
                _write(root(service) / ('preview-' + identity + '.json'),
                       {'preview': view, 'binding': observed}, service.data_dir)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            # Only validation messages originate here; network/installer details
            # never enter shared client state.
            view = {'supported': False, 'scope': 'application-installation',
                    'hostInstanceId': service.instance_id,
                    'reason': str(error) if isinstance(error, ValueError) else
                    'The installation repair target could not be verified. The running app is unchanged.'}
        async with service.lock:
            _publish(service, preview=view, operation=_latest(service), replace_preview=True)
        return view


def _launchd_plist(folder, plan):
    """Closed one-shot definition; no KeepAlive dictionary or external triggers."""
    return {
        'Label': 'com.microsoft.amplifier-unified.repair.' + plan['operationId'],
        'ProgramArguments': [plan['basePython'], '-I', '-S',
                             str(folder / 'installation_repair_worker.py'), str(folder)],
        'WorkingDirectory': str(folder),
        'RunAtLoad': True, 'KeepAlive': False, 'ProcessType': 'Background',
        'StandardOutPath': os.devnull, 'StandardErrorPath': os.devnull,
        'Umask': 0o077,
    }


def _prepare_launchd_job(folder, plan):
    """Prepare the exact private job BEFORE admission, never in LaunchAgents."""
    from .installation_repair_worker import _atomic
    path = folder / 'helper.plist'
    raw = plistlib.dumps(_launchd_plist(folder, plan), sort_keys=True)
    _atomic(path, raw, exclusive=True)
    return {
        **helper_job(plan['operationId']),
        'domain': 'gui/' + str(plan['uid']),
        'plistPath': str(path), 'plistSHA256': hashlib.sha256(raw).hexdigest(),
    }


def _checked_launchd_job(folder, plan):
    """A changed or missing definition never gets rewritten or dispatched."""
    from .installation_repair_worker import _bytes
    if (type(plan.get('uid')) is not int or plan['uid'] != os.getuid()
            or not isinstance(plan.get('operationId'), str)
            or not IDENTITY.fullmatch(plan['operationId'])
            or folder != Path(plan['home']) / 'installation-repair' / plan['operationId']):
        raise ValueError('The repair job owner or operation changed.')
    job = plan['helperJob']
    path = folder / 'helper.plist'
    expected = {**helper_job(plan['operationId']),
                'domain': 'gui/' + str(plan['uid']), 'plistPath': str(path)}
    if (not isinstance(job, dict) or set(job) != {*expected, 'plistSHA256'}
            or any(job[key] != value for key, value in expected.items())):
        raise ValueError('The repair job custody changed.')
    raw = _bytes(path, private=True, limit=16_384)
    if (hashlib.sha256(raw).hexdigest() != job['plistSHA256']
            or raw != plistlib.dumps(_launchd_plist(folder, plan), sort_keys=True)):
        raise ValueError('The one-shot repair definition changed.')
    return job


def launch(folder, plan):
    """Manager-owned job, not a child in the host service's kill group."""
    _check_base_python(plan)
    command = [plan['basePython'], '-I', '-S', str(folder / 'installation_repair_worker.py'), str(folder)]
    # Environment values (possibly index credentials) stay in private plan.json.
    # Pass only manager transport variables to the dispatch command itself.
    transport = {key: value for key, value in os.environ.items() if key in {
        'PATH', 'HOME', 'USER', 'LOGNAME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'}}
    if sys.platform == 'linux':
        from .reset import user_service_environment
        recovered = user_service_environment()
        for key in ('XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'):
            if key in recovered:
                transport[key] = recovered[key]
        argv = ['systemd-run', '--user', '--quiet', '--collect',
            '--unit=amplifier-installation-repair-' + plan['operationId'],
            '--property=Type=exec', '--property=Restart=no',
            '--property=StandardOutput=null', '--property=StandardError=null',
            '--property=WorkingDirectory=' + str(folder), '--', *command]
    elif sys.platform == 'darwin':
        # `launchctl submit` implies keepalive on failure. A private explicit
        # definition is necessary even though the worker itself refuses replay:
        # manager relaunches are not a one-shot dispatch or settled custody.
        job = _checked_launchd_job(folder, plan)
        argv = ['launchctl', 'bootstrap', job['domain'], job['plistPath']]
    else:
        raise ValueError('Installation repair is unsupported on this platform.')
    subprocess.run(argv, env=transport, cwd=folder, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=True, timeout=15)


def helper_job(operation_id):
    return ({'kind': 'systemd-user-service',
             'label': 'amplifier-installation-repair-' + operation_id,
             'cleanupState': 'manager-collects-after-exit',
             'cleanupOwner': 'user-service-manager'}
            if sys.platform == 'linux' else
            {'kind': 'launchd-user-job',
             'label': 'com.microsoft.amplifier-unified.repair.' + operation_id,
             'cleanupState': 'retained-for-explicit-review',
             'cleanupOwner': 'installation-owner',
             'detail': 'The one-shot registration may remain after exit. Remove only this exact settled job after reviewing its receipt; do not relaunch it.'})


def _check_base_python(plan):
    from .installation_repair_worker import executable_identity
    if executable_identity(plan['basePython']) != plan['executables']['basePython']:
        raise ValueError('The selected repair interpreter changed; nothing was dispatched.')


async def confirm(service, args):
    async with service._installation_repair_lock:
        identity = args['previewId']
        _folder(service, identity)
        if args.get('confirm') is not True:
            raise ValueError('Review and explicitly confirm the installation repair preview.')
        # Read prior admission BEFORE rejecting the old host binding: exact
        # retries after a disconnect return the operation, never dispatch twice.
        with _admission(service):
            previous = _latest(service)
            admissions = _admissions(service)
            if identity in admissions['previews']:
                operation_id = admissions['previews'][identity]
                return _operation(service, operation_id)
            if previous and previous.get('status') in ACTIVE:
                raise ValueError('An installation repair is unfinished or unknown. Inspect its receipt; do not repeat it.')
            saved = _read(root(service) / ('preview-' + identity + '.json'), service.data_dir)
        view, expected = saved['preview'], saved['binding']
        if (args['hostInstanceId'] != service.instance_id
                or view['hostInstanceId'] != service.instance_id or time.time() > view['expiresAt']):
            raise ValueError('The host or repair preview changed. Review a fresh preview.')
        current = await binding(service)
        if current != expected:
            raise ValueError('The installation, service, feature selection or launch target changed. Preview again.')
        operation_id = uuid.uuid4().hex
        folder = _folder(service, operation_id)
        folder.mkdir(mode=0o700)
        from . import app_release_artifacts as artifacts, app_updates
        from .app_feature_probe import DEPENDENCY_PROBE
        # Only exact official bytes, not unchecked Git/build input.
        artifact = await artifacts.prepare(folder, view['target']['version'], view['target']['revision'])
        if await binding(service) != expected:
            raise ValueError('The repair target changed during download. Preview again; the host is unchanged.')
        for name in ('installation_repair_worker.py', 'reset.py', 'reset_retention.py',
                     'app_release_artifacts.py'):
            source = Path(__file__).with_name(name)
            from .deployment import write_private
            write_private(folder / name, source.read_bytes())
        operation = {'operationId': operation_id, 'previewId': identity,
            'hostInstanceId': service.instance_id, 'status': 'accepted', 'stage': 'accepted',
            'effectBoundary': {'schema': 1, 'stopIntent': False, 'stopSettled': False, 'mutationIntent': False},
            'reinstalled': False, 'serviceState': 'not-checked', 'healthVerified': False,
            'detail': 'Repair accepted. A detached helper will qualify a fresh candidate before stopping this host.',
            'updatedAt': time.time(), 'helperJob': helper_job(operation_id)}
        plan = {**expected, 'operationId': operation_id, 'previewId': identity,
                'target': view['target'], 'artifact': artifact, 'probe': app_updates.PROBE,
                'dependencyProbe': DEPENDENCY_PROBE, 'recoverySchema': 1}
        if sys.platform == 'darwin':
            # Failure here is still preparation: no admission, local fence or
            # manager dispatch exists. Neither recovery nor retry rewrites it.
            operation['helperJob'] = plan['helperJob'] = _prepare_launchd_job(folder, plan)
        # The final no-await gate closes races across preparation/downloads.
        async with service.lock:
            require_idle(service)
            if time.time() > view['expiresAt']:
                raise ValueError('The repair preview expired. Review a fresh preview.')
            with _admission(service):
                if _latest(service) and _latest(service).get('status') in ACTIVE:
                    raise ValueError('Another repair owns the installation.')
                admissions = _admissions(service)
                # All inputs exist before the sole admission commit. Orphaned
                # preparation files neither fence this host nor authorize a
                # helper. Current/preview lookup become visible together.
                _write(folder / 'plan.json', plan, service.data_dir)
                _write(folder / 'operation.json', operation, service.data_dir)
                committed = {**admissions, 'current': operation_id,
                    'previews': {**admissions['previews'], identity: operation_id}}
                try:
                    _write(root(service) / 'admissions.json', committed, service.data_dir)
                except BaseException:
                    # Atomic replace may have landed before fsync/report failed.
                    # Inspect it; never dispatch from an uncertain commit.
                    try:
                        landed = _admissions(service) == committed
                    except (OSError, ValueError, KeyError, TypeError):
                        landed = True  # Unknown disk state is conservatively held.
                    if landed:
                        service._installation_repair_gate = operation_id
                        service._installation_repair_uncommitted = {
                            'operationId': operation_id, 'prior': admissions}
                    raise
                service._installation_repair_gate = operation_id
            # Projection failure cannot authorize another helper. Exact reads
            # still resolve through the committed admission, never redispatch.
            _publish(service, operation=operation)
        try:
            await asyncio.to_thread(launch, folder, plan)
        except BaseException as error:
            # A dispatch timeout/cancellation may follow a started manager job.
            # Preserve its one-shot admission; status inspects, never redispatches.
            async with service.lock:
                observed = _operation(service, operation_id)
                if observed.get('status') == 'accepted':
                    # Leave canonical receipt to its sole worker writer. This
                    # projection annotates dispatch uncertainty, not a second
                    # mutable operation authority.
                    observed = {**observed, 'status': 'unknown',
                        'detail': 'Helper dispatch could not be confirmed. Inspect this operation; do not confirm again.'}
                _publish(service, operation=observed)
            if not isinstance(error, Exception):
                raise
            return observed
        return operation


async def status(service, args):
    if args.get('operationId') and args.get('previewId'):
        raise ValueError('Choose an operation ID or a preview ID, not both.')
    def read():
        if args.get('previewId'):
            identity = args['previewId']
            _folder(service, identity)
            operation_id = _admissions(service)['previews'].get(identity)
            if not operation_id:
                return None
        else:
            operation_id = args.get('operationId')
        return (_operation(service, operation_id)
                if operation_id else _latest(service))
    operation = await asyncio.to_thread(read)
    # An exact historical read is a response, not the current installation
    # owner. In particular an old browser tab cannot relabel successor health.
    current = await asyncio.to_thread(_latest, service)
    async with service.lock:
        if current and current.get('operationId') == getattr(service, '_installation_repair_gate', None):
            if current.get('status') in RELEASED:
                service._installation_repair_gate = None
        _publish(service, operation=current)
    return {'operation': operation}


async def verify(service, args):
    """Deliberate bounded verification, not another installation admission."""
    async with service._installation_repair_lock:
        if args.get('confirm') is not True or args.get('hostInstanceId') != service.instance_id:
            raise ValueError('Review and confirm verification on this exact host.')
        identity = args['operationId']
        operation = _operation(service, identity)
        if (_latest(service).get('operationId') != identity
                or operation['status'] != 'reinstalled' or not operation['reinstalled']
                or operation['healthVerified']):
            raise ValueError('Only the exact current known reinstalled operation can be verified. Unknown repairs remain held.')
        folder = _folder(service, identity)
        plan = _read(folder / 'plan.json', service.data_dir)
        _check_base_python(plan)
        _private(folder / 'installation_repair_worker.py', service.data_dir)
        command = [plan['basePython'], '-I', '-S', str(folder / 'installation_repair_worker.py'),
                   '--verify', str(folder)]
        try:
            await asyncio.to_thread(subprocess.run, command, cwd=folder,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=240, check=True)
        except (OSError, subprocess.SubprocessError):
            # Verification is read-only until its exact result write. Read the
            # sole receipt; never convert a lost response into installation replay.
            pass
        result = await status(service, {'operationId': identity})
        return result['operation']


async def reconcile(service, args):
    """Explicit positive no-effect reconciliation; never starts/stops/installs."""
    async with service._installation_repair_lock:
        if args.get('confirm') is not True or args.get('hostInstanceId') != service.instance_id:
            raise ValueError('Review and confirm reconciliation on this exact host.')
        identity = args['operationId']
        operation = _operation(service, identity)
        if (_latest(service).get('operationId') != identity
                or _reconcile_target(service, operation) is None):
            raise ValueError('Only the exact current unfinished non-reinstalled repair can be inspected for no-effect proof.')
        folder = _folder(service, identity)
        plan = _read(folder / 'plan.json', service.data_dir)
        _check_base_python(plan)
        _private(folder / 'installation_repair_worker.py', service.data_dir)
        command = [plan['basePython'], '-I', '-S', str(folder / 'installation_repair_worker.py'),
                   '--reconcile', str(folder), service.instance_id]
        try:
            await asyncio.to_thread(subprocess.run, command, cwd=folder,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=45, check=True)
        except (OSError, subprocess.SubprocessError):
            pass  # Missing/unknown proof retains the canonical hold.
        result = await status(service, {'operationId': identity})
        return result['operation']