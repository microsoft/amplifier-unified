"""Whole-fragment pairing when multiple Live delegations are queued."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from amplifier_web.voice import VoiceCall, setup_routes
from test_voice_reconciliation import call_fixture, settle
from test_voice import Service, Socket


async def speech(call, identity, text, start, end):
    await call.handle({'type': 'session.input_transcript.delta', 'event_id': identity,
                       'delta': text, 'start_ms': start, 'end_ms': end})


async def delegation(call, identity, offset):
    await call.handle({'type': 'session.delegation.created', 'event_id': 'event-' + identity,
                       'offset_ms': offset, 'delegation': {'id': identity}})


async def test_queued_delegations_do_not_claim_future_speech_or_strand_next_identity(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_WAIT_SECONDS', .01)
    service, call = call_fixture()
    service.result.set()
    # No event-loop yield: provider messages can already be buffered together.
    await speech(call, 'a', 'Check the report.', 100, 200)
    await delegation(call, 'd1', 250)
    await speech(call, 'b', 'Now inspect the chart.', 500, 600)
    await delegation(call, 'd2', 650)
    await settle(call)
    assert [(row[1], row[0].split('Current spoken user request:\n')[-1]) for row in service.calls] == [
        ('voice:live:d1', 'Check the report.'), ('voice:live:d2', 'Now inspect the chart.')]
    await speech(call, 'c', 'Check the settings.', 1000, 1100)
    await delegation(call, 'd3', 1150)
    await settle(call)
    assert service.calls[-1][1] == 'voice:live:d3'
    assert service.calls[-1][0].endswith('Check the settings.')


async def test_expired_metadata_does_not_consume_later_unrelated_speech(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_WAIT_SECONDS', .01)
    service, call = call_fixture()
    service.result.set()
    await delegation(call, 'missing-transcript', 250)
    await settle(call)
    await speech(call, 'later', 'Inspect the current project.', 1000, 1100)
    await settle(call)
    assert service.calls == []
    await delegation(call, 'current', 1150)
    await settle(call)
    assert len(service.calls) == 1 and service.calls[0][1] == 'voice:live:current'


async def test_late_earlier_fragment_keeps_original_owner_while_later_request_waits(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_WAIT_SECONDS', .02)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'a', 'Check smart clothes.', 100, 200)
    await delegation(call, 'first', 400)
    await settle(call)
    await delegation(call, 'second', 1000)
    await asyncio.sleep(0)
    await speech(call, 'late-a', ' I meant smart tools.', 250, 350)
    await settle(call)
    assert len(service.calls) == 2
    assert service.calls[1][1].startswith('voice:live:first-transcript-')
    assert 'already accepted input voice:live:first' in service.calls[1][0]
    await speech(call, 'b', 'Inspect the chart.', 800, 900)
    await settle(call)
    assert len(service.calls) == 3 and service.calls[2][1] == 'voice:live:second'
    assert service.calls[2][0].endswith('Inspect the chart.')


async def test_admission_retry_keeps_immutable_request_when_more_speech_arrives():
    service, call = call_fixture()
    service.result.set()
    service.voice_delegate = AsyncMock(side_effect=ValueError('admission unavailable'))
    await call.record('user', 'Inspect the report.', 'first')
    await call.delegate_live('first')
    original = service.voice_delegate.await_args.args[0]
    await call.record('user', 'Inspect the chart too.', 'second')
    await call.delegate_live('first')
    assert service.voice_delegate.await_args.args[0] == original
    assert call.pending_delegations['first']['text'] == 'Inspect the report.'
    assert any(not row['consumed'] and 'chart' in row['text'] for row in call.transcript_fragments)


async def test_idle_transcript_limit_requires_restatement_without_losing_shared_history(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_IDLE_MAX_FRAGMENTS', 2)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    service, call = call_fixture()
    service.result.set()
    for index in range(8):
        await speech(call, str(index), 'Earlier conversational context. ', index * 100, index * 100 + 50)
    assert len(call.transcript_fragments) <= 2
    assert len(service.transcripts) == 8  # Canonical transcript persistence is unaffected.
    await delegation(call, 'incomplete', 800)
    await settle(call)
    assert service.calls == []
    assert any('restate your current request' in event.get('content', '') for event in call.socket.sent)
    assert call.pending_delegations['incomplete']['status'] == 'needs_clarification'
    await speech(call, 'restatement', 'Inspect the current report.', 900, 950)
    await delegation(call, 'current', 1000)
    await settle(call)
    assert len(service.calls) == 1 and service.calls[0][1] == 'voice:live:current'
    assert service.calls[0][0].endswith('Inspect the current report.')


async def test_idle_age_expiration_and_late_metadata_never_execute_partial_request(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'old', 'Only if approved, change the deployment.', 100, 200)
    call.transcript_fragments[0]['received_at'] -= 600
    await delegation(call, 'stale', 250)
    await settle(call)
    assert service.calls == []
    # A second delayed provider delegation inside expired history cannot revive it.
    await delegation(call, 'still-stale', 150)
    await settle(call)
    assert service.calls == []
    assert call.pending_delegations['still-stale']['status'] == 'needs_clarification'
    await speech(call, 'late-old', 'Only if approved.', 100, 200)
    await settle(call)
    assert service.calls == [] and call.transcript_fragments[-1]['consumed']


async def test_idle_byte_limit_does_not_trim_known_pending_or_retry_inputs(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_IDLE_MAX_BYTES', 20)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'oversized', 'This idle request is longer than the retained budget.', 100, 200)
    assert call.transcript_fragments == []
    await delegation(call, 'clarify', 250)
    await settle(call)
    assert service.calls == []
    service.voice_delegate = AsyncMock(side_effect=ValueError('admission unavailable'))
    # A known waiting request retains its whole input even if idle limits are small.
    call.pending_delegations['known'] = {'id': 'known', 'provider_id': 'known', 'status': 'waiting',
                                       'offset_ms': 1000, 'capture_version': call.user_version, 'settle': False}
    await speech(call, 'current', 'Inspect the complete original report.', 800, 900)
    await settle(call)
    original = service.voice_delegate.await_args.args[0]
    for row in call.transcript_fragments:
        row['received_at'] -= 600
    call.expire_idle_transcripts()
    assert call.transcript_fragments[0]['text'] == 'Inspect the complete original report.'
    await call.delegate_live('known')
    assert service.voice_delegate.await_args.args[0] == original


@pytest.mark.parametrize('terminal', ['socket', 'pump'])
async def test_late_ready_cannot_resurrect_failed_sideband(aiohttp_client, terminal):
    service = Service()
    app = web.Application()
    app['service'] = service
    manager = setup_routes(app)
    call = manager.call = VoiceCall(manager, 'main')
    call.id, call.attempt_id, call.socket = 'live', 'attempt', Socket()
    call.pump = asyncio.get_running_loop().create_future()
    if terminal == 'socket':
        call.socket.closed = True
    else:
        call.pump.set_result(None)
    await service.set_voice_status({'id': 'live', 'attemptId': 'attempt', 'controlStatus': 'error'})
    client = await aiohttp_client(app)
    response = await client.post('/api/voice/ready', json={'id': 'live', 'attemptId': 'attempt'})
    assert response.status == 409
    assert not any(row.get('status') == 'connected' for row in service.statuses)
    assert call.observer is None
    call.final.set()


# Exact synthetic transcript timing observed in real round7. The harness did
# not retain the top-level delegation offset, so exercise both valid metadata
# cases without claiming its missing value was recovered.
CONTINUOUS_TAIL = [
    (' Also, ask', 31000, 31200), (' Amplifier to', 31200, 31400), (' read', 31400, 31600),
    (' shipment', 32000, 32200), (' dot', 32400, 32600), (' text', 32800, 33000),
    (' in', 33000, 33200), (' this', 33200, 33400), (' workspace', 33800, 34000),
    (', and', 34000, 34200), (' tell', 34400, 34600), (' me', 34600, 34800),
    (' its', 34800, 35000), (' exact', 35600, 35800), (' validation', 36200, 36400),
    (' code', 36800, 37000),
]


@pytest.mark.parametrize('offset', [29200, None])
async def test_continuous_tail_after_early_result_is_one_complete_supplement(monkeypatch, offset):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_SETTLE_SECONDS', .03)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'initial', 'Ask Amplifier for the shipment code in the article I just pasted in this chat', 24800, 29200)
    await delegation(call, 'original', offset)
    await settle(call)
    assert len(service.calls) == 1
    for index, (text, start, end) in enumerate(CONTINUOUS_TAIL):
        await speech(call, 'tail-' + str(index), text, start, end)
        await asyncio.sleep(.005)
        assert len(service.calls) == 1  # No per-word admission while the sentence continues.
    await settle(call)
    assert len(service.calls) == 2
    assert service.calls[1][0].endswith('Continuation: ' + ''.join(row[0] for row in CONTINUOUS_TAIL).strip())
    assert 'already accepted input voice:live:original' in service.calls[1][0]
    assert any('Earlier results cover only the accepted portion' in row.get('content', '') for row in call.socket.sent)


async def test_new_native_delegation_claims_unsubmitted_tail_without_duplicate_work(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_SETTLE_SECONDS', .06)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'initial', 'Read the article.', 28000, 29200)
    await delegation(call, 'original', 29200)
    await settle(call)
    await speech(call, 'tail', ' Also read the file.', 31000, 32000)
    await asyncio.sleep(.01)  # The supplemental batch is coalescing, not admitted.
    await delegation(call, 'native-file', 32000)
    await settle(call)
    assert [row[1] for row in service.calls] == ['voice:live:original', 'voice:live:native-file']
    assert service.calls[-1][0].endswith('Also read the file.')
    assert not any(row[1].startswith('voice:live:original-transcript-') for row in service.calls)


@pytest.mark.parametrize('start', [32000, 105200])
async def test_disjoint_future_speech_does_not_auto_supplement_completed_request(monkeypatch, start):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'initial', 'Read the article.', 28000, 29200)
    await delegation(call, 'original', 29200)
    await settle(call)
    await speech(call, 'later', 'Inspect the current project.', start, start + 800)
    await settle(call)
    assert len(service.calls) == 1
    await delegation(call, 'later', start + 800)
    await settle(call)
    assert service.calls[-1][1] == 'voice:live:later'


async def test_supplement_admission_retry_keeps_original_batch_fingerprint(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_SETTLE_SECONDS', .01)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'initial', 'Read the article.', 28000, 29200)
    await delegation(call, 'original', 29200)
    await settle(call)
    service.voice_delegate = AsyncMock(side_effect=ValueError('admission unavailable'))
    await speech(call, 'tail', ' Also inspect the report.', 31000, 32000)
    await settle(call)
    original_call = service.voice_delegate.await_args
    did = original_call.kwargs['command_id'].split(':', 2)[-1]
    await speech(call, 'more', ' Use the latest version.', 32200, 33000)
    await call.delegate_live(did)
    await settle(call)
    retried = [row for row in service.voice_delegate.await_args_list if row.kwargs['command_id'] == original_call.kwargs['command_id']]
    assert len(retried) == 2 and retried[0].args[0] == retried[1].args[0]


async def test_later_native_without_offset_fences_older_timed_continuation(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_SETTLE_SECONDS', .03)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'a', 'Read the article.', 100, 1000)
    await delegation(call, 'timed', 1000)
    await settle(call)
    await speech(call, 'b', 'Inspect the report.', 1400, 1800)
    await delegation(call, 'untimed', None)
    await settle(call)
    await speech(call, 'c', ' Include its final qualification.', 2000, 2200)
    await settle(call)
    assert [row[1] for row in service.calls[:2]] == ['voice:live:timed', 'voice:live:untimed']
    assert len(service.calls) == 3 and service.calls[2][1].startswith('voice:live:untimed-transcript-')
    assert 'already accepted input voice:live:untimed' in service.calls[2][0]


@pytest.mark.parametrize('limit', ['timeline', 'quiet_wait', 'quiet_wait_native_first'])
async def test_continuation_limit_clarifies_without_submitting_partial_work(monkeypatch, limit):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_SETTLE_SECONDS', .04)
    if limit == 'timeline':
        monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_CONTINUATION_SPAN_MS', 1000)
    else:
        monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_MAX_SETTLE_SECONDS', .02)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'a', 'Inspect the current state.', 100, 1000)
    await delegation(call, 'original', 1000)
    await settle(call)
    await speech(call, 'b', ' Also change the deployment', 1200, 1500)
    await asyncio.sleep(.005)
    if limit == 'timeline':
        await speech(call, 'c', ' only if the review approves it.', 1600, 2200)
    await settle(call)
    assert len(service.calls) == 1
    assert any('Please restate the added request' in row.get('content', '') for row in call.socket.sent)
    assert call.pending_delegations['original']['continuation_blocked']
    # Metadata alone cannot revive the rejected partial batch. Further speech
    # arriving before quiet remains incomplete until the user can restate it.
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_WAIT_SECONDS', .01)
    if limit == 'quiet_wait_native_first':
        await delegation(call, 'late-metadata', 2500)
        await asyncio.sleep(0)
    if limit.startswith('quiet_wait'):
        await speech(call, 'd', ' after the final review.', 2300, 2500)
    if limit != 'quiet_wait_native_first':
        await delegation(call, 'late-metadata', 2500)
    await settle(call)
    assert len(service.calls) == 1
    await asyncio.sleep(.05)
    await speech(call, 'restatement', 'Inspect the report; do not change anything.', 4000, 5000)
    await delegation(call, 'fresh', 5000)
    await settle(call)
    assert len(service.calls) == 2 and service.calls[-1][1] == 'voice:live:fresh'
    assert service.calls[-1][0].endswith('Inspect the report; do not change anything.')


async def test_hangup_during_continuation_quiet_wait_does_not_admit_added_work(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_SETTLE_SECONDS', .08)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'a', 'Read the article.', 100, 1000)
    await delegation(call, 'original', 1000)
    await settle(call)
    await speech(call, 'b', ' Also read the report.', 1200, 1800)
    await asyncio.sleep(.01)
    call.final.set()
    await call.close()
    await settle(call)
    assert [row[1] for row in service.calls] == ['voice:live:original']


async def test_partial_result_ack_during_tail_wait_stays_quiet_without_advancing_revision(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_SETTLE_SECONDS', .08)
    service, call = call_fixture()
    service.result.set()
    await speech(call, 'a', 'Read the article.', 100, 1000)
    await delegation(call, 'original', 1000)
    await settle(call)
    call.socket.sent.clear()
    await call.deliver_generation({'generation_id': 'partial', 'text': 'The first requested fact is verified. ' * 20},
                                  'original', revision=1)
    facts = list(call.socket.sent)
    # Admission fails later: pending transcript alone must not acquire a canonical revision.
    service.voice_delegate = AsyncMock(side_effect=ValueError('admission unavailable'))
    await speech(call, 'b', ' Also inspect the report.', 1200, 1800)
    for event in facts:
        await call.handle({'type': 'session.thinking.appended', 'client_event_id': event['event_id']})
    await asyncio.sleep(.01)
    assert call.request_revision == 1
    assert not any(row['type'] == 'session.commentary.append' for row in call.socket.sent)
    assert any('accepted portion only' in row.get('content', '') for row in call.socket.sent)
    await settle(call)
    assert call.request_revision == 1
    assert any('could not complete' in row.get('content', '') for row in call.socket.sent)
    await call.deliver_generation({'generation_id': 'current-status', 'text': 'The article code is verified.'},
                                  'original', revision=1)
    assert call.socket.sent[-1]['type'] == 'session.commentary.append'
    assert 'added request was not accepted' in call.socket.sent[-1]['content']
    assert 'facts remain unverified' in call.socket.sent[-1]['content']


async def test_complete_tail_arriving_during_admission_wait_reconciles_once(monkeypatch):
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SETTLE_SECONDS', .01)
    monkeypatch.setattr('amplifier_web.voice.TRANSCRIPT_SUPPLEMENT_SETTLE_SECONDS', .03)
    service, call = call_fixture()
    service.result.set()
    admission, entered = asyncio.Event(), asyncio.Event()
    original_delegate = service.voice_delegate
    async def held_admission(text, command_id, **kwargs):
        if command_id == 'voice:live:original':
            entered.set()
            await admission.wait()
        return await original_delegate(text, command_id, **kwargs)
    service.voice_delegate = held_admission
    await speech(call, 'initial', 'Read the article.', 24800, 29200)
    await delegation(call, 'original', 29200)
    await asyncio.wait_for(entered.wait(), .2)
    for index, (text, start, end) in enumerate(CONTINUOUS_TAIL):
        await speech(call, 'tail-' + str(index), text, start, end)
    assert service.calls == []
    admission.set()
    await settle(call)
    assert len(service.calls) == 2
    assert service.calls[-1][0].endswith('Continuation: ' + ''.join(row[0] for row in CONTINUOUS_TAIL).strip())
    assert all(row['consumed'] for row in call.transcript_fragments)
