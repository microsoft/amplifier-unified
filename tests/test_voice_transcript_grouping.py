"""Live display grouping uses approximate provider intervals, not speech intent."""
from unittest.mock import AsyncMock

import pytest

from amplifier_web.voice import VoiceCall, VoiceService
from test_voice import Service, Socket


async def fragment(call, role, text, start, end, identity):
    await call.handle({'type': 'session.input_transcript.delta' if role == 'user'
                       else 'session.output_transcript.delta',
                       'delta': text, 'start_ms': start, 'end_ms': end, 'event_id': identity})


def setup_call():
    service = Service()
    call = VoiceCall(VoiceService(service), 'main')
    call.id, call.socket = 'live', Socket()
    return service, call


@pytest.mark.parametrize('role,other,text,interjection,continuation', [
    ('assistant', 'user', 'That means', 'Yep.', ' the remaining issue is display.'),
    ('user', 'assistant', 'Please check', 'Hmm.', ' the same project.'),
    # The same intervals group identically without recognizing acknowledgments.
    ('assistant', 'user', 'The first part', 'Unrelated words.', ' continues here.'),
])
async def test_overlapping_speakers_keep_independent_display_rows(role, other, text, interjection, continuation):
    service, call = setup_call()
    await fragment(call, role, text, 100, 400, 'first')
    await fragment(call, other, interjection, 300, 500, 'interjection')
    await fragment(call, role, continuation, 400, 700, 'continuation')
    rows = service.transcripts
    assert [row[0] for row in rows] == [role, other, role]
    assert rows[0][2]['item_id'] == rows[2][2]['item_id']
    assert rows[2][2]['append'] is True
    assert rows[1][2]['item_id'] != rows[0][2]['item_id']
    assert [row[1] for row in rows] == [text, interjection, continuation]
    assert service.calls == []


@pytest.mark.parametrize('role,other', [('assistant', 'user'), ('user', 'assistant')])
async def test_nonoverlapping_exchange_starts_a_new_display_row(role, other):
    service, call = setup_call()
    await fragment(call, role, 'First.', 100, 200, 'first')
    await fragment(call, other, 'Reply.', 200, 300, 'reply')
    await fragment(call, role, 'Next.', 300, 400, 'next')
    assert len({row[2]['item_id'] for row in service.transcripts}) == 3
    assert service.transcripts[-1][2]['append'] is False


async def test_late_interval_updates_open_row_then_later_exchange_still_splits():
    service, call = setup_call()
    await fragment(call, 'user', 'First', 100, 200, 'first')
    await fragment(call, 'assistant', 'Reply.', 500, 600, 'reply')
    # The reply arrived first, but this user's fragment precedes it on the
    # provider timeline. A later fragment after that reply is a new row.
    await fragment(call, 'user', ' request.', 200, 400, 'late')
    await fragment(call, 'user', 'Next request.', 700, 800, 'next')
    rows = service.transcripts
    assert rows[2][2]['item_id'] == rows[0][2]['item_id']
    assert rows[2][2]['append'] is True
    assert rows[3][2]['item_id'] != rows[0][2]['item_id']
    assert rows[3][2]['append'] is False
    assert call.user_text == 'First request.Next request.'
    assert service.calls == []


@pytest.mark.parametrize('bad', [(None, 400), (100, None), (500, 400), (400, 400),
                                  (-1, 400), (True, 400), (100, float('inf')),
                                  (float('nan'), 400), ('100', 400)])
@pytest.mark.parametrize('position', [0, 1, 2])
async def test_invalid_intervals_fall_back_to_observed_speaker_order(bad, position):
    service, call = setup_call()
    intervals = [(100, 400), (300, 500), (400, 700)]
    intervals[position] = bad
    for i, (role, text) in enumerate([('user', 'First'), ('assistant', 'Reply'), ('user', ' next')]):
        await fragment(call, role, text, *intervals[i], f'event-{i}')
    assert service.transcripts[2][2]['item_id'] != service.transcripts[0][2]['item_id']
    # Missing timing must not introduce a gap-based split within one speaker.
    await fragment(call, 'user', ' continuation', None, None, 'continuation')
    assert service.transcripts[3][2]['item_id'] == service.transcripts[2][2]['item_id']
    assert service.calls == []


async def test_overlap_display_preserves_admission_identity_and_never_replays():
    service, call = setup_call()
    call.execute = AsyncMock(return_value={'response': 'Done'})
    await fragment(call, 'user', 'Check', 100, 400, 'first')
    await fragment(call, 'assistant', 'Okay.', 300, 500, 'reply')
    await fragment(call, 'user', ' the project.', 400, 700, 'continuation')
    assert call.execute.await_count == 0
    await call.delegate_live('request')
    original = service.transcripts[0][2]['item_id']
    call.execute.assert_awaited_once_with('Check the project.', 'request', spoken_item_id=original)
    await call.delegate_live('request')
    assert call.execute.await_count == 1
    # Acceptance is a strict display fence even with overlapping timestamps.
    await fragment(call, 'user', 'Another request.', 600, 800, 'after-admission')
    assert service.transcripts[-1][2]['item_id'] != original
    assert service.transcripts[-1][2]['append'] is False
    assert call.execute.await_count == 1
    call.final.set()
    await call.close()
