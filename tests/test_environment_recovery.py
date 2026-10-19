"""Exact-owner read-only recovery keeps uncertain effects and backend boundaries."""
import asyncio
import threading

from filelock import FileLock
import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_recovery as recovery
from amplifier_web.service import AppError
from test_configuration_authority import app, grant, revoke
from test_coordination_generation import agent
from test_task_environments import backend, scope, start, act
from test_test_queue import queue, submit, drive, record


def observation(row, status='absent', **changes):
    proof = {'backendIdentity': row.get('backendIdentity', {'backend': 'docker', 'daemon': 'owned-daemon'}),
             'rootOwner': row['sessionId'], 'operationId': row['launchOperationId'],
             'universeId': row['universeId'], 'ownership': 'absent' if status == 'absent' else 'matched',
             'absenceConfirmed': status == 'absent', 'admissionSettled': True, **changes}
    return {'ok': True, 'status': status, 'universeId': row['universeId'], 'observation': proof}


def observer(monkeypatch, status='absent', **changes):
    calls = []
    def inspect(config, home, row, action, args):
        calls.append(action)
        assert action == 'reconcile'
        return observation(row, status, **changes)
    monkeypatch.setattr(owner, 'invoke', inspect)
    return calls


async def uncertain(app, monkeypatch):
    row = (await start(app))['resource']
    def lost(*args):
        raise TimeoutError('lost acknowledgement')
    monkeypatch.setattr(owner, 'invoke', lost)
    result = await act(app, 'execute', row, 'uncertain-command', command='check-once')
    return result['resource'], result['request']


async def test_absence_releases_owner_but_preserves_original_unknown_and_never_replays(app, backend, monkeypatch):
    original = owner.invoke
    row, unknown = await uncertain(app, monkeypatch)
    calls = observer(monkeypatch)
    result = await act(app, 'reconcile', row, 'inspect-absence')
    assert result['request']['state'] == 'completed'
    assert result['resource']['status'] == 'removed'
    assert result['resource']['lastOperation'] == unknown
    assert result['resource']['lastRecovery'] == result['request']
    assert app.operations.requests.read(row['sessionId'], unknown['requestId']) == unknown
    duplicate = await act(app, 'reconcile', row, 'inspect-absence')
    assert duplicate['request'] == result['request'] and calls == ['reconcile']
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert page['items'][0]['status'] == 'removed' and page['items'][0]['lastOperation'] == unknown
    assert calls == ['reconcile']
    monkeypatch.setattr(owner, 'invoke', original)
    assert (await start(app, 'after-confirmed-absence'))['resource']['id'] != row['id']


@pytest.mark.parametrize('state', ['running', 'stopped', 'unknown'])
async def test_inspection_never_stops_removes_or_repeats_command(app, backend, monkeypatch, state):
    row, unknown = await uncertain(app, monkeypatch)
    calls = observer(monkeypatch, state)
    result = await act(app, 'reconcile', row, 'inspect-'+state)
    assert result['request']['state'] == 'completed'
    assert result['resource']['status'] == state
    assert result['resource']['lastOperation'] == unknown
    assert calls == ['reconcile']
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


@pytest.mark.parametrize('changes', [
    {'rootOwner': 'foreign-root'}, {'operationId': 'foreign-launch'}, {'universeId': 'foreign-physical'},
    {'backendIdentity': {'backend': 'docker', 'daemon': 'another-daemon'}},
    {'ownership': 'matched'}, {'absenceConfirmed': False}, {'admissionSettled': False},
])
async def test_foreign_or_unsettled_absence_never_retires_owner(app, backend, monkeypatch, changes):
    row, unknown = await uncertain(app, monkeypatch)
    calls = observer(monkeypatch, **changes)
    result = await act(app, 'reconcile', row, 'invalid-absence')
    assert result['request']['state'] == 'failed'
    assert result['resource']['lastOperation'] == unknown
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
    assert calls == ['reconcile']


async def test_permission_denied_is_not_absence(app, backend, monkeypatch):
    row = (await start(app))['resource']
    monkeypatch.setattr(owner, 'invoke', lambda *a: {'ok': False, 'code': 'permission_denied', 'status': 'unknown'})
    result = await act(app, 'reconcile', row, 'denied-inspection')
    assert result['request']['state'] == 'failed'
    assert result['resource']['status'] == 'running'
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


