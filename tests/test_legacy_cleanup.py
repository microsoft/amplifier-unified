"""Returned legacy export-limit evidence stays qualified; uncertainty stays held."""
import asyncio
import copy
import threading

import pytest

from amplifier_web import task_environments as owner
from amplifier_web.test_environment_legacy import returned_preservation_failure
from amplifier_web.service import AppError
from amplifier_web.operation_requests import OperationRequests
from test_configuration_authority import app
from test_task_environments import backend, scope, start, act
from test_environment_recovery import removal_read_args


def retained_pair():
    row = {'id': 'resource', 'sessionId': 'root', 'workspaceId': 'workspace',
           'backendType': 'docker', 'backendIdentity': {'backend': 'docker', 'daemon': 'daemon'},
           'backendKey': 'backend', 'backendInstallation': 'installation', 'backendAdmitted': True,
           'backendConfigRetained': True, 'status': 'active', 'environmentState': 'stopped',
           'universeId': 'universe', 'launchOperationId': 'launch', 'source': '/synthetic/source',
           'owner': {'sessionId': 'root', 'principal': 'root'},
           'snapshot': {'archive_sha256': 'a' * 64, 'snapshot_commit': 'b' * 40,
                        'snapshot_tree': 'c' * 40}}
    original = {'requestId': 'original', 'action': 'workspace.environments.remove',
                'sessionId': 'root', 'resourceId': 'resource', 'state': 'outcome_unknown',
                'result': {'ok': False, 'status': 'unknown', 'code': 'export-too-large',
                           'universeId': 'universe', 'snapshot': copy.deepcopy(row['snapshot']),
                           'message': 'A bounded returned helper diagnostic'}}
    return row, original


def test_only_exact_returned_receipt_qualifies_without_rewriting_it():
    row, original = retained_pair()
    before = copy.deepcopy((row, original))
    proof = returned_preservation_failure(row, original)
    assert proof['kind'] == 'returned-legacy-export-limit'
    assert proof['originalRequestId'] == 'original' and len(proof['originalSha256']) == 64
    assert (row, original) == before


@pytest.mark.parametrize('case', [
    'timeout', 'restart', 'malformed', 'failed-copy', 'missing-result', 'missing-snapshot',
    'foreign-universe', 'foreign-snapshot', 'foreign-root', 'unbound', 'different-action',
    'not-returned', 'destroy-field', 'missing-backend', 'foreign-backend', 'unretained-config',
    'no-installation', 'removed', 'no-source-identity', 'foreign-owner',
])
def test_uncertain_or_mismatched_legacy_results_stay_held(case):
    row, original = retained_pair()
    if case == 'timeout': original['result']['code'] = 'export-timeout'
    elif case == 'restart': original.pop('result')
    elif case == 'malformed': original['result'] = 'export-too-large'
    elif case == 'failed-copy': original['result']['code'] = 'export-failed'
    elif case == 'missing-result': original['result'] = {}
    elif case == 'missing-snapshot': original['result'].pop('snapshot')
    elif case == 'foreign-universe': original['result']['universeId'] = 'foreign'
    elif case == 'foreign-snapshot': original['result']['snapshot']['snapshot_tree'] = 'd' * 40
    elif case == 'foreign-root': original['sessionId'] = 'foreign'
    elif case == 'unbound': original.pop('resourceId')
    elif case == 'different-action': original['action'] = 'workspace.environments.finishCleanup'
    elif case == 'not-returned': original['state'] = 'running'
    elif case == 'destroy-field': original['result']['destroyEffect'] = 'unknown'
    elif case == 'missing-backend': row.pop('backendIdentity')
    elif case == 'foreign-backend': row['backendType'] = 'incus'
    elif case == 'unretained-config': row['backendConfigRetained'] = False
    elif case == 'no-installation': row.pop('backendInstallation')
    elif case == 'removed': row['guestRemoved'] = True
    elif case == 'no-source-identity': row['snapshot'].pop('snapshot_tree')
    elif case == 'foreign-owner': row['owner']['sessionId'] = 'foreign'
    assert returned_preservation_failure(row, original) is None


