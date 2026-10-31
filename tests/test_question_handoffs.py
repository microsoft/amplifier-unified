"""The user can answer in the originating chat without repeating the decision."""
import copy
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, agent_action, generation, settled, Runtime


async def delivered_answer(app, *, mode='queue', artifacts=None):
    source, target = app.state['sessions']
    # A managed design chat and a release workspace are distinct locations.
    source.update(workspace=source['workspace'] + '/managed', location={'kind': 'managed'})
    target.update(workspace=target['workspace'] + '/release', location={'kind': 'workspace'})
    question = (await app.dispatch('question.create', {
        'sessionId': target['id'], 'prompt': 'Proceed with the corrected check?',
        'required': True, 'dependency': 'Complete validation', 'artifacts': artifacts or [],
    }))['result']
    human = app._message(source, 'user', 'Yes, complete the corrected validation.',
                         inputId='human-answer', inputOrigin='ui')
    await generation(app, source, 'answer-source', [human['inputId']])
    sent = await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'mode': mode,
        'text': 'The user answered your question here; use the retained original input.',
    }, 'answer-handoff')
    if mode == 'queue':
        await settled(app, 'answer-handoff', 'accepted')
    await generation(app, target, 'answer-recipient', ['answer-handoff'])
    args = {'sessionId': target['id'], 'id': question['id'], 'expectedRevision': 1,
            'text': human['text'], 'sourceMessageId': human['id'], 'sourceRequestId': sent['requestId']}
    return source, target, question, human, args


@pytest.mark.parametrize('mode', ['queue', 'notify'])
async def test_original_human_answer_crosses_delivered_handoff_once(app, mode):
    source, target, question, human, args = await delivered_answer(app, mode=mode)
    original_source = copy.deepcopy(source['messages'])
    target['approvals'] = [{'id': 'separate-permission', 'status': 'pending'}]
    before_selection = app.state['selectedSessionId']
    before_draft = app.state['view']['draft']
    result = await agent_action(app, target, 'question.answer', args, 'record-answer')
    answer = result['result']['answer']
    assert result['result']['status'] == 'answered'
    assert result['result']['delivery']['status'] == 'accepted'
    assert answer['provenance']['sourceSessionId'] == source['id']
    assert answer['provenance']['sourceRequestId'] == 'answer-handoff'
    assert answer['provenance']['messageId'] == human['id']
    assert answer['provenance']['inputOrigin'] == 'ui'
    assert answer['provenance']['text'] == human['text']
    assert source['messages'] == original_source
    assert target['approvals'][0]['status'] == 'pending'
    assert app.state['selectedSessionId'] == before_selection
    assert app.state['view']['draft'] == before_draft
    again = await agent_action(app, target, 'question.answer', args, 'record-answer')
    assert again['duplicate']
    answer_input = 'question:' + question['id'] + ':answer'
    assert len([row for row in app.runtime.inputs if row[1] == answer_input]) == 1