@pytest.mark.parametrize('proof', [False, True])
async def test_legacy_identity_needs_durable_launch_binding(app, backend, monkeypatch, proof):
    public = (await start(app))['resource']
    row = owner.get(app.data_dir, public['sessionId'], public['id'])
    row = owner.save(app.data_dir, row, backendIdentity=None)
    observer(monkeypatch, backendIdentity={'backend': 'docker', 'daemon': 'owned-daemon'},
             **({'backendIdentitySource': 'retained-launch'} if proof else {}))
    result = await act(app, 'reconcile', owner.public(app.data_dir, row), 'legacy-inspect')
    assert result['request']['state'] == ('completed' if proof else 'failed')
    assert (result['resource']['status'] == 'removed') is proof


async def test_current_generation_and_owner_lock_guard_recovery(app, backend, monkeypatch):
    row = (await start(app))['resource']
    calls = observer(monkeypatch)
    with pytest.raises(AppError, match='changed'):
        await act(app, 'reconcile', {**row, 'revision': 1}, 'stale-inspection')
    lock = app.data_dir / 'workspace-resources' / ('environment-'+row['id']+'.lock')
    with FileLock(str(lock)):
        denied = await act(app, 'reconcile', row, 'locked-inspection')
    assert denied['request']['state'] == 'failed' and not calls
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['revision'] == row['revision']
    entered, release = threading.Event(), threading.Event()
    def paused(config, home, raw, action, args):
        assert action == 'reconcile'
        entered.set()
        assert release.wait(5)
        return observation(raw)
    monkeypatch.setattr(owner, 'invoke', paused)
    task = asyncio.create_task(act(app, 'reconcile', row, 'stopped-inspection'))
    assert await asyncio.to_thread(entered.wait, 5)
    app.state['sessions'][0]['interruptionRevision'] = 88
    release.set()
    result = await task
    assert result['request']['state'] == 'failed'
    assert result['resource']['status'] == 'running'
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


@pytest.mark.parametrize('persistent', [False, True])
async def test_committed_recovery_survives_projection_failure_then_read_repairs(app, backend, monkeypatch, persistent):
    row, unknown = await uncertain(app, monkeypatch)
    calls = observer(monkeypatch)
    save, failures = owner.save, 0
    def broken_projection(home, resource, **changes):
        nonlocal failures
        if changes.get('lastRecovery', {}).get('state') == 'completed' and (persistent or failures == 0):
            failures += 1
            raise OSError('projection unavailable after durable inspection')
        return save(home, resource, **changes)
    monkeypatch.setattr(owner, 'save', broken_projection)
    result = await act(app, 'reconcile', row, 'durable-inspection')
    assert result['request']['state'] == 'completed'
    assert result['resource']['lastOperation'] == unknown and calls == ['reconcile']
    assert failures == (2 if persistent else 1)
    if persistent:
        pending = (await app.dispatch('workspace.environments.read', scope(app)))['result']['items'][0]
        assert pending['observation']['status'] == 'unverified' and pending['lastOperation'] == unknown
        assert calls == ['reconcile']
    monkeypatch.setattr(owner, 'save', save)
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    assert page['items'][0]['status'] == 'removed' and page['items'][0]['lastOperation'] == unknown
    assert page['items'][0]['lastRecovery'] == result['request']
    assert calls == ['reconcile']


async def test_snapshot_precedes_effect_and_missing_prepare_identity_never_launches(app, backend, monkeypatch):
    original = owner.invoke
    def inspect(config, home, raw, action, args):
        assert raw['backendConfigRetained']
        assert owner.resolve_configuration(app, raw, config) == config
        result = original(config, home, raw, action, args)
        if action == 'prepare':
            result.pop('backendIdentity')
        return result
    monkeypatch.setattr(owner, 'invoke', inspect)
    result = await start(app)
    assert result['request']['state'] == 'failed'
    assert result['resource']['retention']['mode'] == 'files'
    assert [action for action, _ in backend] == ['prepare']
    raw = owner.get(app.data_dir, result['resource']['sessionId'], result['resource']['id'])
    assert not raw.get('backendAdmitted')


