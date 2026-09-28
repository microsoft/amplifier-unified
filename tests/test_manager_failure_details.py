import json

import pytest

from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppService
from amplifier_web.session_health import generation_failure, inspect_session


async def test_local_context_failure_survives_bridge_and_generic_worker_exit(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        event = {'type': 'generation.failed', 'generation_id': 'g-1', 'input_ids': ['input-1'],
            'error_type': 'ContextLengthError', 'error_category': 'context_limit',
            'error_stage': 'context_preparation', 'effects': 'not_rolled_back', 'retryable': False,
            'error_message': 'never copy arbitrary private payloads', 'sdk_response': 'secret'}
        kind, payload = normalize_event(event, session['id'])
        assert 'error_message' not in payload and 'sdk_response' not in payload
        await app.on_runtime_event(kind, payload)
        await app.on_runtime_event('runtime.error', {'sessionId': session['id'], 'error': 'Manager turn failed; no automatic replay'})
        failure = inspect_session(tmp_path, session)['failure']
        assert failure['category'] == 'context_limit' and failure['stage'] == 'context_preparation'
        assert failure['inputIds'] == ['input-1'] and failure['generationId'] == 'g-1'
        assert 'local budget check, not a provider response' in failure['guidance']
        assert 'Local context preparation' in session['error']
        assert failure['effects'] == 'not_rolled_back' and failure['replayed'] is False
        assert session['status'] == 'error' and len(app.state['sessions']) == 1
        assert 'secret' not in json.dumps(failure)
    finally:
        await app.close()


@pytest.mark.parametrize('category', ['authentication', 'rate_limit', 'invalid_request', 'content_filter',
    'provider_timeout', 'provider_unavailable', 'unknown'])
def test_known_failure_has_safe_guidance(category):
    result = generation_failure({'error_category': category, 'error_stage': 'provider_request',
        'error_type': 'RuntimeError', 'error_message': 'private secret', 'effects': 'none',
        'replayed': True, 'retryable': 'yes'})
    assert result['summary'] and result['guidance']
    assert result['effects'] == 'not_rolled_back' and result['replayed'] is False
    assert result['retryable'] is False and 'private secret' not in json.dumps(result)


def test_unknown_failure_vocabulary_does_not_copy_arbitrary_data():
    assert generation_failure({'error_category': 'private secret'}) is None
    result = generation_failure({'error_category': 'unknown', 'error_stage': 'private stage', 'error_type': 'a\nsecret'})
    assert result['stage'] == 'manager_turn' and result['errorType'] == 'Error'


async def test_correctable_tool_error_does_not_become_a_conversation_failure(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        sid = session['id']
        await app.on_runtime_event('runtime.generation', {
            'sessionId': sid, 'event': 'generation.started', 'generation_id': 'turn-1'})
        for phase in ('pre', 'error'):
            await app.on_runtime_event('runtime.tool', {
                'sessionId': sid, 'tool': 'bash', 'callId': 'failed-script', 'phase': phase,
                'error': "TypeError: string indices must be integers, not 'str'"})
        await app.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'Corrected the script.'})
        await app.on_runtime_event('runtime.generation', {
            'sessionId': sid, 'event': 'generation.finished', 'generation_id': 'turn-1'})
        assert not session.get('error') and not session.get('failure')
        assert any(row.get('phase') == 'error' for row in session['runtimeEvents'])
        assert not [row for row in app.get_state()['attention']['items'] if row['id'] == 'session:' + sid and row.get('severity') == 'error']
    finally:
        await app.close()