async def legacy_failure(app, monkeypatch):
    public = (await start(app))['resource']
    public = (await act(app, 'stop', public, 'legacy-stop'))['resource']
    row = owner.get(app.data_dir, public['sessionId'], public['id'])
    row = owner.save(app.data_dir, row, backendInstallation='retained-installation',
        snapshot={'archive_sha256': 'a' * 64, 'snapshot_commit': 'b' * 40, 'snapshot_tree': 'c' * 40})
    def returned(config, home, current, action, args):
        assert action == 'remove'
        return {'ok': False, 'status': 'unknown', 'code': 'export-too-large',
                'snapshot': current['snapshot'], 'universeId': current['universeId'],
                'message': 'Returned before destruction'}
    monkeypatch.setattr(owner, 'invoke', returned)
    failed = await act(app, 'remove', owner.public(app.data_dir, row), 'legacy-returned')
    assert failed['request']['state'] == 'outcome_unknown'
    row = owner.get(app.data_dir, row['sessionId'], row['id'])
    # A supported fresh observation is independent of the original outcome.
    row = owner.save(app.data_dir, row, environmentState='stopped')
    return owner.public(app.data_dir, row), failed['request']


async def test_qualified_cleanup_commits_binding_and_preserves_original(app, backend, monkeypatch):
    row, original = await legacy_failure(app, monkeypatch)
    calls = []
    def finish(config, home, current, action, args):
        assert action == 'finishCleanup'
        calls.append(action)
        request = app.operations.requests.read(current['sessionId'], args['requestId'])
        proof = returned_preservation_failure(current, original)
        assert proof['kind'] == 'returned-legacy-export-limit'
        assert request['legacyCleanupQualification'] == proof
        assert app.operations.requests.read(current['sessionId'], original['requestId']) == original
        return {'ok': True, 'status': 'removed', 'universeId': current['universeId'],
                'retention': {'verified': True, 'archiveOnly': True}}
    monkeypatch.setattr(owner, 'invoke', finish)
    result = await act(app, 'finishCleanup', row, 'qualified-continuation', previousRequestId=original['requestId'])
    assert result['request']['state'] == 'completed'
    assert result['resource']['previousCleanup'] == original
    assert result['resource']['status'] == 'removed'
    duplicate = await act(app, 'finishCleanup', row, 'qualified-continuation', previousRequestId=original['requestId'])
    assert duplicate['request'] == result['request'] and calls == ['finishCleanup']
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


async def test_another_unresolved_request_still_blocks_qualified_cleanup(app, backend, monkeypatch):
    row, original = await legacy_failure(app, monkeypatch)
    competing, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.remove',
                                               {'requestId': 'unbound-competitor'}, {})
    competing = app.operations.requests.finish(competing, 'outcome_unknown')
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: calls.append(args))
    with pytest.raises(AppError, match='original removal remains unresolved'):
        await act(app, 'finishCleanup', row, 'must-not-admit', previousRequestId=original['requestId'])
    assert calls == []
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original
    assert app.operations.requests.read(row['sessionId'], competing['requestId']) == competing


async def test_qualification_publication_failure_prevents_backend_call(app, backend, monkeypatch):
    row, original = await legacy_failure(app, monkeypatch)
    finish = app.operations.requests.finish
    def refuse(record, state, **fields):
        if 'legacyCleanupQualification' in fields:
            raise OSError('Cannot durably publish qualification')
        return finish(record, state, **fields)
    monkeypatch.setattr(app.operations.requests, 'finish', refuse)
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *args: calls.append(args))
    with pytest.raises(AppError, match='durably publish'):
        await act(app, 'finishCleanup', row, 'publication-fails', previousRequestId=original['requestId'])
    assert not calls
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('publication_fails', [False, True])
async def test_common_route_commits_qualification_before_invocation(app, backend, monkeypatch, publication_fails):
    row, original = await legacy_failure(app, monkeypatch)
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'],
            'requestId': 'common-continuation', 'previousRequestId': original['requestId']}
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.finishCleanup', args, {})
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    finish = app.operations.requests.finish
    def publication(record, state, **fields):
        if publication_fails and 'legacyCleanupQualification' in fields:
            raise OSError('Common qualification persistence failed')
        return finish(record, state, **fields)
    monkeypatch.setattr(app.operations.requests, 'finish', publication)
    calls = []
    def invoke(config, home, current, action, arguments):
        saved = app.operations.requests.read(current['sessionId'], args['requestId'])
        assert saved['resourceId'] == row['id']
        proof = returned_preservation_failure(current, original)
        assert proof['kind'] == 'returned-legacy-export-limit'
        assert saved['legacyCleanupQualification'] == proof
        calls.append(action)
        return {'ok': True, 'status': 'removed', 'universeId': current['universeId'],
                'retention': {'verified': True}}
    monkeypatch.setattr(owner, 'invoke', invoke)
    if publication_fails:
        with pytest.raises(OSError, match='persistence'):
            await asyncio.to_thread(owner.effect, app, raw, 'finishCleanup', args,
                                    owner.configuration(app)[0], request, lambda: None)
    else:
        result = await asyncio.to_thread(owner.effect, app, raw, 'finishCleanup', args,
                                        owner.configuration(app)[0], request, lambda: None)
        assert result['request']['state'] == 'completed'
    assert calls == ([] if publication_fails else ['finishCleanup'])
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


