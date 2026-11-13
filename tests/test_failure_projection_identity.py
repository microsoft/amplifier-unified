"""Process readiness is not recovery of a failed peer/native generation."""
import asyncio
import copy
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from amplifier_operations.coordination import peer_input
from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.session_health import current_terminal_failure
from test_collaborative_workspaces import app, generation, settled, until
from test_peer_creation_operation import plan, commission, initial_accepted
from test_retained_failure_recovery import historical_failure, submit


async def terminal(app, target, *, identity='failed-generation', inputs=None):
    inputs = inputs or ['peer-input', 'native-delegate-observation']
    await generation(app, target, identity, inputs)
    await app.on_runtime_event(*normalize_event({
        'type': 'generation.failed', 'generation_id': identity, 'input_ids': inputs,
        'error_type': 'ValueError', 'error_category': 'unknown',
        'error_stage': 'manager_turn'}, target['id']))


@pytest.mark.parametrize('stale_ui_turn', [False, True])
async def test_peer_terminal_survives_generic_exit_with_native_observation(app, stale_ui_turn):
    target = app.state['sessions'][1]
    if stale_ui_turn:
        target['execution'] = {'currentTurnId': 'old-ui-turn', 'turns': [
            {'id': 'old-ui-turn', 'phase': 'completed', 'startedAt': 1, 'endedAt': 2}], 'nodes': []}
    await terminal(app, target)
    before = copy.deepcopy(target['failure'])
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'error': 'Manager turn failed', 'errorType': 'RuntimeError'})
    assert target['failure'] == before
    assert target['failure']['inputIds'] == ['peer-input', 'native-delegate-observation']
    assert target['errorAt'] == before['recordedAt']
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='native-delegate-observation'").fetchone()
    assert not app.runtime.inputs


@pytest.mark.parametrize('change', ['failure-generation', 'failure-input', 'active-generation',
                                  'active-input', 'completed', 'later-root', 'child-only'])
async def test_terminal_retention_requires_the_current_exact_root(app, change):
    target = app.state['sessions'][1]
    await terminal(app, target)
    assert current_terminal_failure(target)
    if change == 'failure-generation': target['failure']['generationId'] = 'older'
    elif change == 'failure-input': target['failure']['inputIds'] = ['older']
    elif change == 'active-generation': target['collaborationGeneration']['id'] = 'newer'
    elif change == 'active-input': target['collaborationGeneration']['inputIds'] = ['newer']
    elif change == 'completed': target['collaborationGeneration']['completed'] = True
    elif change == 'later-root':
        target['generations'].append({'event': 'generation.started', 'generation_id': 'newer'})
    else:
        target['generations'][-1]['sessionId'] = 'child'
    before = copy.deepcopy(target)
    assert not current_terminal_failure(target)
    assert target == before


@pytest.mark.parametrize('bound,event', [(False, 'generation.failed'),
                                       (False, 'generation.detached'), (True, 'generation.failed')])
