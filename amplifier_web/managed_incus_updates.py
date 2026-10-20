"""Update managed Incus engines on their actual configured test machines."""
from __future__ import annotations
import asyncio
import base64
import json
import os
import socket
from pathlib import Path
import subprocess
import sys

from . import registered_test_runners as runners
from . import test_environment_setup as private
from . import incus_engine_probe as probe
from .managed_runner_updates import RunnerUpdateError, supported_private_files

KIND = 'managed-incus-engine'


def raw(app):
    return private.read_json(Path(app.data_dir) / 'test-environments.json') or {}


def authored(value):
    return {key: item for key, item in value.items() if key not in {'interpreter', 'packageDirectory'}}


def selected(app, target):
    if target == 'local':
        value = raw(app)
        return {'incus': authored(value['incus']), 'enabled': True} if value.get('enabled') and value.get('backend') == 'incus' else None
    return next((row for row in runners.store(app)['runners'] if row['id'] == target and row.get('enabled')), None)


def folder(app):
    if not supported_private_files():
        raise RunnerUpdateError('managed-runner-platform-unsupported')
    path = Path(app.data_dir) / 'managed-incus-updates'
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RunnerUpdateError()
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    from .incus_environment_worker import private_directory
    private_directory(path)
    return path


def path_for(app, identity):
    import re
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
        raise RunnerUpdateError()
    return folder(app) / (identity + '.json')


def update_sources(app):
    targets = []
    if not supported_private_files():
        # Inventory is passive on every host; target-native POSIX helpers are
        # never imported to discover whether a runner has been configured.
        configured = (Path(app.data_dir) / 'test-environments.json').exists()
        remote = [row for row in runners.store(app)['runners'] if row.get('enabled')]
        if not configured and not remote:
            return []
        return [{'id': 'managed-runner:incus:unsupported', 'kind': 'smart tool', 'runnerKind': KIND,
                 'label': 'Managed Incus engine', 'updateTier': 'other', 'url': probe.REPOSITORY,
                 'ref': '', 'current': None, 'eligible': False, 'status': 'not_checked', 'usage': 'configured',
                 'detail': 'Automatic Incus engine updates are not supported from this platform. Existing test machines remain configured; their versions have not been checked.'}]
    current = selected(app, 'local')
    if current:
        targets.append(('local', current))
    targets.extend((row['id'], row) for row in runners.store(app)['runners'] if row.get('enabled'))
    # This scopes idempotency, not authorization. The retained target receipt
    # separately verifies the exact account, config path, daemon and owner.
    data = Path(app.data_dir).resolve()
    stat = data.stat()
    origin = runners.digest([socket.gethostname(), str(data), stat.st_dev, stat.st_ino])
    return [{'id': 'managed-runner:incus:' + identity, 'installationId': 'incus-engine-' + runners.digest(identity)[:24],
             'kind': 'smart tool', 'runnerKind': KIND, 'label': 'Managed Incus engine' + (' on another test machine' if identity != 'local' else ''),
             'updateTier': 'other', 'url': probe.REPOSITORY, 'ref': 'main', 'current': None,
             'eligible': True, 'status': 'not_checked', 'usage': 'configured',
             'usageEvidence': ['The engine that runs isolated tests on this configured machine'],
             'runnerTarget': identity, 'runnerSelection': runners.digest(value),
             'runnerOrigin': origin, 'runnerConfigPath': str(data / 'test-environments.json') if identity == 'local' else value.get('configPath', ''),
             'detail': 'Its installed source is verified on the configured test machine before an update is offered.'}
            for identity, value in targets]


def inspect(app, target, value):
    payload = {'configPath': str(Path(app.data_dir) / 'test-environments.json') if target == 'local' else value.get('configPath', ''),
               'expectedIncus': authored(value['incus'])}
    if target == 'local':
        from . import incus_environment_worker as worker
        from . import test_runner_controller as controller
        observed = probe.inspect(payload)
        identity, _ = controller.backend(worker.validate_configuration(observed['configuration']), Path(app.data_dir))
        observed.update(backendIdentity=identity, machineId=None)
    else:
        # Existing retained controller proves native target/daemon/account. No
        # installation or refresh occurs during an ordinary availability check.
        result = runners.call(value, {'action': 'capacity'})
        if not result.get('ok'):
            raise RunnerUpdateError()
        observed = runners.ssh(value['connection'], ['/usr/bin/python3', '-I', '-B', '-c',
            Path(probe.__file__).read_text()], payload)
        observed.update(backendIdentity=value['backendIdentity'], machineId=value['machineId'])
    return observed