@pytest.mark.parametrize('bound', [False, True])
async def test_passive_availability_respects_competing_unknown(app, backend, monkeypatch, bound):
    row, original = await legacy_failure(app, monkeypatch)
    args = removal_read_args(app, row, original)
    before = (await app.dispatch('workspace.environments.removal.read', args))['result']
    assert before['continuationAvailable'] is True
    competing, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.finishCleanup',
                                               {'requestId': 'later-unknown'}, {})
    app.operations.requests.finish(competing, 'outcome_unknown',
        **({'resourceId': row['id']} if bound else {}))
    after = (await app.dispatch('workspace.environments.removal.read', args))['result']
    assert after['status'] == 'qualified' and after['continuationAvailable'] is False
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


async def test_inflight_duplicate_and_competitor_do_not_reexecute(app, backend, monkeypatch):
    row, original = await legacy_failure(app, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(config, home, current, action, args):
        calls.append(action)
        entered.set()
        assert release.wait(5)
        return {'ok': True, 'status': 'removed', 'universeId': current['universeId'],
                'retention': {'verified': True}}
    monkeypatch.setattr(owner, 'invoke', blocked)
    running = asyncio.create_task(act(app, 'finishCleanup', row, 'inflight', previousRequestId=original['requestId']))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        saved = app.operations.requests.read(row['sessionId'], 'inflight')
        duplicate = await act(app, 'finishCleanup', row, 'inflight', previousRequestId=original['requestId'])
        assert duplicate['request'] == saved
        with pytest.raises(AppError, match='changed'):
            await act(app, 'finishCleanup', row, 'competitor', previousRequestId=original['requestId'])
        assert calls == ['finishCleanup']
    finally:
        release.set()
        await running


async def test_restart_after_qualification_never_replays_or_admits_new_identity(app, backend, monkeypatch):
    row, original = await legacy_failure(app, monkeypatch)
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'],
            'requestId': 'interrupted-qualified', 'previousRequestId': original['requestId']}
    actor = {'sessionId': row['sessionId'], 'principal': row['sessionId']}
    request, fresh = owner.begin_removal(app, owner.get(app.data_dir, row['sessionId'], row['id']),
                                        'workspace.environments.finishCleanup', args, actor)
    assert fresh and request['legacyCleanupQualification']
    OperationRequests(app.operations.requests.journal)
    unknown = app.operations.requests.read(row['sessionId'], request['requestId'])
    assert unknown['state'] == 'outcome_unknown'
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *a: calls.append(a))
    duplicate = await app.dispatch('workspace.environments.finishCleanup', args)
    assert duplicate['result']['request'] == unknown
    with pytest.raises(AppError, match='original removal remains unresolved'):
        await act(app, 'finishCleanup', row, 'new-after-restart', previousRequestId=original['requestId'])
    assert not calls
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


async def test_original_change_after_admission_prevents_effect(app, backend, monkeypatch):
    row, original = await legacy_failure(app, monkeypatch)
    args = {**scope(app), 'id': row['id'], 'expectedRevision': row['revision'],
            'requestId': 'qualified-before-change', 'previousRequestId': original['requestId']}
    request, _ = owner.begin_removal(app, owner.get(app.data_dir, row['sessionId'], row['id']),
        'workspace.environments.finishCleanup', args, {})
    changed = app.operations.requests.finish(original, 'outcome_unknown',
        result={**original['result'], 'code': 'export-timeout'})
    calls = []
    monkeypatch.setattr(owner, 'invoke', lambda *a: calls.append(a))
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    result = await asyncio.to_thread(owner.effect, app, raw, 'finishCleanup', args,
                                    owner.configuration(app)[0], request, lambda: None)
    assert result['request']['state'] == 'failed' and not calls
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == changed


async def test_unknown_after_qualified_invocation_keeps_every_new_identity_held(app, backend, monkeypatch):
    row, original = await legacy_failure(app, monkeypatch)
    calls = []
    def unknown(*args):
        calls.append('finishCleanup')
        raise TimeoutError('No acknowledgement after admitted work')
    monkeypatch.setattr(owner, 'invoke', unknown)
    result = await act(app, 'finishCleanup', row, 'qualified-but-unknown', previousRequestId=original['requestId'])
    assert result['request']['state'] == 'outcome_unknown'
    raw = owner.get(app.data_dir, row['sessionId'], row['id'])
    raw = owner.save(app.data_dir, raw, environmentState='stopped')
    current = owner.public(app.data_dir, raw)
    with pytest.raises(AppError, match='original removal remains unresolved'):
        await act(app, 'finishCleanup', current, 'after-unknown', previousRequestId=original['requestId'])
    assert calls == ['finishCleanup']
    assert app.operations.requests.read(row['sessionId'], original['requestId']) == original


