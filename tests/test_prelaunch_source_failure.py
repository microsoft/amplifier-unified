"""Returned source failures are terminal; uncertain physical effects stay held."""
import copy
import json

import pytest
from filelock import FileLock

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_worker as worker
from amplifier_web.service import AppError
from test_configuration_authority import app
from test_coordination_generation import agent
from test_task_environments import backend, scope, act
from test_test_environment_worker import library, Failure, payload
from test_test_queue import queue, submit, drive, record


def test_returned_source_export_failure_reports_no_launch(library, tmp_path):
    lib, calls = library
    def changed(*args):
        failure = Failure('private source path must not escape')
        failure.code = 'repository-source-changed'
        raise failure
    lib.export_repository_snapshot = changed
    data = payload(tmp_path, 'prepare')
    data['snapshot'] = None
    result = worker.execute_call(data)
    assert result['status'] == 'not-started'
    assert result['effectAdmitted'] is False
    assert result['code'] == 'repository-source-changed'
    assert 'No environment or test command was started' in result['message']
    assert 'private source path' not in json.dumps(result)
    assert not calls
    assert not (tmp_path / 'compose.json').exists()


@pytest.mark.parametrize('action,code', [('prepare', 'backend-unavailable'), ('start', 'repository-source-changed')])
def test_other_backend_failures_keep_uncertainty(library, tmp_path, monkeypatch, action, code):
    lib, calls = library
    def failure(*args, **kwargs):
        error = Failure('not a confirmed source preparation refusal')
        error.code = code
        raise error
    if action == 'prepare':
        lib.export_repository_snapshot = failure
    else:
        monkeypatch.setattr(worker, 'docker_admission', failure)
    result = worker.execute_call(payload(tmp_path, action))
    assert result['status'] == 'unknown' and 'effectAdmitted' not in result


async def failed_source(app, queue, monkeypatch, *, legacy=True):
    calls = []
    def invoke(config, home, row, action, args):
        calls.append(action)
        if action == 'prepare':
            universe = 'owned-universe'
            binding = worker.binding({'rootOwner': row['sessionId'], 'operationId': row['launchOperationId']},
                                     universe, {'backend': 'docker', 'daemon': 'owned-daemon'})
            worker.write_private_record(owner.storage.directory(home, row) / 'backend-binding.json', binding)
            return {'ok': False, 'status': 'unknown' if legacy else 'not-started',
                    'universeId': universe, 'snapshot': None, 'code': 'repository-source-changed',
                    'message': 'Historical generic uncertainty' if legacy else 'No environment or test command was started.',
                    **({} if legacy else {'effectAdmitted': False})}
        assert action == 'reconcile', 'No physical effect is allowed after source refusal'
        return {'ok': False, 'status': 'unknown'}
    monkeypatch.setattr(owner, 'invoke', invoke)
    await submit(app)
    await drive(queue)
    check = record(queue, app)
    row = owner.get(app.data_dir, scope(app)['sessionId'], check['job']['resourceId'])
    assert row['status'] == 'retired' and row['environmentState'] == 'not-started'
    assert check['job']['status'] == 'failed' and not row.get('backendAdmitted')
    assert not row.get('preparationPending')
    return row, calls


@pytest.mark.parametrize('legacy', [True, False])
async def test_reconcile_retains_exact_failed_launch_without_backend_replay(app, queue, backend, monkeypatch, legacy):
    admitted_backend = owner.invoke
    row, calls = await failed_source(app, queue, monkeypatch, legacy=legacy)
    original = copy.deepcopy(row['lastOperation'])
    result = await act(app, 'reconcile', row, 'inspect-source-failure')
    assert result['request']['state'] == 'completed'
    proof = result['request']['result']
    assert proof['status'] == 'not-started' and proof['effectAdmitted'] is False
    assert proof['preLaunchFailure']['originalRequestId'] == original['requestId']
    assert 'observation' not in proof  # No fabricated physical-absence claim.
    assert result['resource']['lastOperation'] == original
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original
    assert owner.get(app.data_dir, row['sessionId'], row['id'])['status'] == 'retired'
    assert calls == ['prepare']
    duplicate = await act(app, 'reconcile', row, 'inspect-source-failure')
    assert duplicate['request'] == result['request'] and calls == ['prepare']
    await submit(app)
    await drive(queue)
    assert calls == ['prepare']  # The failed check ID is never retried.
    monkeypatch.setattr(owner, 'invoke', admitted_backend)
    await submit(app, 'explicit-new-check')
    await drive(queue)
    assert record(queue, app, 'explicit-new-check')['job']['status'] == 'passed'
    assert record(queue, app)['job']['status'] == 'failed'
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('changes', [
    {'backendAdmitted': True}, {'preparationPending': True}, {'pendingRequest': 'another-operation'},
    {'hostReservation': {'admitted': True}}, {'snapshot': {'snapshot_commit': 'prepared'}},
    {'status': 'prepared'}, {'environmentState': 'unknown'}, {'commandId': 'different-launch'},
    {'launchOperationId': 'different-physical-launch'}, {'universeId': 'different-universe'},
    {'backendIdentity': {'backend': 'docker', 'daemon': 'different-daemon'}},
    {'backendConfigRetained': False}, {'owner': {'sessionId': 'foreign-root', 'principal': 'foreign'}},
])
async def test_unsettled_or_changed_owner_is_not_reclassified(app, queue, backend, monkeypatch, changes):
    row, calls = await failed_source(app, queue, monkeypatch)
    row = owner.save(app.data_dir, row, **changes)
    result = await act(app, 'reconcile', row, 'held-source-failure')
    assert result['request']['state'] != 'completed'
    assert 'preLaunchFailure' not in (result['request'].get('result') or {})
    assert result['resource']['lastOperation'] == row['lastOperation']


