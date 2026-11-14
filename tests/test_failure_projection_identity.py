"""Process readiness is not recovery of a failed peer/native generation."""
import copy

import pytest

from amplifier_operations.coordination import peer_input
from amplifier_web.runtime import normalize_event
from amplifier_web.session_health import current_terminal_failure
from test_collaborative_workspaces import app, generation, settled
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
        # The fixture explicitly held the unrelated manager return at an
        # unavailable boundary. Make that boundary available before expecting
        # delivery; warming the member is not proof that its manager can steer.
        app.runtime.unavailable_boundaries.discard(source['id'])
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


async def observed(app, target, kind, gen='saved-generation', inputs=None, **extra):
    await app.on_runtime_event('runtime.generation', {
        'sessionId': target['id'], 'rootSessionId': target['id'],
        'event': 'generation.' + kind, 'generation_id': gen,
        'observedAt': 10 if kind == 'started' else 20,
        **({'initial_input_id': (inputs or ['saved-input'])[0]} if kind == 'started'
           else {'input_ids': inputs or ['saved-input']}),
        'disposition': 'manager_turn_finished', **extra})
    if kind == 'started':
        for exact in inputs or ['saved-input']:
            await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'working',
                'event': 'input.delivered', 'inputId': exact})


@pytest.mark.parametrize('error_type', ['WorkerExitedError', 'WorkerCommunicationError', 'RuntimeError'])
async def test_saved_request_survives_later_availability_fault_and_ordinary_send(app, error_type):
    from amplifier_web.execution import ensure_turn
    from amplifier_web.input_status import annotate
    target = app.state['sessions'][1]
    ensure_turn(target, 'saved-input')
    await observed(app, target, 'started')
    await observed(app, target, 'finished')
    before = copy.deepcopy(annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus'])
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'errorType': error_type, 'error': 'activation superseded after the response checkpoint'})
    assert not target.get('error') and not target.get('failure')
    assert target['execution']['turns'][0]['phase'] == 'completed'
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus'] == before
    assert target['runtimeEvents'][-1]['errorType'] == error_type
    assert not [row for row in app.get_state()['attention']['items'] if row['id'] == 'session:' + target['id']]
    assert not app.runtime.inputs  # Observation/reconnect does not resend anything.
    await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'New request'},
                       command_id='new-human-input')
    assert app.runtime.inputs == [(target['id'], 'new-human-input', 'New request')]


@pytest.mark.parametrize('fault', ['persistence', 'pending', 'jobs'])
async def test_finish_is_not_blanket_save_or_pending_work_assurance(app, fault):
    from amplifier_web.execution import ensure_turn
    from amplifier_web.input_status import annotate
    target = app.state['sessions'][1]
    ensure_turn(target, 'saved-input')
    await observed(app, target, 'started')
    await observed(app, target, 'finished', **({'active_job_ids': ['job']} if fault == 'jobs' else {}))
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})
    if fault == 'pending':
        ensure_turn(target, 'next-input')
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'error': 'not assured',
        'errorType': 'RuntimeError' if fault in {'persistence', 'unknown'} else 'WorkerExitedError',
        **({'event': 'persistence.failed'} if fault == 'persistence' else {})})
    assert target.get('failure') and target.get('error')
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus']['phase'] == (
        'unknown' if fault == 'jobs' else 'done')
    if fault == 'persistence':
        assert target['failure']['category'] == 'persistence'
        assert 'could not be saved' in target['failure']['summary']
    elif fault == 'pending':
        assert target['execution']['turns'][-1]['phase'] == 'error'


async def test_exact_failure_stays_with_its_input_after_next_generation(app):
    from amplifier_web.input_status import annotate
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    await observed(app, target, 'failed', error_type='ValueError', error_category='unknown')
    saved = copy.deepcopy(target['failure'])
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus']['failure'] == saved
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'ready'})
    assert target['failure'] == saved
    await observed(app, target, 'started', gen='fresh', inputs=['next-input'])
    assert not target.get('failure') and not target.get('error')
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus']['failure'] == saved
    # Old terminals cannot poison the new request, even if they arrive last.
    await observed(app, target, 'failed', error_type='ValueError', error_category='unknown')
    assert not target.get('failure') and not target.get('error')
    assert target['collaborationGeneration']['id'] == 'fresh'


