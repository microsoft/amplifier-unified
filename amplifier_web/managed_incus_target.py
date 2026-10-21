"""Target-native engine installation and one durable offline qualification.

Only the fixed host updater calls this controller. It never configures an Incus
daemon, project, network or image, and never redirects an existing test owner.
"""
from __future__ import annotations
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from amplifier_web import incus_engine_generations as generations
from amplifier_web import incus_engine_probe as probe
from amplifier_web import test_runner_controller as controller
from amplifier_web import incus_environment_worker as worker
from amplifier_web import test_environment_profiles as profiles
from amplifier_web import incus_engine_core_binding as core_binding

SENTINEL = 'amplifier-incus-engine-qualified-v1'
COMMAND = "python3 -c \"from pathlib import Path; assert Path('/workspace/input.txt').read_text() == 'runner-input-v1'; assert Path('/workspace/uncommitted.txt').read_text() == 'working-tree-v1'; Path('/artifacts/runner-proof.txt').write_text('" + SENTINEL + "'); print('" + SENTINEL + "')\""


class IncompleteInstallation(ValueError):
    pass


def root():
    path = generations.root()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Engine update storage changed.')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    worker.private_directory(path)
    return path


def folder(identity):
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
        raise ValueError('An exact engine update identity is required.')
    path = root() / identity
    path.mkdir(mode=0o700, exist_ok=True)
    worker.private_directory(path)
    return path


def save(path, record, **changes):
    record.update(changes, updatedAt=time.time())
    controller.write(path / 'update.json', record)


def selected(record):
    raw = probe.read(record['configPath'])
    return raw if raw and raw.get('enabled') is True and raw.get('backend') == 'incus' else None