async def pending_legacy_preparation(app, monkeypatch):
    public, original = await legacy_failure(app, monkeypatch)
    row = owner.get(app.data_dir, public['sessionId'], public['id'])
    row = owner.save(app.data_dir, row, preparationPending=True)
    calls = []
    def removed(config, home, current, action, args):
        assert action == 'finishCleanup'
        calls.append(action)
        return {'ok': True, 'status': 'removed', 'universeId': current['universeId'],
                'retention': {'verified': True}}
    monkeypatch.setattr(owner, 'invoke', removed)
    monkeypatch.setattr(owner.dependencies, 'cleanup',
                        lambda *a: {'complete': False, 'status': 'unknown'})
    result = await act(app, 'finishCleanup', owner.public(app.data_dir, row),
                       'qualified-guest-removal', previousRequestId=original['requestId'])
    saved = owner.get(app.data_dir, row['sessionId'], row['id'])
    assert saved['guestRemoved'] and saved['preparationPending']
    assert result['request']['state'] == 'outcome_unknown'
    assert calls == ['finishCleanup']
    assert returned_preservation_failure(saved, original) is None
    return result, original, calls


@pytest.mark.parametrize('restart', [False, True])
async def test_legacy_preparation_continuation_preserves_exact_proof(app, backend, monkeypatch, restart):
    pending, original, calls = await pending_legacy_preparation(app, monkeypatch)
    public = pending['resource']
    if restart:
        app.operations.requests = OperationRequests(app.operations.requests.journal)
    cleanup_calls = []
    def cleanup(*args):
        cleanup_calls.append('cleanup')
        return {'complete': True, 'status': 'removed'}
    monkeypatch.setattr(owner.dependencies, 'cleanup', cleanup)
    recovered = await act(app, 'finishCleanup', public, 'dependency-only',
                          previousRequestId=pending['request']['requestId'])
    assert recovered['preparationRecovery'] is True
    assert recovered['resource']['status'] == 'removed'
    assert recovered['request']['requestId'] == pending['request']['requestId']
    assert recovered['request']['state'] == 'completed'
    assert recovered['request']['legacyCleanupQualification'] == pending['request']['legacyCleanupQualification']
    assert app.operations.requests.read(public['sessionId'], original['requestId']) == original
    assert calls == ['finishCleanup'] and cleanup_calls == ['cleanup']
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(public['sessionId'], 'dependency-only')


@pytest.mark.parametrize('change', [
    'original', 'qualification', 'missing-qualification', 'resource-binding',
    'removal-binding', 'competing', 'wrong-previous',
])
async def test_legacy_preparation_continuation_rejects_changed_evidence(app, backend, monkeypatch, change):
    pending, original, calls = await pending_legacy_preparation(app, monkeypatch)
    public, removal = pending['resource'], pending['request']
    sid = public['sessionId']
    previous = removal['requestId']
    if change == 'original':
        app.operations.requests.finish(original, 'outcome_unknown',
            result={**original['result'], 'code': 'export-timeout'})
    elif change in {'qualification', 'missing-qualification'}:
        proof = ({**removal['legacyCleanupQualification'], 'originalSha256': 'e' * 64}
                 if change == 'qualification' else None)
        app.operations.requests.finish(removal, 'outcome_unknown', legacyCleanupQualification=proof)
    elif change == 'resource-binding':
        row = owner.get(app.data_dir, sid, public['id'])
        row = owner.save(app.data_dir, row, source='/synthetic/changed-source')
        public = owner.public(app.data_dir, row)
    elif change == 'removal-binding':
        app.operations.requests.finish(removal, 'outcome_unknown',
            guestRemovalBinding={**removal['guestRemovalBinding'], 'launchOperationId': 'other-launch'})
    elif change == 'competing':
        other, _ = app.operations.requests.begin(sid, 'workspace.environments.remove',
                                                {'requestId': 'other-unknown'}, {})
        app.operations.requests.finish(other, 'outcome_unknown', resourceId=public['id'])
    elif change == 'wrong-previous':
        previous = original['requestId']
    cleanup_calls = []
    monkeypatch.setattr(owner.dependencies, 'cleanup', lambda *args: cleanup_calls.append(args))
    with pytest.raises(AppError):
        await act(app, 'finishCleanup', public, 'must-not-clean', previousRequestId=previous)
    assert calls == ['finishCleanup'] and not cleanup_calls
    with pytest.raises(ValueError, match='not found'):
        app.operations.requests.read(sid, 'must-not-clean')