async def test_warming_preserves_recovery_evidence_until_exact_admission(app, bound, event):
    source, target, human, old_input = await historical_failure(app, event, native=not bound)
    if bound:
        await terminal(app, target, identity='generation-helper', inputs=[old_input])
        await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
            'error': 'Manager turn failed', 'errorType': 'RuntimeError'})
        await app.on_runtime_event('runtime.ended', {'sessionId': target['id']})
    failure = copy.deepcopy(target['failure'])
    old_receipt = app.db.execute('SELECT receipt FROM commands WHERE id=?', (old_input,)).fetchone()
    assignment = copy.deepcopy(app.assignments.current(source))
    original = app.runtime.collaboration_input
    continuation = app.collaboration.receipt(old_input)['subscription']['continuationId'] if bound else None

    async def warm_then_admit(session, args, guard, emit):
        if (session['id'], args['inputId']) != (target['id'], 'resume-member'):
            # Only the retained failed-generation return may reach the other
            # recipient. Do not simulate member warming for that notification.
            assert bound and (session['id'], args['inputId']) == (source['id'], continuation)
            notice = app.collaboration.receipt(continuation)
            assert notice['origin'] == 'system' and notice['mode'] == 'auto'
            assert notice['dependencyRequestId'] == old_input and notice['dependencySatisfied'] is False
            assert args['automaticDelivery'] is True and guard() is None
            return await original(session, args, guard, emit)
        assert guard() is None
        await emit('runtime.status', {'sessionId': target['id'], 'status': 'starting'})
        await emit('runtime.status', {'sessionId': target['id'], 'status': 'ready'})
        await emit('runtime.warmth', {'sessionId': target['id'], 'status': 'warm'})
        assert target['failure'] == failure
        assert guard() is None
        return await original(session, args, guard, emit)

    app.runtime.collaboration_input = warm_then_admit
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    assert receipt['humanResume']['failedRecovery']['failure'] == failure
    assert target['failure'] == failure
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', (old_input,)).fetchone() == old_receipt
    assert app.assignments.current(source) == assignment
    expected = [(target['id'], 'resume-member')]
    if bound:
        # historical_failure deliberately holds the unrelated manager return.
        # This case also asserts that return, so release its synthetic boundary
        # after target recovery rather than waiting forever for an unavailable one.
        await until(lambda: source['id'] not in app.collaboration.draining)
        app.runtime.unavailable_boundaries.remove(source['id'])
        await app.collaboration.drain(source['id'])
        notice = await settled(app, continuation, 'accepted')
        assert notice['requestId'] == notice['inputId'] == continuation
        assert notice['senderSessionId'] == target['id'] and notice['target']['sessionId'] == source['id']
        assert notice['origin'] == 'system' and notice['mode'] == 'auto'
        assert notice['dependencyRequestId'] == old_input and notice['dependencySatisfied'] is False
        assert notice['continuationReason'] == 'failed'
        assert notice['outcomeSnapshot']['status'] == 'observed'
        assert notice['outcomeSnapshot']['outcome'] == 'failed' and notice['outcomeSnapshot']['qualified'] is False
        assert notice['admission']['accepted'] is True
        message, = [row for row in source['messages'] if row.get('inputId') == continuation]
        assert message['peerEnvelope']['replyToRequestId'] == old_input
        assert message['peerEnvelope']['recipientSessionId'] == source['id']
        delivered, = [row for row in app.runtime.inputs if row[:2] == (source['id'], continuation)]
        assert delivered[2] == peer_input(message['peerEnvelope'], message['text'])
        expected.append((source['id'], continuation))
    assert sorted((sid, identity) for sid, identity, _ in app.runtime.inputs) == sorted(expected)
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', (old_input,)).fetchone() == old_receipt
    assert target['failure'] == failure and app.assignments.current(source) == assignment
    await generation(app, target, 'fresh-generation', ['resume-member'])
    assert not target.get('failure') and not target.get('error')


async def test_fresh_operator_input_does_not_replay_or_relabel_failed_input(app):
    target = app.state['sessions'][1]
    await terminal(app, target)
    before = copy.deepcopy(target['failure'])
    target['status'] = 'error'
    await app.dispatch('conversation.send', {'sessionId': target['id'],
        'text': 'Inspect saved results before continuing remaining work.', 'preserveDraft': True},
        command_id='fresh-operator-request')
    assert app.runtime.inputs == [(target['id'], 'fresh-operator-request',
        'Inspect saved results before continuing remaining work.')]
    assert target['failure'] == before
    await generation(app, target, 'new-operator-generation', ['fresh-operator-request'])
    assert not target.get('failure') and not target.get('error')
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'error': 'A different failure', 'errorType': 'RuntimeError'})
    assert target['failure']['errorType'] == 'RuntimeError'
    assert 'generationId' not in target['failure']


async def test_ready_still_clears_an_actual_startup_failure(app):
    target = app.state['sessions'][1]
    await terminal(app, target)
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'error': 'Worker could not start', 'errorType': 'RuntimeStartupError'})
    assert target['failure']['category'] == 'worker_startup'
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'ready'})
    assert not target.get('failure') and not target.get('error')


async def startup_history(app, event, native):
    """Seed saved history through service actions before replacing the fake peer."""
    source = app.state['sessions'][0]
    plan(app, source)
    # This test owns only target recovery, not an unrelated manager notification.
    created = await commission(app, source, returnNotification=False)
    await initial_accepted(app, created['initialInputId'])
    target = app._session(created['sessionId'])
    old_input = 'native-observation' if native else created['initialInputId']
    await generation(app, target, 'old-generation', [old_input])
    await app.on_runtime_event(*normalize_event({
        'type': event, 'generation_id': 'old-generation', 'input_ids': [old_input],
        'error_type': 'ValueError'}, target['id']))
    await app.on_runtime_event('runtime.error', {
        'sessionId': target['id'], 'error': 'Worker exited; old effects remain unknown.'})
    await app.on_runtime_event('runtime.ended', {'sessionId': target['id']})
    assert current_terminal_failure(target) and 'generationId' not in target['failure']
    human = app._message(source, 'user', 'Reconcile saved work and continue.',
                         inputId='fresh-human-recovery', inputOrigin='ui')
    await generation(app, source, 'fresh-owner-generation', ['fresh-human-recovery'])
    return source, target, human, old_input


