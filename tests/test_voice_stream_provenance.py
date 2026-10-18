"""Streaming and interrupted backend output retain input provenance for display."""
import pytest

from amplifier_web.service import AppService, settle_stream


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
