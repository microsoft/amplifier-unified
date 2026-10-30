"""Process readiness is not recovery of a failed peer/native generation."""
import copy

import pytest

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

    async def warm_then_admit(session, args, guard, emit):
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
    assert len(app.runtime.inputs) == 1 and app.runtime.inputs[0][1] == 'resume-member'
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
