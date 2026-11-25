"""Process readiness is not recovery of a failed peer/native generation."""
import asyncio
import copy
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

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


@pytest.fixture
def suggestion_attempt(app, monkeypatch):
    from amplifier_web.prompt_suggestions import BROWSER
    target = app.state['sessions'][1]
    app._message(target, 'user', 'Earlier human question.', inputOrigin='ui')
    earlier = app._message(target, 'assistant', 'Earlier complete answer.')
    host = app.prompt_suggestions
    app._state['settings']['suggestedMessages'] = True
    config = {'enabled': True, 'provider': 'saved', 'model': 'utility'}
    monkeypatch.setattr(host, 'settings', lambda: (config, {'id': 'saved'}, 'crossing'))
    complete = AsyncMock(return_value={'status': 'ready', 'suggestion': 'Check the new result.',
                                      'usage': None, 'cost': None})
    monkeypatch.setattr(host, 'complete', complete)
    app.clients.attach('suggestion-browser', kind='web')
    app.clients.records['suggestion-browser']['selectedSessionId'] = target['id']

    async def request(identity, latest=earlier['id']):
        token = BROWSER.set({'principal': 'cookie', 'valid': lambda: True})
        try:
            with app.clients.bind('suggestion-browser'):
                args = {'sessionId': target['id'], 'requestId': identity}
                await host.dispatch('session.suggestion.request',
                                    {**args, 'expectedLastMessageId': latest}, 'ui')
                await asyncio.gather(*tuple(host.jobs))
                return await host.dispatch('session.suggestion.status', args, 'ui')
        finally:
            BROWSER.reset(token)
    return request, complete


@pytest.mark.parametrize('dismissed', [False, True])
async def test_prior_failure_notice_survives_fresh_send_preparation_until_admission(app, dismissed, suggestion_attempt):
    request, complete = suggestion_attempt
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    await observed(app, target, 'failed', error_type='CompactionError',
                   error_category='context_compaction', error_code='summary_output_limit')
    failure = copy.deepcopy(target['failure'])

    def notice():
        return next((row for row in app.browser_state()['attention']['items']
                     if row['id'] == 'session:' + target['id']), None)

    original = notice()
    if dismissed:
        await app.dispatch('attention.read', {'ids': [original['id']],
            'fingerprints': {original['id']: original['fingerprint']}})
    await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'Try again?'},
                       command_id='fresh-preparing-input')
    for kind, payload in [
        (None, {}),
        ('runtime.status', {'status': 'starting', 'phase': 'runtime-setup'}),
        ('runtime.warmth', {'status': 'warm'}),
        ('runtime.status', {'status': 'ready'}),
    ]:
        if kind:
            await app.on_runtime_event(kind, {'sessionId': target['id'], **payload})
        current = notice()
        assert current is not None, 'Retained failure must retain its dismissible notice'
        assert current['fingerprint'] == original['fingerprint']
        assert current['read'] is dismissed
        assert target['failure'] == failure
        report = (await app.dispatch('session.inspect', {'id': target['id']}))['result']
        assert report['failure'] == failure
        assert (await request('preparation-' + str(kind) + str(payload)))['status'] != 'ready'
        complete.assert_not_awaited()
    await observed(app, target, 'started', gen='fresh-generation', inputs=['fresh-preparing-input'])
    assert notice() is None
    assert not target.get('failure') and not target.get('error')
    from amplifier_web.input_status import annotate
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus']['failure'] == failure
    reply = app._message(target, 'assistant', 'Fresh completed reply.', inputId='fresh-preparing-input')
    await observed(app, target, 'completed', gen='fresh-generation', inputs=['fresh-preparing-input'])
    target['status'] = 'idle'
    assert (await request('fresh-completed', reply['id']))['status'] == 'ready'
    complete.assert_awaited_once()
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus']['failure'] == failure


