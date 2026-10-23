"""Streaming and interrupted backend output retain input provenance for display."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.service import AppService, settle_stream


@pytest.mark.parametrize('inputs,delivered,expected', [
    (None, 'voice:call:one', 'voice:call:one'),
    (['typed-old', 'voice:call:two'], 'typed-delivered', 'voice:call:two'),
    (['voice:call:old', 'typed-current'], 'voice:call:old', 'typed-current'),
    ([], None, None),
])
def test_delta_and_final_share_explicit_or_delivered_input_identity(inputs, delivered, expected):
    raw = {'text': 'Visible result', 'generation_id': 'generation-one', 'request_id': 'request-one',
           'block_index': 0, 'reasoning': 'private'}
    if inputs is not None:
        raw['input_ids'] = inputs
    _, delta = normalize_event({'type': 'assistant.delta', **raw}, 'root', delivered)
    _, final = normalize_event({'type': 'assistant.message', **raw}, 'root', delivered)
    assert delta['inputId'] == final['inputId'] == expected
    assert delta['generationId'] == final['generationId'] == 'generation-one'
    assert delta['inputIds'] == final['inputIds'] == (inputs or [])
    assert delta['requestId'] == 'request-one' and delta['blockIndex'] == 0
    assert 'reasoning' not in delta


async def test_worker_first_voice_chunk_keeps_delivered_owner_through_browser_projection(tmp_path):
    app = AppService(tmp_path / 'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        sid = session['id']
        app.state['voice'].update(status='connected', sessionId=sid)
        # A newer queued input is not the owner of already-running output.
        session['execution'] = {'nodes': [], 'turns': [], 'currentTurnId': 'typed-queued'}
        canonical, snapshots = [], []

        async def emit(kind, payload):
            await app.on_runtime_event(kind, payload)
            if kind == 'assistant.delta':
                canonical.append((session['streaming'], session.get('streamingInputId')))
                # Stream mutations are coalesced before receiving a durable
                # revision. Observe the same flush used by external reads,
                # rather than a cached projection of the previous revision.
                await app._flush_pending_progress()
                visible = next(row for row in app.browser_state()['sessions'] if row['id'] == sid)
                snapshots.append((visible['streaming'], visible.get('streamingInputId')))

        frames = [
            {'type': 'input.delivered', 'session_id': 'native-root', 'input_id': 'voice:call:one'},
            {'type': 'assistant.delta', 'text': 'First voice chunk'},
            {'type': 'input.delivered', 'session_id': 'child', 'root_session_id': 'native-root', 'input_id': 'child-input'},
            {'type': 'assistant.delta', 'text': ' and more'},
            {'type': 'assistant.message', 'text': 'First voice chunk and more'},
            {'type': 'input.delivered', 'session_id': 'native-root', 'input_id': 'typed-current'},
            {'type': 'assistant.delta', 'text': 'Typed while the call remains connected'},
        ]
        stream = asyncio.StreamReader()
        stream.feed_data(b''.join(json.dumps(row).encode() + b'\n' for row in frames))
        stream.feed_eof()
        ready = asyncio.get_running_loop().create_future()
        ready.set_result({})

        async def exited():
            return 0

        row = {'process': SimpleNamespace(stdout=stream, returncode=0, wait=exited),
               'emit': emit, 'closing': True, 'ready': ready, 'runtime_id': 'native-root',
               'inputId': None, 'pending': {}, 'bridge_tasks': set()}
        manager = RuntimeManager()
        manager.workers[sid] = row
        await manager._read(sid, row)
        assert canonical == snapshots == [
            ('First voice chunk', 'voice:call:one'),
            ('First voice chunk and more', 'voice:call:one'),
            ('Typed while the call remains connected', 'typed-current'),
        ]
        result = next(message for message in session['messages'] if message.get('source') == 'amplifier')
        assert result['inputId'] == 'voice:call:one' and result['via'] == 'call'
    finally:
        await app.close()


async def test_voice_stream_is_attributed_before_final_message_and_cleared_after(tmp_path):
    app = AppService(tmp_path / 'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        sid = session['id']
        await app.on_runtime_event('assistant.delta', {'sessionId': sid, 'inputId': 'voice:call:one', 'text': 'Working'})
        assert session['streamingInputId'] == 'voice:call:one'
        await app.on_runtime_event('assistant.message', {'sessionId': sid, 'inputId': 'voice:call:one', 'text': 'Done'})
        assert 'streamingInputId' not in session
        assert session['messages'][-1]['via'] == 'call'
        assert session['messages'][-1]['source'] == 'amplifier'
        await app.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'Unknown origin'})
        assert 'streamingInputId' not in session
    finally:
        await app.close()


async def test_switching_input_preserves_interrupted_voice_result_without_mislabeling_typed_stream(tmp_path):
    app = AppService(tmp_path / 'data', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        await app.on_runtime_event('assistant.delta', {'sessionId': session['id'], 'inputId': 'voice:call:one', 'text': 'Partial work'})
        old_stream = session['streamingId']
        await app.on_runtime_event('assistant.delta', {'sessionId': session['id'], 'inputId': 'typed-two', 'text': 'Typed work'})
        interrupted = session['messages'][-1]
        assert interrupted['streamId'] == old_stream
        assert interrupted['inputId'] == 'voice:call:one' and interrupted['via'] == 'call'
        assert interrupted['source'] == 'interrupted-stream' and interrupted['partial']
        assert session['streaming'] == 'Typed work' and session['streamingInputId'] == 'typed-two'
        new_stream = session['streamingId']
        await app.on_runtime_event('assistant.message', {'sessionId': session['id'], 'inputId': 'voice:call:one', 'text': 'Late old final'})
        assert 'streamId' not in session['messages'][-1]
        assert session['streamingId'] == new_stream and session['streaming'] == 'Typed work'
        assert session['streamingInputId'] == 'typed-two'
        settle_stream(session)
        assert session['messages'][-1]['via'] == 'chat'
        assert not any(key in session for key in ('streaming', 'streamingId', 'streamingInputId'))
    finally:
        await app.close()
