"""Real queue/resource/operation journals with controlled guest observations."""
# Reused pytest fixtures are imported by name and requested as test arguments.
# ruff: noqa: F401, F811
import asyncio

import pytest

from amplifier_web import task_environments as environments
from amplifier_web.test_queue import TestQueue
from test_configuration_authority import app
from test_coordination_generation import until
from test_task_environments import backend, scope
from test_test_queue import drive, queue, record, submit


@pytest.fixture
def running(monkeypatch, backend):
    invoke = environments.invoke
    states, starts = {}, []
    def guest(config, home, row, action, args):
        result = invoke(config, home, row, action, args)
        identity = args.get('requestId')
        if action == 'execute' and args.get('durableJob'):
            assert identity not in starts
            starts.append(identity)
            states[identity] = 'running'
        if action == 'job_cancel':
            states[identity] = 'cancelled'
        if action in {'execute', 'job_read', 'job_cancel'} and identity in states:
            state = states[identity]
            result['job'] = {'version': 1, 'id': identity, 'state': state,
                             'terminal': state in {'passed', 'failed', 'cancelled', 'timed_out'}}
            result['result']['exit_code'] = 0 if state == 'passed' else None
        return result
    monkeypatch.setattr(environments, 'invoke', guest)
    return states, starts


async def admitted(queue, app):
    await queue.tick()
    await until(lambda: record(queue, app)['job']['status'] == 'checking')
    await until(lambda: record(queue, app)['job'].get('check', {}).get('state') == 'running')


async def test_restart_during_check_observes_same_job_without_cleanup_or_replay(app, queue, running, backend):
    states, starts = running
    await submit(app, profileId='browser-test', timeoutSeconds=1200)
    await admitted(queue, app)
    receipt = record(queue, app)
    assert receipt['job']['profileId'] == 'browser-test'
    assert receipt['job']['timeoutSeconds'] == 1200
    row = environments.get(app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
    assert row['profileId'] == 'browser-test'
    await queue.close()  # Host disappears; the independently admitted guest runs.
    assert len(starts) == 1 and not any(action == 'stop' for action, _ in backend)
    recovered = app.test_queue = TestQueue(app)
    states[starts[0]] = 'passed'
    await drive(recovered)
    assert record(recovered, app)['job']['status'] == 'passed'
    assert len(starts) == 1
    assert [action for action, _ in backend].count('job_read') == 1
    assert [action for action, _ in backend][-2:] == ['stop', 'remove']


async def test_cancel_observes_terminal_before_cleanup(app, queue, running, backend):
    await submit(app, profileId='python-project', timeoutSeconds=1800)
    await admitted(queue, app)
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
    await asyncio.wait_for(asyncio.gather(*queue.tasks.values()), 4)
    assert record(queue, app)['job']['status'] == 'cancelled'
    actions = [action for action, _ in backend]
    assert actions.index('job_cancel') < actions.index('stop') < actions.index('remove')
    assert len(running[1]) == 1


async def test_missing_supervisor_retains_owner_and_never_cleans_unknown_job(app, queue, running, backend):
    await submit(app)
    await admitted(queue, app)
    running[0][running[1][0]] = 'unknown'
    await asyncio.wait_for(asyncio.gather(*queue.tasks.values()), 4)
    receipt = record(queue, app)
    assert receipt['job']['status'] == 'unknown'
    row = environments.get(app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
    assert row['status'] != 'retired'
    assert not any(action in {'stop', 'remove'} for action, _ in backend)
    await submit(app)
    assert len(running[1]) == 1


async def test_two_root_jobs_keep_independent_identity_and_cancel(app, queue, running, backend):
    await submit(app)
    await admitted(queue, app)
    peer = app.state['sessions'][1]
    args = {**scope(app), 'sessionId': peer['id'], 'requestId': 'check',
            'profileId': 'linux-python', 'command': 'true', 'notify': False}
    await app.dispatch('workspace.tests.submit', args)
    await queue.tick()
    await until(lambda: len(running[1]) == 2)
    first, second = running[1]
    assert first != second
    running[0][second] = 'passed'
    await app.dispatch('workspace.tests.cancel', {**scope(app), 'requestId': 'check'})
    await asyncio.wait_for(asyncio.gather(*queue.tasks.values()), 4)
    assert record(queue, app)['job']['status'] == 'cancelled'
    assert queue.get(queue.identity(peer['id'], 'check'))['job']['status'] == 'passed'


async def test_guest_admission_lost_response_recovers_exact_result(app, queue, running, backend, monkeypatch):
    invoke = environments.invoke
    def lost(config, home, row, action, args):
        result = invoke(config, home, row, action, args)
        if action == 'execute':
            running[0][args['requestId']] = 'passed'
            raise TimeoutError('Lost durable admission reply')
        return result
    monkeypatch.setattr(environments, 'invoke', lost)
    await submit(app)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert len(running[1]) == 1
    assert [action for action, _ in backend].count('job_read') == 1


async def test_profile_timeout_limit_is_enforced_before_any_admission(app, queue, backend):
    with pytest.raises(ValueError, match='duration exceeds'):
        await submit(app, timeoutSeconds=301)
    assert not backend and not queue.rows()


async def test_old_receipt_uses_original_execute_and_recovery_path(app, queue, backend):
    await submit(app)
    saved = record(queue, app)
    saved['job'].pop('durableVersion')
    queue.save(saved)
    await drive(queue)
    assert record(queue, app)['job']['status'] == 'passed'
    assert [action for action, _ in backend] == ['prepare', 'start', 'execute', 'stop', 'remove']
    execute = next(args for action, args in backend if action == 'execute')
    assert 'durableJob' not in execute


async def test_direct_execute_cannot_overlap_running_queued_job(app, queue, running):
    await submit(app)
    await admitted(queue, app)
    receipt = record(queue, app)
    row = environments.get(app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
    args = {'requestId': 'overlap', 'expectedRevision': row['revision'], 'command': 'true', 'timeoutSeconds': 1}
    actor = receipt['job']['actor']
    request, _ = app.operations.requests.begin(row['sessionId'], 'workspace.environments.execute', args, actor)
    result = await asyncio.to_thread(environments.effect, app, row, 'execute', args,
        environments.configuration(app)[0], request, lambda: None)
    assert result['request']['state'] == 'failed'
    assert len(running[1]) == 1
    running[0][running[1][0]] = 'passed'
    await asyncio.gather(*queue.tasks.values())


async def test_passive_environment_read_does_not_call_running_guest_abandoned(app, queue, running):
    await submit(app)
    await admitted(queue, app)
    receipt = record(queue, app)
    identity = running[1][0]
    await asyncio.to_thread(environments.read, app, receipt['senderSessionId'], environments.configuration(app)[0])
    row = environments.get(app.data_dir, receipt['senderSessionId'], receipt['job']['resourceId'])
    assert row['pendingRequest'] == identity
    assert row['lastOperation']['state'] == 'running'
    assert row['environmentState'] == 'running'
    running[0][identity] = 'passed'
    await asyncio.gather(*queue.tasks.values())
