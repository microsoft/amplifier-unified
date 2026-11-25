"""Voice timeline, delivery and resume regressions using synthetic provider events."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.voice import VoiceCall, VoiceService, startup_history, result_text
from test_voice import Service, Socket


def call_fixture():
    service = Service()
    call = VoiceCall(VoiceService(service), 'main')
    call.id, call.socket = 'live', Socket()
    return service, call


async def settle(call):
    for _ in range(300):
        if not call.tasks:
            return
        await asyncio.sleep(.01)
    raise AssertionError('voice tasks did not settle')


async def test_child_completion_never_becomes_manager_narration():
    service, call = call_fixture()
    service.state['sessions'][0]['generations'] = [
        {'event': 'generation.finished', 'generation_id': 'unknown-owner', 'text': 'Unverified unknown owner.'},
        {'event': 'generation.finished', 'generation_id': 'child-result',
         'sessionId': 'child', 'rootSessionId': 'main', 'text': 'Unverified child conclusion.'},
        {'event': 'generation.finished', 'generation_id': 'manager-result',
         'sessionId': 'main', 'rootSessionId': 'main', 'text': 'Verified manager conclusion.'}]
    await call.announce_generations(service.state)
    content = ' '.join(event.get('content', '') for event in call.socket.sent)
    assert 'Unverified' not in content and 'Verified manager' in content


async def test_transcript_after_initial_wait_reconciles_same_delegation(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_WAIT_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    service, call = call_fixture()
    service.result.set()
    await call.handle({'type': 'session.delegation.created', 'event_id': 'd-event',
                       'offset_ms': 1000, 'delegation': {'id': 'd', 'target': 'client'}})
    await settle(call)
    assert service.calls == []
    await call.handle({'type': 'session.input_transcript.delta', 'event_id': 'speech',
                       'start_ms': 100, 'end_ms': 800, 'delta': 'Check my work.'})
    await settle(call)
    assert len(service.calls) == 1 and service.calls[0][1] == 'voice:live:d'
    await call.handle({'type': 'session.delegation.created', 'event_id': 'd-repeat',
                       'offset_ms': 1000, 'delegation': {'id': 'd', 'target': 'client'}})
    assert len(service.calls) == 1
    fragment = call.transcript_fragments[-1]
    assert (fragment['start_ms'], fragment['end_ms'], fragment['event_id']) == (100, 800, 'speech')


async def test_settling_fragments_keep_immediate_correction_before_admission(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .03)
    service, call = call_fixture()
    service.result.set()
    await call.handle({'type': 'session.input_transcript.delta', 'event_id': 'one',
                       'start_ms': 10, 'end_ms': 200, 'delta': 'Delete the file'})
    await call.handle({'type': 'session.delegation.created', 'event_id': 'd-event',
                       'offset_ms': 210, 'delegation': {'id': 'd'}})
    await asyncio.sleep(.01)
    assert service.calls == []
    await call.handle({'type': 'session.input_transcript.delta', 'event_id': 'two',
                       'start_ms': 200, 'end_ms': 400, 'delta': ' — no, cancel that.'})
    await settle(call)
    assert len(service.calls) == 1
    assert service.calls[0][0].endswith('Delete the file — no, cancel that.')


async def test_rejected_admission_keeps_original_text_for_retry():
    service, call = call_fixture()
    service.result.set()
    real_delegate = service.voice_delegate
    service.voice_delegate = AsyncMock(side_effect=ValueError('Temporarily unavailable'))
    await call.record('user', 'Check the code.', 'spoken')
    await call.delegate_live('d')
    assert call.handled_version == 0
    service.voice_delegate = real_delegate
    await call.delegate_live('d')
    assert len(service.calls) == 1 and service.calls[0][0].endswith('Check the code.')


async def test_wait_timeout_keeps_accepted_work_and_routes_later_completion():
    service, call = call_fixture()
    service.wait_for_response = AsyncMock(side_effect=TimeoutError)
    await call.record('user', 'Run the long investigation.', 'spoken')
    await call.delegate_live('d')
    assert 'could not complete' not in ' '.join(e.get('content', '') for e in call.socket.sent)
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'late', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['voice:live:d'], 'text': 'The investigation is complete.'}]
    await call.announce_generations(service.state)
    assert any('investigation is complete' in e.get('content', '') for e in call.socket.sent)
    assert len(service.calls) == 1


async def test_failed_delegation_tells_the_voice_no_internal_error_text():
    from amplifier_web.errors import AppError
    service, call = call_fixture()
    service.voice_delegate = AsyncMock(side_effect=KeyError('delivery'))
    await call.record('user', 'Check the code.', 'spoken')
    await call.delegate_live('d')
    told = ' '.join(e.get('content', '') for e in call.socket.sent)
    assert 'could not complete this request, and no work was started: An internal error stopped it.' in told
    assert 'delivery' not in told
    service.voice_delegate = AsyncMock(side_effect=AppError('This assignment is stopped.', 409))
    await call.record('user', 'Check the report.', 'spoken-2')
    await call.delegate_live('d2')
    assert 'no work was started: This assignment is stopped.' in call.socket.sent[-1]['content']


async def test_failure_after_acceptance_says_that_amplifier_has_the_request():
    from amplifier_web.errors import AppError
    service, call = call_fixture()
    service.wait_for_response = AsyncMock(side_effect=AppError('The Amplifier turn ended without a completed response.'))
    await call.record('user', 'Check the code.', 'spoken')
    await call.delegate_live('d')
    assert call.socket.sent[-1]['content'] == (
        'Amplifier received this request but could not complete it: The Amplifier turn ended without a completed response.'
        ' Check the shared chat before asking again.')
    assert call.pending_delegations['d']['status'] == 'accepted'


async def test_realtime_tool_failure_output_has_no_internal_error_text():
    import json
    service, call = call_fixture()
    call.provider = 'realtime'
    service.voice_delegate = AsyncMock(side_effect=TypeError("'NoneType' object is not subscriptable"))
    await call.realtime_tool({'name': 'amplifier_delegate', 'arguments': '{"text": "request"}', 'call_id': 'c1'})
    output = json.loads(next(e for e in call.socket.sent if e['type'] == 'conversation.item.create')['item']['output'])
    assert output == {'error': 'Amplifier could not complete this request, and no work was started: An internal error stopped it.'}


def failed_chat(service, raw):
    """As the host saves a turn that fails during the wait: raw text in `error`, the product's words in `failure`."""
    from amplifier_web.errors import AppError
    async def wait(*args, **kwargs):
        service.state['sessions'][0].update(status='error', error=raw, errorAt=2.0,
            failure={'category': 'unknown', 'summary': 'Something interrupted this chat.', 'recordedAt': 2.0})
        raise AppError(raw)
    service.wait_for_response = wait


