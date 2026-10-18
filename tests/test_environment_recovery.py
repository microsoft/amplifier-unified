"""Exact-owner read-only recovery keeps uncertain effects and backend boundaries."""
import asyncio
import copy
import json
from pathlib import Path
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
from test_test_environment_worker import library, Failure, worker


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


@pytest.mark.parametrize('evidence', ['missing', 'forged', 'stale'])
async def test_legacy_remove_host_journal_preserves_unknown_with_untrusted_files(app, backend, library, monkeypatch, evidence):
    """Host preservation only, NOT a phase-proof consumer/validator test."""
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'legacy-stop'))['resource']
    lib, effects = library
    folder = Path(row['retention']['path'])
    (folder / 'source.tar').write_bytes(b'synthetic snapshot')
    original = owner.invoke
    destroyed = []
    def lost_destroy(identity, **kwargs):
        destroyed.append(identity)  # External effect may have happened.
        raise Failure('lost acknowledgement')
    lib.destroy = lost_destroy
    monkeypatch.setattr(worker, 'docker_json', lambda *args: {'ID':'owned-daemon'})
    def invoke(config, home, raw, action, args):
        if action == 'remove':
            value = worker.execute_call({
                'action':'remove', 'root':str(folder), 'rootOwner':raw['sessionId'],
                'operationId':raw['launchOperationId'], 'universeId':raw['universeId'],
                'expectedBackendIdentity':raw['backendIdentity'],
                'dockerHost':config['dockerHost'], 'requestId':args['requestId'],
                'resourceId':raw['id'], 'resourceRevision':raw['revision'],
                'admissionRevision':args['expectedRevision'],
                'backendKey':raw['backendKey'], 'snapshot':raw['snapshot']})
            # Model the older helper's published envelope, not a new phase proof.
            return {key:item for key, item in value.items() if key != 'removal'}
        if action == 'reconcile':
            return observation(raw, 'stopped')  # Presence is not effect history.
        return original(config, home, raw, action, args)
    monkeypatch.setattr(owner, 'invoke', invoke)
    failed = await act(app, 'remove', row, 'legacy-remove')
    unknown = copy.deepcopy(failed['request'])
    assert unknown['state'] == 'outcome_unknown' and destroyed == ['owned-universe']
    directory, = folder.glob('retained-*')
    if evidence != 'missing':
        (directory / 'untrusted-phase.json').write_text(json.dumps({
            'phase':'manifest-complete', 'destroyEffect':'not_dispatched', 'verified':True,
            'requestId':'legacy-remove' if evidence == 'forged' else 'older-request',
            'resourceRevision':row['revision'] if evidence == 'forged' else 1}))
    before = {p.name:(p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    assert len(list(directory.glob('*.tar'))) == 2
    result = await act(app, 'reconcile', failed['resource'], 'legacy-observe')
    assert result['request']['state'] == 'completed' and result['resource']['status'] == 'stopped'
    assert result['resource']['lastOperation'] == unknown
    assert 'accounting' not in result['resource']['retention']
    assert app.operations.requests.read(row['sessionId'], 'legacy-remove') == unknown
    assert (await act(app, 'remove', row, 'legacy-remove'))['request'] == unknown
    assert destroyed == ['owned-universe'] and sum(c[0] == 'export' for c in effects) == 2
    assert before == {p.name:(p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
    with pytest.raises(AppError, match='already owns an environment'):
        await start(app, 'not-a-new-removal-attempt')


@pytest.mark.parametrize('change', ['root', 'launch', 'physical', 'backend', 'revision'])
async def test_legacy_remove_reconciliation_cannot_change_bound_owner(app, backend, monkeypatch, change):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'bound-stop'))['resource']
    def lost(*args):
        raise TimeoutError('synthetic original removal unknown')
    monkeypatch.setattr(owner, 'invoke', lost)
    failed = await act(app, 'remove', row, 'bound-remove')
    unknown = copy.deepcopy(failed['request'])
    changes = {
        'root': {'rootOwner':'other-root'}, 'launch': {'operationId':'other-launch'},
        'physical': {'universeId':'other-guest'},
        'backend': {'backendIdentity':{'backend':'docker', 'daemon':'other-daemon'}},
        'revision': {},
    }[change]
    calls = observer(monkeypatch, 'stopped', **changes)
    if change == 'revision':
        with pytest.raises(AppError, match='changed'):
            await act(app, 'reconcile', row, 'stale-remove-observation')
        assert calls == []
    else:
        result = await act(app, 'reconcile', failed['resource'], 'wrong-remove-observation')
        assert result['request']['state'] == 'failed' and calls == ['reconcile']
    assert app.operations.requests.read(row['sessionId'], 'bound-remove') == unknown
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


async def test_duplicate_concurrent_remove_and_reconcile_do_not_repeat_destroy(app, backend, library, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'concurrent-stop'))['resource']
    entered, release = threading.Event(), threading.Event()
    lib, effects = library
    folder = Path(row['retention']['path'])
    (folder / 'source.tar').write_bytes(b'synthetic snapshot')
    calls, destroyed = [], []
    def destroy(identity, **kwargs):
        destroyed.append(identity)
        entered.set()
        assert release.wait(5)
        raise Failure('synthetic lost destroy acknowledgement')
    lib.destroy = destroy
    monkeypatch.setattr(worker, 'docker_json', lambda *args: {'ID':'owned-daemon'})
    def invoke(config, home, raw, action, args):
        calls.append((action, args['requestId']))
        assert action == 'remove'
        return worker.execute_call({
            'action':'remove', 'root':str(folder), 'rootOwner':raw['sessionId'],
            'operationId':raw['launchOperationId'], 'universeId':raw['universeId'],
            'expectedBackendIdentity':raw['backendIdentity'], 'dockerHost':config['dockerHost'],
            'requestId':args['requestId'], 'resourceId':raw['id'],
            'resourceRevision':raw['revision'], 'admissionRevision':args['expectedRevision'],
            'backendKey':raw['backendKey'], 'snapshot':raw['snapshot']})
    monkeypatch.setattr(owner, 'invoke', invoke)
    task = asyncio.create_task(act(app, 'remove', row, 'once-remove'))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        duplicate = await act(app, 'remove', row, 'once-remove')
        assert duplicate['request']['state'] == 'admitting'
        current = owner.get(app.data_dir, row['sessionId'], row['id'])
        competing = await act(app, 'reconcile', owner.public(app.data_dir, current), 'competing-observation')
        assert competing['request']['state'] == 'failed'
        assert owner.get(app.data_dir, row['sessionId'], row['id']) == current
        competing_remove = await act(app, 'remove', owner.public(app.data_dir, current), 'competing-remove')
        assert competing_remove['request']['state'] == 'failed'
        assert owner.get(app.data_dir, row['sessionId'], row['id']) == current
    finally:
        release.set()
        settled = await task
    unknown = settled['request']
    assert unknown['state'] == 'outcome_unknown'
    assert (await act(app, 'remove', row, 'once-remove'))['request'] == unknown
    assert calls == [('remove', 'once-remove')]
    assert destroyed == ['owned-universe'] and sum(c[0] == 'export' for c in effects) == 2
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'


async def test_new_remove_identity_cannot_bypass_a_legacy_unknown_removal(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'held-stop'))['resource']
    def lost(*args):
        raise TimeoutError('synthetic removal may have run')
    monkeypatch.setattr(owner, 'invoke', lost)
    failed = await act(app, 'remove', row, 'held-original-remove')
    unknown = copy.deepcopy(failed['request'])
    observed = observer(monkeypatch, 'stopped')
    reconciled = await act(app, 'reconcile', failed['resource'], 'held-inspection')
    assert reconciled['resource']['status'] == 'stopped' and observed == ['reconcile']
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    helpers = []
    def no_effect(*args):
        helpers.append(args)
        return {'ok':False, 'status':'unknown'}
    monkeypatch.setattr(owner, 'invoke', no_effect)
    with pytest.raises(AppError, match='original removal'):
        await act(app, 'remove', reconciled['resource'], 'not-a-recovery-token')
    assert helpers == []
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert app.operations.requests.read(row['sessionId'], 'held-original-remove') == unknown
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(row['sessionId'], 'not-a-recovery-token')


