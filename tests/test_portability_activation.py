"""Activation account checks have a durable, non-replaying attempt boundary."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_foundation.session import SharedSessionStore, SessionTransferFencedError
from amplifier_portability.capsule import read_capsule, write_capsule
from amplifier_web.portability import Portability
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError, AppService
from test_portability import export, host_env, setup_hosts


def forbid_task_execution(monkeypatch, app):
    for name in ('start', 'send', 'control', 'scheduled_input'):
        monkeypatch.setattr(app.runtime, name, AsyncMock(side_effect=AssertionError('Task execution was not authorized')))


def assert_no_task_execution(app):
    for name in ('start', 'send', 'control', 'scheduled_input'):
        getattr(app.runtime, name).assert_not_awaited()
    assert not app.runtime.workers


@pytest.fixture
async def released_hosts(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    hosts = SimpleNamespace(app=app, target=target, source=source, destination=destination,
                            sid=sid, repository=destrepo)
    try:
        host_env(monkeypatch, source)
        exported, _ = await export(app, target, sid, root)
        host_env(monkeypatch, destination)
        staged = (await target.dispatch('portability.stage', {
            'path': exported['package'], 'repository': str(destrepo)}))['result']
        host_env(monkeypatch, source)
        released = (await app.dispatch('portability.release', {'sessionId': sid,
            'id': exported['id'], 'expectedRevision': exported['revision'],
            'path': staged['receiptPath']}))['result']
        hosts.staged = staged
        hosts.args = {'sessionId': sid, 'id': staged['id'],
                      'expectedRevision': staged['revision'], 'path': released['receiptPath']}
        host_env(monkeypatch, destination)
        forbid_task_execution(monkeypatch, target)
        yield hosts
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await hosts.target.close()


def assert_unknown_without_install(hosts):
    row = hosts.target.portability.node.get(hosts.staged['id'])
    assert row['phase'] == 'unknown' and row['previousPhase'] == 'activating'
    assert row['revision'] == hosts.args['expectedRevision'] + 2
    assert row['releaseCertificate'] == read_capsule(Path(hosts.args['path']))
    assert not hosts.target.state['sessions']
    store = SharedSessionStore(hosts.staged['destinationState']['workspace'], hosts.sid)
    with pytest.raises(SessionTransferFencedError):
        store.acquire(app='independent-native-consumer')
    assert_no_task_execution(hosts.target)
    return row


async def test_failed_activation_check_is_durable_and_exact_retry_never_replays(released_hosts, monkeypatch):
    hosts = released_hosts
    probe = AsyncMock(side_effect=ValueError('Synthetic uncertain provider response'))
    monkeypatch.setattr(Portability, 'destination_checks', probe)
    with pytest.raises(AppError, match='uncertain provider response'):
        await hosts.target.dispatch('portability.activate', hosts.args, command_id='same-activation')
    saved = assert_unknown_without_install(hosts)
    for _ in range(2):
        result = (await hosts.target.dispatch('portability.activate', hosts.args,
                                             command_id='same-activation'))['result']
        assert result == {**saved, 'duplicate': True}
    with pytest.raises(AppError, match='different contents'):
        await hosts.target.dispatch('portability.activate', {**hosts.args,
            'expectedRevision': saved['revision']})
    probe.assert_awaited_once()
    assert_unknown_without_install(hosts)


@pytest.mark.parametrize('skip_failure_journal', [False, True], ids=['cancellation', 'restart-recovery'])
async def test_interrupted_activation_stays_unknown_after_disk_reload(released_hosts, monkeypatch, skip_failure_journal):
    hosts = released_hosts
    entered = asyncio.Event()

    async def uncertain(*_):
        # Read the on-disk receipt at the external-effect boundary.
        saved = json.loads(hosts.target.portability.node.path(hosts.staged['id']).read_text())
        assert saved['phase'] == 'activating'
        assert saved['releaseCertificate'] == read_capsule(Path(hosts.args['path']))
        entered.set()
        await asyncio.Event().wait()

    probe = AsyncMock(side_effect=uncertain)
    monkeypatch.setattr(Portability, 'destination_checks', probe)
    if skip_failure_journal:
        # Model process loss before exception cleanup: only the durable
        # activating record exists when the replacement AppService starts.
        monkeypatch.setattr(hosts.target.portability.node, 'unknown', hosts.target.portability.node.get)
    attempt = asyncio.create_task(hosts.target.dispatch('portability.activate', hosts.args))
    try:
        await asyncio.wait_for(entered.wait(), 10)
    finally:
        attempt.cancel()
        with pytest.raises(asyncio.CancelledError):
            await attempt
    assert_no_task_execution(hosts.target)
    before_restart = hosts.target.portability.node.get(hosts.staged['id'])
    assert before_restart['phase'] == ('activating' if skip_failure_journal else 'unknown')
    await hosts.target.close()
    runtime = RuntimeManager()
    runtime.retention.wake = lambda: None
    hosts.target = AppService(hosts.destination / 'app', runtime, workspace=hosts.repository)
    forbid_task_execution(monkeypatch, hosts.target)
    saved = assert_unknown_without_install(hosts)
    result = (await hosts.target.dispatch('portability.activate', hosts.args))['result']
    assert result == {**saved, 'duplicate': True}
    probe.assert_awaited_once()
    assert_unknown_without_install(hosts)


async def test_activation_authenticates_before_probe_and_active_retry_only_finishes_clear(released_hosts, monkeypatch, tmp_path):
    hosts = released_hosts
    checks = {'runtimeVerified': True, 'accountVerified': True,
              'nativeFenceVerified': True, 'credentialsOrigin': 'destination'}
    probe = AsyncMock(return_value=checks)
    monkeypatch.setattr(Portability, 'destination_checks', probe)
    certificate = read_capsule(Path(hosts.args['path']))
    bad_signature = copy.deepcopy(certificate)
    bad_signature['body']['sessionId'] = 'another-task'
    wrong_ready = copy.deepcopy(certificate['body'])
    wrong_ready['readyHash'] = '0' * 64
    wrong_transfer = copy.deepcopy(certificate['body'])
    wrong_transfer['capsuleHash'] = '0' * 64
    for index, invalid in enumerate((bad_signature, hosts.app.portability.node.sign(wrong_ready),
                                     hosts.app.portability.node.sign(wrong_transfer))):
        path = tmp_path / f'invalid-release-{index}.json'
        write_capsule(path, invalid)
        with pytest.raises(AppError):
            await hosts.target.dispatch('portability.activate', {**hosts.args, 'path': str(path)})
        assert hosts.target.portability.node.get(hosts.staged['id'])['phase'] == 'ready'
        probe.assert_not_awaited()

    finish = Portability.finish_native_activation
    with monkeypatch.context() as patch:
        def fail_clear(*_):
            raise OSError('Synthetic final-clear interruption')
        patch.setattr(Portability, 'finish_native_activation', fail_clear)
        with pytest.raises(AppError, match='final-clear interruption'):
            await hosts.target.dispatch('portability.activate', hosts.args)
    committed = hosts.target.portability.node.get(hosts.staged['id'])
    assert committed['phase'] == 'active' and hosts.target.portability.fenced(hosts.sid)
    assert Portability.finish_native_activation is finish
    result = (await hosts.target.dispatch('portability.activate', hosts.args))['result']
    assert result['phase'] == 'active' and result['duplicate']
    assert not hosts.target.portability.fenced(hosts.sid)
    probe.assert_awaited_once()
    assert_no_task_execution(hosts.target)