@pytest.mark.parametrize('raw', ['no_active_job', "'NoneType' object is not subscriptable"])
async def test_the_voice_is_told_the_summary_that_the_chat_shows_not_its_raw_saved_error(raw):
    service, call = call_fixture()
    failed_chat(service, raw)
    await call.record('user', 'Check the code.', 'spoken')
    await call.delegate_live('d')
    assert call.socket.sent[-1]['content'] == (
        'Amplifier received this request but could not complete it: Something interrupted this chat.'
        ' Check the shared chat before asking again.')


async def test_an_older_failure_record_is_not_told_as_the_cause_of_this_turn():
    from amplifier_web.errors import AppError
    service, call = call_fixture()
    chat = service.state['sessions'][0]
    chat.update(errorAt=1.0, failure={'category': 'authentication', 'recordedAt': 1.0,
                                      'summary': 'The provider rejected its credentials.'})
    async def stop_fails(*args, **kwargs):
        # As the host's stop.failed handler does: a new error, the earlier failure record kept.
        chat.update(status='error', error='Stop could not be confirmed. Inspect the runtime details before continuing.')
        raise AppError(chat['error'])
    service.wait_for_response = stop_fails
    await call.record('user', 'Check the code.', 'spoken')
    await call.delegate_live('d')
    assert call.socket.sent[-1]['content'] == (
        'Amplifier received this request but could not complete it: Stop could not be confirmed.'
        ' Inspect the runtime details before continuing. Check the shared chat before asking again.')


async def test_a_stopped_chat_does_not_pass_an_older_saved_error_to_the_voice():
    from amplifier_web.errors import AppError
    service, call = call_fixture()
    service.state['sessions'][0].update(status='stopped', error='raw text of an earlier failure')
    service.wait_for_response = AsyncMock(side_effect=AppError('raw text of an earlier failure'))
    await call.record('user', 'Check the code.', 'spoken')
    await call.delegate_live('d')
    assert 'could not complete it: Execution ended before the response completed.' in call.socket.sent[-1]['content']
    assert 'raw text' not in call.socket.sent[-1]['content']