async def check_source(app, row):
    from .updates import pinned
    if not supported_private_files():
        return
    current = selected(app, row['runnerTarget'])
    if current is None or runners.digest(current) != row['runnerSelection']:
        raise RunnerUpdateError('managed-runner-selection-changed')
    observed = await private.settled_thread(inspect, app, row['runnerTarget'], current)
    policy = observed.get('policy') or {}
    eligible = (policy.get('version') == 1 and policy.get('repository') == probe.REPOSITORY
                and isinstance(policy.get('ref'), str) and bool(policy['ref']) and not pinned(policy['ref']))
    row.update(current=observed['revision'], ref=policy.get('ref', ''), eligible=eligible,
        runnerObserved=observed, runnerPolicy=policy,
        status='not_checked' if eligible else 'pinned' if policy else 'local',
        detail='New engine versions are qualified on this actual test machine before future tests use them.' if eligible
               else 'This engine has an explicit or unrecorded source policy; it is preserved.')


def intent_id(source):
    return runners.digest([source['runnerOrigin'], source['runnerConfigPath'], source['runnerTarget'],
                           source['runnerSelection'], source['latest']])[:32]


def install_controller(app, target, selected_value):
    payload = {**runners.bundle(), 'configPath': str(Path(app.data_dir) / 'test-environments.json') if target == 'local' else selected_value.get('configPath', '')}
    if target == 'local':
        result = subprocess.run([sys.executable, '-I', '-B', '-c', runners.INSTALL], input=json.dumps(payload).encode(),
                                capture_output=True, timeout=90)
        if result.returncode or len(result.stdout) > 4096:
            raise RunnerUpdateError()
        installed = json.loads(result.stdout)
    else:
        installed = runners.ssh(selected_value['connection'], ['/usr/bin/python3', '-I', '-B', '-c', runners.INSTALL], payload, timeout=90)
    return {'controller': installed['controller'], 'packageDigest': payload['digest'],
            'packageFiles': ['__init__.py', *runners.FILES],
            **({'connection': selected_value['connection']} if target != 'local' else {})}


