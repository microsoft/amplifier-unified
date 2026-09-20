import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppService, AppError
from amplifier_web.session_health import inspect_session, exception_details
from amplifier_web.session_store import fork_session


BAD_IMAGE = "Invalid 'input[86].output.image_url'. Expected a base64-encoded data URL, but got an invalid base64-encoded value."


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


async def test_original_provider_cause_survives_runtime_wrapper(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    class Provider:
        def get_info(self):
            return SimpleNamespace(id='test', defaults={'model': 'fixture'})
        async def complete(self, request, **kwargs):
            raise ValueError(BAD_IMAGE)
    provider, emitted = Provider(), []
    events = ExecutionEvents(session['id'], emitted.append)
    events.lifecycle({'type': 'input.delivered', 'input_id': 'turn-id'})
    events.instrument_provider(session['id'], provider)
    with pytest.raises(ValueError):
        await provider.complete(SimpleNamespace(model='fixture'))
    for event in emitted:
        kind, payload = normalize_event(event, session['id'], 'turn-id')
        await app.on_runtime_event(kind, payload)
    await app.on_runtime_event('runtime.error', {'sessionId': session['id'], 'error': 'Manager turn failed; no automatic replay'})
    assert session['failure']['category'] == 'invalid_image'
    assert session['failure']['inputId'] == 'turn-id'
    assert inspect_session(tmp_path, session)['failure']['category'] == 'invalid_image'
    assert exception_details(ValueError('unknown error with secret=never-copy'))['category'] == 'unknown'
    await app.close()