@asynccontextmanager
async def startup_adapter(app, target, tmp_path, monkeypatch):
    """Use the production adapter; replace only its subprocess/provider peer."""
    from amplifier_web.management import Management
    app.management = Management(app)
    log = tmp_path / 'worker-commands.jsonl'
    runtime = RuntimeManager(command=[sys.executable, '-I', '-u',
        str(Path(__file__).parent / 'fixtures/recovery_startup_worker.py'), str(log)],
        startup_timeout=5)
    before_ready, release_ready = asyncio.Event(), asyncio.Event()
    before_ack, release_ack = asyncio.Event(), asyncio.Event()
    observations, guards, admissions = [], [], []
    project, guard = app.on_runtime_event, app.collaboration.guard

    async def bridge(operation, args, sid):
        assert sid == target['id']
        if operation == 'fixture.before_ready':
            before_ready.set()
            await release_ready.wait()
            return {}
        if operation == 'fixture.before_ack':
            before_ack.set()
            await release_ack.wait()
            return {}
        assert operation == 'coordination.admit'
        result = await app.app_bridge(operation, args, sid)
        admissions.append(copy.deepcopy(result))
        return result

    async def observe(kind, payload):
        await project(kind, payload)
        if payload.get('sessionId') == target['id']:
            observations.append((kind, payload.get('status'), copy.deepcopy(target.get('failure'))))

    def observe_guard(receipt):
        # Distinguish the adapter's two calls from drain and host bridge checks.
        if sys._getframe(2).f_code is RuntimeManager.collaboration_input.__code__:
            row = runtime.workers.get(target['id'], {})
            guards.append((bool(row.get('ready') and row['ready'].done()),
                           runtime._locks.get(target['id'], asyncio.Lock()).locked()))
        return guard(receipt)

    runtime.app_bridge = bridge
    monkeypatch.setattr(app, 'on_runtime_event', observe)
    monkeypatch.setattr(app.collaboration, 'guard', observe_guard)
    await app.replace_runtime(runtime)
    try:
        yield (runtime, before_ready, release_ready, before_ack, release_ack,
               observations, guards, admissions, log)
    finally:
        release_ready.set()
        release_ack.set()
        processes = [row['process'] for row in runtime.workers.values()]
        await runtime.close()
        assert all(process.returncode == 0 for process in processes)
        assert not runtime.workers and not runtime._preparations


@pytest.mark.parametrize('event,native', [
    ('generation.failed', False), ('generation.failed', True), ('generation.detached', True)])
async def test_real_startup_preserves_failure_through_exact_admission(app, tmp_path, monkeypatch, event, native):
    source, target, human, old_input = await startup_history(app, event, native)
    failure = copy.deepcopy(target['failure'])
    terminal_before = copy.deepcopy(target['generations'])
    old_receipt = app.db.execute('SELECT receipt FROM commands WHERE id=?', (old_input,)).fetchone()
    assignment = copy.deepcopy(app.assignments.current(source))
    async with startup_adapter(app, target, tmp_path, monkeypatch) as adapter:
        runtime, before_ready, release_ready, before_ack, release_ack, events, guards, admissions, log = adapter
        await submit(app, source, target, human)
        await asyncio.wait_for(before_ready.wait(), 5)
        assert target['status'] == 'starting' and target['failure'] == failure
        assert guards == [(False, False)]
        assert not admissions
        release_ready.set()
        await until(lambda: before_ack.is_set() or
                    app.collaboration.receipt('resume-member')['delivery'] not in {'queued', 'submitting'})
        assert before_ack.is_set(), app.collaboration.receipt('resume-member')
        assert guards == [(False, False), (True, True)]
        assert len(admissions) == 1 and admissions[0]['admitted']
        assert admissions[0]['humanResume']
        assert target['failure'] == failure and target['generations'] == terminal_before
        assert ('runtime.status', 'ready', failure) in events
        assert app.db.execute('SELECT receipt FROM commands WHERE id=?', (old_input,)).fetchone() == old_receipt
        release_ack.set()
        receipt = await settled(app, 'resume-member', 'accepted')
        assert receipt['humanResume']['failedRecovery']['failure'] == failure
        duplicate = await submit(app, source, target, human)
        assert duplicate['duplicate']
        await until(lambda: target['id'] not in app.collaboration.draining)
        await app.collaboration.drain(target['id'])  # A settled accepted receipt cannot re-enter admission.
        commands = [json.loads(line) for line in log.read_text().splitlines()]
        assert [row['op'] for row in commands] == ['start', 'control']
        assert commands[1]['operation'] == 'coordination.submit'
        assert commands[1]['arguments']['inputId'] == 'resume-member'
        assert app.assignments.current(source) == assignment
        assert target['failure'] == failure
        await runtime.control(target['id'], 'fixture.generation', {})
        await until(lambda: (target.get('collaborationGeneration') or {}).get('id') == 'fresh-generation')
        assert not target.get('failure') and not target.get('error')
        assert app.db.execute('SELECT receipt FROM commands WHERE id=?', (old_input,)).fetchone() == old_receipt
        commands = [json.loads(line) for line in log.read_text().splitlines()]
        assert [row.get('arguments', {}).get('inputId') for row in commands
                if row.get('operation') == 'coordination.submit'] == ['resume-member']
        assert len(admissions) == 1