@contextmanager
def settings_lock(path):
    """Use the same flock and filename as normal setup and core publication."""
    directory = path.parent / 'test-runner-setup'
    directory.mkdir(mode=0o700, exist_ok=True)
    worker.private_directory(directory)
    fd = os.open(directory / 'setup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022 or info.st_nlink != 1):
            raise ValueError('The setup lock changed.')
        # FileLock, used by normal setup/core publication, creates 0644 locks.
        # This parent is private 0700; readability is not write authority.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def policy_matches(record):
    return probe.policy(Path(record['configPath']), record['previous'], record['intent']['current']) == record['policy']


def package(python):
    script = ('import importlib.util,importlib.metadata,json; p=importlib.metadata.distribution("amplifier-bundle-digital-twin-universe"); '
        'print(json.dumps({"directory":next(iter(importlib.util.find_spec("amplifier_bundle_digital_twin_universe").submodule_search_locations)), '
        '"version":p.version,"source":json.loads(p.read_text("direct_url.json") or "{}")}))')
    try:
        result = subprocess.run([str(python), '-I', '-B', '-c', script], capture_output=True, timeout=30,
                                env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
    except FileNotFoundError as error:
        raise IncompleteInstallation('The private engine installation is incomplete.') from error
    if result.returncode or len(result.stdout) > 65536:
        raise IncompleteInstallation('The candidate engine installation is incomplete.')
    return json.loads(result.stdout)


def installed_candidate(path, record):
    runtime_path = path / record.get('runtimeDirectory', 'runtime')
    python = runtime_path / 'bin/python'
    info = package(python)
    direct = info['source']; vcs = direct.get('vcs_info') or {}
    if (direct.get('url', '').removesuffix('.git') != generations.REPOSITORY
            or vcs.get('vcs') != 'git' or vcs.get('commit_id') != record['revision']):
        raise ValueError('The candidate engine source did not match the checked revision.')
    launcher = runtime_path / 'bin/amplifier-digital-twin'
    candidate = {**record['previous'], 'executable': str(launcher),
                 'executableSha256': worker.file_digest(launcher, 65536)[0],
                 'packageSha256': worker.package_digest(info['directory'])}
    candidate.pop('interpreter', None); candidate.pop('packageDirectory', None)
    runtime = generations.runtime_identity(python)
    identity = {'version': 1, 'repository': generations.REPOSITORY, 'revision': record['revision'],
        'configuration': generations.binding(candidate), 'owner': record['owner'], 'python': str(python),
        'runtime': runtime, 'backendIdentity': record['backendIdentity'], 'machineId': record['machineId'],
        'snapshot': record['snapshot']}
    existing = controller.read(path / 'identity.json')
    if existing is not None and existing != identity:
        raise ValueError('The retained candidate engine changed.')
    if existing is None:
        controller.write(path / 'identity.json', identity)
    candidate['engineQualification'] = {'version': 1, 'receipt': str(path / 'identity.json'),
                                       'sha256': generations.digest(identity)}
    worker.validate_configuration(candidate, _engine_owner=record['owner'])
    save(path, record, candidate=candidate, state='qualifying', phase='prepare')


def discard_private_runtime(path, record):
    """Remove only an unpublished, settled install; keep its evidence files."""
    if not record.get('installFinished') or record.get('state') == 'selected' or selected_result(path, record):
        return False
    candidate = record.get('candidate')
    if candidate and (root() / ('selection-' + generations.digest(candidate) + '.json')).exists():
        # Written before the config CAS: even a lost acknowledgement or later
        # rollback cannot prove this generation was never used by another job.
        return False
    name = record.get('runtimeDirectory', 'runtime')
    if not re.fullmatch(r'runtime(?:-[1-9][0-9]*)?', name):
        raise ValueError('The private runtime owner changed.')
    runtime = path / name
    if runtime.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('The private runtime path changed.')
    # rmtree unlinks internal symlinks; it never follows them outside this
    # installation. This directory contains installed code, not guest exports.
    if runtime.exists():
        shutil.rmtree(runtime)
    save(path, record, runtimeCleanupComplete=True)
    return True


def stage(request):
    required = {'action', 'id', 'configPath', 'expectedIncus', 'policy', 'current', 'revision',
                'machineId', 'backendIdentity', 'snapshot', 'source'}
    if set(request) != required or not re.fullmatch('[a-f0-9]{40}', request.get('revision', '')):
        raise ValueError('The exact checked engine candidate is required.')
    path = folder(request['id'])
    with controller.locked(path):
        record = controller.read(path / 'update.json')
        intent = {key: value for key, value in request.items() if key != 'action'}
        if record is not None:
            if record['intent'] != intent:
                raise ValueError('The original engine update arguments changed.')
            if record.get('candidate'):
                return {'id': record['id'], 'state': record['state']}
            # Recognize a completed exact install after a lost reply first.
            # An incomplete private install can use a NEW directory: retained
            # files and any late installer from the old attempt cannot alter it.
            try:
                installed_candidate(path, record)
            except IncompleteInstallation:
                if (path / 'identity.json').exists() or record.get('phase'):
                    raise ValueError('The frozen engine identity needs inspection before recovery.')
                attempt = record.get('installAttempt', 1) + 1
                if record.get('state') == 'install_failed':
                    discard_private_runtime(path, record)
                save(path, record, state='installing', installAttempt=attempt, runtimeDirectory='runtime-' + str(attempt),
                     installFinished=False, runtimeCleanupComplete=False)
            else:
                return {'id': record['id'], 'state': record['state']}
        observed = probe.inspect({'configPath': request['configPath'], 'expectedIncus': request['expectedIncus']})
        policy = observed['policy'] or {}
        if (observed['revision'] != request['current'] or policy != request['policy']
                or policy.get('version') != 1 or policy.get('repository') != generations.REPOSITORY
                or not isinstance(policy.get('ref'), str) or not policy['ref']
                or re.fullmatch(r'[a-f0-9]{7,40}|v?\d+(?:\.\d+)*', policy['ref'])):
            raise ValueError('The selected engine update policy changed.')
        config = worker.validate_configuration(observed['configuration'])
        base = controller.state()
        identity, _ = controller.backend(config, base)
        authority = controller.authority_id(base, create=True)
        if request['backendIdentity'] != identity or request['machineId'] not in (None, authority):
            raise ValueError('The actual test machine changed.')
        source = base64.b64decode(request['source'], validate=True)
        if (len(source) > controller.CHUNK or hashlib.sha256(source).hexdigest() != request['snapshot'].get('archive_sha256')):
            raise ValueError('The fixed qualification source changed.')
        if record is None:
            record = {'version': 1, 'id': request['id'], 'owner': 'engine-update-' + request['id'], 'intent': intent,
                'configPath': str(probe.configuration_path(request['configPath'])), 'previous': observed['configuration'],
                'policy': policy, 'revision': request['revision'], 'backendIdentity': identity, 'machineId': authority,
                'snapshot': request['snapshot'], 'source': request['source'], 'state': 'installing', 'createdAt': time.time()}
        save(path, record)
        uv = shutil.which('uv')
        if not uv:
            raise ValueError('The managed package installer is unavailable on this test machine.')
        environment = {**os.environ, 'UV_LINK_MODE': 'copy', 'UV_PYTHON_DOWNLOADS': 'never', 'PYTHONDONTWRITEBYTECODE': '1'}
        runtime = path / record.get('runtimeDirectory', 'runtime')
        python = runtime / 'bin/python'
        # The durable installing receipt precedes both fixed commands. An
        # interrupted candidate is inspected, not installed again on retry.
        for command in ([uv, 'venv', '--python', str(probe.interpreter(config)), str(runtime)],
                        [uv, 'pip', 'install', '--link-mode', 'copy', '--python', str(python),
                         'git+' + generations.REPOSITORY + '@' + record['revision']]):
            try:
                subprocess.run(command, check=True, capture_output=True, timeout=600, env=environment)
            except (subprocess.SubprocessError, OSError) as error:
                save(path, record, state='install_failed', installFinished=isinstance(error, subprocess.CalledProcessError))
                raise
        save(path, record, installFinished=True)
        installed_candidate(path, record)
        return {'id': record['id'], 'state': record['state']}


def payload(record, action):
    return {'action': action, 'rootOwner': record['owner'], 'resourceId': record['id'],
        'operationId': record['id'], 'backendKey': generations.digest(record['candidate']),
        'expectedBackendIdentity': record['backendIdentity'],
        'snapshot': record['snapshot'], 'profileId': 'linux-python',
        'resourceRequest': profiles.freeze('linux-python', {'cpus': 1, 'memoryMiB': 512}),
        'timingRequest': profiles.freeze_timing('linux-python'), 'requestId': record['id'] + '-' + action +
            (('-' + str(record['startAttempt'])) if action == 'start' and record.get('startAttempt') else ''),
        **({'universeId': record['universeId']} if record.get('universeId') else {})}


def call(record, action, **arguments):
    request = {'version': 1, 'machineId': record['machineId'], 'backendIdentity': record['backendIdentity'],
               'payload': {**payload(record, action), **arguments}}
    return controller.dispatch(request, _configuration=record['candidate'], _engine_owner=record['owner'])


def retained_proof(result):
    for item in result.get('archives', []):
        path = Path(item.get('destination', ''))
        if (not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents))
                or not path.is_file() or path.stat().st_size > 64 * 1024**2
                or worker.file_digest(path, 64 * 1024**2)[0] != item.get('sha256')):
            continue
        with tarfile.open(path) as archive:
            for member in archive:
                if (member.name.removeprefix('./').removeprefix('/') in {'artifacts/runner-proof.txt', 'runner-proof.txt'}
                        and member.isfile() and member.size == len(SENTINEL)):
                    content = archive.extractfile(member)
                    if content and content.read(1000).decode() == SENTINEL:
                        return True
    return False