def browser_snapshot(service):
    """As the observer receives it: a browser projection, which omits each generation's text."""
    return {'sessions': [{**row, 'generations': [{key: value for key, value in event.items() if key != 'text'}
                                                  for event in row.get('generations', [])]}
                         for row in service.state['sessions']]}


async def test_late_result_is_spoken_from_the_host_record_when_the_snapshot_has_no_text():
    service, call = call_fixture()
    service.wait_for_response = AsyncMock(side_effect=TimeoutError)
    await call.record('user', 'Run the long investigation.', 'spoken')
    await call.delegate_live('d')
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'late', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['voice:live:d'], 'text': 'The investigation is complete.'}]
    await call.announce_generations(browser_snapshot(service))
    spoken = [e['content'] for e in call.socket.sent if e['type'] == 'session.commentary.append']
    assert spoken == ['The investigation is complete.']
    assert not any('update in the shared chat' in e.get('content', '') for e in call.socket.sent)


async def test_answer_follows_a_reported_failure_when_the_turn_finishes_after_it():
    service, call = call_fixture()
    service.wait_for_response = AsyncMock(side_effect=ValueError('The turn ended without a completed response.'))
    await call.record('user', 'Check the code.', 'spoken')
    await call.delegate_live('d')
    assert any('could not complete' in e.get('content', '') for e in call.socket.sent)
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'after', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['voice:live:d'], 'text': 'The code is correct.'}]
    await call.announce_generations(browser_snapshot(service))
    assert [e['content'] for e in call.socket.sent if e['type'] == 'session.commentary.append'][-1] == 'The code is correct.'
    assert len(service.calls) == 1


async def test_background_result_in_a_snapshot_without_text_is_spoken_from_the_host_record():
    service, call = call_fixture()
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'typed', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['typed-input'], 'active_job_ids': [], 'text': 'The report has three findings.'}]
    await call.announce_generations(browser_snapshot(service))
    assert [e['content'] for e in call.socket.sent if e['type'] == 'session.commentary.append'] == [
        'Amplifier manager update. The report has three findings.']


async def test_realtime_marks_a_late_result_that_newer_accepted_input_followed():
    service, call = call_fixture()
    call.provider = 'realtime'
    service.wait_for_response = AsyncMock(side_effect=TimeoutError)
    await call.realtime_tool({'name': 'amplifier_delegate', 'arguments': '{"text": "Plan Friday."}', 'call_id': 'c1'})
    assert call.pending_delegations['c1']['status'] == 'waiting_result'
    service.state['sessions'][0]['messages'].append({'role': 'user', 'via': 'chat', 'text': 'Thursday, not Friday.',
        'inputId': 'typed-correction', 'delivery': {'status': 'accepted'}})
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'older', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['voice:live:c1'], 'text': 'The Friday plan is ready.'}]
    call.socket.sent.clear()
    await call.announce_generations(browser_snapshot(service))
    told = [e['item']['content'][0]['text'] for e in call.socket.sent
            if e['type'] == 'conversation.item.create' and e['item']['type'] == 'message']
    assert len(told) == 1 and told[0].startswith(
        'Earlier request result; newer input may supersede it. Amplifier manager update. The Friday plan is ready.')


async def test_realtime_current_late_result_is_not_marked_as_earlier():
    service, call = call_fixture()
    call.provider = 'realtime'
    service.wait_for_response = AsyncMock(side_effect=TimeoutError)
    await call.realtime_tool({'name': 'amplifier_delegate', 'arguments': '{"text": "Plan Friday."}', 'call_id': 'c1'})
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'current', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['voice:live:c1'], 'text': 'The Friday plan is ready.'}]
    call.socket.sent.clear()
    await call.announce_generations(browser_snapshot(service))
    told = [e['item']['content'][0]['text'] for e in call.socket.sent
            if e['type'] == 'conversation.item.create' and e['item']['type'] == 'message']
    assert len(told) == 1 and told[0].startswith('Amplifier manager update. The Friday plan is ready.')