@pytest.mark.parametrize('later_receipts', [0, 105])
async def test_original_unknown_survives_later_stop_and_bounded_history_page(app, backend, monkeypatch, later_receipts):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'original-stop'))['resource']
    original_invoke = owner.invoke
    def lost(*args):
        raise TimeoutError('synthetic uncertain removal')
    monkeypatch.setattr(owner, 'invoke', lost)
    failed = await act(app, 'remove', row, 'original-unknown-remove')
    unknown = copy.deepcopy(failed['request'])
    observer(monkeypatch, 'stopped')
    observed = await act(app, 'reconcile', failed['resource'], 'original-observe')
    monkeypatch.setattr(owner, 'invoke', original_invoke)
    later = await act(app, 'stop', observed['resource'], 'later-stop')
    assert later['resource']['lastOperation']['action'] == 'workspace.environments.stop'
    for index in range(later_receipts):
        request, _ = app.operations.requests.begin(row['sessionId'], 'fixture.observation',
            {'requestId':f'later-{index}'}, {})
        app.operations.requests.finish(request, 'completed')
    if later_receipts:
        assert all(r['requestId'] != unknown['requestId'] for r in app.operations.requests.list(row['sessionId']))
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    helpers = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: helpers.append(args))
    with pytest.raises(AppError, match='original removal'):
        await act(app, 'remove', later['resource'], 'new-id-cannot-escape')
    assert not helpers and owner.get(app.data_dir, row['sessionId'], row['id']) == before
    assert app.operations.requests.read(row['sessionId'], unknown['requestId']) == unknown
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(row['sessionId'], 'new-id-cannot-escape')
    assert (await act(app, 'remove', row, 'original-unknown-remove'))['request'] == unknown