@pytest.mark.parametrize('confirmation', ['runtime.delivery', 'runtime.status', 'check'])
async def test_handoff_answer_timeout_reconciles_exact_input_without_replay(app, monkeypatch, confirmation):
    target = app.state['sessions'][1]
    artifact = (await app.app_bridge('dispatch', {'action': 'canvas.show', 'args': {
        'kind': 'markdown', 'title': 'Handoff review', 'content': '# Reviewed v1',
        'presentation': 'saved',
    }}, target['id']))['result']
    source, target, question, human, args = await delivered_answer(
        app, artifacts=[{'id': artifact['id'], 'version': 1}])
    revised = await app.app_bridge('dispatch', {'action': 'canvas.versions.revise', 'args': {
        'id': artifact['id'], 'expectedRevision': 1, 'content': '# Later v2',
    }}, target['id'])
    assert revised['accepted']
    assert next(row for row in app.state['canvasArtifacts'] if row['id'] == artifact['id'])['revision'] == 2
    answer_input = 'question:' + question['id'] + ':answer'
    original_source = copy.deepcopy(source['messages'])
    original_handoff = copy.deepcopy(app.collaboration.receipt('answer-handoff'))
    before_inputs = list(app.runtime.inputs)
    assert [(sid, identity) for sid, identity, _ in before_inputs] == [(target['id'], 'answer-handoff')]
    target['approvals'] = [{'id': 'separate-permission', 'status': 'pending'}]
    before_selection, before_draft = app.state['selectedSessionId'], app.state['view']['draft']

    async def lost_ack(session, text, input_id, emit):
        assert (session['id'], input_id) == (target['id'], answer_input)
        app.runtime.inputs.append((session['id'], input_id, text))
        raise TimeoutError('Lost acknowledgement for the verified human answer')

    send = AsyncMock(side_effect=lost_ack)
    probe = AsyncMock(return_value='unknown')
    replay = AsyncMock(side_effect=AssertionError('Do not replay the delivered handoff'))
    retry = AsyncMock(side_effect=AssertionError('Do not retry the saved answer'))
    monkeypatch.setattr(app.runtime, 'send', send)
    monkeypatch.setattr(app.runtime, 'delivery', probe, raising=False)
    monkeypatch.setattr(app.runtime, 'collaboration_input', replay)
    monkeypatch.setattr(app.runtime, 'retry', retry, raising=False)
    original = copy.deepcopy((await agent_action(
        app, target, 'question.answer', args, 'record-timeout-answer'))['result'])
    assert original['status'] == 'answered' and original['revision'] == 2
    assert original['delivery']['status'] == 'unknown'
    assert original['delivery']['inputId'] == answer_input
    answer = original['answer']
    assert answer['questionRevision'] == question['revision'] == 1
    assert answer['text'] == human['text']
    assert answer['provenance']['sourceSessionId'] == source['id']
    assert answer['provenance']['sourceRequestId'] == 'answer-handoff'
    assert answer['provenance']['messageId'] == human['id']
    assert answer['provenance']['inputOrigin'] == 'ui'
    assert answer['provenance']['text'] == human['text']
    assert answer['artifacts'] == original['artifacts'] == question['artifacts']
    assert [(ref['id'], ref['version']) for ref in answer['artifacts']] == [(artifact['id'], 1)]
    sent_inputs = list(app.runtime.inputs)
    assert sent_inputs[:-1] == before_inputs
    assert sent_inputs[-1][:2] == (target['id'], answer_input)
    messages = [m for m in target['messages'] if m.get('inputId') == answer_input]
    assert len(messages) == 1 and messages[0]['delivery']['status'] == 'unknown'
    message_id = messages[0]['id']

    # Acceptance for another input cannot confirm this saved answer.
    await app.on_runtime_event('runtime.delivery', {
        'sessionId': target['id'], 'inputId': answer_input + ':other', 'delivery': 'accepted'})
    assert app.questions.store.get(target['id'], question['id']) == original
    if confirmation == 'check':
        check_args = {'sessionId': target['id'], 'inputId': answer_input}
        missing = await app.dispatch('conversation.delivery', check_args)
        assert missing['result']['delivery'] == 'unknown'
        assert app.questions.store.get(target['id'], question['id']) == original
        probe.return_value = 'accepted'
        checked = await app.dispatch('conversation.delivery', check_args)
        assert checked['result']['delivery'] == 'accepted'
        assert probe.await_count == 2
        for call in probe.await_args_list:
            assert (call.args[0]['id'], call.args[1]) == (target['id'], answer_input)
    else:
        await app.on_runtime_event(confirmation, {
            'sessionId': target['id'], 'inputId': answer_input, 'delivery': 'accepted',
            'event': 'input.delivered', 'status': 'working'})
        probe.assert_not_awaited()
    saved = (await app.dispatch('question.read', {
        'sessionId': target['id'], 'id': question['id']}))['result']
    assert saved['delivery']['status'] == 'accepted'
    assert saved['delivery']['inputId'] == answer_input
    assert 'message' not in saved['delivery']
    assert {key: value for key, value in saved.items() if key != 'delivery'} == {
        key: value for key, value in original.items() if key != 'delivery'}
    duplicate = await agent_action(app, target, 'question.answer', args, 'record-timeout-answer')
    assert duplicate['duplicate'] and duplicate['result'] == saved
    messages = [m for m in target['messages'] if m.get('inputId') == answer_input]
    assert len(messages) == 1 and messages[0]['id'] == message_id
    assert messages[0]['delivery']['status'] == 'accepted'
    assert messages[0]['answerProvenance'] == answer['provenance']
    send.assert_awaited_once()
    retry.assert_not_awaited()
    replay.assert_not_awaited()
    assert app.runtime.inputs == sent_inputs
    assert source['messages'] == original_source
    assert app.collaboration.receipt('answer-handoff') == original_handoff
    assert target['approvals'] == [{'id': 'separate-permission', 'status': 'pending'}]
    assert app.state['selectedSessionId'] == before_selection
    assert app.state['view']['draft'] == before_draft