async def test_realtime_answer_follows_a_reported_failure_when_the_turn_finishes_after_it():
    import json
    service, call = call_fixture()
    call.provider = 'realtime'
    failed_chat(service, 'no_active_job')
    await call.realtime_tool({'name': 'amplifier_delegate', 'arguments': '{"text": "Check the code."}', 'call_id': 'c1'})
    output = json.loads(next(e for e in call.socket.sent if e['type'] == 'conversation.item.create'
                             and e['item']['type'] == 'function_call_output')['item']['output'])
    assert output == {'error': 'Amplifier received this request but could not complete it: Something interrupted this chat.'
                               ' Check the shared chat before asking again.'}
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'after', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['voice:live:c1'], 'text': 'The code is correct.'}]
    call.socket.sent.clear()
    await call.announce_generations(browser_snapshot(service))
    await call.announce_generations(browser_snapshot(service))
    told = [e['item']['content'][0]['text'] for e in call.socket.sent
            if e['type'] == 'conversation.item.create' and e['item']['type'] == 'message']
    assert len(told) == 1 and told[0].startswith('Amplifier manager update. The code is correct.')
    assert len(service.calls) == 1


async def test_realtime_failure_before_acceptance_does_not_wait_for_a_result():
    service, call = call_fixture()
    call.provider = 'realtime'
    service.voice_delegate = AsyncMock(side_effect=ValueError('admission unavailable'))
    await call.realtime_tool({'name': 'amplifier_delegate', 'arguments': '{"text": "Check the code."}', 'call_id': 'c1'})
    assert call.pending_delegations['c1']['status'] == 'submitting'
    output = json.loads(next(e for e in call.socket.sent if e['type'] == 'conversation.item.create')['item']['output'])
    assert output == {'error': 'Amplifier could not complete this request, and no work was started: An internal error stopped it.'}
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'unrelated', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['voice:live:c1'], 'text': 'A result that this call never asked for.'}]
    call.socket.sent.clear()
    await call.announce_generations(browser_snapshot(service))
    assert call.socket.sent == []


async def test_append_ack_tracks_context_injection_and_rejection_without_reexecution():
    service, call = call_fixture()
    call.execute = AsyncMock(return_value={'text': 'Verified answer.', 'generation_id': 'g'})
    await call.record('user', 'Check it.', 'spoken')
    await call.delegate_live('d')
    sent = next(e for e in call.socket.sent if e['type'] == 'session.commentary.append')
    assert 'g' not in call.delivered_generations
    await call.handle({'type': 'session.commentary.appended', 'event_id': 'ack',
                       'client_event_id': sent['event_id'], 'start_ms': 100, 'end_ms': 300})
    assert 'g' in call.delivered_generations
    assert call.append_events[sent['event_id']]['status'] == 'context_acknowledged'
    await call.append('thinking', 'Some current context.')
    failed = call.socket.sent[-1]
    await call.handle({'type': 'error', 'event_id': 'error', 'error': {
        'code': 'invalid_request', 'client_event_id': failed['event_id']}})
    assert call.append_events[failed['event_id']]['status'] == 'rejected'
    assert call.execute.await_count == 1


async def test_newer_admitted_request_demotes_unsent_old_result_to_context():
    service, call = call_fixture()
    gates = {'one': asyncio.Event(), 'two': asyncio.Event()}
    async def response(session_id, input_id=None, **kwargs):
        key = input_id.rsplit(':', 1)[-1]
        await gates[key].wait()
        return {'text': key + ' result.', 'generation_id': key, 'input_ids': [input_id]}
    service.wait_for_response = response
    await call.record('user', 'Use Friday.', 'u1')
    first = asyncio.create_task(call.delegate_live('one'))
    await asyncio.sleep(0)
    await call.record('user', 'Use Thursday instead.', 'u2')
    second = asyncio.create_task(call.delegate_live('two'))
    await asyncio.sleep(0)
    gates['one'].set(); gates['two'].set()
    await asyncio.gather(first, second)
    assert any(e['type'] == 'session.thinking.append' and 'one result.' in e['content'] for e in call.socket.sent)
    assert not any(e['type'] == 'session.commentary.append' and 'one result.' in e['content'] for e in call.socket.sent)