def target_call(target, request, timeout=90):
    script = str(Path(target['controller']).with_name('managed_incus_target.py'))
    arguments = ['-I', '-B', '-c', runners.VERIFY, script, target['packageDigest'], json.dumps(target['packageFiles'])]
    if target.get('connection'):
        return runners.ssh(target['connection'], ['/usr/bin/python3', *arguments], request, timeout)
    result = subprocess.run([sys.executable, *arguments], input=json.dumps(request).encode(),
                            capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise RunnerUpdateError()
    return json.loads(result.stdout)


def source_snapshot(app, path):
    from . import managed_runner_qualification as qualification
    from . import task_environments as environments
    config, status, _ = environments.configuration(app)
    if status != 'ready' or not config:
        raise RunnerUpdateError()
    directory = path.parent / (path.stem + '-source')
    source = qualification.source(directory)
    snapshot = runners.export_source(config, directory, source)
    content = (directory / 'source.tar').read_bytes()
    if len(content) > runners.CHUNK:
        raise RunnerUpdateError()
    return snapshot, base64.b64encode(content).decode()


async def stage(app, source):
    identity = intent_id(source)
    path = path_for(app, identity)
    current = selected(app, source['runnerTarget'])
    record = private.read_json(path)
    if current is None or runners.digest(current) != source['runnerSelection']:
        if not record or record.get('state') != 'selected':
            raise RunnerUpdateError('managed-runner-selection-changed')
    if record is None:
        verified = await private.settled_thread(inspect, app, source['runnerTarget'], current)
        if verified != source.get('runnerObserved'):
            raise RunnerUpdateError('managed-runner-selection-changed')
        snapshot, content = await private.settled_thread(source_snapshot, app, path)
        target = await private.settled_thread(install_controller, app, source['runnerTarget'], current)
        request = {'action': 'stage', 'id': identity, 'configPath': str(Path(app.data_dir) / 'test-environments.json')
                   if source['runnerTarget'] == 'local' else current.get('configPath', ''),
                   'expectedIncus': verified['configuration'], 'policy': verified['policy'],
                   'current': source['current'], 'revision': source['latest'],
                   'machineId': verified['machineId'], 'backendIdentity': verified['backendIdentity'],
                   'snapshot': snapshot, 'source': content}
        record = {'version': 1, 'id': identity, 'source': source, 'previous': current,
                  'target': target, 'request': request, 'state': 'staging'}
        private.write_json(path, record)
    elif record.get('source') != source:
        raise RunnerUpdateError('managed-runner-selection-changed')
    if record.get('state') == 'staging':
        result = await private.settled_thread(target_call, record['target'], record['request'], 1300)
        if result.get('id') != identity or result.get('state') != 'qualifying':
            raise RunnerUpdateError()
        record['state'] = 'qualifying'
        private.write_json(path, record)
    return {'id': 'incus-engine-' + identity, 'runnerUpdate': identity}


async def advance(app, identity):
    path = path_for(app, identity)
    record = private.read_json(path)
    if not record or record.get('state') not in {'qualifying', 'selected'}:
        raise RunnerUpdateError()
    source = record['source']; current = selected(app, source['runnerTarget'])
    if record.get('state') == 'selected':
        # This receipt describes a completed future-selection commit. A later
        # operator pause/change is not permission to reselect the old candidate.
        return {'complete': True}
    observed = await private.settled_thread(target_call, record['target'], {'action': 'read', 'id': identity}, 90)
    if observed.get('complete') and observed.get('incus'):
        record['candidateIncus'] = observed['incus']
        private.write_json(path, record)
    changed = current != record['previous'] and current != record.get('candidateRunner')
    if source['runnerTarget'] == 'local' and current:
        changed = current['incus'] not in (record['previous']['incus'], record.get('candidateIncus'))
    if changed:
        result = await private.settled_thread(target_call, record['target'], {'action': 'cancel', 'id': identity}, 900)
        if result.get('complete'):
            raise RunnerUpdateError('managed-runner-selection-changed')
        return result
    result = observed if observed.get('complete') else await private.settled_thread(target_call, record['target'], {'action': 'advance', 'id': identity}, 900)
    if not result.get('complete'):
        return result
    if result.get('cancelled'):
        raise RunnerUpdateError('managed-runner-selection-changed')
    record['candidateIncus'] = result['incus']
    private.write_json(path, record)
    if source['runnerTarget'] != 'local':
        # Registration replaces only future routing. Retained job descriptors
        # keep the original controller, executable and backend configuration.
        candidate = await private.settled_thread(runners.register, record['previous']['connection'],
            record['previous']['name'], record['previous'].get('configPath', ''))
        if (candidate['id'] != record['previous']['id']
                or authored(candidate['incus']) != authored(result['incus'])):
            raise RunnerUpdateError('managed-runner-selection-changed')
        record['candidateRunner'] = candidate
        private.write_json(path, record)
        async with app.lock:
            saved = runners.store(app)
            existing = next((row for row in saved['runners'] if row['id'] == candidate['id']), None)
            if existing not in (record['previous'], candidate):
                raise RunnerUpdateError('managed-runner-selection-changed')
            saved['runners'] = [candidate if row['id'] == candidate['id'] else row for row in saved['runners']]
            runners.publish(app, saved)
    record['state'] = 'selected'
    private.write_json(path, record)
    app._test_setup_readiness = None
    return {'complete': True}


async def skip(app, identity):
    path = path_for(app, identity)
    record = private.read_json(path)
    if record and record.get('state') != 'selected':
        await private.settled_thread(target_call, record['target'], {'action': 'skip', 'id': identity}, 90)
        record['state'] = 'skipped'
        private.write_json(path, record)