@pytest.mark.parametrize('status', ['stopped', 'stopping'])
async def test_late_runtime_fault_does_not_erase_actual_stop(app, status):
    target = app.state['sessions'][1]
    target['status'] = status
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'errorType': 'RuntimeError', 'event': 'persistence.failed', 'error': 'save failed'})
    assert target['status'] == status and not target.get('failure')
    assert target['runtimeEvents'][-1]['event'] == 'persistence.failed'


async def test_old_runtime_fault_cannot_settle_newer_live_turn(app):
    from amplifier_web.execution import ensure_turn
    target = app.state['sessions'][1]
    ensure_turn(target, 'saved-input')
    await observed(app, target, 'started')
    await observed(app, target, 'finished')
    ensure_turn(target, 'next-input')
    await observed(app, target, 'started', gen='fresh', inputs=['next-input'])
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'generationId': 'saved-generation',
        'errorType': 'RuntimeError', 'error': 'old process fault'})
    assert not target.get('failure') and not target.get('error')
    assert target['status'] == 'working'
    assert target['execution']['turns'][-1]['phase'] == 'running'


async def test_original_known_terminal_frames_enrich_exact_request_without_reclassifying(app):
    from amplifier_web.input_status import annotate
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    await observed(app, target, 'failed', error_type='ValueError', error_category='invalid_request')
    before = copy.deepcopy(target['failure'])
    capture = {'generationId': 'saved-generation', 'inputIds': ['saved-input'], 'errorType': 'ValueError',
        'exceptionChain': [{'errorType': 'ValueError', 'frames': [
            {'file': 'runtime.py', 'function': 'execute', 'line': 42}]}]}
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'terminalFailure': capture})
    assert target['failure'] == {**before, 'exceptionChain': capture['exceptionChain']}
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus']['failure'] == target['failure']


@pytest.mark.parametrize('late', ['failed', 'detached'])
async def test_late_same_generation_terminal_cannot_relabel_completed_request(app, late):
    from amplifier_web.input_status import annotate
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    await observed(app, target, 'finished')
    target['messages'].append({'id': 'saved-response', 'role': 'assistant',
                              'generationId': 'saved-generation', 'response': {'status': 'completed'}})
    before = copy.deepcopy(annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus'])
    await observed(app, target, late, error_type='ValueError', error_category='unknown')
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus'] == before
    assert target['messages'][-1]['response']['status'] == 'completed'
    assert not target.get('failure') and not target.get('error')


async def test_rejected_duplicate_start_cannot_make_saved_request_current_again(app):
    from amplifier_web.execution import ensure_turn
    target = app.state['sessions'][1]
    ensure_turn(target, 'saved-input')
    await observed(app, target, 'started')
    await observed(app, target, 'finished')
    await observed(app, target, 'started')
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'errorType': 'RuntimeError'})
    assert not target.get('failure') and not target.get('error')
    assert target['execution']['turns'][0]['phase'] == 'completed'


async def test_handled_provider_error_does_not_explain_unrelated_runtime_fault(app):
    from amplifier_web.execution import ensure_turn
    target = app.state['sessions'][1]
    ensure_turn(target, 'saved-input')
    await observed(app, target, 'started')
    await app.on_runtime_event('execution.event', {'sessionId': target['id'], 'rootSessionId': target['id'],
        'id': 'old-provider-error', 'turnId': 'saved-input', 'generationId': 'saved-generation',
        'kind': 'llm', 'phase': 'error', 'failure': {'category': 'authentication', 'errorType': 'AuthenticationError'}})
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'errorType': 'TypeError',
        'executionFailure': {'runtimeSessionId': target['id'], 'stage': 'execution',
            'generationId': 'saved-generation', 'exceptionChain': [{'errorType': 'TypeError', 'frames': []}]}})
    assert target['failure']['category'] == 'unknown'
    assert target['failure']['errorType'] == 'TypeError'
    assert target['failure']['exceptionChain'][0]['errorType'] == 'TypeError'