def test_startup_history_preserves_voice_and_text_with_roles_and_budget():
    rows = [{'role': 'user', 'text': 'Earlier spoken preference.', 'via': 'call'},
            {'role': 'assistant', 'text': 'The verified earlier answer.', 'via': 'call'},
            {'role': 'user', 'text': 'Continue that work.', 'via': 'chat'}]
    seed = startup_history({'sessions': [{'id': 'main', 'messages': rows}]}, 'main')
    assert [item['role'] for item in seed] == ['user', 'assistant', 'user']
    assert seed[0]['content'][0]['text'] == rows[0]['text']
    seed = startup_history({'sessions': [{'id': 'main', 'messages': rows * 1000}]}, 'main')
    assert len(seed) <= 128
    assert sum(len(item['content'][0]['text'].encode()) for item in seed) <= 7500


def test_large_result_never_sends_truncated_claim_or_code_as_spoken_fact():
    result = {'text': 'The change succeeded. ' + ('```python\nprivate_internal()\n```\n' * 100)
              + 'Actually the deployment has not been verified.', 'active_job_ids': ['worker']}
    text = result_text(result)
    assert len(text.encode()) <= 450
    assert 'private_internal' not in text and 'The change succeeded' not in text
    assert 'chat' in text and 'still running' in text


async def test_live_session_uses_role_seed_and_provider_specific_prompt():
    service, call = call_fixture()
    service.state['sessions'][0]['messages'] = [{'role': 'user', 'text': 'Remember blue.', 'via': 'call'}]
    call.manager.http = SimpleNamespace(ws_connect=AsyncMock(return_value=call.socket))
    call.manager.request = AsyncMock(return_value=({'session': {'id': 'live'}, 'transport': {'sdp': 'answer'}}, '', {}))
    await call.create('v=0', 'live')
    config = call.manager.request.call_args.kwargs['json']['session']
    assert config['input'][0]['content'][0]['text'] == 'Remember blue.'
    assert 'Backchannel policy:' in config['instructions'] and 'Do not generate <|SpawnThinking|> when:' in config['instructions']
    assert 'amplifier_delegate' not in config['instructions'] and 'Remember blue.' not in config['instructions']
    call.final.set(); await call.close()


async def test_article_typed_during_call_is_available_without_repeating_or_speaking_it():
    service, call = call_fixture()
    service.state['sessions'][0]['messages'].append({'id': 'article', 'role': 'user', 'via': 'chat',
        'text': 'A long article containing current source material. ' * 200})
    await call.update_typed_context(service.state)
    await call.update_typed_context(service.state)
    assert len(call.socket.sent) == 1
    assert call.socket.sent[0]['type'] == 'session.thinking.append'
    assert 'complete contents are already available to Amplifier' in call.socket.sent[0]['content']
    assert len(call.socket.sent[0]['content'].encode()) <= 450
    assert service.calls == []