async def test_changed_backend_cleanup_uses_retained_config_but_new_execution_does_not(app, backend, monkeypatch):
    row = (await start(app))['resource']
    old = owner.configuration(app)[0]
    new = {**old, 'dockerHost': 'unix:///new/docker.sock'}
    monkeypatch.setattr(owner, 'configuration', lambda app: (new, 'ready', 'new backend'))
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda app, value: (old, 'ready', 'old qualified'))
    seen, invoke = [], owner.invoke
    def exact(config, home, resource, action, args):
        seen.append((action, config))
        return invoke(config, home, resource, action, args)
    monkeypatch.setattr(owner, 'invoke', exact)
    page = (await app.dispatch('workspace.environments.read', scope(app)))['result']
    row = page['items'][0]
    assert row['recovery']['available'] is True
    rejected = await act(app, 'execute', row, 'wrong-backend-command', command='do-not-run')
    assert rejected['request']['state'] == 'failed'
    assert seen == [('read', old)]
    stopped = await act(app, 'stop', row, 'stop-old')
    removed = await act(app, 'remove', stopped['resource'], 'remove-old')
    assert removed['resource']['status'] == 'removed'
    assert seen == [('read', old), ('stop', old), ('remove', old)]


@pytest.mark.parametrize('verified', [False, True])
async def test_only_verified_removal_publishes_retained_file_accounting(app, backend, monkeypatch, verified):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'before-retention'))['resource']
    invoke = owner.invoke
    accounting = {'archiveBytes': 24, 'sourceBytes': 100, 'totalBytes': 124,
                  'format': 'files-tar', 'policy': 'files', 'verified': verified}
    def export(config, home, resource, action, args):
        return {**invoke(config, home, resource, action, args), 'retention': accounting}
    monkeypatch.setattr(owner, 'invoke', export)
    result = await act(app, 'remove', row, 'retained-removal')
    assert (result['resource']['retention'].get('accounting') == accounting) is verified
    if not verified:
        assert 'accounting' not in result['resource']['retention']


async def test_reconcile_agent_authority_root_peer_and_revocation(app, backend, monkeypatch):
    row = (await start(app))['resource']
    source, peer = app.state['sessions']
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'], 'requestId': 'agent-inspect'}
    calls = observer(monkeypatch, 'running')
    with pytest.raises(AppError, match='authority is required'):
        await agent(app, source, 'workspace.environments.reconcile', args, 'no-grant')
    authority = await grant(app, ['test_environments'], ['session'])
    with pytest.raises(AppError, match='calling conversation'):
        await agent(app, source, 'workspace.environments.reconcile', {**args, 'sessionId': peer['id']}, 'peer-inspect')
    result = await agent(app, source, 'workspace.environments.reconcile', args, 'with-grant')
    assert result['result']['request']['state'] == 'completed' and calls == ['reconcile']
    await revoke(app, authority)
    with pytest.raises(AppError, match='authority is required'):
        await agent(app, source, 'workspace.environments.reconcile', {**args, 'requestId': 'revoked'}, 'revoked')
    assert calls == ['reconcile']


async def test_queue_cleanup_survives_backend_change_without_replaying_check(app, queue, backend, monkeypatch):
    old, invoke = owner.configuration(app)[0], owner.invoke
    new = {**old, 'dockerHost': 'unix:///next/backend.sock'}
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda *args: (old, 'ready', 'old qualified'))
    seen = []
    def change_after_check(config, home, row, action, args):
        seen.append((action, config))
        result = invoke(config, home, row, action, args)
        if action == 'execute':
            monkeypatch.setattr(owner, 'configuration', lambda app: (new, 'ready', 'new backend'))
        return result
    monkeypatch.setattr(owner, 'invoke', change_after_check)
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert [action for action, _ in seen] == ['prepare', 'start', 'execute', 'stop', 'remove']
    assert all(config == old for _, config in seen)


async def test_queue_restart_uses_retained_backend_and_preserves_saved_check(app, queue, backend, monkeypatch):
    old = owner.configuration(app)[0]
    cleanup = queue.cleanup
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(queue, 'cleanup', interrupted)
    await submit(app)
    await queue.tick()
    await asyncio.gather(*queue.tasks.values(), return_exceptions=True)
    receipt = record(queue, app)
    assert receipt['job']['check']['state'] == 'completed'
    monkeypatch.setattr(queue, 'cleanup', cleanup)
    monkeypatch.setattr(owner, 'configuration', lambda app: (None, 'disabled', 'new config disabled'))
    monkeypatch.setattr(owner, 'SUPPORTED_HOST', True)
    monkeypatch.setattr(owner, 'validate_configuration_value', lambda *args: (old, 'ready', 'old qualified'))
    await queue.recover(receipt['requestId'])
    assert record(queue, app)['job']['status'] == 'passed'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'read', 'stop', 'remove']


