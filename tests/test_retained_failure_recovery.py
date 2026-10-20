"""Historical recovery uses retained host events and accepted input receipts."""
import copy

import pytest
from amplifier_foundation.session import SharedSessionStore
from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.service import AppError
from test_assignments import app, child, generation
from test_collaborative_workspaces import agent_action
from test_collaborative_workspaces import settled


async def submit(app, source, target, human):
    return await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Reconcile completed effects and continue remaining work.',
        'mode': 'queue', 'resume': {'sourceMessageId': human['id'],
            'expectedInterruptionRevision': target.get('interruptionRevision', 0)}}, 'resume-member')


async def historical_failure(app):
    source = app.state['sessions'][0]
    target, creation = await child(app, source)
    input_id = creation['initialInputId']
    # Older producers did not always classify their terminal event. Exercise
    # the real projection: it retains the event, then projects a generic exit.
    await app.on_runtime_event(*normalize_event({
        'type': 'generation.failed', 'generation_id': 'generation-helper',
        'input_ids': [input_id], 'error_type': 'ValueError',
    }, target['id']))
    await app.on_runtime_event('runtime.error', {
        'sessionId': target['id'], 'error': 'Manager turn failed; no automatic replay'})
    await app.on_runtime_event('runtime.ended', {'sessionId': target['id']})
    assert target['failure']['category'] == 'unknown'
    assert 'generationId' not in target['failure']
    human = app._message(source, 'user', 'Inspect the finished work and resume the remaining task.',
                         inputId='fresh-human-recovery', inputOrigin='ui')
    await generation(app, source, 'fresh-owner-generation', ['fresh-human-recovery'])
    app.runtime.inputs.clear()
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    return source, target, human, input_id


async def test_unknown_historical_failure_recovers_with_separate_exact_terminal_proof(app):
    source, target, human, input_id = await historical_failure(app)
    original_failure = copy.deepcopy(target['failure'])
    original_receipt = copy.deepcopy(app.collaboration.receipt(input_id))
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    proof = receipt['humanResume']['failedRecovery']
    assert proof['failure'] == original_failure
    assert proof['terminalEvidence']['generation']['generation_id'] == 'generation-helper'
    assert proof['terminalEvidence']['generation']['input_ids'] == [input_id]
    assert target['failure'] == original_failure
    assert app.collaboration.receipt(input_id) == original_receipt
    assert len(app.runtime.inputs) == 1 and app.runtime.inputs[0][1] == 'resume-member'


@pytest.mark.parametrize('change', ['missing_receipt', 'wrong_receipt_target', 'unknown_delivery',
                                    'unmatched_input', 'later_root_event', 'native_owner'])
async def test_historical_recovery_refuses_incomplete_or_superseded_proof(app, change):
    source, target, human, input_id = await historical_failure(app)
    held = None
    if change == 'missing_receipt':
        app.db.execute('DELETE FROM commands WHERE id=?', (input_id,)); app.db.commit()
    elif change in {'wrong_receipt_target', 'unknown_delivery'}:
        receipt = app.collaboration.receipt(input_id)
        if change == 'wrong_receipt_target': receipt['target']['sessionId'] = source['id']
        else: receipt['delivery'] = 'unknown'
        app.collaboration.save(input_id, receipt); app.db.commit()
    elif change == 'unmatched_input':
        await app.on_runtime_event(*normalize_event({'type': 'generation.failed',
            'generation_id': 'generation-helper', 'input_ids': ['not-delivered']}, target['id']))
    elif change == 'later_root_event':
        await app.on_runtime_event(*normalize_event({'type': 'generation.finished',
            'generation_id': 'another-generation', 'input_ids': [input_id]}, target['id']))
    else:
        held = SharedSessionStore(target['workspace'], target['id']).acquire(app='independent-owner')
    try:
        with pytest.raises(AppError):
            await submit(app, source, target, human)
        assert not app.runtime.inputs
    finally:
        if held: held.release()


@pytest.mark.parametrize('change', ['input', 'terminal', 'safety', 'stop', 'configuration'])
async def test_historical_proof_rechecked_after_prepare(app, change):
    source, target, human, input_id = await historical_failure(app)

    async def delayed(session, args, guard, emit):
        if change == 'input':
            await generation(app, target, 'new-generation', ['new-input'])
        elif change == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='newer-stop')
        elif change == 'configuration':
            target['fileAccessPending'] = True
        else:
            await app.on_runtime_event(*normalize_event({
                'type': 'generation.failed', 'generation_id': 'generation-helper',
                'input_ids': [input_id], **({'error_category': 'content_filter'} if change == 'safety' else {}),
            }, target['id']))
        assert guard()
        assert not app.collaboration.admission(target['id'], args)['admitted']
        return {'accepted': False, 'reason': guard()}

    app.runtime.collaboration_input = delayed
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'suppressed')
    assert not app.runtime.inputs
    if change == 'safety': assert 'safety' in receipt['admission']['reason']


async def test_retained_terminal_safety_cannot_hide_behind_old_unknown_projection(app, monkeypatch):
    source, target, human, input_id = await historical_failure(app)
    # Emulate the old projection while retaining the real structured terminal.
    # This is a historical negative, not manufactured recovery authority.
    monkeypatch.setattr('amplifier_web.session_health.generation_failure', lambda event: None)
    await app.on_runtime_event(*normalize_event({
        'type': 'generation.failed', 'generation_id': 'generation-helper',
        'input_ids': [input_id], 'error_category': 'content_filter',
    }, target['id']))
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'error': 'Worker exited'})
    assert target['failure']['category'] == 'unknown'
    with pytest.raises(AppError, match='safety'):
        await submit(app, source, target, human)
    assert not app.runtime.inputs


async def test_retained_recovery_survives_host_restart_without_rewriting_saved_failure(app):
    from amplifier_web.service import AppService
    from test_collaborative_workspaces import Runtime
    source, target, human, input_id = await historical_failure(app)
    original = copy.deepcopy(target['failure'])
    app._save()
    await app.close()
    runtime = Runtime()
    reopened = runtime.app = AppService(app.data_dir, runtime, workspace=source['workspace'])
    try:
        owner, recipient = reopened._session(source['id']), reopened._session(target['id'])
        human = reopened._message(owner, 'user', 'Resume the remaining task after inspecting finished effects.',
            inputId='post-restart-human', inputOrigin='ui')
        await generation(reopened, owner, 'post-restart-owner', ['post-restart-human'])
        runtime.stopped_boundary = RuntimeManager().stopped_boundary
        await submit(reopened, owner, recipient, human)
        receipt = await settled(reopened, 'resume-member', 'accepted')
        assert receipt['humanResume']['failedRecovery']['failure'] == original
        assert recipient['failure'] == original
        assert len(runtime.inputs) == 1
    finally:
        await reopened.close()