@pytest.mark.parametrize('late', ['failed', 'detached'])
async def test_delayed_final_message_uses_first_accepted_terminal(app, late):
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    await observed(app, target, 'finished',
        finalMessageRefs={'version': 1, 'generationId': 'saved-generation', 'sequences': [10]})
    await observed(app, target, late, error_type='ValueError', error_category='unknown')
    message = {'sessionId': target['id'], 'generationId': 'saved-generation',
               'inputId': 'saved-input', 'runtimeMessage': {'sequence': 10}, 'text': 'Saved final'}
    await app.on_runtime_event('assistant.message', message)
    assert target['messages'][-1]['response']['status'] == 'completed'
    await app.on_runtime_event('assistant.message', message)
    assert target['messages'][-1]['response']['status'] == 'completed'


@pytest.mark.parametrize('state', ['active', 'finished', 'stopped'])
@pytest.mark.parametrize('callback,identity', [
    ('input', {'input_id': 'rejected-original-input'}),
    ('child_report', {'child_session_id': 'rejected-child', 'call_id': 'rejected-call', 'report_index': 3}),
    ('child_event', {'child_session_id': 'rejected-child', 'call_id': 'rejected-call', 'report_index': 0}),
    ('bundle_turn', {}),
    ('bundle_job', {'job_id': 'rejected-job', 'call_id': 'rejected-call'}),
])
async def test_callback_rejection_reaches_local_inspection_without_replaying_or_relabeling(app, state, callback, identity):
    import json
    from amplifier_web.execution import ensure_turn
    from amplifier_web.feedback_diagnostics import failure_report
    from amplifier_web.input_status import annotate
    from test_collaborative_workspaces import until

    target = app.state['sessions'][1]
    ensure_turn(target, 'saved-input')
    await observed(app, target, 'started')
    if state == 'finished':
        await observed(app, target, 'finished')
    elif state == 'stopped':
        await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='actual-stop')
        await until(lambda: target['status'] == 'stopped')
    before = copy.deepcopy(annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus'])
    commands = app.db.execute('SELECT id, receipt FROM commands ORDER BY id').fetchall()
    metadata = {'callback_kind': callback, 'reason': 'invalid_activation',
                'delivery': 'not_admitted', 'outcome': 'unknown', **identity}
    # Real Runtime.emit attaches session provenance, not a command generation.
    event = normalize_event({'type': 'command.rejected', 'session_id': target['id'],
                             **metadata, 'payload': 'PRIVATE'}, target['id'], 'saved-input')
    assert 'generationId' not in event[1]
    await app.on_runtime_event(*event)
    stored = target['runtimeEvents'][-1]
    assert {key: stored[key] for key in metadata} == metadata
    assert stored['generationId'] == 'saved-generation'  # Existing host store correlation.
    if state == 'active':
        assert target['status'] == 'error' and target.get('failure')
        status = annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus']
        assert status['phase'] == 'unknown'  # Not_admitted is not proof of no earlier effects.
    else:
        assert not target.get('failure') and not target.get('error')
        assert target['status'] == ('stopped' if state == 'stopped' else 'idle')
        assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus'] == before
        assert stored['scope'] == 'runtime'
    report = (await app.dispatch('session.inspect', {'id': target['id']}))['result']
    for rows in (report['timeline'], report['runtimeFaults']):
        detail, = [row for row in rows if row.get('event') == 'command.rejected']
        assert {key: detail[key] for key in metadata} == metadata
    assert report['workReplayed'] is False
    assert 'PRIVATE' not in json.dumps(report)
    shared = json.dumps(failure_report(target))
    assert all(value not in shared for value in identity.values() if isinstance(value, str))
    assert 'callback_kind' not in shared and 'report_index' not in shared
    assert app.db.execute('SELECT id, receipt FROM commands ORDER BY id').fetchall() == commands
    assert not app.runtime.inputs