@pytest.mark.parametrize('pinned', [False, True])
async def test_older_incus_explicit_config_pin_establishes_identity_without_guessing(app, backend, monkeypatch, pinned):
    public = (await start(app))['resource']
    raw = owner.get(app.data_dir, public['sessionId'], public['id'])
    incus = {'project': 'original-project', **({'daemonFingerprint': 'a' * 64} if pinned else {})}
    old = {'backend': 'incus', 'incus': incus, 'python': '/old/python'}
    raw = owner.save(app.data_dir, raw, backendIdentity=None, backendConfigRetained=False, backendKey=owner.config_key(old))
    monkeypatch.setattr(owner, 'configuration', lambda app: (old, 'ready', 'exact old config'))
    def inspect(config, home, row, action, args):
        assert action == 'reconcile'
        if pinned:
            assert row['backendIdentity'] == {'backend': 'incus', 'daemon': 'a' * 64, 'project': 'original-project'}
            return observation(row)
        assert not row.get('backendIdentity')
        return observation(row, backendIdentity={'backend': 'incus', 'daemon': 'a' * 64, 'project': 'original-project'})
    monkeypatch.setattr(owner, 'invoke', inspect)
    result = await act(app, 'reconcile', owner.public(app.data_dir, raw), 'legacy-pinned-inspection')
    assert result['request']['state'] == ('completed' if pinned else 'failed')
    assert (result['resource']['status'] == 'removed') is pinned


async def test_queued_roots_survive_real_cold_restart_and_cancel_stays_cancelled(app, queue, backend, monkeypatch):
    from amplifier_web.service import AppService
    from test_configuration_authority import Runtime
    roots = [*app.state['sessions'], app._new_session({'title': 'Third root'})]
    app.state['sessions'] = roots
    app._publish()
    for source in roots:
        workspace = next(w for w in app.state['workspaces'] if w['path'] == source['workspace'])
        await app.dispatch('workspace.tests.submit', {'workspaceId': workspace['id'], 'sessionId': source['id'],
            'requestId': 'restart-pending', 'profileId': owner.PROFILE, 'command': 'true', 'notify': False})
        # Old releases saved missing fields before cold metadata materialized them.
        saved = queue.get(queue.identity(source['id'], 'restart-pending'))
        saved['job']['binding'] = [source['workspace'], None, None, None]
        queue.save(saved)
    last = roots[-1]
    await app.dispatch('workspace.tests.cancel', {'workspaceId': workspace['id'], 'sessionId': last['id'], 'requestId': 'restart-pending'})
    home, default = app.data_dir, app.default_workspace
    await app.close()
    restored = AppService(home, Runtime(), workspace=default)
    monkeypatch.setattr(restored.test_queue, 'start', lambda: None)
    try:
        from amplifier_web.history_directory import summary
        # Native/cold directory owners use this production metadata projection;
        # selected app-owned records can still be resident after this restart.
        for source in roots:
            cold = restored._session(source['id'], hydrate=False)
            cold.update(summary(cold))
        cold = restored._session(roots[0]['id'], hydrate=False)
        assert cold['workingDirectory'] == roots[0]['workspace'] and cold['executionRevision'] == 0
        await drive(restored.test_queue)
        await drive(restored.test_queue)  # The controlled backend admits one per observation.
        statuses = [restored.test_queue.get(restored.test_queue.identity(row['id'], 'restart-pending'))['job']['status'] for row in roots]
        assert statuses == ['passed', 'passed', 'cancelled']
        assert [action for action, _ in backend].count('execute') == 2
    finally:
        await restored.close()


@pytest.mark.parametrize('field,value', [('workingDirectory', '/different'), ('workspace', '/other'), ('executionRevision', 1), ('interruptionRevision', 1)])
async def test_normalized_defaults_never_hide_real_move_or_stop(app, queue, backend, field, value):
    await submit(app)
    receipt = record(queue, app)
    source = app.state['sessions'][0]
    receipt['job']['binding'] = [source['workspace'], None, None, None]
    queue.save(receipt)
    source[field] = value
    assert queue.continuation_guard(receipt)
    await drive(queue)
    assert queue.get(receipt['requestId'])['job']['status'] == 'cancelled' and not backend