@pytest.mark.parametrize('startup', [False, True])
async def test_new_preparation_failure_is_unread_and_dismissible_after_prior_dismissal(app, startup):
    target = app.state['sessions'][1]
    await terminal(app, target)
    original = next(row for row in app.browser_state()['attention']['items']
                    if row['id'] == 'session:' + target['id'])
    await app.dispatch('attention.read', {'ids': [original['id']],
        'fingerprints': {original['id']: original['fingerprint']}})
    await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'A new request'},
                       command_id='new-preparation-input')
    if not startup:
        await observed(app, target, 'started', gen='new-generation', inputs=['new-preparation-input'])
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'errorType': 'RuntimeStartupError' if startup else 'ValueError',
        'error': 'New failure while preparing' if startup else 'New turn failed'})
    current = next(row for row in app.browser_state()['attention']['items']
                   if row['id'] == original['id'])
    assert current['fingerprint'] != original['fingerprint'] and not current['read']
    await app.dispatch('attention.read', {'ids': [original['id']],
        'fingerprints': {original['id']: original['fingerprint']}})
    assert not next(row for row in app.browser_state()['attention']['items']
                    if row['id'] == original['id'])['read']
    await app.dispatch('attention.read', {'ids': [current['id']],
        'fingerprints': {current['id']: current['fingerprint']}})
    assert next(row for row in app.browser_state()['attention']['items']
                if row['id'] == current['id'])['read']


@pytest.mark.parametrize('status', [None, 'starting', 'ready'])
async def test_new_send_interruption_does_not_inherit_dismissed_prior_failure(app, status, suggestion_attempt):
    request, complete = suggestion_attempt
    from amplifier_web.runtime import RuntimeHostClosingError
    from amplifier_web.input_status import annotate
    target = app.state['sessions'][1]
    await observed(app, target, 'started', inputs=['old-input'])
    await observed(app, target, 'failed', inputs=['old-input'],
                   error_type='ValueError', error_category='unknown')
    failure = copy.deepcopy(target['failure'])
    assert annotate(target, {'role': 'user', 'inputId': 'old-input'})['inputStatus']['failure'] == failure
    original = next(row for row in app.browser_state()['attention']['items']
                    if row['id'] == 'session:' + target['id'])
    await app.dispatch('attention.read', {'ids': [original['id']],
        'fingerprints': {original['id']: original['fingerprint']}})
    async def closing(*args, **kwargs):
        if status:
            await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': status})
        app.closed = True
        raise RuntimeHostClosingError()
    send = app.runtime.send
    app.runtime.send = closing
    with pytest.raises(RuntimeHostClosingError):
        await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'Fresh attempt'},
                           command_id='interrupted-new-input')
    app.closed = False
    current = next(row for row in app.browser_state()['attention']['items']
                   if row['id'] == original['id'])
    assert not current['read'] and current['fingerprint'] != original['fingerprint']
    assert target['failure']['category'] == 'execution_interrupted'
    assert annotate(target, {'role': 'user', 'inputId': 'old-input'})['inputStatus']['failure'] == failure
    assert target['messages'][-1]['delivery']['status'] == 'unknown'
    assert not app.runtime.inputs
    assert (await request('unknown-input'))['status'] != 'ready'
    complete.assert_not_awaited()
    app.runtime.send = send
    await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'A distinct new human turn'},
                       command_id='distinct-fresh-input')
    await observed(app, target, 'started', gen='distinct-generation', inputs=['distinct-fresh-input'])
    reply = app._message(target, 'assistant', 'Fresh completed reply.', inputId='distinct-fresh-input')
    await observed(app, target, 'completed', gen='distinct-generation', inputs=['distinct-fresh-input'])
    target['status'] = 'idle'
    assert (await request('new-completed-input', reply['id']))['status'] == 'ready'
    complete.assert_awaited_once()
    assert annotate(target, {'role': 'user', 'inputId': 'old-input'})['inputStatus']['failure'] == failure


@pytest.mark.parametrize('boundary', ['stopped', 'stopping', 'stop-revision',
                                     'new-error', 'content_filter', 'computer_capture_stop'])