async def test_late_timed_fragment_corrects_original_input_without_replaying_request(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    service, call = call_fixture()
    service.result.set()
    await call.handle({'type': 'session.input_transcript.delta', 'event_id': 'one',
                       'start_ms': 100, 'end_ms': 400, 'delta': 'Check smart clothes.'})
    await call.handle({'type': 'session.delegation.created', 'event_id': 'd-event',
                       'offset_ms': 600, 'delegation': {'id': 'd'}})
    await settle(call)
    await call.handle({'type': 'session.input_transcript.delta', 'event_id': 'two',
                       'start_ms': 450, 'end_ms': 590, 'delta': ' I meant smart tools.'})
    await settle(call)
    assert len(service.calls) == 2
    prompt, identity, _ = service.calls[1]
    assert identity != service.calls[0][1]
    assert 'already accepted input voice:live:d' in prompt
    assert prompt.endswith('Continuation: I meant smart tools.')
    assert 'do not repeat completed actions' in prompt
    assert all(event['delegation_id'] == 'd' for event in call.socket.sent)


async def test_call_end_checks_attempt_and_expected_object_under_manager_lock():
    service, call = call_fixture()
    manager = call.manager
    manager.call = call
    call.attempt_id = 'active-attempt'
    with pytest.raises(Exception, match='no longer active'):
        await manager.end(attempt_id='old-attempt')
    with pytest.raises(Exception, match='no longer active'):
        await manager.end(expected_call=VoiceCall(manager, 'main'))
    assert not call.closing
    call.final.set()
    assert (await manager.end('live', attempt_id='active-attempt', expected_call=call))['closed']


async def test_long_diagnosis_includes_final_caveat_before_announcing_complete():
    _, call = call_fixture()
    text = ('The package was built successfully. The frontend assets were generated. '
            'The local checks passed. The owned test environment started. ' * 4
            + 'The production deployment has NOT been verified; do not call it deployed.')
    await call.deliver_generation({'text': text, 'generation_id': 'long'}, 'd')
    facts = list(call.socket.sent)
    assert len(facts) > 1 and all(event['type'] == 'session.thinking.append' for event in facts)
    assert 'has NOT been verified' in ' '.join(event['content'] for event in facts)
    assert all(len(event['content'].encode()) <= 450 for event in facts)
    for event in facts[:-1]:
        await call.handle({'type': 'session.thinking.appended', 'client_event_id': event['event_id']})
    await settle(call)
    assert not any(event['type'] == 'session.commentary.append' for event in call.socket.sent)
    await call.handle({'type': 'session.thinking.appended', 'client_event_id': facts[-1]['event_id']})
    await settle(call)
    announcements = [event for event in call.socket.sent if event['type'] == 'session.commentary.append']
    assert len(announcements) == 1 and 'result-complete' in announcements[0]['content']
    assert 'long' not in call.delivered_generations  # Playback is not inferred from facts ACKs.


async def test_rejected_fact_prevents_completion_announcement_without_replaying_backend():
    service, call = call_fixture()
    await call.deliver_generation({'text': 'A verified useful finding. ' * 30, 'generation_id': 'rejected'}, 'd')
    facts = list(call.socket.sent)
    for event in facts[:-1]:
        await call.handle({'type': 'session.thinking.appended', 'client_event_id': event['event_id']})
    await call.handle({'type': 'error', 'error': {'code': 'invalid_request', 'client_event_id': facts[-1]['event_id']}})
    await settle(call)
    assert call.result_deliveries['rejected']['status'] == 'rejected'
    assert not any(event['type'] == 'session.commentary.append' for event in call.socket.sent)
    assert 'verified answer' in service.statuses[-1]['error']
    assert 'shared chat' in service.statuses[-1]['error']
    assert 'reconnect and ask for current status' in service.statuses[-1]['error']
    assert 'invalid_request' not in service.statuses[-1]['error']
    assert 'failed' not in service.statuses[-1]['error']
    assert service.calls == []


async def test_typed_root_result_preserves_long_facts_and_pending_worker_status():
    service, call = call_fixture()
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished',
        'generation_id': 'typed-result', 'sessionId': 'main', 'rootSessionId': 'main',
        'input_ids': ['typed-request'], 'active_job_ids': ['worker-still-running'],
        'text': 'The manager verified this useful diagnosis. ' * 20 + 'Final qualification: the worker is still checking the deployment.'}]
    await call.announce_generations(service.state)
    facts = list(call.socket.sent)
    assert all(event['type'] == 'session.thinking.append' for event in facts)
    assert 'Final qualification' in ' '.join(event['content'] for event in facts)
    for event in facts:
        await call.handle({'type': 'session.thinking.appended', 'client_event_id': event['event_id']})
    await settle(call)
    announcements = [event for event in call.socket.sent if event['type'] == 'session.commentary.append']
    assert len(announcements) == 1 and 'Background workers are still running' in announcements[0]['content']
    assert service.calls == []


async def test_accepted_typed_correction_demotes_voice_result_while_fact_acks_are_pending():
    service, call = call_fixture()
    call.request_revision = 1
    call.input_revisions['voice:live:original'] = 1
    await call.deliver_generation({'generation_id': 'old-voice', 'text': 'The Friday plan is ready. ' * 30},
                                  'original', revision=1)
    facts = list(call.socket.sent)
    service.state['sessions'][0]['messages'].append({'role': 'user', 'via': 'chat', 'inputId': 'typed-correction',
        'text': 'Use Thursday instead.', 'delivery': {'status': 'accepted'},
        'steering': {'disposition': 'queued'}})
    # No observer tick: ACK-triggered narration must inspect canonical acceptance itself.
    for event in facts:
        await call.handle({'type': 'session.thinking.appended', 'client_event_id': event['event_id']})
    await settle(call)
    assert call.input_revisions['typed-correction'] > 1
    assert not any(event['type'] == 'session.commentary.append' for event in call.socket.sent)
    assert 'Earlier request result' in call.socket.sent[-1]['content']
    assert service.calls == []


