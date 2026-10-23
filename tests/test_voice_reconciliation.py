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
