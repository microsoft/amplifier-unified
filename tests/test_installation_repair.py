"""Installation repair admission, identity and no-replay boundaries."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import installation_repair as repair
from amplifier_web.service import AppService, AppError
from amplifier_web.updates import UpdateManager
from test_service import Runtime


@pytest.fixture
async def host(tmp_path, monkeypatch):
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    service.update_manager = UpdateManager(service)
    service.runtime.workers = {}
    service.runtime._preparations = {}
    service.runtime.has_pending_operations = lambda: False
    expected = {'home': str(tmp_path), 'uid': os.getuid(),
        'hostInstanceId': service.instance_id, 'extras': ['native-desktop'],
        'basePython': '/qualified/base/python', 'env': {'UV_INDEX_PASSWORD': 'private-secret'},
        'pointer': {'exists': False, 'sha256': None}}
    monkeypatch.setattr(repair, 'binding', AsyncMock(side_effect=lambda _: copy.deepcopy(expected)))
    from amplifier_web import reset, app_release_artifacts
    monkeypatch.setattr(reset, 'release_metadata', lambda env: {'tag_name': 'v99.0.0'})
    monkeypatch.setattr(reset, 'download_command', lambda *args, **kwargs:
        SimpleNamespace(stdout='a' * 40 + '\trefs/tags/v99.0.0\n'))
    monkeypatch.setattr(app_release_artifacts, 'prepare', AsyncMock(return_value={
        'url': 'https://github.com/microsoft/amplifier-unified/releases/download/v99.0.0/amplifier_unified-99.0.0-py3-none-any.whl',
        'path': str(tmp_path / 'wheel.whl'), 'sha256': 'b' * 64}))
    launched = []
    monkeypatch.setattr(repair, 'launch', lambda folder, plan: launched.append((folder, plan)))
    yield SimpleNamespace(service=service, expected=expected, launched=launched)
    await service.close()


async def prepared(host):
    view = (await host.service.dispatch('maintenance.installationRepairPreview', {}))['result']
    return view, {'previewId': view['previewId'],
                  'hostInstanceId': view['hostInstanceId'], 'confirm': True}


async def test_preview_exact_scope_and_no_private_environment_projection(host):
    view, _ = await prepared(host)
    assert view['supported'] and view['target']['revision'] == 'a' * 40
    assert view['installedExtras'] == ['native-desktop']
    assert 'optional-features' in view['preserves']
    assert view['replaces'] == ['uv-tool-environment', 'application-launch-pointer']
    assert not host.launched
    assert 'private-secret' not in json.dumps(host.service.state['maintenance'])


async def test_confirm_returns_same_operation_without_second_helper_even_after_host_change(host):
    view, args = await prepared(host)
    response = await host.service.dispatch('maintenance.installationRepairConfirm', args)
    operation = response['result']
    assert operation['previewId'] == view['previewId'] and operation['status'] == 'accepted'
    assert repair.held(host.service)
    host.service.instance_id = 'new-host'
    repeated = await host.service.dispatch('maintenance.installationRepairConfirm', args)
    assert repeated['result'] == operation
    assert len(host.launched) == 1
    queried = await host.service.dispatch('maintenance.installationRepairStatus', {'previewId': view['previewId']})
    assert queried['result']['operation'] == operation
    assert (await repair.status(host.service, {'previewId': 'e' * 32})) == {'operation': None}


@pytest.mark.parametrize('change', ['host', 'pointer', 'extras', 'expired', 'busy', 'unknown-update'])
async def test_stale_or_busy_confirmation_never_dispatches(host, change):
    view, args = await prepared(host)
    if change == 'host':
        args['hostInstanceId'] = 'wrong'
    elif change == 'pointer':
        host.expected['pointer'] = {'exists': True, 'sha256': 'f' * 64}
    elif change == 'extras':
        host.expected['extras'] = []
    elif change == 'expired':
        path = repair.root(host.service) / ('preview-' + view['previewId'] + '.json')
        saved = repair._read(path, host.service.data_dir)
        saved['preview']['expiresAt'] = 0
        repair._write(path, saved, host.service.data_dir)
    elif change == 'busy':
        await host.service.dispatch('session.create', {})
        host.service._session()['status'] = 'working'
    else:
        host.service.state['updates']['pendingReplacement'] = {'unqualified': True}
    with pytest.raises(AppError):
        await host.service.dispatch('maintenance.installationRepairConfirm', args)
    assert not host.launched
    assert repair._latest(host.service) is None


async def test_activity_started_while_downloading_is_rechecked(host, monkeypatch):
    _, args = await prepared(host)
    from amplifier_web import app_release_artifacts
    original = app_release_artifacts.prepare
    async def download(*a, **kw):
        result = await original(*a, **kw)
        await host.service.dispatch('session.create', {})
        host.service._session()['status'] = 'working'
        return result
    monkeypatch.setattr(app_release_artifacts, 'prepare', download)
    with pytest.raises(AppError, match='Finish active work'):
        await host.service.dispatch('maintenance.installationRepairConfirm', args)
    assert not host.launched


async def test_dispatch_unknown_cannot_redispatch_and_status_is_passive(host, monkeypatch):
    _, args = await prepared(host)
    def unknown(folder, plan):
        host.launched.append((folder, plan))
        raise subprocess.TimeoutExpired('systemd-run', 15)
    monkeypatch.setattr(repair, 'launch', unknown)
    operation = (await host.service.dispatch('maintenance.installationRepairConfirm', args))['result']
    assert operation['status'] == 'unknown' and repair.held(host.service)
    await host.service.dispatch('maintenance.installationRepairConfirm', args)
    await host.service.dispatch('maintenance.installationRepairStatus', {'operationId': operation['operationId']})
    assert len(host.launched) == 1


async def test_agent_cannot_supply_user_confirmation(host):
    _, args = await prepared(host)
    with pytest.raises(AppError, match='explicit user confirmation'):
        await host.service.dispatch('maintenance.installationRepairConfirm', args, origin='agent')
    assert not host.launched


@pytest.mark.parametrize('action,args', [
    ('session.create', {}), ('updates.check', {}),
    ('configuration.defaults', {}), ('coordination.send', {'sessionId': 'chat', 'text': 'new work'}),
    ('runtime.control', {'sessionId': 'chat', 'operation': 'status'}),
    ('maintenance.reset', {'parts': ['cache']}),
    ('voice.preview', {'model': 'gpt-realtime-2.1', 'voice': 'alloy'}),
    ('conversation.configure', {'sessionId': 'chat', 'expectedRevision': 0, 'bundle': 'bundle'}),
    ('operations.submit', {'sessionId': 'chat', 'requestId': 'start', 'command': 'true'}),
    ('kernels.create', {'sessionId': 'chat', 'language': 'python'}),
])
async def test_committed_repair_fence_rejects_new_actions(host, action, args):
    _, confirmation = await prepared(host)
    await host.service.dispatch('maintenance.installationRepairConfirm', confirmation)
    with pytest.raises(AppError, match='Installation repair owns'):
        await host.service.dispatch(action, args)
    from amplifier_web.update_drain import input_gate
    with pytest.raises(ValueError, match='Installation repair owns'):
        input_gate(host.service, 'chat', 'input')


async def test_initial_projection_recovers_only_authoritative_receipt_no_replay(host):
    view, args = await prepared(host)
    operation = (await host.service.dispatch('maintenance.installationRepairConfirm', args))['result']
    repair.initialize(host.service)
    assert host.service.state['maintenance']['installationRepair'] == {'preview': None, 'operation': operation}
    assert repair.held(host.service)
    assert len(host.launched) == 1
    operation.update(status='failed', stage='preflight-failed')
    path = repair._folder(host.service, operation['operationId']) / 'operation.json'
    repair._write(path, operation, host.service.data_dir)
    await repair.status(host.service, {'previewId': view['previewId']})
    assert not repair.held(host.service)


def test_private_receipts_reject_symlinks_and_shared_parents(tmp_path):
    service = SimpleNamespace(data_dir=tmp_path)
    with repair._admission(service):
        path = repair.root(service) / 'receipt.json'
        repair._write(path, {'safe': True}, tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    path.unlink()
    path.symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError):
        repair._write(path, {}, tmp_path)
    path.unlink()
    repair.root(service).chmod(0o755)
    with pytest.raises(ValueError):
        repair._write(path, {}, tmp_path)


def test_owner_0755_data_container_keeps_private_evidence_0700(tmp_path):
    tmp_path.chmod(0o755)
    service = SimpleNamespace(data_dir=tmp_path)
    with repair._admission(service):
        path = repair.root(service) / 'receipt.json'
        repair._write(path, {'safe': True}, tmp_path)
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert repair._read(path, tmp_path) == {'safe': True}


def test_fence_keeps_existing_recovery_inspection_and_stop_controls_available():
    for action in ('coordination.context', 'coordination.result', 'assignment.read',
                   'schedule.list', 'runtime.dependencies', 'updates.diagnostics',
                   'configuration.operation.read', 'conversation.stop'):
        assert not repair.blocks_action(action, {})
    assert repair.blocks_action('schedule.create', {})


@pytest.mark.parametrize('broken', [[1], {'operationId': 'a' * 32}, {'status': 'surprise'}])
async def test_malformed_receipt_initializes_unknown_fence_instead_of_crashing(host, broken):
    with repair._admission(host.service):
        folder = repair._folder(host.service, 'a' * 32)
        folder.mkdir(mode=0o700)
        repair._write(folder / 'operation.json', broken, host.service.data_dir)
        repair._write(repair.root(host.service) / 'latest.json',
                      {'operationId': 'a' * 32}, host.service.data_dir)
    repair.initialize(host.service)
    operation = host.service.state['maintenance']['installationRepair']['operation']
    assert operation['status'] == 'unknown' and repair.held(host.service)
    assert not host.launched


async def test_installed_but_unverified_successor_stays_fenced(host):
    _, args = await prepared(host)
    operation = (await host.service.dispatch('maintenance.installationRepairConfirm', args))['result']
    operation.update(status='reinstalled', stage='checks-incomplete', reinstalled=True)
    repair._write(repair._folder(host.service, operation['operationId']) / 'operation.json',
                  operation, host.service.data_dir)
    repair.initialize(host.service)
    assert repair.held(host.service)
    operation.update(status='ready', stage='ready', healthVerified=True, serviceState='running')
    repair._write(repair._folder(host.service, operation['operationId']) / 'operation.json',
                  operation, host.service.data_dir)
    assert not repair.held(host.service)


def test_declared_extras_preserve_missing_optional_distribution(tmp_path, monkeypatch):
    from amplifier_web import app_updates, application_generations
    prefix = tmp_path / 'tool'
    prefix.mkdir()
    (prefix / 'uv-receipt.toml').write_text(
        '[tool]\nrequirements=[{name="amplifier-unified",extras=["native-desktop","tui"]}]\n')
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: [])
    monkeypatch.setattr(application_generations, 'active', lambda home: None)
    extras, digest = repair._declared_extras(prefix, tmp_path)
    assert extras == ['native-desktop', 'tui'] and len(digest) == 64


def test_detached_dispatch_is_manager_owned_and_credentials_never_in_args(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(repair.subprocess, 'run', lambda *a, **kw: calls.append((a, kw)))
    plan = {'basePython': '/stable/python', 'operationId': 'a' * 32,
            'env': {'UV_INDEX_PASSWORD': 'private-secret'}}
    monkeypatch.setattr(repair.sys, 'platform', 'linux')
    repair.launch(tmp_path, plan)
    argv = calls[-1][0][0]
    assert argv[:2] == ['systemd-run', '--user']
    assert '--property=Restart=no' in argv
    assert 'private-secret' not in str(calls)
    monkeypatch.setattr(repair.sys, 'platform', 'darwin')
    repair.launch(tmp_path, plan)
    assert calls[-1][0][0][:2] == ['launchctl', 'submit']
    assert 'private-secret' not in str(calls)