async def test_late_stop_before_removal_effect_keeps_resource_and_calls_unchanged(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'before-late-stop'))['resource']
    before = owner.get(app.data_dir, row['sessionId'], row['id'])
    entered, release = threading.Event(), threading.Event()
    original = recovery.bind_configuration
    def pause(*args):
        result = original(*args)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(recovery, 'bind_configuration', pause)
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: calls.append(args))
    task = asyncio.create_task(act(app, 'remove', row, 'late-stopped-remove'))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        source = app.state['sessions'][0]
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    finally:
        release.set()
        result = await task
    assert result['request']['state'] == 'failed' and calls == []
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == before


async def test_direct_effect_route_cannot_bypass_original_unknown(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'direct-stop'))['resource']
    config = owner.configuration(app)[0]
    def lost(*args):
        raise TimeoutError('synthetic uncertain removal')
    monkeypatch.setattr(owner, 'invoke', lost)
    failed = await act(app, 'remove', row, 'direct-original')
    observer(monkeypatch, 'stopped')
    observed = await act(app, 'reconcile', failed['resource'], 'direct-observe')
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    args = {**scope(app), 'id':row['id'], 'expectedRevision':raw['revision'], 'requestId':'direct-new'}
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove', args, {})
    request = app.operations.requests.finish(request, 'admitting', resourceId=row['id'])
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: calls.append(args))
    result = await asyncio.to_thread(owner.effect, app, raw, 'remove', args, config, request, lambda: None)
    assert result['request']['state'] == 'failed' and not calls
    assert owner.get(app.data_dir, row['sessionId'], row['id']) == raw
    assert app.operations.requests.read(row['sessionId'], 'direct-original') == failed['request']


async def test_confirmed_destroy_survives_host_guest_removed_persistence_failure(app, backend, monkeypatch):
    row = (await start(app))['resource']
    row = (await act(app, 'stop', row, 'metadata-stop'))['resource']
    original_invoke, original_save = owner.invoke, owner.save
    calls, injected = [], []
    def invoke(config, home, raw, action, args):
        calls.append(action)
        result = original_invoke(config, home, raw, action, args)
        if action == 'remove':
            result['removal'] = {'version':1, 'phase':'settle', 'code':'complete', 'destroyEffect':'confirmed'}
        return result
    def save(home, raw, **changes):
        if changes.get('guestRemoved'):
            injected.append(True)
            raise OSError('private synthetic metadata failure')
        return original_save(home, raw, **changes)
    monkeypatch.setattr(owner, 'invoke', invoke)
    monkeypatch.setattr(owner, 'save', save)
    result = await act(app, 'remove', row, 'metadata-remove')
    assert injected == [True] and calls == ['remove']
    assert result['request']['state'] == 'outcome_unknown'
    assert result['request']['result']['removal'] == {
        'version':1, 'phase':'host-persistence', 'code':'metadata_unconfirmed', 'destroyEffect':'confirmed'}
    assert 'private' not in json.dumps(result['request'])
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] != 'retired'
    duplicate = await act(app, 'remove', row, 'metadata-remove')
    assert duplicate['request'] == result['request'] and calls == ['remove']
