"""Bundle configuration failures survive the real worker protocol safely."""
import json
import sys

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError, AppService
from amplifier_web.worker_diagnostics import save_startup_failure


@pytest.mark.parametrize('error_type,summary', [
    ('BundleDependencyError', 'The selected bundle could not be prepared.'),
    ('BundleLoadError', 'The selected bundle could not be prepared.'),
    ('BundleValidationError', 'The selected bundle could not be prepared.'),
    ('BundleNotFoundError', 'The selected bundle ID could not be resolved.'),
    ('AmbiguousBundleReferenceError', 'The bundle ID casing matches more than one registration.'),
])
@pytest.mark.parametrize('phase', ['cold', 'remount'])
async def test_bundle_failure_preserves_specific_safe_diagnostic_and_unsent_input(
        tmp_path, monkeypatch, error_type, summary, phase):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    private_path = '/private/project/sensitive-bundle.yaml'
    detail = f'{error_type}: {private_path} https://user:synthetic-secret@example.invalid/private-source'
    receipt = save_startup_failure({'stderr': [detail]}, None) if phase == 'remount' else None
    # A protocol fixture isolates the host's real subprocess boundary from
    # network-dependent bundle resolution. It never accepts a user input.
    event = {'type': 'runtime.error', 'error': detail}
    fixture = '''import json,sys
for line in sys.stdin:
    data = json.loads(line)
    if data['op'] == 'start':
        if PHASE == 'cold':
            print(json.dumps(EVENT), flush=True)
            break
        print(json.dumps({'type': 'runtime.ready', 'report': {}}), flush=True)
    elif data['op'] == 'send':
        print(json.dumps({'op': 'reply', 'id': data['id'], 'code': 'worker_preparation_failed',
                          'error': DETAIL, 'diagnosticReceipt': RECEIPT}), flush=True)
        break
'''.replace('PHASE', repr(phase)).replace('EVENT', repr(event)).replace('DETAIL', repr(detail)).replace('RECEIPT', repr(receipt.name if receipt else None))
    manager = RuntimeManager(command=[sys.executable, '-c', fixture], retention={'prewarm_on_select': False})
    app = AppService(tmp_path, manager, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        if phase == 'remount':
            await manager.start(session, app.on_runtime_event)
        with pytest.raises(AppError) as rejected:
            await app.dispatch('conversation.send', {'text': 'Keep this saved request'}, command_id='new-input')
        assert rejected.value.code == 'worker_startup_failed'
        assert rejected.value.receipt['delivery'] == 'failed'
        if phase == 'remount':
            await manager.workers[session['id']]['reader']

        failure = session['failure']
        assert failure['category'] == 'bundle_configuration'
        assert failure['errorType'] == error_type
        assert failure['summary'] == summary
        assert failure['replayed'] is False
        assert session['messages'][0]['text'] == 'Keep this saved request'
        assert session['messages'][0]['delivery']['status'] == 'failed'
        diagnostic = tmp_path / 'logs/workers' / session['diagnosticReceipt']
        assert private_path in diagnostic.read_text()
        if receipt:
            assert diagnostic == receipt

        inspected = await app.dispatch('session.inspect', {'id': session['id']})
        assert inspected['result']['failure'] == failure
        assert inspected['result']['diagnosticReceipt'] == diagnostic.name
        public = json.dumps([app.browser_state(), inspected, str(rejected.value)])
        for private in (private_path, 'synthetic-secret', 'example.invalid', 'private-source'):
            assert private not in public

        duplicate = await app.dispatch('conversation.send', {'text': 'Keep this saved request'}, command_id='new-input')
        assert duplicate['duplicate'] and duplicate['delivery'] == 'failed'
        assert len(session['messages']) == 1
    finally:
        await app.close()
