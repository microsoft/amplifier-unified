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


@pytest.mark.parametrize('tool,detail', [('bash', "TypeError: string indices must be integers, not 'str'"),
                                     ('web_fetch', 'HTTP 404: Not Found')])
async def test_correctable_tool_error_does_not_become_a_conversation_failure(tmp_path, tool, detail):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        sid = session['id']
        from amplifier_web.execution import ensure_turn
        from amplifier_web.navigation_summary import activity
        ensure_turn(session,'turn-1')
        await app.on_runtime_event('runtime.status', {'sessionId':sid,'status':'working'})
        await app.on_runtime_event('runtime.generation', {
            'sessionId': sid, 'event': 'generation.started', 'generation_id': 'turn-1'})
        for phase in ('pre', 'error'):
            await app.on_runtime_event('runtime.tool', {
                'sessionId': sid, 'tool': tool, 'callId': 'failed-action', 'phase': phase,
                'error': detail})
        await app.on_runtime_event('execution.event', {'sessionId':sid,'rootSessionId':sid,
            'id':'failed-action','turnId':'turn-1','kind':'tool','label':tool,'phase':'error',
            'startedAt':1,'endedAt':2,'error':detail})
        running=app.browser_state()
        row=next(row for row in running['chatNavigation']['items'] if row['id']==sid)
        assert row['activity']['kind']=='working'
        assert app.browser_state()['sessions'][0]['execution']['segments'][0]['phase']=='running'
        await app.on_runtime_event('assistant.message', {'sessionId': sid, 'text': 'Corrected the script.'})
        await app.on_runtime_event('runtime.generation', {
            'sessionId': sid, 'event': 'generation.finished', 'generation_id': 'turn-1'})
        await app.on_runtime_event('runtime.status', {'sessionId':sid,'status':'idle'})
        assert activity(session)['kind']=='idle'
        settled=app.browser_state()
        row=next(row for row in settled['chatNavigation']['items'] if row['id']==sid)
        assert row['activity']['kind'] in {'idle','unread'}
        execution=next(row for row in settled['sessions'] if row['id']==sid)['execution']
        assert execution['segments'][0]['phase']=='completed'
        assert execution['nodes']==[]
        from amplifier_web.browser_detail import page
        nodes=page(session,'nodes',group=execution['segments'][0]['id'])['items']
        assert nodes[0]['phase']=='error'
        assert not session.get('error') and not session.get('failure')
        assert any(row.get('phase') == 'error' for row in session['runtimeEvents'])
        assert not [row for row in app.get_state()['attention']['items'] if row['id'] == 'session:' + sid and row.get('severity') == 'error']
    finally:
        await app.close()


@pytest.mark.parametrize('old_type', ['TimeoutError', 'ContextLengthError'])
@pytest.mark.parametrize('identity', [{'errorType': 'RuntimeStartupError'}, {'phase': 'worker_startup'}])
async def test_new_startup_failure_replaces_old_manager_cause_and_time(tmp_path, old_type, identity):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        session.update(failure={**generation_failure({'error_category': 'unknown'}),
            'generationId': 'old', 'inputIds': ['old-input'], 'recordedAt': 100}, errorAt=100,
            turnErrorType=old_type)
        await app.on_runtime_event('runtime.error', {'sessionId': session['id'],
            **identity, 'error': 'The conversation worker could not start.'})
        assert session['failure']['category'] == 'worker_startup'
        assert session['errorType'] == 'RuntimeStartupError'
        assert 'generationId' not in session['failure']
        assert session['errorAt'] > 100
        assert 'manager turn failed' not in session['error'].lower()
    finally:
        await app.close()


async def test_generic_exit_does_not_redate_the_same_manager_failure(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        session['failure'] = {**generation_failure({'error_category': 'unknown'}), 'recordedAt': 100}
        await app.on_runtime_event('runtime.error', {'sessionId': session['id'], 'error': 'Worker exited'})
        assert session['errorAt'] == session['failure']['recordedAt'] == 100
    finally:
        await app.close()


async def test_handled_compaction_error_does_not_mark_conversation_broken(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        await app.on_runtime_event('execution.event', {'id': 'llm:compact', 'sessionId': session['id'],
            'kind': 'llm', 'phase': 'error', 'purpose': 'context_compaction', 'lifecycle': 'turn',
            'failure': generation_failure({'error_category': 'context_limit'}), 'endedAt': 123})
        assert not session.get('failure') and not session.get('error')
        # A real terminal manager failure still owns a turn-level diagnosis.
        kind, payload = normalize_event({'type': 'generation.failed', 'generation_id': 'g1',
            'error_type': 'ContextLengthError', 'error_category': 'context_limit'}, session['id'])
        await app.on_runtime_event(kind, payload)
        assert session['failure']['category'] == 'context_limit'
    finally:
        await app.close()


@pytest.mark.parametrize('code,remedy', [
    ('summary_output_limit', 'summary_max_output_tokens'),
    ('summary_empty', 'summarization model'),
    ('previous_failure', 'retry cooldown'),
    ('summary_failed', 'provider availability'),
    ('native_failed', 'provider availability'),
])
async def test_semantic_compaction_cause_survives_runtime_exit(tmp_path, code, remedy):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        kind, payload = normalize_event({'type': 'generation.failed', 'generation_id': 'g-summary',
            'input_ids': ['input-summary'], 'error_type': 'CompactionError',
            'error_category': 'context_compaction', 'error_code': code,
            'error_message': 'PRIVATE SDK BODY'}, session['id'])
        await app.on_runtime_event(kind, payload)
        await app.on_runtime_event('runtime.error', {'sessionId': session['id'], 'error': 'Manager turn failed'})
        failure = inspect_session(tmp_path, session)['failure']
        assert failure['code'] == code and remedy in failure['guidance']
        assert failure['inputIds'] == ['input-summary'] and failure['generationId'] == 'g-summary'
        assert failure['replayed'] is False and 'PRIVATE' not in json.dumps(failure)
    finally:
        await app.close()


@pytest.mark.parametrize('code', ['private secret', {'secret': 'payload'}, ['secret'], None])
def test_unknown_compaction_code_is_not_exposed(code):
    result = generation_failure({'error_category': 'context_compaction', 'error_code': code})
    assert 'code' not in result and 'secret' not in json.dumps(result)