def observe(record):
    result = call(record, 'reconcile')
    observation = result.get('observation') or {}
    expected = worker.common.binding(payload(record, 'reconcile'), record['universeId'], record['backendIdentity'])
    if (not result.get('ok') or any(observation.get(key) != value for key, value in expected.items())
            or observation.get('admissionSettled') is not True
            or observation.get('ownership') not in {'matched', 'absent'}
            or result.get('status') == 'absent' and observation.get('absenceConfirmed') is not True):
        return None
    if result.get('status') == 'absent':
        # Capacity follows physical settlement, independently of whether the
        # removed guest left enough artifact evidence to qualify an update.
        directory = owner_directory(record)
        with controller.locked(directory):
            reservation = controller.read(directory / 'reservation.json')
            if reservation:
                row = {**controller.row_for(payload(record, 'read')), 'hostReservation': reservation}
                controller.machine.settle(row, release=True)
    return result


def owner_directory(record):
    return controller.state() / ('owner-' + controller.digest(controller.owner(payload(record, 'read'))))


def removed_result(record, action):
    observed = observe(record)
    if not observed or observed.get('status') != 'absent':
        return None
    directory = owner_directory(record)
    request = payload(record, action)['requestId']
    retained = directory / ('retained-' + hashlib.sha256(request.encode()).hexdigest())
    manifest = controller.read(retained / 'manifest.json')
    if (not isinstance(manifest, list) or not manifest or len(manifest) > 16
            or any(not isinstance(item, dict) or item.get('id') != record['universeId']
                   or Path(item.get('destination', '')).parent != retained for item in manifest)):
        return None
    return {'ok': True, 'status': 'removed', 'archives': manifest}