@pytest.mark.parametrize('field,value', [
    ('state', 'outcome_unknown'), ('resourceId', 'foreign-resource'), ('sessionId', 'foreign-root'),
    ('action', 'workspace.environments.execute'), ('requestId', 'foreign-request'),
])
async def test_changed_original_receipt_cannot_release_unknown(app, queue, backend, monkeypatch, field, value):
    row, calls = await failed_source(app, queue, monkeypatch)
    original = app.operations.requests.read(row['sessionId'], row['commandId'])
    changed = {**original, field: value}
    app.operations.requests.journal.db.execute('UPDATE operation_requests SET value=? WHERE session_id=? AND id=?',
        (json.dumps(changed), row['sessionId'], row['commandId']))
    app.operations.requests.journal.db.commit()
    result = await act(app, 'reconcile', row, 'changed-original')
    assert result['request']['state'] != 'completed'
    assert 'preLaunchFailure' not in (result['request'].get('result') or {})


async def test_source_failure_recovery_obeys_revision_lock_and_agent_authority(app, queue, backend, monkeypatch):
    row, calls = await failed_source(app, queue, monkeypatch)
    with pytest.raises(AppError, match='changed'):
        await act(app, 'reconcile', {**row, 'revision': 1}, 'stale')
    with FileLock(str(app.data_dir / 'workspace-resources' / ('environment-' + row['id'] + '.lock'))):
        result = await act(app, 'reconcile', row, 'locked')
    assert result['request']['state'] != 'completed' and calls == ['prepare']
    source, peer = app.state['sessions']
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'], 'requestId': 'wrong-owner'}
    with pytest.raises(AppError, match='calling conversation'):
        await agent(app, source, 'workspace.environments.reconcile', {**args, 'sessionId': peer['id']}, 'foreign')
    assert calls == ['prepare']


@pytest.mark.parametrize('tamper', ['binding-missing', 'binding-root', 'binding-operation', 'binding-universe', 'result-code', 'result-admitted'])
async def test_missing_or_changed_bound_evidence_stays_held(app, queue, backend, monkeypatch, tamper):
    row, calls = await failed_source(app, queue, monkeypatch)
    if tamper.startswith('binding'):
        path = owner.storage.directory(app.data_dir, row) / 'backend-binding.json'
        if tamper == 'binding-missing':
            path.unlink()
        else:
            proof = json.loads(path.read_text())
            proof[{'binding-root': 'rootOwner', 'binding-operation': 'operationId', 'binding-universe': 'universeId'}[tamper]] = 'foreign'
            path.write_text(json.dumps(proof))
    else:
        original = copy.deepcopy(row['lastOperation'])
        original['result'].update({'code': 'other-source-error'} if tamper == 'result-code' else {'effectAdmitted': True})
        app.operations.requests.journal.db.execute('UPDATE operation_requests SET value=? WHERE session_id=? AND id=?',
            (json.dumps(original), row['sessionId'], row['commandId']))
        app.operations.requests.journal.db.commit()
        row = owner.save(app.data_dir, row, lastOperation=original)
    result = await act(app, 'reconcile', row, 'bad-proof')
    assert result['request']['state'] != 'completed'
    assert 'preLaunchFailure' not in (result['request'].get('result') or {})


async def test_changed_configuration_cannot_reclassify_original_source_failure(app, queue, backend, monkeypatch):
    row, calls = await failed_source(app, queue, monkeypatch)
    row = owner.save(app.data_dir, row, backendKey='f' * 64)
    with pytest.raises(AppError):
        await act(app, 'reconcile', row, 'changed-config')
    assert calls == ['prepare']


async def test_committed_source_inspection_repairs_projection_without_replay(app, queue, backend, monkeypatch):
    row, calls = await failed_source(app, queue, monkeypatch)
    save, failures = owner.save, []
    def once(home, resource, **changes):
        if changes.get('lastRecovery', {}).get('state') == 'completed' and not failures:
            failures.append(True)
            raise OSError('projection failed after inspection commit')
        return save(home, resource, **changes)
    monkeypatch.setattr(owner, 'save', once)
    result = await act(app, 'reconcile', row, 'durable-source-inspection')
    assert result['request']['state'] == 'completed' and failures == [True]
    assert result['resource']['lastRecovery'] == result['request']
    assert result['resource']['lastOperation'] == row['lastOperation']
    assert calls == ['prepare']
