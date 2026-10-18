"""Synthetic regressions from the interrupted Settings conversation."""
import asyncio
import json

import pytest

from amplifier_web.voice import spoken_reference, startup_history
from test_voice_reconciliation import call_fixture, settle


@pytest.mark.parametrize('staged', [False, True])
async def test_result_waiting_to_send_yields_to_speech_before_next_delegation(staged):
    service, call = call_fixture()
    call.request_revision = 1
    if staged:
        await call.deliver_generation({'generation_id': 'closed', 'text': 'Settings is closed. ' * 35}, revision=1)
        facts = list(call.socket.sent)
    await call.record('user', 'The latency is too high, and', delta=True, start_ms=8000, at=9000)
    if staged:
        for event in facts:
            await call.handle({'type': 'session.thinking.appended', 'client_event_id': event['event_id']})
        await settle(call)
    else:
        await call.deliver_generation({'generation_id': 'closed', 'text': 'Settings is closed.'}, revision=1)
    assert call.socket.sent[-1]['type'] == 'session.thinking.append'
    assert 'speech' in call.socket.sent[-1]['content']
    assert call.request_revision == 1 and not service.calls
    assert not any(row['type'] == 'session.commentary.append' for row in call.socket.sent)


async def test_speech_persistence_and_append_lock_cannot_hide_pending_input():
    service, call = call_fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    async def persist(*args, **kwargs):
        entered.set()
        await release.wait()
    service.record_voice_transcript = persist
    recording = asyncio.create_task(call.record('user', 'Wait, another thing.', delta=True))
    await entered.wait()
    assert not call.transcript_fragments
    try:
        await call.deliver_generation({'generation_id': 'old', 'text': 'The panel is closed.'})
        assert call.socket.sent[-1]['type'] == 'session.thinking.append'
    finally:
        release.set()
        await recording
    assert not service.calls


async def test_result_queued_on_append_lock_rechecks_new_speech():
    service, call = call_fixture()
    await call.append_lock.acquire()
    result = asyncio.create_task(call.deliver_generation({'generation_id': 'old', 'text': 'The panel is closed.'}))
    await asyncio.sleep(0)
    await call.record('user', 'Please listen to the new issue.', delta=True)
    call.append_lock.release()
    await result
    assert call.socket.sent[-1]['type'] == 'session.thinking.append'
    assert not service.calls


async def test_admission_resolves_speech_gate_without_replaying_completed_work():
    service, call = call_fixture()
    service.result.set()
    await call.record('user', 'Inspect the connection.', 'spoken')
    await call.delegate_live('new')
    assert call.socket.sent[-1]['type'] == 'session.commentary.append'
    assert len(service.calls) == 1
    await call.deliver_generation({'generation_id': 'external', 'text': 'A separate completed update.'})
    assert call.socket.sent[-1]['type'] == 'session.commentary.append'
    assert len(service.calls) == 1


async def test_delegation_references_only_unshared_spoken_context_with_whole_messages():
    service, call = call_fixture()
    service.result.set()
    service.state['sessions'][0]['messages'] = [
        {'role': 'assistant', 'text': 'Backend answer already in native context.', 'source': 'amplifier', 'via': 'call'},
        {'role': 'user', 'text': 'Already admitted request.', 'voiceId': 'live', 'voiceItemId': 'old', 'voiceInputId': 'accepted', 'nativeIndex': 1},
        {'role': 'user', 'text': 'Call me River.', 'voiceId': 'live', 'voiceItemId': 'smalltalk'},
        {'role': 'assistant', 'text': 'Would you like to review the checkout flow?', 'voiceId': 'live', 'voiceItemId': 'offer', 'via': 'call'},
        {'role': 'assistant', 'text': 'Oversized claim. ' * 1000 + 'Final caveat.', 'voiceId': 'live', 'voiceItemId': 'long'},
        {'role': 'user', 'text': 'Yes, do that.', 'voiceId': 'live', 'voiceItemId': 'current'},
    ]
    await call.execute('Yes, do that.', 'new', spoken_item_id='current')
    prompt = service.calls[0][0]
    assert 'Backend answer already' not in prompt and 'Already admitted request' not in prompt
    assert 'Call me River.' in prompt and 'checkout flow' in prompt
    assert 'Oversized claim.' not in prompt  # Never keep a claim while cutting its caveat.
    assert 'longer spoken message remains' in prompt
    assert prompt.count('Yes, do that.') == 1
    assert len(prompt.encode()) < 4500
    from amplifier_web.voice_input_projection import decode
    public = decode(prompt, service.calls[0][1])
    assert public['text'] == 'Yes, do that.' and public['callId'] == 'live'


async def test_failed_provider_preparation_does_not_erase_unresolved_spoken_reference():
    service, call = call_fixture()
    service.result.set()
    service.state['sessions'][0].update(
        messages=[{'role': 'user', 'text': 'Inspect the newly saved connection.', 'voiceId': 'live',
                   'voiceItemId': 'failed', 'voiceInputId': 'failed-input'}],
        execution={'turns': [{'inputId': 'failed-input', 'phase': 'error'}]})
    await call.execute('Can we continue now?', 'current')
    assert 'Inspect the newly saved connection.' in service.calls[0][0]
    assert '"priorAttempt": "error"' in service.calls[0][0]
    assert len(service.calls) == 1 and service.calls[0][1] == 'voice:live:current'


def test_additional_spoken_reference_remains_bounded_with_multibyte_history():
    rows = [{'role': 'assistant', 'text': '界' * 300, 'voiceId': 'call', 'voiceItemId': str(index)} for index in range(100)]
    reference = spoken_reference({'messages': rows}, set())
    assert 0 < len(reference) <= 4
    assert len(json.dumps(reference, ensure_ascii=False).encode()) <= 2400
    assert all(row['text'] == '界' * 300 for row in reference)


def test_resume_labels_backend_facts_separately_from_spoken_transcripts():
    seed = startup_history({'sessions': [{'id': 'main', 'messages': [
        {'role': 'assistant', 'text': 'The settings panel is closed.', 'source': 'amplifier', 'via': 'call'},
        {'role': 'assistant', 'text': 'I will check.', 'voiceId': 'live', 'voiceItemId': 'spoken', 'via': 'call'},
    ]}]}, 'main')
    assert 'backend result' in seed[0]['content'][0]['text']
    assert seed[1]['content'][0]['text'] == 'I will check.'
