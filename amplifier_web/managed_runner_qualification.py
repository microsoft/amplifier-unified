"""Restartable qualification using the ordinary owned environment machinery.

Each external effect is journalled before dispatch. Re-entry observes the exact
owner/job; it never creates another guest or reruns an uncertain test. Capacity
uses the same backend and physical-host ledgers as user checks, independently
of ordinary environment-count quotas.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time

from . import task_environments as environments
from . import test_environment_setup as private
from . import test_environment_profiles as profiles
from . import test_host_resources as resources
from . import test_capacity_settings as capacity
from . import test_environment_recovery as recovery
from . import managed_runner_isolation as isolation

SENTINEL = 'amplifier-runner-qualified-v1'
COMMAND = (
    "python3 -c \"from pathlib import Path; "
    "assert Path('/workspace/input.txt').read_text() == 'runner-input-v1'; "
    "assert Path('/workspace/uncommitted.txt').read_text() == 'working-tree-v1'; "
    "Path('/artifacts/runner-proof.txt').write_text('" + SENTINEL + "'); "
    "print('" + SENTINEL + "')\""
)


def save(path, record, **changes):
    record.update(changes, updatedAt=time.time())
    if record.get('purpose') == 'runner-enrollment':
        from .test_origin_files import replace
        replace(path, record)
    else:
        private.write_json(path, record)


def invoke(config, app, row, action, args):
    qualification = not config.get('remoteRunner')
    return environments.invoke(capacity.apply(app, config, _qualification=qualification),
                               app.data_dir, row, action, args, _qualification=qualification)


def owner(record):
    return ('runner-enrollment-' if record.get('purpose') == 'runner-enrollment' else 'runner-update-') + record['id']


def source(folder):
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = folder / 'input.txt'
    if not (folder / '.git').exists():
        marker.write_text('runner-input-v1')
        environment = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(folder),
                       'USERPROFILE': str(folder), **{key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR') if key in os.environ}, 'GIT_CONFIG_NOSYSTEM': '1',
                       'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
        for command in (['init', '--quiet'], ['add', 'input.txt'],
                        ['-c', 'user.name=Runner qualification', '-c', 'user.email=runner@example.invalid',
                         'commit', '--quiet', '-m', 'Qualification input']):
            subprocess.run(['git', *command], cwd=folder, env=environment, check=True,
                           capture_output=True, timeout=20)
    (folder / 'uncommitted.txt').write_text('working-tree-v1')
    return str(folder)


def effect(app, path, record, row, config, action, admission=None):
    effects = record.setdefault('effects', {})
    if action in effects:
        if config.get('remoteRunner') and effects[action].get('result') is None and action != 'prepare':
            # Read the exact target dispatch receipt; this operation cannot
            # submit a missing effect or repeat a lost command.
            args = effects[action]['args']
            result = invoke(config, app, row, 'receipt', {**args, 'effectAction': action})
            if result.get('receiptState') == 'completed':
                value = result['result']
                if value.get('archives'):
                    from . import registered_test_runners
                    folder = environments.storage.directory(app.data_dir, row)
                    value['downloadedArchives'] = registered_test_runners.fetch_archives(config['remoteRunner'], folder,
                        {'requestId': args['requestId'], 'rootOwner': row['sessionId'], 'resourceId': row['id'],
                         'operationId': row['launchOperationId'], 'backendKey': row['backendKey'],
                         'snapshot': row['snapshot'], 'profileId': row['profileId'],
                         'resourceRequest': row['resourceRequest'], 'timingRequest': row['timingRequest']}, value['archives'])
                effects[action].update(state='returned', result=value)
                save(path, record)
                return value
            return None
        # Snapshot preparation has no external effect and writes only this
        # updater-owned fixed source archive. Its lost response is retryable.
        if action != 'prepare' or effects[action].get('result') is not None:
            return effects[action].get('result')
    if admission and action in {'prepare', 'start', 'job_start'}:
        admission()
    args = {'requestId': record['id'] + '-' + action, 'expectedRevision': row['revision'],
            'jobId': record['id'] + '-check', 'command': COMMAND, 'timeoutSeconds': record.get('checkTimeoutSeconds', 30),
            'retentionMode': 'files'}
    if action == 'start' and record.get('startAttempt'):
        args['requestId'] += '-' + str(record['startAttempt'])
    effects[action] = {'state': 'dispatching', 'requestId': args['requestId'], 'args': args}
    save(path, record)
    try:
        result = invoke(config, app, row, action, args)
        if config.get('remoteRunner') and result.get('archives') and result.get('evidenceTransfer'):
            # Exact target export succeeded, but its download is unsettled.
            # Recover by reading that receipt and its archive, not removing again.
            effects[action].update(state='unknown', errorType='ArtifactTransferIncomplete')
            save(path, record)
            return None
        effects[action].update(state='returned', result=result)
        save(path, record)
        return result
    except Exception as error:
        # The retained dispatch is the authority after lost acknowledgements.
        effects[action].update(state='unknown', errorType=type(error).__name__)
        save(path, record)
        return None


def observe(app, row, config):
    value = invoke(config, app, row, 'reconcile', {'requestId': row['commandId'] + '-observe'})
    return value if value.get('ok') and recovery.verify(row, value) else None


def verify_artifact(result, *, remote=False):
    # Removal's existing bounded exporter has already verified ownership and
    # archive safety. Independently prove the test's exact artifact survived.
    for item in result.get('downloadedArchives', []) + ([] if remote else result.get('archives', [])):
        destination = item.get('destination')
        if not destination:
            continue
        path = Path(destination)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != item.get('sha256'):
            continue
        with tarfile.open(path) as archive:
            for member in archive:
                if (member.name.removeprefix('./').removeprefix('/') in
                        {'artifacts/runner-proof.txt', 'runner-proof.txt'}
                        and member.isfile() and member.size == len(SENTINEL)):
                    handle = archive.extractfile(member)
                    if handle and handle.read(1000).decode() == SENTINEL:
                        return True
    return False



def cancel(app, path, record, config):
    """Withdraw future effects, reconcile uncertain submission, clean only its owner."""
    save(path, record, selectionChanged=True, checkPassed=False)
    if record.get('probeState') in {'qualified', 'failed'} or not record.get('resourceId'):
        save(path, record, probeState='failed')
        return 'failed'
    row = environments.get(app.data_dir, owner(record), record['resourceId'])
    if 'start' not in record.get('effects', {}):
        resources.settle(row, release=True)
        environments.save(app.data_dir, row, status='retired', environmentState='not-started')
        save(path, record, probeState='failed')
        return 'failed'
    if record.get('probeState') not in {'stop', 'remove'}:
        observed = observe(app, row, config)
        if not observed:
            return 'unknown'
        if observed['status'] == 'absent':
            resources.settle(row, release=True)
            environments.save(app.data_dir, row, status='retired', environmentState='removed')
            save(path, record, probeState='failed')
            return 'failed'
        if observed['status'] not in {'running', 'stopped'}:
            return 'unknown'
        save(path, record, probeState='remove' if observed['status'] == 'stopped' else 'stop')
    return advance(app, path, record, config)


def advance(app, path, record, config, *, admission=None):
    """Advance a bounded step; return waiting/qualified/failed/unknown."""
    state = record.get('probeState', 'new')
    if state in {'qualified', 'failed'}:
        return state
    if not record.get('resourceId'):
        if admission:
            admission()
        origin = source(path.parent / (record['id'] + '-source'))
        row = environments.prepare(
            app.data_dir, owner(record), 'runner-qualification',
            {'sessionId': owner(record), 'principal': 'host-updater' if not config.get('remoteRunner') else 'host-runner-enrollment'},
            origin, environments.config_key(config), record['id'], config.get('installationId'), config,
            resource_request=record.get('resourceRequest') or profiles.freeze('linux-python', {'cpus': 1, 'memoryMiB': 512}),
            timing_request=record.get('timingRequest') or profiles.freeze_timing('linux-python'), _qualification=not config.get('remoteRunner'))
        save(path, record, resourceId=row['id'], probeState='prepare')
    row = environments.get(app.data_dir, owner(record), record['resourceId'])
    if row['backendKey'] != environments.config_key(config):
        raise ValueError('The original qualification backend changed.')
    state = record['probeState']
    if state == 'prepare':
        if row['status'] == 'prepared':
            row = environments.storage.allocate(app.data_dir, row)
        if row['status'] != 'ready' or environments.storage.observe(app.data_dir, row)['observation'] != 'present':
            return 'unknown'
        result = effect(app, path, record, row, config, 'prepare', admission)
        if result is None:
            # Re-entry revalidates its exact retained daemon/source binding.
            return 'unknown'
        if not result.get('ok') or not result.get('snapshot') or not result.get('backendIdentity'):
            save(path, record, probeState='failed')
            environments.save(app.data_dir, row, status='retired', environmentState='not-started')
            return 'failed'
        row = environments.save(app.data_dir, row, snapshot=result['snapshot'],
                                universeId=result['universeId'], backendIdentity=result['backendIdentity'])
        save(path, record, probeState='capacity', hostTarget=result.get('hostTarget'))
        return 'waiting'
    if state == 'capacity':
        observed = environments.capacity(app, config, _qualification=not config.get('remoteRunner'))
        if not observed.get('available'):
            return 'waiting'
        if config.get('remoteRunner'):
            # The fixed target controller atomically reserves its own machine
            # and backend budgets at start. No origin reservation duplicates it.
            save(path, record, probeState='start')
            return 'waiting'
        target = record.get('hostTarget') or {}
        if target.get('supported') is not True:
            raise ValueError('This runner cannot prove its physical machine for qualification.')
        reservation = resources.reserve(row, target)
        if not reservation.get('admitted'):
            return 'waiting'
        row = environments.save(app.data_dir, row, hostReservation=reservation, backendAdmitted=True)
        save(path, record, probeState='start')
        return 'waiting'
    if state == 'start':
        result = effect(app, path, record, row, config, 'start', admission)
        if result and result.get('admissionDenied'):
            # Definite refusal: no launch was dispatched, and capacity can be
            # tried again under the same owned intent.
            resources.settle(row, release=True)
            record['effects'].pop('start')
            if config.get('remoteRunner'):
                record['startAttempt'] = record.get('startAttempt', 0) + 1
            environments.save(app.data_dir, row, backendAdmitted=False)
            save(path, record, probeState='capacity')
            return 'waiting'
        if not result or not result.get('ok') or not result.get('sourceReady'):
            observed = observe(app, row, config)
            if not observed:
                return 'unknown'
            if observed['status'] == 'absent':
                resources.settle(row, release=True)
                environments.save(app.data_dir, row, status='retired', environmentState='removed')
                save(path, record, probeState='failed')
                return 'failed'
            read = invoke(config, app, row, 'read', {})
            if observed['status'] != 'running' or not read.get('sourceReady'):
                save(path, record, probeState='stop', checkPassed=False)
                return 'waiting'
        environments.save(app.data_dir, row, sourceReady=True, environmentState='running')
        resources.settle(row, phase='running')
        save(path, record, probeState='check')
        return 'waiting'
    if state == 'check':
        if not record.get('isolation'):
            try:
                proof = isolation.inspect(config, app.data_dir, row)
            except (OSError, ValueError, RuntimeError) as error:
                save(path, record, checkPassed=False, probeState='stop',
                     isolationFailure={'errorType': type(error).__name__, 'detail': str(error)[:1024]})
                return 'waiting'
            save(path, record, isolation=proof)
        result = effect(app, path, record, row, config, 'job_start', admission)
        if result is None or not (result.get('job') or {}).get('terminal'):
            result = invoke(config, app, row, 'job_read',
                            {'jobId': record['id'] + '-check', 'command': COMMAND, 'timeoutSeconds': record.get('checkTimeoutSeconds', 30)})
        job = result.get('job') or {}
        if job.get('terminal') is not True:
            return 'unknown' if job.get('state') == 'unknown' else 'waiting'
        passed = (job.get('state') == 'passed' and result.get('result', {}).get('exit_code') == 0
                  and SENTINEL in result.get('result', {}).get('stdout', ''))
        save(path, record, checkPassed=passed, check=result, probeState='stop')
        return 'waiting'
    if state == 'stop':
        result = effect(app, path, record, row, config, 'stop')
        if not result or result.get('status') != 'stopped' or not result.get('ok'):
            observed = observe(app, row, config)
            if not observed or observed.get('status') != 'stopped':
                return 'unknown'
        environments.save(app.data_dir, row, environmentState='stopped')
        resources.settle(row, release=True)
        save(path, record, probeState='remove')
        return 'waiting'
    if state == 'remove':
        result = effect(app, path, record, row, config, 'remove')
        if result is None and not config.get('remoteRunner'):
            observed = observe(app, row, config)
            if observed and observed.get('status') == 'absent':
                folder = environments.storage.directory(app.data_dir, row)
                request = record['effects']['remove']['requestId']
                retained = folder / ('retained-' + hashlib.sha256(request.encode()).hexdigest())
                manifest = private.read_json(retained / 'manifest.json')
                if isinstance(manifest, list) and all(
                        isinstance(item, dict) and Path(item.get('destination', '')).parent == retained
                        and item.get('id') == row['universeId'] for item in manifest):
                    result = {'ok': True, 'status': 'removed', 'archives': manifest}
        if not result or not result.get('ok') or result.get('status') != 'removed':
            # Exact continuation of the existing conservative retention helper
            # can resume a returned failed export. Unknown teardown is observed,
            # never replayed by creating a fresh resource or removal request.
            if result and result.get('effectAdmitted') is False:
                result = effect(app, path, record, row, config, 'finishCleanup')
            if not result or not result.get('ok') or result.get('status') != 'removed':
                return 'unknown'
        observed = observe(app, row, config)
        if not observed or observed.get('status') != 'absent':
            return 'unknown'
        retained = verify_artifact(result, remote=bool(config.get('remoteRunner')))
        environments.save(app.data_dir, row, status='retired', environmentState='removed',
                          retainedFiles=result.get('retainedFiles'), qualificationProof=record['id'])
        resources.settle(row, release=True)
        state = 'qualified' if record.get('checkPassed') and retained else 'failed'
        save(path, record, probeState=state, proof={
            'version': 1, 'backendIdentity': row['backendIdentity'], 'snapshot': row['snapshot'],
            'operationId': row['launchOperationId'], 'resourceId': row['id'],
            'jobId': record['id'] + '-check', 'probeRemoved': True,
            'artifactVerified': retained, 'checkPassed': bool(record.get('checkPassed')), 'isolation': record.get('isolation')})
        return state
    raise ValueError('The retained qualification step is unsupported.')
