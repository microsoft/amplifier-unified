import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppService, AppError
from amplifier_web.session_health import inspect_session, exception_details, failure_details
from amplifier_web.session_store import fork_session


BAD_IMAGE = "Invalid 'input[86].output.image_url'. Expected a base64-encoded data URL, but got an invalid base64-encoded value."


def test_typed_context_error_is_recognized_without_sdk_message_wording():
    detail = failure_details('OpenAI request exceeds the local input allowance before dispatch.', 'ContextLengthError')
    assert detail['category'] == 'context_limit'
    assert detail['errorType'] == 'ContextLengthError'
    assert 'cause is not available' not in detail['summary']


async def test_runtime_context_limit_materializes_safe_failure_projection(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    await app.on_runtime_event('runtime.error', {
        'sessionId': session['id'], 'errorType': 'ContextLengthError',
        'error': 'provider request includes secret=[REDACTED:SECRET]',
    })
    assert session['status'] == 'error'
    assert session['failure']['category'] == 'context_limit'
    assert session['failure']['errorType'] == 'ContextLengthError'
    assert 'secret' not in json.dumps(session['failure'])
    assert 'provider request' not in session['error']
    await app.close()


async def test_verified_generation_start_clears_stale_failure(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    await app.on_runtime_event('runtime.error', {
        'sessionId': session['id'], 'errorType': 'ContextLengthError', 'error': 'context limit',
    })
    await app.on_runtime_event('runtime.generation', {
        'sessionId': session['id'], 'event': 'generation.started', 'generation_id': 'retry',
    })
    assert 'error' not in session and 'failure' not in session
    await app.close()


async def test_inspection_reports_structured_failure_without_raw_error_or_log_scan(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    session.update(status='stopped', failure={
        **failure_details('Invalid image base64 data', 'ValueError'),
        'inputId': 'failed-input', 'recordedAt': 1790182800,
    })
    from pathlib import Path
    original_open = Path.open
    def no_events(path, *args, **kwargs):
        assert path.name != 'events.jsonl', 'Structured-only inspection does not start a log scan'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_events)
    receipt = await app.dispatch('session.inspect', {'id': session['id']}, origin='agent')
    report = receipt['result']
    assert report['failure'] == session['failure']
    assert report['capturedAt'] > 0
    assert report['workReplayed'] is False and report['status'] == 'stopped'
    assert 'error' not in session and session['messages'] == []
    await app.close()


@pytest.mark.parametrize('change', ['status', 'failure', 'worker', 'title'])
async def test_inspection_does_not_cache_snapshot_changed_while_reading(tmp_path, monkeypatch, change):
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    started, release = threading.Event(), threading.Event()
    def delayed_inspect(home, snapshot):
        started.set()
        assert release.wait(5)
        return inspect_session(home, snapshot)
    monkeypatch.setattr('amplifier_web.session_health.inspect_session', delayed_inspect)
    operation = asyncio.create_task(app.dispatch('session.inspect', {'id': session['id']}))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        if change == 'status':
            await app.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'stopped'})
        elif change == 'failure':
            await app.on_runtime_event('execution.event', {
                'sessionId': session['id'], 'id': 'failed-call', 'kind': 'llm', 'phase': 'error',
                'failure': failure_details('Invalid image base64', 'ValueError'),
            })
        elif change == 'worker':
            session['workers'] = [{'id': 'synthetic-worker', 'status': 'working'}]
        else:
            session['title'] = 'Updated title'
        release.set()
        receipt = await operation
        assert receipt['result']['stale'] is True
        assert 'health' not in session
    finally:
        release.set()
        await operation
        await app.close()


