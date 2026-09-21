"""Stable display identity is independent of command provenance."""
import pytest
from amplifier_web.service import AppService


@pytest.mark.parametrize('event,payload', [
    ('runtime.error', {'error': 'fixture interrupted'}),
    ('runtime.status', {'status': 'stopped'}),
    ('runtime.status', {'status': 'idle'}),
    ('runtime.ownership', {'status': 'yielded'}),
    ('runtime.ended', {'status': 'interrupted'}),
])
async def test_interrupted_partial_never_becomes_the_next_answer(tmp_path, event, payload):
    service = AppService(tmp_path, workspace=tmp_path)
    try:
        await service.dispatch('session.create', {})
        sid = service._session()['id']
        await service.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'incomplete'})
        identity = service._session()['streamingId']
        await service.on_runtime_event(event, {'sessionId': sid, **payload})
        assert 'streamingId' not in service._session()
        partial = service._session()['messages'][-1]
        assert partial['streamId'] == identity and partial['partial']
        assert 'inputId' not in partial
        await service.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'new answer'})
        assert service._session()['streamingId'] != identity
        next_id = service._session()['streamingId']
        await service.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'new answer', 'inputId': 'new'})
        final = service._session()['messages'][-1]
        assert final['streamId'] == next_id and not final.get('partial')
        assert final['inputId'] == 'new'
    finally:
        await service.close()


@pytest.mark.parametrize('monitor', [False, True])
async def test_final_stream_identity_preserves_generation_deduplication(tmp_path, monitor):
    service = AppService(tmp_path, workspace=tmp_path)
    try:
        await service.dispatch('session.create', {})
        sid = service._session()['id']
        await service.on_runtime_event('assistant.delta', {'sessionId': sid, 'text': 'answer'})
        identity = service._session()['streamingId']
        payload = {'sessionId': sid, 'text': 'answer', 'inputId': 'input-1',
                   'generationId': 'generation-1', 'scheduled_monitor_only': monitor}
        await service.on_runtime_event('assistant.message', payload)
        await service.on_runtime_event('assistant.message', payload)
        messages = [row for row in service._session()['messages'] if row['role'] == 'assistant']
        assert len(messages) == 1
        assert messages[0]['streamId'] == identity
        assert messages[0]['generationId'] == 'generation-1'
        assert messages[0]['inputId'] == 'input-1'
        assert messages[0]['via'] == ('schedule' if monitor else 'chat')
        assert 'streamingId' not in service._session()
    finally:
        await service.close()