def never_dispatched(record):
    """Fence the original owner before proving a create was never dispatched.

    Reservation precedes the effect journal, so a crash can leave a reservation
    alone. Under the same dispatch lock, it is safe to release that exact row
    only when neither launch nor preparation ever acquired an effect intent.
    """
    request = payload(record, 'start')
    directory = controller.state() / ('owner-' + controller.digest(controller.owner(request)))
    directory.mkdir(mode=0o700, exist_ok=True)
    worker.private_directory(directory)
    with controller.locked(directory):
        effects = [controller.read(path) for path in directory.glob('effect-*.json')]
        # A completed no-effect capacity refusal is affirmative evidence too;
        # missing/unknown intent or any other operation remains held.
        no_effect = all(item and item.get('state') == 'completed'
                        and item.get('intent', {}).get('action') == 'start'
                        and item.get('result', {}).get('effectAdmitted') is False
                        and item.get('result', {}).get('admissionDenied') is True for item in effects)
        if (directory / 'preparation').exists() or not no_effect:
            return False
        if (directory / 'launch-dispatched.json').exists() and not effects:
            return False
        reservation = controller.read(directory / 'reservation.json')
        if reservation:
            row = {**controller.row_for(request), 'hostReservation': reservation}
            controller.machine.settle(row, release=True)
        return True


def selected_result(path, record):
    current = selected(record)
    if current and current['incus'] == record.get('candidate'):
        generations.verify(record['candidate'])
        if record['state'] != 'selected':
            save(path, record, state='selected')
        return {'complete': True, 'incus': record['candidate']}
    return None