@pytest.mark.parametrize('change', [
    'foreign-target', 'unrelated-message', 'peer-prose', 'unattributed',
    'generated-answer', 'before-question', 'after-handoff', 'queued', 'unknown',
    'cancelled', 'source-stopped', 'target-stopped', 'stopped-before-notice',
    'source-moved', 'target-moved', 'missing-original',
])
async def test_handoff_cannot_substitute_other_messages_or_cross_changed_boundaries(app, change):
    source, target, question, human, args = await delivered_answer(app)
    receipt = app.collaboration.receipt('answer-handoff')
    if change == 'foreign-target':
        receipt['target']['sessionId'] = source['id']
    elif change == 'unrelated-message':
        receipt['sourceInputIds'] = ['another-human-input']
    elif change == 'peer-prose':
        human['inputOrigin'] = 'peer'
    elif change == 'unattributed':
        human.pop('inputOrigin')
    elif change == 'generated-answer':
        human['questionId'] = 'another-question'
    elif change == 'before-question':
        human['createdAt'] = question['createdAt'] - 1
    elif change == 'after-handoff':
        human['createdAt'] = receipt['acceptedAt'] + 1
    elif change in {'queued', 'unknown'}:
        receipt['delivery'] = change
    elif change == 'cancelled':
        receipt['cancellation'] = {'reason': 'No longer wanted'}
    elif change == 'source-stopped':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    elif change == 'target-stopped':
        target['interruptionRevision'] = target.get('interruptionRevision', 0) + 1
    elif change == 'stopped-before-notice':
        target['status'] = 'stopped'
        receipt['delivery'] = 'notified'
    elif change == 'source-moved':
        source['workspace'] += '/different'
    elif change == 'target-moved':
        target['workspace'] += '/different'
    elif change == 'missing-original':
        args.pop('sourceMessageId')
    app.collaboration.save('answer-handoff', receipt)
    before_inputs = list(app.runtime.inputs)
    with pytest.raises(AppError):
        await agent_action(app, target, 'question.answer', args, 'invalid-answer')
    assert app.questions.store.get(target['id'], question['id'])['status'] == 'pending'
    assert app.runtime.inputs == before_inputs


async def test_delivered_answer_does_not_reopen_superseded_question(app):
    _, target, question, _, args = await delivered_answer(app)
    replacement = (await app.dispatch('question.supersede', {
        'sessionId': target['id'], 'id': question['id'], 'expectedRevision': 1,
        'prompt': 'The scope changed. Which option now?', 'required': True,
        'dependency': 'Changed work',
    }))['result']
    with pytest.raises(AppError, match='changed or is already closed'):
        await agent_action(app, target, 'question.answer', args, 'old-answer')
    assert app.questions.store.get(target['id'], replacement['id'])['status'] == 'pending'


