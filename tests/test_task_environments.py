"""Shared task authority and durable DTU admission, with a controlled backend."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from filelock import FileLock
import pytest

from amplifier_web import task_environments as owner
from amplifier_web import task_resources as storage
from amplifier_web.service import AppError
from test_configuration_authority import app, grant, revoke
from test_coordination_generation import agent


def scope(app):
    source = app.state['sessions'][0]
    workspace = next(row for row in app.state['workspaces'] if row['path'] == source['workspace'])
    return {'workspaceId': workspace['id'], 'sessionId': source['id']}


@pytest.fixture
def backend(monkeypatch):
    config = {'python': '/managed/python', 'dockerHost': 'unix:///owned/docker.sock', 'image': owner.APPROVED_IMAGE}
    monkeypatch.setattr(owner, 'configuration', lambda app: (config, 'ready', 'Qualified test backend'))
    calls = []
    def invoke(config, home, row, action, args):
        calls.append((action, dict(args)))
        return {'ok': True, 'universeId': 'owned-universe',
                'snapshot': {'snapshot_commit': 'snapshot', 'archive_sha256': 'archive'},
                'status': {'start': 'running', 'read': row.get('environmentState', 'running'), 'stop': 'stopped', 'remove': 'removed'}.get(action, 'running'),
                'result': {'stdout': 'check passed\n', 'stderr': '', 'exit_code': 0}}
    monkeypatch.setattr(owner, 'invoke', invoke)
    return calls


async def start(app, request='start'):
    return (await app.dispatch('workspace.environments.start', {**scope(app), 'profileId': owner.PROFILE,
        'requestId': request}, command_id='outer-' + request))['result']


async def act(app, action, row, request, **extra):
    return (await app.dispatch('workspace.environments.' + action, {**scope(app), 'id': row['id'],
        'expectedRevision': row['revision'], 'requestId': request, **extra}, command_id='outer-' + request))['result']


async def test_disabled_read_is_passive_and_does_not_hydrate(app, monkeypatch):
    monkeypatch.setattr(app.directory, 'cold_display', lambda *a, **k: pytest.fail('cold history hydration'), raising=False)
    result = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert result['capability']['status'] == 'disabled' and not result['items']
    with pytest.raises(AppError, match='not been enabled'):
        await start(app)
    assert app.operations.requests.list(scope(app)['sessionId']) == []


async def test_start_execute_stop_remove_retain_storage_and_exact_requests(app, backend):
    first = await start(app)
    row = first['resource']
    path = Path(row['retention']['path'])
    (path / 'evidence').write_text('retained')
    assert first['request']['state'] == 'completed' and row['status'] == 'running'
    assert (await start(app))['resource']['id'] == row['id']
    run = await act(app, 'execute', row, 'run', command='python -V')
    assert run['request']['result']['result']['exit_code'] == 0
    assert (await act(app, 'execute', row, 'run', command='python -V'))['request'] == run['request']
    stopped = await act(app, 'stop', run['resource'], 'stop')
    assert stopped['resource']['status'] == 'stopped'
    removed = await act(app, 'remove', stopped['resource'], 'remove')
    assert removed['resource']['status'] == 'removed' and (path / 'evidence').read_text() == 'retained'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']
    assert (await start(app, 'second'))['resource']['id'] != row['id']


async def test_one_environment_per_root_and_no_storage_retirement_bypass(app, backend):
    row = (await start(app))['resource']
    with pytest.raises(AppError, match='already owns'):
        await start(app, 'competing')
    with pytest.raises(AppError, match='dedicated controls'):
        await app.dispatch('workspace.resources.retire', {**scope(app), 'id': row['id'],
            'expectedRevision': row['revision']}, command_id='retire-live')
    page = (await app.dispatch('workspace.resources.read', scope(app)))['result']
    assert not page['items']
    assert len(backend) == 2


async def test_agent_requires_scoped_approval_and_peer_never_borrows_owner(app, backend):
    source, peer = app.state['sessions']
    args = {**scope(app), 'profileId': owner.PROFILE, 'requestId': 'agent-start'}
    with pytest.raises(AppError, match='authority is required'):
        await agent(app, source, 'workspace.environments.start', args, 'agent-start')
    authority = await grant(app, ['test_environments'], ['session'])
    result = await agent(app, source, 'workspace.environments.start', args, 'agent-start')
    assert result['result']['resource']['status'] == 'running'
    with pytest.raises(AppError, match='calling conversation'):
        await agent(app, source, 'workspace.environments.read', {**scope(app), 'sessionId': peer['id']}, 'peer-read')
    row = result['result']['resource']
    await revoke(app, authority)
    with pytest.raises(AppError, match='authority is required'):
        await agent(app, source, 'workspace.environments.stop', {**scope(app), 'id': row['id'],
            'expectedRevision': row['revision'], 'requestId': 'unauthorized-stop'}, 'stop')
    assert [action for action, _ in backend] == ['prepare', 'start']


async def test_timeout_is_retained_unknown_and_same_request_never_runs_again(app, backend, monkeypatch):
    row = (await start(app))['resource']
    def fail(*args):
        raise TimeoutError('lost command acknowledgement')
    monkeypatch.setattr(owner, 'invoke', fail)
    result = await act(app, 'execute', row, 'uncertain', command='long-running-check')
    assert result['request']['state'] == 'outcome_unknown' and result['resource']['status'] == 'unknown'
    monkeypatch.setattr(owner, 'invoke', lambda *args: pytest.fail('replayed unknown command'))
    again = await act(app, 'execute', row, 'uncertain', command='long-running-check')
    assert again['request'] == result['request']
    with pytest.raises(AppError, match='different operation arguments'):
        await act(app, 'execute', row, 'uncertain', command='different-command')


async def test_unready_source_observation_blocks_host_execution(app, backend, monkeypatch):
    await start(app)
    reads = []
    def incomplete(config, home, row, action, args):
        reads.append(action)
        assert action == 'read'
        return {'ok': True, 'universeId': row['universeId'], 'status': 'unknown', 'sourceReady': False,
                'message': 'Source preparation is not confirmed for this exact snapshot.'}
    monkeypatch.setattr(owner, 'invoke', incomplete)
    row = (await app.dispatch('workspace.environments.read', scope(app)))['result']['items'][0]
    assert row['status'] == 'unknown' and row['sourceReady'] is False
    result = await act(app, 'execute', row, 'must-not-run', command='python -m unittest')
    assert result['request']['state'] == 'failed' and reads == ['read']


async def test_start_failure_and_cleanup_failure_keep_ownership(app, backend, monkeypatch):
    def fail(config, home, row, action, args):
        return {'ok': action == 'prepare', 'status': 'unknown', 'universeId': 'maybe-created', 'snapshot': {}}
    monkeypatch.setattr(owner, 'invoke', fail)
    failed = await start(app)
    assert failed['resource']['universeId'] == 'maybe-created' and failed['request']['state'] == 'outcome_unknown'
    with pytest.raises(AppError, match='already owns'):
        await start(app, 'do-not-duplicate')
    row = owner.get(app.data_dir, scope(app)['sessionId'], failed['resource']['id'])
    row = owner.save(app.data_dir, row, environmentState='stopped')
    before = Path(storage.directory(app.data_dir, row))
    (before / 'saved-output').write_text('keep')
    result = await act(app, 'remove', owner.public(app.data_dir, row), 'cleanup')
    assert result['request']['state'] == 'outcome_unknown'
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
    assert (before / 'saved-output').read_text() == 'keep'


async def test_stale_revision_and_active_owner_lock_cannot_clobber_state(app, backend):
    row = (await start(app))['resource']
    with pytest.raises(AppError, match='changed'):
        await act(app, 'stop', {**row, 'revision': 1}, 'stale')
    lock = app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock')
    with FileLock(str(lock)):
        blocked = await act(app, 'stop', row, 'blocked')
    assert blocked['request']['state'] == 'failed'
    current = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert current['environmentState'] == 'running' and current['revision'] == row['revision']
    assert len(backend) == 2


async def test_ownerless_pending_request_becomes_unknown_without_replay(app, backend, monkeypatch):
    row = (await start(app))['resource']
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.execute', {'requestId': 'interrupted'}, {})
    app.operations.requests.finish(request, 'admitting', resourceId=row['id'])
    owner.save(app.data_dir, raw, pendingRequest='interrupted')
    monkeypatch.setattr(owner, 'configuration', lambda app: (None, 'disabled', 'disabled'))
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert page['items'][0]['status'] == 'unknown'
    assert page['items'][0]['lastOperation']['state'] == 'outcome_unknown'
    assert len(backend) == 2


async def test_failed_preparation_can_be_removed_without_backend_teardown(app, backend, monkeypatch):
    monkeypatch.setattr(owner, 'invoke', lambda *args: {'ok': False, 'code': 'not-a-repository'})
    result = await start(app)
    assert result['resource']['status'] == 'not-started' and result['request']['state'] == 'failed'
    monkeypatch.setattr(owner, 'invoke', lambda *args: pytest.fail('never-launched environment called backend'))
    removed = await act(app, 'remove', result['resource'], 'retire-preparation')
    assert removed['resource']['status'] == 'removed'
    assert Path(removed['resource']['retention']['path']).exists()


async def test_stopped_task_during_reservation_leaves_recoverable_local_owner(app, backend, monkeypatch):
    source = app.state['sessions'][0]
    original = owner.prepare
    entered, release = threading.Event(), threading.Event()
    def waiting(*args):
        row = original(*args)
        entered.set()
        assert release.wait(5)
        return row
    monkeypatch.setattr(owner, 'prepare', waiting)
    task = asyncio.create_task(start(app, 'delayed-reservation'))
    assert await asyncio.to_thread(entered.wait, 5)
    source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    release.set()
    with pytest.raises(AppError, match='moved or stopped'):
        await task
    result = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    row = result['items'][0]
    assert row['status'] == 'not-started'
    assert (await act(app, 'remove', row, 'retire-reservation'))['resource']['status'] == 'removed'
    assert backend == []


async def test_managed_uninstall_preserves_backend_until_all_resources_retired(app):
    from amplifier_web.smart_tools import SmartToolsManager
    manager = app.smart_tools = SmartToolsManager(app)
    identity = 'd' * 24
    directory = manager.root / 'installs' / identity
    directory.mkdir(parents=True)
    (directory / 'retained-core').write_text('needed for recovery')
    manager.state['installations'] = [{'id': identity}]
    row = owner.prepare(app.data_dir, 'owner', 'workspace', {'sessionId': 'owner'}, '/source', 'key', 'request', identity)
    with pytest.raises(ValueError, match='still owns test environments'):
        await manager.uninstall(identity)
    assert (directory / 'retained-core').read_text() == 'needed for recovery'
    owner.save(app.data_dir, row, status='retired', environmentState='removed')
    assert (await manager.uninstall(identity))['status'] == 'uninstalled'
    assert not directory.exists()


async def test_stop_or_revocation_while_waiting_prevents_backend_start(app, backend, monkeypatch):
    source = app.state['sessions'][0]
    authority = await grant(app, ['test_environments'], ['session'])
    entered, release = threading.Event(), threading.Event()
    original = storage.allocate
    def waiting(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(storage, 'allocate', waiting)
    args = {**scope(app), 'profileId': owner.PROFILE, 'requestId': 'delayed'}
    task = asyncio.create_task(agent(app, source, 'workspace.environments.start', args, 'delayed'))
    assert await asyncio.to_thread(entered.wait, 5)
    await revoke(app, authority)
    release.set()
    result = await task
    assert result['result']['request']['state'] == 'failed' and backend == []


def test_operator_config_is_exact_private_managed_core(app, tmp_path, monkeypatch):
    pin = 'a' * 40
    monkeypatch.setattr(owner, 'DTU_COMMIT', pin)
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    app.smart_tools = SimpleNamespace(state={'installations': []}, close=AsyncMock())
    identity = 'b' * 24
    python = app.data_dir / 'smart-tools' / 'installs' / identity / 'venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_text('managed interpreter')
    app.smart_tools.state['installations'].append({'id': identity, 'commit': pin, 'repository': owner.REPOSITORY,
        'status': 'installed', 'extras': [], 'python': str(python)})
    target = app.data_dir / 'test-environments.json'
    value = {'enabled': True, 'installationId': identity, 'commit': pin, 'dockerHost': 'unix:///var/run/docker.sock'}
    target.write_text(json.dumps(value)); target.chmod(0o600)
    assert owner.configuration(app)[1] == 'ready'
    from amplifier_web import incus_environment_worker
    incus = {'qualified': 'explicit fixture'}
    monkeypatch.setattr(incus_environment_worker, 'validate_configuration', lambda v: {**v, 'validated': True})
    alternate = {key: item for key, item in value.items() if key != 'dockerHost'}
    target.write_text(json.dumps({**alternate, 'backend': 'incus', 'incus': incus}))
    configured, status, _ = owner.configuration(app)
    assert status == 'ready' and configured['incus']['validated'] and configured['python'] == str(python)
    target.write_text(json.dumps({**value, 'backend': 'incus', 'incus': incus}))
    assert owner.configuration(app)[1] == 'unavailable'  # No mixed/implicit backend selection.
    for bad in [{**value, 'image': 'arbitrary-image'}, {**value, 'commit': 'c' * 40}, {**value, 'dockerHost': 'tcp://remote:2375'}]:
        target.write_text(json.dumps(bad))
        assert owner.configuration(app)[1] == 'unavailable'
    target.write_text(json.dumps(value)); target.chmod(0o644)
    assert owner.configuration(app)[1] == 'unavailable'
    target.chmod(0o600)
    target.write_text('[]')
    assert owner.configuration(app)[1] == 'unavailable'
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', False)
    monkeypatch.delattr(owner.os, 'getuid')
    assert owner.configuration(app)[1] == 'unavailable'
