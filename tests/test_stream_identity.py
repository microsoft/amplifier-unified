"""Stable display identity is independent of command provenance."""
import copy
import time
from unittest.mock import AsyncMock

import pytest
from amplifier_web.execution import ensure_turn
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService


async def owned_turn(service, input_id, owner):
    session = service._session()
    tree = ensure_turn(session, input_id)
    await service.on_runtime_event('runtime.generation', {
        'sessionId': session['id'], 'rootSessionId': session['id'],
        'event': 'generation.started', 'initial_input_id': input_id,
        'generation_id': 'generation-' + input_id, 'observedAt': time.time(),
        'runtimeOwnerId': owner, 'interruptionRevision': session.get('interruptionRevision', 0)})
    turn = next(row for row in tree['turns'] if row['id'] == input_id)
    assert tree['currentTurnId'] == input_id
    assert turn['runtimeOwnerId'] == owner
    assert turn['generationId'] == 'generation-' + input_id
    return turn


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
        if event == 'runtime.ended':
            # A process exit settles only its actual foreground owner. The
            # legacy delta below can still lack a display input association.
            await owned_turn(service, 'interrupted-input', 'interrupted-owner')
            payload = {**payload, 'rootSessionId': sid, 'runtimeOwnerId': 'interrupted-owner'}
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


@pytest.mark.parametrize('boundary', [
    'missing-owner', 'foreign-owner', 'child', 'foreign-root', 'background', 'stale-owner',
])
async def test_unowned_exit_preserves_stream_until_exact_owner_ends(tmp_path, monkeypatch, boundary):
    runtime = RuntimeManager(command=['unused'])
    service = AppService(tmp_path, runtime, workspace=tmp_path)
    send, start = AsyncMock(), AsyncMock()
    monkeypatch.setattr(runtime, 'send', send)
    monkeypatch.setattr(runtime, 'start', start)
    try:
        await service.dispatch('session.create', {})
        session = service._session()
        sid = session['id']
        if boundary == 'stale-owner':
            await owned_turn(service, 'older-input', 'older-owner')
            await service.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
        turn = await owned_turn(service, 'current-input', 'current-owner')
        await service.on_runtime_event('assistant.delta', {
            'sessionId': sid, 'text': 'incomplete', 'inputId': 'current-input'})
        identity = session['streamingId']
        before_turn = copy.deepcopy(turn)
        before_messages = copy.deepcopy(session['messages'])
        ending = {'sessionId': sid, 'rootSessionId': sid,
                  'runtimeOwnerId': 'current-owner', 'status': 'interrupted'}
        unowned = dict(ending)
        if boundary == 'missing-owner':
            unowned.pop('runtimeOwnerId')
        elif boundary == 'foreign-owner':
            unowned['runtimeOwnerId'] = 'foreign-owner'
        elif boundary == 'child':
            unowned['sessionId'] = 'child-session'
        elif boundary == 'foreign-root':
            unowned['rootSessionId'] = 'foreign-root'
        elif boundary == 'background':
            unowned['backgroundOnly'] = True
        else:
            unowned['runtimeOwnerId'] = 'older-owner'
        await service.on_runtime_event('runtime.ended', unowned)
        assert session['streamingId'] == identity and session['streaming'] == 'incomplete'
        assert session['streamingInputId'] == 'current-input'
        assert session['messages'] == before_messages and turn == before_turn
        assert not session.get('failure')

        # The same fixture must settle when the exact current owner does end.
        await service.on_runtime_event('runtime.ended', ending)
        assert 'streamingId' not in session and 'streaming' not in session
        partial = copy.deepcopy(session['messages'][-1])
        assert partial['streamId'] == identity and partial['partial'] is True
        assert partial['text'] == '[Interrupted response]\n\nincomplete'
        assert partial['inputId'] == 'current-input'
        assert turn['phase'] == 'interrupted'

        await owned_turn(service, 'next-input', 'next-owner')
        await service.on_runtime_event('assistant.delta', {
            'sessionId': sid, 'text': 'next answer', 'inputId': 'next-input'})
        next_id = session['streamingId']
        assert next_id != identity
        await service.on_runtime_event('runtime.ended', ending)
        assert session['streamingId'] == next_id and session['streaming'] == 'next answer'
        assert session['messages'][-1] == partial
        await service.on_runtime_event('assistant.message', {
            'sessionId': sid, 'text': 'next answer', 'inputId': 'next-input'})
        assert session['messages'][-2] == partial
        final = session['messages'][-1]
        assert final['streamId'] == next_id and final['inputId'] == 'next-input'
        assert not final.get('partial') and final['text'] == 'next answer'
        send.assert_not_awaited()
        start.assert_not_awaited()
        assert not runtime.workers
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