def rows():
    return [
        {'role': 'system', 'content': 'Old bundle system instructions'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Investigate'}, {'type': 'image', 'source': {'type': 'base64', 'data': 'broken'}}]},
        {'role': 'assistant', 'content': 'Checking', 'tool_calls': [{'id': 'computer-call', 'name': 'computer', 'arguments': {'action': 'click'}}], 'provider_data': {'native_call': True}},
        {'role': 'tool', 'tool_call_id': 'computer-call', 'name': 'computer', 'content': 'halted: human confirmation required'},
        {'role': 'user', 'content': 'Do not try again; respond without tools.'},
    ]


async def test_recovery_is_independent_durable_and_never_executes_or_replays(tmp_path):
    from test_service import Runtime
    runtime = Runtime()
    app = AppService(tmp_path, workspace=tmp_path, runtime=runtime)
    await app.dispatch('session.create', {})
    source = app._session()
    source.update(status='error', error='Manager turn failed; no automatic replay', workers=[{'id': 'old-job'}], approvals=[{'id': 'old-approval'}])
    app._message(source, 'user', 'Investigate', attachments=[{'id': 'image'}], inputId='original-input')
    app._message(source, 'assistant', 'Checking')
    app._message(source, 'user', 'Do not try again; respond without tools.')
    store = SessionStore.for_app(tmp_path, tmp_path)
    store.save(source['id'], rows(), {}, preserve_system=True)
    original = copy.deepcopy(source)
    before = (store.directory(source['id']) / 'transcript.jsonl').read_bytes()
    await app.dispatch('session.recover', {'id': source['id']}, command_id='recover-once', origin='agent')
    recovered = app._session()
    saved, metadata = store.load(recovered['id'])
    assert recovered['id'] != source['id']
    assert recovered['messages'][0]['inputId'] == 'original-input'
    assert recovered['recovery']['sourceSessionId'] == source['id']
    assert recovered['status'] == 'idle' and not recovered['workers'] and not recovered['approvals']
    assert metadata['recovery']['work_replayed'] is False
    assert all(row['role'] in {'user', 'assistant'} and not row.get('tool_calls') and not row.get('provider_data') for row in saved)
    assert '"type": "image"' not in json.dumps(saved)
    assert 'halted: human confirmation required' in json.dumps(saved)
    assert source == original and (store.directory(source['id']) / 'transcript.jsonl').read_bytes() == before
    await asyncio.sleep(.02)
    assert runtime.sent == []
    receipt = await app.dispatch('session.recover', {'id': source['id']}, command_id='recover-once', origin='agent')
    assert receipt['duplicate'] and len(app.state['sessions']) == 2
    # The new transcript still supports normal fork boundaries.
    fork_session(tmp_path, recovered, 'fork-of-recovery', turn=1)
    await app.close()


async def test_recovery_blocks_active_work_and_survives_restart(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {})
    source = app._session()
    source['status'] = 'working'
    with pytest.raises(AppError, match='finish'):
        await app.dispatch('session.recover', {'id': source['id']})
    source['status'] = 'error'
    for status in ('running', 'working'):
        source['workers'] = [{'status': status}]
        assert inspect_session(tmp_path, source)['canRecover'] is False
        with pytest.raises(AppError, match='delegated'):
            await app.dispatch('session.recover', {'id': source['id']})
    source['workers'] = []
    source['error'] = 'failed'
    await app.dispatch('session.recover', {'id': source['id']})
    identity = app._session()['id']
    await app.close()
    app = AppService(tmp_path, workspace=tmp_path)
    assert app._session(identity)['recovery']['sourceSessionId'] == source['id']
    await app.close()


async def test_inspection_reads_legacy_root_error_and_exposes_identity_to_agent(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {})
    source = app._session()
    source.update(status='error', error='RuntimeError: Manager turn failed; no automatic replay')
    folder = SessionStore.for_app(tmp_path, tmp_path).directory(source['id']) / 'context-intelligence'
    folder.mkdir(parents=True, exist_ok=True)
    events = [{'event': 'provider:error', 'timestamp': '2026-09-20T17:38:58+00:00', 'data': {'session_id': source['id'], 'error': {'type': 'InvalidRequestError', 'msg': BAD_IMAGE + ' secret=do-not-disclose'}}},
              {'event': 'provider:error', 'data': {'session_id': 'child', 'error': 'rate limit'}},
              {'event': 'provider:error', 'session_id': 'child', 'data': {'error': 'authentication failed'}},
              {'event': 'provider:error', 'session_id': 'child', 'data': {'session_id': source['id'], 'error': 'rate limit'}}]
    (folder / 'events.jsonl').write_text('\n'.join(json.dumps(row) for row in events) + '\n{partial')
    receipt = await app.dispatch('session.inspect', {'id': source['id']}, origin='agent')
    detail = receipt['result']
    assert detail['runtimeSessionId'] == source['id'] and detail['failure']['category'] == 'invalid_image'
    assert detail['failure']['errorType'] == 'InvalidRequestError'
    assert 'do-not-disclose' not in json.dumps(detail)
    assert source['status'] == 'error' and len(app.state['sessions']) == 1
    await app.close()


@pytest.mark.parametrize('message,error_class,category', [
    (BAD_IMAGE, ValueError, 'invalid_image'),
    ('OpenAI request exceeds the local input allowance before dispatch.',
     type('ContextLengthError', (RuntimeError,), {}), 'context_limit'),
])
async def test_original_provider_cause_survives_runtime_wrapper(tmp_path, message, error_class, category):
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    class Provider:
        def get_info(self):
            return SimpleNamespace(id='test', defaults={'model': 'fixture'})
        async def complete(self, request, **kwargs):
            raise error_class(message)
    provider, emitted = Provider(), []
    events = ExecutionEvents(session['id'], emitted.append)
    events.lifecycle({'type': 'input.delivered', 'input_id': 'turn-id'})
    events.instrument_provider(session['id'], provider)
    with pytest.raises(error_class):
        await provider.complete(SimpleNamespace(model='fixture'))
    for event in emitted:
        kind, payload = normalize_event(event, session['id'], 'turn-id')
        await app.on_runtime_event(kind, payload)
    await app.on_runtime_event('runtime.error', {'sessionId': session['id'], 'error': 'Manager turn failed; no automatic replay'})
    assert session['failure']['category'] == category
    assert session['failure']['inputId'] == 'turn-id'
    assert inspect_session(tmp_path, session)['failure']['category'] == category
    assert exception_details(ValueError('unknown error with secret=never-copy'))['category'] == 'unknown'
    await app.close()


def test_structured_computer_stop_survives_exception_chain_without_payload_leak():
    from amplifier_web.session_health import failure_details
    inner=ValueError('secret arbitrary provider payload')
    inner.code='computer_result_not_image';inner.tool_call_id='call_42';inner.result_kind='error'
    outer=RuntimeError('Turn failed');outer.__cause__=inner
    detail=exception_details(outer)
    assert detail['category']=='computer_capture_stop' and detail['toolCallId']=='call_42'
    assert detail['resultKind']=='error' and detail['retryable'] is False and detail['replayed'] is False
    assert 'secret' not in json.dumps(detail) and 'does not clear' in detail['guidance']
    assert failure_details({'code':inner.code,'tool_call_id':'wrong\nsecret','result_kind':'arbitrary secret'})['code']==inner.code
    assert 'toolCallId' not in failure_details({'code':inner.code,'tool_call_id':'wrong\nsecret'})
    assert 'resultKind' not in failure_details({'code':inner.code,'result_kind':'arbitrary secret'})


def test_native_tool_definition_rejection_has_safe_actionable_failure():
    from amplifier_web.session_health import failure_details
    result = failure_details("tools.45: Input tag 'computer' found using 'type' does not match any expected tags; secret-context", 'BadRequestError')
    assert result['category'] == 'tool_configuration'
    assert 'tool definition' in result['summary']
    assert 'secret-context' not in str(result)
    assert result['replayed'] is False