@pytest.mark.parametrize('change,reason', [
    ('human', 'human resume instruction'), ('stop', 'interrupted'),
    ('assignment', 'assignment changed'), ('access', 'file-access'),
    ('terminal', 'terminal generation changed'), ('safety', 'safety-related'),
    ('task', 'task changed'), ('ownership', 'native owner')])
async def test_real_startup_rechecks_changed_authority_before_transport(app, tmp_path, monkeypatch, change, reason):
    source, target, human, old_input = await startup_history(app, 'generation.detached', True)
    async with startup_adapter(app, target, tmp_path, monkeypatch) as adapter:
        runtime, before_ready, release_ready, _, release_ack, events, guards, admissions, log = adapter
        await submit(app, source, target, human)
        await asyncio.wait_for(before_ready.wait(), 5)
        # Change canonical facts while start still owns its admission lock.
        # No mock of collaboration_input, start, _read, _admit or projection.
        if change == 'human':
            app._message(source, 'user', 'A different request', inputId='later-human', inputOrigin='ui')
        elif change == 'stop':
            target['interruptionRevision'] = target.get('interruptionRevision', 0) + 1
        elif change == 'assignment':
            assignment = app.assignments.current(source)
            assignment['revision'] += 1
            app.assignments.save(assignment)
            app.db.commit()
        elif change == 'access':
            target['fileAccessPending'] = True
        elif change == 'task':
            target['task'] = {'id': 'new-task', 'revision': 1, 'status': 'active'}
        elif change == 'ownership':
            target['ownership'] = {'status': 'blocked'}
        elif change == 'terminal':
            target['generations'][-1]['at'] += 1
        else:
            target['generations'][-1]['error_category'] = 'content_filter'
        release_ack.set()
        release_ready.set()
        receipt = await settled(app, 'resume-member', 'suppressed')
        assert receipt['admission']['accepted'] is False
        assert reason in receipt['admission']['reason'].lower()
        assert guards == [(False, False), (True, True)]
        assert not admissions
        assert [json.loads(line)['op'] for line in log.read_text().splitlines()] == ['start']


async def test_real_ready_clears_only_actual_startup_failure(app, tmp_path, monkeypatch):
    _, target, _, _ = await startup_history(app, 'generation.failed', True)
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'error': 'Worker could not start', 'errorType': 'RuntimeStartupError'})
    assert target['failure']['category'] == 'worker_startup'
    async with startup_adapter(app, target, tmp_path, monkeypatch) as adapter:
        runtime, before_ready, release_ready, _, _, events, _, admissions, log = adapter
        starting = asyncio.create_task(runtime.start(target, app.on_runtime_event))
        try:
            await asyncio.wait_for(before_ready.wait(), 5)
            release_ready.set()
            await asyncio.wait_for(starting, 5)
            await until(lambda: ('runtime.status', 'ready', None) in events)
            assert not target.get('failure') and not target.get('error')
            assert not admissions
            assert [json.loads(line)['op'] for line in log.read_text().splitlines()] == ['start']
        finally:
            release_ready.set()
            await asyncio.gather(starting, return_exceptions=True)
