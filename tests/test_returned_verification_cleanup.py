"""A returned pre-destroy verification failure can use the existing cleanup owner."""
import asyncio
import copy

import pytest

from amplifier_web import task_environments as owner
from amplifier_web import test_environment_legacy as qualification
from amplifier_web.service import AppError
from test_configuration_authority import app
from test_task_environments import backend, scope, act, start
from test_legacy_cleanup import retained_pair
from test_environment_recovery import removal_read_args, forbid_removal_read_effects, removal_read_snapshot


def returned_result(universe):
    return {'ok': False, 'status': 'unknown', 'universeId': universe,
            'removal': {'version': 1, 'phase': 'verify-exports',
                        'code': 'phase_failed', 'destroyEffect': 'not_dispatched'},
            'message': 'Returned before destruction'}


def verification_pair():
    row, original = retained_pair()
    original['result'] = returned_result(row['universeId'])
    return row, original


def test_exact_returned_verification_receipt_is_bound_without_rewriting_history():
    row, original = verification_pair()
    before = copy.deepcopy((row, original))
    proof = qualification.returned_preservation_failure(row, original)
    assert proof['kind'] == 'returned-archive-verification-failure'
    assert len(proof['originalSha256']) == 64
    assert proof['snapshot'] == row['snapshot']
    assert (row, original) == before


@pytest.mark.parametrize('case', [
    'generic', 'message-only', 'missing-phase', 'extra-phase-field', 'version',
    'destroy-unknown', 'destroy-confirmed', 'different-phase', 'different-code',
    'foreign-universe', 'foreign-root', 'unbound', 'different-action', 'not-returned',
    'missing-backend', 'unretained-config', 'no-installation', 'removed',
    'no-source-identity', 'foreign-owner',
])
def test_unknown_or_mismatched_originals_are_never_qualified(case):
    row, original = verification_pair()
    result = original['result']
    if case == 'generic': original['result'] = {'ok': False, 'status': 'unknown', 'message': 'failed'}
    elif case == 'message-only': result.pop('removal'); result['message'] = 'verify-exports not_dispatched'
    elif case == 'missing-phase': result['removal'].pop('phase')
    elif case == 'extra-phase-field': result['removal']['callerProof'] = True
    elif case == 'version': result['removal']['version'] = 2
    elif case == 'destroy-unknown': result['removal']['destroyEffect'] = 'unknown'
    elif case == 'destroy-confirmed': result['removal']['destroyEffect'] = 'confirmed'
    elif case == 'different-phase': result['removal']['phase'] = 'destroy'
    elif case == 'different-code': result['removal']['code'] = 'complete'
    elif case == 'foreign-universe': result['universeId'] = 'foreign'
    elif case == 'foreign-root': original['sessionId'] = 'foreign'
    elif case == 'unbound': original.pop('resourceId')
    elif case == 'different-action': original['action'] = 'workspace.environments.finishCleanup'
    elif case == 'not-returned': original['state'] = 'running'
    elif case == 'missing-backend': row.pop('backendIdentity')
    elif case == 'unretained-config': row['backendConfigRetained'] = False
    elif case == 'no-installation': row.pop('backendInstallation')
    elif case == 'removed': row['guestRemoved'] = True
    elif case == 'no-source-identity': row['snapshot'].pop('snapshot_tree')
    elif case == 'foreign-owner': row['owner']['sessionId'] = 'foreign'
    assert qualification.returned_preservation_failure(row, original) is None


async def verification_failure(app, monkeypatch):
    public = (await start(app))['resource']
    public = (await act(app, 'stop', public, 'verification-stop'))['resource']
    row = owner.get(app.data_dir, public['sessionId'], public['id'])
    row = owner.save(app.data_dir, row, backendInstallation='retained-installation',
        snapshot={'archive_sha256': 'a' * 64, 'snapshot_commit': 'b' * 40, 'snapshot_tree': 'c' * 40})
    monkeypatch.setattr(owner, 'invoke', lambda config, home, current, action, args:
                        returned_result(current['universeId']))
    failed = await act(app, 'remove', owner.public(app.data_dir, row), 'verification-returned')
    assert failed['request']['state'] == 'outcome_unknown'
    row = owner.get(app.data_dir, row['sessionId'], row['id'])
    row = owner.save(app.data_dir, row, environmentState='stopped')
    return owner.public(app.data_dir, row), failed['request']


