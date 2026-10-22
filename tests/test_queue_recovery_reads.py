"""Later exact cleanup is visible without rewriting or replaying old outcomes."""
import copy

import pytest

from amplifier_web import task_environments as environments
from amplifier_web.test_queue import TestQueue
from test_configuration_authority import app
from test_task_environments import backend, scope, act
from test_test_queue import queue, submit, drive, record


async def recovered(app, queue, backend, monkeypatch, *, uncertain_check=False):
    invoke = environments.invoke
    def fail_cleanup(config, home, row, action, args):
        if uncertain_check and action == 'execute':
            backend.append((action, dict(args)))
            raise TimeoutError('check acknowledgement lost')
        if uncertain_check and action == 'job_read':
            backend.append((action, dict(args)))
            return {'ok': False, 'job': {'id': args['requestId'], 'state': 'unknown', 'terminal': False}}
        if action in {'remove', 'finishCleanup'}:
            backend.append((action, dict(args)))
            return {'ok': False, 'effectAdmitted': False, 'status': 'stopped',
                    'phase': 'preserving', 'code': 'preservation_failed', 'message': 'export interrupted'}
        return invoke(config, home, row, action, args)
    monkeypatch.setattr(environments, 'invoke', fail_cleanup)
    await submit(app)
    await drive(queue)
    old = record(queue, app)
    assert old['job']['status'] == 'unknown'
    sid, rid = old['senderSessionId'], old['job']['resourceId']
    row = environments.get(app.data_dir, sid, rid)
    if uncertain_check:
        stopped = await act(app, 'stop', environments.public(app.data_dir, row), 'separate-stop')
        failed = await act(app, 'remove', stopped['resource'], 'separate-remove')
        assert failed['request']['state'] == 'failed'
        row = environments.get(app.data_dir, sid, rid)
    assert row['environmentState'] == 'stopped' and row['status'] != 'retired'
    def complete(config, home, row, action, args):
        result = invoke(config, home, row, action, args)
        if action == 'finishCleanup':
            result.update(status='removed', retention={'verified': True, 'archiveBytes': 123})
        return result
    monkeypatch.setattr(environments, 'invoke', complete)
    later = await act(app, 'finishCleanup', environments.public(app.data_dir, row),
                      'independently-named-finish', previousRequestId=row['lastOperation']['requestId'])
    assert later['request']['state'] == 'completed' and later['resource']['status'] == 'removed'
    return old, later


async def test_read_and_result_show_later_cleanup_without_replaying_or_rewriting(app, queue, backend, monkeypatch):
    old, later = await recovered(app, queue, backend, monkeypatch)
    journal = copy.deepcopy(app.operations.requests.list(old['senderSessionId']))
    calls = copy.deepcopy(backend)
    listed = (await app.dispatch('workspace.tests.read', scope(app)))['result']['items'][0]
    result = (await app.dispatch('workspace.tests.result', {**scope(app), 'requestId': 'check'}))['result']['check']
    for value in (listed, result, TestQueue(app).public(old)):
        assert value['status'] == 'unknown'
        proof = value['recoveredOutcome']
        assert proof['outcome'] == 'passed' and proof['qualified'] is False
        assert proof['cleanup']['requestId'] == later['request']['requestId']
        assert proof['retentionVerified'] is True
    assert record(queue, app) == old and backend == calls
    assert app.operations.requests.list(old['senderSessionId']) == journal


async def test_cleanup_does_not_invent_a_missing_check_outcome(app, queue, backend, monkeypatch):
    old, _ = await recovered(app, queue, backend, monkeypatch, uncertain_check=True)
    proof = queue.public(old)['recoveredOutcome']
    assert proof['outcome'] == 'unknown' and proof['cleanup']['environmentState'] == 'removed'


@pytest.mark.parametrize('change', [
    {'owner': {'sessionId': 'other', 'principal': 'other'}},
    {'commandId': 'another-launch'}, {'backendKey': 'another-backend'},
    {'source': '/different-source'}, {'profileId': 'different-profile'},
    {'universeId': 'another-universe'}, {'status': 'allocated'},
    {'environmentState': 'stopped'}, {'pendingRequest': 'unsettled'},
    {'pendingRecovery': 'unsettled'}, {'preparationPending': True},
])
async def test_unbound_or_unsettled_cleanup_has_no_recovered_claim(app, queue, backend, monkeypatch, change):
    old, _ = await recovered(app, queue, backend, monkeypatch)
    row = environments.get(app.data_dir, old['senderSessionId'], old['job']['resourceId'])
    environments.save(app.data_dir, row, **change)
    assert 'recoveredOutcome' not in queue.public(old)


@pytest.mark.parametrize('uncertain', [False, True])
async def test_cancellation_intent_does_not_replace_recorded_check_outcome(app, queue, backend, monkeypatch, uncertain):
    old, _ = await recovered(app, queue, backend, monkeypatch, uncertain_check=uncertain)
    old['job']['cancelRequested'] = True
    queue.save(old)
    proof = queue.public(old)['recoveredOutcome']
    assert proof['outcome'] == ('unknown' if uncertain else 'passed')
    assert proof['cancelRequested'] is True


async def test_changed_cleanup_receipt_has_no_recovered_claim(app, queue, backend, monkeypatch):
    old, later = await recovered(app, queue, backend, monkeypatch)
    app.operations.requests.finish(later['request'], 'outcome_unknown')
    assert 'recoveredOutcome' not in queue.public(old)


@pytest.mark.parametrize('guest', [{'id': 'other-check', 'terminal': True, 'state': 'passed'},
                                  {'terminal': False, 'state': 'passed'},
                                  {'terminal': True, 'state': 'unknown'}])
async def test_missing_exact_terminal_job_does_not_invent_success(app, queue, backend, monkeypatch, guest):
    old, _ = await recovered(app, queue, backend, monkeypatch)
    check = app.operations.requests.read(old['senderSessionId'], old['requestId'] + ':execute')
    result = {**check['result'], 'job': {**check['result']['job'], **guest}}
    app.operations.requests.finish(check, 'completed', result=result)
    assert queue.public(old)['recoveredOutcome']['outcome'] == 'unknown'


async def test_exact_terminal_cancelled_guest_is_reported(app, queue, backend, monkeypatch):
    old, _ = await recovered(app, queue, backend, monkeypatch)
    check = app.operations.requests.read(old['senderSessionId'], old['requestId'] + ':execute')
    result = {**check['result'], 'ok': False, 'job': {**check['result']['job'], 'state': 'cancelled'}}
    app.operations.requests.finish(check, 'failed', result=result)
    assert queue.public(old)['recoveredOutcome']['outcome'] == 'cancelled'


@pytest.mark.parametrize('result', [
    {'ok': False, 'result': {'exit_code': 0}}, {'result': {'exit_code': 0}},
    {'ok': True}, {'ok': True, 'result': {'exit_code': 1}},
    {'ok': True, 'result': {'exit_code': False}},
])
async def test_legacy_completed_receipt_needs_positive_check_evidence(app, queue, backend, monkeypatch, result):
    old, _ = await recovered(app, queue, backend, monkeypatch)
    old['job'].pop('durableVersion')
    queue.save(old)
    check = app.operations.requests.read(old['senderSessionId'], old['requestId'] + ':execute')
    app.operations.requests.finish(check, 'completed', result=result)
    assert queue.public(old)['recoveredOutcome']['outcome'] == 'unknown'