async def test_remove_full_choice_is_exact_and_never_substitutes_original_backend(app, backend, monkeypatch):
    config = {'python': '/managed/python', 'backend': 'incus',
              'incus': {'project': 'original', 'daemonFingerprint': 'a' * 64}}
    monkeypatch.setattr(owner, 'configuration', lambda app: (config, 'ready', 'qualified Incus fixture'))
    invoke = owner.invoke
    def incus(configured, home, row, action, args):
        assert configured == config
        result = invoke(configured, home, row, action, args)
        if action == 'prepare':
            result['backendIdentity'] = {'backend': 'incus', 'daemon': 'a' * 64, 'project': 'original'}
        return result
    monkeypatch.setattr(owner, 'invoke', incus)
    row = (await start(app))['resource']
    assert row['retention']['fullAvailable'] and row['retention']['mode'] == 'files'
    row = (await act(app, 'stop', row, 'stop-before-full'))['resource']
    removed = await act(app, 'remove', row, 'full-removal', retentionMode='full')
    assert removed['request']['state'] == 'completed'
    again = await act(app, 'remove', row, 'full-removal', retentionMode='full')
    assert again['request'] == removed['request']
    with pytest.raises(AppError, match='different operation arguments'):
        await act(app, 'remove', row, 'full-removal', retentionMode='files')
    removals = [args for action, args in backend if action == 'remove']
    assert len(removals) == 1 and removals[0]['retentionMode'] == 'full'
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert raw['backendKey'] == owner.config_key(config)


async def test_docker_full_override_is_rejected_before_receipt_or_backend_effect(app, backend):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop-before-unsupported-full'))['resource']
    assert row['retention']['fullAvailable'] is False
    with pytest.raises(AppError, match='only.*Incus'):
        await act(app, 'remove', row, 'unsupported-full', retentionMode='full')
    with pytest.raises(ValueError):
        app.operations.requests.read(row['sessionId'], 'unsupported-full')
    assert [action for action, _ in backend] == ['prepare', 'start', 'stop']


@pytest.mark.parametrize('action,expected', [('remove', 'full'), ('read', 'files'), ('start', 'files')])
def test_real_helper_receives_identity_and_retention_override_only_for_remove(tmp_path, monkeypatch, action, expected):
    import sys
    from test_test_environment_worker import allocated
    home, row = allocated(tmp_path)
    row['backendIdentity'] = {'backend': 'incus', 'daemon': 'original-daemon', 'project': 'owned'}
    monkeypatch.setattr(owner, '__file__', str(tmp_path / 'owner.py'))
    (tmp_path / 'incus_environment_worker.py').write_text(
        "import json,sys\np=json.load(sys.stdin)\nprint(json.dumps({'ok':True,'mode':p['retentionMode'],'identity':p['expectedBackendIdentity'],'owner':p['rootOwner'],'operationId':p['operationId']}))\n")
    result = owner.invoke({'python': sys.executable, 'backend': 'incus', 'incus': {'project': 'owned'}},
                          home, row, action, {'requestId': 'exact-request', 'retentionMode': 'full'})
    assert result == {'ok': True, 'mode': expected, 'identity': row['backendIdentity'],
                      'owner': row['sessionId'], 'operationId': row['launchOperationId']}


async def test_finish_cleanup_preserves_original_unknown_and_releases_owner(app, backend, monkeypatch):
    original = owner.invoke
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    monkeypatch.setattr(owner, 'invoke', lambda *args: {'ok': False, 'status': 'unknown'})
    failed = await act(app, 'remove', row, 'lost-removal')
    calls = observer(monkeypatch, 'stopped')
    row = (await act(app, 'reconcile', failed['resource'], 'inspect-stopped'))['resource']
    def finish(config, home, current, action, args):
        assert action == 'finishCleanup'
        calls.append(action)
        return {'ok': True, 'status': 'removed', 'retention': {'verified': True, 'archiveOnly': True}}
    monkeypatch.setattr(owner, 'invoke', finish)
    result = await act(app, 'finishCleanup', row, 'finish-cleanup', previousRequestId='lost-removal')
    assert result['request']['state'] == 'completed' and result['resource']['status'] == 'removed'
    assert result['resource']['previousCleanup'] == failed['request']
    assert app.operations.requests.read(row['sessionId'], 'lost-removal') == failed['request']
    duplicate = await act(app, 'finishCleanup', row, 'finish-cleanup', previousRequestId='lost-removal')
    assert duplicate['request'] == result['request'] and calls == ['reconcile','finishCleanup']
    monkeypatch.setattr(owner, 'invoke', original)
    assert (await start(app, 'next-check'))['resource']['id'] != row['id']


async def test_cleanup_recovery_rejects_unrelated_request_before_effect(app, backend):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'stop'))['resource']
    result = await act(app, 'finishCleanup', row, 'bad-recovery', previousRequestId='stop')
    assert result['request']['state'] == 'failed'
    assert [name for name, _ in backend] == ['prepare', 'start', 'stop']
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
