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