async def test_send_interruption_preserves_stop_newer_failure_and_safety(app, boundary):
    from amplifier_web.runtime import RuntimeHostClosingError
    target = app.state['sessions'][1]
    await terminal(app, target)
    if boundary in {'content_filter', 'computer_capture_stop'}:
        target['failure']['category'] = boundary
    original = next(row for row in app.browser_state()['attention']['items']
                    if row['id'] == 'session:' + target['id'])
    await app.dispatch('attention.read', {'ids': [original['id']],
        'fingerprints': {original['id']: original['fingerprint']}})
    expected = {}
    async def closing(*args, **kwargs):
        if boundary in {'stopped', 'stopping', 'stop-revision'}:
            target['interruptionRevision'] = target.get('interruptionRevision', 0) + 1
            target['status'] = boundary if boundary != 'stop-revision' else 'starting'
        elif boundary == 'new-error':
            await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
                'errorType': 'RuntimeStartupError', 'error': 'A genuinely newer startup failure'})
        expected.update(copy.deepcopy(target['failure']))
        app.closed = True
        raise RuntimeHostClosingError()
    app.runtime.send = closing
    with pytest.raises(RuntimeHostClosingError):
        await app.dispatch('conversation.send', {'sessionId': target['id'], 'text': 'Fresh attempt'},
                           command_id='guarded-interrupted-input')
    app.closed = False
    assert target['failure'] == expected
    if boundary in {'stopped', 'stopping'}:
        assert target['status'] == boundary
    current = next(row for row in app.browser_state()['attention']['items']
                   if row['id'] == original['id'])
    assert current['read'] is (boundary != 'new-error')
    assert not app.runtime.inputs


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


@pytest.mark.parametrize('stage,category', [
    ('context_preparation', 'context_limit'), ('provider_request', 'context_limit'),
    ('manager_turn', 'unknown')])
async def test_exact_terminal_chain_supersedes_earlier_same_class_provider_call(app, stage, category):
    from amplifier_web.input_status import annotate
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    provider_chain = [{'errorType': 'ContextLengthError', 'frames': [
        {'file': 'provider.py', 'function': 'complete', 'line': 101}]}]
    from amplifier_web.session_health import generation_failure
    provider_failure = {**generation_failure({'error_category': 'context_limit',
        'error_type': 'ContextLengthError', 'error_stage': 'provider_request'}),
        'exceptionChain': provider_chain}
    await app.on_runtime_event('execution.event', {'sessionId': target['id'], 'rootSessionId': target['id'],
        'id': 'initial-overflow', 'turnId': 'saved-input', 'generationId': 'saved-generation',
        'kind': 'llm', 'phase': 'error', 'endedAt': 123, 'failure': provider_failure})
    original_node = copy.deepcopy(next(row for row in target['execution']['nodes']
                                       if row['id'] == 'initial-overflow'))
    await observed(app, target, 'failed', error_type='ContextLengthError',
                   error_category=category, error_stage=stage)
    terminal_at = target['generations'][-1]['at']
    terminal_chain = [
        {'errorType': 'ManagerTurnError', 'frames': [{'file': 'loop.py', 'function': 'execute', 'line': 800}]},
        {'errorType': 'ContextLengthError', 'frames': [
            {'file': 'loop.py', 'function': 'recover_context_overflow', 'line': 4773}]}]
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'terminalFailure': {
        'generationId': 'saved-generation', 'inputIds': ['saved-input'],
        'errorType': 'ContextLengthError', 'exceptionChain': terminal_chain}})
    failure = copy.deepcopy(target['failure'])
    assert failure['recordedAt'] == terminal_at and failure['recordedAt'] != 123
    assert failure['stage'] == stage
    assert failure['category'] == category and 'requestCallId' not in failure
    assert failure['exceptionChain'] == terminal_chain
    assert failure['generationId'] == 'saved-generation' and failure['inputIds'] == ['saved-input']
    assert annotate(target, {'role': 'user', 'inputId': 'saved-input'})['inputStatus']['failure'] == failure
    assert next(row for row in target['execution']['nodes'] if row['id'] == 'initial-overflow') == original_node
    report = (await app.dispatch('session.inspect', {'id': target['id']}))['result']
    assert report['failure'] == failure
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'terminalFailure': {
        'generationId': 'saved-generation', 'inputIds': ['saved-input'],
        'errorType': 'ContextLengthError', 'exceptionChain': provider_chain}})
    assert target['failure'] == failure, 'A second capture cannot replace the bound terminal'
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'errorType': 'RuntimeError', 'error': 'Manager turn failed'})
    assert target['failure'] == failure and target['errorAt'] == terminal_at


