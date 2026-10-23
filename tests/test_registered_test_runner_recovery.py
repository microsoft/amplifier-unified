import base64
import hashlib
import asyncio
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_registered_test_runners import target, prepared, request
from amplifier_web import registered_test_runners as client
from amplifier_web import test_runner_controller as controller
from amplifier_web import test_host_resources as host


def test_returned_unknown_launch_can_settle_without_replay(target, monkeypatch):
    value = prepared(target)
    value['expectedBackendIdentity'] = target[1]
    effects = []
    def effect(payload):
        effects.append(payload['action'])
        if payload['action'] == 'start':
            return {'ok': False, 'status': 'unknown', 'effectAdmitted': True}
        observation = controller.worker.common.observation(payload, controller.worker.identity(payload), target[1], 'retained-launch', 'matched', True)
        return {'ok': True, 'status': 'stopped', 'observation': observation}
    monkeypatch.setattr(controller.worker, 'execute_call', effect)
    assert request(target, value, 'start')['status'] == 'unknown'
    assert host.snapshot()['reserved']['count'] == 1
    assert request(target, value, 'reconcile')['status'] == 'stopped'
    assert host.snapshot()['reserved']['count'] == 0
    assert request(target, value, 'start')['creationSettled']
    assert effects == ['start', 'reconcile']


def test_download_rejects_linked_export_directory_without_writing_elsewhere(tmp_path, monkeypatch):
    folder = tmp_path / 'owner'
    folder.mkdir(mode=0o700)
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir(mode=0o700)
    payload = {'requestId': 'export-one'}
    destination = folder / ('remote-export-' + client.digest(payload['requestId']))
    destination.symlink_to(unrelated, target_is_directory=True)
    data = b'archive content'
    archive = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(client, 'call', lambda *args, **kwargs: {'ok': True, 'content': base64.b64encode(data).decode(), 'offset': len(data), **archive})
    try:
        client.fetch_archives({}, folder, payload, [archive])
    except (ValueError, OSError):
        pass
    assert not (unrelated / '0.tar').exists(), 'Remote download escaped the retained owner directory through a linked export folder'


@pytest.mark.parametrize('kind', ['hardlink', 'fifo', 'symlink'])
def test_download_rejects_unowned_file_without_write(tmp_path, monkeypatch, kind):
    folder = tmp_path / 'owner'
    folder.mkdir(mode=0o700)
    payload = {'requestId': 'export-one'}
    destination = folder / ('remote-export-' + client.digest(payload['requestId']))
    destination.mkdir(mode=0o700)
    unrelated = tmp_path / 'unrelated'
    unrelated.write_bytes(b'unchanged')
    unrelated.chmod(0o600)
    path = destination / '0.tar'
    if kind == 'hardlink':
        os.link(unrelated, path)
    elif kind == 'fifo':
        os.mkfifo(path, 0o600)
    else:
        path.symlink_to(unrelated)
    monkeypatch.setattr(client, 'call', lambda *a, **kw: pytest.fail('No download may start'))
    with pytest.raises((ValueError, OSError)):
        client.fetch_archives({}, folder, payload, [{'bytes': 10, 'sha256': 'a'*64}])
    assert unrelated.read_bytes() == b'unchanged'


@pytest.mark.parametrize('kind', ['hardlink', 'fifo', 'symlink'])
def test_target_artifact_refuses_nonregular_or_linked_file(target, kind, tmp_path):
    value = prepared(target)
    root = target[0] / ('owner-' + controller.digest(controller.owner(value)))
    elsewhere = tmp_path / 'unrelated'
    elsewhere.write_bytes(b'private')
    elsewhere.chmod(0o600)
    path = root / 'retained.tar'
    if kind == 'hardlink':
        os.link(elsewhere, path)
    elif kind == 'fifo':
        os.mkfifo(path, 0o600)
    else:
        path.symlink_to(elsewhere)
    controller.write(root / ('effect-' + controller.digest(['export-one', 'remove']) + '.json'),
                     {'state': 'completed', 'result': {'archives': [{'destination': str(path), 'bytes': 7, 'sha256': hashlib.sha256(b'private').hexdigest()}]}})
    with pytest.raises((ValueError, OSError)):
        request(target, value, 'artifact', exportRequestId='export-one', archiveIndex=0, offset=0)