def advance(identity):
    path = folder(identity)
    with controller.locked(path):
        record = controller.read(path / 'update.json')
        if not record or not record.get('candidate'):
            raise ValueError('The exact engine installation has not been confirmed.')
        completed = selected_result(path, record)
        if completed:
            return completed
        current = selected(record)
        if not current or current['incus'] != record['previous'] or not policy_matches(record):
            save(path, record, cancelled=True)
        before = record.get('phase')
        if record.get('cancelled'):
            if before in {'prepare', 'start'} and never_dispatched(record):
                save(path, record, state='cancelled', phase='failed', probeRemoved=True)
            elif before == 'qualified':
                save(path, record, state='cancelled', phase='failed')
            elif before not in {'stop', 'remove', 'failed'}:
                result = observe(record)
                if not result:
                    return {'complete': False, 'waiting': 'unknown'}
                if result.get('status') == 'absent':
                    save(path, record, state='cancelled', phase='failed', probeRemoved=True)
                elif result.get('status') in {'running', 'stopped'}:
                    save(path, record, checkPassed=False, phase='stop' if result['status'] == 'running' else 'remove')
                else:
                    return {'complete': False, 'waiting': 'unknown'}
        phase = record['phase']
        if phase == 'failed':
            if record.get('cancelled'):
                return {'complete': True, 'cancelled': True}
            raise ValueError('The candidate engine did not complete its qualification.')
        if phase == 'prepare':
            uploaded = call(record, 'source', content=record['source'], offset=0)
            if not uploaded.get('ok'):
                return {'complete': False, 'waiting': 'unknown'}
            result = call(record, 'prepare')
            if not result.get('ok') or not result.get('universeId'):
                return {'complete': False, 'waiting': 'unknown'}
            save(path, record, universeId=result['universeId'], phase='start')
        elif phase == 'start':
            result = call(record, 'start')
            if result.get('admissionDenied'):
                if result.get('effectAdmitted') is False:
                    # The native backend may durably decline before create.
                    # Its exact no-effect result permits a new admission check;
                    # unknown/admitted launches always retain their original ID.
                    save(path, record, startAttempt=record.get('startAttempt', 0) + 1)
                return {'complete': False, 'waiting': 'waiting'}
            if not result.get('ok') or not result.get('sourceReady'):
                observed = observe(record)
                if not observed:
                    return {'complete': False, 'waiting': 'unknown'}
                if observed.get('status') == 'absent':
                    save(path, record, phase='failed', probeRemoved=True)
                    raise ValueError('The qualification guest was not created.')
                ready = call(record, 'read')
                if observed.get('status') != 'running' or not ready.get('sourceReady'):
                    save(path, record, phase='stop', checkPassed=False)
                    return {'complete': False, 'waiting': 'waiting', 'progressed': True}
            save(path, record, phase='check')
        elif phase == 'check':
            config = worker.validate_configuration(record['candidate'], _engine_owner=record['owner'])
            env = worker.environment(config, path)
            _, instance = worker.observe(config, env, record['universeId'], payload(record, 'read'), confinement=True)
            limits = instance.get('expanded_config', {}) if instance else {}
            resource = profiles.resources('linux-python', payload(record, 'read')['resourceRequest'])
            expected = {'limits.cpu': str(resource['cpus']), 'limits.memory': str(resource['memoryMiB']) + 'MiB',
                        'limits.processes': str(resource['pids'])}
            if any(limits.get(key) != value for key, value in expected.items()):
                save(path, record, phase='stop', checkPassed=False)
                return {'complete': False, 'waiting': 'waiting', 'progressed': True}
            result = call(record, 'job_start', jobId=identity + '-check', command=COMMAND, timeoutSeconds=30)
            if not (result.get('job') or {}).get('terminal'):
                result = call(record, 'job_read', jobId=identity + '-check', command=COMMAND, timeoutSeconds=30)
            job = result.get('job') or {}
            if not job.get('terminal'):
                return {'complete': False, 'waiting': 'unknown' if job.get('state') == 'unknown' else 'waiting'}
            passed = job.get('state') == 'passed' and result.get('result', {}).get('exit_code') == 0 and SENTINEL in result.get('result', {}).get('stdout', '')
            save(path, record, phase='stop', checkPassed=passed, check=result)
        elif phase == 'stop':
            result = call(record, 'stop')
            if not result.get('ok') or result.get('status') != 'stopped':
                observed = observe(record)
                if not observed or observed.get('status') not in {'stopped', 'absent'}:
                    return {'complete': False, 'waiting': 'unknown'}
                if observed['status'] == 'absent':
                    # Absence settles owned cleanup, never successful artifact
                    # retention. The original manifest is still required.
                    recovered = removed_result(record, record.get('cleanupAction', 'remove'))
                    if not recovered:
                        save(path, record, phase='failed', probeRemoved=True)
                        if record.get('cancelled'):
                            return {'complete': True, 'cancelled': True}
                        raise ValueError('The guest is absent but retained qualification artifacts are unavailable.')
            save(path, record, phase='remove')
        elif phase == 'remove':
            action = record.get('cleanupAction', 'remove')
            result = call(record, action, retentionMode='files')
            if not result.get('ok') or result.get('status') != 'removed':
                recovered = removed_result(record, action)
                if recovered:
                    result = recovered
                elif result.get('effectAdmitted') is False and action == 'remove':
                    save(path, record, cleanupAction='finishCleanup')
                    return {'complete': False, 'waiting': 'waiting', 'progressed': True}
            if not result.get('ok') or result.get('status') != 'removed':
                return {'complete': False, 'waiting': 'unknown'}
            observed = observe(record)
            if not observed or observed.get('status') != 'absent':
                return {'complete': False, 'waiting': 'unknown'}
            verified = retained_proof(result)
            proof = {'version': 1, 'identity': record['candidate']['engineQualification']['sha256'],
                     'checkPassed': bool(record.get('checkPassed')), 'artifactVerified': verified, 'probeRemoved': True,
                     'machineId': record['machineId'], 'backendIdentity': record['backendIdentity'],
                     'snapshot': record['snapshot'], 'owner': record['owner'], 'operationId': identity,
                     'universeId': record['universeId'], 'jobId': identity + '-check'}
            controller.write(path / 'qualification.json', proof)
            save(path, record, proof=proof, phase='qualified' if record.get('checkPassed') and verified and not record.get('cancelled') else 'failed')
        elif phase == 'qualified':
            generations.verify(record['candidate'])
            config_path = Path(record['configPath'])
            # Same lock as normal local setup. Unrelated core updates are kept;
            # only the exact engine selection may change in this commit.
            with settings_lock(config_path):
                current = selected(record)
                if not current or current['incus'] != record['previous'] or not policy_matches(record):
                    save(path, record, state='superseded', phase='failed')
                    raise ValueError('The selected engine changed before publication.')
                candidate = {**current, 'incus': record['candidate']}
                core_binding.compose(config_path, current, candidate, path)
                controller.write(root() / ('selection-' + generations.digest(record['candidate']) + '.json'),
                    {'configuration': record['candidate'], 'revision': record['revision'], 'policy': record['policy'], 'updateId': identity})
                controller.write(config_path, candidate)
            save(path, record, state='selected')
            return {'complete': True, 'incus': record['candidate']}
        else:
            raise ValueError('Unsupported engine qualification step.')
        return {'complete': False, 'waiting': 'waiting', 'progressed': record['phase'] != before}


