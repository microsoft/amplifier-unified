"""Engine lifecycle against real durable controller fences, without live guests."""
import base64
import copy
import hashlib
import io
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

pytest.importorskip('fcntl')
from amplifier_web import managed_incus_target as target
from amplifier_web import incus_engine_generations as generations
from amplifier_web import test_runner_controller as controller
from amplifier_web import test_host_resources as host


@pytest.fixture
def engine(tmp_path, monkeypatch):
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    store = home / 'engines'; store.mkdir(mode=0o700)
    state = home / 'target'; state.mkdir(mode=0o700)
    config_path = home / 'test-environments.json'
    backend = {'backend': 'incus', 'daemon': 'a' * 64, 'project': 'test'}
    previous = {'executable': '/previous/bin/engine', 'project': 'test', 'configDir': str(home),
                'admissionDir': str(state), 'incusExecutable': '/usr/bin/incus',
                'daemonFingerprint': 'a' * 64, 'executableSha256': 'b' * 64, 'packageSha256': 'c' * 64, 'image': 'd' * 64}
    config = {'enabled': True, 'backend': 'incus', 'installationId': 'a' * 24,
              'commit': next(iter(target.core_binding.STATIC_CORE_COMMITS)), 'incus': previous}
    controller.write(config_path, config)
    policy = {'version': 1, 'repository': generations.REPOSITORY, 'ref': 'main', 'origin': 'fixture'}
    monkeypatch.setattr(generations, 'root', lambda: store)
    monkeypatch.setattr(generations, 'runtime_identity', lambda python: {'version': 1, 'runtimeSha256': 'e' * 64})
    monkeypatch.setattr(target.probe, 'inspect', lambda request: {'configuration': previous, 'revision': '1' * 40, 'policy': policy})
    monkeypatch.setattr(target.probe, 'policy', lambda *args: policy)
    monkeypatch.setattr(target.probe, 'interpreter', lambda config: Path('/fixture/python'))
    monkeypatch.setattr(target.worker, 'validate_configuration', lambda config, **kwargs: {**config})
    monkeypatch.setattr(target.worker, 'package_digest', lambda path: 'f' * 64)
    monkeypatch.setattr(target.shutil, 'which', lambda name: '/fixture/uv')
    installs = []
    def install(argv, **kwargs):
        installs.append(argv)
        if argv[1] == 'venv':
            runtime = Path(argv[-1]); (runtime / 'bin').mkdir(parents=True)
            (runtime / 'bin/python').write_text('python')
            (runtime / 'bin/amplifier-digital-twin').write_text('launcher')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(target.subprocess, 'run', install)
    monkeypatch.setattr(target, 'package', lambda python: {'directory': '/fixture/package', 'version': '9.0',
        'source': {'url': generations.REPOSITORY, 'vcs_info': {'vcs': 'git', 'commit_id': '2' * 40}}})
    monkeypatch.setattr(controller, 'state', lambda: state)
    controller.write(state / 'authority.json', {'version': 1, 'id': 'a' * 32})
    monkeypatch.setattr(controller, 'backend', lambda config, root: (backend, {'supported': True, 'machineId': 'machine-a'}))
    monkeypatch.setattr(host, 'identity', lambda: 'machine-a')
    monkeypatch.setattr(host, 'directory', lambda: home / 'machine')
    monkeypatch.setattr(host, 'observe', lambda: {'cpuCount': 8, 'memoryMiB': 32768,
        'availableMemoryMiB': 30000, 'freeBytes': 100 * 1024**3, 'loadPercent': 5})
    monkeypatch.setattr(target.worker, 'environment', lambda *args: {})
    monkeypatch.setattr(target.worker, 'observe', lambda *args, **kwargs: ({}, {'expanded_config': {
        'limits.cpu': '1', 'limits.memory': '512MiB', 'limits.processes': '128'}}))
    effects = []; native = {'status': 'absent', 'deny': False, 'lost_remove': False, 'lost_start': False}
    def effect(p, **kwargs):
        effects.append(p['action'])
        if p['action'] == 'start':
            if native['deny']:
                return {'ok': False, 'status': 'not-started', 'admissionDenied': True, 'effectAdmitted': False}
            native['status'] = 'running'
            if native['lost_start']:
                raise TimeoutError('lost create reply')
            return {'ok': True, 'status': 'running', 'sourceReady': True}
        if p['action'] == 'read':
            return {'ok': True, 'sourceReady': True}
        if p['action'] in {'job_start', 'job_read'}:
            return {'ok': True, 'job': {'terminal': True, 'state': 'passed'},
                    'result': {'exit_code': 0, 'stdout': target.SENTINEL}}
        if p['action'] == 'stop':
            native['status'] = 'stopped'
            return {'ok': True, 'status': 'stopped'}
        if p['action'] == 'remove':
            retained = Path(p['root']) / ('retained-' + hashlib.sha256(p['requestId'].encode()).hexdigest())
            retained.mkdir(mode=0o700)
            archive = retained / 'artifacts.tar'
            with tarfile.open(archive, 'w') as tar:
                member = tarfile.TarInfo('artifacts/runner-proof.txt'); member.size = len(target.SENTINEL)
                tar.addfile(member, io.BytesIO(target.SENTINEL.encode()))
            archives = [{'id': p['universeId'], 'destination': str(archive),
                         'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'bytes': archive.stat().st_size}]
            controller.write(retained / 'manifest.json', archives)
            native['status'] = 'absent'
            if native['lost_remove']:
                raise TimeoutError('lost remove reply')
            return {'ok': True, 'status': 'removed', 'archives': archives}
        if p['action'] == 'reconcile':
            return {'ok': True, 'status': native['status'], 'observation': {
                **target.worker.common.binding(p, p['universeId'], backend), 'admissionSettled': True,
                'ownership': 'absent' if native['status'] == 'absent' else 'matched',
                'absenceConfirmed': native['status'] == 'absent'}}
        raise AssertionError(p['action'])
    monkeypatch.setattr(target.worker, 'execute_call', effect)
    content = b'fixed-snapshot'
    request = {'action': 'stage', 'id': 'b' * 32, 'configPath': str(config_path), 'expectedIncus': previous,
               'policy': policy, 'current': '1' * 40, 'revision': '2' * 40, 'machineId': 'a' * 32,
               'backendIdentity': backend, 'source': base64.b64encode(content).decode(),
               'snapshot': {'archive_sha256': hashlib.sha256(content).hexdigest(), 'snapshot_commit': 'c', 'snapshot_tree': 't'}}
    return SimpleNamespace(home=home, path=store / request['id'], config_path=config_path, request=request,
        previous=previous, config=config, effects=effects, installs=installs, native=native, policy=policy)


def started(engine):
    assert target.stage(engine.request)['state'] == 'qualifying'
    assert target.advance(engine.request['id'])['progressed']
    return engine.request['id']


def qualify(engine):
    identity = started(engine)
    for _ in range(8):
        result = target.advance(identity)
        if result['complete']:
            return result
    raise AssertionError('did not finish')


def test_real_phase_sequence_qualifies_before_selection_and_keeps_old_binding(engine):
    result = qualify(engine)
    assert result['complete']
    assert engine.effects.count('start') == engine.effects.count('job_start') == engine.effects.count('remove') == 1
    assert host.snapshot()['reserved']['count'] == 0
    assert controller.read(engine.config_path)['incus'] == result['incus']
    assert generations.verify(result['incus'])['snapshot'] == engine.request['snapshot']
    assert controller.read(engine.path / 'update.json')['previous'] == engine.previous
    assert target.stage(engine.request)['state'] == 'selected'
    assert len(engine.installs) == 2


def test_stage_retry_recognizes_exact_install_without_reinstall(engine, monkeypatch):
    original = target.installed_candidate
    monkeypatch.setattr(target, 'installed_candidate', lambda *args: (_ for _ in ()).throw(TimeoutError('lost')))
    with pytest.raises(TimeoutError):
        target.stage(engine.request)
    monkeypatch.setattr(target, 'installed_candidate', original)
    assert target.stage(engine.request)['state'] == 'qualifying'
    assert len(engine.installs) == 2
    with pytest.raises(ValueError, match='arguments changed'):
        target.stage({**engine.request, 'revision': '3' * 40})


def test_backend_capacity_decline_rechecks_without_replaying_unknown_create(engine):
    identity = started(engine); engine.native['deny'] = True
    assert target.advance(identity)['waiting'] == 'waiting'
    engine.native['deny'] = False
    assert target.advance(identity)['progressed']
    assert engine.effects.count('start') == 2
    assert controller.read(engine.path / 'update.json')['phase'] == 'check'


def test_unknown_create_recovers_exact_receipt_without_relaunch(engine):
    identity = started(engine); engine.native['lost_start'] = True
    with pytest.raises(TimeoutError):
        target.advance(identity)
    assert target.advance(identity)['progressed']
    assert engine.effects.count('start') == 1


def test_lost_remove_reply_uses_original_retained_manifest_no_remove_replay(engine):
    identity = started(engine)
    for _ in range(3):
        target.advance(identity)
    engine.native['lost_remove'] = True
    with pytest.raises(TimeoutError):
        target.advance(identity)
    assert target.advance(identity)['progressed']
    assert target.advance(identity)['complete']
    assert engine.effects.count('remove') == 1


def test_lost_selection_ack_recovers_without_republishing(engine, monkeypatch):
    original = target.save
    def lost(path, record, **changes):
        if changes.get('state') == 'selected':
            raise TimeoutError('selection reply lost')
        return original(path, record, **changes)
    monkeypatch.setattr(target, 'save', lost)
    with pytest.raises(TimeoutError):
        qualify(engine)
    monkeypatch.setattr(target, 'save', original)
    assert target.dispatch({'action': 'read', 'id': engine.request['id']})['complete']
    before = list(engine.effects)
    assert target.advance(engine.request['id'])['complete']
    assert engine.effects == before


def test_changed_policy_before_start_cancels_without_guest(engine):
    identity = started(engine)
    engine.policy['ref'] = 'fixed'
    # Saved JSON is independent of the current policy object.
    result = target.advance(identity)
    assert result == {'complete': True, 'cancelled': True}
    assert engine.effects == []


def test_private_candidate_cannot_be_used_by_another_owner_or_public_json(engine):
    target.stage(engine.request)
    record = controller.read(engine.path / 'update.json')
    with pytest.raises(ValueError, match='not completed'):
        generations.verify(record['candidate'])
    with pytest.raises(ValueError, match='not completed'):
        generations.verify(record['candidate'], owner='other')
    assert generations.verify(record['candidate'], owner=record['owner'])
    req = {'version': 1, 'machineId': 'a' * 32, 'backendIdentity': record['backendIdentity'],
           'payload': target.payload(record, 'start')}
    with pytest.raises(ValueError, match='exact qualification owner'):
        controller.dispatch(req, _configuration=record['candidate'], _engine_owner='other')
    with pytest.raises(ValueError, match='Unsupported'):
        controller.dispatch({**req, '_engine_owner': record['owner']})


def test_engine_qualification_uses_resources_instead_of_ordinary_count_quotas(engine, monkeypatch):
    identity = started(engine)
    record = controller.read(engine.path / 'update.json')
    base = controller.state()
    current = controller.backend_policy.snapshot(base, record['backendIdentity'], record['candidate'])
    limits = {'maxRunning': 1, 'maxInstances': 1, 'minFreeBytes': 8 * 1024**3}
    controller.backend_policy.save(base, record['backendIdentity'], record['candidate'], current['revision'], limits)
    original = target.worker.execute_call
    observed = []
    def effect(payload, **kwargs):
        if payload['action'] == 'start':
            observed.append(payload['incus'])
            assert payload['incus']['maxRunning'] == payload['incus']['maxInstances'] == 0
            assert payload['incus']['minFreeBytes'] == limits['minFreeBytes']
            assert host.snapshot()['reserved']['count'] == 1
        return original(payload, **kwargs)
    monkeypatch.setattr(target.worker, 'execute_call', effect)
    assert target.advance(identity)['progressed']
    assert observed
    assert controller.backend_policy.snapshot(base, record['backendIdentity'], record['candidate'])['limits'] == limits


def test_engine_qualification_still_waits_for_real_memory_pressure(engine):
    identity = started(engine)
    current = host.snapshot()
    host.save(current['revision'], {'minAvailableMemoryMiB': 30000})
    assert target.advance(identity)['waiting'] == 'waiting'
    assert engine.effects == []
    assert host.snapshot()['reserved']['count'] == 0


def test_wrong_observation_never_releases_uncertain_owner(engine, monkeypatch):
    identity = started(engine)
    target.advance(identity)
    record = controller.read(engine.path / 'update.json')
    monkeypatch.setattr(target, 'call', lambda *args: {'ok': True, 'status': 'absent',
        'observation': {'backendIdentity': record['backendIdentity'], 'ownership': 'absent',
                        'admissionSettled': True, 'absenceConfirmed': True}})
    assert target.observe(record) is None
    assert host.snapshot()['reserved']['count'] == 1


def test_cancel_after_positive_no_effect_capacity_denial_finishes(engine):
    identity = started(engine); engine.native['deny'] = True
    assert target.advance(identity)['waiting'] == 'waiting'
    assert target.dispatch({'action': 'cancel', 'id': identity}) == {'complete': True, 'cancelled': True}
    assert engine.effects == ['start']
    assert host.snapshot()['reserved']['count'] == 0


def test_unknown_dispatch_does_not_claim_never_started(engine):
    identity = started(engine); engine.native['lost_start'] = True
    with pytest.raises(TimeoutError):
        target.advance(identity)
    record = controller.read(engine.path / 'update.json')
    assert target.never_dispatched(record) is False
    assert host.snapshot()['reserved']['count'] == 1


def test_incomplete_private_install_retries_in_separate_directory(engine, monkeypatch):
    original = target.installed_candidate
    calls = []
    def incomplete(path, record):
        calls.append(record.get('runtimeDirectory', 'runtime'))
        if len(calls) < 3:
            raise target.IncompleteInstallation('not installed')
        return original(path, record)
    monkeypatch.setattr(target, 'installed_candidate', incomplete)
    with pytest.raises(target.IncompleteInstallation):
        target.stage(engine.request)
    assert (engine.path / 'runtime').exists()
    assert target.stage(engine.request)['state'] == 'qualifying'
    assert (engine.path / 'runtime-2').exists() and (engine.path / 'runtime').exists()
    assert len(engine.installs) == 4 and engine.effects == []


def test_absent_at_stop_without_original_artifact_never_qualifies(engine, monkeypatch):
    identity = started(engine)
    target.advance(identity); target.advance(identity)
    original = target.call
    monkeypatch.setattr(target, 'call', lambda record, action, **kwargs:
        {'ok': False, 'status': 'unknown'} if action == 'stop' else original(record, action, **kwargs))
    engine.native['status'] = 'absent'
    with pytest.raises(ValueError, match='artifacts are unavailable'):
        target.advance(identity)
    assert controller.read(engine.config_path)['incus'] == engine.previous
    assert not (engine.path / 'qualification.json').exists()


def test_skipping_unstarted_settled_candidate_removes_only_its_private_runtime(engine):
    target.stage(engine.request)
    assert (engine.path / 'runtime').exists()
    assert target.dispatch({'action': 'skip', 'id': engine.request['id']})['complete']
    assert not (engine.path / 'runtime').exists()
    assert (engine.path / 'identity.json').exists()
    assert controller.read(engine.config_path) == engine.config
    assert engine.effects == []


def test_known_failed_install_can_clean_and_retry_without_guest_effect(engine, monkeypatch):
    original = target.subprocess.run; metadata = target.package
    failed = [True]
    def installer(argv, **kwargs):
        if argv[1] == 'pip' and failed[0]:
            raise target.subprocess.CalledProcessError(1, argv)
        return original(argv, **kwargs)
    monkeypatch.setattr(target.subprocess, 'run', installer)
    monkeypatch.setattr(target, 'package', lambda python: (_ for _ in ()).throw(target.IncompleteInstallation())
        if python.parent.parent.name == 'runtime' else metadata(python))
    with pytest.raises(target.subprocess.CalledProcessError):
        target.stage(engine.request)
    failed[0] = False
    assert target.stage(engine.request)['state'] == 'qualifying'
    assert not (engine.path / 'runtime').exists() and (engine.path / 'runtime-2').exists()
    assert engine.effects == []


@pytest.mark.parametrize('damage', ['runtime', 'identity', 'qualification'])
def test_changed_generation_cannot_borrow_qualified_engine_proof(engine, monkeypatch, damage):
    config = qualify(engine)['incus']
    if damage == 'runtime':
        monkeypatch.setattr(generations, 'runtime_identity', lambda python: {'version': 1, 'runtimeSha256': '0' * 64})
    elif damage == 'identity':
        path = engine.path / 'identity.json'
        value = controller.read(path); value['revision'] = '3' * 40
        controller.write(path, value)
    else:
        path = engine.path / 'qualification.json'
        value = controller.read(path); value['artifactVerified'] = False
        controller.write(path, value)
    with pytest.raises(ValueError):
        generations.verify(config)


def test_skip_prelaunch_cancel_removes_known_unused_runtime(engine):
    identity = started(engine)
    engine.policy['ref'] = 'changed'
    assert target.advance(identity) == {'complete': True, 'cancelled': True}
    assert target.dispatch({'action': 'skip', 'id': identity})['complete']
    assert not (engine.path / 'runtime').exists()


def test_skip_failed_artifact_proof_removes_only_settled_unpublished_runtime(engine, monkeypatch):
    identity = started(engine)
    for _ in range(3):
        target.advance(identity)
    monkeypatch.setattr(target, 'retained_proof', lambda result: False)
    target.advance(identity)
    record = controller.read(engine.path / 'update.json')
    assert record['phase'] == 'failed' and record['proof']['probeRemoved']
    assert record['proof']['checkPassed'] and not record['proof']['artifactVerified']
    assert target.dispatch({'action': 'skip', 'id': identity})['complete']
    assert not (engine.path / 'runtime').exists()
    assert (engine.path / 'qualification.json').exists()


@pytest.mark.parametrize('lost_ack', [False, True])
def test_later_selection_change_never_retires_previously_published_runtime(engine, monkeypatch, lost_ack):
    identity = started(engine)
    for _ in range(4):
        target.advance(identity)
    if lost_ack:
        original = target.save
        def lose(path, record, **changes):
            if changes.get('state') == 'selected':
                raise TimeoutError('selection acknowledgement lost')
            return original(path, record, **changes)
        monkeypatch.setattr(target, 'save', lose)
        with pytest.raises(TimeoutError):
            target.advance(identity)
        monkeypatch.setattr(target, 'save', original)
    else:
        assert target.advance(identity)['complete']
    # A different current config says nothing about already accepted old jobs.
    controller.write(engine.config_path, engine.config)
    assert target.dispatch({'action': 'skip', 'id': identity})['complete']
    assert (engine.path / 'runtime').exists()


def test_publication_uses_real_normal_setup_filelock_and_rejects_writable_or_linked_lock(engine):
    from filelock import FileLock, Timeout
    directory = engine.config_path.parent / 'test-runner-setup'
    directory.mkdir(mode=0o700)
    path = directory / 'setup.lock'
    # Normal setup owns the producer semantics, including default0644 mode.
    with FileLock(str(path), timeout=0):
        assert path.stat().st_mode & 0o777 == 0o644
        with pytest.raises(BlockingIOError):
            with target.settings_lock(engine.config_path):
                pytest.fail('Ignored the normal setup lock')
    with target.settings_lock(engine.config_path):
        with pytest.raises(Timeout):
            with FileLock(str(path), timeout=0):
                pytest.fail('Normal setup ignored the updater lock')
    path.chmod(0o666)
    with pytest.raises(ValueError, match='setup lock'):
        with target.settings_lock(engine.config_path):
            pass
    path.unlink(); path.symlink_to(directory / 'other')
    with pytest.raises(OSError):
        with target.settings_lock(engine.config_path):
            pass


def test_new_controller_can_only_recover_original_settled_publication(engine):
    identity = started(engine)
    request = {'action': 'publicationRecovery', 'id': identity,
               'intent': {key: value for key, value in engine.request.items() if key != 'action'}}
    with pytest.raises(ValueError, match='settled qualified'):
        target.dispatch(request)
    for _ in range(4):
        target.advance(identity)
    before = list(engine.effects)
    result = target.dispatch(request)
    assert result['publicationReady'] and result['intentSha256'] == generations.digest(request['intent'])
    assert engine.effects[len(before):] == ['reconcile']
    with pytest.raises(ValueError, match='arguments changed'):
        target.dispatch({**request, 'intent': {**request['intent'], 'configPath': '/other/config'}})
    assert target.advance(identity)['complete']
    assert target.dispatch(request)['complete']  # publication acknowledgement lost
    assert engine.effects.count('start') == engine.effects.count('job_start') == 1


def test_new_controller_refuses_unknown_or_changed_target_publication(engine, monkeypatch):
    identity = started(engine)
    for _ in range(4):
        target.advance(identity)
    request = {'action': 'publicationRecovery', 'id': identity,
               'intent': {key: value for key, value in engine.request.items() if key != 'action'}}
    original = target.observe
    monkeypatch.setattr(target, 'observe', lambda record: None)
    assert target.dispatch(request) == {'complete': False, 'waiting': 'unknown'}
    monkeypatch.setattr(target, 'observe', original)
    value = controller.read(engine.config_path); value['incus']['project'] = 'other'
    controller.write(engine.config_path, value)
    with pytest.raises(ValueError, match='selected engine changed'):
        target.dispatch(request)