@pytest.mark.parametrize('initial', ['incomplete', 'lost-reply'])
def test_exact_cleanup_can_reobserve_then_keep_terminal_receipt(target, monkeypatch, initial):
    value = prepared(target)
    observed = []
    def cleanup(config, root):
        observed.append(root)
        if len(observed) == 1:
            if initial == 'lost-reply':
                raise TimeoutError('Cleanup response lost')
            return {'complete': False, 'status': 'unknown'}
        return {'complete': True, 'status': 'removed'}
    monkeypatch.setattr(controller.preparation, 'cleanup', cleanup)
    monkeypatch.setattr(controller.worker, 'execute_call', lambda *a, **kw: pytest.fail('Cleanup must not launch or execute a test'))
    if initial == 'lost-reply':
        with pytest.raises(TimeoutError):
            request(target, value, 'cleanupDependencies', requestId='cleanup-one')
    else:
        assert request(target, value, 'cleanupDependencies', requestId='cleanup-one')['complete'] is False
    result = request(target, value, 'cleanupDependencies', requestId='cleanup-one')
    assert result['complete'] is True
    assert request(target, value, 'cleanupDependencies', requestId='cleanup-one') == result
    assert len(observed) == 2 and observed[0] == observed[1]
    with pytest.raises(ValueError, match='original'):
        request(target, {**value, 'snapshot': {'archive_sha256': 'b'*64}}, 'cleanupDependencies', requestId='cleanup-one')


def test_retained_manifest_executes_old_package_after_current_membership_changes(tmp_path, monkeypatch):
    contents = {'amplifier_web/__init__.py': '', 'amplifier_web/test_runner_controller.py': base64.b64encode(b'import json,sys;print(json.dumps({"ok":True,"request":json.load(sys.stdin)}))').decode()}
    digest = client.digest(contents)
    root = tmp_path / digest / 'amplifier_web'
    root.mkdir(parents=True, mode=0o700)
    for name, value in contents.items():
        (root / Path(name).name).write_bytes(base64.b64decode(value))
    runner = {'packageFiles': ['__init__.py', 'test_runner_controller.py'], 'packageDigest': digest,
              'controller': str(root / 'test_runner_controller.py'), 'connection': 'target',
              'machineId': 'a'*32, 'backendIdentity': {'backend': 'incus', 'daemon': 'a', 'project': 'test'}}
    def transport(connection, command, payload, timeout):
        assert connection == 'target'
        result = subprocess.run([sys.executable, *command[1:]], input=json.dumps(payload).encode(), capture_output=True, timeout=timeout)
        if result.returncode:
            raise ValueError('Retained controller digest rejected')
        return json.loads(result.stdout)
    monkeypatch.setattr(client, 'ssh', transport)
    monkeypatch.setattr(client, 'FILES', ('entirely_new_module.py',))
    result = client.call(runner, {'action': 'read'})
    assert result['ok'] and result['request']['machineId'] == 'a'*32
    (root / 'test_runner_controller.py').write_text('print("changed")')
    with pytest.raises(ValueError, match='digest rejected'):
        client.call(runner, {'action': 'read'})


@pytest.mark.parametrize('changed_identity', [False, True])
def test_refresh_changes_only_future_descriptor_and_preserves_paused_preference(tmp_path, monkeypatch, changed_identity):
    class AppError(ValueError):
        def __init__(self, message, status):
            super().__init__(message)
            self.status = status
    monkeypatch.setitem(sys.modules, 'amplifier_web.service', SimpleNamespace(AppError=AppError))
    async def scenario():
        from amplifier_web import test_runner_enrollment as enrollment
        from amplifier_web import managed_runner_qualification as qualification
        checks = []
        app = SimpleNamespace(data_dir=tmp_path, lock=asyncio.Lock(),
            configuration_authority=SimpleNamespace(check=lambda authority: checks.append(authority)),
            test_queue=SimpleNamespace(backoff={}, wakeup=asyncio.Event(), start=lambda: None))
        app.test_queue.app = app
        old = {'id': 'a'*64, 'name': 'test machine', 'enabled': False, 'connection': 'test-target',
               'machineId': 'b'*32, 'profiles': ['linux-python'], 'packageDigest': 'c'*64,
               'controller': '/old/controller', 'configPath': '/retained/config', 'incus': {}}
        retained_owner = copy.deepcopy(old)
        client.publish(app, {'version': 1, 'runners': [old]})
        updated = {**old, 'packageDigest': 'd'*64, 'controller': '/new/controller', 'enabled': True}
        if changed_identity:
            updated['id'] = 'e'*64
        monkeypatch.setattr(client, 'register', lambda *args: copy.deepcopy(updated))
        def qualify(app, path, record, config, **kwargs):
            qualification.save(path, record, probeState='qualified', proof={'artifactVerified': True, 'probeRemoved': True})
            return 'qualified'
        monkeypatch.setattr(qualification, 'advance', qualify)
        args = {'id': old['id'], 'expectedRevision': client.snapshot(app)['revision']}
        response = await client.dispatch(app, 'testing.runners.refresh', args, 'host-authority')
        assert response['accepted'] and client.store(app)['runners'] == [old]
        identity = response['result']['enrollments'][0]['id']
        await enrollment.run(app.test_queue, identity)
        if changed_identity:
            assert enrollment.read(app, identity)['status'] == 'failed'
            assert client.store(app)['runners'] == [old]
        else:
            current = client.store(app)['runners'][0]
            assert current['packageDigest'] == 'd'*64 and current['enabled'] is False
        assert retained_owner == old and retained_owner['packageDigest'] == 'c'*64
        assert len(checks) >= 2 and set(checks) == {'host-authority'}
    asyncio.run(scenario())
