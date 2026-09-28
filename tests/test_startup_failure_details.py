"""Exercise the saved UI state after a real, disposable worker startup failure."""
import copy
import json
import sys

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError, AppService


async def test_send_keeps_private_startup_log_location_after_admission_fails(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    fixture = "import sys; sys.stdin.readline(); print('ImportError: private-fixture-token', file=sys.stderr, flush=True); raise SystemExit(1)"
    runtime = RuntimeManager(command=[sys.executable, '-c', fixture],
                             retention={'prewarm_on_select': False})
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        request = {'text': 'Keep this unsent request'}
        with pytest.raises(AppError) as rejected:
            await app.dispatch('conversation.send', request, command_id='startup-failure')
        assert rejected.value.code == 'worker_startup_failed'
        assert rejected.value.receipt['delivery'] == 'failed'
        files = list((tmp_path / 'logs/workers').glob('startup-*.log'))
        assert len(files) == 1
        session = app._session(sid)
        assert str(files[0]) in session['error']
        assert 'private-fixture-token' not in json.dumps(app.get_state())
        assert session['failure']['category'] == 'worker_startup'
        assert 'cause is not available' not in session['failure']['summary']
        assert str(files[0]) in str(rejected.value)
        assert not runtime.workers
        duplicate = await app.dispatch('conversation.send', request, command_id='startup-failure')
        assert duplicate['duplicate'] and duplicate['delivery'] == 'failed'
        assert str(files[0]) in duplicate['error']
        assert len(list((tmp_path / 'logs/workers').glob('startup-*.log'))) == 1
        assert len(session['messages']) == 1 and session['messages'][0]['text'] == request['text']
    finally:
        await app.close()
    restored = AppService(tmp_path, workspace=tmp_path)
    try:
        assert str(files[0]) in restored._session(sid)['error']
        assert restored._session(sid)['failure']['category'] == 'worker_startup'
    finally:
        await restored.close()


async def test_failed_retry_without_new_worker_detail_does_not_reuse_old_error(tmp_path):
    runtime = RuntimeManager(retention={'prewarm_on_select': False})
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        app._message(session, 'user', 'Saved uncertain input', inputId='uncertain-input',
                     delivery={'status': 'unknown'})
        await app.on_runtime_event('runtime.error', {
            'sessionId': session['id'], 'error': 'Old diagnostic: /old/worker.log',
        })
        await runtime.close()
        with pytest.raises(AppError) as rejected:
            await app._send(copy.deepcopy(session), 'Saved uncertain input', 'uncertain-input', retry=True)
        assert rejected.value.receipt['delivery'] == 'unknown'
        assert 'Earlier delivery is still uncertain' in str(rejected.value)
        assert '/old/worker.log' not in str(rejected.value)
        assert '/old/worker.log' not in session['error']
        assert session['failure']['category'] == 'worker_startup'
        assert session['messages'][0]['delivery']['status'] == 'unknown'
        assert len(session['messages']) == 1 and not runtime.workers
    finally:
        await app.close()


@pytest.mark.parametrize('detail, category, label', [
    ('AuthenticationError: provider authentication failed', 'authentication', 'AuthenticationError'),
    ('RateLimitError: provider limit reached', 'rate_limit', 'RateLimitError'),
    ('ContextLengthError: too many tokens', 'context_limit', 'ContextLengthError'),
    ('Invalid image_url: invalid base64 value', 'invalid_image', 'InvalidImageError'),
    ('tools.0 input_schema: extra inputs are not permitted', 'tool_configuration', 'ToolConfigurationError'),
])
async def test_specific_classified_startup_error_keeps_its_guidance(tmp_path, monkeypatch, detail, category, label):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    event = {'type': 'runtime.error', 'error': detail + ' private-provider-payload'}
    fixture = 'import sys,json; sys.stdin.readline(); print(' + repr(json.dumps(event)) + ',flush=True)'
    runtime = RuntimeManager(command=[sys.executable, '-c', fixture],
                             retention={'prewarm_on_select': False})
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        with pytest.raises(AppError) as rejected:
            await app.dispatch('conversation.send', {'text': 'Saved input'}, command_id='auth-start')
        session = app._session()
        assert session['failure']['category'] == category
        from amplifier_web.session_health import failure_details
        assert session['failure']['guidance'] == failure_details(detail)['guidance']
        if category != 'context_limit':
            assert label in session['error']
            assert label in str(rejected.value)
        else:
            assert 'model context limit' in str(rejected.value)
        assert 'private-provider-payload' not in json.dumps(app.get_state())
        assert 'private-provider-payload' not in str(rejected.value)
        assert 'private-provider-payload' in (tmp_path / 'logs/workers' / session['diagnosticReceipt']).read_text()
        assert session['messages'][0]['delivery']['status'] == 'failed'
        assert not runtime.workers
    finally:
        await app.close()
