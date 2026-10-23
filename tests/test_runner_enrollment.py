"""Connect-and-check uses the actual origin/target owner lifecycle, fake backend only."""
import asyncio
import copy
import hashlib
import io
from pathlib import Path
import tarfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amplifier_web import registered_test_runners as runners
from amplifier_web import test_runner_enrollment as enrollment
from amplifier_web import managed_runner_qualification as probe
from amplifier_web import task_environments as environments
from amplifier_web import test_origin_files as files
from amplifier_web import test_environment_profiles as profiles
from amplifier_web import test_execution_settings as execution
from test_registered_test_runners import target, prepared, request
from amplifier_web import test_runner_controller as controller
from amplifier_web import test_host_resources as host


@pytest.fixture
def origin(tmp_path):
    home = tmp_path / 'origin'
    home.mkdir(mode=0o700)
    app = SimpleNamespace(data_dir=home, lock=asyncio.Lock(), configuration_authority=SimpleNamespace(check=Mock()))
    app.test_queue = SimpleNamespace(app=app, tasks={}, backoff={}, wakeup=asyncio.Event(), start=Mock())
    return app


@pytest.fixture
def route(origin, target, monkeypatch):
    runner = {'id': 'target-a', 'name': 'Test machine', 'connection': 'trusted-test', 'configPath': '',
              'controller': '/private/controller', 'packageDigest': 'f'*64, 'packageFiles': ['__init__.py'],
              'machineId': 'a'*32, 'accountId': 'account', 'backendIdentity': target[1],
              'incus': {'project': 'test'}, 'enabled': False, 'profiles': ['linux-python']}
    events, state = [], {'status': 'absent', 'fail': False, 'missing': False, 'available': True}
    monkeypatch.setattr(runners, 'register', lambda *args: copy.deepcopy(runner))
    def native(payload):
        action = payload['action']; events.append(action)
        if action == 'capacity':
            return {'ok': True, 'available': state['available'], 'capacity': {'available': state['available']}}
        if action == 'start':
            if not state['available']:
                return {'ok': False, 'admissionDenied': True, 'effectAdmitted': False}
            state['status'] = 'running'
            return {'ok': True, 'status': 'running', 'sourceReady': True}
        if action in {'job_start', 'job_read'}:
            return {'ok': True, 'job': {'terminal': True, 'state': 'failed' if state['fail'] else 'passed'},
                    'result': {'exit_code': 1 if state['fail'] else 0, 'stdout': probe.SENTINEL}}
        if action == 'stop':
            state['status'] = 'stopped'
            return {'ok': True, 'status': 'stopped'}
        if action == 'remove':
            archive = Path(payload['root']) / 'retained.tar'
            content = b'wrong' if state['missing'] else probe.SENTINEL.encode()
            with tarfile.open(archive, 'w') as out:
                member = tarfile.TarInfo('artifacts/runner-proof.txt'); member.size = len(content)
                out.addfile(member, io.BytesIO(content))
            archive.chmod(0o600)
            state['status'] = 'absent'
            return {'ok': True, 'status': 'removed', 'archives': [{'destination': str(archive), 'bytes': archive.stat().st_size,
                    'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}]}
        if action == 'reconcile':
            absent = state['status'] == 'absent'
            observation = controller.worker.common.observation(payload, controller.worker.identity(payload), target[1], 'retained-launch',
                                                                'absent' if absent else 'matched', True, absent)
            return {'ok': True, 'status': state['status'], 'observation': observation}
        if action == 'read':
            return {'ok': True, 'status': state['status'], 'sourceReady': state['status'] == 'running'}
        raise AssertionError(action)
    monkeypatch.setattr(controller.worker, 'execute_call', native)
    monkeypatch.setattr(controller.worker, 'environment', lambda *args: {})
    monkeypatch.setattr(controller.worker, 'observe', lambda *a, **kw: ('running', {'expanded_config': {
        'limits.cpu': '1', 'limits.memory': '512MiB', 'limits.processes': str(profiles.get('linux-python')['pids'])}}))
    def call(selected, payload, timeout=60):
        return controller.dispatch({'version': 1, 'machineId': selected['machineId'], 'backendIdentity': selected['backendIdentity'], 'payload': payload})
    monkeypatch.setattr(runners, 'call', call)
    return runner, events, state, call


async def accepted(app, request_id='connect-one', **changes):
    args = {'requestId': request_id, 'expectedRevision': runners.snapshot(app)['revision'],
            'name': 'Test machine', 'connection': 'trusted-test', 'configPath': '', **changes}
    response = await runners.dispatch(app, 'testing.runners.register', args, None)
    assert response['accepted']
    return response['result']['enrollments'][-1]['id'], args


async def completed(app, identity):
    for _ in range(5):
        await enrollment.run(app.test_queue, identity)
        record = enrollment.read(app, identity)
        if record['status'] in enrollment.TERMINAL:
            return record
    pytest.fail(str(record))


async def test_actual_controller_lifecycle_verifies_download_and_absence_before_enable(origin, route):
    identity, _ = await accepted(origin)
    assert not runners.snapshot(origin)['runners']
    record = await completed(origin, identity)
    assert record['status'] == 'connected', record
    assert record['proof']['artifactVerified'] and record['proof']['probeRemoved']
    assert record['proof']['isolation']['offline']
    saved = runners.store(origin)['runners'][0]
    assert saved['enabled'] and saved['lastCheck']['requestId'] == identity
    row = environments.get(origin.data_dir, probe.owner(record), record['resourceId'])
    assert row['status'] == 'retired'
    assert host.snapshot()['reserved']['count'] == 0
    for action in ['start', 'job_start', 'stop', 'remove']:
        assert route[1].count(action) == 1


@pytest.mark.parametrize('fault', ['fail', 'missing'])
async def test_failed_check_or_missing_artifact_cleans_owner_and_never_enables(origin, route, fault):
    route[2][fault] = True
    identity, _ = await accepted(origin)
    record = await completed(origin, identity)
    assert record['status'] == 'failed'
    assert not runners.store(origin)['runners']
    assert route[2]['status'] == 'absent'
    assert host.snapshot()['reserved']['count'] == 0


async def test_capacity_wait_is_durable_uses_existing_limits_and_cancel_never_launches(origin, route):
    route[2]['available'] = False
    identity, _ = await accepted(origin)
    await enrollment.run(origin.test_queue, identity)
    record = enrollment.read(origin, identity)
    assert record['status'] == 'capacity' and not record['effects'].get('start')
    assert 'start' not in route[1]
    assert origin.test_queue.backoff['enrollment:'+identity][0] > 0
    enrollment.request_cancel(origin, identity)
    record = await completed(origin, identity)
    assert record['status'] == 'cancelled'
    assert not runners.store(origin)['runners']
    assert 'start' not in route[1]


async def test_lost_create_and_removal_replies_recover_exact_receipts_without_replay(origin, route, monkeypatch):
    original = route[3]
    lost = {'start', 'remove'}
    def call(runner, payload, timeout=60):
        value = original(runner, payload, timeout)
        if payload['action'] in lost:
            lost.remove(payload['action'])
            raise TimeoutError('reply lost after recorded effect')
        return value
    monkeypatch.setattr(runners, 'call', call)
    identity, _ = await accepted(origin)
    record = await completed(origin, identity)
    assert record['status'] == 'connected', record
    assert route[1].count('start') == route[1].count('job_start') == route[1].count('remove') == 1


async def test_unconfirmed_native_create_stays_owned_and_never_enables_or_replays(origin, route, monkeypatch):
    original = controller.worker.execute_call
    def native(payload):
        if payload['action'] == 'start':
            route[1].append('start')
            raise TimeoutError('dispatch uncertain')
        if payload['action'] == 'reconcile':
            return {'ok': False, 'status': 'unknown'}
        return original(payload)
    monkeypatch.setattr(controller.worker, 'execute_call', native)
    identity, _ = await accepted(origin)
    for _ in range(3):
        await enrollment.run(origin.test_queue, identity)
    record = enrollment.read(origin, identity)
    assert record['status'] == 'unknown' and record['resourceId']
    assert route[1].count('start') == 1
    assert not runners.store(origin)['runners']
    assert host.snapshot()['reserved']['count'] == 1


async def test_stable_request_and_receipt_before_index_gap_recover_without_install_replay(origin, route):
    identity, args = await accepted(origin)
    value = runners.store(origin); value.pop('enrollments'); runners.publish(origin, value)
    response = await runners.dispatch(origin, 'testing.runners.register', args, None)
    assert response['result']['enrollments'][0]['id'] == identity
    with pytest.raises(Exception, match='different settings'):
        await runners.dispatch(origin, 'testing.runners.register', {**args, 'name': 'Changed'}, None)
    await completed(origin, identity)
    response = await runners.dispatch(origin, 'testing.runners.register', args, None)
    assert response['result']['runners'][0]['enabled']
    assert route[1].count('start') == 1


async def test_passive_read_does_not_connect_and_legacy_status_is_not_fabricated(origin, route, monkeypatch):
    runner = {**route[0], 'enabled': True}
    runners.publish(origin, {'version': 1, 'runners': [runner]})
    monkeypatch.setattr(runners, 'call', lambda *a: pytest.fail('passive read connected'))
    value = await runners.dispatch(origin, 'testing.runners.read', {}, None)
    assert value['result']['runners'][0]['enabled']
    assert value['result']['runners'][0]['lastCheck']['status'] is None


async def test_refresh_preserves_pause_made_during_check_and_does_not_replace_unrelated_runner(origin, route):
    prior = {**route[0], 'enabled': True}
    other = {**prior, 'id': 'other', 'connection': 'other'}
    runners.publish(origin, {'version': 1, 'runners': [prior, other]})
    result = await runners.dispatch(origin, 'testing.runners.refresh', {'id': prior['id'], 'requestId': 'recheck',
                                    'expectedRevision': runners.snapshot(origin)['revision']}, None)
    identity = result['result']['enrollments'][0]['id']
    value = runners.store(origin); value['runners'][0]['enabled'] = False; runners.publish(origin, value)
    await completed(origin, identity)
    saved = runners.store(origin)['runners']
    assert saved[0]['enabled'] is False and saved[0]['lastCheck']['status'] == 'passed'
    assert saved[1] == other


async def test_revoked_authority_only_cleans_original_work(origin, route):
    identity, _ = await accepted(origin)
    # Advance synchronously to a running owner, before the test command.
    while enrollment.read(origin, identity).get('probeState') != 'check':
        enrollment.step(origin, identity)
    origin.configuration_authority.check.side_effect = ValueError('revoked')
    record = await completed(origin, identity)
    assert record['status'] == 'cancelled'
    assert 'job_start' not in route[1]
    assert route[2]['status'] == 'absent'
    assert not runners.store(origin)['runners']


def test_receipt_read_cannot_dispatch_missing_effect_or_cross_owner(target, monkeypatch):
    value = prepared(target)
    monkeypatch.setattr(controller.worker, 'execute_call', lambda *a: pytest.fail('receipt dispatched effect'))
    result = request(target, value, 'receipt', effectAction='start')
    assert result['receiptState'] == 'missing'
    with pytest.raises(ValueError, match='original source'):
        request(target, {**value, 'resourceRequest': profiles.freeze('linux-python', {'cpus': 4})}, 'receipt', effectAction='start')


async def test_definite_start_capacity_refusal_uses_fresh_admission_not_repeated_check(origin, route, monkeypatch):
    original = controller.worker.execute_call
    denials = []
    def native(payload):
        if payload['action'] == 'start' and not denials:
            denials.append(payload['requestId'])
            return {'ok': False, 'admissionDenied': True, 'effectAdmitted': False}
        return original(payload)
    monkeypatch.setattr(controller.worker, 'execute_call', native)
    identity, _ = await accepted(origin)
    record = await completed(origin, identity)
    assert record['status'] == 'connected'
    assert record['startAttempt'] == 1
    assert record['effects']['start']['requestId'] != denials[0]
    assert route[1].count('start') == route[1].count('job_start') == 1


async def test_download_loss_recovers_original_export_without_repeating_remove(origin, route, monkeypatch):
    original = route[3]
    lost = []
    def call(runner, payload, timeout=60):
        if payload['action'] == 'artifact' and not lost:
            lost.append(True)
            raise OSError('download lost')
        return original(runner, payload, timeout)
    monkeypatch.setattr(runners, 'call', call)
    identity, _ = await accepted(origin)
    record = await completed(origin, identity)
    assert record['status'] == 'connected'
    assert route[1].count('remove') == route[1].count('job_start') == 1
    assert record['proof']['artifactVerified']


async def test_cancel_after_qualified_before_publish_never_enables(origin, route):
    identity, _ = await accepted(origin)
    for _ in range(12):
        record = enrollment.step(origin, identity)
        if record.get('probeState') == 'qualified':
            break
    assert record['probeState'] == 'qualified'
    enrollment.request_cancel(origin, identity)
    await enrollment.finish(origin, record)
    assert enrollment.read(origin, identity)['status'] == 'cancelled'
    assert not runners.store(origin)['runners']


async def test_accepted_profile_and_wait_limits_are_frozen(origin, route):
    execution.write(origin, {'version': 1, 'queue': {'defaultWaitSeconds': 1}, 'profiles': {'linux-python': {
        'memoryMiB': 256, 'defaultTimeoutSeconds': 2, 'maxTimeoutSeconds': 2}}})
    identity, _ = await accepted(origin)
    record = enrollment.read(origin, identity)
    assert record['resourceRequest']['memoryMiB'] == 256
    assert record['checkTimeoutSeconds'] == 2
    execution.write(origin, {'version': 1, 'queue': {'defaultWaitSeconds': 0}, 'profiles': {}})
    record['expiresAt'] = 1
    files.replace(enrollment.path(origin, identity), record)
    await enrollment.run(origin.test_queue, identity)
    assert enrollment.read(origin, identity)['status'] == 'cancelled'
    assert not route[1]


def test_damaged_enrollment_cannot_block_normal_scheduler(origin):
    enrollment.folder(origin, create=True)
    identity = 'f'*32
    files.replace(enrollment.path(origin, identity), {'version': 3})
    runners.publish(origin, {'version': 1, 'runners': [], 'enrollments': [identity]})
    enrollment.schedule(origin.test_queue)
    assert not origin.test_queue.tasks


@pytest.mark.parametrize('diagnostic,message', [
    (b'Host key verification failed. PRIVATE', 'identity'),
    (b'Permission denied (publickey). PRIVATE', 'sign-in'),
    (b'Connection refused PRIVATE', 'online'),
])
def test_connection_errors_are_actionable_without_exposing_remote_output(monkeypatch, diagnostic, message):
    monkeypatch.setattr(runners.shutil, 'which', lambda *args, **kwargs: '/usr/bin/ssh')
    monkeypatch.setattr(runners.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=255,stdout=b'',stderr=diagnostic))
    with pytest.raises(runners.ConnectionFailure, match=message) as error:
        runners.ssh('trusted-test', ['fixed'], {})
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('phase', ['connecting', 'prepare', 'start'])
@pytest.mark.parametrize('interrupt', ['shutdown', 'cancel', 'revoke', 'update'])
async def test_inflight_step_settles_but_no_next_effect_after_interruption(origin, route, monkeypatch, phase, interrupt):
    from amplifier_web.test_queue import TestQueue
    identity, _ = await accepted(origin)
    if phase != 'connecting':
        while enrollment.read(origin, identity).get('probeState', 'prepare') != phase or not enrollment.read(origin, identity).get('runner'):
            enrollment.step(origin, identity)
    entered, release = threading.Event(), threading.Event()
    original = runners.register if phase == 'connecting' else runners.call
    def blocked(*args, **kwargs):
        matches = phase == 'connecting' or args[1]['action'] == phase
        if matches:
            entered.set()
            assert release.wait(5), 'test did not release admitted operation'
        return original(*args, **kwargs)
    monkeypatch.setattr(runners, 'register' if phase == 'connecting' else 'call', blocked)
    queue = origin.test_queue
    queue.runner, queue.closing = None, False
    task = asyncio.create_task(enrollment.run(queue, identity))
    queue.tasks['enrollment:'+identity] = task
    close = None
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.01)
        if interrupt == 'shutdown':
            close = asyncio.create_task(TestQueue.close(queue))
            await asyncio.sleep(.02)
            assert not close.done() and not task.done(), 'shutdown released a live operation'
        elif interrupt == 'cancel':
            enrollment.request_cancel(origin, identity)
        elif interrupt == 'revoke':
            origin.configuration_authority.check.side_effect = ValueError('revoked')
        else:
            origin.state = {'updates': {'phase': 'activating'}}
        release.set()
        if close:
            await asyncio.wait_for(close, 5)
            assert task.cancelled()
        else:
            await asyncio.wait_for(task, 5)
        record = enrollment.read(origin, identity)
        assert record.get('runner'), 'the admitted installation receipt was lost'
        assert not runners.store(origin)['runners']
        assert 'job_start' not in route[1]
        assert route[1].count('start') == (1 if phase == 'start' else 0)
        if interrupt in {'cancel', 'revoke'}:
            record = await completed(origin, identity)
            assert record['status'] == 'cancelled' and route[2]['status'] == 'absent'
        else:
            # Host restart/update resumes this same durable owner, not its
            # already completed installation or launch.
            queue.closing, origin.state = False, {}
            record = await completed(origin, identity)
            assert record['status'] == 'connected'
            assert route[1].count('start') == route[1].count('job_start') == 1
    finally:
        release.set()
        await asyncio.gather(task, *( [close] if close else []), return_exceptions=True)


@pytest.mark.parametrize('reason', ['closing', 'closed', 'update'])
async def test_paused_host_neither_enrolls_nor_schedules_new_effects(origin, route, reason):
    identity, _ = await accepted(origin)
    if reason == 'closing': origin.test_queue.closing = True
    elif reason == 'closed': origin.closed = True
    else: origin.state = {'updates': {'pendingRestart': True}}
    enrollment.schedule(origin.test_queue)
    assert not origin.test_queue.tasks
    with pytest.raises(Exception, match='updating or closing'):
        await accepted(origin, 'other')
    await enrollment.run(origin.test_queue, identity)
    assert enrollment.read(origin, identity)['status'] == 'paused'
    assert not route[1]


@pytest.mark.parametrize('interrupt', ['cancel', 'revoke', 'update'])
async def test_isolation_read_does_not_authorize_later_job_after_guard_changes(origin, route, monkeypatch, interrupt):
    identity, _ = await accepted(origin)
    original = probe.isolation.inspect
    def inspect(*args):
        result = original(*args)
        if interrupt == 'cancel': enrollment.request_cancel(origin, identity)
        elif interrupt == 'revoke': origin.configuration_authority.check.side_effect = ValueError('revoked')
        else: origin.state = {'updates': {'pendingRestart': True}}
        return result
    monkeypatch.setattr(probe.isolation, 'inspect', inspect)
    await enrollment.run(origin.test_queue, identity)
    assert 'job_start' not in route[1]
    assert not runners.store(origin)['runners']
    if interrupt != 'update':
        assert (await completed(origin, identity))['status'] == 'cancelled'
        assert route[2]['status'] == 'absent'