@pytest.mark.parametrize('capture', [False, True])
async def test_original_provider_rethrow_keeps_truthful_frames_through_generic_exit(app, capture):
    from amplifier_web.session_health import generation_failure
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    chain = [{'errorType': 'ContextLengthError', 'frames': [
        {'file': 'provider.py', 'function': 'complete', 'line': 101}]}]
    await app.on_runtime_event('execution.event', {'sessionId': target['id'], 'rootSessionId': target['id'],
        'id': 'provider-rethrow', 'turnId': 'saved-input', 'generationId': 'saved-generation',
        'kind': 'llm', 'phase': 'error', 'endedAt': 123,
        'failure': {**generation_failure({'error_type': 'ContextLengthError',
            'error_category': 'context_limit', 'error_stage': 'provider_request'}), 'exceptionChain': chain}})
    await observed(app, target, 'failed', error_type='ContextLengthError',
                   error_category='context_limit', error_stage='provider_request')
    if capture:
        chain = [{'errorType': 'ManagerTurnError', 'frames': []}, *chain]
        await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'terminalFailure': {
            'generationId': 'saved-generation', 'inputIds': ['saved-input'],
            'errorType': 'ContextLengthError', 'exceptionChain': chain}})
    before = copy.deepcopy(target['failure'])
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'errorType': 'RuntimeError', 'error': 'Manager turn failed'})
    assert target['failure'] == before
    assert target['failure']['exceptionChain'] == chain


@pytest.mark.parametrize('safety', ['content_filter', 'computer_capture_stop'])
async def test_later_non_safety_terminal_cannot_downgrade_prior_safety_call(app, safety):
    from amplifier_web.session_health import generation_failure
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    chain = [{'errorType': 'InvalidRequestError', 'frames': []}]
    await app.on_runtime_event('execution.event', {'sessionId': target['id'], 'rootSessionId': target['id'],
        'id': 'safety-call', 'turnId': 'saved-input', 'generationId': 'saved-generation',
        'kind': 'llm', 'phase': 'error', 'endedAt': 123,
        'failure': {**generation_failure({'error_category': 'content_filter',
            'error_type': 'InvalidRequestError', 'error_stage': 'provider_request'}),
            'category': safety, 'exceptionChain': chain}})
    await observed(app, target, 'failed', error_type='ContextLengthError',
                   error_category='context_limit', error_stage='context_preparation')
    before = copy.deepcopy(target['failure'])
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'terminalFailure': {
        'generationId': 'saved-generation', 'inputIds': ['saved-input'],
        'errorType': 'ContextLengthError', 'exceptionChain': [
            {'errorType': 'ContextLengthError', 'frames': []}]}})
    assert target['failure'] == before
    assert target['failure']['category'] == safety and target['failure']['exceptionChain'] == chain
    assert target['failure']['terminalFailure']['category'] == 'context_limit'
    assert not app.runtime.inputs


@pytest.mark.parametrize('binding', ['exact', 'wrong-input', 'missing-input', 'wrong-generation', 'wrong-type'])
async def test_execution_capture_must_match_bound_terminal_before_attaching_chain(app, binding):
    target = app.state['sessions'][1]
    await observed(app, target, 'started')
    await observed(app, target, 'failed', error_type='ValueError', error_category='unknown')
    before = copy.deepcopy(target['failure'])
    capture = {'runtimeSessionId': target['id'], 'stage': 'execution',
        'generationId': 'other' if binding == 'wrong-generation' else 'saved-generation',
        'inputIds': ['other'] if binding == 'wrong-input' else ['saved-input'],
        'exceptionChain': [{'errorType': 'TypeError' if binding == 'wrong-type' else 'ValueError',
                            'frames': []}]}
    if binding == 'missing-input':
        capture.pop('inputIds')
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'],
        'errorType': 'RuntimeError', 'error': 'Manager turn failed', 'executionFailure': capture})
    assert target['failure'] == ({**before, 'exceptionChain': capture['exceptionChain']}
                                  if binding == 'exact' else before)


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