async def test_passive_read_qualifies_only_exact_returned_record_without_effects(app, backend, monkeypatch):
    row, original = await verification_failure(app, monkeypatch)
    before = removal_read_snapshot(app, row)
    with monkeypatch.context() as passive:
        forbid_removal_read_effects(app, passive)
        result = (await app.dispatch('workspace.environments.removal.read',
                                    removal_read_args(app, row, original)))['result']
    assert result['status'] == 'qualified' and result['continuationAvailable'] is True
    assert result['code'] == 'returned_archive_verification_failure'
    assert result['executor'] == 'returned_and_reaped' and result['physical'] == 'not_observed'
    assert removal_read_snapshot(app, row) == before


async def test_qualified_cleanup_preserves_original_and_is_idempotent(app, backend, monkeypatch):
    row, original = await verification_failure(app, monkeypatch)
    calls = []
    def finish(config, home, current, action, args):
        assert action == 'finishCleanup'
        calls.append(action)
        request = app.operations.requests.read(current['sessionId'], args['requestId'])
        assert request['legacyCleanupQualification'] == qualification.returned_preservation_failure(current, original)
        assert app.operations.requests.read(current['sessionId'], original['requestId']) == original
        return {'ok': True, 'status': 'removed', 'universeId': current['universeId'],
                'retention': {'verified': True, 'archiveOnly': True}}
    monkeypatch.setattr(owner, 'invoke', finish)
    result = await act(app, 'finishCleanup', row, 'verification-continuation', previousRequestId=original['requestId'])
    assert result['request']['state'] == 'completed'
    assert result['resource']['previousCleanup'] == original
    repeated = await act(app, 'finishCleanup', row, 'verification-continuation', previousRequestId=original['requestId'])
    assert repeated['request'] == result['request'] and calls == ['finishCleanup']


@pytest.mark.parametrize('case', ['competing', 'stale-revision', 'changed-original', 'publication'])
async def test_continuation_still_requires_exact_durable_admission(app, backend, monkeypatch, case):
    row, original = await verification_failure(app, monkeypatch)
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *a: calls.append(a))
    if case == 'competing':
        other, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove',
                                                {'requestId': 'other-unknown'}, {})
        app.operations.requests.finish(other, 'outcome_unknown')
    elif case == 'stale-revision':
        row = {**row, 'revision': row['revision'] - 1}
    elif case == 'publication':
        real_finish = app.operations.requests.finish
        def fail(record, state, **fields):
            if 'legacyCleanupQualification' in fields:
                raise OSError('Cannot publish qualification')
            return real_finish(record, state, **fields)
        monkeypatch.setattr(app.operations.requests, 'finish', fail)
    if case == 'changed-original':
        args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'],
                'requestId': 'admitted-before-change', 'previousRequestId': original['requestId']}
        raw = owner.get(app.data_dir, row['sessionId'], row['id'])
        request, _ = owner.begin_removal(app, raw, 'workspace.environments.finishCleanup', args, {})
        changed = copy.deepcopy(original['result'])
        changed['removal']['destroyEffect'] = 'unknown'
        app.operations.requests.finish(original, 'outcome_unknown', result=changed)
        result = await asyncio.to_thread(owner.effect, app, raw, 'finishCleanup', args,
                                        owner.configuration(app)[0], request, lambda: None)
        assert result['request']['state'] == 'failed'
    else:
        with pytest.raises(AppError):
            await act(app, 'finishCleanup', row, 'must-not-dispatch', previousRequestId=original['requestId'])
    assert not calls


async def test_preparation_only_continuation_keeps_returned_verification_proof(app, backend, monkeypatch):
    public, original = await verification_failure(app, monkeypatch)
    row = owner.get(app.data_dir, public['sessionId'], public['id'])
    row = owner.save(app.data_dir, row, preparationPending=True)
    calls = []
    def remove(config, home, current, action, args):
        calls.append(action)
        return {'ok': True, 'status': 'removed', 'universeId': current['universeId'],
                'retention': {'verified': True, 'archiveOnly': True}}
    monkeypatch.setattr(owner, 'invoke', remove)
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': False, 'status': 'unknown'})
    pending = await act(app, 'finishCleanup', owner.public(app.data_dir, row),
                        'verified-guest-removal', previousRequestId=original['requestId'])
    assert pending['request']['state'] == 'outcome_unknown'
    assert pending['request']['legacyCleanupQualification']['kind'] == 'returned-archive-verification-failure'
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *a: {'complete': True, 'status': 'removed'})
    result = await act(app, 'finishCleanup', pending['resource'], 'preparation-only',
                       previousRequestId=pending['request']['requestId'])
    assert result['preparationRecovery'] is True and result['request']['state'] == 'completed'
    assert result['request']['requestId'] == pending['request']['requestId']
    assert result['request']['legacyCleanupQualification'] == pending['request']['legacyCleanupQualification']
    assert calls == ['finishCleanup']
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(row['sessionId'], 'preparation-only')