@pytest.mark.parametrize('delivery', ['sending', 'failed', 'unknown', None])
async def test_unaccepted_typed_message_does_not_supersede_verified_voice_result(delivery):
    service, call = call_fixture()
    call.request_revision = 1
    service.state['sessions'][0]['messages'].append({'role': 'user', 'via': 'chat', 'inputId': 'unaccepted',
        'text': 'Use Thursday instead.', **({'delivery': {'status': delivery}} if delivery else {})})
    await call.deliver_generation({'generation_id': 'verified', 'text': 'The Friday plan is ready.'}, revision=1)
    assert call.socket.sent[-1]['type'] == 'session.commentary.append'
    assert call.request_revision == 1 and 'unaccepted' not in call.input_revisions


async def test_typed_generation_revision_uses_accepted_input_before_observer_narration():
    service, call = call_fixture()
    messages = service.state['sessions'][0]['messages']
    messages.append({'role': 'user', 'via': 'chat', 'source': 'native', 'nativeInputId': 'typed-first', 'text': 'Use Friday.'})
    previous = call
    call = VoiceCall(previous.manager, 'main')
    call.id, call.socket = previous.id, previous.socket
    messages.append({'role': 'user', 'via': 'chat', 'inputId': 'typed-later', 'text': 'Use Thursday instead.',
                     'delivery': {'status': 'accepted'}})
    service.state['sessions'][0]['generations'] = [{'event': 'generation.finished', 'generation_id': 'first-result',
        'sessionId': 'main', 'rootSessionId': 'main', 'input_ids': ['typed-first'], 'text': 'Friday plan is ready.'}]
    await call.announce_generations(service.state)
    assert call.socket.sent[-1]['type'] == 'session.thinking.append'
    assert call.input_revisions['typed-later'] > call.input_revisions['typed-first']
    revision = call.request_revision
    await call.update_typed_context(service.state)
    assert call.request_revision == revision  # Observing the same admission never creates another revision.
    assert service.calls == []


async def test_provider_closed_without_usage_does_not_finalize_last_running_observation():
    service, call = call_fixture()
    service.record_voice_usage = AsyncMock()
    await call.handle({'type': 'session.usage.updated', 'usage': {'seconds': 12}})
    await call.handle({'type': 'session.closed'})
    await settle(call)
    calls = service.record_voice_usage.await_args_list
    assert calls[0].args[4:] == ({'seconds': 12}, 'running')
    assert calls[1].args[4] is None
    assert call.final.is_set() and call.closed


async def test_realtime_close_settles_only_responses_missing_their_final_receipt():
    service, call = call_fixture()
    call.provider = 'realtime'
    service.record_voice_usage = AsyncMock()
    call.manager.request = AsyncMock(return_value=({}, '', {}))
    await call.handle({'type': 'response.created', 'response': {'id': 'missing-final'}})
    await call.handle({'type': 'response.created', 'response': {'id': 'completed'}})
    await call.handle({'type': 'response.done', 'response': {'id': 'completed', 'usage': {'total_tokens': 12}}})
    call.socket.closed = True  # Sideband loss prevents a remaining response.done.
    result = await call.close()
    calls = service.record_voice_usage.await_args_list
    assert [row.args[2] for row in calls] == ['missing-final', 'completed', 'completed', 'missing-final']
    assert calls[2].args[4] == {'total_tokens': 12}
    assert calls[3].args[4:] == ({}, 'incomplete')
    assert not call.pending_realtime_responses
    assert result['closed'] and result['finalized']  # Hangup succeeded; response usage is separately incomplete.
    await call.close()
    assert service.record_voice_usage.await_count == 4


async def test_send_failure_does_not_relabel_successful_backend_work_as_failed():
    service, call = call_fixture()
    call.execute = AsyncMock(return_value={'generation_id': 'saved', 'text': 'The report is saved.'})
    call.socket.send_json = AsyncMock(side_effect=ConnectionError('transport failed'))
    await call.record('user', 'Save the report.', 'spoken')
    await call.delegate_live('d')
    assert call.execute.await_count == 1 and 'saved' not in call.delivered_generations
    assert any(row['status'] == 'delivery_unknown' for row in call.append_events.values())
    assert service.statuses[-1]['controlStatus'] == 'error'
    assert 'results remain in the shared chat' in service.statuses[-1]['error']
    assert call.socket.send_json.await_count == 1