def publication_recovery(value):
    """Prove a settled unpublished candidate before a newer controller adopts CAS.

    The original installation, guest effects and controller receipts stay intact.
    This action cannot install, execute, stop, remove or select an engine.
    """
    if set(value) != {'action', 'id', 'intent'}:
        raise ValueError('Publication recovery needs the exact original intent.')
    path = folder(value['id'])
    with controller.locked(path):
        record = controller.read(path / 'update.json')
        if not record or record.get('intent') != value['intent']:
            raise ValueError('The original engine update arguments changed.')
        completed = selected_result(path, record)
        if completed:
            return completed
        if (record.get('state') != 'qualifying' or record.get('phase') != 'qualified'
                or record.get('cancelled') or not record.get('checkPassed')):
            raise ValueError('Only a settled qualified publication can change controller.')
        generations.verify(record['candidate'])
        observed = observe(record)
        if not observed or observed.get('status') != 'absent':
            return {'complete': False, 'waiting': 'unknown'}
        current = selected(record)
        if not current or current['incus'] != record['previous'] or not policy_matches(record):
            raise ValueError('The selected engine changed before publication recovery.')
        return {'complete': False, 'publicationReady': True,
                'intentSha256': generations.digest(record['intent']),
                'machineId': record['machineId'], 'backendIdentity': record['backendIdentity']}


def dispatch(value):
    if not isinstance(value, dict):
        raise ValueError('Unsupported engine update request.')
    if value.get('action') == 'stage':
        return stage(value)
    if value.get('action') == 'publicationRecovery':
        return publication_recovery(value)
    if set(value) != {'action', 'id'} or value['action'] not in {'advance', 'skip', 'read', 'cancel'}:
        raise ValueError('Unsupported engine update request.')
    if value['action'] in {'read', 'cancel'}:
        path = folder(value['id'])
        with controller.locked(path):
            record = controller.read(path / 'update.json')
            if not record:
                raise ValueError('The original engine update is unavailable.')
            completed = selected_result(path, record)
            if completed:
                return completed
            if value['action'] == 'read':
                return {'complete': False, 'state': record['state'], 'phase': record.get('phase')}
            save(path, record, cancelled=True)
        return advance(value['id'])
    if value['action'] == 'skip':
        path = folder(value['id'])
        with controller.locked(path):
            record = controller.read(path / 'update.json')
            if record and selected_result(path, record):
                return {'complete': True}
            if record and record.get('phase') not in {None, 'prepare', 'failed', 'qualified'}:
                raise ValueError('This candidate still owns a qualification guest. Reconcile it before skipping.')
            if record:
                if record.get('state') == 'install_failed' and not record.get('candidate'):
                    discard_private_runtime(path, record)
                if record.get('candidate'):
                    proof = controller.read(path / 'qualification.json') or {}
                    if (record.get('phase') == 'prepare' and never_dispatched(record)
                            or proof.get('probeRemoved') is True
                            or record.get('phase') == 'failed' and record.get('probeRemoved') is True):
                        discard_private_runtime(path, record)
                save(path, record, state='skipped')
        return {'complete': True}
    return advance(value['id'])


if __name__ == '__main__':
    if sys.version_info < (3, 13):
        if '--qualified-runtime' in sys.argv:
            raise ValueError('The managed engine updater requires Python 3.13 or later.')
        config = controller.configuration()
        python = config['interpreter']
        os.execv(python, [python, '-I', '-B', str(Path(__file__).resolve()), '--qualified-runtime'])
    print(json.dumps(dispatch(json.loads(sys.stdin.buffer.read(2 * 1024**2 + 1))), sort_keys=True))