async def test_delivered_answer_resolves_cold_original_after_restart_without_replaying_handoff(app, monkeypatch):
    source, target, question, human, args = await delivered_answer(app)
    source_id, target_id = source['id'], target['id']
    original_human = copy.deepcopy(human)
    del human  # Do not retain a mutable message alias that correctly prevents retirement.
    for session in (source, target):
        active = session['collaborationGeneration']
        await app.on_runtime_event('runtime.generation', {
            'sessionId': session['id'], 'rootSessionId': session['id'],
            'event': 'generation.finished', 'generation_id': active['id'],
            'input_ids': list(active['inputIds']), 'disposition': 'manager_turn_finished',
            'active_job_ids': [],
        })
        await app.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    app.state['selectedSessionId'] = target_id
    app._publish()
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    monkeypatch.setattr(app.cold_display, 'MIN_BYTES', 1)
    app.cold_display.retire(force=True)
    assert app.cold_display.is_cold(source)
    assert 'messages' not in dict.keys(source)
    app._save()
    await app.close()

    runtime = Runtime()
    restored = runtime.app = AppService(app.data_dir, runtime, workspace=app.default_workspace)
    try:
        assert source_id not in {row['id'] for row in restored.state['sessions']}
        assert runtime.inputs == [], 'restart must not repeat the already delivered handoff'
        recipient = restored._session(target_id)
        await generation(restored, recipient, 'recipient-after-restart', ['answer-handoff'])
        before_selection = restored.state['selectedSessionId']
        result = await agent_action(restored, recipient, 'question.answer', args, 'record-after-restart')
        assert result['result']['status'] == 'answered'
        assert result['result']['answer']['provenance']['messageId'] == original_human['id']
        assert result['result']['answer']['provenance']['text'] == original_human['text']
        assert result['result']['answer']['provenance']['sourceSessionId'] == source_id
        assert restored._session(source_id)['messages'] == [original_human]
        assert restored.state['selectedSessionId'] == before_selection
        answer_input = 'question:' + question['id'] + ':answer'
        assert [(sid, identity) for sid, identity, _ in runtime.inputs] == [(target_id, answer_input)]
        again = await agent_action(restored, recipient, 'question.answer', args, 'record-after-restart')
        assert again['duplicate'] and len(runtime.inputs) == 1
    finally:
        await restored.close()


async def test_editing_original_human_answer_invalidates_old_handoff(app, monkeypatch):
    from amplifier_web.history_revision import rewind
    from amplifier_web.host.storage import SessionStore
    from test_history_revision import Controls

    source, target, question, human, args = await delivered_answer(app)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    store = SessionStore.for_app(app.data_dir, source['workspace'])
    rows = [{'role': 'user', 'content': human['text']}]
    store.save(source['id'], rows, {}, preserve_system=True)
    await app.on_runtime_event('runtime.status', {'sessionId': source['id'], 'status': 'idle'})

    async def edit_context(identity, operation, values):
        assert identity == source['id'] and operation == 'history.edit'
        controls = Controls(app.data_dir, values['source'], rows)
        return await rewind(controls, values)

    monkeypatch.setattr(app.runtime, 'start', AsyncMock(), raising=False)
    monkeypatch.setattr(app.runtime, 'control', edit_context, raising=False)
    await app.dispatch('message.edit', {
        'sessionId': source['id'], 'messageId': human['id'],
        'text': 'No, leave that validation paused.', 'mode': 'current',
    }, command_id='correct-human-answer')
    assert source['historyEdit']['phase'] == 'ready'
    assert all(row['id'] != human['id'] for row in source['messages'])
    replacement = source['messages'][-1]
    assert replacement['text'] == 'No, leave that validation paused.'
    before_inputs = list(app.runtime.inputs)

    # Neither the removed original nor its replacement belongs to this exact
    # already delivered handoff. A new answer needs a new source handoff.
    for index, message_id in enumerate((human['id'], replacement['id'])):
        with pytest.raises(AppError):
            await agent_action(app, target, 'question.answer',
                {**args, 'sourceMessageId': message_id}, f'stale-edited-answer-{index}')
        assert app.questions.store.get(target['id'], question['id'])['status'] == 'pending'
        assert app.runtime.inputs == before_inputs
